"""A small guarded-plan feasibility pilot for Android GUI tasks.

The pilot intentionally keeps the compiled artifact data-only.  A plan is a
JSON object containing slot descriptions, observable selectors, and native
Android JSON actions.  It is interpreted by :mod:`selective_runtime`, rather
than loaded as Python source.  This file owns the experiment protocol and
durable records.  Device and budget primitives are imported lazily so the
offline preparation and unit tests do not need an emulator or credentials.

The command line stages are deliberately explicit::

    python -m guiexp_android.selective_pilot --prepare
    python -m guiexp_android.selective_pilot --train
    python -m guiexp_android.selective_pilot --build
    python -m guiexp_android.selective_pilot --run
    python -m guiexp_android.selective_pilot --analyze

``--train`` is needed when the historical successful discover traces do not
carry complete pre/post observations.  It captures at most two fresh
training episodes per family and uses the first successful one (up to three
successful historical demonstrations are accepted when they have complete
observable evidence).  No test binding is sent to a builder call.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import fcntl
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from .budget_client import BudgetStop
from .selective_recovery_context import build_context
from .selective_recovery_context import (
    validate_policy as validate_recovery_context_policy,
)

PLAN_SCHEMA = "selective-plan/1"
SPEC_SCHEMA = "android-selective-pilot/1"
MODEL = "z-ai/glm-5.3-flash"
PROVIDER = "relace"
NAMESPACE = "selective_20260915"
MAX_ACTIONS = 40
MAX_LOCAL_ASSISTANCE = 3
MAX_TRAIN_ATTEMPTS = 2
TRAIN_SEED_START = 915101
TEST_SEED_START = 915201
TEST_BINDING_COUNT = 2
OBS_MODE = "screenshot+ax"
TEMPERATURE = 0.0
MAX_COMPLETION_TOKENS = 4096

FAMILIES = ("MarkorCreateNote", "FilesMoveFile", "MarkorDeleteNote")
# Active task fields define the program binding.  Setup-only noise pools are
# retained in params_sha256 but never distinguish the serving binding.
SEMANTIC_BINDING_FIELDS = {
    "MarkorCreateNote": ("file_name", "text"),
    "FilesMoveFile": ("file_name", "source_folder", "destination_folder"),
    "MarkorDeleteNote": ("file_name",),
}
ARMS = ("reactive", "full_fallback", "local_rejoin")
ALLOWED_TRANSFORMS = frozenset(("identity", "stem", "suffix", "first_word", "last_word"))
VALUE_SPEC_KEYS = frozenset(("slot", "transform", "prefix", "suffix"))
VALUE_SPEC_REQUIRED_KEYS = frozenset(("slot", "transform"))
ALLOWED_ACTION_TYPES = frozenset(
    (
        "click",
        "long_press",
        "input_text",
        "keyboard_enter",
        "navigate_home",
        "navigate_back",
        "open_app",
        "scroll",
        "wait",
    )
)

# Selectors are intentionally a small data language.  The runtime owns their
# exact matching semantics.  These keys mirror ProgramDevice's plain a11y
# dictionaries.
SELECTOR_KEYS = frozenset(
    (
        "text",
        "contains",
        "hint",
        "description",
        "clickable",
        "editable",
    )
)

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]
DEFAULT_OUT = REPO_ROOT / "experimental-results" / "guiexp_android" / NAMESPACE
HISTORICAL_ROOT = REPO_ROOT / "experimental-results" / "guiexp_android" / "revision_20260913"
HISTORICAL_T16 = REPO_ROOT / "experimental-results" / "guiexp_android" / "t16_build"
SHARED_LEDGER = HISTORICAL_ROOT / "budget.sqlite3"
SHARED_RUN_LOCK = HISTORICAL_ROOT / "run.lock"


class PilotStop(RuntimeError):
    """A fail-closed protocol stop before another physical action or call."""


class PlanInvalid(ValueError):
    """The model response is not a valid data-only selective plan."""


class ActionBudgetStop(PilotStop):
    """The shared per-episode UI-action cap has been reached."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path | str) -> str:
    path = Path(path)
    return sha256_bytes(path.read_bytes())


def hash_json(value: Any) -> str:
    return sha256_bytes(canonical(value).encode("utf-8"))


