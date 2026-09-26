#!/usr/bin/env python3
"""Two-call text/image compatibility receipt for the official Qwen route."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-env-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-budget-cny", required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "code"))
    from guiexp_android.official_qwen_client import (
        EXPERIMENT_MODEL, OfficialQwenClient, official_usage_record)

    out = Path(args.out)
    client = OfficialQwenClient(
        args.private_env_file, run_budget_cny=args.run_budget_cny,
        budget_ledger_path=out.with_suffix(".budget.json"))
    # Valid 1x1 transparent PNG. The receipt tests multimodal transport only.
    png = base64.b64encode(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+X8wZVwAAAABJRU5ErkJggg=="
    )).decode()
    requests = [
        [{"role": "user", "content": "Reply with exactly OK."}],
        [{"role": "user", "content": [
            {"type": "text", "text": "Reply with exactly OK if you can inspect this image."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
        ]}],
    ]
    receipts = []
    for kind, messages in zip(("text", "image"), requests):
        response = client.chat.completions.create(
            model=EXPERIMENT_MODEL, messages=messages, temperature=0.0)
        choice = response.choices[0]
        content = choice.message.content or ""
        receipts.append({
            "kind": kind,
            "response_id": getattr(response, "id", None),
            "finish_reason": getattr(choice, "finish_reason", None),
            "content_length": len(content),
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "official_usage": official_usage_record(response),
        })
    record = {"status": "complete", "calls": 2,
              "route_provenance": client.route_provenance, "receipts": receipts}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
