"""Bounded descriptor-repair diagnostic for the 2026-09-15 Android pilot.

The diagnostic compares one reactive baseline, two executions of the frozen
v6 plan, and one execution of a descriptor-only repair learned from the two
v6 local-rejoin traces.  Preparation is local-only.  Serving uses the
exploratory budget facade and the same shared ledger and worker lock as the
pilot.  The plan remains a data-only artifact interpreted by
``selective_runtime``.

The four public treatments are:

``reactive``
    Existing screenshot/accessibility agent.
``original_full_fallback``
    Frozen v6 plan with whole-agent fallback.
``original_local_rejoin``
    Frozen v6 plan with the existing bounded local-rejoin mode.
``patched_full_fallback``
    Helper-generated descriptor repair with whole-agent fallback.

The v7 driver intentionally has no build phase and never starts a later phase
automatically.  ``--prepare``, ``--run``, and ``--analyze`` are the only
commands exposed by its CLI.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import inspect
import json
import sqlite3
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import selective_pilot as pilot
from .budget_client import BudgetStop


VERSION = "selector_repair_v7"
DEFAULT_VERSION = VERSION
SPEC_SCHEMA = "android-selective-repair-diagnostic/1"
VERSION_SCHEMA = SPEC_SCHEMA
NAMESPACE = pilot.NAMESPACE
FAMILY = "MarkorDeleteNote"
MODEL = "z-ai/glm-5.3-flash"
PROVIDER_PROFILE = "z_ai_fp8"
PROVIDER = "z-ai/fp8"
SERVING_PROFILE_NAME = "serving_4096"
SERVING_PROFILE = {
    "name": SERVING_PROFILE_NAME,
    "model": MODEL,
    "provider": PROVIDER,
    "max_tokens": 4096,
    "temperature": 0.0,
}
MAX_ACTIONS = 40
ORIGIN_VERSION = "projected_v6_schema"
ORIGIN_OUT = pilot.DEFAULT_OUT / "versions" / ORIGIN_VERSION
DEFAULT_OUT = pilot.DEFAULT_OUT / "versions" / VERSION
VERSION_OUT = DEFAULT_OUT
AUTHORIZATION_PATH = pilot.DEFAULT_OUT / "budget_authorization_20260915.json"
ORIGINAL_PLAN_SHA256 = "0c0fa85ada3b2f034198475cd1a74ac7ec242a19935d92e7a379bb8d71e1fed8"
AUTHORIZATION_SHA256 = "2758bbbf836ab04c8d01a128f4d5f597e8fd6c818fb63215c4bb202a87456b44"
FRESH_SEED_START = 915303
FRESH_BINDING_COUNT = 2
KNOWN_ORIGINAL_SEEDS = (915101, 915102, 915201, 915202)
TREATMENTS = (
    "reactive",
    "original_full_fallback",
    "original_local_rejoin",
    "patched_full_fallback",
)
RUN_ARM = {
    "reactive": "reactive",
    "original_full_fallback": "full_fallback",
    "original_local_rejoin": "local_rejoin",
    "patched_full_fallback": "full_fallback",
}
TERMINAL = frozenset(
    {"done", "interrupted", "budget_stopped", "prior_receipt", "uncertain"}
)
NANO = Decimal(1_000_000_000)


class DiagnosticStop(RuntimeError):
    """Fail-closed stop for an unsafe or incomplete diagnostic artifact."""


def _read(path: Path | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DiagnosticStop(f"Cannot read JSON artifact {Path(path)}.") from exc


def _write(path: Path | str, value: Any, *, private: bool = False) -> None:
    path = Path(path)
    pilot.atomic_json(path, value, private=private)
    if private:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.parent.chmod(0o700)
            path.chmod(0o600)
        except OSError:
            pass


def _hash(path: Path | str) -> str:
    try:
        return pilot.sha256_file(path)
    except (OSError, UnicodeError) as exc:
        raise DiagnosticStop(f"Cannot hash required artifact {Path(path)}.") from exc


def _json_hash(value: Any) -> str:
    return hashlib.sha256(pilot.canonical(value).encode("utf-8")).hexdigest()


def _safe_first_line(exc: BaseException, limit: int = 240) -> str:
    lines = str(exc).splitlines()
    return f"{type(exc).__name__}: {(lines[0] if lines else '')[:limit]}"


def _budget():
    try:
        return importlib.import_module(".selective_explore_budget", __package__)
    except (ImportError, ModuleNotFoundError) as exc:
        raise DiagnosticStop("selective_explore_budget is required for this diagnostic.") from exc


def _origin_spec(origin: Path) -> dict[str, Any]:
    spec_path = origin / "spec.json"
    spec = _read(spec_path)
    if not isinstance(spec, Mapping):
        raise DiagnosticStop("v6 origin spec is not an object.")
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or _json_hash(body) != digest:
        raise DiagnosticStop("v6 origin spec hash is invalid.")
    if spec.get("revision") != ORIGIN_VERSION or spec.get("version") != ORIGIN_VERSION:
        raise DiagnosticStop("The repair diagnostic requires projected_v6_schema as its origin.")
    return dict(spec)


def _origin_plan(origin: Path, origin_spec: Mapping[str, Any]) -> tuple[dict[str, Any], Path, str]:
    refs = origin_spec.get("plans") or {}
    raw = refs.get(FAMILY) if isinstance(refs, Mapping) else None
    plan_path = Path(str(raw)) if raw else Path("plans") / f"{FAMILY}.json"
    if not plan_path.is_absolute():
        plan_path = origin / plan_path
    if not plan_path.is_file():
        raise DiagnosticStop("The frozen v6 DeleteNote plan is missing.")
    plan = _read(plan_path)
    if not isinstance(plan, Mapping):
        raise DiagnosticStop("The frozen v6 DeleteNote plan is not an object.")
    digest = _json_hash(plan)
    if digest != ORIGINAL_PLAN_SHA256:
        raise DiagnosticStop("The frozen v6 DeleteNote plan hash changed.")
    try:
        normalized = pilot.validate_plan(plan)
    except Exception as exc:
        raise DiagnosticStop(f"The frozen v6 DeleteNote plan is invalid: {_safe_first_line(exc)}") from exc
    return dict(normalized), plan_path.resolve(), digest


def _parse_response_json(value: Any) -> dict[str, Any] | None:
    try:
        parsed = pilot._json_from_text(str(value or ""))
    except Exception:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _episode_dir(origin: Path, row: Mapping[str, Any]) -> Path:
    episode_id = str(row.get("id") or "")
    if not episode_id.startswith(NAMESPACE + "/"):
        raise DiagnosticStop("Origin witness episode has an unexpected namespace.")
    return origin / "episodes" / episode_id


def _witness_rows(origin: Path, origin_spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load only observed local-rejoin evidence from the two frozen v6 rows.

    This loader deliberately discards final status, reward, oracle, and all
    evaluator-private values.  The helper receives only the fields declared in
    ``selective_selector_repair._EVIDENCE_FIELDS``.
    """

    rows = [
        row
        for row in origin_spec.get("episodes") or []
        if isinstance(row, Mapping)
        and row.get("family") == FAMILY
        and row.get("arm") == "local_rejoin"
    ]
    rows.sort(key=lambda row: str(row.get("id")))
    if len(rows) != 2:
        raise DiagnosticStop("The v6 origin must contain exactly two DeleteNote local-rejoin rows.")
    evidence: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    for row in rows:
        directory = _episode_dir(origin, row)
        state_path = directory / "state.json"
        trajectory_path = directory / "trajectory.jsonl"
        if not state_path.is_file() or not trajectory_path.is_file():
            raise DiagnosticStop("A v6 local-rejoin witness is missing its raw state or trajectory.")
        state = _read(state_path)
        trajectory_records: list[dict[str, Any]] = []
        try:
            for line in trajectory_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, Mapping):
                        trajectory_records.append(dict(item))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DiagnosticStop("A v6 witness trajectory is unreadable.") from exc
        extracted: dict[str, Any] | None = None
        for item in trajectory_records:
            if item.get("record_type") == "model_call" and item.get("purpose") == "binding_extraction":
                extracted = _parse_response_json(item.get("response"))
                if extracted is not None:
                    break
        if extracted is None:
            raise DiagnosticStop("A v6 witness lacks a parseable binding-extraction response.")
        # ``state.result`` is used only to reach the runtime trace.  No final
        # success or oracle field is read by the selection path below.
        result = state.get("result") if isinstance(state, Mapping) else None
        run = result.get("run") if isinstance(result, Mapping) else None
        plan_result = run.get("plan") if isinstance(run, Mapping) else None
        trace = plan_result.get("trace") if isinstance(plan_result, Mapping) else None
        if not isinstance(trace, list):
            raise DiagnosticStop("A v6 witness has no runtime trace.")
        local_count = 0
        for item in trace:
            if not isinstance(item, Mapping) or item.get("kind") != "local":
                continue
            local_count += 1
            evidence_item = item.get("evidence") if isinstance(item.get("evidence"), Mapping) else {}
            after = item.get("guards", {}).get("after", {}) if isinstance(item.get("guards"), Mapping) else {}
            pre_elements = evidence_item.get("elements_before")
            action = item.get("action")
            if not isinstance(pre_elements, list) or not isinstance(action, Mapping):
                raise DiagnosticStop("A v6 local-rejoin witness lacks pre-state element evidence.")
            # These are the only fields sent to the pure repair learner.
            evidence.append(
                {
                    "step_id": str(item.get("step_id") or ""),
                    "extracted_bindings": copy.deepcopy(extracted),
                    "local_action": dict(action),
                    "pre_elements": copy.deepcopy(pre_elements),
                    "after_guard_passed": bool(isinstance(after, Mapping) and after.get("ok") is True),
                }
            )
        if local_count == 0:
            raise DiagnosticStop("A v6 local-rejoin witness contains no local action evidence.")
        refs.append(
            {
                "episode_id": str(row["id"]),
                "binding_id": str(row.get("binding_id") or ""),
                "state_path": str(state_path.resolve()),
                "state_sha256": _hash(state_path),
                "trajectory_path": str(trajectory_path.resolve()),
                "trajectory_sha256": _hash(trajectory_path),
                "local_witness_count": local_count,
            }
        )
    return evidence, refs


