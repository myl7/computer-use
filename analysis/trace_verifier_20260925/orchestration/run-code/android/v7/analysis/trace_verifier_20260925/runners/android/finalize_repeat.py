#!/usr/bin/env python3
"""Finalize a completed repeat verification without deployment or calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    path = Path(args.result).resolve()
    record = json.loads(path.read_text())
    if record.get("attempt_role") != "repeat":
        raise ValueError("refusing to finalize a non-repeat attempt")
    if record.get("terminal_status") != "complete":
        raise ValueError("repeat verification is not complete")
    deployment = record.get("deployment") or {}
    prior_status = deployment.get("status")
    if prior_status not in (None, "pending", "not_required_repeat", "reused", "new_30_use"):
        raise ValueError("unrecognized deployment state")
    auxiliary = None
    if prior_status in ("reused", "new_30_use"):
        auxiliary = dict(deployment)
    record["deployment"] = {
        "status": "not_required_repeat",
        "reason": "repeat_study_ends_after_compilation_verification",
        "reuse_justification": None,
        "record_paths": [],
    }
    if auxiliary:
        record.setdefault("auxiliary_deployments", []).append(auxiliary)
    record["phase"] = "finished"
    record.setdefault("no_call_finalizers", []).append({
        "kind": "repeat_deployment_not_required",
        "model_calls": 0,
        "prior_deployment_status": prior_status,
    })
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    tmp.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
