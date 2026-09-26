"""Focused tests for the zero-model literal trace control."""

from __future__ import annotations

import copy
import json

import pytest

from guiexp_android import selective_literal_control as control


def _ax(*nodes: dict) -> str:
    return "\n".join(
        f"UI element {node['index']}: " + json.dumps(node, sort_keys=True)
        for node in nodes
    )


def _step(number: int, action: dict, pre: list[dict], post: list[dict]) -> dict:
    return {
        "step": number,
        "action": action,
        "pre_obs": {"url": "app", "ax_tree_text": _ax(*pre)},
        "post_obs": {"url": "app", "ax_tree_text": _ax(*post)},
    }


def _demo(*steps: dict) -> dict:
    return {
        "family": "FictionalFamily",
        "goal_text": "Tap the requested label.",
        "steps": list(steps),
    }


def test_unique_observed_selector_prefers_visible_literal_and_rejects_ambiguity():
    nodes = [
        {"index": 0, "text": "Save", "is_clickable": True},
        {"index": 1, "text": "Cancel", "is_clickable": True},
    ]
    assert control.unique_observed_selector(nodes) == {"text": "Save"}
    ambiguous = [
        {"index": 0, "text": "Save", "is_clickable": True},
        {"index": 1, "text": "Save", "is_clickable": True},
    ]
    assert control.unique_observed_selector(ambiguous) is None


def test_no_observable_effect_causes_first_step_abstention():
    demo = _demo(
        _step(
            1,
            {"action_type": "open_app", "app_name": "Fictional"},
            [{"index": 0}],
            [{"index": 0}],
        )
    )
    result = control.build_literal_prefix(demo)
    assert result["status"] == "abstained"
    assert result["prefix_length"] == 0
    assert result["stop"] == {
        "source_step": 1,
        "reason": "after_witness_unavailable_or_nonunique",
    }


def test_ambiguous_target_stops_after_the_valid_leading_step():
    demo = _demo(
        _step(
            1,
            {"action_type": "open_app", "app_name": "Fictional"},
            [{"index": 0}],
            [{"index": 0, "text": "Ready"}],
        ),
        _step(
            2,
            {"action_type": "click", "index": 0},
            [
                {"index": 0, "text": "Save", "is_clickable": True},
                {"index": 1, "text": "Save", "is_clickable": True},
            ],
            [{"index": 0, "text": "Done"}],
        ),
    )
    result = control.build_literal_prefix(demo)
    assert result["valid"] is True
    assert result["prefix_length"] == 1
    assert result["stop"] == {
        "source_step": 2,
        "reason": "target_descriptor_unavailable_or_nonunique",
    }


def test_literal_candidate_fails_a_renamed_heldout_action_value():
    training = _demo(
        _step(
            1,
            {"action_type": "open_app", "app_name": "Fictional"},
            [{"index": 0}],
            [{"index": 0, "text": "Ready"}],
        ),
        _step(
            2,
            {"action_type": "input_text", "index": 1, "text": "alpha"},
            [{"index": 1, "text": "field", "is_editable": True}],
            [{"index": 1, "text": "alpha", "is_editable": True}],
        ),
    )
    heldout = copy.deepcopy(training)
    heldout["steps"][1]["action"]["text"] = "beta"
    heldout["steps"][1]["post_obs"]["ax_tree_text"] = _ax(
        {"index": 1, "text": "beta", "is_editable": True}
    )
    built = control.build_literal_prefix(training)
    comparison = control.compare_literal_to_heldout(
        built,
        heldout,
        {"family": "FictionalFamily", "path": "heldout.jsonl"},
    )
    assert built["valid"] is True
    assert built["prefix_length"] == 2
    assert comparison["valid"] is False
    assert comparison["task_success_evaluated"] is False
    assert any(
        "recorded action field 'text'" in error
        for error in comparison["mismatches"][0]["errors"]
    )


def test_build_does_not_mutate_public_demo():
    demo = _demo(
        _step(
            1,
            {"action_type": "open_app", "app_name": "Fictional"},
            [{"index": 0}],
            [{"index": 0, "text": "Ready"}],
        )
    )
    original = copy.deepcopy(demo)
    control.build_literal_prefix(demo)
    assert demo == original


def test_run_control_reproduces_public_training_lengths_and_writes_frozen_artifacts(
    tmp_path,
):
    out = tmp_path / "literal_v1"
    result = control.run_control(control.DEFAULT_ORIGIN, out)
    assert result["diagnostic_only"]["reproduction_matches"] is True
    assert {
        row["family"]: row["prefix_length"] for row in result["training"]
    } == control.PRIOR_OBSERVED_REFERENCE_LENGTHS
    assert len(result["heldout_literal_comparisons"]) == 6
    assert all(not row["valid"] for row in result["heldout_literal_comparisons"])
    assert {"config.json", "source_manifest.json", "results.json"} == {
        path.name for path in out.iterdir()
    }
    saved = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert saved["oracle_inputs"] == []
    assert saved["private_inputs"] == []
    with pytest.raises(control.ControlStop, match="already exists"):
        control.run_control(control.DEFAULT_ORIGIN, out)