def load_repair_witnesses(origin: Path | str = ORIGIN_OUT) -> list[dict[str, Any]]:
    """Public read-only witness loader used by preparation tests."""

    origin = Path(origin).resolve()
    return _witness_rows(origin, _origin_spec(origin))[0]


def _known_original_inventory(origin: Path) -> dict[str, Any]:
    """Extend the historical inventory with every original train/test draw."""

    try:
        inventory = dict(pilot.historical_inventory())
    except Exception as exc:
        raise DiagnosticStop(f"Cannot build the read-only binding inventory: {_safe_first_line(exc)}") from exc
    for key in ("historical_hashes", "historical_binding_hashes"):
        inventory[key] = list(inventory.get(key) or [])
    blocked_params = set(inventory["historical_hashes"])
    blocked_bindings = set(inventory["historical_binding_hashes"])
    source_files: list[dict[str, str]] = []
    for candidate in (
        origin / "private" / "evaluator_bindings.json",
        pilot.DEFAULT_OUT / "private" / "evaluator_bindings.json",
    ):
        if not candidate.is_file():
            continue
        source_files.append({"path": str(candidate.resolve()), "sha256": _hash(candidate)})
        try:
            body = _read(candidate)
        except DiagnosticStop:
            continue
        for family_rows in (body.get("families") or {}).values() if isinstance(body, Mapping) else []:
            for item in family_rows or []:
                if not isinstance(item, Mapping):
                    continue
                if isinstance(item.get("params_sha256"), str):
                    blocked_params.add(item["params_sha256"])
                if isinstance(item.get("binding_sha256"), str):
                    blocked_bindings.add(item["binding_sha256"])
    for seed in KNOWN_ORIGINAL_SEEDS:
        try:
            params = pilot._test_params(FAMILY, seed)
            hashes = pilot.binding_hashes(params, FAMILY)
        except Exception:
            continue
        blocked_params.add(hashes["params_sha256"])
        blocked_bindings.add(hashes["binding_sha256"])
    inventory["historical_hashes"] = sorted(blocked_params)
    inventory["historical_binding_hashes"] = sorted(blocked_bindings)
    inventory["known_original_sources"] = source_files
    inventory["known_original_seeds"] = list(KNOWN_ORIGINAL_SEEDS)
    return inventory


def _native_goal(family: str, seed: int) -> str:
    try:
        android_env = importlib.import_module(".android_env", __package__)
        task = android_env.get_task(family, "discover", int(seed))
        return str(android_env.goal_text(task))
    except Exception as exc:
        raise DiagnosticStop(f"Cannot resolve native {family} goal for seed {seed}.") from exc


def _choose_bindings(origin: Path) -> list[dict[str, Any]]:
    inventory = _known_original_inventory(origin)
    try:
        selected = pilot.choose_fresh_bindings(
            FAMILY,
            FRESH_BINDING_COUNT,
            FRESH_SEED_START,
            inventory,
        )
    except Exception as exc:
        raise DiagnosticStop(f"Cannot choose fresh DeleteNote bindings: {_safe_first_line(exc)}") from exc
    blocked_params = set(inventory.get("historical_hashes") or ())
    blocked_bindings = set(inventory.get("historical_binding_hashes") or ())
    seen: set[str] = set()
    private: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        if not isinstance(item, Mapping) or not isinstance(item.get("params"), Mapping):
            raise DiagnosticStop("Fresh binding selector returned malformed private parameters.")
        params = dict(item["params"])
        hashes = pilot.binding_hashes(params, FAMILY)
        if hashes["params_sha256"] in blocked_params or hashes["binding_sha256"] in blocked_bindings:
            raise DiagnosticStop("Fresh binding selection collided with an original train/test binding.")
        if hashes["binding_sha256"] in seen:
            raise DiagnosticStop("Fresh binding selection returned duplicate semantic bindings.")
        seed = int(item.get("seed"))
        private.append(
            {
                "binding_id": f"b{index:02d}",
                "seed": seed,
                "params": params,
                "params_sha256": hashes["params_sha256"],
                "binding_sha256": hashes["binding_sha256"],
                "goal_text": _native_goal(FAMILY, seed),
            }
        )
        seen.add(hashes["binding_sha256"])
    if len(private) != FRESH_BINDING_COUNT:
        raise DiagnosticStop("The diagnostic requires exactly two fresh DeleteNote bindings.")
    return private


