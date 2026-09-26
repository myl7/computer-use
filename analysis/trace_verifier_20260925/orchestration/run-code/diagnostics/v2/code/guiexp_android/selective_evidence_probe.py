"""Build-only probe for verifier-driven, evidence-on-demand GUI prefixes.

The probe compares three builder views of the same recorded training input:
``full_demo`` supplies the preserved raw trace, ``static_projection`` supplies
the skeleton plus a fixed union of target/effect packets, and ``on_demand``
starts from the skeleton and lets the model request bounded packets.  Every
candidate is checked against the preserved raw trace with the existing guard
validator.  No function in this module executes Android actions or reads an
evaluator answer.

Preparation is local and freezes the input hashes, source/runtime manifest,
provider lock, condition order, output contract, and the one diagnostic
receipt prefix.  ``--run`` performs only bounded builder calls.  It does not
serve a plan.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shlex
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import selective_diagnostic_budget as diagnostic_budget
from . import selective_evidence_projection as evidence_projection
from . import selective_evidence_store as evidence_store_module
from . import selective_explore_budget as explore_budget
from . import selective_guard_evidence as guard_evidence
from . import selective_literal_control as literal_control
from . import selective_pilot as pilot
from . import selective_plan_contract as plan_contract
from . import selective_prefix as prefix_contract
from . import selective_projected as projected_module
from . import selective_trace_compatibility as trace_compatibility
from .budget_client import BudgetStop
from .selective_evidence_store import EvidenceStore, EvidenceStoreError, build_store

VERSION = "evidence_probe_v2"
LEGACY_VERSION = "evidence_probe_v1"
VERSION_SCHEMA = "selective-evidence-probe/1"
NAMESPACE = pilot.NAMESPACE
MODEL = explore_budget.MODEL
PROVIDER_PROFILE = "z_ai_fp8"
PROVIDER = "z-ai/fp8"
BUILDER_PROFILE_NAME = "builder_16384"
BUILDER_MAX_TOKENS = 16384
MAX_LOGICAL_CALLS = 4
MAX_QUERY_ROUNDS = 3
CONDITIONS = ("full_demo", "static_projection", "on_demand")
QUERIES = ("target", "effect", "state_pre", "state_post")
QUERY_SCHEMA = "selective-evidence-query/1"
QUERY_BATCH_SCHEMA = "selective-evidence-query-batch/1"
MAX_QUERIES_PER_CALL = 8
TRAINING_INPUT_SCHEMA = "selective-evidence-training/1"
COMPATIBILITY_INPUT_SCHEMA = "selective-evidence-compatibility/1"
COMPATIBILITY_INPUTS_NAME = "compatibility_inputs.json"
COMPATIBILITY_BINDING_CALL = "compatibility_binding"
PREFIX_SCHEMA = prefix_contract.PREFIX_SCHEMA
HANDOFF_POLICY = prefix_contract.HANDOFF_POLICY
DEFAULT_OUT = diagnostic_budget.SELECTIVE_OUT / VERSION
VERSION_OUT = DEFAULT_OUT
DEFAULT_ORIGIN = pilot.DEFAULT_OUT
# v1 and v2 intentionally share one diagnostic accounting prefix.  Row IDs
# add their version segment below this prefix so v2 cannot collide with v1
# receipts while both remain under the same USD 1 cap.
DIAGNOSTIC_PREFIX = f"{NAMESPACE}/{LEGACY_VERSION}"
DIAGNOSTIC_CONFIG_NAME = "diagnostic_budget.json"
TRAINING_INPUTS_NAME = "training_inputs.json"
SPEC_NAME = "spec.json"
ANALYSIS_NAME = "analysis.json"
BUILD_MANIFEST_NAME = "build_manifest.json"
RUN_IDENTITY_NAME = "run_identity.json"

# Concise aliases used by small harnesses and review scripts.
MAX_CALLS = MAX_LOGICAL_CALLS
QUERY_NAMES = QUERIES
MAX_QUERY_BATCH = MAX_QUERIES_PER_CALL

VALID_SINGLE_QUERY_EXAMPLE = {
    "schema": QUERY_SCHEMA,
    "query": "target",
    "source_step": 1,
}
VALID_BATCH_QUERY_EXAMPLE = {
    "schema": QUERY_BATCH_SCHEMA,
    "queries": [
        {"query": "target", "source_step": 1},
        {"query": "effect", "source_step": 2},
        {"query": "state_post", "source_step": 2},
    ],
}
VALID_PREFIX_WRAPPER_EXAMPLE = {
    "schema": PREFIX_SCHEMA,
    "plan": plan_contract.FICTIONAL_EXAMPLE,
    "source_steps": {"open_settings": 1, "tap_label": 2},
    "terminal_source_step": 2,
    "handoff_policy": HANDOFF_POLICY,
}
PARAMETER_REUSE_POLICY = {
    "all_user_specific_values_are_slots": True,
    "slot_description_is_semantic_role": True,
    "training_constants_are_not_parameters": True,
    "repair_preserves_declared_slots": True,
    "goal_only_binding_on_changed_values": True,
}
VERIFIER_SEMANTICS = {
    "after": "nonempty; every selector is true in post; at least one selector is absent in pre as an effect witness",
    "before": "every declared before selector is true in pre",
    "target": "click, long_press, and input_text targets uniquely match the recorded pre-state target",
    "action": "native action type and literal or resolved arguments match the aligned recorded action",
    "targetless_non_open": "a targetless non-open action requires a before witness",
    "alignment": "each plan step maps to one positive, increasing, contiguous source step",
}

TERMINAL_STATUSES = frozenset(
    {
        "built",
        "failed_build",
        "budget_stopped",
        "empty_response",
        "infrastructure_failure",
        "prior_receipt",
        "ambiguous",
    }
)
_SOURCE_FILES = {
    "probe": Path(__file__).resolve(),
    "projected": Path(projected_module.__file__).resolve(),
    "prefix": Path(prefix_contract.__file__).resolve(),
    "guard": Path(guard_evidence.__file__).resolve(),
    "evidence_projection": Path(evidence_projection.__file__).resolve(),
    "evidence_store": Path(evidence_store_module.__file__).resolve(),
    "plan_contract": Path(plan_contract.__file__).resolve(),
    "diagnostic_budget": Path(diagnostic_budget.__file__).resolve(),
    "explore_budget": Path(explore_budget.__file__).resolve(),
    "literal_control": Path(literal_control.__file__).resolve(),
    "trace_compatibility": Path(trace_compatibility.__file__).resolve(),
}


def _source_paths() -> dict[str, Path]:
    """Return the explicit helpers plus every local top-level module.

    The dynamic top-level closure covers indirect runtime/parser/action and
    budget imports without relying on a hand-maintained transitive list.  A
    newly added module also changes the frozen key set and therefore fails
    closed after preparation.
    """

    paths = dict(_SOURCE_FILES)
    module_root = Path(__file__).resolve().parent
    for path in sorted(module_root.glob("*.py")):
        paths[f"local_module:{path.name}"] = path.resolve()
    return paths

COMPATIBILITY_TRACES = (
    {
        "family": "MarkorCreateNote",
        "version": "projected_v11_create_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v11_create_dense/episodes/selective_20260915/"
            "projected_v11_create_dense/MarkorCreateNote/b01/full_fallback/trajectory.jsonl"
        ),
    },
    {
        "family": "FilesMoveFile",
        "version": "projected_v12_files_dense",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v12_files_dense/episodes/selective_20260915/"
            "projected_v12_files_dense/FilesMoveFile/b02/reactive/trajectory.jsonl"
        ),
    },
    {
        "family": "MarkorDeleteNote",
        "version": "projected_v6_schema",
        "relative_path": (
            "experimental-results/guiexp_android/selective_20260915/versions/"
            "projected_v6_schema/episodes/selective_20260915/"
            "projected_v6_schema/MarkorDeleteNote/b01/reactive/trajectory.jsonl"
        ),
    },
)


class ProbeStop(pilot.PilotStop):
    """A local protocol stop that must leave the current row terminal."""


class EmptyResponse(ProbeStop):
    """The provider returned a response with no text to parse."""


class InfrastructureFailure(ProbeStop):
    """A model client or local artifact failed before a candidate was built."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise ProbeStop(f"required artifact cannot be hashed: {path}") from exc


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeStop(f"artifact cannot be read: {path}") from exc


def _write(path: Path, value: Any, *, private: bool = False) -> None:
    pilot.atomic_json(path, value, private=private)


def _safe_error(exc: BaseException, limit: int = 600) -> str:
    value = str(exc).splitlines()[0] if str(exc).splitlines() else type(exc).__name__
    return " ".join(value.split())[:limit]


def _safe_version(value: str | None) -> str:
    value = VERSION if value is None else str(value)
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise ProbeStop("probe version must be a safe identifier")
    if any(not (char.isalnum() or char in "_.-") for char in value):
        raise ProbeStop("probe version must be a safe identifier")
    return value


def _safe_key(value: Any) -> str:
    text = str(value)
    if not text or any(not (char.isalnum() or char in "_.-") for char in text):
        raise ProbeStop("row key contains an unsafe path segment")
    return text


def diagnostic_prefix(version: str = VERSION) -> str:
    """Return the one receipt prefix shared by every condition and build."""

    # The prefix is intentionally tied to the probe family, not to a row or
    # condition.  A versioned prefix prevents two independently frozen specs
    # from silently sharing diagnostic accounting.
    _safe_version(version)
    return DIAGNOSTIC_PREFIX


def _expected_diagnostic_prefix(version: str) -> str:
    return diagnostic_prefix(version)


def exact_command(out: Path | str = DEFAULT_OUT) -> str:
    """Return the canonical command to run from the ``computer-use`` cwd."""

    return (
        f"../.venv-android/bin/python -m guiexp_android.selective_evidence_probe "
        f"--run --out {shlex.quote(str(Path(out).resolve()))}"
    )


def _source_manifest() -> dict[str, Any]:
    hashes: dict[str, str] = {}
    for name, path in _source_paths().items():
        if not path.is_file():
            raise ProbeStop(f"required source module is missing: {name}")
        hashes[name] = _hash_file(path)
    # The budget facade's runtime manifest pins the interpreter and package
    # versions without making a network call.
    runtime = explore_budget.runtime_manifest()
    return {
        "source_sha256": hashes,
        "runtime": runtime,
        "working_directory": "computer-use",
        "interpreter": "../.venv-android/bin/python",
    }


def _verify_source_manifest(value: Mapping[str, Any]) -> None:
    current = _source_manifest()
    if dict(value) != current:
        raise ProbeStop("probe source or runtime manifest changed after preparation")


def _provider_lock(provider_profile: str = PROVIDER_PROFILE) -> dict[str, Any]:
    if provider_profile != PROVIDER_PROFILE:
        raise ProbeStop("probe provider is frozen to z_ai_fp8")
    try:
        locks = explore_budget._lock_set(
            None,
            provider_profile=PROVIDER_PROFILE,
            request_profile=BUILDER_PROFILE_NAME,
        )
    except AttributeError as exc:
        raise ProbeStop("exploratory budget lock API is unavailable") from exc
    if set(locks) != {MODEL} or locks[MODEL].get("provider") != PROVIDER:
        raise ProbeStop("probe model/provider lock is not GLM z_ai_fp8")
    return copy.deepcopy(locks[MODEL])


def _profile_document(lock: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": BUILDER_PROFILE_NAME,
        "model": MODEL,
        "provider": PROVIDER,
        "reasoning": {"effort": "low"},
        "max_tokens": BUILDER_MAX_TOKENS,
        "temperature": 0.0,
        "max_physical_attempts": int(lock.get("max_physical_attempts", 1)),
    }


