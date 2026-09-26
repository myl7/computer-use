"""Evidence-grounded, deterministic descriptor rendering for a selective plan.

The repair rule is deliberately small.  It learns a literal prefix and
suffix around an identity slot from verified local-action witnesses.  The
primary result is a deterministic ``render_rules`` mapping.  A descriptor
patched plan is retained only as an inspectable candidate.  The input is
already projected evidence supplied by the caller.  This module does not
read trajectories, evaluator bindings, or benchmark outcomes.  An identity
descriptor reference with a nonempty literal wrapper is already rendered and
causes repair to decline.  Empty wrappers retain the legacy repair behavior.
"""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

_DESCRIPTOR_FIELDS = ("description", "text", "hint")
_FIELD_ALIASES = {
    "text": ("text",),
    "hint": ("hint", "hint_text"),
    "description": ("description", "content_description"),
}
_EVIDENCE_FIELDS = (
    "step_id",
    "extracted_bindings",
    "local_action",
    "pre_elements",
    "after_guard_passed",
)
_VALUE_SPEC_KEYS = frozenset(("slot", "transform", "prefix", "suffix"))
_VALUE_SPEC_REQUIRED_KEYS = frozenset(("slot", "transform"))


def _collapse(value: Any) -> str:
    """Apply the same Unicode and whitespace normalization as the runtime."""

    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _normalize(value: Any) -> str:
    return _collapse(value).casefold()


def _detached(plan: Any) -> Any:
    try:
        return copy.deepcopy(plan)
    except Exception:  # noqa: BLE001 - malformed caller data must fail closed.
        # JSON plans are expected.  Keeping this fallback makes a malformed
        # caller object fail closed without returning its mutable reference.
        if isinstance(plan, Mapping):
            return dict(plan)
        return None


def _is_identity_ref(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and _VALUE_SPEC_REQUIRED_KEYS <= set(value) <= _VALUE_SPEC_KEYS
        and isinstance(value.get("slot"), str)
        and bool(value.get("slot", "").strip())
        and all(
            key not in value or isinstance(value[key], str)
            for key in ("prefix", "suffix")
        )
        and value.get("transform") == "identity"
    )


