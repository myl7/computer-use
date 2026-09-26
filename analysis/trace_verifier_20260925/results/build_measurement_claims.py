"""Build prose-facing revised measurement claims from validated selected evidence."""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from diagnostics_validation import validate_diagnostics
from normalize_results import normalize
from validate_results import ROOT, check_record, expected_main

HERE = Path(__file__).resolve().parent
SELECTIONS = HERE / "evidence-selection.json"
PROFILES = HERE / "profiles.json"
OUT = HERE / "measurement-claim-deltas.json"
OLD_CONFIG = ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"


def main() -> None:
    selected = []
    for item in json.loads(SELECTIONS.read_text())["selections"]:
        if item.get("aggregation_state", "selected") != "selected":
            continue
        path = ROOT / item["raw_path"]
        record = normalize(json.loads(path.read_text()))
        record.update(item["canonical_identity"])
        errors, _ = check_record(path, record)
        if errors:
            raise SystemExit(f"Invalid selected record: {path}: {errors}")
        selected.append(record)
    mains = [r for r in selected if r["attempt_role"] == "initial" and r["attempt_id"] == "initial"
             and (r["platform"], r["model"], r["family"]) in expected_main()]
    repeats = [r for r in selected if r["attempt_role"] == "repeat"]
    if len(mains) != 20:
        raise SystemExit(f"Expected 20 mains, found {len(mains)}")
    profiles = json.loads(PROFILES.read_text())
    old_config = json.loads(OLD_CONFIG.read_text())
    old = {}
    for wrapper in old_config["jobs"]:
        spec = wrapper["spec"]
        for name, profile in spec["profiles"].items():
            old.setdefault((spec["model"], name), profile)
    admitted_costs = [r["charges"]["C"] for r in mains if r["admission"]]
    rejected_exact = [r["charges"]["C"] for r in mains if not r["admission"] and r["charges"].get("exact", True)]
    lower = [r for r in mains if not r["charges"].get("exact", True)]
    payback, payback_traces = [], []
    per_cell = []
    for model, rows in profiles["models"].items():
        for name, values in rows.items():
            revised = values["revised"]; base = old[(model, name)]
            saving = base["c"] * (1 - revised["q"]) - revised["d"]
            row = {"model": model, "cell": name, "admitted": revised["observed_admitted"],
                   "C": revised["C"], "C_fail": revised["C_fail"], "d": revised["d"],
                   "q": revised["q"], "c_reused": base["c"], "saving_per_use": saving,
                   "source": revised["source"]}
            if revised["observed_admitted"] and saving > 0:
                row["payback_compilation_only"] = revised["C"] / saving
                row["payback_including_three_traces"] = (revised["C"] + 3 * base["c"]) / saving
                payback.append(row["payback_compilation_only"])
                payback_traces.append(row["payback_including_three_traces"])
            if revised.get("C_fail_imputation"):
                row["C_fail_imputation"] = revised["C_fail_imputation"]
            per_cell.append(row)
    repeat_status = Counter(
        "admitted" if r["admission"] else
        ("rejected_program" if r["terminal_status"] == "complete" else r["terminal_status"])
        for r in repeats)
    diagnostics = validate_diagnostics(True)
    diag_manifest = json.loads((ROOT / "analysis/trace_verifier_20260925/runners/diagnostics/manifest.json").read_text())
    t20_rows, frag_cells = [], []
    appearance = {"font_large", "font_small", "density_small", "locale_fr", "dark_theme"}
    interruptions = {"notification", "low_battery", "permission_dialog", "update_prompt"}
    for cell in diag_manifest["cells"]:
        if cell["t20"]["action"] == "reuse":
            raw = json.loads((ROOT / cell["t20"]["historical_path"]).read_text())
            direct, extracted = raw["injection_passed_now"], raw["extraction_passed"]
        else:
            raw = json.loads((ROOT / cell["new_diagnostic"]["path"]).read_text())
            rows20 = raw["results"]["t20"]
            direct = sum(row["direct"]["passed"] for row in rows20)
            extracted = sum(row["extraction"]["success"] for row in rows20)
        t20_rows.append({"model": cell["model"], "family": cell["family"],
                         "direct": direct, "extracted": extracted, "trials": 5})
        if cell["t18"]["action"] == "reuse":
            frag = json.loads((ROOT / cell["t18"]["historical_path"]).read_text())
            family_record = next(row for row in frag["results"] if row["family"] == cell["family"])
            entries = family_record["arms"]
        else:
            frag = json.loads((ROOT / cell["new_diagnostic"]["path"]).read_text())
            entries = [row["entry"] for row in frag["results"]["t18"]]
        by_arm = defaultdict(lambda: {"pass": 0, "loud": 0, "n": 0, "runs": [], "skipped": False})
        for entry in entries:
            arm = entry.get("arm")
            if isinstance(arm, dict): arm = arm.get("name")
            if not arm: continue
            by_arm[arm]["skipped"] = bool(entry.get("skipped"))
            by_arm[arm]["runs"].extend(entry.get("runs") or [])
            by_arm[arm]["pass"] += entry.get("pass", 0)
            by_arm[arm]["loud"] += entry.get("loud", 0)
            by_arm[arm]["n"] += entry.get("n", 0)
        clean = by_arm["clean"]["pass"]
        clean_ids = {json.dumps(run.get("binding"), sort_keys=True) for run in by_arm["clean"]["runs"] if run.get("passed")}
        def conditional_failure(arms):
            tested = failed = 0
            for arm in arms:
                if by_arm[arm]["skipped"]:
                    continue
                rows = [run for run in by_arm[arm]["runs"]
                        if json.dumps(run.get("binding"), sort_keys=True) in clean_ids]
                tested += len(rows); failed += sum(not run.get("passed") for run in rows)
            return (failed/tested if tested else None, failed, tested)
        appearance_rate, appearance_failed, appearance_tested = conditional_failure(appearance)
        interruption_rate, interruption_failed, interruption_tested = conditional_failure(interruptions)
        failures = sum(v["n"] - v["pass"] for v in by_arm.values())
        frag_cells.append({"model": cell["model"], "family": cell["family"],
                           "clean_passes": clean, "clean_trials": by_arm["clean"]["n"],
                           "appearance_failure_fraction_conditional": appearance_rate,
                           "appearance_failures": appearance_failed, "appearance_trials": appearance_tested,
                           "interruption_failure_fraction_conditional": interruption_rate,
                           "interruption_failures": interruption_failed, "interruption_trials": interruption_tested,
                           "detectable_error_fraction_all_failures": (sum(v["loud"] for v in by_arm.values())/failures if failures else None),
                           "detectable_failures": sum(v["loud"] for v in by_arm.values()),
                           "total_failures": failures,
                           "excluded_from_conditional": clean == 0})
    old_measurement = json.loads((ROOT / "paper/measurement_update_20260918.json").read_text())
    paired_map = {(row["model"], row["family"]): row for row in
                  old_measurement["paired_replays"] + old_measurement["qwen_paired_replays"]}
    admitted_android = [row for row in per_cell if row["admitted"] and row["cell"].startswith("Android/")]
    main_map = {(r["model"], r["family"]): r for r in mains}
    matched = []
    for row in admitted_android:
        family = row["cell"].split("/", 1)[1]; prior = paired_map.get((row["model"], family))
        main_record = main_map[(row["model"], family)]
        metrics = main_record.get("profile_metrics") or {}
        if prior:
            matched.append({"model": row["model"], "family": family, "bindings": 30,
                            "agent_successes": prior["agent_success"],
                            "program_successes": round((1-row["q"])*30),
                            "matched_agent_cost_mean": prior["c"],
                            "program_extraction_cost_mean": row["d"],
                            "saving_share": 1 - row["q"] - row["d"] / prior["c"],
                            "agent_source": "paper/measurement_update_20260918.json paired replay on identical tasks"})
    grouped_repeat = []
    repeat_map = defaultdict(list)
    for r in mains + repeats:
        if r["platform"] == "android": repeat_map[(r["model"],r["family"])].append(r)
    for (model,family), records in sorted(repeat_map.items()):
        if len(records) != 3: continue
        records.sort(key=lambda r: (r["attempt_role"] == "repeat", r["attempt_id"]))
        grouped_repeat.append({"model":model,"family":family,
                               "outcomes":["Pass" if r["admission"] else
                                           ("Generation failure" if r["terminal_status"]=="generation_output_budget_failure" else "Fail")
                                           for r in records],
                               "C_pw":[r["charges"]["C"] for r in records],
                               "C_exact":[r["charges"].get("exact",True) for r in records]})
    result = {
        "schema": "three-building-measurement-claims/1",
        "protocol_id": "three-building-model-extracted-v1", "status": "complete",
        "main": {"attempts": 20, "admitted": sum(r["admission"] for r in mains),
                 "rejected": sum(not r["admission"] for r in mains),
                 "repair_rounds": sum(len(r.get("repairs", [])) for r in mains),
                 "candidate_evaluations": sum(len(r.get("candidate_history", [])) for r in mains),
                 "admitted_C_pw": {"median": statistics.median(admitted_costs),
                                    "range": [min(admitted_costs), max(admitted_costs)]},
                 "rejected_exact_C_pw": {"n": len(rejected_exact),
                                          "median": statistics.median(rejected_exact),
                                          "range": [min(rejected_exact), max(rejected_exact)]},
                 "lower_bound_costs": [{"model": r["model"], "family": r["family"],
                                         "C_pw": r["charges"]["C"], "cost_labels": r.get("cost_labels")}
                                        for r in lower]},
        "payback_admitted": {"n": len(payback),
                             "compilation_only_range": [min(payback), max(payback)],
                             "including_three_traces_range": [min(payback_traces), max(payback_traces)]},
        "repeats": {"attempts": len(repeats), "outcomes": dict(repeat_status),
                    "repair_rounds": sum(len(r.get("repairs", [])) for r in repeats),
                    "lower_bound_costs": [{"model": r["model"], "family": r["family"],
                                            "attempt_id": r["attempt_id"], "C_pw": r["charges"]["C"],
                                            "cost_labels": r.get("cost_labels")}
                                           for r in repeats if not r["charges"].get("exact", True)]},
        "diagnostics": diagnostics,
        "admitted_serving": {"d_range": [min(row["d"] for row in per_cell if row["admitted"]),
                                            max(row["d"] for row in per_cell if row["admitted"])],
                               "saving_share_range": [min(row["saving_per_use"]/row["c_reused"] for row in per_cell if row["admitted"]),
                                                      max(row["saving_per_use"]/row["c_reused"] for row in per_cell if row["admitted"])]},
        "matched_android": {"cells": matched, "rows": 30*len(matched),
                            "agent_successes": sum(row["agent_successes"] for row in matched),
                            "program_successes": sum(row["program_successes"] for row in matched),
                            "saving_share_range": [min(row["saving_share"] for row in matched), max(row["saving_share"] for row in matched)]},
        "repeat_families": grouped_repeat,
        "t20": t20_rows,
        "fragility": {"cells": frag_cells,
                       "by_model": {model: {
                           "appearance_failure_fraction_conditional_mean": statistics.mean(
                               row["appearance_failure_fraction_conditional"] for row in frag_cells
                               if row["model"] == model and not row["excluded_from_conditional"]),
                           "interruption_failure_fraction_conditional_mean": statistics.mean(
                               row["interruption_failure_fraction_conditional"] for row in frag_cells
                               if row["model"] == model and not row["excluded_from_conditional"]),
                           "detectable_error_fraction_all_failures": (sum(row["detectable_failures"] for row in frag_cells if row["model"] == model) /
                               sum(row["total_failures"] for row in frag_cells if row["model"] == model)),
                           "included_cells": sum(row["model"] == model and not row["excluded_from_conditional"] for row in frag_cells),
                           "excluded_zero_clean_pass_cells": sum(row["model"] == model and row["excluded_from_conditional"] for row in frag_cells)
                       } for model in sorted({row["model"] for row in frag_cells})},
                       "conditional_cells": sum(not row["excluded_from_conditional"] for row in frag_cells),
                       "excluded_zero_clean_pass_cells": sum(row["excluded_from_conditional"] for row in frag_cells)},
        "per_cell": per_cell,
        "limitations": [
            "c and agent pi are retained from identical historical exploration tasks and conditions.",
            "Web DeepSeek C is a measured lower bound; its modeled C_fail donor and clamp are explicit per cell.",
            "Qwen Calc model-call charges are exact but wall time excludes interrupted segments.",
            "Repeat attempts do not require deployment and do not define serving d or q."
        ]}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"admitted": result["main"]["admitted"],
                      "repair_rounds": result["main"]["repair_rounds"],
                      "candidate_evaluations": result["main"]["candidate_evaluations"],
                      "repeat_outcomes": result["repeats"]["outcomes"]}))


if __name__ == "__main__":
    main()
