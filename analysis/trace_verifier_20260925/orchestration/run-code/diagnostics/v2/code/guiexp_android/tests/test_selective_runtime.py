"""Offline tests for the data-only selective plan runtime."""

from __future__ import annotations

import copy
import json

import pytest

from guiexp_android.budget_client import BudgetStop
from guiexp_android.selective_runtime import (
    resolve_step,
    resolve_value,
    run_plan,
    validate_plan,
)


_DEFAULT = object()


def _plan(*, after=None, before=None, action=None, target=_DEFAULT, slots=None):
    return {
        "schema": "selective-plan/1",
        "slots": {} if slots is None else slots,
        "steps": [{
            "id": "save",
            "intent": "save the form",
            "action": {"action_type": "click"} if action is None else action,
            "target": {"text": "Save"} if target is _DEFAULT else target,
            "before": [] if before is None else before,
            "after": [{"text": "Done"}] if after is None else after,
        }],
    }


class FakeAdapter:
    def __init__(self, screen, transitions=None, error=False):
        self.screen = copy.deepcopy(screen)
        self.transitions = list(transitions or [])
        self.error = error
        self.executed = []
        self.observe_count = 0

    def elements(self):
        return copy.deepcopy(self.screen)

    def observe(self):
        self.observe_count += 1
        return {"visible": [e.get("text", "") for e in self.screen]}

    def execute(self, action):
        self.executed.append(copy.deepcopy(action))
        if self.error:
            raise RuntimeError("side effect may have happened")
        if self.transitions:
            self.screen = copy.deepcopy(self.transitions.pop(0))


def test_duplicate_target_is_not_resolved_and_current_index_is_added():
    step = _plan()["steps"][0]
    elements = [
        {"index": 4, "text": "Save", "clickable": True},
        {"index": 9, "text": "Save", "clickable": True},
    ]
    assert resolve_step(step, elements, {}) is None
    resolved = resolve_step(step, [elements[1]], {})
    assert resolved == {"action_type": "click", "index": 9}
    assert "index" not in step["action"]


def test_slot_resolution_is_declared_and_transforms_are_data_only():
    bindings = {"filename": "notes/today.txt", "name": "Ada Lovelace"}
    assert resolve_value("literal", bindings) == "literal"
    assert resolve_value({"slot": "filename", "transform": "identity"}, bindings) == "notes/today.txt"
    assert resolve_value({"slot": "filename", "transform": "stem"}, bindings) == "today"
    assert resolve_value({"slot": "filename", "transform": "suffix"}, bindings) == ".txt"
    assert resolve_value({"slot": "name", "transform": "first_word"}, bindings) == "Ada"
    assert resolve_value({"slot": "name", "transform": "last_word"}, bindings) == "Lovelace"
    with pytest.raises(ValueError, match="unresolved slot"):
        resolve_value({"slot": "missing", "transform": "identity"}, bindings)
    with pytest.raises(ValueError, match="unknown value transform"):
        resolve_value({"slot": "name", "transform": "eval"}, bindings)


def test_slot_resolution_applies_literal_wrappers_after_the_transform():
    bindings = {"filename": "notes/today.txt", "name": "Ada Lovelace"}
    assert resolve_value(
        {
            "slot": "filename",
            "transform": "stem",
            "prefix": "File ",
            "suffix": ".md",
        },
        bindings,
    ) == "File today.md"
    assert resolve_value(
        {"slot": "name", "transform": "identity", "prefix": "", "suffix": ""},
        bindings,
    ) == "Ada Lovelace"


def test_wrapped_identity_can_drive_raw_input_and_wrapped_selector_once():
    adapter = FakeAdapter(
        [{"index": 0, "text": "Delete trainingname", "editable": True}],
        transitions=[[{"index": 1, "text": "Done"}]],
    )
    reference = {
        "slot": "goal_note_name",
        "transform": "identity",
        "prefix": "Delete ",
        "suffix": "",
    }
    plan = _plan(
        slots={"goal_note_name": "the requested note name"},
        action={
            "action_type": "input_text",
            "text": {"slot": "goal_note_name", "transform": "identity"},
        },
        target={"text": reference, "editable": True},
        after=[{"text": "Done"}],
    )
    assert resolve_value(reference, {"goal_note_name": "trainingname"}) == "Delete trainingname"
    result = run_plan(plan, {"goal_note_name": "trainingname"}, adapter, None, "full_fallback")
    assert result["status"] == "complete"
    assert adapter.executed == [{"action_type": "input_text", "text": "trainingname", "index": 0}]


def test_wrapped_value_specs_require_exact_fields_and_string_wrappers():
    slots = {"value": "the supplied value"}
    for malformed in (
        {"slot": "value", "transform": "identity", "extra": "unsafe"},
        {"slot": "value", "prefix": "missing transform"},
        {"slot": "value", "transform": "identity", "prefix": 1},
        {"slot": "value", "transform": "identity", "suffix": None},
    ):
        report = validate_plan(_plan(slots=slots, target={"text": malformed}))
        assert report["valid"] is False

    with pytest.raises(ValueError, match="prefix"):
        resolve_value(
            {"slot": "value", "transform": "identity", "prefix": 1},
            {"value": "raw"},
        )


