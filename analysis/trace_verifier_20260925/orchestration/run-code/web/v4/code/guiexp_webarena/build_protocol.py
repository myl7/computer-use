"""Driver: the whole AutoRPA-style build for ONE family x ONE model on WebArena.

Stage ladder and build.json schema mirror guiexp_osworld/build_protocol.py
(the approved new-pipeline t-layout; the numbers manifest reads this shape):

  0. floor: 18 no-task probe runs -> floor_unit_mean (priced unit);
  1. exploration: 3 building episodes (seeds 1-3), <= 2 reflection retries;
  2. translator: one call per effective action of each trajectory;
  3. builder: k in {1,2,3} x {code, doc} -- 6 calls, all charged;
  4. per-k held-out gate (5 bindings, sha256 namespace, disjoint);
  5. verify + repair (M=3, from the breakpoint) on the k=3 code artifact;
  6. 30 deploy uses (extraction call + bounded retry + program + checker);
  7. doc arm: 3 episodes (seeds 4-6) on the k=3 doc artifact.

Accounting: the PRICED unit (fresh + r_c x cached + r_o x completion) with
the cold-host imputation applied inside every agent-side episode, the
floor subtracted from agent/document sides only, and d priced from the
recorded per-use extraction calls. cost_usd is OpenRouter's bill and is
never adjusted. Both views recorded in build.json.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import env as env_mod
from .compiler import compile_trajectories
from .cost_ledger import (
    episode_unit,
    price_weighted_call,
    price_weighted_totals,
    weights_for,
    zero,
)
from .deploy_runner import deploy_uses, run_deployment
from .explore import BUILDING_SEEDS, episode_usage, run_exploration, step_cap
from .family import FAMILY, instance_params
from .floor import measure_floor
from .gate_runner import GATE_K, GATE_MIN_PASS, heldout_bindings, run_gate
from .program_runtime import ProgramRunner, program_from_source
from .translator import translate_trajectory
from .verify_runner import verify_and_repair

DEFAULT_K_VALUES = (1, 2, 3)
DEPLOY_USES = 30
DOC_ARM_SEEDS = (4, 5, 6)


class BudgetExceeded(RuntimeError):
    pass


class ResumeError(RuntimeError):
    pass


def _read_json(path: Path, stage: str) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ResumeError(f"cannot resume {stage}: {path} is missing")
    return json.loads(path.read_text())


def resumed_record(out_dir: Path) -> dict | None:
    path = Path(out_dir) / "build.json"
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text())
    except Exception:  # noqa: BLE001 - a truncated checkpoint is no resume base
        return None
    return record if isinstance(record, dict) and record.get("record_type") == "build" else None


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
    floor_runs: int | None = None,
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
        "cache_ratio_r": weights(model)["r_c"],
        "record_type": "build",
    }
    spent = {"usd": 0.0}

    done: list[str] = []
    previous = None
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
        done_list = record.get("stages_done", []) + [stage]
        record["stages_done"] = list(dict.fromkeys(done_list))  # dedupe, keep order
        record["wall_s"] = round(time.time() - t0, 1)
        (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
        if max_cost_usd is not None and spent["usd"] > max_cost_usd:
            raise BudgetExceeded(
                f"spent ${spent['usd']:.4f} after {stage}, cap ${max_cost_usd:.2f}")

    # -- 0. floor ----------------------------------------------------------
    if (out_dir / "floor" / "floor.json").is_file():
        print("[0/7] floor: reusing floor/floor.json", flush=True)
        floor = _read_json(out_dir / "floor" / "floor.json", "floor")
        record["floor"] = floor
    else:
        print("[0/7] floor: 18 no-task probe runs", flush=True)
        floor = measure_floor(
            model, out_dir / "floor", env=env, client=client, family=family,
            runs=floor_runs or 18,
        )
        record["floor"] = floor
        checkpoint("floor")
    floor_unit = floor["floor_unit_mean"]
    spent["usd"] += sum(r.get("cost_usd") or 0.0
                        for r in floor.get("per_run") or [])

    def episode_floor(calls_detail):
        return priced_episode(calls_detail, model, floor_unit)

    # -- 1. exploration ------------------------------------------------------
    if (out_dir / "explore" / "exploration.json").is_file():
        print("[1/7] exploration: reusing explore/exploration.json", flush=True)
        exploration = _read_json(out_dir / "explore" / "exploration.json", "exploration")
        record["exploration"] = exploration
        spent["usd"] += exploration["totals"]["cost_usd"]
    else:
        print(f"[1/7] exploration: seeds {list(seeds)}, step cap {step_cap(family)}",
              flush=True)
        exploration = run_exploration(
            family=family, model=model, out_dir=out_dir / "explore", seeds=seeds,
            obs_mode=obs_mode, client=client, env=env, resume=True,
        )
        spent["usd"] += exploration["totals"]["cost_usd"]
        record["exploration"] = exploration
        checkpoint("exploration")

    building = [
        {"seed": i["seed"], "trajectory": i["best_trajectory"], "success": i["success"],
         "goal": i["goal"]}
        for i in exploration["instances"]
    ]
    # priced, floored per-episode costs (the c figures come from attempt 0)
    for instance in exploration["instances"]:
        for attempt in instance["attempts"]:
            attempt["usage"]["calls_detail"] = attempt["usage"].get("calls_detail") or []
            attempt["unit_floored"] = episode_floor(attempt["usage"]["calls_detail"])

    # -- 2. translator --------------------------------------------------------
    translations = None
    if (out_dir / "translation.json").is_file():
        print("[2/7] translator: reusing translation.json", flush=True)
        raw = _read_json(out_dir / "translation.json", "translator")
        translations = {int(k): v for k, v in raw.items()}
        for translation in translations.values():
            spent["usd"] += translation["totals"]["cost_usd"]
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
    checkpoint("translator")

    # -- 3. builder: k x {code, doc} ------------------------------------------
    entries_all = [
        {
            "trajectory": item["trajectory"],
            "translation": translations.get(item["seed"]),
            "label": f"seed {item['seed']}",
        }
        for item in building
    ]
    goal_texts = {item["seed"]: item["goal"] for item in building}
    builds: dict[str, dict] = {}
    if "builder" in done or (record.get("builder") or {}).get("initial"):
        print("[3/7] builder: reusing the recorded artifacts", flush=True)
        previous_initial = (previous or {}).get("builder", {}).get("initial") \
            or (record.get("builder") or {}).get("initial") or {}
        for k in k_values:
            for artifact in ("code", "doc"):
                key = f"k{k}_{artifact}"
                suffix = "py" if artifact == "code" else "txt"
                path = out_dir / f"artifact_{key}.{suffix}"
                if not path.is_file():
                    raise ResumeError(f"cannot resume builder: {path} is missing")
                builds[key] = dict(previous_initial.get(key) or {})
                builds[key]["artifact_text"] = path.read_text()
        spent["usd"] += sum((v or {}).get("cost_usd") or 0.0
                           for v in previous_initial.values() if isinstance(v, dict))
    else:
        print(f"[3/7] builder: k in {list(k_values)} x code/doc", flush=True)
        for k in k_values:
            for artifact in ("code", "doc"):
                result = compile_trajectories(
                    model, entries_all[:k], family, artifact=artifact, client=client,
                    goal_texts=goal_texts,
                )
                key = f"k{k}_{artifact}"
                builds[key] = result
                spent["usd"] += result["cost_usd"]
                suffix = "py" if artifact == "code" else "txt"
                (out_dir / f"artifact_{key}.{suffix}").write_text(result["artifact_text"])
                print(f"  {key}: {result['usage'].get('total_tokens')} tok, "
                      f"${result['cost_usd']:.6f}", flush=True)
    record["builder"] = {
        "initial": {
            key: {"k": result["k"], "artifact": result["artifact"],
                  "usage": result["usage"], "cost_usd": result["cost_usd"],
                  "attempts": result.get("attempts"),
                  "calls_detail": result.get("calls_detail") or []}
            for key, result in builds.items()
        }
    }
    checkpoint("builder")

    # -- 4. held-out gate per k (code artifacts, pre-repair) -----------------
    runner = ProgramRunner(env)
    if record.get("gate_per_k"):
        print("[4/7] held-out gate per k: reusing the recorded gate", flush=True)
    else:
        print("[4/7] held-out gate per k", flush=True)
        exclude = [instance_params(family, s, env=env) for s in seeds]
        draws = heldout_bindings(family, k=GATE_K, exclude_params=exclude, env=env)
        gates = {}
        for k in k_values:
            source = builds[f"k{k}_code"]["artifact_text"]
            try:
                _module, program = program_from_source(source)
                gate = run_gate(program, family, draws, runner)
            except Exception as exc:  # noqa: BLE001 - a program that will not load is a 0/5
                gate = {"bindings_passed": 0, "bindings_total": len(draws), "detail": [],
                        "load_error": f"{type(exc).__name__}: {exc}"}
            gates[f"k{k}"] = gate
            print(f"  k={k}: {gate['bindings_passed']}/{gate['bindings_total']}", flush=True)
        record["gate_per_k"] = gates
        checkpoint("gate_per_k")

    # -- 5. verification with hybrid repair on k=3 code ------------------------
    selected_k = max(k_values)
    if (out_dir / "verify.json").is_file():
        print("[5/7] verify + repair: reusing verify.json", flush=True)
        verification = _read_json(out_dir / "verify.json", "verification")
        record["verification"] = verification
        spent["usd"] += (
            verification["totals"]["analyzer"]["cost_usd"]
            + verification["totals"]["resume_episodes"]["cost_usd"]
            + verification["totals"]["builder_refinements"]["cost_usd"]
        )
    else:
        print(f"[5/7] verify + repair (M=3) on k={selected_k} code", flush=True)
        verification = verify_and_repair(
            model, family, builds[f"k{selected_k}_code"]["artifact_text"], env,
            seeds=seeds, client=client, obs_mode=obs_mode,
            out_dir=out_dir / "repair",
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
    refine_totals = verification["totals"]["builder_refinements"]
    for field in ("calls", "prompt_tokens", "cached_tokens", "completion_tokens",
                  "total_tokens"):
        builder_selected_usage[field] = (
            (builder_selected_usage.get(field) or 0)
            + (refine_totals.get(field) or 0)
        )
    builder_selected_usage["cost_usd"] = round(
        (builder_selected_usage.get("cost_usd") or 0.0)
        + (refine_totals.get("cost_usd") or 0.0), 8)
    record["builder"]["selected_arm"] = {
        "k": selected_k, "artifact": "code",
        "initial_plus_refinements": builder_selected_usage,
    }
    (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))

    # -- 6. deployment: 30 uses of the verified program -----------------------
    deploy = None
    deploy_progress = out_dir / "deploy_progress.jsonl"
    if (out_dir / "deploy.json").is_file():
        print("[6/7] deploy: reusing the recorded uses", flush=True)
        deploy = _read_json(out_dir / "deploy.json", "deploy")
        spent["usd"] += deploy["total_cost_usd"]
        record["deploy"] = {
            "n": deploy["n"],
            "success_count": deploy["success_count"],
            "success_rate": deploy["success_rate"],
            "d_tokens_mean": deploy["d_tokens_mean"],
            "total_tokens": deploy["total_tokens"],
            "cost_usd": deploy["total_cost_usd"],
            # the per-use extraction calls, so d can be priced exactly
            # (raw token means alone understate the output-priced share)
            "uses": deploy["uses"],
        }
    elif verification["admitted"]:
        print(f"[6/7] deploy {deploy_n} uses", flush=True)
        _module, program = program_from_source(verification["final_artifact"])
        deploy_client = client
        if deploy_client is None:
            from .compiler import _openai_client

            deploy_client = _openai_client()
        deploy = run_deployment(
            program, family, deploy_uses(family, deploy_n, env), deploy_client,
            model, runner, role=f"webarena_build_k{selected_k}",
            progress_path=deploy_progress,
        )
        (out_dir / "deploy.json").write_text(json.dumps(deploy, indent=1, default=str))
        spent["usd"] += deploy["total_cost_usd"]
        record["deploy"] = {
            "n": deploy["n"],
            "success_count": deploy["success_count"],
            "success_rate": deploy["success_rate"],
            "d_tokens_mean": deploy["d_tokens_mean"],
            "total_tokens": deploy["total_tokens"],
            "cost_usd": deploy["total_cost_usd"],
            # the per-use extraction calls, so d can be priced exactly
            # (raw token means alone understate the output-priced share)
            "uses": deploy["uses"],
        }
        checkpoint("deploy")
    else:
        print("[6/7] deploy: skipped (not admitted by the held-out gate)",
              flush=True)
        record["deploy"] = {"skipped": (
            f"no version passed the held-out gate: best "
            f"{verification['gate'].get('bindings_passed')}/{verification['gate'].get('bindings_total')}, "
            f"threshold {GATE_MIN_PASS}/{GATE_K}"
        )}
        checkpoint("deploy")

    # -- 7. doc arm: L_doc on unseen seeds -------------------------------------
    doc_dir = out_dir / "doc_arm"
    if record.get("doc_arm") and len(record["doc_arm"].get("episodes") or []) == len(doc_seeds):
        print("[7/7] doc arm: reusing the recorded episodes", flush=True)
    else:
        print(f"[7/7] doc arm: {len(doc_seeds)} episodes on seeds {list(doc_seeds)}",
              flush=True)
        from .explore import read_final
        from .runner import run_episode

        doc_text = builds[f"k{selected_k}_doc"]["artifact_text"]
        doc_episodes = []
        doc_totals = zero()
        for seed in doc_seeds:
            traj = doc_dir / f"s{seed}" / "trajectory.jsonl"
            usage = final = None
            if traj.is_file():  # a finished episode from an interrupted run
                try:
                    final = read_final(traj)
                    usage = episode_usage(traj)
                except ValueError:
                    usage = final = None
            if usage is None:
                run_episode(
                    family=family, condition="doc", seed=seed, model=model,
                    obs_mode=obs_mode, max_steps=step_cap(family),
                    out_dir=doc_dir / f"s{seed}", client=client, env=env,
                    doc_text=doc_text, close_env=False,
                )
                traj = doc_dir / f"s{seed}" / "trajectory.jsonl"
                final = read_final(traj)
                usage = episode_usage(traj)
            spent["usd"] += usage["cost_usd"]
            usage["calls_detail"] = usage.get("calls_detail") or []
            usage["unit_floored"] = episode_floor(usage["calls_detail"])
            doc_episodes.append({"seed": seed, "success": bool(final.get("success")),
                                 "steps": final.get("steps"), **usage})
            for field in ("prompt_tokens", "cached_tokens", "completion_tokens",
                          "total_tokens"):
                doc_totals[field] += usage[field]
            doc_totals["calls"] += usage["model_calls"]
            doc_totals["cost_usd"] = round(doc_totals["cost_usd"] + usage["cost_usd"], 8)
        record["doc_arm"] = {
            "doc_from": f"k{selected_k}_doc",
            "doc_chars": len(doc_text),
            "episodes": doc_episodes,
            "success_count": sum(1 for e in doc_episodes if e["success"]),
            "L_doc_tokens_mean": (
                doc_totals["total_tokens"] / len(doc_episodes)) if doc_episodes else None,
            "L_doc_unit_floored_mean": (
                sum(e["unit_floored"] for e in doc_episodes) / len(doc_episodes)
            ) if doc_episodes else None,
            "totals": doc_totals,
        }
        checkpoint("doc_arm")

    # -- accounting -------------------------------------------------------------
    record["total_cost_usd"] = round(spent["usd"], 6)
    record["break_even"] = break_even(record, model)
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
    # episodes priced + floored; the reflection calls (single calls) priced
    exploration_priced = sum(
        (a.get("unit_floored") if a.get("unit_floored") is not None
         else ep_unit(a["usage"]))
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
    parser = argparse.ArgumentParser(description="run one WebArena build cell")
    parser.add_argument("--family", default=FAMILY)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--k", default="1,2,3")
    parser.add_argument("--deploy-uses", type=int, default=DEPLOY_USES)
    parser.add_argument("--doc-seeds", default="4,5,6")
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--floor-runs", type=int, default=18)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    env = env_mod.WebArenaEnv()
    client = None
    record = {}
    try:
        from .compiler import _openai_client

        client = _openai_client()
        record = run_build(
            family=args.family, model=args.model, out_dir=args.out, env=env,
            seeds=tuple(int(s) for s in args.seeds.split(",")),
            k_values=tuple(int(k) for k in args.k.split(",")),
            deploy_n=args.deploy_uses,
            doc_seeds=tuple(int(s) for s in args.doc_seeds.split(",")),
            obs_mode=args.obs_mode,
            client=client, max_cost_usd=args.max_cost_usd,
            resume=args.resume, floor_runs=args.floor_runs,
        )
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}")
        return 2
    finally:
        env.close()
    print(json.dumps(record.get("break_even") or {}, indent=1))
    print(f"wrote {Path(args.out) / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())