"""Read-only analysis of the frozen recovery-validation pilot.

The ledger is opened with SQLite ``mode=ro``. Importing this module writes
nothing. CLI execution writes only the selected version's ``analysis.json``
and ``analysis.md``. The default version is v2.
Input-equivalent tokens use the frozen model-specific price ratios, before
platform discounts. Cached input and reasoning are subsets, not extra tokens.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "experimental-results/recovery_validation_20260916"
AUTHORIZED_BUDGET_NUSD = 30_000_000_000
NANO = Decimal(1_000_000_000)
DEFAULT_MODELS = ["z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash"]
DEFAULT_FAMILIES = ["MarkorDeleteNote", "FilesMoveFile"]
DEFAULT_ARMS = ["clean", "font_large", "notification_after_selection", "focus_after_commit"]
TOKEN_FIELDS = ("prompt_tokens", "cached_prompt_tokens", "uncached_prompt_tokens",
                "completion_tokens", "reasoning_tokens")
OUTCOME_FIELDS = ("model", "family", "seed", "arm", "treatment", "program_verified",
                  "recovery_used", "policy_complete", "oracle_success", "infrastructure_error",
                  "verification_unavailable", "error", "spec_sha256", "program_sha256",
                  "initial_public_state_sha256", "model_call_count")


def usd(nanos: int) -> str:
    return format(Decimal(nanos) / NANO, ".9f")


def decimal_text(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.000001")), "f") if value is not None else None


def nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def json_read(path: Path, warnings: list[str]) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"Could not read {path}: {type(exc).__name__}")
        return None


def read_ledger(path: Path, warnings: list[str]) -> tuple[list[dict], dict[str, str]]:
    if not path.exists():
        warnings.append("The budget ledger is missing. Observed zero rows do not prove zero spend.")
        return [], {}
    try:
        # Do not instantiate BudgetLedger: its constructor changes pending rows.
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = [dict(row) for row in connection.execute("SELECT * FROM requests ORDER BY created_at, request_id")]
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            connection.rollback()
        finally:
            connection.close()
        return rows, metadata
    except sqlite3.Error as exc:
        warnings.append(f"Could not read the ledger snapshot: {type(exc).__name__}")
        return [], {}


def episode_identity(label: str) -> dict[str, str | None]:
    match = re.match(r"^(v[1-9][0-9]*)/", label)
    version = match.group(1) if match else "v1"
    local = label[match.end():] if match else label
    stage = local.split("/", 1)[0] or "unknown"
    if "/recovery/" in local:
        base, step = local.split("/recovery/", 1)
        operation = "reactive_actor" if base.endswith("/reactive") else "recovery"
    elif local.endswith("/binding"):
        base, operation, step = local[:-8], "binding", None
    else:
        base, step = local, None
        operation = "compilation" if stage == "build" else "preflight" if stage in {"smoke", "preflight"} else "other"
    parts = base.split("/")
    family = parts[2] if stage in {"build", "development", "screen", "confirm", "anchors"} and len(parts) > 2 else None
    lifecycle = {
        "build": "one_time_compilation", "development": "one_time_development",
        "smoke": "preflight", "preflight": "preflight",
        "screen": "screening_serving", "confirm": "confirmation_serving",
        "anchors": "reactive_anchors",
    }.get(stage, "other")
    return {"version": version, "stage": stage, "operation": operation,
            "family": family, "episode": base, "episode_key": f"{version}/{base}",
            "recovery_step": step, "lifecycle": lifecycle}


def extract_prices(lock_data: Any, source: str, warnings: list[str]) -> dict[str, dict[str, Decimal]]:
    if not isinstance(lock_data, dict):
        return {}
    prices = {}
    for model, lock in lock_data.items():
        try:
            endpoints = lock["endpoints"]
            if not endpoints:
                raise ValueError("Empty pool")
            selected = {key: Decimal(str(endpoints[0]["pricing"][key]))
                        for key in ("prompt", "completion", "input_cache_read")}
            if not all(value.is_finite() and value >= 0 for value in selected.values()) or selected["prompt"] <= 0:
                raise ValueError("Invalid price")
            if any(any(Decimal(str(endpoint["pricing"][key])) != value
                       for key, value in selected.items()) for endpoint in endpoints):
                raise ValueError("Mixed quotes")
            prices[model] = selected
        except (KeyError, TypeError, ValueError, InvalidOperation):
            warnings.append(f"No consistent token-price pool for {model} in {source}")
    return prices


def reasoning_anomaly(row: dict) -> dict | None:
    try:
        billing = json.loads(row.get("billing_json") or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(billing, dict) or not billing.get("reasoning_tokens_anomaly"):
        return None
    anomaly = billing["reasoning_tokens_anomaly"]
    return {"details": anomaly, "raw_value": billing.get("reasoning_tokens_raw", anomaly.get("raw_value") if isinstance(anomaly, dict) else None)}


def token_counts(row: dict) -> dict[str, int | None]:
    counts = {field: nonnegative_int(row.get(field)) for field in TOKEN_FIELDS if field != "uncached_prompt_tokens"}
    anomalous_reasoning = reasoning_anomaly(row)
    if anomalous_reasoning is not None:
        counts["reasoning_tokens"] = None
    # Malformed/unknown receipts may be saved before validated columns are filled.
    # These remain reported counts and never become settled cost observations.
    try:
        usage = json.loads(row.get("usage_json") or "null")
    except (TypeError, ValueError):
        usage = None
    if isinstance(usage, dict):
        raw = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "cached_prompt_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            if isinstance(usage.get("prompt_tokens_details"), dict) else None,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            if isinstance(usage.get("completion_tokens_details"), dict) else None,
        }
        for field, value in raw.items():
            if field == "reasoning_tokens" and anomalous_reasoning is not None:
                continue
            if counts[field] is None:
                counts[field] = nonnegative_int(value)
    prompt, cached = counts["prompt_tokens"], counts["cached_prompt_tokens"]
    counts["uncached_prompt_tokens"] = prompt - cached if prompt is not None and cached is not None and cached <= prompt else None
    return counts


def is_settled(row: dict) -> bool:
    return row.get("state") == "settled" and nonnegative_int(row.get("actual_cost_nusd")) is not None


def price_equivalent(row: dict, prices_by_version: dict) -> Decimal | None:
    identity = episode_identity(str(row.get("episode", "")))
    prices = prices_by_version.get(identity["version"], {}).get(row.get("model_requested"))
    counts = token_counts(row)
    if not prices or any(counts[field] is None for field in ("uncached_prompt_tokens", "cached_prompt_tokens", "completion_tokens")):
        return None
    return (Decimal(counts["uncached_prompt_tokens"])
            + Decimal(counts["cached_prompt_tokens"]) * prices["input_cache_read"] / prices["prompt"]
            + Decimal(counts["completion_tokens"]) * prices["completion"] / prices["prompt"])


def summarize_calls(rows: list[dict], prices_by_version: dict) -> dict:
    states = Counter(str(row.get("state", "missing")) for row in rows)
    settled = [row for row in rows if is_settled(row)]
    unresolved = [row for row in rows if not is_settled(row)]
    actual = sum(row["actual_cost_nusd"] for row in settled)
    reported = sum(nonnegative_int(row.get("actual_cost_nusd")) or 0 for row in rows)
    retained = sum(max(nonnegative_int(row.get("reservation_nusd")) or 0,
                       nonnegative_int(row.get("actual_cost_nusd")) or 0) for row in unresolved)
    known_token_sums, missing = {}, {}
    for field in TOKEN_FIELDS:
        values = [token_counts(row)[field] for row in rows]
        known_token_sums[field] = sum(value for value in values if value is not None)
        missing[field] = sum(value is None for value in values)
    settled_tokens = {field: sum(token_counts(row)[field] or 0 for row in settled) for field in TOKEN_FIELDS}
    settled_missing = {field: sum(token_counts(row)[field] is None for row in settled) for field in TOKEN_FIELDS}
    equivalents = [price_equivalent(row, prices_by_version) for row in settled]
    models = {row.get("model_requested") for row in rows}
    # A GLM input-price unit and a DeepSeek input-price unit are different units.
    equivalent = sum(equivalents, Decimal(0)) if len(models) <= 1 and all(value is not None for value in equivalents) else None
    expected_values = [nonnegative_int(row.get("expected_cost_nusd")) for row in settled]
    expected = sum(value for value in expected_values if value is not None)
    discount = sum(nonnegative_int(row.get("platform_discount_nusd")) or 0 for row in settled)
    anomalies = [{"request_id": row.get("request_id"), "episode": row.get("episode"),
                  "model": row.get("model_requested"), "state": row.get("state"),
                  "billed_completion_tokens": token_counts(row)["completion_tokens"],
                  **reasoning_anomaly(row)} for row in rows if reasoning_anomaly(row) is not None]
    return {
        "requests": len(rows), "states": dict(sorted(states.items())),
        "settled_requests": len(settled), "unresolved_requests": len(unresolved),
        "settled_actual_nusd": actual, "settled_actual_usd": usd(actual),
        "reported_actual_including_unresolved_nusd": reported,
        "unresolved_exposure_nusd": retained, "unresolved_exposure_usd": usd(retained),
        "total_exposure_nusd": actual + retained, "total_exposure_usd": usd(actual + retained),
        "settled_expected_quote_nusd": expected, "settled_expected_quote_usd": usd(expected),
        "settled_missing_expected_cost": sum(value is None for value in expected_values),
        "settled_platform_discount_nusd": discount, "settled_platform_discount_usd": usd(discount),
        "reported_token_sums": known_token_sums, "reported_missing_token_fields": missing,
        "settled_token_sums": settled_tokens, "settled_missing_token_fields": settled_missing,
        "settled_input_equivalent_tokens": decimal_text(equivalent),
        "reasoning_anomaly_requests": len(anomalies), "reasoning_anomalies": anomalies,
        "settled_reasoning_anomaly_requests": sum(reasoning_anomaly(row) is not None for row in settled),
        "input_equivalent_missing_prices_or_counts": sum(value is None for value in equivalents),
        "mixed_model_input_units_not_summed": len(models) > 1,
    }


def group_calls(rows: list[dict], prices: dict, fields: tuple[str, ...]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        identity = {**episode_identity(str(row.get("episode", ""))), "model": row.get("model_requested", "missing")}
        grouped[tuple(identity[field] for field in fields)].append(row)
    return [{**dict(zip(fields, key)), **summarize_calls(members, prices)}
            for key, members in sorted(grouped.items(), key=lambda item: tuple(str(part) for part in item[0]))]


def outcome_exclusion_reasons(row: dict) -> list[str]:
    reasons = []
    for field, reason in (("infrastructure_error", "infrastructure_error"),
                          ("verification_unavailable", "verification_unavailable"),
                          ("_model_call_count_mismatch", "ledger_call_count_mismatch"),
                          ("_missing_result", "started_without_result"),
                          ("_data_conflict", "conflicting_outcome_records")):
        if row.get(field):
            reasons.append(reason)
    if row.get("error") not in (None, ""):
        reasons.append("terminal_error")
    if type(row.get("oracle_success")) is not bool:
        reasons.append("oracle_outcome_missing")
    return reasons


def outcome_available(row: dict) -> bool:
    return not outcome_exclusion_reasons(row)


def attach_zero_call_evidence(outcomes: dict, episode_costs: dict, prices: dict,
                              version: str, warnings: list[str]) -> None:
    for episode, row in outcomes.items():
        declared = row.get("model_call_count")
        if type(declared) is not int or declared < 0:
            continue
        key = version + "/" + episode
        cost = episode_costs.get(key)
        actual = cost["requests"] if cost is not None else 0
        if declared != actual:
            row["_model_call_count_mismatch"] = True
            warnings.append(f"Terminal model_call_count differs from ledger rows for {key}")
            continue
        if declared == 0 and cost is None and outcome_available(row):
            episode_costs[key] = {
                **episode_identity(key), "model": row.get("model"), **summarize_calls([], prices),
                "operations": {}, "zero_model_calls_recorded": True,
                "cost_evidence": "Complete terminal result explicitly records model_call_count=0 and no ledger request exists",
                "model_label_proves_inference": False,
            }


def anchor_summary(outcomes: dict, episode_costs: dict, version: str,
                   models: list, families: list, spec: dict) -> dict:
    anchors = []
    for row in outcomes.values():
        if not row["episode"].startswith("anchors/"):
            continue
        cost = episode_costs.get(version + "/" + row["episode"])
        known = bool(cost and (cost["requests"] > 0 or cost.get("zero_model_calls_recorded")))
        reasons = outcome_exclusion_reasons(row)
        if row.get("arm") != "clean" and row.get("perturbation_triggered") is not True:
            reasons.append("perturbation_not_realized")
        if not known:
            reasons.append("ledger_calls_missing")
        elif cost["unresolved_requests"]:
            reasons.append("unresolved_billing")
        anchors.append({
            **{field: row.get(field) for field in ("episode", "model", "family", "seed", "arm", "treatment",
                                                  "oracle_success", "policy_complete", "actions", "model_call_count", "error")},
            "perturbation_triggered": row.get("perturbation_triggered"),
            "valid_outcome_and_billing": not reasons, "exclusion_reasons": reasons,
            "actual_model_calls": cost["requests"] if known else None,
            "settled_actual_usd": cost["settled_actual_usd"] if known else None,
            "settled_actual_nusd": cost["settled_actual_nusd"] if known else None,
            "actual_model_inference_observed": bool(cost and cost["settled_requests"] > 0),
        })
    return {
        "expected_if_all_admitted": len(models) * len(families) * 2 if spec.get("unconditional_reactive_anchors") else None,
        "recorded_episodes": len(anchors), "rows": anchors,
        "models_with_actual_reactive_calls": sorted({row["model"] for row in anchors if row["actual_model_inference_observed"]}),
        "valid_oracle_success": sum(row["valid_outcome_and_billing"] and row["oracle_success"] is True for row in anchors),
        "settled_actual_usd": usd(sum(row["settled_actual_nusd"] or 0 for row in anchors)),
        "interpretation": "Separate compact-reactive reference runs. Their actual calls test model-agent behavior. Zero-call deterministic routing labels do not establish independent model inference or model-specific performance.",
    }


def load_outcomes(directory: Path, warnings: list[str], version: str = "v2") -> tuple[dict[str, dict], list[dict], dict[str, Any]]:
    outcomes: dict[str, dict] = {}
    inputs = {}

    def add(row: Any, source: Path, canonical: bool = False):
        if not isinstance(row, dict) or not isinstance(row.get("episode"), str):
            warnings.append(f"Outcome without an episode ID in {source}")
            return
        episode = row["episode"].removeprefix(version + "/")
        old = outcomes.get(episode)
        conflict = bool(old and any(key in old and key in row and old[key] != row[key] for key in OUTCOME_FIELDS))
        selected = dict(row) if old is None or canonical else dict(old)
        selected["episode"] = episode
        selected["_sources"] = sorted(set((old or {}).get("_sources", []) + [str(source)]))
        if conflict or (old or {}).get("_data_conflict"):
            selected["_data_conflict"] = True
            if conflict:
                warnings.append(f"Conflicting outcome records for {episode}")
        outcomes[episode] = selected

    admissions = {}
    admission_path = directory / "admission.json"
    aggregate = json_read(admission_path, warnings)
    inputs["admission.json"] = aggregate is not None
    admission_sources = [(admission_path, aggregate)]
    admission_sources += [(path, json_read(path, warnings)) for path in sorted((directory / "build").glob("*/*/admission.json"))]
    for source, data in admission_sources:
        records = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for record in records:
            key = (record.get("model"), record.get("family"))
            if None in key:
                warnings.append(f"Admission lacks model or family in {source}")
                continue
            if key in admissions and admissions[key].get("admitted") != record.get("admitted"):
                warnings.append(f"Conflicting admission for {key[0]} / {key[1]}")
                record = {**record, "_data_conflict": True}
            admissions[key] = {**record, "_source": str(source)}
            if not record.get("qualification_reused_from"):
                for row in record.get("development", []):
                    add(row, source)
    for name in ("screen-results.json", "confirmation-results.json", "anchor-results.json"):
        path = directory / name
        rows = json_read(path, warnings)
        inputs[name] = rows is not None
        if isinstance(rows, list):
            for row in rows:
                add(row, path)
    for path in sorted((directory / "episodes").glob("**/result.json")):
        add(json_read(path, warnings), path, canonical=True)
    for path in sorted((directory / "episodes").glob("**/started.json")):
        if path.with_name("result.json").exists():
            continue
        row = json_read(path, warnings)
        if isinstance(row, dict) and isinstance(row.get("episode"), str):
            row = {**row, "_missing_result": True, "oracle_success": None,
                   "error": "Episode started without a terminal result file"}
            add(row, path, canonical=True)
    return outcomes, list(admissions.values()), inputs


def admission_summary(admissions: list[dict], outcomes: dict, models: list, families: list) -> list[dict]:
    indexed = {(row["model"], row["family"]): row for row in admissions}
    result = []
    for model in models:
        for family in families:
            admission = indexed.get((model, family))
            reused_from = admission.get("qualification_reused_from") if admission else None
            current_development = [row for row in outcomes.values() if row.get("model") == model
                                   and row.get("family") == family and row["episode"].startswith("development/")]
            development = [dict(row) for row in admission.get("development", [])] if reused_from else current_development
            valid = [row for row in development if outcome_available(row)]
            fixed_controls = bool(admission and admission.get("fixed_public_contract") is True
                                  and admission.get("last_attempt") is None)
            final_version = f"/v{admission.get('last_attempt')}/" if admission else None
            final_dev = list(valid) if fixed_controls else [row for row in valid if final_version and final_version in row["episode"]]
            result.append({
                "model": model, "family": family,
                "status": "missing" if admission is None else "conflict" if admission.get("_data_conflict") else "admitted" if admission.get("admitted") is True else "not_admitted",
                "last_attempt": admission.get("last_attempt") if admission else None,
                "compiled_by": admission.get("compiled_by") if admission else None,
                "borrowed_from": admission.get("borrowed_from") if admission else None,
                "program_sha256": admission.get("program_sha256") if admission else None,
                "development_protocol": "fixed_controls" if fixed_controls else "generated_program_attempt",
                "qualification_reused_from": reused_from,
                "qualification_spec_sha256": admission.get("qualification_spec_sha256") if admission else None,
                "new_development_episodes": len(current_development),
                "development_scope": "reused_parent_qualification" if reused_from else "current_version",
                "development_episodes": len(development), "development_valid": len(valid),
                "development_excluded": len(development) - len(valid),
                "development_infrastructure_or_missing": sum(bool(row.get("infrastructure_error") or row.get("_missing_result"))
                    or type(row.get("oracle_success")) is not bool for row in development),
                "development_terminal_errors": sum(row.get("error") not in (None, "") for row in development),
                "development_verification_unavailable": sum(bool(row.get("verification_unavailable")) for row in development),
                "development_program_and_oracle_success": sum(row.get("program_verified") is True and row["oracle_success"] for row in valid),
                "final_attempt_valid_development": len(final_dev),
                "final_attempt_program_and_oracle_success": sum(row.get("program_verified") is True and row["oracle_success"] for row in final_dev),
                "development_episode_ids": [row["episode"] for row in development],
            })
    return result


def screening_summary(outcomes: dict, admissions: list[dict], models: list, families: list,
                      arms: list, expected_bindings: int, *, shared_deterministic: bool = False) -> list[dict]:
    indexed = {(row["model"], row["family"]): row for row in admissions}
    summaries = []
    screening_models = ["deterministic_shared"] if shared_deterministic else models
    for model in screening_models:
        for family in families:
            if shared_deterministic:
                eligible = [row for row in admissions if row["family"] == family and row["status"] == "admitted"]
                admission = {"status": "admitted" if eligible else "missing_qualified_shared_program"}
            else:
                admission = indexed[(model, family)]
            for arm in arms:
                rows = [row for row in outcomes.values() if row["episode"].startswith("screen/")
                        and (row.get("model"), row.get("family"), row.get("arm")) == (model, family, arm)]
                valid = [row for row in rows if outcome_available(row)]
                realized = [row for row in valid if arm == "clean" or row.get("perturbation_triggered") is True]
                excluded = []
                for row in rows:
                    reasons = outcome_exclusion_reasons(row)
                    if arm != "clean" and row.get("perturbation_triggered") is not True:
                        reasons.append("perturbation_not_realized")
                    if reasons:
                        excluded.append({"episode": row["episode"], "reasons": reasons,
                            "error": row.get("error"), "verification_unavailable": row.get("verification_unavailable", False),
                            "program_verified": row.get("program_verified"), "oracle_success": row.get("oracle_success"),
                            "perturbation_triggered": row.get("perturbation_triggered")})
                false_accept = sum(row.get("program_verified") is True and not row["oracle_success"] for row in realized)
                false_reject = sum(row.get("program_verified") is False and row["oracle_success"] for row in realized)
                summaries.append({
                    "model": model, "family": family, "arm": arm,
                    "shared_deterministic_prefix": shared_deterministic,
                    "model_inference_implied_by_label": False if shared_deterministic else None,
                    "admission_status": admission["status"],
                    "planned_if_admitted": expected_bindings,
                    "expected_episodes": expected_bindings if admission["status"] == "admitted" else 0,
                    "recorded_episodes": len(rows), "valid_outcomes": len(valid),
                    "zero_model_call_outcomes": sum(type(row.get("model_call_count")) is int and row["model_call_count"] == 0 for row in valid),
                    "infrastructure_error": sum(bool(row.get("infrastructure_error")) for row in rows),
                    "verification_unavailable": sum(bool(row.get("verification_unavailable")) for row in rows),
                    "terminal_error": sum(row.get("error") not in (None, "") for row in rows),
                    "started_without_result": sum(bool(row.get("_missing_result")) for row in rows),
                    "missing_or_invalid_outcome": len(rows) - len(valid),
                    "triggered": sum(row.get("perturbation_triggered") is True for row in rows),
                    "realized_exposure_valid": len(realized),
                    "untriggered_valid": sum(row.get("perturbation_triggered") is False for row in valid) if arm != "clean" else 0,
                    "verified": sum(row.get("program_verified") is True for row in valid),
                    "oracle_success": sum(row["oracle_success"] for row in valid),
                    "program_and_oracle_success": sum(row.get("program_verified") is True and row["oracle_success"] for row in realized),
                    "program_false_accept": false_accept, "program_false_reject": false_reject,
                    "verified_true_oracle_false": false_accept,
                    "verified_false_oracle_true": false_reject,
                    "program_and_task_failed": sum(row.get("program_verified") is False and not row["oracle_success"] for row in realized),
                    "program_sha256s": sorted({row["program_sha256"] for row in realized if isinstance(row.get("program_sha256"), str)}),
                    "genuine_failure_program_sha256s": sorted({row["program_sha256"] for row in realized
                        if row.get("program_verified") is False and not row["oracle_success"] and isinstance(row.get("program_sha256"), str)}),
                    "excluded_from_realized_comparisons": len(excluded), "excluded_episodes": excluded,
                    "episode_ids": [row["episode"] for row in rows],
                })
    return summaries


def confirmation_analysis(pairing: Any, outcomes: dict, episode_costs: dict,
                          warnings: list[str], version: str = "v2", *, require_data_match: bool = False) -> dict:
    results = []
    if not isinstance(pairing, list):
        pairing = []
    seen = set()
    for item in pairing:
        if not isinstance(item, dict):
            warnings.append("Malformed confirmation pairing entry")
            continue
        key = tuple(item.get(field) for field in ("model", "family", "seed", "arm"))
        reasons = []
        if key in seen:
            reasons.append("duplicate_pair_manifest_key")
        seen.add(key)
        if item.get("exposure_matched") is not True:
            reasons.append("exposure_not_matched")
        identifiers = item.get("episodes", [])
        members = [outcomes.get(str(episode).removeprefix(version + "/")) for episode in identifiers]
        if len(members) != 2 or any(row is None for row in members):
            reasons.append("missing_pair_member")
        available = [row for row in members if row is not None]
        by_treatment = {row.get("treatment"): row for row in available}
        if set(by_treatment) != {"fallback", "reactive"}:
            reasons.append("expected_treatments_missing")
        if any(tuple(row.get(field) for field in ("model", "family", "seed", "arm")) != key for row in available):
            reasons.append("pair_identity_mismatch")
        if any(not outcome_available(row) for row in available):
            reasons.extend(reason for row in available for reason in outcome_exclusion_reasons(row))
        for field, reason in (("program_sha256", "program_source_mismatch"),
                              ("initial_public_state_sha256", "initial_public_state_mismatch")):
            hashes = [row.get(field) for row in available]
            if require_data_match or any(value is not None for value in hashes):
                if len(hashes) != 2 or not all(isinstance(value, str) and value for value in hashes) or hashes[0] != hashes[1]:
                    reasons.append(reason)
        if item.get("arm") != "clean" and any(row.get("perturbation_triggered") is not True for row in available):
            reasons.append("realized_exposure_not_verified")
        costs = {name: episode_costs.get(version + "/" + row["episode"]) for name, row in by_treatment.items()}
        if any(cost is None or (cost["requests"] == 0 and not cost.get("zero_model_calls_recorded"))
               for cost in costs.values()) or len(costs) != 2:
            reasons.append("ledger_calls_missing")
        if any(cost is not None and cost["unresolved_requests"] for cost in costs.values()):
            reasons.append("unresolved_billing")
        result = {field: item.get(field) for field in ("model", "family", "seed", "arm", "exposure_matched")}
        result.update({"episode_ids": identifiers, "included": not reasons,
                       "exclusion_reasons": sorted(set(reasons))})
        if not reasons:
            fallback, reactive = by_treatment["fallback"], by_treatment["reactive"]
            fallback_cost, reactive_cost = costs["fallback"], costs["reactive"]
            both_success = fallback["oracle_success"] and reactive["oracle_success"]
            difference = reactive_cost["settled_actual_nusd"] - fallback_cost["settled_actual_nusd"]
            denominator = reactive_cost["settled_actual_nusd"]
            recovery_cost = fallback_cost["operations"].get("recovery", {})
            result.update({
                "fallback_oracle_success": fallback["oracle_success"],
                "reactive_oracle_success": reactive["oracle_success"],
                "both_oracle_success": both_success,
                "fallback_policy_complete": fallback.get("policy_complete"),
                "fallback_program_verified": fallback.get("program_verified"),
                "program_sha256": fallback.get("program_sha256"),
                "initial_public_state_sha256": fallback.get("initial_public_state_sha256"),
                "fallback_recovery_used": fallback.get("recovery_used"),
                "fallback_actual_usd": fallback_cost["settled_actual_usd"],
                "reactive_actual_usd": reactive_cost["settled_actual_usd"],
                "fallback_actual_nusd": fallback_cost["settled_actual_nusd"],
                "reactive_actual_nusd": reactive_cost["settled_actual_nusd"],
                "fallback_input_equivalent_tokens": fallback_cost["settled_input_equivalent_tokens"],
                "reactive_input_equivalent_tokens": reactive_cost["settled_input_equivalent_tokens"],
                "fallback_recovery_requests": recovery_cost.get("requests", 0),
                "fallback_recovery_actual_nusd": recovery_cost.get("settled_actual_nusd", 0),
                "fallback_recovery_actual_usd": recovery_cost.get("settled_actual_usd", usd(0)),
                "both_success_actual_difference_nusd": difference if both_success else None,
                "both_success_actual_difference_usd": usd(difference) if both_success else None,
                "both_success_actual_reduction_fraction": decimal_text(Decimal(difference) / denominator) if both_success and denominator else None,
            })
        results.append(result)
    represented = {str(episode).removeprefix(version + "/") for item in pairing if isinstance(item, dict) for episode in item.get("episodes", [])}
    unpaired = [row["episode"] for row in outcomes.values() if row["episode"].startswith("confirm/") and row["episode"] not in represented]
    included = [row for row in results if row["included"]]
    return {"pairs": results, "included_pairs": len(included),
            "excluded_pairs": len(results) - len(included),
            "both_success_pairs": sum(row["both_oracle_success"] for row in included),
            "confirmation_episodes_without_pair_manifest": sorted(unpaired),
            "comparison_rule": "Only exposure-matched, identity-matched pairs with complete outcomes and settled bills are compared. Cost-reduction ratios are shown only when both policies succeed.",
            "statistical_claim": "Descriptive paired observations only. Repeated bindings are clustered within model and task family. No significance claim."}


def anchor_pair_analysis(pairing: Any, outcomes: dict, episode_costs: dict,
                         version: str, models: list) -> dict:
    results = []
    seen_reactive = set()
    for item in pairing if isinstance(pairing, list) else []:
        if not isinstance(item, dict):
            continue
        program_id = item.get("program_episode")
        reactive_id = item.get("reactive_episode")
        program_id = program_id.removeprefix(version + "/") if isinstance(program_id, str) else None
        reactive_id = reactive_id.removeprefix(version + "/") if isinstance(reactive_id, str) else None
        program, reactive = outcomes.get(program_id), outcomes.get(reactive_id)
        reasons = []
        if reactive_id in seen_reactive:
            reasons.append("duplicate_reactive_pair")
        seen_reactive.add(reactive_id)
        for field, reason in (("initial_data_matched", "manifest_initial_data_not_matched"),
                              ("exposure_matched", "manifest_exposure_not_matched"),
                              ("program_source_matched", "manifest_program_not_matched")):
            if item.get(field) is not True:
                reasons.append(reason)
        if program is None or reactive is None:
            reasons.append("missing_pair_member")
        members = [row for row in (program, reactive) if row is not None]
        reasons.extend(reason for row in members for reason in outcome_exclusion_reasons(row))
        if program is not None and (program.get("model") != "deterministic_shared"
                                    or program.get("treatment") != "deterministic"
                                    or program.get("shared_deterministic_prefix") is not True):
            reasons.append("shared_deterministic_baseline_missing")
        if reactive is not None and (reactive.get("model") not in models or reactive.get("treatment") != "reactive"):
            reasons.append("configured_reactive_model_missing")
        if len(members) == 2:
            for field in ("family", "seed", "arm"):
                if program.get(field) != reactive.get(field):
                    reasons.append("pair_identity_mismatch")
            for field, reason in (("program_sha256", "program_source_mismatch"),
                                  ("initial_public_state_sha256", "initial_public_state_mismatch")):
                left, right = program.get(field), reactive.get(field)
                if not isinstance(left, str) or not left or left != right:
                    reasons.append(reason)
            if reactive.get("arm") != "clean" and not all(row.get("perturbation_triggered") is True for row in members):
                reasons.append("realized_exposure_not_verified")
        program_cost = episode_costs.get(version + "/" + program_id) if program_id else None
        reactive_cost = episode_costs.get(version + "/" + reactive_id) if reactive_id else None
        if not program_cost or not program_cost.get("zero_model_calls_recorded") or program_cost["requests"] != 0:
            reasons.append("explicit_zero_call_program_evidence_missing")
        if not reactive_cost or reactive_cost["requests"] <= 0:
            reasons.append("reactive_model_calls_missing")
        elif reactive_cost["unresolved_requests"]:
            reasons.append("unresolved_reactive_billing")
        elif reactive is not None and reactive_cost.get("model") != reactive.get("model"):
            reasons.append("reactive_receipt_model_mismatch")
        result = {
            "program_episode": program_id, "reactive_episode": reactive_id,
            **{field: reactive.get(field) if reactive else None for field in ("model", "family", "seed", "arm")},
            "included": not reasons, "exclusion_reasons": sorted(set(reasons)),
            "program_source_matched": item.get("program_source_matched"),
            "initial_data_matched": item.get("initial_data_matched"), "exposure_matched": item.get("exposure_matched"),
        }
        if not reasons:
            both_oracle = program["oracle_success"] and reactive["oracle_success"]
            both_complete = both_oracle and program.get("program_verified") is True and reactive.get("policy_complete") is True
            difference = reactive_cost["settled_actual_nusd"] - program_cost["settled_actual_nusd"]
            result.update({
                "program_sha256": program["program_sha256"],
                "initial_public_state_sha256": program["initial_public_state_sha256"],
                "program_verified": program.get("program_verified"),
                "program_oracle_success": program["oracle_success"], "reactive_oracle_success": reactive["oracle_success"],
                "both_oracle_success": both_oracle, "both_policy_and_oracle_success": both_complete,
                "program_model_calls": 0, "reactive_model_calls": reactive_cost["requests"],
                "program_actual_nusd": program_cost["settled_actual_nusd"], "program_actual_usd": program_cost["settled_actual_usd"],
                "reactive_actual_nusd": reactive_cost["settled_actual_nusd"], "reactive_actual_usd": reactive_cost["settled_actual_usd"],
                "reactive_input_equivalent_tokens": reactive_cost["settled_input_equivalent_tokens"],
                "successful_marginal_difference_nusd": difference if both_complete else None,
                "successful_marginal_difference_usd": usd(difference) if both_complete else None,
            })
        results.append(result)
    included = [row for row in results if row["included"]]
    return {
        "pairs": results, "included_pairs": len(included), "excluded_pairs": len(results) - len(included),
        "both_policy_and_oracle_success_pairs": sum(row["both_policy_and_oracle_success"] for row in included),
        "unique_shared_program_episodes": len({row["program_episode"] for row in included}),
        "interpretation": "The same shared deterministic episode may be paired with both real models. These pairs do not duplicate the deterministic run or establish independent deterministic model behavior. Costs are marginal serving costs; v2 acquisition and compilation remain in the shared budget.",
    }


def premise_decision(models: list, admissions: list, screening: list, confirmation: dict,
                     *, spec_present: bool, audit_blocked: bool, warnings: list[str]) -> dict:
    by_model = []
    for model in models:
        admitted = [row for row in admissions if row["model"] == model]
        screened = [row for row in screening if row["model"] in {model, "deterministic_shared"}]
        failures = [row for row in screened if row["arm"] != "clean" and row["program_and_task_failed"] > 0]
        def matching_failure(pair):
            return any((row["family"], row["arm"]) == (pair["family"], pair["arm"])
                       and (row["model"] != "deterministic_shared"
                            or (pair.get("program_sha256") and pair["program_sha256"] in row.get("genuine_failure_program_sha256s", [])))
                       for row in failures)
        clean_valid_families = {row["family"] for row in admitted if row["status"] == "admitted"
                               and row["final_attempt_valid_development"] >= 2
                               and row["final_attempt_program_and_oracle_success"] == row["final_attempt_valid_development"]}
        observed_recoveries = [row for row in confirmation["pairs"] if row["included"] and row["model"] == model
                              and row["fallback_program_verified"] is False and row["fallback_recovery_used"] is True
                              and row["fallback_recovery_requests"] > 0]
        evidence = [row for row in confirmation["pairs"] if row["included"] and row["model"] == model
                    and matching_failure(row) and row["family"] in clean_valid_families
                    and row["fallback_program_verified"] is False and row["fallback_recovery_used"] is True
                    and row["fallback_oracle_success"] is True and row["fallback_policy_complete"] is True
                    and row["fallback_recovery_requests"] > 0]
        complete_success = (spec_present and not observed_recoveries and admitted and all(row["status"] == "admitted" for row in admitted)
                            and screened and all(row["expected_episodes"] > 0
                            and row["recorded_episodes"] == row["expected_episodes"]
                            and row["realized_exposure_valid"] == row["expected_episodes"]
                            and row["program_and_oracle_success"] == row["expected_episodes"] for row in screened))
        status = "supports" if evidence else "no_support" if complete_success else "inconclusive"
        by_model.append({"model": model, "status": status,
                         "screen_evidence_shared": any(row["model"] == "deterministic_shared" for row in screened),
                         "matched_fallback_recovery_cases_observed": len(observed_recoveries),
                         "matched_residual_recovery_cases": len(evidence),
                         "model_family_arm_clusters": sorted({f"{row['family']}/{row['arm']}" for row in evidence}),
                         "observed_recovery_actual_usd": usd(sum(row["fallback_recovery_actual_nusd"] for row in evidence)),
                         "complete_realized_screening_without_program_failures": bool(complete_success)})
    statuses = {row["status"] for row in by_model}
    overall = "supports" if statuses == {"supports"} else "no_support" if statuses == {"no_support"} else "inconclusive"
    if warnings or audit_blocked:
        overall = "inconclusive"
    explanations = {
        "supports": "Both specified models show settled model-recovery expenditure after genuine program and task failures under realized perturbations, with successful matched confirmation recoveries. This supports a residual-cost premise within the observed task families.",
        "no_support": "The admitted programs completed the entire planned, realized screening sample under both model-routing labels without a residual program failure. Zero-call program runs do not constitute independent model-inference validation. This pilot provides no support for residual program recovery cost within that tested scope.",
        "inconclusive": "The available records do not establish the residual-cost premise across both specified models. Check admission, realized perturbation coverage, matched confirmation, and billing completeness before drawing a stronger conclusion.",
    }
    if any(row["model"] == "deterministic_shared" for row in screening):
        explanations["no_support"] = "The shared deterministic programs completed the planned, realized screening sample without a residual program failure. This is one shared screen across recovery configurations. Actual model inference is measured separately in reactive anchors and any recovery calls. The tested shared programs provide no support for residual recovery cost in this scope."
    return {"status": "inconclusive", "observed_residual_cost_status": overall,
            "explanation": explanations[overall] + " Clean admission alone does not establish a strong verifier baseline, so these records do not yet establish the stronger premise requested by the study.",
            "by_model": by_model,
            "baseline_quality": {"strong_assertion_baseline_established": False,
                "basis": "Two clean development executions establish observed admission only. Independent verifier/control quality assessment is not inferred from these outcome fields.",
                "implication": "The observed residual-cost status is descriptive and conditional on verifier correctness. Main-premise status remains inconclusive until the separate baseline-control audit is integrated."},
            "new_recovery_method_tested": False, "further_cost_reducibility_established": False,
            "scope": "Existence of observed residual recovery expenditure after admitted programs, conditional on verifier correctness. A strong assertion baseline and further cost reducibility are separate prerequisites.",
            "decision_rule": "Observed cost support requires successful, billed matched recoveries after realized perturbation failures in both models and clean development admission. Observed no support requires complete realized screening with all programs succeeding. The stronger premise additionally requires an independent baseline-control audit, which is not inferred here.",
            "by_model_status_basis": "Descriptive residual-cost evidence only, conditional on verifier correctness.",
            "limitations": [
                "This is a small AndroidWorld pilot with repeated bindings in two task families, not independent coverage of computer-use agents generally.",
                "Programs may be shared across routing model labels. A zero-call result measures deterministic execution only; model-agent behavior is assessed from actual billed reactive or recovery calls.",
                "Confirmation arms were adaptively selected from screening in a frozen order. Confirmation is conditional on that selection.",
                "A triggered flag does not prove equal perturbation timing or equal difficulty for the two policies.",
                "Observed recovery use does not prove that recovery was necessary or that a new recovery method will be better.",
                "Generated assertions can have false accepts or false rejects. Terminal benchmark evaluation is reported separately.",
                "Historical demonstration acquisition was reused and is not billed in this new ledger. No complete lifecycle savings, novelty, or publication-acceptance claim is made.",
            ]}


def analyze(root: str | Path = DEFAULT_ROOT, version: str = "v2") -> dict:
    if not re.fullmatch(r"v[1-9][0-9]*", version):
        raise ValueError("version must be a directory label such as v2 or v3")
    root = Path(root).expanduser().resolve()
    directory = root / version
    warnings: list[str] = []
    rows, metadata = read_ledger(root / "budget.sqlite3", warnings)
    spec = json_read(directory / "spec.json", warnings)
    spec_present = isinstance(spec, dict)
    spec = spec if spec_present else {}
    models, families, arms = spec.get("models", DEFAULT_MODELS), spec.get("families", DEFAULT_FAMILIES), spec.get("arms", DEFAULT_ARMS)
    try:
        metadata_locks = json.loads(metadata.get("locks_json", "null"))
    except ValueError:
        metadata_locks = None
        warnings.append("Ledger metadata locks_json is malformed")
    prices, lock_sources = {}, {}
    ledger_versions = {episode_identity(str(row.get("episode", "")))["version"] for row in rows}
    for price_version in sorted(ledger_versions | {"v1", version}):
        path = root / "price-locks.json" if price_version == "v1" else root / price_version / "price-locks.json"
        locks = json_read(path, warnings)
        lock_sources[price_version] = str(path) if locks is not None else "ledger.metadata.locks_json"
        prices[price_version] = extract_prices(locks if locks is not None else metadata_locks, lock_sources[price_version], warnings)
    totals = summarize_calls(rows, prices)
    budget_metadata = metadata.get("budget_nusd")
    if budget_metadata is not None and budget_metadata != str(AUTHORIZED_BUDGET_NUSD):
        warnings.append("Ledger budget metadata differs from the authorized USD 30 cap")
    totals.update({"authorized_budget_nusd": AUTHORIZED_BUDGET_NUSD, "authorized_budget_usd": "30.000000000",
                   "ledger_budget_nusd": budget_metadata,
                   "remaining_after_exposure_nusd": max(0, AUTHORIZED_BUDGET_NUSD - totals["total_exposure_nusd"]),
                   "remaining_after_exposure_usd": usd(max(0, AUTHORIZED_BUDGET_NUSD - totals["total_exposure_nusd"])),
                   "blocked": bool(totals["unresolved_requests"]),
                   "over_authorized_budget": totals["total_exposure_nusd"] > AUTHORIZED_BUDGET_NUSD})
    episode_rows = defaultdict(list)
    for row in rows:
        episode_rows[episode_identity(str(row.get("episode", "")))["episode_key"]].append(row)
    episode_costs = {}
    for episode_key, members in sorted(episode_rows.items()):
        identity = episode_identity(str(members[0]["episode"]))
        by_operation = defaultdict(list)
        for row in members:
            by_operation[episode_identity(str(row["episode"]))["operation"]].append(row)
        episode_costs[episode_key] = {**identity, "model": members[0].get("model_requested"),
            **summarize_calls(members, prices),
            "operations": {operation: summarize_calls(calls, prices) for operation, calls in sorted(by_operation.items())}}
    outcomes, admission_records, inputs = load_outcomes(directory, warnings, version)
    attach_zero_call_evidence(outcomes, episode_costs, prices, version, warnings)
    admissions = admission_summary(admission_records, outcomes, models, families)
    shared_screen = bool(spec.get("shared_deterministic_screen") or any(row.get("model") == "deterministic_shared" for row in outcomes.values()))
    screening = screening_summary(outcomes, admissions, models, families, arms, int(spec.get("screen_bindings_per_family", 3)),
                                   shared_deterministic=shared_screen)
    pairing_path = directory / "confirmation-pairing.json"
    pairing = json_read(pairing_path, warnings)
    inputs["confirmation-pairing.json"] = pairing is not None
    confirmation = confirmation_analysis(pairing, outcomes, episode_costs, warnings, version,
                                          require_data_match=bool(spec.get("initialization_noise_seeded")))
    anchors = anchor_summary(outcomes, episode_costs, version, models, families, spec)
    anchor_pairs_path = directory / "anchor-pairing.json"
    anchor_pairs = json_read(anchor_pairs_path, warnings)
    inputs["anchor-pairing.json"] = anchor_pairs is not None
    anchor_comparisons = anchor_pair_analysis(anchor_pairs, outcomes, episode_costs, version, models)
    shared_rows = [row for row in outcomes.values() if row.get("model") == "deterministic_shared" and row["episode"].startswith("screen/")]
    shared_costs = [episode_costs.get(version + "/" + row["episode"]) for row in shared_rows]
    generation_counts = Counter(row.get("generation_id") for row in rows if row.get("generation_id"))
    repeated = {generation: count for generation, count in generation_counts.items() if count > 1}
    if repeated:
        warnings.append("Repeated generation IDs exist across physical request records")
    decision = premise_decision(models, admissions, screening, confirmation, spec_present=spec_present,
                                audit_blocked=totals["blocked"] or totals["over_authorized_budget"], warnings=warnings)
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_root": str(root), "analysis_version": version,
        "inputs": {"ledger": str(root / "budget.sqlite3"), "ledger_read_mode": "ro",
                   "spec_present": spec_present, "price_sources": lock_sources, **inputs},
        "budget": totals,
        "cost_by_version": group_calls(rows, prices, ("version",)),
        "cost_by_model": group_calls(rows, prices, ("model",)),
        "cost_by_model_stage_operation": group_calls(rows, prices, ("version", "model", "stage", "operation")),
        "cost_by_lifecycle": group_calls(rows, prices, ("version", "lifecycle")),
        "prices_usd_per_token": {version: {model: {key: str(value) for key, value in rates.items()}
                                         for model, rates in model_rates.items()} for version, model_rates in prices.items()},
        "token_accounting": {"prompt_includes_cached": True, "completion_includes_reasoning": True,
            "input_equivalent_formula": "(prompt - cached) + cached * cache_read_price / prompt_price + completion * completion_price / prompt_price",
            "input_equivalent_basis": "Frozen quote before platform discount. Model-specific units are not summed across models.",
            "missing_fields": "Token sums include known values only; missing-field counters are retained and null reasoning is not presented as measured zero.",
            "reasoning_anomalies": "An explicitly flagged reasoning subcount remains null even if raw usage_json contains a number. Raw anomalous values and affected-request counts are reported separately; primary completion billing is unchanged."},
        "lifecycle_accounting": {"preflight": "All v1 preflight and all versioned requests remain inside the same USD 30 ledger.",
            "one_time_costs": "Compilation attempts and clean development, including failed branches, are separate from marginal serving.",
            "historical_demonstrations": "Reused from old reactive runs. Their original acquisition cost is outside this ledger and was not charged again.",
            "reactive_anchors": "Anchors are separate compact-reactive runs and costs, not residual recovery and not evidence inferred from zero-call program routing labels.",
            "zero_calls": "A complete terminal result explicitly declaring model_call_count=0 with no matching ledger rows is measured zero. Missing/ambiguous outcomes remain incomplete.",
            "reused_qualification": "Parent qualification rows remain attributed to their original version and are not copied into the current outcome or cost population.",
            "serving": "Each episode includes binding plus ordinary reactive actor calls or post-program fallback recovery. The runner names both actor kinds /recovery/, so the treatment disambiguates them. Deterministic screening has zero recovery calls by design; failures only create candidates."},
        "admission": {"expected_model_family_combinations": len(models) * len(families),
                      "status_counts": dict(Counter(row["status"] for row in admissions)), "rows": admissions},
        "screening": screening,
        "shared_deterministic_screen": {
            "enabled": shared_screen,
            "planned_episodes": len(families) * int(spec.get("screen_bindings_per_family", 3)) * len(arms) if shared_screen else 0,
            "recorded_episodes": len(shared_rows),
            "measured_zero_call_episodes": sum(bool(cost and cost.get("zero_model_calls_recorded")) for cost in shared_costs),
            "incomplete_or_unmeasured_cost_episodes": sum(cost is None for cost in shared_costs),
            "interpretation": "One deterministic run per family, binding and arm, shared across recovery-model configurations. The deterministic_shared label names no model inference.",
        },
        "confirmation": confirmation, "reactive_anchors": anchors, "anchor_pairing": anchor_comparisons,
        "episode_costs": episode_costs,
        "episode_outcomes": [outcomes[key] for key in sorted(outcomes)],
        "ledger_audit": {"physical_request_records": len(rows), "repeated_generation_ids": repeated,
                         "unresolved_requests": [{key: row.get(key) for key in ("request_id", "episode", "state", "generation_id", "reservation_nusd", "actual_cost_nusd", "error")}
                                                 for row in rows if not is_settled(row)]},
        "premise": decision, "warnings": warnings,
    }


def render_markdown(report: dict) -> str:
    budget = report["budget"]
    lines = [f"# Recovery validation ({report['analysis_version']}): current evidence", "", f"Generated: {report['generated_at']}", "",
             f"**Residual-cost premise: {report['premise']['status']}.** {report['premise']['explanation']}", "",
             f"Observed residual-cost status: {report['premise']['observed_residual_cost_status']}. Admission alone does not establish assertion coverage. A new recovery method and further cost reducibility have not been tested.", "",
             "## Spending", "",
             f"Settled actual charge: ${budget['settled_actual_usd']}. Unresolved exposure: ${budget['unresolved_exposure_usd']}. "
             f"Total exposure: ${budget['total_exposure_usd']} of $30. Remaining after reservations: ${budget['remaining_after_exposure_usd']}.", "",
             "| Version | Requests | Settled USD | Unresolved exposure USD |", "|---|---:|---:|---:|"]
    for row in report["cost_by_version"]:
        lines.append(f"| {row['version']} | {row['requests']} | {row['settled_actual_usd']} | {row['unresolved_exposure_usd']} |")
    lines += ["", "Compilation and development are one-time costs. Serving includes binding and either ordinary reactive actor inference or recovery after a program failure. Historical demonstration acquisition was reused and is absent from the new charge total.", "",
              "## Tokens and marginal costs", "",
              "The table uses settled calls. Cached input is contained in prompt tokens; reasoning is contained in completion tokens. Input-equivalent units use each model's frozen pricing before platform discount.", "",
              "| Version / model | Stage / operation | Calls | Prompt | Cached | Uncached | Output | Reasoning | Input-equivalent | USD |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report["cost_by_model_stage_operation"]:
        tokens = row["settled_token_sums"]
        def display(field):
            count = row["settled_missing_token_fields"][field]
            return f"{tokens[field]} + {count} missing" if count else str(tokens[field])
        lines.append(f"| {row['version']} / {row['model']} | {row['stage']} / {row['operation']} | {row['settled_requests']} | "
                     f"{display('prompt_tokens')} | {display('cached_prompt_tokens')} | {display('uncached_prompt_tokens')} | "
                     f"{display('completion_tokens')} | {display('reasoning_tokens')} | {row['settled_input_equivalent_tokens'] or 'unavailable'} | {row['settled_actual_usd']} |")
    if budget["reasoning_anomaly_requests"]:
        lines += ["", f"{budget['reasoning_anomaly_requests']} requests contain unusable reasoning subcounts. They remain missing in token totals; raw values are retained in JSON. Billed prompt, cache and completion counts are unchanged."]
    lines += ["", "## Admission", "", "| Recovery configuration | Family | Status | Qualification source | Final qualification correct / valid | New development episodes |",
              "|---|---|---|---|---:|---:|"]
    for row in report["admission"]["rows"]:
        origin = "reused parent" if row.get("qualification_reused_from") else "current version"
        lines.append(f"| {row['model']} | {row['family']} | {row['status']} | {origin} | {row['final_attempt_program_and_oracle_success']} / {row['final_attempt_valid_development']} | {row['new_development_episodes']} |")
    anchors = report["reactive_anchors"]
    if anchors["expected_if_all_admitted"] is not None or anchors["recorded_episodes"]:
        lines += ["", "## Compact reactive anchors", "",
                  f"Recorded anchors: {anchors['recorded_episodes']}; planned if all baselines are admitted: {anchors['expected_if_all_admitted']}. Settled anchor charge: ${anchors['settled_actual_usd']}.", "",
                  "These are separate reactive reference runs. Model names on zero-call program outcomes identify routing configurations and do not show that the model performed inference.", "",
                  "| Model / family | Arm | Actual model calls | Oracle success | Triggered | USD | Exclusions |",
                  "|---|---|---:|---|---|---:|---|"]
        for row in anchors["rows"]:
            lines.append(f"| {row['model']} / {row['family']} | {row['arm']} | {row['actual_model_calls']} | {row['oracle_success']} | {row['perturbation_triggered']} | {row['settled_actual_usd']} | {', '.join(row['exclusion_reasons']) or 'none'} |")
    anchor_pairs = report["anchor_pairing"]
    if anchor_pairs["pairs"]:
        lines += ["", "### Anchors matched to the shared program", "",
                  f"Included pairs: {anchor_pairs['included_pairs']}; excluded: {anchor_pairs['excluded_pairs']}. The included pairs reuse {anchor_pairs['unique_shared_program_episodes']} unique deterministic episodes.", "",
                  "Pairing requires matching family, seed, arm, program hash, initial public-state hash and realized exposure. Shared deterministic rows are counted once; pairing them with two models does not create new deterministic runs.", "",
                  "| Reactive model / family | Arm | Included | Program / reactive oracle | Program / reactive USD |",
                  "|---|---|---|---|---|"]
        for row in anchor_pairs["pairs"]:
            included = "yes" if row["included"] else ", ".join(row["exclusion_reasons"])
            outcomes = f"{row['program_oracle_success']} / {row['reactive_oracle_success']}" if row["included"] else "not compared"
            costs = f"{row['program_actual_usd']} / {row['reactive_actual_usd']}" if row["included"] else "not compared"
            lines.append(f"| {row['model']} / {row['family']} | {row['arm']} | {included} | {outcomes} | {costs} |")
    lines += ["", "## Screening", "", "Counts are grouped by model, task family, and perturbation. Clean rows count as realized exposure. Non-clean rows count only when the perturbation triggered.", "",
              "False accept means verified=True/oracle=False; false reject means verified=False/oracle=True. Terminal errors and unavailable checks are excluded. Each excluded row and its reasons remain in analysis.json.", "",
              "| Model / family | Arm | Recorded / expected | Triggered / realized valid | Program + oracle correct | False accept / reject | Error / unavailable / missing |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for row in report["screening"]:
        lines.append(f"| {row['model']} / {row['family']} | {row['arm']} | {row['recorded_episodes']} / {row['expected_episodes']} | "
                     f"{row['triggered']} / {row['realized_exposure_valid']} | {row['program_and_oracle_success']} | {row['program_false_accept']} / {row['program_false_reject']} | {row['missing_or_invalid_outcome']} |")
    zero_screen = sum(row["zero_model_call_outcomes"] for row in report["screening"])
    if zero_screen:
        lines += ["", f"{zero_screen} valid screening outcomes explicitly record zero model calls. Their marginal model-token charge is measured zero; historical acquisition and compilation remain separate costs."]
    if report["shared_deterministic_screen"]["enabled"]:
        shared = report["shared_deterministic_screen"]
        lines += ["", f"Shared deterministic screen: {shared['recorded_episodes']} recorded of {shared['planned_episodes']} planned episodes. These cases are shared across the recovery-model configurations and are not counted twice."]
    confirmation = report["confirmation"]
    lines += ["", "## Matched confirmation", "",
              f"Included pairs: {confirmation['included_pairs']}. Excluded pairs: {confirmation['excluded_pairs']}. Both policies succeeded in {confirmation['both_success_pairs']} included pairs.", "",
              "| Model / family / seed | Arm | Included | Fallback / reactive success | Fallback / reactive USD | Recovery USD |",
              "|---|---|---|---|---|---:|"]
    for row in confirmation["pairs"]:
        if row["included"]:
            outcomes = f"{row['fallback_oracle_success']} / {row['reactive_oracle_success']}"
            costs = f"{row['fallback_actual_usd']} / {row['reactive_actual_usd']}"
            included, recovery = "yes", row["fallback_recovery_actual_usd"]
        else:
            outcomes, costs, recovery = "not compared", "not compared", "not compared"
            included = ", ".join(row["exclusion_reasons"])
        lines.append(f"| {row['model']} / {row['family']} / {row['seed']} | {row['arm']} | {included} | {outcomes} | {costs} | {recovery} |")
    if confirmation["confirmation_episodes_without_pair_manifest"]:
        lines += ["", f"{len(confirmation['confirmation_episodes_without_pair_manifest'])} confirmation episodes have no completed pairing-manifest entry and are excluded from paired comparisons."]
    lines += ["", "No significance claims are made from these few cases clustered within task families. Cost differences are interpreted jointly with success; complete lifecycle savings are not estimated.", "",
              "## Coverage and limits", ""]
    lines += [f"- {limitation}" for limitation in report["premise"]["limitations"]]
    if report["warnings"]:
        lines += ["", "## Data issues", ""] + [f"- {warning}" for warning in report["warnings"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="Experiment base containing budget.sqlite3 and version directories")
    parser.add_argument("--version", default="v2", help="Outcome/output directory label, default v2")
    args = parser.parse_args(argv)
    report = analyze(args.root, args.version)
    output = Path(report["experiment_root"]) / report["analysis_version"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "analysis.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "analysis.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"analysis_json": str(output / "analysis.json"), "analysis_markdown": str(output / "analysis.md"),
                      "premise": report["premise"]["status"], "settled_actual_usd": report["budget"]["settled_actual_usd"],
                      "unresolved_exposure_usd": report["budget"]["unresolved_exposure_usd"]}))


if __name__ == "__main__":
    main()
