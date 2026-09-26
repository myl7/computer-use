"""False-negative and fail-closed tests for observed guard evidence."""

from __future__ import annotations

import json

from guiexp_android.selective_guard_evidence import validate_guard_evidence


def _ax(nodes):
    return "\n".join(f"UI element {node['index']}: {json.dumps(node)}" for node in nodes)


def _plan(after, *, action=None, before=None, target=None, slots=None):
    return {
        "schema": "selective-plan/1",
        "slots": (
            {"file_name": "public file name"}
            if slots is None and any(isinstance(v, dict) for v in after[0].values())
            else ({} if slots is None else slots)
        ),
        "steps": [{
            "id": "step",
            "intent": "perform the action",
            "action": action or {"action_type": "click"},
            "target": {"text": "Save"} if target is None else target,
            "before": [] if before is None else before,
            "after": after,
        }],
    }


def _demo(pre, post, action):
    return {"steps": [{
        "step": 1,
        "action": action,
        "pre_obs": {"ax_tree_text": _ax(pre)},
        "post_obs": {"ax_tree_text": _ax(post)},
    }]}


def test_dynamic_after_value_is_grounded_from_observed_action_text():
    plan = _plan(
        [{"text": {"slot": "file_name", "transform": "stem"}}],
        action={
            "action_type": "input_text",
            "text": {"slot": "file_name", "transform": "identity"},
        },
        target={"hint": "Name", "editable": True},
    )
    report = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "hint": "Name", "editable": True}],
            [{"index": 0, "text": "backup_cool_bear", "hint": "Name", "editable": True}],
            {"action_type": "input_text", "text": "backup_cool_bear.txt", "index": 0},
        )],
    )
    assert report["status"] == "valid"
    assert report["steps"][0]["source_indices"] == [1]
    assert report["steps"][0]["evidence"][0]["changed_after_predicate"] is True


def test_wrapped_identity_guard_uses_raw_action_binding_once():
    wrapped = {
        "slot": "file_name",
        "transform": "identity",
        "prefix": "Delete ",
        "suffix": "",
    }
    plan = _plan(
        [{"text": wrapped}],
        action={
            "action_type": "input_text",
            "text": {"slot": "file_name", "transform": "identity"},
        },
        target={"hint": "Name", "editable": True},
    )
    report = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "hint": "Name", "editable": True}],
            [{"index": 0, "text": "Delete trainingname", "hint": "Name", "editable": True}],
            {"action_type": "input_text", "text": "trainingname", "index": 0},
        )],
    )
    assert report["status"] == "valid"
    evidence = report["steps"][0]["evidence"][0]
    assert evidence["action"]["text"]["expected"] == "trainingname"
    assert evidence["selectors"][0]["after_matches"] == [0]


def test_wrapped_identity_target_is_inverted_from_the_observed_prestate():
    wrapped = {
        "slot": "file_name",
        "transform": "identity",
        "prefix": "Delete ",
        "suffix": ".md",
    }
    plan = _plan(
        [{"text": "Saved"}],
        action={"action_type": "click"},
        target={"text": wrapped},
        slots={"file_name": "public file name"},
    )
    report = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "text": "Delete trainingname.md"}],
            [{"index": 1, "text": "Saved"}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert report["status"] == "valid"
    assert report["steps"][0]["evidence"][0]["action"]["target"]["pre_matches"] == [0]


def test_wrong_wrapper_is_invalid_when_raw_binding_establishes_the_slot():
    wrapped = {
        "slot": "file_name",
        "transform": "identity",
        "prefix": "Delete ",
        "suffix": "",
    }
    plan = _plan(
        [{"text": "Saved"}],
        action={
            "action_type": "input_text",
            "text": {"slot": "file_name", "transform": "identity"},
        },
        target={"text": wrapped, "editable": True},
        slots={"file_name": "public file name"},
    )
    report = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "text": "Remove trainingname", "editable": True}],
            [{"index": 1, "text": "Saved"}],
            {"action_type": "input_text", "text": "trainingname", "index": 0},
        )],
    )
    assert report["status"] == "invalid"
    assert "recorded pre index" in " ".join(report["errors"])


