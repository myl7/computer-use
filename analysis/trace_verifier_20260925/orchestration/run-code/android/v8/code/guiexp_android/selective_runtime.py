"""Restricted data-only linear GUI programs for the selective pilot.

Plans contain steps, actions, and guards.  Selectors may use only normalized
visible ``text``, ``hint``, or ``description`` plus ``editable`` and
``clickable`` flags.  A targeted action receives its numeric index only from
the current ``Adapter.elements()`` result.  Plans cannot contain indexes,
resource IDs, coordinates, or executable code.

Values are literal strings or ``{"slot": name, "transform": name}`` with
optional literal ``prefix`` and ``suffix`` strings.  The transform runs first,
then the prefix and suffix are applied.  ``identity`` stringifies the binding,
``stem`` returns the final path component without its final suffix, and
``suffix`` returns that suffix including its leading dot.  A single leading dot
file has no suffix.  ``first_word`` and ``last_word`` select the first or last
whitespace-delimited word.  These are string operations only and never access
a filesystem.

The runtime never reads a benchmark oracle.  ``full_fallback`` returns a
reactive handoff at the current program counter when a guard fails.
``local_rejoin`` can ask its callback for at most three one-action
interventions and advances only after the same node's ``after`` guards are
observed.  Callback claims, including ``status=complete``, do not establish
success.  ``BudgetStop`` propagates to the harness.
"""

from __future__ import annotations

import copy
import json
import unicodedata
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .budget_client import BudgetStop

SCHEMA = "selective-plan/1"
MAX_LOCAL_REJOIN_ACTIONS = 3

_PLAN_KEYS = frozenset({"schema", "slots", "steps"})
_STEP_KEYS = frozenset({"id", "intent", "action", "target", "before", "after"})
_ACTION_KEYS = frozenset({"action_type", "text", "app_name", "direction"})
_SELECTOR_KEYS = frozenset({"text", "hint", "description", "editable", "clickable"})
_TEXT_KEYS = ("text", "hint", "description")
_FLAG_KEYS = ("editable", "clickable")
_TRANSFORMS = frozenset({"identity", "stem", "suffix", "first_word", "last_word"})
_VALUE_SPEC_KEYS = frozenset({"slot", "transform", "prefix", "suffix"})
_VALUE_SPEC_REQUIRED_KEYS = frozenset({"slot", "transform"})
_ACTION_TYPES = frozenset({
    "click", "long_press", "input_text", "keyboard_enter", "navigate_home",
    "navigate_back", "open_app", "scroll", "wait",
})
_TARGET_TYPES = frozenset({"click", "long_press", "input_text"})
_DIRECTIONS = frozenset({"down", "up", "left", "right"})
_MODEL_ACTION_KEYS = frozenset({
    "action_type", "index", "text", "app_name", "direction",
})


class PlanValidationError(ValueError):
    """Malformed plan component used by the public diagnostic validator."""


def _err(path: str, message: str) -> str:
    return f"{path}: {message}"


def _bool(value: Any) -> bool:
    return type(value) is bool


def _slot_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _scalar(value: Any) -> bool:
    return type(value) in (str, int, float, bool)


def _validate_value(value: Any, path: str, slots: Mapping[str, str] | None = None) -> None:
    if isinstance(value, str):
        return
    if (
        not isinstance(value, dict)
        or not _VALUE_SPEC_REQUIRED_KEYS <= set(value)
        or not set(value) <= _VALUE_SPEC_KEYS
    ):
        raise PlanValidationError(
            _err(path, "must be a literal string or slot value spec with optional prefix/suffix")
        )
    if not _slot_name(value["slot"]):
        raise PlanValidationError(_err(path + ".slot", "must be a nonempty string"))
    if not isinstance(value["transform"], str) or value["transform"] not in _TRANSFORMS:
        raise PlanValidationError(_err(path + ".transform", "unknown transform"))
    for key in ("prefix", "suffix"):
        if key in value and not isinstance(value[key], str):
            raise PlanValidationError(_err(path + "." + key, "must be a string"))
    if slots is not None and value["slot"] not in slots:
        raise PlanValidationError(_err(path + ".slot", f"unresolved slot {value['slot']!r}"))


