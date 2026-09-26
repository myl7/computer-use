#!/usr/bin/env python3
"""Attach a guarded deployment to an admitted Web verifier record."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "code"))

from guiexp_webarena.compiler import _openai_client  # noqa: E402
from guiexp_webarena.deploy_runner import deploy_uses, run_deployment  # noqa: E402
from guiexp_webarena.env import WebArenaEnv  # noqa: E402
from guiexp_webarena.family import FAMILY  # noqa: E402
from guiexp_webarena.program_runtime import program_from_source  # noqa: E402
from guiexp_webarena.trace_verifier import HardReplayRunner  # noqa: E402


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--uses", type=int, default=30)
    parser.add_argument("--reddit-url", default=None)
    parser.add_argument("--promotion-source", default=None)
    args = parser.parse_args()
    result_path = Path(args.result)
    record = json.loads(result_path.read_text())
    if record.get("terminal_status") != "complete" or not record.get("admitted"):
        raise SystemExit("deployment requires a complete admitted verifier record")
    program_path = result_path.parent / "verified_program.py"
    source = program_path.read_text()
    digest = sha256_bytes(program_path.read_bytes())
    if digest != record.get("final_program_sha256"):
        raise SystemExit("verified_program.py hash does not match result.json")
    _module, program = program_from_source(source)
    env = WebArenaEnv(reddit_url=args.reddit_url)
    try:
        deployed = run_deployment(
            program, FAMILY, deploy_uses(FAMILY, args.uses, env),
            _openai_client(), record["model"], HardReplayRunner(source, env),
            role="three-building-model-extracted-v1",
            progress_path=str(result_path.parent / "deploy_progress.jsonl"),
        )
    finally:
        env.close()
    deploy_path = result_path.parent / "deploy.json"
    deploy_path.write_text(json.dumps(deployed, indent=1))
    record["deployment"] = {
        "status": "new", "path": str(deploy_path),
        "program_byte_identical": True,
        "historical_deployed_program_sha256": None,
        "final_program_sha256": digest, "program_hash_equal": True,
        "task_generator_identical": True, "input_protocol_identical": True,
        "promotion_source": args.promotion_source,
        **deployed,
    }
    result_path.write_text(json.dumps(record, indent=1, default=str))
    print(json.dumps({"n": deployed["n"], "success_count": deployed["success_count"],
                      "result": str(result_path)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
