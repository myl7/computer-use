"""Loss-aware parsing for AndroidWorld's numbered AX element text.

``android_env._ui_element_line`` writes a JSON-shaped object directly into a
text line.  A control character in an element's text can therefore be literal
JSON text, even though a normal strict JSON decoder rejects it.  This module
parses the complete observation with ``JSONDecoder(strict=False)`` so an
element is never truncated merely because its value contains a newline.

The parser deliberately accepts only the observed ``UI element N:`` records.
It returns the decoded node dictionaries without changing their fields or
their ``index`` values.  Any malformed or discarded record is reported in
``issues`` and makes the result partial.  Parsing stops at a malformed record
so a marker in the malformed payload cannot be mistaken for another node.
Because the native renderer does not escape backslashes, a backslash in an
otherwise decodable object is reported as ``ambiguous_escape`` and rejected.
"""

from __future__ import annotations

import json
import re
from typing import Any


_MARKER_RE = re.compile(r"UI element (?P<marker>[0-9]+):")
_DECODER = json.JSONDecoder(strict=False)
_JSON_WHITESPACE = " \t\r\n"


def _skip_whitespace(text: str, start: int) -> int:
    """Return the first non-JSON-whitespace offset at or after ``start``."""

    end = start
    while end < len(text) and text[end] in _JSON_WHITESPACE:
        end += 1
    return end


def _raw_object_backslash(text: str, start: int) -> int | None:
    """Find a backslash before the current faux object's closing brace."""

    depth = 0
    in_string = False
    for offset in range(start, len(text)):
        char = text[offset]
        if char == "\\":
            return offset
        if char == '"':
            in_string = not in_string
        elif not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return None
    return None


def _issue(kind: str, offset: int, **details: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "offset": offset}
    item.update(details)
    return item


def parse_ax_tree(ax_text: Any) -> dict[str, Any]:
    """Parse numbered AX records from one observation string.

    The returned mapping has this stable shape::

        {
            "nodes": [decoded node dictionaries],
            "issues": [{"kind": str, "offset": int, ...}],
            "complete": bool,
            "completeness": "complete" | "partial",
            "markers_seen": int,
            "nodes_parsed": int,
            "truncated": bool,
        }

    A blank observation is a complete empty element list.  For a malformed
    record, nodes decoded before that record are retained, the malformed
    record is described in ``issues``, and ``truncated`` is true.  No attempt
    is made to recover by searching inside the malformed payload.
    """

    result: dict[str, Any] = {
        "nodes": [],
        "issues": [],
        "complete": True,
        "completeness": "complete",
        "markers_seen": 0,
        "nodes_parsed": 0,
        "truncated": False,
    }

    if not isinstance(ax_text, str):
        result["complete"] = False
        result["completeness"] = "partial"
        result["issues"].append(
            _issue("invalid_input", 0, expected="str", received=type(ax_text).__name__)
        )
        return result

    if not ax_text.strip():
        return result

    cursor = 0
    seen_markers: set[int] = set()
    while True:
        marker_match = _MARKER_RE.search(ax_text, cursor)
        if marker_match is None:
            if ax_text[cursor:].strip():
                result["issues"].append(
                    _issue("unparsed_text", cursor, length=len(ax_text) - cursor)
                )
            break

        gap = ax_text[cursor : marker_match.start()]
        if gap.strip():
            result["issues"].append(
                _issue("unparsed_text", cursor, length=len(gap))
            )

        marker = int(marker_match.group("marker"))
        result["markers_seen"] += 1
        if marker in seen_markers:
            result["issues"].append(
                _issue("duplicate_marker", marker_match.start(), marker=marker)
            )
        seen_markers.add(marker)

        payload_start = _skip_whitespace(ax_text, marker_match.end())
        if payload_start >= len(ax_text) or ax_text[payload_start] != "{":
            result["issues"].append(
                _issue("missing_json_object", payload_start, marker=marker)
            )
            result["truncated"] = True
            break

        raw_backslash = _raw_object_backslash(ax_text, payload_start)
        if raw_backslash is not None:
            result["issues"].append(
                _issue(
                    "ambiguous_escape",
                    payload_start,
                    marker=marker,
                    escape_offset=raw_backslash,
                )
            )
            result["truncated"] = True
            break

        try:
            node, end = _DECODER.raw_decode(ax_text, payload_start)
        except json.JSONDecodeError as exc:
            result["issues"].append(
                _issue(
                    "malformed_json",
                    payload_start,
                    marker=marker,
                    error=exc.msg,
                    error_offset=exc.pos,
                )
            )
            result["truncated"] = True
            break

        # The Android renderer writes string values without JSON escaping.
        # A backslash that happens to form a valid JSON escape (for example
        # ``C:\\new``) would otherwise be silently rewritten by raw_decode.
        # There is no reliable way to recover the native value, so reject the
        # complete raw object instead of returning a false observation.
        raw_payload = ax_text[payload_start:end]
        if "\\" in raw_payload:
            result["issues"].append(
                _issue("ambiguous_escape", payload_start, marker=marker)
            )
            result["truncated"] = True
            break

        cursor = end
        if not isinstance(node, dict):
            result["issues"].append(
                _issue("node_not_object", payload_start, marker=marker)
            )
            continue

        node_index = node.get("index")
        if type(node_index) is not int:
            result["issues"].append(
                _issue(
                    "invalid_node_index",
                    payload_start,
                    marker=marker,
                    node_index=node_index,
                )
            )
            continue

        if node_index != marker:
            result["issues"].append(
                _issue(
                    "marker_index_mismatch",
                    payload_start,
                    marker=marker,
                    node_index=node_index,
                )
            )

        # Keep the decoded mapping intact.  In particular, do not replace its
        # index with the marker number or project it to a reduced field set.
        result["nodes"].append(node)
        result["nodes_parsed"] += 1

    if result["issues"]:
        result["complete"] = False
        result["completeness"] = "partial"
    return result


def parse_ax_tree_text(ax_text: Any) -> dict[str, Any]:
    """Compatibility spelling for callers that name the input field."""

    return parse_ax_tree(ax_text)


def parse_ui_elements(ax_text: Any) -> dict[str, Any]:
    """Compatibility spelling for callers that name the parsed records."""

    return parse_ax_tree(ax_text)


__all__ = ["parse_ax_tree", "parse_ax_tree_text", "parse_ui_elements"]
