#!/usr/bin/env python3
"""Run one OSWorld cell under the three-building verifier revision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "code"))

from guiexp_osworld.compiler import _openai_client  # noqa: E402
from guiexp_osworld.run_batch import load_env_file  # noqa: E402
from guiexp_osworld.three_building_verifier import (  # noqa: E402
    load_initial_attempt, run_protocol_deployment, verify_and_repair)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--timeout-s", type=float, default=1200)
    parser.add_argument("--attempt-id", default="initial")
    parser.add_argument("--attempt-role", choices=("initial", "repeat"), default="initial")
    parser.add_argument("--skip-deployment", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    load_env_file()
    source = load_initial_attempt(args.build)
    model_slug = source["model"].replace("/", "_")
    output = (args.output_root / model_slug / source["family"] /
              "attempts" / args.attempt_id / "result.json")
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint(record, program_source):
        output.write_text(json.dumps(record, indent=2) + "\n")
        if program_source is not None:
            (output.parent / "final_program.py").write_text(program_source)

    try:
        prior_result = json.loads(output.read_text()) if args.resume and output.is_file() else None
        prior_program_path = output.parent / "final_program.py"
        prior_source = prior_program_path.read_text() if prior_result and prior_program_path.is_file() else None
        result, final_source = verify_and_repair(
            args.build, _openai_client(), timeout_s=args.timeout_s,
            attempt_id=args.attempt_id, attempt_role=args.attempt_role,
            checkpoint=checkpoint, prior_result=prior_result, prior_source=prior_source)
        if result.get("admitted") and not args.skip_deployment:
            result = run_protocol_deployment(
                result, final_source, _openai_client(), timeout_s=args.timeout_s,
                checkpoint=checkpoint)
    except KeyboardInterrupt:
        result = {"protocol_id": "three-building-model-extracted-v1",
                  "platform": "desktop", "terminal_status": "operator_aborted"}
    except Exception as exc:  # provider and setup failures remain incomplete
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        credit_error = "402" in message or "insufficient" in message.lower() and "credit" in message.lower()
        generation_error = type(exc).__name__ == "GenerationFailure"
        if output.is_file():
            result = json.loads(output.read_text())
        else:
            result = {"protocol_id": "three-building-model-extracted-v1",
                      "platform": "desktop", "model": source["model"],
                      "family": source["family"], "attempt_id": args.attempt_id,
                      "attempt_role": args.attempt_role}
        result["terminal_status"] = (
            "insufficient_credit" if credit_error else
            "generation_failure" if generation_error else "provider_error")
        if generation_error:
            result["failure_kind"] = "model_generation"
        result["error"] = message
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"result={output}")
    print(json.dumps({k: result.get(k) for k in
                      ("model", "family", "attempt_id", "admitted", "terminal_status", "wall_s")}))
    return 0 if result.get("terminal_status") in {"complete", "provisional_initial"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
