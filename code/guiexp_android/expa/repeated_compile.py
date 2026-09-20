"""Exp A: independent re-runs of the FULL compile path per t16 cell.

Per cell (4 families x 2 models) the SAME 3 building trajectories and the
RECORDED t16 translations feed the full builder stage -- k = 1, 2, 3 building
trajectories x {code, doc}, all six calls priced as C -- then each k's code
artifact runs the held-out gate on the same 5 seed-deterministic bindings
verify_and_repair gates on (sha256 namespace
``guiexp_android:gate:<family>:<seed>``, excluding the first building
instance), and the k=3 code artifact goes through ``verify_and_repair`` --
initial 5-binding held-out gate, then per failing building instance the
analyzer call, the charged reactive resume, one builder refinement, re-gate,
up to M=3, and the final seed-0 test replay. Two attempts per cell = 16
compile paths.

What is re-drawn is everything downstream of translation: the builder's
samples, the repair draws, and the verification replays -- the lottery a
deployment retry faces. Translations, trajectories and instance/gate seeds
are all fixed, so any attempt-to-attempt difference is that lottery.

Output per cell (structure aligned with t14_compilepath per-attempt records,
extended with the builder grid and per-k gates of the t16 build records):

    t19_repeated/<model_dir>/<family>/attempt{N}/{family_program.py,
    compile.json, gate_per_k.json, verify.json, verified_program.py,
    artifact_k{1,2,3}_{code.py,doc.txt}, refine*_program.py, ...}
    t19_repeated/<model_dir>/<family>/summary.json
    t19_repeated/_spend.jsonl

    ../venv-expa/bin/python -m guiexp_android.expa.repeated_compile \
        --family ContactsAddContact --model z-ai/glm-5.3-flash \
        --console-port 5562 --grpc-port 8600 --avd guiexpExpA

RECOVERY NOTE (2026-09-20): this module was reconstructed from the surviving
server bytecode (/tmp/repeated_compile_upgraded.pyc, CPython 3.11, copied off
cs659b; md5 79ae4e87a108862a693a1a1b0271bc1e) after the original server-only
t19 upgrade of this driver was lost to an rsync before it ever reached the
repo. The reconstruction was verified by recursive code-object comparison
against that bytecode: all 19 code objects byte-identical (co_code, co_consts,
co_names, varnames, exception tables, line tables) prior to the qwen
MODEL_DIRS entry and this note.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from .lane import (
    HKD_PER_USD,
    SpendLedger,
    configure_lane,
    device_booted,
    load_env_file,
    make_client,
    remap_trajectory_path,
)

MODEL_DIRS = {
    "z-ai/glm-5.3-flash": "z-ai_glm-5.3-flash",
    "deepseek/deepseek-v4-flash-vision-exp": "deepseek_deepseek-v4-flash-vision-exp",
    "qwen/qwen3.8-flash": "qwen_qwen3.8-flash",
}

K_VALUES = (1, 2, 3)
ARTIFACTS = ("code", "doc")
USAGE_KEYS = ("calls", "prompt_tokens", "cached_tokens", "completion_tokens",
              "total_tokens", "cost_usd")


def _usage_brief(usage: dict) -> dict:
    keys = ("calls", "prompt_tokens", "cached_tokens", "completion_tokens",
            "total_tokens", "cost_usd")
    return {k: usage.get(k) for k in keys if k in usage}


def _merge_usage(usages: list) -> dict:
    out = {}
    for key in USAGE_KEYS:
        out[key] = sum(u.get(key) or 0 for u in usages)
    return out


def run_cell(args, out_root: Path) -> int:
    from .. import android_env

    model_dir = MODEL_DIRS[args.model]
    cell_dir = out_root / model_dir / args.family
    src_dir = (android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
               / "t16_build" / model_dir / args.family)
    translations = json.loads((src_dir / "translation.json").read_text())
    exploration = json.loads((src_dir / "explore" / "exploration.json").read_text())
    building = [
        {"seed": item["seed"], "trajectory": remap_trajectory_path(item["best_trajectory"])}
        for item in exploration["instances"]
    ]
    for item in building:
        if not item["trajectory"].exists():
            raise FileNotFoundError(f"missing building trajectory {item['trajectory']}")
    entries = [
        {
            "trajectory": item["trajectory"],
            "translation": translations[str(item["seed"])],
            "label": f"seed {item['seed']}",
        }
        for item in building
    ]
    seeds = tuple(item["seed"] for item in building)

    summary_path = cell_dir / "summary.json"
    summary = {"family": args.family, "model": args.model,
               "source_cell": str(src_dir), "building_seeds": list(seeds),
               "building_trajectories": [str(e["trajectory"]) for e in entries],
               "translations_reused": True, "attempts": []}
    if summary_path.exists():
        previous = json.loads(summary_path.read_text())
        if previous.get("attempts"):
            summary = previous
    ledger = SpendLedger(out_root / "_spend.jsonl")

    env = None
    done = len(summary["attempts"])
    try:
        if not device_booted():
            raise RuntimeError("lane emulator not booted (start it before the driver)")
        from ..compiler import compile_trajectories
        from ..verify_runner import verify_and_repair
        from ..gate_runner import GATE_K, heldout_bindings, run_gate
        from ..program_runtime import ProgramRunner, program_from_source

        env = android_env.AndroidWorldEnv(
            console_port=args.console_port, grpc_port=args.grpc_port)
        client = make_client()

        # same held-out gate draws for every attempt of this cell
        instance0 = android_env.instance_params(args.family, seeds[0])
        draws = heldout_bindings(args.family, k=GATE_K, exclude_params=instance0)
        runner = ProgramRunner(env)
        for n in range(done + 1, args.attempts + 1):
            spent = ledger.total()
            if spent > args.spend_cap_usd:
                summary["stopped_at_spend_usd"] = round(spent, 6)
                print(f"spend cap ${args.spend_cap_usd} hit (${spent:.4f}); "
                      "not starting another attempt", flush=True)
                break
            attempt_dir = cell_dir / f"attempt{n}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.time()
            record = {"attempt": n, "family": args.family, "model": args.model,
                      "translation_reused": True, "k_values": list(K_VALUES)}
            print(f"[{args.family} {args.model}] attempt {n}: builder k in "
                  f"{list(K_VALUES)} x code/doc (6 calls)", flush=True)
            try:

                builds = {}
                for k in K_VALUES:
                    for artifact in ARTIFACTS:
                        result = compile_trajectories(
                            args.model, entries[:k], args.family,
                            artifact=artifact, client=client)
                        key = f"k{k}_{artifact}"
                        builds[key] = result
                        suffix = "py" if artifact == "code" else "txt"
                        (attempt_dir / f"artifact_{key}.{suffix}").write_text(
                            result["artifact_text"])
                        print(f"    {key}: {result['usage'].get('prompt_tokens')} + "
                              f"{result['usage'].get('completion_tokens')} tok, "
                              f"${result.get('cost_usd') or 0:.6f}", flush=True)
                builder_usage = _merge_usage([b["usage"] for b in builds.values()])
                builder_cost = builder_usage["cost_usd"]
                (attempt_dir / "family_program.py").write_text(
                    builds["k3_code"]["program_source"])
                compile_record = {
                    "record_type": "compile",
                    "stage": "builder_initial",
                    "family": args.family,
                    "model": args.model,
                    "attempt": n,
                    "k_values": list(K_VALUES),
                    "artifacts": list(ARTIFACTS),
                    "seeds": list(seeds),
                    "building_trajectories": [str(e["trajectory"]) for e in entries],
                    "translations_reused": True,
                    "builder": {
                        "initial": {
                            key: {
                                "k": result["k"], "artifact": result["artifact"],
                                **_usage_brief(result["usage"]),
                                "cost_usd": result.get("cost_usd"),
                                "calls_detail": result.get("calls_detail") or [],
                                "attempts": result.get("attempts"),
                                "api_failures": result.get("api_failures") or [],
                            }
                            for key, result in builds.items()
                        },
                        "totals_all_calls": dict(builder_usage),
                    },
                    "usage": _usage_brief(builder_usage),
                    "cost_usd": builder_cost,
                }
                (attempt_dir / "compile.json").write_text(
                    json.dumps(compile_record, indent=1))
                ledger.add("builder_initial", builder_cost,
                           family=args.family, model=args.model, attempt=n)
                print(f"  builder total: {builder_usage.get('prompt_tokens')} + "
                      f"{builder_usage.get('completion_tokens')} tok, "
                      f"${builder_cost:.6f}", flush=True)

                record["compile_cost_usd"] = builder_cost
                record["compile_usage"] = _usage_brief(builder_usage)


                gates = {}
                for k in K_VALUES:
                    source = builds[f"k{k}_code"]["program_source"]
                    try:
                        _module, program = program_from_source(source)
                        gate = run_gate(program, args.family, draws, runner)
                    except Exception as exc:
                        gate = {"bindings_passed": 0, "bindings_total": len(draws),
                                "detail": [], "load_error": f"{type(exc).__name__}: {exc}"}
                    gates[f"k{k}"] = gate
                    print(f"    gate k={k}: {gate['bindings_passed']}/"
                          f"{gate['bindings_total']}", flush=True)
                (attempt_dir / "gate_per_k.json").write_text(json.dumps(
                    {"gate_seed_namespace": f"guiexp_android:gate:{args.family}",
                     "exclude_seed": seeds[0], "draws": draws, "gates": gates},
                    indent=1, default=str))
                record["gate_per_k"] = {
                    f"k{k}": {"passed": gates[f"k{k}"]["bindings_passed"],
                              "total": gates[f"k{k}"]["bindings_total"]}
                    for k in K_VALUES}


                print(f"  verify + repair (seeds {list(seeds)}, M={args.m})", flush=True)
                verify = verify_and_repair(
                    args.model, args.family, builds["k3_code"]["program_source"], env,
                    seeds=seeds, m_max=args.m, client=client,
                    obs_mode=args.obs_mode, artifact="code", out_dir=attempt_dir)
                (attempt_dir / "verify.json").write_text(json.dumps(verify, indent=1, default=str))
                (attempt_dir / "verified_program.py").write_text(verify["final_artifact"])
                totals = verify["totals"]
                repair_cost = (
                    totals["analyzer"]["cost_usd"]
                    + totals["resume_episodes"]["cost_usd"]
                    + totals["builder_refinements"]["cost_usd"]
                )
                ledger.add("verify_repair", repair_cost,
                           family=args.family, model=args.model, attempt=n)
                gate = verify.get("gate") or {}
                record.update({
                    "gate_passed": gate.get("bindings_passed"),
                    "gate_total": gate.get("bindings_total"),
                    "gate_history": verify.get("gate_history") or [],
                    "admitted": verify.get("admitted"),
                    "admitted_version": verify.get("admitted_version"),
                    "refinements": verify.get("refinements"),
                    "unautomatable": verify.get("unautomatable"),
                    "test_seed0": verify.get("test_seed0"),
                    "replays": verify.get("replays"),
                    "repair_cost_usd": round(repair_cost, 8),
                    "repair_totals": {k: _usage_brief(v) for k, v in totals.items()
                                      if isinstance(v, dict)},
                    "attempt_cost_usd": round(builder_cost + repair_cost, 8),
                })
                print(f"  gate {gate.get('bindings_passed')}/{gate.get('bindings_total')} (admitted "
                      f"{verify.get('admitted')}, refinements "
                      f"{verify.get('refinements')}, unautomatable {verify.get('unautomatable')})",
                      flush=True)
                print(f"  repair cost ${repair_cost:.6f}; {ledger.report()}", flush=True)
            except Exception as exc:  # noqa: BLE001 - a failed draw is data
                record["error"] = f"{type(exc).__name__}: {exc}"
                record["traceback"] = traceback.format_exc(limit=8)
                print(f"  attempt {n} FAILED: {record['error']}", flush=True)
            record["wall_s"] = round(time.time() - t0, 1)
            summary["attempts"] = [a for a in summary["attempts"] if a.get("attempt") != n]
            summary["attempts"].append(record)
            summary["wall_s_total"] = round(
                sum(a.get("wall_s") or 0 for a in summary["attempts"]), 1)
            summary_path.write_text(json.dumps(summary, indent=1, default=str))
    finally:
        if env is not None:
            env.close()  # never stops the emulator: the lane owns it externally
    usd = sum(a.get("attempt_cost_usd") or 0 for a in summary["attempts"])
    print(f"cell done: {len(summary['attempts'])}/{args.attempts} attempts, "
          f"${usd:.4f} model spend; {ledger.report()}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True)
    parser.add_argument("--model", required=True, choices=sorted(MODEL_DIRS))
    parser.add_argument("--attempts", type=int, default=2)
    parser.add_argument("--m", type=int, default=3, help="repair budget per building instance")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--console-port", type=int, default=5562)
    parser.add_argument("--grpc-port", type=int, default=8600)
    parser.add_argument("--avd", default="guiexpExpA")
    parser.add_argument("--out-root", default=None,
                        help="default: <repo>/experimental-results/guiexp_android/t19_repeated")
    parser.add_argument("--spend-cap-usd", type=float, default=3.5,
                        help="stop starting new attempts when the lane bill crosses this")
    args = parser.parse_args()

    load_env_file()
    configure_lane(args.console_port, args.grpc_port, args.avd)
    from .. import android_env

    if args.out_root is None:
        args.out_root = str(android_env.REPO_ROOT / "experimental-results"
                            / "guiexp_android" / "t19_repeated")
    print(f"expA repeated_compile: {args.family} x {args.model}, "
          f"{args.attempts} attempts, avd {args.avd} "
          f"(emulator-{args.console_port}, grpc {args.grpc_port}); cap $"
          f"{args.spend_cap_usd} = {args.spend_cap_usd * HKD_PER_USD:.1f} HKD", flush=True)
    return run_cell(args, Path(args.out_root))


if __name__ == "__main__":
    raise SystemExit(main())
