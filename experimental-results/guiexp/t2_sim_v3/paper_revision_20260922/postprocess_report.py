"""Validate frozen outputs and append descriptive cost decompositions.

This script reads completed simulations. It does not run or alter a scenario.
"""
import hashlib
import json
import math
from pathlib import Path
import statistics

OUT = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n")


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-11, abs_tol=1e-7), (a, b)


def main():
    config = json.loads((OUT / "config.json").read_text())
    summary = json.loads((OUT / "summary.json").read_text())
    cells = {r["key"]: json.loads((OUT / r["file"]).read_text()) for r in summary["cells"]}
    assert len(cells) == 109
    hashes = {}
    row_count = 0
    for spec in config["jobs"]:
        c = cells[spec["key"]]
        assert c["spec"] == spec
        assert list(sorted(c["rows"])) == list(sorted(spec["policies"]))
        assert len(c["stream_sha256"]) == len(c["mapping_sha256"]) == 20
        for policy, rows in c["rows"].items():
            assert len(rows) == 20
            for i, row in enumerate(rows):
                raw = row["engine"]
                token_cost = sum(raw[k] for k in ("reactive_tokens", "extraction_tokens", "compile_tokens", "router_tokens"))
                close(token_cost, row["token_cost"])
                close(raw["tokens"], token_cost + row["auxiliary_penalty"])
                close(raw["harm_tokens"], row["auxiliary_penalty"])
                close(raw["tokens"], row["penalized_objective"])
                assert raw["arrivals"] == c["stream_summary"]["n_arrivals"]
                close(raw["success_rate"], raw["successes"] / raw["arrivals"])
                if spec["silent"] == 0:
                    assert raw["harm_tokens"] == raw["silent_failures"] == 0
                    assert raw["successes"] >= c["rows"]["reactive"][i]["engine"]["successes"]
                row_count += 1
            close(c["summary"][policy]["mean_token_cost"], statistics.mean(r["token_cost"] for r in rows))
            close(c["summary"][policy]["token_ratio_to_reactive"],
                  statistics.mean(r["token_cost"] for r in rows) /
                  statistics.mean(r["token_cost"] for r in c["rows"]["reactive"]))
        for name, comparison in c["comparisons"].items():
            candidates = config["comparator_definitions"][name]
            best = min(candidates, key=lambda p: c["summary"][p]["mean_token_cost"])
            assert comparison["selected"] == best
            close(comparison["ratio"], c["summary"]["projected_cap"]["mean_token_cost"] /
                  c["summary"][best]["mean_token_cost"])
    for row in summary["cells"]:
        hashes[row["file"]] = sha(OUT / row["file"])
    for relative, expected in config["source_sha256"].items():
        assert sha(ROOT / relative) == expected, relative
    assert row_count == 17440
    for key, aggregate in summary["aggregates"].items():
        selected = [c for c in cells.values() if c["spec"]["tag"] == "base"
                    and f'{c["spec"]["model"]}/{c["spec"]["admission_mode"]}' == key]
        assert len(selected) == 7
        for policy, values in aggregate["policies"].items():
            close(values["mean_cell_token_ratio"], statistics.mean(
                c["summary"][policy]["token_ratio_to_reactive"] for c in selected))

    key = "deepseek/deepseek-v4-flash-vision-exp/binary_replay/poisson/base"
    c = cells[key]
    record = next(r for r in summary["cells"] if r["key"] == key)
    threshold = {}
    for family, profile in c["spec"]["profiles"].items():
        if not profile["observed_admitted"]:
            continue
        h = c["spec"]["h"]
        saving = profile["c"] - profile["d"] - (h + (1-h)*profile["q"]) * profile["c"]
        buy = profile["C"] + profile["C_fail"]
        threshold[family] = dict(C=profile["C"], C_fail=profile["C_fail"],
                                 C_fail_over_C=profile["C_fail"]/profile["C"],
                                 initial_p_est=0.5, initial_buy=buy,
                                 projected_use_ceiling=1/h,
                                 maximum_projected_saving=saving/h,
                                 ceiling_below_initial_buy=saving/h <= buy)
    base = [v for v in cells.values() if v["spec"]["tag"] == "base"]
    worst = {}
    for comparator in ("reactive", "best_fixed", "best_other_rule"):
        def ratio(v):
            return (v["summary"]["projected_cap"]["token_ratio_to_reactive"] if comparator == "reactive"
                    else v["comparisons"][comparator]["ratio"])
        chosen = max(base, key=ratio)
        interval = (chosen["summary"]["projected_cap"]["token_ratio_ci95"] if comparator == "reactive"
                    else chosen["comparisons"][comparator]["ratio_ci95"])
        worst[comparator] = dict(key=chosen["spec"]["key"], ratio=ratio(chosen), ratio_ci95=interval)
    decomposition = dict(
        source_cell=record["file"], source_cell_sha256=sha(OUT / record["file"]),
        cell_key=key, policy_summaries=c["summary"], threshold_dependencies=threshold,
        worst_base_cells=worst,
        interpretation="Descriptive accounting and an algebraic implication of the frozen engine and inputs. No added simulation or causal ablation was performed. Failed-build cost for admitted DeepSeek families was not measured and was filled with 2,502,646.7636363637 PW, the median complete failure cost across other DeepSeek families.")
    dump(OUT / "component_breakdown.json", decomposition)
    validation = dict(cells=109, repetitions_per_cell=20, policies_per_cell=8,
                      run_records=row_count, base_cells=63, source_hashes_verified=True,
                      accounting_verified=True, ratios_of_means_verified=True,
                      aggregate_equal_cell_weighting_verified=True,
                      no_silent_quality_invariant_verified=True,
                      config_sha256=sha(OUT / "config.json"),
                      summary_sha256=sha(OUT / "summary.json"),
                      postprocess_source_sha256=sha(__file__), cell_sha256=hashes)
    dump(OUT / "validation.json", validation)

    marker = "\n## Post-run descriptive decomposition\n"
    report = (OUT / "REPORT.md").read_text().split(marker)[0]
    lines = [marker, "This section uses the frozen outputs. It introduces no additional scenario or causal ablation.", "",
             "### Main comparison across seven streams", "",
             "Each entry gives the equal-weight mean of seven per-stream cost ratios to reactive. Reactive is 1 in every row. The interval for projected_cap uses paired bootstrap resampling shared across the seven streams. Intervals for every other rule are retained in summary.json.", "",
             "| Model / admission | earliest | earliest_cap | fixed10_cap | success10_cap | breakeven_cap | projected | projected_cap [95% CI] |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, aggregate in summary["aggregates"].items():
        values = aggregate["policies"]
        middle = " | ".join(f"{values[p]['mean_cell_token_ratio']:.4f}" for p in
                            ("earliest", "earliest_cap", "fixed10_cap", "success10_cap", "breakeven_cap", "projected"))
        point = values["projected_cap"]["mean_cell_token_ratio"]
        lo, hi = values["projected_cap"]["mean_cell_token_ratio_ci95"]
        lines.append(f"| {name} | {middle} | {point:.4f} [{lo:.4f}, {hi:.4f}] |")
    lines += ["", "With silent=0, a detected program failure uses the same reactive outcome coin as the reactive baseline. A successful program use counts as success. Thus non-decreasing quality in the base study follows from the simulator's construction. It is not independent empirical evidence that the deployed protocol preserves quality.",
              "", "### DeepSeek binary-replay Poisson cell", "",
             f"Source: `{record['file']}`. All entries are means over the same 20 repetitions. Costs are in millions of price-weighted input-token units.", "",
             "| Policy | Total tokens | Agent | Extraction | Compile | Router | Failed compile | Token ratio to reactive |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for policy in c["spec"]["policies"]:
        s = c["summary"][policy]
        parts = s["mean_components"]
        lines.append(f"| {policy} | {s['mean_token_cost']/1e6:.6f} | "
                     f"{parts['reactive_tokens']/1e6:.6f} | {parts['extraction_tokens']/1e6:.6f} | "
                     f"{parts['compile_tokens']/1e6:.6f} | {parts['router_tokens']/1e6:.6f} | "
                     f"{s['mean_failed_compile_tokens']/1e6:.6f} | {s['token_ratio_to_reactive']:.6f} |")
    lines += ["", "The failure cost of an already admitted DeepSeek family was not measured. Its scenario value is 2,502,646.76 PW, the median complete failed-build cost from other DeepSeek families. This fill can be much larger than the family's measured successful build cost.", "",
              "At the initial admission estimate 0.5, estimated admission cost equals C + C_fail. The default hazard caps projected uses at 50. The next table computes the upper bound 50*s from each admitted profile, before the nonnegative router allowance. If this bound is below the initial admission cost, projected eligibility cannot produce a first attempt for that family. The estimate therefore remains 0.5. This is a threshold implication of the fixed inputs and code, not an estimate of a causal effect of imputation.", "",
              "| Admitted DeepSeek family | C | Imputed C_fail | Initial C+C_fail | Maximum 50*s | Blocks first projected attempt |",
              "|---|---:|---:|---:|---:|---|"]
    for family, values in threshold.items():
        lines.append(f"| {family} | {values['C']:.2f} | {values['C_fail']:.2f} | "
                     f"{values['initial_buy']:.2f} | {values['maximum_projected_saving']:.2f} | "
                     f"{values['ceiling_below_initial_buy']} |")
    lines += ["", "### Largest projected-cap cost ratio among all 63 base cells", "",
              "This reporting rule inspects the complete base set. It is a descriptive maximum, with each selected cell's paired bootstrap interval.", "",
              "| Comparator | Selected cell | Ratio [95% CI] |", "|---|---|---:|"]
    for name, value in worst.items():
        lo, hi = value["ratio_ci95"]
        lines.append(f"| {name} | {value['key']} | {value['ratio']:.6f} [{lo:.6f}, {hi:.6f}] |")
    lines += ["", "`component_breakdown.json` preserves unrounded values and the source cell hash. "
              "`validation.json` records reconciliation of all 17,440 run records, all source hashes, ratio conventions, and aggregate weights. "
              "Recreate this postprocessing with `python3 experimental-results/guiexp/t2_sim_v3/paper_revision_20260922/postprocess_report.py`.", ""]
    (OUT / "REPORT.md").write_text(report + "\n".join(lines))
    print(f"Validated {row_count} run records across {len(cells)} cells.")


if __name__ == "__main__":
    main()