def _atomic_write(path: Path | str, data: bytes, mode: int | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if mode is not None:
        try:
            path.chmod(mode)
        except OSError:
            pass


def atomic_json(path: Path | str, value: Any, private: bool = False) -> None:
    _atomic_write(
        path,
        (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
        0o600 if private else None,
    )


def append_jsonl(path: Path | str, value: Any, private: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if private:
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _json_read(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _path_rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _out_rel(out: Path, path: Path) -> str:
    return str(Path(path).resolve().relative_to(Path(out).resolve()))


def _selective_receipts_exist(path: Path = SHARED_LEDGER) -> bool:
    """Check the shared ledger read-only before amending a no-receipt draft."""

    if not Path(path).is_file():
        return False
    try:
        with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=2) as database:
            row = database.execute(
                "SELECT 1 FROM calls WHERE id LIKE ? OR episode LIKE ? LIMIT 1",
                (NAMESPACE + "/%", NAMESPACE + "/%"),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        raise PilotStop("Cannot verify the shared ledger before amending a draft.") from None


def _preserve_preflight_draft(out: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{time.time_ns() % 1000000:06d}"
    destination = out / "preflight_drafts" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for relative in (
        Path("spec.json"),
        Path("manifest.json"),
        Path("historical_inventory.json"),
        Path("private/evaluator_bindings.json"),
    ):
        source = out / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(THIS_FILE, destination / "selective_pilot.py")
    atomic_json(
        destination / "draft_manifest.json",
        {
            "record_type": "preflight-draft-preservation",
            "source_spec": str(out / "spec.json"),
            "current_pilot_sha256": sha256_file(THIS_FILE),
            "preserved_unix": time.time(),
        },
    )
    return destination


def _safe_first_line(exc: BaseException, limit: int = 240) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:limit]}"


def _safe_budget_reason(exc: BaseException, limit: int = 240) -> str:
    """Return the exact safe stop reason carried by the budget guard."""

    lines = str(exc).splitlines()
    message = (lines[0] if lines else "").strip()[:limit]
    return message or type(exc).__name__


def source_manifest(root: Path | str = REPO_ROOT) -> dict[str, Any]:
    """Hash protocol code and execution dependencies for the frozen spec.

    Missing optional runtime/budget modules are recorded explicitly.  They are
    required by ``--build`` and ``--run`` but allowing preparation to describe
    a pending capture makes offline setup inspectable before those modules are
    present.
    """

    root = Path(root).resolve()
    package_root = root / "computer-use" / "guiexp_android"
    candidates = {
        "selective_pilot.py": THIS_FILE,
        "selective_recovery_context.py": package_root / "selective_recovery_context.py",
        "selective_runtime.py": package_root / "selective_runtime.py",
        "selective_budget.py": package_root / "selective_budget.py",
        "selective_supervisor.py": package_root / "selective_supervisor.py",
        "agent.py": package_root / "agent.py",
        "actions.py": package_root / "actions.py",
        "android_env.py": package_root / "android_env.py",
        "program_runtime.py": package_root / "program_runtime.py",
        "conditions.py": package_root / "conditions.py",
        "forksafe.py": package_root / "forksafe.py",
        "android_world/contacts.py": root / "third-party" / "android_world" / "android_world" / "task_evals" / "single" / "contacts.py",
        "android_world/calendar.py": root / "third-party" / "android_world" / "android_world" / "task_evals" / "single" / "calendar" / "calendar.py",
        "android_world/markor.py": root / "third-party" / "android_world" / "android_world" / "task_evals" / "single" / "markor.py",
        "android_world/files.py": root / "third-party" / "android_world" / "android_world" / "task_evals" / "single" / "files.py",
    }
    result: dict[str, Any] = {}
    for name, path in candidates.items():
        path = Path(path)
        result[name] = {
            "path": _path_rel(path),
            "exists": path.is_file(),
            "sha256": sha256_file(path) if path.is_file() else None,
            "size": path.stat().st_size if path.is_file() else None,
        }
    # The budget facade freezes the complete v9 accounting stack.  Include
    # its dependency hashes and runtime metadata in the same digest so a
    # later run cannot silently mix versions.
    try:
        budget = importlib.import_module(".selective_budget", __package__)
    except (ImportError, ModuleNotFoundError):
        budget = None
    if budget is not None:
        source_hashes = getattr(budget, "source_hashes", None)
        result["budget_source_sha256"] = source_hashes() if callable(source_hashes) else None
        runtime_manifest = getattr(budget, "runtime_manifest", None)
        result["budget_runtime_manifest"] = runtime_manifest() if callable(runtime_manifest) else None
    return result


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    return hash_json(manifest)


def _private_file(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        if path.exists():
            path.chmod(0o600)
    except OSError:
        pass


def _test_params(family: str, seed: int) -> dict[str, Any]:
    """Resolve a private task binding through the canonical Android generator."""

    try:
        module = importlib.import_module(".android_env", __package__)
        return dict(module.instance_params(family, int(seed)))
    except Exception as exc:  # pragma: no cover - only exercised without AW
        raise PilotStop(f"Cannot generate private {family} binding for seed {seed}.") from exc


def binding_hashes(binding: Mapping[str, Any], family: str | None = None) -> dict[str, str]:
    """Hash the full task fixture and its active goal binding separately."""

    params = dict(binding)
    params_digest = hash_json(params)
    if family is None:
        keys = set(params)
        if {"source_folder", "destination_folder"} <= keys:
            family = "FilesMoveFile"
        elif "noise_candidates" in keys:
            family = "MarkorDeleteNote"
        elif {"file_name", "text"} <= keys:
            family = "MarkorCreateNote"
    fields = SEMANTIC_BINDING_FIELDS.get(family or "")
    semantic_fields = fields if fields and all(field in params for field in fields) else tuple(params)
    semantic = {field: params[field] for field in semantic_fields if field in params}
    return {"params_sha256": params_digest, "binding_sha256": hash_json(semantic)}


def _raw_value_candidates(value: Any, key: str = "") -> Iterator[str]:
    """Yield likely binding literals from old JSON/JSONL records.

    This is deliberately conservative about what becomes an exclusion.  The
    old audit's cryptographic hashes are also loaded, so a candidate is
    rejected even when a prior file contains no parseable raw value.
    """

    key_lower = key.lower()
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            yield from _raw_value_candidates(child_value, str(child_key))
        return
    if isinstance(value, list):
        for child in value:
            yield from _raw_value_candidates(child, key)
        return
    if isinstance(value, (str, int, float)) and key_lower in {
        "file_name",
        "filename",
        "name",
        "number",
        "text",
        "event_title",
        "event_description",
        "source_folder",
        "destination_folder",
        "source_file",
        "destination_file",
        "year",
        "month",
        "day",
        "hour",
        "duration_mins",
        "title",
        "description",
    }:
        text = str(value).strip()
        if text:
            yield text


def _walk_json_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}:
            yield path


def historical_inventory(
    historical_root: Path | str = HISTORICAL_ROOT,
    t16_root: Path | str = HISTORICAL_T16,
) -> dict[str, Any]:
    """Return old binding hashes/literals without changing old artifacts."""

    roots = [Path(historical_root), Path(t16_root)]
    hash_values: set[str] = set()
    historical_binding_hashes: set[str] = set()
    raw_values: set[str] = set()
    file_hashes: dict[str, str] = {}
    parsed_files = 0
    for root in roots:
        for path in _walk_json_files(root):
            try:
                raw = path.read_bytes()
                file_hashes[_path_rel(path)] = sha256_bytes(raw)
                if path.name == "binding_exclusion_audit.json":
                    data = json.loads(raw)
                    for section in (data.get("planned") or {}).values():
                        for item in (section.get("bindings") or {}).values():
                            for key in ("params_sha256", "binding_sha256"):
                                if isinstance(item.get(key), str):
                                    hash_values.add(item[key])
                if path.suffix.lower() == ".jsonl":
                    data_items = []
                    for line in raw.decode("utf-8", errors="replace").splitlines():
                        try:
                            data_items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                    data: Any = data_items
                else:
                    data = json.loads(raw)
                raw_values.update(_raw_value_candidates(data))
                # Exploration indexes record the family and seed.  Recreate
                # only the canonical parameter draw to obtain an exact
                # binding hash for exclusion.  This is read-only and does not
                # touch the emulator or replay a UI episode.
                if path.name == "exploration.json" and isinstance(data, Mapping):
                    family = data.get("family")
                    if family in FAMILIES:
                        raw_instances = data.get("instances") or {}
                        instances = raw_instances.values() if isinstance(raw_instances, Mapping) else raw_instances if isinstance(raw_instances, list) else []
                        for instance in instances:
                            if not isinstance(instance, Mapping):
                                continue
                            try:
                                seed = int(instance.get("seed"))
                                generated = _test_params(family, seed)
                                generated_hashes = binding_hashes(generated, family)
                                historical_binding_hashes.add(generated_hashes["binding_sha256"])
                                hash_values.add(generated_hashes["params_sha256"])
                            except (TypeError, ValueError, PilotStop):
                                continue
                parsed_files += 1
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
    return {
        "roots": [_path_rel(path) for path in roots],
        "files": file_hashes,
        "parsed_file_count": parsed_files,
        "historical_hashes": sorted(hash_values),
        "historical_binding_hashes": sorted(historical_binding_hashes),
        "raw_value_sha256": sorted(hash_json(v) for v in raw_values),
        "raw_value_count": len(raw_values),
        "record_type": "historical-binding-inventory",
    }


def _is_old_binding(params: Mapping[str, Any], inventory: Mapping[str, Any]) -> bool:
    hashes = binding_hashes(params)
    blocked = set(inventory.get("historical_hashes") or ()) | set(inventory.get("historical_binding_hashes") or ())
    if hashes["params_sha256"] in blocked or hashes["binding_sha256"] in blocked:
        return True
    # Individual field values are intentionally not exclusion keys.  Folder
    # names and stock note text recur across valid historical bindings.  The
    # exact canonical binding hash above is the unit that must be disjoint.
    return False


def choose_fresh_bindings(
    family: str,
    count: int = TEST_BINDING_COUNT,
    start: int = TEST_SEED_START,
    inventory: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Choose deterministic, disjoint seed bindings for the evaluator.

    The returned values are private preparation material.  Public spec rows
    contain only a stable evaluator id and never the parameters or hashes.
    """

    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}")
    inventory = inventory or historical_inventory()
    chosen: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for seed in range(int(start), int(start) + 10000):
        params = _test_params(family, seed)
        hashes = binding_hashes(params, family)
        if _is_old_binding(params, inventory) or hashes["binding_sha256"] in seen_hashes:
            continue
        chosen.append({"seed": seed, "params": params, **hashes})
        seen_hashes.add(hashes["binding_sha256"])
        if len(chosen) >= count:
            return chosen
    raise PilotStop(f"Could not find {count} fresh bindings for {family}.")


def _episode_rows(test_refs: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order = 0
    balanced_permutations = (
        ("reactive", "full_fallback", "local_rejoin"),
        ("local_rejoin", "full_fallback", "reactive"),
        ("full_fallback", "local_rejoin", "reactive"),
        ("reactive", "local_rejoin", "full_fallback"),
        ("local_rejoin", "reactive", "full_fallback"),
        ("full_fallback", "reactive", "local_rejoin"),
    )
    for family_index, family in enumerate(FAMILIES):
        for binding_index, _binding in enumerate(test_refs[family], start=1):
            binding_id = f"b{binding_index:02d}"
            arms = balanced_permutations[family_index * 2 + binding_index - 1]
            for arm in arms:
                order += 1
                rows.append(
                    {
                        "order": order,
                        "id": f"{NAMESPACE}/{family}/{binding_id}/{arm}",
                        "family": family,
                        "binding_id": binding_id,
                        "arm": arm,
                        "seed": int(_binding["seed"]),
                        "condition": "discover",
                        "obs_mode": OBS_MODE,
                        "max_actions": MAX_ACTIONS,
                    }
                )
    return rows


def _find_complete_historical_demos(
    family: str,
    t16_root: Path | str = HISTORICAL_T16,
    max_demos: int = 3,
) -> list[dict[str, Any]]:
    """Read old successful discover traces only when full obs evidence exists."""

    t16_root = Path(t16_root)
    selected: list[dict[str, Any]] = []
    # t16's exploration index is the least ambiguous source of successful
    # discover traces.  Resolve paths but never replay or rewrite them.
    for model_dir in sorted(t16_root.glob("*/")):
        index = model_dir / family / "explore" / "exploration.json"
        if not index.is_file():
            continue
        try:
            data = _json_read(index)
        except (OSError, json.JSONDecodeError):
            continue
        raw_instances = data.get("instances") or {}
        instances = raw_instances.values() if isinstance(raw_instances, Mapping) else raw_instances if isinstance(raw_instances, list) else []
        for item in instances:
            if not isinstance(item, Mapping) or item.get("success") is not True:
                continue
            trajectory = Path(str(item.get("best_trajectory") or ""))
            if not trajectory.is_absolute():
                candidate = (index.parent / trajectory).resolve()
                if candidate.is_file():
                    trajectory = candidate
                else:
                    trajectory = (REPO_ROOT / trajectory).resolve()
            if not trajectory.is_file():
                continue
            try:
                records = [json.loads(line) for line in trajectory.read_text().splitlines() if line.strip()]
            except (OSError, json.JSONDecodeError):
                continue
            steps = [r for r in records if isinstance(r, Mapping) and r.get("record_type") != "final"]
            # Old runner records have only post-action metadata.  Do not
            # pretend that this is a complete guard-training trace.
            complete = bool(steps) and all(
                isinstance(step.get("pre_obs"), Mapping)
                and isinstance(step.get("post_obs"), Mapping)
                and isinstance(step.get("pre_obs", {}).get("ax_tree_text"), str)
                and isinstance(step.get("post_obs", {}).get("ax_tree_text"), str)
                for step in steps
            )
            if not complete:
                continue
            selected.append(
                {
                    "family": family,
                    "seed": int(item.get("seed", 0)),
                    "goal_text": str(item.get("goal") or ""),
                    "trajectory": str(trajectory),
                    "trace_sha256": sha256_file(trajectory),
                    "source": "historical_discover",
                }
            )
            if len(selected) >= max_demos:
                return selected
    return selected


def _training_ready(out: Path, spec: Mapping[str, Any]) -> tuple[bool, str]:
    training = spec.get("training") or {}
    if training.get("status") == "ready":
        return True, "ready"
    manifest = out / "training_manifest.json"
    if manifest.is_file():
        try:
            data = _json_read(manifest)
            if data.get("status") in {"ready", "partial"} and set(FAMILIES) <= set((data.get("families") or {}).keys()):
                return True, str(data.get("status"))
        except (OSError, json.JSONDecodeError):
            pass
    return False, str(training.get("status") or "missing")


def prepare(
    out: Path | str = DEFAULT_OUT,
    *,
    historical_root: Path | str = HISTORICAL_ROOT,
    t16_root: Path | str = HISTORICAL_T16,
    source_root: Path | str = REPO_ROOT,
) -> dict[str, Any]:
    """Create the immutable public spec and private evaluator bindings."""

    out = Path(out).resolve()
    spec_path = out / "spec.json"
    existing_private: dict[str, Any] | None = None
    existing_private_path: Path | None = None
    private_needs_update = False
    if spec_path.is_file():
        try:
            return load_spec(out)
        except PilotStop:
            # A draft spec made before the final dependency hashes were known
            # may be amended exactly once while its ledger namespace is empty.
            # Preserve every old draft artifact and, crucially, reuse its
            # evaluator-private bindings byte-for-byte.
            if _selective_receipts_exist():
                raise PilotStop("Cannot amend a selective draft after its first receipt.") from None
            private_ref = "private/evaluator_bindings.json"
            try:
                old_spec = _json_read(spec_path)
                private_ref = str(old_spec.get("private_bindings") or private_ref)
            except (OSError, UnicodeError, json.JSONDecodeError):
                old_spec = {}
            private_path = out / private_ref
            if private_path.is_file():
                try:
                    existing_private = _json_read(private_path)
                    existing_private_path = private_path
                except (OSError, UnicodeError, json.JSONDecodeError):
                    existing_private = None
            _preserve_preflight_draft(out)
    out.mkdir(parents=True, exist_ok=True)
    inventory = historical_inventory(historical_root, t16_root)
    atomic_json(out / "historical_inventory.json", inventory)

    private_bindings: dict[str, list[dict[str, Any]]] = {}
    public_refs: dict[str, list[dict[str, Any]]] = {}
    existing_families = (existing_private or {}).get("families") if existing_private else None
    for family in FAMILIES:
        selected = existing_families.get(family) if isinstance(existing_families, Mapping) else None
        if not isinstance(selected, list) or len(selected) != TEST_BINDING_COUNT:
            selected = choose_fresh_bindings(family, TEST_BINDING_COUNT, TEST_SEED_START, inventory)
        else:
            selected = [dict(item) for item in selected]
            for item in selected:
                if "params" not in item or "seed" not in item:
                    raise PilotStop(f"Existing private bindings for {family} are incomplete.")
                expected_hashes = binding_hashes(item["params"], family)
                if item.get("params_sha256") != expected_hashes["params_sha256"] or item.get("binding_sha256") != expected_hashes["binding_sha256"]:
                    item.update(expected_hashes)
                    private_needs_update = True
        private_bindings[family] = selected
        public_refs[family] = [
            {
                "binding_id": f"b{i:02d}",
                "seed": int(item["seed"]),
            }
            for i, item in enumerate(selected, start=1)
        ]
    private_path = out / "private" / "evaluator_bindings.json"
    _private_file(private_path)
    if existing_private is None:
        atomic_json(
            private_path,
            {
                "record_type": "evaluator-private-bindings",
                "namespace": NAMESPACE,
                "families": private_bindings,
                "created_unix": time.time(),
            },
            private=True,
        )
    else:
        if existing_private_path is not None and existing_private_path.resolve() != private_path.resolve() and not private_path.exists():
            private_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(existing_private_path, private_path)
        if private_needs_update:
            existing_private = dict(existing_private)
            existing_private["families"] = private_bindings
            atomic_json(private_path, existing_private, private=True)
        _private_file(private_path)

    historical_demos = {
        family: _find_complete_historical_demos(family, t16_root=t16_root)
        for family in FAMILIES
    }
    historical_counts = {family: len(items) for family, items in historical_demos.items()}
    missing_historical = [family for family, n in historical_counts.items() if n == 0]
    training_status = "historical_ready" if not missing_historical else "fresh_required"
    training = {
        "status": training_status,
        "source": "historical_discover_when_complete_else_fresh_train",
        "max_successful_demos_per_family": 3,
        "max_fresh_attempts_per_family": MAX_TRAIN_ATTEMPTS,
        "candidate_seed_start": TRAIN_SEED_START,
        "historical_demo_counts": historical_counts,
        "fresh_required_families": missing_historical,
        "historical_demo_refs": historical_demos,
    }
    rows = _episode_rows(public_refs)
    manifest = source_manifest(source_root)
    budget_manifest_path = out / "manifest.json"
    budget_manifest = None
    try:
        budget_module = importlib.import_module(".selective_budget", __package__)
    except (ImportError, ModuleNotFoundError):
        budget_module = None
    freeze = getattr(budget_module, "freeze_manifest", None) if budget_module is not None else None
    if callable(freeze):
        budget_manifest = freeze(
            {MODEL: {
                "provider": PROVIDER,
                "prompt_per_m": "0.09",
                "completion_per_m": "0.30",
                "max_tokens": MAX_COMPLETION_TOKENS,
            }},
            path=budget_manifest_path,
            extra={"pilot_source_manifest_sha256": _manifest_digest(manifest)},
        )
    body: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "version": 1,
        "namespace": NAMESPACE,
        "model": MODEL,
        "provider": PROVIDER,
        "model_locks": {
            MODEL: {
                "provider": PROVIDER,
                "prompt_per_m": "0.09",
                "completion_per_m": "0.30",
                "max_tokens": MAX_COMPLETION_TOKENS,
                "temperature": TEMPERATURE,
            }
        },
        "families": list(FAMILIES),
        "arms": list(ARMS),
        "episodes": rows,
        "action_budget": MAX_ACTIONS,
        "local_assistance_max": MAX_LOCAL_ASSISTANCE,
        "obs_mode": OBS_MODE,
        "temperature": TEMPERATURE,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "ledger": _path_rel(SHARED_LEDGER),
        "run_lock": _path_rel(SHARED_RUN_LOCK),
        "ledger_absolute": str(SHARED_LEDGER.resolve()),
        "run_lock_absolute": str(SHARED_RUN_LOCK.resolve()),
        "private_bindings": _out_rel(out, private_path),
        "private_binding_visibility": "evaluator_only",
        "historical_inventory": _out_rel(out, out / "historical_inventory.json"),
        "training": training,
        "source_manifest": manifest,
        "source_manifest_sha256": _manifest_digest(manifest),
        "budget_manifest": _out_rel(out, budget_manifest_path) if budget_manifest is not None else None,
        "budget_manifest_sha256": hash_json(budget_manifest) if budget_manifest is not None else None,
        "plan_schema": PLAN_SCHEMA,
        "plans": {family: f"plans/{family}.json" for family in FAMILIES},
        "reporting": {
            "prototype_only": True,
            "selector_algorithm_implemented": False,
            "training_acquisition_reported_separately": True,
            "shared_build_counted_once_physically_per_family": True,
            "shared_build_counted_per_alternative_in_cumulative_economics": True,
        },
    }
    body["spec_sha256"] = hash_json(body)
    atomic_json(spec_path, body)
    return body


def _runtime_module(required: bool = True):
    try:
        return importlib.import_module(".selective_runtime", __package__)
    except (ImportError, ModuleNotFoundError) as exc:
        if required:
            raise PilotStop("selective_runtime.py is not available; no paid work may start.") from exc
        return None


def _budget_module(required: bool = True):
    try:
        return importlib.import_module(".selective_budget", __package__)
    except (ImportError, ModuleNotFoundError) as exc:
        if required:
            raise PilotStop("selective_budget.py is not available; no paid work may start.") from exc
        return None


def _check_source_freeze(spec: Mapping[str, Any]) -> None:
    current = source_manifest(REPO_ROOT)
    if _manifest_digest(current) != spec.get("source_manifest_sha256"):
        raise PilotStop("Frozen source or execution dependency hashes changed after preparation.")
    if spec.get("model") != MODEL or spec.get("provider") != PROVIDER:
        raise PilotStop("Selective pilot model/provider lock changed.")
    if spec.get("obs_mode") != OBS_MODE or spec.get("action_budget") != MAX_ACTIONS:
        raise PilotStop("Selective pilot observation or action budget changed.")


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    out = Path(out).resolve()
    spec_path = out / "spec.json"
    if not spec_path.is_file():
        raise PilotStop(f"Prepared spec is missing: {spec_path}")
    spec = _json_read(spec_path)
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or hash_json(body) != digest:
        raise PilotStop("Prepared spec hash does not match its contents.")
    _check_source_freeze(spec)
    budget_manifest_ref = spec.get("budget_manifest")
    if budget_manifest_ref:
        budget_path = out / budget_manifest_ref
        if not budget_path.is_file() or hash_json(_json_read(budget_path)) != spec.get("budget_manifest_sha256"):
            raise PilotStop("Frozen selective budget manifest changed.")
        budget = _budget_module(required=True)
        loader = getattr(budget, "load_manifest", None)
        if not callable(loader):
            raise PilotStop("selective_budget.load_manifest() is required for frozen execution.")
        loader(budget_path)
    if tuple(spec.get("families") or ()) != FAMILIES or tuple(spec.get("arms") or ()) != ARMS:
        raise PilotStop("Frozen family or arm registry changed.")
    private_path = out / spec.get("private_bindings", "private/evaluator_bindings.json")
    if not private_path.is_file() or private_path.stat().st_mode & 0o077:
        raise PilotStop("Private evaluator binding file is missing or group/world readable.")
    return spec


def _validate_selector(selector: Any, where: str, slots: set[str] | None = None) -> None:
    if not isinstance(selector, Mapping):
        raise PlanInvalid(f"{where} must be an object")
    unknown = set(selector) - SELECTOR_KEYS
    if unknown:
        raise PlanInvalid(f"{where} has unsupported selector keys: {sorted(unknown)}")
    for key, value in selector.items():
        if key in {"clickable", "editable"}:
            if not isinstance(value, bool):
                raise PlanInvalid(f"{where}.{key} must be boolean")
        else:
            _validate_slot_ref(value, f"{where}.{key}", slots or set())


def _validate_slot_ref(value: Any, where: str, slots: set[str]) -> None:
    if isinstance(value, Mapping):
        keys = set(value)
        if VALUE_SPEC_REQUIRED_KEYS <= keys or keys & VALUE_SPEC_KEYS:
            if not VALUE_SPEC_REQUIRED_KEYS <= keys or not keys <= VALUE_SPEC_KEYS:
                raise PlanInvalid(f"{where} has unsupported slot reference keys")
            slot = value.get("slot")
            transform = value.get("transform")
            if (
                not isinstance(slot, str)
                or not isinstance(transform, str)
                or slot not in slots
                or transform not in ALLOWED_TRANSFORMS
            ):
                raise PlanInvalid(f"{where} has an unknown slot transform")
            for key in ("prefix", "suffix"):
                if key in value and not isinstance(value[key], str):
                    raise PlanInvalid(f"{where}.{key} must be a string")
            return
        for key, child in value.items():
            _validate_slot_ref(child, f"{where}.{key}", slots)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_slot_ref(child, f"{where}[{index}]", slots)
        return
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise PlanInvalid(f"{where} must be JSON data")
    if isinstance(value, str):
        # String templates are accepted only in this exact small language.
        for match in re.finditer(r"\{\{slot:([A-Za-z_][A-Za-z0-9_]*)\|([a-z_]+)\}\}", value):
            if match.group(1) not in slots or match.group(2) not in ALLOWED_TRANSFORMS:
                raise PlanInvalid(f"{where} has an unknown string template")
        if "{{" in value and not re.fullmatch(r"(?:[^{}]|\{\{slot:[A-Za-z_][A-Za-z0-9_]*\|[a-z_]+\}\})*", value):
            raise PlanInvalid(f"{where} has an unsupported template expression")


def validate_plan_local(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the restricted plan schema without importing the runtime."""

    if not isinstance(plan, Mapping) or plan.get("schema") != PLAN_SCHEMA:
        raise PlanInvalid(f"plan schema must be {PLAN_SCHEMA}")
    unknown = set(plan) - {"schema", "slots", "steps"}
    if unknown:
        raise PlanInvalid(f"plan has unsupported top-level keys: {sorted(unknown)}")
    slots = plan.get("slots")
    if not isinstance(slots, Mapping):
        raise PlanInvalid("plan slots must be an object")
    for slot, spec in slots.items():
        if not isinstance(slot, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", slot):
            raise PlanInvalid("slot names must be identifiers")
        if not isinstance(spec, str) or not spec.strip():
            raise PlanInvalid(f"slot {slot!r} needs a description")
    slot_names = set(slots)
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlanInvalid("plan steps must be a non-empty list")
    seen_ids: set[str] = set()
    for index, step in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(step, Mapping):
            raise PlanInvalid(f"{where} must be an object")
        required = {"id", "intent", "action", "target", "before", "after"}
        missing = required - set(step)
        if missing:
            raise PlanInvalid(f"{where} is missing {sorted(missing)}")
        if not isinstance(step["id"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", step["id"]):
            raise PlanInvalid(f"{where}.id is invalid")
        if step["id"] in seen_ids:
            raise PlanInvalid(f"duplicate step id {step['id']!r}")
        seen_ids.add(step["id"])
        if not isinstance(step["intent"], str) or not step["intent"].strip():
            raise PlanInvalid(f"{where}.intent must be non-empty text")
        action = step["action"]
        if not isinstance(action, Mapping) or action.get("action_type") not in ALLOWED_ACTION_TYPES:
            raise PlanInvalid(f"{where}.action uses an unsupported action type")
        _validate_slot_ref(action, f"{where}.action", slot_names)
        target = step["target"]
        if target is not None:
            _validate_selector(target, f"{where}.target", slot_names)
            _validate_slot_ref(target, f"{where}.target", slot_names)
        for guard_name in ("before", "after"):
            guard = step[guard_name]
            if not isinstance(guard, list) or (guard_name == "after" and not guard):
                raise PlanInvalid(f"{where}.{guard_name} must be a selector list (after is non-empty)")
            for guard_index, selector in enumerate(guard):
                _validate_selector(selector, f"{where}.{guard_name}[{guard_index}]", slot_names)
                _validate_slot_ref(selector, f"{where}.{guard_name}[{guard_index}]", slot_names)
    normalized = dict(plan)
    normalized["slots"] = {str(k): str(v) for k, v in slots.items()}
    normalized["steps"] = [dict(step) for step in steps]
    return normalized


def validate_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate through the shared runtime, with the local schema as a guard."""

    normalized = validate_plan_local(plan)
    runtime = _runtime_module(required=False)
    if runtime is not None and callable(getattr(runtime, "validate_plan", None)):
        result = runtime.validate_plan(normalized)
        if result is False or (isinstance(result, Mapping) and result.get("valid") is False):
            raise PlanInvalid("selective_runtime rejected the plan")
        if isinstance(result, Mapping) and isinstance(result.get("plan"), Mapping):
            normalized = dict(result["plan"])
    return normalized


def _selector_fingerprint(selector: Mapping[str, Any]) -> str:
    return canonical(selector)


def _selector_literal(selector: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("text", "contains", "hint", "description"):
        value = selector.get(key)
        if isinstance(value, str) and value.strip() and "{{slot:" not in value:
            values.append(value.strip().casefold())
    return values


def _ax_contains(ax: Any, value: str) -> bool:
    if not isinstance(ax, str):
        return False
    return value.casefold() in ax.casefold()


def validate_effect_guards(plan: Mapping[str, Any], demonstrations: Sequence[Mapping[str, Any]]) -> None:
    """Reject guards that describe an unchanged or already-present UI state."""

    steps = list(plan.get("steps") or [])
    for index, step in enumerate(steps):
        before = list(step.get("before") or [])
        after = list(step.get("after") or [])
        if any(_selector_fingerprint(item) in {_selector_fingerprint(x) for x in before} for item in after):
            raise PlanInvalid(f"steps[{index}].after repeats a before selector")
        evidence = []
        for demo in demonstrations:
            for recorded in demo.get("steps") or []:
                try:
                    recorded_step = int(recorded.get("step", -1))
                except (TypeError, ValueError):
                    continue
                if recorded_step != index + 1:
                    continue
                pre_ax = (recorded.get("pre_obs") or {}).get("ax_tree_text", "")
                post_ax = (recorded.get("post_obs") or {}).get("ax_tree_text", "")
                literals = [literal for selector in after for literal in _selector_literal(selector)]
                observed_effect = bool(literals) and any(
                    not _ax_contains(pre_ax, literal) and _ax_contains(post_ax, literal)
                    for literal in literals
                )
                evidence.append(observed_effect)
        if not evidence:
            raise PlanInvalid(f"steps[{index}].after evidence status is unknown because no matching training record exists")
        if not any(evidence):
            raise PlanInvalid(f"steps[{index}].after has no observed effect-specific evidence")
        if demonstrations:
            pre_satisfied = []
            for demo in demonstrations:
                for recorded in demo.get("steps") or []:
                    try:
                        recorded_step = int(recorded.get("step", -1))
                    except (TypeError, ValueError):
                        continue
                    if recorded_step != index + 1:
                        continue
                    pre_ax = (recorded.get("pre_obs") or {}).get("ax_tree_text", "")
                    literals = [literal for selector in after for literal in _selector_literal(selector)]
                    if literals and all(_ax_contains(pre_ax, literal) for literal in literals):
                        pre_satisfied.append(True)
            if pre_satisfied and len(pre_satisfied) == len(evidence) and all(pre_satisfied):
                raise PlanInvalid(f"steps[{index}].after is already satisfied in every training pre-state")


def _json_from_text(text: str) -> Any:
    text = str(text or "").strip()
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[index:])
                if isinstance(value, Mapping):
                    return value
            except json.JSONDecodeError:
                continue
    raise PlanInvalid("model response did not contain one JSON object")


def _response_dump(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping):
        return dict(response)
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            return dict(model_dump(mode="json"))
        except TypeError:
            return dict(model_dump())
    return {"repr": repr(response)}


def response_content(response: Any) -> str:
    if isinstance(response, Mapping):
        choices = response.get("choices") or []
        if choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message") or {}
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str):
                    return content
        content = response.get("content")
        return content if isinstance(content, str) else ""
    choices = getattr(response, "choices", None) or []
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
    return ""


def usage_from_response(response: Any) -> dict[str, Any]:
    raw: Any
    if isinstance(response, Mapping):
        raw = response.get("usage") or {}
    else:
        raw = getattr(response, "usage", None)
    if raw is None:
        return {"prompt_tokens": None, "completion_tokens": None, "cached_tokens": None, "cost_usd": None}
    if isinstance(raw, Mapping):
        get = raw.get
        details = get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
        cost = get("cost")
        return {
            "prompt_tokens": get("prompt_tokens"),
            "completion_tokens": get("completion_tokens"),
            "cached_tokens": cached,
            "cost_usd": cost,
        }
    details = getattr(raw, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    model_extra = getattr(raw, "model_extra", None) or {}
    if cached is None and isinstance(model_extra.get("prompt_tokens_details"), Mapping):
        cached = model_extra["prompt_tokens_details"].get("cached_tokens")
    cost = getattr(raw, "cost", None)
    if cost is None:
        cost = model_extra.get("cost")
    return {
        "prompt_tokens": getattr(raw, "prompt_tokens", None),
        "completion_tokens": getattr(raw, "completion_tokens", None),
        "cached_tokens": cached,
        "cost_usd": cost,
    }


def _call_client(client: Any, messages: list[dict[str, Any]], episode_id: str) -> tuple[Any, dict[str, Any]]:
    """Call either the selective budget facade or an OpenAI-compatible stub."""

    if hasattr(client, "begin_episode"):
        current = getattr(client, "episode", None)
        if current != episode_id:
            client.begin_episode(episode_id)
    kwargs = {"model": MODEL, "messages": messages, "temperature": TEMPERATURE}
    if callable(getattr(client, "create", None)):
        response = client.create(**kwargs)
    else:
        completions = getattr(getattr(client, "chat", None), "completions", None)
        create = getattr(completions, "create", None)
        if not callable(create):
            raise PilotStop("Client does not expose a bounded chat completion call.")
        response = create(**kwargs)
    return response, usage_from_response(response)


def _begin_episode(client: Any, episode_id: str) -> None:
    begin = getattr(client, "begin_episode", None)
    if not callable(begin):
        raise PilotStop("Bounded client must expose begin_episode() before model or UI work.")
    current = getattr(client, "episode", None)
    if current != episode_id:
        begin(episode_id)


def build_prompt(family: str, demonstrations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Construct a builder request from goals, visible observations, actions."""

    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}")
    examples: list[dict[str, Any]] = []
    for demo in demonstrations:
        # The builder input is deliberately projected from the data-only trace
        # and contains no private test rows or benchmark evaluator fields.
        steps = []
        for item in demo.get("steps", []):
            steps.append(
                {
                    "step": item.get("step"),
                    "action": item.get("action"),
                    "pre_observation": {
                        "activity": (item.get("pre_obs") or {}).get("url"),
                        "ax_tree_text": (item.get("pre_obs") or {}).get("ax_tree_text", ""),
                    },
                    "post_observation": {
                        "activity": (item.get("post_obs") or {}).get("url"),
                        "ax_tree_text": (item.get("post_obs") or {}).get("ax_tree_text", ""),
                    },
                    "screenshot_files": item.get("screenshot_files") or {},
                }
            )
        examples.append({"goal_text": demo.get("goal_text", ""), "steps": steps})
    user_text = (
        "Build one guarded, reusable Android action plan from these successful "
        "discover demonstrations. The plan must generalize by resolving only "
        "observable UI selectors at execution time. Return exactly one JSON "
        f"object with schema {PLAN_SCHEMA}. Required top-level keys are "
        "schema, slots, and steps. slots maps each slot name to its plain "
        "description string. Each step has id, intent, action, target, "
        "before, and after. before and after are non-empty lists of observable "
        "selectors. Actions are native JSON action dictionaries. A value that "
        "depends on a slot uses {slot: name, transform: identity} as a JSON "
        "object and may add optional string prefix and suffix fields, applied "
        "after the transform. identity stringifies the binding, stem takes the "
        "final path component without its final suffix, suffix includes its "
        "leading dot, and first_word/last_word use whitespace-delimited words. "
        "Never emit Python, code, eval, exec, shell, benchmark oracle "
        "fields, hidden task parameters, or a procedural note. Use only the "
        "goals, screenshots, accessibility trees, and actions shown below.\n\n"
        + json.dumps({"family": family, "demonstrations": examples}, ensure_ascii=True, indent=2)
    )
    return [
        {
            "role": "system",
            "content": "You produce data-only guarded Android plans as strict JSON.",
        },
        {"role": "user", "content": user_text},
    ]


def _load_training_demos(out: Path, spec: Mapping[str, Any], family: str) -> list[dict[str, Any]]:
    manifest_path = out / "training_manifest.json"
    if manifest_path.is_file():
        manifest = _json_read(manifest_path)
        references = (manifest.get("families") or {}).get(family) or []
        demos: list[dict[str, Any]] = []
        for ref in references:
            path = Path(ref.get("trajectory", ""))
            if not path.is_absolute():
                path = out / path
            if not path.is_file() or ref.get("success") is not True:
                continue
            steps: list[dict[str, Any]] = []
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("record_type") == "step":
                    steps.append(record)
            if steps and all(isinstance(item.get("pre_obs"), Mapping) and isinstance(item.get("post_obs"), Mapping) for item in steps):
                demos.append({"family": family, "goal_text": ref.get("goal_text", ""), "steps": steps, "source": ref.get("source", "fresh_train")})
        if demos:
            return demos[:3]
    historical = ((spec.get("training") or {}).get("historical_demo_refs") or {}).get(family) or []
    demos: list[dict[str, Any]] = []
    for item in historical[:3]:
        if item.get("source") != "historical_discover":
            continue
        path = Path(str(item.get("trajectory", "")))
        if not path.is_file():
            continue
        try:
            steps = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip() and json.loads(line).get("record_type") == "step"
            ]
        except (OSError, json.JSONDecodeError):
            continue
        if steps:
            demos.append({"family": family, "goal_text": item.get("goal_text", ""), "steps": steps, "source": "historical_discover"})
    return demos


def _plan_result_dir(out: Path, family: str) -> Path:
    path = out / "build" / family
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_build_call(path: Path, label: str, response: Any, usage: Mapping[str, Any], raw_text: str, error: str | None = None) -> None:
    atomic_json(
        path / f"{label}.json",
        {
            "record_type": "plan-build-response",
            "label": label,
            "raw_response": _response_dump(response),
            "raw_text": raw_text,
            "usage": dict(usage),
            "error": error,
        },
        private=False,
    )


def build(
    out: Path | str = DEFAULT_OUT,
    *,
    client_factory: Callable[[Mapping[str, Any], str], Any] | None = None,
    env_file: Path | str | None = None,
) -> dict[str, Any]:
    """Generate one plan per family plus at most one schema-repair call."""

    out = Path(out).resolve()
    spec = load_spec(out)
    ready, reason = _training_ready(out, spec)
    if not ready:
        raise PilotStop(f"Training evidence is not frozen and complete ({reason}); run --train first.")
    runtime = _runtime_module(required=True)
    budget = _budget_module(required=True)
    ledger = _make_ledger(budget, spec)
    live_spec = dict(spec)
    live_spec["model_locks"] = _validated_model_locks(budget, spec)
    results: dict[str, Any] = {}
    with _exclusive_context(budget, spec, "build"):
        for family in FAMILIES:
            plan_path = out / "plans" / f"{family}.json"
            if plan_path.is_file():
                try:
                    plan = validate_plan(_json_read(plan_path))
                    results[family] = {"status": "already_built", "plan_sha256": hash_json(plan)}
                    continue
                except Exception:
                    # Preserve the previous response.  A subsequent explicit
                    # build call may repair once, but never silently rebuild a
                    # valid immutable artifact.
                    pass
            demos = _load_training_demos(out, spec, family)
            if not demos:
                results[family] = {"status": "unbuildable", "reason": "no complete successful training demo"}
                atomic_json(_plan_result_dir(out, family) / "build.json", results[family])
                continue
            messages = build_prompt(family, demos)
            build_id = f"{NAMESPACE}/build/{family}"
            client = client_factory(live_spec, build_id) if client_factory else _make_client(budget, ledger, live_spec, build_id, env_file)
            result: dict[str, Any] = {"status": "unbuildable", "calls": 0, "family": family}
            build_dir = _plan_result_dir(out, family)
            try:
                response, usage = _call_client(client, messages, build_id)
                result["calls"] = 1
                raw_text = response_content(response)
                _save_build_call(build_dir, "first_response", response, usage, raw_text)
                try:
                    candidate = validate_plan(_json_from_text(raw_text))
                    validate_effect_guards(candidate, demos)
                except Exception as first_error:
                    repair_messages = [
                        messages[0],
                        {
                            "role": "user",
                            "content": (
                                messages[1]["content"]
                                + "\n\nThe first response failed validation with this local schema error: "
                                + str(first_error)
                                + "\nReturn one corrected JSON object only."
                            ),
                        },
                        {"role": "assistant", "content": raw_text},
                    ]
                    repair_response, repair_usage = _call_client(client, repair_messages, build_id + "/repair")
                    result["calls"] = 2
                    repair_text = response_content(repair_response)
                    _save_build_call(build_dir, "repair_response", repair_response, repair_usage, repair_text)
                    candidate = validate_plan(_json_from_text(repair_text))
                    validate_effect_guards(candidate, demos)
                    result["repair"] = True
                else:
                    result["repair"] = False
                # The runtime validates the same object before it can be served.
                runtime_report = runtime.validate_plan(candidate)
                if isinstance(runtime_report, Mapping) and runtime_report.get("valid") is False:
                    raise PlanInvalid("selective_runtime rejected the generated plan")
                if isinstance(runtime_report, Mapping) and isinstance(runtime_report.get("plan"), Mapping):
                    candidate = dict(runtime_report["plan"])
                atomic_json(plan_path, candidate)
                result.update(status="built", plan_sha256=hash_json(candidate), plan_path=_path_rel(plan_path))
            except BudgetStop as exc:
                if not (build_dir / "first_response.json").exists():
                    _save_build_call(build_dir, "first_response", {"error_type": type(exc).__name__}, {}, "", _safe_first_line(exc))
                result.update(status="budget_stopped", stop_reason=_safe_budget_reason(exc), error_type=type(exc).__name__)
                atomic_json(build_dir / "build.json", result)
                raise
            except Exception as exc:
                result.update(status="unbuildable", error=_safe_first_line(exc))
                # The first raw response is saved before this record, including an
                # invalid response or a transport failure envelope.
            atomic_json(build_dir / "build.json", result)
            results[family] = result
    atomic_json(out / "build_manifest.json", {"record_type": "selective-build", "families": results, "created_unix": time.time()})
    return results


def _make_ledger(budget: Any, spec: Mapping[str, Any]) -> Any:
    factory = getattr(budget, "make_ledger", None)
    if not callable(factory):
        raise PilotStop("selective_budget does not expose make_ledger().")
    ledger = factory()
    if Path(getattr(ledger, "path", spec["ledger_absolute"])).resolve() != Path(spec["ledger_absolute"]).resolve():
        raise PilotStop("Selective pilot must use the existing revision ledger.")
    return ledger


def _make_client(budget: Any, ledger: Any, spec: Mapping[str, Any], episode_id: str, env_file: Path | str | None) -> Any:
    factory = getattr(budget, "make_client", None)
    if not callable(factory):
        raise PilotStop("selective_budget does not expose make_client().")
    kwargs: dict[str, Any] = {"ledger": ledger}
    if env_file is not None:
        kwargs["env_path"] = env_file
    client = factory(spec["model_locks"], **kwargs)
    return client


def _validated_model_locks(budget: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Refresh free provider metadata before any paid request."""

    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise PilotStop("selective_budget does not expose validate_locks().")
    return dict(validator(spec["model_locks"]))


def _exclusive_context(budget: Any, spec: Mapping[str, Any], mode: str):
    exclusive = getattr(budget, "exclusive_run", None)
    if not callable(exclusive):
        raise PilotStop("selective_budget does not expose exclusive_run().")
    return exclusive()


def _save_obs(out_dir: Path, label: str, obs: Mapping[str, Any], private: bool = True) -> dict[str, Any]:
    metadata = {
        "url": obs.get("url"),
        "ax_tree_text": obs.get("ax_tree_text") or "",
        "goal_text": obs.get("goal_text") or "",
        "last_action": obs.get("last_action"),
        "last_action_error": obs.get("last_action_error"),
    }
    files: dict[str, str | None] = {"raw": None, "som": None}
    for key, suffix in (("screenshot_b64", "png"), ("som_screenshot_b64", "som.png")):
        encoded = obs.get(key)
        if not encoded:
            continue
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            continue
        file_name = f"{label}.{suffix}"
        _atomic_write(out_dir / file_name, data, 0o600 if private else None)
        files["raw" if key == "screenshot_b64" else "som"] = file_name
    metadata["screenshot_files"] = files
    return metadata


class AndroidActionExecutor:
    """Common physical UI executor used by reactive and compiled arms.

    ``selective_runtime.run_plan`` consumes exactly this adapter interface:
    ``observe()``, ``elements()``, and ``execute(action)``.  The same adapter
    is used by the reactive handoff, which keeps the UI action cap and host
    guard identical across arms.
    """

    def __init__(self, env: Any, out_dir: Path, episode_id: str, max_actions: int, budget: Any, goal_text: str):
        self.env = env
        self.out_dir = out_dir
        self.episode_id = episode_id
        self.max_actions = max_actions
        self.budget = budget
        self.goal_text = goal_text
        self.source = "unknown"
        self.actions_sent = 0
        self.records: list[dict[str, Any]] = []

    def observe(self) -> dict[str, Any]:
        if callable(getattr(self.env, "observe", None)):
            return dict(self.env.observe(self.goal_text))
        return dict(self.env._observe(self.goal_text))

    def elements(self) -> list[dict[str, Any]]:
        state = self.env.aw_env.get_state(wait_to_stabilize=False)
        elements: list[dict[str, Any]] = []
        for index, element in enumerate(state.ui_elements):
            elements.append(
                {
                    "index": index,
                    "text": getattr(element, "text", "") or "",
                    "hint": getattr(element, "hint_text", "") or "",
                    "description": getattr(element, "content_description", "") or "",
                    "clickable": bool(getattr(element, "is_clickable", False)),
                    "editable": bool(getattr(element, "is_editable", False)),
                }
            )
        return elements

    def execute(self, action: Mapping[str, Any]) -> None:
        if self.actions_sent >= self.max_actions:
            raise ActionBudgetStop(f"episode action budget {self.max_actions} reached")
        action_dict = dict(action)
        if action_dict.get("action_type") == "status":
            raise ValueError("status actions are terminal and are not sent through the physical executor")
        action_index = self.actions_sent + 1
        pre_obs = self.observe()
        pre_meta = _save_obs(self.out_dir, f"pre_{self.actions_sent + 1:03d}", pre_obs)
        _before_ui_action(self.budget)
        try:
            from android_world.env import json_action

            native = json_action.JSONAction(**action_dict)
            self.env.aw_env.execute_action(native)
        except BudgetStop:
            raise
        except Exception as exc:
            # The action was handed to the device.  No plan or fallback
            # action may be resent after this point.  Runtime turns this into
            # an uncertain terminal result.
            self.actions_sent += 1
            raise PilotStop(f"issued action {action_index} failed: {_safe_first_line(exc)}") from None
        settle = getattr(self.env, "wait_after_action_seconds", 0.0) or 0.0
        if settle:
            time.sleep(float(settle))
        self.actions_sent += 1
        obs = self.observe()
        post_meta = _save_obs(self.out_dir, f"post_{self.actions_sent:03d}", obs)
        record = {
            "record_type": "action",
            "action_index": action_index,
            "source": self.source,
            "action": action_dict,
            "action_issued": True,
        }
        self.records.append(record)
        _record_action(self.out_dir, record, pre_meta, post_meta)


def _before_ui_action(budget: Any) -> None:
    guard = getattr(budget, "before_ui_action", None)
    if not callable(guard):
        raise PilotStop("selective_budget.before_ui_action() is required before UI actions.")
    guard()


def _action_obj_to_dict(action: Any) -> dict[str, Any]:
    if isinstance(action, Mapping):
        return dict(action)
    value = getattr(action, "json_str", None)
    if callable(value):
        return dict(json.loads(value()))
    if isinstance(value, str):
        return dict(json.loads(value))
    raise ValueError("action was not a JSON action object")


def _agent(model: str, client: Any):
    from .agent import AndroidAgent

    return AndroidAgent(model=model, obs_mode=OBS_MODE, client=client, temperature=TEMPERATURE)


def _record_call(
    out_dir: Path,
    episode_id: str,
    call_index: int,
    purpose: str,
    prompt: Any,
    response: Any,
    usage: Mapping[str, Any],
    private: bool = True,
    context: Any = None,
) -> dict[str, Any]:
    existing = 0
    path = out_dir / "trajectory.jsonl"
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and json.loads(line).get("record_type") == "model_call":
                    existing += 1
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = 0
    call_index = max(int(call_index), existing + 1)
    record = {
        "record_type": "model_call",
        "call_index": call_index,
        "episode_id": episode_id,
        "purpose": purpose,
        "prompt": prompt,
        "response": response_content(response),
        "usage": dict(usage),
    }
    if context is not None:
        record.update(
            {
                "recovery_context_policy": context.policy,
                "recovery_context_digest": context.digest,
                "recovery_context_bytes": context.byte_length,
            }
        )
    append_jsonl(out_dir / "trajectory.jsonl", record, private=private)
    return record


def _record_action(out_dir: Path, action_record: Mapping[str, Any], pre_meta: Mapping[str, Any], post_meta: Mapping[str, Any], private: bool = True) -> None:
    append_jsonl(
        out_dir / "trajectory.jsonl",
        {
            "record_type": "step",
            "step": action_record.get("action_index"),
            "action": action_record.get("action"),
            "action_issued": action_record.get("action_issued", False),
            "source": action_record.get("source"),
            "pre_obs": dict(pre_meta),
            "post_obs": dict(post_meta),
            "screenshot_files": {"pre": pre_meta.get("screenshot_files"), "post": post_meta.get("screenshot_files")},
        },
        private=private,
    )


def _reactive_loop(
    executor: AndroidActionExecutor,
    goal_prompt: str,
    episode_id: str,
    client: Any,
    out_dir: Path,
    initial_obs: Mapping[str, Any],
    *,
    purpose: str = "reactive",
    scoped_prompt: str | None = None,
    max_model_calls: int = 160,
    recovery_context_policy: str | None = None,
    node_intent: str | None = None,
) -> dict[str, Any]:
    validate_recovery_context_policy(recovery_context_policy)
    agent = _agent(MODEL, client) if recovery_context_policy is None else None
    obs = dict(initial_obs)
    first = True
    calls = 0
    malformed = 0
    deterministic_start = executor.actions_sent
    while executor.actions_sent < executor.max_actions and calls < max_model_calls:
        context = None
        if recovery_context_policy is not None:
            context = build_context(
                recovery_context_policy,
                goal_text=executor.goal_text,
                goal_prompt=goal_prompt,
                observation=obs,
                issued_records=executor.records,
                node_intent=node_intent if first else None,
                remaining_actions=executor.max_actions - executor.actions_sent,
            )
            prompt = context.prompt
            agent = _agent(MODEL, client)
        else:
            prompt = (scoped_prompt if first else None) if scoped_prompt is not None else (goal_prompt if first else None)
        try:
            response_text, usage = agent.act(prompt, obs)
            response = {"text": response_text}
        except BudgetStop:
            raise
        except Exception as exc:
            raise PilotStop(f"{purpose} model call failed: {_safe_first_line(exc)}") from None
        calls += 1
        _record_call(out_dir, episode_id, calls, purpose, prompt, response, usage, context=context)
        first = False
        try:
            from .actions import parse_action

            action = _action_obj_to_dict(parse_action(response_text))
        except Exception as exc:
            malformed += 1
            if malformed >= 3:
                return {"status": "model_format_stop", "model_calls": calls, "actions": executor.actions_sent - deterministic_start, "error": _safe_first_line(exc)}
            obs = dict(obs)
            obs["last_action_error"] = f"Malformed model action: {type(exc).__name__}"
            continue
        malformed = 0
        if action.get("action_type") == "status":
            return {
                "status": "model_terminal",
                "goal_status": action.get("goal_status"),
                "model_calls": calls,
                "actions": executor.actions_sent - deterministic_start,
            }
        executor.source = purpose
        try:
            executor.execute(action)
        except BudgetStop:
            raise
        except PilotStop:
            return {
                "status": "uncertain",
                "model_calls": calls,
                "actions": executor.actions_sent - deterministic_start,
                "error": "issued UI action failed; episode is terminal",
            }
        obs = executor.observe()
    return {
        "status": "action_budget_stop" if executor.actions_sent >= executor.max_actions else "model_call_stop",
        "model_calls": calls,
        "actions": executor.actions_sent - deterministic_start,
    }


def _run_plan(
    plan: Mapping[str, Any],
    bindings: Mapping[str, Any],
    executor: AndroidActionExecutor,
    decision: Callable[[dict[str, Any]], dict[str, Any]] | None,
    mode: str,
) -> dict[str, Any]:
    runtime = _runtime_module(required=True)
    function = getattr(runtime, "run_plan", None)
    if not callable(function):
        raise PilotStop("selective_runtime.run_plan() is required for compiled arms.")
    executor.source = "plan"
    # This exact call is the boundary shared with the runtime agent.  The
    # runtime owns all guard checks and local-rejoin attempts.
    result = function(
        plan=dict(plan),
        bindings=dict(bindings),
        adapter=executor,
        decision=decision,
        mode=mode,
        max_actions=executor.max_actions - executor.actions_sent,
    )
    if not isinstance(result, Mapping):
        raise PilotStop("selective_runtime.run_plan() returned malformed data.")
    return dict(result)


def _scoped_prompt(
    goal_prompt: str,
    step: Mapping[str, Any],
    prior_action: Mapping[str, Any] | None,
    suffix_start: int | None,
    remaining_actions: int | None = None,
) -> str:
    return (
        "Full task request and all constraints:\n"
        + goal_prompt
        + "\n\nCurrent guarded-program node intent:\n"
        + str(step.get("intent", ""))
        + "\n\nAlready-issued action evidence:\n"
        + json.dumps(prior_action, ensure_ascii=True, sort_keys=True)
        + "\n\nThe model statement that an action succeeded is not evidence of a UI postcondition. "
        "At this node, issue at most one native Android JSON action using the current screenshot and accessibility tree. "
        "Do not repeat the already-issued action. Preserve every task constraint above."
        + (f"\nThe deterministic suffix begins at plan step {suffix_start}." if suffix_start is not None else "")
        + (f"\nRemaining shared UI action budget: {remaining_actions}." if remaining_actions is not None else "")
    )


def _extract_prompt(plan: Mapping[str, Any], goal_text: str) -> list[dict[str, str]]:
    slots = {str(name): str(description) for name, description in (plan.get("slots") or {}).items()}
    return [
        {
            "role": "system",
            "content": "Extract only the user supplied task values as one JSON object. Do not add explanation.",
        },
        {
            "role": "user",
            "content": (
                "Return exactly one JSON object with one scalar value for each named plan slot. "
                "Use the task request only. Do not infer hidden parameters or inspect the benchmark.\n"
                + json.dumps({"slot_descriptions": slots, "task_goal_text": goal_text}, ensure_ascii=True, indent=2)
            ),
        },
    ]


def _extract_binding(
    plan: Mapping[str, Any],
    goal_text: str,
    client: Any,
    episode_id: str,
    out_dir: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Make the one bounded compiled-arm extraction call from goal text."""

    slots = plan.get("slots") or {}
    if isinstance(slots, Mapping) and not slots:
        return {}, {
            "status": "valid",
            "provenance": "deterministic_empty_slots",
            "no_model_call": True,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "cost_usd": 0,
            },
        }

    messages = _extract_prompt(plan, goal_text)
    response, usage = _call_client(client, messages, episode_id)
    _record_call(out_dir, episode_id, 1, "binding_extraction", messages, response, usage)
    try:
        value = _json_from_text(response_content(response))
    except Exception as exc:
        return None, {"status": "invalid", "error": _safe_first_line(exc), "usage": usage}
    slots = set((plan.get("slots") or {}).keys())
    if set(value) != slots:
        return None, {"status": "invalid", "error": "extracted binding keys do not match plan slots", "usage": usage}
    if any(not isinstance(item, (str, int, float, bool)) for item in value.values()):
        return None, {"status": "invalid", "error": "extracted binding values must be scalar", "usage": usage}
    return dict(value), {"status": "valid", "usage": usage}


def _validate_binding_render_rules(
    plan: Mapping[str, Any] | None,
    rules: Mapping[str, Any] | None,
) -> dict[str, dict[str, str]] | None:
    """Validate the optional literal binding rendering config before UI work."""
    if rules is None:
        return None
    if not isinstance(plan, Mapping):
        raise PlanInvalid("binding_render_rules require a plan with declared slots")
    slots = plan.get("slots")
    if not isinstance(slots, Mapping):
        raise PlanInvalid("binding_render_rules require plan.slots")
    if not isinstance(rules, Mapping):
        raise PlanInvalid("binding_render_rules must be a mapping")
    normalized: dict[str, dict[str, str]] = {}
    for slot, rule in rules.items():
        if not isinstance(slot, str) or slot not in slots:
            raise PlanInvalid(f"binding_render_rules contains unknown slot {slot!r}")
        if not isinstance(rule, Mapping) or set(rule) != {"prefix", "suffix"}:
            raise PlanInvalid(f"binding_render_rules[{slot!r}] needs prefix and suffix strings")
        prefix, suffix = rule.get("prefix"), rule.get("suffix")
        if not isinstance(prefix, str) or not isinstance(suffix, str):
            raise PlanInvalid(f"binding_render_rules[{slot!r}] needs prefix and suffix strings")
        normalized[slot] = {"prefix": prefix, "suffix": suffix}
    return normalized


def _render_binding_once(
    binding: Mapping[str, Any],
    rules: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Render selected extracted slots once, retaining the raw binding."""
    if not isinstance(binding, Mapping):
        raise PlanInvalid("extracted binding must be a mapping")
    raw = copy.deepcopy(dict(binding))
    rendered = copy.deepcopy(raw)
    for slot, rule in rules.items():
        if slot not in raw:
            raise PlanInvalid(f"extracted binding has no slot {slot!r}")
        value = raw[slot]
        if not isinstance(value, (str, int, float, bool)):
            raise PlanInvalid(f"extracted binding slot {slot!r} is not scalar")
        rendered[slot] = f"{rule['prefix']}{value}{rule['suffix']}"
    return rendered, {"raw": raw, "rendered": copy.deepcopy(rendered), "rules": copy.deepcopy(dict(rules))}


def _make_local_decision(
    goal_prompt: str,
    executor: AndroidActionExecutor,
    client: Any,
    out_dir: Path,
    episode_id: str,
    recovery_context_policy: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    validate_recovery_context_policy(recovery_context_policy)
    assistant = _agent(MODEL, client) if recovery_context_policy is None else None
    calls = {"n": 0}

    def decide(payload: dict[str, Any]) -> dict[str, Any]:
        assistant_agent = assistant
        observation = dict(payload.get("observation") or executor.observe())
        step = {"intent": payload.get("intent", "")}
        previous = payload.get("previous_action") or payload.get("last_action")
        context = None
        if recovery_context_policy is not None:
            context = build_context(
                recovery_context_policy,
                goal_text=executor.goal_text,
                goal_prompt=goal_prompt,
                observation=observation,
                issued_records=executor.records,
                node_intent=str(step.get("intent") or ""),
                remaining_actions=executor.max_actions - executor.actions_sent,
            )
            prompt = context.prompt
            assistant_agent = _agent(MODEL, client)
        else:
            prompt = _scoped_prompt(
                goal_prompt,
                step,
                previous,
                payload.get("pc"),
                executor.max_actions - executor.actions_sent,
            )
        reply, usage = assistant_agent.act(prompt, observation)
        calls["n"] += 1
        _record_call(
            out_dir,
            episode_id,
            calls["n"],
            "local_rejoin",
            prompt,
            {"text": reply},
            usage,
            context=context,
        )
        try:
            from .actions import parse_action

            action = _action_obj_to_dict(parse_action(reply))
        except Exception:
            return {"action": None, "usage": usage}
        if action.get("action_type") == "status":
            return {"stop": True, "usage": usage}
        return {"action": action, "usage": usage}

    return decide


def _handoff_context(plan: Mapping[str, Any], result: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any] | None, int]:
    pc = int(result.get("pc", 0) or 0)
    steps = list(plan.get("steps") or [])
    step = steps[pc] if 0 <= pc < len(steps) else {"intent": "continue the task"}
    trace = list(result.get("trace") or [])
    previous = trace[-1].get("action") if trace and isinstance(trace[-1], Mapping) else None
    return step, previous, pc


def _private_binding_map(out: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    path = out / spec["private_bindings"]
    data = _json_read(path)
    mapping: dict[str, dict[str, Any]] = {}
    for family, rows in (data.get("families") or {}).items():
        for index, row in enumerate(rows, start=1):
            mapping[f"{family}/b{index:02d}"] = dict(row)
    return mapping


def _ledger_has_episode(ledger: Any, episode_id: str) -> bool:
    checker = getattr(ledger, "has_episode", None)
    if not callable(checker):
        raise BudgetStop("Shared ledger cannot verify prior episode receipts.")
    try:
        return bool(checker(episode_id))
    except BudgetStop:
        raise
    except Exception as exc:
        raise BudgetStop("Shared ledger receipt lookup failed; no replay is safe.") from exc


def _terminal_oracle(env: Any) -> tuple[bool | None, str | None]:
    try:
        reward = float(env.reward())
        return reward >= 1.0, None
    except Exception as exc:
        return None, _safe_first_line(exc)


def _validate_completion_policy(policy: str | None) -> None:
    if policy not in (None, "reactive_handoff"):
        raise ValueError(
            f"unknown completion_policy {policy!r}; expected None or 'reactive_handoff'"
        )


def _completion_handoff_prompt(
    goal_prompt: str,
    compiled_plan_step_boundary: int,
    remaining_actions: int,
    completed_steps: Sequence[Mapping[str, Any]],
    issued_actions: Sequence[Mapping[str, Any]],
) -> str:
    return (
        "Full task request and all constraints:\n"
        + goal_prompt
        + "\n\nVerified prefix complete != task complete. The guarded plan prefix "
        "completed with its final guards verified. Continue the full task reactively "
        "from the current observation. Do not reset the environment, restart the "
        "episode, or replay any action already issued.\n"
        + "Completed guarded plan steps: "
        + json.dumps(list(completed_steps), ensure_ascii=True, separators=(",", ":"))
        + "\nIssued action evidence: "
        + json.dumps(list(issued_actions), ensure_ascii=True, separators=(",", ":"))
        + "\n"
        + f"Verified compiled plan step boundary: {compiled_plan_step_boundary}.\n"
        + f"Remaining shared UI action budget: {remaining_actions}."
    )


def _run_one_episode(
    out: Path,
    spec: Mapping[str, Any],
    row: Mapping[str, Any],
    binding_row: Mapping[str, Any],
    *,
    env: Any,
    client: Any,
    budget: Any,
    plan: Mapping[str, Any] | None,
    binding_render_rules: Mapping[str, Any] | None = None,
    completion_policy: str | None = None,
    recovery_context_policy: str | None = None,
) -> dict[str, Any]:
    validate_recovery_context_policy(recovery_context_policy)
    _validate_completion_policy(completion_policy)
    binding_render_rules = _validate_binding_render_rules(plan, binding_render_rules)
    family = str(row["family"])
    arm = str(row["arm"])
    episode_id = str(row["id"])
    episode_dir = out / "episodes" / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    try:
        episode_dir.chmod(0o700)
    except OSError:
        pass
    state_path = episode_dir / "state.json"
    state = {
        "record_type": "episode-state",
        "status": "running",
        "episode_id": episode_id,
        "family": family,
        "arm": arm,
        "binding_id": row["binding_id"],
        "seed": row["seed"],
        "started_unix": time.time(),
    }
    atomic_json(state_path, state, private=True)
    trajectory = episode_dir / "trajectory.jsonl"
    if trajectory.exists() and trajectory.stat().st_size:
        raise PilotStop("Episode directory already contains actions; it cannot be resumed.")
    trajectory.write_text("")
    try:
        _begin_episode(client, episode_id)
        from . import android_env
        from .conditions import build_prompt as condition_prompt

        task = android_env.get_task(family, "discover", int(row["seed"]))
        goal = android_env.goal_text(task)
        try:
            _before_ui_action(budget)
            env.reset(task)
        except Exception as exc:
            raise PilotStop(f"task reset failed: {_safe_first_line(exc)}") from None
        initial_obs = dict(env.observe(goal) if callable(getattr(env, "observe", None)) else env._observe(goal))
        initial_meta = _save_obs(episode_dir, "initial", initial_obs)
        append_jsonl(trajectory, {"record_type": "initial", "obs": initial_meta}, private=True)
        goal_prompt = condition_prompt("discover", family, goal)
        executor = AndroidActionExecutor(
            env, episode_dir, episode_id, int(spec["action_budget"]), budget, goal
        )
        extraction = None
        extraction_result = None
        if arm == "reactive":
            run_result = _reactive_loop(
                executor,
                goal_prompt,
                episode_id,
                client,
                episode_dir,
                initial_obs,
                recovery_context_policy=recovery_context_policy,
            )
        elif plan is None:
            run_result = {"status": "skipped_unbuildable", "reason": "family plan unavailable"}
        else:
            plan = validate_plan(plan)
            extraction, extraction_result = _extract_binding(
                plan, goal, client, episode_id, episode_dir
            )
            if extraction is None:
                run_result = {"status": "extraction_invalid", "extraction": extraction_result}
            else:
                decision = None
                runtime_binding = extraction
                binding_rendering = None
                if binding_render_rules is not None:
                    runtime_binding, binding_rendering = _render_binding_once(
                        extraction, binding_render_rules
                    )
                if arm == "local_rejoin":
                    decision = _make_local_decision(
                        goal_prompt,
                        executor,
                        client,
                        episode_dir,
                        episode_id,
                        recovery_context_policy=recovery_context_policy,
                    )
                plan_result = _run_plan(plan, runtime_binding, executor, decision, arm)
                run_result = {
                    "status": plan_result.get("status", "unknown"),
                    "plan": plan_result,
                    "extraction": extraction_result,
                }
                if binding_rendering is not None:
                    run_result["binding_rendering"] = binding_rendering
                if plan_result.get("status") == "handoff":
                    step, previous, pc = _handoff_context(plan, plan_result)
                    current_obs = dict(plan_result.get("observation") or executor.observe())
                    scope = _scoped_prompt(
                        goal_prompt,
                        step,
                        previous,
                        pc,
                        executor.max_actions - executor.actions_sent,
                    )
                    fallback = _reactive_loop(
                        executor,
                        goal_prompt,
                        episode_id,
                        client,
                        episode_dir,
                        current_obs,
                        purpose="full_fallback" if arm == "full_fallback" else "local_rejoin_fallback",
                        scoped_prompt=scope,
                        recovery_context_policy=recovery_context_policy,
                        node_intent=str(step.get("intent") or ""),
                    )
                    run_result["fallback"] = fallback
                elif (
                    completion_policy == "reactive_handoff"
                    and plan_result.get("status") == "complete"
                    and plan_result.get("verified") is True
                ):
                    compiled_plan_step_boundary = plan_result.get("pc")
                    if type(compiled_plan_step_boundary) is not int or compiled_plan_step_boundary < 0:
                        compiled_plan_step_boundary = len(plan.get("steps") or [])
                    plan_action_count = plan_result.get("action_count", plan_result.get("actions"))
                    if type(plan_action_count) is not int or plan_action_count < 0:
                        plan_action_count = executor.actions_sent
                    completed_steps = [
                        {"compiled_step": index + 1, "intent": str(step.get("intent", ""))}
                        for index, step in enumerate((plan.get("steps") or [])[:compiled_plan_step_boundary])
                        if isinstance(step, Mapping)
                    ]
                    issued_actions = []
                    for item in plan_result.get("trace") or []:
                        if not isinstance(item, Mapping) or item.get("issued") is not True:
                            continue
                        trace_step = item.get("pc")
                        if type(trace_step) is int and trace_step >= 0:
                            trace_step += 1
                        issued_actions.append(
                            {
                                "compiled_step": trace_step,
                                "action": copy.deepcopy(item.get("action")),
                            }
                        )
                    prefix_marker = {
                        "record_type": "prefix_verified",
                        "episode_id": episode_id,
                        "compiled_plan_step_boundary": compiled_plan_step_boundary,
                        "action_count": plan_action_count,
                    }
                    append_jsonl(trajectory, prefix_marker, private=True)
                    current_obs = dict(plan_result.get("observation") or executor.observe())
                    handoff_prompt = _completion_handoff_prompt(
                        goal_prompt,
                        compiled_plan_step_boundary,
                        executor.max_actions - executor.actions_sent,
                        completed_steps,
                        issued_actions,
                    )
                    fallback = _reactive_loop(
                        executor,
                        goal_prompt,
                        episode_id,
                        client,
                        episode_dir,
                        current_obs,
                        purpose="reactive_handoff",
                        scoped_prompt=handoff_prompt,
                        recovery_context_policy=recovery_context_policy,
                    )
                    run_result["prefix_verified"] = prefix_marker
                    run_result["reactive_handoff"] = fallback
        success, oracle_error = _terminal_oracle(env)
        final = {
            "record_type": "final",
            "episode_id": episode_id,
            "family": family,
            "arm": arm,
            "binding_id": row["binding_id"],
            "success": success,
            "oracle_error": oracle_error,
            "actions": executor.actions_sent,
            "run": run_result,
            "ended_unix": time.time(),
        }
        if binding_render_rules is not None and isinstance(run_result, Mapping):
            final["binding_rendering"] = copy.deepcopy(run_result.get("binding_rendering"))
        append_jsonl(trajectory, final, private=True)
        state.update(status="done", result=final, ended_unix=time.time())
        atomic_json(state_path, state, private=True)
        return final
    except BudgetStop as exc:
        state.update(status="budget_stopped", stop_reason=_safe_budget_reason(exc), error_type=type(exc).__name__, ended_unix=time.time())
        atomic_json(state_path, state, private=True)
        raise
    except PilotStop as exc:
        state.update(status="interrupted", error=_safe_first_line(exc), ended_unix=time.time())
        atomic_json(state_path, state, private=True)
        raise
    except Exception as exc:
        state.update(status="interrupted", error=_safe_first_line(exc), ended_unix=time.time())
        atomic_json(state_path, state, private=True)
        raise
    finally:
        # This is task teardown.  The caller owns the shared emulator process.
        try:
            env.close()
        except Exception:
            pass


def _recover_running(out: Path, spec: Mapping[str, Any]) -> int:
    changed = 0
    for row in spec.get("episodes") or []:
        path = out / "episodes" / row["id"] / "state.json"
        if not path.is_file():
            continue
        try:
            state = _json_read(path)
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") == "running":
            state.update(status="interrupted", recovery_note="prior running episode is terminal; no UI action or receipt is resent", ended_unix=time.time())
            atomic_json(path, state, private=True)
            changed += 1
    return changed


def run(
    out: Path | str = DEFAULT_OUT,
    *,
    max_episodes: int | None = None,
    families: Sequence[str] | None = None,
    client_factory: Callable[[Mapping[str, Any], str], Any] | None = None,
    env_factory: Callable[[], Any] | None = None,
    env_file: Path | str | None = None,
) -> dict[str, Any]:
    """Run balanced serving rows with durable terminal states."""

    out = Path(out).resolve()
    spec = load_spec(out)
    budget = _budget_module(required=True)
    _runtime_module(required=True)
    _recover_running(out, spec)
    family_filter = set(families) if families is not None else set(FAMILIES)
    if not family_filter <= set(FAMILIES):
        raise PilotStop("Run family filter contains an out-of-scope family.")
    plans: dict[str, Mapping[str, Any] | None] = {}
    for family in FAMILIES:
        path = out / spec["plans"][family]
        try:
            plans[family] = validate_plan(_json_read(path)) if path.is_file() else None
        except Exception:
            plans[family] = None
    ledger = _make_ledger(budget, spec)
    if not any(plans.get(family) is not None for family in family_filter):
        result = _progress_payload(out, spec, ledger, "no_plans", [])
        result["stop_reason"] = "No executable guarded plan is available; reactive serving was not started."
        atomic_json(out / "progress.json", result)
        return result
    private_map = _private_binding_map(out, spec)
    live_spec = dict(spec)
    live_spec["model_locks"] = _validated_model_locks(budget, spec)
    episodes_done = 0
    batch_status = "complete"
    progress_rows: list[dict[str, Any]] = []
    with _exclusive_context(budget, spec, "run"):
        for row in spec.get("episodes") or []:
            if row.get("family") not in family_filter:
                continue
            episode_dir = out / "episodes" / row["id"]
            state_path = episode_dir / "state.json"
            previous = _json_read(state_path) if state_path.is_file() else None
            if previous and previous.get("status") in {"done", "budget_stopped", "interrupted", "skipped_unbuildable"}:
                progress_rows.append({"id": row["id"], "status": previous["status"]})
                continue
            if _ledger_has_episode(ledger, row["id"]):
                episode_dir.mkdir(parents=True, exist_ok=True)
                state = {
                    "record_type": "episode-state",
                    "status": "prior_receipt",
                    "episode_id": row["id"],
                    "reason": "shared ledger already contains a receipt; no UI replay",
                    "ended_unix": time.time(),
                }
                atomic_json(state_path, state, private=True)
                progress_rows.append({"id": row["id"], "status": "prior_receipt"})
                episodes_done += 1
                continue
            if max_episodes is not None and episodes_done >= max_episodes:
                batch_status = "pilot_limit"
                break
            family = row["family"]
            plan = plans[family] if row["arm"] != "reactive" else None
            binding_row = private_map[f"{family}/{row['binding_id']}"]
            if row["arm"] != "reactive" and plan is None:
                episode_dir.mkdir(parents=True, exist_ok=True)
                state = {"record_type": "episode-state", "status": "skipped_unbuildable", "episode_id": row["id"], "reason": "family plan unavailable"}
                atomic_json(episode_dir / "state.json", state, private=True)
                progress_rows.append({"id": row["id"], "status": state["status"]})
                episodes_done += 1
                continue
            episode_id = row["id"]
            client = client_factory(live_spec, episode_id) if client_factory else _make_client(budget, ledger, live_spec, episode_id, env_file)
            env = env_factory() if env_factory else _new_env()
            try:
                result = _run_one_episode(out, spec, row, binding_row, env=env, client=client, budget=budget, plan=plan)
                progress_rows.append({"id": episode_id, "status": "done", "success": result.get("success")})
            except BudgetStop as exc:
                batch_status = "budget_stopped"
                stop_reason = _safe_budget_reason(exc)
                progress_rows.append({"id": episode_id, "status": "budget_stopped", "reason": stop_reason, "error_type": type(exc).__name__})
                atomic_json(out / "progress.json", _progress_payload(out, spec, ledger, batch_status, progress_rows))
                break
            except PilotStop as exc:
                batch_status = "interrupted"
                progress_rows.append({"id": episode_id, "status": "interrupted", "reason": str(exc)})
                break
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            episodes_done += 1
            atomic_json(out / "progress.json", _progress_payload(out, spec, ledger, batch_status, progress_rows))
    result = _progress_payload(out, spec, ledger, batch_status, progress_rows)
    atomic_json(out / "progress.json", result)
    return result


def _new_env() -> Any:
    from .android_env import AndroidWorldEnv

    return AndroidWorldEnv(boot_if_needed=False)


def _progress_payload(out: Path, spec: Mapping[str, Any], ledger: Any, batch_status: str, current: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    observed = {str(item["id"]): dict(item) for item in current}
    for row in spec.get("episodes") or []:
        path = out / "episodes" / row["id"] / "state.json"
        if path.is_file():
            try:
                state = _json_read(path)
                item = {"id": row["id"], "status": state.get("status")}
                if state.get("status") == "done":
                    result = state.get("result") or {}
                    item.update({"success": result.get("success"), "actions": result.get("actions")})
                rows.append(item)
                continue
            except (OSError, json.JSONDecodeError):
                pass
        rows.append(observed.get(row["id"], {"id": row["id"], "status": "pending"}))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    try:
        budget_summary = ledger.summary()
    except Exception as exc:
        budget_summary = {"error": _safe_first_line(exc)}
    return {
        "record_type": "selective-progress",
        "spec_sha256": spec["spec_sha256"],
        "batch_status": batch_status,
        "planned": len(rows),
        "counts": counts,
        "episodes": rows,
        "budget": budget_summary,
    }


def _number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except Exception:
        return None


def _sum_known(items: Iterable[Any], key: str) -> tuple[Decimal | None, int]:
    total = Decimal("0")
    missing = 0
    for item in items:
        value = _number((item or {}).get(key) if isinstance(item, Mapping) else None)
        if value is None:
            missing += 1
        else:
            total += value
    return (None if missing else total), missing


def _usage_summary(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    records = list(records)
    summary: dict[str, Any] = {"calls": len(records), "missing": {}}
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd"):
        total, missing = _sum_known(records, key)
        summary[key] = int(total) if total is not None and key != "cost_usd" else (str(total) if total is not None else None)
        summary["missing"][key] = missing
    if summary["prompt_tokens"] is not None and summary["completion_tokens"] is not None:
        summary["total_tokens"] = summary["prompt_tokens"] + summary["completion_tokens"]
    else:
        summary["total_tokens"] = None
    return summary


def _episode_usage(path: Path) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    trajectory = path / "trajectory.jsonl"
    if trajectory.is_file():
        try:
            for line in trajectory.read_text().splitlines():
                value = json.loads(line)
                if value.get("record_type") == "model_call":
                    records.append(value.get("usage") or {})
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    return _usage_summary(records)


def _build_usage(out: Path, family: str) -> dict[str, Any]:
    records: list[Mapping[str, Any]] = []
    build_dir = out / "build" / family
    for path in sorted(build_dir.glob("*_response.json")):
        try:
            records.append(_json_read(path).get("usage") or {})
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return _usage_summary(records)


def analyze(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    """Produce an honest feasibility/cost report from every attempted arm."""

    out = Path(out).resolve()
    spec = load_spec(out)
    try:
        analyzer = importlib.import_module(".selective_analyze", __package__)
    except (ImportError, ModuleNotFoundError):
        analyzer = None
    delegated = getattr(analyzer, "analyze", None) if analyzer is not None else None
    if callable(delegated):
        return delegated(out, spec)
    ledger = None
    budget = _budget_module(required=False)
    if budget is not None:
        try:
            ledger = _make_ledger(budget, spec)
        except Exception:
            ledger = None
    rows: list[dict[str, Any]] = []
    for item in spec.get("episodes") or []:
        path = out / "episodes" / item["id"] / "state.json"
        if not path.is_file():
            rows.append({"id": item["id"], "status": "pending", "family": item["family"], "arm": item["arm"], "usage": _usage_summary([])})
            continue
        try:
            state = _json_read(path)
        except (OSError, json.JSONDecodeError):
            rows.append({"id": item["id"], "status": "unreadable", "family": item["family"], "arm": item["arm"], "usage": _usage_summary([])})
            continue
        result = state.get("result") or {}
        rows.append({
            "id": item["id"],
            "family": item["family"],
            "arm": item["arm"],
            "binding_id": item["binding_id"],
            "status": state.get("status"),
            "success": result.get("success"),
            "actions": result.get("actions"),
            "run": result.get("run"),
            "usage": _episode_usage(path.parent),
        })
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        grouped.setdefault(row["family"], {}).setdefault(row["arm"], []).append(row)
    comparisons: dict[str, Any] = {}
    for family, arms in grouped.items():
        comparisons[family] = {}
        for arm, items in arms.items():
            attempted = [item for item in items if item.get("status") == "done"]
            successes = [item for item in attempted if item.get("success") is True]
            comparisons[family][arm] = {
                "planned": len(items),
                "attempted": len(attempted),
                "successes": len(successes),
                "success_rate": (len(successes) / len(attempted)) if attempted else None,
                "actions_known": all(item.get("actions") is not None for item in attempted),
                "usage": _usage_summary([item.get("usage") or {} for item in attempted]),
            }
    build_report: dict[str, Any] = {}
    for family in FAMILIES:
        build_report[family] = _build_usage(out, family)
    training_report: dict[str, Any] = {}
    training_manifest = out / "training_manifest.json"
    if training_manifest.is_file():
        try:
            training_data = _json_read(training_manifest)
            for family, items in (training_data.get("families") or {}).items():
                calls = []
                for item in items:
                    calls.extend((item.get("usage_records") or []))
                    trajectory = Path(str(item.get("trajectory", "")))
                    if not trajectory.is_absolute():
                        trajectory = out / trajectory
                    if trajectory.is_file():
                        training_report.setdefault(family, []).append({**item, "usage": _episode_usage(trajectory.parent)})
                if family not in training_report:
                    training_report[family] = []
        except (OSError, UnicodeError, json.JSONDecodeError):
            training_report = {"error": "training_manifest unreadable"}
    budget_summary = None
    if ledger is not None:
        try:
            budget_summary = ledger.summary()
        except Exception as exc:
            budget_summary = {"error": _safe_first_line(exc)}
    economics: dict[str, Any] = {"serving_only": {}, "acquisition_plus_serving": {}}
    for family, arms in grouped.items():
        build_cost = _build_usage(out, family).get("cost_usd")
        economics["serving_only"][family] = {}
        economics["acquisition_plus_serving"][family] = {}
        for arm, items in arms.items():
            attempted = [item for item in items if item.get("status") == "done"]
            serving_cost, serving_missing = _sum_known(
                [(item.get("usage") or {}) for item in attempted], "cost_usd"
            )
            serving_value = str(serving_cost) if serving_cost is not None else None
            economics["serving_only"][family][arm] = {
                "attempted": len(attempted),
                "successes": sum(item.get("success") is True for item in attempted),
                "actual_cost_usd": serving_value,
                "unknown_cost_calls": serving_missing,
            }
            total = None
            if serving_cost is not None and (arm == "reactive" or build_cost is not None):
                total = str(serving_cost + (Decimal(str(build_cost)) if arm != "reactive" else Decimal("0")))
            economics["acquisition_plus_serving"][family][arm] = {
                "attempted": len(attempted),
                "actual_or_known_cost_usd": total,
                "shared_build_counted_once_for_this_alternative": arm != "reactive",
                "unknown_cost_calls": serving_missing + (_build_usage(out, family).get("missing", {}).get("cost_usd", 0) if arm != "reactive" else 0),
            }
    report = {
        "record_type": "selective-analysis",
        "spec_sha256": spec["spec_sha256"],
        "prototype_scope": "guarded-plan feasibility only",
        "selector_algorithm_implemented": False,
        "families": list(FAMILIES),
        "arms": list(ARMS),
        "bindings_per_family": TEST_BINDING_COUNT,
        "planned_serving_episodes": len(spec.get("episodes") or []),
        "episodes": rows,
        "comparisons": comparisons,
        "economics": economics,
        "build": build_report,
        "training": training_report,
        "budget": budget_summary,
        "accounting": {
            "shared_build_physically_charged_once_per_family": True,
            "shared_build_charged_once_per_alternative_in_cumulative_economics": True,
            "training_acquisition_separate_from_marginal_reuse": True,
            "unknown_costs_are_not_zero": True,
            "historical_demo_acquisition_separate": True,
            "inference_limit": "two bindings per family cannot establish non-inferiority or generalization",
        },
    }
    atomic_json(out / "analysis.json", report)
    return report


def _train_episode(
    out: Path,
    spec: Mapping[str, Any],
    family: str,
    seed: int,
    attempt: int,
    *,
    env: Any,
    client: Any,
    budget: Any,
) -> dict[str, Any]:
    """Capture one full pre/post-observation discover trace."""

    from . import android_env
    from .actions import parse_action
    from .conditions import build_prompt as condition_prompt

    task = android_env.get_task(family, "discover", int(seed))
    goal = android_env.goal_text(task)
    episode_id = f"{NAMESPACE}/train/{family}/s{seed}/a{attempt}"
    episode_dir = out / "training" / family / f"s{seed}_a{attempt}"
    trajectory = episode_dir / "trajectory.jsonl"
    _before_ui_action(budget)
    env.reset(task)
    obs = dict(env.observe(goal) if callable(getattr(env, "observe", None)) else env._observe(goal))
    initial_meta = _save_obs(episode_dir, "initial", obs)
    append_jsonl(trajectory, {"record_type": "initial", "goal_text": goal, "obs": initial_meta}, private=True)
    executor = AndroidActionExecutor(env, episode_dir, episode_id, MAX_ACTIONS, budget, goal)
    agent = _agent(MODEL, client)
    prompt = condition_prompt("discover", family, goal)
    calls = 0
    malformed = 0
    success_status = False
    for _step in range(MAX_ACTIONS):
        try:
            response_text, usage = agent.act(prompt if calls == 0 else None, obs)
        except BudgetStop:
            raise
        calls += 1
        _record_call(episode_dir, episode_id, calls, "training_discover", prompt if calls == 1 else None, {"text": response_text}, usage)
        try:
            action = _action_obj_to_dict(parse_action(response_text))
        except Exception as exc:
            malformed += 1
            if malformed >= 3:
                break
            obs = dict(obs)
            obs["last_action_error"] = f"Malformed model action: {type(exc).__name__}"
            continue
        if action.get("action_type") == "status":
            success_status = action.get("goal_status") == "complete"
            break
        executor.source = "training_discover"
        try:
            executor.execute(action)
        except BudgetStop:
            raise
        except PilotStop:
            break
        obs = executor.observe()
    success, oracle_error = _terminal_oracle(env)
    # The oracle is used here only to identify a successful demonstration for
    # the next offline build.  It is never placed in builder input.
    final = {
        "record_type": "final",
        "success": success,
        "model_declared_complete": success_status,
        "oracle_error": oracle_error,
        "actions": executor.actions_sent,
        "model_calls": calls,
        "goal_text": goal,
        "family": family,
        "seed": seed,
        "attempt": attempt,
    }
    append_jsonl(trajectory, final, private=True)
    return {
        "family": family,
        "seed": seed,
        "attempt": attempt,
        "success": success is True,
        "goal_text": goal,
        "trajectory": str(trajectory.relative_to(out)),
        "trajectory_sha256": sha256_file(trajectory),
        "actions": executor.actions_sent,
        "model_calls": calls,
        "oracle_error": oracle_error,
        "source": "fresh_train",
    }


def _training_attempt_paths(out: Path, family: str, seed: int, attempt: int) -> tuple[str, Path, Path, Path]:
    episode_id = f"{NAMESPACE}/train/{family}/s{seed}/a{attempt}"
    episode_dir = out / "training" / family / f"s{seed}_a{attempt}"
    return episode_id, episode_dir, episode_dir / "state.json", episode_dir / "trajectory.jsonl"


def _training_terminal_record(
    out: Path,
    family: str,
    seed: int,
    attempt: int,
    ledger: Any,
) -> dict[str, Any] | None:
    """Inspect an old training attempt before any reset, model call, or truncate."""

    episode_id, episode_dir, state_path, trajectory = _training_attempt_paths(out, family, seed, attempt)
    state: dict[str, Any] | None = None
    if state_path.is_file():
        try:
            value = _json_read(state_path)
            state = dict(value) if isinstance(value, Mapping) else {"status": "invalid_state"}
        except (OSError, UnicodeError, json.JSONDecodeError):
            state = {"status": "unreadable_state"}
        status = state.get("status")
        if status == "running":
            state.update(
                status="interrupted",
                recovery_note="prior training attempt is terminal; raw trace retained and never replayed",
                ended_unix=time.time(),
            )
            atomic_json(state_path, state, private=True)
            return {"family": family, "seed": seed, "attempt": attempt, "success": False, "status": "interrupted", "trajectory": str(trajectory.relative_to(out)) if trajectory.exists() else None}
        if status:
            result = state.get("result") or {}
            return {
                "family": family,
                "seed": seed,
                "attempt": attempt,
                "success": result.get("success") is True,
                "status": str(status),
                "goal_text": result.get("goal_text", ""),
                "trajectory": str(trajectory.relative_to(out)) if trajectory.exists() else None,
                "trajectory_sha256": sha256_file(trajectory) if trajectory.is_file() else None,
                "raw_retained": True,
            }
    if _ledger_has_episode(ledger, episode_id):
        # A receipt without state or trace is still a terminal refusal.  Do
        # not create a trajectory, reset the phone, or ask the model again.
        episode_dir.mkdir(parents=True, exist_ok=True)
        terminal = {
            "record_type": "training-state",
            "status": "prior_receipt",
            "episode_id": episode_id,
            "family": family,
            "seed": seed,
            "attempt": attempt,
            "reason": "shared ledger contains an earlier receipt; no training replay",
            "ended_unix": time.time(),
        }
        atomic_json(state_path, terminal, private=True)
        return {"family": family, "seed": seed, "attempt": attempt, "success": False, "status": "prior_receipt", "trajectory": str(trajectory.relative_to(out)) if trajectory.exists() else None, "raw_retained": trajectory.exists()}
    if trajectory.exists():
        # Any nonempty trace is action evidence.  Even an empty pre-created
        # file is retained as an existing artifact and never truncated.
        episode_dir.mkdir(parents=True, exist_ok=True)
        terminal = {
            "record_type": "training-state",
            "status": "prior_action_evidence" if trajectory.stat().st_size else "prior_artifact",
            "episode_id": episode_id,
            "family": family,
            "seed": seed,
            "attempt": attempt,
            "reason": "existing training trace/artifact; no replay",
            "ended_unix": time.time(),
        }
        atomic_json(state_path, terminal, private=True)
        return {"family": family, "seed": seed, "attempt": attempt, "success": False, "status": terminal["status"], "trajectory": str(trajectory.relative_to(out)), "trajectory_sha256": sha256_file(trajectory), "raw_retained": True}
    return None


def _persist_training_manifest(out: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_unix"] = time.time()
    statuses = []
    for family in FAMILIES:
        rows = manifest.get("families", {}).get(family, [])
        statuses.append(any(row.get("success") is True for row in rows))
    manifest["status"] = "ready" if all(statuses) else "partial"
    atomic_json(out / "training_manifest.json", manifest, private=True)


def train(
    out: Path | str = DEFAULT_OUT,
    *,
    max_episodes: int | None = None,
    client_factory: Callable[[Mapping[str, Any], str], Any] | None = None,
    env_factory: Callable[[], Any] | None = None,
    env_file: Path | str | None = None,
) -> dict[str, Any]:
    """Run at most two fresh training attempts per family, first success wins.

    A prior attempt is classified before constructing a client or environment.
    Its state and raw trace are terminal evidence and are never truncated or
    replayed.  The manifest is updated after every classification/attempt so
    a second invocation can continue with the next candidate.
    """

    out = Path(out).resolve()
    spec = load_spec(out)
    budget = _budget_module(required=True)
    _runtime_module(required=True)
    ledger = _make_ledger(budget, spec)
    live_spec = dict(spec)
    live_spec["model_locks"] = _validated_model_locks(budget, spec)
    serving_seeds = {int(row["seed"]) for row in spec.get("episodes") or []}
    manifest_path = out / "training_manifest.json"
    if manifest_path.is_file():
        try:
            loaded = _json_read(manifest_path)
            manifest = dict(loaded) if isinstance(loaded, Mapping) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            manifest = None
        if manifest is None or manifest.get("record_type") != "selective-training":
            raise PilotStop("Existing training manifest is invalid; no training replay is safe.")
    else:
        manifest = {"record_type": "selective-training", "status": "pending", "families": {}, "created_unix": time.time()}
    families = manifest.setdefault("families", {})
    attempted_episodes = 0
    with _exclusive_context(budget, spec, "train"):
        for family in FAMILIES:
            family_rows = list(families.get(family) or [])
            # A successful attempt is the immutable training source for this
            # family.  No later candidate is needed.
            if any(row.get("success") is True for row in family_rows if isinstance(row, Mapping)):
                families[family] = family_rows
                _persist_training_manifest(out, manifest)
                continue
            for attempt in range(MAX_TRAIN_ATTEMPTS):
                if any(int(row.get("attempt", -1)) == attempt for row in family_rows if isinstance(row, Mapping)):
                    continue
                if max_episodes is not None and attempted_episodes >= max_episodes:
                    break
                seed = TRAIN_SEED_START + attempt
                while seed in serving_seeds:
                    seed += 1000
                existing = _training_terminal_record(out, family, seed, attempt, ledger)
                if existing is not None:
                    family_rows.append(existing)
                    attempted_episodes += 1
                    families[family] = family_rows
                    _persist_training_manifest(out, manifest)
                    continue
                episode_id, episode_dir, state_path, trajectory = _training_attempt_paths(out, family, seed, attempt)
                # All existing artefacts were checked above.  Establish the
                # running marker before a reset or first action, then create
                # the trace exactly once with exclusive creation semantics.
                episode_dir.mkdir(parents=True, exist_ok=True)
                try:
                    episode_dir.chmod(0o700)
                except OSError:
                    pass
                atomic_json(
                    state_path,
                    {
                        "record_type": "training-state",
                        "status": "running",
                        "episode_id": episode_id,
                        "family": family,
                        "seed": seed,
                        "attempt": attempt,
                        "started_unix": time.time(),
                    },
                    private=True,
                )
                try:
                    # No prior trajectory survived the checks.  Use x-mode so
                    # a race cannot erase an evidence file.
                    with trajectory.open("x", encoding="utf-8"):
                        pass
                except FileExistsError:
                    terminal = _training_terminal_record(out, family, seed, attempt, ledger)
                    if terminal is None:
                        raise PilotStop("Training trace appeared during start; no replay is safe.")
                    family_rows.append(terminal)
                    families[family] = family_rows
                    _persist_training_manifest(out, manifest)
                    continue
                attempted_episodes += 1
                env = None
                try:
                    client = client_factory(live_spec, episode_id) if client_factory else _make_client(budget, ledger, live_spec, episode_id, env_file)
                    _begin_episode(client, episode_id)
                    env = env_factory() if env_factory else _new_env()
                    result = _train_episode(out, spec, family, seed, attempt, env=env, client=client, budget=budget)
                    state = _json_read(state_path)
                    state.update(status="done", result=result, ended_unix=time.time())
                    atomic_json(state_path, state, private=True)
                    family_rows.append(result)
                except BudgetStop as exc:
                    state = _json_read(state_path) if state_path.is_file() else {"record_type": "training-state", "episode_id": episode_id}
                    state.update(status="budget_stopped", stop_reason=_safe_budget_reason(exc), error_type=type(exc).__name__, ended_unix=time.time())
                    row = {"family": family, "seed": seed, "attempt": attempt, "success": False, "status": "budget_stopped", "stop_reason": _safe_budget_reason(exc), "error_type": type(exc).__name__, "trajectory": str(trajectory.relative_to(out)) if trajectory.exists() else None}
                    family_rows.append(row)
                    atomic_json(state_path, state, private=True)
                    families[family] = family_rows
                    _persist_training_manifest(out, manifest)
                    raise
                except PilotStop as exc:
                    state = _json_read(state_path) if state_path.is_file() else {"record_type": "training-state", "episode_id": episode_id}
                    state.update(status="interrupted", error=_safe_first_line(exc), ended_unix=time.time())
                    atomic_json(state_path, state, private=True)
                    family_rows.append({"family": family, "seed": seed, "attempt": attempt, "success": False, "status": "interrupted", "error": _safe_first_line(exc), "trajectory": str(trajectory.relative_to(out)) if trajectory.exists() else None})
                    families[family] = family_rows
                    _persist_training_manifest(out, manifest)
                    break
                finally:
                    if env is not None:
                        try:
                            env.close()
                        except Exception:
                            pass
                families[family] = family_rows
                _persist_training_manifest(out, manifest)
                if result.get("success") is True:
                    break
            families[family] = family_rows
            _persist_training_manifest(out, manifest)
    _persist_training_manifest(out, manifest)
    return manifest


def _cli_exit_code(mode: str, result: Any, max_episodes: int | None = None) -> int:
    """Map semantic stage outcomes to a nonzero worker exit status."""

    if mode == "prepare" or mode == "analyze":
        return 0
    if mode == "build":
        values = list(result.values()) if isinstance(result, Mapping) else []
        if any(item.get("status") in {"budget_stopped", "provider_stopped"} for item in values if isinstance(item, Mapping)):
            return 2
        return 0 if values and all(item.get("status") in {"built", "already_built"} for item in values if isinstance(item, Mapping)) else 3
    if mode == "train":
        statuses = [item.get("status") for rows in (result.get("families") or {}).values() for item in rows if isinstance(item, Mapping)] if isinstance(result, Mapping) else []
        if "budget_stopped" in statuses or (isinstance(result, Mapping) and result.get("stop_reason")):
            return 2
        if max_episodes is not None:
            return 0
        return 0 if isinstance(result, Mapping) and result.get("status") == "ready" else 3
    if mode == "run":
        if not isinstance(result, Mapping):
            return 3
        batch_status = result.get("batch_status")
        if batch_status in {"budget_stopped", "provider_stopped"}:
            return 2
        if batch_status in {"interrupted", "no_plans"}:
            return 3
        if batch_status == "pilot_limit" and max_episodes is not None:
            return 0
        counts = result.get("counts") or {}
        if counts.get("pending", 0) or counts.get("running", 0):
            return 3
        return 0
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--train", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--analyze", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-episodes", type=int, default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    if args.prepare:
        result = prepare(args.out)
        print(json.dumps({"status": "prepared", "spec_sha256": result["spec_sha256"], "training": result["training"]["status"]}, indent=2))
        return 0
    if args.train:
        result = train(args.out, max_episodes=args.max_episodes, env_file=args.env_file)
    elif args.build:
        result = build(args.out, env_file=args.env_file)
    elif args.run:
        result = run(args.out, max_episodes=args.max_episodes, env_file=args.env_file)
    else:
        result = analyze(args.out)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    selected_mode = "prepare" if args.prepare else "train" if args.train else "build" if args.build else "run" if args.run else "analyze"
    return _cli_exit_code(selected_mode, result, args.max_episodes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {_safe_budget_reason(exc)}", file=sys.stderr)
        raise SystemExit(2)
