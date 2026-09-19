from __future__ import annotations

import copy
import json

import pytest

from guiexp_android.selective_evidence_store import (
    EvidenceStoreError,
    build_store,
)


def _ax(nodes: list[dict]) -> str:
    return "\n".join(f"UI element {node['index']}: {json.dumps(node)}" for node in nodes)


def _step(
    number: int,
    pre: str,
    post: str,
    action: dict | None = None,
    *,
    pre_activity: str = "app.Before",
    post_activity: str = "app.After",
) -> dict:
    return {
        "step": number,
        "action": action or {"action_type": "click", "index": 0},
        "pre_obs": {"url": pre_activity, "ax_tree_text": pre},
        "post_obs": {"url": post_activity, "ax_tree_text": post},
    }


def _store(steps: list[dict] | None = None):
    steps = steps or [
        _step(
            1,
            _ax([
                {"index": 0, "text": "Start", "clickable": True},
                {"index": 1, "text": "Save", "clickable": True},
                {"index": 2, "text": "Save", "clickable": True},
            ]),
            _ax([
                {"index": 0, "text": "Saved", "clickable": True},
                {"index": 1, "text": "Save", "clickable": True},
                {"index": 2, "text": "Save", "clickable": True},
                {"index": 3, "text": "Done", "clickable": True},
                {"index": 4, "text": "Done", "clickable": True},
            ]),
            {"action_type": "click", "index": 1, "text": "literal"},
        ),
    ]
    return build_store("save the item", steps)


def test_skeleton_exposes_safe_action_metadata_but_not_target_index_or_intent():
    store = _store()
    skeleton = store.skeleton

    assert skeleton["goal"] == "save the item"
    assert skeleton["source_alignment"] == [{"position": 0, "source_step": 1}]
    step = skeleton["steps"][0]
    assert step["action_type"] == "click"
    assert step["action_fields"] == {"text": "literal"}
    assert step["activity_transition"] == {"pre": "app.Before", "post": "app.After"}
    assert "intent" not in step
    assert "index" not in json.dumps(skeleton)


def test_target_query_returns_action_target_and_preserves_descriptor_duplicates():
    packet = _store().query(1, "target")

    assert packet["query"] == "target"
    assert packet["status"] == "found"
    assert packet["target_index"] == 1
    assert packet["target_node"]["text"] == "Save"
    assert packet["descriptor_candidate_count"] == 2
    assert [candidate["index"] for candidate in packet["descriptor_candidates"]] == [1, 2]
    assert all(candidate["field"] == "text" for candidate in packet["descriptor_candidates"])
    assert isinstance(packet["source_hash"], str) and len(packet["source_hash"]) == 64
    assert packet["char_count"] == packet["charcount"]


def test_effect_query_keeps_post_only_duplicates_and_negative_effect_is_unknown():
    store = _store()
    packet = store.query(1, "effect")

    assert packet["status"] == "evidenced"
    assert packet["effect_witness"] == "evidenced"
    done = [entry for entry in packet["post_only_visible_values"] if entry["value"] == "Done"]
    assert len(done) == 2
    group = next(group for group in packet["post_only_groups"] if group["normalized_value"] == "done")
    assert group["occurrence_count"] == 2
    assert group["indices"] == [3, 4]

    same = _ax([{"index": 0, "text": "Same"}])
    negative = build_store("no visible change", [_step(1, same, same)]).query(1, "effect")
    assert negative["status"] == "unknown"
    assert negative["effect_witness"] == "unknown"
    assert negative["reason"] == "no_post_only_visible_value"
    assert negative["post_only_visible_values"] == []


def test_state_queries_return_full_raw_observation_and_parse_sidecars():
    raw_pre = _ax([{"index": 0, "text": "Before", "editable": True}])
    raw_post = _ax([{"index": 0, "text": "After", "editable": True}])
    store = build_store("edit", [_step(7, raw_pre, raw_post, {"action_type": "input_text", "index": 0, "text": "After"})])

    pre = store.query(7, "state_pre")
    post = store.query(7, "state_post")
    assert pre["status"] == "complete"
    assert pre["raw_evidence"]["ax_tree_text"] == raw_pre
    assert post["raw_evidence"]["ax_tree_text"] == raw_post
    assert pre["activity"] == "app.Before"
    assert post["activity"] == "app.After"
    assert pre["parse"]["complete"] is True


