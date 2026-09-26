"""Pure tests for the explicit verified-prefix contract."""

from __future__ import annotations

import json

from guiexp_android import selective_plan_contract as contract
from guiexp_android.selective_prefix import validate_prefix_response


def _ax(index: int, text: str, **flags: object) -> str:
    node = {"index": index, "text": text, **flags}
    return f"UI element {index}: " + json.dumps(node)


def _plan() -> dict:
    return {
        "schema": contract.PLAN_SCHEMA,
        "slots": {"label": "the fictional visible label"},
        "steps": [
            {
                "id": "open_settings",
                "intent": "open Settings",
                "action": {"action_type": "open_app", "app_name": "Settings"},
                "target": None,
                "before": [],
                "after": [{"text": "Settings"}],
            },
            {
                "id": "tap_label",
                "intent": "tap the requested label",
                "action": {"action_type": "click"},
                "target": {"text": {"slot": "label", "transform": "identity"}},
                "before": [],
                "after": [{"text": "Completed"}],
            },
        ],
    }


def _demos() -> list[dict]:
    return [
        {
            "steps": [
                {
                    "step": 1,
                    "action": {"action_type": "open_app", "app_name": "Settings"},
                    "pre_obs": {"ax_tree_text": ""},
                    "post_obs": {"ax_tree_text": _ax(0, "Settings")},
                },
                {
                    "step": 2,
                    "action": {"action_type": "click", "index": 0},
                    "pre_obs": {"ax_tree_text": _ax(0, "Desired")},
                    "post_obs": {"ax_tree_text": _ax(0, "Completed")},
                },
            ]
        }
    ]


def _wrapper(plan: dict, **changes: object) -> dict:
    value = {
        "schema": "selective-prefix/1",
        "plan": plan,
        "source_steps": {step["id"]: index for index, step in enumerate(plan["steps"], 1)},
        "terminal_source_step": len(plan["steps"]),
        "handoff_policy": "reactive",
    }
    value.update(changes)
    return value


def test_valid_prefix_returns_alignment_and_full_demo_evidence():
    result = validate_prefix_response(_wrapper(_plan()), _demos())
    assert result["valid"] is True
    assert result["terminal_source_step"] == 2
    assert result["handoff_policy"] == "reactive"
    assert result["alignment"]["steps"]["tap_label"] == [{"source_step": 2}]
    assert result["guard_evidence"]["status"] == "valid"


def test_wrapped_selector_passes_full_raw_guard_evidence():
    plan = _plan()
    plan["steps"][1]["target"] = {
        "text": {
            "slot": "label",
            "transform": "identity",
            "prefix": "Label: ",
            "suffix": "",
        }
    }
    demos = _demos()
    demos[0]["steps"][1]["pre_obs"]["ax_tree_text"] = _ax(0, "Label: Desired")
    result = validate_prefix_response(_wrapper(plan), demos)
    assert result["valid"] is True
    assert result["guard_evidence"]["status"] == "valid"


def test_prefix_rejects_bad_alignment_or_terminal_boundary():
    plan = _plan()
    for source_steps, terminal in (
        ({"open_settings": 1}, 1),
        ({"open_settings": 1, "tap_label": 1}, 1),
        ({"open_settings": 2, "tap_label": 1}, 1),
        ({"open_settings": 1, "tap_label": 2}, 1),
    ):
        value = _wrapper(plan, source_steps=source_steps, terminal_source_step=terminal)
        assert validate_prefix_response(value)["valid"] is False


def test_prefix_rejects_invariant_save_guard_without_oracle_inference():
    plan = {
        "schema": contract.PLAN_SCHEMA,
        "slots": {},
        "steps": [
            {
                "id": "press_save",
                "intent": "press Save",
                "action": {"action_type": "click"},
                "target": {"description": "Save"},
                "before": [],
                "after": [{"description": "Save"}],
            }
        ],
    }
    demos = [
        {
            "steps": [
                {
                    "step": 1,
                    "action": {"action_type": "click", "index": 0},
                    "pre_obs": {"ax_tree_text": _ax(0, "", content_description="Save")},
                    "post_obs": {"ax_tree_text": _ax(0, "", content_description="Save")},
                }
            ]
        }
    ]
    result = validate_prefix_response(_wrapper(plan), demos)
    assert result["valid"] is False
    assert result["guard_evidence"]["status"] == "invalid"
    assert "unchanged context" in " ".join(result["guard_evidence"]["errors"])
