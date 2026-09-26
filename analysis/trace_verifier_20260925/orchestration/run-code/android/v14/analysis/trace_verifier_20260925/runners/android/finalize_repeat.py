#!/usr/bin/env python3
"""Finalize a completed repeat verification without deployment or calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--confirmed-no-active-model-call", action="store_true")
    args = parser.parse_args()
    path = Path(args.result).resolve()
    record = json.loads(path.read_text())
    if record.get("attempt_role") != "repeat":
        raise ValueError("refusing to finalize a non-repeat attempt")
    terminal = record.get("terminal_status")
    stopped_after_admission = terminal == "running"
    terminal_generation_failure = (
        terminal == "failed_stage_error"
        and record.get("failure_kind") == "empty_artifact_responses_exhausted"
        and record.get("admitted") is False
        and (record.get("failed_stage") or {}).get(
            "max_attempts_exceeded_without_extra_retry") is True
        and (record.get("failed_stage") or {}).get("response_ledger_calls") == 3
    )
    if terminal not in ("complete", "running") and not terminal_generation_failure:
        raise ValueError("repeat is neither complete nor safely stopped after admission")
    if stopped_after_admission:
        if not args.confirmed_no_active_model_call:
            raise ValueError("running repeat requires explicit no-active-model-call confirmation")
        candidates = record.get("candidate_history") or []
        final = candidates[-1] if candidates else {}
        statuses = [row.get("status") for row in final.get("tasks", [])]
        if not (record.get("admitted") is True and final.get("admitted") is True
                and final.get("passed") == 3 and statuses == ["pass", "pass", "pass"]
                and record.get("phase") == "deployment"):
            raise ValueError("running repeat lacks a complete 3/3 admitted final candidate")
    deployment = record.get("deployment") or {}
    prior_status = deployment.get("status")
    if prior_status not in (None, "pending", "skipped", "not_required_repeat",
                            "reused", "new_30_use"):
        raise ValueError("unrecognized deployment state")
    auxiliary = None
    if prior_status in ("reused", "new_30_use"):
        auxiliary = dict(deployment)
    elif prior_status == "skipped":
        auxiliary = {**deployment, "status": "auxiliary_rejected_deployment_skip"}
    elif stopped_after_admission and prior_status in (None, "pending"):
        auxiliary = {
            **deployment,
            "status": "interrupted_unnecessary_repeat_deployment",
            "response_call_ledger_totals": record.get("response_call_ledger_totals"),
        }
    elif terminal_generation_failure and prior_status in (None, "pending"):
        auxiliary = {
            **deployment,
            "status": "not_run_due_terminal_generation_failure",
            "failed_stage_response_ledger": (record.get("usage") or {}).get(
                "failed_stage_response_ledger"),
        }
    record["deployment"] = {
        "status": "not_required_repeat",
        "reason": "repeat_study_ends_after_compilation_verification",
        "reuse_justification": None,
        "record_paths": [],
    }
    if auxiliary:
        record.setdefault("auxiliary_deployments", []).append(auxiliary)
    record["phase"] = "finished"
    if not terminal_generation_failure:
        record["status"] = record["terminal_status"] = "complete"
    record.setdefault("no_call_finalizers", []).append({
        "kind": "repeat_deployment_not_required",
        "model_calls": 0,
        "prior_deployment_status": prior_status,
        "confirmed_no_active_model_call": bool(args.confirmed_no_active_model_call),
        "stopped_after_admission": stopped_after_admission,
        "terminal_generation_failure_preserved": terminal_generation_failure,
    })
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    tmp.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
