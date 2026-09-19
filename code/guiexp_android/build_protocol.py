"""Driver: the whole AutoRPA-style build for ONE family and ONE model.

Stages, in the order they run (docs/autorpa-replication-protocol.md section 5):

  1. exploration, 3 building episodes (seeds 1, 2, 3) with up to 2 reflection
     retries each, per-family step cap ceil(10 x complexity) capped at 50;
  2. translator, one call per effective action of each building trajectory;
  3. builder, for k = 1, 2, 3 building trajectories and for both artifacts
     ("code", a parameterized program; "doc", a plain-text operation
     document). All six calls are charged as C;
  4. our held-out gate on each k's code artifact, 5 unseen bindings each;
  5. verification with hybrid repair (M = 3) on the k = 3 code artifact,
     with the held-out gate run on every version the loop produces (the
     initial program and each refinement); the best gate-passing version is
     what the stage returns;
  6. 30 deploy uses of the verified program, giving d and q, run iff that
     version was admitted (at least GATE_MIN_PASS of GATE_K held-out
     bindings). The AutoRPA loop's ``unautomatable`` verdict is recorded but
     decides nothing;
  7. 3 doc-arm episodes on unseen bindings, giving L_doc.

The output ``build.json`` carries every stage total in raw prompt / cached /
completion tokens, so the cache-adjusted unit (fresh + r x cached +
completion, r = 0.20 GLM, 0.318 DeepSeek; docs/cache-adjusted-accounting.md)
can be recomputed later without re-running anything, and N* under two
accountings:

    marginal        C_builder / s          (our accounting: the compile call
                                            is the only extra cost against a
                                            reactive run that happened anyway)
    autorpa_build   (exploration + translator + builder + verification) / s

with s = (1 - q) c - d.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.build_protocol \
        --family ContactsAddContact --model z-ai/glm-5.3-flash \
        --out experimental-results/guiexp_android/t16_build/z-ai_glm-5.3-flash/ContactsAddContact
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import android_env
from .accounting_check import warn_if_unaccountable
from .compiler import compile_trajectories
from .deploy_runner import deploy_uses, run_deployment
from .explore import BUILDING_SEEDS, episode_usage, run_exploration, step_cap
from .gate_runner import GATE_K, GATE_MIN_PASS, heldout_bindings, run_gate
from .program_runtime import ProgramRunner, program_from_source
from .translator import translate_trajectory
from .verify_runner import verify_and_repair

CACHE_RATIO = {  # r = cache-read price / input price
    "z-ai/glm-5.3-flash": 0.20,
    "z-ai/glm-5v-turbo": 0.20,
    "deepseek/deepseek-v4-flash-vision-exp": 0.318,
}
DEFAULT_K_VALUES = (1, 2, 3)
DEPLOY_USES = 30
DOC_ARM_SEEDS = (4, 5, 6)  # unseen: 1-3 built, 0 is the AutoRPA test instance

# The batch registry: the families the full build iterates over, in the order
# it runs them (cheapest step cap first, so a budget stop loses the least).
# ``conditions.FAMILIES`` is the authority on which names exist; this tuple
# only fixes the batch's order and is checked against it at import time.
BATCH_FAMILIES = (
    "MarkorDeleteNote",
    "ContactsAddContact",
    "OsmAndFavorite",
    "MarkorCreateNote",
    "OsmAndMarker",
    "FilesMoveFile",
    "SimpleCalendarAddOneEvent",
)
BATCH_MODELS = ("z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp")


def batch_cells(families: tuple = BATCH_FAMILIES, models: tuple = BATCH_MODELS) -> list[tuple]:
    """Every (family, model, step_cap) cell of the full batch."""
    return [(family, model, step_cap(family)) for model in models for family in families]


def _check_batch_registry() -> None:
    """The batch must name every family and no other; drift is a bug."""
    from .conditions import FAMILIES

    missing = set(FAMILIES) - set(BATCH_FAMILIES)
    extra = set(BATCH_FAMILIES) - set(FAMILIES)
    if missing or extra:
        raise RuntimeError(
            f"BATCH_FAMILIES is out of step with conditions.FAMILIES: "
            f"missing {sorted(missing)}, unknown {sorted(extra)}"
        )


_check_batch_registry()


class BudgetExceeded(RuntimeError):
    """The run's spend cap was reached; stages already done are still written."""


class ResumeError(RuntimeError):
    """A resumed run cannot rebuild a stage that build.json calls done."""


