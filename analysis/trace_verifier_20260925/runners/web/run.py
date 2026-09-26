#!/usr/bin/env python3
"""CLI for one WebArena trace-verifier attempt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "code"))

from guiexp_webarena.env import WebArenaEnv  # noqa: E402
from guiexp_webarena.trace_verifier import run_revision  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--deploy-uses", type=int, default=30)
    parser.add_argument("--reddit-url", default=None)
    parser.add_argument("--attempt-role", choices=("initial", "repeat"), default="initial")
    args = parser.parse_args()
    env = WebArenaEnv(reddit_url=args.reddit_url)
    record = None
    try:
        try:
            record = run_revision(Path(args.source_dir), Path(args.out), env,
                                  deploy_n=args.deploy_uses, attempt_role=args.attempt_role)
        except KeyboardInterrupt:
            status, exc = "operator_aborted", None
        except Exception as caught:  # preserve the last completed checkpoint
            exc = caught
            status = ("insufficient_credit" if getattr(caught, "status_code", None) == 402
                      else "provider_error")
        if record is None:
            result_path = Path(args.out) / "result.json"
            record = json.loads(result_path.read_text()) if result_path.is_file() else {}
            record["terminal_status"] = status
            if exc is not None:
                record["provider_status_code"] = getattr(exc, "status_code", None)
                record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
                if getattr(exc, "calls_detail", None):
                    record["failed_provider_calls"] = exc.calls_detail
                    record["provider_api_failures"] = getattr(exc, "api_failures", [])
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(record, indent=1, default=str))
    finally:
        env.close()
    print(json.dumps({key: record.get(key) for key in
                      ("attempt_id", "terminal_status", "admitted", "wall_s")}, indent=1))
    return 0 if record.get("terminal_status") in {"complete", "refused"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
