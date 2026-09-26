"""Validate revision coverage, provenance, accounting, and admission semantics."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from normalize_results import normalize, usage_pw

ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = ROOT / "experimental-results/trace_verifier_20260925"
OUT = Path(__file__).resolve().parent / "coverage-report.json"
SELECTIONS = Path(__file__).resolve().parent / "evidence-selection.json"
DESKTOP_BUNDLES = ROOT / "analysis/trace_verifier_20260925/orchestration/desktop_bundle_provenance.json"
PROTOCOL = "three-building-model-extracted-v1"
MODELS = {"deepseek/deepseek-v4-flash-vision-exp", "z-ai/glm-5.3-flash", "qwen/qwen3.8-flash"}
FAMILIES = {
    "android": ["ContactsAddContact", "MarkorDeleteNote", "OsmAndMarker", "SimpleCalendarAddOneEvent"],
    "desktop": ["CalcTableSave", "WriterMemoSave"],
    "web": ["CommentPost"],
}
TERMINAL = {"complete", "extraction_failure", "generation_output_budget_failure", "incomplete", "refused", "provider_error", "insufficient_credit", "timeout", "operator_aborted"}
MEASUREMENT_TERMINAL = {"complete", "extraction_failure", "generation_output_budget_failure"}
NONMEASUREMENT = TERMINAL - MEASUREMENT_TERMINAL
CHARGES = ("reused_initial_translation", "reused_initial_builder", "new_extraction", "new_repair")
REPEAT_FAMILIES = {
    "deepseek/deepseek-v4-flash-vision-exp": FAMILIES["android"],
    "z-ai/glm-5.3-flash": FAMILIES["android"],
    "qwen/qwen3.8-flash": ["ContactsAddContact", "MarkorDeleteNote", "SimpleCalendarAddOneEvent"],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_source_path(value: str) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    parts = path.parts
    for marker in ("experimental-results", "analysis", "paper", "code"):
        if marker in parts:
            candidate = ROOT.joinpath(*parts[parts.index(marker):])
            if candidate.is_file():
                return candidate
    return ROOT / value


def desktop_bundle_errors(path: Path, record: dict) -> list[str]:
    if record.get("platform") != "desktop":
        return []
    if not DESKTOP_BUNDLES.is_file():
        return ["missing_desktop_bundle_provenance"]
    sidecar = json.loads(DESKTOP_BUNDLES.read_text())
    relative = str(path.relative_to(ROOT))
    matches = [row for row in sidecar.get("attempts", [])
               if row.get("result_path") == relative and row.get("model") == record.get("model")
               and row.get("family") == record.get("family")]
    if len(matches) != 1:
        return ["desktop_attempt_bundle_mapping_missing_or_duplicate"]
    errors = []
    known = []
    for segment in matches[0].get("segments", []):
        bundle_id = segment.get("bundle_id")
        if bundle_id == "legacy_mutable_unknown":
            continue
        bundle = sidecar.get("bundles", {}).get(bundle_id)
        if not bundle:
            errors.append("desktop_unknown_bundle:" + str(bundle_id)); continue
        manifest = ROOT / bundle["manifest_path"]
        if not manifest.is_file() or sha256(manifest) != bundle["manifest_sha256"]:
            errors.append("desktop_bundle_manifest_hash_mismatch:" + str(bundle_id))
        known.append(bundle_id)
    if not known:
        errors.append("desktop_attempt_has_no_immutable_bundle_segment")
    return errors


def expected_main() -> set[tuple[str, str, str]]:
    expected = {(platform, model, family) for platform, families in FAMILIES.items()
                for model in MODELS for family in families}
    expected.remove(("web", "qwen/qwen3.8-flash", "CommentPost"))
    return expected


def expected_repeats() -> set[tuple[str, str, str, str]]:
    return {("android", model, family, f"repeat-{number}")
            for model, families in REPEAT_FAMILIES.items()
            for family in families for number in (1, 2)}


def check_record(path: Path, record: dict) -> tuple[list[str], bool]:
    errors: list[str] = []
    required = ("protocol_id", "platform", "model", "family", "attempt_id", "attempt_role",
                "terminal_status", "source_artifacts", "charges", "timings")
    for key in required:
        if key not in record:
            errors.append(f"missing:{key}")
    if errors:
        return errors, False
    if record["protocol_id"] != PROTOCOL:
        errors.append("wrong_protocol")
    status = record["terminal_status"]
    if status not in TERMINAL:
        errors.append("invalid_terminal_status")
    complete_required = ("original_tasks", "extractions", "initial_program_sha256",
                         "candidate_history", "task_results", "admission", "deployment")
    if status == "complete":
        for key in complete_required:
            if key not in record:
                errors.append(f"complete_missing:{key}")
    if errors:
        return errors, False
    tasks = record.get("original_tasks", [])
    extractions = record.get("extractions", [])
    outcomes = record.get("task_results", [])
    if status == "complete" and (len(tasks) != 3 or sorted(t.get("seed") for t in tasks) != [1, 2, 3]):
        errors.append("original_tasks_must_be_seeds_1_2_3")
    if status == "complete" and (len(extractions) != 3 or sorted(e.get("seed") for e in extractions) != [1, 2, 3]):
        errors.append("extractions_must_cover_seeds_1_2_3")
    if status == "complete" and (len(outcomes) != 3 or sorted(t.get("seed") for t in outcomes) != [1, 2, 3]):
        errors.append("task_results_must_cover_seeds_1_2_3")
    outcome_statuses = [t.get("status") for t in sorted(outcomes, key=lambda x: x.get("seed", 0))]
    if any(s not in {"pass", "fail", "untested"} for s in outcome_statuses):
        errors.append("invalid_task_status")
    if "untested" in outcome_statuses:
        first_untested = outcome_statuses.index("untested")
        if any(s != "untested" for s in outcome_statuses[first_untested:]):
            errors.append("tested_task_after_untested")
    if "fail" in outcome_statuses:
        first_fail = outcome_statuses.index("fail")
        if any(s != "untested" for s in outcome_statuses[first_fail + 1:]):
            errors.append("tested_task_after_first_failure")
    admission = record.get("admission")
    if status == "extraction_failure" and admission is not False:
        errors.append("extraction_failure_requires_rejection")
    if status == "generation_output_budget_failure" and admission is not False:
        errors.append("generation_failure_requires_rejection")
    if admission is True and outcome_statuses != ["pass", "pass", "pass"]:
        errors.append("admission_requires_three_passes")
    if status == "complete" and admission is None:
        errors.append("complete_requires_admission_decision")
    if False in [e.get("type_check") for e in extractions] and any(s == "fail" for s in outcome_statuses):
        errors.append("extraction_failure_must_not_be_program_failure")
    charges = record["charges"]
    for key in CHARGES:
        value = charges.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            errors.append(f"invalid_charge:{key}")
    total = charges.get("C")
    computed = sum(charges.get(k, 0) for k in CHARGES)
    if not isinstance(total, (int, float)) or isinstance(total, bool) or not math.isfinite(total) or abs(total - computed) > 1e-8:
        errors.append("C_must_equal_four_charge_components")
    if charges.get("unit") != "pw" or not charges.get("price_sheet"):
        errors.append("charges_require_pw_unit_and_price_sheet")
    charge_records = charges.get("charge_records")
    if not isinstance(charge_records, list):
        errors.append("missing_charge_records")
    else:
        by_category = {key: 0.0 for key in CHARGES}
        for item in charge_records:
            category, amount = item.get("category"), item.get("amount_pw")
            valid_amount = (isinstance(amount, (int, float)) and not isinstance(amount, bool)
                            and math.isfinite(amount) and amount >= 0)
            if category not in by_category or not valid_amount:
                errors.append("invalid_charge_record")
                continue
            if amount > 0 and category.startswith("new_") and not item.get("call_record"):
                errors.append("new_charge_missing_call_record")
            if amount > 0 and category.startswith("reused_") and not item.get("source_artifact"):
                errors.append("reused_charge_missing_source_artifact")
            by_category[category] += amount
        for key in CHARGES:
            if isinstance(charges.get(key), (int, float)) and abs(by_category[key] - charges[key]) > 1e-8:
                errors.append(f"charge_record_mismatch:{key}")
    for artifact in record["source_artifacts"]:
        source = local_source_path(artifact.get("path", ""))
        if not source.is_file():
            errors.append(f"missing_source:{artifact.get('path')}")
        elif artifact.get("sha256") != sha256(source):
            errors.append(f"source_hash_mismatch:{artifact.get('path')}")
    deployment = record.get("deployment", {})
    if admission is True:
        if record.get("attempt_role") == "repeat":
            if deployment.get("status") not in {"not_required_repeat", "reused", "new_30_use"}:
                errors.append("admitted_repeat_requires_not_required_status")
        elif deployment.get("status") == "reused":
            if not all(deployment.get(k) is True for k in ("byte_identical_program", "unchanged_execution_protocol", "unchanged_input_protocol")):
                errors.append("invalid_deployment_reuse")
            if deployment.get("deployed_program_sha256") != record.get("final_program_sha256"):
                errors.append("deployment_program_hash_mismatch")
        elif deployment.get("status") != "new_30_use":
            errors.append("admitted_program_requires_deployment_evidence")
        elif record.get("attempt_role") != "repeat" and len(deployment.get("records", [])) != 30:
            errors.append("new_deployment_requires_30_records")
    official = record.get("official_route_accounting")
    if official:
        receipts = official.get("receipts") or []
        if official.get("separate_from_frozen_pw") is not True:
            errors.append("official_cny_must_be_separate_from_frozen_pw")
        aggregate_ledger = official.get("receipt_scope", "").startswith("aggregate recovered")
        if not aggregate_ledger and official.get("calls") != len(receipts):
            errors.append("official_receipt_count_mismatch")
        for receipt in receipts:
            temperature = receipt.get("requested_temperature",
                                      receipt.get("requested_temp", receipt.get("temperature")))
            if temperature not in (0, 0.0):
                errors.append("official_route_temperature_not_zero")
            route = receipt.get("route") or receipt.get("provider") or receipt.get("base_url")
            if not receipt.get("response_id") or not route:
                errors.append("official_receipt_missing_route_or_response_id")
        expected_pw = usage_pw({key: official.get(key) or 0 for key in
                                ("prompt_tokens", "cached_tokens", "completion_tokens")}, record["model"])
        if not math.isclose(official.get("frozen_pw_from_actual_tokens", -1), expected_pw,
                            rel_tol=1e-12, abs_tol=1e-8):
            errors.append("official_frozen_pw_token_reconciliation_failed")
    eligible = status in MEASUREMENT_TERMINAL and charges.get("exact", True) is True and not errors
    if status in NONMEASUREMENT and eligible:
        errors.append("nonmeasurement_status_became_eligible")
        eligible = False
    return errors, eligible


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    records = []
    seen_main = set()
    seen_repeats = set()
    manifest = json.loads(SELECTIONS.read_text())
    selections = manifest.get("selections", [])
    identities = [tuple(item["canonical_identity"][key] for key in
                        ("platform", "model", "family", "attempt_role", "attempt_id"))
                  for item in selections]
    duplicate_selections = sorted({identity for identity in identities if identities.count(identity) > 1})
    all_candidates = set(RESULT_ROOT.glob("**/result.json")) | set(RESULT_ROOT.glob("**/attempt.json"))
    selected_paths = {ROOT / item["raw_path"] for item in selections}
    unselected_artifacts = sorted(str(path.relative_to(ROOT)) for path in all_candidates - selected_paths)
    for selection in selections:
        path = ROOT / selection["raw_path"]
        try:
            if sha256(path) != selection["raw_sha256"]:
                raise ValueError("selection_hash_mismatch")
            native_record = json.loads(path.read_text())
            record = normalize(native_record)
            identity = selection["canonical_identity"]
            for field in ("platform", "model", "family", "attempt_id", "attempt_role"):
                record[field] = identity[field]
            errors, eligible = check_record(path, record)
            aggregation_state = selection.get("aggregation_state", "selected")
            if aggregation_state != "selected":
                eligible = False
            bundle_errors = desktop_bundle_errors(path, record)
            errors.extend(bundle_errors)
            if bundle_errors:
                eligible = False
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            record, errors, eligible = {}, [f"unreadable:{exc}"], False
        key = (record.get("platform"), record.get("model"), record.get("family"))
        if (not errors and selection.get("aggregation_state", "selected") == "selected"
                and record.get("attempt_role") == "initial"
                and record.get("attempt_id") == "initial" and key in expected_main()):
            seen_main.add(key)
        repeat_key = key + (record.get("attempt_id"),)
        if (not errors and selection.get("aggregation_state", "selected") == "selected"
                and record.get("attempt_role") == "repeat" and repeat_key in expected_repeats()):
            seen_repeats.add(repeat_key)
        records.append({"path": str(path.relative_to(ROOT)), "key": key,
                        "attempt_id": record.get("attempt_id"),
                        "attempt_role": record.get("attempt_role"),
                        "aggregation_state": selection.get("aggregation_state", "selected"),
                        "aggregation_exclusion_reason": selection.get("aggregation_exclusion_reason"),
                        "terminal_status": record.get("terminal_status"),
                        "eligible": eligible, "errors": errors})
    expected = expected_main()
    missing = sorted(expected - seen_main)
    expected_repeat = expected_repeats()
    missing_repeats = sorted(expected_repeat - seen_repeats)
    report = {
        "schema": "three-building-coverage-report/1",
        "protocol_id": PROTOCOL,
        "result_root": str(RESULT_ROOT.relative_to(ROOT)),
        "expected_main_count": len(expected),
        "seen_main_count": len(seen_main),
        "missing_main": missing,
        "expected_repeat_count": len(expected_repeat),
        "seen_repeat_count": len(seen_repeats),
        "missing_repeats": missing_repeats,
        "record_count": len(records),
        "eligible_record_count": sum(r["eligible"] for r in records),
        "terminal_status_counts": {s: sum(r["terminal_status"] == s for r in records) for s in sorted(TERMINAL)},
        "invalid_records": [r for r in records if r["errors"]],
        "selection_manifest": str(SELECTIONS.relative_to(ROOT)),
        "duplicate_selections": duplicate_selections,
        "unselected_artifacts": unselected_artifacts,
        "records": records,
        "coverage_ready": not missing and not missing_repeats and not duplicate_selections,
        "profile_ready": not missing and all(
            r["eligible"] for r in records
            if tuple(r["key"]) in expected and r.get("attempt_role") == "initial"
            and r.get("attempt_id") == "initial"),
    }
    OUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: report[k] for k in ("expected_main_count", "seen_main_count", "expected_repeat_count", "seen_repeat_count", "record_count", "eligible_record_count", "coverage_ready", "profile_ready")}, sort_keys=True))
    return 1 if args.strict and not report["coverage_ready"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
