"""Driver: the whole AutoRPA-style build for ONE family and ONE model.

Stage list (the Android arm's t16 protocol, same order, same charging):

  0. floor, 18 no-task probe runs -> the per-episode floor subtracted from
     every agent/document-side cost below (compile and extraction never);
  1. exploration, 3 building episodes (seeds 1, 2, 3), up to 2 reflection
     retries each, step cap ceil(10 x complexity) capped at 50;
  2. translator, one call per effective action of each building trajectory;
  3. builder, k = 1, 2, 3 x {code, doc} -- six calls, all charged as C;
  4. held-out gate (5 bindings) per k's code artifact;
  5. verification with hybrid repair (M = 3) on the k = 3 code artifact,
     gated after every version; the best gate-passing version is returned;
  6. deploy 30 uses (iff admitted: >= 4/5 gate), each use = one extraction
     call (+ one bounded retry) -> type check -> program -> checker;
  7. doc arm, 3 episodes on unseen seeds 4-6 with the k3 doc in the prompt.

build.json carries every stage in RAW tokens AND in the price-weighted unit
(fresh + r_c x cached + r_o x completion) with the cold-host imputation
applied to in-episode call sequences, plus N* under the marginal and AutoRPA
accountings with s = (1 - q) c - d (floored).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import families, guest_env
from .accounting import (
    episode_unit, price_weighted_call, price_weighted_totals, weights_for, zero,
)
from .compiler import compile_trajectories
from .program_runtime import ProgramRunner, program_from_source
from .deploy_runner import deploy_uses, run_deployment
from .explore import BUILDING_SEEDS, episode_usage, run_exploration, step_cap
from .floor import measure_floor
from .gate_runner import GATE_K, GATE_MIN_PASS, heldout_bindings, run_gate
from .translator import translate_trajectory
from .verify_runner import verify_and_repair

DEFAULT_K_VALUES = (1, 2, 3)
DEPLOY_USES = 30
DOC_ARM_SEEDS = (4, 5, 6)


class BudgetExceeded(RuntimeError):
    """The run's spend cap was reached; stages already done are still written."""


class ResumeError(RuntimeError):
    """A resumed run cannot rebuild a stage that build.json calls done."""


_STAGE_RECORD_KEY = {
    "floor": "floor",
    "exploration": "exploration",
    "translator": "translator",
    "builder": "builder",
    "gate_per_k": "gate_per_k",
    "verification": "verification",
    "deploy": "deploy",
    "doc_arm": "doc_arm",
}


def _read_json(path: Path, stage: str):
    if not path.is_file():
        raise ResumeError(f"cannot resume {stage}: {path} is missing")
    return json.loads(path.read_text())


def resumed_record(out_dir: Path | str) -> dict | None:
    path = Path(out_dir) / "build.json"
    return json.loads(path.read_text()) if path.is_file() else None


# ---------------------------------------------------------------- priced unit


def weights(model: str) -> dict:
    return weights_for(model)


def priced_totals(totals: dict, model: str) -> int:
    """A stage total (single-call stages: no imputation) in the priced unit."""
    w = weights(model)
    return price_weighted_totals(totals, w["r_c"], w["r_o"])


def priced_episode(calls_detail: list[dict], model: str,
                   floor_unit: int | None) -> int:
    """One episode's calls (in order) -> priced unit, cold-host applied,
    floor subtracted when given."""
    w = weights(model)
    return episode_unit(calls_detail, w["r_c"], w["r_o"], floor_unit=floor_unit)["unit_tokens"]


# --------------------------------------------------------------- the build


