"""Offline compatibility checking for a recorded guarded-plan prefix.

The checker validates a candidate plan against already recorded pre/post AX
snapshots and an already supplied goal binding.  It never derives bindings
from an expected action, executes an action, or consults an oracle.  Its
result is recorded-trajectory compatibility only.  It is not a counterfactual
UI success or generalization test.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from .selective_ax_parser import parse_ax_tree
from .selective_runtime import _guards, resolve_step, validate_plan

SCHEMA = "selective-trace-compatibility/1"
_QUERIED_ACTION_FIELDS = ("action_type", "index", "text", "app_name", "direction")
_SCALAR_TYPES = (str, int, float, bool)
_VALUE_SPEC_KEYS = frozenset(("slot", "transform", "prefix", "suffix"))
_VALUE_SPEC_REQUIRED_KEYS = frozenset(("slot", "transform"))


class TraceCompatibilityError(ValueError):
    """Reserved error type for callers that want to identify this checker."""


def _scalar(value: Any) -> bool:
    return type(value) in _SCALAR_TYPES


def _detach(value: Any) -> Any:
    return copy.deepcopy(value)


def _refs(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        if (
            _VALUE_SPEC_REQUIRED_KEYS <= set(value) <= _VALUE_SPEC_KEYS
            and isinstance(value.get("slot"), str)
        ):
            return [value["slot"]]
        found: list[str] = []
        for child in value.values():
            found.extend(_refs(child))
        return found
    if isinstance(value, list):
        found = []
        for child in value:
            found.extend(_refs(child))
        return found
    return []


def _plan_refs(plan: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    for step in plan.get("steps", []):
        if isinstance(step, Mapping):
            for key in ("action", "target", "before", "after"):
                found.update(_refs(step.get(key)))
    return found


def _invalid_result(reason: str, *, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "invalid_input",
        "scope": "recorded_trajectory_compatibility",
        "counterfactual_ui_success": None,
        "task_success_evaluated": False,
        "generalization_proof": False,
        "prefix_steps_matched": 0,
        "first_divergence": {"kind": reason, "details": dict(details or {})},
        "steps": [],
        "limitations": [
            "No binding is inferred from an expected action.",
            "No action or UI state is executed.",
            "This result is not counterfactual UI success or generalization evidence.",
        ],
    }


def _validate_inputs(
    plan: Any,
    bindings: Any,
    recorded_steps: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[Mapping[str, Any]] | None, dict[str, Any] | None]:
    if not isinstance(plan, Mapping):
        return None, None, None, _invalid_result("invalid_plan", details={"message": "plan must be a mapping"})
    detached_plan = _detach(plan)
    if not isinstance(detached_plan, dict):
        return None, None, None, _invalid_result("invalid_plan", details={"message": "plan could not be detached"})
    validation = validate_plan(detached_plan)
    if not isinstance(validation, Mapping) or validation.get("valid") is not True:
        errors = validation.get("errors", []) if isinstance(validation, Mapping) else []
        return None, None, None, _invalid_result(
            "invalid_plan",
            details={"errors": [str(error) for error in errors] if isinstance(errors, list) else [str(errors)]},
        )
    if not isinstance(bindings, Mapping):
        return None, None, None, _invalid_result("invalid_bindings", details={"message": "bindings must be a mapping"})
    detached_bindings = _detach(dict(bindings))
    if any(not _scalar(value) for value in detached_bindings.values()):
        return None, None, None, _invalid_result(
            "invalid_bindings", details={"message": "binding values must be scalar"}
        )
    required_slots = _plan_refs(detached_plan)
    missing = sorted(slot for slot in required_slots if slot not in detached_bindings)
    if missing:
        return None, None, None, _invalid_result(
            "missing_binding", details={"slots": missing}
        )
    if not isinstance(recorded_steps, Sequence) or isinstance(recorded_steps, (str, bytes, bytearray)):
        return None, None, None, _invalid_result(
            "invalid_trace", details={"message": "recorded_steps must be a sequence"}
        )
    detached_steps: list[Mapping[str, Any]] = []
    for index, record in enumerate(recorded_steps):
        if not isinstance(record, Mapping):
            return None, None, None, _invalid_result(
                "invalid_trace", details={"position": index, "message": "record must be a mapping"}
            )
        detached_steps.append(_detach(dict(record)))
    return detached_plan, detached_bindings, detached_steps, None


def _record_step_number(record: Mapping[str, Any]) -> Any:
    if "source_step" in record:
        return record["source_step"]
    return record.get("step")


def _record_action(record: Mapping[str, Any]) -> Any:
    for key in ("action", "actual_action", "recorded_action"):
        if key in record:
            return record[key]
    return None


def _record_observation(record: Mapping[str, Any], side: str) -> Any:
    keys = ("pre_obs", "raw_pre", "pre") if side == "pre" else ("post_obs", "raw_post", "post")
    for key in keys:
        if key in record:
            return record[key]
    return None


def _parse_observation(observation: Any) -> dict[str, Any]:
    if not isinstance(observation, Mapping) or "ax_tree_text" not in observation:
        return {
            "nodes": [],
            "complete": False,
            "completeness": "partial",
            "markers_seen": 0,
            "nodes_parsed": 0,
            "truncated": False,
            "issues": [{"kind": "missing_ax_tree_text"}],
        }
    return parse_ax_tree(observation.get("ax_tree_text"))


def _parse_sidecar(parsed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "complete": bool(parsed.get("complete")),
        "completeness": parsed.get("completeness"),
        "markers_seen": parsed.get("markers_seen", 0),
        "nodes_parsed": parsed.get("nodes_parsed", 0),
        "truncated": bool(parsed.get("truncated")),
        "issues": copy.deepcopy(parsed.get("issues") or []),
    }


def _normalized_action(action: Mapping[str, Any]) -> dict[str, Any]:
    """Keep every supported native field for an exact action comparison."""

    normalized: dict[str, Any] = {}
    for field in _QUERIED_ACTION_FIELDS:
        if field not in action:
            continue
        normalized[field] = copy.deepcopy(action[field])
    return normalized


def _action_difference(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
    expected_normalized = _normalized_action(expected)
    actual_normalized = _normalized_action(actual)
    if expected_normalized.get("index") != actual_normalized.get("index"):
        return "target_mismatch", {
            "resolved": expected_normalized,
            "recorded": actual_normalized,
            "field": "index",
        }
    if expected_normalized != actual_normalized:
        differing = sorted(
            field
            for field in set(expected_normalized) | set(actual_normalized)
            if expected_normalized.get(field) != actual_normalized.get(field)
        )
        return "action_argument_mismatch", {
            "resolved": expected_normalized,
            "recorded": actual_normalized,
            "fields": differing,
        }
    return None, {
        "resolved": expected_normalized,
        "recorded": actual_normalized,
    }


def _guard_failure(report: Mapping[str, Any]) -> bool:
    return report.get("ok") is not True


def _guard_states(report: Mapping[str, Any]) -> list[str]:
    return [
        str(guard.get("state"))
        for guard in report.get("guards", [])
        if isinstance(guard, Mapping)
    ]


def _step_detail(
    *,
    index: int,
    step: Mapping[str, Any] | None,
    source_step: Any,
) -> dict[str, Any]:
    return {
        "plan_index": index,
        "source_step": source_step,
        "step_id": step.get("id") if isinstance(step, Mapping) else None,
    }


def check_prefix(
    plan: Mapping[str, Any],
    bindings: Mapping[str, Any],
    recorded_steps: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Check only the contiguous leading plan prefix against recorded states.

    Bindings are caller-supplied goal extraction.  The checker resolves the
    plan with those bindings, then compares the resolved native action with
    the recorded action.  It never repairs a wrong binding from the trace.
    """

    detached_plan, detached_bindings, records, invalid = _validate_inputs(
        plan, bindings, recorded_steps
    )
    if invalid is not None:
        return invalid
    assert detached_plan is not None and detached_bindings is not None and records is not None
    plan_steps = detached_plan.get("steps")
    if not isinstance(plan_steps, list):
        return _invalid_result("invalid_plan", details={"message": "plan steps must be a list"})

    details: list[dict[str, Any]] = []
    matched = 0
    first: dict[str, Any] | None = None
    for index, step in enumerate(plan_steps):
        expected_source = index + 1
        detail = _step_detail(index=index, step=step, source_step=expected_source)
        if index >= len(records):
            detail.update({"status": "diverged", "divergence": {"kind": "trace_missing"}})
            details.append(detail)
            first = {
                "kind": "trace_missing",
                "plan_index": index,
                "source_step": expected_source,
                "step_id": step.get("id"),
                "details": {"message": "recorded source step is absent"},
            }
            break
        record = records[index]
        observed_source = _record_step_number(record)
        if type(observed_source) is not int or observed_source != expected_source:
            detail.update(
                {
                    "status": "diverged",
                    "observed_source_step": observed_source,
                    "divergence": {"kind": "trace_missing"},
                }
            )
            details.append(detail)
            first = {
                "kind": "trace_missing",
                "plan_index": index,
                "source_step": expected_source,
                "step_id": step.get("id"),
                "details": {"observed_source_step": observed_source},
            }
            break

        actual_action = _record_action(record)
        pre_observation = _record_observation(record, "pre")
        post_observation = _record_observation(record, "post")
        pre_parse = _parse_observation(pre_observation)
        post_parse = _parse_observation(post_observation)
        detail["parse"] = {
            "pre": _parse_sidecar(pre_parse),
            "post": _parse_sidecar(post_parse),
        }
        if not pre_parse.get("complete") or not post_parse.get("complete"):
            detail.update(
                {
                    "status": "unknown",
                    "divergence": {"kind": "parse_unknown"},
                }
            )
            details.append(detail)
            first = {
                "kind": "parse_unknown",
                "plan_index": index,
                "source_step": expected_source,
                "step_id": step.get("id"),
                "details": {"message": "pre or post AX parse is incomplete"},
            }
            break
        if not isinstance(actual_action, Mapping):
            detail.update(
                {
                    "status": "diverged",
                    "divergence": {"kind": "trace_missing"},
                }
            )
            details.append(detail)
            first = {
                "kind": "trace_missing",
                "plan_index": index,
                "source_step": expected_source,
                "step_id": step.get("id"),
                "details": {"message": "recorded action is absent"},
            }
            break

        pre_nodes = pre_parse.get("nodes", [])
        post_nodes = post_parse.get("nodes", [])
        before_report = _guards(step.get("before", []), pre_nodes, detached_bindings)
        after_report = _guards(step.get("after", []), post_nodes, detached_bindings)
        detail["before_guard"] = copy.deepcopy(before_report)
        detail["after_guard"] = copy.deepcopy(after_report)
        try:
            resolved_action = resolve_step(step, pre_nodes, detached_bindings)
        except (TypeError, ValueError) as exc:
            resolved_action = None
            resolution_error = str(exc)
        else:
            resolution_error = None
        detail["resolution"] = {
            "resolved_action": copy.deepcopy(resolved_action),
            "error": resolution_error,
        }
        detail["recorded_action"] = copy.deepcopy(dict(actual_action))

        if resolved_action is None and step.get("target") is not None:
            kind = "target_mismatch"
            reason = "target selector did not resolve to one pre-state node"
            divergence_details = {"guard_states": _guard_states(before_report)}
        else:
            kind, action_details = _action_difference(
                resolved_action or {"action_type": step.get("action", {}).get("action_type")},
                actual_action,
            )
            detail["action_comparison"] = action_details
            reason = "resolved action differs from recorded action" if kind else ""
            divergence_details = action_details
        if kind is None and (_guard_failure(before_report) or _guard_failure(after_report)):
            kind = "guard_not_applicable"
            reason = "before or after guard did not hold in the recorded states"
            divergence_details = {
                "before_states": _guard_states(before_report),
                "after_states": _guard_states(after_report),
            }
        if kind is not None:
            detail.update(
                {
                    "status": "diverged",
                    "divergence": {"kind": kind, "reason": reason, "details": divergence_details},
                }
            )
            details.append(detail)
            first = {
                "kind": kind,
                "plan_index": index,
                "source_step": expected_source,
                "step_id": step.get("id"),
                "details": divergence_details,
            }
            break

        detail["status"] = "matched"
        details.append(detail)
        matched += 1

    status = "compatible" if first is None else "unknown" if first["kind"] == "parse_unknown" else "diverged"
    return {
        "schema": SCHEMA,
        "status": status,
        "scope": "recorded_trajectory_compatibility",
        "counterfactual_ui_success": None,
        "task_success_evaluated": False,
        "generalization_proof": False,
        "prefix_steps_matched": matched,
        "first_divergence": first,
        "steps": details,
        "limitations": [
            "Bindings are caller-supplied and are never inferred from expected or recorded actions.",
            "Only the matching contiguous prefix is evaluated. Later states are not used after divergence.",
            "Missing or alternative paths are evidence limits, not task failures.",
            "This result is recorded-trajectory compatibility, not counterfactual UI success or generalization proof.",
        ],
    }


__all__ = ["TraceCompatibilityError", "check_prefix"]
