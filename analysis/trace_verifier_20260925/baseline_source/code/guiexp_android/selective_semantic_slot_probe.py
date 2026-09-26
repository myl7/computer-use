"""One-pass, source-only semantic-slot annotation probe.

Preparation freezes the three source rows, prompts, profile, source/runtime,
and original ``evidence_probe_v1`` diagnostic config. Run sends one
``serving_4096`` request per row, saves raw evidence first, and never repairs,
builds a program, uses held-out data, or executes Android actions.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import selective_diagnostic_budget as diagnostic_budget
from . import selective_evidence_probe as evidence_probe
from . import selective_explore_budget as explore_budget
from . import selective_pilot as pilot
from .budget_client import BudgetStop

VERSION = "semantic_slots_v1"
SCHEMA = "selective-semantic-slot-probe/1"
NAMESPACE = "selective_20260915"
DIAGNOSTIC_PREFIX = "selective_20260915/evidence_probe_v1"
MODEL = explore_budget.MODEL
PROVIDER_PROFILE = "z_ai_fp8"
PROVIDER = "z-ai/fp8"
REQUEST_PROFILE = "serving_4096"
MAX_PHYSICAL_ATTEMPTS = 1
INPUT_SHA256 = "e8731f59ae1a1fa0310c5a200cb1cb58f147d8e1da84cf3787f61d52b8ee584a"
ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = ROOT / "experimental-results/guiexp_android/selective_20260915/offline_controls/goal_template_input_v1/inputs.json"
DIAGNOSTIC_CONFIG_PATH = ROOT / "experimental-results/guiexp_android/selective_20260915/evidence_probe_v1/diagnostic_budget.json"
SHARED_LEDGER_PATH = diagnostic_budget.SHARED_LEDGER_PATH
SHARED_RUN_LOCK_PATH = diagnostic_budget.SHARED_RUN_LOCK_PATH
AUTHORIZATION_PATH = diagnostic_budget.AUTHORIZATION_PATH
DEFAULT_OUT = ROOT / "experimental-results/guiexp_android/selective_20260915/semantic_slots_v1"
SPEC_NAME, SUMMARY_NAME, CLAIM_NAME = "spec.json", "batch_summary.json", "run_claim.json"

ProbeStop = pilot.PilotStop
_hash, _file_hash, _read = evidence_probe._hash_value, evidence_probe._hash_file, evidence_probe._read
_receipts, _billing = evidence_probe._ledger_receipts, evidence_probe._billing_known


def _write(path: Path, value: Any, *, private: bool = True) -> None:
    pilot.atomic_json(path, value, private=private)


def _safe_error(exc: BaseException) -> str:
    return evidence_probe._safe_error(exc)


def _profile() -> dict[str, Any]:
    try:
        lock = explore_budget._lock_set(None, PROVIDER_PROFILE, REQUEST_PROFILE)[MODEL]
    except (AttributeError, BudgetStop) as exc:
        raise ProbeStop("serving profile lock is unavailable") from exc
    if (lock.get("provider"), lock.get("max_tokens"), lock.get("reasoning"),
            lock.get("max_physical_attempts")) != (PROVIDER, 4096, None, MAX_PHYSICAL_ATTEMPTS):
        raise ProbeStop("serving profile is not the frozen z_ai_fp8/4096 one-attempt profile")
    return {"name": REQUEST_PROFILE, "model": MODEL, "provider_profile": PROVIDER_PROFILE,
            "provider": PROVIDER, "max_tokens": 4096, "reasoning": None,
            "temperature": 0.0, "max_physical_attempts": MAX_PHYSICAL_ATTEMPTS,
            "model_lock": lock}


def _source_manifest() -> dict[str, Any]:
    return evidence_probe._source_manifest()


def _verify_source_manifest(value: Mapping[str, Any]) -> None:
    try:
        evidence_probe._verify_source_manifest(value)
    except evidence_probe.ProbeStop as exc:
        raise ProbeStop(str(exc)) from exc


def _input(path: Path) -> dict[str, Any]:
    if path.resolve() == INPUT_PATH.resolve() and _file_hash(path) != INPUT_SHA256:
        raise ProbeStop("frozen semantic-slot input hash changed")
    value = _read(path); rows = value.get("rows") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or value.get("schema") != "goal-template-source-input/1":
        raise ProbeStop("semantic-slot input schema changed")
    if value.get("heldout_data_used") not in (None, False) or value.get("oracle_fields_used") not in (None, []):
        raise ProbeStop("semantic-slot input contains held-out or oracle data")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ProbeStop("semantic-slot probe requires exactly three source rows")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("family"), str) or not row["family"]:
            raise ProbeStop("source row family is invalid")
        if row["family"] in seen or not isinstance(row.get("source_goal"), str) or not row["source_goal"].strip():
            raise ProbeStop("source rows need unique families and nonempty goals")
        if not isinstance(row.get("literals"), list) or any(type(item) is not str or not item.strip() for item in row["literals"]):
            raise ProbeStop("source row literals are invalid")
        try:
            evidence_probe._safe_key(row["family"])
        except evidence_probe.ProbeStop as exc:
            raise ProbeStop("source row family is not a safe identifier") from exc
        seen.add(row["family"])
    return dict(value)


def build_prompt(row: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build the sole request from one source goal and observed literals."""
    goal, literals = row.get("source_goal"), row.get("literals")
    if not isinstance(goal, str) or not isinstance(literals, list):
        raise ProbeStop("source row cannot build a prompt")
    instruction = (
        "Return exactly one JSON object with exactly this shape: "
        '{"source_values": ["exact nonempty source-goal substring"]}. '
        "List task-specific values whose changes matter to observed literal strings. Prefer "
        "a complete filename including its extension, the full text body, and full source or "
        "destination names. Every value must be an exact nonempty substring of SOURCE GOAL "
        "and supported by an observed literal through exact identity, filename stem or suffix, "
        "first or last word, or a literal-wrapper relation. Exclude fields when mapping is "
        "unknown. Do not choose operation verbs, app names, navigation labels, or buttons "
        "merely because letters overlap. Use only the source goal and source literals below. "
        "Return no explanation, program, JSON patch, held-out goal, or action."
    )
    content = instruction + "\n\nSOURCE GOAL:\n" + goal + "\n\nOBSERVED SOURCE LITERALS:\n"
    return [{"role": "system", "content": "You emit one exact data-only JSON object."},
            {"role": "user", "content": content + json.dumps(literals, ensure_ascii=True, indent=2)}]


