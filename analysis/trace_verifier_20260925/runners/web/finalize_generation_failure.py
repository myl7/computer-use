#!/usr/bin/env python3
"""No-call finalization for the audited DeepSeek output-budget failure."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    path = Path(args.result)
    record = json.loads(path.read_text())
    retries = record.get("infrastructure_retries") or []
    fireworks = [r for r in retries if
                 ((r.get("provider_preferences") or {}).get("order") == ["fireworks"])]
    if len(fireworks) != 1:
        raise SystemExit("expected exactly one Fireworks infrastructure retry")
    calls = fireworks[0].get("calls_detail") or []
    if len(calls) != 3:
        raise SystemExit("expected exactly three completed Fireworks calls")
    for call in calls:
        if call.get("finish_reason") != "length" or not call.get("empty_content"):
            raise SystemExit("Fireworks evidence is not three empty length completions")
        usage = call.get("usage") or {}
        if usage.get("completion_tokens") != 131072:
            raise SystemExit("unexpected Fireworks completion token count")
    measured_fireworks_cost = round(sum(
        (call.get("usage") or {}).get("cost_usd") or 0.0 for call in calls
    ), 12)
    known = record.get("new_charges") or {}
    known_lower_bound = round(
        ((known.get("extraction") or {}).get("cost_usd") or 0.0)
        + ((known.get("repair") or {}).get("cost_usd") or 0.0), 12)
    record.update({
        "terminal_status": "complete", "admitted": False,
        "failure_kind": "generation_output_budget_failure",
        "active_call": None,
        "deployment": {"status": "skipped",
                       "reason": "no program candidate was produced by refinement"},
        "generation_failure_evidence": {
            "requested_model": record.get("model"),
            "historical_generation_kwargs": {
                "temperature": 0.0, "max_tokens": None,
                "reasoning": None, "provider": None,
            },
            "fireworks_transition": {
                "provider_order": ["fireworks"], "allow_fallbacks": False,
                "temperature": 0.0, "max_tokens": None, "reasoning": None,
            },
            "observed_calls": 3, "observed_finish_reason": "length",
            "observed_completion_tokens_each": 131072,
            "observed_content": "empty", "observed_output": "reasoning_only",
            "observed_limit_note": (
                "131072 was observed from completed responses; it was not explicitly requested"
            ),
            "classification": (
                "model generation exhausted the observed output budget before program text"
            ),
        },
        "cost_completeness": {
            "known_pre_fireworks_lower_bound_usd": known_lower_bound,
            "measured_fireworks_usd": measured_fireworks_cost,
            "known_total_lower_bound_usd": round(known_lower_bound + measured_fireworks_cost, 12),
            "missing": "usage and cost for the original three empty default-route replies",
        },
    })
    record.pop("error", None)
    path.write_text(json.dumps(record, indent=1, default=str))
    print(json.dumps({"terminal_status": record["terminal_status"],
                      "failure_kind": record["failure_kind"],
                      "known_total_lower_bound_usd":
                          record["cost_completeness"]["known_total_lower_bound_usd"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
