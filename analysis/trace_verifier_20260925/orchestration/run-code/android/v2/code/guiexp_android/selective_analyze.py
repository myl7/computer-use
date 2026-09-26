"""Read-only accounting and outcome analysis for the selective pilot.

This module is intentionally independent of ``selective_pilot``.  The harness
passes a source-verified spec, and this analyzer reads only episode artifacts
and the canonical SQLite ledger in read-only mode.  It writes the two report
files in the supplied output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA = "selective-analysis/1"
NAMESPACE = "selective_20260915"
PHASES = ("train", "build", "serving")
ARMS = ("reactive", "full_fallback", "local_rejoin")
NANO = Decimal(1000000000)
LOGICAL_RE = re.compile(r"/logical-(?P<sequence>\d+)(?:/|$)")
MODULE_VERSION = SCHEMA


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _text(value: Any, limit: int = 240) -> str | None:
    if value is None:
        return None
    value = " ".join(str(value).split())
    if not value:
        return None
    return value[:limit - 1] + "…" if len(value) >= limit else value


def _money(nano: int | Decimal | None) -> str | None:
    if nano is None:
        return None
    return format(Decimal(nano) / NANO, "f")


def _module_sha256() -> str | None:
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _path_label(path: Path, out: Path, origin_out: Path | None = None) -> str:
    try:
        return str(path.relative_to(out))
    except ValueError:
        return f"origin/{path.relative_to(origin_out or out)}"


def _ledger_path(out: Path, spec: Mapping[str, Any]) -> Path:
    raw = spec.get("ledger_absolute") or spec.get("ledger")
    if not raw:
        raw = out.parent / "revision_20260913" / "budget.sqlite3"
    path = Path(str(raw))
    if not path.is_absolute():
        # The prepared pilot stores relative ledger references from the
        # repository root, while tests may provide an output-local ledger.
        candidates = ((Path(__file__).resolve().parents[2] / path), (out / path), (out.parent / path))
        path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), candidates[0].resolve())
    else:
        path = path.resolve()
    if path.name != "budget.sqlite3" or "revision_20260913" not in path.parts:
        raise ValueError("selective analyzer requires the revision_20260913 budget ledger")
    return path


def _version_for_spec(spec: Mapping[str, Any]) -> str | None:
    value = spec.get("revision")
    if isinstance(value, str) and value:
        return value
    value = spec.get("version")
    return value if isinstance(value, str) and value else None


def _origin_out(out: Path, spec: Mapping[str, Any]) -> Path:
    provenance = _dict(spec.get("provenance"))
    value = provenance.get("origin_root") or provenance.get("origin_out")
    if not isinstance(value, str) or not value:
        return out
    path = Path(value)
    if not path.is_absolute():
        candidates = (out / path, Path(__file__).resolve().parents[2] / path, out.parent / path)
        path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    return path.resolve()


def _episode_parts(
    episode: str,
    families: set[str],
    version: str | None = None,
) -> tuple[str | None, list[str]]:
    prefix = NAMESPACE + "/"
    if not isinstance(episode, str) or not episode.startswith(prefix):
        return None, []
    parts = episode[len(prefix):].split("/")
    if parts and (parts[0] in {"train", "build"} or parts[0] in families):
        return "base", parts
    if version and len(parts) > 1 and parts[0] == version:
        return "version", parts[1:]
    return None, []


def _phase_for_episode(episode: str, families: set[str], version: str | None = None) -> str:
    _scope, parts = _episode_parts(episode, families, version)
    if parts and parts[0] in {"train", "build"}:
        return parts[0]
    if parts and parts[0] in families:
        return "serving"
    return "unattributed"


def _family_for_episode(episode: str, families: set[str], version: str | None = None) -> str | None:
    _scope, parts = _episode_parts(episode, families, version)
    if parts and parts[0] in {"train", "build"}:
        return parts[1] if len(parts) > 1 and parts[1] in families else None
    return parts[0] if parts and parts[0] in families else None


def _logical_sequence(call_id: str) -> int | None:
    match = LOGICAL_RE.search(call_id or "")
    return int(match.group("sequence")) if match else None


def _scan_local_calls(out: Path, origin_out: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Index local model-call records without copying prompts or responses."""
    by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    output_roots = [out] if origin_out is None or origin_out == out else [out, origin_out]
    for output_root in output_roots:
        roots = [output_root / "episodes", output_root / "training"]
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("trajectory.jsonl"):
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    continue
                for line in lines:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(value, Mapping) or value.get("record_type") != "model_call":
                        continue
                    episode = value.get("episode_id")
                    purpose = value.get("purpose")
                    if not isinstance(episode, str):
                        continue
                    by_episode[episode].append(
                        {
                            "call_index": _int(value.get("call_index")),
                            "purpose": purpose if isinstance(purpose, str) and purpose else None,
                            "usage": _dict(value.get("usage")),
                            "path": str(path.relative_to(output_root)),
                        }
                    )
    return by_episode


