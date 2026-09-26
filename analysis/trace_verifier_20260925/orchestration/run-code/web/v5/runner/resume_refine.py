#!/usr/bin/env python3
"""Resume a Web attempt whose only incomplete operation is builder refinement."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "code"))

from guiexp_webarena.compiler import _openai_client, refine_artifact  # noqa: E402
from guiexp_webarena.env import WebArenaEnv  # noqa: E402
from guiexp_webarena.program_runtime import ProgramRunner, program_from_source  # noqa: E402
from guiexp_webarena.trace_verifier import (  # noqa: E402
    add_usage, evaluate, sha256_text, zero_usage,
)


def save(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, indent=1, default=str))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--reddit-url", default=None)
    parser.add_argument("--provider", default=None,
                        help="OpenRouter provider name for same-model routing")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--reasoning-max-tokens", type=int, default=None)
    args = parser.parse_args()
    path = Path(args.result)
    record = json.loads(path.read_text())
    if record.get("terminal_status") != "provider_error":
        raise SystemExit("resume_refine requires a provider_error checkpoint")
    rounds = record.get("repairs") or []
    if not rounds or rounds[-1].get("refinement_usage"):
        raise SystemExit("last repair is not a missing builder refinement")
    tasks = record["tasks"]
    bindings = {item["seed"]: item["extracted_binding"] for item in record["extractions"]}
    source_path = Path(record["source_paths"]["artifact_k3_code"])
    source = source_path.read_text()
    env = WebArenaEnv(reddit_url=args.reddit_url)
    try:
        _module, program = program_from_source(source)
        outcome = evaluate(program, tasks, bindings, ProgramRunner(env),
                           program_source=source, env=env)
        failure = outcome["failure"]
        if failure is None:
            raise RuntimeError("replay no longer reproduces the checkpointed failure")
        previous = rounds[-1]
        analysis = previous["analysis"]
        analysis_text = "\n".join(
            f"{key.upper()}: {analysis.get(key, '')}" for key in
            ("cause", "done", "plan", "decision")
        )
        resume_actions = [item.get("action", "") for item in
                          previous.get("resume_calls_detail") or []]
        hybrid = {
            "binding": failure["instance"]["binding"],
            "goal": failure["instance"]["goal"], "failure": failure["error"],
            "program_trace": failure["trace"], "breakpoint_screen": failure["screen"],
            "analysis": analysis_text, "resume_actions": resume_actions,
            "resume_success": previous.get("resume_success"),
        }
        record["terminal_status"] = "running"
        record["active_call"] = {"stage": "builder_refine_resume",
                                 "seed": failure["instance"]["seed"],
                                 "round": previous["round"],
                                 "started_at_unix": time.time(), "request_timeout_s": 300.0}
        provider_preferences = None
        if args.provider:
            provider_preferences = {"order": [args.provider], "allow_fallbacks": False}
        generation_settings = None
        if args.max_tokens is not None or args.reasoning_max_tokens is not None:
            generation_settings = {"max_tokens": args.max_tokens,
                                   "reasoning": ({"max_tokens": args.reasoning_max_tokens}
                                                 if args.reasoning_max_tokens is not None else None)}
        record.setdefault("infrastructure_retries", []).append({
            "stage": "builder_refine", "reason": "three prior empty provider responses",
            "requested_model": record["model"],
            "provider_preferences": provider_preferences,
            "generation_settings": generation_settings,
            "generation_settings_rationale": (
                "bound reasoning so final program text has reserved output capacity"
                if generation_settings else "historical default settings"
            ),
            "started_at_unix": record["active_call"]["started_at_unix"],
            "reuses": ["extractions", "analyzer", "react_resume"],
        })
        save(path, record)
        started = time.monotonic()
        def record_response(call):
            previous.setdefault("refinement_calls_detail", []).append(call)
            save(path, record)

        refined = refine_artifact(record["model"], record["family"], source,
                                  hybrid, [], client=_openai_client(),
                                  on_response=record_response,
                                  provider_preferences=provider_preferences,
                                  generation_settings=generation_settings)
        wall_s = round(time.monotonic() - started, 3)
        previous["refinement_usage"] = refined["usage"]
        previous["refinement_calls_detail"] = refined.get("calls_detail") or []
        previous["refinement_wall_s"] = wall_s
        previous["resumed_after_provider_error"] = True
        record["infrastructure_retries"][-1].update({
            "terminal_status": "complete", "wall_s": wall_s,
            "calls_detail": refined.get("calls_detail") or [],
        })
        repair_total = (record.get("new_charges") or {}).get("repair") or zero_usage()
        add_usage(repair_total, refined["usage"], calls=refined["usage"].get("calls", 1))
        record.setdefault("new_charges", {})["repair"] = repair_total
        candidate = refined["artifact_text"]
        candidate_path = path.parent / f"candidate_refine{previous['round']}.py"
        candidate_path.write_text(candidate)
        _module, candidate_program = program_from_source(candidate)
        final = evaluate(candidate_program, tasks, bindings, ProgramRunner(env),
                         program_source=candidate, env=env)
        record.setdefault("candidate_history", []).append({
            "version": f"refine{previous['round']}", "sha256": sha256_text(candidate),
            "tasks": final["detail"], "passed": final["passed"],
        })
        record["active_call"] = None
        record["final_tasks"] = final["detail"]
        record["final_program_sha256"] = sha256_text(candidate)
        record["admitted"] = final["passed"] == 3
        record["terminal_status"] = "complete" if record["admitted"] else "needs_repair"
        record["failure_kind"] = None if record["admitted"] else "task_failure"
        if record["admitted"]:
            (path.parent / "verified_program.py").write_text(candidate)
            record["deployment"] = {"status": "not_run", "reason": "resumed verification only"}
        save(path, record)
    except KeyboardInterrupt:
        record["terminal_status"] = "operator_aborted"
        save(path, record)
        raise
    except Exception as exc:
        record["terminal_status"] = (
            "insufficient_credit" if getattr(exc, "status_code", None) == 402 else "provider_error"
        )
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        if record.get("infrastructure_retries"):
            record["infrastructure_retries"][-1].update({
                "terminal_status": record["terminal_status"],
                "calls_detail": getattr(exc, "calls_detail", []),
                "api_failures": getattr(exc, "api_failures", []),
            })
        save(path, record)
        raise
    finally:
        env.close()
    print(json.dumps({"terminal_status": record["terminal_status"],
                      "admitted": record["admitted"]}, indent=1))
    return 0 if record["admitted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