def _iter_slot_refs(value: Any, path: str):
    if isinstance(value, Mapping):
        if _VALUE_SPEC_REQUIRED_KEYS <= set(value) <= _VALUE_SPEC_KEYS:
            yield path, value
            return
        for key, child in value.items():
            yield from _iter_slot_refs(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_slot_refs(child, f"{path}[{index}]")


def _wrapped_identity_slots(plan: Mapping[str, Any]) -> set[str]:
    return {
        str(ref["slot"])
        for _path, ref in _iter_slot_refs(plan, "plan")
        if _is_identity_ref(ref) and (ref.get("prefix", "") or ref.get("suffix", ""))
    }


def _candidate_slots(plan: Mapping[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[tuple[str, str]]]:
    """Return direct descriptor identity uses and every slot reference."""

    candidates: dict[str, list[dict[str, Any]]] = {}
    refs: list[tuple[str, str]] = []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return candidates, refs
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        step_id = step.get("id")
        target = step.get("target")
        if isinstance(target, Mapping):
            for field in _DESCRIPTOR_FIELDS:
                value = target.get(field)
                if _is_identity_ref(value):
                    slot = str(value["slot"])
                    candidates.setdefault(slot, []).append(
                        {
                            "step_index": index,
                            "step_id": step_id,
                            "field": field,
                            "action_type": (step.get("action") or {}).get("action_type")
                            if isinstance(step.get("action"), Mapping)
                            else None,
                        }
                    )
        for path, value in _iter_slot_refs(step, f"steps[{index}]"):
            slot = value.get("slot")
            if isinstance(slot, str):
                refs.append((path, slot))
    return candidates, refs


def _validation(plan: Any) -> dict[str, Any]:
    """Run both structural validators without importing them at module load."""

    report: dict[str, Any] = {}
    try:
        from . import selective_pilot

        try:
            selective_pilot.validate_plan_local(plan)
        except Exception as exc:  # noqa: BLE001 - validator failure is a decline.
            report["pilot"] = {"valid": False, "errors": [str(exc)]}
        else:
            report["pilot"] = {"valid": True, "errors": []}
    except Exception as exc:  # noqa: BLE001 - validator import failure is a decline.
        report["pilot"] = {"valid": False, "errors": [f"validator import failed: {exc}"]}

    try:
        from . import selective_runtime

        try:
            result = selective_runtime.validate_plan(plan)
        except Exception as exc:  # noqa: BLE001 - validator failure is a decline.
            report["runtime"] = {"valid": False, "errors": [str(exc)]}
        else:
            if isinstance(result, Mapping) and result.get("valid") is False:
                errors = result.get("errors", [])
                report["runtime"] = {
                    "valid": False,
                    "errors": [str(item) for item in errors] if isinstance(errors, list) else [str(errors)],
                }
            else:
                report["runtime"] = {"valid": True, "errors": []}
    except Exception as exc:  # noqa: BLE001 - validator import failure is a decline.
        report["runtime"] = {"valid": False, "errors": [f"validator import failed: {exc}"]}
    return report


def _all_valid(validation: Mapping[str, Any]) -> bool:
    return all(
        isinstance(validation.get(name), Mapping) and validation[name].get("valid") is True
        for name in ("pilot", "runtime")
    )


def _base_result(plan: Any, reason: str, *, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    detached = _detached(plan)
    return {
        "status": "declined",
        "patched": False,
        "plan": detached,
        "primary_plan": detached,
        "patched_plan": detached,
        "descriptor_candidate": None,
        "render_rules": {},
        "patch": None,
        "report": {
            "reason": reason,
            "details": dict(details or {}),
        },
        "provenance": {
            "evidence_only": True,
            "raw_paths_read": False,
            "oracle_used": False,
            "evaluator_params_used": False,
            "fields_used": list(_EVIDENCE_FIELDS),
        },
    }


def _runtime_index(node: Mapping[str, Any], position: int) -> int | None:
    value = node.get("index", position)
    return value if type(value) is int and value >= 0 else None


def _binding_value(value: Any) -> str | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = _collapse(value)
    return text if text else None


def _observed_field(node: Mapping[str, Any], field: str) -> str | None:
    for alias in _FIELD_ALIASES[field]:
        if alias in node:
            value = node[alias]
            return value if isinstance(value, str) and value.strip() else None
    return None


def _display_parts(observed: str, binding: str, prefix: str, suffix: str) -> tuple[str, str]:
    """Keep observed casing for the generated slot description when possible."""

    raw_observed = _collapse(observed)
    raw_binding = _collapse(binding)
    raw_position = raw_observed.casefold().find(raw_binding.casefold())
    if raw_position >= 0:
        return (
            raw_observed[:raw_position],
            raw_observed[raw_position + len(raw_binding) :],
        )
    return prefix, suffix


def _render_description(old: str, field: str, prefix: str, suffix: str) -> str:
    base = old.strip().rstrip(".")
    if prefix and suffix:
        rendering = (
            f"the literal prefix {prefix!r} before and literal suffix {suffix!r} "
            "after the task value"
        )
    elif prefix:
        rendering = f"the literal prefix {prefix!r} before the task value"
    elif suffix:
        rendering = f"the literal suffix {suffix!r} after the task value"
    else:
        rendering = "the task value without a literal wrapper"
    return f"{base}. The exact {field} accessibility label uses {rendering}."


def _decline_with_validation(
    result: dict[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    result["report"]["validation"] = copy.deepcopy(dict(validation))
    return result


def repair_descriptor(
    plan: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Infer and apply one safe identity-descriptor repair.

    ``evidence`` must contain projected local-action witnesses.  Only a
    witness whose action type and current pre-state index are grounded, and
    whose ``after_guard_passed`` value is exactly ``True``, contributes to the
    inferred rendering.  The returned primary ``plan`` is always detached
    from the input and remains unchanged on success.  A declined result also
    leaves the plan unchanged.  Callers should render each extracted binding
    exactly once with ``render_rules`` before resolving the original plan.
    """

    if not isinstance(plan, Mapping):
        return _base_result(plan, "invalid_plan", details={"message": "plan must be a mapping"})
    detached_original = _detached(plan)
    if not isinstance(detached_original, dict):
        return _base_result(plan, "invalid_plan", details={"message": "plan could not be detached"})

    original_validation = _validation(detached_original)
    if not _all_valid(original_validation):
        return _decline_with_validation(
            _base_result(plan, "invalid_plan"), original_validation
        )
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes, bytearray)):
        return _decline_with_validation(
            _base_result(plan, "malformed_evidence", details={"message": "evidence must be a sequence"}),
            original_validation,
        )

    candidates, refs = _candidate_slots(detached_original)
    if not candidates:
        return _decline_with_validation(
            _base_result(plan, "no_identity_descriptor_slot"), original_validation
        )
    already_wrapped = sorted(_wrapped_identity_slots(detached_original) & set(candidates))
    if already_wrapped:
        return _decline_with_validation(
            _base_result(
                plan,
                "already_wrapped_reference",
                details={"slots": already_wrapped},
            ),
            original_validation,
        )
    if len(candidates) != 1:
        return _decline_with_validation(
            _base_result(
                plan,
                "multiple_candidate_slots",
                details={"candidate_count": len(candidates)},
            ),
            original_validation,
        )
    slot, uses = next(iter(candidates.items()))
    fields = {str(use["field"]) for use in uses}
    if len(fields) != 1:
        return _decline_with_validation(
            _base_result(plan, "multiple_descriptor_fields", details={"field_count": len(fields)}),
            original_validation,
        )
    field = next(iter(fields))
    allowed_paths = {
        f"steps[{use['step_index']}].target.{field}" for use in uses
    }
    bad_uses = [path for path, ref_slot in refs if ref_slot == slot and path not in allowed_paths]
    if bad_uses:
        return _decline_with_validation(
            _base_result(
                plan,
                "slot_used_outside_same_descriptor_field",
                details={"reference_count": len(bad_uses)},
            ),
            original_validation,
        )

    use_by_step = {str(use["step_id"]): use for use in uses}
    step_by_id = {
        str(step.get("id")): step
        for step in detached_original.get("steps", [])
        if isinstance(step, Mapping) and isinstance(step.get("id"), str)
    }
    witnesses: list[dict[str, str]] = []
    unverified = 0
    for row in evidence:
        if not isinstance(row, Mapping):
            return _decline_with_validation(
                _base_result(plan, "malformed_evidence", details={"message": "row must be a mapping"}),
                original_validation,
            )
        step_id = row.get("step_id")
        if not isinstance(step_id, str) or step_id not in step_by_id:
            return _decline_with_validation(
                _base_result(plan, "malformed_evidence", details={"message": "unknown step id"}),
                original_validation,
            )
        if step_id not in use_by_step:
            continue
        if row.get("after_guard_passed") is not True:
            unverified += 1
            continue

        use = use_by_step[step_id]
        bindings = row.get("extracted_bindings")
        action = row.get("local_action")
        pre_elements = row.get("pre_elements")
        if not isinstance(bindings, Mapping) or not isinstance(action, Mapping):
            return _decline_with_validation(
                _base_result(plan, "malformed_evidence", details={"step_id": step_id}),
                original_validation,
            )
        binding = _binding_value(bindings.get(slot))
        if binding is None:
            return _decline_with_validation(
                _base_result(plan, "empty_binding", details={"step_id": step_id}),
                original_validation,
            )
        if action.get("action_type") != use.get("action_type"):
            return _decline_with_validation(
                _base_result(plan, "action_type_mismatch", details={"step_id": step_id}),
                original_validation,
            )
        target_index = action.get("index")
        if type(target_index) is not int or target_index < 0:
            return _decline_with_validation(
                _base_result(plan, "invalid_local_index", details={"step_id": step_id}),
                original_validation,
            )
        if not isinstance(pre_elements, Sequence) or isinstance(pre_elements, (str, bytes, bytearray)):
            return _decline_with_validation(
                _base_result(plan, "malformed_pre_elements", details={"step_id": step_id}),
                original_validation,
            )
        matching: list[Mapping[str, Any]] = []
        for position, node in enumerate(pre_elements):
            if isinstance(node, Mapping) and _runtime_index(node, position) == target_index:
                matching.append(node)
        if len(matching) != 1:
            return _decline_with_validation(
                _base_result(
                    plan,
                    "ambiguous_local_index",
                    details={"step_id": step_id, "match_count": len(matching)},
                ),
                original_validation,
            )
        observed = _observed_field(matching[0], field)
        if observed is None:
            return _decline_with_validation(
                _base_result(plan, "missing_observed_descriptor", details={"step_id": step_id}),
                original_validation,
            )
        observed_norm = _normalize(observed)
        binding_norm = _normalize(binding)
        occurrence_count = observed_norm.count(binding_norm) if binding_norm else 0
        if occurrence_count != 1:
            return _decline_with_validation(
                _base_result(
                    plan,
                    "binding_not_observed_once",
                    details={"step_id": step_id, "occurrence_count": occurrence_count},
                ),
                original_validation,
            )
        position = observed_norm.find(binding_norm)
        prefix = observed_norm[:position]
        suffix = observed_norm[position + len(binding_norm) :]
        display_prefix, display_suffix = _display_parts(observed, binding, prefix, suffix)
        witnesses.append(
            {
                "step_id": step_id,
                "binding": binding_norm,
                "prefix": prefix,
                "suffix": suffix,
                "display_prefix": display_prefix,
                "display_suffix": display_suffix,
            }
        )

    distinct_bindings = {witness["binding"] for witness in witnesses}
    if len(distinct_bindings) < 2:
        return _decline_with_validation(
            _base_result(
                plan,
                "insufficient_distinct_bindings",
                details={
                    "verified_witness_count": len(witnesses),
                    "distinct_binding_count": len(distinct_bindings),
                    "unverified_count": unverified,
                },
            ),
            original_validation,
        )
    prefixes = {witness["prefix"] for witness in witnesses}
    suffixes = {witness["suffix"] for witness in witnesses}
    if len(prefixes) != 1 or len(suffixes) != 1:
        return _decline_with_validation(
            _base_result(
                plan,
                "inconsistent_rendering",
                details={"prefix_count": len(prefixes), "suffix_count": len(suffixes)},
            ),
            original_validation,
        )
    binding_norms = distinct_bindings
    if any(
        any(value and value in binding_norms for value in (witness["prefix"], witness["suffix"]))
        for witness in witnesses
    ):
        return _decline_with_validation(
            _base_result(plan, "rendering_contains_binding"), original_validation
        )

    old_description = detached_original.get("slots", {}).get(slot)
    if not isinstance(old_description, str) or not old_description.strip():
        return _decline_with_validation(
            _base_result(plan, "invalid_slot_description"), original_validation
        )
    new_description = _render_description(
        old_description,
        field,
        witnesses[0]["display_prefix"],
        witnesses[0]["display_suffix"],
    )
    patched = _detached(detached_original)
    patched["slots"][slot] = new_description
    patched_validation = _validation(patched)
    if not _all_valid(patched_validation):
        return _decline_with_validation(
            _base_result(plan, "patched_plan_invalid"), patched_validation
        )

    render_rules = {
        slot: {
            "prefix": witnesses[0]["display_prefix"],
            "suffix": witnesses[0]["display_suffix"],
        }
    }
    patch = {
        "slot": slot,
        "field": field,
        "path": f"slots.{slot}",
        "before": old_description,
        "after": new_description,
        "prefix": next(iter(prefixes)),
        "suffix": next(iter(suffixes)),
        "render_rule": render_rules[slot],
        "primary": "render_rules",
    }
    result = {
        "status": "patched",
        "patched": True,
        "plan": _detached(detached_original),
        "primary_plan": _detached(detached_original),
        "patched_plan": patched,
        "descriptor_candidate": patched,
        "render_rules": render_rules,
        "patch": patch,
        "report": {
            "reason": "verified_identity_descriptor_prefix_suffix",
            "step_ids": [use["step_id"] for use in uses],
            "witness_count": len(witnesses),
            "distinct_binding_count": len(distinct_bindings),
            "unverified_count": unverified,
            "validation": patched_validation,
        },
        "provenance": {
            "evidence_only": True,
            "raw_paths_read": False,
            "oracle_used": False,
            "evaluator_params_used": False,
            "fields_used": list(_EVIDENCE_FIELDS),
            "ignored_fields": ["final_success", "oracle", "evaluator_params"],
        },
    }
    return result


def render_bound_value(value: Any, rule: Mapping[str, Any]) -> str:
    """Apply one verified literal rendering rule to one raw binding value."""

    if not isinstance(rule, Mapping):
        raise TypeError("render rule must be a mapping")
    prefix = rule.get("prefix")
    suffix = rule.get("suffix")
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise TypeError("render rule prefix and suffix must be strings")
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("raw binding must be a scalar")
    text = _collapse(value)
    if not text:
        raise ValueError("raw binding must be nonempty")
    return f"{prefix}{text}{suffix}"


def render_bindings(
    bindings: Mapping[str, Any],
    render_rules: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return detached bindings after applying each selected rule once."""

    if not isinstance(bindings, Mapping):
        raise TypeError("bindings must be a mapping")
    if not isinstance(render_rules, Mapping):
        raise TypeError("render_rules must be a mapping")
    rendered = copy.deepcopy(dict(bindings))
    for slot, rule in render_rules.items():
        if slot in rendered:
            rendered[slot] = render_bound_value(rendered[slot], rule)
    return rendered


# A short alias keeps callers independent of the implementation name.
repair_plan = repair_descriptor


__all__ = [
    "render_bindings",
    "render_bound_value",
    "repair_descriptor",
    "repair_plan",
]
