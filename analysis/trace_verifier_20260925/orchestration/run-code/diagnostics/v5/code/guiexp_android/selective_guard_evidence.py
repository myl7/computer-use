"""Pure evidence validation for selective-plan after guards.
Recorded pre/post accessibility trees are checked with the runtime's exact
selector predicate.  Dynamic values are grounded only in action text or the
recorded target's semantic fields.  Identity references with literal wrappers
are grounded by stripping an exact observed prefix and suffix before applying
the same conservative rules.  This helper never reads evaluator data or an
oracle and is not part of serving execution.
"""
from __future__ import annotations
import copy
from collections.abc import Mapping, Sequence
from typing import Any
from .selective_ax_parser import parse_ax_tree
from .selective_runtime import _guards, _hits, _normalize, resolve_value, validate_plan

_VALUE_SPEC_KEYS = frozenset({"slot", "transform", "prefix", "suffix"})
_VALUE_SPEC_REQUIRED_KEYS = frozenset({"slot", "transform"})

def _refs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if _VALUE_SPEC_REQUIRED_KEYS <= set(value) <= _VALUE_SPEC_KEYS:
            return [value]
        return sum((_refs(item) for item in value.values()), [])
    if isinstance(value, list):
        return sum((_refs(item) for item in value), [])
    return []

def _field(node: Mapping[str, Any], key: str) -> str | None:
    for name in {"text": ("text",), "hint": ("hint", "hint_text"),
                 "description": ("description", "content_description")}[key]:
        value = node.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None

def _target_nodes(record: Mapping[str, Any], pre: list[dict]) -> list[dict]:
    action = record.get("action") if isinstance(record.get("action"), Mapping) else {}
    index = action.get("index")
    if type(index) is int:
        # Element indexes are observation-local.  An action's target index
        # addresses the pre-state only; never use a post-state node with the
        # same number as a semantic binding source.
        return [node for node in pre if node.get("index") == index]
    target = record.get("target")
    return [dict(target)] if isinstance(target, Mapping) else []

def _unwrap_identity(value: Any, ref: Mapping[str, Any]) -> str | None:
    """Invert only a literal identity wrapper that is present verbatim."""
    if not isinstance(value, str) or ref.get("transform") != "identity":
        return None
    prefix = ref.get("prefix", "")
    suffix = ref.get("suffix", "")
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        return None
    if len(prefix) + len(suffix) > len(value):
        return None
    if not value.startswith(prefix) or not value.endswith(suffix):
        return None
    end = len(value) - len(suffix) if suffix else len(value)
    return value[len(prefix):end]

def _ground(step: Mapping[str, Any], record: Mapping[str, Any],
            pre: list[dict]) -> tuple[dict[str, Any], list[str]]:
    """Ground every declared slot from a structurally linked identity field."""
    by_slot: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    sources = [("action", step.get("action") or {}), ("target", step.get("target") or {})]
    sources.extend((name, step.get(name) or []) for name in ("before", "after"))
    for origin, source in sources:
        if isinstance(source, Mapping):
            for field, value in source.items():
                for ref in _refs(value):
                    by_slot.setdefault(str(ref["slot"]), []).append((origin, str(field), ref))
        elif isinstance(source, list):
            for selector in source:
                if isinstance(selector, Mapping):
                    for field, value in selector.items():
                        for ref in _refs(value):
                            by_slot.setdefault(str(ref["slot"]), []).append((origin, str(field), ref))
    action = record.get("action") if isinstance(record.get("action"), Mapping) else {}
    plan_action = step.get("action") if isinstance(step.get("action"), Mapping) else {}
    plan_target = step.get("target") if isinstance(step.get("target"), Mapping) else {}
    recorded_type = action.get("action_type")
    if by_slot and plan_action.get("action_type") != recorded_type:
        return {}, ["recorded action_type does not match the plan action"]
    targets = _target_nodes(record, pre)
    bindings: dict[str, Any] = {}
    errors: list[str] = []
    for slot, refs in by_slot.items():
        source_transforms = {
            ref.get("transform") for origin, _field, ref in refs if origin in {"action", "target"}
        }
        if source_transforms and source_transforms != {"identity"}:
            errors.append(f"slot {slot!r} has ambiguous transforms")
            continue
        candidates: list[str] = []
        linked = False
        for origin, field, ref in refs:
            # A source expression itself must expose the identity base.  A
            # nonidentity source would require an inverse transform, which is
            # intentionally unavailable.
            if origin == "action" and field in {"text", "app_name", "direction"} and plan_action.get(field) is ref:
                candidate = _unwrap_identity(action.get(field), ref)
                if candidate is not None:
                    candidates.append(candidate)
                    linked = True
                continue
            if origin == "target" and field in {"text", "hint", "description"} and plan_target.get(field) is ref:
                if ref.get("transform") == "identity":
                    for node in targets:
                        value = _field(node, field)
                        candidate = _unwrap_identity(value, ref)
                        if candidate is not None:
                            candidates.append(candidate)
                            linked = True
                continue
            # Guard refs link to an identity source on the same semantic
            # field in the plan action or target.
            for source in (plan_action, plan_target):
                source_ref = source.get(field) if isinstance(source, Mapping) else None
                if not isinstance(source_ref, Mapping) or str(source_ref.get("slot")) != slot:
                    continue
                if source_ref.get("transform") != "identity":
                    continue
                if source is plan_action and plan_action.get("action_type") == recorded_type and isinstance(action.get(field), str):
                    candidate = _unwrap_identity(action[field], source_ref)
                    if candidate is not None:
                        candidates.append(candidate)
                        linked = True
                elif source is plan_target:
                    for node in targets:
                        value = _field(node, field)
                        candidate = _unwrap_identity(value, source_ref)
                        if candidate is not None:
                            candidates.append(candidate)
                            linked = True
        if not linked:
            errors.append(f"slot {slot!r} has no identity-linked observed grounding")
            continue
        unique = {value for value in candidates if isinstance(value, str) and value.strip()}
        if len(unique) != 1:
            errors.append(f"slot {slot!r} has {'no' if not unique else 'ambiguous'} observed grounding")
        else:
            bindings[slot] = next(iter(unique))
    return bindings, errors