# ------------------------------------------------------------------- resume
#
# A killed cell is restarted with --resume: every stage already in
# ``stages_done`` is skipped and the objects the later stages consume are read
# back off disk, so the model-token stages are never paid for twice. Each
# reader below rebuilds exactly what the pipeline goes on to use.
#
#   exploration   explore/exploration.json   -> the instances, hence the
#                                               building trajectories
#   translator    translation.json           -> {seed: translation}
#   builder       artifact_k{k}_{code,doc}.{py,txt} plus build.json's
#                 builder.initial            -> the six artifacts and usages
#   gate_per_k    nothing later reads it     -> skipped outright
#   verification  verify.json                -> the whole verification record
#   deploy        deploy.json                -> the deployment summary
#   doc_arm       nothing later reads it     -> skipped outright
#
# The stage's own contribution to the run's cost is re-added from the same
# numbers the stage itself added, so the spend cap and total_cost_usd of a
# resumed run match a run that never stopped.

_STAGE_RECORD_KEY = {
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
    """The build.json a previous run of this cell left behind, if any."""
    path = Path(out_dir) / "build.json"
    return json.loads(path.read_text()) if path.is_file() else None


def resumed_exploration(out_dir: Path | str) -> dict:
    """The exploration stage record, exactly as run_exploration returned it."""
    return _read_json(Path(out_dir) / "explore" / "exploration.json", "exploration")


def resumed_translations(out_dir: Path | str) -> dict:
    """{seed: translation}, re-keyed by int seed as the translator stage keys it."""
    raw = _read_json(Path(out_dir) / "translation.json", "translator")
    return {int(seed): value for seed, value in raw.items()}


def resumed_builds(record: dict, out_dir: Path | str, k_values=DEFAULT_K_VALUES) -> dict:
    """The six builder results: artifact text off disk, usage out of build.json.

    Rebuilt are the fields the later stages read (``artifact_text``, ``usage``,
    ``cost_usd``, ``k``, ``artifact``, and ``program_source`` for the code
    arm). The transport bookkeeping of the original call -- ``attempts`` and
    ``api_failures`` -- is not written anywhere and no later stage reads it.
    """
    out_dir = Path(out_dir)
    initial = ((record.get("builder") or {}).get("initial")) or {}
    builds: dict[str, dict] = {}
    for k in k_values:
        for artifact in ("code", "doc"):
            key = f"k{k}_{artifact}"
            if key not in initial:
                raise ResumeError(
                    f"cannot resume builder: build.json has no builder.initial.{key}"
                )
            suffix = "py" if artifact == "code" else "txt"
            path = out_dir / f"artifact_{key}.{suffix}"
            if not path.is_file():
                raise ResumeError(f"cannot resume builder: {path} is missing")
            usage = {
                f: v for f, v in initial[key].items()
                if f not in ("k", "artifact", "calls_detail")
            }
            text = path.read_text()
            builds[key] = {
                "k": initial[key]["k"],
                "artifact": initial[key]["artifact"],
                "artifact_text": text,
                "usage": usage,
                "calls_detail": initial[key].get("calls_detail") or [],
                "cost_usd": usage.get("cost_usd") or 0.0,
            }
            if artifact == "code":
                builds[key]["program_source"] = text
    return builds


def gate_pass_rate(gate: dict | None) -> float | None:
    """passed / total, the p_after the deployment decision is made on."""
    if not gate or not gate.get("bindings_total"):
        return None
    return round(gate["bindings_passed"] / gate["bindings_total"], 4)


def gate_summary(gate: dict | None) -> str:
    if not gate:
        return "no gate"
    return f"{gate.get('bindings_passed')}/{gate.get('bindings_total')}"


def resumed_verification(out_dir: Path | str) -> dict:
    """The verification record, exactly as verify_and_repair returned it.

    A verify.json written before the gate became the admission criterion
    carries no ``admitted`` field, and carries ``gate: null`` whenever the
    AutoRPA loop gave up. Both are filled in here from what is on disk, so a
    resumed run reaches the same deployment decision a fresh one would: the
    recorded gate decides, and an absent gate is not an admission.
    """
    record = _read_json(Path(out_dir) / "verify.json", "verification")
    gate = record.get("gate")
    if "admitted" not in record:
        passed = (gate or {}).get("bindings_passed")
        record["admitted"] = passed is not None and passed >= GATE_MIN_PASS
    record.setdefault("gate_min_pass", GATE_MIN_PASS)
    if not record.get("gate_history"):
        record["gate_history"] = (
            [{"version": record.get("admitted_version") or "final",
              "passed": gate["bindings_passed"], "total": gate["bindings_total"]}]
            if gate else []
        )
    record.setdefault("admitted_version", record["gate_history"][-1]["version"]
                      if record["gate_history"] else None)
    return record


def resumed_deploy(out_dir: Path | str) -> dict | None:
    """The deployment summary, or None when the type was unautomatable."""
    path = Path(out_dir) / "deploy.json"
    return json.loads(path.read_text()) if path.is_file() else None


# ------------------------------------------------------------------ totals


def zero() -> dict:
    return {
        "calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
        "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
    }


def add(totals: dict, usage: dict, calls: int = 1) -> dict:
    totals["calls"] += calls
    totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
    totals["cached_tokens"] += usage.get("cached_tokens") or 0
    totals["completion_tokens"] += usage.get("completion_tokens") or 0
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = round(totals["cost_usd"] + (usage.get("cost_usd") or 0.0), 8)
    return totals


def merge(*totals: dict) -> dict:
    out = zero()
    for item in totals:
        out["calls"] += item.get("calls", 0)
        out["prompt_tokens"] += item.get("prompt_tokens", 0)
        out["cached_tokens"] += item.get("cached_tokens", 0)
        out["completion_tokens"] += item.get("completion_tokens", 0)
        out["cost_usd"] = round(out["cost_usd"] + item.get("cost_usd", 0.0), 8)
    out["total_tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    return out


def cache_adjusted(totals: dict, r: float) -> int:
    """fresh + r x cached + completion (docs/cache-adjusted-accounting.md)."""
    cached = totals.get("cached_tokens") or 0
    fresh = (totals.get("prompt_tokens") or 0) - cached
    return int(round(fresh + r * cached + (totals.get("completion_tokens") or 0)))


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
    reuse_grid: bool = True,
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
        "cache_ratio_r": CACHE_RATIO.get(model),
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
                    f"not {family} / {model}"
                )
            for key, value in previous.items():
                record.setdefault(key, value)
            done = list(previous.get("stages_done") or [])
            for stage in done:
                section = _STAGE_RECORD_KEY.get(stage)
                if section and section not in record:
                    raise ResumeError(
                        f"build.json lists {stage} as done but carries no "
                        f"{section!r} section"
                    )
            record["stages_done"] = list(done)
            record["resumed_from_stages"] = list(done)
            print(f"resume: reusing {len(done)} recorded stage(s): {done}", flush=True)

    def checkpoint(stage: str) -> None:
        """Persist what is done and stop if the spend cap has been reached."""
        record["stages_done"] = record.get("stages_done", []) + [stage]
        record["wall_s"] = round(time.time() - t0, 1)
        (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
        if max_cost_usd is not None and spent["usd"] > max_cost_usd:
            raise BudgetExceeded(
                f"spent ${spent['usd']:.4f} after {stage}, cap ${max_cost_usd:.2f}"
            )

    # -- 1. exploration ---------------------------------------------------
    if "exploration" in done:
        print("[1/7] exploration: reusing explore/exploration.json", flush=True)
        exploration = resumed_exploration(out_dir)
        spent["usd"] += exploration["totals"]["cost_usd"]
    else:
        exploration = _run_exploration_stage(
            record, spent, checkpoint, family, model, out_dir, env, seeds,
            obs_mode, client, reuse_grid,
        )

    building = [
        {"seed": i["seed"], "trajectory": i["best_trajectory"], "success": i["success"]}
        for i in exploration["instances"]
    ]

    # -- 2. translator ----------------------------------------------------
    if "translator" in done:
        print("[2/7] translator: reusing translation.json", flush=True)
        translations = resumed_translations(out_dir)
        for translation in translations.values():
            spent["usd"] += translation["totals"]["cost_usd"]
    else:
        translations = _run_translator_stage(
            record, spent, checkpoint, family, model, out_dir, env, building, client,
        )

    # -- 3. builder, k in {1,2,3} x {code, doc} ---------------------------
    if "builder" in done:
        print("[3/7] builder: reusing the six recorded artifacts", flush=True)
        builds = resumed_builds(record, out_dir, k_values)
        for result in builds.values():
            spent["usd"] += result["cost_usd"]
    else:
        builds = _run_builder_stage(
            record, spent, checkpoint, family, model, out_dir, building,
            translations, k_values, client,
        )

    # -- 4. held-out gate per k (code artifacts, pre-repair) --------------
    runner = ProgramRunner(env)
    if "gate_per_k" in done:
        print("[4/7] held-out gate per k: reusing the recorded gate", flush=True)
    else:
        _run_gate_stage(record, checkpoint, family, seeds, builds, k_values, runner)

    # -- 5. verification with hybrid repair, on the largest k code artifact
    selected_k = max(k_values)
    if "verification" in done:
        print("[5/7] verify + repair: reusing verify.json", flush=True)
        verification = resumed_verification(out_dir)
        verify_totals = merge(
            verification["totals"]["analyzer"],
            verification["totals"]["resume_episodes"],
        )
        spent["usd"] += (
            verify_totals["cost_usd"]
            + verification["totals"]["builder_refinements"]["cost_usd"]
        )
        # A build.json written before the gate became the admission criterion
        # carries no verdict in its verification section; fill it from the
        # verify.json the reader just normalized.
        section = record.get("verification") or {}
        section.setdefault("admitted", verification["admitted"])
        section.setdefault("admitted_version", verification.get("admitted_version"))
        section.setdefault("gate_min_pass", verification["gate_min_pass"])
        section.setdefault("gate_history", verification["gate_history"])
        section.setdefault("p_after", gate_pass_rate(verification["gate"]))
        record["verification"] = section
    else:
        verification = _run_verification_stage(
            record, spent, checkpoint, family, model, out_dir, env, seeds,
            obs_mode, builds, selected_k, client,
        )

    builder_selected = merge(
        builds[f"k{selected_k}_code"]["usage"],
        verification["totals"]["builder_refinements"],
    )
    record["builder"]["selected_arm"] = {
        "k": selected_k,
        "artifact": "code",
        "initial_plus_refinements": builder_selected,
    }

    # -- 6. deployment: 30 uses of the verified program -------------------
    if "deploy" in done:
        print("[6/7] deploy: reusing the recorded uses", flush=True)
        deploy = resumed_deploy(out_dir)
        if deploy is not None:
            spent["usd"] += deploy["total_cost_usd"]
    else:
        _run_deploy_stage(
            record, spent, checkpoint, family, model, out_dir, verification,
            deploy_n, selected_k, runner, client,
        )

    # -- 7. doc arm: L_doc on unseen bindings -----------------------------
    if "doc_arm" in done:
        print("[7/7] doc arm: reusing the recorded episodes", flush=True)
        for episode in (record.get("doc_arm") or {}).get("episodes") or []:
            spent["usd"] += episode.get("cost_usd") or 0.0
    else:
        _run_doc_arm_stage(
            record, spent, checkpoint, family, model, out_dir, env, obs_mode,
            builds, selected_k, doc_seeds, client,
        )

    record["break_even"] = break_even(record, model)
    record["total_cost_usd"] = round(spent["usd"], 6)
    record["wall_s"] = round(time.time() - t0, 1)
    (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
    # A stage charged for model calls but holding only their total cannot have
    # its cache-adjusted unit recomputed later. Warn loudly; never throw away a
    # finished run over it.
    record["accounting_problems"] = warn_if_unaccountable(out_dir)
    (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
    return record


# ------------------------------------------------------------- the stages
#
# One function per stage, each doing exactly what the inline stage did: run
# the work, add its spend, write its artifacts, fill its section of the
# record, and checkpoint. A resumed run calls none of them for a stage
# already in ``stages_done`` and reads that stage's artifacts back instead.


def _run_exploration_stage(
    record, spent, checkpoint, family, model, out_dir, env, seeds, obs_mode,
    client, reuse_grid,
) -> dict:
    print(f"[1/7] exploration: seeds {list(seeds)}, step cap {step_cap(family)}", flush=True)
    exploration = run_exploration(
        family=family, model=model, out_dir=out_dir / "explore", seeds=seeds,
        obs_mode=obs_mode, client=client, env=env, keep_emulator=True,
        reuse_grid=reuse_grid,
    )
    explore_totals = merge(exploration["totals"])
    explore_totals["calls"] = sum(
        a["usage"]["model_calls"] for i in exploration["instances"] for a in i["attempts"]
    ) + exploration["totals"]["reflection_calls"]
    spent["usd"] += exploration["totals"]["cost_usd"]
    record["exploration"] = {
        "episodes": exploration["totals"]["episodes"],
        "retry_episodes": exploration["totals"]["retry_episodes"],
        "reused_episodes": exploration["totals"]["reused_episodes"],
        "reflection_calls": exploration["totals"]["reflection_calls"],
        "per_episode": [
            {
                "seed": i["seed"], "attempt": a["attempt"], "reused": a["reused"],
                "success": a["success"], "steps": a["steps"], **a["usage"],
            }
            for i in exploration["instances"] for a in i["attempts"]
        ],
        "reflections": [
            {"seed": i["seed"], "for_attempt": r["for_attempt"], **r["usage"]}
            for i in exploration["instances"] for r in i["reflections"]
        ],
        "totals": explore_totals,
    }
    checkpoint("exploration")
    return exploration


def _run_translator_stage(
    record, spent, checkpoint, family, model, out_dir, env, building, client,
) -> dict:
    print(f"[2/7] translator over {len(building)} building trajectories", flush=True)
    translations = {}
    translator_totals = zero()
    for item in building:
        result = translate_trajectory(
            model, item["trajectory"], family, client=client, env=env
        )
        translations[item["seed"]] = result
        add(translator_totals, result["totals"], calls=result["totals"]["calls"])
        spent["usd"] += result["totals"]["cost_usd"]
    (out_dir / "translation.json").write_text(
        json.dumps({str(k): v for k, v in translations.items()}, indent=1, default=str)
    )
    record["translator"] = {
        "per_trajectory": [
            {"seed": seed, "effective_actions": t["effective_actions"], **t["totals"]}
            for seed, t in translations.items()
        ],
        "totals": translator_totals,
    }
    checkpoint("translator")
    return translations


def _run_builder_stage(
    record, spent, checkpoint, family, model, out_dir, building, translations,
    k_values, client,
) -> dict:
    print(f"[3/7] builder: k in {list(k_values)} x code/doc", flush=True)
    entries_all = [
        {
            "trajectory": item["trajectory"],
            "translation": translations.get(item["seed"]),
            "label": f"seed {item['seed']}",
        }
        for item in building
    ]
    builds: dict[str, dict] = {}
    builder_totals = zero()
    for k in k_values:
        for artifact in ("code", "doc"):
            result = compile_trajectories(
                model, entries_all[:k], family, artifact=artifact, client=client
            )
            key = f"k{k}_{artifact}"
            builds[key] = result
            add(builder_totals, result["usage"])
            spent["usd"] += result["cost_usd"]
            suffix = "py" if artifact == "code" else "txt"
            (out_dir / f"artifact_{key}.{suffix}").write_text(result["artifact_text"])
            print(f"    {key}: {result['usage'].get('prompt_tokens')} + "
                  f"{result['usage'].get('completion_tokens')} tok", flush=True)
    record["builder"] = {
        "initial": {
            key: {"k": result["k"], "artifact": result["artifact"], **result["usage"],
                  "calls_detail": result.get("calls_detail") or []}
            for key, result in builds.items()
        },
        "totals_all_calls": dict(builder_totals),
    }
    checkpoint("builder")
    return builds


def _run_gate_stage(record, checkpoint, family, seeds, builds, k_values, runner) -> dict:
    print("[4/7] held-out gate per k", flush=True)
    instance0 = android_env.instance_params(family, seeds[0])
    draws = heldout_bindings(family, k=5, exclude_params=instance0)
    gates: dict[str, dict] = {}
    for k in k_values:
        source = builds[f"k{k}_code"]["artifact_text"]
        try:
            _module, program = program_from_source(source)
            gate = run_gate(program, family, draws, runner)
        except Exception as exc:  # noqa: BLE001 - a program that will not load is a 0/5
            gate = {"bindings_passed": 0, "bindings_total": len(draws),
                    "detail": [], "load_error": f"{type(exc).__name__}: {exc}"}
        gates[f"k{k}"] = gate
        print(f"    k={k}: {gate['bindings_passed']}/{gate['bindings_total']}", flush=True)
    record["gate_per_k"] = gates
    checkpoint("gate_per_k")
    return gates


def _run_verification_stage(
    record, spent, checkpoint, family, model, out_dir, env, seeds, obs_mode,
    builds, selected_k, client,
) -> dict:
    print(f"[5/7] verify + repair (M=3) on k={selected_k} code", flush=True)
    verification = verify_and_repair(
        model, family, builds[f"k{selected_k}_code"]["artifact_text"], env,
        seeds=seeds, client=client, obs_mode=obs_mode, artifact="code",
        out_dir=out_dir / "repair",
    )
    (out_dir / "verify.json").write_text(json.dumps(verification, indent=1, default=str))
    (out_dir / "verified_program.py").write_text(verification["final_artifact"])
    verify_totals = merge(
        verification["totals"]["analyzer"],
        verification["totals"]["resume_episodes"],
    )
    spent["usd"] += verify_totals["cost_usd"] + verification["totals"]["builder_refinements"]["cost_usd"]
    record["verification"] = {
        "selected_k": selected_k,
        "refinements": verification["refinements"],
        "program_replays": verification["replays"],
        "unautomatable": verification["unautomatable"],
        "admitted": verification["admitted"],
        "admitted_version": verification.get("admitted_version"),
        "gate_min_pass": verification.get("gate_min_pass", GATE_MIN_PASS),
        "gate_history": verification.get("gate_history") or [],
        "analyzer": verification["totals"]["analyzer"],
        "resume_episodes": verification["totals"]["resume_episodes"],
        "builder_refinements": verification["totals"]["builder_refinements"],
        "gate_after_repair": verification["gate"],
        "p_after": gate_pass_rate(verification["gate"]),
        "test_seed0": verification["test_seed0"],
        "totals_excluding_refinements": verify_totals,
    }
    checkpoint("verification")
    return verification


def _run_deploy_stage(
    record, spent, checkpoint, family, model, out_dir, verification, deploy_n,
    selected_k, runner, client,
) -> dict | None:
    deploy = None
    if verification["admitted"]:
        print(f"[6/7] deploy {deploy_n} uses", flush=True)
        _module, program = program_from_source(verification["final_artifact"])
        if client is None:  # the extraction chain needs a real client object
            from .compiler import _openai_client

            deploy_client = _openai_client()
        else:
            deploy_client = client
        deploy = run_deployment(
            program, family, deploy_uses(family, deploy_n), deploy_client, model, runner,
            role=f"t16_build_k{selected_k}",
        )
        (out_dir / "deploy.json").write_text(json.dumps(deploy, indent=1, default=str))
        spent["usd"] += deploy["total_cost_usd"]
    record["deploy"] = (
        {
            "n": deploy["n"],
            "success_count": deploy["success_count"],
            "success_rate": deploy["success_rate"],
            "d_tokens_mean": deploy["d_tokens_mean"],
            "total_tokens": deploy["total_tokens"],
            "cost_usd": deploy["total_cost_usd"],
        }
        if deploy
        else {"skipped": (
            f"no version passed the held-out gate: best "
            f"{gate_summary(verification['gate'])}, threshold "
            f"{verification.get('gate_min_pass', GATE_MIN_PASS)}/{GATE_K}"
        )}
    )
    checkpoint("deploy")
    return deploy


def _run_doc_arm_stage(
    record, spent, checkpoint, family, model, out_dir, env, obs_mode, builds,
    selected_k, doc_seeds, client,
) -> dict:
    print(f"[7/7] doc arm: {len(doc_seeds)} episodes on seeds {list(doc_seeds)}", flush=True)
    from .runner import run_episode

    doc_text = builds[f"k{selected_k}_doc"]["artifact_text"]
    doc_dir = out_dir / "doc_arm"
    doc_episodes = []
    doc_totals = zero()
    for seed in doc_seeds:
        run_episode(
            family=family, condition="doc", seed=seed, model=model, obs_mode=obs_mode,
            max_steps=step_cap(family), out_dir=doc_dir / f"s{seed}", client=client,
            env=env, keep_emulator=True, doc_text=doc_text, close_env=False,
        )
        traj = doc_dir / f"s{seed}" / "trajectory.jsonl"
        usage = episode_usage(traj)
        from .explore import read_final

        final = read_final(traj)
        doc_episodes.append({"seed": seed, "success": bool(final.get("success")),
                             "steps": final.get("steps"), **usage})
        add(doc_totals, usage, calls=usage["model_calls"])
        spent["usd"] += usage["cost_usd"]
    record["doc_arm"] = {
        "doc_from": f"k{selected_k}_doc",
        "doc_chars": len(doc_text),
        "episodes": doc_episodes,
        "success_count": sum(1 for e in doc_episodes if e["success"]),
        "totals": doc_totals,
        "L_doc_tokens_mean": (doc_totals["total_tokens"] / len(doc_episodes)) if doc_episodes else None,
    }
    checkpoint("doc_arm")
    return record["doc_arm"]


# --------------------------------------------------------------- redeploy
#
# A cell that finished under the old rule -- deploy iff the AutoRPA loop did
# not give up -- can have its deployment redone from any artifact the cell
# already wrote, without paying for a single stage again. The artifact is
# gated on the same five held-out bindings the gate stage drew, and the
# deploy stage runs only if it is admitted. Nothing but ``deploy`` and the
# ``redeploy`` note is written back into build.json.


def redeploy_from_artifact(
    family: str,
    model: str,
    out_dir: Path | str,
    env,
    artifact: str,
    deploy_n: int = DEPLOY_USES,
    seeds=BUILDING_SEEDS,
    client=None,
    doc_arm: bool = False,
    obs_mode: str = "screenshot+ax",
    doc_seeds=DOC_ARM_SEEDS,
) -> dict:
    """Gate one existing artifact and, if admitted, redo the deploy stage.

    ``artifact`` is a path, absolute or relative to the cell directory (so
    ``artifact_k3_code.py`` names the cell's own k=3 builder output). The
    gate draws are ``heldout_bindings(family, k=GATE_K, exclude_params=
    instance_params(family, seeds[0]))``, exactly what stage 4 and the
    verification stage use, so the number is comparable with the ones
    already in build.json.
    """
    out_dir = Path(out_dir)
    record = resumed_record(out_dir)
    if record is None:
        raise ResumeError(f"cannot redeploy: {out_dir / 'build.json'} is missing")
    if record.get("family") != family or record.get("model") != model:
        raise ResumeError(
            f"{out_dir / 'build.json'} records a build of {record.get('family')} / "
            f"{record.get('model')}, not {family} / {model}"
        )
    source_path = Path(artifact)
    if not source_path.is_absolute():
        source_path = out_dir / artifact
    if not source_path.is_file():
        raise ResumeError(f"cannot redeploy: {source_path} is missing")
    source = source_path.read_text()

    runner = ProgramRunner(env)
    draws = heldout_bindings(
        family, k=GATE_K, exclude_params=android_env.instance_params(family, seeds[0])
    )
    try:
        _module, program = program_from_source(source)
        gate = run_gate(program, family, draws, runner)
    except Exception as exc:  # noqa: BLE001 - a program that will not load is a 0/5
        program = None
        gate = {"bindings_passed": 0, "bindings_total": len(draws), "detail": [],
                "load_error": f"{type(exc).__name__}: {exc}"}
    admitted = gate["bindings_passed"] >= GATE_MIN_PASS
    print(f"redeploy gate: {gate_summary(gate)} "
          f"(threshold {GATE_MIN_PASS}/{GATE_K}), admitted: {admitted}", flush=True)

    selected_k = (record.get("verification") or {}).get("selected_k") or max(DEFAULT_K_VALUES)
    note = {
        "source_artifact": str(source_path),
        "gate": gate,
        "p_after": gate_pass_rate(gate),
        "admitted": admitted,
        "gate_min_pass": GATE_MIN_PASS,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "deploy_uses": deploy_n,
        "doc_arm": doc_arm,
    }
    spent = {"usd": 0.0}
    deploy = None
    if admitted:
        print(f"redeploy: {deploy_n} uses", flush=True)
        if client is None:
            from .compiler import _openai_client

            deploy_client = _openai_client()
        else:
            deploy_client = client
        deploy = run_deployment(
            program, family, deploy_uses(family, deploy_n), deploy_client, model,
            runner, role=f"t16_build_redeploy_k{selected_k}",
        )
        previous = out_dir / "deploy.json"
        if previous.is_file():  # never lose the uses the old decision produced
            previous.replace(out_dir / "deploy_before_redeploy.json")
        previous.write_text(json.dumps(deploy, indent=1, default=str))
        spent["usd"] += deploy["total_cost_usd"]
        record["deploy"] = {
            "n": deploy["n"],
            "success_count": deploy["success_count"],
            "success_rate": deploy["success_rate"],
            "d_tokens_mean": deploy["d_tokens_mean"],
            "total_tokens": deploy["total_tokens"],
            "cost_usd": deploy["total_cost_usd"],
        }
    else:
        record["deploy"] = {"skipped": (
            f"no version passed the held-out gate: best {gate_summary(gate)}, "
            f"threshold {GATE_MIN_PASS}/{GATE_K}"
        )}

    if doc_arm:
        def _noop(_stage: str) -> None:
            return None

        builds = {f"k{selected_k}_doc": {
            "artifact_text": (out_dir / f"artifact_k{selected_k}_doc.txt").read_text()
        }}
        _run_doc_arm_stage(
            record, spent, _noop, family, model, out_dir, env, obs_mode,
            builds, selected_k, doc_seeds, client,
        )

    record["redeploy"] = note
    if all(k in record for k in ("exploration", "translator", "builder", "verification")):
        record["break_even"] = break_even(record, model)
    record["total_cost_usd"] = round(
        (record.get("total_cost_usd") or 0.0) + spent["usd"], 6
    )
    (out_dir / "build.json").write_text(json.dumps(record, indent=1, default=str))
    print(f"wrote {out_dir / 'build.json'}")
    return record


# ------------------------------------------------------------- break-even


def break_even(record: dict, model: str) -> dict:
    """N* under both accountings, in raw and cache-adjusted tokens.

    c is the mean cost of a first-attempt building episode (a reactive
    discover run under the family's step cap). d and q come from the 30
    deploy uses. s = (1 - q) c - d.
    """
    r = CACHE_RATIO.get(model)
    firsts = [e for e in record["exploration"]["per_episode"] if e["attempt"] == 0]
    c_raw = sum(e["total_tokens"] for e in firsts) / len(firsts) if firsts else 0.0
    c_adj = (
        sum(cache_adjusted(e, r) for e in firsts) / len(firsts)
        if firsts and r is not None else None
    )
    deploy = record.get("deploy") or {}
    d = deploy.get("d_tokens_mean")
    success_rate = deploy.get("success_rate")
    q = (1.0 - success_rate) if success_rate is not None else None

    explore = record["exploration"]["totals"]
    translator = record["translator"]["totals"]
    builder = record["builder"]["selected_arm"]["initial_plus_refinements"]
    verify = record["verification"]["totals_excluding_refinements"]
    autorpa_build = merge(explore, translator, builder, verify)

    def nstar(numerator: int | float, s: float | None):
        if not s or s <= 0:
            return None
        return round(numerator / s, 4)

    out = {
        "c_raw_tokens": round(c_raw, 1),
        "c_cache_adjusted_tokens": round(c_adj, 1) if c_adj is not None else None,
        "d_tokens": d,
        "q": q,
        "cache_ratio_r": r,
        "build_totals_raw": {
            "exploration": explore["total_tokens"],
            "translator": translator["total_tokens"],
            "builder_selected": builder["total_tokens"],
            "verification": verify["total_tokens"],
            "autorpa_build": autorpa_build["total_tokens"],
        },
    }
    if r is not None:
        out["build_totals_cache_adjusted"] = {
            "exploration": cache_adjusted(explore, r),
            "translator": cache_adjusted(translator, r),
            "builder_selected": cache_adjusted(builder, r),
            "verification": cache_adjusted(verify, r),
            "autorpa_build": cache_adjusted(autorpa_build, r),
        }
    if d is None or q is None:
        out["note"] = "no deploy arm: s could not be formed, so N* is undefined"
        return out

    s_raw = (1 - q) * c_raw - d
    out["s_raw"] = round(s_raw, 1)
    out["nstar_raw"] = {
        "marginal": nstar(builder["total_tokens"], s_raw),
        "autorpa_build": nstar(autorpa_build["total_tokens"], s_raw),
    }
    if r is not None and c_adj is not None:
        s_adj = (1 - q) * c_adj - d
        out["s_cache_adjusted"] = round(s_adj, 1)
        out["nstar_cache_adjusted"] = {
            "marginal": nstar(cache_adjusted(builder, r), s_adj),
            "autorpa_build": nstar(cache_adjusted(autorpa_build, r), s_adj),
        }
    return out


# --------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--k", default="1,2,3", help="builder k values")
    parser.add_argument("--deploy-uses", type=int, default=DEPLOY_USES)
    parser.add_argument("--doc-seeds", default="4,5,6")
    parser.add_argument("--max-cost-usd", type=float, default=None,
                        help="stop between stages once the run has spent this much")
    parser.add_argument("--no-reuse", action="store_true",
                        help="re-run building seeds that already exist in t12_grid")
    parser.add_argument("--resume", action="store_true",
                        help="reuse every stage already listed in <out>/build.json "
                             "instead of paying for it again")
    parser.add_argument("--redeploy-from", default=None, metavar="ARTIFACT",
                        help="run ONLY the deploy stage of a finished cell, from this "
                             "artifact (a path, or a name inside <out> such as "
                             "artifact_k3_code.py): gate it on the 5 held-out bindings "
                             f"and deploy iff it passes at least {GATE_MIN_PASS}. "
                             "Writes build.json's deploy section and a redeploy note; "
                             "every other stage is left alone")
    parser.add_argument("--redeploy-doc-arm", action="store_true",
                        help="with --redeploy-from, also re-run the doc arm")
    parser.add_argument("--mock", action="store_true", help="deterministic offline MockOpenAI")
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-emulator", action="store_true", default=True)
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")

    client = None
    model = args.model
    if args.mock:
        from .mock_model import MockOpenAI

        client = MockOpenAI()
        model = "mock"

    if args.redeploy_from:
        env = android_env.AndroidWorldEnv()
        try:
            record = redeploy_from_artifact(
                family=args.family, model=model, out_dir=args.out, env=env,
                artifact=args.redeploy_from, deploy_n=args.deploy_uses,
                seeds=tuple(int(s) for s in args.seeds.split(",")),
                client=client, doc_arm=args.redeploy_doc_arm,
                obs_mode=args.obs_mode,
                doc_seeds=tuple(int(s) for s in args.doc_seeds.split(",")),
            )
        finally:
            env.close()
            if not args.keep_emulator:
                env.stop_emulator()
        note = record["redeploy"]
        print(f"redeploy: gate {gate_summary(note['gate'])}, "
              f"admitted {note['admitted']}, from {note['source_artifact']}")
        return 0 if note["admitted"] else 1

    env = android_env.AndroidWorldEnv()
    try:
        record = run_build(
            family=args.family, model=model, out_dir=args.out, env=env,
            seeds=tuple(int(s) for s in args.seeds.split(",")),
            k_values=tuple(int(k) for k in args.k.split(",")),
            deploy_n=args.deploy_uses,
            doc_seeds=tuple(int(s) for s in args.doc_seeds.split(",")),
            obs_mode=args.obs_mode, client=client,
            max_cost_usd=args.max_cost_usd, reuse_grid=not args.no_reuse,
            resume=args.resume,
        )
    except BudgetExceeded as exc:
        print(f"STOPPED: {exc}")
        return 2
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()
    print(json.dumps(record["break_even"], indent=1))
    print(f"wrote {Path(args.out) / 'build.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
