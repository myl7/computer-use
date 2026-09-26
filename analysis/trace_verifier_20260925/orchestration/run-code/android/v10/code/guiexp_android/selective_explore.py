"""Independent cross-provider exploration for the selective Android pilot.

``provider_v3`` reuses immutable training and terminal evidence from the
original selective output.  It does not resume old UI episodes.  The new
budget facade owns the user-authorized USD 10 tranche and exposes the same
guarded adapter shape as ``selective_budget`` with Wafer as the provider.

The builder profile is fixed to GLM 5.3 Flash, Wafer, low reasoning effort, and
16384 output tokens.  Serving settings stay at a separate 4096-token profile.
Preparation only records hashes and references.  Build and serving are the
only stages that may construct a client.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import selective_pilot as pilot
from .budget_client import BudgetStop


VERSION = "provider_v3"
VERSION_SCHEMA = "android-selective-explore/1"
MODEL = pilot.MODEL
PROVIDER = "wafer"
VERSION_OUT = pilot.DEFAULT_OUT / "versions" / VERSION
BUILD_FAMILIES = ("MarkorCreateNote", "MarkorDeleteNote", "FilesMoveFile")
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
SERVING_MODEL_LOCKS = {
    MODEL: {
        "provider": PROVIDER,
        "prompt_per_m": "0.10",
        "completion_per_m": "0.35",
        "context_length": 1048576,
        "max_tokens": 4096,
        "reservation_usd": "0.1062912",
    }
}
TERMINAL = frozenset({"done", "budget_stopped", "interrupted", "prior_receipt", "skipped_unbuildable"})


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> str:
    return pilot.sha256_file(path)


def _provider_bounds(model_locks: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Strip serving-request fields before deriving a builder profile."""

    result: dict[str, dict[str, Any]] = {}
    for model, lock in model_locks.items():
        result[str(model)] = {
            key: lock[key]
            for key in ("provider", "prompt_per_m", "completion_per_m", "context_length")
            if key in lock
        }
    return result


def _write(path: Path, value: Any, private: bool = False) -> None:
    pilot.atomic_json(path, value, private=private)


def _origin_spec(origin: Path) -> dict[str, Any]:
    spec = _read(origin / "spec.json")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or pilot.hash_json(body) != digest:
        raise pilot.PilotStop("Origin selective spec hash is invalid.")
    return spec


def _state(origin: Path, row: Mapping[str, Any]) -> tuple[str, Path]:
    state_path = origin / "episodes" / str(row["id"]) / "state.json"
    if not state_path.is_file():
        return "pending", state_path
    try:
        return str(_read(state_path).get("status") or "unknown"), state_path
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unreadable", state_path


def _new_id(old_id: str) -> str:
    prefix = pilot.NAMESPACE + "/"
    suffix = old_id[len(prefix) :] if old_id.startswith(prefix) else old_id
    return f"{pilot.NAMESPACE}/{VERSION}/{suffix}"


def _copy_private(origin: Path, out: Path) -> tuple[str, str]:
    source = origin / "private" / "evaluator_bindings.json"
    if not source.is_file():
        raise pilot.PilotStop("Origin evaluator-private bindings are missing.")
    destination = out / "private" / "evaluator_bindings.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _hash(destination) != _hash(source):
        raise pilot.PilotStop("provider_v3 private bindings changed.")
    if not destination.is_file():
        shutil.copy2(source, destination)
    destination.chmod(0o600)
    destination.parent.chmod(0o700)
    return str(destination.relative_to(out)), _hash(destination)


def _training_snapshot(origin: Path) -> dict[str, Any]:
    source = origin / "training_manifest.json"
    if not source.is_file():
        raise pilot.PilotStop("Origin training manifest is missing.")
    manifest = _read(source)
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
        "manifest_path": str(source.resolve()),
        "manifest_sha256": _hash(source),
        "families": families,
        "readonly": True,
    }