def _state(hits: list[int], error: str | None) -> str:
    return "unknown" if error else "pass" if len(hits) == 1 else "missing" if not hits else "ambiguous"

def _parse_sidecar(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Expose parser completeness without exposing an incomplete node view."""
    return {
        "complete": bool(parsed.get("complete")),
        "completeness": parsed.get("completeness"),
        "markers_seen": parsed.get("markers_seen", 0),
        "nodes_parsed": parsed.get("nodes_parsed", 0),
        "truncated": bool(parsed.get("truncated")),
        "issues": copy.deepcopy(parsed.get("issues") or []),
    }

def _parse_error(side: str, parsed: Mapping[str, Any]) -> str:
    kinds = [
        str(issue.get("kind", "unknown"))
        for issue in parsed.get("issues", [])
        if isinstance(issue, Mapping)
    ]
    detail = ", ".join(kinds) if kinds else "incomplete observation"
    return f"{side} accessibility tree parse is incomplete: {detail}"


def _action_target_checks(step: Mapping[str, Any], record: Mapping[str, Any],
                          binding: Mapping[str, Any], pre: list[dict]) -> tuple[dict[str, Any], list[str]]:
    plan_action = step["action"]
    action = record.get("action") if isinstance(record.get("action"), Mapping) else {}
    checks: dict[str, Any] = {}
    errors: list[str] = []
    for field in ("action_type", "text", "app_name", "direction"):
        if field not in plan_action:
            continue
        try:
            expected = plan_action[field] if field == "action_type" else resolve_value(plan_action[field], binding)
        except ValueError:
            errors.append(f"action field {field!r} is unresolved")
            continue
        actual = action.get(field)
        equal = (actual == expected) if field == "action_type" else _normalize(actual) == _normalize(expected)
        checks[field] = {"expected": expected, "actual": actual, "match": equal}
        if not equal:
            errors.append(f"recorded action field {field!r} does not match")
    target = step.get("target")
    if target is not None:
        index = action.get("index")
        hits, target_error = _hits(target, pre, binding)
        target_ok = type(index) is int and target_error is None and len(hits) == 1 and hits[0] == index
        checks["target"] = {"recorded_index": index, "pre_matches": hits, "match": target_ok}
        if target_error:
            errors.append("target selector is unresolved")
        elif not target_ok:
            errors.append("target selector does not resolve to the recorded pre index")
    elif "index" in action:
        checks["target"] = {"recorded_index": action.get("index"), "match": False}
        errors.append("targetless plan action has a recorded element index")
    return checks, errors

def _alignment_refs(alignment: Any, pi: int, sid: str) -> tuple[list[Any] | None, list[str]]:
    if alignment is None:
        return None, []
    value: Any = alignment.get("steps") if isinstance(alignment, Mapping) and "steps" in alignment else alignment
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            return [], ["explicit alignment contains a numeric step alias"]
        if sid not in value:
            return [], ["explicit alignment requires exact plan step IDs"]
        value = value[sid]
    elif isinstance(value, list):
        rows = [row for row in value if isinstance(row, Mapping) and row.get("plan_step") == sid]
        if any(not isinstance(row, Mapping) or not isinstance(row.get("plan_step"), str) for row in value):
            return [], ["explicit alignment list requires plan_step IDs"]
        if len(rows) != 1:
            return [], ["explicit alignment requires one row per plan step ID"]
        value = [{"source_step": row.get("source_step")} for row in rows]
    else:
        return [], ["explicit alignment must be a mapping or list"]
    refs = value if isinstance(value, list) else [value]
    seen: set[tuple[str, int]] = set()
    normalized: list[Any] = []
    for ref in refs:
        if isinstance(ref, Mapping):
            if set(ref) != {"source_step"}:
                return [], ["explicit source reference requires source_step"]
            key, number = "source_step", ref["source_step"]
        else:
            key, number = "source_step", ref
        if type(number) is not int or number <= 0:
            return [], ["explicit source_step must be a positive one-based integer"]
        marker = (key, number)
        if marker in seen:
            return [], ["explicit alignment contains duplicate source references"]
        seen.add(marker)
        normalized.append({key: number})
    return normalized, []


def _validate_alignment(alignment: Any, step_ids: Sequence[str]) -> list[str]:
    if alignment is None:
        return []
    value: Any = alignment.get("steps") if isinstance(alignment, Mapping) and "steps" in alignment else alignment
    rows: dict[str, Any] = {}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            return ["explicit alignment contains a numeric step alias"]
        if set(value) != set(step_ids):
            return ["explicit alignment keys must exactly equal plan step IDs"]
        rows = dict(value)
    elif isinstance(value, list):
        if any(not isinstance(row, Mapping) or set(row) != {"plan_step", "source_step"} for row in value):
            return ["explicit alignment rows require plan_step and source_step"]
        if len(value) != len(step_ids) or {row["plan_step"] for row in value} != set(step_ids):
            return ["explicit alignment must contain one row per plan step ID"]
        if len({row["plan_step"] for row in value}) != len(value):
            return ["explicit alignment contains duplicate plan step IDs"]
        rows = {row["plan_step"]: row["source_step"] for row in value}
    else:
        return ["explicit alignment must be a mapping or list"]
    numbers: list[int] = []
    for sid in step_ids:
        refs = rows[sid] if isinstance(rows[sid], list) else [rows[sid]]
        if len(refs) != 1:
            return [f"explicit alignment for {sid!r} must contain one source_step"]
        ref = refs[0].get("source_step") if isinstance(refs[0], Mapping) else refs[0]
        if type(ref) is not int or ref <= 0:
            return [f"explicit source_step for {sid!r} must be positive one-based integer"]
        numbers.append(ref)
    if len(set(numbers)) != len(numbers) or numbers != sorted(numbers):
        return ["explicit source_step values must be unique and monotonic"]
    return []

def _sources(records: list[Mapping[str, Any]], pi: int, sid: str,
             alignment: Any) -> tuple[list[tuple[int, Mapping[str, Any]]], list[str]]:
    refs, errors = _alignment_refs(alignment, pi, sid)
    if refs is None:
        if pi >= len(records):
            return [], ["positional source step is absent"]
        record = records[pi]
        try:
            number = int(record.get("step", -1))
        except (TypeError, ValueError):
            number = -1
        return ([(number, record)] if number == pi + 1 else []), ([] if number == pi + 1 else ["positional source step is not aligned"])
    selected: list[tuple[int, Mapping[str, Any]]] = []
    for ref in refs:
        candidates: list[tuple[int, Mapping[str, Any]]] = []
        if isinstance(ref, Mapping):
            ref = ref.get("source_step")
        if not candidates:
            if type(ref) is not int:
                errors.append("explicit source step must be an integer")
                continue
            candidates = [(int(record.get("step", -1)), record) for record in records if record.get("step") == ref]
        if len(candidates) != 1:
            errors.append("explicit source reference is absent or ambiguous")
        else:
            selected.append(candidates[0])
    return selected, errors

def _check_step(step: Mapping[str, Any], demos: Sequence[Mapping[str, Any]], pi: int,
                alignment: Any) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    sources: list[int] = []
    errors: list[str] = []
    unknown = invalid = False
    for di, demo in enumerate(demos):
        raw = demo.get("steps") if isinstance(demo.get("steps"), list) else []
        records = [record for record in raw if isinstance(record, Mapping)]
        selected, align_errors = _sources(records, pi, step["id"], alignment)
        if align_errors:
            unknown = True
            errors.extend(f"demo {di}: {error}" for error in align_errors)
        for source, record in selected:
            sources.append(source)
            pre_obs = record.get("pre_obs") if isinstance(record.get("pre_obs"), Mapping) else {}
            post_obs = record.get("post_obs") if isinstance(record.get("post_obs"), Mapping) else {}
            pre_parse = parse_ax_tree(pre_obs.get("ax_tree_text", ""))
            post_parse = parse_ax_tree(post_obs.get("ax_tree_text", ""))
            pre, post = pre_parse["nodes"], post_parse["nodes"]
            parse_sidecar = {
                "pre": _parse_sidecar(pre_parse),
                "post": _parse_sidecar(post_parse),
            }
            if not pre_parse["complete"] or not post_parse["complete"]:
                unknown = True
                if not pre_parse["complete"]:
                    errors.append(f"demo {di} source {source}: {_parse_error('pre', pre_parse)}")
                if not post_parse["complete"]:
                    errors.append(f"demo {di} source {source}: {_parse_error('post', post_parse)}")
                evidence.append({
                    "demo_index": di,
                    "source_index": source,
                    "selectors": [],
                    "changed_after_predicate": None,
                    "unchanged_context": [],
                    "action": {},
                    "before_guard": {"ok": False, "state": "unknown", "guards": []},
                    "parse": parse_sidecar,
                })
                continue
            if not pre and not post:
                unknown = True
                errors.append(f"demo {di} source {source}: observations are unavailable")
                continue
            binding, ground_errors = _ground(step, record, pre)
            if ground_errors:
                unknown = True
                errors.extend(f"demo {di} source {source}: {error}" for error in ground_errors)
                continue
            action_checks, action_errors = _action_target_checks(step, record, binding, pre)
            if action_errors:
                if any("unresolved" in error for error in action_errors):
                    unknown = True
                else:
                    invalid = True
                errors.extend(f"demo {di} source {source}: {error}" for error in action_errors)
            before_report = _guards(step["before"], pre, binding)
            if not before_report["ok"]:
                if any(item.get("state") == "error" for item in before_report["guards"]):
                    unknown = True
                    errors.append(f"demo {di} source {source}: before guard is unresolved")
                else:
                    invalid = True
                    errors.append(f"demo {di} source {source}: before guard does not hold in pre-state")
            selectors: list[dict[str, Any]] = []
            transition = False
            context: list[dict[str, Any]] = []
            selector_errors: list[str] = []
            for selector in step["after"]:
                before_hits, before_error = _hits(selector, pre, binding)
                after_hits, after_error = _hits(selector, post, binding)
                before_state, after_state = _state(before_hits, before_error), _state(after_hits, after_error)
                if before_error or after_error:
                    selector_errors.append("selector value is unresolved")
                elif after_state != "pass":
                    invalid = True
                    selector_errors.append(f"postcondition is {after_state}")
                elif before_state == "pass":
                    context.append(copy.deepcopy(selector))
                else:
                    transition = True
                selectors.append({"selector": copy.deepcopy(selector), "before": before_state,
                                  "after": after_state, "before_matches": before_hits,
                                  "after_matches": after_hits})
            if selector_errors:
                if any("unresolved" in error for error in selector_errors):
                    unknown = True
                errors.extend(f"demo {di} source {source}: {error}" for error in selector_errors)
            elif not transition:
                invalid = True
                errors.append(f"demo {di} source {source}: after guards are unchanged context")
            evidence.append({"demo_index": di, "source_index": source, "selectors": selectors,
                             "changed_after_predicate": transition, "unchanged_context": context,
                             "action": action_checks, "before_guard": before_report,
                             "parse": parse_sidecar})
    status = "invalid" if invalid else "unknown" if unknown or not evidence else "valid"
    return {"plan_index": pi, "step_id": step["id"], "status": status,
            "source_indices": sorted(set(sources)), "evidence": evidence, "errors": errors}

def validate_guard_evidence(plan: Mapping[str, Any], demonstrations: Sequence[Mapping[str, Any]], alignment: Any = None) -> dict[str, Any]:
    """Report observed guard transitions as ``valid``, ``unknown``, or ``invalid``."""
    report = validate_plan(plan)
    if not report.get("valid"):
        return {"status": "invalid", "steps": [], "source_indices": [], "errors": report["errors"]}
    plan_steps = report["plan"]["steps"]
    alignment_errors = _validate_alignment(alignment, [step["id"] for step in plan_steps])
    if alignment_errors:
        return {"status": "invalid", "steps": [], "source_indices": [], "errors": alignment_errors}
    steps = [_check_step(step, demonstrations, i, alignment) for i, step in enumerate(plan_steps)]
    status = "invalid" if any(item["status"] == "invalid" for item in steps) else "unknown" if any(item["status"] == "unknown" for item in steps) else "valid"
    return {"status": status, "steps": steps,
            "source_indices": sorted({i for item in steps for i in item["source_indices"]}),
            "errors": [error for item in steps for error in item["errors"]]}

__all__ = ["validate_guard_evidence"]
