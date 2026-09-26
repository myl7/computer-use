"""Offline conservative derivation of a dense verified prefix.

The helper consumes an already saved compiler response and complete recorded
demos.  It never calls a model, executes a UI action, or uses an evaluator
oracle.  A derived prefix keeps only the maximal leading source-step run
``1, 2, ..., k`` and revalidates that prefix against the complete demos.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .selective_prefix import PREFIX_SCHEMA, validate_prefix_response


DENSE_PREFIX_SCHEMA = "selective-dense-prefix/1"
_VALUE_SPEC_KEYS = frozenset(("slot", "transform", "prefix", "suffix"))
_VALUE_SPEC_REQUIRED_KEYS = frozenset(("slot", "transform"))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _failure(message: str, **extra: Any) -> dict[str, Any]:
    return {"valid": False, "status": "invalid", "errors": [message], **extra}


def _collect_slot_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, Mapping):
        if _VALUE_SPEC_REQUIRED_KEYS <= set(value) <= _VALUE_SPEC_KEYS:
            slot = value.get("slot")
            if isinstance(slot, str):
                refs.add(slot)
            return
        for child in value.values():
            _collect_slot_refs(child, refs)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _collect_slot_refs(child, refs)


def _prefix_wrapper(
    plan: Mapping[str, Any],
    source_steps: Mapping[str, int],
    terminal_source_step: int,
) -> dict[str, Any]:
    retained_steps = list(plan.get("steps") or [])[:terminal_source_step]
    retained_plan = {**dict(plan), "steps": retained_steps}
    slots = plan.get("slots")
    if isinstance(slots, Mapping):
        refs: set[str] = set()
        _collect_slot_refs(retained_steps, refs)
        retained_plan["slots"] = {
            name: description
            for name, description in slots.items()
            if name in refs
        }
    return {
        "schema": PREFIX_SCHEMA,
        "plan": retained_plan,
        "source_steps": dict(source_steps),
        "terminal_source_step": terminal_source_step,
        "handoff_policy": "reactive",
    }


def derive_dense_prefix(
    candidate: Mapping[str, Any],
    demonstrations: Sequence[Mapping[str, Any]],
    *,
    source_reference: Mapping[str, Any] | None = None,
    source_raw_sha256: str | None = None,
) -> dict[str, Any]:
    """Derive and fully revalidate the maximal dense leading prefix.

    ``candidate`` is the parsed saved compiler response.  The source response
    itself is never rewritten.  ``source_raw_sha256`` should be the hash of the
    immutable saved raw response file when available.
    """

    if not isinstance(candidate, Mapping) or set(candidate) != {
        "schema", "plan", "source_steps", "terminal_source_step", "handoff_policy"
    }:
        return _failure("source prefix response has unsupported or missing wrapper keys")
    if candidate.get("schema") != PREFIX_SCHEMA or candidate.get("handoff_policy") != "reactive":
        return _failure("source prefix response has an invalid schema or handoff policy")
    plan = candidate.get("plan")
    source_steps = candidate.get("source_steps")
    if not isinstance(plan, Mapping) or not isinstance(source_steps, Mapping):
        return _failure("source prefix response lacks a plan or source-step mapping")
    if set(plan) != {"schema", "slots", "steps"} or plan.get("schema") != "selective-plan/1":
        return _failure("source prefix plan has unsupported or missing keys")
    plan_steps = plan.get("steps")
    if not isinstance(plan_steps, list) or not plan_steps:
        return _failure("source prefix plan steps are missing")
    ordered_ids = [str(step.get("id")) for step in plan_steps if isinstance(step, Mapping)]
    if len(ordered_ids) != len(plan_steps) or len(set(ordered_ids)) != len(ordered_ids):
        return _failure("source prefix plan step IDs are missing or duplicated")
    if set(source_steps) != set(ordered_ids):
        return _failure("source_steps must contain exactly one entry for every plan step id")
    ordered_values = [source_steps.get(step_id) for step_id in ordered_ids]
    if any(type(value) is not int or value <= 0 for value in ordered_values):
        return _failure("source_steps must contain positive one-based integers")
    if ordered_values != sorted(ordered_values) or len(set(ordered_values)) != len(ordered_values):
        return _failure("source_steps must be strictly increasing without duplicates")
    if type(candidate.get("terminal_source_step")) is not int or candidate["terminal_source_step"] != ordered_values[-1]:
        return _failure("terminal_source_step must equal the final source_steps value")

    dense_count = 0
    cut_reason: dict[str, Any] | None = None
    for position, value in enumerate(ordered_values, start=1):
        if value != position:
            cut_reason = {
                "plan_position": position,
                "expected_source_step": position,
                "observed_source_step": value,
                "reason": "first_nonconsecutive_source_step",
            }
            break
        prefix_ids = ordered_ids[:position]
        prefix_steps = {step_id: int(source_steps[step_id]) for step_id in prefix_ids}
        structural = validate_prefix_response(
            _prefix_wrapper(plan, prefix_steps, position)
        )
        if structural.get("valid") is not True:
            errors = structural.get("errors") or ["prefix plan validation failed"]
            cut_reason = {
                "plan_position": position,
                "source_step": value,
                "reason": "first_invalid_plan_step",
                "error": str(errors[0]),
            }
            break
        guarded = validate_prefix_response(
            _prefix_wrapper(plan, prefix_steps, position), demonstrations
        )
        if guarded.get("valid") is not True:
            guard_report = guarded.get("guard_evidence")
            cut_reason = {
                "plan_position": position,
                "source_step": value,
                "reason": "first_guard_evidence_" + str((guard_report or {}).get("status", "invalid")),
                "errors": list((guard_report or {}).get("errors") or guarded.get("errors") or [])[:8],
            }
            break
        dense_count = position
    if dense_count < 1:
        return _failure(
            "source prefix has no valid dense leading source step beginning at one",
            cut_reason=cut_reason,
        )

    retained_ids = ordered_ids[:dense_count]
    retained_steps = {step_id: int(source_steps[step_id]) for step_id in retained_ids}
    derived_candidate = _prefix_wrapper(plan, retained_steps, dense_count)
    validated = validate_prefix_response(derived_candidate, demonstrations)
    guard_evidence = validated.get("guard_evidence")
    if validated.get("valid") is not True or not isinstance(guard_evidence, Mapping):
        return _failure(
            "derived dense prefix lacks valid full-demo guard evidence",
            cut_reason=cut_reason,
            guard_evidence=dict(guard_evidence) if isinstance(guard_evidence, Mapping) else None,
        )

    original_steps = {step_id: source_steps[step_id] for step_id in ordered_ids}
    metadata = dict(source_reference or {})
    metadata.setdefault("source_plan_sha256", _sha256_bytes(_canonical(plan).encode("utf-8")))
    result: dict[str, Any] = {
        "valid": True,
        "status": "derived",
        "schema": DENSE_PREFIX_SCHEMA,
        "derived": True,
        "source_reference": metadata,
        "source_raw_sha256": source_raw_sha256,
        "source_response_sha256": source_raw_sha256,
        "source_steps_original": original_steps,
        "source_steps_retained": retained_steps,
        "retained_plan_step_ids": retained_ids,
        "terminal_source_step": dense_count,
        "cut_reason": cut_reason or {"reason": "candidate_already_dense"},
        "truncation_provenance": {
            "method": "maximal_leading_source_steps_1_to_k",
            "stopped_at_first_gap": cut_reason is not None,
            "guard_validation": "full_original_demonstrations",
        },
        "prefix_response": validated,
        "alignment": validated["alignment"],
        "guard_evidence": dict(guard_evidence),
    }
    return result


def load_saved_candidate(path: Path | str) -> tuple[dict[str, Any], str]:
    """Read one immutable saved response and return parsed wrapper plus hash."""

    path = Path(path).resolve()
    raw_file = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_file, Mapping):
        raise ValueError("saved compiler response is not an object")
    raw_text = raw_file.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ValueError("saved compiler response has no raw_text")
    from . import selective_pilot as pilot

    candidate = pilot._json_from_text(raw_text)
    if not isinstance(candidate, Mapping):
        raise ValueError("saved compiler response does not contain an object")
    return dict(candidate), _sha256_bytes(path.read_bytes())


__all__ = ["DENSE_PREFIX_SCHEMA", "derive_dense_prefix", "load_saved_candidate"]