def _ledger_has(ledger: Path, episode: str) -> bool:
    # Use the new budget facade's ledger check when available.  This fallback
    # is read-only and is used only by offline preparation tests.
    import sqlite3

    if not ledger.is_file():
        return False
    try:
        with sqlite3.connect(f"file:{ledger.resolve()}?mode=ro", uri=True, timeout=2) as database:
            row = database.execute("SELECT 1 FROM calls WHERE episode=? OR id LIKE ? LIMIT 1", (episode, episode + "/%" )).fetchone()
        return row is not None
    except sqlite3.Error as exc:
        raise pilot.PilotStop("Cannot inspect the provider_v3 ledger for receipt collisions.") from exc


def _execution_manifest() -> dict[str, Any]:
    manifest = {
        "pilot": pilot.source_manifest(pilot.REPO_ROOT),
        "explore_source_sha256": _hash(Path(__file__).resolve()),
    }
    try:
        budget = __import__("guiexp_android.selective_explore_budget", fromlist=["source_hashes", "runtime_manifest"])
    except ImportError as exc:
        raise pilot.PilotStop("selective_explore_budget is required before provider_v3 preparation.") from exc
    source_hashes = getattr(budget, "source_hashes", None)
    runtime_manifest = getattr(budget, "runtime_manifest", None)
    if not callable(source_hashes) or not callable(runtime_manifest):
        raise pilot.PilotStop("selective_explore_budget source/runtime freeze API is incomplete.")
    manifest["budget_source_sha256"] = source_hashes()
    manifest["budget_runtime_manifest"] = runtime_manifest()
    supervisor_path = Path(__file__).with_name("selective_explore_supervisor.py")
    if not supervisor_path.is_file():
        raise pilot.PilotStop("selective_explore_supervisor.py is required before provider_v3 preparation.")
    manifest["supervisor_source_sha256"] = _hash(supervisor_path)
    return manifest


def _freeze_phase_manifests(out: Path, model_locks: Mapping[str, Any], authorization_path: Path) -> dict[str, Any]:
    budget = _budget(required=True)
    freezer = getattr(budget, "freeze_phase_manifest", None)
    if not callable(freezer):
        raise BudgetStop("selective_explore_budget.freeze_phase_manifest() is required before provider_v3 preparation.")
    phases: dict[str, Any] = {}
    for phase, profile in (("build", BUILDER_PROFILE_NAME), ("run", SERVING_PROFILE_NAME)):
        path = out / f"phase_{phase}.json"
        manifest = freezer(
            f"{VERSION}_{phase}",
            model_locks=model_locks,
            provider_profile=PROVIDER,
            request_profile=profile,
            path=path,
            authorization_path=authorization_path,
            extra={"version": VERSION},
        )
        phases[phase] = {"path": str(path.relative_to(out)), "sha256": _hash(path), "phase_manifest_sha256": manifest.get("phase_manifest_sha256")}
    return phases