def _validate_selector(
    selector: Any,
    path: str,
    slots: Mapping[str, str] | None = None,
) -> None:
    if not isinstance(selector, dict):
        raise PlanValidationError(_err(path, "must be a selector object"))
    unknown = set(selector) - _SELECTOR_KEYS
    if unknown:
        raise PlanValidationError(_err(path, f"unsafe selector field(s): {sorted(unknown)}"))
    if not selector:
        raise PlanValidationError(_err(path, "must contain at least one field"))
    meaningful = False
    for key, value in selector.items():
        if key in _TEXT_KEYS:
            _validate_value(value, f"{path}.{key}", slots)
            meaningful |= not isinstance(value, str) or bool(_normalize(value))
        else:
            if not _bool(value):
                raise PlanValidationError(_err(f"{path}.{key}", "must be boolean"))
            meaningful = True
    if not meaningful:
        raise PlanValidationError(_err(path, "must contain a nonempty criterion"))


def _validate_action(
    action: Any,
    path: str,
    slots: Mapping[str, str] | None = None,
) -> str:
    if not isinstance(action, dict):
        raise PlanValidationError(_err(path, "must be an action object"))
    unknown = set(action) - _ACTION_KEYS
    if unknown:
        raise PlanValidationError(_err(path, f"unsafe action field(s): {sorted(unknown)}"))
    action_type = action.get("action_type")
    if not isinstance(action_type, str) or action_type not in _ACTION_TYPES:
        raise PlanValidationError(_err(path + ".action_type", f"unsupported action {action_type!r}"))
    for key in set(action) - {"action_type"}:
        _validate_value(action[key], f"{path}.{key}", slots)
    required = {"input_text": "text", "open_app": "app_name", "scroll": "direction"}
    if action_type in required and required[action_type] not in action:
        raise PlanValidationError(_err(path, f"{action_type} requires {required[action_type]}"))
    allowed = {
        "input_text": {"action_type", "text"},
        "open_app": {"action_type", "app_name"},
        "scroll": {"action_type", "direction"},
    }.get(action_type, {"action_type"})
    extras = set(action) - allowed
    if extras:
        raise PlanValidationError(_err(path, f"fields are not valid for {action_type}: {sorted(extras)}"))
    if isinstance(action.get("app_name"), str) and not action["app_name"].strip():
        raise PlanValidationError(_err(path + ".app_name", "must be nonempty"))
    if isinstance(action.get("direction"), str) and action["direction"].strip().casefold() not in _DIRECTIONS:
        raise PlanValidationError(_err(path + ".direction", "invalid scroll direction"))
    return action_type


def _validate_step(
    step: Any,
    path: str,
    slots: Mapping[str, str] | None = None,
) -> str:
    if not isinstance(step, dict):
        raise PlanValidationError(_err(path, "must be an object"))
    missing = _STEP_KEYS - set(step)
    unknown = set(step) - _STEP_KEYS
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unsafe fields {sorted(unknown)}")
        raise PlanValidationError(_err(path, ", ".join(details)))
    for key in ("id", "intent"):
        if not isinstance(step[key], str) or not step[key].strip():
            raise PlanValidationError(_err(f"{path}.{key}", "must be nonempty string"))
    action_type = _validate_action(step["action"], path + ".action", slots)
    target = step["target"]
    if target is None:
        if action_type in _TARGET_TYPES:
            raise PlanValidationError(_err(path + ".target", f"{action_type} requires a target"))
    else:
        _validate_selector(target, path + ".target", slots)
        if action_type not in _TARGET_TYPES:
            raise PlanValidationError(_err(path + ".target", f"{action_type} cannot have a target"))
    for name in ("before", "after"):
        guards = step[name]
        if not isinstance(guards, list):
            raise PlanValidationError(_err(path + "." + name, "must be a list"))
        for i, guard in enumerate(guards):
            _validate_selector(guard, f"{path}.{name}[{i}]", slots)
        if name == "after" and not guards:
            raise PlanValidationError(_err(path + ".after", "requires a postcondition"))
        if name == "before" and target is None and action_type != "open_app" and not guards:
            raise PlanValidationError(_err(path + ".before", "targetless action requires a precondition"))
    return action_type


