"""Three-building-task verifier introduced by the 2026-09-25 revision.

This module is deliberately separate from :mod:`verify_runner`.  Historical
five-binding results therefore remain reproducible.  The revised verifier
starts from the saved k=3 artifact, extracts one binding for each original
building prompt, caches those bindings while the interface is unchanged,
and admits only a candidate that passes all three original task oracles.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from . import android_env
from .accounting_check import call_record
from .agent import AndroidAgent
from .compiler import FAMILY_BINDINGS, binding_fields
from .deploy_runner import (
    binding_type_errors,
    build_extraction_prompt,
    build_retry_prompt,
    normalize_binding,
    parse_binding_json,
)
from .explore import BUILDING_SEEDS

PROTOCOL_ID = "three-building-model-extracted-v1"
PLATFORM = "Android"
REPLAY_TIMEOUT_S = 1200
OUTER_REPLAY_GRACE_S = 60


def deployment_requirement(attempt_role: str, admitted: bool) -> str:
    if attempt_role == "repeat":
        return "not_required_repeat"
    if attempt_role != "initial":
        raise ValueError(f"unknown attempt role: {attempt_role}")
    return "required" if admitted else "skipped_rejected"


def output_entry_decision(result_path: Path | str, resume: bool) -> str:
    """Return start/resume/skip or reject unsafe canonical-output reuse."""
    path = Path(result_path)
    if not path.exists():
        return "start"
    record = json.loads(path.read_text())
    terminal = record.get("terminal_status") or record.get("status")
    if terminal == "complete":
        return "skip_complete"
    if terminal == "running":
        if resume:
            return "resume"
        raise FileExistsError("partial canonical result exists; pass --resume after validation")
    raise FileExistsError(f"canonical result has terminal status {terminal!r}; preserve it")


def replay_watchdog_state(started_epoch: float, timeout_s: float = REPLAY_TIMEOUT_S) -> dict:
    """Deadlines for a child replay and its outer process supervisor.

    The semantic cap remains ``timeout_s``.  The outer process gets a fixed
    grace interval to kill/reap the child and checkpoint the failed replay.
    """
    return {
        "phase": "replay",
        "started_epoch": started_epoch,
        "semantic_deadline_epoch": started_epoch + timeout_s,
        "deadline_epoch": started_epoch + timeout_s + OUTER_REPLAY_GRACE_S,
        "cleanup_grace_s": OUTER_REPLAY_GRACE_S,
    }


def outer_watchdog_expired(state: dict, now_epoch: float) -> bool:
    return state.get("phase") == "replay" and now_epoch > state["deadline_epoch"]


def response_ledger_entry(response, model: str, sequence: int) -> dict:
    """Record a paid response before artifact parsing can reject it."""
    usage = AndroidAgent._usage(response)
    choices = list(getattr(response, "choices", None) or [])
    message = getattr(choices[0], "message", None) if choices else None
    content = getattr(message, "content", None) if message is not None else None
    reasoning = getattr(message, "reasoning", None) if message is not None else None
    if reasoning is None and message is not None:
        reasoning = getattr(message, "reasoning_content", None)
    return {
        "sequence": sequence,
        "completed": True,
        "response_id": getattr(response, "id", None),
        "model": model,
        "finish_reason": getattr(choices[0], "finish_reason", None) if choices else None,
        "empty_response": not bool((content or "").strip()),
        "raw_output_length": len(content or ""),
        "reasoning_length": len(reasoning or ""),
        "usage": usage,
        "cost_usd": usage.get("cost_usd"),
    }


def response_ledger_totals(entries: list[dict]) -> dict:
    totals = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
              "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    for entry in entries:
        if not entry.get("completed"):
            continue
        usage = entry.get("usage") or {}
        totals["calls"] += 1
        for key in ("prompt_tokens", "cached_tokens", "completion_tokens"):
            totals[key] += usage.get(key) or 0
        totals["cost_usd"] += usage.get("cost_usd") or 0.0
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = round(totals["cost_usd"], 8)
    return totals


def sha256_path(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def resolve_recorded_trajectory(raw_path: Path | str, source_cell: Path | str,
                                seed: int, remapped_path: Path | str) -> tuple[Path, dict | None]:
    """Resolve a recorded path through bounded historical-layout aliases."""
    raw = Path(raw_path)
    source_cell = Path(source_cell).resolve()
    remapped = Path(remapped_path).resolve()
    live_repo = next((parent.parent for parent in source_cell.parents
                      if parent.name == "experimental-results"), None)
    candidates: list[tuple[str, Path]] = [("lane_remap", remapped)]
    text = str(raw_path)
    marker = "/experimental-results/"
    if live_repo is not None and marker in text:
        candidates.append(("live_repo_absolute_suffix",
                           live_repo / "experimental-results" / text.split(marker, 1)[1]))
    if live_repo is not None and not raw.is_absolute():
        candidates.append(("live_repo_code_relative", (live_repo / "code" / raw).resolve()))
    candidates.append(("source_cell_relative_alias",
                       source_cell / "explore" / f"s{seed}_a0" / "trajectory.jsonl"))
    seen = set()
    for kind, candidate in candidates:
        candidate = candidate.resolve()
        if str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if candidate.exists():
            if candidate == remapped and str(candidate) == str(raw):
                return candidate, None
            return candidate, {"kind": kind, "recorded_path": text,
                               "resolved_path": str(candidate)}
    return remapped, None


def original_building_tasks(
    family: str,
    trajectory_paths: list[Path | str],
    seeds=BUILDING_SEEDS,
    source_identity: dict | None = None,
) -> list[dict]:
    """Recover original task identities and reconstruct their missing goals.

    Android trajectory finals save family, condition, seed and task_id but do
    not save the goal.  We require those fields to match, then regenerate the
    prompt with the unchanged android_world task generator.
    """
    if len(trajectory_paths) != len(seeds):
        raise ValueError("need exactly one source trajectory per building seed")
    tasks = []
    generator_path = Path(android_env.__file__).resolve()
    generator_sha = sha256_path(generator_path)
    for seed, raw_path in zip(seeds, trajectory_paths):
        path = Path(raw_path)
        if not path.exists():
            if not source_identity:
                raise FileNotFoundError(path)
            if source_identity.get("family") != family or list(source_identity.get("seeds", [])) != list(seeds):
                raise ValueError("source record does not match requested family/building seeds")
            params = android_env.instance_params(family, seed)
            task = android_env.get_task(family, "discover", seed)
            tasks.append({
                "seed": seed,
                "prompt": android_env.goal_text(task),
                "prompt_provenance": {
                    "kind": "reconstructed_from_recorded_seed_missing_trajectory",
                    "recorded_trajectory_path": str(path),
                    "source_record_path": source_identity["path"],
                    "source_record_sha256": source_identity["sha256"],
                    "generator_family": family,
                    "generator_condition": "discover",
                    "generator_seed": seed,
                    "generator_code_path": str(generator_path),
                    "generator_code_sha256": generator_sha,
                    "source_identity_match": True,
                    "recorded_prompt_match": "not_checkable_missing_trajectory",
                    "recorded_params_match": "not_checkable_missing_trajectory",
                },
                "truth_params": params,
            })
            continue
        lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        final = next((row for row in reversed(lines) if row.get("record_type") == "final"), None)
        expected_id = f"{family}__discover__s{seed}"
        if not final or final.get("family") != family or final.get("seed") != seed:
            raise ValueError(f"trajectory identity mismatch: {path}")
        if final.get("task_id") != expected_id:
            raise ValueError(f"trajectory task_id mismatch: {path}")
        params = android_env.instance_params(family, seed)
        task = android_env.get_task(family, "discover", seed)
        tasks.append({
            "seed": seed,
            "prompt": android_env.goal_text(task),
            "prompt_provenance": {
                "kind": "reconstructed_from_recorded_seed",
                "trajectory_path": str(path),
                "trajectory_sha256": sha256_path(path),
                "recorded_task_id": final["task_id"],
                "generator_family": family,
                "generator_condition": "discover",
                "generator_seed": seed,
                "generator_code_path": str(generator_path),
                "generator_code_sha256": generator_sha,
                "source_identity_match": True,
                "recorded_prompt_match": "not_byte_checkable_prompt_not_recorded",
                "recorded_params_match": "not_byte_checkable_params_not_recorded",
            },
            "truth_params": params,
        })
        resolution = (source_identity or {}).get("trajectory_resolution", {}).get(str(seed))
        if resolution:
            tasks[-1]["prompt_provenance"]["trajectory_resolution"] = resolution
    return tasks


def extract_binding(client, model: str, family: str, task: dict) -> dict:
    """Extract once with the deployment prompt and its one bounded retry."""
    started = time.monotonic()
    calls = []
    outputs = []
    errors: list[str] = []
    try:
        messages = build_extraction_prompt(task["prompt"], family)
        for attempt in range(2):
            call_started = time.monotonic()
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=0.0
            )
            usage = AndroidAgent._usage(response)
            raw = response.choices[0].message.content or ""
            choice = response.choices[0]
            message = choice.message
            reasoning = getattr(message, "reasoning", None)
            if reasoning is None:
                reasoning = getattr(message, "reasoning_content", None)
            got = parse_binding_json(raw)
            errors = binding_type_errors(got, family)
            outputs.append(raw)
            calls.append({
                **call_record(attempt + 1, attempt + 1, usage,
                              stage="verification_extract",
                              kind="extract" if attempt == 0 else "retry"),
                "wall_s": round(time.monotonic() - call_started, 3),
                "response_id": getattr(response, "id", None),
                "finish_reason": getattr(choice, "finish_reason", None),
                "raw_output_length": len(raw),
                "reasoning_length": len(reasoning or ""),
            })
            if not raw.strip():
                finish_reason = getattr(choice, "finish_reason", None)
                status = ("model_output_generation_failure" if finish_reason == "length"
                          else "provider_output_undetermined")
                return {
                    "status": status,
                    "input": {"goal": task["prompt"], "family": family,
                              "fields": list(binding_fields(family))},
                    "outputs": outputs,
                    "binding": None,
                    "type_check": {"passed": False, "errors": []},
                    "retry_used": False,
                    "calls": calls,
                    "response_metadata": {
                        "response_id": getattr(response, "id", None),
                        "finish_reason": finish_reason,
                        "raw_output_length": 0,
                        "reasoning_length": len(reasoning or ""),
                    },
                    "wall_s": round(time.monotonic() - started, 3),
                }
            if not errors:
                binding = normalize_binding(got, family)
                return {
                    "status": "ok",
                    "input": {"goal": task["prompt"], "family": family,
                              "fields": list(binding_fields(family))},
                    "outputs": outputs,
                    "binding": binding,
                    "type_check": {"passed": True, "errors": []},
                    "retry_used": attempt == 1,
                    "calls": calls,
                    "wall_s": round(time.monotonic() - started, 3),
                }
            if attempt == 0:
                messages = build_retry_prompt(task["prompt"], family, errors)
        return {
            "status": "extraction_json" if got is None else "type_check",
            "input": {"goal": task["prompt"], "family": family,
                      "fields": list(binding_fields(family))},
            "outputs": outputs,
            "binding": None,
            "type_check": {"passed": False, "errors": errors},
            "retry_used": True,
            "calls": calls,
            "wall_s": round(time.monotonic() - started, 3),
        }
    except Exception as exc:  # provider errors are terminal and distinct
        status_code = getattr(exc, "status_code", None)
        status = "insufficient_credit" if status_code == 402 else "provider_error"
        return {
            "status": status,
            "input": {"goal": task["prompt"], "family": family,
                      "fields": list(binding_fields(family))},
            "outputs": outputs,
            "binding": None,
            "type_check": {"passed": False, "errors": []},
            "retry_used": bool(outputs),
            "calls": calls,
            "error": f"{type(exc).__name__}: {exc}",
            "provider_status_code": status_code,
            "wall_s": round(time.monotonic() - started, 3),
        }


def usage_total(extractions: list[dict]) -> dict:
    total = {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
             "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    for extraction in extractions:
        for call in extraction.get("calls", []):
            usage = call.get("usage") or call
            total["calls"] += 1
            for key in ("prompt_tokens", "cached_tokens", "completion_tokens"):
                total[key] += usage.get(key) or 0
            total["cost_usd"] += usage.get("cost_usd") or 0.0
    total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
    total["cost_usd"] = round(total["cost_usd"], 8)
    return total


def evaluate_candidate(
    source: str,
    tasks: list[dict],
    extractions: list[dict],
    replay: Callable[[str, dict, dict, float], dict],
    timeout_s: float = REPLAY_TIMEOUT_S,
) -> dict:
    """Check 3/3 in order and mark rows after the first failure untested."""
    detail = []
    for task, extraction in zip(tasks, extractions):
        if extraction.get("status") != "ok":
            detail.append({"seed": task["seed"], "status": "untested",
                           "reason": "extraction_failed", "wall_s": 0.0})
            continue
        started = time.monotonic()
        outcome = replay(source, extraction["binding"], task["truth_params"], timeout_s)
        wall_s = round(time.monotonic() - started, 3)
        status = "pass" if outcome.get("passed") else "fail"
        detail.append({"seed": task["seed"], "status": status,
                       "error": outcome.get("error"), "error_type": outcome.get("error_type"),
                       "trace": outcome.get("trace", []), "screen": outcome.get("screen", ""),
                       "wall_s": outcome.get("wall_s", wall_s)})
        if status == "fail":
            for rest in tasks[len(detail):]:
                detail.append({"seed": rest["seed"], "status": "untested",
                               "reason": "stopped_after_first_failure", "wall_s": 0.0})
            break
    passed = sum(row["status"] == "pass" for row in detail)
    return {"passed": passed, "total": 3, "admitted": passed == 3,
            "tasks": detail}


def base_result(
    *, model: str, family: str, attempt_id: str, attempt_role: str,
    price_sheet_id: str, source_path: Path | str, code_bundle_id: str | None = None,
    source_record_paths: list[Path | str], tasks: list[dict],
    reused_charges: dict | None = None,
) -> dict:
    source_path = Path(source_path)
    return {
        "protocol_id": PROTOCOL_ID,
        "platform": PLATFORM,
        "model": model,
        "family": family,
        "attempt_id": attempt_id,
        "attempt_role": attempt_role,
        "price_sheet_id": price_sheet_id,
        "code_bundle_id": code_bundle_id,
        "status": "running",
        "terminal_status": "running",
        "source": {
            "initial_program": {"path": str(source_path), "sha256": sha256_path(source_path)},
            "records": [{"path": str(Path(p)), "sha256": sha256_path(p)}
                        for p in source_record_paths],
        },
        "tasks": tasks,
        "extractions": [],
        "initial_program_sha256": sha256_path(source_path),
        "final_program_sha256": None,
        "candidate_history": [],
        "admitted": False,
        "repairs": [],
        "usage": {
            "reused_translation_builder": reused_charges or {},
            "new_extraction": {},
            "new_repair": {},
            "reconciliation": {"reconciled": False, "reason": "attempt_in_progress"},
        },
        "deployment": {"status": "pending", "reuse_justification": None,
                       "historical_program_sha256": None,
                       "final_program_sha256": None,
                       "program_hash_equal": None,
                       "execution_protocol_equal": None,
                       "input_protocol_equal": None, "record_paths": []},
        "timing": {},
    }


def interface_signature(family: str) -> str:
    payload = {"fields": list(binding_fields(family)),
               "int_fields": list(FAMILY_BINDINGS[family]["int_fields"])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run_attempt(
    *, model: str, family: str, attempt_id: str, attempt_role: str,
    price_sheet_id: str, source_path: Path | str, code_bundle_id: str | None = None,
    source_record_paths: list[Path | str], trajectory_paths: list[Path | str],
    client, replay: Callable[[str, dict, dict, float], dict],
    repair: Callable[[str, dict, list[str]], dict] | None = None,
    reused_charges: dict | None = None, m_max: int = 3,
    checkpoint: Callable[[dict], None] | None = None,
    source_identity: dict | None = None,
    resume_result: dict | None = None,
) -> dict:
    """Execute one complete revised attempt with cached extractions.

    ``replay`` must enforce its timeout outside generated code.  The shipped
    command runner does this by supervising the worker process.  ``repair``
    receives the failed task with its extracted binding and may return a new
    source plus charged usage.  Tests can inject small deterministic fakes.
    """
    started = time.monotonic()
    tasks = original_building_tasks(family, trajectory_paths,
                                    source_identity=source_identity)
    result = resume_result or base_result(
        model=model, family=family, attempt_id=attempt_id,
        attempt_role=attempt_role, price_sheet_id=price_sheet_id,
        code_bundle_id=code_bundle_id,
        source_path=source_path, source_record_paths=source_record_paths,
        tasks=tasks, reused_charges=reused_charges)
    if resume_result:
        if result.get("protocol_id") != PROTOCOL_ID:
            raise ValueError("resume result protocol mismatch")
        if result.get("initial_program_sha256") != sha256_path(source_path):
            raise ValueError("resume result initial program mismatch")
        result.setdefault("implementation_interruptions", []).append({
            "resumed_at_epoch": time.time(),
            "previous_terminal_status": result.get("terminal_status"),
            "reason": "resume_from_checkpoint",
        })
        result["status"] = result["terminal_status"] = "running"
        result["code_bundle_id"] = code_bundle_id

    def save() -> None:
        if checkpoint:
            checkpoint(result)

    save()
    extractions = list(result.get("extractions") or [])
    if len(extractions) > len(tasks) or any(x.get("status") != "ok" for x in extractions):
        raise ValueError("resume checkpoint has unusable extraction cache")
    for task in tasks[len(extractions):]:
        extraction = extract_binding(client, model, family, task)
        extractions.append(extraction)
        result["extractions"] = extractions
        result["usage"]["new_extraction"] = usage_total(extractions)
        save()
        if extraction["status"] != "ok":
            result["status"] = extraction["status"]
            result["terminal_status"] = result["status"]
            result["timing"]["attempt_wall_s"] = round(time.monotonic() - started, 3)
            save()
            return result

    source = result.get("final_program_source") or Path(source_path).read_text()
    repair_causes: list[str] = []
    repair_counts = {task["seed"]: 0 for task in tasks}
    cached_candidate = None
    if result.get("candidate_history"):
        possible = result["candidate_history"][-1]
        if possible.get("program_sha256") == sha256_text(source):
            cached_candidate = possible
    while True:
        candidate_started = time.monotonic()
        if cached_candidate is not None:
            gate = {"passed": cached_candidate["passed"], "total": 3,
                    "admitted": cached_candidate["admitted"],
                    "tasks": cached_candidate["tasks"]}
            cached_candidate = None
        else:
            gate = evaluate_candidate(source, tasks, extractions, replay)
        candidate = {
            "version": len(result["candidate_history"]),
            "program_sha256": sha256_text(source),
            "interface_sha256": interface_signature(family),
            "tasks": gate["tasks"],
            "passed": gate["passed"],
            "admitted": gate["admitted"],
            "wall_s": round(time.monotonic() - candidate_started, 3),
        }
        if not result["candidate_history"] or result["candidate_history"][-1].get("program_sha256") != candidate["program_sha256"]:
            result["candidate_history"].append(candidate)
        save()
        if gate["admitted"]:
            result["admitted"] = True
            result["status"] = "complete"
            result["terminal_status"] = "complete"
            break
        failed = next((row for row in gate["tasks"] if row["status"] == "fail"), None)
        if failed is None or repair is None:
            result["status"] = "complete"
            result["terminal_status"] = "complete"
            break
        seed = failed["seed"]
        if repair_counts[seed] >= m_max:
            result["status"] = "complete"
            result["terminal_status"] = "complete"
            break
        task_index = [task["seed"] for task in tasks].index(seed)
        repair_task = {**tasks[task_index],
                       "binding": extractions[task_index]["binding"],
                       "failure": failed}
        repaired = repair(source, repair_task, repair_causes)
        repair_counts[seed] += 1
        repair_record = {k: v for k, v in repaired.items() if k != "source"}
        repair_record["seed"] = seed
        repair_record["round"] = repair_counts[seed]
        result["repairs"].append(repair_record)
        if repaired.get("status") != "ok" or not repaired.get("source"):
            result["status"] = repaired.get("status") or "provider_error"
            result["terminal_status"] = result["status"]
            break
        source = repaired["source"]
        repair_causes.append(str(repaired.get("cause") or "")[:300])

    result["final_program_sha256"] = sha256_text(source)
    result["deployment"]["final_program_sha256"] = result["final_program_sha256"]
    result["final_program_source"] = source
    repair_calls = [{"calls": repair.get("calls", [])} for repair in result["repairs"]]
    result["usage"]["new_repair"] = usage_total(repair_calls)
    result["usage"]["reconciliation"] = {
        "reconciled": True,
        "new_calls": ((result["usage"]["new_extraction"].get("calls") or 0)
                      + (result["usage"]["new_repair"].get("calls") or 0)),
        "new_cost_usd": round(
            (result["usage"]["new_extraction"].get("cost_usd") or 0.0)
            + (result["usage"]["new_repair"].get("cost_usd") or 0.0), 8),
        "call_source": "extractions[].calls plus repairs[].calls",
    }
    result["timing"]["attempt_wall_s"] = round(time.monotonic() - started, 3)
    save()
    return result
