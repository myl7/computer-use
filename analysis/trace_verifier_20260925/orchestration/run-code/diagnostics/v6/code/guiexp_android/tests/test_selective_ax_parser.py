"""Focused tests for the loss-aware AX observation parser."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from guiexp_android.android_env import _ui_element_line
from guiexp_android.selective_ax_parser import parse_ax_tree


def _android_node(*, text: str, index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        content_description="",
        hint_text="",
        tooltip="",
        is_clickable=True,
        is_long_clickable=False,
        is_editable=False,
        is_scrollable=False,
        is_focusable=False,
        is_selected=False,
        is_checked=False,
        index=index,
    )


def _record(node: dict) -> str:
    return f"UI element {node['index']}: {json.dumps(node)}"


def test_android_renderer_fixture_with_literal_newline_stays_one_node():
    # This is the exact faux-JSON shape emitted by android_env._ui_element_line.
    source = _ui_element_line(_android_node(text="Create\nNote", index=8), 8)

    parsed = parse_ax_tree(source)

    assert parsed["complete"] is True
    assert parsed["completeness"] == "complete"
    assert parsed["markers_seen"] == parsed["nodes_parsed"] == 1
    assert parsed["nodes"][0]["index"] == 8
    assert parsed["nodes"][0]["text"] == "Create\nNote"
    assert parsed["issues"] == []


def test_multiple_multiline_records_preserve_current_node_shape_and_indexes():
    first = {"index": 8, "text": "Create\nNote", "is_clickable": True}
    second = {"index": 9, "text": "Save", "is_clickable": False}
    source = (
        'UI element 8: {"index": 8, "text": "Create\nNote", "is_clickable": true}\n'
        'UI element 9: {"index": 9, "text": "Save", "is_clickable": false}'
    )

    parsed = parse_ax_tree(source)

    assert parsed["nodes"] == [first, second]
    assert parsed["markers_seen"] == parsed["nodes_parsed"] == 2
    assert parsed["truncated"] is False


def test_marker_inside_valid_quoted_text_is_not_a_new_record():
    node = {
        "index": 0,
        "text": "literal UI element 99: {noise}",
        "is_clickable": False,
    }

    # Keep this in the native renderer's syntax.  The marker is inside the
    # quoted value and must not be mistaken for a second element prefix.
    source = 'UI element 0: {"index": 0, "text": "literal UI element 99: {noise}", "is_clickable": false}'
    parsed = parse_ax_tree(source)

    assert parsed["nodes"] == [node]
    assert parsed["markers_seen"] == 1
    assert parsed["issues"] == []


def test_json_escaped_quotes_and_backslashes_are_rejected_as_ambiguous():
    node = {
        "index": 3,
        "text": 'quote " and slash \\ and tab\t',
        "is_editable": True,
    }

    parsed = parse_ax_tree(_record(node))

    assert parsed["nodes"] == []
    assert parsed["complete"] is False
    assert parsed["truncated"] is True
    assert parsed["issues"][0]["kind"] == "ambiguous_escape"


def test_native_renderer_backslash_is_not_falsely_decoded_as_newline():
    source = _ui_element_line(_android_node(text=r"C:\new", index=4), 4)

    parsed = parse_ax_tree(source)

    assert parsed["nodes"] == []
    assert parsed["complete"] is False
    assert parsed["completeness"] == "partial"
    assert parsed["truncated"] is True
    assert parsed["issues"][0]["kind"] == "ambiguous_escape"
    assert all("\n" not in str(node.get("text")) for node in parsed["nodes"])


@pytest.mark.parametrize(
    "malformed",
    [
        '{"index": 1, "text": "bad"quote"}',
        '{"index": 1, "text": "bad\\q"}',
    ],
)
def test_malformed_quote_or_backslash_fails_closed_with_partial_evidence(malformed: str):
    source = (
        _record({"index": 0, "text": "before"})
        + "\nUI element 1: "
        + malformed
        + "\n"
        + _record({"index": 2, "text": "after"})
    )

    parsed = parse_ax_tree(source)

    assert parsed["nodes"] == [{"index": 0, "text": "before"}]
    assert parsed["complete"] is False
    assert parsed["completeness"] == "partial"
    assert parsed["truncated"] is True
    assert parsed["issues"][0]["kind"] in {"malformed_json", "ambiguous_escape"}
    # The later marker is in an unparsed tail and cannot be fabricated as a node.
    assert all(node["index"] != 2 for node in parsed["nodes"])


def test_invalid_node_shape_is_reported_instead_of_silently_dropped():
    parsed = parse_ax_tree(
        "UI element 0: {\"text\": \"missing index\"}\n"
        "UI element 1: [1, 2]"
    )

    assert parsed["nodes"] == []
    assert parsed["complete"] is False
    assert [issue["kind"] for issue in parsed["issues"]] == [
        "invalid_node_index",
        "missing_json_object",
    ]