def run_build(
    family: str,
    model: str,
    out_dir: Path | str,
    env,
    seeds=BUILDING_SEEDS,
    k_values=DEFAULT_K_VALUES,
    deploy_n: int = DEPLOY_USES,
    doc_seeds=DOC_ARM_SEEDS,
    obs_mode: str = "screenshot+ax",
    client=None,
    max_cost_usd: float | None = None,
    resume: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    record: dict = {
        "family": family,
        "model": model,
        "seeds": list(seeds),
        "step_cap": step_cap(family),
        "price_weights": weights(model),
        "record_type": "build",
    }
    spent = {"usd": 0.0}

    done: list[str] = []
    if resume:
        previous = resumed_record(out_dir)
        if previous is not None:
            if previous.get("family") != family or previous.get("model") != model:
                raise ResumeError(
                    f"{out_dir / 'build.json'} records a build of "
                    f"{previous.get('family')} / {previous.get('model')}, "
                    f"not {family} / {model}")
            for key, value in previous.items():
                record.setdefault(key, value)
            done = list(previous.get("stages_done") or [])
            record["stages_done"] = list(done)
            record["resumed_from_stages"] = list(done)
            print(f"resume: reusing {len(done)} recorded stage(s): {done}", flush=True)

    def checkpoint(stage: str) -> None:
        record["stages_done"] = record.get("stages_done", []) + [stage]
        record["wall_s"] = round(time.time() - t0, 1)
        (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
        if max_cost_usd is not None and spent["usd"] > max_cost_usd:
            raise BudgetExceeded(
                f"spent ${spent['usd']:.4f} after {stage}, cap ${max_cost_usd:.2f}")

    # -- 0. floor ----------------------------------------------------------
    if "floor" in done:
        print("[0/7] floor: reusing floor/floor.json", flush=True)
        floor = _read_json(out_dir / "floor" / "floor.json", "floor")
        record["floor"] = floor
    else:
        print("[0/7] floor: 18 no-task probe runs", flush=True)
        floor = measure_floor(model, out_dir / "floor", client=client, env=env)
        record["floor"] = floor
        checkpoint("floor")
    floor_unit = floor["floor_unit_mean"]

    def episode_floor(calls_detail):
        return priced_episode(calls_detail, model, floor_unit)

    # -- 1. exploration ------------------------------------------------------
    if "exploration" in done:
        print("[1/7] exploration: reusing explore/exploration.json", flush=True)
        exploration = _read_json(out_dir / "explore" / "exploration.json", "exploration")
        record["exploration"] = exploration
        spent["usd"] += exploration["totals"]["cost_usd"]
    else:
        print(f"[1/7] exploration: seeds {list(seeds)}, step cap {step_cap(family)}",
              flush=True)
        exploration = run_exploration(
            family=family, model=model, out_dir=out_dir / "explore", seeds=seeds,
            obs_mode=obs_mode, client=client, env=env,
        )
        spent["usd"] += exploration["totals"]["cost_usd"]
        record["exploration"] = exploration
        checkpoint("exploration")

    building = [
        {"seed": i["seed"], "trajectory": i["best_trajectory"], "success": i["success"]}
        for i in exploration["instances"]
    ]
    # priced, floored per-episode costs (the c figures come from attempt 0)
    for instance in exploration["instances"]:
        for attempt in instance["attempts"]:
            attempt["unit_floored"] = episode_floor(attempt["usage"].get("calls_detail") or [])
    # -- 2. translator --------------------------------------------------------
    if "translator" in done:
        print("[2/7] translator: reusing translation.json", flush=True)
        translations = {int(k): v for k, v in
                        _read_json(out_dir / "translation.json", "translator").items()}
        for translation in translations.values():
            spent["usd"] += translation["totals"]["cost_usd"]
        record["translator"] = None  # filled by the shared block below
    else:
        print("[2/7] translator", flush=True)
        translations = {}
        for entry in building:
            translation = translate_trajectory(
                model, entry["trajectory"], family, env=env, client=client)
            translations[entry["seed"]] = translation
            spent["usd"] += translation["totals"]["cost_usd"]
            print(f"  seed {entry['seed']}: {translation['effective_actions']} effective "
                  f"actions, ${translation['totals']['cost_usd']:.6f}", flush=True)
        (out_dir / "translation.json").write_text(
            json.dumps({str(k): v for k, v in translations.items()}, indent=1, default=str))
        checkpoint("translator")
    record["translator"] = {
        "per_trajectory": [
            {"seed": seed, "effective_actions": t["effective_actions"],
             "recorded_actions": t["recorded_actions"],
             "replay_stopped_at": t.get("replay_stopped_at"),
             "totals": t["totals"]}
            for seed, t in sorted(translations.items())
        ],
        "totals": {
            field: sum(t["totals"][field] for t in translations.values())
            for field in ("calls", "prompt_tokens", "cached_tokens",
                          "completion_tokens", "total_tokens")
        },
    }
    record["translator"]["totals"]["cost_usd"] = round(
        sum(t["totals"]["cost_usd"] for t in translations.values()), 8)

    # -- 3. builder, k x artifact ----------------------------------------------
    if "builder" in done:
        print("[3/7] builder: reusing the six recorded artifacts", flush=True)
        builds = {}
        initial = record["builder"]["initial"]
        for k in k_values:
            for artifact in ("code", "doc"):
                key = f"k{k}_{artifact}"
                suffix = "py" if artifact == "code" else "txt"
                path = out_dir / f"artifact_{key}.{suffix}"
                if not path.is_file():
                    raise ResumeError(f"cannot resume builder: {path} is missing")
                usage = dict(initial[key].get("usage") or {
                    f: v for f, v in initial[key].items()
                    if f not in ("k", "artifact", "calls_detail", "usage")})
                text = path.read_text()
                builds[key] = {
                    "k": initial[key]["k"], "artifact": initial[key]["artifact"],
                    "artifact_text": text, "usage": usage,
                    "calls_detail": initial[key].get("calls_detail") or [],
                    "cost_usd": usage.get("cost_usd") or 0.0,
                }
                if artifact == "code":
                    builds[key]["program_source"] = text
                spent["usd"] += builds[key]["cost_usd"]
    else:
        print("[3/7] builder: 6 calls (k=1,2,3 x code,doc)", flush=True)
        annotated = {}
        for entry in building:
            from .compiler import annotate_trajectory
            annotated[entry["seed"]] = annotate_trajectory(
                entry["trajectory"], family, env=env)
        builds = {}
        initial = {}
        for k in k_values:
            entries = [
                {"trajectory": entry["trajectory"],
                 "annotations": annotated[entry["seed"]],
                 "translation": translations.get(entry["seed"]),
                 "label": f"seed {entry['seed']}"}
                for entry in building[:k]
            ]
            for artifact in ("code", "doc"):
                key = f"k{k}_{artifact}"
                result = compile_trajectories(model, entries, family, artifact=artifact,
                                               client=client)
                builds[key] = result
                spent["usd"] += result["cost_usd"]
                initial[key] = {
                    "k": result["k"], "artifact": result["artifact"],
                    "usage": result["usage"], "cost_usd": result["cost_usd"],
                    "attempts": result.get("attempts"),
                    "calls_detail": result.get("calls_detail") or [],
                }
                suffix = "py" if artifact == "code" else "txt"
                (out_dir / f"artifact_{key}.{suffix}").write_text(result["artifact_text"])
                print(f"  {key}: {result['usage'].get('total_tokens')} tok, "
                      f"${result['cost_usd']:.6f}", flush=True)
        record["builder"] = {"initial": initial}
        checkpoint("builder")
    record.setdefault("builder", {})["initial"] = record["builder"].get("initial") or initial

    # -- 4. held-out gate per k -------------------------------------------------
    runner = ProgramRunner(env)
    if "gate_per_k" in done:
        print("[4/7] held-out gate per k: reusing the recorded gate", flush=True)
    else:
        print("[4/7] held-out gate per k", flush=True)
        gate_per_k = {}
        exclude = [families.instance_params(family, seed) for seed in seeds]
        draws = heldout_bindings(family, k=GATE_K, exclude_params=exclude)
        for k in k_values:
            key = f"k{k}_code"
            _module, program = program_from_source(builds[key]["program_source"])
            gate = run_gate(program, family, draws, runner)
            gate_per_k[f"k{k}"] = gate
            print(f"  k={k}: {gate['bindings_passed']}/{gate['bindings_total']}", flush=True)
        record["gate_per_k"] = gate_per_k
        checkpoint("gate_per_k")

    # -- 5. verification with hybrid repair --------------------------------------
    selected_k = max(k_values)
    if "verification" in done:
        print("[5/7] verify + repair: reusing verify.json", flush=True)
        verification = _read_json(out_dir / "verify.json", "verification")
        record["verification"] = verification
        spent["usd"] += (
            verification["totals"]["analyzer"]["cost_usd"]
            + verification["totals"]["resume_episodes"]["cost_usd"]
            + verification["totals"]["builder_refinements"]["cost_usd"]
        )
    else:
        print(f"[5/7] verify + repair on k={selected_k} code", flush=True)
        verification = verify_and_repair(
            model, family, builds[f"k{selected_k}_code"]["program_source"], env,
            seeds=seeds, obs_mode=obs_mode, client=client, out_dir=out_dir,
        )
        spent["usd"] += (
            verification["totals"]["analyzer"]["cost_usd"]
            + verification["totals"]["resume_episodes"]["cost_usd"]
            + verification["totals"]["builder_refinements"]["cost_usd"]
        )
        (out_dir / "verify.json").write_text(json.dumps(verification, indent=1, default=str))
        (out_dir / "verified_program.py").write_text(verification["final_artifact"])
        record["verification"] = verification
        checkpoint("verification")

    builder_selected_usage = dict(builds[f"k{selected_k}_code"]["usage"])
    for field in ("calls", "prompt_tokens", "cached_tokens", "completion_tokens",
                  "total_tokens"):
        builder_selected_usage[field] = (
            (builder_selected_usage.get(field) or 0)
            + (verification["totals"]["builder_refinements"].get(field) or 0)
        )
    builder_selected_usage["cost_usd"] = round(
        (builder_selected_usage.get("cost_usd") or 0.0)
        + (verification["totals"]["builder_refinements"].get("cost_usd") or 0.0), 8)
    record["builder"]["selected_arm"] = {
        "k": selected_k, "artifact": "code",
        "initial_plus_refinements": builder_selected_usage,
    }

    record.setdefault("verification", verification)

    # -- 6. deployment ------------------------------------------------------------
    if "deploy" in done:
        print("[6/7] deploy: reusing the recorded uses", flush=True)
        deploy = _read_json(out_dir / "deploy.json", "deploy") \
            if (out_dir / "deploy.json").is_file() else None
        if deploy is not None:
            spent["usd"] += deploy["total_cost_usd"]
    else:
        deploy = None
        if verification["admitted"]:
            print(f"[6/7] deploy {deploy_n} uses", flush=True)
            _module, program = program_from_source(verification["final_artifact"])
            if client is None:
                from .compiler import _openai_client
                deploy_client = _openai_client()
            else:
                deploy_client = client
            deploy = run_deployment(
                program, family, deploy_uses(family, deploy_n), deploy_client,
                model, runner, role=f"osworld_build_k{selected_k}",
            )
            (out_dir / "deploy.json").write_text(json.dumps(deploy, indent=1, default=str))
            spent["usd"] += deploy["total_cost_usd"]
        else:
            print("[6/7] deploy: skipped (not admitted by the held-out gate)", flush=True)
        record["deploy"] = (
            {
                "n": deploy["n"], "success_count": deploy["success_count"],
                "success_rate": deploy["success_rate"],
                "d_tokens_mean": deploy["d_tokens_mean"],
                "total_tokens": deploy["total_tokens"],
                "cost_usd": deploy["total_cost_usd"],
            }
            if deploy
            else {"skipped": "no version passed the held-out gate"}
        )
        checkpoint("deploy")

    # -- 7. doc arm ----------------------------------------------------------------
    if "doc_arm" in done:
        print("[7/7] doc arm: reusing the recorded episodes", flush=True)
    else:
        print(f"[7/7] doc arm: {len(doc_seeds)} episodes on seeds {list(doc_seeds)}",
              flush=True)
        from .explore import read_final
        from .runner import run_episode

        doc_text = builds[f"k{selected_k}_doc"]["artifact_text"]
        doc_dir = out_dir / "doc_arm"
        doc_episodes = []
        doc_totals = zero()
        for seed in doc_seeds:
            run_episode(
                family=family, condition="doc", seed=seed, model=model,
                obs_mode=obs_mode, max_steps=step_cap(family),
                out_dir=doc_dir / f"s{seed}", client=client, env=env,
                doc_text=doc_text, close_env=False,
            )
            traj = doc_dir / f"s{seed}" / "trajectory.jsonl"
            usage = episode_usage(traj)
            final = read_final(traj)
            usage["unit_floored"] = episode_floor(usage.get("calls_detail") or [])
            doc_episodes.append({"seed": seed, "success": bool(final.get("success")),
                                 "steps": final.get("steps"), **usage})
            for field in ("prompt_tokens", "cached_tokens", "completion_tokens",
                          "total_tokens"):
                doc_totals[field] += usage[field]
            doc_totals["calls"] += usage["model_calls"]
            doc_totals["cost_usd"] = round(doc_totals["cost_usd"] + usage["cost_usd"], 8)
            spent["usd"] += usage["cost_usd"]
        record["doc_arm"] = {
            "doc_from": f"k{selected_k}_doc",
            "doc_chars": len(doc_text),
            "episodes": doc_episodes,
            "success_count": sum(1 for e in doc_episodes if e["success"]),
            "totals": doc_totals,
        }
        checkpoint("doc_arm")

    # -- N* -------------------------------------------------------------------------
    record["break_even"] = break_even(record, model)
    record["total_cost_usd"] = round(spent["usd"], 6)
    record["wall_s"] = round(time.time() - t0, 1)
    (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
    return record


# ------------------------------------------------------------------ break-even


def break_even(record: dict, model: str) -> dict:
    """N* under the priced unit (floored where the ruling says to floor).

    c  = mean FLOORED priced cost of a first-attempt building episode;
    d  = mean priced extraction tokens per deploy use (never floored, single
         calls are fresh in full);
    q  = 1 - deploy success rate;
    s  = (1 - q) c - d;  N* = C / s under marginal (builder only) and
    AutoRPA (exploration + translator + builder + verification) accounting.
    """
    w = weights(model)
    floor_unit = (record.get("floor") or {}).get("floor_unit_mean") or 0

    def ep_unit(usage: dict) -> int:
        return priced_episode(usage.get("calls_detail") or [], model, floor_unit)

    firsts = [e for e in record["exploration"]["instances"][0:0]]  # placeholder
    firsts = []
    for instance in record["exploration"]["instances"]:
        for attempt in instance["attempts"]:
            if attempt["attempt"] == 0:
                firsts.append(attempt)
    c_units = [a.get("unit_floored") if a.get("unit_floored") is not None
               else ep_unit(a["usage"]) for a in firsts]
    c_unit = round(sum(c_units) / len(c_units), 1) if c_units else 0.0

    deploy = record.get("deploy") or {}
    d_unit = None
    q = None
    if deploy and deploy.get("d_tokens_mean") is not None:
        # per-use priced extraction tokens from the recorded per-call details
        units = []
        for use in deploy.get("uses") or []:
            unit = sum(
                price_weighted_call(
                    c.get("prompt_tokens") or 0, c.get("cached_tokens"),
                    c.get("completion_tokens") or 0, w["r_c"], w["r_o"])
                for c in (use.get("calls_detail") or [])
            )
            units.append(unit)
        d_unit = round(sum(units) / len(units), 1) if units else deploy["d_tokens_mean"]
        success_rate = deploy.get("success_rate")
        q = (1.0 - success_rate) if success_rate is not None else None

    def single_stage_priced(totals: dict) -> int:
        return price_weighted_totals(totals, w["r_c"], w["r_o"])

    explore_totals = record["exploration"]["totals"]
    translator_totals = record["translator"]["totals"]
    builder_totals = record["builder"]["selected_arm"]["initial_plus_refinements"]
    verify_analyzer = record["verification"]["totals"]["analyzer"]
    verify_resume = record["verification"]["totals"]["resume_episodes"]
    # resume episodes are agent-side: floored, cold-host applied
    resume_unit = 0
    for rnd in record["verification"].get("rounds") or []:
        calls = ((rnd.get("resume") or {}).get("calls_detail")) or []
        resume_unit += priced_episode(calls, model, floor_unit)
    resume_unit += single_stage_priced(verify_analyzer)

    build_raw = {
        "exploration": explore_totals.get("total_tokens", 0),
        "translator": translator_totals.get("total_tokens", 0),
        "builder_selected": builder_totals.get("total_tokens", 0),
        "verification_analyzer": verify_analyzer.get("total_tokens", 0),
        "verification_resume": verify_resume.get("total_tokens", 0),
        "autorpa_build": (
            explore_totals.get("total_tokens", 0)
            + translator_totals.get("total_tokens", 0)
            + builder_totals.get("total_tokens", 0)
            + verify_analyzer.get("total_tokens", 0)
            + verify_resume.get("total_tokens", 0)
        ),
    }
    exploration_priced = sum(
        ep_unit(a["usage"])
        for instance in record["exploration"]["instances"]
        for a in instance["attempts"]
    ) + single_stage_priced({
        "prompt_tokens": explore_totals["prompt_tokens"]
        - sum(a["usage"]["prompt_tokens"] for i in record["exploration"]["instances"]
              for a in i["attempts"]),
        "cached_tokens": explore_totals["cached_tokens"]
        - sum(a["usage"]["cached_tokens"] for i in record["exploration"]["instances"]
              for a in i["attempts"]),
        "completion_tokens": explore_totals["completion_tokens"]
        - sum(a["usage"]["completion_tokens"] for i in record["exploration"]["instances"]
              for a in i["attempts"]),
    })
    builder_priced = single_stage_priced(builder_totals)
    translator_priced = single_stage_priced(translator_totals)
    autorpa_priced = exploration_priced + translator_priced + builder_priced + resume_unit

    out = {
        "c_unit_floored": c_unit,
        "floor_unit": floor_unit,
        "d_unit": d_unit,
        "q": q,
        "price_weights": w,
        "build_totals_raw": build_raw,
        "build_totals_priced": {
            "exploration_floored": exploration_priced,
            "translator": translator_priced,
            "builder_selected": builder_priced,
            "verification_analyzer_plus_floored_resume": resume_unit,
            "autorpa_build": autorpa_priced,
        },
    }
    if d_unit is None or q is None:
        out["note"] = "no deploy arm: s could not be formed, so N* is undefined"
        return out
    s = (1 - q) * c_unit - d_unit
    out["s_unit"] = round(s, 1)

    def nstar(numerator: float):
        return round(numerator / s, 4) if s and s > 0 else None

    out["nstar"] = {
        "marginal": nstar(builder_priced),
        "autorpa_build": nstar(autorpa_priced),
    }
    return out


# ----------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True, choices=families.FAMILIES)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--k", default="1,2,3")
    parser.add_argument("--deploy-uses", type=int, default=DEPLOY_USES)
    parser.add_argument("--doc-seeds", default="4,5,6")
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-container", action="store_true", default=True)
    args = parser.parse_args()

    seeds = tuple(int(s) for s in args.seeds.split(","))
    k_values = tuple(int(k) for k in args.k.split(","))
    doc_seeds = tuple(int(s) for s in args.doc_seeds.split(","))

    env = guest_env.OSWorldEnv()
    try:
        record = run_build(
            family=args.family, model=args.model, out_dir=args.out, env=env,
            seeds=seeds, k_values=k_values, deploy_n=args.deploy_uses,
            doc_seeds=doc_seeds, obs_mode=args.obs_mode,
            max_cost_usd=args.max_cost_usd, resume=args.resume,
        )
    finally:
        env.close()
        if not args.keep_container:
            env.stop_container()
    print(json.dumps({k: record.get(k) for k in
                      ("total_cost_usd", "wall_s", "break_even")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