def validate_plan(plan: Any) -> dict:
    """Return a diagnostic dict for a plan without touching an adapter.

    A valid result has ``valid=True``, ``ok=True``, and a detached ``plan``
    copy.  Invalid data is rejected with ``valid=False`` and ``errors``.
    ``run_plan`` records invalid plans as ``status='invalid_plan'``.
    """
    if not isinstance(plan, dict):
        return {"valid": False, "ok": False, "errors": ["plan: must be an object"], "plan": None}
    errors: list[str] = []
    missing = _PLAN_KEYS - set(plan)
    unknown = set(plan) - _PLAN_KEYS
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unsafe fields {sorted(unknown)}")
        errors.append(_err("plan", ", ".join(details)))
    if plan.get("schema") != SCHEMA:
        errors.append(_err("plan.schema", f"must equal {SCHEMA!r}"))

    slots = plan.get("slots")
    valid_slots: dict[str, str] = {}
    if not isinstance(slots, dict):
        errors.append(_err("plan.slots", "must be an object"))
    else:
        for name, description in slots.items():
            if not _slot_name(name):
                errors.append(_err("plan.slots", f"invalid slot name {name!r}"))
            elif not isinstance(description, str) or not description.strip():
                errors.append(_err(f"plan.slots.{name}", "must be nonempty string"))
            else:
                valid_slots[name] = description

    steps = plan.get("steps")
    if not isinstance(steps, list):
        errors.append(_err("plan.steps", "must be a list"))
    elif not steps:
        errors.append(_err("plan.steps", "must contain at least one step"))
    else:
        seen: set[str] = set()
        for i, step in enumerate(steps):
            try:
                _validate_step(step, f"plan.steps[{i}]", valid_slots)
            except PlanValidationError as exc:
                errors.append(str(exc))
                continue
            if step["id"] in seen:
                errors.append(_err(f"plan.steps[{i}].id", f"duplicate id {step['id']!r}"))
            seen.add(step["id"])
    if errors:
        return {"valid": False, "ok": False, "errors": errors, "plan": None}
    detached = copy.deepcopy(plan)
    return {
        "valid": True,
        "ok": True,
        "errors": [],
        "schema": SCHEMA,
        "slots": list(valid_slots),
        "step_ids": [step["id"] for step in detached["steps"]],
        "step_count": len(detached["steps"]),
        "plan": detached,
    }


