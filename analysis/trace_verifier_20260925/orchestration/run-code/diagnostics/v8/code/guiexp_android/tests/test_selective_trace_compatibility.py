from __future__ import annotations

import copy
import json

from guiexp_android.selective_trace_compatibility import check_prefix


def _ax(nodes: list[dict]) -> str:
    return "\n".join(f"UI element {node['index']}: {json.dumps(node)}" for node in nodes)


def _plan(*, after: list[dict] | None = None, two_steps: bool = False) -> dict:
    first = {
        "id": "type_name",
        "intent": "type the supplied name",
        "action": {
            "action_type": "input_text",
            "text": {"slot": "name", "transform": "identity"},
        },
        "target": {"hint": "Name", "editable": True},
        "before": [{"hint": "Name", "editable": True}],
        "after": after or [{"text": {"slot": "name", "transform": "identity"}, "editable": True}],
    }
    steps = [first]
    if two_steps:
        steps.append({
            "id": "finish",
            "intent": "finish",
            "action": {"action_type": "click"},
            "target": {"text": "Done"},
            "before": [{"text": "Done"}],
            "after": [{"text": "Finished"}],
        })
    return {"schema": "selective-plan/1", "slots": {"name": "public name"}, "steps": steps}


def _record(
    source_step: int = 1,
    *,
    value: str = "correct",
    actual_action: dict | None = None,
    pre_nodes: list[dict] | None = None,
    post_nodes: list[dict] | None = None,
) -> dict:
    pre_nodes = pre_nodes or [{"index": 3, "hint": "Name", "editable": True}]
    post_nodes = post_nodes or [{"index": 3, "text": value, "hint": "Name", "editable": True}]
    return {
        "step": source_step,
        "action": actual_action or {"action_type": "input_text", "text": value, "index": 3},
        "pre_obs": {"url": "app.Before", "ax_tree_text": _ax(pre_nodes)},
        "post_obs": {"url": "app.After", "ax_tree_text": _ax(post_nodes)},
    }


def test_correct_goal_extracted_binding_matches_after_resolution():
    result = check_prefix(_plan(), {"name": "correct"}, [_record()])

    assert result["status"] == "compatible"
    assert result["counterfactual_ui_success"] is None
    assert result["task_success_evaluated"] is False
    assert result["prefix_steps_matched"] == 1
    assert result["first_divergence"] is None
    detail = result["steps"][0]
    assert detail["status"] == "matched"
    assert detail["resolution"]["resolved_action"] == {
        "action_type": "input_text",
        "text": "correct",
        "index": 3,
    }
    assert detail["action_comparison"]["resolved"] == detail["action_comparison"]["recorded"]


def test_wrapped_selector_resolves_with_raw_goal_binding_and_missing_slot_is_early():
    wrapped_plan = _plan(after=[{"text": "Done"}])
    wrapped_plan["steps"][0]["target"] = {
        "text": {
            "slot": "name",
            "transform": "identity",
            "prefix": "Delete ",
            "suffix": "",
        }
    }
    record = _record(
        value="Done",
        actual_action={"action_type": "input_text", "text": "trainingname", "index": 3},
        pre_nodes=[{"index": 3, "text": "Delete trainingname", "hint": "Name", "editable": True}],
        post_nodes=[{"index": 4, "text": "Done"}],
    )
    result = check_prefix(wrapped_plan, {"name": "trainingname"}, [record])
    assert result["status"] == "compatible"
    assert result["prefix_steps_matched"] == 1
    assert result["steps"][0]["resolution"]["resolved_action"] == {
        "action_type": "input_text",
        "text": "trainingname",
        "index": 3,
    }

    missing = check_prefix(wrapped_plan, {}, [record])
    assert missing["status"] == "invalid_input"
    assert missing["first_divergence"]["kind"] == "missing_binding"
    assert missing["steps"] == []


def test_literal_parameter_difference_is_an_action_argument_mismatch():
    plan = _plan()
    plan["steps"][0]["action"]["text"] = "original"
    plan["steps"][0]["after"] = [{"text": "Ready"}]
    result = check_prefix(
        plan,
        {"name": "unused"},
        [_record(value="renamed", post_nodes=[{"index": 3, "text": "Ready", "hint": "Name", "editable": True}])],
    )

    assert result["first_divergence"]["kind"] == "action_argument_mismatch"
    assert result["first_divergence"]["details"]["fields"] == ["text"]


