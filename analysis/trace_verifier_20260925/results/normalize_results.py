"""Normalize Android, Desktop, and Web revision records for aggregation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRICE_SHEET = ROOT / "paper/measurement_update_20260918.json"
PRICE_SHEET_ID = "measurement_update_20260918-prices-v1"
UNRESOLVED_OFFICIAL = Path(__file__).resolve().parent / "official-qwen-unresolved-exposures.json"


def _local_path(value: str) -> Path:
    path = Path(value)
    if path.is_file():
        return path
    for marker in ("experimental-results", "analysis", "paper", "code"):
        if marker in path.parts:
            candidate = ROOT.joinpath(*path.parts[path.parts.index(marker):])
            if candidate.is_file():
                return candidate
    return path


def _prices() -> dict:
    return json.loads(PRICE_SHEET.read_text())["prices"]


def usage_pw(usage: dict | None, model: str) -> float:
    usage = usage or {}
    if isinstance(usage.get("totals"), dict):
        usage = usage["totals"]
    price = _prices()[model]
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    cached = usage.get("cached_tokens") or 0
    completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    if not (0 <= cached <= prompt):
        raise ValueError(f"Invalid cached-token subset: cached={cached}, prompt={prompt}")
    return float((prompt - cached) + cached * price["p_c"] / price["p_in"]
                 + completion * price["p_o"] / price["p_in"])


def _sources(record: dict) -> list[dict]:
    if "source_artifacts" in record:
        return copy.deepcopy(record["source_artifacts"])
    source = record.get("source") or {}
    if source:
        if "initial_program" in source:
            rows = [{"role": "initial_program", **source["initial_program"]}]
            rows += [{"role": "source_record", **row} for row in source.get("records", [])]
            return rows
        rows = []
        for role, path_key, hash_key in (
            ("build", "build_path", "build_sha256"),
            ("initial_program", "artifact_path", "artifact_sha256"),
        ):
            if source.get(path_key):
                rows.append({"role": role, "path": source[path_key], "sha256": source.get(hash_key)})
        return rows
    paths, hashes = record.get("source_paths", {}), record.get("source_sha256", {})
    return [{"role": role, "path": path, "sha256": hashes.get(role)}
            for role, path in paths.items() if role in hashes]


def _tasks(record: dict) -> tuple[list[dict], list[dict], list[dict]]:
    tasks = record.get("tasks") or []
    originals = [{"seed": row.get("seed"), "prompt": row.get("prompt"),
                  "prompt_provenance": row.get("prompt_provenance") or row.get("provenance")}
                 for row in tasks]
    extractions = record.get("extractions")
    if extractions is None:
        extractions = [row.get("extraction") for row in tasks if row.get("extraction") is not None]
    normalized_extractions = []
    for index, item in enumerate(extractions or []):
        item = copy.deepcopy(item)
        if item.get("seed") is None:
            item["seed"] = tasks[index].get("seed") if index < len(tasks) else None
        item.setdefault("input", item.get("input_prompt"))
        item.setdefault("output", item.get("outputs"))
        item.setdefault("binding", item.get("extracted_binding"))
        type_check = item.get("type_check")
        if isinstance(type_check, dict):
            item["type_check"] = type_check.get("passed") is True and not type_check.get("errors")
        else:
            item["type_check"] = type_check in (True, "pass", "ok")
        item.setdefault("retry_count", int(bool(item.get("retry_used"))))
        item.setdefault("elapsed_seconds", item.get("wall_s", item.get("elapsed_s", 0.0)))
        normalized_extractions.append(item)
    final = record.get("final_tasks")
    if final is None and record.get("candidate_history"):
        final = record["candidate_history"][-1].get("tasks")
    if final is None:
        final = [{"seed": row.get("seed"), "status": row.get("status", "untested")} for row in tasks]
    return originals, normalized_extractions, copy.deepcopy(final)


def _charge_parts(record: dict) -> tuple[dict, dict]:
    model = record["model"]
    if record.get("charges_pw"):
        src = record["charges_pw"]
        parts = {key: float(src.get(key) or 0) for key in
                 ("reused_initial_translation", "reused_initial_builder", "new_extraction", "new_repair")}
        references = src.get("references") or {}
        return parts, references
    reused = record.get("reused_initial_charges") or (record.get("usage") or {}).get("reused_translation_builder") or {}
    new = record.get("new_charges") or record.get("usage") or {}
    translation = reused.get("reused_initial_translation") or reused.get("translation") or reused.get("translator") or {}
    builder = (reused.get("reused_initial_builder") or reused.get("builder")
               or reused.get("builder_initial_k3")
               or ((reused.get("builder_initial") or {}).get("k3_code")) or {})
    extraction = new.get("extraction") or new.get("new_extraction") or {}
    repair = new.get("repair") or new.get("new_repair") or {}
    failed_stage = (record.get("usage") or {}).get("failed_stage_response_ledger") or {}
    infrastructure_usage = {"prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0}
    for retry in record.get("infrastructure_retries") or []:
        for call in retry.get("calls_detail") or []:
            usage = call.get("usage") or call
            for key in infrastructure_usage:
                infrastructure_usage[key] += usage.get(key) or 0
    parts = {
        "reused_initial_translation": usage_pw(translation, model),
        "reused_initial_builder": usage_pw(builder, model),
        "new_extraction": usage_pw(extraction, model),
        "new_repair": (usage_pw(repair, model) + usage_pw(infrastructure_usage, model)
                       + usage_pw(failed_stage, model)),
    }
    extraction_refs = [f"#/extractions/{i}/calls/{j}"
                       for i, extraction in enumerate(record.get("extractions") or [])
                       for j, _ in enumerate(extraction.get("calls") or [])]
    repair_refs = []
    for i, repair_row in enumerate(record.get("repairs") or []):
        for field in ("calls", "resume_calls_detail", "refinement_calls_detail"):
            repair_refs.extend(f"#/repairs/{i}/{field}/{j}"
                               for j, _ in enumerate(repair_row.get(field) or []))
    repair_refs.extend(f"#/infrastructure_retries/{i}/calls_detail/{j}"
                       for i, retry in enumerate(record.get("infrastructure_retries") or [])
                       for j, _ in enumerate(retry.get("calls_detail") or []))
    if failed_stage.get("calls"):
        repair_refs.append("#/usage/failed_stage_response_ledger")
    references = {
        "reused_initial_translation": "source build record#/translator",
        "reused_initial_builder": "source build record#/builder/initial/k3_code",
        "new_extraction": extraction_refs,
        "new_repair": repair_refs,
    }
    return parts, references


def normalize(record: dict) -> dict:
    """Return the common aggregation view without mutating the native record."""
    out = copy.deepcopy(record)
    out["platform"] = str(out.get("platform", "")).lower()
    status = out.get("terminal_status", out.get("status"))
    if status == "complete" and out.get("failure_kind") == "generation_output_budget_failure":
        status = "generation_output_budget_failure"
    if (status == "failed_stage_error" and out.get("attempt_role") == "repeat"
            and out.get("failure_kind") in {
                "empty_artifact_responses_exhausted", "artifact_generation_attempts_exhausted"}):
        status = "generation_output_budget_failure"
    out["terminal_status"] = {"running": "incomplete", "incomplete_source": "incomplete",
                              "verification_complete": "complete",
                              "provisional_initial": "incomplete"}.get(status, status)
    out.setdefault("attempt_role", "initial")
    out["source_artifacts"] = _sources(record)
    originals, extractions, task_results = _tasks(record)
    out["original_tasks"], out["extractions"], out["task_results"] = originals, extractions, task_results
    out["admission"] = bool(record.get("admission", record.get("admitted", False)))
    out["timings"] = copy.deepcopy(record.get("timings") or record.get("timing") or {})
    out["timings"].setdefault("total_seconds", record.get("wall_s", out["timings"].get("attempt_wall_s", 0.0)))
    parts, references = _charge_parts(record)
    charge_records = []
    for category, amount in parts.items():
        ref = references.get(category)
        refs = ref if isinstance(ref, list) else [ref]
        refs = [item for item in refs if item]
        charge_records.append({"category": category, "amount_pw": amount,
                               "call_record": refs or None if category.startswith("new_") else None,
                               "source_artifact": refs or category if category.startswith("reused_") else None})
    exact = record.get("charges_exact")
    if exact is None:
        completeness = record.get("cost_completeness") or {}
        exact = not bool(record.get("missing_charge_records") or record.get("missing_costs")
                         or record.get("charges_lower_bound") or record.get("cost_lower_bound")
                         or completeness.get("missing"))
    unresolved = None
    if UNRESOLVED_OFFICIAL.is_file():
        for item in json.loads(UNRESOLVED_OFFICIAL.read_text()).get("records", []):
            if (item["platform"] == out["platform"] and item["model"] == record.get("model")
                    and item["family"] == record.get("family")
                    and item["raw_attempt_id"] == record.get("attempt_id")):
                unresolved = item
                exact = False
                break
    out["charges"] = {"unit": "pw", "price_sheet": PRICE_SHEET_ID, "exact": bool(exact), **parts,
                      "C": sum(parts.values()), "charge_records": charge_records}
    if unresolved:
        out["charges"]["lower_bound_reason"] = unresolved["reason"]
        out["charges"]["unsettled_official_calls"] = unresolved["unsettled_calls"]
        out["cost_labels"] = {
            "known_completed_official_cost_cny": unresolved["known_completed_builder_cost_cny"],
            "reservation_ceiling_cny": unresolved["reservation_ceiling_cny"],
            "reservation_is_spend": False,
            "unsettled_calls": unresolved["unsettled_calls"],
            "frozen_pw_C_lower_bound": out["charges"]["C"],
            "note": "The reservation is neither paid cost nor zero; total attempt cost remains a lower bound."
        }
    completeness = record.get("cost_completeness") or {}
    if completeness:
        reused = record.get("reused_initial_charges") or {}
        translator = reused.get("reused_initial_translation") or {}
        builder = reused.get("reused_initial_builder") or {}
        translator_usd = (translator.get("totals") or translator).get("cost_usd") or 0.0
        builder_usd = (builder.get("usage") or builder).get("cost_usd") or 0.0
        new_lower = completeness.get("known_total_lower_bound_usd")
        if new_lower is not None:
            out["cost_labels"] = {
                "known_new_call_lower_bound_usd": new_lower,
                "reused_initial_translation_usd": translator_usd,
                "reused_initial_builder_usd": builder_usd,
                "known_total_acquisition_lower_bound_usd": new_lower + translator_usd + builder_usd,
                "frozen_pw_C": out["charges"]["C"],
                "note": "Provider USD lower bounds and frozen price-weighted-token C are separate accounting units.",
            }
    deployment = copy.deepcopy(record.get("deployment") or {"status": "not_applicable"})
    deployment["status"] = {"new": "new_30_use", "skipped": "not_applicable",
                            "not_run": "pending", "required_if_admitted": "pending"}.get(
                                deployment.get("status"), deployment.get("status"))
    if (deployment.get("status") == "complete" and deployment.get("n") == 30
            and len(deployment.get("uses") or []) == 30):
        deployment["status"] = "new_30_use"
        deployment["records"] = deployment["uses"]
    if out.get("attempt_role") == "repeat" and deployment.get("status") in {
            None, "pending", "not_applicable", "incomplete"}:
        deployment["status"] = "not_required_repeat"
    deployment.setdefault("deployed_program_sha256", deployment.get("historical_deployed_program_sha256")
                          or deployment.get("historical_program_sha256"))
    deployment.setdefault("byte_identical_program", deployment.get("program_byte_identical",
                                                                     deployment.get("program_hash_equal")))
    deployment.setdefault("unchanged_execution_protocol", deployment.get("task_generator_identical",
                                                                           deployment.get("execution_protocol_equal")))
    deployment.setdefault("unchanged_input_protocol", deployment.get("input_protocol_identical",
                                                                       deployment.get("input_protocol_equal")))
    if deployment.get("status") in {"new_30_use", "reused"} and "records" not in deployment:
        deployment["records"] = deployment.get("uses") or deployment.get("detail") or []
        if not deployment["records"]:
            record_paths = deployment.get("record_paths") or []
            if len(record_paths) == 1:
                deployment_record = _local_path(record_paths[0])
                if deployment_record.is_file():
                    payload = json.loads(deployment_record.read_text())
                    deployment["records"] = payload.get("uses") or []
                    deployment["resolved_record_path"] = str(deployment_record.relative_to(ROOT))
                    deployment["record_sha256"] = __import__("hashlib").sha256(
                        deployment_record.read_bytes()).hexdigest()
                    if payload.get("d_tokens_mean") is not None:
                        deployment["reported_raw_d_tokens_mean"] = payload["d_tokens_mean"]
    if deployment.get("status") in {"new_30_use", "reused"} and "profile_metrics" not in out:
        records = deployment.get("records") or []
        if len(records) == 30:
            costs, failures = [], 0
            for use in records:
                extraction = use.get("extraction") or {}
                calls = extraction.get("calls") or use.get("calls_detail") or []
                costs.append(sum(usage_pw(call.get("usage", call), record["model"]) for call in calls))
                passed = use.get("status") == "pass" or use.get("success") is True
                failures += not passed
            out["profile_metrics"] = {
                "d": sum(costs) / len(costs), "q": failures / len(records),
                "source": "30 deployment records; frozen pw from actual extraction tokens",
                "uses": len(records), "failures": failures,
            }
    out["deployment"] = deployment
    official = record.get("official_route_accounting")
    failed_stage = (record.get("usage") or {}).get("failed_stage_response_ledger") or {}
    if not official and failed_stage.get("cost_cny") is not None:
        official = {
            "separate_from_frozen_pw": True,
            "calls": failed_stage.get("calls") or 0,
            "prompt_tokens": failed_stage.get("prompt_tokens") or 0,
            "cached_tokens": failed_stage.get("cached_tokens") or 0,
            "completion_tokens": failed_stage.get("completion_tokens") or 0,
            "cost_cny": float(failed_stage["cost_cny"]),
            "receipts": [],
            "receipt_scope": "aggregate recovered response-call ledger; individual response IDs retained outside canonical result if available",
        }
    if official:
        normalized_official = copy.deepcopy(official)
        actual_usage = {key: official.get(key) or 0 for key in
                        ("prompt_tokens", "cached_tokens", "completion_tokens")}
        normalized_official["frozen_pw_from_actual_tokens"] = usage_pw(actual_usage, record["model"])
        normalized_official["frozen_pw_price_sheet"] = PRICE_SHEET_ID
        normalized_official["cost_cny_separate_from_frozen_pw"] = True
        normalized_official["accounting_note"] = (
            "CNY receipts are retained in their native currency; frozen pw is recomputed only from actual token counts.")
        out["official_route_accounting"] = normalized_official
    return out