def prepare(origin: Path | str = pilot.DEFAULT_OUT, out: Path | str = VERSION_OUT) -> dict[str, Any]:
    """Freeze provider_v3 references without making a model request."""

    origin = Path(origin).resolve()
    out = Path(out).resolve()
    origin_spec = _origin_spec(origin)
    if (out / "spec.json").is_file():
        existing = _read(out / "spec.json")
        body = dict(existing)
        digest = body.pop("spec_sha256", None)
        if existing.get("revision") != VERSION or not isinstance(digest, str) or pilot.hash_json(body) != digest:
            raise pilot.PilotStop("Existing provider_v3 spec is invalid and immutable.")
        return existing
    out.mkdir(parents=True, exist_ok=True)
    private_ref, private_sha = _copy_private(origin, out)
    training = _training_snapshot(origin)
    origin_files = {
        "spec.json": _hash(origin / "spec.json"),
        "training_manifest.json": training["manifest_sha256"],
        "private/evaluator_bindings.json": _hash(origin / "private/evaluator_bindings.json"),
    }
    preserved: list[dict[str, Any]] = []
    versioned: list[dict[str, Any]] = []
    for row in origin_spec.get("episodes") or []:
        status, state_path = _state(origin, row)
        old_id = str(row["id"])
        trajectory = origin / "episodes" / old_id / "trajectory.jsonl"
        if status in TERMINAL:
            preserved.append({
                "id": old_id,
                "source_episode_id": old_id,
                "receipt_episode_id": old_id,
                "status": status,
                "state_path": str(state_path.resolve()) if state_path.is_file() else None,
                "state_sha256": _hash(state_path) if state_path.is_file() else None,
                "trajectory_path": str(trajectory.resolve()) if trajectory.is_file() else None,
                "trajectory_sha256": _hash(trajectory) if trajectory.is_file() else None,
            })
            if status != "skipped_unbuildable":
                continue
        new_row = dict(row)
        new_row.update(
            id=_new_id(old_id),
            source_episode_id=old_id,
            receipt_episode_id=_new_id(old_id),
            version=VERSION,
            provider=PROVIDER,
        )
        versioned.append(new_row)
    for row in versioned:
        if _ledger_has(Path(origin_spec.get("ledger_absolute", pilot.SHARED_LEDGER)), row["receipt_episode_id"]):
            raise pilot.PilotStop("provider_v3 episode ID collides with an existing receipt.")
    execution = _execution_manifest()
    authorization_path = Path((pilot.DEFAULT_OUT / "budget_authorization_20260915.json").resolve())
    phase_manifests = _freeze_phase_manifests(out, _provider_bounds(SERVING_MODEL_LOCKS), authorization_path)
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
        "versioned_rows": [{"id": row["id"], "versioned_from": row["source_episode_id"]} for row in versioned],
        "provider": PROVIDER,
        "created_unix": time.time(),
    }
    _write(out / "origin.json", origin_record)
    origin_sha = _hash(out / "origin.json")
    body = {
        "schema": "android-selective-explore/1",
        "revision": VERSION,
        "namespace": pilot.NAMESPACE,
        "model": MODEL,
        "provider": PROVIDER,
        "model_locks": SERVING_MODEL_LOCKS,
        "serving_model_locks": SERVING_MODEL_LOCKS,
        "families": list(pilot.FAMILIES),
        "arms": list(pilot.ARMS),
        "episodes": versioned,
        "action_budget": pilot.MAX_ACTIONS,
        "local_assistance_max": pilot.MAX_LOCAL_ASSISTANCE,
        "obs_mode": pilot.OBS_MODE,
        "temperature": pilot.TEMPERATURE,
        "max_completion_tokens": pilot.MAX_COMPLETION_TOKENS,
        "ledger_absolute": origin_spec.get("ledger_absolute", str(pilot.SHARED_LEDGER)),
        "run_lock_absolute": origin_spec.get("run_lock_absolute", str(pilot.SHARED_RUN_LOCK)),
        "authorization_path": str((pilot.DEFAULT_OUT / "budget_authorization_20260915.json").resolve()),
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
        "builder_profile": BUILDER_PROFILE,
        "serving_profile": SERVING_PROFILE,
        "phase_manifests": phase_manifests,
        "training": {"readonly_origin": training, "reuse_failed_cost_provenance": True},
        "reused_training_refs": training["families"],
        "imported_rows": {
            item["id"]: {
                "source_episode_id": item["source_episode_id"],
                "receipt_episode_id": item["receipt_episode_id"],
                "status": item["status"],
                "provider": "relace",
            }
            for item in preserved
        },
        "provenance": {
            "origin_root": str(origin),
            "origin_spec_sha256": origin_spec["spec_sha256"],
            "source_hashes": origin_spec.get("source_manifest"),
            "provider": PROVIDER,
            "cross_provider_imports": True,
            "preserved_terminal_rows": preserved,
        },
        "plans": {family: f"plans/{family}.json" for family in pilot.FAMILIES},
        "reporting": {"prototype_only": True, "cross_provider_exploratory": True, "selector_algorithm_implemented": False},
    }
    body["spec_sha256"] = pilot.hash_json(body)
    _write(out / "spec.json", body)
    return body