def test_ambiguous_wrapped_grounding_stays_unknown():
    wrapped = {
        "slot": "file_name",
        "transform": "identity",
        "prefix": "Delete ",
        "suffix": "",
    }
    plan = _plan(
        [{"text": "Saved"}],
        action={
            "action_type": "input_text",
            "text": {"slot": "file_name", "transform": "identity"},
        },
        target={"text": wrapped, "editable": True},
        slots={"file_name": "public file name"},
    )
    report = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "text": "Delete othername", "editable": True}],
            [{"index": 1, "text": "Saved"}],
            {"action_type": "input_text", "text": "trainingname", "index": 0},
        )],
    )
    assert report["status"] == "unknown"
    assert "ambiguous observed grounding" in " ".join(report["errors"])


def test_unique_predicate_accepts_multiplicity_and_flag_transitions():
    multiplicity = validate_guard_evidence(
        _plan([{"text": "Downloads"}], target={"text": "Downloads", "clickable": True}),
        [_demo(
            [{"index": 0, "text": "Downloads", "clickable": True}, {"index": 1, "text": "Downloads", "clickable": False}],
            [{"index": 0, "text": "Downloads", "clickable": True}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert multiplicity["status"] == "valid"
    assert multiplicity["steps"][0]["evidence"][0]["changed_after_predicate"] is True

    flags = validate_guard_evidence(
        _plan([{"text": "Toggle", "clickable": True}], target={"text": "Toggle"}),
        [_demo(
            [{"index": 0, "text": "Toggle", "clickable": False}],
            [{"index": 0, "text": "Toggle", "clickable": True}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert flags["status"] == "valid"


def test_repeated_context_after_guard_is_allowed_with_distinct_changed_predicate():
    report = validate_guard_evidence(
        _plan([{"text": "More options"}, {"text": "Movies"}], target={"text": "More options"}),
        [_demo(
            [{"index": 0, "text": "More options"}],
            [{"index": 0, "text": "More options"}, {"index": 1, "text": "Movies"}],
            {"action_type": "click", "index": 0},
        )],
    )
    evidence = report["steps"][0]["evidence"][0]
    assert report["status"] == "valid"
    assert len(evidence["unchanged_context"]) == 1
    assert evidence["changed_after_predicate"] is True


def test_dynamic_after_requires_matching_action_type_and_linked_slot():
    plan = _plan(
        [{"text": {"slot": "file_name", "transform": "identity"}}],
        action={
            "action_type": "input_text",
            "text": {"slot": "file_name", "transform": "identity"},
        },
    )
    wrong_action = validate_guard_evidence(
        plan,
        [_demo(
            [{"index": 0, "text": "Save"}],
            [{"index": 0, "text": "done.txt"}],
            {"action_type": "click", "index": 0, "text": "done.txt"},
        )],
    )
    assert wrong_action["status"] == "unknown"
    assert "action_type" in " ".join(wrong_action["errors"])

    unrelated = validate_guard_evidence(
        {
            **plan,
            "slots": {"file_name": "name", "other": "unrelated"},
            "steps": [{
                **plan["steps"][0],
                "action": {"action_type": "input_text", "text": {"slot": "other", "transform": "identity"}},
                "after": [{"text": {"slot": "file_name", "transform": "identity"}}],
            }],
        },
        [_demo(
            [{"index": 0, "text": "Save"}],
            [{"index": 0, "text": "done.txt"}],
            {"action_type": "input_text", "text": "done.txt", "index": 0},
        )],
    )
    assert unrelated["status"] == "unknown"
    assert "identity-linked" in " ".join(unrelated["errors"])


def test_literal_target_and_app_name_must_match_the_aligned_action():
    wrong_target = validate_guard_evidence(
        _plan([{"text": "Done"}], target={"text": "Save"}),
        [_demo(
            [{"index": 0, "text": "Save"}, {"index": 1, "text": "Delete"}],
            [{"index": 2, "text": "Done"}],
            {"action_type": "click", "index": 1},
        )],
    )
    assert wrong_target["status"] == "invalid"
    assert "recorded pre index" in " ".join(wrong_target["errors"])

    open_app = {
        "schema": "selective-plan/1",
        "slots": {},
        "steps": [{
            "id": "launch",
            "intent": "launch the notes app",
            "action": {"action_type": "open_app", "app_name": "Notes"},
            "target": None,
            "before": [],
            "after": [{"text": "Notes"}],
        }],
    }
    wrong_app = validate_guard_evidence(
        open_app,
        [_demo(
            [{"index": 0, "text": "Home"}],
            [{"index": 0, "text": "Notes"}],
            {"action_type": "open_app", "app_name": "Calendar"},
        )],
    )
    assert wrong_app["status"] == "invalid"
    assert "app_name" in " ".join(wrong_app["errors"])


def test_literal_text_direction_and_before_guard_are_checked_against_observation():
    text_plan = _plan(
        [{"text": "Done"}],
        action={"action_type": "input_text", "text": "expected"},
        target={"hint": "Title"},
    )
    text_result = validate_guard_evidence(
        text_plan,
        [_demo(
            [{"index": 0, "hint": "Title"}],
            [{"index": 0, "text": "Done"}],
            {"action_type": "input_text", "text": "different", "index": 0},
        )],
    )
    assert text_result["status"] == "invalid"
    assert "text" in " ".join(text_result["errors"])

    scroll_plan = {
        "schema": "selective-plan/1",
        "slots": {},
        "steps": [{
            "id": "scroll",
            "intent": "scroll down",
            "action": {"action_type": "scroll", "direction": "down"},
            "target": None,
            "before": [{"text": "List"}],
            "after": [{"text": "Bottom"}],
        }],
    }
    scroll_result = validate_guard_evidence(
        scroll_plan,
        [_demo(
            [{"index": 0, "text": "Other"}],
            [{"index": 0, "text": "Bottom"}],
            {"action_type": "scroll", "direction": "up"},
        )],
    )
    assert scroll_result["status"] == "invalid"
    assert "direction" in " ".join(scroll_result["errors"]) or "before guard" in " ".join(scroll_result["errors"])


def test_nonidentity_target_grounding_uses_prestate_only_and_needs_identity_base():
    no_identity = validate_guard_evidence(
        {
            **_plan([{"text": {"slot": "file_name", "transform": "stem"}}]),
            "slots": {"file_name": "name"},
            "steps": [{
                **_plan([{"text": {"slot": "file_name", "transform": "stem"}}])["steps"][0],
                "target": {"text": {"slot": "file_name", "transform": "stem"}},
            }],
        },
        [_demo(
            [{"index": 0, "text": "old.txt"}],
            [{"index": 0, "text": "new.txt"}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert no_identity["status"] == "unknown"

    prestate_only = validate_guard_evidence(
        {
            **_plan([{"text": {"slot": "file_name", "transform": "suffix"}}]),
            "slots": {"file_name": "name"},
            "steps": [{
                **_plan([{"text": {"slot": "file_name", "transform": "suffix"}}])["steps"][0],
                "target": {"text": {"slot": "file_name", "transform": "identity"}},
            }],
        },
        [_demo(
            [{"index": 0, "text": "old.txt"}],
            [{"index": 0, "text": "new.txt"}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert prestate_only["status"] == "invalid"


def test_negative_postcondition_and_unresolved_slot_fail_closed():
    negative = validate_guard_evidence(
        _plan([{"text": "Done"}]),
        [_demo(
            [{"index": 0, "text": "Save"}],
            [{"index": 0, "text": "Retry"}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert negative["status"] == "invalid"

    unresolved = validate_guard_evidence(
        {
            **_plan([{"text": {"slot": "missing", "transform": "identity"}}]),
            "slots": {"missing": "public value"},
            "steps": [{
                **_plan([{"text": {"slot": "missing", "transform": "identity"}}])["steps"][0],
                "target": {"clickable": True},
            }],
        },
        [_demo(
            [{"index": 0, "clickable": True}],
            [{"index": 0, "clickable": True}],
            {"action_type": "click", "index": 0},
        )],
    )
    assert unresolved["status"] == "unknown"
    assert "identity-linked" in " ".join(unresolved["errors"])


def test_missing_alignment_is_unknown_and_explicit_sidecar_can_align_compressed_steps():
    plan = {
        "schema": "selective-plan/1", "slots": {}, "steps": [
            {"id": "first", "intent": "first", "action": {"action_type": "click"}, "target": {"text": "A"}, "before": [], "after": [{"text": "B"}]},
            {"id": "second", "intent": "second", "action": {"action_type": "click"}, "target": {"text": "B"}, "before": [], "after": [{"text": "C"}]},
        ],
    }
    demo = {"steps": [
        {"step": 1, "action": {"action_type": "click", "index": 0}, "pre_obs": {"ax_tree_text": _ax([{"index": 0, "text": "A"}])}, "post_obs": {"ax_tree_text": _ax([{"index": 0, "text": "B"}])}},
        {"step": 3, "action": {"action_type": "click", "index": 0}, "pre_obs": {"ax_tree_text": _ax([{"index": 0, "text": "B"}])}, "post_obs": {"ax_tree_text": _ax([{"index": 0, "text": "C"}])}},
    ]}
    assert validate_guard_evidence(plan, [demo])["status"] == "unknown"
    aligned = validate_guard_evidence(plan, [demo], {"first": [1], "second": [3]})
    assert aligned["status"] == "valid"
    assert aligned["steps"][1]["source_indices"] == [3]
    listed = validate_guard_evidence(
        plan,
        [demo],
        [{"plan_step": "first", "source_step": 1}, {"plan_step": "second", "source_step": 3}],
    )
    assert listed["status"] == "valid"
    aliases = validate_guard_evidence(plan, [demo], {0: [1], 1: [3]})
    assert aliases["status"] == "invalid"
    assert "numeric step alias" in " ".join(aliases["errors"])


def test_multiline_ax_records_are_valid_guard_evidence():
    plan = _plan([{"text": "Saved\ncopy"}], target={"text": "Save"})
    report = validate_guard_evidence(
        plan,
        [{"steps": [{
            "step": 1,
            "action": {"action_type": "click", "index": 0},
            "pre_obs": {"ax_tree_text": 'UI element 0: {"index": 0, "text": "Save"}'},
            "post_obs": {"ax_tree_text": 'UI element 0: {"index": 0, "text": "Saved\ncopy"}'},
        }]}],
    )

    assert report["status"] == "valid"
    evidence = report["steps"][0]["evidence"][0]
    assert evidence["parse"]["post"]["complete"] is True
    assert evidence["changed_after_predicate"] is True


def test_incomplete_pre_or_post_ax_parse_is_unknown_without_guard_invalidation():
    plan = _plan([{"text": "Saved"}], target={"text": "Save"})
    malformed = 'UI element 1: {"index": 1, "text": "bad"quote"}'
    cases = [
        (malformed, 'UI element 0: {"index": 0, "text": "Saved"}'),
        ('UI element 0: {"index": 0, "text": "Save"}', malformed),
    ]

    for pre, post in cases:
        report = validate_guard_evidence(
            plan,
            [{"steps": [{
                "step": 1,
                "action": {"action_type": "click", "index": 0},
                "pre_obs": {"ax_tree_text": pre},
                "post_obs": {"ax_tree_text": post},
            }]}],
        )

        assert report["status"] == "unknown"
        assert report["steps"][0]["status"] == "unknown"
        evidence = report["steps"][0]["evidence"][0]
        assert evidence["action"] == {}
        assert any(not side["complete"] for side in evidence["parse"].values())
        assert "parse is incomplete" in " ".join(report["errors"])
