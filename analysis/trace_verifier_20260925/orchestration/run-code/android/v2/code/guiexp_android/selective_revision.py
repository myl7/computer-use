"""Versioned compiler-build migration for the selective Android pilot.

This module creates ``selective_20260915/versions/compiler_v2`` from the
current pilot output without replaying an old UI episode.  It reuses the
training traces and terminal serving evidence by immutable path/hash
references.  Serving still uses the original 4096-token reactive settings.
Only the builder profile is new: the budget facade must explicitly provide a
bounded GLM/Relace profile with low reasoning effort and a 16384-token output
cap.  Preparation never creates a model client or sends a request.

The expected budget facade entry point is::

    make_builder_client(model_locks, ledger=ledger, env_path=..., episode=...,
                        profile=BUILDER_PROFILE)

It must validate the provider metadata and reservation for that profile before
the first physical request.  A missing or unsupported profile stops the build
without a paid call.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import selective_pilot as pilot
from .budget_client import BudgetStop


VERSION = "compiler_v2"
VERSION_SCHEMA = "android-selective-revision/1"
VERSION_OUT = pilot.DEFAULT_OUT / "versions" / VERSION
BUILD_FAMILIES = ("MarkorCreateNote", "MarkorDeleteNote", "FilesMoveFile")
BUILDER_PROFILE_NAME = "builder_16384"
BUILDER_PROFILE = {
    "name": BUILDER_PROFILE_NAME,
    "model": pilot.MODEL,
    "provider": pilot.PROVIDER,
    "reasoning": {"effort": "low"},
    "max_tokens": 16384,
    "temperature": 0.0,
}
TERMINAL_STATUSES = frozenset(
    {"done", "budget_stopped", "interrupted", "prior_receipt", "skipped_unbuildable", "prior_action_evidence", "prior_artifact"}
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return pilot.sha256_file(path)


def _atomic_json(path: Path, value: Any) -> None:
    pilot.atomic_json(path, value)


def _origin_spec(origin: Path) -> dict[str, Any]:
    path = origin / "spec.json"
    spec = _read_json(path)
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or pilot.hash_json(body) != digest:
        raise pilot.PilotStop("Origin pilot spec hash is invalid.")
    return spec


def _receipt_ids(ledger: Path) -> set[str]:
    if not ledger.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{ledger.resolve()}?mode=ro", uri=True, timeout=2) as database:
            rows = database.execute(
                "SELECT id FROM calls WHERE id LIKE ? OR episode LIKE ?",
                (pilot.NAMESPACE + "/%", pilot.NAMESPACE + "/%"),
            ).fetchall()
    except sqlite3.Error as exc:
        raise pilot.PilotStop("Cannot inspect the shared ledger for version-ID collisions.") from exc
    return {str(row[0]) for row in rows if row and row[0] is not None}


def _state_for(origin: Path, row: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None, Path]:
    state_path = origin / "episodes" / str(row["id"]) / "state.json"
    if not state_path.is_file():
        return "pending", None, state_path
    try:
        state = _read_json(state_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unreadable", None, state_path
    return str(state.get("status") or "unknown"), state, state_path


def _new_id(old_id: str) -> str:
    prefix = pilot.NAMESPACE + "/"
    suffix = old_id[len(prefix) :] if old_id.startswith(prefix) else old_id
    return f"{pilot.NAMESPACE}/{VERSION}/{suffix}"


def _copy_private_bindings(origin: Path, version_out: Path) -> tuple[str, str]:
    source = origin / "private" / "evaluator_bindings.json"
    if not source.is_file():
        raise pilot.PilotStop("Origin evaluator-private bindings are missing.")
    destination = version_out / "private" / "evaluator_bindings.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _hash(destination) != _hash(source):
            raise pilot.PilotStop("Version private evaluator bindings changed.")
    else:
        shutil.copy2(source, destination)
    destination.chmod(0o600)
    destination.parent.chmod(0o700)
    return str(destination.relative_to(version_out)), _hash(destination)


def _preserve_revision_draft(out: Path) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{time.time_ns() % 1000000:06d}"
    destination = out / "preflight_drafts" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for relative in (
        Path("spec.json"),
        Path("origin.json"),
        Path("manifest.json"),
        Path("private/evaluator_bindings.json"),
    ):
        source = out / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    _atomic_json(
        destination / "draft_manifest.json",
        {
            "record_type": "compiler_v2-preflight-draft-preservation",
            "source_spec": str(out / "spec.json"),
            "source_spec_sha256": _hash(out / "spec.json"),
            "preserved_unix": time.time(),
        },
    )
    return destination


def _training_snapshot(origin: Path) -> dict[str, Any]:
    path = origin / "training_manifest.json"
    if not path.is_file():
        raise pilot.PilotStop("Origin training manifest is missing.")
    manifest = _read_json(path)
    families: dict[str, list[dict[str, Any]]] = {}
    for family, rows in (manifest.get("families") or {}).items():
        families[family] = []
        for row in rows or []:
            item = dict(row)
            trace = Path(str(item.get("trajectory") or ""))
            if not trace.is_absolute():
                trace = origin / trace
            item["trajectory_absolute"] = str(trace.resolve())
            item["trajectory_sha256_frozen"] = _hash(trace) if trace.is_file() else None
            families[family].append(item)
    return {
        "manifest_path": str(path.resolve()),
        "manifest_sha256": _hash(path),
        "families": families,
        "readonly": True,
    }


def prepare(
    origin: Path | str = pilot.DEFAULT_OUT,
    out: Path | str = VERSION_OUT,
) -> dict[str, Any]:
    """Freeze a new version from existing evidence, with no model generation."""

    origin = Path(origin).resolve()
    out = Path(out).resolve()
    origin_spec = _origin_spec(origin)
    if out.exists() and (out / "spec.json").is_file():
        try:
            return _load_version(out)
        except pilot.PilotStop:
            receipts = _receipt_ids(Path(origin_spec.get("ledger_absolute", pilot.SHARED_LEDGER)))
            if any(receipt.startswith(f"{pilot.NAMESPACE}/{VERSION}/") for receipt in receipts):
                raise pilot.PilotStop("Compiler_v2 already has receipts; its frozen spec cannot be amended.") from None
            _preserve_revision_draft(out)
    out.mkdir(parents=True, exist_ok=True)
    private_ref, private_sha = _copy_private_bindings(origin, out)
    training = _training_snapshot(origin)
    origin_files = {
        "spec.json": _hash(origin / "spec.json"),
        "manifest.json": _hash(origin / "manifest.json") if (origin / "manifest.json").is_file() else None,
        "training_manifest.json": training["manifest_sha256"],
        "private/evaluator_bindings.json": _hash(origin / "private/evaluator_bindings.json"),
    }
    receipts = _receipt_ids(Path(origin_spec.get("ledger_absolute", pilot.SHARED_LEDGER)))
    preserved: list[dict[str, Any]] = []
    versioned: list[dict[str, Any]] = []
    for old_row in origin_spec.get("episodes") or []:
        status, state, state_path = _state_for(origin, old_row)
        old_id = str(old_row["id"])
        trajectory_path = origin / "episodes" / old_id / "trajectory.jsonl"
        has_receipt = old_id in receipts or any(old_id in receipt for receipt in receipts)
        if status in TERMINAL_STATUSES or has_receipt:
            preserved.append({
                "id": old_id,
                "source_episode_id": old_id,
                "receipt_episode_id": old_id,
                "status": status if status != "pending" else "prior_receipt",
                "state_path": str(state_path.resolve()) if state_path.is_file() else None,
                "state_sha256": _hash(state_path) if state_path.is_file() else None,
                "trajectory_path": str(trajectory_path.resolve()) if trajectory_path.is_file() else None,
                "trajectory_sha256": _hash(trajectory_path) if trajectory_path.is_file() else None,
                "receipt_evidence": has_receipt,
                "reason": "origin terminal evidence is preserved and never replayed",
            })
            # Old skipped rows performed no UI action and are eligible for a
            # versioned replacement.  Other terminal rows remain historical.
            if status != "skipped_unbuildable":
                continue
        new_row = dict(old_row)
        new_row["id"] = _new_id(old_id)
        new_row["versioned_from"] = old_id
        new_row["source_episode_id"] = old_id
        new_row["receipt_episode_id"] = new_row["id"]
        new_row["version"] = VERSION
        versioned.append(new_row)
    new_ids = {row["id"] for row in versioned}
    if new_ids & receipts:
        raise pilot.PilotStop("Compiler_v2 episode IDs collide with an existing ledger receipt.")
    origin_record = {
        "schema": VERSION_SCHEMA,
        "version": VERSION,
        "origin_root": str(origin),
        "origin_spec_sha256": origin_spec["spec_sha256"],
        "origin_spec_file_sha256": _hash(origin / "spec.json"),
        "origin_source_manifest_sha256": origin_spec.get("source_manifest_sha256"),
        "origin_files": origin_files,
        "training": training,
        "preserved_terminal_rows": preserved,
        "versioned_rows": [{"id": row["id"], "versioned_from": row["versioned_from"]} for row in versioned],
        "shared_ledger": origin_spec.get("ledger_absolute", str(pilot.SHARED_LEDGER)),
        "created_unix": time.time(),
    }
    _atomic_json(out / "origin.json", origin_record)
    body = {
        "schema": pilot.SPEC_SCHEMA,
        "revision_schema": VERSION_SCHEMA,
        "revision": VERSION,
        "namespace": pilot.NAMESPACE,
        "model": origin_spec["model"],
        "provider": origin_spec["provider"],
        "model_locks": origin_spec["model_locks"],
        "families": list(pilot.FAMILIES),
        "arms": list(pilot.ARMS),
        "episodes": versioned,
        "action_budget": origin_spec["action_budget"],
        "local_assistance_max": origin_spec["local_assistance_max"],
        "obs_mode": origin_spec["obs_mode"],
        "temperature": origin_spec["temperature"],
        "max_completion_tokens": origin_spec["max_completion_tokens"],
        "ledger": origin_spec["ledger"],
        "run_lock": origin_spec["run_lock"],
        "ledger_absolute": origin_spec["ledger_absolute"],
        "run_lock_absolute": origin_spec["run_lock_absolute"],
        "private_bindings": private_ref,
        "private_binding_sha256": private_sha,
        "private_binding_visibility": "evaluator_only",
        "budget_manifest": None,
        "budget_manifest_sha256": None,
        "source_manifest": pilot.source_manifest(pilot.REPO_ROOT),
        "source_manifest_sha256": pilot._manifest_digest(pilot.source_manifest(pilot.REPO_ROOT)),
        "revision_source_sha256": _hash(Path(__file__).resolve()),
        "origin": "origin.json",
        "origin_sha256": None,
        "training": {"readonly_origin": training, "reuse_failed_cost_provenance": True},
        "reused_training_refs": training["families"],
        "imported_rows": {
            item["id"]: {
                "source_episode_id": item["source_episode_id"],
                "receipt_episode_id": item["receipt_episode_id"],
                "status": item["status"],
                "state_sha256": item.get("state_sha256"),
            }
            for item in preserved
        },
        "provenance": {
            "origin_root": str(origin),
            "origin_spec_sha256": origin_spec["spec_sha256"],
            "origin_source_manifest_sha256": origin_spec.get("source_manifest_sha256"),
            "origin_training_manifest_sha256": training["manifest_sha256"],
            "preserved_terminal_rows": preserved,
            "imported_rows": {
                item["id"]: {
                    "source_episode_id": item["source_episode_id"],
                    "receipt_episode_id": item["receipt_episode_id"],
                    "status": item["status"],
                    "state_sha256": item.get("state_sha256"),
                }
                for item in preserved
            },
        },
        "builder_profile": BUILDER_PROFILE,
        "plans": {family: f"plans/{family}.json" for family in pilot.FAMILIES},
        "reporting": {
            "preserve_origin_terminal_rows": True,
            "replace_old_skipped_without_ui": True,
            "serving_settings_unchanged": True,
            "builder_profile_only_new_setting": True,
        },
    }
    origin_record["origin_sha256"] = pilot.hash_json({k: v for k, v in origin_record.items() if k != "origin_sha256"})
    _atomic_json(out / "origin.json", origin_record)
    body["origin_sha256"] = _hash(out / "origin.json")
    # Freeze a serving-profile budget manifest in the new version directory.
    # This is metadata only.  It keeps the old 4096-token serving bounds while
    # allowing the origin manifest to remain immutable evidence.
    budget = pilot._budget_module(required=False)
    freeze = getattr(budget, "freeze_manifest", None) if budget is not None else None
    if callable(freeze):
        frozen = freeze(origin_spec["model_locks"], path=out / "manifest.json", extra={"revision": VERSION})
        body["budget_manifest"] = "manifest.json"
        body["budget_manifest_sha256"] = pilot.hash_json(_read_json(out / "manifest.json"))
    body["spec_sha256"] = pilot.hash_json(body)
    _atomic_json(out / "spec.json", body)
    return body


def _load_version(out: Path) -> dict[str, Any]:
    spec = _read_json(out / "spec.json")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if spec.get("schema") != pilot.SPEC_SCHEMA or spec.get("revision_schema") != VERSION_SCHEMA or pilot.hash_json(body) != digest:
        raise pilot.PilotStop("Compiler_v2 spec hash/schema validation failed.")
    origin = _read_json(out / spec["origin"])
    if _hash(out / spec["origin"]) != spec.get("origin_sha256"):
        raise pilot.PilotStop("Compiler_v2 origin snapshot changed.")
    origin_root = Path(origin["origin_root"])
    origin_spec = _origin_spec(origin_root)
    if origin_spec.get("spec_sha256") != origin["origin_spec_sha256"] or _hash(origin_root / "spec.json") != origin["origin_spec_file_sha256"]:
        raise pilot.PilotStop("Origin pilot artifacts changed after compiler_v2 preparation.")
    training_path = Path(origin["training"]["manifest_path"])
    if not training_path.is_file() or _hash(training_path) != origin["training"]["manifest_sha256"]:
        raise pilot.PilotStop("Readonly training manifest changed.")
    for family_rows in (origin.get("training", {}).get("families") or {}).values():
        for row in family_rows or []:
            trace = Path(str(row.get("trajectory_absolute") or ""))
            digest = row.get("trajectory_sha256_frozen")
            if digest is not None and (not trace.is_file() or _hash(trace) != digest):
                raise pilot.PilotStop("A referenced training trace changed after compiler_v2 preparation.")
    for name, digest in (origin.get("origin_files") or {}).items():
        path = origin_root / name
        if digest is not None and (not path.is_file() or _hash(path) != digest):
            raise pilot.PilotStop(f"Origin artifact changed: {name}")
    for item in origin.get("preserved_terminal_rows") or []:
        for path_key, digest_key in (("state_path", "state_sha256"), ("trajectory_path", "trajectory_sha256")):
            path_value = item.get(path_key)
            digest = item.get(digest_key)
            if digest is not None and (not path_value or not Path(path_value).is_file() or _hash(Path(path_value)) != digest):
                raise pilot.PilotStop("A preserved terminal UI artifact changed after compiler_v2 preparation.")
    private_path = out / spec["private_bindings"]
    if not private_path.is_file() or _hash(private_path) != spec["private_binding_sha256"]:
        raise pilot.PilotStop("Compiler_v2 private bindings changed.")
    current_manifest = pilot.source_manifest(pilot.REPO_ROOT)
    if pilot._manifest_digest(current_manifest) != spec.get("source_manifest_sha256"):
        raise pilot.PilotStop("Compiler_v2 execution source changed after freezing.")
    if _hash(Path(__file__).resolve()) != spec.get("revision_source_sha256"):
        raise pilot.PilotStop("Compiler_v2 migration source changed after freezing.")
    # Reuse the serving loader's frozen budget and schema checks.  This call
    # is read-only and cannot start a model request or emulator action.
    pilot.load_spec(out)
    return spec


def _builder_client(
    budget: Any,
    ledger: Any,
    spec: Mapping[str, Any],
    episode: str,
    env_file: Path | str | None,
    *,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    sleep: Any = time.sleep,
    host_guard: Any = None,
):
    factory = getattr(budget, "make_builder_client", None)
    if not callable(factory):
        raise BudgetStop("selective_budget.make_builder_client() is required for compiler_v2.")
    kwargs: dict[str, Any] = {
        "ledger": ledger,
        "env_path": env_file,
        "episode": episode,
        "profile": BUILDER_PROFILE_NAME,
        "sleep": sleep,
    }
    if sdk is not None:
        kwargs["sdk"] = sdk
    if metadata_fetcher is not None:
        kwargs["metadata_fetcher"] = metadata_fetcher
    if host_guard is not None:
        kwargs["host_guard"] = host_guard
    client = factory(spec["model_locks"], **kwargs)
    if not callable(getattr(client, "create", None)) and not callable(getattr(getattr(getattr(client, "chat", None), "completions", None), "create", None)):
        raise BudgetStop("Builder client does not expose a bounded completion call.")
    return client


def _load_demos(origin: Path, training: Mapping[str, Any], family: str) -> list[dict[str, Any]]:
    demos: list[dict[str, Any]] = []
    for row in (training.get("families") or {}).get(family, []):
        if row.get("success") is not True:
            continue
        path = Path(str(row.get("trajectory_absolute") or ""))
        if not path.is_file() or row.get("trajectory_sha256_frozen") != _hash(path):
            raise pilot.PilotStop("Readonly successful training trace changed.")
        steps = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("record_type") == "step":
                steps.append(record)
        if steps and all(isinstance(step.get("pre_obs"), Mapping) and isinstance(step.get("post_obs"), Mapping) for step in steps):
            demos.append({"family": family, "goal_text": row.get("goal_text", ""), "steps": steps, "source": "readonly_training"})
    return demos[:3]


def build(
    out: Path | str = VERSION_OUT,
    *,
    max_families: int | None = None,
    env_file: Path | str | None = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    sleep: Any = time.sleep,
    host_guard: Any = None,
) -> dict[str, Any]:
    """Build one new-profile plan per family, with one repair at most."""

    out = Path(out).resolve()
    spec = _load_version(out)
    budget = pilot._budget_module(required=True)
    runtime = pilot._runtime_module(required=True)
    ledger = pilot._make_ledger(budget, spec)
    # The budget facade owns profile validation and reservation calculation.
    validator = getattr(budget, "validate_builder_locks", None)
    if not callable(validator):
        raise BudgetStop("selective_budget.validate_builder_locks() is required before compiler_v2 paid work.")
    validate_kwargs: dict[str, Any] = {"profile": BUILDER_PROFILE_NAME, "sleep": sleep}
    if metadata_fetcher is not None:
        validate_kwargs["metadata_fetcher"] = metadata_fetcher
    validator(spec["model_locks"], **validate_kwargs)
    results: dict[str, Any] = {}
    status = "complete" if max_families is None or max_families >= len(BUILD_FAMILIES) else "pilot_limit"
    selected = BUILD_FAMILIES if max_families is None else BUILD_FAMILIES[:max_families]
    with budget.exclusive_run():
        origin = Path(_read_json(out / "origin.json")["origin_root"])
        training = _read_json(out / "origin.json")["training"]
        for family in selected:
            plan_path = out / spec["plans"][family]
            build_dir = out / "build" / family
            build_dir.mkdir(parents=True, exist_ok=True)
            if plan_path.is_file():
                plan = pilot.validate_plan(_read_json(plan_path))
                results[family] = {"status": "already_built", "plan_sha256": pilot.hash_json(plan)}
                continue
            demos = _load_demos(origin, training, family)
            if not demos:
                results[family] = {"status": "unbuildable", "reason": "no readonly successful training trace"}
                status = "partial"
                continue
            messages = pilot.build_prompt(family, demos)
            episode = f"{pilot.NAMESPACE}/{VERSION}/build/{family}"
            client = _builder_client(
                budget,
                ledger,
                spec,
                episode,
                env_file,
                sdk=sdk,
                metadata_fetcher=metadata_fetcher,
                sleep=sleep,
                host_guard=host_guard,
            )
            record: dict[str, Any] = {"status": "unbuildable", "family": family, "calls": 0, "repair": False}
            try:
                response, usage = pilot._call_client(client, messages, episode)
                raw = pilot.response_content(response)
                pilot._save_build_call(build_dir, "first_response", response, usage, raw)
                record["calls"] = 1
                try:
                    candidate = pilot.validate_plan(pilot._json_from_text(raw))
                    pilot.validate_effect_guards(candidate, demos)
                except Exception as first_error:
                    repair_messages = [
                        messages[0],
                        {"role": "user", "content": messages[1]["content"] + "\n\nFirst response validation failed: " + str(first_error) + "\nReturn one corrected JSON object only."},
                        {"role": "assistant", "content": raw},
                    ]
                    repair_episode = episode + "/repair"
                    repair_response, repair_usage = pilot._call_client(client, repair_messages, repair_episode)
                    repair_raw = pilot.response_content(repair_response)
                    pilot._save_build_call(build_dir, "repair_response", repair_response, repair_usage, repair_raw)
                    record["calls"] = 2
                    record["repair"] = True
                    candidate = pilot.validate_plan(pilot._json_from_text(repair_raw))
                    pilot.validate_effect_guards(candidate, demos)
                runtime_report = runtime.validate_plan(candidate)
                if isinstance(runtime_report, Mapping) and runtime_report.get("valid") is False:
                    raise pilot.PlanInvalid("selective_runtime rejected compiler_v2 plan")
                if isinstance(runtime_report, Mapping) and isinstance(runtime_report.get("plan"), Mapping):
                    candidate = dict(runtime_report["plan"])
                _atomic_json(plan_path, candidate)
                record.update(status="built", plan_sha256=pilot.hash_json(candidate))
            except BudgetStop as exc:
                record.update(status="budget_stopped", stop_reason=pilot._safe_budget_reason(exc), error_type=type(exc).__name__)
                status = "budget_stopped"
                _atomic_json(build_dir / "build.json", record)
                results[family] = record
                _atomic_json(
                    out / "build_manifest.json",
                    {"record_type": "compiler_v2-build", "version": VERSION, "status": status, "profile": BUILDER_PROFILE, "families": results},
                )
                raise
            except Exception as exc:
                record.update(status="unbuildable", error_type=type(exc).__name__, error=pilot._safe_first_line(exc))
                status = "partial"
            _atomic_json(build_dir / "build.json", record)
            results[family] = record
    summary = {"record_type": "compiler_v2-build", "version": VERSION, "status": status, "profile": BUILDER_PROFILE, "families": results}
    _atomic_json(out / "build_manifest.json", summary)
    return summary


def _mark_unbuilt_family_rows(out: Path, spec: Mapping[str, Any]) -> None:
    """Terminally skip rows for families without a plan before serving."""

    available = {family: (out / spec["plans"][family]).is_file() for family in pilot.FAMILIES}
    failed_build = {}
    for family in pilot.FAMILIES:
        build_file = out / "build" / family / "build.json"
        try:
            status = _read_json(build_file).get("status") if build_file.is_file() else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            status = None
        failed_build[family] = status in {"unbuildable", "budget_stopped", "provider_stopped"}
    for row in spec.get("episodes") or []:
        family = row["family"]
        if available.get(family, False) or not failed_build.get(family, False):
            continue
        state_path = out / "episodes" / row["id"] / "state.json"
        if state_path.is_file():
            continue
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            state_path,
            {
                "record_type": "episode-state",
                "status": "skipped_unbuildable",
                "episode_id": row["id"],
                "reason": "family plan unavailable in compiler_v2; no reactive serving started",
                "ended_unix": time.time(),
            },
        )


def run(out: Path | str = VERSION_OUT, *, max_episodes: int | None = None, env_file: Path | str | None = None) -> dict[str, Any]:
    out = Path(out).resolve()
    spec = _load_version(out)
    _mark_unbuilt_family_rows(out, spec)
    families = tuple(
        family for family in pilot.FAMILIES
        if (out / spec["plans"][family]).is_file()
    )
    return pilot.run(out, max_episodes=max_episodes, families=families, env_file=env_file)


def analyze(out: Path | str = VERSION_OUT) -> dict[str, Any]:
    out = Path(out).resolve()
    spec = _load_version(out)
    analyzer = pilot._runtime_module(required=False)
    del analyzer
    try:
        module = __import__("guiexp_android.selective_analyze", fromlist=["analyze"])
    except (ImportError, ModuleNotFoundError):
        return pilot.analyze(out)
    return module.analyze(out, spec)


def _exit_code(mode: str, result: Mapping[str, Any], max_episodes: int | None) -> int:
    if mode == "build":
        if result.get("status") == "budget_stopped":
            return 2
        return 0 if result.get("status") == "complete" else 3
    if mode == "run":
        if result.get("batch_status") in {"budget_stopped", "provider_stopped"}:
            return 2
        return 0 if result.get("batch_status") in {"complete", "pilot_limit"} and not result.get("stop_reason") else 3
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--build", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--analyze", action="store_true")
    parser.add_argument("--out", type=Path, default=VERSION_OUT)
    parser.add_argument("--max-families", type=int)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.max_families is not None and args.max_families < 1:
        parser.error("--max-families must be positive")
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    if args.prepare:
        result = prepare(out=args.out)
        print(json.dumps({"status": "prepared", "spec_sha256": result["spec_sha256"]}, indent=2))
        return 0
    if args.build:
        result = build(args.out, max_families=args.max_families, env_file=args.env_file)
        mode = "build"
    elif args.run:
        result = run(args.out, max_episodes=args.max_episodes, env_file=args.env_file)
        mode = "run"
    else:
        result = analyze(args.out)
        mode = "analyze"
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return _exit_code(mode, result, args.max_episodes)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (pilot.PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {pilot._safe_budget_reason(exc)}", file=sys.stderr)
        raise SystemExit(2)