def _load(out: Path) -> dict[str, Any]:
    spec = _read(out / "spec.json")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if spec.get("revision") != VERSION or not isinstance(digest, str) or pilot.hash_json(body) != digest:
        raise pilot.PilotStop("provider_v3 spec hash changed.")
    if spec.get("model") != MODEL or spec.get("provider") != PROVIDER:
        raise pilot.PilotStop("provider_v3 model/provider lock changed.")
    if spec.get("builder_profile") != BUILDER_PROFILE or spec.get("serving_profile") != SERVING_PROFILE:
        raise pilot.PilotStop("provider_v3 request profile changed.")
    origin = _read(out / spec["origin"])
    if _hash(out / spec["origin"]) != spec.get("origin_sha256"):
        raise pilot.PilotStop("provider_v3 origin snapshot changed.")
    origin_root = Path(origin["origin_root"])
    if _hash(origin_root / "spec.json") != origin["origin_spec_file_sha256"]:
        raise pilot.PilotStop("Origin spec changed after provider_v3 preparation.")
    for name, digest in origin.get("origin_files", {}).items():
        path = origin_root / name
        if digest is not None and (not path.is_file() or _hash(path) != digest):
            raise pilot.PilotStop(f"Origin artifact changed: {name}")
    for rows in (origin.get("training", {}).get("families") or {}).values():
        for row in rows or []:
            trace = Path(str(row.get("trajectory_absolute") or ""))
            digest = row.get("trajectory_sha256_frozen")
            if digest is not None and (not trace.is_file() or _hash(trace) != digest):
                raise pilot.PilotStop("Readonly training trace changed.")
    for item in origin.get("preserved_terminal_rows") or []:
        for path_key, digest_key in (("state_path", "state_sha256"), ("trajectory_path", "trajectory_sha256")):
            path_value, digest = item.get(path_key), item.get(digest_key)
            if digest is not None and (not path_value or not Path(path_value).is_file() or _hash(Path(path_value)) != digest):
                raise pilot.PilotStop("Preserved terminal UI artifact changed.")
    private = out / spec["private_bindings"]
    if not private.is_file() or _hash(private) != spec["private_binding_sha256"]:
        raise pilot.PilotStop("provider_v3 private bindings changed.")
    if pilot.hash_json(_execution_manifest()) != spec.get("source_manifest_sha256"):
        raise pilot.PilotStop("provider_v3 execution source changed after freezing.")
    phases = spec.get("phase_manifests") or {}
    if phases:
        budget = _budget(required=True)
        loader = getattr(budget, "load_phase_manifest", None)
        if not callable(loader):
            raise BudgetStop("selective_explore_budget.load_phase_manifest() is required before provider_v3 execution.")
        for item in phases.values():
            path = out / item["path"]
            if _hash(path) != item["sha256"]:
                raise BudgetStop("provider_v3 phase manifest changed.")
            loader(path)
    return spec


def _budget(required: bool = True):
    try:
        return __import__("guiexp_android.selective_explore_budget", fromlist=["make_ledger"])
    except ModuleNotFoundError as exc:
        if required:
            raise BudgetStop("selective_explore_budget is unavailable; no paid call may start.") from exc
        return None


def _build_demos(out: Path, spec: Mapping[str, Any], family: str) -> list[dict[str, Any]]:
    origin_record = _read(out / spec["origin"])
    demos: list[dict[str, Any]] = []
    for row in (origin_record["training"].get("families") or {}).get(family, []):
        if row.get("success") is not True:
            continue
        path = Path(str(row.get("trajectory_absolute") or ""))
        if not path.is_file() or _hash(path) != row.get("trajectory_sha256_frozen"):
            raise pilot.PilotStop("Readonly successful training trace changed.")
        steps = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and (record := json.loads(line)).get("record_type") == "step":
                steps.append(record)
        if steps and all(isinstance(step.get("pre_obs"), Mapping) and isinstance(step.get("post_obs"), Mapping) for step in steps):
            demos.append({"family": family, "goal_text": row.get("goal_text", ""), "steps": steps, "source": "readonly_training"})
    return demos[:3]


