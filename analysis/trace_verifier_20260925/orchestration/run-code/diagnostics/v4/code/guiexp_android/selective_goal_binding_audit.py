"""Pure source-goal binding checks for recorded selective-plan traces.

The existing inverse validator remains a separately reported baseline.  These
helpers accept caller-supplied bindings, resolve the plan forward, and never
derive a binding from an action, target, or UI observation.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from . import selective_plan_contract as plan_contract
from . import selective_runtime as runtime
from . import selective_trace_compatibility as trace_compatibility
from .selective_ax_parser import parse_ax_tree

SCHEMA = "selective-goal-binding-audit/1"


def validate_candidate(value: Any, recorded_steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the prefix wrapper and contiguous source steps ``1..k``.

    This is structural admission only.  It deliberately does not call the
    inverse prefix or guard-evidence validators.
    """
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["candidate must be an object"]}
    required = {"schema", "plan", "source_steps", "terminal_source_step", "handoff_policy"}
    if set(value) != required:
        errors.append("candidate keys are not the frozen prefix keys")
    if value.get("schema") != "selective-prefix/1":
        errors.append("candidate schema must be selective-prefix/1")
    report = plan_contract.validate_plan(value.get("plan"))
    plan = report.get("plan") if report.get("valid") is True else None
    if plan is None:
        errors.extend(f"plan: {item}" for item in report.get("errors", []))
    ids = [step["id"] for step in (plan or {}).get("steps", [])]
    source = value.get("source_steps")
    ordered = [source[item] for item in ids] if isinstance(source, Mapping) and set(source) == set(ids) else []
    if not isinstance(source, Mapping) or set(source) != set(ids):
        errors.append("source_steps must contain exactly one entry per plan step id")
    elif any(type(item) is not int for item in ordered):
        errors.append("source_steps values must be exact integers")
    elif ordered != list(range(1, len(ids) + 1)):
        errors.append(f"source_steps must be contiguous 1..k, got {ordered!r}")
    elif len(ordered) > len(recorded_steps):
        errors.append("source_steps extend beyond the frozen training trace")
    else:
        for position, number in enumerate(ordered):
            record = recorded_steps[position]
            observed = record.get("step", record.get("source_step"))
            if type(observed) is not int or observed != number:
                errors.append(f"source step {number!r} is absent or out of order")
                break
    terminal = value.get("terminal_source_step")
    if type(terminal) is not int or not ordered or terminal != ordered[-1]:
        errors.append("terminal_source_step must equal the final source step")
    if value.get("handoff_policy") != "reactive":
        errors.append("handoff_policy must be reactive")
    return {
        "valid": not errors,
        "errors": errors,
        "plan": copy.deepcopy(plan) if not errors else None,
        "source_steps": dict(source) if isinstance(source, Mapping) else {},
    }


def _obs(record: Mapping[str, Any], side: str) -> Mapping[str, Any]:
    keys = ("pre_obs", "raw_pre", "pre") if side == "pre" else ("post_obs", "raw_post", "post")
    return next((record[name] for name in keys if isinstance(record.get(name), Mapping)), {})


def _state(selector: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]], bindings: Mapping[str, Any]) -> tuple[str, str | None]:
    hits, error = runtime._hits(dict(selector), nodes, bindings)
    if error:
        return "unknown", error
    return ("pass" if len(hits) == 1 else "missing" if not hits else "ambiguous"), None


def effect_after_rule(plan: Mapping[str, Any], bindings: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require every after selector in post and one nonpass selector in pre."""
    plan_steps = plan.get("steps") if isinstance(plan, Mapping) else None
    if not isinstance(plan_steps, list) or not plan_steps:
        return {"status": "invalid", "valid": False, "steps": [], "reason": "plan must contain at least one step"}
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(plan_steps):
        selectors = step.get("after") if isinstance(step, Mapping) else None
        if not isinstance(selectors, list) or not selectors:
            steps.append({"step_id": step.get("id") if isinstance(step, Mapping) else None, "status": "invalid", "reason": "after guards must be nonempty"})
            continue
        if index >= len(records):
            steps.append({"step_id": step.get("id"), "status": "unknown", "reason": "source step is absent"})
            continue
        try:
            pre = parse_ax_tree(_obs(records[index], "pre").get("ax_tree_text", ""))
            post = parse_ax_tree(_obs(records[index], "post").get("ax_tree_text", ""))
        except (AttributeError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            steps.append({"step_id": step.get("id"), "status": "unknown", "reason": str(exc).splitlines()[0][:360]})
            continue
        if not pre.get("complete") or not post.get("complete"):
            steps.append({"step_id": step.get("id"), "status": "unknown", "reason": "pre or post AX parse is incomplete"})
            continue
        detail = []
        for selector in selectors:
            before, before_error = _state(selector, pre.get("nodes", []), bindings)
            after, after_error = _state(selector, post.get("nodes", []), bindings)
            detail.append({"selector": copy.deepcopy(selector), "pre": before, "post": after, "pre_error": before_error, "post_error": after_error})
        post_ok = all(item["post"] == "pass" for item in detail)
        changed = any(item["pre"] != "pass" for item in detail)
        unknown = any(item["pre"] == "unknown" or item["post"] == "unknown" for item in detail)
        status = "valid" if post_ok and changed and not unknown else "unknown" if unknown else "invalid"
        steps.append({"step_id": step.get("id"), "status": status, "all_after_true_post": post_ok, "at_least_one_not_pass_pre": changed, "selectors": detail})
    status = "invalid" if any(item.get("status") == "invalid" for item in steps) else "unknown" if any(item.get("status") == "unknown" for item in steps) else "valid"
    return {"status": status, "valid": status == "valid", "steps": steps}


def forward_validate(plan: Mapping[str, Any], bindings: Mapping[str, Any], recorded_steps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve supplied bindings against the recorded source trace only."""
    trace = trace_compatibility.check_prefix(plan, bindings, recorded_steps)
    effect = effect_after_rule(plan, bindings, recorded_steps)
    if trace.get("status") == "compatible" and effect.get("status") == "valid":
        status, valid = "compatible", True
    elif effect.get("status") == "invalid":
        status, valid = "invalid", False
    elif trace.get("status") == "unknown" or effect.get("status") == "unknown":
        status, valid = "unknown", None
    else:
        status, valid = str(trace.get("status") or "diverged"), False
    return {
        "schema": SCHEMA,
        "scope": "source_trace_validation",
        "status": status,
        "valid": valid,
        "task_success_evaluated": False,
        "counterfactual_ui_success": None,
        "generalization_proof": False,
        "trace_compatibility": trace,
        "effect_specific_after": effect,
    }


__all__ = ["SCHEMA", "effect_after_rule", "forward_validate", "validate_candidate"]