def _repair_result(plan: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, str]], dict[str, Any]]:
    try:
        learner = importlib.import_module(".selective_selector_repair", __package__)
    except (ImportError, ModuleNotFoundError) as exc:
        raise DiagnosticStop("selective_selector_repair.py is required before patch learning.") from exc
    repair = getattr(learner, "repair_descriptor", None)
    if not callable(repair):
        raise DiagnosticStop("selective_selector_repair.repair_descriptor() is required.")
    result = repair(copy.deepcopy(plan), copy.deepcopy(list(evidence)))
    if not isinstance(result, Mapping) or result.get("status") != "patched" or result.get("patched") is not True:
        reason = (result.get("report") or {}).get("reason") if isinstance(result, Mapping) else None
        raise DiagnosticStop(f"Descriptor repair declined: {reason or 'unknown reason'}.")
    provenance = result.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("oracle_used") is not False or provenance.get("evaluator_params_used") is not False:
        raise DiagnosticStop("Descriptor repair provenance did not prove oracle/evaluator isolation.")
    patched = result.get("patched_plan") or result.get("plan")
    if not isinstance(patched, Mapping):
        raise DiagnosticStop("Descriptor repair returned no patched plan.")
    try:
        contract = importlib.import_module(".selective_plan_contract", __package__)
        contract_report = contract.validate_plan(patched)
        if not isinstance(contract_report, Mapping) or contract_report.get("valid") is not True:
            raise DiagnosticStop("Descriptor patch failed the frozen plan contract.")
        normalized = pilot.validate_plan(patched)
    except DiagnosticStop:
        raise
    except Exception as exc:
        raise DiagnosticStop(f"Descriptor patch failed plan validation: {_safe_first_line(exc)}") from exc
    patch = result.get("patch")
    if not isinstance(patch, Mapping):
        raise DiagnosticStop("Descriptor repair returned no patch proof.")
    if patch.get("slot") != "note_name" or patch.get("field") != "description":
        raise DiagnosticStop("The diagnostic requires a note_name description-only patch.")
    prefix = patch.get("prefix")
    suffix = patch.get("suffix")
    render_rule = patch.get("render_rule")
    if not isinstance(prefix, str) or not isinstance(suffix, str):
        raise DiagnosticStop("Descriptor patch rendering rule is not textual.")
    if not isinstance(render_rule, Mapping) or not isinstance(render_rule.get("prefix"), str) or not isinstance(render_rule.get("suffix"), str):
        raise DiagnosticStop("Descriptor repair did not return a serving render rule.")
    rules = {"note_name": {"prefix": render_rule["prefix"], "suffix": render_rule["suffix"]}}
    proof = {
        "record_type": "descriptor-repair-proof",
        "helper": "guiexp_android.selective_selector_repair.repair_descriptor",
        "helper_sha256": _hash(Path(learner.__file__).resolve()),
        "status": result.get("status"),
        "patched": result.get("patched"),
        "patch": copy.deepcopy(dict(patch)),
        "render_rules": copy.deepcopy(rules),
        "report": copy.deepcopy(dict(result.get("report") or {})),
        "provenance": copy.deepcopy(dict(provenance)),
        "inferred_binding_render_rules": rules,
        "oracle_used": False,
        "evaluator_params_used": False,
    }
    return dict(normalized), rules, proof


def execution_manifest() -> dict[str, Any]:
    """Hash the exact diagnostic, learner, pilot, runtime, and budget inputs."""

    budget = _budget()
    package = Path(__file__).resolve().parent
    files = {
        "diagnostic": Path(__file__).resolve(),
        "selective_selector_repair": package / "selective_selector_repair.py",
        "selective_pilot": package / "selective_pilot.py",
        "selective_runtime": package / "selective_runtime.py",
    }
    result: dict[str, Any] = {
        "files": {
            name: {"path": str(path), "sha256": _hash(path)}
            for name, path in files.items()
        },
    }
    source_hashes = getattr(budget, "source_hashes", None)
    runtime_manifest = getattr(budget, "runtime_manifest", None)
    if not callable(source_hashes) or not callable(runtime_manifest):
        raise DiagnosticStop("The exploratory budget source/runtime freeze API is incomplete.")
    result["budget_source_hashes"] = source_hashes()
    result["budget_runtime_manifest"] = runtime_manifest()
    result["sha256"] = _json_hash(result)
    return result


def _authorization_record(path: Path) -> dict[str, Any]:
    body = _read(path)
    if not isinstance(body, Mapping):
        raise DiagnosticStop("Exploratory budget authorization is not an object.")
    digest = body.get("authorization_sha256")
    unsigned = {key: value for key, value in body.items() if key != "authorization_sha256"}
    if not isinstance(digest, str) or _json_hash(unsigned) != digest:
        raise DiagnosticStop("Exploratory budget authorization hash changed.")
    if body.get("schema") != "selective-explore-budget-authorization/1" or body.get("namespace") != NAMESPACE:
        raise DiagnosticStop("Unexpected exploratory budget authorization schema.")
    if body.get("total_occupied_ceiling_usd") != "20" or body.get("new_tranche_occupied_ceiling_usd") != "10":
        raise DiagnosticStop("Exploratory budget ceilings changed.")
    return {
        "path": str(path.resolve()),
        "file_sha256": _hash(path),
        "authorization_sha256": digest,
        "shared_ledger": body.get("shared_ledger"),
        "shared_run_lock": body.get("shared_run_lock"),
        "total_occupied_ceiling_usd": body.get("total_occupied_ceiling_usd"),
        "new_tranche_occupied_ceiling_usd": body.get("new_tranche_occupied_ceiling_usd"),
    }


def _freeze_phase_manifest(out: Path, locks: Mapping[str, Any], auth_path: Path) -> dict[str, Any]:
    budget = _budget()
    freezer = getattr(budget, "freeze_phase_manifest", None)
    if not callable(freezer):
        raise DiagnosticStop("selective_explore_budget.freeze_phase_manifest() is required.")
    path = out / "phase_run.json"
    manifest = freezer(
        f"{VERSION}_run",
        model_locks=copy.deepcopy(dict(locks)),
        provider_profile=PROVIDER_PROFILE,
        request_profile=SERVING_PROFILE_NAME,
        path=path,
        authorization_path=auth_path,
        extra={"version": VERSION, "diagnostic": "descriptor_repair"},
    )
    if not isinstance(manifest, Mapping):
        raise DiagnosticStop("Phase manifest freezer returned malformed data.")
    return {"path": str(path.name), "sha256": _hash(path), "phase_manifest_sha256": manifest.get("phase_manifest_sha256")}