def _scan_build_calls(
    out: Path,
    families: set[str],
    version: str | None = None,
    origin_out: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    by_episode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if version:
        build_roots = [(version, out / "build")]
        if origin_out is not None and origin_out != out:
            build_roots.append((None, origin_out / "build"))
    else:
        output_roots = [out] if origin_out is None or origin_out == out else [out, origin_out]
        build_roots = [(None, output_root / "build") for output_root in output_roots]
    for family in families:
        for layer, build_root in build_roots:
            family_dir = build_root / family
            for path in sorted(family_dir.glob("*_response.json")):
                label = path.stem
                purpose = {
                    "first_response": "build_initial",
                    "repair_response": "build_repair",
                }.get(label)
                if purpose is None:
                    continue
                build_episode = f"{NAMESPACE}/build/{family}"
                if layer:
                    build_episode = f"{NAMESPACE}/{layer}/build/{family}"
                if label == "repair_response":
                    build_episode += "/repair"
                by_episode[build_episode].append(
                    {
                        # A repair starts a fresh episode and sequence.
                        "call_index": 1,
                        "purpose": purpose,
                        "usage": _dict(_read_json(path) or {}).get("usage", {}),
                        "path": _path_label(path, out, origin_out),
                    }
                )
    return by_episode


def _purpose_index(
    out: Path,
    families: set[str],
    version: str | None = None,
    origin_out: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    result = _scan_local_calls(out, origin_out)
    for episode, calls in _scan_build_calls(out, families, version, origin_out).items():
        result.setdefault(episode, []).extend(calls)
    return result


def _purpose_for(
    receipt: Mapping[str, Any],
    purpose_index: Mapping[str, list[Mapping[str, Any]]],
) -> str:
    episode = str(receipt.get("episode") or "")
    candidates = list(purpose_index.get(episode) or [])
    sequence = _logical_sequence(str(receipt.get("id") or ""))
    if sequence is not None:
        candidates = [c for c in candidates if c.get("call_index") == sequence]
    if len(candidates) != 1:
        return "unattributed"
    purpose = candidates[0].get("purpose")
    return str(purpose) if isinstance(purpose, str) and purpose else "unattributed"


def _usage_from_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("response_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    usage = _dict(response).get("usage")
    usage = _dict(usage)
    prompt_details = _dict(usage.get("prompt_tokens_details"))
    # The shared ledger preserves the provider response.  Normalize the one
    # nested cache field that the pilot records; do not derive absent totals.
    return {
        "prompt_tokens": _int(usage.get("prompt_tokens")),
        "completion_tokens": _int(usage.get("completion_tokens")),
        "cached_tokens": _int(usage.get("cached_tokens")) if usage.get("cached_tokens") is not None else _int(prompt_details.get("cached_tokens")),
        "total_tokens": _int(usage.get("total_tokens")),
        "cost_usd": usage.get("cost") if isinstance(usage.get("cost"), (int, float)) and not isinstance(usage.get("cost"), bool) else None,
    }


def _receipt(
    row: Mapping[str, Any],
    families: set[str],
    purpose_index: Mapping[str, list[Mapping[str, Any]]],
    version: str | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    state = str(row.get("state") or "unknown")
    reserved = _int(row.get("reserved_nano")) or 0
    actual = _int(row.get("actual_nano"))
    bill_known = actual is not None
    unresolved = actual is None or state != "settled"
    category = "settled" if state == "settled" and bill_known else "unknown_bill" if not bill_known else "incomplete"
    lower = actual if actual is not None else 0
    upper = lower + (reserved if unresolved else 0)
    episode = str(row.get("episode") or "")
    return {
        "id": str(row.get("id") or ""),
        "episode": episode,
        "phase": _phase_for_episode(episode, families, version),
        "family": _family_for_episode(episode, families, version),
        "scope": scope,
        "purpose": _purpose_for(row, purpose_index),
        "state": state,
        "category": category,
        "bill_known": bill_known,
        "unresolved_reservation": unresolved,
        "unresolved_kind": "in_flight" if state == "reserved" and not bill_known else "unknown" if not bill_known else "incomplete" if unresolved else "none",
        "actual_nano": actual,
        "reserved_nano": reserved,
        "actual_cost_usd": _money(actual),
        "reserved_cost_usd": _money(reserved),
        "lower_bound_usd": _money(lower),
        "upper_bound_usd": _money(upper),
        "usage": _usage_from_receipt(row),
        "error_type": _text(row.get("error_type"), 120),
        "created_unix": row.get("created"),
    }


def _preserved_imports(spec: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    imports: dict[str, dict[str, str]] = {}
    for row_id, item in _dict(spec.get("imported_rows")).items():
        mapping = _dict(item)
        source = mapping.get("source_episode_id")
        receipt = mapping.get("receipt_episode_id")
        if all(isinstance(value, str) and value for value in (row_id, source, receipt)):
            imports[row_id] = {
                "source_episode_id": source,
                "receipt_episode_id": receipt,
            }
    return imports


def _episode_imports(spec: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    imports: dict[str, dict[str, str]] = {}
    for row in _list(spec.get("episodes")):
        if not isinstance(row, Mapping):
            continue
        row_id = row.get("id")
        source = row.get("source_episode_id")
        receipt = row.get("receipt_episode_id")
        if all(isinstance(value, str) and value for value in (row_id, source, receipt)):
            imports[row_id] = {
                "source_episode_id": source,
                "receipt_episode_id": receipt,
            }
    imports.update(_preserved_imports(spec))
    return imports


def _versioned_row_mappings(spec: Mapping[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in _list(spec.get("episodes")):
        if not isinstance(row, Mapping):
            continue
        row_id = row.get("id")
        source = row.get("source_episode_id")
        receipt = row.get("receipt_episode_id")
        if all(isinstance(value, str) and value for value in (row_id, source, receipt)):
            rows.append({
                "id": row_id,
                "source_episode_id": source,
                "receipt_episode_id": receipt,
            })
    return rows


def _imported_serving_rows(
    families: set[str],
    version: str | None,
    imports: Mapping[str, Mapping[str, str]],
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not version:
        return []
    source_ids = {
        row.get("source_episode_id")
        for row in rows
        if isinstance(row.get("source_episode_id"), str)
    }
    result: list[dict[str, Any]] = []
    for row_id, mapping in sorted(imports.items()):
        source = mapping.get("source_episode_id")
        if source in source_ids or _phase_for_episode(source, families, version) != "serving":
            continue
        suffix = source[len(NAMESPACE) + 1:] if source.startswith(NAMESPACE + "/") else ""
        parts = suffix.split("/")
        if len(parts) < 3 or parts[0] not in families:
            continue
        result.append(
            {
                "id": f"{NAMESPACE}/{version}/{suffix}",
                "family": parts[0],
                "binding_id": parts[1],
                "arm": parts[2],
                "source_episode_id": source,
                "receipt_episode_id": mapping["receipt_episode_id"],
                "imported_from_row_id": row_id,
            }
        )
    return result


def _receipt_scope(
    episode: str,
    receipt_id: str,
    families: set[str],
    version: str | None,
    imported_receipts: set[str],
) -> str | None:
    phase = _phase_for_episode(episode, families, version)
    layer, _parts = _episode_parts(episode, families, version)
    if phase == "train":
        return "shared_training"
    if phase == "build":
        return "version_build" if layer == "version" else "development_build"
    if phase == "serving":
        if layer == "version":
            return "version_serving"
        if episode in imported_receipts or receipt_id in imported_receipts:
            return "imported_serving"
    return None


def _load_receipts(out: Path, spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    families = {str(x) for x in _list(spec.get("families"))}
    version = _version_for_spec(spec)
    imports = _episode_imports(spec)
    preserved_imports = _preserved_imports(spec)
    versioned_sources = {
        item["source_episode_id"]
        for item in _versioned_row_mappings(spec)
    }
    active_preserved_imports = {
        row_id: mapping
        for row_id, mapping in preserved_imports.items()
        if mapping["source_episode_id"] not in versioned_sources
    }
    imported_receipts = {
        value
        for item in imports.values()
        for value in (item["receipt_episode_id"],)
    }
    ledger_path = _ledger_path(out, spec)
    origin_out = _origin_out(out, spec)
    purpose_index = _purpose_index(out, families, version, origin_out)
    try:
        with sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True, timeout=5) as database:
            columns = [row[1] for row in database.execute("PRAGMA table_info(calls)").fetchall()]
            required = {"id", "episode", "reserved_nano", "actual_nano", "state", "response_json", "error_type", "created"}
            if not required <= set(columns):
                raise ValueError("shared ledger calls table has an unexpected schema")
            rows = database.execute(
                "SELECT id,episode,reserved_nano,actual_nano,state,response_json,error_type,created "
                "FROM calls WHERE id LIKE ? OR episode LIKE ? ORDER BY created,id",
                (NAMESPACE + "/%", NAMESPACE + "/%"),
            ).fetchall()
            whole_rows = database.execute(
                "SELECT state,reserved_nano,actual_nano FROM calls ORDER BY created,id"
            ).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise ValueError(f"could not read the shared ledger read-only: {type(exc).__name__}") from None
    keys = ("id", "episode", "reserved_nano", "actual_nano", "state", "response_json", "error_type", "created")
    selected_rows: list[tuple[tuple[Any, ...], str]] = []
    for row in rows:
        row_dict = dict(zip(keys, row))
        scope = _receipt_scope(
            str(row_dict.get("episode") or ""),
            str(row_dict.get("id") or ""),
            families,
            version,
            imported_receipts,
        )
        if version is None and scope is None:
            # The v1 report historically included every current namespace
            # row. Keep that behavior for its existing spec.
            scope = "unattributed"
        if scope is not None:
            selected_rows.append((row, scope))
    receipts = [
        _receipt(dict(zip(keys, row)), families, purpose_index, version, scope)
        for row, scope in selected_rows
    ]
    whole_actuals = [row[2] for row in whole_rows if isinstance(row[2], int)]
    whole = {
        "row_count": len(whole_rows),
        "state_counts": dict(sorted(Counter(str(row[0]) for row in whole_rows).items())),
        "in_flight_count": sum(row[2] is None and row[0] == "reserved" for row in whole_rows),
        "unknown_bill_count": sum(row[2] is None and row[0] != "reserved" for row in whole_rows),
        "unknown_bill_total_count": sum(row[2] is None for row in whole_rows),
        "incomplete_count": sum(row[2] is not None and row[0] != "settled" for row in whole_rows),
        "actual_known_usd": _money(sum(whole_actuals) if whole_actuals else None),
        "unresolved_reserved_usd": _money(sum((row[1] or 0) for row in whole_rows if row[2] is None or row[0] != "settled")),
        "lower_bound_usd": _money(sum((row[2] if isinstance(row[2], int) else 0) for row in whole_rows)),
        "upper_bound_usd": _money(sum((row[2] if isinstance(row[2], int) else 0) + ((row[1] or 0) if row[2] is None or row[0] != "settled" else 0) for row in whole_rows)),
    }
    return receipts, {
        "path": str(ledger_path),
        "mode": "ro",
        "whole_budget": whole,
        "version": version,
        "origin_out": str(origin_out),
        "imported_episode_count": len(preserved_imports),
        "versioned_row_count": len(_versioned_row_mappings(spec)),
        "active_imported_episode_count": len(active_preserved_imports),
        "missing_import_receipt_count": sum(
            not any(
                row[0] == item["receipt_episode_id"] or row[1] == item["receipt_episode_id"]
                for row in rows
            )
            for item in active_preserved_imports.values()
        ),
    }


def _fee_summary(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(receipts)
    actual = [int(r["actual_cost_usd"] is not None) for r in records]
    actual_nano = []
    lower_nano = []
    upper_nano = []
    for record in records:
        lower_nano.append(int(Decimal(str(record.get("lower_bound_usd") or 0)) * NANO))
        upper_nano.append(int(Decimal(str(record.get("upper_bound_usd") or 0)) * NANO))
        if record.get("actual_cost_usd") is not None:
            actual_nano.append(int(Decimal(str(record["actual_cost_usd"])) * NANO))
    categories = Counter(str(record.get("category") or "unknown") for record in records)
    states = Counter(str(record.get("state") or "unknown") for record in records)
    in_flight = sum(record.get("unresolved_kind") == "in_flight" for record in records)
    unknown_total = categories.get("unknown_bill", 0)
    return {
        "receipt_count": len(records),
        "state_counts": dict(sorted(states.items())),
        "category_counts": dict(sorted(categories.items())),
        "settled_count": categories.get("settled", 0),
        # These fee-table counts are disjoint: an in-flight reservation is
        # shown in its own column even though it has no settled bill.
        "unknown_bill_count": unknown_total - in_flight,
        "unknown_bill_total_count": unknown_total,
        "incomplete_count": categories.get("incomplete", 0),
        "in_flight_count": in_flight,
        "unresolved_unknown_count": sum(record.get("unresolved_kind") == "unknown" for record in records),
        "known_bill_count": sum(actual),
        "actual_known_usd": _money(sum(actual_nano) if actual_nano else None),
        "lower_bound_usd": _money(sum(lower_nano)),
        "upper_bound_usd": _money(sum(upper_nano)),
    }


def _token_summary(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(receipts)
    output: dict[str, Any] = {"receipt_count": len(records), "missing": {}}
    for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        values = [_int(_dict(r.get("usage")).get(field)) for r in records]
        known = [value for value in values if value is not None]
        output[field] = sum(known) if known else None
        output["missing"][field] = len(values) - len(known)
    return output


def _model_call_summary(calls: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(calls)
    summary = _token_summary([{"usage": _dict(call.get("usage"))} for call in records])
    summary["call_count"] = len(records)
    summary.pop("receipt_count", None)
    return summary


def _summarize_receipts(receipts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(receipts)
    purpose_counts = Counter(str(r.get("purpose") or "unattributed") for r in records)
    result = _fee_summary(records)
    result["tokens"] = _token_summary(records)
    result["purpose_counts"] = dict(sorted(purpose_counts.items()))
    result["unattributed_count"] = purpose_counts.get("unattributed", 0)
    return result


def _trace_stats(result: Mapping[str, Any], arm: str) -> dict[str, Any]:
    if arm == "reactive":
        return {
            "trace_evidence": "not_applicable",
            "verified_rejoin_events": 0,
            "local_rejoin_actions": 0,
            "program_actions_following_join": 0,
            "useful_local_rejoin": False,
        }
    run = _dict(result.get("run"))
    plan = _dict(run.get("plan"))
    trace = plan.get("trace")
    if not isinstance(trace, list):
        return {
            "trace_evidence": "unknown",
            "verified_rejoin_events": None,
            "local_rejoin_actions": None,
            "program_actions_following_join": None,
            "useful_local_rejoin": False,
        }
    joins = 0
    local_actions = 0
    suffix_actions = 0
    joined = False
    for item in trace:
        if not isinstance(item, Mapping):
            continue
        kind = item.get("kind")
        before = _dict(_dict(item.get("guards")).get("before"))
        after = _dict(_dict(item.get("guards")).get("after"))
        if kind == "local":
            local_actions += 1
            if after.get("ok") is True and before.get("ok") is not True:
                joins += 1
                joined = True
        elif kind == "program" and joined:
            suffix_actions += 1
    final_success = result.get("success") is True
    return {
        "trace_evidence": "evidenced",
        "verified_rejoin_events": joins,
        "local_rejoin_actions": local_actions,
        "program_actions_following_join": suffix_actions,
        "useful_local_rejoin": bool(local_actions and joins and suffix_actions and final_success),
    }


def _state_entry(
    out: Path,
    row: Mapping[str, Any],
    receipts: list[dict[str, Any]],
    local_calls: Mapping[str, list[Mapping[str, Any]]],
    imports: Mapping[str, Mapping[str, str]] | None = None,
    version: str | None = None,
    origin_out: Path | None = None,
) -> dict[str, Any]:
    episode_id = str(row.get("id") or "")
    imported = _dict((imports or {}).get(episode_id))
    if not imported and all(
        isinstance(row.get(key), str) and row.get(key)
        for key in ("source_episode_id", "receipt_episode_id")
    ):
        imported = {
            "source_episode_id": row["source_episode_id"],
            "receipt_episode_id": row["receipt_episode_id"],
        }
    receipt_episode_id = str(imported.get("receipt_episode_id") or episode_id)
    imported_reference = bool(imported and receipt_episode_id != episode_id)
    state_episode_id = str(imported.get("source_episode_id") or episode_id) if imported_reference else episode_id
    state_root = (origin_out or out) if imported_reference else out
    state_path = state_root / "episodes" / state_episode_id / "state.json"
    state = _read_json(state_path) if state_path.is_file() else None
    if state is None:
        status = "missing" if not state_path.exists() else "unreadable"
        result: dict[str, Any] = {}
    else:
        status = str(state.get("status") or "unknown")
        result = _dict(state.get("result"))
    success = result.get("success") if isinstance(result.get("success"), bool) else None
    complete = status == "done" and isinstance(success, bool)
    episode_receipts = [r for r in receipts if r.get("episode") == receipt_episode_id]
    episode_calls = list(local_calls.get(state_episode_id) or [])
    stats = _trace_stats(result, str(row.get("arm") or "")) if complete else {
        "trace_evidence": "not_applicable" if row.get("arm") == "reactive" else "unknown",
        "verified_rejoin_events": 0 if row.get("arm") == "reactive" else None,
        "local_rejoin_actions": 0 if row.get("arm") == "reactive" else None,
        "program_actions_following_join": 0 if row.get("arm") == "reactive" else None,
        "useful_local_rejoin": False,
    }
    return {
        "id": episode_id,
        "source_episode_id": imported.get("source_episode_id") if imported else None,
        "receipt_episode_id": receipt_episode_id,
        "provenance": "imported" if imported_reference else "version" if version else "current",
        "family": row.get("family"),
        "binding_id": row.get("binding_id"),
        "arm": row.get("arm"),
        "seed": row.get("seed"),
        "state_status": status,
        "complete": complete,
        "final_success": success,
        "actions": _int(result.get("actions")),
        "trajectory_present": (state_path.parent / "trajectory.jsonl").is_file(),
        "model_calls": len(episode_calls),
        "model_call_tokens": _model_call_summary(episode_calls),
        "fees": _summarize_receipts(episode_receipts),
        **stats,
    }


def _exact_episode_cost(entry: Mapping[str, Any]) -> int | None:
    fees = _dict(entry.get("fees"))
    if not entry.get("complete") or fees.get("receipt_count", 0) <= 0 or fees.get("unknown_bill_count", 0) or fees.get("incomplete_count"):
        return None
    value = fees.get("actual_known_usd")
    return int(Decimal(str(value)) * NANO) if value is not None else None


def _serving_comparisons(entries: list[dict[str, Any]], families: list[str]) -> dict[str, Any]:
    groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for entry in entries:
        groups[(str(entry.get("family")), str(entry.get("binding_id")))][str(entry.get("arm"))] = entry
    output: dict[str, Any] = {}
    for family in families:
        eligible: list[dict[str, Any]] = []
        ineligible: list[dict[str, Any]] = []
        for (group_family, binding_id), arms in sorted(groups.items()):
            if group_family != family:
                continue
            if all(arm in arms and arms[arm].get("complete") for arm in ARMS):
                eligible.append({"binding_id": binding_id, "arms": arms})
            else:
                ineligible.append(
                    {
                        "binding_id": binding_id,
                        "arm_status": {arm: _dict(arms.get(arm)).get("state_status", "missing") for arm in ARMS},
                        "arm_fees": {arm: _dict(arms.get(arm)).get("fees", {"receipt_count": 0}) for arm in ARMS},
                    }
                )
        arm_report: dict[str, Any] = {}
        for arm in ARMS:
            selected = [group["arms"][arm] for group in eligible]
            successes = sum(item.get("final_success") is True for item in selected)
            exact = [cost for item in selected if (cost := _exact_episode_cost(item)) is not None]
            lower_nano = sum(int(Decimal(str(_dict(item.get("fees")).get("lower_bound_usd") or 0)) * NANO) for item in selected)
            upper_nano = sum(int(Decimal(str(_dict(item.get("fees")).get("upper_bound_usd") or 0)) * NANO) for item in selected)
            unknown_bill_episodes = sum(_dict(item.get("fees")).get("unknown_bill_count", 0) > 0 for item in selected)
            in_flight_episodes = sum(_dict(item.get("fees")).get("in_flight_count", 0) > 0 for item in selected)
            incomplete_episodes = sum(_dict(item.get("fees")).get("incomplete_count", 0) > 0 for item in selected)
            missing_receipt_episodes = sum(_dict(item.get("fees")).get("receipt_count", 0) == 0 for item in selected)
            arm_report[arm] = {
                "complete_matched_count": len(selected),
                "success_count": successes,
                "failed_complete_count": sum(item.get("final_success") is False for item in selected),
                "success_rate": successes / len(selected) if selected else None,
                "total_lower_bound_usd": _money(lower_nano),
                "total_upper_bound_usd": _money(upper_nano),
                "unknown_bill_episode_count": unknown_bill_episodes,
                "in_flight_fee_episode_count": in_flight_episodes,
                "incomplete_fee_episode_count": incomplete_episodes,
                "missing_receipt_episode_count": missing_receipt_episodes,
                "non_exact_bill_episode_count": sum(_exact_episode_cost(item) is None for item in selected),
                "exact_bill_episode_count": len(exact),
                "exact_bill_mean_usd": _money(Decimal(sum(exact)) / len(exact)) if exact else None,
            }
        output[family] = {
            "eligible_complete_matched_bindings": len(eligible),
            "complete_definition": "state status done with a boolean oracle success; success=false remains a complete failed episode",
            "cost_scope": "eligible complete matched bindings only",
            "ineligible_bindings": ineligible,
            "arms": arm_report,
        }
    return output


def _build_cost_reports(
    out: Path,
    families: list[str],
    receipts: list[dict[str, Any]],
    version: str | None,
    origin_out: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    def collect(scope: str, root: Path) -> dict[str, Any]:
        per_family: dict[str, Any] = {}
        for family in families:
            family_root = root / family
            response_items: list[dict[str, Any]] = []
            for path in sorted(family_root.glob("*_response.json")):
                value = _read_json(path) or {}
                try:
                    path_label = str(path.relative_to(out))
                except ValueError:
                    path_label = f"origin/{path.relative_to(origin_out or root)}"
                response_items.append(
                    {
                        "label": path.stem,
                        "path": path_label,
                        "usage": _dict(value.get("usage")),
                    }
                )
            build_path = family_root / "build.json"
            build_value = _read_json(build_path)
            build_status = str(build_value.get("status") or "unknown") if build_value is not None else "missing" if not build_path.exists() else "unreadable"
            family_receipts = [
                receipt for receipt in receipts
                if receipt.get("scope") == scope and receipt.get("family") == family
            ]
            per_family[family] = {
                "status": build_status,
                "response_count": len(response_items),
                "responses": response_items,
                "physical_receipt_count": len(family_receipts),
                "fees": _summarize_receipts(family_receipts),
            }
        all_receipts = [receipt for receipt in receipts if receipt.get("scope") == scope]
        return {
            "response_count": sum(item["response_count"] for item in per_family.values()),
            "physical_receipt_count": len(all_receipts),
            "fees": _summarize_receipts(all_receipts),
            "families": per_family,
        }

    empty = {"response_count": 0, "physical_receipt_count": 0, "fees": _summarize_receipts([]), "families": {}}
    development_root = (origin_out or out) / "build"
    development = collect("development_build", development_root) if version else empty
    version_root = out / "build"
    version_report = collect("version_build" if version else "development_build", version_root)
    return development, version_report


def _counterfactual_economics(
    receipts: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    families: list[str],
    version: str | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for family in families:
        training = [r for r in receipts if r.get("phase") == "train" and r.get("family") == family]
        development_build = [
            r for r in receipts
            if r.get("phase") == "build" and r.get("family") == family and r.get("scope") == "development_build"
        ]
        version_build = [
            r for r in receipts
            if r.get("phase") == "build" and r.get("family") == family
            and r.get("scope") == ("version_build" if version else "development_build")
        ]
        if not version:
            development_build = []
        output[family] = {}
        for arm in ARMS:
            serving = [
                r
                for entry in entries
                if entry.get("family") == family and entry.get("arm") == arm
                for r in receipts
                if r.get("episode") == entry.get("receipt_episode_id", entry.get("id"))
            ]
            training_fee = _fee_summary(training)
            build_fee = _fee_summary(version_build)
            development_fee = _fee_summary(development_build)
            serving_fee = _fee_summary(serving)
            lower = int(Decimal(str(training_fee["lower_bound_usd"])) * NANO)
            upper = int(Decimal(str(training_fee["upper_bound_usd"])) * NANO)
            if arm != "reactive":
                lower += int(Decimal(str(build_fee["lower_bound_usd"])) * NANO)
                upper += int(Decimal(str(build_fee["upper_bound_usd"])) * NANO)
            lower += int(Decimal(str(serving_fee["lower_bound_usd"])) * NANO)
            upper += int(Decimal(str(serving_fee["upper_bound_usd"])) * NANO)
            cumulative_lower = lower + int(Decimal(str(development_fee["lower_bound_usd"])) * NANO)
            cumulative_upper = upper + int(Decimal(str(development_fee["upper_bound_usd"])) * NANO)
            output[family][arm] = {
                "lower_bound_usd": _money(lower),
                "upper_bound_usd": _money(upper),
                "version_lower_bound_usd": _money(lower),
                "version_upper_bound_usd": _money(upper),
                "cumulative_including_all_attempts_lower_bound_usd": _money(cumulative_lower),
                "cumulative_including_all_attempts_upper_bound_usd": _money(cumulative_upper),
                "components": {
                    "shared_training_prefix": training_fee,
                    "shared_build": build_fee if arm != "reactive" else {"receipt_count": 0, "lower_bound_usd": "0", "upper_bound_usd": "0", "note": "reactive arm does not use the compiled build"},
                    "sunk_development_build": development_fee,
                    "serving_arm": serving_fee,
                },
                "physical_build_receipts_counted_once": len(version_build),
                "physical_development_build_receipts_counted_once": len(development_build),
            }
    return output


def _phase_reports(receipts: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    phases = {phase: [r for r in receipts if r.get("phase") == phase] for phase in PHASES}
    phase_costs = {phase: _summarize_receipts(items) for phase, items in phases.items()}
    phases["unattributed"] = [r for r in receipts if r.get("phase") == "unattributed"]
    phase_costs["unattributed"] = _summarize_receipts(phases["unattributed"])
    purposes: dict[str, Any] = {}
    for phase, items in phases.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[str(item.get("purpose") or "unattributed")].append(item)
        purposes[phase] = {purpose: _summarize_receipts(group) for purpose, group in sorted(grouped.items())}
        purposes[phase].setdefault("unattributed", _summarize_receipts([]))
    return phase_costs, purposes


def _version_receipts(receipts: list[dict[str, Any]], version: str | None) -> list[dict[str, Any]]:
    if not version:
        return list(receipts)
    allowed = {"shared_training", "version_build", "version_serving", "imported_serving"}
    return [receipt for receipt in receipts if receipt.get("scope") in allowed]


def _local_call_counts(
    out: Path,
    spec: Mapping[str, Any],
    version: str | None = None,
    origin_out: Path | None = None,
) -> dict[str, Any]:
    families = {str(x) for x in _list(spec.get("families"))}
    index = _scan_local_calls(out, origin_out)
    counts = Counter()
    for episode, calls in index.items():
        counts[_phase_for_episode(episode, families, version)] += len(calls)
    return {"total": sum(counts.values()), "by_phase": dict(sorted(counts.items()))}


_NONTERMINAL_STATUSES = frozenset(("running", "pending", "missing", "unreadable"))


def _terminal_record(
    path: Path,
    out: Path,
    phase: str,
    family: str,
    fallback_id: str,
    receipts: Iterable[Mapping[str, Any]],
    origin_out: Path | None = None,
) -> dict[str, Any]:
    value = _read_json(path) if path.is_file() else None
    status = str(value.get("status") or "unknown") if value is not None else "missing" if not path.exists() else "unreadable"
    identifier = value.get("episode_id") if value is not None else None
    owned = [receipt for receipt in receipts if receipt.get("episode") == (identifier or fallback_id)]
    return {
        "id": str(identifier) if isinstance(identifier, str) and identifier else fallback_id,
        "family": family,
        "phase": phase,
        "status": status,
        "terminal": status not in _NONTERMINAL_STATUSES,
        "owned_receipt_count": len(owned),
        "path": _path_label(path, out, origin_out),
    }


def _terminal_statuses(
    out: Path,
    families: list[str],
    serving: list[Mapping[str, Any]],
    receipts: Iterable[Mapping[str, Any]],
    version: str | None = None,
    origin_out: Path | None = None,
) -> dict[str, Any]:
    receipts = list(receipts)
    train_records: list[dict[str, Any]] = []
    training_root = (origin_out or out) / "training"
    for family in families:
        for path in sorted((training_root / family).glob("*/state.json")):
            train_records.append(_terminal_record(path, out, "train", family, str(path.parent.name), receipts, origin_out))
    build_root = out / "build"
    build_records = [
        _terminal_record(
            build_root / family / "build.json",
            out,
            "build",
            family,
            f"{NAMESPACE}/{version + '/' if version else ''}build/{family}",
            receipts,
            origin_out,
        )
        for family in families
    ]
    development_records = [
        _terminal_record(
            (origin_out or out) / "build" / family / "build.json",
            out,
            "development_build",
            family,
            f"{NAMESPACE}/build/{family}",
            receipts,
            origin_out,
        )
        for family in families
    ] if version else []
    serving_statuses = Counter(str(entry.get("state_status") or "unknown") for entry in serving)

    def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "artifact_count": len(records),
            "terminal_count": sum(record["terminal"] for record in records),
            "status_counts": dict(sorted(Counter(record["status"] for record in records).items())),
            "records": records,
        }

    result = {
        "train": summarize(train_records),
        "build": summarize(build_records),
        "serving": {
            "planned_count": len(serving),
            "terminal_count": sum(status not in _NONTERMINAL_STATUSES for status in serving_statuses.elements()),
            "status_counts": dict(sorted(serving_statuses.items())),
        },
    }
    if version:
        result["development_build"] = summarize(development_records)
    return result


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Selective pilot accounting analysis",
        "",
        "This report reads the source-verified spec, local episode/build artifacts, and the shared ledger through SQLite read-only mode. It makes no model, API, emulator, or UI calls.",
        "",
        f"The planned serving set has {report.get('planned_serving_episodes', 0)} episodes. The selective ledger contributes {len(report.get('receipts', []))} physical receipts for version {report.get('version') or 'v1'}. The whole-budget row is reported separately because the shared ledger also contains earlier revision calls.",
        "",
        "## Phase fee bounds",
        "",
        "| phase | receipts | settled | in flight | unknown bill (excl. in flight) | incomplete billing | lower bound | upper bound |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    phase_costs = _dict(report.get("phase_costs"))
    for phase in PHASES:
        item = _dict(phase_costs.get(phase))
        lines.append(
            f"| {phase} | {item.get('receipt_count', 0)} | {item.get('settled_count', 0)} | {item.get('in_flight_count', 0)} | {item.get('unknown_bill_count', 0)} | {item.get('incomplete_count', 0)} | {item.get('lower_bound_usd', '0')} | {item.get('upper_bound_usd', '0')} |"
        )
    unattributed = _dict(phase_costs.get("unattributed"))
    if unattributed.get("receipt_count", 0):
        lines.append(
            f"| unattributed | {unattributed.get('receipt_count', 0)} | {unattributed.get('settled_count', 0)} | {unattributed.get('in_flight_count', 0)} | {unattributed.get('unknown_bill_count', 0)} | {unattributed.get('incomplete_count', 0)} | {unattributed.get('lower_bound_usd', '0')} | {unattributed.get('upper_bound_usd', '0')} |"
        )
    whole = _dict(_dict(report.get("ledger")).get("whole_budget"))
    lines.append(f"| whole shared ledger | {whole.get('row_count', 0)} | | {whole.get('in_flight_count', 0)} | {whole.get('unknown_bill_count', 0)} | {whole.get('incomplete_count', 0)} | {whole.get('lower_bound_usd')} | {whole.get('upper_bound_usd')} |")
    lines.extend(["", "Fee-table counts are disjoint billing states; in-flight reservations have no settled bill but remain in the upper bound.", "", "## Build cost provenance", "", "| build scope | response records | physical receipts | lower bound | upper bound |", "|---|---:|---:|---:|---:|"])
    for label, key in (("sunk development attempts", "development_build_costs"), ("current version build", "version_build_costs")):
        item = _dict(report.get(key))
        fees = _dict(item.get("fees"))
        lines.append(f"| {label} | {item.get('response_count', 0)} | {item.get('physical_receipt_count', 0)} | {fees.get('lower_bound_usd', '0')} | {fees.get('upper_bound_usd', '0')} |")
    lines.extend(["", "## Terminal artifact states", "", "| phase | records | terminal | status counts |", "|---|---:|---:|---|"])
    terminal_phases = ["train", "build"] + (["development_build"] if "development_build" in _dict(report.get("terminal_states")) else []) + ["serving"]
    for phase in terminal_phases:
        item = _dict(_dict(report.get("terminal_states")).get(phase))
        counts = ", ".join(f"{key}={value}" for key, value in _dict(item.get("status_counts")).items()) or "none"
        lines.append(f"| {phase} | {item.get('artifact_count', item.get('planned_count', 0))} | {item.get('terminal_count', 0)} | {counts} |")
    lines.extend(["", "## Planned serving episode states", "", "| family | binding | arm | state | complete | final success | receipts | model calls | actions | joins / local / suffix | useful |", "|---|---|---|---|---:|---|---:|---:|---:|---|---:|"])
    for entry in report.get("serving_episodes", []):
        joins = entry.get("verified_rejoin_events")
        local = entry.get("local_rejoin_actions")
        suffix = entry.get("program_actions_following_join")
        lines.append(
            f"| {entry.get('family')} | {entry.get('binding_id')} | {entry.get('arm')} | {entry.get('state_status')} | {str(entry.get('complete')).lower()} | {entry.get('final_success')} | {_dict(entry.get('fees')).get('receipt_count', 0)} | {entry.get('model_calls', 0)} | {entry.get('actions')} | {joins} / {local} / {suffix} | {str(entry.get('useful_local_rejoin')).lower()} |"
        )
    lines.extend(["", "## Complete matched-binding comparisons", "", "A denominator row requires a done state with a boolean oracle result in all three arms for the same binding; success=false remains a complete failure. Cost bounds here cover matched-complete episodes only. Exact bill means use only that subset whose receipts all have settled bills; unknown-bill and in-flight fees remain separate.", ""])
    for family, comparison in _dict(report.get("serving_comparisons")).items():
        lines.append(f"### {family}")
        lines.append("")
        lines.append("| arm | matched complete | successes / failures | success rate | exact mean known bills | unknown / in-flight / incomplete / missing | matched-complete lower / upper |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for arm, item in _dict(comparison).get("arms", {}).items():
            lines.append(
                f"| {arm} | {item.get('complete_matched_count', 0)} | {item.get('success_count', 0)} / {item.get('failed_complete_count', 0)} | {item.get('success_rate')} | {item.get('exact_bill_mean_usd')} ({item.get('exact_bill_episode_count', 0)}) | {item.get('unknown_bill_episode_count', 0)} / {item.get('in_flight_fee_episode_count', 0)} / {item.get('incomplete_fee_episode_count', 0)} / {item.get('missing_receipt_episode_count', 0)} | {item.get('total_lower_bound_usd')} / {item.get('total_upper_bound_usd')} |"
            )
    lines.extend(["", "## Counterfactual cumulative economics", "", "Each compiled arm includes the current version build once. Cumulative bounds add sunk development receipts once as a provenance cost; they do not duplicate physical ledger rows.", "", "| family | arm | version lower / upper | cumulative lower / upper | version build receipts | development receipts |", "|---|---|---:|---:|---:|---:|"])
    for family, arms in _dict(report.get("counterfactual_economics")).items():
        for arm, item in _dict(arms).items():
            lines.append(f"| {family} | {arm} | {item.get('version_lower_bound_usd')} / {item.get('version_upper_bound_usd')} | {item.get('cumulative_including_all_attempts_lower_bound_usd')} / {item.get('cumulative_including_all_attempts_upper_bound_usd')} | {item.get('physical_build_receipts_counted_once')} | {item.get('physical_development_build_receipts_counted_once')} |")
    lines.extend(["", "## Limitations", ""])
    for limitation in report.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def analyze(out: Path | str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze verified spec/output artifacts and write ``analysis.json/md``."""
    out = Path(out).resolve()
    if not isinstance(spec, Mapping):
        raise TypeError("spec must be a mapping already verified by the harness")
    families = [str(x) for x in _list(spec.get("families"))]
    version = _version_for_spec(spec)
    imports = _episode_imports(spec)
    origin_out = _origin_out(out, spec)
    receipts, ledger_info = _load_receipts(out, spec)
    phase_costs, purpose_costs = _phase_reports(receipts)
    version_phase_costs, version_purpose_costs = _phase_reports(_version_receipts(receipts, version))
    local_calls = _scan_local_calls(out, origin_out)
    serving_rows = [row for row in _list(spec.get("episodes")) if isinstance(row, Mapping)]
    serving_rows = [row for row in serving_rows if _phase_for_episode(str(row.get("id") or ""), set(families), version) == "serving"]
    serving_rows.extend(_imported_serving_rows(families, version, imports, serving_rows))
    serving_episodes = [_state_entry(out, row, receipts, local_calls, imports, version, origin_out) for row in serving_rows]
    terminal_states = _terminal_statuses(out, families, serving_episodes, receipts, version, origin_out)
    local_counts = _local_call_counts(out, spec, version, origin_out)
    receipt_phase_counts = Counter(str(r.get("phase")) for r in receipts)
    binding_counts = {
        family: len({str(row.get("binding_id")) for row in serving_rows if row.get("family") == family})
        for family in families
    }
    development_build_costs, version_build_costs = _build_cost_reports(out, families, receipts, version, origin_out)
    provenance = _dict(spec.get("provenance"))
    imported_rows = [
        {"id": row_id, **mapping}
        for row_id, mapping in sorted(_preserved_imports(spec).items())
    ]
    active_imported_rows = [
        {"id": row_id, **mapping}
        for row_id, mapping in sorted(_preserved_imports(spec).items())
        if mapping["source_episode_id"] not in {
            item["source_episode_id"] for item in _versioned_row_mappings(spec)
        }
    ]
    versioned_rows = _versioned_row_mappings(spec)
    report: dict[str, Any] = {
        "record_type": "selective-analysis",
        "schema": SCHEMA,
        "namespace": NAMESPACE,
        "version": version,
        "model": spec.get("model"),
        "prototype_scope": "guarded-plan feasibility only",
        "selector_algorithm_implemented": False,
        "spec_verified_by_harness": True,
        "source_freeze": {
            "analyzer_version": MODULE_VERSION,
            "analyzer_sha256": _module_sha256(),
            "spec_sha256": spec.get("spec_sha256"),
            "source_manifest_sha256": spec.get("source_manifest_sha256"),
            "budget_manifest_sha256": spec.get("budget_manifest_sha256"),
            "revision_source_sha256": spec.get("revision_source_sha256"),
        },
        "provenance": {
            key: provenance[key]
            for key in (
                "origin_root",
                "origin_out",
                "origin_spec_sha256",
                "origin_source_manifest_sha256",
                "origin_training_manifest_sha256",
                "source_hashes",
            )
            if key in provenance
        },
        "imported_rows": imported_rows,
        "active_imported_rows": active_imported_rows,
        "versioned_rows": versioned_rows,
        "families": families,
        "arms": [str(x) for x in _list(spec.get("arms"))] or list(ARMS),
        "bindings_per_family": binding_counts,
        "planned_serving_episodes": len(serving_rows),
        "receipts": receipts,
        "ledger": ledger_info,
        "phase_receipt_counts": dict(sorted(receipt_phase_counts.items())),
        "phase_costs": phase_costs,
        "version_phase_costs": version_phase_costs,
        "purpose_costs": purpose_costs,
        "version_purpose_costs": version_purpose_costs,
        "development_build_costs": development_build_costs,
        "version_build_costs": version_build_costs,
        "local_model_call_records": local_counts,
        "serving_episodes": serving_episodes,
        "terminal_states": terminal_states,
        "serving_comparisons": _serving_comparisons(serving_episodes, families),
        "counterfactual_economics": _counterfactual_economics(receipts, serving_episodes, families, version),
        "accounting": {
            "ledger_mode": "ro",
            "physical_calls_included": "all selective namespace receipt rows, including settled, unknown-bill, and incomplete rows",
            "unknown_bill_policy": "fee-table unknown-bill count excludes in-flight reservations; actual lower bound is separate from actual_cost_usd=null and every unresolved reservation is included only in the upper bound",
            "version_scope": "nested compiler version receipts plus explicitly imported source/receipt episodes",
            "shared_training_scope": "all current selective training receipts, including failed and successful attempts",
            "development_build_scope": "base selective build receipts are reported as sunk development and included only in cumulative-all-attempts bounds",
            "version_build_scope": "nested version build receipts are the build component of version-only bounds",
            "physical_receipt_deduplication": "each ledger call row is loaded once; imported references reuse the same receipt row",
            "shared_training_prefix_per_arm": True,
            "shared_build_physical_receipts_counted_once": True,
            "failed_complete_serving_episodes_included": True,
            "unknown_costs_are_not_zero": True,
        },
        "limitations": [
            "Only two serving bindings per family are planned. This is a feasibility pilot and cannot establish generalization or non-inferiority.",
            "No cost selector is implemented. Purpose is reported only when one local record unambiguously matches the receipt sequence. Otherwise it is placed in unattributed.",
            "Imported rows use only explicit source_episode_id and receipt_episode_id mappings from the verified spec. Previous-revision pilot receipts are excluded from selective phase and cumulative costs; they remain in the whole shared-ledger total.",
            "Fee counts are disjoint: in-flight, unknown-bill, and incomplete-billing counts are reported separately. Terminal interrupted or running artifacts are reported separately from billing state.",
            "Unknown bills and incomplete receipts are retained as bounds. They are not converted to zero and are not treated as confidence intervals.",
            "Serving comparisons require a complete boolean state for all three arms of the same family/binding. Incomplete bindings remain listed in serving_episodes but are excluded from matched rates.",
            "Local rejoin usefulness requires a recorded local action whose after guard passes, a following deterministic program suffix action, and final oracle success. Missing trace evidence remains unknown.",
        ],
    }
    (out / "analysis.json").write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "analysis.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=None)
    args = parser.parse_args(argv)
    spec_path = args.spec or (args.out / "spec.json")
    spec = _read_json(spec_path)
    if spec is None:
        raise SystemExit(f"cannot read spec: {spec_path}")
    report = analyze(args.out, spec)
    print(f"Wrote {args.out / 'analysis.json'} and {args.out / 'analysis.md'} ({len(report['receipts'])} receipts).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
