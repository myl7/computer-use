#!/usr/bin/env python3
"""Finalize deployment state for a completed rejected attempt, without calls."""

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
    if record.get("terminal_status") != "complete":
        raise ValueError("refusing to finalize a non-complete attempt")
    if record.get("admitted") is not False:
        raise ValueError("refusing to skip deployment for an admitted attempt")
    deployment = record.get("deployment") or {}
    if deployment.get("status") not in (None, "pending", "skipped"):
        raise ValueError("refusing to overwrite existing deployment evidence")
    record["deployment"] = {
        **deployment,
        "status": "skipped",
        "reuse_justification": None,
        "reason": "attempt_rejected_by_three_building_verifier",
        "record_paths": [],
    }
    record["phase"] = "finished"
    record.setdefault("no_call_finalizers", []).append({
        "kind": "rejected_attempt_deployment_skip",
        "model_calls": 0,
    })
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, default=str))
    tmp.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
