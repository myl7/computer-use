"""Offline audit of selective and local compilation evidence.

The audit reads the frozen T16 build/verification records and the local
revision episode trajectories.  It does not import the Android runner, call a
model, contact an API, or start an emulator.  The default output directory is
``experimental-results/guiexp_android/selective_20260915`` relative to the
workspace containing this package.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
ANALYSIS_ID = "selective_20260915"
TOKEN_FIELDS = (
    "calls",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "total_tokens",
    "cost_usd",
)
REPAIR_STAGE_FIELDS = {
    "reactive_resume": "resume_episodes",
    "analyzer": "analyzer",
    "rewrite": "builder_refinements",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _clean_text(value: Any, limit: int = 360) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _relative(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path, problems: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"could not read {path}: {type(exc).__name__}")
        return None
    if not isinstance(value, dict):
        problems.append(f"expected an object in {path}")
        return None
    return value


def _recorded_usage(value: Any) -> dict[str, int | float | None]:
    record = _dict(value)
    return {field: _number(record.get(field)) for field in TOKEN_FIELDS}


def _usage_sum(records: Iterable[dict[str, Any]], field: str) -> int | float | None:
    values = [_number(record.get(field)) for record in records]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return sum(values)


def _sum_usage(records: Iterable[dict[str, Any]]) -> dict[str, int | float | None]:
    materialized = list(records)
    return {field: _usage_sum(materialized, field) for field in TOKEN_FIELDS}


def _merge_counts(target: Counter[str], source: Counter[str]) -> None:
    for key, value in source.items():
        target[key] += value


def _normal_message(value: Any) -> str | None:
    return _clean_text(value, limit=500)


def _canonical_action(value: Any) -> tuple[str, dict[str, Any] | None] | None:
    """Return a stable action representation and its parsed object.

    ``action`` is normally a compact JSON string.  A malformed or non-JSON
    action remains available for last-observation reporting, but is never
    treated as a comparable semantic action.
    """

    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":")), value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("action:"):
        text = text.split(":", 1)[1].strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        return " ".join(text.split()), None
    if not isinstance(parsed, dict):
        return " ".join(text.split()), None
    return json.dumps(parsed, sort_keys=True, separators=(",", ":")), parsed


def _step_records(trajectory_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    records: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    try:
        lines = trajectory_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records, final
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        if value.get("record_type") == "final":
            final = value
        elif "step" in value and isinstance(value.get("usage"), dict):
            records.append(value)
    return records, final


def _action_sequence(records: Iterable[dict[str, Any]]) -> list[str]:
    sequence: list[str] = []
    for record in records:
        canonical = _canonical_action(record.get("action"))
        if canonical is None:
            continue
        text, parsed = canonical
        if parsed is None:
            continue
        # ``status`` is the episode protocol marker, not a GUI action.
        if isinstance(parsed, dict) and parsed.get("action_type") == "status":
            continue
        sequence.append(text)
    return sequence


def _longest_common_prefix(left: list[str], right: list[str]) -> int:
    length = 0
    for left_item, right_item in zip(left, right):
        if left_item != right_item:
            break
        length += 1
    return length


def _integerish(value: int | float) -> int | float:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _prefix_summary(
    samples: list[dict[str, Any]],
    *,
    basis: str,
    min_samples: int = 2,
) -> dict[str, Any]:
    comparable = [sample for sample in samples if sample.get("action_sequence")]
    if len(comparable) < min_samples:
        return {
            "length": None,
            "status": "unknown",
            "basis": basis,
            "sample_count": len(comparable),
            "reason": f"fewer than {min_samples} comparable completed trajectories",
        }
    sequences = [sample["action_sequence"] for sample in comparable]
    common = list(sequences[0])
    for sequence in sequences[1:]:
        common = common[: _longest_common_prefix(common, sequence)]
    pairwise = [
        _longest_common_prefix(left["action_sequence"], right["action_sequence"])
        for left, right in itertools.combinations(comparable, 2)
    ]
    length = len(common)
    return {
        "length": length,
        "status": "evidenced" if length > 0 else "evidenced_zero",
        "basis": basis,
        "sample_count": len(comparable),
        "all_sample_lcp": length,
        "pair_count": len(pairwise),
        "pairwise_min": min(pairwise),
        "pairwise_median": _integerish(statistics.median(pairwise)),
        "pairwise_max": max(pairwise),
        "sample_seeds": [sample.get("seed") for sample in comparable],
    }


def _paired_prefix_summary(
    pairs: list[dict[str, Any]],
    *,
    basis: str,
) -> dict[str, Any]:
    if len(pairs) < 2:
        return {
            "length": None,
            "status": "unknown",
            "basis": basis,
            "sample_count": len(pairs),
            "reason": "fewer than 2 matched discover/doc pairs",
        }
    lengths = [int(pair["length"]) for pair in pairs]
    return {
        "length": _integerish(statistics.median(lengths)),
        "status": "evidenced" if any(length > 0 for length in lengths) else "evidenced_zero",
        "basis": basis,
        "sample_count": len(pairs),
        "min": min(lengths),
        "median": _integerish(statistics.median(lengths)),
        "max": max(lengths),
        "matched_seeds": [pair.get("seed") for pair in pairs],
        "per_pair_lengths": lengths,
    }


def _action_type(record: dict[str, Any] | None) -> str | None:
    if not record:
        return None
    canonical = _canonical_action(record.get("action"))
    if canonical and isinstance(canonical[1], dict):
        value = canonical[1].get("action_type")
        return str(value) if value is not None else None
    return None


def _episode_record(
    state_path: Path,
    workspace_root: Path,
    problems: list[str],
) -> dict[str, Any]:
    parts = state_path.parts
    # Caller only passes .../episodes/<model>/<family>/<seed>/<condition>/state.json.
    model_dir, family, seed_dir, condition = parts[-5:-1]
    state = _read_json(state_path, problems) or {}
    trajectory_path = state_path.with_name("trajectory.jsonl")
    records: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    if trajectory_path.exists():
        records, final = _step_records(trajectory_path)
    state_result = _dict(state.get("result"))
    result = final if final is not None else state_result
    success = result.get("success") if isinstance(result.get("success"), bool) else None
    status = state.get("status")
    if status is None and final is not None:
        status = "done"
    action_sequence = _action_sequence(records)
    action_errors = Counter(
        message
        for record in records
        for message in [_normal_message(_dict(record.get("obs_meta")).get("last_action_error"))]
        if message is not None
    )
    last_record = records[-1] if records else None
    last_meta = _dict(last_record.get("obs_meta")) if last_record else {}
    last_action = _canonical_action(last_record.get("action")) if last_record else None
    observed_usage = _sum_usage(_dict(record.get("usage")) for record in records)
    recorded_total_tokens = _number(result.get("total_tokens"))
    if recorded_total_tokens is None:
        recorded_total_tokens = observed_usage.get("total_tokens")
    recorded_cost = _number(result.get("total_cost_usd"))
    if recorded_cost is None:
        recorded_cost = observed_usage.get("cost_usd")
    steps = result.get("steps")
    if not isinstance(steps, int):
        numeric_steps = [record.get("step") for record in records if isinstance(record.get("step"), int)]
        steps = max(numeric_steps) if numeric_steps else 0
    model = result.get("model") or state.get("model")
    family_value = result.get("family") or state.get("family") or family
    seed = result.get("seed") or state.get("seed")
    return {
        "model_dir": model_dir,
        "model": model,
        "family": family_value,
        "family_dir": family,
        "seed": seed,
        "seed_dir": seed_dir,
        "condition": result.get("condition") or state.get("condition") or condition,
        "status": status,
        "stop_reason": _clean_text(state.get("stop_reason"), 260),
        "success": success,
        "steps": steps,
        "model_calls": _number(result.get("model_calls")),
        "total_tokens": recorded_total_tokens,
        "total_cost_usd": recorded_cost,
        "trajectory_path": _relative(trajectory_path, workspace_root) if trajectory_path.exists() else None,
        "state_path": _relative(state_path, workspace_root),
        "trajectory_present": trajectory_path.exists(),
        "action_sequence": action_sequence,
        "action_errors": action_errors,
        "observed_usage": observed_usage,
        "last_observed": {
            "step": last_record.get("step") if last_record else None,
            "activity": _clean_text(last_meta.get("url"), 240),
            "action_type": _action_type(last_record),
            "last_action_error": _normal_message(last_meta.get("last_action_error")),
        }
        if last_record
        else None,
        "last_action_canonical": last_action[0][:220] if last_action else None,
    }


def _episode_mode_summary(
    records: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    mode_records = [record for record in records if record.get("condition") == mode]
    status_counts = Counter(str(record.get("status")) for record in mode_records)
    completed = [
        record
        for record in mode_records
        if record.get("status") == "done" and record.get("success") in (True, False)
    ]
    successes = sum(record.get("success") is True for record in completed)
    failures = sum(record.get("success") is False for record in completed)
    unknown_outcomes = len(mode_records) - len(completed)
    all_action_errors: Counter[str] = Counter()
    for record in mode_records:
        _merge_counts(all_action_errors, record.get("action_errors", Counter()))
    final_tokens = [record.get("total_tokens") for record in mode_records if _number(record.get("total_tokens")) is not None]
    final_costs = [record.get("total_cost_usd") for record in mode_records if _number(record.get("total_cost_usd")) is not None]
    final_calls = [record.get("model_calls") for record in mode_records if _number(record.get("model_calls")) is not None]
    return {
        "episodes": len(mode_records),
        "trajectory_files": sum(bool(record.get("trajectory_present")) for record in mode_records),
        "status_counts": dict(sorted(status_counts.items())),
        "completed_with_boolean_outcome": len(completed),
        "success_count": successes,
        "failure_count": failures,
        "success_rate_completed": (successes / len(completed)) if completed else None,
        "unknown_or_incomplete_outcome_count": unknown_outcomes,
        "recorded_model_calls_sum": sum(final_calls) if final_calls else None,
        "recorded_total_tokens_sum": sum(final_tokens) if final_tokens else None,
        "recorded_cost_usd_sum": sum(final_costs) if final_costs else None,
        "observed_step_usage": _sum_usage(
            record.get("observed_usage", {}) for record in mode_records
        ),
        "observed_action_error_counts": dict(sorted(all_action_errors.items())),
    }


def _episode_failure_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") != "done" or record.get("success") is not False:
            continue
        evidence.append(
            {
                "condition": record.get("condition"),
                "seed": record.get("seed"),
                "steps": record.get("steps"),
                "trajectory_path": record.get("trajectory_path"),
                "last_observed": record.get("last_observed"),
                "action_error_messages": dict(sorted(record.get("action_errors", {}).items())),
                "basis": "completed trajectory final result recorded success=false",
            }
        )
    return evidence


def _cross_condition_pairs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_seed: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        if record.get("status") != "done" or record.get("success") not in (True, False):
            continue
        if not record.get("action_sequence"):
            continue
        by_seed[str(record.get("seed"))][str(record.get("condition"))] = record
    pairs: list[dict[str, Any]] = []
    for seed, modes in sorted(by_seed.items()):
        discover = modes.get("discover")
        doc = modes.get("doc")
        if not discover or not doc:
            continue
        pairs.append(
            {
                "seed": discover.get("seed"),
                "length": _longest_common_prefix(
                    discover["action_sequence"], doc["action_sequence"]
                ),
                "discover_success": discover.get("success"),
                "doc_success": doc.get("success"),
            }
        )
    return pairs


def _stable_prefixes(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_mode = {
        mode: _prefix_summary(
            [
                record
                for record in records
                if record.get("condition") == mode
                and record.get("status") == "done"
                and record.get("success") in (True, False)
            ],
            basis=(
                "exact canonical action JSON prefix across at least two completed "
                f"{mode} trajectories"
            ),
        )
        for mode in ("discover", "doc")
    }
    pairs = _cross_condition_pairs(records)
    return {
        "definition": (
            "A prefix is comparable only when the trajectory has a completed boolean "
            "outcome and a recorded action. Actions are canonicalized JSON. The terminal "
            "status marker is omitted."
        ),
        "discover": by_mode["discover"],
        "doc": by_mode["doc"],
        "cross_condition": _paired_prefix_summary(
            pairs,
            basis="exact canonical action JSON prefix within matched discover/doc seeds",
        ),
    }


def _error_entry(
    *,
    message: Any,
    source: str,
    path: str,
    round_index: int | None = None,
    seed: Any = None,
    verified_basis: str,
) -> dict[str, Any]:
    return {
        "message": _normal_message(message),
        "source": source,
        "path": path,
        "round": round_index,
        "seed": seed,
        "verified_basis": verified_basis,
    }


def _failed_gate_details(
    gate: Any,
    *,
    source: str,
    path: str,
) -> list[dict[str, Any]]:
    gate_dict = _dict(gate)
    details = _list(gate_dict.get("detail"))
    output: list[dict[str, Any]] = []
    for index, detail in enumerate(details, start=1):
        detail_dict = _dict(detail)
        if detail_dict.get("passed") is not False:
            continue
        binding = _dict(detail_dict.get("binding"))
        output.append(
            {
                "binding_index": index,
                "binding_keys": sorted(str(key) for key in binding),
                "message": _normal_message(detail_dict.get("error")),
                "source": source,
                "path": path,
                "verified_basis": "recorded gate detail passed=false",
            }
        )
    return output


def _verification_crosscheck(
    build_verification: dict[str, Any],
    verify_totals: dict[str, Any],
) -> dict[str, Any]:
    mismatches: list[str] = []
    for output_name, source_name in REPAIR_STAGE_FIELDS.items():
        left = _recorded_usage(build_verification.get(source_name))
        right = _recorded_usage(verify_totals.get(source_name))
        for field in TOKEN_FIELDS:
            if left[field] is None or right[field] is None:
                continue
            if left[field] != right[field]:
                mismatches.append(f"{output_name}.{field}: build={left[field]} verify={right[field]}")
    return {"matches_on_recorded_fields": not mismatches, "mismatches": mismatches}


def _t16_cell_audit(
    build_path: Path,
    verify_path: Path,
    workspace_root: Path,
    problems: list[str],
) -> dict[str, Any]:
    build = _read_json(build_path, problems) or {}
    verify = _read_json(verify_path, problems) or {}
    family_dir = build_path.parent.name
    model_dir = build_path.parent.parent.name
    family = build.get("family") or verify.get("family") or family_dir
    model = build.get("model") or verify.get("model") or model_dir
    rounds = _list(verify.get("rounds"))
    round_audits: list[dict[str, Any]] = []
    verified_errors: list[dict[str, Any]] = []
    null_failure_entries = 0
    rounds_with_exception = 0
    for index, round_value in enumerate(rounds, start=1):
        round_dict = _dict(round_value)
        replay_detail = _list(round_dict.get("replay_detail"))
        failed_detail = [
            _dict(detail)
            for detail in replay_detail
            if _dict(detail).get("passed") is False
        ]
        failure_seed = round_dict.get("failure_seed")
        round_error = _normal_message(round_dict.get("error"))
        messages: list[str] = []
        if round_error is not None:
            messages.append(round_error)
            verified_errors.append(
                _error_entry(
                    message=round_error,
                    source="t16_verify.repair_round",
                    path=_relative(verify_path, workspace_root),
                    round_index=index,
                    seed=failure_seed,
                    verified_basis="recorded repair round has a replay failure",
                )
            )
        for detail in failed_detail:
            message = _normal_message(detail.get("error"))
            seed = detail.get("seed", failure_seed)
            if message is None:
                null_failure_entries += 1
            elif message not in messages:
                messages.append(message)
            if message is not None and message != round_error:
                verified_errors.append(
                    _error_entry(
                        message=message,
                        source="t16_verify.replay_detail",
                        path=_relative(verify_path, workspace_root),
                        round_index=index,
                        seed=seed,
                        verified_basis="recorded replay_detail passed=false",
                    )
                )
        if messages:
            rounds_with_exception += 1
        round_audits.append(
            {
                "round": index,
                "failure_seed": failure_seed,
                "seen_seeds": round_dict.get("seen_seeds"),
                "failed_replay_seeds": [detail.get("seed", failure_seed) for detail in failed_detail],
                "exception_messages": messages,
                "failure_without_exception": not messages,
                "resume_steps": _dict(round_dict.get("resume")).get("steps"),
                "resume_success": _dict(round_dict.get("resume")).get("success"),
                "restarted": _dict(round_dict.get("resume")).get("restarted"),
                "source": "t16_build/*/*/verify.json",
            }
        )

    test_seed0 = _dict(verify.get("test_seed0"))
    test_error = _normal_message(test_seed0.get("error"))
    if test_error is not None:
        verified_errors.append(
            _error_entry(
                message=test_error,
                source="t16_verify.test_seed0",
                path=_relative(verify_path, workspace_root),
                verified_basis="recorded seed-0 test result passed=false",
            )
        )
    gate_errors: list[dict[str, Any]] = []
    for key, value in sorted(_dict(build.get("gate_per_k")).items()):
        gate_errors.extend(
            _failed_gate_details(
                value,
                source=f"t16_build.gate_per_k.{key}",
                path=_relative(build_path, workspace_root),
            )
        )
    gate_errors.extend(
        _failed_gate_details(
            verify.get("gate"),
            source="t16_verify.gate",
            path=_relative(verify_path, workspace_root),
        )
    )
    for gate_error in gate_errors:
        if gate_error.get("message") is not None:
            verified_errors.append(
                _error_entry(
                    message=gate_error["message"],
                    source=gate_error["source"],
                    path=gate_error["path"],
                    verified_basis=gate_error["verified_basis"],
                )
            )

    build_verification = _dict(build.get("verification"))
    verify_totals = _dict(verify.get("totals"))
    token_costs = {
        name: _recorded_usage(build_verification.get(source))
        for name, source in REPAIR_STAGE_FIELDS.items()
    }
    token_values = [
        value
        for stage in token_costs.values()
        for field, value in stage.items()
        if field == "total_tokens" and value is not None
    ]
    gate = _dict(verify.get("gate"))
    final_test_passed = test_seed0.get("passed") if isinstance(test_seed0.get("passed"), bool) else None
    resume_failure_rounds = sum(
        _dict(_dict(round_value).get("resume")).get("success") is False
        for round_value in rounds
    )
    return {
        "model_dir": model_dir,
        "family_dir": family_dir,
        "model": model,
        "family": family,
        "input_paths": {
            "build": _relative(build_path, workspace_root),
            "verify": _relative(verify_path, workspace_root),
        },
        "step_cap": build.get("step_cap"),
        "build_total_cost_usd": _number(build.get("total_cost_usd")),
        "repair_rounds": {
            "failed_rounds": len(rounds),
            "failed_replay_rounds": len(rounds),
            "resume_failure_rounds": resume_failure_rounds,
            "resume_successful_rounds": len(rounds) - resume_failure_rounds,
            "rounds_with_exception_message": rounds_with_exception,
            "rounds_without_exception_message": len(rounds) - rounds_with_exception,
            "failure_entries_without_exception": null_failure_entries,
            "details": round_audits,
        },
        "outcome": {
            "unautomatable": verify.get("unautomatable"),
            "final_test_seed0_passed": final_test_passed,
            "final_test_seed0_error": test_error,
            "gate_bindings_passed": gate.get("bindings_passed"),
            "gate_bindings_total": gate.get("bindings_total"),
            "replays": verify.get("replays"),
            "refinements": verify.get("refinements"),
        },
        "repair_token_costs": token_costs,
        "repair_total_tokens_sum": sum(token_values) if len(token_values) == len(token_costs) else None,
        "verification_total_crosscheck": _verification_crosscheck(
            build_verification, verify_totals
        ),
        "verified_failure_evidence": {
            "exception_or_error_records": verified_errors,
            "gate_failures": gate_errors,
            "test_seed0": {
                "passed": final_test_passed,
                "error": test_error,
                "source": "t16_verify.test_seed0",
                "path": _relative(verify_path, workspace_root),
            },
        },
        "analyzer_explanations": [
            {
                "round": index,
                "failure_seed": _dict(round_value).get("failure_seed"),
                "cause": _clean_text(_dict(_dict(round_value).get("analysis")).get("cause")),
                "done": _clean_text(_dict(_dict(round_value).get("analysis")).get("done"), 260),
                "decision": _clean_text(_dict(_dict(round_value).get("analysis")).get("decision"), 80),
                "source": "recorded LLM analyzer fields in t16 verify.json",
                "verified": False,
            }
            for index, round_value in enumerate(rounds, start=1)
        ],
    }


def _discover_t16_cells(
    t16_root: Path,
    workspace_root: Path,
    problems: list[str],
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for build_path in sorted(t16_root.glob("*/*/build.json")):
        if ".spoiled-" in build_path.parent.name:
            continue
        verify_path = build_path.with_name("verify.json")
        if not verify_path.exists():
            problems.append(f"missing paired verify.json for {build_path}")
            continue
        cells.append(_t16_cell_audit(build_path, verify_path, workspace_root, problems))
    return cells


def _discover_episodes(
    episodes_root: Path,
    workspace_root: Path,
    problems: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    records: list[dict[str, Any]] = []
    state_count = 0
    trajectory_count = 0
    for state_path in sorted(episodes_root.glob("*/*/*/*/state.json")):
        state_count += 1
        if state_path.with_name("trajectory.jsonl").exists():
            trajectory_count += 1
        records.append(_episode_record(state_path, workspace_root, problems))
    return records, state_count, trajectory_count


def _aggregate_stage_usage(cells: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for stage_name in REPAIR_STAGE_FIELDS:
        stage_records = [cell["repair_token_costs"].get(stage_name, {}) for cell in cells]
        output[stage_name] = _sum_usage(stage_records)
    return output


def _family_cell_map(cells: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        grouped[str(cell.get("family"))].append(cell)
    for family in grouped:
        grouped[family].sort(key=lambda cell: str(cell.get("model")))
    return dict(sorted(grouped.items()))


def _episode_cell_summary(
    cell: dict[str, Any],
    episode_records: list[dict[str, Any]],
) -> dict[str, Any]:
    cell_records = [
        record
        for record in episode_records
        if record.get("model_dir") == cell.get("model_dir")
        and record.get("family_dir") == cell.get("family_dir")
    ]
    return {
        "episodes_root_match": len(cell_records),
        "by_condition": {
            mode: _episode_mode_summary(cell_records, mode)
            for mode in ("discover", "doc")
        },
        "failure_evidence": _episode_failure_evidence(cell_records),
        "stable_prefixes": _stable_prefixes(cell_records),
        "incomplete_status_reasons": dict(
            sorted(
                Counter(
                    record.get("stop_reason")
                    for record in cell_records
                    if record.get("status") != "done" and record.get("stop_reason")
                ).items()
            )
        ),
    }


def _cell_completed_rate(cell: dict[str, Any]) -> float | None:
    revision = _dict(cell.get("revision"))
    by_condition = _dict(revision.get("by_condition"))
    total = sum(
        int(_dict(by_condition.get(mode)).get("completed_with_boolean_outcome") or 0)
        for mode in ("discover", "doc")
    )
    success = sum(
        int(_dict(by_condition.get(mode)).get("success_count") or 0)
        for mode in ("discover", "doc")
    )
    return success / total if total else None


def _stable_positive(cell: dict[str, Any]) -> bool:
    prefixes = _dict(cell.get("revision", {}).get("stable_prefixes"))
    for key in ("discover", "doc", "cross_condition"):
        length = _dict(prefixes.get(key)).get("length")
        if isinstance(length, (int, float)) and length > 0:
            return True
    return False


def _stable_max(cell: dict[str, Any]) -> int | float | None:
    prefixes = _dict(cell.get("revision", {}).get("stable_prefixes"))
    lengths = [
        _dict(prefixes.get(key)).get("length")
        for key in ("discover", "doc", "cross_condition")
    ]
    lengths = [length for length in lengths if isinstance(length, (int, float))]
    return max(lengths) if lengths else None


def _candidate_cell_details(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for cell in cells:
        outcome = _dict(cell.get("outcome"))
        gate_passed = outcome.get("gate_bindings_passed")
        gate_total = outcome.get("gate_bindings_total")
        details.append(
            {
                "model": cell.get("model"),
                "failed_replay_rounds": _dict(cell.get("repair_rounds")).get("failed_replay_rounds"),
                "resume_failure_rounds": _dict(cell.get("repair_rounds")).get("resume_failure_rounds"),
                "unautomatable": outcome.get("unautomatable"),
                "seed0_test_passed": outcome.get("final_test_seed0_passed"),
                "gate": (
                    f"{gate_passed}/{gate_total}"
                    if gate_passed is not None and gate_total is not None
                    else None
                ),
                "completed_revision_success_rate": _cell_completed_rate(cell),
                "max_positive_stable_prefix": _stable_max(cell),
                "repair_total_tokens": cell.get("repair_total_tokens_sum"),
            }
        )
    return details


def _full_gate(cell: dict[str, Any]) -> bool:
    outcome = _dict(cell.get("outcome"))
    passed = outcome.get("gate_bindings_passed")
    total = outcome.get("gate_bindings_total")
    return isinstance(passed, int) and isinstance(total, int) and total > 0 and passed >= total


def _family_summary_and_candidates(
    families: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    deprioritized: list[dict[str, Any]] = []
    for family, cells in families.items():
        completed = sum(
            int(_dict(_dict(cell.get("revision")).get("by_condition").get(mode)).get("completed_with_boolean_outcome") or 0)
            for cell in cells
            for mode in ("discover", "doc")
        )
        successes = sum(
            int(_dict(_dict(cell.get("revision")).get("by_condition").get(mode)).get("success_count") or 0)
            for cell in cells
            for mode in ("discover", "doc")
        )
        final_pass_cells = [
            cell
            for cell in cells
            if _dict(cell.get("outcome")).get("final_test_seed0_passed") is True
        ]
        zero_round_pass_cells = [
            cell
            for cell in final_pass_cells
            if int(_dict(cell.get("repair_rounds")).get("failed_rounds") or 0) == 0
        ]
        stable_cells = [cell for cell in cells if _stable_positive(cell)]
        stable_passing_cells = [cell for cell in final_pass_cells if _stable_positive(cell)]
        success_rate = successes / completed if completed else None
        summary = {
            "family": family,
            "cell_count": len(cells),
            "models": [cell.get("model") for cell in cells],
            "completed_revision_episodes": completed,
            "successful_revision_episodes": successes,
            "completed_revision_success_rate": success_rate,
            "t16_final_test_pass_cells": len(final_pass_cells),
            "t16_zero_round_and_final_pass_cells": len(zero_round_pass_cells),
            "t16_failed_repair_rounds_total": sum(
                int(_dict(cell.get("repair_rounds")).get("failed_rounds") or 0)
                for cell in cells
            ),
            "cells_with_positive_stable_prefix_evidence": len(stable_cells),
        }
        summaries.append(summary)

        strong = (
            len(cells) >= 2
            and len(final_pass_cells) == len(cells)
            and len(zero_round_pass_cells) == len(cells)
            and all(_full_gate(cell) for cell in cells)
            and success_rate is not None
            and success_rate >= 0.8
        )
        conditional_cells = [
            cell
            for cell in final_pass_cells
            if int(_dict(cell.get("repair_rounds")).get("failed_rounds") or 0) <= 3
            and (_cell_completed_rate(cell) or 0) >= 0.8
            and _stable_positive(cell)
        ]
        if strong:
            candidates.append(
                {
                    "family": family,
                    "tier": "strong",
                    "candidate_cells": [cell.get("model") for cell in cells],
                    "cell_details": _candidate_cell_details(cells),
                    "basis": [
                        "both model cells pass the recorded seed-0 test",
                        "both model cells have zero recorded failed repair rounds",
                        "both model cells pass all recorded held-out gate bindings",
                        f"revision completed success rate is {success_rate:.3f} across {completed} episodes",
                    ],
                    "stable_prefix_note": (
                        "Exact stable prefixes are reported per cell below. The candidate does not "
                        "treat a weak prefix as a failure because index-based actions can diverge."
                    ),
                }
            )
        elif conditional_cells:
            candidates.append(
                {
                    "family": family,
                    "tier": "conditional",
                    "candidate_cells": [cell.get("model") for cell in conditional_cells],
                    "cell_details": _candidate_cell_details(conditional_cells),
                    "basis": [
                        "at least one model cell passes the recorded seed-0 test",
                        "the selected cell has at most three recorded repair rounds",
                        "the selected cell has at least 0.8 completed revision success and positive exact-prefix evidence",
                    ],
                    "caveat": (
                        "This is a model-specific pilot candidate. The other model cell, if present, "
                        "may be unresolved or may have materially different repair behavior. "
                        + (
                            "The selected cell still records unautomatable=true. "
                            if any(cell.get("outcome", {}).get("unautomatable") is True for cell in conditional_cells)
                            else ""
                        )
                    ),
                }
            )
        else:
            reasons: list[str] = []
            if not final_pass_cells:
                reasons.append("no model cell passes the recorded seed-0 test")
            if final_pass_cells and not stable_passing_cells:
                reasons.append("no positive exact-prefix evidence is available in a passing cell")
            if success_rate is not None and success_rate < 0.8:
                reasons.append(f"completed revision success rate is {success_rate:.3f}")
            if not reasons:
                reasons.append("does not meet the strong or conditional screening criteria")
            deprioritized.append({"family": family, "reasons": reasons})
    return summaries, candidates, deprioritized


def _markdown(audit: dict[str, Any]) -> str:
    inventory = _dict(audit.get("inventory"))
    lines = [
        "# Offline selective/local compilation audit",
        "",
        "This report is generated from local T16 `build.json`/`verify.json` records and the local `revision_20260913/episodes` tree. It performs no model, API, or emulator calls.",
        "",
        f"The corpus contains {inventory.get('t16_build_verify_cells')} T16 model/family cells across {len(audit.get('models', []))} models and {len(audit.get('families', []))} families. The revision tree contains {inventory.get('episode_trajectory_files')} trajectory files and {inventory.get('episode_state_files')} state files.",
        "",
        "## Cell readout",
        "",
        "Repair tokens are shown as `reactive_resume / analyzer / rewrite`, using the recorded `verification.resume_episodes`, `verification.analyzer`, and `verification.builder_refinements` totals. Program replays are zero-token records in this protocol.",
        "A failed repair round is a recorded replay round that entered the repair loop. JSON also reports whether the reactive resume itself returned success.",
        "",
        "| model | family | failed repair rounds | seed-0 test | gate | repair tokens | completed revision success | stable prefix evidence |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for cell in audit.get("cells", []):
        outcome = _dict(cell.get("outcome"))
        rounds = _dict(cell.get("repair_rounds"))
        revision = _dict(cell.get("revision"))
        by_condition = _dict(revision.get("by_condition"))
        completed = sum(int(_dict(by_condition.get(mode)).get("completed_with_boolean_outcome") or 0) for mode in ("discover", "doc"))
        successes = sum(int(_dict(by_condition.get(mode)).get("success_count") or 0) for mode in ("discover", "doc"))
        rate = f"{successes}/{completed}" if completed else "unknown"
        prefixes = _dict(revision.get("stable_prefixes"))
        prefix_bits = []
        for key in ("discover", "doc", "cross_condition"):
            summary = _dict(prefixes.get(key))
            value = summary.get("length")
            prefix_bits.append(f"{key}={value if value is not None else '?'}")
        token = cell.get("repair_token_costs", {})
        token_bits = [
            str(_dict(token.get(key)).get("total_tokens"))
            if _dict(token.get(key)).get("total_tokens") is not None
            else "?"
            for key in ("reactive_resume", "analyzer", "rewrite")
        ]
        gate_pass = outcome.get("gate_bindings_passed")
        gate_total = outcome.get("gate_bindings_total")
        gate = f"{gate_pass}/{gate_total}" if gate_pass is not None and gate_total is not None else "unknown"
        test = outcome.get("final_test_seed0_passed")
        test_text = "pass" if test is True else "fail" if test is False else "unknown"
        lines.append(
            f"| {cell.get('model')} | {cell.get('family')} | {rounds.get('failed_rounds', 0)} | {test_text} | {gate} | {' / '.join(token_bits)} | {rate} | {'; '.join(prefix_bits)} |"
        )

    lines.extend(["", "## Candidate families", ""])
    candidates = audit.get("candidate_families", [])
    if candidates:
        for candidate in candidates:
            cells = ", ".join(str(value) for value in candidate.get("candidate_cells", []))
            basis = "; ".join(str(value) for value in candidate.get("basis", []))
            lines.append(f"- **{candidate.get('family')}** ({candidate.get('tier')}, cells: {cells}). {basis}.")
            if candidate.get("caveat"):
                lines.append(f"  {candidate['caveat']}")
    else:
        lines.append("No family meets the screening criteria in this offline corpus.")

    lines.extend(["", "## Verified failure evidence", ""])
    lines.append("The following entries come from runner outcomes, replay details, gate details, or observed trajectory metadata. They are evidence of what was recorded, not a reconstruction of an unrecorded breakpoint.")
    for cell in audit.get("cells", []):
        failure = _dict(cell.get("verified_failure_evidence"))
        errors = failure.get("exception_or_error_records", [])
        episode_failures = _dict(cell.get("revision")).get("failure_evidence", [])
        if not errors and not episode_failures:
            continue
        message_counts = Counter(
            record.get("message") or "<no exception message>"
            for record in errors
        )
        messages = "; ".join(f"{msg} (x{count})" for msg, count in message_counts.most_common(4))
        extra = "" if len(message_counts) <= 4 else f"; +{len(message_counts) - 4} more messages"
        lines.append(f"- **{cell.get('model')} / {cell.get('family')}**: {len(_dict(cell.get('repair_rounds')).get('details', []))} repair rounds, {len(episode_failures)} completed revision failures. {messages}{extra}")

    lines.extend(["", "## Recorded analyzer explanations", ""])
    lines.append("Analyzer `cause` strings are retained as recorded LLM explanations. They are marked `verified: false` in JSON and are not counted as independent failure evidence.")
    for cell in audit.get("cells", []):
        explanations = cell.get("analyzer_explanations", [])
        if not explanations:
            continue
        examples = [item.get("cause") or "<empty cause>" for item in explanations[:2]]
        suffix = "" if len(explanations) <= 2 else f"; +{len(explanations) - 2} more recorded causes"
        lines.append(f"- **{cell.get('model')} / {cell.get('family')}**: " + " | ".join(examples) + suffix)

    lines.extend(["", "## Limitations", ""])
    for limitation in audit.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.extend(["", "The JSON file is the machine-readable audit and includes per-round errors, per-condition episode counts, exact-prefix summaries, and source paths.", ""])
    return "\n".join(lines)


def build_audit(workspace_root: Path) -> dict[str, Any]:
    results_root = workspace_root / "experimental-results" / "guiexp_android"
    t16_root = results_root / "t16_build"
    revision_root = results_root / "revision_20260913"
    episodes_root = revision_root / "episodes"
    problems: list[str] = []
    cells = _discover_t16_cells(t16_root, workspace_root, problems)
    episodes, state_count, trajectory_count = _discover_episodes(
        episodes_root, workspace_root, problems
    )
    for cell in cells:
        cell["revision"] = _episode_cell_summary(cell, episodes)
    families = _family_cell_map(cells)
    family_summaries, candidates, deprioritized = _family_summary_and_candidates(families)
    models = sorted({str(cell.get("model")) for cell in cells})
    family_names = sorted({str(cell.get("family")) for cell in cells})
    episode_statuses = Counter(str(record.get("status")) for record in episodes)
    episode_outcomes = Counter(
        "success" if record.get("success") is True else "failure" if record.get("success") is False else "unknown"
        for record in episodes
    )
    by_model_tokens: dict[str, Any] = {}
    for model in models:
        by_model_tokens[model] = _aggregate_stage_usage(
            [cell for cell in cells if cell.get("model") == model]
        )
    limitations = [
        "The revision corpus has incomplete and skipped pairs. Budget-stopped or skipped episodes are kept in status counts and are not counted as failures or successes.",
        "T16 verify.json records round-level analyzer text but omits per-call usage details in these artifacts. Stage token totals therefore come from the paired build.json verification totals.",
        "A stable prefix means exact canonical action JSON agreement across at least two completed trajectories. It is a conservative syntactic signal and does not prove that a local compiler can safely reuse the prefix.",
        "A completed trajectory with success=false often has no action exception. Its last observed activity and step are reported as the available location evidence, and no semantic breakpoint is invented.",
        "Analyzer causes may describe UI state or a likely cause that is absent from the trajectory record. They remain labeled as unverified recorded explanations.",
        "This audit does not estimate deployment accuracy, causal repair value, or savings from a selective compiler. Those require a new controlled experiment.",
    ]
    if problems:
        limitations.append(f"Input parsing reported {len(problems)} issue(s). See input_issues in the JSON.")
    audit: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": ANALYSIS_ID,
        "read_only": True,
        "api_calls": 0,
        "emulator_calls": 0,
        "source_scope": {
            "t16": "experimental-results/guiexp_android/t16_build/<model>/<family>/{build,verify}.json",
            "revision_episodes": "experimental-results/guiexp_android/revision_20260913/episodes/<model>/<family>/<seed>/<condition>/",
        },
        "models": models,
        "families": family_names,
        "inventory": {
            "expected_models": 2,
            "expected_families": 7,
            "expected_t16_cells": 14,
            "t16_build_verify_cells": len(cells),
            "episode_state_files": state_count,
            "episode_trajectory_files": trajectory_count,
            "episode_status_counts": dict(sorted(episode_statuses.items())),
            "episode_outcome_counts": dict(sorted(episode_outcomes.items())),
        },
        "repair_token_stage_definition": {
            "reactive_resume": "build.json verification.resume_episodes",
            "analyzer": "build.json verification.analyzer",
            "rewrite": "build.json verification.builder_refinements",
            "program_replays": "zero model tokens by the recorded T16 protocol",
        },
        "repair_token_totals": {
            "all_cells": _aggregate_stage_usage(cells),
            "by_model": by_model_tokens,
        },
        "cells": cells,
        "family_summaries": family_summaries,
        "candidate_families": candidates,
        "deprioritized_families": deprioritized,
        "limitations": limitations,
        "input_issues": problems,
    }
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="workspace containing experimental-results (defaults to the package's workspace)",
    )
    args = parser.parse_args(argv)
    script_path = Path(__file__).resolve()
    workspace_root = (args.workspace_root or script_path.parents[2]).resolve()
    audit = build_audit(workspace_root)
    output_dir = workspace_root / "experimental-results" / "guiexp_android" / "selective_20260915"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "offline_audit.json"
    markdown_path = output_dir / "offline_audit.md"
    json_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(audit), encoding="utf-8")
    print(
        f"Wrote {json_path} and {markdown_path} "
        f"({len(audit['cells'])} cells, {audit['inventory']['episode_trajectory_files']} trajectories)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
