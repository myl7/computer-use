"""Pure projection of recorded GUI demonstrations for bounded plan building."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

from .selective_ax_parser import parse_ax_tree

_VISIBLE_FIELDS = ("text", "hint", "hint_text", "content_description", "description")
_FLAG_FIELDS = ("is_clickable", "is_editable", "is_long_clickable", "clickable", "editable")
_NODE_FIELDS = (
    "index",
    "text",
    "hint",
    "content_description",
    "hint_text",
    "description",
    "tooltip",
    "is_clickable",
    "is_long_clickable",
    "is_editable",
    "clickable",
    "editable",
)
def _nodes(ax_text: Any) -> list[dict[str, Any]]:
    """Return parsed nodes for legacy callers.

    Projection itself uses :func:`parse_ax_tree` so it can preserve the raw
    observation whenever parsing is incomplete.  This adapter intentionally
    keeps the old list-only API for callers that already use ``_nodes``.
    """

    return parse_ax_tree(ax_text)["nodes"]


def _parse_sidecar(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Keep parse completeness and issues alongside projected evidence."""

    return {
        "complete": bool(parsed.get("complete")),
        "completeness": parsed.get("completeness"),
        "markers_seen": parsed.get("markers_seen", 0),
        "nodes_parsed": parsed.get("nodes_parsed", 0),
        "truncated": bool(parsed.get("truncated")),
        "issues": copy.deepcopy(parsed.get("issues") or []),
    }


def _visible_values(node: Mapping[str, Any]) -> set[tuple[str, str]]:
    return {
        (field, value.strip())
        for field in _VISIBLE_FIELDS
        for value in (node.get(field),)
        if isinstance(value, str) and value.strip()
    }


def _project_nodes(
    pre_text: Any,
    post_text: Any,
    action: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    pre_parse = parse_ax_tree(pre_text)
    post_parse = parse_ax_tree(post_text)
    pre = pre_parse["nodes"]
    post = post_parse["nodes"]
    parse_sidecar = {
        "pre": _parse_sidecar(pre_parse),
        "post": _parse_sidecar(post_parse),
    }
    if not pre_parse["complete"] or not post_parse["complete"]:
        return (
            pre_text if isinstance(pre_text, str) else "",
            post_text if isinstance(post_text, str) else "",
            {
                "effect_witness": "unknown",
                "witness_kinds": [],
                "target_evidence": "unknown",
                "projection_fallback": "raw_unparsed",
                "parse": parse_sidecar,
            },
        )
    pre_values = set().union(*(_visible_values(node) for node in pre)) if pre else set()
    post_values = set().union(*(_visible_values(node) for node in post)) if post else set()
    post_only = post_values - pre_values

    target_index = action.get("index") if isinstance(action, Mapping) else None
    wanted_values: set[tuple[str, str]] = set(post_only)
    if type(target_index) is int:
        for node in pre + post:
            if node.get("index") == target_index:
                wanted_values.update(_visible_values(node))

    changed_indices: set[int] = set()
    changed_flags: list[dict[str, Any]] = []
    pre_by_index = {node["index"]: node for node in pre}
    post_by_index = {node["index"]: node for node in post}
    for index in pre_by_index.keys() & post_by_index.keys():
        for field in _FLAG_FIELDS:
            before = pre_by_index[index].get(field)
            after = post_by_index[index].get(field)
            if before != after:
                changed_flags.append({"index": index, "field": field, "pre": before, "post": after})
        if any(item["index"] == index for item in changed_flags):
            changed_indices.add(index)
    # A visible post-only value already witnesses the effect. Keep flag-only
    # nodes when that is the only available observed change; retain the exact
    # flag diff in metadata in either case.
    if post_only:
        changed_indices = {
            index
            for index in changed_indices
            if not ((_visible_values(pre_by_index[index]) | _visible_values(post_by_index[index])) & wanted_values)
        }

    def choose(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected = []
        for node in nodes:
            index = node.get("index")
            if index == target_index or index in changed_indices or _visible_values(node) & wanted_values:
                selected.append({key: node[key] for key in _NODE_FIELDS if key in node})
        return selected

    pre_selected = choose(pre)
    post_selected = choose(post)
    witness_kinds = []
    if post_only:
        witness_kinds.append("post_only_visible_value")
    if changed_indices:
        witness_kinds.append("selector_flag_change")
    evidence = {
        "effect_witness": "evidenced" if witness_kinds else "unknown",
        "witness_kinds": witness_kinds,
        "post_only_value_count": len(post_only),
        "changed_selector_flags": changed_flags,
        "target_evidence": "retained" if any(node.get("index") == target_index for node in pre_selected + post_selected) else "unknown",
        "retained_pre_nodes": len(pre_selected),
        "retained_post_nodes": len(post_selected),
        "parse": parse_sidecar,
    }

    def render(nodes: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"UI element {node['index']}: "
            + json.dumps(node, ensure_ascii=True, separators=(",", ":"))
            for node in nodes
        )

    return render(pre_selected), render(post_selected), evidence


def project_demonstrations(demos: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return a detached builder view while leaving full demonstrations untouched."""
    projected = copy.deepcopy(list(demos))
    for demo in projected:
        if not isinstance(demo, dict):
            continue
        steps = demo.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            pre = step.get("pre_obs")
            post = step.get("post_obs")
            if not isinstance(pre, dict) or not isinstance(post, dict):
                step["projection"] = {
                    "effect_witness": "unknown",
                    "witness_kinds": [],
                    "target_evidence": "unknown",
                }
                continue
            pre_ax = pre.get("ax_tree_text", "")
            post_ax = post.get("ax_tree_text", "")
            pre_projected, post_projected, evidence = _project_nodes(
                pre_ax,
                post_ax,
                step.get("action") if isinstance(step.get("action"), Mapping) else {},
            )
            pre["ax_tree_text"] = pre_projected
            post["ax_tree_text"] = post_projected
            step["projection"] = evidence
    return projected


__all__ = ["project_demonstrations"]
