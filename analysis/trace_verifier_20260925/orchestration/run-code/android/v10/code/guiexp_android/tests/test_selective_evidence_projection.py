"""Tests for the read-only demonstration evidence projection."""

from __future__ import annotations

import copy
import json

from guiexp_android.selective_evidence_projection import project_demonstrations


def _ax(nodes):
    return "\n".join(f"UI element {node['index']}: {json.dumps(node)}" for node in nodes)


def _step(pre, post, action=None):
    return {
        "step": 1,
        "action": action or {"action_type": "click", "index": 0},
        "pre_obs": {"url": "app/.Before", "ax_tree_text": _ax(pre), "screenshot_files": {"raw": "pre.png"}},
        "post_obs": {"url": "app/.After", "ax_tree_text": _ax(post), "screenshot_files": {"raw": "post.png"}},
    }


def _parse(ax):
    return [json.loads(line.split(": ", 1)[1]) for line in ax.splitlines()]


def test_projection_is_detached_and_preserves_builder_fields():
    demos = [{"goal_text": "save the item", "steps": [_step(
        [{"index": 0, "text": "Save", "is_clickable": True, "is_editable": False}],
        [{"index": 0, "text": "Saved", "is_clickable": False, "is_editable": False}],
    )]}]
    before = copy.deepcopy(demos)
    projected = project_demonstrations(demos)

    assert demos == before
    assert projected is not demos and projected[0] is not demos[0]
    assert projected[0]["goal_text"] == "save the item"
    assert projected[0]["steps"][0]["action"] == demos[0]["steps"][0]["action"]
    assert projected[0]["steps"][0]["pre_obs"]["url"] == "app/.Before"
    assert projected[0]["steps"][0]["post_obs"]["screenshot_files"] == {"raw": "post.png"}


def test_projection_keeps_target_duplicates_post_only_values_and_changed_flags():
    pre = [
        {"index": 0, "text": "Save", "is_clickable": True, "is_long_clickable": False, "is_editable": False},
        {"index": 1, "text": "Save", "is_clickable": True, "is_long_clickable": False, "is_editable": False},
        {"index": 2, "text": "Static", "is_clickable": False, "is_editable": False},
        {"index": 3, "content_description": "Toggle", "is_clickable": False, "is_editable": False},
    ]
    post = [
        {"index": 0, "text": "Save", "is_clickable": False, "is_long_clickable": False, "is_editable": False},
        {"index": 1, "text": "Save", "is_clickable": True, "is_long_clickable": False, "is_editable": False},
        {"index": 2, "text": "New", "is_clickable": True, "is_editable": False},
        {"index": 3, "content_description": "Toggle", "is_clickable": True, "is_editable": False},
    ]
    projected = project_demonstrations([{"steps": [_step(pre, post)]}])[0]["steps"][0]
    pre_nodes, post_nodes = _parse(projected["pre_obs"]["ax_tree_text"]), _parse(projected["post_obs"]["ax_tree_text"])
    assert {node["index"] for node in pre_nodes} == {0, 1, 3}
    assert {node["index"] for node in post_nodes} == {0, 1, 2, 3}
    assert sum(node.get("text") == "Save" for node in pre_nodes) == 2
    assert any(node.get("text") == "New" for node in post_nodes)
    assert all("is_clickable" in node and "is_editable" in node for node in pre_nodes + post_nodes)
    assert projected["projection"]["effect_witness"] == "evidenced"
    assert set(projected["projection"]["witness_kinds"]) == {"post_only_visible_value", "selector_flag_change"}


def test_no_effect_is_explicitly_unknown_without_fabricated_postcondition():
    same = [{"index": 0, "text": "Save", "is_clickable": True, "is_editable": False}]
    projected = project_demonstrations([{"steps": [_step(same, same)]}])[0]["steps"][0]
    assert projected["projection"]["effect_witness"] == "unknown"
    assert projected["projection"]["witness_kinds"] == []
    assert "after" not in projected["projection"]
    assert _parse(projected["post_obs"]["ax_tree_text"]) == same


def test_missing_action_target_is_not_invented():
    projected = project_demonstrations([{"steps": [_step(
        [{"index": 0, "text": "Other", "is_clickable": True}],
        [{"index": 0, "text": "Other", "is_clickable": True}],
        {"action_type": "click", "index": 99},
    )]}])[0]["steps"][0]
    assert projected["projection"]["target_evidence"] == "unknown"
    assert all(node["index"] != 99 for node in _parse(projected["pre_obs"]["ax_tree_text"]))


def test_projection_keeps_a_real_multiline_ax_record_and_projects_its_node():
    # Match android_env._ui_element_line: the newline is literal in the faux
    # JSON payload, rather than an escaped JSON sequence.
    pre = 'UI element 8: {"index": 8, "text": "Create\nNote", "is_clickable": true}'
    post = 'UI element 8: {"index": 8, "text": "Created\nNote", "is_clickable": true}'
    demo = {
        "steps": [{
            "step": 14,
            "action": {"action_type": "click", "index": 8},
            "pre_obs": {"ax_tree_text": pre},
            "post_obs": {"ax_tree_text": post},
        }],
    }

    projected = project_demonstrations([demo])[0]["steps"][0]

    assert projected["projection"]["effect_witness"] == "evidenced"
    assert projected["projection"]["parse"]["pre"]["complete"] is True
    assert projected["projection"]["parse"]["post"]["complete"] is True
    assert _parse(projected["pre_obs"]["ax_tree_text"])[0]["text"] == "Create\nNote"
    assert _parse(projected["post_obs"]["ax_tree_text"])[0]["text"] == "Created\nNote"


def test_projection_returns_both_raw_views_when_either_ax_parse_is_incomplete():
    pre = (
        'UI element 0: {"index": 0, "text": "Save", "is_clickable": true}\n'
        'UI element 1: {"index": 1, "text": "bad"quote"}'
    )
    post = 'UI element 0: {"index": 0, "text": "Saved", "is_clickable": true}'

    projected = project_demonstrations([{
        "steps": [{
            "step": 1,
            "action": {"action_type": "click", "index": 0},
            "pre_obs": {"ax_tree_text": pre},
            "post_obs": {"ax_tree_text": post},
        }],
    }])[0]["steps"][0]

    assert projected["pre_obs"]["ax_tree_text"] == pre
    assert projected["post_obs"]["ax_tree_text"] == post
    assert projected["projection"]["effect_witness"] == "unknown"
    assert projected["projection"]["projection_fallback"] == "raw_unparsed"
    parse_report = projected["projection"]["parse"]
    assert parse_report["pre"]["complete"] is False
    assert any(issue["kind"] == "malformed_json" for issue in parse_report["pre"]["issues"])
    assert parse_report["post"]["complete"] is True
