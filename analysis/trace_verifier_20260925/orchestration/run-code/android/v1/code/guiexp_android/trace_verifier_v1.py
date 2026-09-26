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


def sha256_path(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
            got = parse_binding_json(raw)
            errors = binding_type_errors(got, family)
            outputs.append(raw)
            calls.append({
                **call_record(attempt + 1, attempt + 1, usage,
                              stage="verification_extract",
                              kind="extract" if attempt == 0 else "retry"),
                "wall_s": round(time.monotonic() - call_started, 3),
            })
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
    price_sheet_id: str, source_path: Path | str,
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
    price_sheet_id: str, source_path: Path | str,
    source_record_paths: list[Path | str], trajectory_paths: list[Path | str],
    client, replay: Callable[[str, dict, dict, float], dict],
    repair: Callable[[str, dict, list[str]], dict] | None = None,
    reused_charges: dict | None = None, m_max: int = 3,
    checkpoint: Callable[[dict], None] | None = None,
    source_identity: dict | None = None,
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
    result = base_result(model=model, family=family, attempt_id=attempt_id,
                         attempt_role=attempt_role, price_sheet_id=price_sheet_id,
                         source_path=source_path, source_record_paths=source_record_paths,
                         tasks=tasks, reused_charges=reused_charges)

    def save() -> None:
        if checkpoint:
            checkpoint(result)

    save()
    extractions = []
    for task in tasks:
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

    source = Path(source_path).read_text()
    repair_causes: list[str] = []
    repair_counts = {task["seed"]: 0 for task in tasks}
    while True:
        candidate_started = time.monotonic()
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