def _episode_rows(private: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    order = 0
    for binding in private:
        binding_id = str(binding["binding_id"])
        seed = int(binding["seed"])
        for treatment in TREATMENTS:
            order += 1
            arm = RUN_ARM[treatment]
            rows.append(
                {
                    "order": order,
                    "id": f"{NAMESPACE}/{VERSION}/{FAMILY}/{binding_id}/{treatment}",
                    "family": FAMILY,
                    "binding_id": binding_id,
                    "seed": seed,
                    "treatment": treatment,
                    "arm": arm,
                    "condition": "discover",
                    "obs_mode": pilot.OBS_MODE,
                    "max_actions": MAX_ACTIONS,
                    "source_plan": "original" if treatment.startswith("original") else "patched" if treatment.startswith("patched") else None,
                    "execution_plan": "original" if treatment.startswith("original") else "original_with_binding_render_rules" if treatment.startswith("patched") else "none",
                }
            )
    return rows


def _private_bindings(out: Path, spec: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    data = _read(out / str(spec["private_bindings"]))
    result: dict[str, dict[str, Any]] = {}
    for family, rows in (data.get("families") or {}).items() if isinstance(data, Mapping) else []:
        for row in rows or []:
            if isinstance(row, Mapping) and family == FAMILY:
                result[str(row["binding_id"])] = dict(row)
    return result


def _validate_spec(spec: Mapping[str, Any], out: Path) -> dict[str, Any]:
    body = dict(spec)
    digest = body.pop("spec_sha256", None)
    if not isinstance(digest, str) or _json_hash(body) != digest:
        raise DiagnosticStop("Repair diagnostic spec hash changed.")
    if spec.get("schema") != SPEC_SCHEMA or spec.get("version") != VERSION:
        raise DiagnosticStop("Repair diagnostic spec schema/version changed.")
    if spec.get("model") != MODEL or spec.get("provider") != PROVIDER or spec.get("provider_profile") != PROVIDER_PROFILE:
        raise DiagnosticStop("Repair diagnostic model/provider lock changed.")
    serving = spec.get("serving_profile") or {}
    if serving.get("name") != SERVING_PROFILE_NAME or serving.get("max_tokens") != 4096:
        raise DiagnosticStop("Repair diagnostic serving profile changed.")
    if spec.get("action_budget") != MAX_ACTIONS or spec.get("family") != FAMILY:
        raise DiagnosticStop("Repair diagnostic family or action budget changed.")
    if tuple(spec.get("treatments") or ()) != TREATMENTS:
        raise DiagnosticStop("Repair diagnostic treatment registry changed.")
    execution = spec.get("execution_manifest")
    if not isinstance(execution, Mapping) or _json_hash({key: value for key, value in execution.items() if key != "sha256"}) != execution.get("sha256"):
        raise DiagnosticStop("Repair diagnostic execution manifest is invalid.")
    if execution_manifest() != execution:
        raise DiagnosticStop("Frozen repair diagnostic sources changed after preparation.")
    auth = spec.get("authorization") or {}
    auth_path = Path(str(auth.get("path") or AUTHORIZATION_PATH))
    current_auth = _authorization_record(auth_path)
    if current_auth != auth:
        raise DiagnosticStop("Frozen exploratory authorization changed.")
    if auth.get("shared_ledger") != spec.get("ledger_absolute") or auth.get("shared_run_lock") != spec.get("run_lock_absolute"):
        raise DiagnosticStop("Frozen authorization paths do not match the shared diagnostic paths.")
    if auth_path == (pilot.DEFAULT_OUT / "budget_authorization_20260915.json").resolve() and auth.get("authorization_sha256") != AUTHORIZATION_SHA256:
        raise DiagnosticStop("The immutable 2026-09-15 authorization hash changed.")
    origin = spec.get("origin") or {}
    origin_root = Path(str(origin.get("root") or ORIGIN_OUT))
    origin_spec_path = origin_root / "spec.json"
    if _hash(origin_spec_path) != origin.get("spec_file_sha256"):
        raise DiagnosticStop("Frozen v6 origin spec changed.")
    original_path = Path(str((spec.get("original_plan") or {}).get("path") or ""))
    original_plan_ref = spec.get("original_plan") or {}
    if not original_path.is_file():
        raise DiagnosticStop("Frozen v6 original plan changed.")
    original_plan_value = _read(original_path)
    if _json_hash(original_plan_value) != original_plan_ref.get("sha256") or original_plan_ref.get("file_sha256") != _hash(original_path):
        raise DiagnosticStop("Frozen v6 original plan changed.")
    if original_plan_ref.get("sha256") != ORIGINAL_PLAN_SHA256:
        raise DiagnosticStop("Unexpected original DeleteNote plan hash.")
    private_path = out / str(spec.get("private_bindings") or "private/evaluator_bindings.json")
    if not private_path.is_file() or private_path.stat().st_mode & 0o077 or _hash(private_path) != spec.get("private_bindings_sha256"):
        raise DiagnosticStop("Private v7 binding artifact is missing, exposed, or changed.")
    witness_path = out / str(spec.get("repair_witnesses") or "private/repair_witnesses.json")
    if not witness_path.is_file() or witness_path.stat().st_mode & 0o077 or _hash(witness_path) != spec.get("repair_witnesses_sha256"):
        raise DiagnosticStop("Private v7 witness artifact is missing, exposed, or changed.")
    witness = _read(witness_path)
    if witness.get("oracle_used") is not False or witness.get("evaluator_params_used") is not False:
        raise DiagnosticStop("Repair witness provenance did not prove selector isolation.")
    proof_path = out / str(spec.get("patch_proof") or "patch_proof.json")
    if not proof_path.is_file() or _hash(proof_path) != spec.get("patch_proof_sha256"):
        raise DiagnosticStop("Descriptor patch proof is missing or changed.")
    proof = _read(proof_path)
    if proof.get("render_rules") != spec.get("inferred_binding_render_rules") or proof.get("oracle_used") is not False or proof.get("evaluator_params_used") is not False:
        raise DiagnosticStop("Descriptor patch proof is inconsistent or unsafe.")
    plan_path = out / str(spec.get("patched_plan") or "plans/MarkorDeleteNote.json")
    if not plan_path.is_file() or _json_hash(_read(plan_path)) != spec.get("patched_plan_sha256") or _hash(plan_path) != spec.get("patched_plan_file_sha256"):
        raise DiagnosticStop("Patched DeleteNote plan is missing or changed.")
    try:
        contract = importlib.import_module(".selective_plan_contract", __package__)
        if contract.validate_plan(_read(plan_path)).get("valid") is not True:
            raise DiagnosticStop("Patched plan failed the exact plan contract.")
        pilot.validate_plan(_read(plan_path))
    except DiagnosticStop:
        raise
    except Exception as exc:
        raise DiagnosticStop(f"Patched plan validation failed: {_safe_first_line(exc)}") from exc
    phase = spec.get("phase_run") or {}
    phase_path = out / str(phase.get("path") or "phase_run.json")
    if not phase_path.is_file() or _hash(phase_path) != phase.get("sha256"):
        raise DiagnosticStop("Frozen run phase manifest is missing or changed.")
    loader = getattr(_budget(), "load_phase_manifest", None)
    if not callable(loader):
        raise DiagnosticStop("selective_explore_budget.load_phase_manifest() is required.")
    loader(phase_path)
    rows = spec.get("episodes")
    if not isinstance(rows, list) or len(rows) != FRESH_BINDING_COUNT * len(TREATMENTS):
        raise DiagnosticStop("Repair diagnostic episode registry is incomplete.")
    ids = [str(row.get("id")) for row in rows if isinstance(row, Mapping)]
    if len(ids) != len(set(ids)) or any(not value.startswith(f"{NAMESPACE}/{VERSION}/{FAMILY}/") for value in ids):
        raise DiagnosticStop("Repair diagnostic episode IDs are invalid or duplicated.")
    if {str(row.get("treatment")) for row in rows if isinstance(row, Mapping)} != set(TREATMENTS):
        raise DiagnosticStop("Repair diagnostic episodes do not cover all treatments.")
    for row in rows:
        if not isinstance(row, Mapping):
            raise DiagnosticStop("Repair diagnostic episode row is malformed.")
        treatment = str(row.get("treatment"))
        if row.get("arm") != RUN_ARM.get(treatment):
            raise DiagnosticStop("Repair diagnostic treatment-to-arm mapping changed.")
        expected_plan = (
            "none"
            if treatment == "reactive"
            else "original_with_binding_render_rules"
            if treatment == "patched_full_fallback"
            else "original"
        )
        if row.get("execution_plan") != expected_plan:
            raise DiagnosticStop("Repair diagnostic execution plan role changed.")
    return dict(spec)


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    out = Path(out).resolve()
    path = out / "spec.json"
    if not path.is_file():
        raise DiagnosticStop(f"Prepared repair diagnostic is missing: {path}")
    return _validate_spec(_read(path), out)


_load = load_spec


def _supports_binding_render_rules() -> bool:
    try:
        params = inspect.signature(pilot._run_one_episode).parameters
    except (TypeError, ValueError):
        return False
    return "binding_render_rules" in params or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params.values())


def _write_binding_render(episode_dir: Path, rendering: Mapping[str, Any] | None, rule: Mapping[str, Any] | None) -> dict[str, Any]:
    path = episode_dir / "binding_render.json"
    payload = {
        "record_type": "binding-render",
        "status": "not_applicable" if rule is None else "applied" if isinstance(rendering, Mapping) else "not_applied",
        "rule": copy.deepcopy(dict(rule or {})),
        "application_count": 1 if isinstance(rendering, Mapping) else 0,
        "raw_bindings": copy.deepcopy(rendering.get("raw")) if isinstance(rendering, Mapping) else None,
        "rendered_bindings": copy.deepcopy(rendering.get("rendered")) if isinstance(rendering, Mapping) else None,
    }
    _write(path, payload, private=True)
    return {"path": path.name, "sha256": _hash(path), "status": payload["status"]}


def _annotate_state(out: Path, row: Mapping[str, Any], spec: Mapping[str, Any], rendering: Mapping[str, Any] | None, rule: Mapping[str, Any] | None) -> None:
    state_path = out / "episodes" / str(row["id"]) / "state.json"
    if not state_path.is_file():
        return
    state = _read(state_path)
    render_ref = _write_binding_render(state_path.parent, rendering, rule)
    state.update(
        version=VERSION,
        treatment=row["treatment"],
        arm=row["arm"],
        provider=PROVIDER,
        provider_profile=PROVIDER_PROFILE,
        source_plan_hash=(
            spec["original_plan"]["sha256"]
            if row.get("execution_plan") in {"original", "original_with_binding_render_rules"}
            else None
        ),
        patch_candidate_hash=spec["patched_plan_sha256"] if row.get("treatment") == "patched_full_fallback" else None,
        patch_proof_source=spec["patch_proof_source"],
        binding_render=render_ref,
    )
    pilot.atomic_json(state_path, state, private=True)


def _mark_state(out: Path, row: Mapping[str, Any], status: str, reason: str) -> None:
    path = out / "episodes" / str(row["id"]) / "state.json"
    value = {
        "record_type": "episode-state",
        "status": status,
        "episode_id": row["id"],
        "family": FAMILY,
        "binding_id": row["binding_id"],
        "treatment": row["treatment"],
        "arm": row["arm"],
        "seed": row["seed"],
        "provider": PROVIDER,
        "provider_profile": PROVIDER_PROFILE,
        "reason": reason,
        "ended_unix": time.time(),
    }
    _write(path, value, private=True)


def _state_for(out: Path, row: Mapping[str, Any]) -> dict[str, Any] | None:
    path = out / "episodes" / str(row["id"]) / "state.json"
    if not path.is_file():
        return None
    value = _read(path)
    return dict(value) if isinstance(value, Mapping) else None


def _progress(out: Path, spec: Mapping[str, Any], status: str, completed_in_invocation: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in spec.get("episodes") or []:
        state = _state_for(out, row)
        item = {
            "id": row["id"],
            "family": FAMILY,
            "binding_id": row["binding_id"],
            "treatment": row["treatment"],
            "arm": row["arm"],
            "provider": PROVIDER,
            "status": state.get("status") if state else "pending",
        }
        if state and state.get("status") == "done":
            result = state.get("result") or {}
            item.update(success=result.get("success"), actions=result.get("actions"))
        rows.append(item)
        counts[str(item["status"])] += 1
    payload = {
        "record_type": f"{VERSION}-progress",
        "version": VERSION,
        "batch_status": status,
        "planned": len(rows),
        "completed_in_invocation": completed_in_invocation,
        "counts": dict(sorted(counts.items())),
        "episodes": rows,
        "provider": PROVIDER,
        "provider_profile": PROVIDER_PROFILE,
    }
    _write(out / "progress.json", payload)
    return payload


def _make_ledger(budget: Any, spec: Mapping[str, Any], host_guard: Any) -> Any:
    factory = getattr(budget, "make_ledger", None)
    if not callable(factory):
        raise DiagnosticStop("selective_explore_budget.make_ledger() is required.")
    kwargs = {"authorization_path": spec["authorization"]["path"]}
    if host_guard is not None:
        kwargs["host_guard"] = host_guard
    try:
        ledger = factory(**kwargs)
    except TypeError:
        ledger = factory()
    if Path(getattr(ledger, "path", spec["ledger_absolute"])).resolve() != Path(spec["ledger_absolute"]).resolve():
        raise DiagnosticStop("Repair diagnostic must use the shared revision ledger.")
    return ledger


def _exclusive(budget: Any, out: Path):
    function = getattr(budget, "exclusive_run", None)
    if not callable(function):
        raise DiagnosticStop("selective_explore_budget.exclusive_run() is required.")
    try:
        params = inspect.signature(function).parameters
    except (TypeError, ValueError):
        params = {}
    if "identity_path" in params or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return function(identity_path=out / "run_identity.json", mode="run")
    return function()


def _client(budget: Any, spec: Mapping[str, Any], ledger: Any, row: Mapping[str, Any], *, sdk: Any, env_file: Path | str | None, metadata_fetcher: Any, host_guard: Any, sleep: Callable[[float], Any] | None) -> Any:
    factory = getattr(budget, "make_client", None)
    if not callable(factory):
        raise DiagnosticStop("selective_explore_budget.make_client() is required.")
    kwargs: dict[str, Any] = {
        "ledger": ledger,
        "episode": row["id"],
        "profile": SERVING_PROFILE_NAME,
        "provider_profile": PROVIDER_PROFILE,
        "authorization_path": spec["authorization"]["path"],
    }
    if env_file is not None:
        kwargs["env_path"] = env_file
    if sdk is not None:
        kwargs["sdk"] = sdk
    if metadata_fetcher is not None:
        kwargs["metadata_fetcher"] = metadata_fetcher
    if host_guard is not None:
        kwargs["host_guard"] = host_guard
    if sleep is not None:
        kwargs["sleep"] = sleep
    return factory(spec["model_locks"], **kwargs)


def _uncertain_ui(final: Mapping[str, Any]) -> bool:
    if final.get("success") is None or final.get("oracle_error"):
        return True
    def walk(value: Any) -> bool:
        if isinstance(value, Mapping):
            if value.get("status") == "uncertain":
                return True
            return any(walk(item) for item in value.values())
        if isinstance(value, list):
            return any(walk(item) for item in value)
        return False
    return walk(final.get("run"))


def prepare(
    out: Path | str = DEFAULT_OUT,
    *,
    origin: Path | str = ORIGIN_OUT,
    authorization_path: Path | str = AUTHORIZATION_PATH,
) -> dict[str, Any]:
    """Prepare the immutable v7 spec and patched plan without model calls."""

    out = Path(out).resolve()
    origin = Path(origin).resolve()
    auth_path = Path(authorization_path).resolve()
    if (out / "spec.json").is_file():
        return load_spec(out)
    origin_spec = _origin_spec(origin)
    original_plan, original_plan_path, original_plan_sha = _origin_plan(origin, origin_spec)
    evidence, witness_refs = _witness_rows(origin, origin_spec)
    patched_plan, rule, proof = _repair_result(original_plan, evidence)
    private = _choose_bindings(origin)
    out.mkdir(parents=True, exist_ok=True)
    private_path = out / "private" / "evaluator_bindings.json"
    _write(
        private_path,
        {
            "record_type": "selector-repair-private-bindings",
            "namespace": NAMESPACE,
            "version": VERSION,
            "families": {FAMILY: private},
            "visibility": "evaluator_only",
            "created_unix": time.time(),
        },
        private=True,
    )
    witness_path = out / "private" / "repair_witnesses.json"
    _write(
        witness_path,
        {
            "record_type": "selector-repair-readonly-witnesses",
            "version": VERSION,
            "source_version": ORIGIN_VERSION,
            "fields_used": ["step_id", "extracted_bindings", "local_action", "pre_elements", "after_guard_passed"],
            "witnesses": evidence,
            "source_refs": witness_refs,
            "oracle_used": False,
            "evaluator_params_used": False,
        },
        private=True,
    )
    proof["source_refs"] = witness_refs
    proof["source_plan_sha256"] = original_plan_sha
    proof_path = out / "patch_proof.json"
    _write(proof_path, proof)
    plan_path = out / "plans" / f"{FAMILY}.json"
    _write(plan_path, patched_plan)
    auth = _authorization_record(auth_path)
    budget_module = _budget()
    origin_locks = origin_spec.get("serving_model_locks") or origin_spec.get("model_locks") or {}
    if not isinstance(origin_locks, Mapping) or MODEL not in origin_locks:
        raise DiagnosticStop("v6 serving model locks are missing.")
    locks = {
        MODEL: {
            "provider": PROVIDER,
            "prompt_per_m": "0.15",
            "completion_per_m": "0.50",
            "context_length": 1048576,
            "max_tokens": 4096,
            "reservation_usd": "0.1593344",
        }
    }
    # Freeze the exact serving phase through the actual new budget facade.
    phase = _freeze_phase_manifest(out, locks, auth_path)
    execution = execution_manifest()
    rows = _episode_rows(private)
    body: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "version": VERSION,
        "namespace": NAMESPACE,
        "family": FAMILY,
        "model": MODEL,
        "provider": PROVIDER,
        "provider_profile": PROVIDER_PROFILE,
        "serving_profile": {
            "name": SERVING_PROFILE_NAME,
            "model": MODEL,
            "provider": PROVIDER,
            "max_tokens": 4096,
            "temperature": 0.0,
        },
        "model_locks": locks,
        "action_budget": MAX_ACTIONS,
        "obs_mode": pilot.OBS_MODE,
        "treatments": list(TREATMENTS),
        "run_arms": dict(RUN_ARM),
        "episodes": rows,
        "ledger_absolute": str(pilot.SHARED_LEDGER.resolve()),
        "run_lock_absolute": str(pilot.SHARED_RUN_LOCK.resolve()),
        "authorization": auth,
        "phase_run": phase,
        "origin": {
            "root": str(origin),
            "version": ORIGIN_VERSION,
            "spec_file_sha256": _hash(origin / "spec.json"),
            "spec_sha256": origin_spec.get("spec_sha256"),
            "source_manifest_sha256": origin_spec.get("source_manifest_sha256"),
        },
        "original_plan": {
            "path": str(original_plan_path),
            "sha256": original_plan_sha,
            "file_sha256": _hash(original_plan_path),
            "version": ORIGIN_VERSION,
        },
        "patched_plan": str(plan_path.relative_to(out)),
        "patched_plan_sha256": _json_hash(patched_plan),
        "patched_plan_file_sha256": _hash(plan_path),
        "patch_proof": str(proof_path.relative_to(out)),
        "patch_proof_sha256": _hash(proof_path),
        "patch_proof_source": proof["helper"],
        "inferred_static_prefix": rule["note_name"]["prefix"],
        "inferred_binding_render_rules": rule,
        "repair_witnesses": str(witness_path.relative_to(out)),
        "repair_witnesses_sha256": _hash(witness_path),
        "private_bindings": str(private_path.relative_to(out)),
        "private_bindings_sha256": _hash(private_path),
        "private_binding_visibility": "evaluator_only",
        "repair_inputs": {
            "source_plan_sha256": original_plan_sha,
            "source_version": ORIGIN_VERSION,
            "witness_source_refs": witness_refs,
            "fields_used": ["step_id", "extracted_bindings", "local_action", "pre_elements", "after_guard_passed"],
            "oracle_used": False,
            "evaluator_params_used": False,
        },
        "execution_manifest": execution,
        "reporting": {
            "diagnostic_only": True,
            "no_build_phase": True,
            "raw_and_rendered_bindings_persisted_separately": True,
            "unknown_costs_reported_as_bounds": True,
            "prior_learning_and_build_reported_separately": True,
            "success_failures_retained_in_denominators": True,
        },
    }
    body["spec_sha256"] = _json_hash(body)
    _write(out / "spec.json", body)
    # ``budget_module`` is intentionally retained only to force the actual
    # facade import before the prepared artifact is delivered.
    del budget_module
    return dict(body)


def run(
    out: Path | str = DEFAULT_OUT,
    *,
    max_episodes: int | None = None,
    env_file: Path | str | None = None,
    env_factory: Callable[[], Any] | None = None,
    ledger: Any = None,
    sdk: Any = None,
    metadata_fetcher: Any = None,
    host_guard: Any = None,
    sleep: Callable[[float], Any] | None = None,
) -> dict[str, Any]:
    """Run up to all eight fresh rows under one shared worker lock."""

    out = Path(out).resolve()
    spec = load_spec(out)
    if any(row.get("treatment") == "patched_full_fallback" for row in spec.get("episodes") or []) and not _supports_binding_render_rules():
        raise DiagnosticStop("The stable pilot lacks the required binding_render_rules hook for patched_full_fallback.")
    budget = _budget()
    if ledger is None:
        ledger = _make_ledger(budget, spec, host_guard)
    validator = getattr(budget, "validate_locks", None)
    if not callable(validator):
        raise DiagnosticStop("selective_explore_budget.validate_locks() is required.")
    validator_kwargs = {
        "provider_profile": PROVIDER_PROFILE,
        "request_profile": SERVING_PROFILE_NAME,
    }
    if metadata_fetcher is not None:
        validator_kwargs["metadata_fetcher"] = metadata_fetcher
    if sleep is not None:
        validator_kwargs["sleep"] = sleep
    validator(spec["model_locks"], **validator_kwargs)
    private = _private_bindings(out, spec)
    if set(private) != {"b01", "b02"}:
        raise DiagnosticStop("Private v7 binding map is incomplete.")
    attempted = 0
    batch_status = "complete"
    with _exclusive(budget, out):
        for row in spec.get("episodes") or []:
            state = _state_for(out, row)
            if state and state.get("status") in TERMINAL:
                continue
            trajectory = out / "episodes" / str(row["id"]) / "trajectory.jsonl"
            if state or trajectory.is_file() and trajectory.stat().st_size:
                _mark_state(out, row, "interrupted", "prior state or action evidence is terminal; no replay")
                batch_status = "interrupted"
                _progress(out, spec, batch_status, attempted)
                break
            checker = getattr(ledger, "has_episode", None)
            if not callable(checker):
                raise BudgetStop("Shared ledger cannot verify prior episode receipts.")
            try:
                prior = bool(checker(row["id"]))
            except Exception as exc:
                raise BudgetStop("Shared ledger receipt lookup failed; no replay is safe.") from exc
            if prior:
                _mark_state(out, row, "prior_receipt", "receipt already exists; no UI replay")
                batch_status = "blocked_prior_receipt"
                _progress(out, spec, batch_status, attempted)
                break
            if max_episodes is not None and attempted >= max_episodes:
                batch_status = "pilot_limit"
                break
            binding = private.get(str(row["binding_id"]))
            if binding is None:
                raise DiagnosticStop(f"Private binding {row['binding_id']} is missing.")
            treatment = str(row["treatment"])
            plan = None
            rules: dict[str, dict[str, str]] | None = None
            rendering: Mapping[str, Any] | None = None
            if treatment == "original_full_fallback" or treatment == "original_local_rejoin":
                plan = _origin_plan(Path(spec["origin"]["root"]), _origin_spec(Path(spec["origin"]["root"])))[0]
            elif treatment == "patched_full_fallback":
                # Keep the extraction prompt and raw model JSON identical to
                # the original-plan baseline.  The learned descriptor repair
                # is the deterministic binding render rule applied by the
                # stable pilot immediately before run_plan.
                plan = _origin_plan(Path(spec["origin"]["root"]), _origin_spec(Path(spec["origin"]["root"])))[0]
                rules = copy.deepcopy(dict(spec["inferred_binding_render_rules"]))
            env = None
            try:
                client = _client(
                    budget,
                    spec,
                    ledger,
                    row,
                    sdk=sdk,
                    env_file=env_file,
                    metadata_fetcher=metadata_fetcher,
                    host_guard=host_guard,
                    sleep=sleep,
                )
                env = env_factory() if env_factory is not None else pilot._new_env()
            except (BudgetStop, DiagnosticStop, pilot.PilotStop) as exc:
                if env is not None:
                    try:
                        env.close()
                    except Exception:
                        pass
                status = "budget_stopped" if isinstance(exc, BudgetStop) else "interrupted"
                _mark_state(out, row, status, _safe_first_line(exc))
                _progress(out, spec, status, attempted + 1)
                attempted += 1
                batch_status = status
                break
            try:
                kwargs: dict[str, Any] = {
                    "out": out,
                    "spec": {"action_budget": MAX_ACTIONS},
                    "row": row,
                    "binding_row": binding,
                    "env": env,
                    "client": client,
                    "budget": budget,
                    "plan": plan,
                }
                if rules is not None:
                    kwargs["binding_render_rules"] = rules
                final = pilot._run_one_episode(**kwargs)
                if isinstance(final, Mapping):
                    candidate_rendering = final.get("binding_rendering")
                    if candidate_rendering is None and isinstance(final.get("run"), Mapping):
                        candidate_rendering = final["run"].get("binding_rendering")
                    rendering = candidate_rendering if isinstance(candidate_rendering, Mapping) else None
                _annotate_state(out, row, spec, rendering, rules)
                if _uncertain_ui(final):
                    state_path = out / "episodes" / str(row["id"]) / "state.json"
                    state = _read(state_path) if state_path.is_file() else {}
                    state.update(status="uncertain", uncertainty="UI/oracle evidence was unavailable; no later row is started")
                    _write(state_path, state, private=True)
                    batch_status = "uncertain"
                    _progress(out, spec, batch_status, attempted + 1)
                    attempted += 1
                    break
            except BudgetStop:
                _annotate_state(out, row, spec, rendering, rules)
                batch_status = "budget_stopped"
                _progress(out, spec, batch_status, attempted + 1)
                attempted += 1
                break
            except (pilot.PilotStop, DiagnosticStop):
                _annotate_state(out, row, spec, rendering, rules)
                batch_status = "interrupted"
                _progress(out, spec, batch_status, attempted + 1)
                attempted += 1
                break
            finally:
                try:
                    env.close()
                except Exception:
                    pass
            attempted += 1
            _progress(out, spec, batch_status, attempted)
    final_progress = _progress(out, spec, batch_status, attempted)
    if batch_status == "complete" and final_progress["counts"].get("pending", 0):
        final_progress["batch_status"] = "incomplete"
        _write(out / "progress.json", final_progress)
    return final_progress


def _number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() else None


def _money(nano: int | Decimal | None) -> str | None:
    if nano is None:
        return None
    return format(Decimal(nano) / NANO, "f")


def _receipt_rows(ledger_path: Path, episode: str | None = None) -> list[dict[str, Any]]:
    if not ledger_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{ledger_path.resolve()}?mode=ro", uri=True, timeout=5) as database:
            columns = {str(row[1]) for row in database.execute("PRAGMA table_info(calls)").fetchall()}
            required = {"id", "episode", "state", "reserved_nano", "actual_nano", "response_json"}
            if not required <= columns:
                raise DiagnosticStop("Shared ledger calls table has an unexpected schema.")
            select = "SELECT id,episode,state,reserved_nano,actual_nano,response_json FROM calls"
            params: tuple[Any, ...] = ()
            if episode is not None:
                select += " WHERE episode=? OR episode LIKE ?"
                params = (episode, episode + "/%")
            select += " ORDER BY created,id" if "created" in columns else " ORDER BY id"
            rows = database.execute(select, params).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise DiagnosticStop("Shared ledger could not be read read-only.") from exc
    return [
        {
            "id": row[0],
            "episode": row[1],
            "state": row[2],
            "reserved_nano": row[3] if isinstance(row[3], int) else 0,
            "actual_nano": row[4] if isinstance(row[4], int) else None,
            "response_json": row[5],
        }
        for row in rows
    ]


def _usage_from_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    usage = body.get("usage") if isinstance(body, Mapping) else None
    if not isinstance(usage, Mapping):
        return {}
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), Mapping) else {}
    return {
        "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
        "completion_tokens": usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
        "cached_tokens": usage.get("cached_tokens") if isinstance(usage.get("cached_tokens"), int) else details.get("cached_tokens") if isinstance(details.get("cached_tokens"), int) else None,
        "total_tokens": usage.get("total_tokens") if isinstance(usage.get("total_tokens"), int) else None,
    }


