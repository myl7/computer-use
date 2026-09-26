"""Projected-data provider experiment for the selective Android pilot.

This is a new, versioned driver for projected selective plans.  It reuses the frozen
training traces and terminal serving evidence from the original pilot.  The
builder sees the data-only accessibility projection while guard validation
always reads the complete recorded demonstrations.  The provider and version
are selected during preparation and then read from the frozen spec.

Preparation freezes references and hashes.  ``--build`` and ``--run`` are the
only commands that can construct a paid client, and both are guarded by the
new exploratory budget facade.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shutil
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import selective_pilot as pilot
from .budget_client import BudgetStop
from .selective_evidence_projection import project_demonstrations
from .selective_guard_evidence import validate_guard_evidence
from .selective_plan_contract import (
    build_messages,
    repair_messages,
    validate_compilation_response,
    validate_plan as validate_contract_plan,
)
from .selective_prefix import (
    HANDOFF_POLICY,
    PREFIX_SCHEMA,
    validate_prefix_response,
)
from .selective_dense_prefix import (
    DENSE_PREFIX_SCHEMA,
    derive_dense_prefix,
    load_saved_candidate,
)
from . import selective_plan_contract as plan_contract


DEFAULT_VERSION = "projected_v4"
VERSION = DEFAULT_VERSION
VERSION_SCHEMA = "android-selective-explore/1"
MODEL = pilot.MODEL
DEFAULT_PROVIDER_PROFILE = "deepinfra_fp4"
PROVIDER_PROFILE = DEFAULT_PROVIDER_PROFILE
PROVIDER = "deepinfra/fp4"
NAMESPACE = pilot.NAMESPACE
VERSION_OUT = pilot.DEFAULT_OUT / "versions" / VERSION
# Delete is the first bounded build because its four-step evidence has a
# direct observed effect at every node.  Serving row order remains inherited
# from the frozen origin spec.
BUILD_FAMILIES = ("MarkorDeleteNote", "MarkorCreateNote", "FilesMoveFile")
BUILDER_PROFILE_NAME = "builder_16384"
SERVING_PROFILE_NAME = "serving_4096"
BUILDER_PROFILE = {
    "name": BUILDER_PROFILE_NAME,
    "model": MODEL,
    "provider": PROVIDER,
    "reasoning": {"effort": "low"},
    "max_tokens": 16384,
    "temperature": 0.0,
}
SERVING_PROFILE = {
    "name": SERVING_PROFILE_NAME,
    "model": MODEL,
    "provider": PROVIDER,
    "max_tokens": 4096,
    "temperature": 0.0,
}
# This is a serving lock.  Builder reservations are derived by the budget
# facade from the same provider-only fields and its 16384-token profile.
PROVIDER_PROFILE_CHOICES = ("wafer", "deepinfra_fp4", "z_ai_fp8")
PROVIDER_REGISTRY = {
    "wafer": {
        "provider": "wafer",
        "prompt_per_m": "0.10",
        "completion_per_m": "0.35",
        "context_length": 1048576,
    },
    "deepinfra_fp4": {
        "provider": "deepinfra/fp4",
        "prompt_per_m": "0.15",
        "completion_per_m": "0.50",
        "context_length": 1048576,
    },
    "z_ai_fp8": {
        "provider": "z-ai/fp8",
        "prompt_per_m": "0.15",
        "completion_per_m": "0.50",
        "context_length": 1048576,
    },
}


def _provider_config(provider_profile: str) -> dict[str, Any]:
    try:
        config = PROVIDER_REGISTRY[str(provider_profile)]
    except (KeyError, TypeError):
        raise BudgetStop(f"Unsupported projected provider profile: {provider_profile!r}.") from None
    return dict(config)


def _serving_locks(provider_profile: str) -> dict[str, dict[str, Any]]:
    config = _provider_config(provider_profile)
    prompt = config["prompt_per_m"]
    completion = config["completion_per_m"]
    context = int(config["context_length"])
    reservation = (
        (context * Decimal(str(prompt)) + 4096 * Decimal(str(completion)))
        / Decimal(1000000)
    )
    return {
        MODEL: {
            **config,
            "max_tokens": 4096,
            "reservation_usd": str(reservation),
        }
    }


SERVING_MODEL_LOCKS = _serving_locks(DEFAULT_PROVIDER_PROFILE)
COMPILATION_SCOPES = ("complete", "prefix")
DEFAULT_COMPILATION_SCOPE = "complete"


def _profile_documents(provider_profile: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Return immutable request documents for one registered provider."""

    config = _provider_config(provider_profile)
    provider = str(config["provider"])
    builder = {
        "name": BUILDER_PROFILE_NAME,
        "model": MODEL,
        "provider": provider,
        "reasoning": {"effort": "low"},
        "max_tokens": 16384,
        "temperature": 0.0,
    }
    serving = {
        "name": SERVING_PROFILE_NAME,
        "model": MODEL,
        "provider": provider,
        "max_tokens": 4096,
        "temperature": 0.0,
    }
    return builder, serving, _serving_locks(provider_profile)
AUTHORIZATION_PATH = pilot.DEFAULT_OUT / "budget_authorization_20260915.json"
PROJECTION_PATH = Path(__file__).with_name("selective_evidence_projection.py")
PROJECTED_SUPERVISOR_PATH = Path(__file__).with_name("selective_projected_supervisor.py")
PLAN_CONTRACT_PATH = Path(__file__).with_name("selective_plan_contract.py")
GUARD_EVIDENCE_PATH = Path(__file__).with_name("selective_guard_evidence.py")
AX_PARSER_PATH = Path(__file__).with_name("selective_ax_parser.py")
PREFIX_MODULE_PATH = Path(__file__).with_name("selective_prefix.py")
DENSE_PREFIX_PATH = Path(__file__).with_name("selective_dense_prefix.py")
TERMINAL = frozenset(
    {"done", "budget_stopped", "interrupted", "prior_receipt", "skipped_unbuildable", "uncertain"}
)
_VERSION_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z")


def _safe_version(value: str | None) -> str:
    version = DEFAULT_VERSION if value is None else str(value)
    if not _VERSION_RE.fullmatch(version) or "/" in version or version in {".", ".."}:
        raise pilot.PilotStop("Projected version must be a safe identifier without path separators.")
    return version


def _version_out(version: str) -> Path:
    return pilot.DEFAULT_OUT / "versions" / version


def _check_out_version(out: Path, version: str) -> None:
    if out.name != version:
        raise pilot.PilotStop("Projected output basename must equal its frozen version identifier.")


def _selected_families(families: Sequence[str] | None) -> tuple[str, ...]:
    selected = tuple(pilot.FAMILIES if families is None else families)
    if not selected or any(family not in pilot.FAMILIES for family in selected):
        raise pilot.PilotStop("Projected family selection must contain known nonempty families.")
    if len(set(selected)) != len(selected):
        raise pilot.PilotStop("Projected family selection contains duplicates.")
    return selected


def _compilation_scope(value: str | None) -> str:
    scope = DEFAULT_COMPILATION_SCOPE if value is None else str(value)
    if scope not in COMPILATION_SCOPES:
        raise pilot.PilotStop(f"Unsupported compilation scope: {scope!r}.")
    return scope


def _source_id(row: Mapping[str, Any]) -> str:
    return str(row.get("source_episode_id") or row.get("id") or "")


def _id_parts(episode_id: str) -> tuple[str | None, str | None, str | None]:
    prefix = NAMESPACE + "/"
    if not episode_id.startswith(prefix):
        return None, None, None
    parts = episode_id[len(prefix) :].split("/")
    if parts and parts[0] in pilot.FAMILIES:
        return (
            parts[0],
            parts[1] if len(parts) > 1 else None,
            parts[2] if len(parts) > 2 else None,
        )
    if len(parts) >= 4 and parts[1] in pilot.FAMILIES:
        return (parts[1], parts[2], parts[3])
    return None, None, None


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise pilot.PilotStop(f"Cannot read JSON artifact {path}.") from exc


def _hash(path: Path) -> str:
    try:
        return pilot.sha256_file(path)
    except (OSError, UnicodeError) as exc:
        raise pilot.PilotStop(f"Cannot hash required artifact {path}.") from exc


def _write(path: Path, value: Any, private: bool = False) -> None:
    pilot.atomic_json(path, value, private=private)


def _budget(required: bool = True):
    try:
        return importlib.import_module(".selective_explore_budget", __package__)
    except (ImportError, ModuleNotFoundError) as exc:
        if required:
            raise BudgetStop("selective_explore_budget is unavailable; no projected paid work may start.") from exc
        return None