def _origin_spec(origin: Path) -> dict[str, Any]:
    value = _read(origin / SPEC_NAME)
    if not isinstance(value, Mapping):
        raise ProbeStop("origin spec is not an object")
    digest = value.get("spec_sha256")
    body = {key: item for key, item in value.items() if key != "spec_sha256"}
    if not isinstance(digest, str) or _hash_value(body) != digest:
        raise ProbeStop("origin spec hash is invalid")
    return dict(value)


def _training_snapshot(origin: Path) -> dict[str, Any]:
    # selective_projected owns the frozen trace loader and its path/hash
    # checks.  Keeping this call here means preparation cannot silently use a
    # different training source format.
    from . import selective_projected as projected

    return projected._training_snapshot(origin)


def _load_demos(origin: Path, origin_spec: Mapping[str, Any], families: Sequence[str]) -> list[dict[str, Any]]:
    from . import selective_projected as projected

    selected: list[dict[str, Any]] = []
    training = _training_snapshot(origin)
    for family in families:
        demos = projected._build_demos(origin, origin_spec, family)
        by_hash = {
            str(item.get("trajectory_sha256_frozen")):
                item
            for item in (training.get("families") or {}).get(family, [])
            if isinstance(item, Mapping) and item.get("success") is True
        }
        for position, demo in enumerate(demos):
            if not isinstance(demo, Mapping):
                continue
            item = dict(demo)
            trace_hash = str(item.get("source_trajectory_sha256") or "")
            metadata = by_hash.get(trace_hash)
            if metadata is None:
                raise ProbeStop(f"successful training trace metadata is missing for {family}")
            source_path = Path(str(metadata.get("trajectory_absolute") or "")).resolve()
            if not source_path.is_file() or _hash_file(source_path) != trace_hash:
                raise ProbeStop(f"successful training trace changed for {family}")
            item["_source_path"] = str(source_path)
            item["_source_sha256"] = trace_hash
            item["_source_position"] = position
            # Keep only evidence needed to build and verify.  In particular,
            # never copy training manifest success/oracle fields into a prompt.
            item.pop("source", None)
            selected.append(item)
    return selected


def _select_demos(demos: Sequence[Mapping[str, Any]], max_demos: int) -> list[dict[str, Any]]:
    if type(max_demos) is not int or max_demos < 1:
        raise ProbeStop("max_demos must be a positive integer")
    # Current selective training has one successful trace per family.  The
    # round-robin selection keeps the exploratory pilot at three demos while
    # remaining balanced if a future source has several successes per family.
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for demo in demos:
        family = str(demo.get("family") or "")
        if family:
            grouped[family].append(demo)
    result: list[dict[str, Any]] = []
    families = [family for family in pilot.FAMILIES if family in grouped]
    families.extend(family for family in sorted(grouped) if family not in families)
    cursor = 0
    while len(result) < max_demos and families:
        added = False
        for family in families:
            rows = grouped[family]
            if cursor < len(rows):
                result.append(copy.deepcopy(dict(rows[cursor])))
                added = True
                if len(result) >= max_demos:
                    break
        if not added:
            break
        cursor += 1
    if not result:
        raise ProbeStop("no successful recorded training input is available")
    return result


def _demo_public(demo: Mapping[str, Any]) -> dict[str, Any]:
    """Detach a demo while excluding preparation-only filesystem metadata."""

    steps: list[dict[str, Any]] = []
    for item in list(demo.get("steps") or []):
        if not isinstance(item, Mapping):
            continue
        # These are the complete observations needed by the guard.  Dropping
        # ancillary trajectory fields keeps evaluator/private metadata out of
        # every model view while preserving the raw pre/post accessibility
        # payloads and the actual recorded action.
        steps.append(
            {
                "step": item.get("step"),
                "action": copy.deepcopy(item.get("action")),
                "pre_obs": copy.deepcopy(item.get("pre_obs")),
                "post_obs": copy.deepcopy(item.get("post_obs")),
            }
        )
    return {
        "family": demo.get("family"),
        "goal_text": demo.get("goal_text", ""),
        "steps": steps,
        "source": "recorded_training",
    }


