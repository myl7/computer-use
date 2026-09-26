"""Pure contract validation for an explicitly verified program prefix.

This module does not execute actions and does not inspect a benchmark oracle.
It validates a compiler response that declares the last recorded training step
covered by a guarded prefix and an explicit reactive handoff policy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .selective_guard_evidence import validate_guard_evidence
from .selective_plan_contract import validate_plan


PREFIX_SCHEMA = "selective-prefix/1"
HANDOFF_POLICY = "reactive"
PREFIX_KEYS = frozenset(
    {"schema", "plan", "source_steps", "terminal_source_step", "handoff_policy"}
)


def _invalid(*errors: str, guard_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"valid": False, "errors": [str(error) for error in errors]}
    if guard_evidence is not None:
        result["guard_evidence"] = dict(guard_evidence)
    return result


def _alignment(source_steps: Mapping[str, int]) -> dict[str, Any]:
    return {
        "steps": {
            step_id: [{"source_step": source_step}]
            for step_id, source_step in source_steps.items()
        }
    }


def validate_prefix_response(
    value: Any,
    demonstrations: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a prefix wrapper and optionally its full-demo guard evidence.

    The return value is a detached, execution-ready description.  A valid
    result contains the runtime plan, the explicit alignment, its terminal
    source step, and the fixed reactive handoff policy.  Passing demonstrations
    is the only way this function evaluates empirical guard evidence.
    """

    if not isinstance(value, Mapping):
        return _invalid("prefix response must be an object")
    if set(value) != PREFIX_KEYS:
        return _invalid("prefix response keys must be exactly schema, plan, source_steps, terminal_source_step, handoff_policy")
    if value.get("schema") != PREFIX_SCHEMA:
        return _invalid(f"prefix response schema must be {PREFIX_SCHEMA}")
    if value.get("handoff_policy") != HANDOFF_POLICY:
        return _invalid("handoff_policy must be reactive")

    plan_report = validate_plan(value.get("plan"))
    if plan_report.get("valid") is not True:
        return _invalid(*(f"plan: {error}" for error in plan_report.get("errors", [])))
    plan = plan_report.get("plan")
    if not isinstance(plan, Mapping):
        return _invalid("validated plan is missing")
    plan_steps = list(plan.get("steps") or [])
    step_ids = [str(step.get("id")) for step in plan_steps if isinstance(step, Mapping)]

    source_steps = value.get("source_steps")
    if not isinstance(source_steps, Mapping):
        return _invalid("source_steps must be an object")
    if set(source_steps) != set(step_ids):
        return _invalid("source_steps must contain exactly one entry for every plan step id")
    ordered: list[int] = []
    normalized_source_steps: dict[str, int] = {}
    for step_id in step_ids:
        source_step = source_steps.get(step_id)
        if type(source_step) is not int or source_step <= 0:
            return _invalid(f"source_steps[{step_id!r}] must be a positive one-based integer")
        ordered.append(source_step)
        normalized_source_steps[step_id] = source_step
    if len(set(ordered)) != len(ordered):
        return _invalid("source_steps must not contain duplicate original training steps")
    if ordered != sorted(ordered):
        return _invalid("source_steps must be strictly increasing in plan step order")

    terminal = value.get("terminal_source_step")
    if type(terminal) is not int or terminal <= 0:
        return _invalid("terminal_source_step must be a positive one-based integer")
    if not ordered or terminal != ordered[-1]:
        return _invalid("terminal_source_step must equal the last covered source step")

    alignment = _alignment(normalized_source_steps)
    result: dict[str, Any] = {
        "valid": True,
        "schema": PREFIX_SCHEMA,
        "plan": dict(plan),
        "source_steps": normalized_source_steps,
        "terminal_source_step": terminal,
        "handoff_policy": HANDOFF_POLICY,
        "alignment": alignment,
    }
    if demonstrations is not None:
        try:
            guard_evidence = validate_guard_evidence(
                result["plan"], demonstrations, alignment=alignment
            )
        except Exception as exc:
            guard_evidence = {
                "status": "unknown",
                "steps": [],
                "source_indices": [],
                "errors": [f"guard evidence helper failed: {type(exc).__name__}"],
            }
        result["guard_evidence"] = guard_evidence
        if guard_evidence.get("status") != "valid":
            return {
                **_invalid(
                    "full-demo guard evidence is " + str(guard_evidence.get("status")),
                    guard_evidence=guard_evidence,
                ),
                "schema": PREFIX_SCHEMA,
                "plan": dict(plan),
                "source_steps": normalized_source_steps,
                "terminal_source_step": terminal,
                "handoff_policy": HANDOFF_POLICY,
                "alignment": alignment,
            }
    return result


__all__ = [
    "HANDOFF_POLICY",
    "PREFIX_KEYS",
    "PREFIX_SCHEMA",
    "validate_prefix_response",
]
