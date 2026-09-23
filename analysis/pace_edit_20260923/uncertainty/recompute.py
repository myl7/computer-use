"""Recompute table uncertainty from frozen records, with no simulator/API calls.

Run with Python 3 from any working directory. All outputs stay beside this file.
The implementation uses the standard library only. Bootstrap draws preserve
policy pairing, resample repetitions rather than tasks, and keep conditions fixed.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
B = 2000
SEEDS = {8: 20260923008, 10: 20260922017}
SENSITIVITY_SEED = 20260923041
MEASUREMENT_SEED = 20260923071
MAIN = "safe_projected_025"
MODELS = ["z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp", "qwen/qwen3.8-flash"]
BASE = "analysis/safe_projected_paper_20260922/evaluation-evidence.json"
EXTRA = "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/evidence.json"
CONTROLS = "experimental-results/guiexp/t2_sim_v3/pace_review_controls_20260923/evidence.json"
SENSITIVITY = "experimental-results/guiexp/t2_sim_v3/pace_price_sensitivity_20260923/evidence.json"
MEASUREMENT = "paper/measurement_update_20260918.json"
PROVENANCE = {}
CHECKS = defaultdict(int)
OSWORLD_RELEASE_PATHS = frozenset(
    f"experimental-results/guiexp_osworld/{model}/{family}/build.json"
    for model in (
        "deepseek_deepseek-v4-flash-vision-exp",
        "qwen_qwen3.8-flash",
        "z-ai_glm-5.3-flash",
    )
    for family in ("CalcTableSave", "WriterMemoSave")
)
HOME_USER = re.compile(r"(?P<prefix>/(?:Users|home)/)[^/\r\n]+(?=/)")
RELEASE_MANIFEST = OUT / "osworld_release_integrity.json"


def canonical_osworld(data, fields):
    """Mask only the username component at approved JSON string locations."""
    for field in fields:
        target = data
        for part in field[:-1]:
            target = target[part]
        last = field[-1]
        value = target[last]
        if not isinstance(value, str):
            raise ValueError("Approved OSWorld path field is not a string")
        masked, count = HOME_USER.subn(r"\g<prefix><USER>", value)
        if count != 1:
            raise ValueError("Approved OSWorld path field lacks one home-directory username")
        target[last] = masked
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def osworld_release_manifest():
    manifest = json.loads(RELEASE_MANIFEST.read_text())
    if manifest.get("schema") != "pace-osworld-release-integrity/1" or set(manifest.get("files", {})) != OSWORLD_RELEASE_PATHS:
        raise ValueError("OSWorld release integrity manifest has an unexpected file set")
    return manifest["files"]


def read(source, expected_hash=None):
    path = ROOT / source
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if expected_hash is not None:
        if digest != expected_hash:
            if source not in OSWORLD_RELEASE_PATHS:
                raise AssertionError((source, "source hash changed"))
            entry = osworld_release_manifest()[source]
            if expected_hash != entry["original_sha256"]:
                raise AssertionError((source, "expected source hash differs from frozen release manifest"))
            try:
                canonical = canonical_osworld(json.loads(data), entry["username_path_fields"])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise AssertionError((source, "invalid approved home-path redaction")) from exc
            if canonical != entry["canonical_sha256"]:
                raise AssertionError((source, "scientific or billing content changed"))
            CHECKS["home_path_redactions_verified"] += 1
        CHECKS["source_hashes_checked"] += 1
    PROVENANCE[source] = digest
    return json.loads(data)


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8), (a, b)


def quantile(values, probability):
    ordered = sorted(values)
    z = probability * (len(ordered) - 1)
    lower = int(z)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (z - lower) * (ordered[upper] - ordered[lower])


def display(point, low, high):
    minus, plus = point - low, high - point
    return {
        "point_3dp": f"{point:.3f}",
        "interval_3dp": f"[{low:.3f}, {high:.3f}]",
        "asymmetric_deviation_3dp": f"^{{+{plus:.3f}}}_{{-{minus:.3f}}}",
        "degenerate_at_print_precision": f"{low:.3f}" == f"{high:.3f}",
        "interval_contains_point": low <= point <= high,
    }


def estimate(point, draws, method="paired percentile bootstrap"):
    low, high = quantile(draws, 0.025), quantile(draws, 0.975)
    return {
        "estimate": float(point), "ci95": [float(low), float(high)],
        "minus": float(point - low), "plus": float(high - point),
        "method": method, "bootstrap_samples": len(draws),
        "display": display(point, low, high),
    }


def indices(n, seed):
    rng = random.Random(seed)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(B)]


def boot_means(values, samples):
    n = len(values)
    return [sum(values[i] for i in ids) / n for ids in samples]


def load_online():
    base, extra, controls = read(BASE), read(EXTRA), read(CONTROLS)
    cells = {}
    for group, entries in base["cells"].items():
        if group not in ("original_grid", "fresh_grid", "real_arrivals"):
            continue
        cells[group] = []
        updates = [{c["key"]: c for c in study["cells"][group]} for study in (extra, controls)]
        assert all(set(u) == {c["key"] for c in entries} for u in updates)
        for entry in entries:
            raw = read(entry["source"], entry["source_sha256"])
            rows = raw["rows"].copy()
            sources = [entry["source"]]
            for update in updates:
                add = update[entry["key"]]
                more = read(add["source"], add["source_sha256"])
                assert more["input_hashes"] == raw["input_hashes"], entry["key"]
                CHECKS["paired_source_inputs_checked"] += 1
                sources.append(add["source"])
                for policy, observations in more["rows"].items():
                    if policy in rows:
                        assert len(rows[policy]) == len(observations)
                        for before, after in zip(rows[policy], observations):
                            close(before["cost"], after["cost"])
                            CHECKS["overlapping_policy_costs_checked"] += 1
                    rows[policy] = observations
            reps = entry["repetitions"]
            assert all(len(v) == reps for v in rows.values())
            agent_cost = statistics.mean(r["cost"] for r in rows["reactive"])
            for policy, reference in entry["policies"].items():
                close(statistics.mean(r["cost"] for r in rows[policy]) / agent_cost, reference["ratio_agent"])
                CHECKS["published_point_estimates_checked"] += 1
            cells[group].append({"key": entry["key"], "model": entry["model"],
                                 "pattern": entry["pattern"], "repetitions": reps,
                                 "sources": sources, "rows": rows})
    return cells, base


def summarize_online(cells, group_seeds=None):
    summaries, cell_results = {}, {}
    for group, entries in cells.items():
        reps = entries[0]["repetitions"]
        assert all(c["repetitions"] == reps for c in entries)
        seed = group_seeds[group] if group_seeds else SEEDS[reps]
        samples = indices(reps, seed)
        policies = sorted(entries[0]["rows"])
        assert all(set(c["rows"]) == set(policies) for c in entries)
        aggregates = {}
        for model in MODELS + ["all_models"]:
            aggregates[model] = {p: {"agent": [0.0] * B, "pace": [0.0] * B,
                                    "max": [0.0] * B, "points": [], "pace_points": []}
                                 for p in policies}
        results = []
        for entry in entries:
            costs = {p: [r["cost"] for r in entry["rows"][p]] for p in policies}
            means = {p: statistics.mean(v) for p, v in costs.items()}
            boot = {p: boot_means(v, samples) for p, v in costs.items()}
            record = {k: v for k, v in entry.items() if k != "rows"}
            record["policies"] = {}
            for policy in policies:
                point = means[policy] / means["reactive"]
                pace_point = means[policy] / means[MAIN]
                ratio = [x / y for x, y in zip(boot[policy], boot["reactive"])]
                pace_ratio = [x / y for x, y in zip(boot[policy], boot[MAIN])]
                prefix = [r.get("prefix_ratio") for r in entry["rows"][policy]
                          if r.get("prefix_ratio") is not None]
                record["policies"][policy] = {
                    "ratio_agent": estimate(point, ratio),
                    "ratio_pace": estimate(pace_point, pace_ratio),
                    "mean_cost": means[policy],
                    "max_observed_prefix_ratio": max(prefix) if prefix else None,
                }
                for model in (entry["model"], "all_models"):
                    accum = aggregates[model][policy]
                    accum["points"].append(point)
                    accum["pace_points"].append(pace_point)
                    accum["agent"] = [a + b for a, b in zip(accum["agent"], ratio)]
                    accum["pace"] = [a + b for a, b in zip(accum["pace"], pace_ratio)]
                    accum["max"] = [max(a, b) for a, b in zip(accum["max"], ratio)]
            results.append(record)
        summaries[group] = {"bootstrap_seed": seed, "repetitions_per_condition": reps, "by_model": {}}
        for model, policies_data in aggregates.items():
            summaries[group]["by_model"][model] = {}
            for policy, accum in policies_data.items():
                count = len(accum["points"])
                if not count:
                    continue
                summaries[group]["by_model"][model][policy] = {
                    "conditions": count,
                    "mean_ratio_agent": estimate(statistics.mean(accum["points"]),
                                                 [v / count for v in accum["agent"]]),
                    "mean_ratio_pace": estimate(statistics.mean(accum["pace_points"]),
                                                [v / count for v in accum["pace"]]),
                    "max_condition_mean_ratio_agent": {
                        "estimate": max(accum["points"]), "ci95": None,
                        "reporting": "Descriptive maximum over fixed tested condition means. No worst-case confidence claim.",
                        "bootstrap_range_diagnostic": estimate(max(accum["points"]), accum["max"]),
                    },
                }
        cell_results[group] = results
        print(f"Summarized {group}: {len(entries)} conditions, {len(policies)} policies.", flush=True)
    return {"summaries": summaries, "cells": cell_results}


def load_sensitivity():
    evidence = read(SENSITIVITY)
    groups = defaultdict(list)
    for entry in evidence["cells"]:
        raw = read(entry["source"], entry["source_sha256"])
        original = read(entry["original_source"], entry["original_source_sha256"])
        assert [x["original"] for x in raw["input_hashes"]] == original["input_hashes"]
        CHECKS["sensitivity_original_pairing_checked"] += 1
        # Reassigning cost profiles by recurrence deliberately changes the
        # agent reference. Its own analytical-reference checks replace equality
        # with the historical allocation.
        for rep, (new, old) in enumerate(zip(raw["rows"]["reactive"], original["rows"]["reactive"])):
            check = raw["checks"][rep]
            assert check["analytical_reference_matches"] and check["reference_matches"]
            if raw["input_hashes"][rep]["profile_assignment_preserved"]:
                close(new["cost"], old["cost"])
        groups[entry["scenario"]].append({"key": entry["key"], "model": entry["model"],
                                         "pattern": entry["pattern"], "repetitions": entry["repetitions"],
                                         "sources": [entry["source"], entry["original_source"]],
                                         "rows": raw["rows"]})
    return groups, evidence


def binomial_cdf(k, n, probability):
    return sum(math.comb(n, i) * probability ** i * (1 - probability) ** (n - i)
               for i in range(k + 1))


def cp_interval(successes, n, alpha=0.05):
    def invert(k, target):
        low, high = 0.0, 1.0
        for _ in range(80):
            mid = (low + high) / 2
            if binomial_cdf(k, n, mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2
    return [0.0 if successes == 0 else invert(successes - 1, 1 - alpha / 2),
            1.0 if successes == n else invert(successes, alpha / 2)]


def count_record(successes, n):
    low, high = cp_interval(successes, n)
    return {"successes": successes, "trials": n, "estimate": successes / n,
            "count_display": f"{successes}/{n}", "ci95": [low, high],
            "method": "Clopper-Pearson interval under independent Bernoulli working model",
            "display": display(successes / n, low, high),
            "scope": "Conditional on this artifact and binding protocol. Does not establish IID attempts or future robustness."}


def gate_count_record(successes, n):
    return {"successes": int(successes), "trials": n, "estimate": successes / n,
            "count_display": f"{int(successes)}/{n}", "ci95": None,
            "reporting": "Exact observed count. Candidate selection or repair uses these bindings, so a binomial test-set CI is not claimed."}


def weighted_tokens(usage, model, prices):
    p = prices[model]
    prompt, cache, completion = (usage.get(k) or 0 for k in ("prompt_tokens", "cached_tokens", "completion_tokens"))
    return prompt - cache + cache * p["p_c"] / p["p_in"] + completion * p["p_o"] / p["p_in"]


def measurement_uncertainty():
    measurement = read(MEASUREMENT)
    prices = measurement["prices"]
    row_map = {(r["model"], r["family"]): r for r in measurement["rows"] + measurement["qwen_rows"]}
    serving, price_records = [], []
    paired = []
    for row in row_map.values():
        source = row["source"]
        read(source, measurement["source_sha256"].get(source))
        record = {k: row[k] for k in ("model", "family", "platform", "admitted", "source")}
        record["agent_cost"] = {"estimate": row["c"], "ci95": None, "episodes": row["n_exploration_episodes"],
                                "reason": "Adapted exploration episodes include retry/reflection history and sometimes imputed cache counts. No IID sampling CI is claimed."}
        record["saving_share"] = {"estimate": row["program_share"], "ci95": None,
                                  "reason": "Descriptive plug-in estimate using exploration mean and recorded deployment outcomes. No population CI for adapted exploration costs."}
        if row["admitted"]:
            slug = row["model"].replace("/", "_")
            deploy_source = row.get("deploy_source") or f"experimental-results/guiexp_android/t16_build/{slug}/{row['family']}/deploy.json"
            deploy = read(deploy_source, measurement["source_sha256"].get(deploy_source))
            uses = deploy["uses"]
            if row["platform"] == "Android":
                costs = [u["cost_usd"] / prices[row["model"]]["p_in"] for u in uses]
            else:
                costs = [sum(weighted_tokens(call, row["model"], prices) for call in u["calls_detail"]) for u in uses]
            close(statistics.mean(costs), row["d"])
            seed = MEASUREMENT_SEED + int(hashlib.sha256((row["model"] + row["family"]).encode()).hexdigest()[:7], 16)
            record["program_cost"] = estimate(statistics.mean(costs), boot_means(costs, indices(len(costs), seed)), "percentile bootstrap over recorded bindings")
            record["program_cost"]["sample_sd"] = statistics.stdev(costs)
            record["program_cost"]["n"] = len(costs)
            record["program_failure"] = count_record(sum(not u["success"] for u in uses), len(uses))
            record["deploy_source"] = deploy_source
        else:
            record["program_cost"] = record["program_failure"] = None
        serving.append(record)
        measured_cost = row.get("measured", {}).get("C_with_repair", row["C"])
        price_records.append({"model": row["model"], "family": row["family"],
                              "attempt_cost": {"estimate": measured_cost, "ci95": None,
                                               "reason": "One recorded attempt charge, not an estimate across independent attempts.",
                                               "partial": bool(row.get("C_partial"))},
                              "initial_pass": None if row["gates"][2] is None else gate_count_record(row["gates"][2], 5),
                              "final_pass": None if row["final_gate"] is None else gate_count_record(int(row["final_gate"]), 5),
                              "payback_marginal": {"estimate": row["nstar"], "ci95": None},
                              "payback_inclusive": {"estimate": row["nstar_incl_3c"], "ci95": None}})
    for published in measurement["paired_replays"] + measurement["qwen_paired_replays"]:
        model, family = published["model"], published["family"]
        row = row_map[(model, family)]
        slug = model.replace("/", "_")
        directory = ROOT / f"experimental-results/guiexp_android/t21_paired_replay/{slug}/{family}"
        paths = sorted(directory.glob("use_*/summary.json"))
        records = [read(str(p.relative_to(ROOT)), measurement["source_sha256"].get(str(p.relative_to(ROOT)))) for p in paths]
        deploy_source = f"experimental-results/guiexp_android/t16_build/{slug}/{family}/deploy.json"
        deploy = read(deploy_source, measurement["source_sha256"].get(deploy_source))["uses"]
        assert len(records) == len(deploy) == 30
        a, p, failures, agent_success = [], [], [], []
        for entry in records:
            use = deploy[entry["use_index"]]
            assert entry["goal"] == use["goal"] and entry["binding"] == use["expected"]
            a.append(weighted_tokens(entry["usage"], model, prices) - row["floor"])
            p.append(use["cost_usd"] / prices[model]["p_in"])
            failures.append(int(not use["success"]))
            agent_success.append(int(bool(entry["success"])))
            CHECKS["measurement_bindings_paired"] += 1
        ac, pc, q = statistics.mean(a), statistics.mean(p), statistics.mean(failures)
        close(ac, published["c"])
        close(pc, published["d"])
        close(1 - q - pc / ac, published["share"])
        close(statistics.stdev(a) / ac, published["cv"])
        seed = MEASUREMENT_SEED + int(hashlib.sha256((model + family).encode()).hexdigest()[:7], 16)
        samples = indices(30, seed)
        ba, bp, bq = (boot_means(v, samples) for v in (a, p, failures))
        share_draws = [1 - failure - prog / agent for failure, prog, agent in zip(bq, bp, ba)]
        # This is a conditional empirical interval, including recorded failures.
        # It cannot reveal a failure mode absent from all recorded bindings.
        share_est = estimate(published["share"], share_draws, "paired percentile bootstrap over matched bindings")
        share_est["zero_observed_failure_warning"] = not any(failures)
        share_est["scope"] = "Uncertainty under the empirical binding distribution only. Zero failures cannot rule out unobserved failure risk. Read with the binomial failure interval."
        agent_est = estimate(ac, ba, "percentile bootstrap over replay bindings")
        agent_est["sample_sd"] = statistics.stdev(a)
        agent_est["mean_plus_minus_sd_k"] = f"{ac / 1000:.1f} ± {statistics.stdev(a) / 1000:.1f}"
        agent_est["scope"] = "Conditional on recorded calibration floor and price weights. Cache imputation and baseline-estimation error are not resampled."
        paired.append({"model": model, "family": family, "n": 30, "bootstrap_seed": seed,
                       "agent_cost": agent_est, "program_cost": estimate(pc, bp, "paired percentile bootstrap over replay bindings"),
                       "agent_cv": {"estimate": published["cv"], "ci95": None, "reporting": "Descriptive coefficient of variation."},
                       "agent_success": count_record(sum(agent_success), 30),
                       "program_success": count_record(30 - sum(failures), 30),
                       "program_failure": count_record(sum(failures), 30),
                       "saving_share": share_est, "sources": [str(p.relative_to(ROOT)) for p in paths] + [deploy_source]})
    repeat = []
    for row in measurement["repeated_and_extraction"] + measurement["qwen_repeated_and_extraction"]:
        repeat.append({"model": row["model"], "family": row["family"],
                       "verified": {"successes": row["verified_initial"] + row["verified_extra"], "attempts": 3,
                                    "ci95": None, "provider_errors": row["provider_errors"],
                                    "reason": "Three attempts mix the initial acquisition and two repeats sharing traces/translation. Provider interruptions are separate. No IID verification-probability CI."},
                       "supplied": count_record(row["injection_now"], 5),
                       "extracted": count_record(row["extraction"], 5)})
    return {"serving": serving, "compilation": price_records, "paired_replays": paired,
            "repeat_verification": repeat,
            "working_model_examples": {f"{s}/{n}": count_record(s, n) for s, n in [(0, 30), (1, 30), (6, 30), (5, 5), (0, 5)]}}


def main():
    cells, base = load_online()
    online = summarize_online(cells)
    # Exact agreement with the already-reported real-log bootstrap design.
    for policy in ("safe_projected_025", "projected", "projected_cap", "safe_earliest_025"):
        old = base["groups"]["real_arrivals"][policy] if "groups" in base else base["summaries"]["real_arrivals"][policy]
        new = online["summaries"]["real_arrivals"]["by_model"]["all_models"][policy]["mean_ratio_agent"]
        close(new["estimate"], old["mean_ratio_agent"])
        for left, right in zip(new["ci95"], old["mean_ratio_agent_ci95"]):
            close(left, right)
        CHECKS["published_real_log_intervals_reproduced"] += 1
    sensitivity, reference = load_sensitivity()
    sensitivity_out = summarize_online(sensitivity, {group: SENSITIVITY_SEED for group in sensitivity})
    for group, summary in sensitivity_out["summaries"].items():
        for model, policies in summary["by_model"].items():
            for policy, record in policies.items():
                old = reference["groups"][group][policy] if model == "all_models" else reference["by_model"][model][group][policy]
                close(record["mean_ratio_agent"]["estimate"], old["mean_ratio_agent"])
                if old.get("mean_ratio_agent_ci95"):
                    for left, right in zip(record["mean_ratio_agent"]["ci95"], old["mean_ratio_agent_ci95"]):
                        close(left, right)
                    CHECKS["published_sensitivity_intervals_reproduced"] += 1
    output = {
        "schema": "pace-table-uncertainty-v1", "models_in_display_order": MODELS,
        "method": {"bootstrap_samples": B, "confidence_level": 0.95,
                   "point_estimate": "Equal-weight mean of condition-wise ratios of repetition mean costs. A single condition uses ratio of means, never mean of per-repetition ratios.",
                   "resampling": "Shared repetition indices across all policies, models and fixed conditions within a study. No resampling of condition identity, families, tasks or cost constants.",
                   "seed_by_repetition_count": SEEDS, "sensitivity_seed": SENSITIVITY_SEED,
                   "scope": "Conditional simulation/mapping Monte Carlo uncertainty. Excludes uncertainty of measured GUI costs, imputed parameters, benchmark choice and data-dependent protocol selection.",
                   "coverage": "Pointwise percentile intervals. No simultaneous coverage or multiple-comparison correction.",
                   "small_sample": "Eight or ten repetitions give a coarse empirical bootstrap. Intervals should not support fine-grained ranking claims.",
                   "presentation": "Use estimate with [lower, upper] or asymmetric +upper/-lower deviations. Do not replace percentile intervals with a symmetric half-width."},
        "online": online, "sensitivity": sensitivity_out,
    }
    (OUT / "online-uncertainty.json").write_text(json.dumps(output, indent=2) + "\n")
    measurement = measurement_uncertainty()
    (OUT / "measurement-uncertainty.json").write_text(json.dumps(measurement, indent=2) + "\n")
    table_data = {"schema": output["schema"], "models_in_display_order": MODELS,
                  "method": output["method"], "online_summary": online["summaries"],
                  "real_cells": online["cells"]["real_arrivals"],
                  "sensitivity_summary": sensitivity_out["summaries"],
                  "measurement": measurement}
    (OUT / "table-data.json").write_text(json.dumps(table_data, indent=2) + "\n")
    provenance = {"source_sha256": PROVENANCE, "checks": dict(CHECKS),
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "outputs_sha256": {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
                                     for name in ("online-uncertainty.json", "measurement-uncertainty.json", "table-data.json")}}
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"checks": dict(CHECKS), "sources": len(PROVENANCE)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