def _training_artifact(demos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, demo in enumerate(demos):
        public = _demo_public(demo)
        source_path = str(demo.get("_source_path") or "")
        source_hash = str(demo.get("_source_sha256") or "")
        if not source_path or not source_hash:
            raise ProbeStop("training input is missing its frozen source hash")
        rows.append(
            {
                "demo_index": index,
                "family": str(public.get("family") or ""),
                "goal_text": public.get("goal_text", ""),
                "steps": public["steps"],
                "source_path": source_path,
                "source_sha256": source_hash,
                "input_sha256": _hash_value(public),
            }
        )
    return {
        "schema": TRAINING_INPUT_SCHEMA,
        "count": len(rows),
        "selection": "round_robin_successful_recorded_training",
        "rows": rows,
    }


def _load_training_artifact(out: Path, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = out / str(spec.get("training_inputs") or TRAINING_INPUTS_NAME)
    if not path.is_file() or _hash_file(path) != spec.get("training_inputs_sha256"):
        raise ProbeStop("preserved training input artifact is missing or changed")
    value = _read(path)
    if not isinstance(value, Mapping) or value.get("schema") != TRAINING_INPUT_SCHEMA:
        raise ProbeStop("preserved training input artifact has an unexpected schema")
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ProbeStop("preserved training input artifact is empty")
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ProbeStop("preserved training input row is invalid")
        public = {
            "family": row.get("family"),
            "goal_text": row.get("goal_text", ""),
            "steps": copy.deepcopy(row.get("steps") or []),
            "source": "recorded_training",
        }
        if row.get("demo_index") != index or row.get("input_sha256") != _hash_value(public):
            raise ProbeStop("preserved training input hash changed")
        source_path = Path(str(row.get("source_path") or "")).resolve()
        source_hash = str(row.get("source_sha256") or "")
        if not source_path.is_file() or _hash_file(source_path) != source_hash:
            raise ProbeStop("source training trace drifted after preparation")
        item = dict(public)
        item["_source_path"] = str(source_path)
        item["_source_sha256"] = source_hash
        item["_source_position"] = index
        result.append(item)
    return result


def _compatibility_path(item: Mapping[str, Any]) -> Path:
    root = Path(literal_control.REPO_ROOT).resolve()
    path = (root / str(item.get("relative_path") or "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ProbeStop("fixed compatibility trace escapes the workspace") from exc
    return path


def _load_compatibility_inputs(training_demos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Freeze development traces and a training-only literal baseline."""

    training_by_family = {
        str(demo.get("family")): demo for demo in training_demos if isinstance(demo, Mapping)
    }
    rows: list[dict[str, Any]] = []
    for item in COMPATIBILITY_TRACES:
        base = {
            "family": item["family"],
            "version": item["version"],
            "relative_path": item["relative_path"],
            "status": "unknown",
            "unknown_is_not_task_failure": True,
        }
        path = _compatibility_path(item)
        try:
            demo, metadata = literal_control.load_public_demo(path, item["family"])
            training_demo = training_by_family.get(item["family"])
            if not isinstance(training_demo, Mapping):
                base["reason"] = "corresponding selected training input is unavailable"
                rows.append(base)
                continue
            # The literal baseline is constructed from the selected training
            # input only.  The fixed development trace is used solely as the
            # later compatibility target, so it cannot leak into baseline
            # construction.
            literal = literal_control.build_literal_prefix(_demo_public(training_demo))
            candidate = literal.get("candidate") if isinstance(literal, Mapping) else None
            literal_compatibility = None
            if isinstance(candidate, Mapping) and isinstance(candidate.get("plan"), Mapping):
                literal_compatibility = trace_compatibility.check_prefix(
                    candidate["plan"], {}, demo.get("steps") or []
                )
            base.update(
                {
                    "status": "available",
                    "source_path": str(path),
                    "source_sha256": metadata.get("sha256"),
                    "validation_source_sha256": metadata.get("sha256"),
                    "training_source_sha256": training_demo.get("_source_sha256"),
                    "training_input_sha256": _hash_value(_demo_public(training_demo)),
                    "validation_input_sha256": _hash_value(_demo_public(demo)),
                    "training_demo_index": training_demo.get("_source_position"),
                    "goal_text_sha256": metadata.get("goal_text_sha256"),
                    "action_count": metadata.get("action_count"),
                    "public_fields_used": metadata.get("public_fields_used"),
                    "oracle_fields_used": metadata.get("oracle_fields_used"),
                    "demo": _demo_public(demo),
                    "literal_control": {
                        "bindings": {},
                        "training_source_sha256": training_demo.get("_source_sha256"),
                        "status": literal.get("status"),
                        "valid": literal.get("valid"),
                        "prefix_length": literal.get("prefix_length"),
                        "stop": literal.get("stop"),
                        "compatibility": literal_compatibility,
                    },
                }
            )
        except (literal_control.ControlStop, trace_compatibility.TraceCompatibilityError, TypeError, ValueError) as exc:
            base.update(
                {
                    "reason": f"fixed compatibility input unavailable: {type(exc).__name__}",
                    "source_path": str(path),
                }
            )
        rows.append(base)
    return {
        "schema": COMPATIBILITY_INPUT_SCHEMA,
        "selection": "fixed inspected development traces checked against training-only literal baselines",
        "heldout_data_in_builder_or_repair": False,
        "rows": rows,
    }


def _load_compatibility_artifact(out: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    path = out / str(spec.get("compatibility_inputs") or COMPATIBILITY_INPUTS_NAME)
    if not path.is_file() or _hash_file(path) != spec.get("compatibility_inputs_sha256"):
        raise ProbeStop("frozen compatibility input artifact is missing or changed")
    value = _read(path)
    if not isinstance(value, Mapping) or value.get("schema") != COMPATIBILITY_INPUT_SCHEMA:
        raise ProbeStop("frozen compatibility input artifact has an unexpected schema")
    expected = {(item["family"], item["version"], item["relative_path"]) for item in COMPATIBILITY_TRACES}
    result: dict[str, dict[str, Any]] = {}
    for item in value.get("rows") or []:
        if not isinstance(item, Mapping):
            continue
        marker = (item.get("family"), item.get("version"), item.get("relative_path"))
        if marker not in expected:
            raise ProbeStop("frozen compatibility input is outside the fixed trace set")
        copied = dict(item)
        if copied.get("status") == "available":
            source_path = Path(str(copied.get("source_path") or "")).resolve()
            if not source_path.is_file() or _hash_file(source_path) != copied.get("source_sha256"):
                copied["status"] = "unknown"
                copied["reason"] = "development compatibility trace drifted after preparation"
                copied.pop("demo", None)
            elif copied.get("validation_input_sha256") != _hash_value(copied.get("demo")):
                copied["status"] = "unknown"
                copied["reason"] = "development compatibility input changed after preparation"
                copied.pop("demo", None)
        result[str(copied["family"])] = copied
    return result


def _query_request(value: Any) -> dict[str, Any] | None:
    """Validate one strict local evidence query, or return ``None``."""

    if not isinstance(value, Mapping):
        return None
    report = validate_query_request(value)
    if report.get("valid") is not True or len(report.get("requests") or []) != 1:
        return None
    return dict(report["requests"][0], schema=QUERY_SCHEMA)


def validate_query_request(value: Any, *, max_source_step: int | None = None) -> dict[str, Any]:
    """Return a fail-closed report for a model evidence query request.

    Only a query name and a positive one-based ``source_step`` are accepted.
    There is no path, URL, network, command, or free-form selector channel.
    """

    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["query request must be an object"]}
    if set(value) == {"schema", "query", "source_step"}:
        if value.get("schema") != QUERY_SCHEMA:
            return {"valid": False, "errors": [f"query request schema must be {QUERY_SCHEMA}"]}
        requests_value: Any = [{"query": value.get("query"), "source_step": value.get("source_step")}]
        schema = QUERY_SCHEMA
    elif set(value) == {"schema", "queries"}:
        if value.get("schema") != QUERY_BATCH_SCHEMA:
            return {"valid": False, "errors": [f"query batch schema must be {QUERY_BATCH_SCHEMA}"]}
        requests_value = value.get("queries")
        schema = QUERY_BATCH_SCHEMA
        if not isinstance(requests_value, list) or not requests_value:
            return {"valid": False, "errors": ["query batch queries must be a nonempty array"]}
        if len(requests_value) > MAX_QUERIES_PER_CALL:
            return {"valid": False, "errors": [f"query batch may contain at most {MAX_QUERIES_PER_CALL} requests"]}
    else:
        return {"valid": False, "errors": ["query request keys must be an exact single or batch envelope"]}
    requests: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for index, request in enumerate(requests_value):
        if not isinstance(request, Mapping) or set(request) != {"query", "source_step"}:
            return {"valid": False, "errors": [f"query request {index} keys must be exactly query, source_step"]}
        query = request.get("query")
        source_step = request.get("source_step")
        if not isinstance(query, str) or query not in QUERIES:
            return {"valid": False, "errors": [f"query must be one of {QUERIES}"]}
        if type(source_step) is not int or source_step <= 0:
            return {"valid": False, "errors": ["source_step must be a positive one-based integer"]}
        if max_source_step is not None and source_step > max_source_step:
            return {"valid": False, "errors": ["source_step is outside the recorded training trace"]}
        marker = (source_step, str(query))
        if marker in seen:
            return {"valid": False, "errors": ["query batch contains duplicate source_step/query requests"]}
        seen.add(marker)
        requests.append({"query": str(query), "source_step": source_step})
    result: dict[str, Any] = {"valid": True, "schema": schema, "requests": requests}
    if len(requests) == 1:
        result.update(requests[0])
    return result


def _oracle_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(token in normalized for token in ("oracle", "evaluator", "heldout", "ground_truth", "reward", "success")):
                return True
            if _oracle_key(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_oracle_key(item) for item in value)
    return False


def _candidate_report(value: Any, full_demos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate schema, contiguous source prefix, and full raw guard evidence."""

    if _oracle_key(value):
        return {"valid": False, "errors": ["candidate contains evaluator or oracle fields"]}
    report = prefix_contract.validate_prefix_response(value, full_demos)
    if not isinstance(report, Mapping) or report.get("valid") is not True:
        return dict(report) if isinstance(report, Mapping) else {"valid": False, "errors": ["invalid prefix candidate"]}
    source_steps = report.get("source_steps")
    if not isinstance(source_steps, Mapping):
        return {"valid": False, "errors": ["candidate source_steps are missing"]}
    # Keep the step-ID lookup explicit so a mapping with a different insertion
    # order from the plan cannot change the source alignment.
    ordered = [source_steps[str(step["id"])] for step in report["plan"]["steps"]]
    expected = list(range(1, len(ordered) + 1))
    if ordered != expected:
        return {
            "valid": False,
            "errors": [f"candidate source_steps must be contiguous source 1..k, got {ordered!r}"],
            "guard_evidence": report.get("guard_evidence"),
        }
    alignment = report.get("alignment")
    try:
        guard = guard_evidence.validate_guard_evidence(report["plan"], full_demos, alignment=alignment)
    except (AttributeError, KeyError, IndexError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "valid": False,
            "errors": [f"full-raw guard validator failed: {type(exc).__name__}: {_safe_error(exc)}"],
            "guard_evidence": {"status": "unknown", "steps": [], "errors": [_safe_error(exc)]},
        }
    if guard.get("status") != "valid":
        return {
            "valid": False,
            "errors": ["full-raw guard evidence is " + str(guard.get("status"))]
                       + [str(item) for item in (guard.get("errors") or [])[:8]],
            "guard_evidence": guard,
        }
    detached = copy.deepcopy(dict(report))
    detached["guard_evidence"] = copy.deepcopy(guard)
    detached["scope"] = {
        "kind": "verified_prefix",
        "plan_steps": len(ordered),
        "source_steps": ordered,
        "terminal_source_step": ordered[-1],
    }
    detached["yield"] = {
        "verified_prefix_steps": len(ordered),
        "nontrivial_training_prefix": _nontrivial_training_prefix(report["plan"], len(ordered)),
        "training_validity_only": True,
        "definition": "verified contiguous source prefix; one-step open-only is reference-only",
    }
    return detached


def validate_prefix_candidate(value: Any, full_demos: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Public alias used by offline tests and review tooling."""

    return _candidate_report(value, full_demos)


validate_candidate = validate_prefix_candidate


def _nontrivial_training_prefix(plan: Mapping[str, Any], count: int) -> bool:
    if count != 1:
        return True
    steps = plan.get("steps") or []
    if len(steps) != 1 or not isinstance(steps[0], Mapping):
        return False
    action = steps[0].get("action") or {}
    return action.get("action_type") != "open_app"


_useful_yield = _nontrivial_training_prefix


def _output_contract() -> dict[str, Any]:
    return {
        "schema": PREFIX_SCHEMA,
        "exact_keys": ["schema", "plan", "source_steps", "terminal_source_step", "handoff_policy"],
        "plan": "one selective-plan/1 object satisfying the complete plan contract",
        "source_steps": "every plan step ID -> positive one-based source step, exactly 1..k",
        "terminal_source_step": "equals the final contiguous source step",
        "handoff_policy": HANDOFF_POLICY,
        "verification": "the harness checks the plan against complete preserved raw training traces",
        "oracle_policy": "no evaluator oracle, held-out answer, reward, or task-success field",
        "parameter_policy": "declare every user-specific task value as a named slot with a semantic role description",
        "training_constant_policy": "do not copy concrete training values into the reusable plan when a slot represents that value",
        "repair_policy": "preserve all declared task-value slots and parameters; do not delete them merely to fit one training trace",
        "later_binding_policy": "goal-only extraction resolves the same slots for a changed task request after compilation",
        "rendered_slot_policy": "follow any optional prefix/suffix fields defined by the complete plan contract",
        "verifier_semantics": VERIFIER_SEMANTICS,
    }


def _contract_text() -> str:
    text = json.dumps(
        {
            "prefix_output_contract": _output_contract(),
            "complete_plan_contract": plan_contract.CONTRACT,
            "fictional_valid_example": plan_contract.FICTIONAL_EXAMPLE,
            "valid_prefix_wrapper_example": VALID_PREFIX_WRAPPER_EXAMPLE,
        },
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
    )
    return text + "\n\nVALID PREFIX WRAPPER JSON EXAMPLE:\n" + json.dumps(
        VALID_PREFIX_WRAPPER_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2
    )


def _query_contract_text() -> str:
    text = json.dumps(
        {
            "single_query_schema": QUERY_SCHEMA,
            "single_query_exact_keys": ["schema", "query", "source_step"],
            "batch_query_schema": QUERY_BATCH_SCHEMA,
            "batch_query_exact_keys": ["schema", "queries"],
            "batch_item_exact_keys": ["query", "source_step"],
            "max_batch_requests": MAX_QUERIES_PER_CALL,
            "query": {"type": "string", "enum": list(QUERIES)},
            "source_step": "positive one-based integer within the supplied training trace",
            "access": "local recorded training evidence only, with no path, URL, network, command, or oracle channel",
            "query_semantics": {
                "target": "pre-state target node and descriptor candidates",
                "effect": "post-only visible value witnesses and ambiguity metadata",
                "state_pre": "complete raw pre-state observation when descriptors are insufficient",
                "state_post": "complete raw post-state observation when effect evidence is insufficient",
                "unknown": "request a raw state packet or stop before extending the verified boundary",
            },
            "valid_single_query_example": VALID_SINGLE_QUERY_EXAMPLE,
            "valid_batch_query_example": VALID_BATCH_QUERY_EXAMPLE,
        },
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
    )
    return text + "\n\nVALID SINGLE QUERY JSON EXAMPLE:\n" + json.dumps(
        VALID_SINGLE_QUERY_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2
    ) + "\n\nVALID BATCH QUERY JSON EXAMPLE:\n" + json.dumps(
        VALID_BATCH_QUERY_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2
    )


def _static_view(store: EvidenceStore) -> dict[str, Any]:
    skeleton = store.skeleton
    packets: list[dict[str, Any]] = []
    for row in skeleton.get("source_alignment") or []:
        source_step = row.get("source_step") if isinstance(row, Mapping) else None
        if type(source_step) is not int:
            continue
        packets.append(store.query(source_step, "target"))
        packets.append(store.query(source_step, "effect"))
    return {
        "view": "static_projection",
        "skeleton": skeleton,
        "evidence_packets": packets,
        "evidence_policy": "fixed union of target and effect packets for every recorded training step",
    }


def _on_demand_view(store: EvidenceStore, packets: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    # Deduplicate by source step/query so a model cannot obtain extra context
    # by repeating a cached request.
    unique: dict[tuple[int, str], Mapping[str, Any]] = {}
    for packet in packets:
        if isinstance(packet, Mapping) and type(packet.get("source_step")) is int and isinstance(packet.get("query"), str):
            unique[(packet["source_step"], packet["query"])] = packet
    ordered = [unique[key] for key in sorted(unique)]
    return {
        "view": "on_demand",
        "skeleton": store.skeleton,
        "retrieved_evidence_packets": [copy.deepcopy(dict(packet)) for packet in ordered],
        "evidence_policy": "only explicitly requested local packets; initial response receives the skeleton only",
    }


def _full_view(demo: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "view": "full_demo",
        "demonstrations": [_demo_public(demo)],
        "evidence_policy": "complete preserved raw training trace supplied to the builder",
    }


def build_messages(
    condition: str,
    demo: Mapping[str, Any],
    store: EvidenceStore,
    *,
    packets: Sequence[Mapping[str, Any]] = (),
    candidate_raw: str | None = None,
    feedback: str | None = None,
    force_final: bool = False,
    static_escalated: bool = False,
) -> list[dict[str, str]]:
    """Build one fresh request using the frozen contract and current view."""

    if condition not in CONDITIONS:
        raise ProbeStop(f"unknown probe condition: {condition}")
    if condition == "full_demo":
        view = _full_view(demo)
    elif condition == "static_projection":
        if static_escalated:
            view = _full_view(demo)
            view["view"] = "static_projection_repair_full_raw"
            view["repair_escalation"] = "complete raw training trace supplied after first invalid candidate"
        else:
            view = _static_view(store)
    else:
        view = _on_demand_view(store, packets)
    instruction = (
        "Return exactly one candidate object satisfying the PREFIX OUTPUT CONTRACT. "
        "Compile the longest contiguous evidence-supported prefix source steps 1..k. "
        "A prefix boundary is a verification scope marker and makes no task-success claim. "
        "Represent every user-specific task value as a declared slot whose description states "
        "its semantic role, and reuse that slot in the relevant action and guards. Training "
        "constants are structural evidence, not reusable task parameters. Preserve all declared "
        "task-value slots and parameters during repair. Never delete a parameter merely to fit "
        "one training trace. A later goal-only extractor must be able to bind the same slots "
        "when the task values change. Preserve any optional prefix/suffix fields permitted by "
        "the complete plan contract. "
        "Use only the supplied recorded training evidence. Never emit evaluator, oracle, "
        "held-out answer, reward, or success fields.\n\n"
    )
    if condition == "on_demand" and not force_final:
        instruction += (
            "ON-DEMAND QUERY MECHANISM: You may either return a candidate or one exact single "
            "or batch evidence query object. A query consumes this same logical call budget. The last available "
            "call must return a candidate, so request only evidence needed for a longer verified "
            "prefix.\n\nQUERY CONTRACT:\n" + _query_contract_text() + "\n\n"
        )
    elif condition == "on_demand" and force_final:
        instruction += "FINAL SLOT: return a candidate object now. A query request is invalid in this slot.\n\n"
    common = (
        instruction
        + "PREFIX OUTPUT CONTRACT AND COMPLETE PLAN CONTRACT:\n"
        + _contract_text()
        + "\n\nCURRENT SUPPLIED VIEW:\n"
        + json.dumps(view, ensure_ascii=True, sort_keys=True, indent=2)
    )
    if candidate_raw is not None:
        common += "\n\nPREVIOUS CANDIDATE RAW TEXT:\n" + str(candidate_raw)[:12000]
    if feedback:
        common += "\n\nPRECISE VERIFIER FEEDBACK:\n" + str(feedback)[:2400]
    return [
        {
            "role": "system",
            "content": "You produce data-only Android prefix candidates under an exact JSON contract.",
        },
        {"role": "user", "content": common},
    ]


def precise_feedback(report: Mapping[str, Any] | None, error: str | None = None) -> str:
    """Reduce verifier output to bounded, source-specific repair feedback."""

    parts: list[str] = []
    if error:
        parts.append("protocol: " + " ".join(str(error).split())[:600])
    if isinstance(report, Mapping):
        for item in (report.get("errors") or [])[:8]:
            parts.append("candidate: " + " ".join(str(item).split())[:500])
        guard = report.get("guard_evidence")
        if isinstance(guard, Mapping):
            for step in (guard.get("steps") or [])[:8]:
                if not isinstance(step, Mapping) or step.get("status") == "valid":
                    continue
                source = ",".join(str(value) for value in (step.get("source_indices") or [])) or "none"
                detail = "; ".join(" ".join(str(value).split()) for value in (step.get("errors") or [])[:3])
                parts.append(f"guard step {step.get('step_id', '?')} source {source}: {detail or 'no detail'}")
    return " | ".join(parts)[:2400] or "candidate failed verifier checks; return one corrected candidate"


def repair_messages(
    condition: str,
    demo: Mapping[str, Any],
    store: EvidenceStore,
    raw_response: str,
    report: Mapping[str, Any] | None = None,
    *,
    packets: Sequence[Mapping[str, Any]] = (),
    static_escalated: bool = False,
    force_final: bool = False,
) -> list[dict[str, str]]:
    """Build precise feedback without accumulating prior prompt copies."""

    return build_messages(
        condition,
        demo,
        store,
        packets=packets,
        candidate_raw=raw_response,
        feedback=precise_feedback(report),
        static_escalated=static_escalated,
        force_final=force_final,
    )


def _row_key(row: Mapping[str, Any]) -> str:
    return _safe_key(row.get("key"))


def _row_dir(out: Path, row: Mapping[str, Any]) -> Path:
    return out / "build" / _row_key(row)


def _state_path(out: Path, row: Mapping[str, Any]) -> Path:
    return _row_dir(out, row) / "state.json"


def _build_path(out: Path, row: Mapping[str, Any]) -> Path:
    return _row_dir(out, row) / "build.json"


def _call_path(out: Path, row: Mapping[str, Any], call_number: int) -> Path:
    return _row_dir(out, row) / f"call_{call_number:02d}.json"


def _episode(row: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    value = str(row.get("id") or "")
    prefix = str(spec.get("diagnostic_prefix") or "")
    if not prefix or value != prefix and not value.startswith(prefix + "/"):
        raise ProbeStop("row episode is outside the frozen diagnostic prefix")
    return value


def _ledger_receipts(ledger: Any, episode: str) -> list[str]:
    direct = getattr(ledger, "receipt_ids", None)
    if callable(direct):
        try:
            return [str(value) for value in direct(episode)]
        except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            pass
    connect = getattr(ledger, "connect", None)
    if not callable(connect):
        return []
    try:
        with connect() as database:
            rows = database.execute(
                "SELECT id FROM calls WHERE episode=? ORDER BY created, id", (episode,)
            ).fetchall()
    except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return []
    return [str(row[0]) for row in rows]


def _ledger_has_episode(ledger: Any, episode: str) -> bool:
    method = getattr(ledger, "has_episode", None)
    if callable(method):
        try:
            return bool(method(episode))
        except Exception as exc:
            raise ProbeStop("diagnostic receipt lookup failed") from exc
    return bool(_ledger_receipts(ledger, episode))


def _save_call(
    out: Path,
    row: Mapping[str, Any],
    call_number: int,
    response: Any = None,
    usage: Mapping[str, Any] | None = None,
    *,
    error: str | None = None,
    purpose: str,
    ledger: Any,
    phase: str = "builder",
    episode_id: str | None = None,
    file_label: str | None = None,
) -> dict[str, Any]:
    raw_dump = pilot._response_dump(response) if response is not None else {}
    raw_text = pilot.response_content(response) if response is not None else ""
    effective_episode = str(episode_id or row.get("id") or "")
    receipts = _ledger_receipts(ledger, effective_episode)
    record = {
        "record_type": "evidence-probe-call",
        "call_number": call_number,
        "purpose": purpose,
        "condition": row.get("condition"),
        "row_id": row.get("id"),
        "episode_id": effective_episode,
        "phase": phase,
        "raw_response": raw_dump,
        "raw_text": raw_text,
        "response_sha256": _hash_value(raw_dump),
        "raw_text_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "raw_char_count": len(raw_text),
        "char_count": len(raw_text),
        "usage": dict(usage or {}),
        "generation_id": raw_dump.get("id") if isinstance(raw_dump, Mapping) else None,
        "receipt_ids": receipts,
        "receipt_ids_complete": bool(receipts),
        "error": error,
        # This flag is useful in audits and makes the write-before-parse order
        # an explicit invariant rather than an implementation accident.
        "saved_before_parse_or_validation": True,
    }
    path = _row_dir(out, row) / (file_label or f"call_{call_number:02d}.json")
    _write(path, record)
    return record


def _save_feedback(out: Path, row: Mapping[str, Any], call_number: int, feedback: str, raw_sha256: str) -> None:
    _write(
        _row_dir(out, row) / f"feedback_{call_number:02d}.json",
        {
            "record_type": "evidence-probe-feedback",
            "after_call": call_number,
            "raw_response_sha256": raw_sha256,
            "feedback": feedback,
            "char_count": len(feedback),
        },
    )


def _save_state(out: Path, row: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    _write(_state_path(out, row), dict(value))


def _save_result(out: Path, row: Mapping[str, Any], value: Mapping[str, Any]) -> None:
    _write(_build_path(out, row), dict(value))


def _stopped_result(
    out: Path,
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    status: str,
    exc: BaseException,
) -> dict[str, Any]:
    """Retain a validated candidate checkpoint when a later call stops."""

    result = _row_result_base(spec, row)
    path = _build_path(out, row)
    if path.is_file():
        value = _read(path)
        if isinstance(value, Mapping):
            result.update(value)
    calls = _read_calls(out, row)
    compatibility_calls = [call for call in calls if call.get("phase") == "binding"]
    result.update(
        status=status,
        error_type=type(exc).__name__,
        error=_safe_error(exc),
        calls=[call for call in calls if call.get("phase") != "binding"],
        calls_made=len(calls),
        builder_calls_made=len(calls) - len(compatibility_calls),
        compatibility_calls_made=len(compatibility_calls),
        compatibility_calls=compatibility_calls,
    )
    if result.get("candidate_valid") is True and result.get("compatibility") is None:
        result["compatibility"] = {
            "status": "unknown",
            "reason": "compatibility call stopped before a result",
            "training_validity_only": True,
        }
    return result


def _existing_row(out: Path, row: Mapping[str, Any], ledger: Any, spec: Mapping[str, Any]) -> dict[str, Any] | None:
    state_path = _state_path(out, row)
    build_path = _build_path(out, row)
    row_dir = _row_dir(out, row)
    call_files = sorted(row_dir.glob("call_*.json")) if row_dir.is_dir() else []
    other_evidence = (
        [
            path
            for path in row_dir.iterdir()
            if path.is_file() and path.name not in {"state.json", "build.json"}
        ]
        if row_dir.is_dir()
        else []
    )
    episode = _episode(row, spec)
    if state_path.is_file():
        value = _read(state_path)
        status = str(value.get("status") or "unknown") if isinstance(value, Mapping) else "unknown"
        if status in TERMINAL_STATUSES:
            if build_path.is_file():
                build_value = _read(build_path)
                if isinstance(build_value, Mapping):
                    return dict(build_value)
            return dict(value)
        # A running or malformed state is durable ambiguity.  It is never
        # resumed, even if no response file was flushed before interruption.
        value = {
            "record_type": "evidence-probe-state",
            "status": "ambiguous",
            "row_id": row.get("id"),
            "reason": "prior nonterminal state prevents replay",
            "existing_state": status,
            "call_files": [path.name for path in call_files],
            "evidence_files": [path.name for path in other_evidence],
        }
        _save_state(out, row, value)
        return value
    if call_files or other_evidence or build_path.is_file() or _ledger_has_episode(ledger, episode):
        value = {
            "record_type": "evidence-probe-state",
            "status": "prior_receipt" if not call_files and not other_evidence and not build_path.is_file() else "ambiguous",
            "row_id": row.get("id"),
            "reason": "prior response or receipt prevents replay",
            "call_files": [path.name for path in call_files],
            "evidence_files": [path.name for path in other_evidence],
        }
        _save_state(out, row, value)
        if build_path.is_file():
            existing = _read(build_path)
            if isinstance(existing, Mapping):
                return dict(existing)
        return value
    return None


def _training_for_row(training: Sequence[Mapping[str, Any]], row: Mapping[str, Any]) -> dict[str, Any]:
    index = row.get("demo_index")
    if type(index) is not int or index < 0 or index >= len(training):
        raise ProbeStop("row training input index is invalid")
    demo = dict(training[index])
    if str(demo.get("family")) != str(row.get("family")):
        raise ProbeStop("row family does not match its frozen training input")
    if str(demo.get("_source_sha256")) != str(row.get("source_sha256")):
        raise ProbeStop("row training source hash does not match its preserved input")
    return demo


def _make_client(
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    ledger: Any,
    *,
    env_file: Path | str | None = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "ledger": ledger,
        "profile": BUILDER_PROFILE_NAME,
        "provider_profile": PROVIDER_PROFILE,
        "episode": _episode(row, spec),
        "host_guard": host_guard,
    }
    if env_file is not None:
        kwargs["env_path"] = env_file
    if sdk is not None:
        kwargs["sdk"] = sdk
    if metadata_fetcher is not None:
        kwargs["metadata_fetcher"] = metadata_fetcher
    return explore_budget.make_builder_client(spec["model_locks"], **kwargs)


def _billing_known(usage: Mapping[str, Any] | None) -> bool:
    if not isinstance(usage, Mapping):
        return False
    value = usage.get("cost_usd")
    try:
        number = float(value)
        return value is not None and math.isfinite(number) and number >= 0
    except (TypeError, ValueError, OverflowError):
        return False


def _extract_compatibility_binding(
    plan: Mapping[str, Any],
    goal_text: str,
    client: Any,
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    out: Path,
    ledger: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    """Make at most one goal-only extraction call for a valid candidate."""

    slots = plan.get("slots") if isinstance(plan, Mapping) else None
    if not isinstance(slots, Mapping):
        return None, {"status": "unknown", "reason": "candidate slots are unavailable"}, None
    if not slots:
        return {}, {
            "status": "valid",
            "no_model_call": True,
            "provenance": "deterministic_empty_slots",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost_usd": 0},
        }, None
    compatibility_episode = f"{_episode(row, spec)}/compatibility"
    messages = pilot._extract_prompt(plan, goal_text)
    try:
        response, usage = _call_client(client, messages, compatibility_episode)
    except BudgetStop:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InfrastructureFailure(_safe_error(exc)) from None
    call_record = _save_call(
        out,
        row,
        1,
        response,
        usage,
        purpose=COMPATIBILITY_BINDING_CALL,
        ledger=ledger,
        phase="binding",
        episode_id=compatibility_episode,
        file_label="binding_01.json",
    )
    if not _billing_known(usage):
        raise BudgetStop("compatibility binding response lacks settled billing evidence")
    raw = pilot.response_content(response)
    if not raw.strip():
        raise EmptyResponse("compatibility binding response was empty")
    try:
        value = pilot._json_from_text(raw)
    except (TypeError, ValueError) as exc:
        return None, {"status": "invalid", "reason": _safe_error(exc)}, call_record
    if not isinstance(value, Mapping) or set(value) != {str(slot) for slot in slots}:
        return None, {"status": "invalid", "reason": "extracted binding keys do not match candidate slots"}, call_record
    if any(type(item) not in (str, int, float, bool) for item in value.values()):
        return None, {"status": "invalid", "reason": "extracted binding values must be scalar"}, call_record
    return dict(value), {"status": "valid", "provenance": "goal_only_extraction"}, call_record


def _compatibility_summary(report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(report, Mapping):
        return None
    first = report.get("first_divergence")
    kind = first.get("kind") if isinstance(first, Mapping) else None
    if kind in {"action_argument_mismatch", "target_mismatch"}:
        action = first
        guard = None
    elif kind == "guard_not_applicable":
        action = None
        guard = first
    else:
        action = None
        guard = None
    observed_status = report.get("status", "unknown")
    if kind in {"trace_missing", "parse_unknown"}:
        observed_status = "unknown"
    return {
        "status": observed_status,
        "checker_status": report.get("status", "unknown"),
        "prefix_steps_matched": report.get("prefix_steps_matched"),
        "first_divergence": copy.deepcopy(first),
        "action_divergence": copy.deepcopy(action),
        "guard_divergence": copy.deepcopy(guard),
        "counterfactual_ui_success": None,
        "task_success_evaluated": False,
        "generalization_proof": False,
        "training_validity_only": True,
        "missing_or_alternative_path_is_unknown": True,
    }


def _run_compatibility(
    plan: Mapping[str, Any],
    compatibility: Mapping[str, Any] | None,
    client: Any,
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    out: Path,
    ledger: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Check one fixed public development trace after candidate validation."""

    if not isinstance(compatibility, Mapping) or compatibility.get("status") != "available":
        return {
            "status": "unknown",
            "reason": (compatibility or {}).get("reason", "fixed compatibility trace unavailable"),
            "training_validity_only": True,
            "counterfactual_ui_success": None,
            "generalization_proof": False,
        }, None, None
    if compatibility.get("training_source_sha256") not in {None, str(row.get("source_sha256"))}:
        return {
            "status": "unknown",
            "reason": "literal baseline training source does not match this row",
            "training_validity_only": True,
            "counterfactual_ui_success": None,
            "generalization_proof": False,
        }, None, None
    demo = compatibility.get("demo")
    if not isinstance(demo, Mapping) or not isinstance(demo.get("steps"), list):
        return {
            "status": "unknown",
            "reason": "fixed compatibility trace has no public steps",
            "training_validity_only": True,
            "counterfactual_ui_success": None,
            "generalization_proof": False,
        }, None, None
    binding, binding_report, binding_call = _extract_compatibility_binding(
        plan,
        str(demo.get("goal_text") or ""),
        client,
        row,
        spec,
        out,
        ledger,
    )
    if binding_report.get("status") != "valid" or binding is None:
        return {
            "status": "unknown",
            "reason": binding_report.get("reason", "goal-only binding extraction was unavailable"),
            "binding_extraction": dict(binding_report),
            "training_validity_only": True,
            "counterfactual_ui_success": None,
            "generalization_proof": False,
        }, binding, binding_call
    try:
        report = trace_compatibility.check_prefix(plan, binding, demo["steps"])
    except (TypeError, ValueError, trace_compatibility.TraceCompatibilityError) as exc:
        return {
            "status": "unknown",
            "reason": f"compatibility checker unavailable: {type(exc).__name__}",
            "binding_extraction": dict(binding_report),
            "training_validity_only": True,
            "counterfactual_ui_success": None,
            "generalization_proof": False,
        }, binding, binding_call
    summary = _compatibility_summary(report) or {
        "status": "unknown",
        "training_validity_only": True,
    }
    summary["binding_extraction"] = dict(binding_report)
    summary["trace"] = {
        "family": compatibility.get("family"),
        "version": compatibility.get("version"),
        "validation_source_sha256": compatibility.get("validation_source_sha256") or compatibility.get("source_sha256"),
        "training_source_sha256": compatibility.get("training_source_sha256"),
    }
    summary["literal_control_baseline"] = copy.deepcopy(compatibility.get("literal_control"))
    return summary, binding, binding_call


def _call_client(client: Any, messages: list[dict[str, str]], episode: str) -> tuple[Any, dict[str, Any]]:
    pilot._begin_episode(client, episode)
    return pilot._call_client(client, messages, episode)


def _query_max_step(store: EvidenceStore) -> int:
    values = [row.get("source_step") for row in store.skeleton.get("source_alignment") or [] if isinstance(row, Mapping)]
    return max(values) if values else 0


def _query_packet(store: EvidenceStore, request: Mapping[str, Any]) -> dict[str, Any]:
    if set(request) == {"query", "source_step"}:
        request = dict(request, schema=QUERY_SCHEMA)
    checked = validate_query_request(request, max_source_step=_query_max_step(store))
    if checked.get("valid") is not True:
        raise ProbeStop(precise_feedback(checked))
    try:
        packet = store.query(checked["source_step"], checked["query"])
    except (EvidenceStoreError, IndexError, TypeError, ValueError) as exc:
        raise ProbeStop(f"recorded evidence query failed: {_safe_error(exc)}") from exc
    packet = copy.deepcopy(packet)
    packet["evidence_source"] = "recorded_training_trace"
    packet["accessed_by"] = "validated_local_query"
    return packet


def _query_packets(store: EvidenceStore, request_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Resolve one validated single or batch request with deterministic order."""

    requests = request_report.get("requests") or []
    packets: list[dict[str, Any]] = []
    for request in requests:
        packets.append(_query_packet(store, request))
    return packets


def _row_result_base(spec: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_type": "evidence-probe-build",
        "row_id": row.get("id"),
        "family": row.get("family"),
        "demo_index": row.get("demo_index"),
        "condition": row.get("condition"),
        "condition_order": row.get("condition_order"),
        "provider": spec.get("provider"),
        "provider_profile": spec.get("provider_profile"),
        "model": spec.get("model"),
        "profile": spec.get("builder_profile"),
        "diagnostic_prefix": spec.get("diagnostic_prefix"),
        "max_logical_calls": MAX_LOGICAL_CALLS,
        "status": "pending",
        "calls": [],
        "compatibility_calls": [],
        "query_requests": [],
        "query_packets": [],
        "scope": None,
        "yield": None,
        "candidate_valid": False,
        "compatibility": None,
        "binding": None,
    }


def _accept_candidate(
    result: dict[str, Any],
    candidate_report: Mapping[str, Any],
    *,
    compatibility: Mapping[str, Any] | None,
    client: Any,
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    out: Path,
    row_dir: Path,
    ledger: Any,
) -> None:
    result.update(
        status="built",
        candidate_valid=True,
        scope=candidate_report.get("scope"),
        plan=candidate_report.get("plan"),
        guard_evidence=candidate_report.get("guard_evidence"),
        source_steps=candidate_report.get("source_steps"),
    )
    result["yield"] = candidate_report.get("yield")
    # Persist the validated candidate and its full raw training guard report
    # before spending a separate compatibility/binding call.  A later budget
    # stop therefore cannot erase the builder result.
    _write(row_dir / "candidate.json", candidate_report)
    _save_result(out, row, {**result, "status": "built_pending_compatibility"})
    _save_state(
        out,
        row,
        {
            "record_type": "evidence-probe-state",
            "status": "built_pending_compatibility",
            "row_id": row.get("id"),
            "candidate_checkpoint": "candidate.json",
        },
    )
    compatibility_report, binding, binding_call = _run_compatibility(
        candidate_report["plan"],
        compatibility,
        client,
        row,
        spec,
        out,
        ledger,
    )
    result["compatibility"] = compatibility_report
    result["binding"] = binding
    if binding_call is not None:
        result["compatibility_calls"] = [binding_call]


def _build_row(
    out: Path,
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    demo: Mapping[str, Any],
    *,
    ledger: Any,
    client_factory: Callable[..., Any] | None = None,
    env_file: Path | str | None = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
    compatibility: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = _row_result_base(spec, row)
    row_dir = _row_dir(out, row)
    row_dir.mkdir(parents=True, exist_ok=True)
    _save_state(
        out,
        row,
        {
            "record_type": "evidence-probe-state",
            "status": "running",
            "row_id": row.get("id"),
            "started_unix": time.time(),
        },
    )
    try:
        store = build_store(demo.get("goal_text", ""), demo.get("steps") or [])
    except (EvidenceStoreError, TypeError, ValueError) as exc:
        result.update(status="failed_build", error_type=type(exc).__name__, error=_safe_error(exc))
        _save_result(out, row, result)
        _save_state(out, row, {"record_type": "evidence-probe-state", "status": "failed_build", "row_id": row.get("id"), "reason": result["error"]})
        return result
    condition = str(row["condition"])
    packets: list[dict[str, Any]] = []
    static_escalated = False
    candidate_report: dict[str, Any] | None = None
    latest_candidate_raw = ""
    last_response_raw = ""
    query_rounds = 0
    messages = build_messages(condition, demo, store)
    episode = _episode(row, spec)
    try:
        client = client_factory(spec, row, ledger) if client_factory is not None else _make_client(
            spec,
            row,
            ledger,
            env_file=env_file,
            sdk=sdk,
            metadata_fetcher=metadata_fetcher,
            host_guard=host_guard,
        )
    except BudgetStop:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InfrastructureFailure(_safe_error(exc)) from None
    for call_number in range(1, MAX_LOGICAL_CALLS + 1):
        purpose = "initial_candidate" if call_number == 1 else "revision"
        if condition == "on_demand" and call_number == MAX_LOGICAL_CALLS:
            messages = build_messages(
                condition,
                demo,
                store,
                packets=packets,
                candidate_raw=latest_candidate_raw or None,
                feedback=precise_feedback(candidate_report),
                force_final=True,
            )
            purpose = "final_candidate"
        try:
            response, usage = _call_client(client, messages, episode)
            call_record = _save_call(
                out,
                row,
                call_number,
                response,
                usage,
                purpose=purpose,
                ledger=ledger,
            )
            result["calls"].append(call_record)
            raw = pilot.response_content(response)
            last_response_raw = raw
            if not _billing_known(usage):
                raise BudgetStop("builder response lacks settled billing evidence")
            if not raw.strip():
                raise EmptyResponse("model response was empty")
        except BudgetStop:
            raise
        except EmptyResponse:
            raise
        except Exception as exc:  # noqa: BLE001
            # The call artifact is still written if a response existed.  A
            # client failure before response persistence is retained as a
            # bounded error artifact, then the one-pass run halts.
            if len(result["calls"]) < call_number:
                result["calls"].append(
                    _save_call(
                        out,
                        row,
                        call_number,
                        None,
                        None,
                        error=_safe_error(exc),
                        purpose=purpose,
                        ledger=ledger,
                    )
                )
            raise InfrastructureFailure(_safe_error(exc)) from None
        try:
            value = pilot._json_from_text(last_response_raw)
        except (TypeError, ValueError) as exc:
            candidate_report = {"valid": False, "errors": [_safe_error(exc)]}
            latest_candidate_raw = last_response_raw
            feedback = precise_feedback(candidate_report)
            _save_feedback(out, row, call_number, feedback, result["calls"][-1]["response_sha256"])
            if call_number >= MAX_LOGICAL_CALLS:
                break
            if condition == "static_projection":
                static_escalated = True
            messages = repair_messages(
                condition,
                demo,
                store,
                latest_candidate_raw,
                candidate_report,
                packets=packets,
                static_escalated=static_escalated,
            )
            continue
        if condition == "on_demand":
            query_report = validate_query_request(value, max_source_step=_query_max_step(store))
            if query_report.get("valid") is True:
                result["calls"][-1]["phase"] = "query"
                result["calls"][-1]["purpose"] = "query_request"
                _write(_call_path(out, row, call_number), result["calls"][-1])
                if call_number >= MAX_LOGICAL_CALLS:
                    candidate_report = {"valid": False, "errors": ["final logical call must return a candidate, not a query"]}
                    _save_feedback(out, row, call_number, precise_feedback(candidate_report), result["calls"][-1]["response_sha256"])
                    break
                query_rounds += 1
                try:
                    resolved_packets = _query_packets(store, query_report)
                except ProbeStop as exc:
                    raise InfrastructureFailure(_safe_error(exc)) from None
                result["query_requests"].extend(
                    [dict(request) for request in query_report.get("requests") or []]
                )
                result["query_packets"].extend(resolved_packets)
                packets.extend(resolved_packets)
                _write(
                    row_dir / f"query_{call_number:02d}.json",
                    {
                        "record_type": "evidence-probe-query-result",
                        "after_call": call_number,
                        "requests": [dict(request) for request in query_report.get("requests") or []],
                        "packets": resolved_packets,
                        "evidence_source": "recorded_training_trace",
                    },
                )
                packet_label = ", ".join(
                    f"source {packet['source_step']} ({packet['query']})" for packet in resolved_packets
                )
                messages = build_messages(
                    condition,
                    demo,
                    store,
                    packets=packets,
                    candidate_raw=latest_candidate_raw or None,
                    feedback=(
                        f"Retrieved query packet(s) {packet_label}. Return a candidate or one next query batch."
                        + (" Previous candidate feedback: " + precise_feedback(candidate_report) if candidate_report else "")
                    ),
                )
                continue
            if isinstance(value, Mapping) and value.get("schema") in {QUERY_SCHEMA, QUERY_BATCH_SCHEMA}:
                candidate_report = query_report
            else:
                candidate_report = _candidate_report(value, [_demo_public(demo)])
            latest_candidate_raw = last_response_raw
            if candidate_report.get("valid") is True:
                _accept_candidate(
                    result,
                    candidate_report,
                    compatibility=compatibility,
                    client=client,
                    row=row,
                    spec=spec,
                    out=out,
                    row_dir=row_dir,
                    ledger=ledger,
                )
                break
        else:
            candidate_report = _candidate_report(value, [_demo_public(demo)])
            latest_candidate_raw = last_response_raw
            if candidate_report.get("valid") is True:
                _accept_candidate(
                    result,
                    candidate_report,
                    compatibility=compatibility,
                    client=client,
                    row=row,
                    spec=spec,
                    out=out,
                    row_dir=row_dir,
                    ledger=ledger,
                )
                break
        feedback = precise_feedback(candidate_report)
        _save_feedback(out, row, call_number, feedback, result["calls"][-1]["response_sha256"])
        if call_number >= MAX_LOGICAL_CALLS:
            break
        if condition == "static_projection":
            static_escalated = True
        messages = repair_messages(
            condition,
            demo,
            store,
            latest_candidate_raw,
            candidate_report,
            packets=packets,
            static_escalated=static_escalated,
            force_final=condition == "on_demand" and call_number + 1 >= MAX_LOGICAL_CALLS,
        )
    if result["status"] == "pending":
        result.update(
            status="failed_build",
            error_type="PlanInvalid",
            error=precise_feedback(candidate_report),
        )
    result["builder_calls_made"] = len(result["calls"])
    result["compatibility_calls_made"] = len(result.get("compatibility_calls") or [])
    result["calls_made"] = result["builder_calls_made"] + result["compatibility_calls_made"]
    result["query_rounds"] = query_rounds
    _save_result(out, row, result)
    _save_state(
        out,
        row,
        {
            "record_type": "evidence-probe-state",
            "status": result["status"],
            "row_id": row.get("id"),
            "calls_made": result["calls_made"],
            "ended_unix": time.time(),
        },
    )
    return result


def _load_spec(out: Path, *, expected_version: str | None = None) -> dict[str, Any]:
    value = _read(out / SPEC_NAME)
    if not isinstance(value, Mapping):
        raise ProbeStop("probe spec is not an object")
    digest = value.get("spec_sha256")
    body = {key: item for key, item in value.items() if key != "spec_sha256"}
    if not isinstance(digest, str) or _hash_value(body) != digest:
        raise ProbeStop("probe spec hash is invalid")
    spec = dict(value)
    if spec.get("schema") != VERSION_SCHEMA:
        raise ProbeStop("unexpected probe spec schema")
    if expected_version is not None and spec.get("version") != expected_version:
        raise ProbeStop("probe version does not match the requested version")
    if spec.get("model") != MODEL or spec.get("provider_profile") != PROVIDER_PROFILE or spec.get("provider") != PROVIDER:
        raise ProbeStop("probe model/provider lock changed")
    if spec.get("conditions") != list(CONDITIONS):
        raise ProbeStop("probe conditions changed")
    if spec.get("exact_command") != exact_command(out):
        raise ProbeStop("probe canonical run command changed")
    if spec.get("max_logical_calls") != MAX_LOGICAL_CALLS:
        raise ProbeStop("probe logical call ceiling changed")
    builder_profile = spec.get("builder_profile")
    if (
        not isinstance(builder_profile, Mapping)
        or builder_profile.get("name") != BUILDER_PROFILE_NAME
        or builder_profile.get("model") != MODEL
        or builder_profile.get("provider") != PROVIDER
        or builder_profile.get("max_tokens") != BUILDER_MAX_TOKENS
        or builder_profile.get("reasoning") != {"effort": "low"}
    ):
        raise ProbeStop("probe builder profile changed")
    if spec.get("parameter_reuse_policy") != PARAMETER_REUSE_POLICY:
        raise ProbeStop("probe parameter reuse policy changed")
    lock = spec.get("model_locks")
    if not isinstance(lock, Mapping) or set(lock) != {MODEL} or lock[MODEL].get("provider") != PROVIDER:
        raise ProbeStop("probe model lock is invalid")
    if dict(lock[MODEL]) != _provider_lock(PROVIDER_PROFILE):
        raise ProbeStop("probe provider lock changed")
    _verify_source_manifest(spec.get("source_manifest") or {})
    if spec.get("source_manifest_sha256") != _hash_value(spec.get("source_manifest")):
        raise ProbeStop("probe source manifest hash changed")
    if spec.get("diagnostic_prefix") != diagnostic_prefix(str(spec.get("version") or VERSION)):
        raise ProbeStop("probe diagnostic prefix changed")
    versioned_row_prefix = f"{spec.get('diagnostic_prefix')}/{spec.get('version')}/"
    for row in spec.get("episodes") or []:
        if not isinstance(row, Mapping) or not str(row.get("id") or "").startswith(versioned_row_prefix):
            raise ProbeStop("probe row is outside the frozen diagnostic prefix")
    selection = spec.get("training_selection")
    if isinstance(selection, Mapping) and selection.get("manifest_path") and selection.get("manifest_sha256"):
        manifest_path = Path(str(selection["manifest_path"])).resolve()
        if not manifest_path.is_file() or _hash_file(manifest_path) != selection.get("manifest_sha256"):
            raise ProbeStop("training manifest drifted after preparation")
    config_path = Path(str(spec.get("diagnostic_config") or "")).resolve()
    if not config_path.is_file() or _hash_file(config_path) != spec.get("diagnostic_config_sha256"):
        raise ProbeStop("diagnostic budget config is missing or changed")
    compatibility_path = out / str(spec.get("compatibility_inputs") or "")
    if spec.get("compatibility_inputs") and (
        not compatibility_path.is_file()
        or _hash_file(compatibility_path) != spec.get("compatibility_inputs_sha256")
    ):
        raise ProbeStop("frozen compatibility input artifact is missing or changed")
    loaded_config = diagnostic_budget.load_diagnostic_config(
        config_path,
        expected_prefix=str(spec.get("diagnostic_prefix")),
        ledger_path=Path(str(spec.get("ledger_absolute"))),
        authorization_path=Path(str(spec.get("authorization_path"))),
    )
    if loaded_config.get("diagnostic_prefix") != spec.get("diagnostic_prefix"):
        raise ProbeStop("diagnostic prefix changed")
    return spec


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    """Load and verify an immutable prepared probe specification."""

    return _load_spec(Path(out).resolve())


load = load_spec


validate_query = validate_query_request


def prepare(
    origin: Path | str = DEFAULT_ORIGIN,
    out: Path | str = DEFAULT_OUT,
    *,
    version: str | None = None,
    provider_profile: str = PROVIDER_PROFILE,
    families: Sequence[str] | None = None,
    max_demos: int = 3,
    diagnostic_prefix_value: str | None = None,
    diagnostic_prefix: str | None = None,
    authorization_path: Path | str | None = None,
    ledger_path: Path | str | None = None,
) -> dict[str, Any]:
    """Freeze a local build-only spec without constructing an SDK client."""

    out_argument = Path(out)
    version = _safe_version(
        version if version is not None else (VERSION if out_argument.resolve() == DEFAULT_OUT.resolve() else out_argument.name)
    )
    out = out_argument.resolve()
    origin = Path(origin).resolve()
    if out.name != version:
        raise ProbeStop("probe output basename must equal its version")
    selected_families = tuple(pilot.FAMILIES if families is None else (str(family) for family in families))
    if not selected_families or any(family not in pilot.FAMILIES for family in selected_families) or len(set(selected_families)) != len(selected_families):
        raise ProbeStop("families must be known and unique")
    # Import/callability checks happen before any config or spec is written.
    required = (
        evidence_projection.project_demonstrations,
        prefix_contract.validate_prefix_response,
        guard_evidence.validate_guard_evidence,
        build_store,
        diagnostic_budget.prepare_diagnostic_config,
        explore_budget.make_builder_client,
    )
    if any(not callable(item) for item in required):
        raise ProbeStop("probe dependencies are incomplete")
    origin_spec = _origin_spec(origin)
    training_snapshot = _training_snapshot(origin)
    demos = _select_demos(_load_demos(origin, origin_spec, selected_families), max_demos)
    training_artifact = _training_artifact(demos)
    compatibility_artifact = _load_compatibility_inputs(demos)
    # Exercise the existing projection helper at preparation time on a
    # detached copy.  Static prompts use the evidence-store packet encoding,
    # whose target/effect packets are also recorded in this spec's protocol.
    evidence_projection.project_demonstrations([_demo_public(demo) for demo in demos])
    if provider_profile != PROVIDER_PROFILE:
        raise ProbeStop("probe provider is frozen to z_ai_fp8")
    lock = _provider_lock(provider_profile)
    profile = _profile_document(lock)
    prefix = diagnostic_prefix_value or diagnostic_prefix or _expected_diagnostic_prefix(version)
    if prefix != _expected_diagnostic_prefix(version):
        raise ProbeStop("diagnostic prefix is frozen to the probe version")
    out.mkdir(parents=True, exist_ok=True)
    if (out / SPEC_NAME).is_file():
        existing = _load_spec(out, expected_version=version)
        if tuple(existing.get("families") or ()) != selected_families or existing.get("max_demos") != max_demos:
            raise ProbeStop("existing probe spec has different frozen inputs")
        return existing
    if any(path for path in out.iterdir() if path.name not in {DIAGNOSTIC_CONFIG_NAME, TRAINING_INPUTS_NAME, COMPATIBILITY_INPUTS_NAME}):
        raise ProbeStop("probe output contains an unarchived partial draft")
    # Diagnostic config owns its own shared lock.  Prepare it before this
    # worker can acquire the run lock during --run.
    auth = Path(authorization_path).resolve() if authorization_path is not None else diagnostic_budget.AUTHORIZATION_PATH
    ledger = Path(ledger_path).resolve() if ledger_path is not None else diagnostic_budget.SHARED_LEDGER_PATH
    config_path = out / DIAGNOSTIC_CONFIG_NAME
    config = diagnostic_budget.prepare_diagnostic_config(
        prefix,
        path=config_path,
        ledger_path=ledger,
        authorization_path=auth,
    )
    _write(out / TRAINING_INPUTS_NAME, training_artifact, private=True)
    _write(out / COMPATIBILITY_INPUTS_NAME, compatibility_artifact, private=True)
    source_manifest = _source_manifest()
    rows: list[dict[str, Any]] = []
    for demo_index, demo in enumerate(demos):
        family = str(demo.get("family") or "")
        # Counterbalanced rotations make condition position independent of the
        # family while preserving one row per condition per input.
        rotation = demo_index % len(CONDITIONS)
        condition_order = CONDITIONS[rotation:] + CONDITIONS[:rotation]
        for order, condition in enumerate(condition_order):
            key = _safe_key(f"d{demo_index:02d}_{condition}")
            rows.append(
                {
                    "key": key,
                    "id": f"{prefix}/{version}/{key}",
                    "family": family,
                    "demo_index": demo_index,
                    "source_sha256": demo["_source_sha256"],
                    "condition": condition,
                    "condition_order": order,
                    "condition_order_for_demo": list(condition_order),
                }
            )
    body: dict[str, Any] = {
        "schema": VERSION_SCHEMA,
        "version": version,
        "namespace": NAMESPACE,
        "model": MODEL,
        "provider_profile": PROVIDER_PROFILE,
        "provider": PROVIDER,
        "builder_profile": profile,
        "model_locks": {MODEL: lock},
        "families": list(selected_families),
        "conditions": list(CONDITIONS),
        "condition_order_policy": "counterbalanced rotation by selected demo index",
        "episodes": rows,
        "max_demos": max_demos,
        "max_logical_calls": MAX_LOGICAL_CALLS,
        "max_query_rounds": MAX_QUERY_ROUNDS,
        "query_names": list(QUERIES),
        "query_examples": {
            "single": VALID_SINGLE_QUERY_EXAMPLE,
            "batch": VALID_BATCH_QUERY_EXAMPLE,
        },
        "query_contract": {
            "single_schema": QUERY_SCHEMA,
            "batch_schema": QUERY_BATCH_SCHEMA,
            "single_exact_keys": ["schema", "query", "source_step"],
            "batch_exact_keys": ["schema", "queries"],
            "batch_item_exact_keys": ["query", "source_step"],
            "max_batch_requests": MAX_QUERIES_PER_CALL,
            "query": {"type": "string", "enum": list(QUERIES)},
            "source_step": "positive one-based integer",
            "query_semantics": {
                "target": "pre-state target node and descriptor candidates",
                "effect": "post-only visible value witnesses and ambiguity metadata",
                "state_pre": "complete raw pre-state observation",
                "state_post": "complete raw post-state observation",
                "unknown": "request a raw state packet or stop before the boundary",
            },
            "network": False,
            "arbitrary_path": False,
            "source": "recorded training evidence only",
        },
        "prefix_output_contract": _output_contract(),
        "prefix_wrapper_example": VALID_PREFIX_WRAPPER_EXAMPLE,
        "parameter_reuse_policy": PARAMETER_REUSE_POLICY,
        "training_inputs": TRAINING_INPUTS_NAME,
        "training_inputs_sha256": _hash_file(out / TRAINING_INPUTS_NAME),
        "compatibility_inputs": COMPATIBILITY_INPUTS_NAME,
        "compatibility_inputs_sha256": _hash_file(out / COMPATIBILITY_INPUTS_NAME),
        "compatibility_policy": {
            "checker": "guiexp_android.selective_trace_compatibility.check_prefix",
            "fixed_development_traces": [dict(item) for item in COMPATIBILITY_TRACES],
            "literal_control_bindings": {},
            "development_trace_is_not_heldout_success": True,
            "missing_or_alternative_path": "unknown_not_task_failure",
            "binding_calls_max_per_built_row": 1,
            "zero_slots_binding_calls": 0,
        },
        "training_selection": {
            "successful_recorded_inputs": len(demos),
            "source_hashes": [demo["_source_sha256"] for demo in demos],
            "source_paths": [demo["_source_path"] for demo in demos],
            "manifest_path": training_snapshot.get("manifest_path"),
            "manifest_sha256": training_snapshot.get("manifest_sha256"),
            "readonly": True,
        },
        "source_manifest": source_manifest,
        "source_manifest_sha256": _hash_value(source_manifest),
        "origin_spec_sha256": origin_spec.get("spec_sha256"),
        "origin_root": str(origin),
        "diagnostic_prefix": prefix,
        "diagnostic_config": str(config_path),
        "diagnostic_config_sha256": _hash_file(config_path),
        "diagnostic_limit_usd": "1",
        "ledger_absolute": str(config.get("shared_ledger")),
        "run_lock_absolute": str(config.get("shared_run_lock")),
        "authorization_path": str(config.get("authorization_path")),
        "global_budget_ceiling_usd": "20",
        "new_tranche_ceiling_usd": "10",
        "execution": {
            "mode": "build_only",
            "ui_actions": False,
            "serving": False,
            "heldout_answers": False,
            "same_diagnostic_prefix_for_all_rows": True,
            "shared_runner_lock": True,
            "host_wake_guard": True,
            "provider_metadata_validation": "official current endpoint before paid request",
        },
        "cli": {
            "working_directory": "computer-use",
            "interpreter": "../.venv-android/bin/python",
            "prepare_command": exact_command(out).replace("--run", "--prepare"),
            "run_command": exact_command(out),
        },
        "exact_command": exact_command(out),
        "reporting": {
            "scope_separate_from_tokens": True,
            "yield_separate_from_tokens": True,
            "task_success_claims": False,
            "parameter_generalization_untested": True,
            "contribution_evidence": False,
            "training_validity_only": True,
            "missing_rows_are_missing": True,
            "one_step_open_only": "reference_only",
            "comparability": "same output contract, profile, four logical calls, and verifier feedback across conditions",
        },
    }
    body["spec_sha256"] = _hash_value(body)
    _write(out / SPEC_NAME, body)
    return body


def _provider_validate(spec: Mapping[str, Any], metadata_fetcher: Any = None) -> Any:
    validator = getattr(explore_budget, "validate_locks", None)
    if not callable(validator):
        raise ProbeStop("current provider validation API is unavailable")
    kwargs: dict[str, Any] = {
        "provider_profile": PROVIDER_PROFILE,
        "request_profile": BUILDER_PROFILE_NAME,
    }
    if metadata_fetcher is not None:
        kwargs["metadata_fetcher"] = metadata_fetcher
    return validator(spec["model_locks"], **kwargs)


def _exclusive_run(out: Path):
    context = getattr(diagnostic_budget, "exclusive_run", None)
    if callable(context):
        return context(identity_path=out / RUN_IDENTITY_NAME, mode="build")
    context = getattr(explore_budget, "exclusive_run", None)
    if not callable(context):
        raise ProbeStop("shared runner lock API is unavailable")
    return context(identity_path=out / RUN_IDENTITY_NAME, mode="build")


def run(
    out: Path | str = DEFAULT_OUT,
    *,
    max_rows: int | None = None,
    client_factory: Callable[..., Any] | None = None,
    env_file: Path | str | None = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
    ledger: Any = None,
) -> dict[str, Any]:
    """Perform one finite build pass and persist every request/result."""

    out = Path(out).resolve()
    spec = _load_spec(out)
    training = _load_training_artifact(out, spec)
    compatibility_inputs = (
        _load_compatibility_artifact(out, spec)
        if spec.get("compatibility_inputs")
        else {}
    )
    if max_rows is not None and (type(max_rows) is not int or max_rows < 1):
        raise ProbeStop("max_rows must be a positive integer")
    if ledger is None:
        factory = getattr(diagnostic_budget, "make_diagnostic_ledger", None)
        if not callable(factory):
            raise ProbeStop("diagnostic ledger factory is unavailable")
        ledger = factory(spec["diagnostic_config"], host_guard=host_guard)
    # Test clients are fully offline.  Production clients are validated by
    # the current official metadata endpoint before any paid call is made.
    if client_factory is None or metadata_fetcher is not None:
        _provider_validate(spec, metadata_fetcher)
    results: list[dict[str, Any]] = []
    batch_status = "complete"
    stopped_reason: str | None = None
    processed = 0
    with _exclusive_run(out):
        for row in spec.get("episodes") or []:
            if max_rows is not None and processed >= max_rows:
                batch_status = "pilot_limit"
                break
            prior = _existing_row(out, row, ledger, spec)
            if prior is not None:
                results.append(prior)
                continue
            demo = _training_for_row(training, row)
            try:
                result = _build_row(
                    out,
                    spec,
                    row,
                    demo,
                    ledger=ledger,
                    client_factory=client_factory,
                    env_file=env_file,
                    sdk=sdk,
                    metadata_fetcher=metadata_fetcher,
                    host_guard=host_guard,
                    compatibility=compatibility_inputs.get(str(row.get("family"))),
                )
            except BudgetStop as exc:
                result = _stopped_result(out, spec, row, "budget_stopped", exc)
                _save_result(out, row, result)
                _save_state(out, row, {"record_type": "evidence-probe-state", "status": "budget_stopped", "row_id": row.get("id"), "reason": result["error"]})
                results.append(result)
                batch_status = "budget_stopped"
                stopped_reason = result["error"]
                break
            except EmptyResponse as exc:
                result = _stopped_result(out, spec, row, "empty_response", exc)
                _save_result(out, row, result)
                _save_state(out, row, {"record_type": "evidence-probe-state", "status": "empty_response", "row_id": row.get("id"), "reason": result["error"]})
                results.append(result)
                batch_status = "empty_response"
                stopped_reason = result["error"]
                break
            except InfrastructureFailure as exc:
                result = _stopped_result(out, spec, row, "infrastructure_failure", exc)
                _save_result(out, row, result)
                _save_state(out, row, {"record_type": "evidence-probe-state", "status": "infrastructure_failure", "row_id": row.get("id"), "reason": result["error"]})
                results.append(result)
                batch_status = "infrastructure_failure"
                stopped_reason = result["error"]
                break
            except ProbeStop as exc:
                result = _row_result_base(spec, row)
                calls = _read_calls(out, row)
                result.update(status="failed_build", error_type=type(exc).__name__, error=_safe_error(exc), calls=calls, calls_made=len(calls))
                _save_result(out, row, result)
                _save_state(out, row, {"record_type": "evidence-probe-state", "status": "failed_build", "row_id": row.get("id"), "reason": result["error"]})
                results.append(result)
                processed += 1
                continue
            except Exception as exc:  # noqa: BLE001
                result = _stopped_result(out, spec, row, "infrastructure_failure", exc)
                _save_result(out, row, result)
                _save_state(out, row, {"record_type": "evidence-probe-state", "status": "infrastructure_failure", "row_id": row.get("id"), "reason": result["error"]})
                results.append(result)
                batch_status = "infrastructure_failure"
                stopped_reason = result["error"]
                break
            results.append(result)
            processed += 1
    counts = Counter(str(item.get("status") or "unknown") for item in results)
    planned = len(spec.get("episodes") or [])
    if batch_status == "complete" and len(results) < planned:
        batch_status = "incomplete"
    summary = {
        "record_type": "evidence-probe-build-manifest",
        "status": batch_status,
        "version": spec["version"],
        "model": spec["model"],
        "provider": spec["provider"],
        "provider_profile": spec["provider_profile"],
        "diagnostic_prefix": spec["diagnostic_prefix"],
        "max_logical_calls": MAX_LOGICAL_CALLS,
        "planned_rows": planned,
        "processed_rows": processed,
        "counts": dict(counts),
        "stop_reason": stopped_reason,
        "rows": results,
        "pending_rows": max(0, planned - len(results)),
    }
    _write(out / BUILD_MANIFEST_NAME, summary)
    return summary


def build(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Alias for callers that use the conventional build-stage name."""

    return run(*args, **kwargs)


def _read_calls(out: Path, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    directory = _row_dir(out, row)
    calls: list[dict[str, Any]] = []
    paths = []
    if directory.is_dir():
        paths = list(directory.glob("call_*.json")) + list(directory.glob("binding_*.json"))
    for path in sorted(paths):
        value = _read(path)
        if isinstance(value, Mapping):
            calls.append(dict(value))
    return calls


def _usage_summary(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
    known: dict[str, int] = {key: 0 for key in totals}
    cost: list[float] = []
    for call in calls:
        usage = call.get("usage") or {}
        if not isinstance(usage, Mapping):
            continue
        for key in totals:
            value = usage.get(key)
            if type(value) is int and value >= 0:
                totals[key] += value
                known[key] += 1
        raw_cost = usage.get("cost_usd")
        try:
            if raw_cost is not None:
                cost.append(float(raw_cost))
        except (TypeError, ValueError):
            pass
    result: dict[str, Any] = {key: (totals[key] if known[key] else None) for key in totals}
    result["known_call_counts"] = known
    result["cost_usd_sum"] = sum(cost) if cost else None
    result["calls"] = len(calls)
    return result


def _analyze_row(out: Path, row: Mapping[str, Any]) -> dict[str, Any]:
    all_calls = _read_calls(out, row)
    build_path = _build_path(out, row)
    if build_path.is_file():
        value = _read(build_path)
        result = dict(value) if isinstance(value, Mapping) else _row_result_base({}, row)
    elif all_calls:
        result = _row_result_base({}, row)
        result.update(status="ambiguous", error="call evidence exists without a terminal build result")
    else:
        result = _row_result_base({}, row)
        result.update(status="missing", error="no build result or call evidence was recorded")
    builder_calls = [call for call in all_calls if call.get("phase") in {None, "builder", "query"}]
    query_calls = [call for call in all_calls if call.get("phase") == "query"]
    binding_calls = [call for call in all_calls if call.get("phase") == "binding"]
    result["calls"] = builder_calls
    result["compatibility_calls"] = binding_calls
    result["all_calls"] = all_calls
    result["calls_made"] = len(all_calls)
    result["builder_calls_made"] = len(builder_calls)
    result["compatibility_calls_made"] = len(binding_calls)
    result["usage"] = _usage_summary(all_calls)
    result["costs_by_phase"] = {
        "builder": _usage_summary([call for call in builder_calls if call.get("phase") != "query"]),
        "query": _usage_summary(query_calls),
        "binding": _usage_summary(binding_calls),
    }
    # Missing rows retain null scope/yield and a distinct status.  They are
    # never converted to zero-token or zero-yield observations.
    if result.get("status") == "missing":
        result["scope"] = None
        result["yield"] = None
    return result


def _reference_open_only(training: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "status": "reference_only",
        "model_calls": 0,
        "verified_prefix_steps": None,
        "nontrivial_training_prefix": False,
        "training_validity_only": True,
        "definition": "zero-LLM open-only capability is reserved as a lower-capability reference",
        "source_inputs": len(training),
        "model_results": False,
    }


def analyze(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    """Aggregate build evidence with scope/yield and token usage separate."""

    out = Path(out).resolve()
    spec = _load_spec(out)
    training = _load_training_artifact(out, spec)
    compatibility_inputs = (
        _load_compatibility_artifact(out, spec)
        if spec.get("compatibility_inputs")
        else {}
    )
    rows = [_analyze_row(out, row) for row in spec.get("episodes") or []]
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition[str(row.get("condition"))].append(row)
    condition_summary: dict[str, Any] = {}
    for condition in CONDITIONS:
        entries = by_condition.get(condition, [])
        valid = [entry for entry in entries if entry.get("candidate_valid") is True]
        scopes = [entry.get("scope") for entry in valid if isinstance(entry.get("scope"), Mapping)]
        yields = [entry.get("yield") for entry in valid if isinstance(entry.get("yield"), Mapping)]
        calls = [call for entry in entries for call in (entry.get("all_calls") or entry.get("calls") or []) if isinstance(call, Mapping)]
        builder_calls = [call for call in calls if call.get("phase") in {None, "builder"}]
        query_calls = [call for call in calls if call.get("phase") == "query"]
        binding_calls = [call for call in calls if call.get("phase") == "binding"]
        compatibility = [entry.get("compatibility") for entry in entries if isinstance(entry.get("compatibility"), Mapping)]
        condition_summary[condition] = {
            "planned": len(entries),
            "status_counts": dict(Counter(str(entry.get("status") or "unknown") for entry in entries)),
            "scope": {
                "verified_prefix_steps": [scope.get("plan_steps") for scope in scopes],
                "mean_verified_prefix_steps": (sum(scope.get("plan_steps", 0) for scope in scopes) / len(scopes)) if scopes else None,
            },
            "yield": {
                "nontrivial_training_prefix_count": sum(bool(item.get("nontrivial_training_prefix")) for item in yields),
                "verified_prefix_count": len(yields),
                "values": [dict(item) for item in yields],
            },
            "compatibility": {
                "status_counts": dict(Counter(str(item.get("status") or "unknown") for item in compatibility)),
                "prefix_steps_matched": [item.get("prefix_steps_matched") for item in compatibility],
                "action_divergence": [item.get("action_divergence") for item in compatibility if item.get("action_divergence") is not None],
                "guard_divergence": [item.get("guard_divergence") for item in compatibility if item.get("guard_divergence") is not None],
                "literal_control_baselines": [item.get("literal_control_baseline") for item in compatibility if item.get("literal_control_baseline") is not None],
            },
            "tokens": _usage_summary(calls),
            "costs_by_phase": {
                "builder": _usage_summary(builder_calls),
                "query": _usage_summary(query_calls),
                "binding": _usage_summary(binding_calls),
            },
        }
    all_calls = [call for row in rows for call in (row.get("all_calls") or row.get("calls") or []) if isinstance(call, Mapping)]
    report = {
        "record_type": "evidence-probe-analysis",
        "status": "observed" if all(row.get("status") != "missing" for row in rows) else "incomplete",
        "version": spec["version"],
        "model": spec["model"],
        "provider": spec["provider"],
        "provider_profile": spec["provider_profile"],
        "diagnostic_prefix": spec["diagnostic_prefix"],
        "planned_rows": len(rows),
        "status_counts": dict(Counter(str(row.get("status") or "unknown") for row in rows)),
        "scope": {
            "by_condition": {condition: condition_summary[condition]["scope"] for condition in CONDITIONS},
            "definition": "verified contiguous source prefix scope",
        },
        "yield": {
            "by_condition": {condition: condition_summary[condition]["yield"] for condition in CONDITIONS},
            "definition": "verified prefix yield, with one-step open-only marked reference-only",
        },
        "compatibility": {
            "by_condition": {condition: condition_summary[condition]["compatibility"] for condition in CONDITIONS},
            "definition": "recorded development trace compatibility after goal-only binding extraction",
        },
        "tokens": _usage_summary(all_calls),
        "costs_by_phase": {
            "builder": _usage_summary([call for call in all_calls if call.get("phase") in {None, "builder"}]),
            "query": _usage_summary([call for call in all_calls if call.get("phase") == "query"]),
            "binding": _usage_summary([call for call in all_calls if call.get("phase") == "binding"]),
        },
        "conditions": condition_summary,
        "rows": rows,
        "open_only_reference": _reference_open_only(training),
        "compatibility_inputs": {
            family: {"status": value.get("status"), "reason": value.get("reason")}
            for family, value in compatibility_inputs.items()
        },
        "task_success_claims": False,
        "parameter_generalization_untested": True,
        "contribution_evidence": False,
        "training_validity_only": True,
        "missing_rows_are_missing": True,
    }
    _write(out / ANALYSIS_NAME, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--build", action="store_true")
    group.add_argument("--analyze", action="store_true")
    parser.add_argument("--origin", type=Path, default=DEFAULT_ORIGIN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--version", default=None)
    parser.add_argument("--families", nargs="+", choices=pilot.FAMILIES, default=None)
    parser.add_argument("--max-demos", type=int, default=3)
    parser.add_argument("--max-rows", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        version = _safe_version(args.version or args.out.name)
        if args.prepare:
            result = prepare(
                origin=args.origin,
                out=args.out,
                version=version,
                families=args.families,
                max_demos=args.max_demos,
            )
            print(json.dumps({"status": "prepared", "version": result["version"], "spec_sha256": result["spec_sha256"], "exact_command": result["cli"]["run_command"]}, indent=2, ensure_ascii=True))
            return 0
        if args.run or args.build:
            result = run(args.out, max_rows=args.max_rows)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if result.get("status") in {"complete", "pilot_limit", "incomplete"} else 2
        result = analyze(args.out)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result.get("status") in {"observed", "complete"} else 2
    except (ProbeStop, pilot.PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {_safe_error(exc)}", file=sys.stderr)
        return 2


__all__ = [
    "BUILDER_MAX_TOKENS",
    "BUILDER_PROFILE_NAME",
    "COMPATIBILITY_INPUTS_NAME",
    "COMPATIBILITY_INPUT_SCHEMA",
    "COMPATIBILITY_TRACES",
    "CONDITIONS",
    "DIAGNOSTIC_PREFIX",
    "HANDOFF_POLICY",
    "LEGACY_VERSION",
    "MAX_CALLS",
    "MAX_LOGICAL_CALLS",
    "MAX_QUERIES_PER_CALL",
    "MAX_QUERY_BATCH",
    "MODEL",
    "PARAMETER_REUSE_POLICY",
    "PREFIX_SCHEMA",
    "PROVIDER",
    "PROVIDER_PROFILE",
    "QUERIES",
    "QUERY_BATCH_SCHEMA",
    "QUERY_NAMES",
    "QUERY_SCHEMA",
    "VALID_BATCH_QUERY_EXAMPLE",
    "VALID_PREFIX_WRAPPER_EXAMPLE",
    "VALID_SINGLE_QUERY_EXAMPLE",
    "VERSION",
    "EmptyResponse",
    "InfrastructureFailure",
    "ProbeStop",
    "analyze",
    "build",
    "build_messages",
    "diagnostic_prefix",
    "exact_command",
    "load",
    "load_spec",
    "main",
    "precise_feedback",
    "prepare",
    "repair_messages",
    "run",
    "validate_candidate",
    "validate_prefix_candidate",
    "validate_query",
    "validate_query_request",
]


if __name__ == "__main__":
    raise SystemExit(main())
