"""The exact data grammar shared by projected plan requests and validation.

The runtime is deliberately stricter than a general JSON description.  This
contract names only the fields accepted by ``selective_runtime``.  It is kept
in its own source file so a prepared experiment can freeze the request
contract independently of the provider and version.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


PLAN_SCHEMA = "selective-plan/1"
CONTRACT_SCHEMA = "selective-plan-contract/1"
COMPILATION_SCHEMA = "selective-compilation/1"
SLOT_TRANSFORMS = ("identity", "stem", "suffix", "first_word", "last_word")
SELECTOR_KEYS = ("text", "hint", "description", "editable", "clickable")
ACTION_TYPES = (
    "click",
    "long_press",
    "input_text",
    "keyboard_enter",
    "navigate_home",
    "navigate_back",
    "open_app",
    "scroll",
    "wait",
)
ACTION_FIELDS = {
    "click": ("action_type",),
    "long_press": ("action_type",),
    "input_text": ("action_type", "text"),
    "keyboard_enter": ("action_type",),
    "navigate_home": ("action_type",),
    "navigate_back": ("action_type",),
    "open_app": ("action_type", "app_name"),
    "scroll": ("action_type", "direction"),
    "wait": ("action_type",),
}

# This document is intentionally precise instead of delegating to a generic
# JSON Schema.  In particular, a string is always a literal.  A slot reference
# is the only permitted object value inside an action or selector.
CONTRACT: dict[str, Any] = {
    "schema": CONTRACT_SCHEMA,
    "plan": {
        "exact_keys": ["schema", "slots", "steps"],
        "schema_value": PLAN_SCHEMA,
        "slots": "object, possibly empty: identifier -> nonempty plain description string",
        "steps": "nonempty array of step objects",
    },
    "slot_value": {
        "literal": "JSON string, kept literally",
        "reference": {
            "exact_keys": ["slot", "transform"],
            "optional_keys": ["prefix", "suffix"],
            "allowed_keys": ["slot", "transform", "prefix", "suffix"],
            "key_rule": "use exactly slot and transform, plus zero or more optional prefix/suffix keys",
            "slot": "one declared slot name",
            "transform": list(SLOT_TRANSFORMS),
            "transform_semantics": {
                "identity": "stringify the bound scalar",
                "stem": "take the final path component without its final suffix",
                "suffix": "take the final suffix, including its leading dot; a single leading-dot file has no suffix",
                "first_word": "take the first whitespace-delimited word",
                "last_word": "take the last whitespace-delimited word",
            },
            "prefix": "optional JSON string prepended after the transform",
            "suffix": "optional JSON string appended after the transform",
        },
        "interpolation": "forbidden: strings such as {{slot:name|identity}} remain invalid literals",
    },
    "selector": {
        "exact_keys": list(SELECTOR_KEYS),
        "allowed": "one or more fields from the exact key set",
        "text_fields": "text, hint, description accept a literal string or one slot reference",
        "flag_fields": "editable and clickable accept booleans only",
        "forbidden": [
            "contains",
            "activity",
            "content_description",
            "is_clickable",
            "is_editable",
            "index",
            "resource_id",
            "coordinates",
        ],
    },
    "action": {
        "action_types": list(ACTION_TYPES),
        "exact_fields_by_type": {key: list(value) for key, value in ACTION_FIELDS.items()},
        "input_text": "text is a literal string or one slot reference",
        "open_app": "app_name is a literal string or one slot reference",
        "scroll": "direction is one literal string: down, up, left, or right",
        "target_index": "never emit an index; runtime resolves the target selector against current elements",
    },
    "step": {
        "exact_keys": ["id", "intent", "action", "target", "before", "after"],
        "before": "selector array, possibly empty when the action has a target",
        "after": "nonempty selector array",
        "target": "selector for click, long_press, or input_text; null for all other action types",
    },
    "output": {
        "one_json_object": True,
        "no_code": True,
        "no_oracle": True,
        "no_hidden_parameters": True,
    },
}

# A small fictional example exercises target resolution without encoding any
# benchmark family procedure or task binding.
FICTIONAL_EXAMPLE: dict[str, Any] = {
    "schema": PLAN_SCHEMA,
    "slots": {
        "label": "the user supplied label, with the target shown after a literal Label: prefix",
    },
    "steps": [
        {
            "id": "open_settings",
            "intent": "open the Settings app",
            "action": {"action_type": "open_app", "app_name": "Settings"},
            "target": None,
            "before": [],
            "after": [{"text": "Settings"}],
        },
        {
            "id": "tap_label",
            "intent": "tap the requested visible label",
            "action": {"action_type": "click"},
            "target": {
                "text": {
                    "slot": "label",
                    "transform": "identity",
                    "prefix": "Label: ",
                    "suffix": "",
                },
            },
            "before": [],
            "after": [{"text": "Completed"}],
        },
    ],
}

FICTIONAL_COMPILATION_EXAMPLE: dict[str, Any] = {
    "schema": COMPILATION_SCHEMA,
    "plan": FICTIONAL_EXAMPLE,
    "source_steps": {"open_settings": 1, "tap_label": 2},
}


def projected_demo_payload(family: str, demonstrations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the same projected evidence payload used by the pilot builder."""

    examples: list[dict[str, Any]] = []
    for demo in demonstrations:
        steps: list[dict[str, Any]] = []
        for item in demo.get("steps", []):
            steps.append(
                {
                    "step": item.get("step"),
                    "action": item.get("action"),
                    "pre_observation": {
                        "activity": (item.get("pre_obs") or {}).get("url"),
                        "ax_tree_text": (item.get("pre_obs") or {}).get("ax_tree_text", ""),
                    },
                    "post_observation": {
                        "activity": (item.get("post_obs") or {}).get("url"),
                        "ax_tree_text": (item.get("post_obs") or {}).get("ax_tree_text", ""),
                    },
                    "screenshot_files": item.get("screenshot_files") or {},
                }
            )
        examples.append({"goal_text": demo.get("goal_text", ""), "steps": steps})
    return {"family": family, "demonstrations": examples}


