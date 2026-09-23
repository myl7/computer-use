"""Recompute paper evidence from frozen results without running a simulation.

All writes remain in this analysis directory. Ratios use cell means, and cells
receive equal weight. The bootstrap resamples stored paired repetitions only.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DATA = ROOT / "experimental-results/guiexp/t2_sim_v3/protocol_explore_20260922_v2"
N_BOOTSTRAP = 2000
BOOTSTRAP_SEED = 20260922017
MAIN = "safe_projected_025"
SELECTED = ["reactive", "earliest", "earliest_cap", "fixed10_cap", "projected", "projected_cap", "safe_earliest_025", MAIN, "safe_projected_100"]
LABELS = {
    "reactive": "Agent only", "earliest": "Earliest", "earliest_cap": "Earliest + old check",
    "fixed10_cap": "Fixed10 + old check", "projected": "Projected (unprotected)",
    "projected_cap": "Projected + old check", "safe_earliest_025": "SafeEarliest (0.25)",
    MAIN: "SafeProjected (0.25)", "safe_projected_100": "SafeProjected (1.0)",
    "safe_success10_025": "SafeSuccess10 (0.25)", "safe_history_025": "SafeHistory (0.25)",
}
GROUP = {"fresh_grid": "fresh_grid", "paired_validation_grid": "original_grid", "paired_validation_real": "real_arrivals", "paired_development": "known_development", "paired_boundary": "boundary"}
COMPONENTS = ["reactive_tokens", "extraction_tokens", "compile_tokens", "router_tokens"]


def mean(xs):
    return statistics.mean(xs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path):
    return str(path.relative_to(ROOT))


def percentile(xs, q):
    vals = sorted(xs)
    z = q * (len(vals) - 1)
    lo = int(z)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] * (hi - z) + vals[hi] * (z - lo) if lo != hi else vals[lo]


def interval(xs):
    return [percentile(xs, 0.025), percentile(xs, 0.975)]


def epsilon(policy):
    if not policy.startswith("safe_"):
        return None
    return int(policy.rsplit("_", 1)[1]) / 100


stream_spec = importlib.util.spec_from_file_location("evidence_streams", ROOT / "code/t2sim/streams.py")
streams = importlib.util.module_from_spec(stream_spec)
stream_spec.loader.exec_module(streams)
stream_meta = {}
cells = defaultdict(list)
paths = sorted((DATA / "cells").glob("*.json")) + sorted((DATA / "original_grid_crosscheck/cells").glob("*.json"))
validated_rows = 0
for path in paths:
    raw = json.loads(path.read_text())
    group = GROUP[raw["suite"]]
    spec = raw["spec"]
    n = spec.get("n")
    if group == "real_arrivals":
        name = spec["pattern"]
        if name not in stream_meta:
            sequence = streams.load_stream(spec["stream_spec"])
            source = ROOT / spec["stream_spec"]["path"] if spec["stream_spec"]["format"] == "wiki_jsonl" else streams.OLD_REAL_STREAMS
            stream_meta[name] = {
                "arrivals": len(sequence), "families": len(set(sequence)),
                "source": rel(source), "source_sha256": sha(source),
                "stream_sha256": hashlib.sha256(json.dumps(sequence, sort_keys=True).encode()).hexdigest(),
                "window": spec["stream_spec"].get("window"),
            }
        n = stream_meta[name]["arrivals"]
        assert all(x["stream"] == stream_meta[name]["stream_sha256"] for x in raw["input_hashes"])
    if n is None and group == "known_development":
        n = spec.get("n_arrivals")
    assert len(raw["rows"]) == 22
    assert all(len(rows) == spec["reps"] for rows in raw["rows"].values())
    costs = {p: mean([r["cost"] for r in rows]) for p, rows in raw["rows"].items()}
    best = min(costs.values())
    policies = {}
    for p, rows in raw["rows"].items():
        for row in rows:
            assert math.isclose(row["cost"], sum(row["components"][k] for k in COMPONENTS), rel_tol=1e-10, abs_tol=1e-7)
            validated_rows += 1
        quality = mean([r["quality"] for r in rows])
        agent_quality = mean([r["quality"] for r in raw["rows"]["reactive"]])
        prefix = [r["prefix_ratio"] for r in rows if r["prefix_ratio"] is not None]
        ep = epsilon(p)
        exact_overshoots = sum(x > 1 + ep for x in prefix) if ep is not None else None
        # All retained baselines have A_t >= 1. The code tolerance implies
        # a ratio tolerance of at most 1e-8 * (1 + epsilon).
        violations = sum(x > (1 + ep) * (1 + 1e-8) + 1e-12 for x in prefix) if ep is not None else None
        assert not violations
        policies[p] = {
            "mean_cost": costs[p], "ratio_agent": costs[p] / costs["reactive"],
            "ratio_best22": costs[p] / best,
            "mean_success_rate": quality, "success_rate_delta_agent": quality - agent_quality,
            "cost_per_success": costs[p] / (n * quality) if n and quality else None,
            "cost_per_success_ratio_agent": costs[p] / costs["reactive"] * agent_quality / quality if quality else None,
            "max_prefix_ratio": max(prefix) if prefix else None,
            "prefix_recorded_runs": len(prefix), "prefix_exact_overshoot_runs": exact_overshoots,
            "prefix_tolerance_violation_runs": violations,
            "max_final_run_ratio_agent": max(r["cost"] / a["cost"] for r, a in zip(rows, raw["rows"]["reactive"])),
            "mean_attempts": mean([r["attempts"] for r in rows]),
            "mean_failed_spend": mean([r["failed_spend"] for r in rows]),
            "mean_compile_rejections": mean([r["safety_rejections"] for r in rows]),
            "mean_components": {k: mean([r["components"][k] for r in rows]) for k in COMPONENTS},
        }
        assert math.isclose(policies[p]["ratio_agent"], raw["summary"][p]["ratio_agent"], rel_tol=1e-11)
        assert math.isclose(policies[p]["ratio_best22"], raw["summary"][p]["ratio_best_all"], rel_tol=1e-11)
    cells[group].append({
        "key": raw["key"], "source": rel(path), "source_sha256": sha(path),
        "suite": raw["suite"], "model": spec.get("model"), "pattern": spec.get("pattern"),
        "arrivals": n, "repetitions": spec["reps"],
        "parameters": {k: spec[k] for k in ("admission", "world", "families", "n", "h", "m", "tau0", "ttl", "k_min", "price_scale", "cf_scale", "silent", "fallback_mult", "seed") if k in spec},
        "best22_cost": best, "best22_policies": [p for p, c in costs.items() if math.isclose(c, best, rel_tol=1e-10)],
        "policies": policies, "_raw": raw,
    })

for group in cells:
    cells[group].sort(key=lambda c: c["key"])
assert {g: len(cs) for g, cs in cells.items()} == {"original_grid": 300, "fresh_grid": 300, "real_arrivals": 12, "known_development": 27, "boundary": 240}

summaries = {}
for group, cs in cells.items():
    summaries[group] = {}
    for p in cs[0]["policies"]:
        ps = [c["policies"][p] for c in cs]
        prefix = [s["max_prefix_ratio"] for s in ps if s["max_prefix_ratio"] is not None]
        worst_agent = max(cs, key=lambda c: c["policies"][p]["ratio_agent"])
        worst_best22 = max(cs, key=lambda c: c["policies"][p]["ratio_best22"])
        ep = epsilon(p)
        summaries[group][p] = {
            "cells": len(cs), "runs": sum(c["repetitions"] for c in cs),
            "mean_ratio_agent": mean([s["ratio_agent"] for s in ps]),
            "min_cell_ratio_agent": min(s["ratio_agent"] for s in ps),
            "max_cell_ratio_agent": max(s["ratio_agent"] for s in ps),
            "mean_ratio_best22": mean([s["ratio_best22"] for s in ps]),
            "worst_ratio_best22": max(s["ratio_best22"] for s in ps),
            "mean_cost_per_success_ratio_agent": mean([s["cost_per_success_ratio_agent"] for s in ps if s["cost_per_success_ratio_agent"] is not None]),
            "mean_success_rate_delta_agent": mean([s["success_rate_delta_agent"] for s in ps]),
            "saving_cells": sum(s["ratio_agent"] < 1 - 1e-10 for s in ps),
            "more_costly_than_agent_cells": sum(s["ratio_agent"] > 1 + 1e-10 for s in ps),
            "above_1_25_agent_cells": sum(s["ratio_agent"] > 1.25 + 1e-8 * 1.25 for s in ps),
            "above_2_agent_cells": sum(s["ratio_agent"] > 2 for s in ps),
            "max_prefix_ratio": max(prefix) if prefix else None,
            "prefix_recorded_runs": sum(s["prefix_recorded_runs"] for s in ps),
            "prefix_exact_overshoot_runs": sum(s["prefix_exact_overshoot_runs"] for s in ps) if ep is not None else None,
            "prefix_tolerance_violation_runs": sum(s["prefix_tolerance_violation_runs"] for s in ps) if ep is not None else None,
            "prefix_tolerance_violation_cells": sum(bool(s["prefix_tolerance_violation_runs"]) for s in ps) if ep is not None else None,
            "max_final_run_ratio_agent": max(s["max_final_run_ratio_agent"] for s in ps),
            "mean_attempts_per_cell": mean([s["mean_attempts"] for s in ps]),
            "mean_compile_rejections_per_cell": mean([s["mean_compile_rejections"] for s in ps]),
            "worst_agent_cell": worst_agent["key"], "worst_best22_cell": worst_best22["key"],
        }

# Share each bootstrap sample's repetition indices across all 12 fixed cells
# and across every policy, retaining pairing and equal cell weighting.
rng = random.Random(BOOTSTRAP_SEED)
resamples = [[rng.randrange(10) for _ in range(10)] for _ in range(N_BOOTSTRAP)]
boot = {p: defaultdict(list) for p in cells["real_arrivals"][0]["policies"]}
comparisons = ["projected", "projected_cap", "safe_earliest_025", "safe_projected_100"]
aggregate_pair_boot = {p: [] for p in comparisons}
for c in cells["real_arrivals"]:
    raw = c["_raw"]
    for p in c["policies"]:
        ratios, success_ratios, quality_deltas = [], [], []
        for ids in resamples:
            numerator = sum(raw["rows"][p][i]["cost"] for i in ids)
            denominator = sum(raw["rows"]["reactive"][i]["cost"] for i in ids)
            success = sum(raw["rows"][p][i]["quality"] for i in ids)
            agent_success = sum(raw["rows"]["reactive"][i]["quality"] for i in ids)
            ratios.append(numerator / denominator)
            success_ratios.append(numerator / denominator * agent_success / success)
            quality_deltas.append((success - agent_success) / len(ids))
        c["policies"][p]["ratio_agent_ci95"] = interval(ratios)
        c["policies"][p]["cost_per_success_ratio_agent_ci95"] = interval(success_ratios)
        c["policies"][p]["success_rate_delta_agent_ci95"] = interval(quality_deltas)
        boot[p]["ratio_agent"].append(ratios)
        boot[p]["cost_per_success_ratio_agent"].append(success_ratios)
        boot[p]["success_rate_delta_agent"].append(quality_deltas)
    for other in comparisons:
        draws = []
        for ids in resamples:
            numerator = sum(raw["rows"][MAIN][i]["cost"] for i in ids)
            denominator = sum(raw["rows"][other][i]["cost"] for i in ids)
            draws.append(numerator / denominator)
        aggregate_pair_boot[other].append(draws)

for p, metrics in boot.items():
    for metric, by_cell in metrics.items():
        samples = [mean([draws[b] for draws in by_cell]) for b in range(N_BOOTSTRAP)]
        summaries["real_arrivals"][p]["mean_" + metric + "_ci95"] = interval(samples)

paired = {}
for group, cs in cells.items():
    paired[group] = {}
    for other in cs[0]["policies"]:
        ratios = [c["policies"][MAIN]["mean_cost"] / c["policies"][other]["mean_cost"] for c in cs]
        reverse = [1 / x for x in ratios]
        record = {
            "cells": len(cs), "mean_ratio_safeprojected_to_comparator": mean(ratios),
            "max_ratio_safeprojected_to_comparator": max(ratios),
            "mean_delta_ratio_agent_safe_minus_comparator": mean([c["policies"][MAIN]["ratio_agent"] - c["policies"][other]["ratio_agent"] for c in cs]),
            "safe_cheaper_cells": sum(x < 1 - 1e-9 for x in ratios),
            "numerically_tied_cells": sum(abs(x - 1) <= 1e-9 for x in ratios),
            "safe_more_costly_cells": sum(x > 1 + 1e-9 for x in ratios),
            "comparator_at_least_twice_safe_cells": sum(x >= 2 for x in reverse),
            "max_comparator_over_safe": max(reverse),
            "comparator_at_least_twice_safe_cell_keys": [c["key"] for c, x in zip(cs, reverse) if x >= 2],
        }
        if group == "real_arrivals" and other in aggregate_pair_boot:
            draws = [mean([v[b] for v in aggregate_pair_boot[other]]) for b in range(N_BOOTSTRAP)]
            record["mean_ratio_safeprojected_to_comparator_ci95"] = interval(draws)
        paired[group][other] = record

# Verify recorded source provenance without importing or running an engine.
manifest_checks = []
for mp in [DATA.parent / "protocol_explore_20260922/config.json", DATA / "config.json"]:
    manifest = json.loads(mp.read_text())
    for name, expected in manifest["source_sha256"].items():
        path = ROOT / name
        manifest_checks.append({"manifest": rel(mp), "source": name, "recorded_sha256": expected, "current_sha256": sha(path), "matches": sha(path) == expected})
assert all(x["matches"] for x in manifest_checks)

boundary = next(c for c in cells["boundary"] if c["key"] == "boundary/n300/C2.0/F100.0/p1.0/h0.0")
boundary_description = {
    "source": boundary["source"], "key": boundary["key"],
    "model": "one family; c=1; d=0; C=2; C_fail=200; p=1; q=0; pi=1; h=0; m=tau0=0; n=300; k_min=3",
    "safeprojected_cost": boundary["policies"][MAIN]["mean_cost"],
    "safeprojected_prefix_ratio": boundary["policies"][MAIN]["max_prefix_ratio"],
    "earliest_cost": boundary["policies"]["earliest"]["mean_cost"],
    "ratio_best22": boundary["policies"][MAIN]["ratio_best22"],
    "explanation": "A failed attempt could cost 200, while the 0.25 ledger has at most 75 extra units without reuse. No attempt is funded. SafeProjected pays 300, equal to agent service. Earliest pays 3 agent services plus successful compilation at 2, total 5. This is no violation of the agent-relative guarantee.",
}

metric_definitions = {
    "raw_cost": "rows[policy][rep].cost = sum(rows[policy][rep].components[k] for k in reactive_tokens, extraction_tokens, compile_tokens, router_tokens); excludes penalty/harm.",
    "normalization": "Cost is in each model's price-weighted input-token units supplied by the original measurement reader. Absolute units are never pooled across models.",
    "ratio_agent": "mean_rep(rows[p].cost) / mean_rep(rows[reactive].cost), not mean_rep of cost ratios; denominator includes supplied agent cost c_f and common router tau0.",
    "ratio_best22": "mean_rep(rows[p].cost) / min over all 22 policies of mean_rep(rows[policy].cost); descriptive finite-rule best, not OPT or an executable comparator.",
    "mean_ratio_agent": "Arithmetic mean of ratio_agent over all cells in the named panel, giving every cell equal weight; every model has 4 real-log cells or 100 grid cells.",
    "mean_success_rate": "mean_rep(rows[p].quality); raw quality = successes / arrivals, as set by the simulator.",
    "cost_per_success": "mean_rep(rows[p].cost) / (arrivals * mean_rep(rows[p].quality)); ratio of totals across repetitions, not mean of run-level cost per success.",
    "cost_per_success_ratio_agent": "ratio_agent * mean_rep(reactive.quality) / mean_rep(p.quality).",
    "max_prefix_ratio": "max over stored rows[p][rep].prefix_ratio; each field is the engine's maximum K_t/A_t over completed arrival prefixes. Null means the source did not record this metric.",
    "prefix_tolerance_violation_runs": "Count saved runs with max_prefix_ratio > (1+epsilon)*(1+1e-8)+1e-12. All retained A_t>=1; implementation tolerance in cost is 1e-8*max(1,(1+epsilon)*A_t).",
    "prefix_exact_overshoot_runs": "Count saved runs above the exact 1+epsilon ratio, including harmless implementation-tolerance overshoots; reported separately from tolerance violations.",
    "prefix_recorded_runs": "Number of saved per-run maximum-prefix records, not number of newly simulated runs or individually reread prefixes.",
    "bootstrap": "2000 percentile resamples of 10 existing paired repetition indices, seed 20260922017; a draw uses the same indices for all 22 policies and all 12 real-log cells. Percentiles use linear interpolation. No new simulation, task stream, model call, or cost-profile sample.",
    "bootstrap_scope": "Conditional uncertainty over retained mappings and simulator random outcomes for these four fixed logs and supplied cost profiles. Does not cover measurement uncertainty, alternative logs, policy selection, or population deployment frequencies.",
    "pair_comparison": "Each ratio uses stored per-cell mean costs for SafeProjected-0.25 and the named comparator, then receives equal weight across cells; tied means within 1e-9 in cost ratio.",
    "twice_safe_cells": "Count cells with comparator mean cost / SafeProjected-0.25 mean cost >= 2, not a probability or guarantee.",
    "quality_scope": "All reported panels use silent=0 and fallback_mult=1. Detected failures use the paired agent outcome, so no-quality-loss relative to agent is built into this simulator. This is not independent deployment-quality evidence.",
}
result = {
    "schema": "safe-projected-paper-evidence/1", "date": "2026-09-22", "main_policy": MAIN,
    "source_root": rel(DATA), "selected_policies": SELECTED, "policy_labels": LABELS,
    "metric_definitions": metric_definitions,
    "design": {
        "original_grid": "All 300 original cells = 3 model profiles x 4 admission settings x 5 arrival patterns x 5 cost/drift/routing worlds, retrospectively crosschecked for 22 policies. Eight paired repetitions, 1200 arrivals, 30 recurrent families except sparse generation. Admission p is 0.9, 0.5, 0, or a per-family mixture over {0,0.1,0.5,0.9,1}. Patterns are uniform labels (named poisson in code), Zipf, bursts, regime shift, and sparse. Base h=0.02, m=91, tau0=368, TTL=100. Worlds are base, failure cost x5, both compilation prices x5, h=0.1, and m=1820.",
        "fresh_grid": "All 300 changed conditions have the same 3 x 4 x 5 x 5 structure, eight paired repetitions, 1800 arrivals, and 40 recurrent families except sparse generation. Admission p is 0.95, 0.35, 0, or a per-family mixture over {0,0.05,0.3,0.7,1}. Changed worlds use failure cost x3, both compilation prices x7, h=0.07, and m=910; base parameters match the original grid. Inherited world labels are not literal parameter values. Changes were selected before round-two evaluation but after round one; exploratory evidence, not independent confirmation of final selection.",
        "real_arrivals": "3 models x 4 existing arrival logs x 10 paired repetitions. Costs mapped to family IDs and admission probabilities assigned independently of original admission labels. These are simulations on existing recurrence patterns, not 12 deployment studies.",
        "known_development": "27 previously inspected synthetic cells, first 10 saved paired repetitions; retained separately and not counted as independent validation.",
        "boundary": "All 240 single-family price/horizon conditions with 3 repetitions; retained separately.",
        "scope_warning": "The old 109-cell paper_revision_20260922 study evaluates projected_cap, not SafeProjected. Its result table cannot be relabeled as the new method.",
    },
    "real_streams": stream_meta, "summaries": summaries, "paired_comparisons": paired,
    "boundary_counterexample": boundary_description,
    "checks": {"cells": len(paths), "policy_run_records_checked": validated_rows, "cost_components_reconcile": True, "stored_summary_means_match": True, "real_stream_hashes_match": True, "source_manifest_checks": manifest_checks},
    "cells": {g: [{k: v for k, v in c.items() if k != "_raw"} for c in cs] for g, cs in cells.items()},
}
(OUT / "evaluation-evidence.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")


def f(x, digits=3):
    return "--" if x is None else f"{x:.{digits}f}"


def ci(value, bounds):
    return f"{value:.3f} [{bounds[0]:.3f}, {bounds[1]:.3f}]"


lines = ["# SafeProjected evidence from frozen simulations", "", "This analysis changes no experiment or manuscript file.", "The main policy is `safe_projected_025`; `projected_cap` is the previous method.", "All 879 stored conditions and all 22 rules are retained in the accompanying JSON, with source file paths and SHA-256 hashes.", "", "## Metrics and comparison scope", ""]
lines += [f"- **{k}**: {v}" for k, v in metric_definitions.items()]
lines += ["", "## Panel definitions", ""]
lines += [f"- **{k}**: {v}" for k, v in result["design"].items()]
lines += ["", "## Existing-log aggregate (12 equally weighted cells)", "", "The confidence intervals use 2000 shared-index paired bootstrap resamples of stored repetitions.", "", "| Policy | Mean cost / agent [95% CI] | Max cell / agent | Max recorded prefix / agent | Cost per success / agent [95% CI] | Saving cells | Earliest cost >= 2x this policy |", "|---|---:|---:|---:|---:|---:|---:|"]
for p in SELECTED:
    s = summaries["real_arrivals"][p]
    twice = sum(c["policies"]["earliest"]["mean_cost"] / c["policies"][p]["mean_cost"] >= 2 for c in cells["real_arrivals"])
    lines.append(f"| {LABELS[p]} | {ci(s['mean_ratio_agent'], s['mean_ratio_agent_ci95'])} | {f(s['max_cell_ratio_agent'])} | {f(s['max_prefix_ratio'])} | {ci(s['mean_cost_per_success_ratio_agent'], s['mean_cost_per_success_ratio_agent_ci95'])} | {s['saving_cells']}/12 | {twice}/12 |")
lines += ["", "## Each existing-log condition", "", "Every cost ratio below uses the cell's agent-only mean as its denominator.", "Cost/success is normalized by the same cell's agent-only cost per success.", "", "| Model | Log | N | SafeProjected / agent [95% CI] | Cost/success / agent [95% CI] | Success rate | Quality change (pp) | Max prefix |", "|---|---|---:|---:|---:|---:|---:|---:|"]
for c in cells["real_arrivals"]:
    s = c["policies"][MAIN]
    lines.append(f"| {c['model']} | {c['pattern']} | {c['arrivals']} | {ci(s['ratio_agent'], s['ratio_agent_ci95'])} | {ci(s['cost_per_success_ratio_agent'], s['cost_per_success_ratio_agent_ci95'])} | {s['mean_success_rate']:.4f} | {100*s['success_rate_delta_agent']:.3f} | {s['max_prefix_ratio']:.9f} |")
lines += ["", "### Matched baselines in the same 12 conditions", "", "| Model | Log | Agent | Earliest | Earliest old check | Fixed10 old check | Projected | Projected old check | SafeEarliest .25 | SafeProjected .25 | SafeProjected 1.0 |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
for c in cells["real_arrivals"]:
    lines.append("| " + " | ".join([c["model"], c["pattern"]] + [f(c["policies"][p]["ratio_agent"]) for p in SELECTED]) + " |")
for group in ("original_grid", "fresh_grid"):
    lines += ["", f"## {group}: all 300 cells", "", "Mean and worst best22 ratios are secondary descriptive metrics. Their denominator is the lowest mean among the 22 retained rules in the same cell, not an offline optimum.", "", "| Policy | Mean / agent | Max cell / agent | Mean / best22 | Worst / best22 | Max saved prefix | Prefix violations / saved runs | Saving cells |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for p in SELECTED:
        s = summaries[group][p]
        audit = "--" if s['prefix_tolerance_violation_runs'] is None else f"{s['prefix_tolerance_violation_runs']}/{s['prefix_recorded_runs']}"
        lines.append(f"| {LABELS[p]} | {f(s['mean_ratio_agent'])} | {f(s['max_cell_ratio_agent'])} | {f(s['mean_ratio_best22'])} | {f(s['worst_ratio_best22'])} | {f(s['max_prefix_ratio'], 9)} | {audit} | {s['saving_cells']}/300 |")
lines += ["", "## Matched protection ablation and budget sensitivity", "", "Counts and ratios include every cell, including increased costs after protection.", "", "| Panel | Comparator | Safe / comparator (mean cell ratio) | Safe cheaper / tied / dearer | Mean change in cost/agent | Comparator >= 2x Safe |", "|---|---|---:|---:|---:|---:|"]
for group in ("original_grid", "fresh_grid", "real_arrivals"):
    for other in comparisons:
        s = paired[group][other]
        val = ci(s['mean_ratio_safeprojected_to_comparator'], s['mean_ratio_safeprojected_to_comparator_ci95']) if 'mean_ratio_safeprojected_to_comparator_ci95' in s else f(s['mean_ratio_safeprojected_to_comparator'])
        lines.append(f"| {group} | {LABELS[other]} | {val} | {s['safe_cheaper_cells']} / {s['numerically_tied_cells']} / {s['safe_more_costly_cells']} | {s['mean_delta_ratio_agent_safe_minus_comparator']:+.5f} | {s['comparator_at_least_twice_safe_cells']}/{s['cells']} |")
lines += ["", "The old check is a projected-savings check. It is not the realized-cost protection used by SafeProjected and SafeEarliest.", "Removing the safety layer also removes its early-manifest-removal control. The existing unprotected `projected` rule is the matched proposer comparison under that change in available control.", "", "## Recorded prefix checks, all panels", "", "These are checks of saved per-run maximum-prefix statistics; no trajectory was rerun.", "", "| Panel | Policy | Runs with stored maxima | Maximum | Exact-bound overshoots | Violations beyond code tolerance |", "|---|---|---:|---:|---:|---:|"]
for group in ("original_grid", "fresh_grid", "real_arrivals", "known_development", "boundary"):
    for p in ("safe_earliest_025", MAIN, "safe_projected_100"):
        s = summaries[group][p]
        lines.append(f"| {group} | {LABELS[p]} | {s['prefix_recorded_runs']} | {s['max_prefix_ratio']:.12f} | {s['prefix_exact_overshoot_runs']} | {s['prefix_tolerance_violation_runs']} |")
lines += ["", "## Retained boundary counterexample", "", f"Source: `{boundary_description['source']}`.", f"Parameters: {boundary_description['model']}.", boundary_description['explanation'], "", "This case limits a best-policy claim, not the stated agent-relative protection.", "", "## Evidence limitations and source check", "", f"- Reconciled all cost components in {validated_rows:,} stored policy-run records and all stored cell-mean ratios.", "- Real-stream lengths and content hashes match the saved inputs.", "- Every checked frozen manifest source hash matches the current source.", "- All 27 known-development cells remain separate from validation evidence.", "- The old 109-cell manuscript study is not SafeProjected evidence.", "- Earlier-grid and changed-grid outcomes influenced candidate selection. Intervals do not account for selection or new deployment settings.", "", "Note: The 1.25 bound is conditional on supplied cost references and valid enforceable charge bounds. These saved simulations do not turn historical mean costs into such bounds."]
(OUT / "evaluation-evidence.md").write_text("\n".join(lines) + "\n")

tex = ["% Draft tables generated from frozen results by prepare_evaluation_evidence.py.", "% Standalone analysis artifact only; no manuscript file is changed.", "% Requires booktabs. Cell-mean cost ratios use paired agent-only means.", "\\begin{table}[t]", "\\centering", "\\small", "\\begin{tabular}{lrrrr}", "\\toprule", "Policy & Mean cost / agent & Max cell / agent & Max prefix & Saving cells \\\\", "\\midrule"]
for p in SELECTED:
    s = summaries["real_arrivals"][p]
    tex.append(f"{LABELS[p]} & {f(s['mean_ratio_agent'])} & {f(s['max_cell_ratio_agent'])} & {f(s['max_prefix_ratio'])} & {s['saving_cells']}/12 \\\\")
tex += ["\\bottomrule", "\\end{tabular}", "\\caption{", "Cost on four existing arrival logs with three model cost profiles and ten paired repetitions per condition.", "The mean gives equal weight to all twelve conditions.", "A dash means the original rule did not record a prefix maximum.", "}", "\\label{tab:safe-real-draft}", "\\end{table}", "", "\\begin{table}[t]", "\\centering", "\\small", "\\begin{tabular}{llrrr}", "\\toprule", "Panel & Policy & Mean / agent & Max cell / agent & Max prefix \\\\", "\\midrule"]
for group, label in (("original_grid", "Original grid"), ("fresh_grid", "Changed grid")):
    for p in SELECTED:
        s = summaries[group][p]
        tex.append(f"{label} & {LABELS[p]} & {f(s['mean_ratio_agent'])} & {f(s['max_cell_ratio_agent'])} & {f(s['max_prefix_ratio'])} \\\\")
tex += ["\\bottomrule", "\\end{tabular}", "\\caption{", "Cost across all 300 conditions in each synthetic panel, with eight paired repetitions per condition.", "The two panels remain separate because their horizons, prices, probabilities, and routing costs differ.", "Safe rules were within their stated prefix budget up to the implementation's numerical tolerance in every saved run.", "}", "\\label{tab:safe-grid-draft}", "\\end{table}", "", "\\begin{table}[t]", "\\centering", "\\small", "\\begin{tabular}{llrr}", "\\toprule", "Model & Log & SafeProjected / agent [95\\% CI] & Cost per success / agent \\\\", "\\midrule"]
models = {"deepseek/deepseek-v4-flash-vision-exp": "DeepSeek", "qwen/qwen3.8-flash": "Qwen", "z-ai/glm-5.3-flash": "GLM"}
logs = {"bpi2019": "BPI 2019", "sepsis": "Sepsis", "wiki_A": "Wikipedia A", "wiki_B": "Wikipedia B"}
for c in cells["real_arrivals"]:
    s = c["policies"][MAIN]
    tex.append(f"{models[c['model']]} & {logs[c['pattern']]} & {ci(s['ratio_agent'], s['ratio_agent_ci95'])} & {f(s['cost_per_success_ratio_agent'])} \\\\")
tex += ["\\bottomrule", "\\end{tabular}", "\\caption{", "SafeProjected with $\\epsilon=0.25$ in each existing-log condition.", "Intervals are 95\\% paired percentile bootstrap intervals from 2000 resamples of the stored repetition indices.", "Each resample shares indices across policies and conditions.", "Cost per success divides total cost by total successful tasks before normalizing to the agent-only result.", "}", "\\label{tab:safe-real-detail-draft}", "\\end{table}"]
(OUT / "tables-draft.tex").write_text("\n".join(tex) + "\n")
print(json.dumps({"cells": len(paths), "run_records_checked": validated_rows, "real_safe_projected": summaries["real_arrivals"][MAIN], "boundary": boundary_description, "outputs": ["evaluation-evidence.json", "evaluation-evidence.md", "tables-draft.tex"]}, indent=2))
