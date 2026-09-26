from __future__ import annotations

import copy

import pytest

from guiexp_android.selective_selector_repair import (
    render_bindings,
    render_bound_value,
    repair_descriptor,
)


def _plan() -> dict:
    return {
        "schema": "selective-plan/1",
        "slots": {"note_name": "the note name to delete"},
        "steps": [
            {
                "id": "open",
                "intent": "open Markor",
                "action": {"action_type": "open_app", "app_name": "Markor"},
                "target": None,
                "before": [],
                "after": [{"text": "Markor"}],
            },
            {
                "id": "delete",
                "intent": "hold the note",
                "action": {"action_type": "long_press"},
                "target": {
                    "description": {"slot": "note_name", "transform": "identity"},
                    "clickable": True,
                },
                "before": [],
                "after": [{"description": "Delete"}],
            },
            {
                "id": "confirm",
                "intent": "confirm deletion",
                "action": {"action_type": "click"},
                "target": {"text": "OK"},
                "before": [],
                "after": [{"text": "Files"}],
            },
        ],
    }


def _witness(name: str, *, after: object = True, index: int = 14) -> dict:
    return {
        "step_id": "delete",
        "extracted_bindings": {"note_name": name},
        "local_action": {"action_type": "long_press", "index": index},
        "pre_elements": [
            {"index": index, "description": f"File {name} ", "clickable": True}
        ],
        "after_guard_passed": after,
    }


def test_repairs_from_two_grounded_values_and_keeps_primary_plan_unchanged():
    plan = _plan()
    original = copy.deepcopy(plan)
    result = repair_descriptor(plan, [_witness("alpha"), _witness("beta")])

    assert result["status"] == "patched"
    assert result["plan"] == original
    assert result["patched_plan"] != original
    assert result["render_rules"] == {"note_name": {"prefix": "File ", "suffix": ""}}
    assert result["patch"]["primary"] == "render_rules"
    assert "alpha" not in repr(result["render_rules"])
    assert "beta" not in repr(result["patched_plan"])
    assert result["report"]["validation"] == {
        "pilot": {"valid": True, "errors": []},
        "runtime": {"valid": True, "errors": []},
    }
    assert plan == original


def test_inputs_and_oracle_like_fields_are_not_used():
    plan = _plan()
    evidence = [_witness("alpha"), _witness("beta")]
    poisoned = copy.deepcopy(evidence)
    for row in poisoned:
        row["final_success"] = False
        row["oracle"] = {"success": "poison"}
        row["evaluator_params"] = {"note_name": "poison"}

    clean = repair_descriptor(plan, evidence)
    poisoned_result = repair_descriptor(plan, poisoned)
    assert poisoned_result["render_rules"] == clean["render_rules"]
    assert poisoned_result["patch"] == clean["patch"]
    assert poisoned_result["provenance"]["oracle_used"] is False
    assert poisoned_result["provenance"]["evaluator_params_used"] is False
    assert evidence == [_witness("alpha"), _witness("beta")]


def test_whitespace_is_normalized_but_observed_case_is_preserved():
    first = _witness("alpha")
    first["pre_elements"][0]["description"] = "  File\talpha  "
    second = _witness("beta")
    result = repair_descriptor(_plan(), [first, second])

    assert result["status"] == "patched"
    assert result["patch"]["prefix"] == "file "
    assert result["render_rules"]["note_name"]["prefix"] == "File "
    assert render_bound_value("gamma", result["render_rules"]["note_name"]) == "File gamma"


def test_conflicting_prefix_declines_without_changing_plan():
    plan = _plan()
    second = _witness("beta")
    second["pre_elements"][0]["description"] = "Document beta"
    result = repair_descriptor(plan, [_witness("alpha"), second])

    assert result["status"] == "declined"
    assert result["report"]["reason"] == "inconsistent_rendering"
    assert result["plan"] == plan
    assert result["render_rules"] == {}


def test_nonempty_wrapped_reference_declines_a_second_repair_but_empty_wrapper_is_identity():
    already_wrapped = _plan()
    already_wrapped["steps"][1]["target"]["description"] = {
        "slot": "note_name",
        "transform": "identity",
        "prefix": "File ",
        "suffix": "",
    }
    original = copy.deepcopy(already_wrapped)
    declined = repair_descriptor(already_wrapped, [_witness("alpha"), _witness("beta")])
    assert declined["status"] == "declined"
    assert declined["report"]["reason"] == "already_wrapped_reference"
    assert declined["plan"] == original
    assert declined["render_rules"] == {}

    empty_wrapper = _plan()
    empty_wrapper["steps"][1]["target"]["description"] = {
        "slot": "note_name",
        "transform": "identity",
        "prefix": "",
        "suffix": "",
    }
    patched = repair_descriptor(empty_wrapper, [_witness("alpha"), _witness("beta")])
    assert patched["status"] == "patched"
    assert patched["render_rules"] == {"note_name": {"prefix": "File ", "suffix": ""}}


@pytest.mark.parametrize(
    "change, reason",
    [
        (lambda p: p["steps"][1]["target"]["description"].update(transform="stem"), "no_identity_descriptor_slot"),
        (lambda p: p["steps"][1]["before"].append({"description": {"slot": "note_name", "transform": "identity"}}), "slot_used_outside_same_descriptor_field"),
        (lambda p: p["steps"][1]["target"].update(text={"slot": "note_name", "transform": "identity"}), "multiple_descriptor_fields"),
    ],
)
def test_incompatible_slot_use_declines(change, reason):
    plan = _plan()
    change(plan)
    result = repair_descriptor(plan, [_witness("alpha"), _witness("beta")])

    assert result["status"] == "declined"
    assert result["report"]["reason"] == reason
    assert result["plan"] == plan


def test_requires_verified_witnesses_and_distinct_bindings():
    plan = _plan()
    assert repair_descriptor(plan, [_witness("alpha", after=False), _witness("alpha")])["report"]["reason"] == "insufficient_distinct_bindings"
    assert repair_descriptor(plan, [_witness("alpha"), _witness("alpha")])["report"]["reason"] == "insufficient_distinct_bindings"


def test_duplicate_index_and_duplicate_binding_text_are_ambiguous():
    duplicate_index = _witness("alpha")
    duplicate_index["pre_elements"].append({"index": 14, "description": "Other alpha"})
    result = repair_descriptor(_plan(), [duplicate_index, _witness("beta")])
    assert result["report"]["reason"] == "ambiguous_local_index"

    repeated_value = _witness("alpha")
    repeated_value["pre_elements"][0]["description"] = "File alpha alpha"
    result = repair_descriptor(_plan(), [repeated_value, _witness("beta")])
    assert result["report"]["reason"] == "binding_not_observed_once"


def test_renderer_detaches_bindings_and_does_not_invent_missing_values():
    bindings = {"note_name": "gamma", "other": "keep"}
    rules = {"note_name": {"prefix": "File ", "suffix": ".md"}}
    original = copy.deepcopy(bindings)
    assert render_bindings(bindings, rules) == {"note_name": "File gamma.md", "other": "keep"}
    assert bindings == original
    assert render_bindings({}, rules) == {}
    with pytest.raises(ValueError):
        render_bound_value("", rules["note_name"])