def test_multiline_ax_is_parsed_and_raw_backslash_is_explicitly_unknown():
    multiline_pre = 'UI element 0: {"index": 0, "text": "Before\\nline"}'
    multiline_post = 'UI element 0: {"index": 0, "text": "After\\nline"}'
    # The source format contains an actual newline inside the faux JSON value.
    multiline_pre = multiline_pre.replace("\\n", "\n")
    multiline_post = multiline_post.replace("\\n", "\n")
    multiline = build_store("edit", [_step(1, multiline_pre, multiline_post)]).query(1, "effect")
    assert multiline["status"] == "evidenced"
    assert any(entry["value"] == "After\nline" for entry in multiline["post_only_visible_values"])

    raw_backslash = 'UI element 0: {"index": 0, "text": "C:\\new"}'
    backslash_store = build_store("path", [_step(1, raw_backslash, raw_backslash)])
    effect = backslash_store.query(1, "effect")
    assert effect["status"] == "unknown"
    assert effect["reason"] == "pre_or_post_accessibility_tree_incomplete"
    assert any(issue["kind"] == "ambiguous_escape" for issue in effect["parse"]["pre"]["issues"])
    assert backslash_store.query(1, "state_pre")["raw_evidence"]["ax_tree_text"] == raw_backslash


def test_incomplete_and_ambiguous_target_queries_fail_closed_with_raw_fallback():
    malformed = (
        'UI element 0: {"index": 0, "text": "Save"}\n'
        'UI element 1: {"index": 1, "text": "bad"quote"}'
    )
    store = build_store("save", [_step(1, malformed, malformed, {"action_type": "click", "index": 0})])
    target = store.query(1, "target")
    assert target["status"] == "unknown"
    assert target["reason"] == "pre_accessibility_tree_incomplete"
    assert target["raw_available_via"] == "state_pre"
    assert store.query(1, "state_pre")["raw_evidence"]["ax_tree_text"] == malformed

    duplicate_index = _ax([
        {"index": 0, "text": "A"},
        {"index": 0, "text": "B"},
    ])
    ambiguous = build_store("pick", [_step(1, duplicate_index, duplicate_index, {"action_type": "click", "index": 0})]).query(1, "target")
    assert ambiguous["status"] == "unknown"
    assert ambiguous["reason"] == "pre_accessibility_tree_incomplete"


def test_queries_validate_names_and_source_steps():
    store = _store()
    with pytest.raises(EvidenceStoreError):
        store.query(1, "oracle")
    with pytest.raises(EvidenceStoreError):
        store.query(True, "target")
    with pytest.raises(IndexError):
        store.query(99, "target")
    with pytest.raises(EvidenceStoreError):
        build_store("bad", [])
    with pytest.raises(EvidenceStoreError):
        build_store("bad", [{"step": 1, "action": {"action_type": "click"}}])


def test_inputs_packets_and_repeated_queries_are_detached_and_deterministic():
    steps = [_step(2, _ax([{"index": 0, "text": "A"}]), _ax([{"index": 0, "text": "B"}]))]
    original = copy.deepcopy(steps)
    first = build_store("goal", steps)
    second = build_store("goal", steps)
    assert first.source_hash == second.source_hash
    packet1 = first.query(2, "effect")
    packet2 = second.query("effect", 2)
    assert packet1 == packet2
    assert first.cache_info() == {"entries": 1, "hits": 0, "misses": 1}
    packet1["post_only_visible_values"].clear()
    assert first.query(2, "effect")["post_only_visible_values"]
    assert first.cache_info() == {"entries": 1, "hits": 1, "misses": 1}
    skeleton = first.skeleton
    skeleton["steps"].clear()
    assert first.skeleton["steps"]
    steps[0]["pre_obs"]["ax_tree_text"] = "mutated"
    assert original[0]["pre_obs"]["ax_tree_text"] != "mutated"
    assert first.query(2, "state_pre")["raw_evidence"]["ax_tree_text"] == original[0]["pre_obs"]["ax_tree_text"]
