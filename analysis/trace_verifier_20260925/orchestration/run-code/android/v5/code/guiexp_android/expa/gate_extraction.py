"""t20: the t16 verified programs' gate bindings through the EXTRACTION gate.

The t16 admission gate INJECTS the binding dict straight into the program.
Deployment cannot inject: a real use arrives as a natural-language goal, so
the chain is goal prompt -> ONE model extraction call (type check with one
bounded retry, the deploy boundary) -> the program -> the family oracle.
This driver runs the 8 t16 verified programs on their OWN 5 gate bindings
(the same seed-deterministic draws verify_and_repair used, recomputed here
and cross-checked against the recorded gate detail) through that chain, and
alongside re-runs the same bindings by direct injection so the comparison is
same-day on the same emulator. Injection replays cost zero model tokens; the
only spend is the extraction calls (cents).

Output: t20_gate_extraction/<model_dir>/<family>/extraction.json plus a
rolling top-level summary.json.

    ../venv-expa/bin/python -m guiexp_android.expa.gate_extraction \
        --family ContactsAddContact --model z-ai/glm-5.3-flash \
        --console-port 5562 --grpc-port 8600 --avd guiexpExpA
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .lane import (
    HKD_PER_USD,
    SpendLedger,
    configure_lane,
    device_booted,
    load_env_file,
    make_client,
)
from .repeated_compile import MODEL_DIRS


def _match(recorded: list[dict] | None, binding: dict):
    """The recorded t16 injection outcome for a binding, when present."""
    for row in recorded or []:
        if row.get("binding") == binding:
            return {"passed": row.get("passed"), "error": row.get("error")}
    return None


def run_cell(args, out_root: Path) -> int:
    from .. import android_env
    from ..compiler import binding_fields
    from ..deploy_runner import run_single_use
    from ..gate_runner import GATE_K, heldout_bindings, run_gate
    from ..program_runtime import ProgramRunner, program_from_path

    model_dir = MODEL_DIRS[args.model]
    src_dir = (android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
               / "t16_build" / model_dir / args.family)
    program_path = src_dir / "verified_program.py"
    _module, program = program_from_path(program_path)

    recorded_gate = None
    verify_path = src_dir / "verify.json"
    if verify_path.exists():
        verify = json.loads(verify_path.read_text())
        recorded_gate = ((verify.get("gate") or {}).get("detail"))
        if recorded_gate is None:
            recorded_gate = ((verify.get("final_gate") or {}).get("detail"))

    # The same draws verify_and_repair gated on: k=5, excluding seed-1 params.
    exclude = android_env.instance_params(args.family, 1)
    draws = heldout_bindings(args.family, k=GATE_K, exclude_params=exclude)
    for draw in draws:  # binding must be fully populated
        missing = [f for f in binding_fields(args.family)
                   if not str(draw["binding"].get(f, "")).strip()]
        if missing:
            raise ValueError(f"gate draw missing fields {missing}: {draw['binding']}")
    matched = [bool(_match(recorded_gate, d["binding"]) is not None) for d in draws]
    print(f"draws: {len(draws)}; matched to recorded t16 gate detail: "
          f"{sum(matched)}/{len(draws)}", flush=True)

    cell_dir = out_root / model_dir / args.family
    cell_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(out_root / "_spend.jsonl")
    env = None
    t0 = time.time()
    try:
        if not device_booted():
            raise RuntimeError("lane emulator not booted (start it before the driver)")
        env = android_env.AndroidWorldEnv(
            console_port=args.console_port, grpc_port=args.grpc_port)
        runner = ProgramRunner(env)
        client = make_client()

        print("injection replay (zero model tokens)", flush=True)
        injection = run_gate(program, args.family, draws, runner)
        print(f"  injection: {injection['bindings_passed']}/{injection['bindings_total']}", flush=True)

        uses = []
        for i, draw in enumerate(draws, start=1):
            task = android_env.AndroidTask(
                family=args.family, condition="discover", seed=0, params=draw["params"])
            goal = android_env.goal_text(task)
            record = run_single_use(
                program, args.family, goal, draw["binding"], draw["params"],
                client, args.model, runner)
            record["goal"] = goal
            record["binding"] = draw["binding"]
            record["injection_passed_now"] = injection["detail"][i - 1]["passed"]
            record["injection_error_now"] = injection["detail"][i - 1]["error"]
            record["injection_passed_t16"] = (_match(recorded_gate, draw["binding"]) or {}).get("passed")
            uses.append(record)
            tag = record["error_type"] or ("OK" if record["success"] else "FAIL")
            print(f"  use {i}: {record['tokens']} tok (r{record['retries']}) {tag} "
                  f"[extracted={json.dumps(record.get('extracted'))}]", flush=True)
        ledger.add("gate_extraction",
                   sum(u["cost_usd"] for u in uses),
                   family=args.family, model=args.model)
    finally:
        if env is not None:
            env.close()

    extraction_cost = sum(u["cost_usd"] for u in uses)
    comparison = {
        "family": args.family,
        "model": args.model,
        "program_path": str(program_path),
        "bindings_total": len(draws),
        "injection_passed_now": injection["bindings_passed"],
        "injection_passed_t16": (json.loads(verify_path.read_text()).get("gate") or {}
                                 ).get("bindings_passed") if verify_path.exists() else None,
        "extraction_passed": sum(1 for u in uses if u["success"]),
        "extraction_type_check_fails": sum(1 for u in uses
                                           if u["error_type"] in ("extraction_json", "type_check")),
        "extraction_cost_usd": round(extraction_cost, 8),
        "per_binding": [
            {
                "binding": u["binding"],
                "injection_passed_now": u["injection_passed_now"],
                "injection_passed_t16": u["injection_passed_t16"],
                "extraction_passed": u["success"],
                "extraction_error_type": u["error_type"],
                "extracted": u.get("extracted"),
                "retries": u["retries"],
                "tokens": u["tokens"],
                "cost_usd": u["cost_usd"],
            }
            for u in uses
        ],
        "record_type": "gate_extraction",
        "wall_s": round(time.time() - t0, 1),
    }
    (cell_dir / "extraction.json").write_text(
        json.dumps({**comparison, "uses": uses}, indent=1, default=str))

    summary_path = out_root / "summary.json"
    summary = {"cells": {}, "record_type": "gate_extraction_summary"}
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
    summary["cells"][f"{model_dir}/{args.family}"] = comparison
    total_cost = 0.0
    for cell in summary["cells"].values():
        total_cost += cell.get("extraction_cost_usd") or 0.0
    summary["extraction_cost_usd_total"] = round(total_cost, 8)
    summary_path.write_text(json.dumps(summary, indent=1, default=str))
    print(f"cell done: injection {comparison['injection_passed_now']}/5 vs "
          f"extraction {comparison['extraction_passed']}/5, extraction "
          f"${extraction_cost:.6f}; {ledger.report()}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True)
    parser.add_argument("--model", required=True, choices=sorted(MODEL_DIRS))
    parser.add_argument("--console-port", type=int, default=5562)
    parser.add_argument("--grpc-port", type=int, default=8600)
    parser.add_argument("--avd", default="guiexpExpA")
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--spend-cap-usd", type=float, default=1.0)
    args = parser.parse_args()

    load_env_file()
    configure_lane(args.console_port, args.grpc_port, args.avd)
    from .. import android_env

    if args.out_root is None:
        args.out_root = str(android_env.REPO_ROOT / "experimental-results"
                            / "guiexp_android" / "t20_gate_extraction")
    print(f"t20 gate_extraction: {args.family} x {args.model}, avd {args.avd}; "
          f"cap ${args.spend_cap_usd} = {args.spend_cap_usd * HKD_PER_USD:.1f} HKD", flush=True)
    return run_cell(args, Path(args.out_root))


if __name__ == "__main__":
    raise SystemExit(main())