def _config(path: Path, ledger: Path, auth: Path) -> dict[str, Any]:
    try:
        value = diagnostic_budget.load_diagnostic_config(path, expected_prefix=DIAGNOSTIC_PREFIX,
                                                         ledger_path=ledger, authorization_path=auth)
    except BudgetStop as exc:
        raise ProbeStop(f"diagnostic config cannot be verified: {_safe_error(exc)}") from exc
    if value.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH):
        raise ProbeStop("diagnostic config does not use the shared run lock")
    return value


def exact_command(out: Path | str = DEFAULT_OUT) -> str:
    return ("../.venv-android/bin/python -m guiexp_android.selective_semantic_slot_probe "
            f"--run --out {shlex.quote(str(Path(out).resolve()))}")


def _claim_run(out: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Create one permanent lifetime claim before any validation or lock."""
    path = out / CLAIM_NAME
    value = {"schema": SCHEMA, "status": "claimed", "version": VERSION,
             "spec_sha256": spec.get("spec_sha256"), "pid": os.getpid(),
             "claimed_unix": time.time()}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
            handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise BudgetStop("semantic-slot run claim already exists; rerun is refused") from exc
    except OSError as exc:
        raise BudgetStop("semantic-slot run claim could not be created") from exc
    return value


def prepare(out: Path | str = DEFAULT_OUT, *, input_path: Path | str | None = None,
            diagnostic_config_path: Path | str | None = None, ledger_path: Path | str | None = None,
            authorization_path: Path | str | None = None) -> dict[str, Any]:
    """Freeze three source-only requests without constructing an SDK."""
    out, source_path = Path(out).resolve(), Path(input_path or INPUT_PATH).resolve()
    config_path = Path(diagnostic_config_path or DIAGNOSTIC_CONFIG_PATH).resolve()
    ledger, auth = Path(ledger_path or SHARED_LEDGER_PATH).resolve(), Path(authorization_path or AUTHORIZATION_PATH).resolve()
    source = _input(source_path); _config(config_path, ledger, auth); profile = _profile(); manifest = _source_manifest()
    rows = [{"index": i, "family": str(row["family"]),
             "episode": f"{DIAGNOSTIC_PREFIX}/{VERSION}/{row['family']}",
             "source_row_sha256": _hash(row), "prompt_sha256": _hash(build_prompt(row))}
            for i, row in enumerate(source["rows"])]
    body: dict[str, Any] = {"schema": SCHEMA, "version": VERSION, "namespace": NAMESPACE,
        "input_path": str(source_path), "input_sha256": _file_hash(source_path),
        "diagnostic_config_path": str(config_path), "diagnostic_config_sha256": _file_hash(config_path),
        "diagnostic_prefix": DIAGNOSTIC_PREFIX, "shared_ledger": str(ledger),
        "shared_run_lock": str(SHARED_RUN_LOCK_PATH), "authorization_path": str(auth),
        "model": MODEL, "provider_profile": PROVIDER_PROFILE, "provider": PROVIDER,
        "profile": profile, "profile_sha256": _hash(profile), "source_manifest": manifest,
        "source_manifest_sha256": _hash(manifest), "rows": rows, "request_count": 3,
        "max_physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        "budgets": {"global_usd": "20", "tranche_usd": "10", "diagnostic_usd": "1"},
        "execution": {"ui_actions": False, "heldout_data": False, "repair_calls": False, "sdk_in_prepare": False},
        "prompt_policy": "source_goal plus observed source literals only", "exact_command": exact_command(out)}
    body["spec_sha256"] = _hash(body); out.mkdir(parents=True, exist_ok=True); path = out / SPEC_NAME
    if path.exists():
        current = load_spec(out)
        if current != body: raise ProbeStop("existing semantic-slot spec differs from the frozen request")
        return current
    if any(item.name != SPEC_NAME for item in out.iterdir()):
        raise ProbeStop("semantic-slot output contains a partial draft")
    _write(path, body, private=False); return body


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    out = Path(out).resolve(); value = _read(out / SPEC_NAME)
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA or value.get("spec_sha256") != _hash({k: v for k, v in value.items() if k != "spec_sha256"}):
        raise ProbeStop("semantic-slot spec schema or hash is invalid")
    if value.get("request_count") != 3 or value.get("diagnostic_prefix") != DIAGNOSTIC_PREFIX:
        raise ProbeStop("semantic-slot request bound or diagnostic prefix changed")
    if value.get("input_sha256") != _file_hash(Path(value["input_path"])):
        raise ProbeStop("semantic-slot source input drifted after preparation")
    if value.get("source_manifest_sha256") != _hash(value.get("source_manifest")):
        raise ProbeStop("semantic-slot source or runtime manifest changed")
    _verify_source_manifest(value.get("source_manifest") or {})
    if value.get("profile_sha256") != _hash(value.get("profile")) or value.get("profile") != _profile():
        raise ProbeStop("semantic-slot serving profile changed")
    config_path = Path(value["diagnostic_config_path"])
    if value.get("diagnostic_config_sha256") != _file_hash(config_path):
        raise ProbeStop("semantic-slot diagnostic config changed")
    _config(config_path, Path(value["shared_ledger"]), Path(value["authorization_path"]))
    if len(value.get("rows") or []) != 3: raise ProbeStop("semantic-slot spec does not contain exactly three rows")
    return dict(value)


def _ledger_raw(ledger: Any, episode: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    try:
        with ledger.connect() as db:
            row = db.execute("SELECT response_json FROM calls WHERE episode=? ORDER BY created DESC,id DESC LIMIT 1", (episode,)).fetchone()
        raw = json.loads(row[0]) if row and row[0] else {}; raw = dict(raw) if isinstance(raw, Mapping) else {}
        return raw, pilot.usage_from_response(raw), _receipts(ledger, episode)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {}, _receipts(ledger, episode)


def _prior_output(out: Path, spec: Mapping[str, Any], ledger: Any) -> None:
    if any((out / name).exists() for name in (SUMMARY_NAME, CLAIM_NAME, "run_identity.json")):
        raise BudgetStop("semantic-slot output already has run state; rerun is refused")
    if any(p.name == "state.json" or p.name.startswith("call_") for p in out.rglob("*.json")):
        raise BudgetStop("semantic-slot output already has state or raw receipts; rerun is refused")
    if any(_receipts(ledger, str(row["episode"])) for row in spec["rows"]):
        raise BudgetStop("semantic-slot episode already has a receipt; rerun is refused")


def _verify_request(spec: Mapping[str, Any], item: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, str]]:
    if _file_hash(Path(spec["input_path"])) != spec["input_sha256"]:
        raise ProbeStop("source input hash drifted before request")
    row = source["rows"][item["index"]]
    if _hash(row) != item["source_row_sha256"] or row.get("family") != item.get("family"):
        raise ProbeStop("source row drifted before request")
    if _file_hash(Path(spec["diagnostic_config_path"])) != spec["diagnostic_config_sha256"]:
        raise ProbeStop("diagnostic config drifted before request")
    if _hash(spec["source_manifest"]) != spec["source_manifest_sha256"]:
        raise ProbeStop("source or runtime manifest drifted before request")
    _verify_source_manifest(spec["source_manifest"])
    if spec.get("profile_sha256") != _hash(spec.get("profile")) or spec.get("profile") != _profile():
        raise ProbeStop("serving profile drifted before request")
    _config(Path(spec["diagnostic_config_path"]), Path(spec["shared_ledger"]), Path(spec["authorization_path"]))
    messages = build_prompt(row)
    if _hash(messages) != item["prompt_sha256"]: raise ProbeStop("semantic-slot prompt changed before request")
    return messages


def _parse(raw_text: str, row: Mapping[str, Any]) -> tuple[str, list[str] | None, str | None]:
    try: value = json.loads(raw_text.strip())
    except (TypeError, ValueError) as exc: return "malformed_json", None, _safe_error(exc)
    if not isinstance(value, Mapping) or set(value) != {"source_values"} or not isinstance(value.get("source_values"), list):
        return "invalid_schema", None, "response must contain only source_values array"
    values = value["source_values"]
    if any(type(item) is not str or not item.strip() or item not in str(row["source_goal"]) for item in values):
        return "invalid_source_values", None, "every source value must be a nonempty exact goal substring"
    if len(values) != len(set(values)): return "invalid_source_values", None, "source_values contains duplicates"
    return "returned", list(values), None


def _make_client(spec: Mapping[str, Any], row: Mapping[str, Any], ledger: Any, *, env_file: Path | str | None,
                 sdk: Any, metadata_fetcher: Any, host_guard: Any) -> Any:
    kwargs: dict[str, Any] = {"ledger": ledger, "episode": row["episode"], "profile": REQUEST_PROFILE,
                              "provider_profile": PROVIDER_PROFILE, "host_guard": host_guard}
    if env_file is not None: kwargs["env_path"] = env_file
    if sdk is not None: kwargs["sdk"] = sdk
    if metadata_fetcher is not None: kwargs["metadata_fetcher"] = metadata_fetcher
    return explore_budget.make_client({MODEL: spec["profile"]["model_lock"]}, **kwargs)


def _finish(out: Path, row_dir: Path, call_path: Path, item: Mapping[str, Any], status: str,
            receipts: Sequence[str], error: str | None = None, values: Sequence[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"family": item["family"], "status": status,
                              "call": str(call_path.relative_to(out)), "receipt_ids": list(receipts)}
    if values is not None: result["source_values"] = list(values)
    if error: result["error"] = error
    _write(row_dir / "state.json", {"schema": SCHEMA, **result}); return result


def _save_call(path: Path, item: Mapping[str, Any], raw: Mapping[str, Any], usage: Mapping[str, Any],
               receipts: Sequence[str], error: str | None = None) -> None:
    value = {"schema": SCHEMA, "family": item["family"], "episode": item["episode"],
             "raw_response": dict(raw), "usage": dict(usage), "receipt_ids": list(receipts),
             "response_sha256": _hash(raw), "saved_before_parse": True}
    if error: value["error"] = error
    _write(path, value)


def _provider_validate(spec: Mapping[str, Any], metadata_fetcher: Any) -> None:
    kwargs: dict[str, Any] = {"provider_profile": PROVIDER_PROFILE, "request_profile": REQUEST_PROFILE}
    if metadata_fetcher is not None: kwargs["metadata_fetcher"] = metadata_fetcher
    explore_budget.validate_locks({MODEL: spec["profile"]["model_lock"]}, **kwargs)


def run(out: Path | str = DEFAULT_OUT, *, client_factory: Callable[..., Any] | None = None,
        env_file: Path | str | None = None, sdk: Any = None, metadata_fetcher: Any = None,
        host_guard: Any = None, ledger: Any = None) -> dict[str, Any]:
    """Send one request per row and stop the batch on uncertainty."""
    out, spec = Path(out).resolve(), load_spec(out); source = _input(Path(spec["input_path"]))
    guard = host_guard if host_guard is not None else explore_budget.read_host_state
    if ledger is None: ledger = diagnostic_budget.make_diagnostic_ledger(spec["diagnostic_config_path"], host_guard=guard)
    _prior_output(out, spec, ledger)
    _claim_run(out, spec)
    if client_factory is None or metadata_fetcher is not None: _provider_validate(spec, metadata_fetcher)
    results: list[dict[str, Any]] = []; batch_status, stop_reason = "complete", None
    with explore_budget.exclusive_run(identity_path=out / "run_identity.json", mode=VERSION):
        for item in spec["rows"]:
            row_dir = out / "rows" / str(item["family"]); row_dir.mkdir(parents=True, exist_ok=True)
            messages = _verify_request(spec, item, _input(Path(spec["input_path"])))
            _write(row_dir / "state.json", {"schema": SCHEMA, "status": "running", "family": item["family"], "episode": item["episode"]})
            call_path, raw, usage, receipts = row_dir / "call_01.json", {}, {}, []
            try:
                client = client_factory(spec, item, ledger) if client_factory is not None else _make_client(
                    spec, item, ledger, env_file=env_file, sdk=sdk, metadata_fetcher=metadata_fetcher, host_guard=guard)
                pilot._begin_episode(client, item["episode"])
                response, usage = pilot._call_client(client, messages, item["episode"])
                raw, receipts = pilot._response_dump(response), _receipts(ledger, item["episode"])
            except Exception as exc:  # noqa: BLE001
                raw, usage, receipts = _ledger_raw(ledger, item["episode"]); error = _safe_error(exc)
                _save_call(call_path, item, raw, usage, receipts, error)
                status = "budget_stopped" if isinstance(exc, BudgetStop) else "ambiguous"
                results.append(_finish(out, row_dir, call_path, item, status, receipts, error))
                batch_status, stop_reason = status, error; break
            _save_call(call_path, item, raw, usage, receipts)
            status, values, error = "returned", None, None
            if not _billing(usage): status, error = "budget_stopped", "response lacks settled billing evidence"
            elif not pilot.response_content(raw).strip(): status, error = "empty_response", "model response was empty"
            else: status, values, error = _parse(pilot.response_content(raw), source["rows"][item["index"]])
            results.append(_finish(out, row_dir, call_path, item, status, receipts, error, values))
            if status in {"budget_stopped", "empty_response", "ambiguous"}:
                batch_status, stop_reason = status, error; break
    summary = {"schema": SCHEMA, "version": VERSION, "status": batch_status, "request_count": 3,
               "attempted_rows": len(results), "physical_receipts": sum(len(row.get("receipt_ids") or []) for row in results),
               "diagnostic_prefix": DIAGNOSTIC_PREFIX, "stop_reason": stop_reason, "rows": results}
    _write(out / SUMMARY_NAME, summary, private=False); return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0]); modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true"); modes.add_argument("--run", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT); parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try: result = prepare(args.out) if args.prepare else run(args.out, env_file=args.env_file)
    except (BudgetStop, ProbeStop) as exc:
        print(json.dumps({"status": "blocked", "error": _safe_error(exc)})); return 2
    if args.prepare:
        output = {"status": "prepared", "output": str(Path(args.out).resolve()), "attempted_rows": 0}
        print(json.dumps(output)); return 0
    output = {"status": result["status"], "output": str(Path(args.out).resolve()), "attempted_rows": result["attempted_rows"], "physical_receipts": result["physical_receipts"]}
    print(json.dumps(output)); return 0 if result["status"] == "complete" else 2


if __name__ == "__main__": raise SystemExit(main())


__all__ = ["CLAIM_NAME", "DEFAULT_OUT", "DIAGNOSTIC_PREFIX", "INPUT_PATH", "INPUT_SHA256", "MODEL", "PROVIDER", "PROVIDER_PROFILE", "REQUEST_PROFILE", "build_prompt", "exact_command", "load_spec", "main", "prepare", "run"]
