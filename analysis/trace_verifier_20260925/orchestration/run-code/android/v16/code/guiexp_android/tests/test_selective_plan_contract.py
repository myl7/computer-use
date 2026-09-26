"""Contract and prompt checks for projected plan generation."""

from __future__ import annotations

import json

import pytest

from guiexp_android import selective_pilot as pilot
from guiexp_android import selective_runtime as runtime
from guiexp_android import selective_plan_contract as contract
from guiexp_android.selective_guard_evidence import validate_guard_evidence


def test_fictional_example_passes_both_plan_validators():
    pilot_plan = pilot.validate_plan(contract.FICTIONAL_EXAMPLE)
    runtime_report = runtime.validate_plan(contract.FICTIONAL_EXAMPLE)
    assert pilot_plan["schema"] == contract.PLAN_SCHEMA
    assert runtime_report["valid"] is True
    assert runtime_report["plan"] == pilot_plan
    wrapper_report = contract.validate_compilation_response(contract.FICTIONAL_COMPILATION_EXAMPLE)
    assert wrapper_report["valid"] is True
    assert wrapper_report["source_steps"] == {"open_settings": 1, "tap_label": 2}


def test_literal_plan_with_empty_slots_passes_both_plan_validators():
    plan = {
        "schema": contract.PLAN_SCHEMA,
        "slots": {},
        "steps": [{
            "id": "open_settings",
            "intent": "open Settings",
            "action": {"action_type": "open_app", "app_name": "Settings"},
            "target": None,
            "before": [],
            "after": [{"text": "Settings"}],
        }],
    }
    pilot_plan = pilot.validate_plan(plan)
    runtime_report = runtime.validate_plan(plan)
    contract_report = contract.validate_plan(plan)
    assert pilot_plan["slots"] == {}
    assert runtime_report["valid"] is True
    assert contract_report["valid"] is True


def test_contract_documents_and_accepts_optional_literal_wrappers():
    reference = contract.CONTRACT["slot_value"]["reference"]
    assert reference["exact_keys"] == ["slot", "transform"]
    assert reference["optional_keys"] == ["prefix", "suffix"]
    assert reference["allowed_keys"] == ["slot", "transform", "prefix", "suffix"]
    assert "including its leading dot" in reference["transform_semantics"]["suffix"]
    assert "single leading-dot file" in reference["transform_semantics"]["suffix"]

    plan = {
        "schema": contract.PLAN_SCHEMA,
        "slots": {"label": "the requested label"},
        "steps": [{
            "id": "enter",
            "intent": "enter the requested label",
            "action": {
                "action_type": "input_text",
                "text": {"slot": "label", "transform": "identity"},
            },
            "target": {
                "text": {
                    "slot": "label",
                    "transform": "identity",
                    "prefix": "Label: ",
                    "suffix": "",
                },
                "editable": True,
            },
            "before": [],
            "after": [{"text": "Completed"}],
        }],
    }
    assert contract.validate_plan(plan)["valid"] is True
    assert runtime.validate_plan(plan)["valid"] is True


def test_compilation_wrapper_rejects_missing_duplicate_or_reordered_alignment():
    for source_steps in (
        {"open_settings": 1},
        {"open_settings": 1, "tap_label": 1},
        {"open_settings": 2, "tap_label": 1},
    ):
        bad = json.loads(json.dumps(contract.FICTIONAL_COMPILATION_EXAMPLE))
        bad["source_steps"] = source_steps
        assert contract.validate_compilation_response(bad)["valid"] is False


def test_guard_evidence_rejects_wrong_step_id_alignment():
    demos = [
        {
            "steps": [
                {
                    "step": 1,
                    "action": {"action_type": "open_app"},
                    "pre_obs": {"ax_tree_text": ""},
                    "post_obs": {"ax_tree_text": ""},
                },
                {
                    "step": 2,
                    "action": {"action_type": "click", "index": 0},
                    "pre_obs": {"ax_tree_text": "UI element 0: {\"index\":0,\"text\":\"Ready\"}"},
                    "post_obs": {"ax_tree_text": "UI element 0: {\"index\":0,\"text\":\"Completed\"}"},
                },
            ]
        }
    ]
    wrong = {"steps": {"open_settings": [{"source_step": 2}], "tap_label": [{"source_step": 1}]}}
    report = validate_guard_evidence(contract.FICTIONAL_EXAMPLE, demos, alignment=wrong)
    assert report["status"] != "valid"


def test_contract_rejects_runtime_unsafe_selector_and_interpolation():
    bad = json.loads(json.dumps(contract.FICTIONAL_EXAMPLE))
    bad["steps"][0]["after"] = [{"contains": "Settings"}]
    with pytest.raises(pilot.PlanInvalid):
        pilot.validate_plan(bad)
    bad = json.loads(json.dumps(contract.FICTIONAL_EXAMPLE))
    bad["steps"][1]["target"] = {"text": "{{slot:label|identity}}"}
    assert runtime.validate_plan(bad)["valid"] is True
    assert contract.validate_plan(bad)["valid"] is False


def test_builder_and_repair_messages_keep_projected_payload_and_full_contract():
    demos = [
        {
            "goal_text": "Set a fictional label.",
            "steps": [
                {
                    "step": 1,
                    "action": {"action_type": "open_app", "app_name": "Settings"},
                    "pre_obs": {"url": "before", "ax_tree_text": "Before"},
                    "post_obs": {"url": "after", "ax_tree_text": "After"},
                }
            ],
        }
    ]
    messages = contract.build_messages("MarkorDeleteNote", demos)
    assert len(messages) == 2
    assert "COMPLETE PLAN CONTRACT" in messages[1]["content"]
    assert "fictional_valid_example" in messages[1]["content"]
    assert "{{slot:name|identity}}" in messages[1]["content"]
    payload_text = json.dumps(
        contract.projected_demo_payload("MarkorDeleteNote", demos), ensure_ascii=True, indent=2
    )
    assert payload_text in messages[1]["content"]
    assert payload_text in pilot.build_prompt("MarkorDeleteNote", demos)[1]["content"]
    repaired = contract.repair_messages(messages, "{}", "bad selector")
    assert [message["role"] for message in repaired] == ["system", "user", "assistant", "user"]
    assert "COMPLETE PLAN CONTRACT" in repaired[1]["content"]
    assert "bad selector" in repaired[3]["content"]