def _normalize(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not _scalar(value):
        raise ValueError("bound slot value must be a string or scalar")
    return str(value)


def _stem(value: str) -> str:
    leaf = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in leaf or (leaf.startswith(".") and leaf.count(".") == 1):
        return leaf
    return leaf[:leaf.rfind(".")]


def _suffix(value: str) -> str:
    leaf = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in leaf or (leaf.startswith(".") and leaf.count(".") == 1):
        return ""
    return "." + leaf.rsplit(".", 1)[1]


def resolve_value(value: Any, bindings: Mapping[str, Any]) -> str:
    """Resolve a literal or declared slot spec to a string."""
    if isinstance(value, str):
        return value
    if (
        not isinstance(value, dict)
        or not _VALUE_SPEC_REQUIRED_KEYS <= set(value)
        or not set(value) <= _VALUE_SPEC_KEYS
    ):
        raise ValueError("value must be a literal string or {slot, transform} with optional prefix/suffix")
    slot = value["slot"]
    transform = value["transform"]
    if not _slot_name(slot):
        raise ValueError(f"invalid slot name {slot!r}")
    if not isinstance(transform, str) or transform not in _TRANSFORMS:
        raise ValueError(f"unknown value transform {transform!r}")
    for key in ("prefix", "suffix"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"value {key!r} must be a string")
    if not isinstance(bindings, Mapping) or slot not in bindings:
        raise ValueError(f"unresolved slot {slot!r}")
    text = _as_text(bindings[slot])
    if transform == "identity":
        rendered = text
    elif transform == "stem":
        rendered = _stem(text)
    elif transform == "suffix":
        rendered = _suffix(text)
    else:
        words = text.split()
        if not words:
            raise ValueError(f"slot {slot!r} is empty for {transform}")
        rendered = words[0] if transform == "first_word" else words[-1]
    return value.get("prefix", "") + rendered + value.get("suffix", "")


def _index(element: Mapping[str, Any], position: int) -> int | None:
    value = element.get("index", position)
    return value if type(value) is int and value >= 0 else None


def _element_text(element: Mapping[str, Any], key: str) -> Any:
    aliases = {
        "text": ("text",),
        "hint": ("hint", "hint_text"),
        "description": ("description", "content_description"),
    }
    for alias in aliases[key]:
        if alias in element:
            return element[alias]
    return ""


def _element_flag(element: Mapping[str, Any], key: str) -> bool:
    if key in element:
        return bool(element[key])
    return bool(element.get("is_" + key, False))


def _hits(
    selector: dict,
    elements: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> tuple[list[int], str | None]:
    try:
        _validate_selector(selector, "selector")
        wanted = {
            key: resolve_value(selector[key], bindings)
            for key in _TEXT_KEYS
            if key in selector
        }
    except (PlanValidationError, ValueError) as exc:
        return [], str(exc)
    hits: list[int] = []
    for position, element in enumerate(elements):
        if not isinstance(element, Mapping):
            continue
        index = _index(element, position)
        if index is None:
            continue
        if any(_normalize(_element_text(element, key)) != _normalize(value)
               for key, value in wanted.items()):
            continue
        if any(key in selector and _element_flag(element, key) is not selector[key]
               for key in _FLAG_KEYS):
            continue
        hits.append(index)
    return hits, None


def resolve_step(
    step: dict,
    elements: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> dict | None:
    """Return a fresh current-indexed action, or ``None`` for nonunique target."""
    try:
        action_type = _validate_step(step, "step")
    except PlanValidationError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(elements, Sequence) or isinstance(elements, (str, bytes)):
        raise ValueError("elements must be a sequence")
    if not isinstance(bindings, Mapping):
        raise ValueError("bindings must be a mapping")
    action = {"action_type": action_type}
    for key in ("text", "app_name", "direction"):
        if key in step["action"]:
            action[key] = resolve_value(step["action"][key], bindings)
    if action_type == "open_app" and not action["app_name"].strip():
        raise ValueError("open_app app_name must be nonempty")
    if action_type == "scroll":
        direction = action["direction"].strip().casefold()
        if direction not in _DIRECTIONS:
            raise ValueError(f"invalid scroll direction {direction!r}")
        action["direction"] = direction
    if step["target"] is None:
        return action
    hits, error = _hits(step["target"], elements, bindings)
    if error:
        raise ValueError(error)
    if len(hits) != 1:
        return None
    action["index"] = hits[0]
    return action


def _guards(
    selectors: Sequence[dict],
    elements: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Any],
) -> dict:
    records = []
    ok = True
    for selector in selectors:
        hits, error = _hits(selector, elements, bindings)
        state = "error" if error else "pass" if len(hits) == 1 else "missing" if not hits else "ambiguous"
        ok &= state == "pass"
        item = {"selector": copy.deepcopy(selector), "state": state, "matches": hits}
        if error:
            item["error"] = error
        records.append(item)
    return {"ok": ok, "guards": records}


def _unknown(phase: str, reason: str) -> dict:
    return {"ok": False, "guards": [], "state": "unknown", "phase": phase, "error": reason}


def _elements(adapter: Any) -> tuple[list[dict] | None, str | None]:
    try:
        value = adapter.elements()
    except BudgetStop:
        raise
    except Exception as exc:  # noqa: BLE001 - adapter boundary
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"
    if not isinstance(value, list):
        return None, "adapter.elements() must return a list"
    try:
        return copy.deepcopy(value), None
    except Exception as exc:  # noqa: BLE001 - malformed adapter data
        return None, f"element copy failed: {type(exc).__name__}"


def _observe(adapter: Any) -> tuple[dict | None, str | None]:
    try:
        value = adapter.observe()
    except BudgetStop:
        raise
    except Exception as exc:  # noqa: BLE001 - adapter boundary
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"
    if not isinstance(value, dict):
        return None, "adapter.observe() must return a dict"
    try:
        return copy.deepcopy(value), None
    except Exception as exc:  # noqa: BLE001 - malformed adapter data
        return None, f"observation copy failed: {type(exc).__name__}"


def _usage(total: dict[str, Any], value: Any) -> None:
    if not isinstance(value, dict):
        return
    for key, number in value.items():
        if type(number) in (int, float):
            total[key] = total.get(key, 0) + number


def _base(mode: str, pc: int, trace: list[dict], count: int, usage: dict) -> dict:
    return {
        "status": "",
        "mode": mode,
        "pc": pc,
        "trace": trace,
        "actions": count,
        "action_count": count,
        "usage": usage,
        "verified": False,
    }


def _write(result: dict, out_path: str | Path | None) -> dict:
    if out_path is not None:
        Path(out_path).write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return result


def _result(
    status: str,
    mode: str,
    pc: int,
    trace: list[dict],
    count: int,
    usage: dict,
    reason: str | None = None,
    **extra: Any,
) -> dict:
    value = _base(mode, pc, trace, count, usage)
    value["status"] = status
    if reason is not None:
        value["reason"] = reason
    value.update(extra)
    return value


def _handoff(
    mode: str,
    pc: int,
    trace: list[dict],
    count: int,
    usage: dict,
    reason: str,
    observation: dict | None,
    phase: str,
    **extra: Any,
) -> dict:
    handoff = {"scope": "reactive", "pc": pc, "action": None}
    if observation is not None:
        handoff["observation"] = observation
    return _result(
        "handoff", mode, pc, trace, count, usage, reason,
        observation=observation, phase=phase, handoff=handoff, **extra,
    )


def _record(
    trace: list[dict],
    *,
    pc: int,
    step: dict,
    kind: str,
    action: dict,
    before: dict,
    after: dict,
    observation: dict | None,
    before_elements: Sequence[Mapping[str, Any]] | None,
    after_elements: Sequence[Mapping[str, Any]] | None,
    error: str | None = None,
) -> None:
    item = {
        "pc": pc,
        "step_id": step["id"],
        "intent": step["intent"],
        "kind": kind,
        "action": copy.deepcopy(action),
        "issued": True,
        "guards": {"before": before, "after": after},
        "observation": copy.deepcopy(observation),
        "evidence": {
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
            "elements_before": copy.deepcopy(before_elements),
            "elements_after": copy.deepcopy(after_elements),
        },
    }
    if error is not None:
        item["execution_error"] = error
    trace.append(item)


def _issue(
    adapter: Any,
    action: dict,
    *,
    pc: int,
    step: dict,
    kind: str,
    before: dict,
    before_elements: list[dict],
    count: int,
    trace: list[dict],
    bindings: Mapping[str, Any],
) -> tuple[str, int, dict | None, list[dict] | None, dict, str | None]:
    """Execute one action and collect its verified after guard."""
    count += 1
    try:
        adapter.execute(copy.deepcopy(action))
    except BudgetStop:
        raise
    except Exception as exc:  # noqa: BLE001 - side effect may have happened
        reason = f"{type(exc).__name__}: {str(exc).splitlines()[0][:240]}"
        _record(
            trace, pc=pc, step=step, kind=kind, action=action, before=before,
            after=_unknown("after", "execute raised"), observation=None,
            before_elements=before_elements, after_elements=None, error=reason,
        )
        return "uncertain", count, None, None, _unknown("after", reason), f"UI action exception: {reason}"
    observation, error = _observe(adapter)
    if error:
        after = _unknown("after", error)
        _record(
            trace, pc=pc, step=step, kind=kind, action=action, before=before,
            after=after, observation=None, before_elements=before_elements,
            after_elements=None, error=error,
        )
        return "uncertain", count, None, None, after, f"observation exception: {error}"
    elements, error = _elements(adapter)
    if error:
        after = _unknown("after", error)
        _record(
            trace, pc=pc, step=step, kind=kind, action=action, before=before,
            after=after, observation=observation, before_elements=before_elements,
            after_elements=None, error=error,
        )
        return "uncertain", count, observation, None, after, f"elements exception: {error}"
    after = _guards(step["after"], elements, bindings)
    _record(
        trace, pc=pc, step=step, kind=kind, action=action, before=before,
        after=after, observation=observation, before_elements=before_elements,
        after_elements=elements,
    )
    return "ok", count, observation, elements, after, None


def _action_error(action: Any, elements: Sequence[Mapping[str, Any]]) -> str | None:
    if not isinstance(action, dict):
        return "decision action must be an object or null"
    unknown = set(action) - _MODEL_ACTION_KEYS
    if unknown:
        return f"unsafe decision action field(s): {sorted(unknown)}"
    action_type = action.get("action_type")
    if action_type not in _ACTION_TYPES:
        return f"unsupported or unverified decision action {action_type!r}"
    allowed = {
        "click": {"action_type", "index"},
        "long_press": {"action_type", "index"},
        "input_text": {"action_type", "text", "index"},
        "open_app": {"action_type", "app_name"},
        "scroll": {"action_type", "direction"},
        "keyboard_enter": {"action_type"},
        "navigate_home": {"action_type"},
        "navigate_back": {"action_type"},
        "wait": {"action_type"},
    }[action_type]
    missing = allowed - set(action)
    extras = set(action) - allowed
    if missing or extras:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extras:
            details.append(f"unsafe fields {sorted(extras)}")
        return "decision action " + ", ".join(details)
    if action_type in {"click", "long_press", "input_text"}:
        index = action["index"]
        if type(index) is not int or index < 0:
            return "decision action index must be nonnegative integer"
        current = {
            value
            for position, element in enumerate(elements)
            if isinstance(element, Mapping)
            for value in [_index(element, position)]
            if value is not None
        }
        if index not in current:
            return "decision action index is absent from current elements"
        if action_type == "input_text" and not isinstance(action["text"], str):
            return "decision input_text text must be a string"
    elif action_type == "open_app":
        if not isinstance(action["app_name"], str) or not action["app_name"].strip():
            return "decision open_app app_name must be nonempty string"
    elif action_type == "scroll" and action["direction"] not in _DIRECTIONS:
        return "decision scroll direction is invalid"
    return None


def _payload(
    pc: int,
    step: dict,
    previous: dict | None,
    observation: dict | None,
    elements: list[dict],
    trace: Sequence[dict],
) -> dict:
    return {
        "kind": "local_rejoin",
        "mode": "local_rejoin",
        "pc": pc,
        "step_id": step["id"],
        "intent": step["intent"],
        "after": copy.deepcopy(step["after"]),
        "previous_action": copy.deepcopy(previous),
        "last_action": copy.deepcopy(previous),
        "observation": copy.deepcopy(observation),
        "elements": copy.deepcopy(elements),
        "trace": copy.deepcopy(list(trace)),
    }


def _rejoin(
    adapter: Any,
    bindings: Mapping[str, Any],
    decision: Callable[[dict], dict],
    *,
    pc: int,
    step: dict,
    previous: dict | None,
    elements: list[dict],
    observation: dict | None,
    trace: list[dict],
    count: int,
    max_actions: int,
    usage: dict,
) -> tuple[str, int, list[dict], dict | None, str | None]:
    """Return outcome ``joined``, ``handoff``, ``uncertain``, or ``budget``."""
    if _guards(step["after"], elements, bindings)["ok"]:
        return "joined", count, elements, observation, None
    for _ in range(MAX_LOCAL_REJOIN_ACTIONS):
        if count >= max_actions:
            return "budget", count, elements, observation, "max_actions exhausted during local rejoin"
        try:
            response = decision(_payload(pc, step, previous, observation, elements, trace))
        except BudgetStop:
            raise
        except Exception as exc:  # noqa: BLE001 - callback boundary
            return "handoff", count, elements, observation, f"decision callback error: {type(exc).__name__}: {str(exc).splitlines()[0][:240]}"
        if not isinstance(response, dict):
            return "handoff", count, elements, observation, "decision must return an object"
        _usage(usage, response.get("usage", {}))
        if response.get("stop"):
            return "handoff", count, elements, observation, "decision requested stop before verified rejoin"
        action = response.get("action")
        if action is None:
            return "handoff", count, elements, observation, "decision returned no action"
        error = _action_error(action, elements)
        if error:
            return "handoff", count, elements, observation, error
        if previous is not None and action == previous:
            return "handoff", count, elements, observation, "decision repeated an already-issued action"
        before = _guards(step["after"], elements, bindings)
        state, count, observation, next_elements, after, reason = _issue(
            adapter, copy.deepcopy(action), pc=pc, step=step, kind="local",
            before=before, before_elements=elements, count=count, trace=trace,
            bindings=bindings,
        )
        if state == "uncertain":
            return "uncertain", count, next_elements or elements, observation, reason
        elements = next_elements or []
        previous = copy.deepcopy(action)
        if after["ok"]:
            return "joined", count, elements, observation, None
    return "handoff", count, elements, observation, "local rejoin cap exhausted"


def _invalid(mode: str, report: dict, reason: str) -> dict:
    return _result("invalid_plan", mode, 0, [], 0, {}, reason, validation=report)


def run_plan(
    plan: dict,
    bindings: Mapping[str, Any],
    adapter: Any,
    decision: Callable[[dict], dict] | None,
    mode: str,
    max_actions: int = 40,
    out_path: str | Path | None = None,
) -> dict:
    """Run a plan through ``elements``, ``execute``, and ``observe`` only."""
    report = validate_plan(plan)
    if mode not in {"full_fallback", "local_rejoin"}:
        return _write(_invalid(mode, report, "mode must be full_fallback or local_rejoin"), out_path)
    if type(max_actions) is not int or max_actions < 0:
        return _write(_invalid(mode, report, "max_actions must be a nonnegative integer"), out_path)
    if not report["valid"]:
        return _write(_invalid(mode, report, "plan validation failed"), out_path)
    if not isinstance(bindings, Mapping):
        return _write(_invalid(mode, report, "bindings must be a mapping"), out_path)
    if mode == "local_rejoin" and not callable(decision):
        return _write(_invalid(mode, report, "local_rejoin requires a decision callback"), out_path)

    selected = report["plan"]
    trace: list[dict] = []
    usage: dict[str, Any] = {}
    count = 0
    pc = 0
    while pc < len(selected["steps"]):
        step = selected["steps"][pc]
        elements, error = _elements(adapter)
        if error:
            return _write(_result("uncertain", mode, pc, trace, count, usage, f"elements exception: {error}"), out_path)
        before = _guards(step["before"], elements, bindings)
        action = None
        reason = None
        target_guard = None
        if before["ok"]:
            try:
                action = resolve_step(step, elements, bindings)
            except ValueError as exc:
                reason = str(exc)
            if action is None and reason is None:
                reason = "target selector did not have exactly one current match"
            if action is None:
                target_guard = _guards([step["target"]], elements, bindings) if step["target"] is not None else None
        else:
            reason = "before guard did not have exactly one match per selector"

        if action is None:
            observation, observe_error = _observe(adapter)
            if observe_error:
                return _write(_result("uncertain", mode, pc, trace, count, usage, f"observation exception: {observe_error}"), out_path)
            phase = "before" if not before["ok"] else "target"
            guard = before if phase == "before" else target_guard
            if mode == "full_fallback":
                return _write(_handoff(
                    mode, pc, trace, count, usage, reason or "guard miss", observation, phase,
                    guard=guard,
                ), out_path)
            outcome, count, elements, observation, local_reason = _rejoin(
                adapter, bindings, decision, pc=pc, step=step, previous=None,
                elements=elements, observation=observation, trace=trace, count=count,
                max_actions=max_actions, usage=usage,
            )
            if outcome == "joined":
                pc += 1
                continue
            if outcome == "budget":
                return _write(_result("budget_exhausted", mode, pc, trace, count, usage, local_reason), out_path)
            if outcome == "uncertain":
                return _write(_result("uncertain", mode, pc, trace, count, usage, local_reason), out_path)
            return _write(_handoff(
                mode, pc, trace, count, usage, local_reason or reason or "local rejoin failed",
                observation, phase, guard=guard,
            ), out_path)

        if count >= max_actions:
            return _write(_result("budget_exhausted", mode, pc, trace, count, usage, "max_actions exhausted"), out_path)
        state, count, observation, next_elements, after, issue_reason = _issue(
            adapter, action, pc=pc, step=step, kind="program", before=before,
            before_elements=elements, count=count, trace=trace, bindings=bindings,
        )
        if state == "uncertain":
            return _write(_result("uncertain", mode, pc, trace, count, usage, issue_reason), out_path)
        elements = next_elements or []
        if after["ok"]:
            pc += 1
            continue
        if mode == "full_fallback":
            return _write(_handoff(
                mode, pc, trace, count, usage,
                "after guard did not have exactly one match per selector",
                observation, "after", guard=after,
            ), out_path)
        outcome, count, elements, observation, local_reason = _rejoin(
            adapter, bindings, decision, pc=pc, step=step, previous=action,
            elements=elements, observation=observation, trace=trace, count=count,
            max_actions=max_actions, usage=usage,
        )
        if outcome == "joined":
            pc += 1
            continue
        if outcome == "budget":
            return _write(_result("budget_exhausted", mode, pc, trace, count, usage, local_reason), out_path)
        if outcome == "uncertain":
            return _write(_result("uncertain", mode, pc, trace, count, usage, local_reason), out_path)
        return _write(_handoff(
            mode, pc, trace, count, usage, local_reason or "local rejoin failed",
            observation, "after", guard=after,
        ), out_path)

    return _write(_result("complete", mode, pc, trace, count, usage, verified=True), out_path)


__all__ = [
    "resolve_step", "resolve_value", "run_plan", "validate_plan",
]
