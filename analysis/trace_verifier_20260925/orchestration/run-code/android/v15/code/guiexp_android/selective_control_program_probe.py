"""Source-only baseline probe for complete Python action programs.

Three frozen v2 traces are sent once, one per family. Returned source is parsed
with :mod:`ast` for metadata only. This module never executes returned Python,
issues Android actions, reads held-out data, or repairs a response.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import shlex
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import selective_diagnostic_budget as diagnostic_budget
from . import selective_evidence_probe as evidence_probe
from . import selective_explore_budget as explore_budget
from . import selective_pilot as pilot
from .selective_ax_parser import parse_ax_tree
from .budget_client import BudgetStop

VERSION, SCHEMA, NAMESPACE = "control_program_probe_v1", "selective-control-program-probe/1", "selective_20260915"
DIAGNOSTIC_PREFIX = "selective_20260915/evidence_probe_v1"
MODEL, PROVIDER_PROFILE, PROVIDER = explore_budget.MODEL, "z_ai_fp8", "z-ai/fp8"
REQUEST_PROFILE, MAX_TOKENS, MAX_PHYSICAL_ATTEMPTS = "builder_8192", 8192, 1
ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = ROOT / "experimental-results/guiexp_android/selective_20260915/evidence_probe_v2/training_inputs.json"
INPUT_SHA256 = "c0ce74671dc6390f1113cecaad2f11ad9cf2d7faa69209ae8510012c17fae754"
SLOT_PATH = ROOT / "experimental-results/guiexp_android/selective_20260915/offline_controls/goal_subset_v1/results.json"
FROZEN_SLOT_PATH, SLOT_SHA256 = SLOT_PATH, "7c946a4497a1fa305939081397ba11c30ef4f74c963bf6489f3a36549be746f2"
PROGRAM_SHADOW_PATH = ROOT / "tmp/shadow-consensus-staging/program_shadow.py"
DIAGNOSTIC_CONFIG_PATH = ROOT / "experimental-results/guiexp_android/selective_20260915/evidence_probe_v1/diagnostic_budget.json"
SHARED_LEDGER_PATH, SHARED_RUN_LOCK_PATH, AUTHORIZATION_PATH = diagnostic_budget.SHARED_LEDGER_PATH, diagnostic_budget.SHARED_RUN_LOCK_PATH, diagnostic_budget.AUTHORIZATION_PATH
DEFAULT_OUT = ROOT / "experimental-results/guiexp_android/selective_20260915/control_program_probe_v1"
SPEC_NAME, SUMMARY_NAME, CLAIM_NAME = "spec.json", "batch_summary.json", "run_claim.json"
ProbeStop = pilot.PilotStop
_hash, _file_hash, _read = evidence_probe._hash_value, evidence_probe._hash_file, evidence_probe._read
_receipts, _billing = evidence_probe._ledger_receipts, evidence_probe._billing_known
_SAFE_NAMES = {"abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min", "range", "round", "sorted", "str", "sum", "tuple", "zip", "stem", "suffix", "date_add_days", "add_days"}
_METHODS = {"get", "items", "keys", "values", "split", "strip", "lstrip", "rstrip", "lower", "upper", "casefold", "startswith", "endswith", "replace", "find", "rfind", "count", "join", "append", "extend", "insert", "pop", "remove", "index", "sort", "reverse", "clear", "setdefault"}
_FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "print", "create", "invoke", "request", "requests", "chat", "completions", "model", "llm", "client"}
_ALLOWED_NODES = {getattr(ast, n) for n in "Module FunctionDef arguments arg Return Expr Assign AugAssign If For While Break Continue Pass Yield Constant Name Load Store BinOp UnaryOp BoolOp Compare IfExp Subscript Slice List Tuple Dict ListComp DictComp GeneratorExp comprehension Call keyword Attribute JoinedStr FormattedValue Add Sub Mult Div FloorDiv Mod UAdd USub Not And Or Eq NotEq Lt LtE Gt GtE In NotIn Is IsNot".split()}


def _write(path: Path, value: Any, *, private: bool = True) -> None: pilot.atomic_json(path, value, private=private)
def _safe_error(exc: BaseException) -> str: return evidence_probe._safe_error(exc)


def _profile() -> dict[str, Any]:
    try: serving = explore_budget._lock_set(None, PROVIDER_PROFILE, "serving_4096")[MODEL]
    except (AttributeError, BudgetStop) as exc: raise ProbeStop("serving profile lock is unavailable") from exc
    if (serving.get("provider"), serving.get("max_tokens"), serving.get("reasoning"), serving.get("max_physical_attempts")) != (PROVIDER, 4096, None, 1):
        raise ProbeStop("base serving profile is not the frozen z_ai_fp8/4096 one-attempt profile")
    lock = dict(serving, max_tokens=MAX_TOKENS, reasoning={"effort": "low"})
    lock["reservation_usd"] = str((Decimal(lock["context_length"]) * Decimal(lock["prompt_per_m"]) + Decimal(MAX_TOKENS) * Decimal(lock["completion_per_m"])) / Decimal(1000000))
    return {"name": REQUEST_PROFILE, "base_profile": "serving_4096", "model": MODEL, "provider_profile": PROVIDER_PROFILE, "provider": PROVIDER, "max_tokens": MAX_TOKENS, "reasoning": {"effort": "low"}, "temperature": 0.0, "max_physical_attempts": 1, "profile_basis": "existing z_ai_fp8 serving lock, with the existing low-effort builder policy applied at the explicit 8192 cap", "model_lock": lock}


def _source_manifest() -> dict[str, Any]:
    value = evidence_probe._source_manifest()
    value.update(program_shadow_path=str(PROGRAM_SHADOW_PATH.resolve()), program_shadow_sha256=_file_hash(PROGRAM_SHADOW_PATH))
    return value
def _verify_source_manifest(value: Mapping[str, Any]) -> None:
    if dict(value) != _source_manifest(): raise ProbeStop("probe source or runtime manifest changed after preparation")
def _forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping): return any(any(word in str(key).lower() for word in ("heldout", "oracle", "private", "evaluator", "answer_key")) or _forbidden_key(item) for key, item in value.items())
    return isinstance(value, list) and any(_forbidden_key(item) for item in value)


def _input(path: Path) -> dict[str, Any]:
    if path.resolve() == INPUT_PATH.resolve() and _file_hash(path) != INPUT_SHA256: raise ProbeStop("frozen control-program input hash changed")
    value = _read(path); rows = value.get("rows") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or value.get("schema") != "selective-evidence-training/1" or value.get("count") != 3 or _forbidden_key(value) or not isinstance(rows, list) or len(rows) != 3: raise ProbeStop("control-program input is not the frozen source-only three-row set")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("family"), str) or row["family"] in seen or not isinstance(row.get("goal_text"), str) or not row["goal_text"].strip() or not isinstance(row.get("steps"), list) or not row["steps"]: raise ProbeStop("source row goal or trace is invalid")
        try: evidence_probe._safe_key(row["family"])
        except ProbeStop as exc: raise ProbeStop("source row family is not a safe identifier") from exc
        if any(not isinstance(step, Mapping) or not isinstance(step.get("action"), Mapping) or not isinstance(step.get("pre_obs"), Mapping) or not isinstance(step.get("post_obs"), Mapping) for step in row["steps"]): raise ProbeStop("source trace must retain action, pre_obs, and post_obs")
        seen.add(row["family"])
    return dict(value)


def _slot_spans(row: Mapping[str, Any]) -> dict[str, Any]:
    if SLOT_PATH.resolve() == FROZEN_SLOT_PATH.resolve() and _file_hash(SLOT_PATH) != SLOT_SHA256: raise ProbeStop("automatic source-subset artifact hash changed")
    value = _read(SLOT_PATH); rows = value.get("rows") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or value.get("schema") != "automatic-source-subset-probe/1" or not isinstance(rows, list): raise ProbeStop("automatic source-subset artifact is invalid")
    found = next((item for item in rows if isinstance(item, Mapping) and item.get("family") == row.get("family")), None); template = found.get("template") if isinstance(found, Mapping) else None
    if not isinstance(template, Mapping) or template.get("source_goal") != row.get("goal_text"): return {"compatible": False, "reason": "source goal does not match automatic subset", "spans": []}
    spans = []
    for item in template.get("source_spans") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("slot"), str) or type(item.get("start")) is not int or type(item.get("end")) is not int: raise ProbeStop("automatic source-subset span is invalid")
        start, end = item["start"], item["end"]
        if not 0 <= start < end <= len(row["goal_text"]) or row["goal_text"][start:end] != item.get("value"): raise ProbeStop("automatic source-subset span does not match source goal")
        spans.append({"slot": item["slot"], "start": start, "end": end, "value": item["value"]})
    return {"compatible": True, "spans": spans}


def _project_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_ax_tree(observation.get("ax_tree_text", ""))
    metadata = {key: copy.deepcopy(value) for key, value in observation.items() if key not in {"url", "goal_text", "ax_tree_text"}}
    metadata["ax_parse"] = {key: parsed[key] for key in ("complete", "completeness", "issues", "markers_seen", "nodes_parsed", "truncated")}
    return {"observation": {"url": observation.get("url"), "elements": copy.deepcopy(parsed["nodes"])}, "metadata": metadata}
def _project_trace(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for step in row["steps"]:
        pre, post = step["pre_obs"], step["post_obs"]
        pre_projected, post_projected = _project_observation(pre), _project_observation(post)
        result.append({"step": step.get("step"), "action": copy.deepcopy(step["action"]), "pre_observation": pre_projected["observation"], "post_observation": post_projected["observation"], "pre_recorded_metadata": pre_projected["metadata"], "post_recorded_metadata": post_projected["metadata"]})
    return result


def build_prompt(row: Mapping[str, Any]) -> list[dict[str, str]]:
    if not isinstance(row.get("goal_text"), str) or not isinstance(row.get("steps"), list): raise ProbeStop("source row cannot build a prompt")
    instruction = "Return exactly one JSON object with exactly keys source, bindings, and unsupported_cases. source must be complete reusable Python defining exactly def program(bindings), a generator. It may use if/elif/else, bounded for/while loops, and comprehensions, and must yield only {\"op\":\"observe\"} or {\"op\":\"action\",\"action\": ACTION}. The actual observation supplied by the harness is a JSON object shaped as {url, elements}, where elements is the saved list of observed node attribute objects. Recorded metadata is supplied separately for context. Do not call json.loads. Supported native actions are click/long_press (action_type,index), input_text (action_type,index,text), keyboard_enter, navigate_home, navigate_back, wait, open_app (action_type,app_name), and scroll (action_type,direction). Supported pure builtins are abs, all, any, bool, dict, enumerate, float, int, len, list, max, min, range, round, sorted, str, sum, tuple, and zip. Supported pure helpers are stem, suffix, date_add_days, and add_days. Supported methods are get, items, keys, values, split, strip, lstrip, rstrip, lower, upper, casefold, startswith, endswith, replace, find, rfind, count, join, append, extend, insert, pop, remove, index, sort, reverse, clear, and setdefault. Do not import, perform I/O, use reflection, call a model/API, or put model calls inside source. The restricted subset also forbids leading-underscore identifiers, annotations, decorators, default or variadic arguments, try/raise/assert/lambda, and unsupported builtins. Stop by returning a JSON-compatible outcome. Check the goal from current observations when evidence supports it, bound all actions and loops, and avoid accidental retries by checking current state before repeating an action. If a postcondition cannot be observed, state that case in unsupported_cases and do not claim verification. Screenshot filenames in recorded metadata are provenance only, not vision inputs. The AST check is an experiment guard and is not a security sandbox. bindings must contain grammar (a string describing the accepted mapping) and slots (a mapping from declared slot name to {source_span:[start,end],source_value:string,description:string}). Automatic spans are optional hints. You may declare any exact source-goal occurrence, including a span omitted by the hints. A repeated text requires its explicit source occurrence indices. unsupported_cases must be an explicit array of nonempty strings. Use only the source goal, full recorded source observations/actions, and source spans below."
    payload = {"source_goal": row["goal_text"], "source_trace": _project_trace(row), "automatic_source_slot_hints": _slot_spans(row)}
    return [{"role": "system", "content": "You produce one source-only complete-program baseline as strict JSON."}, {"role": "user", "content": instruction + "\n\nSOURCE INPUT:\n" + json.dumps(payload, ensure_ascii=True, indent=2)}]


def _config(path: Path, ledger: Path, auth: Path) -> dict[str, Any]:
    try: value = diagnostic_budget.load_diagnostic_config(path, expected_prefix=DIAGNOSTIC_PREFIX, ledger_path=ledger, authorization_path=auth)
    except BudgetStop as exc: raise ProbeStop(f"diagnostic config cannot be verified: {_safe_error(exc)}") from exc
    if value.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH): raise ProbeStop("diagnostic config does not use the shared run lock")
    return value
def exact_command(out: Path | str = DEFAULT_OUT) -> str: return "../.venv-android/bin/python -m guiexp_android.selective_control_program_probe --run --out " + shlex.quote(str(Path(out).resolve()))


def _claim_run(out: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    value = {"schema": SCHEMA, "status": "claimed", "version": VERSION, "spec_sha256": spec.get("spec_sha256"), "pid": os.getpid(), "claimed_unix": time.time()}
    try:
        fd = os.open(out / CLAIM_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n"); handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as exc: raise BudgetStop("control-program run claim already exists; rerun is refused") from exc
    except OSError as exc: raise BudgetStop("control-program run claim could not be created") from exc
    return value


def prepare(out: Path | str = DEFAULT_OUT, *, input_path: Path | str | None = None, diagnostic_config_path: Path | str | None = None, ledger_path: Path | str | None = None, authorization_path: Path | str | None = None) -> dict[str, Any]:
    out, source_path = Path(out).resolve(), Path(input_path or INPUT_PATH).resolve(); config_path = Path(diagnostic_config_path or DIAGNOSTIC_CONFIG_PATH).resolve(); ledger, auth = Path(ledger_path or SHARED_LEDGER_PATH).resolve(), Path(authorization_path or AUTHORIZATION_PATH).resolve()
    source = _input(source_path); _config(config_path, ledger, auth); profile, manifest = _profile(), _source_manifest()
    rows = [{"index": i, "family": str(row["family"]), "episode": f"{DIAGNOSTIC_PREFIX}/{VERSION}/{row['family']}", "source_row_sha256": _hash(row), "prompt_sha256": _hash(build_prompt(row))} for i, row in enumerate(source["rows"])]
    body: dict[str, Any] = {"schema": SCHEMA, "version": VERSION, "namespace": NAMESPACE, "input_path": str(source_path), "input_sha256": _file_hash(source_path), "source_subset_path": str(SLOT_PATH.resolve()), "source_subset_sha256": _file_hash(SLOT_PATH), "diagnostic_config_path": str(config_path), "diagnostic_config_sha256": _file_hash(config_path), "diagnostic_prefix": DIAGNOSTIC_PREFIX, "shared_ledger": str(ledger), "shared_run_lock": str(SHARED_RUN_LOCK_PATH), "authorization_path": str(auth), "model": MODEL, "provider_profile": PROVIDER_PROFILE, "provider": PROVIDER, "profile": profile, "profile_sha256": _hash(profile), "source_manifest": manifest, "source_manifest_sha256": _hash(manifest), "rows": rows, "request_count": 3, "max_physical_attempts": 1, "budgets": {"global_usd": "20", "tranche_usd": "10", "diagnostic_usd": "1"}, "execution": {"ui_actions": False, "heldout_data": False, "repair_calls": False, "generated_code_executed": False, "security_guard_is_not_sandbox": True, "automatic_followups": False, "sdk_in_prepare": False}, "prompt_policy": "source goal plus full recorded source observations/actions and optional automatic source spans only", "exact_command": exact_command(out)}
    body["spec_sha256"] = _hash(body); out.mkdir(parents=True, exist_ok=True); path = out / SPEC_NAME
    if path.exists():
        current = load_spec(out)
        if current != body: raise ProbeStop("existing control-program spec differs from the frozen request")
        return current
    if any(item.name != SPEC_NAME for item in out.iterdir()): raise ProbeStop("control-program output contains a partial draft")
    _write(path, body, private=False); return body


def load_spec(out: Path | str = DEFAULT_OUT) -> dict[str, Any]:
    out = Path(out).resolve(); value = _read(out / SPEC_NAME)
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA or value.get("spec_sha256") != _hash({k: v for k, v in value.items() if k != "spec_sha256"}): raise ProbeStop("control-program spec schema or hash is invalid")
    if value.get("request_count") != 3 or value.get("diagnostic_prefix") != DIAGNOSTIC_PREFIX: raise ProbeStop("control-program request bound or diagnostic prefix changed")
    if value.get("input_sha256") != _file_hash(Path(value["input_path"])) or value.get("source_subset_sha256") != _file_hash(Path(value["source_subset_path"])): raise ProbeStop("frozen source input or slot artifact drifted after preparation")
    if value.get("source_manifest_sha256") != _hash(value.get("source_manifest")): raise ProbeStop("control-program source or runtime manifest changed")
    _verify_source_manifest(value.get("source_manifest") or {})
    if value.get("profile_sha256") != _hash(value.get("profile")) or value.get("profile") != _profile(): raise ProbeStop("control-program serving profile changed")
    config_path = Path(value["diagnostic_config_path"])
    if value.get("diagnostic_config_sha256") != _file_hash(config_path): raise ProbeStop("control-program diagnostic config changed")
    _config(config_path, Path(value["shared_ledger"]), Path(value["authorization_path"]))
    if len(value.get("rows") or []) != 3: raise ProbeStop("control-program spec does not contain exactly three rows")
    return dict(value)


def _ledger_raw(ledger: Any, episode: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    try:
        with ledger.connect() as db: row = db.execute("SELECT response_json FROM calls WHERE episode=? ORDER BY created DESC,id DESC LIMIT 1", (episode,)).fetchone()
        raw = json.loads(row[0]) if row and row[0] else {}; raw = dict(raw) if isinstance(raw, Mapping) else {}
        return raw, pilot.usage_from_response(raw), _receipts(ledger, episode)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError): return {}, {}, _receipts(ledger, episode)
def _prior_output(out: Path, spec: Mapping[str, Any], ledger: Any) -> None:
    if any((out / name).exists() for name in (SUMMARY_NAME, CLAIM_NAME, "run_identity.json")) or any(p.name == "state.json" or p.name.startswith("call_") for p in out.rglob("*.json")): raise BudgetStop("control-program output already has run state; rerun is refused")
    if any(_receipts(ledger, str(row["episode"])) for row in spec["rows"]): raise BudgetStop("control-program episode already has a receipt; rerun is refused")
def _verify_request(spec: Mapping[str, Any], item: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, str]]:
    if _file_hash(Path(spec["input_path"])) != spec["input_sha256"] or _file_hash(Path(spec["source_subset_path"])) != spec["source_subset_sha256"]: raise ProbeStop("source input or automatic span artifact drifted before request")
    row = source["rows"][item["index"]]
    if _hash(row) != item["source_row_sha256"] or row.get("family") != item.get("family"): raise ProbeStop("source row drifted before request")
    if _file_hash(Path(spec["diagnostic_config_path"])) != spec["diagnostic_config_sha256"]: raise ProbeStop("diagnostic config drifted before request")
    _verify_source_manifest(spec["source_manifest"]); _config(Path(spec["diagnostic_config_path"]), Path(spec["shared_ledger"]), Path(spec["authorization_path"]))
    if spec.get("profile_sha256") != _hash(spec.get("profile")) or spec.get("profile") != _profile(): raise ProbeStop("serving profile drifted before request")
    messages = build_prompt(row)
    if _hash(messages) != item["prompt_sha256"]: raise ProbeStop("control-program prompt changed before request")
    return messages


class _SourceRejected(ValueError): pass
def _source_metadata(source: Any) -> dict[str, Any]:
    if not isinstance(source, str): raise _SourceRejected("source must be a string")
    try: tree = ast.parse(source, mode="exec")
    except SyntaxError as exc: raise _SourceRejected(f"syntax error: {exc.msg}") from exc
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES: raise _SourceRejected(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id.startswith("_"): raise _SourceRejected("private names are forbidden")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr not in _METHODS): raise _SourceRejected(f"attribute is not allowlisted: {node.attr!r}")
        if isinstance(node, ast.Call):
            target = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if target is None or target in _FORBIDDEN_CALLS or isinstance(node.func, ast.Name) and target not in _SAFE_NAMES | functions or isinstance(node.func, ast.Attribute) and target not in _METHODS: raise _SourceRejected(f"call is not allowlisted: {target!r}")
            if any(keyword.arg is None for keyword in node.keywords): raise _SourceRejected("dictionary expansion in calls is forbidden")
        if isinstance(node, ast.Constant) and (not (node.value is None or type(node.value) in (bool, int, float, str)) or isinstance(node.value, str) and len(node.value) > 10000): raise _SourceRejected("constant is outside the JSON scalar bound")
        if isinstance(node, ast.FunctionDef) and (node.name.startswith("_") or node.decorator_list or node.returns or node.args.vararg or node.args.kwarg or node.args.kwonlyargs or node.args.posonlyargs or node.args.defaults or node.args.kw_defaults): raise _SourceRejected("private functions, decorators, annotations, and complex arguments are forbidden")
        if isinstance(node, ast.arg) and (node.annotation is not None or node.arg.startswith("_")): raise _SourceRejected("argument annotations and private names are forbidden")
    if any(not isinstance(item, ast.FunctionDef) and not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)) for item in tree.body): raise _SourceRejected("module may contain only functions and a docstring")
    programs = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "program"]
    if len(programs) != 1 or len(programs[0].args.args) != 1 or programs[0].args.args[0].arg != "bindings": raise _SourceRejected("source must define def program(bindings) exactly")
    yields = [node for node in ast.walk(programs[0]) if isinstance(node, ast.Yield)]
    if not yields: raise _SourceRejected("program must yield observe or action requests")
    return {"syntax_valid": True, "generator": True, "yield_count": len(yields), "uses_conditionals": any(isinstance(n, ast.If) for n in ast.walk(programs[0])), "uses_loops": any(isinstance(n, (ast.For, ast.While)) for n in ast.walk(programs[0])), "executed": False}


def _parse(raw_text: str, row: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None, str | None]:
    try: value = json.loads(raw_text.strip())
    except (TypeError, ValueError) as exc: return "malformed_json", None, _safe_error(exc)
    if not isinstance(value, Mapping) or set(value) != {"source", "bindings", "unsupported_cases"}: return "invalid_schema", None, "response must contain source, bindings, and unsupported_cases only"
    bindings = value["bindings"]
    if not isinstance(bindings, Mapping) or set(bindings) != {"grammar", "slots"} or not isinstance(bindings["grammar"], str) or not bindings["grammar"].strip() or not isinstance(bindings["slots"], Mapping): return "invalid_bindings", None, "bindings must contain a grammar string and slot mapping"
    if not isinstance(value["unsupported_cases"], list) or any(type(item) is not str or not item.strip() for item in value["unsupported_cases"]): return "invalid_unsupported_cases", None, "unsupported_cases must be an explicit array of nonempty strings"
    goal = row["goal_text"]
    for name, declaration in bindings["slots"].items():
        if not isinstance(name, str) or not isinstance(declaration, Mapping) or set(declaration) != {"source_span", "source_value", "description"} or not isinstance(declaration["source_span"], list) or len(declaration["source_span"]) != 2 or any(type(x) is not int for x in declaration["source_span"]): return "invalid_bindings", None, "slot declarations have the wrong shape"
        start, end = declaration["source_span"]
        if not 0 <= start < end <= len(goal) or declaration["source_value"] != goal[start:end] or not isinstance(declaration["description"], str) or not declaration["description"].strip(): return "invalid_bindings", None, "slot declarations must identify exact source-goal occurrences"
    try: metadata = _source_metadata(value["source"])
    except _SourceRejected as exc: return "invalid_source", None, _safe_error(exc)
    return "returned", {"source": value["source"], "bindings": bindings, "unsupported_cases": value["unsupported_cases"], "source_metadata": metadata}, None


def _make_client(spec: Mapping[str, Any], row: Mapping[str, Any], ledger: Any, *, env_file: Path | str | None, sdk: Any, metadata_fetcher: Any, host_guard: Any) -> Any:
    lock = spec["profile"]["model_lock"]
    if sdk is None: client = explore_budget.real_client(ledger, {MODEL: lock}, Path(env_file or explore_budget.DEFAULT_ENV_PATH))
    else: client = explore_budget.SelectiveExploreClient(ledger, {MODEL: lock}, sdk, metadata_fetcher=metadata_fetcher or explore_budget.fetch_metadata)
    client.sdk = explore_budget.base._GuardedSDK(client.sdk, host_guard); client.host_guard = host_guard; client.call_prefix = explore_budget.CALL_PREFIX; client.begin_episode(row["episode"]); return client
def _finish(out: Path, row_dir: Path, call_path: Path, item: Mapping[str, Any], status: str, receipts: Sequence[str], error: str | None = None, candidate: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"family": item["family"], "status": status, "call": str(call_path.relative_to(out)), "receipt_ids": list(receipts)}
    if candidate is not None: result["candidate"] = dict(candidate)
    if error: result["error"] = error
    _write(row_dir / "state.json", {"schema": SCHEMA, **result}); return result
def _save_call(path: Path, item: Mapping[str, Any], raw: Mapping[str, Any], usage: Mapping[str, Any], receipts: Sequence[str], error: str | None = None) -> None:
    value = {"schema": SCHEMA, "family": item["family"], "episode": item["episode"], "raw_response": dict(raw), "usage": dict(usage), "receipt_ids": list(receipts), "response_sha256": _hash(raw), "saved_before_parse": True}
    if error: value["error"] = error
    _write(path, value)
def _provider_validate(spec: Mapping[str, Any], metadata_fetcher: Any) -> None: explore_budget.validated_explore_metadata(MODEL, spec["profile"]["model_lock"], metadata_fetcher or explore_budget.fetch_metadata)


def run(out: Path | str = DEFAULT_OUT, *, client_factory: Callable[..., Any] | None = None, env_file: Path | str | None = None, sdk: Any = None, metadata_fetcher: Any = None, host_guard: Any = None, ledger: Any = None) -> dict[str, Any]:
    out, spec = Path(out).resolve(), load_spec(out); source = _input(Path(spec["input_path"])); guard = host_guard if host_guard is not None else explore_budget.read_host_state
    if ledger is None: ledger = diagnostic_budget.make_diagnostic_ledger(spec["diagnostic_config_path"], host_guard=guard)
    _prior_output(out, spec, ledger); _claim_run(out, spec)
    if client_factory is None or metadata_fetcher is not None: _provider_validate(spec, metadata_fetcher)
    results: list[dict[str, Any]] = []; batch_status, stop_reason = "complete", None
    with explore_budget.exclusive_run(identity_path=out / "run_identity.json", mode=VERSION):
        for item in spec["rows"]:
            row_dir = out / "rows" / str(item["family"]); row_dir.mkdir(parents=True, exist_ok=True); messages = _verify_request(spec, item, _input(Path(spec["input_path"])))
            _write(row_dir / "state.json", {"schema": SCHEMA, "status": "running", "family": item["family"], "episode": item["episode"]}); call_path, raw, usage, receipts = row_dir / "call_01.json", {}, {}, []
            try:
                client = client_factory(spec, item, ledger) if client_factory is not None else _make_client(spec, item, ledger, env_file=env_file, sdk=sdk, metadata_fetcher=metadata_fetcher, host_guard=guard)
                pilot._begin_episode(client, item["episode"]); response, usage = pilot._call_client(client, messages, item["episode"]); raw, receipts = pilot._response_dump(response), _receipts(ledger, item["episode"])
            except Exception as exc:  # noqa: BLE001
                raw, usage, receipts = _ledger_raw(ledger, item["episode"]); error = _safe_error(exc); _save_call(call_path, item, raw, usage, receipts, error); status = "budget_stopped" if isinstance(exc, BudgetStop) else "ambiguous"; results.append(_finish(out, row_dir, call_path, item, status, receipts, error)); batch_status, stop_reason = status, error; break
            _save_call(call_path, item, raw, usage, receipts); status, candidate, error = "returned", None, None
            if not _billing(usage): status, error = "budget_stopped", "response lacks settled billing evidence"
            elif not pilot.response_content(raw).strip(): status, error = "empty_response", "model response was empty"
            else: status, candidate, error = _parse(pilot.response_content(raw), source["rows"][item["index"]])
            results.append(_finish(out, row_dir, call_path, item, status, receipts, error, candidate))
            if status in {"budget_stopped", "empty_response", "ambiguous"}: batch_status, stop_reason = status, error; break
    summary = {"schema": SCHEMA, "version": VERSION, "status": batch_status, "request_count": 3, "attempted_rows": len(results), "physical_receipts": sum(len(row.get("receipt_ids") or []) for row in results), "diagnostic_prefix": DIAGNOSTIC_PREFIX, "stop_reason": stop_reason, "generated_code_executed": False, "security_guard_is_not_sandbox": True, "automatic_followups": False, "task_success_evaluated": False, "rows": results}; _write(out / SUMMARY_NAME, summary, private=False); return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0]); modes = parser.add_mutually_exclusive_group(required=True); modes.add_argument("--prepare", action="store_true"); modes.add_argument("--run", action="store_true"); parser.add_argument("--out", type=Path, default=DEFAULT_OUT); parser.add_argument("--env-file", type=Path); args = parser.parse_args(argv)
    try: result = prepare(args.out) if args.prepare else run(args.out, env_file=args.env_file)
    except (BudgetStop, ProbeStop) as exc: print(json.dumps({"status": "blocked", "error": _safe_error(exc)})); return 2
    output = {"status": "prepared" if args.prepare else result["status"], "output": str(Path(args.out).resolve()), "attempted_rows": 0 if args.prepare else result["attempted_rows"]}
    if not args.prepare: output["physical_receipts"] = result["physical_receipts"]
    print(json.dumps(output)); return 0 if args.prepare or result["status"] == "complete" else 2


if __name__ == "__main__": raise SystemExit(main())
__all__ = ["CLAIM_NAME", "DEFAULT_OUT", "DIAGNOSTIC_PREFIX", "INPUT_PATH", "INPUT_SHA256", "MODEL", "PROVIDER", "PROVIDER_PROFILE", "REQUEST_PROFILE", "build_prompt", "exact_command", "load_spec", "main", "prepare", "run"]
