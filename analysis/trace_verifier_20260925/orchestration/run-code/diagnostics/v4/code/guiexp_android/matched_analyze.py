"""Read-only analysis of complete matched pairs; never calls models/devices.

The only output is analysis.json in the prepared revision directory. Incomplete
pairs remain in the exclusion list and their known/uncertain costs are retained.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path

from .budget_client import atomic_json
from .matched_run import DEFAULT_OUT


def quantile(values, q):
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def summarize(pairs, bootstrap=2000):
    n = len(pairs)
    if not n:
        return {"n_pairs": 0, "confidence_interval": None}
    reactive = [p["discover"]["total_cost_usd"] for p in pairs]
    document = [p["doc"]["total_cost_usd"] for p in pairs]
    delta = [d - r for d, r in zip(document, reactive)]
    success_delta = [int(p["doc"]["success"]) - int(p["discover"]["success"]) for p in pairs]
    out = {"n_pairs": n,
           "discover_mean_usd": statistics.mean(reactive),
           "doc_mean_usd": statistics.mean(document),
           "paired_doc_minus_discover_mean_usd": statistics.mean(delta),
           "paired_doc_minus_discover_median_usd": statistics.median(delta),
           "paired_doc_minus_discover_success_rate": statistics.mean(success_delta),
           "doc_saving_fraction_of_total_reactive_cost": 1 - sum(document) / sum(reactive) if sum(reactive) else None,
           "success_counts": {arm: sum(p[arm].get("success") is True for p in pairs) for arm in ("discover", "doc")}}
    out["usd_per_observed_success"] = {
        arm: sum(p[arm]["total_cost_usd"] for p in pairs) / count if count else None
        for arm, count in out["success_counts"].items()
    }
    out["success_rates"] = {arm: count / n for arm, count in out["success_counts"].items()}
    out["success_discordant_pairs"] = {
        "doc_only": sum(d == 1 for d in success_delta),
        "discover_only": sum(d == -1 for d in success_delta),
    }
    if n < 2:
        out["confidence_interval"] = None
        out["uncertainty_note"] = "One distinct binding: no confidence interval is reported."
    else:
        rng = random.Random(20260913)
        samples = [[rng.randrange(n) for _ in range(n)] for _ in range(bootstrap)]
        means = [statistics.mean(delta[i] for i in sample) for sample in samples]
        success_means = [statistics.mean(success_delta[i] for i in sample) for sample in samples]
        out["confidence_interval"] = {"method": "paired percentile bootstrap", "resamples": bootstrap,
                                      "level": 0.95, "doc_minus_discover_usd": [quantile(means, .025), quantile(means, .975)],
                                      "doc_minus_discover_success_rate": [quantile(success_means, .025), quantile(success_means, .975)]}
        out["uncertainty_note"] = "Descriptive paired bootstrap over at most five fixed distinct bindings. The sample is small; no admission or model-build inference is made."
    return out


def token_totals(usages):
    prompt = completion = cached = 0
    missing_prompt = missing_completion = missing_cache = 0
    for usage in usages:
        p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
        details = usage.get("prompt_tokens_details") or {}
        cache = details.get("cached_tokens")
        if isinstance(p, int):
            prompt += p
        else:
            missing_prompt += 1
        if isinstance(c, int):
            completion += c
        else:
            missing_completion += 1
        if isinstance(cache, int) and isinstance(p, int) and 0 <= cache <= p:
            cached += cache
        else:
            missing_cache += 1
    return {"calls": len(usages), "prompt_tokens_reported_sum": prompt,
            "completion_tokens_reported_sum": completion, "cached_input_tokens_reported_sum": cached,
            "uncached_input_tokens": prompt - cached if not missing_prompt and not missing_cache else None,
            "calls_missing_prompt": missing_prompt, "calls_missing_completion": missing_completion,
            "calls_missing_or_invalid_cache_split": missing_cache,
            "cache_imputation": False}


def serving_summary(pairs):
    """Deterministic cost bounds use every unresolved physical reservation."""
    n = len(pairs)
    success = {arm: sum(p[arm]["success"] is True for p in pairs) for arm in ("discover", "doc")}
    mean_intervals = {
        arm: [sum(p[arm]["cost_usd_interval"][bound] for p in pairs) / n for bound in (0, 1)]
        for arm in ("discover", "doc")
    }
    delta = [sum(p["doc_minus_discover_usd_interval"][bound] for p in pairs) / n for bound in (0, 1)]
    return {"n_pairs": n, "distinct_binding_n": len({p["binding_sha256"] for p in pairs}),
            "success_counts": success, "success_rates": {arm: count / n for arm, count in success.items()},
            "paired_doc_minus_discover_success_rate": (success["doc"] - success["discover"]) / n,
            "mean_cost_usd_intervals": mean_intervals,
            "mean_doc_minus_discover_usd_interval": delta,
            "bound_note": "These are conservative billing bounds, not confidence intervals. Unknown bills are never replaced with zero.",
            "pairs": pairs}


def analyze(out=DEFAULT_OUT):
    out = Path(out)
    versions = [p for p in out.glob("spec.v*.json") if p.stem.removeprefix("spec.v").isdigit()]
    spec_path = max(versions, key=lambda p: int(p.stem.removeprefix("spec.v"))) if versions else out / "spec.json"
    spec = json.loads(spec_path.read_text())
    groups = defaultdict(list)
    service_groups = defaultdict(list)
    excluded = []
    recorded_cost = {}
    usages = defaultdict(list)
    call_states = defaultdict(int)
    ledger_path = out / "budget.sqlite3"
    with sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True) as db:
        for episode, actual, reserved, state, response_json in db.execute(
                "SELECT episode, actual_nano, reserved_nano, state, response_json FROM calls"):
            # One primary-key row per request. Failed/rejected action replies
            # remain included; no summation over duplicate trajectory copies.
            entry = recorded_cost.setdefault(episode, {"actual_usd": 0, "unresolved_reserved_usd": 0,
                                                       "calls": 0, "unknown_bill_calls": 0})
            entry["actual_usd"] += (actual or 0) / 1e9
            entry["unresolved_reserved_usd"] += reserved / 1e9 if state != "settled" else 0
            entry["calls"] += 1
            entry["unknown_bill_calls"] += actual is None
            call_states[state] += 1
            response = json.loads(response_json) if response_json else {}
            usages[episode].append(response.get("usage") or {})
    rows = spec["episodes"]
    for i in range(0, len(rows), 2):
        pair = rows[i:i+2]
        loaded = {}
        for row in pair:
            path = out / "episodes" / row["id"] / "state.json"
            loaded[row["condition"]] = json.loads(path.read_text()) if path.exists() else {"status": "pending"}
            if row["id"].rsplit("/", 1)[0] in spec.get("censored_pairs", {}):
                loaded[row["condition"]]["status"] = "censored_prior"
        key = f"{pair[0]['model']}/{pair[0]['family']}"
        bills_complete = all(recorded_cost.get(row["id"], {}).get("calls", 0) > 0 and
                             recorded_cost[row["id"]]["unknown_bill_calls"] == 0 for row in pair)
        serving_complete = all(state["status"] == "done" for state in loaded.values())
        if serving_complete:
            bounded = {"seed": pair[0]["seed"], "binding_sha256": loaded["discover"].get("binding_sha256"),
                       "exact_bills_available": bills_complete}
            for row in pair:
                arm = row["condition"]
                bill = recorded_cost.get(row["id"])
                # A completed real episode should always have a ledger row.
                # If it does not, no finite billing bound can be asserted.
                if bill is None:
                    serving_complete = False
                    break
                lo = bill["actual_usd"]
                hi = lo + bill["unresolved_reserved_usd"]
                bounded[arm] = {"success": loaded[arm]["result"]["success"],
                                "cost_usd_interval": [lo, hi], "unknown_bill_calls": bill["unknown_bill_calls"],
                                "token_split": token_totals(usages[row["id"]])}
            if serving_complete:
                rlo, rhi = bounded["discover"]["cost_usd_interval"]
                dlo, dhi = bounded["doc"]["cost_usd_interval"]
                bounded["doc_minus_discover_usd_interval"] = [dlo - rhi, dhi - rlo]
                bounded["saving_usd_interval"] = [rlo - dhi, rhi - dlo]
                service_groups[key].append(bounded)
        if any(state["status"] != "done" for state in loaded.values()) or not bills_complete:
            excluded.append({"cell": key, "seed": pair[0]["seed"],
                             "statuses": {arm: state["status"] for arm, state in loaded.items()},
                             "bills_complete": bills_complete,
                             "serving_complete": serving_complete,
                             "excluded_from": "exact_cost_only" if serving_complete else "serving_and_exact_cost",
                             "ledger": {row["condition"]: recorded_cost.get(row["id"]) for row in pair}})
            continue
        values = {arm: dict(state["result"]) for arm, state in loaded.items()}
        for row in pair:
            arm = row["condition"]
            values[arm]["episode_reported_usd"] = values[arm]["total_cost_usd"]
            values[arm]["total_cost_usd"] = recorded_cost[row["id"]]["actual_usd"]
            values[arm]["token_split"] = token_totals(usages[row["id"]])
            values[arm]["bill_matches_episode"] = abs(values[arm]["total_cost_usd"] - values[arm]["episode_reported_usd"]) < 1e-7
        values["seed"] = pair[0]["seed"]
        values["binding_sha256"] = loaded["discover"].get("binding_sha256")
        groups[key].append(values)
    cells = {key: summarize(groups.get(key, [])) for key in service_groups}
    for key, pairs in groups.items():
        cells[key]["seeds"] = [p["seed"] for p in pairs]
        cells[key]["distinct_binding_n"] = len({p["binding_sha256"] for p in pairs})
        cells[key]["exact_cost_distinct_binding_n"] = cells[key]["distinct_binding_n"]
        cells[key]["token_split"] = {}
        cells[key]["actual_usd_total"] = {}
        for arm in ("discover", "doc"):
            selected = [row for row in rows if f"{row['model']}/{row['family']}" == key and
                        row["condition"] == arm and row["seed"] in cells[key]["seeds"]]
            cells[key]["token_split"][arm] = token_totals([u for row in selected for u in usages[row["id"]]])
            cells[key]["actual_usd_total"][arm] = sum(p[arm]["total_cost_usd"] for p in pairs)
        cells[key]["pairs"] = [
            {"seed": p["seed"], "binding_sha256": p["binding_sha256"],
             "discover_usd": p["discover"]["total_cost_usd"], "doc_usd": p["doc"]["total_cost_usd"],
             "saving_usd": p["discover"]["total_cost_usd"] - p["doc"]["total_cost_usd"],
             "saving_fraction": 1 - p["doc"]["total_cost_usd"] / p["discover"]["total_cost_usd"] if p["discover"]["total_cost_usd"] else None,
             "discover_success": p["discover"]["success"], "doc_success": p["doc"]["success"],
             "bills_match_episode": p["discover"]["bill_matches_episode"] and p["doc"]["bill_matches_episode"]}
            for p in pairs]
    for key, pairs in service_groups.items():
        cells[key]["complete_serving"] = serving_summary(pairs)
        cells[key]["distinct_binding_n"] = cells[key]["complete_serving"]["distinct_binding_n"]
        exact_n = len(groups.get(key, []))
        cells[key]["exact_cost_subset_n"] = exact_n
        cells[key]["exact_cost_selection_note"] = (
            f"Exact cost means and bootstrap intervals use {exact_n} of {len(pairs)} complete serving pairs. "
            "Receipt availability can select a different subset; use the all-serving-pair cost bounds before claiming a cost advantage."
        )
    result = {"spec_sha256": spec["spec_sha256"], "updated_unix": time.time(), "planned_episodes": len(rows),
              "complete_pairs": sum(len(p) for p in service_groups.values()),
              "complete_serving_pairs": sum(len(p) for p in service_groups.values()),
              "exact_cost_pairs": sum(len(p) for p in groups.values()), "cells": cells,
              "excluded_pairs": excluded,
              "all_requests_actual_usd": sum(x["actual_usd"] for x in recorded_cost.values()),
              "all_requests_unresolved_reserved_usd": sum(x["unresolved_reserved_usd"] for x in recorded_cost.values()),
              "request_status_counts": dict(call_states),
              "all_requests_token_split": token_totals([u for episode in usages.values() for u in episode]),
              "notes": ["Every complete serving pair retains success outcomes and conservative cost bounds, including unknown physical-attempt bills.",
                        "Exact cost means and confidence intervals use only the bill-complete subset and must not imply an all-pair cost advantage.",
                        "All request spending, including incomplete episodes, remains in the ledger total.",
                        "Costs use provider-reported USD, without floor subtraction or cache imputation.",
                        "No claim of equal success is inferred from lower cost alone."]}
    atomic_json(out / "analysis.json", result)
    report = ["# Matched Android serving analysis", "",
              f"Complete serving pairs: {result['complete_pairs']} / {len(rows)//2}; exact-cost pairs: {result['exact_cost_pairs']}.",
              f"All requests, including incomplete episodes: USD {result['all_requests_actual_usd']:.6f} known bills; USD {result['all_requests_unresolved_reserved_usd']:.6f} unresolved reservations.", "",
              "The table includes all complete serving pairs, with billing intervals that retain every unknown request reservation. Exact means and bootstrap intervals in the JSON use the bill-complete subset only. Both arms share task parameters and step caps.", "",
              "| Cell | Serving pairs / distinct bindings / exact-cost pairs | Discover mean USD bounds | Doc mean USD bounds | Discover / doc successes | Doc minus discover USD bounds |",
              "|---|---:|---:|---:|---:|---:|"]
    for key, cell in cells.items():
        service = cell["complete_serving"]
        def interval(values):
            return f"[{values[0]:.6f}, {values[1]:.6f}]"
        report.append(f"| {key} | {service['n_pairs']} / {service['distinct_binding_n']} / {cell['exact_cost_subset_n']} | {interval(service['mean_cost_usd_intervals']['discover'])} | {interval(service['mean_cost_usd_intervals']['doc'])} | {service['success_counts']['discover']} / {service['success_counts']['doc']} | {interval(service['mean_doc_minus_discover_usd_interval'])} |")
    report += ["", "The JSON report contains per-pair cost savings, actual USD totals, prompt/completion/cache token splits, and paired success differences. Missing cache counts remain missing; no cache share is imputed.",
               "", "At most five distinct bindings are available per cell. Paired bootstrap intervals are descriptive and do not support admission-rate or model-build inference. No interval is reported for a one-pair cell.", "",
               "Pending, interrupted and censored pairs are excluded from serving comparisons. Complete serving pairs with unknown bills retain cost bounds and success outcomes, but are excluded from exact-cost means and intervals. All spending remains in the ledger total."]
    (out / "analysis.md").write_text("\n".join(report) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = analyze(args.out)
    print(json.dumps({"complete_pairs": result["complete_pairs"], "completed_cells": len(result["cells"]),
                      "all_requests_actual_usd": result["all_requests_actual_usd"]}, indent=2))


if __name__ == "__main__":
    main()
