"""Aggregate selected evidence without converting missing cells to measurements."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from normalize_results import normalize
from validate_results import ROOT, check_record, expected_main

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "evidence-selection.json"
OUT = HERE / "aggregation.json"


def main() -> None:
    rows = []
    for selection in json.loads(MANIFEST.read_text())["selections"]:
        path = ROOT / selection["raw_path"]
        record = normalize(json.loads(path.read_text()))
        record.update(selection["canonical_identity"])
        errors, eligible = check_record(path, record)
        outcome_eligible = (record["terminal_status"] in
                            {"complete", "extraction_failure", "generation_output_budget_failure"}
                            and not errors)
        key = (record["platform"], record["model"], record["family"])
        outcome_counts = Counter(row.get("status") for row in record.get("task_results", []))
        rows.append({
            "identity": selection["canonical_identity"], "raw_path": selection["raw_path"],
            "raw_sha256": selection["raw_sha256"], "terminal_status": record["terminal_status"],
            "aggregation_state": selection.get("aggregation_state", "selected"),
            "aggregation_exclusion_reason": selection.get("aggregation_exclusion_reason"),
            "in_main_20": (selection.get("aggregation_state", "selected") == "selected"
                           and key in expected_main() and record.get("attempt_role") == "initial"
                           and record.get("attempt_id") == "initial"), "valid": not errors,
            "outcome_eligible": outcome_eligible, "exact_cost_eligible": eligible,
            "profile_eligible": eligible,
            "admission": record.get("admission"),
            "task_counts": {name: outcome_counts[name] for name in ("pass", "fail", "untested")},
            "C_pw": record["charges"]["C"] if outcome_eligible else None,
            "C_is_lower_bound": outcome_eligible and not record["charges"].get("exact", True),
            "cost_labels": record.get("cost_labels"),
            "charge_components_pw": ({key: record["charges"][key] for key in
                                      ("reused_initial_translation", "reused_initial_builder",
                                       "new_extraction", "new_repair")} if eligible else None),
            "deployment_status": record.get("deployment", {}).get("status"),
            "errors": errors,
        })
    main_rows = [row for row in rows if row["in_main_20"]]
    complete = len(main_rows) == len(expected_main()) and all(row["valid"] for row in main_rows)
    result = {
        "schema": "three-building-selected-aggregation/1",
        "protocol_id": "three-building-model-extracted-v1",
        "status": "complete" if complete else "partial",
        "main_expected": len(expected_main()), "main_selected": len(main_rows),
        "main_valid": sum(row["valid"] for row in main_rows),
        "profile_eligible": sum(row["profile_eligible"] for row in main_rows),
        "outcome_eligible": sum(row["outcome_eligible"] for row in main_rows),
        "exact_cost_eligible": sum(row["exact_cost_eligible"] for row in main_rows),
        "admitted": sum(row["admission"] is True for row in main_rows),
        "rejected": sum(row["admission"] is False and row["outcome_eligible"] for row in main_rows),
        "undetermined_or_missing": len(expected_main()) - sum(row["outcome_eligible"] for row in main_rows),
        "reporting": ("Complete selected-outcome counts; uncertainty and modeled profile claims remain governed by their own provenance."
                      if complete else "Partial counts are progress only. No denominator-based paper claim or uncertainty interval is emitted until complete."),
        "rows": rows,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in
                      ("status", "main_selected", "main_valid", "profile_eligible", "admitted",
                       "rejected", "undetermined_or_missing")}, sort_keys=True))


if __name__ == "__main__":
    main()