def test_wrong_supplied_binding_is_not_silently_corrected():
    plan = _plan(after=[{"text": "Ready"}])
    result = check_prefix(
        plan,
        {"name": "renamed"},
        [_record(value="correct", post_nodes=[{"index": 3, "text": "Ready", "hint": "Name", "editable": True}])],
    )

    assert result["first_divergence"]["kind"] == "action_argument_mismatch"
    assert result["steps"][0]["resolution"]["resolved_action"]["text"] == "renamed"
    assert result["steps"][0]["recorded_action"]["text"] == "correct"


def test_nonunique_target_abstains_as_target_mismatch():
    duplicate = [
        {"index": 3, "hint": "Name", "editable": True},
        {"index": 4, "hint": "Name", "editable": True},
    ]
    result = check_prefix(_plan(), {"name": "correct"}, [_record(pre_nodes=duplicate)])

    assert result["status"] == "diverged"
    assert result["prefix_steps_matched"] == 0
    assert result["first_divergence"]["kind"] == "target_mismatch"
    assert result["steps"][0]["before_guard"]["guards"][0]["state"] == "ambiguous"


def test_missing_after_guard_is_guard_not_applicable():
    result = check_prefix(
        _plan(after=[{"text": "Ready"}]),
        {"name": "correct"},
        [_record(value="correct")],
    )

    assert result["first_divergence"]["kind"] == "guard_not_applicable"
    assert result["steps"][0]["after_guard"]["guards"][0]["state"] == "missing"


def test_malformed_ax_is_parse_unknown_and_does_not_use_partial_nodes():
    malformed = (
        'UI element 3: {"index": 3, "hint": "Name", "editable": true}\n'
        'UI element 4: {"index": 4, "text": "bad"quote"}'
    )
    record = _record()
    record["pre_obs"]["ax_tree_text"] = malformed
    result = check_prefix(_plan(), {"name": "correct"}, [record])

    assert result["status"] == "unknown"
    assert result["first_divergence"]["kind"] == "parse_unknown"
    assert result["steps"][0]["parse"]["pre"]["complete"] is False
    assert "resolution" not in result["steps"][0]


def test_only_contiguous_prefix_is_counted_after_first_divergence():
    plan = _plan(after=[{"text": "Ready"}], two_steps=True)
    first = _record(value="correct")
    first["post_obs"]["ax_tree_text"] = _ax([{"index": 3, "text": "Not ready", "hint": "Name", "editable": True}])
    later = _record(2, value="ignored")
    later["pre_obs"]["ax_tree_text"] = "not parsed after divergence"
    result = check_prefix(plan, {"name": "correct"}, [first, later])

    assert result["prefix_steps_matched"] == 0
    assert len(result["steps"]) == 1
    assert result["steps"][0]["step_id"] == "type_name"
    assert result["first_divergence"]["source_step"] == 1


def test_target_index_difference_is_not_ignored():
    actual = _record(actual_action={"action_type": "input_text", "text": "correct", "index": 4})
    result = check_prefix(_plan(), {"name": "correct"}, [actual])

    assert result["first_divergence"]["kind"] == "target_mismatch"
    assert result["first_divergence"]["details"]["field"] == "index"


def test_plan_and_bindings_validate_before_trace_evaluation():
    invalid_plan = _plan()
    invalid_plan["schema"] = "other"
    result = check_prefix(invalid_plan, {"name": "correct"}, ["would be ignored"])
    assert result["status"] == "invalid_input"
    assert result["counterfactual_ui_success"] is None
    assert result["task_success_evaluated"] is False
    assert result["first_divergence"]["kind"] == "invalid_plan"
    assert result["steps"] == []

    missing = check_prefix(_plan(), {}, [_record()])
    assert missing["status"] == "invalid_input"
    assert missing["first_divergence"]["kind"] == "missing_binding"
    assert missing["steps"] == []


def test_inputs_and_results_are_detached():
    plan = _plan()
    bindings = {"name": "correct"}
    records = [_record()]
    originals = copy.deepcopy((plan, bindings, records))
    result = check_prefix(plan, bindings, records)

    result["steps"][0]["recorded_action"]["text"] = "changed"
    result["limitations"].clear()
    assert (plan, bindings, records) == originals


def test_missing_recorded_step_is_trace_missing():
    result = check_prefix(_plan(), {"name": "correct"}, [])
    assert result["status"] == "diverged"
    assert result["first_divergence"]["kind"] == "trace_missing"
    assert result["prefix_steps_matched"] == 0