def _contract_text() -> str:
    return json.dumps(
        {
            "contract": CONTRACT,
            "fictional_valid_example": FICTIONAL_EXAMPLE,
            "compilation_wrapper": {
                "schema": COMPILATION_SCHEMA,
                "exact_keys": ["schema", "plan", "source_steps"],
                "plan": "one selective-plan/1 object satisfying the complete plan contract",
                "source_steps": "mapping from every explicit plan step id to one positive one-based original training step",
                "alignment": "source step values are strictly increasing in plan step order and have no duplicates",
            },
            "fictional_valid_compilation_example": FICTIONAL_COMPILATION_EXAMPLE,
        },
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )


def build_messages(family: str, demonstrations: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Build a plan request with the exact runtime contract and evidence."""

    payload = projected_demo_payload(family, demonstrations)
    user_text = (
        "Return exactly one JSON object that satisfies the complete compilation response "
        "contract below. The response has schema selective-compilation/1, a plan field "
        "containing one selective-plan/1 object, and a source_steps field mapping every "
        "explicit plan step id to one positive one-based original training step. Source "
        "step values must be strictly increasing in plan step order with no duplicates. "
        "The fictional example only demonstrates syntax. Build the plan from the supplied "
        "task goals, projected observations, and native actions. Every top-level, step, "
        "selector, and action object must contain only the contract's exact keys. "
        "Observation fields such as activity and raw accessibility names are input evidence, "
        "not output selector keys. Use literal strings unless a field value is one explicit "
        "slot reference object. A reference may add literal string prefix and suffix fields, "
        "which wrap the transformed slot value. Do not emit code, interpolation strings, benchmark oracle "
        "fields, evaluator parameters, or procedural notes.\n\n"
        "COMPLETE PLAN CONTRACT:\n"
        + _contract_text()
        + "\n\nPROJECTED DEMONSTRATIONS:\n"
        + json.dumps(payload, ensure_ascii=True, indent=2)
    )
    return [
        {
            "role": "system",
            "content": "You produce data-only guarded Android plans as strict JSON under the supplied exact contract.",
        },
        {"role": "user", "content": user_text},
    ]


def repair_messages(
    original_messages: Sequence[Mapping[str, Any]],
    raw_response: str,
    validation_error: str,
) -> list[dict[str, str]]:
    """Return the bounded repair exchange while retaining the full contract."""

    if len(original_messages) < 2:
        raise ValueError("original plan request must contain system and user messages")
    return [
        {"role": "system", "content": str(original_messages[0].get("content", ""))},
        {"role": "user", "content": str(original_messages[1].get("content", ""))},
        {"role": "assistant", "content": str(raw_response)},
        {
            "role": "user",
            "content": (
                "The response above failed the exact compilation response contract. Check the "
                "wrapper schema, plan, source_steps mapping, strict source-step alignment, "
                "and every action/selector rule against the COMPLETE PLAN CONTRACT in the "
                "preceding user message, then return one corrected JSON object only. Validation error: "
                + str(validation_error).splitlines()[0][:400]
            ),
        },
    ]


def validate_plan(plan: Any) -> dict[str, Any]:
    """Apply the runtime grammar plus the contract's literal-string rule."""

    from .selective_runtime import validate_plan as runtime_validate_plan

    report = runtime_validate_plan(plan)
    if not isinstance(report, Mapping) or report.get("valid") is not True:
        return dict(report) if isinstance(report, Mapping) else {"valid": False, "errors": ["invalid plan"]}

    def has_interpolation(value: Any) -> bool:
        if isinstance(value, str):
            return "{{" in value or "}}" in value
        if isinstance(value, Mapping):
            return any(has_interpolation(key) or has_interpolation(item) for key, item in value.items())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(has_interpolation(item) for item in value)
        return False

    if has_interpolation(plan):
        return {
            **dict(report),
            "valid": False,
            "ok": False,
            "errors": ["plan contains forbidden string interpolation; use a slot reference object"],
            "plan": None,
        }
    return dict(report)


def validate_compilation_response(value: Any) -> dict[str, Any]:
    """Validate the response wrapper and return its detached plan/alignment."""

    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["compilation response must be an object"]}
    if set(value) != {"schema", "plan", "source_steps"}:
        return {"valid": False, "errors": ["compilation response keys must be exactly schema, plan, source_steps"]}
    if value.get("schema") != COMPILATION_SCHEMA:
        return {"valid": False, "errors": [f"compilation response schema must be {COMPILATION_SCHEMA}"]}
    plan_report = validate_plan(value.get("plan"))
    if plan_report.get("valid") is not True:
        return {"valid": False, "errors": [f"plan: {error}" for error in plan_report.get("errors", [])]}
    plan = plan_report.get("plan")
    steps = list(plan.get("steps") or []) if isinstance(plan, Mapping) else []
    step_ids = [str(step["id"]) for step in steps]
    source_steps = value.get("source_steps")
    if not isinstance(source_steps, Mapping):
        return {"valid": False, "errors": ["source_steps must be an object"]}
    if set(source_steps) != set(step_ids):
        return {"valid": False, "errors": ["source_steps must contain exactly one entry for every plan step id"]}
    ordered: list[int] = []
    for step_id in step_ids:
        number = source_steps.get(step_id)
        if type(number) is not int or number <= 0:
            return {"valid": False, "errors": [f"source_steps[{step_id!r}] must be a positive one-based integer"]}
        ordered.append(number)
    if len(set(ordered)) != len(ordered):
        return {"valid": False, "errors": ["source_steps must not contain duplicate original training steps"]}
    if ordered != sorted(ordered):
        return {"valid": False, "errors": ["source_steps must be strictly increasing in plan step order"]}
    return {
        "valid": True,
        "schema": COMPILATION_SCHEMA,
        "plan": dict(plan),
        "source_steps": {step_id: source_steps[step_id] for step_id in step_ids},
    }


__all__ = [
    "ACTION_FIELDS",
    "ACTION_TYPES",
    "COMPILATION_SCHEMA",
    "CONTRACT",
    "CONTRACT_SCHEMA",
    "FICTIONAL_EXAMPLE",
    "FICTIONAL_COMPILATION_EXAMPLE",
    "PLAN_SCHEMA",
    "SELECTOR_KEYS",
    "SLOT_TRANSFORMS",
    "build_messages",
    "projected_demo_payload",
    "repair_messages",
    "validate_compilation_response",
    "validate_plan",
]