def _provider_bounds(model_locks: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Remove serving request fields before deriving the builder lock."""

    result: dict[str, dict[str, Any]] = {}
    for model, lock in model_locks.items():
        if not isinstance(lock, Mapping):
            raise BudgetStop("Projected provider lock is invalid.")
        result[str(model)] = {
            key: lock[key]
            for key in ("provider", "prompt_per_m", "completion_per_m", "context_length")
            if key in lock
        }
    return result


def _origin_spec(origin: Path) -> dict[str, Any]:
    spec = _read(origin / "spec.json")
    if not isinstance(spec, Mapping):
        raise pilot.PilotStop("Origin selective spec is not an object.")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or pilot.hash_json(body) != digest:
        raise pilot.PilotStop("Origin selective spec hash is invalid.")
    return dict(spec)


def _state(origin: Path, row: Mapping[str, Any]) -> tuple[str, Path]:
    path = origin / "episodes" / str(row["id"]) / "state.json"
    if not path.is_file():
        return "pending", path
    try:
        value = _read(path)
    except pilot.PilotStop:
        return "unreadable", path
    return str(value.get("status") or "unknown"), path


def _trajectory(origin: Path, row_id: str) -> Path:
    return origin / "episodes" / row_id / "trajectory.jsonl"


def _new_id(old_id: str, version: str = DEFAULT_VERSION) -> str:
    prefix = NAMESPACE + "/"
    suffix = old_id[len(prefix) :] if old_id.startswith(prefix) else old_id
    return f"{NAMESPACE}/{version}/{suffix}"


def _copy_private(origin: Path, out: Path) -> tuple[str, str]:
    source = origin / "private" / "evaluator_bindings.json"
    if not source.is_file():
        raise pilot.PilotStop("Origin evaluator-private bindings are missing.")
    destination = out / "private" / "evaluator_bindings.json"
    if destination.is_file() and _hash(destination) != _hash(source):
        raise pilot.PilotStop("Projected private evaluator bindings changed.")
    if not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    destination.parent.chmod(0o700)
    destination.chmod(0o600)
    return str(destination.relative_to(out)), _hash(destination)


def _training_snapshot(origin: Path) -> dict[str, Any]:
    source = origin / "training_manifest.json"
    if not source.is_file():
        inherited = _origin_record(origin, _read(origin / "spec.json")) if (origin / "spec.json").is_file() else {}
        training = inherited.get("training")
        if isinstance(training, Mapping) and isinstance(training.get("families"), Mapping):
            snapshot = dict(training)
            snapshot["readonly"] = True
            snapshot["failed_attempts_retained"] = True
            return snapshot
        raise pilot.PilotStop("Origin training manifest is missing.")
    manifest = _read(source)
    families: dict[str, list[dict[str, Any]]] = {}
    for family, rows in (manifest.get("families") or {}).items():
        copied: list[dict[str, Any]] = []
        for row in rows or []:
            item = dict(row)
            raw = Path(str(item.get("trajectory") or ""))
            trace = raw if raw.is_absolute() else origin / raw
            item["trajectory_absolute"] = str(trace.resolve())
            item["trajectory_sha256_frozen"] = _hash(trace) if trace.is_file() else None
            copied.append(item)
        families[str(family)] = copied
    return {
        "manifest_path": str(source.resolve()),
        "manifest_sha256": _hash(source),
        "families": families,
        "readonly": True,
        "failed_attempts_retained": True,
    }


def _origin_record(origin: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    path = origin / str(spec.get("origin") or "origin.json")
    if not path.is_file():
        return {}
    value = _read(path)
    return dict(value) if isinstance(value, Mapping) else {}


def _inherited_imports(origin: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect prior imported rows without copying their UI artifacts."""

    imports: dict[str, dict[str, Any]] = {}
    for row_id, value in (spec.get("imported_rows") or {}).items():
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        item["id"] = str(row_id)
        item.setdefault("source_episode_id", str(row_id))
        item.setdefault("receipt_episode_id", str(row_id))
        imports[str(row_id)] = item
    inherited = _origin_record(origin, spec)
    for value in inherited.get("preserved_terminal_rows") or []:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        row_id = str(item.get("id") or item.get("source_episode_id") or "")
        if row_id:
            item.setdefault("source_episode_id", row_id)
            item.setdefault("receipt_episode_id", row_id)
            imports[row_id] = item
    return imports


def _row_evidence(
    origin: Path,
    row: Mapping[str, Any],
    status: str,
    state_path: Path,
) -> dict[str, Any]:
    row_id = str(row.get("id") or "")
    trajectory = _trajectory(origin, row_id)
    return {
        "id": row_id,
        "source_episode_id": _source_id(row),
        "receipt_episode_id": str(row.get("receipt_episode_id") or row_id),
        "family": row.get("family"),
        "binding_id": row.get("binding_id"),
        "arm": row.get("arm"),
        "status": status,
        "provider": row.get("provider"),
        "state_path": str(state_path.resolve()) if state_path.is_file() else None,
        "state_sha256": _hash(state_path) if state_path.is_file() else None,
        "trajectory_path": str(trajectory.resolve()) if trajectory.is_file() else None,
        "trajectory_sha256": _hash(trajectory) if trajectory.is_file() else None,
    }


def _ledger_has(ledger_path: Path, episode_id: str) -> bool:
    """Read the shared ledger fail-closed when checking new ID collisions."""

    if not ledger_path.is_file():
        return False
    try:
        with sqlite3.connect(
            f"file:{ledger_path.resolve()}?mode=ro", uri=True, timeout=5
        ) as database:
            row = database.execute(
                "SELECT 1 FROM calls WHERE episode=? OR id LIKE ? LIMIT 1",
                (episode_id, episode_id + "/%"),
            ).fetchone()
    except sqlite3.Error as exc:
        raise BudgetStop("Projected ledger receipt lookup failed; no new ID is safe.") from exc
    return row is not None


def _execution_manifest() -> dict[str, Any]:
    if not PROJECTION_PATH.is_file():
        raise pilot.PilotStop("selective_evidence_projection.py is required before projected preparation.")
    if not PROJECTED_SUPERVISOR_PATH.is_file():
        raise pilot.PilotStop("selective_projected_supervisor.py is required before projected preparation.")
    if not PLAN_CONTRACT_PATH.is_file():
        raise pilot.PilotStop("selective_plan_contract.py is required before projected preparation.")
    if not GUARD_EVIDENCE_PATH.is_file():
        raise pilot.PilotStop("selective_guard_evidence.py is required before projected preparation.")
    if not AX_PARSER_PATH.is_file():
        raise pilot.PilotStop("selective_ax_parser.py is required before projected preparation.")
    if not PREFIX_MODULE_PATH.is_file():
        raise pilot.PilotStop("selective_prefix.py is required before projected preparation.")
    if not DENSE_PREFIX_PATH.is_file():
        raise pilot.PilotStop("selective_dense_prefix.py is required before projected preparation.")
    budget = _budget(required=True)
    source_hashes = getattr(budget, "source_hashes", None)
    runtime_manifest = getattr(budget, "runtime_manifest", None)
    if not callable(source_hashes) or not callable(runtime_manifest):
        raise pilot.PilotStop("selective_explore_budget source/runtime freeze API is incomplete.")
    return {
        "pilot": pilot.source_manifest(pilot.REPO_ROOT),
        "projected_source_sha256": _hash(Path(__file__).resolve()),
        "projection_source_sha256": _hash(PROJECTION_PATH),
        "projected_supervisor_source_sha256": _hash(PROJECTED_SUPERVISOR_PATH),
        "plan_contract_source_sha256": _hash(PLAN_CONTRACT_PATH),
        "guard_evidence_source_sha256": _hash(GUARD_EVIDENCE_PATH),
        "ax_parser_source_sha256": _hash(AX_PARSER_PATH),
        "prefix_source_sha256": _hash(PREFIX_MODULE_PATH),
        "dense_prefix_source_sha256": _hash(DENSE_PREFIX_PATH),
        "budget_source_sha256": source_hashes(),
        "budget_runtime_manifest": runtime_manifest(),
    }


def _freeze_phase_manifests(
    out: Path,
    model_locks: Mapping[str, Any],
    authorization_path: Path,
    *,
    version: str = DEFAULT_VERSION,
    provider_profile: str = DEFAULT_PROVIDER_PROFILE,
    builder_profile: Mapping[str, Any] | None = None,
    serving_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    budget = _budget(required=True)
    freezer = getattr(budget, "freeze_phase_manifest", None)
    if not callable(freezer):
        raise BudgetStop("selective_explore_budget.freeze_phase_manifest() is required before projected preparation.")
    phases: dict[str, Any] = {}
    provider_locks = _provider_bounds(model_locks)
    builder_name = str((builder_profile or {}).get("name") or BUILDER_PROFILE_NAME)
    serving_name = str((serving_profile or {}).get("name") or SERVING_PROFILE_NAME)
    for phase, profile in (("build", builder_name), ("run", serving_name)):
        path = out / f"phase_{phase}.json"
        manifest = freezer(
            f"{version}_{phase}",
            model_locks=provider_locks,
            provider_profile=provider_profile,
            request_profile=profile,
            path=path,
            authorization_path=authorization_path,
            extra={
                "version": version,
                "provider": _provider_config(provider_profile)["provider"],
                "provider_profile": provider_profile,
            },
        )
        phases[phase] = {
            "path": str(path.relative_to(out)),
            "sha256": _hash(path),
            "phase_manifest_sha256": manifest.get("phase_manifest_sha256"),
        }
    return phases


def prepare(
    origin: Path | str = pilot.DEFAULT_OUT,
    out: Path | str | None = None,
    *,
    version: str | None = None,
    provider_profile: str | None = None,
    families: Sequence[str] | None = None,
    compilation_scope: str | None = None,
    derived_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze one projected version/provider without making a model request."""

    version = _safe_version(version)
    provider_profile = str(provider_profile or DEFAULT_PROVIDER_PROFILE)
    selected_families = _selected_families(families)
    compilation_scope = _compilation_scope(compilation_scope)
    builder_profile, serving_profile, model_locks = _profile_documents(provider_profile)
    provider = str(model_locks[MODEL]["provider"])
    origin = Path(origin).resolve()
    out = (_version_out(version) if out is None else Path(out)).resolve()
    _check_out_version(out, version)
    origin_spec = _origin_spec(origin)
    if (out / "spec.json").is_file():
        existing = _load(
            out,
            expected_version=version,
            expected_provider_profile=provider_profile,
            expected_compilation_scope=compilation_scope,
        )
        if tuple(existing.get("families") or ()) != selected_families:
            raise pilot.PilotStop("Existing projected output has a different frozen family selection.")
        return existing
    if out.exists() and any(out.iterdir()):
        raise pilot.PilotStop("Projected output contains an unarchived partial draft; no overwrite is safe.")
    out.mkdir(parents=True, exist_ok=True)
    private_ref, private_sha = _copy_private(origin, out)
    training = _training_snapshot(origin)
    origin_files = {
        "spec.json": _hash(origin / "spec.json"),
        "private/evaluator_bindings.json": _hash(origin / "private/evaluator_bindings.json"),
    }
    if (origin / "training_manifest.json").is_file():
        origin_files["training_manifest.json"] = training["manifest_sha256"]
    else:
        origin_files["origin.json"] = _hash(origin / str(origin_spec.get("origin") or "origin.json"))
    preserved: list[dict[str, Any]] = []
    versioned: list[dict[str, Any]] = []
    excluded_families: list[dict[str, Any]] = []
    excluded_binding_keys: set[tuple[str, str]] = set()
    inherited_imports = _inherited_imports(origin, origin_spec)
    interrupted_baselines: list[dict[str, Any]] = []
    for inherited in inherited_imports.values():
        family, binding, arm = _id_parts(str(inherited.get("id") or inherited.get("source_episode_id") or ""))
        if (
            family == "FilesMoveFile"
            and binding
            and arm == "reactive"
            and str(inherited.get("status")) in {"interrupted", "budget_stopped"}
        ):
            excluded_binding_keys.add((family, binding))
            interrupted_baselines.append(inherited)
    for row in origin_spec.get("episodes") or []:
        old_id = str(row["id"])
        status, state_path = _state(origin, row)
        trajectory = _trajectory(origin, old_id)
        family = str(row.get("family") or "")
        binding_id = str(row.get("binding_id") or "")
        source_id = _source_id(row)
        if family not in selected_families:
            excluded_families.append(
                dict(
                    _row_evidence(origin, row, status, state_path),
                    execution_excluded=True,
                    reason="family not selected for this version",
                )
            )
            continue
        if (family, binding_id) in excluded_binding_keys:
            excluded_families.append(
                dict(
                    _row_evidence(origin, row, status, state_path),
                    execution_excluded=True,
                    reason="binding excluded because its reactive baseline is interrupted",
                )
            )
            continue
        if status in TERMINAL:
            preserved.append(
                {
                    "id": old_id,
                    "source_episode_id": source_id,
                    "receipt_episode_id": old_id,
                    "status": status,
                    "provider": origin_spec.get("provider", "relace"),
                    "state_path": str(state_path.resolve()) if state_path.is_file() else None,
                    "state_sha256": _hash(state_path) if state_path.is_file() else None,
                    "trajectory_path": str(trajectory.resolve()) if trajectory.is_file() else None,
                    "trajectory_sha256": _hash(trajectory) if trajectory.is_file() else None,
                }
            )
            # An unbuildable marker is a planned row, so replace that row with
            # a fresh versioned ID.  Completed and interrupted UI are imports.
            if status != "skipped_unbuildable":
                continue
        new_row = dict(row)
        new_id = _new_id(source_id, version)
        new_row.update(
            {
                "id": new_id,
                "source_episode_id": source_id,
                "receipt_episode_id": new_id,
                "version": version,
                "provider": provider,
                "provider_profile": provider_profile,
                "source_status": status,
            }
        )
        versioned.append(new_row)
    preserved_ids = {str(item.get("id")) for item in preserved}
    for inherited in inherited_imports.values():
        inherited_id = str(inherited.get("id") or "")
        family, binding_id, _arm = _id_parts(
            str(inherited.get("source_episode_id") or inherited_id)
        )
        if (
            inherited_id
            and family in selected_families
            and binding_id
            and (family, binding_id) not in excluded_binding_keys
            and inherited_id not in preserved_ids
        ):
            preserved.append(
                {
                    "id": inherited_id,
                    "source_episode_id": str(inherited.get("source_episode_id") or inherited_id),
                    "receipt_episode_id": str(inherited.get("receipt_episode_id") or inherited_id),
                    "status": str(inherited.get("status") or "imported"),
                    "provider": inherited.get("provider", "relace"),
                    "state_path": inherited.get("state_path"),
                    "state_sha256": inherited.get("state_sha256"),
                    "trajectory_path": inherited.get("trajectory_path"),
                    "trajectory_sha256": inherited.get("trajectory_sha256"),
                }
            )
            preserved_ids.add(inherited_id)
    excluded_bindings: list[dict[str, Any]] = []
    for family, binding_id in sorted(excluded_binding_keys):
        source_ids = sorted(
            {
                str(item.get("id") or item.get("source_episode_id"))
                for item in interrupted_baselines
                if _id_parts(str(item.get("id") or item.get("source_episode_id") or ""))[:2]
                == (family, binding_id)
            }
        )
        source_ids.extend(
            sorted(
                {
                    str(item.get("id"))
                    for item in excluded_families
                    if str(item.get("family")) == family and str(item.get("binding_id")) == binding_id
                }
            )
        )
        excluded_bindings.append(
            {
                "family": family,
                "binding_id": binding_id,
                "reason": "reactive baseline is terminal interrupted or budget_stopped",
                "source_episode_ids": sorted(set(source_ids)),
                "source_evidence": [
                    {
                        key: item.get(key)
                        for key in (
                            "id",
                            "status",
                            "state_path",
                            "state_sha256",
                            "trajectory_path",
                            "trajectory_sha256",
                        )
                    }
                    for item in interrupted_baselines
                    if _id_parts(str(item.get("id") or item.get("source_episode_id") or ""))[:2]
                    == (family, binding_id)
                ],
                "execution_excluded": True,
            }
        )
    ledger_path = Path(origin_spec.get("ledger_absolute", pilot.SHARED_LEDGER)).resolve()
    for row in versioned:
        if _ledger_has(ledger_path, str(row["receipt_episode_id"])):
            raise BudgetStop("Projected episode ID collides with an existing shared receipt.")
    execution = _execution_manifest()
    authorization_path = AUTHORIZATION_PATH.resolve()
    phase_manifests = _freeze_phase_manifests(
        out,
        model_locks,
        authorization_path,
        version=version,
        provider_profile=provider_profile,
        builder_profile=builder_profile,
        serving_profile=serving_profile,
    )
    origin_record = {
        "schema": VERSION_SCHEMA,
        "version": version,
        "selected_families": list(selected_families),
        "excluded_families": excluded_families,
        "excluded_bindings": excluded_bindings,
        "origin_root": str(origin),
        "origin_spec_sha256": origin_spec["spec_sha256"],
        "origin_spec_file_sha256": _hash(origin / "spec.json"),
        "origin_files": origin_files,
        "training": training,
        "preserved_terminal_rows": preserved,
        "versioned_rows": [
            {"id": row["id"], "versioned_from": row["source_episode_id"]} for row in versioned
        ],
        "provider": provider,
        "provider_profile": provider_profile,
        "created_unix": time.time(),
    }
    _write(out / "origin.json", origin_record)
    origin_sha = _hash(out / "origin.json")
    body: dict[str, Any] = {
        "schema": VERSION_SCHEMA,
        "revision": version,
        "version": version,
        "compilation_scope": compilation_scope,
        "completion_policy": "reactive_handoff" if compilation_scope == "prefix" else None,
        "prefix_handoff_policy": HANDOFF_POLICY if compilation_scope == "prefix" else None,
        "namespace": NAMESPACE,
        "model": MODEL,
        "provider": provider,
        "provider_profile": provider_profile,
        "model_locks": model_locks,
        "serving_model_locks": model_locks,
        "families": list(selected_families),
        "arms": list(pilot.ARMS),
        "episodes": versioned,
        "action_budget": pilot.MAX_ACTIONS,
        "local_assistance_max": pilot.MAX_LOCAL_ASSISTANCE,
        "obs_mode": pilot.OBS_MODE,
        "temperature": pilot.TEMPERATURE,
        "max_completion_tokens": pilot.MAX_COMPLETION_TOKENS,
        "ledger_absolute": str(ledger_path),
        "run_lock_absolute": origin_spec.get("run_lock_absolute", str(pilot.SHARED_RUN_LOCK)),
        "authorization_path": str(authorization_path),
        "budget_ceiling_total_usd": "20",
        "budget_ceiling_new_tranche_usd": "10",
        "private_bindings": private_ref,
        "private_binding_sha256": private_sha,
        "private_binding_visibility": "evaluator_only",
        "origin": "origin.json",
        "origin_sha256": origin_sha,
        "origin_files": origin_files,
        "source_manifest": execution,
        "source_manifest_sha256": pilot.hash_json(execution),
        "builder_profile": builder_profile,
        "serving_profile": serving_profile,
        "phase_manifests": phase_manifests,
        "build_input_projection": {
            "module": "guiexp_android.selective_evidence_projection",
            "source_sha256": _hash(PROJECTION_PATH),
            "builder_view": "projected",
            "guard_validation_view": "full_original_demonstrations",
        },
        "plan_contract": {
            "module": "guiexp_android.selective_plan_contract",
            "source_sha256": _hash(PLAN_CONTRACT_PATH),
            "schema": "selective-plan-contract/1",
        },
        "guard_evidence_policy": {
            "module": "guiexp_android.selective_guard_evidence",
            "source_sha256": _hash(GUARD_EVIDENCE_PATH),
            "alignment": "explicit plan step id to positive original training step",
            "validation_view": "full_original_demonstrations",
        },
        "ax_parser": {
            "module": "guiexp_android.selective_ax_parser",
            "source_sha256": _hash(AX_PARSER_PATH),
        },
        "prefix_policy": {
            "module": "guiexp_android.selective_prefix",
            "source_sha256": _hash(PREFIX_MODULE_PATH),
            "schema": PREFIX_SCHEMA,
            "handoff_policy": HANDOFF_POLICY,
            "completion_policy": "reactive_handoff",
        },
        "dense_prefix_policy": {
            "module": "guiexp_android.selective_dense_prefix",
            "source_sha256": _hash(DENSE_PREFIX_PATH),
            "schema": DENSE_PREFIX_SCHEMA,
            "method": "maximal_leading_source_steps_1_to_k",
        },
        "training": {
            "readonly_origin": training,
            "reuse_failed_cost_provenance": True,
            "full_evidence_for_guard_validation": True,
        },
        "derived_provenance": dict(derived_provenance) if derived_provenance is not None else None,
        "reused_training_refs": training["families"],
        "imported_rows": {
            item["id"]: {
                "source_episode_id": item["source_episode_id"],
                "receipt_episode_id": item["receipt_episode_id"],
                "status": item["status"],
                "provider": item.get("provider", origin_spec.get("provider", "relace")),
                "cross_provider": item.get("provider") != provider,
            }
            for item in preserved
        },
        "provenance": {
            "origin_root": str(origin),
            "origin_spec_sha256": origin_spec["spec_sha256"],
            "source_hashes": origin_spec.get("source_manifest"),
            "origin_provider": origin_spec.get("provider", "relace"),
            "provider": provider,
            "provider_profile": provider_profile,
            "cross_provider_imports": True,
            "preserved_terminal_rows": preserved,
            "projection_source_sha256": _hash(PROJECTION_PATH),
        },
        "plans": {family: f"plans/{family}.json" for family in selected_families},
        "selected_families": list(selected_families),
        "excluded_families": excluded_families,
        "excluded_bindings": excluded_bindings,
        "reporting": {
            "prototype_only": True,
            "cross_provider_exploratory": True,
            "selector_algorithm_implemented": False,
            "provider_attribution_per_call": True,
            "full_original_evidence_used_for_guard_validation": True,
            "compilation_scope": compilation_scope,
            "prefix_handoff_policy": HANDOFF_POLICY if compilation_scope == "prefix" else None,
        },
    }
    body["spec_sha256"] = pilot.hash_json(body)
    _write(out / "spec.json", body)
    return body


def _load(
    out: Path,
    *,
    expected_version: str | None = None,
    expected_provider_profile: str | None = None,
    expected_compilation_scope: str | None = None,
) -> dict[str, Any]:
    out = Path(out).resolve()
    spec = _read(out / "spec.json")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    frozen_version = _safe_version(spec.get("revision"))
    frozen_provider_profile = str(spec.get("provider_profile") or "")
    frozen_compilation_scope = _compilation_scope(spec.get("compilation_scope"))
    builder_profile, serving_profile, model_locks = _profile_documents(frozen_provider_profile)
    provider = str(model_locks[MODEL]["provider"])
    if (
        spec.get("revision") != frozen_version
        or spec.get("schema") != VERSION_SCHEMA
        or not isinstance(digest, str)
        or pilot.hash_json(body) != digest
    ):
        raise pilot.PilotStop("Projected spec hash or schema changed.")
    _check_out_version(out, frozen_version)
    if expected_version is not None and _safe_version(expected_version) != frozen_version:
        raise pilot.PilotStop("Requested version does not match the frozen projected spec.")
    if expected_provider_profile is not None and str(expected_provider_profile) != frozen_provider_profile:
        raise BudgetStop("Requested provider profile does not match the frozen projected spec.")
    if (
        expected_compilation_scope is not None
        and _compilation_scope(expected_compilation_scope) != frozen_compilation_scope
    ):
        raise pilot.PilotStop("Requested compilation scope does not match the frozen projected spec.")
    expected_completion_policy = "reactive_handoff" if frozen_compilation_scope == "prefix" else None
    expected_prefix_policy = HANDOFF_POLICY if frozen_compilation_scope == "prefix" else None
    if (
        spec.get("compilation_scope") != frozen_compilation_scope
        or spec.get("completion_policy") != expected_completion_policy
        or spec.get("prefix_handoff_policy") != expected_prefix_policy
    ):
        raise pilot.PilotStop("Projected compilation policy changed after freezing.")
    if (
        spec.get("model") != MODEL
        or spec.get("provider") != provider
        or frozen_provider_profile not in PROVIDER_PROFILE_CHOICES
    ):
        raise BudgetStop("Projected model/provider lock changed.")
    if (
        spec.get("model_locks") != model_locks
        or spec.get("serving_model_locks") != model_locks
        or spec.get("builder_profile") != builder_profile
        or spec.get("serving_profile") != serving_profile
    ):
        raise BudgetStop("Projected request profile or provider lock changed.")
    selected_families = tuple(spec.get("families") or ())
    if (
        not selected_families
        or any(family not in pilot.FAMILIES for family in selected_families)
        or len(set(selected_families)) != len(selected_families)
        or tuple(spec.get("arms") or ()) != pilot.ARMS
        or set((spec.get("plans") or {})) != set(selected_families)
    ):
        raise pilot.PilotStop("Projected family selection or arm registry changed.")
    origin_path = out / str(spec.get("origin") or "origin.json")
    origin = _read(origin_path)
    if _hash(origin_path) != spec.get("origin_sha256"):
        raise pilot.PilotStop("Projected origin snapshot changed.")
    origin_root = Path(str(origin["origin_root"])).resolve()
    if _hash(origin_root / "spec.json") != origin.get("origin_spec_file_sha256"):
        raise pilot.PilotStop("Origin spec changed after projected preparation.")
    for name, frozen in (origin.get("origin_files") or {}).items():
        path = origin_root / name
        if frozen is not None and (not path.is_file() or _hash(path) != frozen):
            raise pilot.PilotStop(f"Origin artifact changed: {name}")
    for rows in (origin.get("training", {}).get("families") or {}).values():
        for row in rows or []:
            trace = Path(str(row.get("trajectory_absolute") or ""))
            frozen = row.get("trajectory_sha256_frozen")
            if frozen is not None and (not trace.is_file() or _hash(trace) != frozen):
                raise pilot.PilotStop("Readonly training trace changed.")
    for item in origin.get("preserved_terminal_rows") or []:
        for path_key, digest_key in (("state_path", "state_sha256"), ("trajectory_path", "trajectory_sha256")):
            value, frozen = item.get(path_key), item.get(digest_key)
            if frozen is not None and (not value or not Path(value).is_file() or _hash(Path(value)) != frozen):
                raise pilot.PilotStop("Preserved terminal UI artifact changed.")
    for item in origin.get("excluded_families") or []:
        for path_key, digest_key in (("state_path", "state_sha256"), ("trajectory_path", "trajectory_sha256")):
            value, frozen = item.get(path_key), item.get(digest_key)
            if frozen is not None and (not value or not Path(value).is_file() or _hash(Path(value)) != frozen):
                raise pilot.PilotStop("Excluded terminal UI artifact changed.")
    for binding in origin.get("excluded_bindings") or []:
        for item in binding.get("source_evidence") or []:
            for path_key, digest_key in (("state_path", "state_sha256"), ("trajectory_path", "trajectory_sha256")):
                value, frozen = item.get(path_key), item.get(digest_key)
                if frozen is not None and (not value or not Path(value).is_file() or _hash(Path(value)) != frozen):
                    raise pilot.PilotStop("Excluded binding evidence changed.")
    private = out / str(spec.get("private_bindings") or "private/evaluator_bindings.json")
    if not private.is_file() or private.stat().st_mode & 0o077:
        raise pilot.PilotStop("Projected private evaluator bindings are missing or readable.")
    if _hash(private) != spec.get("private_binding_sha256"):
        raise pilot.PilotStop("Projected private evaluator bindings changed.")
    projection = spec.get("build_input_projection") or {}
    if projection.get("source_sha256") != _hash(PROJECTION_PATH):
        raise pilot.PilotStop("Projection helper changed after projected preparation.")
    contract = spec.get("plan_contract") or {}
    if (
        contract.get("schema") != "selective-plan-contract/1"
        or contract.get("source_sha256") != _hash(PLAN_CONTRACT_PATH)
    ):
        raise pilot.PilotStop("Plan contract changed after projected preparation.")
    guard_policy = spec.get("guard_evidence_policy") or {}
    if (
        guard_policy.get("module") != "guiexp_android.selective_guard_evidence"
        or guard_policy.get("source_sha256") != _hash(GUARD_EVIDENCE_PATH)
    ):
        raise pilot.PilotStop("Guard evidence policy changed after projected preparation.")
    ax_parser = spec.get("ax_parser") or {}
    if (
        ax_parser.get("module") != "guiexp_android.selective_ax_parser"
        or ax_parser.get("source_sha256") != _hash(AX_PARSER_PATH)
    ):
        raise pilot.PilotStop("AX parser changed after projected preparation.")
    prefix_policy = spec.get("prefix_policy") or {}
    if (
        prefix_policy.get("module") != "guiexp_android.selective_prefix"
        or prefix_policy.get("source_sha256") != _hash(PREFIX_MODULE_PATH)
        or prefix_policy.get("schema") != PREFIX_SCHEMA
        or prefix_policy.get("handoff_policy") != HANDOFF_POLICY
        or prefix_policy.get("completion_policy") != "reactive_handoff"
    ):
        raise pilot.PilotStop("Prefix policy changed after projected preparation.")
    dense_policy = spec.get("dense_prefix_policy") or {}
    if (
        dense_policy.get("module") != "guiexp_android.selective_dense_prefix"
        or dense_policy.get("source_sha256") != _hash(DENSE_PREFIX_PATH)
        or dense_policy.get("schema") != DENSE_PREFIX_SCHEMA
        or dense_policy.get("method") != "maximal_leading_source_steps_1_to_k"
    ):
        raise pilot.PilotStop("Dense-prefix policy changed after projected preparation.")
    derived = spec.get("derived_provenance")
    if derived is not None:
        if not isinstance(derived, Mapping) or derived.get("derived") is not True:
            raise pilot.PilotStop("Derived prefix provenance is invalid.")
        source_response = derived.get("source_response_path")
        source_hash = derived.get("source_response_sha256")
        if (
            not isinstance(source_response, str)
            or not isinstance(source_hash, str)
            or not Path(source_response).is_file()
            or _hash(Path(source_response)) != source_hash
        ):
            raise pilot.PilotStop("Derived prefix source response changed or is unavailable.")
    if pilot.hash_json(_execution_manifest()) != spec.get("source_manifest_sha256"):
        raise pilot.PilotStop("Projected execution source changed after freezing.")
    budget = _budget(required=True)
    loader = getattr(budget, "load_phase_manifest", None)
    phases = spec.get("phase_manifests") or {}
    if phases and not callable(loader):
        raise BudgetStop("selective_explore_budget.load_phase_manifest() is required for projected execution.")
    for item in phases.values():
        path = out / str(item["path"])
        if not path.is_file() or _hash(path) != item.get("sha256"):
            raise BudgetStop("Projected phase manifest changed.")
        loader(path)
    return spec


def _build_demos(out: Path, spec: Mapping[str, Any], family: str) -> list[dict[str, Any]]:
    origin_path = out / str(spec.get("origin") or "origin.json")
    origin = _read(origin_path) if origin_path.is_file() else {"training": _training_snapshot(out)}
    demos: list[dict[str, Any]] = []
    for row in (origin.get("training", {}).get("families") or {}).get(family, []):
        if row.get("success") is not True:
            continue
        trace = Path(str(row.get("trajectory_absolute") or ""))
        frozen = row.get("trajectory_sha256_frozen")
        if not trace.is_file() or frozen is None or _hash(trace) != frozen:
            raise pilot.PilotStop("Readonly successful training trace changed or lacks a frozen hash.")
        steps: list[dict[str, Any]] = []
        try:
            lines = trace.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("record_type") == "step":
                    steps.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise pilot.PilotStop("Readonly successful training trace is invalid.") from exc
        if steps and all(
            isinstance(item.get("pre_obs"), Mapping) and isinstance(item.get("post_obs"), Mapping)
            for item in steps
        ):
            demos.append(
                {
                    "family": family,
                    "goal_text": row.get("goal_text", ""),
                    "steps": steps,
                    "source": "readonly_training",
                    "source_trajectory_sha256": frozen,
                }
            )
    return demos[:3]


def _derive_dense_prefix_from_source(
    source_out: Path,
    family: str,
    source_response: Path,
) -> dict[str, Any]:
    source_spec = _origin_spec(source_out)
    origin_ref = {"origin": str(source_spec.get("origin") or "origin.json")}
    full_demos = _build_demos(source_out, origin_ref, family)
    candidate, response_hash = load_saved_candidate(source_response)
    result = derive_dense_prefix(
        candidate,
        full_demos,
        source_reference={
            "source_response_path": str(source_response.resolve()),
            "source_version": source_spec.get("revision") or source_spec.get("version"),
            "source_family": family,
        },
        source_raw_sha256=response_hash,
    )
    if result.get("valid") is not True:
        errors = result.get("errors") or ["dense prefix derivation failed"]
        raise pilot.PlanInvalid(str(errors[0]))
    return result


def _install_derived_prefix(
    out: Path,
    spec: Mapping[str, Any],
    family: str,
    derived: Mapping[str, Any],
) -> dict[str, Any]:
    if _compilation_scope(spec.get("compilation_scope")) != "prefix":
        raise pilot.PilotStop("Derived dense prefixes require a prefix compilation spec.")
    if family not in tuple(spec.get("families") or ()):
        raise pilot.PilotStop(f"Derived prefix family {family} is not selected in the frozen spec.")
    prefix_response = derived.get("prefix_response")
    if not isinstance(prefix_response, Mapping) or prefix_response.get("valid") is not True:
        raise pilot.PlanInvalid("Derived dense prefix response is not valid.")
    plan = prefix_response.get("plan")
    if not isinstance(plan, Mapping):
        raise pilot.PlanInvalid("Derived dense prefix has no runtime plan.")
    checked_plan = _validate_generated_plan(plan)
    build_dir = out / "build" / family
    plan_path = out / spec["plans"][family]
    if plan_path.exists() or build_dir.exists() and any(build_dir.iterdir()):
        raise pilot.PilotStop("Derived prefix destination already contains build evidence; no overwrite is safe.")
    alignment = prefix_response.get("alignment")
    guard_report = prefix_response.get("guard_evidence")
    if not isinstance(alignment, Mapping) or not isinstance(guard_report, Mapping):
        raise pilot.PlanInvalid("Derived dense prefix lacks alignment or guard evidence.")
    build_dir.mkdir(parents=True, exist_ok=True)
    _write(plan_path, checked_plan)
    _write(build_dir / "alignment.json", alignment)
    _write(build_dir / "guard_evidence.json", guard_report)
    boundary = _prefix_boundary(
        plan=checked_plan,
        prefix_report=prefix_response,
        version=str(spec["revision"]),
        provider=str(spec["provider"]),
        provider_profile=str(spec["provider_profile"]),
    )
    _write(build_dir / "prefix_boundary.json", boundary)
    _write(build_dir / "dense_prefix_derivation.json", dict(derived))
    record = {
        "family": family,
        "status": "derived",
        "derived": True,
        "calls": 0,
        "repair": False,
        "compilation_scope": "prefix",
        "provider": spec["provider"],
        "provider_profile": spec["provider_profile"],
        "source_response_path": (derived.get("source_reference") or {}).get("source_response_path"),
        "source_response_sha256": derived.get("source_raw_sha256"),
        "source_raw_sha256": derived.get("source_raw_sha256"),
        "source_steps": derived.get("source_steps_retained"),
        "terminal_source_step": derived.get("terminal_source_step"),
        "cut_reason": derived.get("cut_reason"),
        "plan_sha256": pilot.hash_json(checked_plan),
        "alignment_path": "alignment.json",
        "alignment_sha256": _hash(build_dir / "alignment.json"),
        "guard_evidence_path": "guard_evidence.json",
        "guard_evidence_sha256": _hash(build_dir / "guard_evidence.json"),
        "guard_evidence_status": guard_report.get("status"),
        "prefix_boundary_path": "prefix_boundary.json",
        "prefix_boundary_sha256": _hash(build_dir / "prefix_boundary.json"),
    }
    _write(build_dir / "build.json", record)
    manifest = {
        "record_type": f"{spec['revision']}-build",
        "status": "complete",
        "derived_only": True,
        "profile": spec["builder_profile"],
        "provider": spec["provider"],
        "provider_profile": spec["provider_profile"],
        "compilation_scope": "prefix",
        "families": {family: record},
    }
    _write(out / "build_manifest.json", manifest)
    return record


def _existing_build_state(out: Path, spec: Mapping[str, Any], family: str) -> dict[str, Any] | None:
    version = str(spec.get("revision") or "projected")
    plan_path = out / spec["plans"][family]
    build_dir = out / "build" / family
    build_path = build_dir / "build.json"
    if plan_path.is_file():
        record = _read(build_path) if build_path.is_file() else None
        alignment_path = build_dir / "alignment.json"
        guard_path = build_dir / "guard_evidence.json"
        if not alignment_path.is_file() or not guard_path.is_file():
            raise pilot.PilotStop(
                f"{version} plan for {family} lacks immutable alignment or guard evidence; no reuse is safe."
            )
        if isinstance(record, Mapping):
            for path, key in ((alignment_path, "alignment_sha256"), (guard_path, "guard_evidence_sha256")):
                frozen = record.get(key)
                if frozen is not None and _hash(path) != frozen:
                    raise pilot.PilotStop(f"{version} guard evidence changed for {family}.")
        plan = _validate_generated_plan(_read(plan_path))
        if _compilation_scope(spec.get("compilation_scope")) == "prefix":
            boundary_path = build_dir / "prefix_boundary.json"
            if not boundary_path.is_file():
                raise pilot.PilotStop(
                    f"{version} prefix plan for {family} lacks its immutable boundary artifact."
                )
            _validate_prefix_boundary(
                boundary_path,
                plan,
                version=version,
                provider=str(spec["provider"]),
                provider_profile=str(spec["provider_profile"]),
            )
        return {"status": "already_built", "plan_sha256": pilot.hash_json(plan)}
    if build_path.is_file():
        record = _read(build_path)
        status = record.get("status")
        if status in {"budget_stopped", "provider_stopped", "interrupted", "unbuildable", "built"}:
            raise pilot.PilotStop(
                f"{version} build for {family} is terminal ({status}); use another version."
            )
        raise pilot.PilotStop(f"Existing {version} build state is not a recognized terminal result.")
    if any(build_dir.glob("*_response.json")):
        raise pilot.PilotStop(
            f"{version} build response evidence exists for {family} without terminal state; no resend is safe."
        )
    return None


def _validate_generated_plan(value: Any) -> dict[str, Any]:
    """Apply the frozen output contract before the shared pilot validator."""

    report = validate_contract_plan(value)
    if not isinstance(report, Mapping) or report.get("valid") is not True:
        errors = report.get("errors") if isinstance(report, Mapping) else None
        detail = errors[0] if isinstance(errors, list) and errors else "invalid plan"
        raise pilot.PlanInvalid(str(detail))
    return pilot.validate_plan(value)


def _save_build_call(
    path: Path,
    label: str,
    response: Any,
    usage: Mapping[str, Any],
    raw_text: str,
    projection: Mapping[str, Any],
    *,
    provider: str,
    provider_profile: str,
    version: str,
) -> None:
    pilot._save_build_call(path, label, response, usage, raw_text)
    record_path = path / f"{label}.json"
    record = _read(record_path)
    record.update(
        {
            "provider": provider,
            "provider_profile": provider_profile,
            "version": version,
            "projection": dict(projection),
        }
    )
    _write(record_path, record)


def _guard_failure_message(report: Mapping[str, Any]) -> str:
    """Reduce guard evidence to actionable, bounded repair feedback."""

    parts: list[str] = []
    for step in report.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        if step.get("status") == "valid" and not step.get("errors"):
            continue
        step_id = str(step.get("step_id") or step.get("plan_index") or "unknown")
        sources = ",".join(str(item) for item in (step.get("source_indices") or [])) or "none"
        errors = "; ".join(" ".join(str(error).split()) for error in (step.get("errors") or []))
        parts.append(f"{step_id}[source {sources}]={step.get('status', 'unknown')}: {errors or 'no detail'}")
    detail = " | ".join(parts) or "; ".join(" ".join(str(error).split()) for error in (report.get("errors") or []))
    return f"guard evidence status={report.get('status', 'unknown')}; {detail or 'no step detail'}"[:1800]


def _save_guard_attempt(
    build_dir: Path,
    label: str,
    report: Mapping[str, Any],
    *,
    provider: str,
    provider_profile: str,
    version: str,
) -> None:
    """Persist guard evidence before a failed attempt can trigger repair."""

    payload = {
        "record_type": "guard-evidence-attempt",
        "label": label,
        "version": version,
        "provider": provider,
        "provider_profile": provider_profile,
        "status": report.get("status"),
        "failure_summary": _guard_failure_message(report),
        "report": dict(report),
    }
    _write(build_dir / f"{label}.guard_evidence.json", payload)


def _checked_guard_evidence(
    candidate: Mapping[str, Any],
    full_demos: Sequence[Mapping[str, Any]],
    alignment: Mapping[str, Any],
    build_dir: Path,
    label: str,
    *,
    provider: str,
    provider_profile: str,
    version: str,
) -> dict[str, Any]:
    try:
        report = validate_guard_evidence(candidate, full_demos, alignment=alignment)
    except Exception as exc:
        report = {
            "status": "unknown",
            "steps": [],
            "source_indices": [],
            "errors": [f"guard evidence helper failed: {type(exc).__name__}: {str(exc).splitlines()[0][:400]}"],
        }
        _save_guard_attempt(
            build_dir,
            label,
            report,
            provider=provider,
            provider_profile=provider_profile,
            version=version,
        )
        raise pilot.PlanInvalid(_guard_failure_message(report)) from None
    _save_guard_attempt(
        build_dir,
        label,
        report,
        provider=provider,
        provider_profile=provider_profile,
        version=version,
    )
    if report.get("status") != "valid":
        raise pilot.PlanInvalid(_guard_failure_message(report))
    return report


def _build_messages(
    compilation_scope: str,
    family: str,
    projected_demos: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    if compilation_scope != "prefix":
        return build_messages(family, projected_demos)
    prefix_instruction = (
        "PREFIX MODE: compile only an explicitly verified deterministic prefix. Return the "
        "selective-prefix/1 wrapper with exact keys schema, plan, source_steps, "
        "terminal_source_step, and handoff_policy. source_steps maps each plan step ID to "
        "its positive one-based original training step in strict increasing order. "
        "terminal_source_step must equal the last covered source step. Set handoff_policy "
        "to reactive. The terminal source step is a verified prefix boundary, not a task "
        "success claim. Omit trailing actions whose postcondition is not visibly evidenced. "
        "After this prefix completes, the harness will hand the remaining original user "
        "request to the reactive agent with the same action budget. Preserve the complete "
        "task constraints and never infer a benchmark or filesystem effect. The nested plan "
        "must satisfy the exact plan contract below.\n\n"
    )
    contract_text = json.dumps(
        {
            "contract": plan_contract.CONTRACT,
            "fictional_valid_example": plan_contract.FICTIONAL_EXAMPLE,
        },
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
    user_text = (
        prefix_instruction
        + "Return one outer object with exact keys schema, plan, source_steps, "
        "terminal_source_step, and handoff_policy. The nested plan must use the "
        "selective-plan/1 grammar below. source_steps must map every explicit plan "
        "step ID to one positive one-based source training step in strict increasing "
        "order with no duplicates. Set handoff_policy to reactive.\n\n"
        "COMPLETE PLAN CONTRACT:\n"
        + contract_text
        + "\n\nPROJECTED DEMONSTRATIONS:\n"
        + json.dumps(
            plan_contract.projected_demo_payload(family, projected_demos),
            ensure_ascii=True,
            indent=2,
        )
    )
    return [
        {
            "role": "system",
            "content": "You produce a data-only guarded Android prefix and reactive handoff as strict JSON.",
        },
        {"role": "user", "content": user_text},
    ]


def _repair_messages(
    compilation_scope: str,
    original_messages: Sequence[Mapping[str, Any]],
    raw_response: str,
    validation_error: str,
) -> list[dict[str, str]]:
    messages = repair_messages(original_messages, raw_response, validation_error)
    if compilation_scope == "prefix":
        messages[-1]["content"] += (
            " Prefix mode remains binding: return the selective-prefix/1 wrapper, cover only "
            "steps with complete observed guards, set terminal_source_step to the final "
            "covered source step, and set handoff_policy to reactive."
        )
    return messages


def _prefix_boundary(
    *,
    plan: Mapping[str, Any],
    prefix_report: Mapping[str, Any],
    version: str,
    provider: str,
    provider_profile: str,
) -> dict[str, Any]:
    return {
        "schema": PREFIX_SCHEMA,
        "compilation_scope": "prefix",
        "version": version,
        "provider": provider,
        "provider_profile": provider_profile,
        "plan_sha256": pilot.hash_json(plan),
        "source_steps": dict(prefix_report["source_steps"]),
        "terminal_source_step": prefix_report["terminal_source_step"],
        "handoff_policy": HANDOFF_POLICY,
        "completion_policy": "reactive_handoff",
        "alignment": prefix_report["alignment"],
        "guard_evidence_status": (prefix_report.get("guard_evidence") or {}).get("status"),
    }


def _validate_prefix_boundary(
    path: Path,
    plan: Mapping[str, Any],
    *,
    version: str,
    provider: str,
    provider_profile: str,
) -> dict[str, Any]:
    record = _read(path)
    if (
        record.get("schema") != PREFIX_SCHEMA
        or record.get("compilation_scope") != "prefix"
        or record.get("version") != version
        or record.get("provider") != provider
        or record.get("provider_profile") != provider_profile
        or record.get("plan_sha256") != pilot.hash_json(plan)
        or record.get("handoff_policy") != HANDOFF_POLICY
        or record.get("completion_policy") != "reactive_handoff"
        or record.get("guard_evidence_status") != "valid"
    ):
        raise pilot.PilotStop("Projected prefix boundary artifact does not match the frozen plan.")
    wrapper = {
        "schema": PREFIX_SCHEMA,
        "plan": dict(plan),
        "source_steps": record.get("source_steps"),
        "terminal_source_step": record.get("terminal_source_step"),
        "handoff_policy": record.get("handoff_policy"),
    }
    checked = validate_prefix_response(wrapper)
    if checked.get("valid") is not True:
        raise pilot.PlanInvalid("Projected prefix boundary alignment is invalid.")
    return record


def build(
    out: Path | str = VERSION_OUT,
    *,
    max_families: int | None = None,
    env_file: Path | str | None = None,
    ledger: Any = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
    expected_version: str | None = None,
    expected_provider_profile: str | None = None,
    expected_compilation_scope: str | None = None,
) -> dict[str, Any]:
    """Build at most one initial response and one repair per family."""

    out = Path(out).resolve()
    spec = _load(
        out,
        expected_version=expected_version,
        expected_provider_profile=expected_provider_profile,
        expected_compilation_scope=expected_compilation_scope,
    )
    version = str(spec["revision"])
    provider_profile = str(spec["provider_profile"])
    provider = str(spec["provider"])
    builder_profile = dict(spec["builder_profile"])
    builder_profile_name = str(builder_profile["name"])
    selected_families = tuple(str(family) for family in spec["families"])
    compilation_scope = _compilation_scope(spec.get("compilation_scope"))
    budget = _budget()
    runtime = pilot._runtime_module(required=True)
    if ledger is None:
        factory = getattr(budget, "make_ledger", None)
        if not callable(factory):
            raise BudgetStop("selective_explore_budget.make_ledger() is required.")
        ledger = factory(authorization_path=spec["authorization_path"], host_guard=host_guard)
    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise BudgetStop("selective_explore_budget.validate_locks() is required.")
    builder_locks = _provider_bounds(spec.get("model_locks") or {})
    validator_kwargs: dict[str, Any] = {
        "provider_profile": provider_profile,
        "request_profile": builder_profile_name,
    }
    if metadata_fetcher is not None:
        validator_kwargs["metadata_fetcher"] = metadata_fetcher
    validator(builder_locks, **validator_kwargs)
    build_order = tuple(family for family in BUILD_FAMILIES if family in selected_families)
    selected = build_order if max_families is None else build_order[:max_families]
    status = "complete" if max_families is None or max_families >= len(build_order) else "pilot_limit"
    results: dict[str, Any] = {}
    with budget.exclusive_run(identity_path=out / "run_identity.json", mode="build"):
        for family in selected:
            build_dir = out / "build" / family
            build_dir.mkdir(parents=True, exist_ok=True)
            prior = _existing_build_state(out, spec, family)
            if prior is not None:
                results[family] = prior
                continue
            full_demos = _build_demos(out, spec, family)
            projected_demos = project_demonstrations(full_demos)
            projection = {
                "module": "guiexp_android.selective_evidence_projection",
                "source_sha256": _hash(PROJECTION_PATH),
                "full_demo_count": len(full_demos),
                "projected_demo_count": len(projected_demos),
                "guard_validation": "full_original_demonstrations",
                "compilation_scope": compilation_scope,
                "plan_contract": {
                    "module": "guiexp_android.selective_plan_contract",
                    "source_sha256": _hash(PLAN_CONTRACT_PATH),
                },
            }
            result: dict[str, Any] = {
                "family": family,
                "status": "unbuildable",
                "calls": 0,
                "repair": False,
                "provider": provider,
                "provider_profile": provider_profile,
                "projection": projection,
                "compilation_scope": compilation_scope,
            }
            if not full_demos:
                result["reason"] = "no successful readonly training trace"
                status = "partial"
                _write(build_dir / "build.json", result)
                results[family] = result
                continue
            factory = getattr(budget, "make_builder_client", None)
            if not callable(factory):
                raise BudgetStop("selective_explore_budget.make_builder_client() is required.")
            episode = f"{NAMESPACE}/{version}/build/{family}"
            client_kwargs: dict[str, Any] = {
                "ledger": ledger,
                "env_path": env_file,
                "profile": builder_profile_name,
                "provider_profile": provider_profile,
                "episode": episode,
            }
            if sdk is not None:
                client_kwargs["sdk"] = sdk
            if metadata_fetcher is not None:
                client_kwargs["metadata_fetcher"] = metadata_fetcher
            if host_guard is not None:
                client_kwargs["host_guard"] = host_guard
            client = factory(builder_locks, **client_kwargs)
            messages = _build_messages(compilation_scope, family, projected_demos)
            alignment: dict[str, Any] | None = None
            guard_report: dict[str, Any] | None = None
            try:
                response, usage = pilot._call_client(client, messages, episode)
                raw = pilot.response_content(response)
                _save_build_call(
                    build_dir,
                    "first_response",
                    response,
                    usage,
                    raw,
                    projection,
                    provider=provider,
                    provider_profile=provider_profile,
                    version=version,
                )
                result["calls"] = 1
                try:
                    if compilation_scope == "prefix":
                        compilation = validate_prefix_response(
                            pilot._json_from_text(raw), full_demos
                        )
                        if compilation.get("valid") is not True:
                            guard_report = compilation.get("guard_evidence")
                            if isinstance(guard_report, Mapping):
                                _save_guard_attempt(
                                    build_dir,
                                    "first_response",
                                    guard_report,
                                    provider=provider,
                                    provider_profile=provider_profile,
                                    version=version,
                                )
                                raise pilot.PlanInvalid(_guard_failure_message(guard_report))
                            errors = compilation.get("errors") or ["invalid prefix response"]
                            raise pilot.PlanInvalid(str(errors[0]))
                        candidate = _validate_generated_plan(compilation["plan"])
                        alignment = dict(compilation["alignment"])
                        guard_report = dict(compilation["guard_evidence"])
                        _save_guard_attempt(
                            build_dir,
                            "first_response",
                            guard_report,
                            provider=provider,
                            provider_profile=provider_profile,
                            version=version,
                        )
                    else:
                        compilation = validate_compilation_response(pilot._json_from_text(raw))
                        if compilation.get("valid") is not True:
                            errors = compilation.get("errors") or ["invalid compilation response"]
                            raise pilot.PlanInvalid(str(errors[0]))
                        candidate = _validate_generated_plan(compilation["plan"])
                        alignment = {
                            "steps": {
                                step_id: [{"source_step": source_step}]
                                for step_id, source_step in compilation["source_steps"].items()
                            }
                        }
                        guard_report = _checked_guard_evidence(
                            candidate,
                            full_demos,
                            alignment,
                            build_dir,
                            "first_response",
                            provider=provider,
                            provider_profile=provider_profile,
                            version=version,
                        )
                except Exception as first_error:
                    repair_exchange = _repair_messages(
                        compilation_scope, messages, raw, str(first_error)
                    )
                    repair_episode = episode + "/repair"
                    repair_response, repair_usage = pilot._call_client(
                        client, repair_exchange, repair_episode
                    )
                    repair_raw = pilot.response_content(repair_response)
                    _save_build_call(
                        build_dir,
                        "repair_response",
                        repair_response,
                        repair_usage,
                        repair_raw,
                        projection,
                        provider=provider,
                        provider_profile=provider_profile,
                        version=version,
                    )
                    result.update(calls=2, repair=True)
                    if compilation_scope == "prefix":
                        compilation = validate_prefix_response(
                            pilot._json_from_text(repair_raw), full_demos
                        )
                        if compilation.get("valid") is not True:
                            guard_report = compilation.get("guard_evidence")
                            if isinstance(guard_report, Mapping):
                                _save_guard_attempt(
                                    build_dir,
                                    "repair_response",
                                    guard_report,
                                    provider=provider,
                                    provider_profile=provider_profile,
                                    version=version,
                                )
                                raise pilot.PlanInvalid(_guard_failure_message(guard_report))
                            errors = compilation.get("errors") or ["invalid prefix response"]
                            raise pilot.PlanInvalid(str(errors[0]))
                        candidate = _validate_generated_plan(compilation["plan"])
                        alignment = dict(compilation["alignment"])
                        guard_report = dict(compilation["guard_evidence"])
                        _save_guard_attempt(
                            build_dir,
                            "repair_response",
                            guard_report,
                            provider=provider,
                            provider_profile=provider_profile,
                            version=version,
                        )
                    else:
                        compilation = validate_compilation_response(pilot._json_from_text(repair_raw))
                        if compilation.get("valid") is not True:
                            errors = compilation.get("errors") or ["invalid compilation response"]
                            raise pilot.PlanInvalid(str(errors[0]))
                        candidate = _validate_generated_plan(compilation["plan"])
                        alignment = {
                            "steps": {
                                step_id: [{"source_step": source_step}]
                                for step_id, source_step in compilation["source_steps"].items()
                            }
                        }
                        guard_report = _checked_guard_evidence(
                            candidate,
                            full_demos,
                            alignment,
                            build_dir,
                            "repair_response",
                            provider=provider,
                            provider_profile=provider_profile,
                            version=version,
                        )
                if alignment is None or guard_report is None:
                    raise pilot.PlanInvalid("compilation response evidence was not established")
                _write(build_dir / "alignment.json", alignment)
                _write(build_dir / "guard_evidence.json", guard_report)
                result.update(
                    compilation_scope=compilation_scope,
                    source_steps=compilation["source_steps"],
                    alignment_path="alignment.json",
                    alignment_sha256=_hash(build_dir / "alignment.json"),
                    guard_evidence_path="guard_evidence.json",
                    guard_evidence_sha256=_hash(build_dir / "guard_evidence.json"),
                    guard_evidence_status=guard_report.get("status"),
                )
                if compilation_scope == "prefix":
                    boundary = _prefix_boundary(
                        plan=candidate,
                        prefix_report=compilation,
                        version=version,
                        provider=provider,
                        provider_profile=provider_profile,
                    )
                    _write(build_dir / "prefix_boundary.json", boundary)
                    result.update(
                        prefix_boundary_path="prefix_boundary.json",
                        prefix_boundary_sha256=_hash(build_dir / "prefix_boundary.json"),
                        terminal_source_step=boundary["terminal_source_step"],
                        handoff_policy=boundary["handoff_policy"],
                    )
                report = runtime.validate_plan(candidate)
                if not isinstance(report, Mapping) or report.get("valid") is False:
                    raise pilot.PlanInvalid("selective_runtime rejected projected plan")
                if isinstance(report.get("plan"), Mapping):
                    candidate = dict(report["plan"])
                _write(out / spec["plans"][family], candidate)
                result.update(status="built", plan_sha256=pilot.hash_json(candidate))
            except BudgetStop as exc:
                result.update(
                    status="budget_stopped",
                    stop_reason=pilot._safe_budget_reason(exc),
                    error_type=type(exc).__name__,
                )
                status = "budget_stopped"
                _write(build_dir / "build.json", result)
                results[family] = result
                _write(
                    out / "build_manifest.json",
                    {
                        "record_type": f"{version}-build",
                        "status": status,
                        "profile": builder_profile,
                        "provider": provider,
                        "provider_profile": provider_profile,
                        "compilation_scope": compilation_scope,
                        "families": results,
                    },
                )
                raise
            except Exception as exc:
                result.update(
                    status="unbuildable",
                    error_type=type(exc).__name__,
                    error=pilot._safe_first_line(exc),
                    guard_attempts=[
                        str(path.relative_to(build_dir))
                        for path in sorted(build_dir.glob("*.guard_evidence.json"))
                    ],
                )
                status = "partial"
            _write(build_dir / "build.json", result)
            results[family] = result
    summary = {
        "record_type": f"{version}-build",
        "status": status,
        "profile": builder_profile,
        "provider": provider,
        "provider_profile": provider_profile,
        "compilation_scope": compilation_scope,
        "projection_source_sha256": _hash(PROJECTION_PATH),
        "families": results,
    }
    _write(out / "build_manifest.json", summary)
    return summary


def _private_map(out: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    data = _read(out / str(spec["private_bindings"]))
    return {
        f"{family}/b{index:02d}": dict(row)
        for family, rows in (data.get("families") or {}).items()
        for index, row in enumerate(rows, 1)
    }


def _progress_rows(
    out: Path,
    spec: Mapping[str, Any],
    current: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    provider = str(spec.get("provider") or "") or None
    observed = {str(item.get("id")): dict(item) for item in current}
    rows: list[dict[str, Any]] = []
    for row in spec.get("episodes") or []:
        path = out / "episodes" / row["id"] / "state.json"
        if path.is_file():
            try:
                state = _read(path)
            except pilot.PilotStop:
                state = {"status": "unreadable"}
            item = {
                "id": row["id"],
                "family": row.get("family"),
                "binding_id": row.get("binding_id"),
                "arm": row.get("arm"),
                "provider": row.get("provider", provider),
                "status": state.get("status"),
            }
            if state.get("status") == "done":
                final = state.get("result") or {}
                item.update(success=final.get("success"), actions=final.get("actions"))
            rows.append(item)
        else:
            rows.append(
                observed.get(
                    row["id"],
                    {
                        "id": row["id"],
                        "family": row.get("family"),
                        "binding_id": row.get("binding_id"),
                        "arm": row.get("arm"),
                        "provider": row.get("provider", provider),
                        "status": "pending",
                    },
                )
            )
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get("status") or "pending")
        counts[value] = counts.get(value, 0) + 1
    return rows, counts


def _ledger_has_episode(ledger: Any, episode_id: str) -> bool:
    checker = getattr(ledger, "has_episode", None)
    if not callable(checker):
        raise BudgetStop("Projected ledger cannot verify prior episode receipts.")
    try:
        return bool(checker(episode_id))
    except BudgetStop:
        raise
    except Exception as exc:
        raise BudgetStop("Projected ledger receipt lookup failed; no UI replay is safe.") from exc


def _mark_prior_terminal(
    path: Path,
    row: Mapping[str, Any],
    status: str,
    reason: str,
    *,
    provider: str,
    provider_profile: str,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "record_type": "episode-state",
        "status": status,
        "episode_id": row["id"],
        "family": row.get("family"),
        "arm": row.get("arm"),
        "provider": provider,
        "provider_profile": provider_profile,
        "reason": reason,
        "ended_unix": time.time(),
    }
    _write(path, value, private=True)
    return value


def run(
    out: Path | str = VERSION_OUT,
    *,
    max_episodes: int | None = None,
    env_file: Path | str | None = None,
    ledger: Any = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
    expected_version: str | None = None,
    expected_provider_profile: str | None = None,
    expected_compilation_scope: str | None = None,
) -> dict[str, Any]:
    """Serve only rows whose family has a validated projected plan."""

    out = Path(out).resolve()
    spec = _load(
        out,
        expected_version=expected_version,
        expected_provider_profile=expected_provider_profile,
        expected_compilation_scope=expected_compilation_scope,
    )
    version = str(spec["revision"])
    provider_profile = str(spec["provider_profile"])
    provider = str(spec["provider"])
    serving_profile = dict(spec["serving_profile"])
    serving_profile_name = str(serving_profile["name"])
    selected_families = tuple(str(family) for family in spec["families"])
    compilation_scope = _compilation_scope(spec.get("compilation_scope"))
    budget = _budget()
    plans: dict[str, dict[str, Any] | None] = {}
    for family in selected_families:
        path = out / spec["plans"][family]
        if not path.is_file():
            plans[family] = None
            continue
        try:
            plans[family] = _validate_generated_plan(_read(path))
        except Exception:
            plans[family] = None
    build_order = tuple(family for family in BUILD_FAMILIES if family in selected_families)
    available = tuple(family for family in build_order if plans.get(family) is not None)
    if ledger is None:
        factory = getattr(budget, "make_ledger", None)
        if not callable(factory):
            raise BudgetStop("selective_explore_budget.make_ledger() is required for serving.")
        ledger = factory(authorization_path=spec["authorization_path"], host_guard=host_guard)
    if not available:
        rows, counts = _progress_rows(out, spec, [])
        result = {
            "record_type": f"{version}-progress",
            "batch_status": "no_plans",
            "planned": len(rows),
            "completed_in_invocation": 0,
            "counts": counts,
            "episodes": rows,
            "provider": provider,
            "provider_profile": provider_profile,
            "stop_reason": f"No executable {version} plan; no reactive serving started.",
        }
        _write(out / "progress.json", result)
        return result
    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise BudgetStop("selective_explore_budget.validate_locks() is required for serving.")
    validator_kwargs: dict[str, Any] = {
        "provider_profile": provider_profile,
        "request_profile": serving_profile_name,
    }
    if metadata_fetcher is not None:
        validator_kwargs["metadata_fetcher"] = metadata_fetcher
    validator(spec.get("model_locks") or {}, **validator_kwargs)
    private = _private_map(out, spec)
    done = 0
    progress: list[dict[str, Any]] = []
    batch_status = "complete"
    requested_limit_fulfilled = False
    with budget.exclusive_run(identity_path=out / "run_identity.json", mode="run"):
        for row in spec.get("episodes") or []:
            if row.get("family") not in available:
                continue
            state_path = out / "episodes" / row["id"] / "state.json"
            if state_path.is_file():
                state = _read(state_path)
                state_status = str(state.get("status") or "unknown")
                if state_status in TERMINAL:
                    progress.append({"id": row["id"], "status": state_status, "provider": provider})
                    continue
                trajectory = state_path.parent / "trajectory.jsonl"
                if state_status == "running" or (trajectory.is_file() and trajectory.stat().st_size):
                    _mark_prior_terminal(
                        state_path,
                        row,
                        "interrupted",
                        "prior running or action evidence is terminal; raw evidence retained and never replayed",
                        provider=provider,
                        provider_profile=provider_profile,
                    )
                    progress.append({"id": row["id"], "status": "interrupted", "provider": provider})
                    continue
                _mark_prior_terminal(
                    state_path,
                    row,
                    "interrupted",
                    "prior episode state is not pending; no replay is safe",
                    provider=provider,
                    provider_profile=provider_profile,
                )
                progress.append({"id": row["id"], "status": "interrupted", "provider": provider})
                continue
            if _ledger_has_episode(ledger, str(row["id"])):
                _mark_prior_terminal(
                    state_path,
                    row,
                    "prior_receipt",
                    "receipt already exists; no UI replay",
                    provider=provider,
                    provider_profile=provider_profile,
                )
                progress.append({"id": row["id"], "status": "prior_receipt", "provider": provider})
                continue
            if max_episodes is not None and done >= max_episodes:
                batch_status = "pilot_limit"
                requested_limit_fulfilled = True
                break
            family = str(row["family"])
            binding = private.get(f"{family}/{row['binding_id']}")
            if binding is None:
                raise pilot.PilotStop(f"Private binding {family}/{row['binding_id']} is missing.")
            client_kwargs: dict[str, Any] = {
                "ledger": ledger,
                "env_path": env_file,
                "profile": serving_profile_name,
                "provider_profile": provider_profile,
                "episode": row["id"],
            }
            if sdk is not None:
                client_kwargs["sdk"] = sdk
            if metadata_fetcher is not None:
                client_kwargs["metadata_fetcher"] = metadata_fetcher
            if host_guard is not None:
                client_kwargs["host_guard"] = host_guard
            client = budget.make_client(spec.get("model_locks") or {}, **client_kwargs)
            env = pilot._new_env()
            try:
                episode_kwargs: dict[str, Any] = {
                    "env": env,
                    "client": client,
                    "budget": budget,
                    "plan": plans[family],
                }
                if compilation_scope == "prefix":
                    episode_kwargs["completion_policy"] = "reactive_handoff"
                final = pilot._run_one_episode(out, spec, row, binding, **episode_kwargs)
                state_path = out / "episodes" / row["id"] / "state.json"
                if state_path.is_file():
                    state = _read(state_path)
                    state["provider"] = provider
                    state["provider_profile"] = provider_profile
                    if isinstance(state.get("result"), Mapping):
                        state["result"] = dict(state["result"], provider=provider, provider_profile=provider_profile)
                    _write(state_path, state, private=True)
                progress.append(
                    {
                        "id": row["id"],
                        "status": "done",
                        "success": final.get("success"),
                        "provider": provider,
                    }
                )
            except BudgetStop as exc:
                batch_status = "budget_stopped"
                progress.append(
                    {
                        "id": row["id"],
                        "status": "budget_stopped",
                        "stop_reason": pilot._safe_budget_reason(exc),
                        "error_type": type(exc).__name__,
                        "provider": provider,
                    }
                )
                break
            except pilot.PilotStop as exc:
                batch_status = "interrupted"
                progress.append(
                    {
                        "id": row["id"],
                        "status": "interrupted",
                        "stop_reason": pilot._safe_first_line(exc),
                        "provider": provider,
                    }
                )
                break
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            done += 1
    rows, counts = _progress_rows(out, spec, progress)
    if batch_status == "complete" and max_episodes is not None and done >= max_episodes:
        batch_status = "pilot_limit"
        requested_limit_fulfilled = True
    elif batch_status == "complete" and counts.get("pending", 0):
        batch_status = "incomplete"
    result = {
        "record_type": f"{version}-progress",
        "batch_status": batch_status,
        "planned": len(rows),
        "completed_in_invocation": done,
        "counts": counts,
        "episodes": rows,
        "provider": provider,
        "provider_profile": provider_profile,
        "requested_limit": max_episodes,
        "requested_limit_fulfilled": requested_limit_fulfilled,
        "pending_deferred": counts.get("pending", 0),
    }
    _write(out / "progress.json", result)
    return result


def _annotate_report(
    report: Mapping[str, Any],
    *,
    version: str,
    provider: str,
    provider_profile: str,
    origin_provider: str = "relace",
    compilation_scope: str = DEFAULT_COMPILATION_SCOPE,
    prefix_handoff_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = json.loads(json.dumps(dict(report)))
    result["version"] = version
    result["compilation_scope"] = compilation_scope
    result["provider_profile"] = provider_profile
    result["serving_provider"] = provider
    result["provider_attribution"] = {
        "version_build": provider,
        "version_serving": provider,
        "shared_training": origin_provider,
        "imported_serving": origin_provider,
        "unknown_or_unattributed": None,
    }
    result["prefix_handoff"] = dict(
        prefix_handoff_summary
        or {
            "enabled": compilation_scope == "prefix",
            "completion_policy": "reactive_handoff" if compilation_scope == "prefix" else None,
            "prefix_verified_marker_count": None,
            "reactive_handoff_model_call_count": None,
        }
    )
    for entry in result.get("serving_episodes", []) or []:
        if isinstance(entry, Mapping):
            entry["provider"] = origin_provider if entry.get("provenance") == "imported" else provider
            entry["provider_profile"] = "origin" if entry.get("provenance") == "imported" else provider_profile
    for receipt in result.get("receipts", []) or []:
        if isinstance(receipt, Mapping):
            scope = receipt.get("scope")
            receipt["provider"] = (
                provider
                if scope in {"version_build", "version_serving"}
                else origin_provider
                if scope in {"shared_training", "imported_serving", "development_build"}
                else None
            )
            receipt["provider_profile"] = provider_profile if receipt["provider"] == provider else "origin" if receipt["provider"] else None
    for key in ("version_build_costs", "development_build_costs"):
        section = result.get(key)
        if isinstance(section, Mapping):
            for family in (section.get("families") or {}).values():
                if isinstance(family, Mapping):
                    for response in family.get("responses") or []:
                        if isinstance(response, Mapping):
                            response["provider"] = provider if key == "version_build_costs" else origin_provider
                            response["provider_profile"] = provider_profile if key == "version_build_costs" else "origin"
    return result


def _prefix_handoff_summary(out: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    scope = _compilation_scope(spec.get("compilation_scope"))
    if scope != "prefix":
        return {
            "enabled": False,
            "completion_policy": None,
            "prefix_verified_marker_count": 0,
            "reactive_handoff_model_call_count": 0,
            "trace_missing_count": 0,
        }
    markers = 0
    handoff_calls = 0
    missing = 0
    for row in spec.get("episodes") or []:
        path = out / "episodes" / str(row.get("id")) / "trajectory.jsonl"
        if not path.is_file():
            missing += 1
            continue
        try:
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, UnicodeError, json.JSONDecodeError):
            missing += 1
            continue
        markers += sum(record.get("record_type") == "prefix_verified" for record in records if isinstance(record, Mapping))
        handoff_calls += sum(
            record.get("record_type") == "model_call" and record.get("purpose") == "reactive_handoff"
            for record in records
            if isinstance(record, Mapping)
        )
    return {
        "enabled": True,
        "completion_policy": "reactive_handoff",
        "prefix_verified_marker_count": markers,
        "reactive_handoff_model_call_count": handoff_calls,
        "trace_missing_count": missing,
    }


def analyze(
    out: Path | str = VERSION_OUT,
    *,
    expected_version: str | None = None,
    expected_provider_profile: str | None = None,
    expected_compilation_scope: str | None = None,
) -> dict[str, Any]:
    out = Path(out).resolve()
    spec = _load(
        out,
        expected_version=expected_version,
        expected_provider_profile=expected_provider_profile,
        expected_compilation_scope=expected_compilation_scope,
    )
    version = str(spec["revision"])
    provider = str(spec["provider"])
    provider_profile = str(spec["provider_profile"])
    compilation_scope = _compilation_scope(spec.get("compilation_scope"))
    origin_provider = str((spec.get("provenance") or {}).get("origin_provider") or "relace")
    try:
        module = importlib.import_module(".selective_analyze", __package__)
    except (ImportError, ModuleNotFoundError):
        return {"record_type": f"{version}-analysis", "status": "analyzer_unavailable", "version": version}
    report = _annotate_report(
        module.analyze(out, spec),
        version=version,
        provider=provider,
        provider_profile=provider_profile,
        origin_provider=origin_provider,
        compilation_scope=compilation_scope,
        prefix_handoff_summary=_prefix_handoff_summary(out, spec),
    )
    _write(out / "analysis.json", report)
    return report


def derive_prefix_version(
    origin: Path | str,
    out: Path | str,
    source_response: Path | str,
    family: str,
    *,
    version: str | None = None,
    provider_profile: str | None = None,
) -> dict[str, Any]:
    """Prepare and install a derived dense prefix without a paid build call."""

    version = _safe_version(version or Path(out).name)
    provider_profile = str(provider_profile or DEFAULT_PROVIDER_PROFILE)
    origin_path = Path(origin).resolve()
    source_response_path = Path(source_response).resolve()
    derived = _derive_dense_prefix_from_source(origin_path, family, source_response_path)
    source_reference = dict(derived.get("source_reference") or {})
    provenance = {
        "derived": True,
        "schema": DENSE_PREFIX_SCHEMA,
        "source_response_path": source_reference.get("source_response_path"),
        "source_response_sha256": derived.get("source_raw_sha256"),
        "source_version": source_reference.get("source_version"),
        "source_family": family,
        "source_plan_sha256": source_reference.get("source_plan_sha256"),
        "source_steps_original": derived.get("source_steps_original"),
        "source_steps_retained": derived.get("source_steps_retained"),
        "terminal_source_step": derived.get("terminal_source_step"),
        "cut_reason": derived.get("cut_reason"),
        "derivation_module": "guiexp_android.selective_dense_prefix",
        "derivation_module_sha256": _hash(DENSE_PREFIX_PATH),
    }
    spec = prepare(
        origin_path,
        Path(out).resolve(),
        version=version,
        provider_profile=provider_profile,
        families=(family,),
        compilation_scope="prefix",
        derived_provenance=provenance,
    )
    record = _install_derived_prefix(Path(out).resolve(), spec, family, derived)
    return {
        "status": "derived",
        "version": version,
        "family": family,
        "spec_sha256": spec["spec_sha256"],
        "build": record,
        "source_response_sha256": derived.get("source_raw_sha256"),
        "cut_reason": derived.get("cut_reason"),
        "terminal_source_step": derived.get("terminal_source_step"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--derive-prefix", type=Path)
    group.add_argument("--build", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--analyze", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--origin", type=Path, default=pilot.DEFAULT_OUT)
    parser.add_argument("--version", type=str, default=None)
    parser.add_argument("--provider-profile", choices=PROVIDER_PROFILE_CHOICES, default=None)
    parser.add_argument("--families", nargs="+", choices=pilot.FAMILIES, default=None)
    parser.add_argument("--family", choices=pilot.FAMILIES, default=None)
    parser.add_argument("--compilation-scope", choices=COMPILATION_SCOPES, default=None)
    parser.add_argument("--max-families", type=int)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.max_families is not None and args.max_families < 1:
        parser.error("--max-families must be positive")
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    if args.families is not None and not args.prepare:
        parser.error("--families is supported only for preparation")
    if args.compilation_scope is not None and not (args.prepare or args.derive_prefix is not None):
        parser.error("--compilation-scope is supported only for preparation")
    if args.family is not None and args.derive_prefix is None:
        parser.error("--family is supported only for --derive-prefix")
    if args.derive_prefix is not None:
        if args.family is None:
            parser.error("--derive-prefix requires --family")
        if args.families is not None and tuple(args.families) != (args.family,):
            parser.error("--derive-prefix accepts only its --family selection")
        if args.compilation_scope not in (None, "prefix"):
            parser.error("--derive-prefix requires prefix compilation scope")
    version = _safe_version(args.version or (args.out.name if args.out is not None else DEFAULT_VERSION))
    out = (_version_out(version) if args.out is None else args.out).resolve()
    _check_out_version(out, version)
    try:
        if args.derive_prefix is not None:
            result = derive_prefix_version(
                args.origin,
                out,
                args.derive_prefix,
                args.family,
                version=version,
                provider_profile=args.provider_profile or DEFAULT_PROVIDER_PROFILE,
            )
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.prepare:
            spec = prepare(
                origin=args.origin,
                out=out,
                version=version,
                provider_profile=args.provider_profile or DEFAULT_PROVIDER_PROFILE,
                families=args.families,
                compilation_scope=args.compilation_scope or DEFAULT_COMPILATION_SCOPE,
            )
            print(json.dumps({"status": "prepared", "spec_sha256": spec["spec_sha256"]}, indent=2))
            return 0
        if args.build:
            result = build(
                out,
                max_families=args.max_families,
                env_file=args.env_file,
                expected_version=version if args.version is not None else None,
                expected_provider_profile=args.provider_profile,
                expected_compilation_scope=args.compilation_scope,
            )
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if result.get("status") in {"complete", "pilot_limit"} else 3
        if args.run:
            result = run(
                out,
                max_episodes=args.max_episodes,
                env_file=args.env_file,
                expected_version=version if args.version is not None else None,
                expected_provider_profile=args.provider_profile,
                expected_compilation_scope=args.compilation_scope,
            )
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if result.get("batch_status") in {"complete", "pilot_limit"} else 2
        result = analyze(
            out,
            expected_version=version if args.version is not None else None,
            expected_provider_profile=args.provider_profile,
            expected_compilation_scope=args.compilation_scope,
        )
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if result.get("status") not in {"analyzer_unavailable", "incomplete"} else 2
    except (pilot.PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {pilot._safe_budget_reason(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