def _trace_usage(path: Path) -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    calls = 0
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                item = json.loads(line)
                if isinstance(item, Mapping) and item.get("record_type") == "model_call":
                    calls += 1
                    values.append(dict(item.get("usage") or {}))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"model_calls": None, "usage": {"error": "trajectory_unreadable"}}
    result: dict[str, Any] = {"model_calls": calls, "usage": {"missing": {}}}
    for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        known = [int(item[field]) for item in values if isinstance(item.get(field), int) and not isinstance(item.get(field), bool)]
        result["usage"][field] = sum(known) if known else None
        result["usage"]["missing"][field] = len(values) - len(known)
    return result


def _receipt_summary(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    lower = 0
    upper = 0
    unknown = 0
    settled = 0
    usages: list[dict[str, Any]] = []
    for row in receipts:
        actual = row.get("actual_nano") if isinstance(row.get("actual_nano"), int) else None
        reserved = int(row.get("reserved_nano") or 0)
        state = str(row.get("state") or "unknown")
        if actual is not None:
            lower += actual
            upper += actual
        if actual is None or state != "settled":
            upper += reserved
            unknown += 1
        if actual is not None and state == "settled":
            settled += 1
        usages.append(_usage_from_response(row.get("response_json")))
    tokens: dict[str, Any] = {"missing": {}}
    for field in ("prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens"):
        known = [item[field] for item in usages if isinstance(item.get(field), int)]
        tokens[field] = sum(known) if known else None
        tokens["missing"][field] = len(usages) - len(known)
    return {
        "receipt_count": len(receipts),
        "settled_receipts": settled,
        "unknown_receipts": unknown,
        "lower_bound_usd": _money(lower),
        "upper_bound_usd": _money(upper),
        "tokens": tokens,
    }


def _trace_stats(state: Mapping[str, Any], treatment: str) -> dict[str, Any]:
    result = state.get("result") if isinstance(state, Mapping) else None
    run = result.get("run") if isinstance(result, Mapping) else None
    plan = run.get("plan") if isinstance(run, Mapping) else None
    trace = plan.get("trace") if isinstance(plan, Mapping) else None
    if not isinstance(trace, list):
        return {"verified_rejoin": None, "local_actions": None, "suffix_actions": None}
    verified = 0
    local_actions = 0
    suffix = 0
    joined = False
    for item in trace:
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == "local":
            local_actions += 1
            guards = item.get("guards") if isinstance(item.get("guards"), Mapping) else {}
            before = guards.get("before") if isinstance(guards.get("before"), Mapping) else {}
            after = guards.get("after") if isinstance(guards.get("after"), Mapping) else {}
            if before.get("ok") is not True and after.get("ok") is True:
                verified += 1
                joined = True
        elif item.get("kind") == "program" and joined:
            suffix += 1
    return {
        "verified_rejoin": verified,
        "local_actions": local_actions,
        "suffix_actions": suffix,
    }


def _aggregate_cost(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return _receipt_summary(list(rows))


def _prior_costs(ledger_path: Path) -> dict[str, Any]:
    rows = _receipt_rows(ledger_path)
    learning = [row for row in rows if str(row.get("episode") or "").startswith(NAMESPACE + "/train/")]
    build = [
        row
        for row in rows
        if str(row.get("episode") or "").startswith(NAMESPACE + "/")
        and "/build/" in str(row.get("episode") or "")
        and not str(row.get("episode") or "").startswith(NAMESPACE + "/" + VERSION + "/")
    ]
    return {
        "learning": _aggregate_cost(learning),
        "build": _aggregate_cost(build),
        "learning_episode_prefix": NAMESPACE + "/train/",
        "build_receipt_scope": "all prior selective build episodes, including v6",
    }


def _write_analysis_md(out: Path, report: Mapping[str, Any]) -> None:
    lines = [
        f"# {VERSION} analysis",
        "",
        "Receipt-derived diagnostic report. Unknown billing remains an upper-bound range.",
        "",
        "| Treatment | Complete | Successes | Failures | Model calls | Verified rejoin | Suffix actions | Cost lower USD | Cost upper USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for treatment in TREATMENTS:
        item = report["treatments"][treatment]
        cost = item["cost_bounds"]
        lines.append(
            f"| {treatment} | {str(item['complete']).lower()} | {item['successes']} | {item['failures']} | "
            f"{item['model_calls']} | {item['verified_rejoin']} | {item['suffix_actions']} | "
            f"{cost['lower_bound_usd']} | {cost['upper_bound_usd']} |"
        )
    lines.extend(
        [
            "",
            "Prior learning and build receipts are reported separately under `prior_costs`.",
        ]
    )
    (out / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    """Analyze every row, retaining completed failures and unknown cost bounds."""

    out = Path(out).resolve()
    spec = load_spec(out)
    episode_reports: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {treatment: [] for treatment in TREATMENTS}
    ledger_path = Path(spec["ledger_absolute"])
    for row in spec.get("episodes") or []:
        state = _state_for(out, row)
        state_status = str(state.get("status") if state else "pending")
        final = state.get("result") if isinstance(state, Mapping) else None
        final = final if isinstance(final, Mapping) else {}
        trace_usage = _trace_usage(out / "episodes" / str(row["id"]) / "trajectory.jsonl")
        receipts = _receipt_rows(ledger_path, str(row["id"]))
        cost = _receipt_summary(receipts)
        trace = _trace_stats(state or {}, str(row["treatment"]))
        complete = state_status == "done" and isinstance(final.get("success"), bool)
        item = {
            "id": row["id"],
            "binding_id": row["binding_id"],
            "treatment": row["treatment"],
            "arm": row["arm"],
            "status": state_status,
            "complete": complete,
            "success": final.get("success") if complete else None,
            "failure": bool(complete and final.get("success") is False),
            "model_calls": trace_usage.get("model_calls"),
            "usage": trace_usage.get("usage"),
            "cost_bounds": cost,
            "verified_rejoin": trace.get("verified_rejoin"),
            "local_actions": trace.get("local_actions"),
            "suffix_actions": trace.get("suffix_actions"),
        }
        episode_reports.append(item)
        grouped.setdefault(str(row["treatment"]), []).append(item)
    treatments: dict[str, Any] = {}
    for treatment in TREATMENTS:
        items = grouped[treatment]
        completed = [item for item in items if item["complete"]]
        successes = sum(item["success"] is True for item in completed)
        failures = sum(item["failure"] is True for item in completed)
        costs = _receipt_summary(
            [receipt for item in items for receipt in _receipt_rows(ledger_path, str(item["id"]))]
        )
        model_calls = [item["model_calls"] for item in items if isinstance(item["model_calls"], int)]
        verified = [item["verified_rejoin"] for item in items if isinstance(item["verified_rejoin"], int)]
        suffix = [item["suffix_actions"] for item in items if isinstance(item["suffix_actions"], int)]
        cached = [
            int((item.get("usage") or {}).get("cached_tokens"))
            for item in items
            if isinstance((item.get("usage") or {}).get("cached_tokens"), int)
        ]
        treatments[treatment] = {
            "planned": len(items),
            "completed": len(completed),
            "complete": len(completed) == len(items) if items else False,
            "successes": successes,
            "failures": failures,
            "incomplete": len(items) - len(completed),
            "success_rate": successes / len(completed) if completed else None,
            "model_calls": sum(model_calls) if model_calls else 0,
            "cached_tokens": sum(cached) if cached else None,
            "verified_rejoin": sum(verified) if verified else 0,
            "suffix_actions": sum(suffix) if suffix else 0,
            "cost_bounds": costs,
        }
    progress = _read(out / "progress.json") if (out / "progress.json").is_file() else {}
    report = {
        "record_type": f"{VERSION}-analysis",
        "version": VERSION,
        "family": FAMILY,
        "model": MODEL,
        "provider": PROVIDER,
        "provider_profile": PROVIDER_PROFILE,
        "complete": all(item["complete"] for item in episode_reports) if episode_reports else False,
        "progress_status": progress.get("batch_status") if isinstance(progress, Mapping) else None,
        "episodes": episode_reports,
        "treatments": treatments,
        "prior_costs": _prior_costs(ledger_path),
        "budget": {
            "ledger": str(ledger_path),
            "mode": "read_only",
            "authorization_sha256": spec["authorization"]["authorization_sha256"],
            "unknown_costs_are_upper_bounds": True,
        },
        "accounting": {
            "successes_include_completed_failures_in_denominator": True,
            "all_attempted_receipts_retained": True,
            "prior_learning_and_build_separate": True,
            "rendered_binding_costs_are_serving_only": True,
            "selector_learning_used_oracle": False,
        },
    }
    _write(out / "analysis.json", report)
    _write_analysis_md(out, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--analyze", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-episodes", type=int, default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    out = (DEFAULT_OUT if args.out is None else args.out).resolve()
    if out.name != VERSION:
        parser.error(f"--out basename must be {VERSION!r}")
    try:
        if args.prepare:
            result = prepare(out)
            print(json.dumps({"status": "prepared", "spec_sha256": result["spec_sha256"]}, indent=2))
            return 0
        if args.run:
            result = run(out, max_episodes=args.max_episodes, env_file=args.env_file)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if result.get("batch_status") in {"complete", "pilot_limit"} else 2
        result = analyze(out)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (DiagnosticStop, pilot.PilotStop, BudgetStop) as exc:
        print(f"STOPPED: {_safe_first_line(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
