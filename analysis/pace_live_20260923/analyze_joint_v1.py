"""Analyze the frozen joint component experiment without invoking its runner.

Only local artifacts are read. SQLite is opened with uri mode=ro, never through
BudgetLedger. The analyzer defaults to run v3 and reports interrupted v2
separately. Reproduced outputs use a separate directory, preserving the frozen
summary files used by the paper tables.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
from itertools import product
import json
from pathlib import Path
import sqlite3
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = ROOT / "experimental-results/pace_live_20260923/v3"
NANO = Decimal(1_000_000_000)
TOLERANCE = 1_000
POLICIES = ("agent", "program_fallback")
ARMS = ("clean", "renamed_submit", "blocking_dialog")
FIELDS = ("title", "date", "description", "location", "url", "invitees")


def read_json(path: Path, issues: list[str], required: bool = False) -> Any:
    if not path.exists():
        if required:
            issues.append(f"Missing file: {path}")
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        issues.append(f"Unreadable JSON: {path}: {type(exc).__name__}")
        return None


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nanos(value: Any) -> int:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid nonnegative amount")
    return int((amount * NANO).to_integral_value(rounding=ROUND_CEILING))


def usd(value: int | float | None) -> str:
    return "NA" if value is None else f"{Decimal(str(value)) / NANO:.9f}"


def timestamp(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return None


def occupied(row: dict) -> int:
    actual = row.get("actual_cost_nusd")
    if row.get("state") == "settled" and actual is not None:
        return int(actual)
    return max(int(row.get("reservation_nusd") or 0), int(actual or 0))


def billing_totals(rows: list[dict], available: bool = True) -> dict:
    if not available:
        return {"available": False, "requests": None, "known_actual_nusd": None,
                "settled_actual_nusd": None, "occupied_nusd": None,
                "unsettled_requests": None, "exact_actual_nusd": None,
                "bill_lower_nusd": None, "bill_upper_nusd": None}
    known = sum(int(r["actual_cost_nusd"]) for r in rows if r.get("actual_cost_nusd") is not None)
    settled = sum(int(r["actual_cost_nusd"] or 0) for r in rows if r.get("state") == "settled")
    unresolved = [r for r in rows if r.get("state") != "settled" or r.get("actual_cost_nusd") is None]
    return {"available": True, "requests": len(rows), "states": dict(Counter(r["state"] for r in rows)),
            "known_actual_nusd": known, "settled_actual_nusd": settled,
            "occupied_nusd": sum(occupied(r) for r in rows),
            "unsettled_requests": len(unresolved),
            "unsettled_reserved_nusd": sum(int(r["reservation_nusd"]) for r in unresolved),
            "unknown_charge_requests": sum(r.get("actual_cost_nusd") is None for r in rows),
            "bill_lower_nusd": known, "bill_upper_nusd": sum(occupied(r) for r in rows),
            "exact_actual_nusd": known if not unresolved else None}


def request_phase(row: dict) -> str:
    if row["episode"].startswith("pace/acquisition/"):
        return "acquisition"
    try:
        request = json.loads(row.get("request_json") or "{}")
        first = request.get("messages", [{}])[0].get("content", "")
        if isinstance(first, str) and first.startswith("Extract calendar event fields"):
            return "binding"
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    return "agent_action"


def read_ledger(path: Path, issues: list[str]) -> tuple[list[dict], dict, bool]:
    if not path.exists():
        issues.append(f"Ledger not yet present: {path}")
        return [], {}, False
    try:
        # A normal read-only connection sees committed WAL data. immutable=1
        # would be inappropriate while the runner can still append records.
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            rows = [dict(r) for r in con.execute("SELECT * FROM requests ORDER BY created_at, request_id")]
            metadata = dict(con.execute("SELECT key, value FROM metadata"))
        finally:
            con.close()
        return rows, metadata, True
    except sqlite3.Error as exc:
        issues.append(f"Read-only ledger snapshot unavailable: {type(exc).__name__}: {exc}")
        return [], {}, False


def anticipated_tasks() -> tuple[list[dict], list[dict]]:
    tasks = [{"pair": f"s{seed:02d}_{arm}", "seed": seed, "arm": arm,
              "order": list(POLICIES if seed % 2 == 0 else reversed(POLICIES))}
             for seed in range(4) for arm in ARMS]
    return tasks, [dict(tasks[i], pair=f"tight_{i}") for i in (1, 5, 7, 11)]


def state_rows(value: Any) -> list[dict] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("events", "data", "calendar"):
            if isinstance(value.get(key), list):
                return value[key]
    return None


def terminal_check(path: Path, binding: dict, issues: list[str]) -> dict | None:
    before = state_rows(read_json(path / "initial-state.json", issues))
    after = state_rows(read_json(path / "final-state.json", issues))
    if before is None or after is None or not binding:
        return None
    try:
        matches = lambda row: all(str(row.get(k) or "") == v for k, v in binding.items())
        old = {r["id"]: r for r in before}
        final_old = {r["id"]: r for r in after if r["id"] in old}
        matched_delta = sum(map(matches, after)) - sum(map(matches, before))
        count_delta = len(after) - len(before)
        return {"matching_event_delta": matched_delta, "event_count_delta": count_delta,
                "unrelated_state_preserved": old == final_old,
                "success": matched_delta == 1 and count_delta == 1 and old == final_old}
    except (KeyError, TypeError, AttributeError):
        issues.append(f"Cannot independently evaluate terminal state: {path}")
        return None


def episode_record(raw: Path, task: dict, policy: str, index: int, tight: bool,
                   rows: list[dict], ledger_available: bool, issues: list[str], snapshot_at: float) -> dict:
    stream = "tight" if tight else ("pace" if policy == "program_fallback" else "agent")
    label = f"{stream}/{task['pair']}/{policy}"
    path = raw / "episodes" / label
    result = read_json(path / "result.json", issues)
    started = read_json(path / "started.json", issues)
    present = isinstance(result, dict)
    result = result if present else {}
    episode_rows = [r for r in rows if r["episode"] == label]
    bill = billing_totals(episode_rows, ledger_available)
    trajectory = read_json(path / "trajectory.json", issues) or []
    if not isinstance(trajectory, list):
        issues.append(f"Trajectory is not a list: {label}")
        trajectory = []
    generation_ids = [e["generation_id"] for e in trajectory if isinstance(e, dict) and e.get("generation_id")]
    admissions = [e["budget_admission"] for e in trajectory if isinstance(e, dict) and "budget_admission" in e]
    success = result.get("success") if isinstance(result.get("success"), bool) else None
    binding = task.get("binding") or result.get("binding") or {}
    terminal = terminal_check(path, binding, issues) if present else None
    if terminal is not None and "matching_event_delta" in result and terminal["success"] != success:
        issues.append(f"Saved success disagrees with terminal-state recomputation: {label}")
    if present and result.get("episode") != label:
        issues.append(f"Result episode label mismatch: {label}")
    values = result.get("fallback_form_values")
    field_checks = None
    if isinstance(values, list):
        field_checks = {k: any(v.get("name") == k and str(v.get("value") or "") == str(binding.get(k))
                               for v in values if isinstance(v, dict)) for k in FIELDS}
    aligned = present and isinstance(result.get("end_time"), (int, float)) and result["end_time"] <= snapshot_at
    complete_bill = aligned and bill["exact_actual_nusd"] is not None
    result_cost_match = (result.get("cost_nusd") == bill["occupied_nusd"]
                         if aligned and ledger_available and "cost_nusd" in result else None)
    if result_cost_match is False:
        issues.append(f"Result cost differs from current occupied ledger amount: {label}")
    binder_response = read_json(path / "binder-response.json", issues)
    binder_id = binder_response.get("id") if isinstance(binder_response, dict) else None
    extracted = result.get("extracted_binding")
    binding_checks = ({key: extracted.get(key) == binding.get(key) for key in FIELDS}
                      if isinstance(extracted, dict) else None)
    return {"episode": label, "pair": task["pair"], "seed": task["seed"], "arm": task["arm"],
            "policy": policy, "prefix_index": index, "tight": tight,
            "status": "recorded" if present else ("started_without_result" if started else "not_started"),
            "result_present": present, "success": success, "termination": result.get("termination"),
            "result_precedes_ledger_snapshot": aligned,
            "error": result.get("error"), "program_error": result.get("program_error"),
            "binding_error": result.get("binding_error"), "binding_attempted": result.get("binding_attempted"),
            "program_attempted": result.get("program_attempted"), "fallback_reason": result.get("fallback_reason"),
            "extracted_binding_matches": binding_checks, "binding_generation_id": binder_id,
            "binding_admission": read_json(path / "binder-admission.json", issues),
            "binding_billing": billing_totals([r for r in episode_rows if request_phase(r) == "binding"], ledger_available),
            "agent_action_billing": billing_totals([r for r in episode_rows if request_phase(r) == "agent_action"], ledger_available),
            "fallback": result.get("fallback"), "perturbation_exposed": result.get("perturbation_exposed"),
            "dialog_dismissed": result.get("dialog_dismissed"),
            "retained_form_fields": field_checks,
            "retained_correct_fields": sum(field_checks.values()) if field_checks is not None else None,
            "all_six_form_fields_correct": all(field_checks.values()) if field_checks is not None else None,
            "initial_state_sha256": result.get("initial_state_sha256"),
            "initial_ax_sha256": result.get("initial_ax_sha256"),
            "end_time": result.get("end_time"), "terminal_state_recheck": terminal,
            "calls_reported": result.get("calls"), "trajectory_generation_ids": generation_ids,
            "trajectory_action_errors": sum(bool(e.get("error")) for e in trajectory if isinstance(e, dict)),
            "budget_admissions": admissions, "billing": bill,
            "exact_complete_actual_nusd": bill["exact_actual_nusd"] if complete_bill else None,
            "complete_bill_bounds_nusd": [bill["bill_lower_nusd"], bill["bill_upper_nusd"]] if aligned and ledger_available else None,
            "goal_holds_at_abort": result.get("goal_holds_at_abort"),
            "result_cost_nusd": result.get("cost_nusd"), "result_cost_matches_ledger": result_cost_match,
            "reference_prefix_nusd": result.get("reference_prefix_nusd"),
            "prefix_ceiling_nusd": result.get("prefix_ceiling_nusd"),
            "recorded_prefix_cost_nusd": result.get("prefix_cost_nusd"),
            "recorded_prefix_bound_holds": result.get("prefix_bound_holds")}


def success_summary(episodes: list[dict]) -> dict:
    total = len(episodes)
    success = sum(e["success"] is True for e in episodes)
    failure = sum(e["success"] is False for e in episodes)
    missing = total - success - failure
    return {"planned": total, "successes": success, "recorded_unsuccessful": failure,
            "unknown_outcome": missing, "observed_fraction": f"{success}/{success + failure}",
            "termination_counts": dict(Counter(e["termination"] or e["status"] for e in episodes)),
            "provider_or_billing_aborts": sum(e["termination"] == "billing_stop" for e in episodes),
            "infrastructure_aborts": sum(e["termination"] == "infrastructure_error" for e in episodes),
            "prefix_denials": sum(e["termination"] == "prefix_denied" for e in episodes),
            "planned_success_fraction": f"{success}/{total}" if not missing else None,
            "planned_success_bounds": [f"{success}/{total}", f"{success + missing}/{total}"]}


def quantile(values: list[float], probability: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * probability
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def compare_pairs(pairs: list[dict], allow_interval: bool, reason: str) -> dict:
    eligible = [p for p in pairs if all(p[q]["exact_complete_actual_nusd"] is not None for q in POLICIES)]
    result = {"planned_or_selected_pairs": len(pairs), "exact_cost_pairs": len(eligible),
              "included_pair_ids": [p["pair"] for p in pairs],
              "missing_cost_pair_ids": [p["pair"] for p in pairs if p not in eligible],
              "success": {q: success_summary([p[q] for p in pairs]) for q in POLICIES},
              "observed_complete_pair_mean_nusd": {}, "full_selected_mean_nusd": None,
              "full_selected_mean_bill_bounds_nusd": None,
              "cost_bound_bootstrap_95_envelope_nusd": None,
              "paired_seed_cluster_bootstrap_95ci_nusd": None,
              "interval_unavailable_reason": reason}
    bounded = [p for p in pairs if all(p[q]["complete_bill_bounds_nusd"] is not None for q in POLICIES)]
    if bounded and len(bounded) == len(pairs):
        bounds = [(p["agent"]["complete_bill_bounds_nusd"],
                   p["program_fallback"]["complete_bill_bounds_nusd"]) for p in pairs]
        result["full_selected_mean_bill_bounds_nusd"] = {
            "agent": [mean(a[0] for a, _ in bounds), mean(a[1] for a, _ in bounds)],
            "program_fallback": [mean(b[0] for _, b in bounds), mean(b[1] for _, b in bounds)],
            "program_minus_agent": [mean(b[0] - a[1] for a, b in bounds), mean(b[1] - a[0] for a, b in bounds)]}
        clusters = defaultdict(list)
        for pair, bound in zip(pairs, bounds):
            clusters[pair["seed"]].append(bound)
        result["bill_bound_seed_clusters"] = sorted(clusters)
        if allow_interval and len(clusters) >= 2 and len(eligible) != len(pairs):
            lower = {key: [] for key in result["full_selected_mean_bill_bounds_nusd"]}
            upper = {key: [] for key in lower}
            for sampled_seeds in product(clusters, repeat=len(clusters)):
                sample = [bound for seed in sampled_seeds for bound in clusters[seed]]
                lower["agent"].append(mean(a[0] for a, _ in sample))
                upper["agent"].append(mean(a[1] for a, _ in sample))
                lower["program_fallback"].append(mean(b[0] for _, b in sample))
                upper["program_fallback"].append(mean(b[1] for _, b in sample))
                lower["program_minus_agent"].append(mean(b[0] - a[1] for a, b in sample))
                upper["program_minus_agent"].append(mean(b[1] - a[0] for a, b in sample))
            result["cost_bound_bootstrap_95_envelope_nusd"] = {
                key: [quantile(lower[key], 0.025), quantile(upper[key], 0.975)] for key in lower}
            result["bill_bound_bootstrap_ordered_resamples"] = len(next(iter(lower.values())))
            result["bill_bound_interpretation"] = "Reservation-based identification bounds, conditional on frozen price ceilings. Bootstrap envelopes propagate these bounds and are not exact-bill confidence intervals."
    if not eligible:
        return result
    observations = [(p["agent"]["exact_complete_actual_nusd"],
                     p["program_fallback"]["exact_complete_actual_nusd"]) for p in eligible]
    means = {"agent": mean(a for a, _ in observations),
             "program_fallback": mean(b for _, b in observations),
             "program_minus_agent": mean(b - a for a, b in observations)}
    result["observed_complete_pair_mean_nusd"] = means
    if len(eligible) == len(pairs):
        result["full_selected_mean_nusd"] = means
    clusters = defaultdict(list)
    for pair, costs in zip(eligible, observations):
        clusters[pair["seed"]].append(costs)
    result["seed_clusters"] = sorted(clusters)
    if not allow_interval or len(eligible) != len(pairs) or len(clusters) < 2:
        return result
    draws = {"agent": [], "program_fallback": [], "program_minus_agent": []}
    # Four seeds imply 4**4 = 256 ordered bootstrap resamples. Exhaustive
    # enumeration avoids Monte Carlo noise and retains paired policies and arms.
    for sampled_seeds in product(clusters, repeat=len(clusters)):
        sampled = [costs for seed in sampled_seeds for costs in clusters[seed]]
        draws["agent"].append(mean(a for a, _ in sampled))
        draws["program_fallback"].append(mean(b for _, b in sampled))
        draws["program_minus_agent"].append(mean(b - a for a, b in sampled))
    result["paired_seed_cluster_bootstrap_95ci_nusd"] = {
        key: [quantile(values, 0.025), quantile(values, 0.975)] for key, values in draws.items()}
    result["bootstrap_ordered_resamples"] = len(next(iter(draws.values())))
    result["interval_unavailable_reason"] = None
    return result


def audit_request(row: dict, spec: dict) -> dict:
    checked, failed, unavailable = [], [], []

    def check(name: str, value: bool | None) -> None:
        (unavailable if value is None else (checked if value else failed)).append(name)

    def parse(field: str) -> Any:
        try:
            return json.loads(row[field]) if row.get(field) else None
        except (ValueError, TypeError):
            failed.append(f"valid_{field}")
            return None

    request, response = parse("request_json"), parse("response_json")
    lock = spec.get("locks", {}).get(row.get("model_requested"))
    check("generation_id_present", bool(row.get("generation_id")) if response and not response.get("error") else None)
    check("actual_within_reserved_bound", row["actual_cost_nusd"] <= row["reservation_nusd"]
          if row.get("actual_cost_nusd") is not None else None)
    computed = {}
    if lock and isinstance(request, dict):
        try:
            endpoints = lock["endpoints"]
            prices = {k: Decimal(str(endpoints[0]["pricing"][k]))
                      for k in ("prompt", "completion", "input_cache_read")}
            check("endpoint_prices_agree", all(all(Decimal(str(e["pricing"][k])) == v for k, v in prices.items()) for e in endpoints))
            check("request_model_is_locked", request.get("model") == row["model_requested"])
            provider = request.get("provider", {})
            check("provider_pool_is_locked", set(provider.get("only", [])) == {e["tag"] for e in endpoints})
            check("provider_requires_parameters", provider.get("require_parameters") is True)
            caps = provider.get("max_price", {})
            check("request_price_ceilings_match", all(Decimal(str(caps[k])) == prices[k] * 1_000_000 for k in ("prompt", "completion")) and Decimal(str(caps["image"])) == 0)
            tokens = request["max_tokens"]
            expected_reserve = nanos(lock["context_length"] * prices["prompt"] + tokens * prices["completion"])
            computed["reservation_nusd"] = expected_reserve
            check("reservation_matches_frozen_quote", row["reservation_nusd"] == expected_reserve)
            phase = request_phase(row)
            expected_tokens = (spec.get("compile_pilot", {}).get("max_tokens") if phase == "acquisition"
                               else spec.get("binding_extraction", {}).get("max_tokens") if phase == "binding"
                               else spec.get("max_tokens"))
            check("request_token_cap_matches_manifest", tokens == expected_tokens)
            if isinstance(response, dict) and response.get("error"):
                unavailable.append("provider_error_has_no_verified_usage")
                error = response.get("error")
                computed["provider_error_code"] = error.get("code") if isinstance(error, dict) else None
                computed["transport_http_status"] = response.get("_transport", {}).get("http_status")
            elif isinstance(response, dict):
                check("response_generation_matches_ledger", response.get("id") == row.get("generation_id"))
                check("response_model_is_locked", response.get("model") == row["model_requested"])
                check("response_provider_is_locked", response.get("provider") in {e["provider_name"] for e in endpoints})
                usage = response["usage"]
                prompt, completion, total = (usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
                cached = usage["prompt_tokens_details"]["cached_tokens"]
                valid_counts = all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (prompt, completion, total, cached))
                check("usage_integer_counts", valid_counts and prompt > 0 and total > 0)
                check("usage_token_bounds", 0 <= cached <= prompt and total == prompt + completion and completion <= tokens and total <= lock["context_length"])
                check("no_byok_billing", usage.get("is_byok") is not True)
                expected_prompt = nanos((prompt - cached) * prices["prompt"] + cached * prices["input_cache_read"])
                expected_completion = nanos(completion * prices["completion"])
                expected = expected_prompt + expected_completion
                actual = nanos(usage["cost"])
                computed.update(actual_nusd=actual, expected_nusd=expected)
                check("receipt_actual_matches_ledger", actual == row.get("actual_cost_nusd"))
                check("receipt_actual_within_reservation", actual <= row["reservation_nusd"])
                details = usage.get("cost_details") or {}
                upstream_raw = details.get("upstream_inference_cost")
                upstream = nanos(upstream_raw) if upstream_raw is not None else None
                if upstream is None:
                    check("receipt_matches_locked_token_price", abs(actual - expected) <= TOLERANCE)
                else:
                    check("upstream_matches_locked_token_price", abs(upstream - expected) <= TOLERANCE)
                    check("actual_not_above_verified_upstream", actual <= upstream + TOLERANCE)
                for key, expected_part in (("upstream_inference_prompt_cost", expected_prompt),
                                           ("upstream_inference_completions_cost", expected_completion)):
                    if details.get(key) is not None:
                        check(key + "_matches", abs(nanos(details[key]) - expected_part) <= TOLERANCE)
                if row.get("expected_cost_nusd") is not None:
                    check("computed_expected_matches_ledger", expected == row["expected_cost_nusd"])
                check("serialized_response_hash_matches", digest(canonical(response).encode()) == row.get("response_hash"))
                computed["finish_reason"] = response.get("choices", [{}])[0].get("finish_reason")
            else:
                unavailable.append("receipt_price_and_identity_checks")
        except (KeyError, TypeError, ValueError, ArithmeticError, IndexError, AttributeError) as exc:
            failed.append(f"required_price_or_receipt_field:{type(exc).__name__}")
    else:
        unavailable.append("frozen_quote_or_request_missing")
    return {"request_id": row["request_id"], "episode": row["episode"], "state": row["state"],
            "generation_id": row.get("generation_id"), "phase": request_phase(row), "reservation_nusd": row["reservation_nusd"],
            "actual_cost_nusd": row.get("actual_cost_nusd"), "expected_cost_nusd": row.get("expected_cost_nusd"),
            "passed_checks": checked, "failed_checks": failed, "unavailable_checks": unavailable,
            "computed": computed, "status": "fail" if failed else ("incomplete" if unavailable else "pass"),
            "error": row.get("error")}


def guard_audit(rows: list[dict], episodes: list[dict], spec: dict, acquisition_result: dict) -> dict:
    by_episode = {e["episode"]: e for e in episodes}
    main_increment = spec.get("reference_increment_nusd", 80_000_000)
    tight_increment = spec.get("tight_increment_nusd", 20_000_000)
    ratio = Decimal(1) + Decimal(str(spec.get("epsilon", "0.25")))
    absolute_cap = nanos(spec.get("total_usd_cap", "5"))
    checks, previous = [], []
    for row in rows:
        now = timestamp(row.get("created_at"))

        def past_exposure(prefix: str = "") -> int:
            amount = 0
            for old in previous:
                if not old["episode"].startswith(prefix):
                    continue
                finalized = timestamp(old.get("finalized_at"))
                if now is not None and finalized is not None and finalized <= now:
                    amount += occupied(old)
                else:
                    amount += max(int(old["reservation_nusd"]), int(old.get("actual_cost_nusd") or 0))
            return amount

        label = row["episode"]
        admission = {"request_id": row["request_id"], "episode": label,
                     "reservation_nusd": row["reservation_nusd"],
                     "global_exposure_before_nusd": past_exposure(), "global_cap_nusd": absolute_cap}
        admission["global_admission_holds"] = past_exposure() + row["reservation_nusd"] <= absolute_cap
        consecutive = 0
        for old in reversed(previous):
            if old["state"] == "settled":
                break
            consecutive += 1
        admission["consecutive_unsettled_before"] = consecutive
        if spec.get("binding_extraction"):
            admission["three_consecutive_error_breaker_holds"] = consecutive < 3
        episode = by_episode.get(label)
        acquisition = label.startswith("pace/acquisition/")
        if episode is not None or acquisition:
            cap = spec.get("compiler_total_cost_cap_nusd", 60_000_000) if acquisition else spec.get("service_total_cost_cap_nusd", 80_000_000)
            admission["service_exposure_before_nusd"] = past_exposure(label)
            admission["service_cap_nusd"] = cap
            admission["service_admission_holds"] = past_exposure(label) + row["reservation_nusd"] <= cap
            if label.startswith(("pace/", "tight/")):
                tight = label.startswith("tight/")
                prefix = "tight/" if tight else "pace/"
                index = acquisition_result.get("prefix_index", spec.get("compile_pilot", {}).get("after_completed_agent_traces", 3)) if acquisition else episode["prefix_index"]
                reference = index * (tight_increment if tight else main_increment)
                ceiling = int(Decimal(reference) * ratio)
                reserve = cap if acquisition else row["reservation_nusd"]
                admission.update(stream_exposure_before_nusd=past_exposure(prefix),
                                 declared_reference_prefix_nusd=reference, prefix_ceiling_nusd=ceiling,
                                 admission_envelope_nusd=reserve,
                                 prefix_admission_holds=past_exposure(prefix) + reserve <= ceiling)
        checks.append(admission)
        previous.append(row)
    failures = [c for c in checks if any(v is False for k, v in c.items() if k.endswith("_holds"))]
    final_violations = [e["episode"] for e in episodes if e["recorded_prefix_bound_holds"] is False]
    recorded_admissions = []
    for episode in episodes:
        events = list(episode["budget_admissions"])
        if episode.get("binding_admission"):
            events.append(episode["binding_admission"])
        for event in events:
            used = event.get("exposure_nusd", event.get("prefix_used_nusd"))
            reserve, ceiling = event.get("reservation_nusd"), event.get("ceiling_nusd")
            present = all(isinstance(value, int) for value in (used, reserve, ceiling))
            recorded_admissions.append({"episode": episode["episode"], "recorded": event,
                                        "decision_matches_arithmetic": event.get("admitted") == (used + reserve <= ceiling) if present else None})
    return {"request_checks": checks, "failed_request_checks": failures,
            "recorded_admission_checks": recorded_admissions,
            "recorded_admission_mismatches": [r for r in recorded_admissions if r["decision_matches_arithmetic"] is not True],
            "recorded_terminal_prefix_violations": final_violations,
            "all_observed_request_guards_hold": not failures if rows else None,
            "declared_reference_is_measured_agent_bill": False,
            "epsilon": str(ratio - 1), "absolute_cap_nusd": absolute_cap}


def analyze(raw: Path) -> dict:
    issues: list[str] = []
    manifest = read_json(raw / "manifest.json", issues, required=True)
    frozen = isinstance(manifest, dict)
    spec = manifest if frozen else {}
    tasks, tight_tasks = anticipated_tasks()
    if frozen:
        tasks, tight_tasks = spec.get("tasks", tasks), spec.get("tight_stream", tight_tasks)
    snapshot_at = datetime.now(timezone.utc).timestamp()
    rows, metadata, ledger_available = read_ledger(raw / "budget.sqlite3", issues)
    episodes = [episode_record(raw, task, policy, index, False, rows, ledger_available, issues, snapshot_at)
                for index, task in enumerate(tasks, 1) for policy in POLICIES]
    tight = [episode_record(raw, task, "program_fallback", index, True, rows, ledger_available, issues, snapshot_at)
             for index, task in enumerate(tight_tasks, 1)]
    pairs = []
    for i, task in enumerate(tasks):
        a, b = episodes[2 * i:2 * i + 2]
        hashes_present = all(e[key] for e in (a, b) for key in ("initial_state_sha256", "initial_ax_sha256"))
        matched = all(a[k] == b[k] for k in ("initial_state_sha256", "initial_ax_sha256")) if hashes_present else None
        pairs.append({"pair": task["pair"], "seed": task["seed"], "arm": task["arm"],
                      "initial_states_match": matched, "agent": a, "program_fallback": b,
                      "both_perturbation_exposed": a["perturbation_exposed"] is True and b["perturbation_exposed"] is True})
    all_outcomes = all(e["success"] is not None for e in episodes)
    all_matching = all(p["initial_states_match"] is True for p in pairs)
    interval_ok = frozen and all_outcomes and all_matching
    reason = ("Exact bills are unavailable for some completed episodes."
              if all_outcomes and all_matching else
              "Full prespecified design is incomplete or initial-state pairing is unverified.")
    comparisons = {"all_pairs": compare_pairs(pairs, interval_ok, reason)}
    for arm in ARMS:
        selected = [p for p in pairs if p["arm"] == arm]
        valid = frozen and all(all(p[q]["success"] is not None for q in POLICIES) and p["initial_states_match"] is True for p in selected)
        comparisons[arm] = compare_pairs(selected, valid, reason)
    exposed = [p for p in pairs if p["arm"] != "clean" and p["both_perturbation_exposed"]]
    comparisons["both_exposed_perturbed_cases"] = compare_pairs(exposed, interval_ok, reason)
    comparisons["both_exposed_perturbed_cases"]["selection_definition"] = "Non-clean pair with perturbation_exposed=true for both policies. Descriptive, post-exposure subset."

    acq_raw = read_json(raw / "acquisition/result.json", issues)
    acq = acq_raw if isinstance(acq_raw, dict) else {}
    acq_rows = [r for r in rows if r["episode"].startswith("pace/acquisition/")]
    acq_response = read_json(raw / "acquisition/response.json", issues)
    traces = read_json(raw / "acquisition/input-traces.json", issues) or []
    training = [{"episode": t.get("outcome", {}).get("episode"),
                 "success": t.get("outcome", {}).get("success"),
                 "termination": t.get("outcome", {}).get("termination")} for t in traces if isinstance(t, dict)]
    acq_complete = bool(acq) and ("cost_nusd" in acq or acq.get("admission", {}).get("admitted") is False)
    validation = acq.get("validation", [])
    expected_seeds = spec.get("compile_pilot", {}).get("validation_seeds", [99, 100])
    training_candidates = [e for e in episodes if e["policy"] == "agent" and e["result_present"]]
    if spec.get("binding_extraction"):
        training_candidates = [e for e in training_candidates if e["termination"] not in ("billing_stop", "infrastructure_error")]
    expected_training = [e["episode"] for e in training_candidates[:3]]
    acquisition = {"status": "complete" if acq_complete else ("in_progress" if acq or acq_rows else "not_started"),
                   "result": acq_raw, "billing": billing_totals(acq_rows, ledger_available),
                   "training_outcomes": training,
                   "expected_training_episodes": expected_training,
                   "first_three_completed_agent_traces_preserved": [t["episode"] for t in training] == expected_training if training else None,
                   "validation_seeds": expected_seeds, "validation_successes": sum(v.get("success") is True for v in validation),
                   "validation_records": len(validation),
                   "gate_fully_observed": sorted(v.get("seed") for v in validation) == sorted(expected_seeds),
                   "accepted": acq.get("accepted") if acq_complete else None,
                   "single_attempt_holds": len(acq_rows) <= 1 if ledger_available else None,
                   "response_finish_reason": acq_response.get("choices", [{}])[0].get("finish_reason") if isinstance(acq_response, dict) else None,
                   "candidate_deployed": False, "prefix_index": acq.get("prefix_index", 3 if acq else None),
                   "schedule": "At first completed matched pair with three eligible completed agent traces. Interrupted traces are ineligible in v3, while their evaluation outcomes remain included.",
                   "gate_scope": "Two-instance pilot, no repair or retry. The fixed historical deployment program is unchanged."}
    if acq_complete and ledger_available and acq.get("attempts") != len(acq_rows):
        issues.append("Completed acquisition attempt count differs from physical ledger requests.")
    if acquisition["accepted"] and not (acquisition["gate_fully_observed"] and acquisition["validation_successes"] == len(expected_seeds)):
        issues.append("Accepted acquisition does not have all planned validation successes.")

    known_labels = {e["episode"] for e in episodes + tight}
    result_paths = set(raw.glob("episodes/**/result.json"))
    expected_paths = {raw / "episodes" / label / "result.json" for label in known_labels}
    unexpected_results = [str(p) for p in sorted(result_paths - expected_paths)]
    if unexpected_results:
        issues.append("Unexpected episode result files exist and are retained in the audit listing.")
    groups = {
        "all_requests": rows,
        "agent_serving": [r for r in rows if r["episode"].startswith("agent/")],
        "program_fallback_serving": [r for r in rows if r["episode"].startswith("pace/") and not r["episode"].startswith("pace/acquisition/")],
        "new_acquisition_pilot": acq_rows,
        "pace_including_new_acquisition": [r for r in rows if r["episode"].startswith("pace/")],
        "tight_diagnostics": [r for r in rows if r["episode"].startswith("tight/")],
        "binding_requests_in_serving": [r for r in rows if request_phase(r) == "binding"],
        "unassigned": [r for r in rows if r["episode"] not in known_labels and not r["episode"].startswith("pace/acquisition/")],
    }
    spending = {name: billing_totals(group, ledger_available) for name, group in groups.items()}
    price_audits = [audit_request(r, spec) for r in rows]
    ids = defaultdict(list)
    for r in rows:
        if r.get("generation_id"):
            ids[r["generation_id"]].append(r["request_id"])
    duplicates = {key: values for key, values in ids.items() if len(values) > 1}
    artifact_ids = [i for e in episodes + tight for i in e["trajectory_generation_ids"]]
    artifact_ids.extend(e["binding_generation_id"] for e in episodes + tight if e["binding_generation_id"])
    if isinstance(acq_response, dict) and acq_response.get("id"):
        artifact_ids.append(acq_response["id"])
    artifact_counts = Counter(artifact_ids)
    generation_audit = {"physical_requests": len(rows) if ledger_available else None,
                        "requests_with_generation_id": sum(bool(r.get("generation_id")) for r in rows),
                        "unique_generation_ids": len(ids), "duplicate_generation_ids": duplicates,
                        "request_ids_missing_generation_id": [r["request_id"] for r in rows if not r.get("generation_id")],
                        "all_physical_generation_ids_unique": not duplicates if ledger_available and rows else None,
                        "artifact_ids_without_ledger_receipt": sorted(set(artifact_ids) - set(ids)),
                        "ledger_ids_without_trajectory_or_acquisition_copy": sorted(set(ids) - set(artifact_ids)),
                        "repeated_ids_across_distinct_call_artifacts": {k: v for k, v in artifact_counts.items() if v > 1},
                        "interpretation": "Uniqueness checks physical requests in the ledger. It is not proof of independent sampling or uncached provider execution."}
    source_audit = []
    for name, expected in spec.get("source_sha256", {}).items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or path.name == ".env":
            source_audit.append({"path": name, "status": "not_read_outside_allowed_source_scope"})
            continue
        actual = digest(path.read_bytes()) if path.is_file() else None
        source_audit.append({"path": name, "expected_sha256": expected,
                             "actual_sha256": actual, "matches": actual == expected})
    lock_hash = digest(canonical(spec.get("locks")).encode()) if frozen else None
    audit = {"generation_identity": generation_audit, "price_bound_requests": price_audits,
             "price_check_status_counts": dict(Counter(a["status"] for a in price_audits)),
             "unsettled_records": [a for a in price_audits if a["state"] != "settled"],
             "source_hashes": source_audit,
             "ledger_locks_match_manifest": metadata.get("locks_sha256") == lock_hash if ledger_available and frozen else None,
             "ledger_budget_matches_manifest": int(metadata.get("budget_nusd", -1)) == nanos(spec.get("total_usd_cap", "5")) if ledger_available and frozen else None,
             "guards": guard_audit(rows, episodes + tight, spec, acq),
             "unexpected_result_files": unexpected_results,
             "ledger_read_mode": "sqlite3 URI mode=ro, query_only, single read transaction"}
    main_done = sum(e["result_present"] for e in episodes)
    tight_done = sum(e["result_present"] for e in tight)
    complete = frozen and main_done == len(episodes) and tight_done == len(tight) and acq_complete
    return {"schema": "pace-live-analysis/1", "generated_at": datetime.now(timezone.utc).isoformat(),
            "ledger_snapshot_started_at": snapshot_at,
            "input_dir": str(raw), "manifest_present": frozen,
            "manifest_sha256": digest((raw / "manifest.json").read_bytes()) if frozen else None,
            "manifest_schema": spec.get("schema"), "manifest_frozen_at": spec.get("frozen_at"),
            "snapshot_complete": complete, "ledger_available": ledger_available,
            "planned_pair_count": len(tasks), "planned_tight_count": len(tight_tasks),
            "main_episode_results": main_done, "tight_episode_results": tight_done,
            "scope": {"role": spec.get("role"), "binding_cost": spec.get("binding_cost"),
                      "binding_extraction": spec.get("binding_extraction"), "routing": spec.get("routing"),
                      "failure_rule": spec.get("failure_rule"),
                      "prior_v2_exposure_nusd": spec.get("prior_v2_exposure_nusd"),
                      "aggregate_authorized_ceiling_nusd": spec.get("aggregate_authorized_ceiling_nusd"),
                      "fixed_historical_deployment_artifact": True,
                      "historical_artifact_acquisition_in_measured_spend": False,
                      "complete_online_pace_deployment": False,
                      "reference": spec.get("reference"), "compile_pilot": spec.get("compile_pilot")},
            "pairs": pairs, "tight_diagnostics": tight, "tight_success": success_summary(tight),
            "comparisons": comparisons, "acquisition": acquisition, "spending": spending,
            "audit": audit, "issues": issues,
            "inference_limits": ["Four planned seed clusters. Bootstrap intervals are exploratory and unstable with such a small cluster count.",
                                 "Cases share one calendar family, one fixed program, and controlled synthetic perturbations.",
                                 "Missing results remain unknown. Observed failed, denied, and aborted results remain in the stated denominators and costs.",
                                 "Acquisition is scheduled after three eligible traces, uses a two-instance pilot gate, and does not replace the deployment artifact.",
                                 "Declared reference prefixes are supplied billing allowances, not measured agent-only bills.",
                                 spec.get("binding_cost", "Binding cost scope is not yet frozen."),
                                 "These records validate components and do not establish complete PACE online selection or deployment."]}


def cell(value: Any) -> str:
    if value is None:
        return "NA"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("|", "\\|").replace("\n", " ")


def fraction(value: dict) -> str:
    if value["planned"] == 0:
        return "no selected cases"
    if value["unknown_outcome"]:
        return (f"{value['observed_fraction']} among recorded outcomes; {value['unknown_outcome']} unknown; "
                f"planned bounds [{', '.join(value['planned_success_bounds'])}]")
    return value["planned_success_fraction"]


def episode_cost(episode: dict) -> str:
    exact = episode["exact_complete_actual_nusd"]
    if exact is not None:
        return usd(exact)
    bounds = episode["complete_bill_bounds_nusd"]
    return f"[{usd(bounds[0])}, {usd(bounds[1])}]" if bounds is not None else "NA"


def report(summary: dict) -> str:
    lines = ["# Joint GUI recovery and budget component results", "",
             f"Generated {summary['generated_at']} from `{summary['input_dir']}`.", "",
             f"Snapshot: **{'complete' if summary['snapshot_complete'] else 'incomplete'}**. "
             f"Main results {summary['main_episode_results']}/{2 * summary['planned_pair_count']}; "
             f"tight diagnostics {summary['tight_episode_results']}/{summary['planned_tight_count']}; "
             f"acquisition {summary['acquisition']['status']}.", "",
             f"Manifest `{summary['manifest_schema']}` SHA-256 `{summary['manifest_sha256']}`. "
             + ("All scheduled task results are present. The ledger still contains unsettled requests."
                if summary["snapshot_complete"] else
                "If the runner is still active, rerun this analyzer after it exits. Raw files and the ledger can advance between reads."), "",
             "## Scope and accounting", "",
             "The deployed program is a fixed historical artifact. Its historical acquisition cost is outside measured serving costs. "
             "A separate new cold-build attempt is scheduled at the first paired prefix with three eligible completed agent traces. "
             "It uses two validation instances, has no repair retry, and never replaces the deployed program. "
             + (summary["scope"].get("binding_cost") or "Binding cost scope is not yet frozen.") + " "
             "This is component evidence, not a complete PACE online deployment.", "",
             (summary["scope"].get("failure_rule") or "Failure rule is not yet frozen."), "",
             "Actual bills and declared reference prefixes are separate quantities. "
             "The main reference adds USD 0.08 per arrival, while the tight reference adds USD 0.02. "
             "The prefix ceiling is 1.25 times that supplied allowance. "
             "The allowance is not the observed agent-only bill. Occupied budget retains worst-case reservations for unsettled requests.", "",
             "| Scope | Requests | Known actual USD | Settled actual USD | Occupied USD | Unsettled |",
             "|---|---:|---:|---:|---:|---:|"]
    for name, item in summary["spending"].items():
        lines.append(f"| {name} | {cell(item['requests'])} | {usd(item['known_actual_nusd'])} | {usd(item['settled_actual_nusd'])} | {usd(item['occupied_nusd'])} | {cell(item['unsettled_requests'])} |")
    if summary.get("prior_run"):
        prior, combined = summary["prior_run"], summary["combined_spending"]
        lines.extend(["", f"Prior v2 remains a separate interrupted experiment with {prior['main_episode_results']}/24 main results. "
                      f"Its known actual bill is USD {usd(prior['spending']['known_actual_nusd'])} and occupied liability is USD {usd(prior['spending']['occupied_nusd'])}. "
                      "Its direct supplied bindings and stopped run are not pooled with v3 policy comparisons. "
                      "See `joint_v2_report.md` and `joint_v2_summary.json` for every retained v2 case.", "",
                      f"Combined v2 + v3 known actual: USD {usd(combined['known_actual_nusd'])}. "
                      f"Combined occupied liability: USD {usd(combined['occupied_nusd'])}. "
                      f"Aggregate authorized ceiling: USD {usd(combined['aggregate_cap_nusd'])}. "
                      f"Within that ceiling: {cell(combined['occupied_within_aggregate_cap'])}. "
                      f"Cross-run duplicate generation IDs: {len(combined['duplicate_generation_ids'])}."])
    lines.extend(["", "The rows overlap: pace including acquisition contains serving plus the new pilot. All requests is the total paid account. "
                  "Known actual charges on unsettled requests are retained, but are not treated as fully verified bills.", "",
                  "## All prespecified paired cases", "",
                  "No observed failure is excluded. Missing results are unknown, not zero-cost successful tasks. "
                  "Bills below are exact only when the episode has a result and all its requests are settled. "
                  "Bracketed amounts are known-charge lower and occupied-reservation upper bounds. "
                  "The retained-field count is the number of six target fields already correct at fallback entry.", "",
                  "| Pair | Initial match | Agent outcome | Program + fallback outcome | Agent actual USD | P+F actual USD | Fallback | Exposed A/P | Retained fields | Termination A/P |",
                  "|---|---|---|---|---:|---:|---|---|---|---|"])
    for pair in summary["pairs"]:
        a, b = (pair[q] for q in POLICIES)
        outcome = lambda e: ("success" if e["success"] else "unsuccessful") if e["success"] is not None else e["status"]
        retained = f"{b['retained_correct_fields']}/6" if b["retained_correct_fields"] is not None else "NA"
        lines.append(f"| {pair['pair']} | {cell(pair['initial_states_match'])} | {outcome(a)} | {outcome(b)} | {episode_cost(a)} | {episode_cost(b)} | {cell(b['fallback'])} | {cell(a['perturbation_exposed'])}/{cell(b['perturbation_exposed'])} | {retained} | {cell(a['termination'])}/{cell(b['termination'])} |")
    if summary["scope"].get("binding_extraction"):
        lines.extend(["", "Binding charges are included in program-plus-fallback serving costs. "
                      "A malformed binding can trigger fallback before the program runs, which differs from recovery after a program error.", "",
                      "| Pair | Binding attempted | Binding known / occupied USD | Extracted fields correct | Program attempted | Fallback reason |",
                      "|---|---|---:|---|---|---|"])
        for pair in summary["pairs"]:
            e = pair["program_fallback"]
            checks = e["extracted_binding_matches"]
            correct = f"{sum(checks.values())}/6" if checks is not None else "NA"
            lines.append(f"| {pair['pair']} | {cell(e['binding_attempted'])} | {usd(e['binding_billing']['known_actual_nusd'])} / {usd(e['binding_billing']['occupied_nusd'])} | {correct} | {cell(e['program_attempted'])} | {cell(e['fallback_reason'])} |")
    lines.extend(["", "## Cost and success comparisons", "",
                  "Means include recorded unsuccessful, budget-denied, and aborted episodes when their bills are settled. "
                  "A negative program-minus-agent difference means lower serving cost for program plus fallback. "
                  "Paired percentile bootstrap intervals resample seed clusters and retain all cases and both policies within each drawn seed. "
                  "With four seeds, all 256 ordered resamples are enumerated. Four clusters are too few for precise population inference.", ""])
    lines.extend(["Provider or billing interruptions are counted as unsuccessful outcomes and reported separately below. "
                  "A provider interruption can end a trace before GUI work finishes. Its shorter trace and smaller known charge do not establish better GUI ability. "
                  "Unknown bills remain in reservation-based cost bounds.", "",
                  "| Policy | Successes | Recorded unsuccessful | Provider / billing aborts | Infrastructure aborts | Prefix denials | Missing outcomes |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for policy, item in summary["comparisons"]["all_pairs"]["success"].items():
        lines.append(f"| {policy} | {item['successes']} | {item['recorded_unsuccessful']} | {item['provider_or_billing_aborts']} | {item['infrastructure_aborts']} | {item['prefix_denials']} | {item['unknown_outcome']} |")
    lines.append("")
    for name, comparison in summary["comparisons"].items():
        means = comparison["full_selected_mean_nusd"]
        observed = comparison["observed_complete_pair_mean_nusd"]
        ci = comparison["paired_seed_cluster_bootstrap_95ci_nusd"]
        bounds = comparison["full_selected_mean_bill_bounds_nusd"]
        envelope = comparison["cost_bound_bootstrap_95_envelope_nusd"]
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"Exact-cost pairs {comparison['exact_cost_pairs']}/{comparison['planned_or_selected_pairs']}. "
                     f"Agent success: {fraction(comparison['success']['agent'])}. "
                     f"Program plus fallback success: {fraction(comparison['success']['program_fallback'])}.")
        if comparison.get("selection_definition"):
            lines.append(comparison["selection_definition"])
        if observed:
            label = "Full selected set" if means is not None else "Pairs with exact bills only"
            values = means or observed
            lines.append(f"{label}: agent mean USD {usd(values['agent'])}, program plus fallback mean USD {usd(values['program_fallback'])}, difference USD {usd(values['program_minus_agent'])}.")
        if bounds and means is None:
            lines.append("All selected cases, retaining unsettled requests: mean bill bounds (USD) " + ", ".join(f"{key} [{usd(value[0])}, {usd(value[1])}]" for key, value in bounds.items()) + ".")
        if envelope:
            lines.append("Paired seed-cluster 95% bootstrap envelope of the bill bounds (USD): " + ", ".join(f"{key} [{usd(value[0])}, {usd(value[1])}]" for key, value in envelope.items()) + ". This propagates reservation-based uncertainty and is not an exact-bill confidence interval.")
        if ci:
            lines.append("Paired seed-cluster bootstrap 95% intervals (USD): " + ", ".join(f"{key} [{usd(value[0])}, {usd(value[1])}]" for key, value in ci.items()) + ".")
        else:
            lines.append("No exact-bill paired interval: " + comparison["interval_unavailable_reason"])
        lines.append("")
    lines.extend(["## Tight-budget diagnostics", "", f"Success: {fraction(summary['tight_success'])}.", "",
                  "| Case | Status / outcome | Actual USD | Occupied USD | Declared prefix USD | Ceiling USD | Prefix occupied USD | Bound | Fallback / exposed | Retained fields | Termination |",
                  "|---|---|---:|---:|---:|---:|---:|---|---|---|---|"])
    for e in summary["tight_diagnostics"]:
        state = e["status"] if e["success"] is None else ("success" if e["success"] else "unsuccessful")
        retained = f"{e['retained_correct_fields']}/6" if e["retained_correct_fields"] is not None else "NA"
        lines.append(f"| {e['pair']} | {state} | {episode_cost(e)} | {usd(e['billing']['occupied_nusd'])} | {usd(e['reference_prefix_nusd'])} | {usd(e['prefix_ceiling_nusd'])} | {usd(e['recorded_prefix_cost_nusd'])} | {cell(e['recorded_prefix_bound_holds'])} | {cell(e['fallback'])}/{cell(e['perturbation_exposed'])} | {retained} | {cell(e['termination'])} |")
    lines.extend(["", "The first tight arrival has a USD 0.025 ceiling, below the USD 0.040345600 full-request reservation. "
                  "For v3 the binder reservation is USD 0.039833600, also above that ceiling. "
                  "A denial therefore tests the affordability guard and counts as an unsuccessful task if recorded.", "",
                  "## Single acquisition attempt", ""])
    acq = summary["acquisition"]
    lines.append(f"Status {acq['status']}; physical requests {cell(acq['billing']['requests'])}; "
                 f"known actual USD {usd(acq['billing']['known_actual_nusd'])}; occupied USD {usd(acq['billing']['occupied_nusd'])}. "
                 f"Accepted: {cell(acq['accepted'])}. Validation successes: {acq['validation_successes']}/{len(acq['validation_seeds'])}, "
                 f"with {acq['validation_records']} validation records. Finish reason: {cell(acq['response_finish_reason'])}.")
    lines.append(f"First three training episodes preserved: {cell(acq['first_three_completed_agent_traces_preserved'])}. "
                 f"At most one physical acquisition request: {cell(acq['single_attempt_holds'])}.")
    lines.append("")
    for entry in acq["training_outcomes"]:
        lines.append(f"- Training `{entry['episode']}`: success {cell(entry['success'])}, termination {cell(entry['termination'])}.")
    for entry in (acq.get("result") or {}).get("validation", []):
        lines.append(f"- Validation seed {entry.get('seed')}: success {cell(entry.get('success'))}, unrelated state preserved {cell(entry.get('unrelated_state_preserved'))}, error {cell(entry.get('error'))}.")
    if (acq.get("result") or {}).get("error"):
        lines.append(f"Acquisition error: `{cell(acq['result']['error'])}`.")
    lines.extend(["", "## Ledger, identity, price, and prefix audits", ""])
    audit = summary["audit"]
    ids = audit["generation_identity"]
    lines.append(f"Ledger opened using `{audit['ledger_read_mode']}`. "
                 f"Generation IDs: {ids['requests_with_generation_id']} present across {cell(ids['physical_requests'])} requests, "
                 f"{ids['unique_generation_ids']} unique, {len(ids['duplicate_generation_ids'])} duplicated IDs. "
                 f"Missing generation IDs: {len(ids['request_ids_missing_generation_id'])}. "
                 f"Price checks: `{json.dumps(audit['price_check_status_counts'], sort_keys=True)}`. "
                 f"Unsettled requests: {len(audit['unsettled_records'])}.")
    lines.append(f"Frozen ledger quotes match manifest: {cell(audit['ledger_locks_match_manifest'])}. "
                 f"Ledger absolute cap matches manifest: {cell(audit['ledger_budget_matches_manifest'])}. "
                 f"Observed request guards hold: {cell(audit['guards']['all_observed_request_guards_hold'])}. "
                 f"Recorded terminal prefix violations: {len(audit['guards']['recorded_terminal_prefix_violations'])}.")
    source_bad = [r for r in audit["source_hashes"] if r.get("matches") is not True]
    lines.append(f"Frozen source hash mismatches or unavailable sources: {len(source_bad)}/{len(audit['source_hashes'])}. "
                 "Per-request generation IDs, price calculations, guard calculations, and unsettled records are retained in the companion JSON. "
                 "Unique generation IDs do not establish independent stochastic samples or rule out provider caching.")
    lines.extend(["", "| Main P+F arrival | Actual episode USD | Declared reference prefix USD | Ceiling USD | Recorded occupied prefix USD | Bound |",
                  "|---|---:|---:|---:|---:|---|"])
    for p in summary["pairs"]:
        e = p["program_fallback"]
        lines.append(f"| {p['pair']} | {episode_cost(e)} | {usd(e['reference_prefix_nusd'])} | {usd(e['prefix_ceiling_nusd'])} | {usd(e['recorded_prefix_cost_nusd'])} | {cell(e['recorded_prefix_bound_holds'])} |")
    lines.append("")
    for entry in audit["price_bound_requests"]:
        if entry["status"] != "pass" or entry["state"] != "settled":
            lines.append(f"- Request `{entry['request_id']}` ({entry['episode']}): {entry['state']}, audit {entry['status']}, "
                         f"failures {entry['failed_checks']}, unavailable {entry['unavailable_checks']}, error {cell(entry['error'])}.")
    lines.extend(["", "## Retained failures and missingness", ""])
    for e in [p[q] for p in summary["pairs"] for q in POLICIES] + summary["tight_diagnostics"]:
        if e["success"] is not True or e["program_error"]:
            lines.append(f"- `{e['episode']}`: {e['status']}, success {cell(e['success'])}, termination {cell(e['termination'])}, "
                         f"program error {cell(e['program_error'])}, terminal error {cell(e['error'])}, "
                         f"known actual USD {usd(e['billing']['known_actual_nusd'])}, occupied USD {usd(e['billing']['occupied_nusd'])}.")
    if summary["issues"]:
        lines.extend(["", "## Snapshot issues", ""])
        lines.extend(f"- {issue}" for issue in summary["issues"])
    missing = sum(p[q]["success"] is None for p in summary["pairs"] for q in POLICIES)
    unsettled = summary["spending"]["all_requests"]["unsettled_requests"]
    caveats = []
    if missing:
        caveats.append(f"{missing} main outcomes remain missing.")
    if unsettled:
        caveats.append(f"{unsettled} request bills remain unsettled and retain their reservations.")
    lines.extend(["", "Note: Four seed clusters, one calendar family, and synthetic perturbations limit inference. "
                  + " ".join(caveats)
                  + " The scheduled two-instance acquisition pilot and supplied billing references support component claims only.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=HERE / "reproduced")
    args = parser.parse_args()
    raw, output = args.input_dir.resolve(), args.output_dir.resolve()
    if output == raw or output.is_relative_to(raw):
        parser.error("Output must be outside the raw experiment directory.")
    summary = analyze(raw)
    summaries = [(raw.name, summary)]
    prior_path = raw.parent / "v2"
    if summary["scope"].get("prior_v2_exposure_nusd") is not None and prior_path != raw:
        prior = analyze(prior_path)
        summaries.insert(0, ("v2", prior))
        previous, current = (s["spending"]["all_requests"] for s in (prior, summary))
        total_known = previous["known_actual_nusd"] + current["known_actual_nusd"] if previous["available"] and current["available"] else None
        total_occupied = previous["occupied_nusd"] + current["occupied_nusd"] if previous["available"] and current["available"] else None
        identities = defaultdict(list)
        for label, run_summary in summaries:
            for entry in run_summary["audit"]["price_bound_requests"]:
                if entry["generation_id"]:
                    identities[entry["generation_id"]].append(f"{label}/{entry['request_id']}")
        cap = summary["scope"]["aggregate_authorized_ceiling_nusd"]
        summary["prior_run"] = {"input_dir": prior["input_dir"], "manifest_sha256": prior["manifest_sha256"],
                                "main_episode_results": prior["main_episode_results"], "spending": previous,
                                "pooled_into_current_comparisons": False,
                                "frozen_prior_liability_matches_read_only_ledger": previous["occupied_nusd"] == summary["scope"]["prior_v2_exposure_nusd"] if previous["available"] else None}
        summary["combined_spending"] = {"known_actual_nusd": total_known, "occupied_nusd": total_occupied,
                                        "aggregate_cap_nusd": cap,
                                        "occupied_within_aggregate_cap": total_occupied <= cap if total_occupied is not None and cap is not None else None,
                                        "duplicate_generation_ids": {k: v for k, v in identities.items() if len(v) > 1}}
    output.mkdir(parents=True, exist_ok=True)
    for run_name, run_summary in summaries:
        stem = "joint_" + run_name
        summary_path, report_path = output / f"{stem}_summary.json", output / f"{stem}_report.md"
        for path, text in ((summary_path, json.dumps(run_summary, indent=2, sort_keys=True) + "\n"),
                           (report_path, report(run_summary))):
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(text)
            temporary.replace(path)
    print(json.dumps({"summary": str(summary_path), "report": str(report_path),
                      "complete": summary["snapshot_complete"],
                      "main_results": summary["main_episode_results"],
                      "tight_results": summary["tight_episode_results"],
                      "spending": summary["spending"]["all_requests"],
                      "price_checks": summary["audit"]["price_check_status_counts"],
                      "issues": len(summary["issues"])}, indent=2))


if __name__ == "__main__":
    main()