def _existing_build_state(out: Path, spec: Mapping[str, Any], family: str) -> dict[str, Any] | None:
    """Classify prior v3 build evidence before constructing a client."""

    plan_path = out / spec["plans"][family]
    build_dir = out / "build" / family
    build_path = build_dir / "build.json"
    if plan_path.is_file():
        plan = pilot.validate_plan(_read(plan_path))
        return {"status": "already_built", "plan_sha256": pilot.hash_json(plan)}
    if build_path.is_file():
        try:
            record = _read(build_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise pilot.PilotStop("Existing provider_v3 build state is unreadable; no resend is safe.") from exc
        status = record.get("status")
        if status in {"budget_stopped", "provider_stopped", "interrupted", "unbuildable"}:
            raise pilot.PilotStop(f"Provider_v3 build for {family} is already terminal ({status}); use a new version.")
        raise pilot.PilotStop("Existing provider_v3 build state is not a recognized terminal result.")
    if any(build_dir.glob("*_response.json")):
        raise pilot.PilotStop(f"Provider_v3 build response evidence exists for {family} without terminal state; no resend is safe.")
    return None


def build(out: Path | str = VERSION_OUT, *, max_families: int | None = None, env_file: Path | str | None = None) -> dict[str, Any]:
    """Build provider_v3 plans with one initial call and one repair maximum."""

    out = Path(out).resolve()
    spec = _load(out)
    budget = _budget()
    runtime = pilot._runtime_module(required=True)
    ledger_factory = getattr(budget, "make_ledger", None)
    if not callable(ledger_factory):
        raise BudgetStop("selective_explore_budget.make_ledger() is required.")
    ledger = ledger_factory()
    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise BudgetStop("selective_explore_budget.validate_locks() is required.")
    builder_locks = _provider_bounds(spec.get("model_locks") or {})
    validator(builder_locks, provider_profile=PROVIDER, request_profile=BUILDER_PROFILE_NAME)
    selected = BUILD_FAMILIES if max_families is None else BUILD_FAMILIES[:max_families]
    status = "complete" if max_families is None or max_families >= len(BUILD_FAMILIES) else "pilot_limit"
    results: dict[str, Any] = {}
    with budget.exclusive_run():
        for family in selected:
            path = out / spec["plans"][family]
            build_dir = out / "build" / family
            build_dir.mkdir(parents=True, exist_ok=True)
            prior = _existing_build_state(out, spec, family)
            if prior is not None:
                results[family] = prior
                continue
            demos = _build_demos(out, spec, family)
            result: dict[str, Any] = {"family": family, "status": "unbuildable", "calls": 0, "repair": False}
            if not demos:
                result["reason"] = "no successful readonly training trace"
                status = "partial"
                _write(build_dir / "build.json", result)
                results[family] = result
                continue
            client_factory = getattr(budget, "make_builder_client", None)
            if not callable(client_factory):
                raise BudgetStop("selective_explore_budget.make_builder_client() is required.")
            episode = f"{pilot.NAMESPACE}/{VERSION}/build/{family}"
            client = client_factory(
                builder_locks,
                ledger=ledger,
                env_path=env_file,
                profile=BUILDER_PROFILE_NAME,
                provider_profile=PROVIDER,
                episode=episode,
            )
            messages = pilot.build_prompt(family, demos)
            try:
                response, usage = pilot._call_client(client, messages, episode)
                raw = pilot.response_content(response)
                pilot._save_build_call(build_dir, "first_response", response, usage, raw)
                result["calls"] = 1
                try:
                    candidate = pilot.validate_plan(pilot._json_from_text(raw))
                    pilot.validate_effect_guards(candidate, demos)
                except Exception as first_error:
                    repair_messages = [
                        messages[0],
                        messages[1],
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": "The first response failed validation: " + str(first_error) + ". Return one corrected JSON object only."},
                    ]
                    repair_episode = episode + "/repair"
                    repair_response, repair_usage = pilot._call_client(client, repair_messages, repair_episode)
                    repair_raw = pilot.response_content(repair_response)
                    pilot._save_build_call(build_dir, "repair_response", repair_response, repair_usage, repair_raw)
                    result.update(calls=2, repair=True)
                    candidate = pilot.validate_plan(pilot._json_from_text(repair_raw))
                    pilot.validate_effect_guards(candidate, demos)
                report = runtime.validate_plan(candidate)
                if isinstance(report, Mapping) and report.get("valid") is False:
                    raise pilot.PlanInvalid("selective_runtime rejected provider_v3 plan")
                if isinstance(report, Mapping) and isinstance(report.get("plan"), Mapping):
                    candidate = dict(report["plan"])
                _write(path, candidate)
                result.update(status="built", plan_sha256=pilot.hash_json(candidate))
            except BudgetStop as exc:
                result.update(status="budget_stopped", stop_reason=pilot._safe_budget_reason(exc), error_type=type(exc).__name__)
                status = "budget_stopped"
                _write(build_dir / "build.json", result)
                results[family] = result
                _write(out / "build_manifest.json", {"record_type": "provider_v3-build", "status": status, "profile": BUILDER_PROFILE, "families": results})
                raise
            except Exception as exc:
                result.update(status="unbuildable", error_type=type(exc).__name__, error=pilot._safe_first_line(exc))
                status = "partial"
            _write(build_dir / "build.json", result)
            results[family] = result
    summary = {"record_type": "provider_v3-build", "status": status, "profile": BUILDER_PROFILE, "families": results}
    _write(out / "build_manifest.json", summary)
    return summary


def _private_map(out: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    data = _read(out / spec["private_bindings"])
    return {f"{family}/b{index:02d}": dict(row) for family, rows in (data.get("families") or {}).items() for index, row in enumerate(rows, 1)}


def _progress_rows(out: Path, spec: Mapping[str, Any], current: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    observed = {str(item.get("id")): dict(item) for item in current}
    rows: list[dict[str, Any]] = []
    for row in spec.get("episodes") or []:
        state_path = out / "episodes" / row["id"] / "state.json"
        if state_path.is_file():
            try:
                state = _read(state_path)
                item = {"id": row["id"], "family": row.get("family"), "arm": row.get("arm"), "status": state.get("status")}
                if state.get("status") == "done":
                    result = state.get("result") or {}
                    item.update(success=result.get("success"), actions=result.get("actions"))
                rows.append(item)
                continue
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
        rows.append(observed.get(row["id"], {"id": row["id"], "family": row.get("family"), "arm": row.get("arm"), "status": "pending"}))
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
    return rows, counts


def run(out: Path | str = VERSION_OUT, *, max_episodes: int | None = None, env_file: Path | str | None = None) -> dict[str, Any]:
    """Run only families with a valid plan, leaving unbuilt families pending."""

    out = Path(out).resolve()
    spec = _load(out)
    budget = _budget()
    plans = {}
    for family in pilot.FAMILIES:
        path = out / spec["plans"][family]
        try:
            plans[family] = pilot.validate_plan(_read(path)) if path.is_file() else None
        except Exception:
            plans[family] = None
    available = tuple(family for family in pilot.FAMILIES if plans.get(family) is not None)
    ledger = budget.make_ledger()
    if not available:
        result = {"record_type": "provider_v3-progress", "batch_status": "no_plans", "planned": len(spec.get("episodes") or []), "counts": {"pending": len(spec.get("episodes") or [])}, "stop_reason": "No executable provider_v3 plan; no reactive serving started."}
        _write(out / "progress.json", result)
        return result
    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise BudgetStop("selective_explore_budget.validate_locks() is required for serving.")
    validator(spec.get("model_locks") or {}, provider_profile=PROVIDER, request_profile=SERVING_PROFILE_NAME)
    private = _private_map(out, spec)
    done = 0
    progress: list[dict[str, Any]] = []
    batch_status = "complete"
    with budget.exclusive_run():
        for row in spec.get("episodes") or []:
            if row.get("family") not in available:
                continue
            state_path = out / "episodes" / row["id"] / "state.json"
            if state_path.is_file():
                state = _read(state_path)
                if state.get("status") in TERMINAL:
                    progress.append({"id": row["id"], "status": state.get("status")})
                    continue
                if state.get("status") == "running":
                    state.update(status="interrupted", reason="prior provider_v3 episode was running; raw evidence retained and never replayed", ended_unix=time.time())
                    _write(state_path, state, private=True)
                    progress.append({"id": row["id"], "status": "interrupted"})
                    continue
                trajectory = state_path.parent / "trajectory.jsonl"
                if trajectory.is_file() and trajectory.stat().st_size:
                    state.update(status="interrupted", reason="prior provider_v3 action evidence exists; never replayed", ended_unix=time.time())
                    _write(state_path, state, private=True)
                    progress.append({"id": row["id"], "status": "interrupted"})
                    continue
            has_episode = getattr(ledger, "has_episode", None)
            if not callable(has_episode):
                raise BudgetStop("provider_v3 ledger cannot verify prior episode receipts.")
            try:
                if has_episode(row["id"]):
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    _write(state_path, {"record_type": "episode-state", "status": "prior_receipt", "episode_id": row["id"], "reason": "receipt already exists; no UI replay", "ended_unix": time.time()}, private=True)
                    progress.append({"id": row["id"], "status": "prior_receipt"})
                    continue
            except BudgetStop:
                raise
            except Exception as exc:
                raise BudgetStop("provider_v3 ledger receipt lookup failed; no replay is safe.") from exc
            if max_episodes is not None and done >= max_episodes:
                batch_status = "pilot_limit"
                break
            episode = row["id"]
            client = budget.make_client(
                spec.get("model_locks") or {},
                ledger=ledger,
                env_path=env_file,
                profile=SERVING_PROFILE_NAME,
                provider_profile=PROVIDER,
                episode=episode,
            )
            env = pilot._new_env()
            try:
                result = pilot._run_one_episode(out, spec, row, private[f"{row['family']}/{row['binding_id']}"], env=env, client=client, budget=budget, plan=plans[row["family"]])
                progress.append({"id": episode, "status": "done", "success": result.get("success")})
            except BudgetStop as exc:
                batch_status = "budget_stopped"
                progress.append({"id": episode, "status": "budget_stopped", "stop_reason": pilot._safe_budget_reason(exc), "error_type": type(exc).__name__})
                break
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            done += 1
    rows, counts = _progress_rows(out, spec, progress)
    if batch_status == "complete" and counts.get("pending", 0):
        batch_status = "incomplete"
    result = {"record_type": "provider_v3-progress", "batch_status": batch_status, "planned": len(spec.get("episodes") or []), "completed_in_invocation": done, "counts": counts, "episodes": rows}
    _write(out / "progress.json", result)
    return result


def analyze(out: Path | str = VERSION_OUT) -> dict[str, Any]:
    out = Path(out).resolve()
    spec = _load(out)
    try:
        module = __import__("guiexp_android.selective_analyze", fromlist=["analyze"])
    except (ImportError, ModuleNotFoundError):
        return {"record_type": "provider_v3-analysis", "status": "analyzer_unavailable", "version": VERSION}
    return module.analyze(out, spec)


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
        return 0 if result.get("status") == "complete" else 3
    if args.run:
        result = run(args.out, max_episodes=args.max_episodes, env_file=args.env_file)
        return 0 if result.get("batch_status") in {"complete", "pilot_limit"} else 2
    result = analyze(args.out)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (pilot.PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {pilot._safe_budget_reason(exc)}", file=sys.stderr)
        raise SystemExit(2)