def test_schema_and_unsafe_fields_are_rejected_without_adapter_access():
    unsafe = _plan(action={"action_type": "click", "index": 7})
    unsafe["steps"][0]["target"]["resource_id"] = "save_button"
    report = validate_plan(unsafe)
    assert report["valid"] is False
    assert any("unsafe" in error for error in report["errors"])

    wrong_schema = _plan()
    wrong_schema["schema"] = "other/1"
    assert validate_plan(wrong_schema)["valid"] is False

    unresolved = _plan(
        target={"text": {"slot": "missing", "transform": "identity"}},
    )
    assert validate_plan(unresolved)["valid"] is False


def test_targetless_non_launch_actions_require_before_guard():
    plan = _plan(
        action={"action_type": "wait"},
        target=None,
        before=[],
    )
    assert validate_plan(plan)["valid"] is False

    launch = _plan(
        action={"action_type": "open_app", "app_name": "Markor"},
        target=None,
        before=[],
    )
    assert validate_plan(launch)["valid"] is True


def test_after_guard_is_checked_on_post_action_observation(tmp_path):
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[[{"index": 6, "text": "Done"}]],
    )
    out_path = tmp_path / "trace.json"
    result = run_plan(_plan(), {}, adapter, None, "full_fallback", out_path=out_path)
    assert result["status"] == "complete"
    assert result["verified"] is True
    assert result["pc"] == 1
    assert result["actions"] == 1
    assert result["trace"][0]["guards"]["after"]["ok"] is True
    assert json.loads(out_path.read_text())["status"] == "complete"


def test_full_fallback_hands_off_at_after_miss_with_prefix_and_no_status_action():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[[{"index": 7, "text": "Retry", "clickable": True}]],
    )
    result = run_plan(_plan(), {}, adapter, None, "full_fallback")
    assert result["status"] == "handoff"
    assert result["pc"] == 0
    assert result["actions"] == 1
    assert result["trace"][0]["action"] == {"action_type": "click", "index": 2}
    assert result["handoff"]["action"] is None
    assert all(record["action"].get("action_type") != "status" for record in result["trace"])


def test_local_rejoin_uses_previous_action_and_never_replays_program_action():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[
            [{"index": 7, "text": "Retry", "clickable": True}],
            [{"index": 8, "text": "Done"}],
        ],
    )
    payloads = []

    def decision(payload):
        payloads.append(payload)
        return {
            "action": {"action_type": "click", "index": 7},
            "stop": False,
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }

    result = run_plan(_plan(), {}, adapter, decision, "local_rejoin")
    assert result["status"] == "complete"
    assert result["actions"] == 2
    assert [a["action_type"] for a in adapter.executed] == ["click", "click"]
    assert payloads[0]["previous_action"] == {"action_type": "click", "index": 2}
    assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 2}
    assert [r["kind"] for r in result["trace"]] == ["program", "local"]


def test_local_rejoin_rejects_exact_replay_of_program_action():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[[{"index": 2, "text": "Save", "clickable": True}]],
    )

    def decision(payload):
        assert payload["previous_action"] == {"action_type": "click", "index": 2}
        return {"action": {"action_type": "click", "index": 2}, "stop": False, "usage": {}}

    result = run_plan(_plan(), {}, adapter, decision, "local_rejoin")
    assert result["status"] == "handoff"
    assert result["actions"] == 1
    assert len(adapter.executed) == 1


def test_local_rejoin_cap_hands_off_without_claiming_completion():
    class Stuck(FakeAdapter):
        def execute(self, action):
            self.executed.append(copy.deepcopy(action))
            self.screen = [{"index": 7, "text": "Retry", "clickable": True}]

    adapter = Stuck([{"index": 2, "text": "Save", "clickable": True}])
    calls = []

    def decision(payload):
        calls.append(payload)
        candidates = [
            {"action_type": "click", "index": 7},
            {"action_type": "scroll", "direction": "down"},
            {"action_type": "wait"},
        ]
        return {
            "action": candidates[len(calls) - 1],
            "stop": False,
            "usage": {},
        }

    result = run_plan(_plan(), {}, adapter, decision, "local_rejoin", max_actions=4)
    assert result["status"] == "handoff"
    assert result["pc"] == 0
    assert len(calls) == 3
    assert result["actions"] == 4
    assert result["verified"] is False


def test_ui_exception_is_uncertain_and_never_replayed():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        error=True,
    )
    result = run_plan(_plan(), {}, adapter, None, "full_fallback")
    assert result["status"] == "uncertain"
    assert result["actions"] == 1
    assert len(adapter.executed) == 1
    assert result["pc"] == 0


def test_action_budget_is_shared_by_program_and_local_actions():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[[{"index": 6, "text": "Retry", "clickable": True}]],
    )
    result = run_plan(_plan(), {}, adapter, None, "full_fallback", max_actions=0)
    assert result["status"] == "budget_exhausted"
    assert result["actions"] == 0
    assert adapter.executed == []


def test_budget_stop_propagates_from_decision_and_stops_local_loop():
    adapter = FakeAdapter(
        [{"index": 2, "text": "Save", "clickable": True}],
        transitions=[[{"index": 7, "text": "Retry", "clickable": True}]],
    )
    calls = []

    def decision(payload):
        calls.append(payload)
        raise BudgetStop("frozen budget")

    with pytest.raises(BudgetStop, match="frozen budget"):
        run_plan(_plan(), {}, adapter, decision, "local_rejoin")
    assert len(calls) == 1
    assert len(adapter.executed) == 1
