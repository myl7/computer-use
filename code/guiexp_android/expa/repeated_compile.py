"""Exp A: independent re-runs of the FULL compile path per t16 cell.

Per cell (4 families x 2 models) the SAME 3 building trajectories and the
RECORDED t16 translations feed a fresh builder call (k=3 code artifact) and
the artifact then goes through ``verify_and_repair`` — initial 5-binding
held-out gate, then per failing building instance the analyzer call, the
charged reactive resume, one builder refinement, re-gate, up to M=3, and the
final seed-0 test replay. Two attempts per cell = 16 compile paths.

What is re-drawn is everything downstream of translation: the builder's
sample, the repair draws, and the verification replays — the lottery a
deployment retry faces. Translations, trajectories and instance/gate seeds
are all fixed, so any attempt-to-attempt difference is that lottery.

Output per cell (structure aligned with t14_compilepath per-attempt records,
extended with the verification stage's records):

    t19_repeated/<model_dir>/<family>/attempt{N}/{family_program.py,
    compile.json, verify.json, verified_program.py, refine*_program.py, ...}
    t19_repeated/<model_dir>/<family>/summary.json
    t19_repeated/_spend.jsonl

    ../venv-expa/bin/python -m guiexp_android.expa.repeated_compile \
        --family ContactsAddContact --model z-ai/glm-5.3-flash \
        --console-port 5562 --grpc-port 8600 --avd guiexpExpA
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
}


def _usage_brief(usage: dict) -> dict:
    keys = ("calls", "prompt_tokens", "cached_tokens", "completion_tokens",
            "total_tokens", "cost_usd")
    return {k: usage.get(k) for k in keys if k in usage}


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

        env = android_env.AndroidWorldEnv(
            console_port=args.console_port, grpc_port=args.grpc_port)
        client = make_client()
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
                      "translation_reused": True}
            print(f"[{args.family} {args.model}] attempt {n}: builder k=3 code", flush=True)
            try:
                built = compile_trajectories(
                    args.model, entries, args.family, artifact="code", client=client)
                (attempt_dir / "family_program.py").write_text(built["program_source"])
                compile_record = {
                    "record_type": "compile",
                    "stage": "builder_initial",
                    "family": args.family,
                    "model": args.model,
                    "k": built["k"],
                    "artifact": "code",
                    "seeds": list(seeds),
                    "building_trajectories": [str(e["trajectory"]) for e in entries],
                    "translations_reused": True,
                    "usage": _usage_brief(built["usage"]),
                    "calls_detail": built.get("calls_detail") or [],
                    "attempts": built.get("attempts"),
                    "api_failures": built.get("api_failures") or [],
                    "cost_usd": built.get("cost_usd"),
                }
                (attempt_dir / "compile.json").write_text(json.dumps(compile_record, indent=1))
                ledger.add("builder_initial", built.get("cost_usd") or 0.0,
                           family=args.family, model=args.model, attempt=n)
                print(f"  builder: {built['usage'].get('prompt_tokens')} + "
                      f"{built['usage'].get('completion_tokens')} tok, "
                      f"${built.get('cost_usd') or 0:.6f}", flush=True)

                record["compile_cost_usd"] = built.get("cost_usd") or 0.0
                record["compile_usage"] = _usage_brief(built["usage"])

                print(f"  verify + repair (seeds {list(seeds)}, M={args.m})", flush=True)
                verify = verify_and_repair(
                    args.model, args.family, built["program_source"], env,
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
                    "attempt_cost_usd": round((built.get("cost_usd") or 0.0) + repair_cost, 8),
                })
                print(f"  gate {gate.get('bindings_passed')}/{gate.get('bindings_total')} "
                      f"(admitted {verify.get('admitted')}, refinements "
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
          f"(emulator-{args.console_port}, grpc {args.grpc_port}); "
          f"cap ${args.spend_cap_usd} = {args.spend_cap_usd * HKD_PER_USD:.1f} HKD", flush=True)
    return run_cell(args, Path(args.out_root))


if __name__ == "__main__":
    raise SystemExit(main())
