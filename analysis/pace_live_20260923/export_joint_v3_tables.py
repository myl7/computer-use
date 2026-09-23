"""Export compact, traceable tables from the read-only v3 analysis snapshot."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUMMARY = HERE / "joint_v3_summary.json"
RAW = ROOT / "experimental-results/pace_live_20260923/v3"


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    source_bytes = SUMMARY.read_bytes()
    data = json.loads(source_bytes)
    if not data["snapshot_complete"] or data["issues"]:
        raise RuntimeError("Final export requires a complete, issue-free analysis")
    if data["manifest_sha256"] != hashlib.sha256((RAW / "manifest.json").read_bytes()).hexdigest():
        raise RuntimeError("Analysis manifest differs from the raw manifest")
    reconciliation_path = HERE / "generation-reconciliation-final.json"
    reconciliation = json.loads(reconciliation_path.read_text())
    v2_lookups = [entry for entry in reconciliation["queries"] if entry["version"] == "v2"]
    pairs = []
    for pair in data["pairs"]:
        agent, pace = pair["agent"], pair["program_fallback"]
        pairs.append({
            "pair": pair["pair"], "seed": pair["seed"], "arm": pair["arm"],
            "initial_states_match": pair["initial_states_match"],
            "agent_success": agent["success"], "agent_termination": agent["termination"],
            "agent_bill_lower_nusd": agent["billing"]["bill_lower_nusd"],
            "agent_bill_upper_nusd": agent["billing"]["bill_upper_nusd"],
            "pace_success": pace["success"], "pace_termination": pace["termination"],
            "pace_bill_lower_nusd": pace["billing"]["bill_lower_nusd"],
            "pace_bill_upper_nusd": pace["billing"]["bill_upper_nusd"],
            "binding_fields_correct": sum(pace["extracted_binding_matches"].values())
            if pace["extracted_binding_matches"] is not None else None,
            "program_attempted": pace["program_attempted"], "fallback": pace["fallback"],
            "fallback_reason": pace["fallback_reason"],
            "fields_correct_at_fallback": pace["retained_correct_fields"],
            "agent_perturbation_exposed": agent["perturbation_exposed"],
            "pace_perturbation_exposed": pace["perturbation_exposed"],
            "pace_reference_prefix_nusd": pace["reference_prefix_nusd"],
            "pace_prefix_ceiling_nusd": pace["prefix_ceiling_nusd"],
            "pace_prefix_occupied_nusd": pace["recorded_prefix_cost_nusd"],
            "pace_prefix_bound_holds": pace["recorded_prefix_bound_holds"],
        })
    tight = []
    for episode in data["tight_diagnostics"]:
        tight.append({
            "case": episode["pair"], "seed": episode["seed"], "arm": episode["arm"],
            "success": episode["success"], "termination": episode["termination"],
            "bill_lower_nusd": episode["billing"]["bill_lower_nusd"],
            "bill_upper_nusd": episode["billing"]["bill_upper_nusd"],
            "fallback": episode["fallback"],
            "fields_correct_at_fallback": episode["retained_correct_fields"],
            "reference_prefix_nusd": episode["reference_prefix_nusd"],
            "prefix_ceiling_nusd": episode["prefix_ceiling_nusd"],
            "prefix_occupied_nusd": episode["recorded_prefix_cost_nusd"],
            "prefix_bound_holds": episode["recorded_prefix_bound_holds"],
        })
    write_csv(HERE / "joint_v3_pairs.csv", pairs)
    write_csv(HERE / "joint_v3_tight.csv", tight)
    all_spend = data["spending"]["all_requests"]
    comparison = data["comparisons"]["all_pairs"]
    provider_stop = [row["pair"] for row in pairs if row["agent_termination"] == "billing_stop"
                     or row["pace_termination"] == "billing_stop"]
    payload = {
        "schema": "pace-live-closeout/1",
        "source_summary": str(SUMMARY),
        "source_summary_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_manifest_sha256": data["manifest_sha256"],
        "raw_run": str(RAW),
        "generated_at": data["generated_at"],
        "pairs_csv": str(HERE / "joint_v3_pairs.csv"),
        "tight_csv": str(HERE / "joint_v3_tight.csv"),
        "main_pairs": len(pairs),
        "initial_states_matched": sum(row["initial_states_match"] is True for row in pairs),
        "agent_successes": comparison["success"]["agent"]["successes"],
        "pace_successes": comparison["success"]["program_fallback"]["successes"],
        "agent_billing_stops": comparison["success"]["agent"]["provider_or_billing_aborts"],
        "pace_billing_stops": comparison["success"]["program_fallback"]["provider_or_billing_aborts"],
        "provider_stop_pairs": provider_stop,
        "perturbed_program_failures_with_fallback": sum(row["fallback"] is True for row in pairs),
        "successful_perturbed_fallbacks": sum(row["fallback"] is True and row["pace_success"] is True for row in pairs),
        "successful_fallbacks_with_six_correct_fields_at_entry": sum(
            row["fallback"] is True and row["pace_success"] is True
            and row["fields_correct_at_fallback"] == 6 for row in pairs),
        "both_exposed_perturbed_pairs": data["comparisons"]["both_exposed_perturbed_cases"]["included_pair_ids"],
        "all_pair_bill_bound_totals_nusd": {
            "agent": [sum(row["agent_bill_lower_nusd"] for row in pairs),
                      sum(row["agent_bill_upper_nusd"] for row in pairs)],
            "program_fallback": [sum(row["pace_bill_lower_nusd"] for row in pairs),
                                 sum(row["pace_bill_upper_nusd"] for row in pairs)],
        },
        "all_pair_mean_bill_bounds_nusd": comparison["full_selected_mean_bill_bounds_nusd"],
        "all_pair_seed_bootstrap_bill_bound_95_envelope_nusd": comparison["cost_bound_bootstrap_95_envelope_nusd"],
        "exact_bill_pairs": comparison["exact_cost_pairs"],
        "tight_successes": data["tight_success"]["successes"],
        "tight_prefix_denials": data["tight_success"]["prefix_denials"],
        "tight_billing_stops": data["tight_success"]["provider_or_billing_aborts"],
        "v3_requests": all_spend["requests"],
        "v3_settled_requests": all_spend["states"].get("settled", 0),
        "v3_unsettled_requests": all_spend["unsettled_requests"],
        "v3_known_bill_nusd": all_spend["known_actual_nusd"],
        "v3_occupied_upper_nusd": all_spend["occupied_nusd"],
        "v2_v3_known_bill_nusd": data["combined_spending"]["known_actual_nusd"],
        "v2_v3_occupied_upper_nusd": data["combined_spending"]["occupied_nusd"],
        "v2_v3_authorized_ceiling_nusd": data["combined_spending"]["aggregate_cap_nusd"],
        "request_price_checks": data["audit"]["price_check_status_counts"],
        "observed_request_guards_hold": data["audit"]["guards"]["all_observed_request_guards_hold"],
        "recorded_admission_mismatches": len(data["audit"]["guards"]["recorded_admission_mismatches"]),
        "recorded_terminal_prefix_violations": len(data["audit"]["guards"]["recorded_terminal_prefix_violations"]),
        "main_reference_increment_nusd": 80_000_000,
        "tight_reference_increment_nusd": 20_000_000,
        "epsilon": data["audit"]["guards"]["epsilon"],
        "reference_is_measured_agent_bill": data["audit"]["guards"]["declared_reference_is_measured_agent_bill"],
        "acquisition_accepted": data["acquisition"]["accepted"],
        "acquisition_validation_successes": data["acquisition"]["validation_successes"],
        "acquisition_validation_records": data["acquisition"]["validation_records"],
        "acquisition_known_bill_nusd": data["acquisition"]["billing"]["known_actual_nusd"],
        "generation_reconciliation_file": str(reconciliation_path),
        "v2_unsettled_generation_lookup_http_status": [entry.get("http_status") for entry in v2_lookups],
        "v3_unsettled_generation_ids_available": sum(bool(entry["generation_id"])
                                                    for entry in data["audit"]["unsettled_records"]),
        "limits": data["inference_limits"],
    }
    (HERE / "joint_v3_closeout.json").write_text(json.dumps(payload, indent=2) + "\n")


if __name__ == "__main__":
    main()
