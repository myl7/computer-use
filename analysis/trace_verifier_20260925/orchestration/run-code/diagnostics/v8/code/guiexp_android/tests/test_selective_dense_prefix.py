"""Offline tests for conservative dense-prefix derivation."""

from __future__ import annotations

import copy
import json

from guiexp_android import selective_pilot as pilot
from guiexp_android.selective_dense_prefix import derive_dense_prefix, load_saved_candidate


def _ax(index: int, text: str) -> str:
    return f'UI element {index}: ' + json.dumps({"index": index, "text": text})


def _plan(step_count: int = 4) -> dict:
    return {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "fictional value"},
        "steps": [
            {
                "id": f"step_{index}",
                "intent": f"perform fictional step {index}",
                "action": {"action_type": "click"},
                "target": {"text": f"Before {index}"},
                "before": [{"text": f"Before {index}"}],
                "after": [{"text": f"After {index}"}],
            }
            for index in range(1, step_count + 1)
        ],
    }


def _wrapper(source_steps: dict[str, int], plan: dict | None = None) -> dict:
    plan = plan or _plan(len(source_steps))
    return {
        "schema": "selective-prefix/1",
        "plan": plan,
        "source_steps": source_steps,
        "terminal_source_step": max(source_steps.values()),
        "handoff_policy": "reactive",
    }


def _demos() -> list[dict]:
    return [
        {
            "steps": [
                {
                    "step": index,
                    "action": {"action_type": "click", "index": index - 1},
                    "pre_obs": {"ax_tree_text": _ax(index - 1, f"Before {index}")},
                    "post_obs": {"ax_tree_text": _ax(index - 1, f"After {index}")},
                }
                for index in range(1, 5)
            ]
        }
    ]


def test_v9_style_gap_keeps_only_dense_leading_prefix_and_revalidates_guards():
    candidate = _wrapper({"step_1": 1, "step_2": 2, "step_3": 3, "step_4": 13})
    result = derive_dense_prefix(
        candidate,
        _demos(),
        source_reference={"version": "projected_v9_create_prefix"},
        source_raw_sha256="raw-hash",
    )
    assert result["valid"] is True
    assert result["source_steps_original"] == {"step_1": 1, "step_2": 2, "step_3": 3, "step_4": 13}
    assert result["source_steps_retained"] == {"step_1": 1, "step_2": 2, "step_3": 3}
    assert result["terminal_source_step"] == 3
    assert result["cut_reason"]["expected_source_step"] == 4
    assert result["cut_reason"]["observed_source_step"] == 13
    assert result["guard_evidence"]["status"] == "valid"
    assert result["source_raw_sha256"] == "raw-hash"
    assert result["source_reference"]["version"] == "projected_v9_create_prefix"


def test_dense_derivation_allows_literal_file_prefix_with_no_slots():
    candidate = _wrapper({"step_1": 1})
    result = derive_dense_prefix(candidate, _demos())
    assert result["valid"] is True
    assert result["prefix_response"]["plan"]["slots"] == {}
    assert candidate["plan"]["slots"] == {"value": "fictional value"}


def test_dense_derivation_retains_a_slot_used_only_by_a_wrapped_selector():
    plan = _plan(1)
    wrapped = {"slot": "value", "transform": "identity", "prefix": "Label: ", "suffix": ""}
    plan["steps"][0]["target"] = {"text": wrapped}
    plan["steps"][0]["before"] = [{"text": wrapped}]
    candidate = _wrapper({"step_1": 1}, plan)
    demos = [{"steps": [{
        "step": 1,
        "action": {"action_type": "click", "index": 0},
        "pre_obs": {"ax_tree_text": _ax(0, "Label: fictional value")},
        "post_obs": {"ax_tree_text": _ax(0, "After 1")},
    }]}]

    result = derive_dense_prefix(candidate, demos)
    assert result["valid"] is True
    assert result["prefix_response"]["plan"]["slots"] == {"value": "fictional value"}


def test_dense_derivation_stops_without_inventing_a_prefix_when_step_one_is_missing():
    result = derive_dense_prefix(_wrapper({"step_1": 2, "step_2": 3}), _demos())
    assert result["valid"] is False
    assert "dense leading" in result["errors"][0]


def test_dense_derivation_stops_before_a_bad_guard_without_inventing_proof():
    candidate = _wrapper({"step_1": 1, "step_2": 2, "step_3": 9})
    bad_plan = copy.deepcopy(candidate["plan"])
    bad_plan["steps"][1]["after"] = [{"text": "Before 2"}]
    candidate["plan"] = bad_plan
    result = derive_dense_prefix(candidate, _demos())
    assert result["valid"] is True
    assert result["source_steps_retained"] == {"step_1": 1}
    assert result["cut_reason"]["reason"] == "first_guard_evidence_invalid"


def test_dense_derivation_salvages_valid_leading_steps_before_later_schema_error():
    candidate = _wrapper({"step_1": 1, "step_2": 2, "step_3": 3, "step_4": 4})
    candidate["plan"]["steps"][3]["action"] = {"action_type": "keyboard_enter"}
    candidate["plan"]["steps"][3]["target"] = None
    candidate["plan"]["steps"][3]["before"] = []
    result = derive_dense_prefix(candidate, _demos())
    assert result["valid"] is True
    assert result["source_steps_retained"] == {"step_1": 1, "step_2": 2, "step_3": 3}
    assert result["cut_reason"]["reason"] == "first_invalid_plan_step"
    assert result["cut_reason"]["plan_position"] == 4


def test_load_saved_candidate_preserves_the_saved_response_file_hash(tmp_path):
    candidate = _wrapper({"step_1": 1, "step_2": 2})
    path = tmp_path / "first_response.json"
    path.write_text(json.dumps({"raw_text": json.dumps(candidate)}), encoding="utf-8")
    loaded, digest = load_saved_candidate(path)
    assert loaded == candidate
    import hashlib

    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
