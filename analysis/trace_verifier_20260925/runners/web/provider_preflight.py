#!/usr/bin/env python3
"""Development-only same-model provider availability probes."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from openai import OpenAI


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--providers", default="deepinfra,gmicloud,siliconflow")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    client = OpenAI(base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                    api_key=os.environ["OPENROUTER_API_KEY"], timeout=90.0, max_retries=0)
    records = []
    for provider in args.providers.split(","):
        started = time.time()
        row = {"provider_requested": provider, "model_requested": args.model,
               "started_at_unix": started, "max_tokens": 64,
               "purpose": "development_provider_availability_preflight"}
        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": "Reply with exactly: READY"}],
                temperature=0.0, max_tokens=64,
                extra_body={"provider": {"order": [provider], "allow_fallbacks": False}},
            )
            choice = response.choices[0]
            message = choice.message
            content = message.content or ""
            reasoning = getattr(message, "reasoning", None) or ""
            usage = response.usage
            row.update({
                "http_status": 200, "response_id": getattr(response, "id", None),
                "model_served": getattr(response, "model", None),
                "provider_served": getattr(response, "provider", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "native_finish_reason": (getattr(response, "model_extra", {}) or {}).get(
                    "native_finish_reason"),
                "content_length": len(content), "reasoning_length": len(reasoning),
                "content_nonempty": bool(content.strip()),
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "cost_usd": getattr(usage, "cost", None),
                "wall_s": round(time.time() - started, 3), "status": "complete",
            })
        except Exception as exc:
            row.update({"http_status": getattr(exc, "status_code", None),
                        "error_type": type(exc).__name__, "error": str(exc)[:300],
                        "wall_s": round(time.time() - started, 3), "status": "error"})
        records.append(row)
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"record_type": "provider_preflight",
                                    "measured_compilation_charge": False,
                                    "records": records}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
