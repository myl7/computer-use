"""Bounded, read-only access to recorded selective-training evidence.

``build_store`` accepts native training step records with an actual action and
raw ``pre_obs``/``post_obs`` mappings.  The returned store keeps a detached
copy of those records and exposes four explicit queries.  Target indices are
withheld from the initial skeleton and appear only in a ``target`` packet.
This module never calls a model, reads an oracle, or builds a serving prompt.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .selective_ax_parser import parse_ax_tree

SCHEMA = "selective-evidence-store/1"
PACKET_SCHEMA = "selective-evidence-packet/1"
QUERIES = frozenset({"target", "effect", "state_pre", "state_post"})
_VISIBLE_FIELDS = ("text", "hint", "hint_text", "content_description", "description")
_LITERAL_ACTION_FIELDS = ("text", "app_name", "direction")


class EvidenceStoreError(ValueError):
    """Invalid in-memory store input or query."""


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise EvidenceStoreError("evidence must be JSON-serializable") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _normalize(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def _scalar(value: Any) -> bool:
    return value is None or type(value) in (str, int, float, bool)


def _activity(observation: Mapping[str, Any]) -> Any:
    for key in ("activity", "url"):
        if key in observation and _scalar(observation[key]):
            return copy.deepcopy(observation[key])
    return None


def _parse(observation: Mapping[str, Any]) -> dict[str, Any]:
    raw = observation.get("ax_tree_text", "")
    return parse_ax_tree(raw)


def _parse_sidecar(parsed: Mapping[str, Any], *, include_issue_details: bool = True) -> dict[str, Any]:
    issues = copy.deepcopy(parsed.get("issues") or [])
    if not include_issue_details:
        issues = [
            {"kind": issue.get("kind", "unknown")}
            for issue in issues
            if isinstance(issue, Mapping)
        ]
    return {
        "complete": bool(parsed.get("complete")),
        "completeness": parsed.get("completeness"),
        "markers_seen": parsed.get("markers_seen", 0),
        "nodes_parsed": parsed.get("nodes_parsed", 0),
        "truncated": bool(parsed.get("truncated")),
        "issues": issues,
    }


def _visible_entries(node: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for field in _VISIBLE_FIELDS:
        value = node.get(field)
        if isinstance(value, str) and value.strip():
            entries.append(
                {
                    "field": field,
                    "value": value,
                    "normalized_value": _normalize(value),
                    "index": node.get("index"),
                }
            )
    return entries


def _step_value(step: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in step:
            return step[key]
    return None


def _literal_action(action: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _LITERAL_ACTION_FIELDS:
        value = action.get(key)
        if _scalar(value) and value is not None:
            fields[key] = copy.deepcopy(value)
    return fields


def _record_from_step(step: Mapping[str, Any], position: int) -> dict[str, Any]:
    source_step = _step_value(step, "source_step", "step")
    if type(source_step) is not int or source_step <= 0:
        raise EvidenceStoreError(f"steps[{position}].step must be a positive integer")
    action = _step_value(step, "action", "actual_training_action")
    if not isinstance(action, Mapping):
        raise EvidenceStoreError(f"steps[{position}] needs an actual training action")
    pre = _step_value(step, "pre_obs", "raw_pre", "pre")
    post = _step_value(step, "post_obs", "raw_post", "post")
    if not isinstance(pre, Mapping) or not isinstance(post, Mapping):
        raise EvidenceStoreError(f"steps[{position}] needs raw pre_obs and post_obs mappings")
    return {
        "source_step": source_step,
        "position": position,
        "action": copy.deepcopy(dict(action)),
        "pre_obs": copy.deepcopy(dict(pre)),
        "post_obs": copy.deepcopy(dict(post)),
    }


def _packet_base(
    store_hash: str,
    record: Mapping[str, Any],
    query: str,
) -> dict[str, Any]:
    step_source_hash = _sha256(
        {
            "source_step": record["source_step"],
            "action": record["action"],
            "pre_obs": record["pre_obs"],
            "post_obs": record["post_obs"],
        }
    )
    return {
        "schema": PACKET_SCHEMA,
        "source_step": record["source_step"],
        "source_hash": store_hash,
        "source_sha256": store_hash,
        "step_source_hash": step_source_hash,
        "query": query,
    }


def _finish_packet(packet: dict[str, Any]) -> dict[str, Any]:
    # ``char_count`` covers the evidence payload before this accounting field
    # is added.  It is deterministic and avoids counting a self-referential
    # value.
    packet["char_count"] = len(_canonical(packet))
    packet["charcount"] = packet["char_count"]
    return packet


class EvidenceStore:
    """Detached evidence records with bounded, cached queries."""

    def __init__(self, goal: Any, records: Sequence[Mapping[str, Any]]) -> None:
        detached_records = [copy.deepcopy(dict(record)) for record in records]
        self._goal = copy.deepcopy(goal)
        self._records = {int(record["source_step"]): record for record in detached_records}
        self._ordered_steps = [int(record["source_step"]) for record in detached_records]
        self._source_hash = _sha256({"goal": self._goal, "steps": detached_records})
        self._parsed: dict[tuple[int, str], dict[str, Any]] = {}
        self._cache: dict[tuple[int, str], dict[str, Any]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._skeleton = self._make_skeleton(detached_records)

    @property
    def source_hash(self) -> str:
        return self._source_hash

    @property
    def summary(self) -> dict[str, Any]:
        return copy.deepcopy(self._skeleton)

    @property
    def skeleton(self) -> dict[str, Any]:
        return copy.deepcopy(self._skeleton)

    def __getitem__(self, key: str) -> Any:
        return self.summary[key]

    def _make_skeleton(self, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        for record in records:
            pre = record["pre_obs"]
            post = record["post_obs"]
            action = record["action"]
            pre_parse = _parse(pre)
            post_parse = _parse(post)
            steps.append(
                {
                    "source_step": record["source_step"],
                    "position": record["position"],
                    "action_type": copy.deepcopy(action.get("action_type")),
                    "action_fields": _literal_action(action),
                    "activity_transition": {
                        "pre": _activity(pre),
                        "post": _activity(post),
                    },
                    "parse_completeness": {
                        "pre": _parse_sidecar(pre_parse, include_issue_details=False),
                        "post": _parse_sidecar(post_parse, include_issue_details=False),
                    },
                }
            )
        return {
            "schema": SCHEMA,
            "goal": copy.deepcopy(self._goal),
            "source_hash": self._source_hash,
            "source_alignment": [
                {"position": record["position"], "source_step": record["source_step"]}
                for record in records
            ],
            "steps": steps,
            "target_policy": "target metadata is available only from target query",
            "intent_policy": "no intents inferred",
        }

    def _record(self, source_step: int) -> dict[str, Any]:
        if type(source_step) is not int or source_step <= 0:
            raise EvidenceStoreError("source_step must be a positive integer")
        try:
            return self._records[source_step]
        except KeyError as exc:
            raise IndexError(f"source_step {source_step} is outside this store") from exc

    def _parsed_side(self, record: Mapping[str, Any], side: str) -> dict[str, Any]:
        key = (int(record["source_step"]), side)
        if key not in self._parsed:
            observation = record["pre_obs"] if side == "pre" else record["post_obs"]
            self._parsed[key] = _parse(observation)
        return self._parsed[key]

    def _target(self, record: Mapping[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        parsed = self._parsed_side(record, "pre")
        packet["parse"] = _parse_sidecar(parsed)
        action = record["action"]
        packet["action"] = copy.deepcopy(action)
        target_index = action.get("index")
        if type(target_index) is not int or target_index < 0:
            packet.update(
                {
                    "status": "unknown",
                    "reason": "actual_training_action_has_no_valid_target_index",
                    "target_index": target_index,
                    "target_nodes": [],
                    "descriptor_candidates": [],
                }
            )
            return packet
        packet["target_index"] = target_index
        if not parsed.get("complete"):
            packet.update(
                {
                    "status": "unknown",
                    "reason": "pre_accessibility_tree_incomplete",
                    "target_nodes": [],
                    "descriptor_candidates": [],
                    "raw_available_via": "state_pre",
                }
            )
            return packet
        target_nodes = [
            copy.deepcopy(node)
            for node in parsed.get("nodes", [])
            if isinstance(node, Mapping) and node.get("index") == target_index
        ]
        packet["target_nodes"] = target_nodes
        if len(target_nodes) != 1:
            packet.update(
                {
                    "status": "ambiguous" if len(target_nodes) > 1 else "missing",
                    "reason": "pre_target_index_is_not_unique",
                    "descriptor_candidates": [],
                }
            )
            return packet

        target_entries = _visible_entries(target_nodes[0])
        candidates: list[dict[str, Any]] = []
        for node in parsed.get("nodes", []):
            if not isinstance(node, Mapping):
                continue
            node_entries = _visible_entries(node)
            for wanted in target_entries:
                if any(
                    entry["field"] == wanted["field"]
                    and entry["normalized_value"] == wanted["normalized_value"]
                    for entry in node_entries
                ):
                    candidates.append(
                        {
                            "index": node.get("index"),
                            "field": wanted["field"],
                            "value": wanted["value"],
                            "normalized_value": wanted["normalized_value"],
                            "node": copy.deepcopy(dict(node)),
                        }
                    )
        packet.update(
            {
                "status": "found",
                "target_node": copy.deepcopy(target_nodes[0]),
                "target": copy.deepcopy(target_nodes[0]),
                "descriptor_candidates": candidates,
                "descriptor_candidate_count": len(candidates),
            }
        )
        return packet

    def _effect(self, record: Mapping[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        pre = self._parsed_side(record, "pre")
        post = self._parsed_side(record, "post")
        packet["parse"] = {
            "pre": _parse_sidecar(pre),
            "post": _parse_sidecar(post),
        }
        if not pre.get("complete") or not post.get("complete"):
            packet.update(
                {
                    "status": "unknown",
                    "effect_witness": "unknown",
                    "reason": "pre_or_post_accessibility_tree_incomplete",
                    "post_only_visible_values": [],
                    "post_only_groups": [],
                    "raw_available_via": ["state_pre", "state_post"],
                }
            )
            return packet
        before = {
            (entry["field"], entry["normalized_value"])
            for node in pre.get("nodes", [])
            if isinstance(node, Mapping)
            for entry in _visible_entries(node)
        }
        post_entries = [
            entry
            for node in post.get("nodes", [])
            if isinstance(node, Mapping)
            for entry in _visible_entries(node)
        ]
        post_only = [
            copy.deepcopy(entry)
            for entry in post_entries
            if (entry["field"], entry["normalized_value"]) not in before
        ]
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in post_only:
            key = (entry["field"], entry["normalized_value"])
            group = groups.setdefault(
                key,
                {
                    "field": entry["field"],
                    "normalized_value": entry["normalized_value"],
                    "values": [],
                    "indices": [],
                    "occurrence_count": 0,
                },
            )
            group["values"].append(entry["value"])
            group["indices"].append(entry["index"])
            group["occurrence_count"] += 1
        packet.update(
            {
                "status": "evidenced" if post_only else "unknown",
                "effect_witness": "evidenced" if post_only else "unknown",
                "reason": "post_only_visible_values" if post_only else "no_post_only_visible_value",
                "post_only_visible_values": post_only,
                "post_only_groups": list(groups.values()),
            }
        )
        return packet

    def _state(self, record: Mapping[str, Any], packet: dict[str, Any], side: str) -> dict[str, Any]:
        observation = record["pre_obs"] if side == "pre" else record["post_obs"]
        parsed = self._parsed_side(record, side)
        packet.update(
            {
                "status": "complete" if parsed.get("complete") else "unknown",
                "activity": _activity(observation),
                "parse": _parse_sidecar(parsed),
                "raw_evidence": copy.deepcopy(observation),
            }
        )
        if not parsed.get("complete"):
            packet["reason"] = "accessibility_tree_incomplete"
        return packet

    def query(self, source_step: int, query: str) -> dict[str, Any]:
        """Return one cached packet for a source step and bounded query.

        The canonical argument order is ``query(source_step, query)``.  For
        small callers that naturally write ``query(query, source_step)``, the
        two arguments are accepted in that order as well.
        """

        if isinstance(source_step, str) and type(query) is int:
            source_step, query = query, source_step
        if query not in QUERIES:
            raise EvidenceStoreError(f"query must be one of {sorted(QUERIES)}")
        record = self._record(source_step)
        key = (source_step, query)
        if key in self._cache:
            self._cache_hits += 1
            return copy.deepcopy(self._cache[key])
        self._cache_misses += 1
        packet = _packet_base(self._source_hash, record, query)
        if query == "target":
            packet = self._target(record, packet)
        elif query == "effect":
            packet = self._effect(record, packet)
        elif query == "state_pre":
            packet = self._state(record, packet, "pre")
        else:
            packet = self._state(record, packet, "post")
        packet = _finish_packet(packet)
        self._cache[key] = copy.deepcopy(packet)
        return copy.deepcopy(packet)

    def get(self, source_step: int, query: str) -> dict[str, Any]:
        """Alias for :meth:`query`."""

        return self.query(source_step, query)

    def cache_info(self) -> dict[str, int]:
        return {
            "entries": len(self._cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
        }


def build_store(goal: Any, steps: Sequence[Mapping[str, Any]]) -> EvidenceStore:
    """Build a detached evidence store from native training step records."""

    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes, bytearray)):
        raise EvidenceStoreError("steps must be a sequence of mappings")
    records = [_record_from_step(step, position) for position, step in enumerate(steps)]
    if not records:
        raise EvidenceStoreError("steps must contain at least one record")
    source_steps = [record["source_step"] for record in records]
    if len(set(source_steps)) != len(source_steps):
        raise EvidenceStoreError("source steps must be unique")
    # Validate the detached JSON shape once.  The store never serializes or
    # reads the caller's original objects after this point.
    _canonical({"goal": goal, "steps": records})
    return EvidenceStore(copy.deepcopy(goal), records)


def query_store(store: EvidenceStore, source_step: int, query: str) -> dict[str, Any]:
    """Small functional wrapper for callers that do not retain a method."""

    if not isinstance(store, EvidenceStore):
        raise TypeError("store must be an EvidenceStore")
    return store.query(source_step, query)


__all__ = [
    "EvidenceStore",
    "EvidenceStoreError",
    "build_store",
    "query_store",
]
