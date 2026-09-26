"""Three-building-task verifier revision for OSWorld.

This module is deliberately separate from the historical five-binding gate.
It starts from the cached k=3 code artifact, extracts the three original task
bindings once, and admits only a candidate that passes all three original
tasks. Replays run in killable child processes so generated code cannot catch
the parent's wall-clock deadline.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import queue as queue_module
import inspect
import time
from pathlib import Path
from typing import Callable

from . import families, guest_env
from .accounting import call_record, price_weighted_call, price_weighted_totals, weights_for
from .deploy_runner import (
    binding_type_errors,
    build_extraction_prompt,
    build_retry_prompt,
    normalize_binding,
    parse_binding_json,
)
from .program_runtime import ProgramRunner, program_from_source

PROTOCOL_ID = "three-building-model-extracted-v1"
REPLAY_TIMEOUT_S = 1200


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_initial_attempt(build_path: Path | str) -> dict:
    """Load the immutable k=3 initial artifact and reconstruct source tasks."""
    path = Path(build_path).resolve()
    build = json.loads(path.read_text())
    artifact_record = build["builder"]["initial"]["k3_code"]
    if artifact_record.get("artifact") != "code":
        raise ValueError("builder.initial.k3_code is not a code artifact")
    artifact_path = path.parent / "artifact_k3_code.py"
    artifact = artifact_path.read_text()
    instances = []
    by_seed = {int(x["seed"]): x for x in build["exploration"]["instances"]}
    for seed in (1, 2, 3):
        recorded = by_seed[seed]
        params = families.instance_params(build["family"], seed)
        generated_goal = families.goal_text(build["family"], seed, params)
        goal = recorded["goal"]
        if goal != generated_goal:
            raise ValueError(f"recorded prompt does not match generator for seed {seed}")
        instances.append({
            "seed": seed,
            "prompt": goal,
            "params": params,
            "prompt_provenance": {
                "kind": "recorded_exploration_prompt",
                "source_path": str(path),
                "generator_match": True,
            },
        })
    record = {
        "build_path": str(path),
        "build_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "artifact_path": str(artifact_path),
        "model": build["model"],
        "family": build["family"],
        "program_source": artifact,
        "program_sha256": sha256_text(artifact),
        "instances": instances,
        "reused_charges": {
            "translator": build["translator"]["totals"],
            "builder_initial_k3": build["builder"]["initial"]["k3_code"]["usage"],
        },
    }
    return record


def extract_binding(client, model: str, family: str, prompt: str) -> dict:
    """Run the existing deployment extractor with its one bounded retry."""
    from .agent import OSWorldAgent

    started = time.monotonic()
    calls = []
    raw_outputs = []
    provider_attempts = []

    def is_rate_limit(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        status = status or getattr(response, "status_code", None)
        text = str(exc).lower()
        return status == 429 or "429" in text or "rate limit" in text

    def invoke(messages, kind):
        call_started = time.monotonic()
        for transport_attempt in range(1, 4):
            try:
                response = client.chat.completions.create(
                    model=model, messages=messages, temperature=0.0)
                provider_attempts.append({"kind": kind, "attempt": transport_attempt,
                                          "status": "success"})
                break
            except Exception as exc:
                provider_attempts.append({
                    "kind": kind, "attempt": transport_attempt,
                    "status": "rate_limited" if is_rate_limit(exc) else "error",
                    "error_type": type(exc).__name__,
                })
                if not is_rate_limit(exc) or transport_attempt == 3:
                    raise
                time.sleep(10.0 * transport_attempt)
        usage = OSWorldAgent._usage(response)
        raw = response.choices[0].message.content or ""
        raw_outputs.append(raw)
        record = call_record(len(calls) + 1, len(calls) + 1, usage,
                             stage="verification_extract", kind=kind)
        record["wall_s"] = round(time.monotonic() - call_started, 3)
        calls.append(record)
        return raw

    raw = invoke(build_extraction_prompt(prompt, family), "extract")
    got = parse_binding_json(raw)
    errors = binding_type_errors(got, family)
    if errors:
        raw = invoke(build_retry_prompt(prompt, family, errors), "retry")
        got = parse_binding_json(raw)
        errors = binding_type_errors(got, family)
    return {
        "input": {"task_prompt": prompt, "parameter_schema": list(families.binding_fields(family))},
        "raw_outputs": raw_outputs,
        "binding": normalize_binding(got, family) if not errors else None,
        "type_check": {"passed": not errors, "errors": errors},
        "retry_count": max(0, len(calls) - 1),
        "calls": calls,
        "provider_attempts": provider_attempts,
        "wall_s": round(time.monotonic() - started, 3),
    }


def _replay_child(queue, source, binding, family, judge_params):
    try:
        from .verify_runner import TracingDevice, screen_text

        _module, program = program_from_source(source)
        holder = {}

        def factory(env):
            holder["device"] = TracingDevice(env)
            return holder["device"]

        outcome = ProgramRunner(guest_env.OSWorldEnv()).run(
            program, binding, family, judge_params=judge_params,
            device_factory=factory, timeout_s=0)
        device = outcome.get("device") or holder.get("device")
        serial = {k: v for k, v in outcome.items() if k != "device"}
        serial["trace"] = list(getattr(device, "trace", []))
        serial["screen"] = screen_text(device) if device is not None else ""
        queue.put(serial)
    except BaseException as exc:  # process boundary must always report a shape
        queue.put({"passed": False, "reward": 0.0,
                   "error": f"{type(exc).__name__}: {str(exc)[:300]}"})


def hard_replay(source: str, binding: dict, family: str, judge_params: dict,
                timeout_s: float = REPLAY_TIMEOUT_S,
                context: mp.context.BaseContext | None = None,
                _worker: Callable = _replay_child) -> dict:
    """Replay in a child and enforce the deadline in the parent process."""
    context = context or mp.get_context("spawn")
    queue = context.Queue(1)
    process = context.Process(
        target=_worker,
        args=(queue, source, binding, family, judge_params),
        daemon=True,
    )
    started = time.monotonic()
    process.start()
    outcome = None
    while time.monotonic() - started < timeout_s:
        try:
            # Drain before join. multiprocessing.Queue's feeder can block
            # process exit when a trace is larger than the pipe buffer.
            outcome = queue.get(timeout=min(0.2, timeout_s))
            break
        except queue_module.Empty:
            if not process.is_alive():
                break
    wall_s = round(time.monotonic() - started, 3)
    if outcome is None and process.is_alive():
        process.terminate()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join(10)
        return {"passed": False, "reward": 0.0, "error": "hard replay timeout",
                "status": "timeout", "wall_s": wall_s}
    if outcome is None:
        process.join(1)
        return {"passed": False, "reward": 0.0,
                "error": f"replay worker exited {process.exitcode} without a result",
                "status": "error", "wall_s": wall_s}
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(10)
    outcome.update(status="pass" if outcome.get("passed") else "fail", wall_s=wall_s)
    return outcome


def verify_initial(build_path: Path | str, client,
                   replay: Callable[..., dict] = hard_replay,
                   timeout_s: float = REPLAY_TIMEOUT_S,
                   attempt_id: str = "initial",
                   attempt_role: str = "initial") -> dict:
    """Verify the cached initial candidate. Repairs are a later attempt."""
    started = time.monotonic()
    source = load_initial_attempt(build_path)
    tasks = []
    terminal_status = "provisional_initial"
    # Compilation-attempt inputs are fixed before any candidate replay. This
    # cache remains valid for later candidates with the same interface.
    for instance in source["instances"]:
        extraction = extract_binding(
            client, source["model"], source["family"], instance["prompt"])
        task = {k: instance[k] for k in ("seed", "prompt", "prompt_provenance")}
        task["extraction"] = extraction
        task["judge_params"] = instance["params"]
        task["status"] = "untested"
        if extraction["binding"] is None:
            task["error"] = "extraction_failure"
        tasks.append(task)
    extraction_ok = all(t["extraction"]["binding"] is not None for t in tasks)
    if extraction_ok:
        for task in tasks:
            outcome = replay(source["program_source"], task["extraction"]["binding"],
                             source["family"], task["judge_params"], timeout_s=timeout_s)
            task["replay"] = outcome
            task["status"] = "pass" if outcome.get("passed") else "fail"
            if outcome["status"] == "timeout":
                task["failure_kind"] = "replay_timeout"
                break
            if not outcome.get("passed"):
                break
    admitted = len(tasks) == 3 and all(t["status"] == "pass" for t in tasks)
    if admitted:
        terminal_status = "complete"
    extraction_calls = [c for t in tasks for c in t.get("extraction", {}).get("calls", [])]
    reused_translation = source["reused_charges"]["translator"].get("cost_usd", 0.0)
    reused_builder = source["reused_charges"]["builder_initial_k3"].get("cost_usd", 0.0)
    new_extraction = sum(c.get("cost_usd") or 0.0 for c in extraction_calls)
    charges = {
        "unit": "usd",
        "reused_initial_translation": reused_translation,
        "reused_initial_builder": reused_builder,
        "new_extraction": round(new_extraction, 8),
        "new_repair": 0.0,
    }
    charges["C"] = round(sum(v for k, v in charges.items()
                              if k not in {"unit", "C"}), 8)
    weights = weights_for(source["model"])
    extraction_pw = [
        price_weighted_call(c.get("prompt_tokens") or 0, c.get("cached_tokens"),
                            c.get("completion_tokens") or 0,
                            weights["r_c"], weights["r_o"])
        for c in extraction_calls
    ]
    charges_pw = {
        "unit": "price_weighted_tokens",
        "price_sheet_id": "osworld-price-weights-2026-09-05",
        "price_weights": weights,
        "reused_initial_translation": price_weighted_totals(
            source["reused_charges"]["translator"], **weights),
        "reused_initial_builder": price_weighted_totals(
            source["reused_charges"]["builder_initial_k3"], **weights),
        "new_extraction": sum(extraction_pw),
        "new_repair": 0,
        "references": {
            "reused_initial_translation": "source build.json#/translator/totals",
            "reused_initial_builder": "source build.json#/builder/initial/k3_code/usage",
            "new_extraction": [f"#/tasks/{i}/extraction/calls/{j}"
                               for i, task in enumerate(tasks)
                               for j, _call in enumerate(task.get("extraction", {}).get("calls", []))],
            "new_repair": [],
        },
        "new_extraction_per_call": extraction_pw,
    }
    charges_pw["C"] = (charges_pw["reused_initial_translation"]
                         + charges_pw["reused_initial_builder"]
                         + charges_pw["new_extraction"] + charges_pw["new_repair"])
    record = {
        "protocol_id": PROTOCOL_ID,
        "platform": "desktop",
        "model": source["model"],
        "family": source["family"],
        "attempt_id": attempt_id,
        "attempt_role": attempt_role,
        "source": {
            "build_path": source["build_path"],
            "build_sha256": source["build_sha256"],
            "artifact_path": source["artifact_path"],
            "artifact_sha256": source["program_sha256"],
        },
        "initial_program_sha256": source["program_sha256"],
        "final_program_sha256": source["program_sha256"],
        "candidate_history": [{"version": "initial_k3", "sha256": source["program_sha256"],
                               "source": "builder.initial.k3_code.artifact"}],
        "tasks": sorted(tasks, key=lambda x: x["seed"]),
        "admitted": admitted,
        "terminal_status": terminal_status,
        "repairs": [],
        "repair_rounds": 0,
        "usage": {
            "reused": source["reused_charges"],
            "new_extraction_calls": extraction_calls,
            "new_repair_calls": [],
        },
        "charges": charges,
        "charges_pw": charges_pw,
        "deployment": {
            "status": "required_if_admitted",
            "reuse_justification": None,
            "byte_identical_program": None,
            "unchanged_execution_protocol": False,
            "unchanged_input_protocol": False,
        },
        "wall_s": round(time.monotonic() - started, 3),
    }
    record["extractions"] = [t["extraction"] for t in record["tasks"]
                             if "extraction" in t]
    return record


def verify_and_repair(build_path: Path | str, client,
                      replay: Callable[..., dict] = hard_replay,
                      timeout_s: float = REPLAY_TIMEOUT_S,
                      attempt_id: str = "initial",
                      attempt_role: str = "initial",
                      m_per_seed: int = 3,
                      checkpoint: Callable[[dict, str | None], None] | None = None,
                      prior_result: dict | None = None,
                      prior_source: str | None = None) -> tuple[dict, str]:
    """Run the complete fixed-three-task verifier with bounded repairs."""
    from .compiler import refine_artifact
    from .verify_runner import analyze, react_resume

    source_record = load_initial_attempt(build_path)
    if prior_result is None:
        result = verify_initial(build_path, client, replay, timeout_s,
                                attempt_id=attempt_id, attempt_role=attempt_role)
    else:
        result = prior_result
        result["terminal_status"] = "provisional_resume"
        result.pop("error", None)
        result.setdefault("extractions", [t["extraction"] for t in result["tasks"]
                                          if "extraction" in t])
    current_source = prior_source or source_record["program_source"]
    started = time.monotonic()
    if checkpoint:
        checkpoint(result, current_source)
    if result["admitted"]:
        return result, current_source
    if any(t.get("extraction", {}).get("binding") is None for t in result["tasks"]):
        result["terminal_status"] = "complete"
        result["failure_kind"] = "extraction_failure"
        return result, current_source
    first = next((t for t in result["tasks"] if t["status"] == "fail"), None)
    if first and "ConnectionError" in (first.get("replay", {}).get("error") or ""):
        result["terminal_status"] = "infrastructure_error"
        result["failure_kind"] = "guest_connection"
        return result, current_source

    env = guest_env.OSWorldEnv()
    per_seed = {1: 0, 2: 0, 3: 0}
    for old_repair in result.get("repairs", []):
        seed = int(old_repair["failure_seed"])
        per_seed[seed] = max(per_seed[seed], int(old_repair.get("round_for_seed", 0)))
    repair_calls = list(result.get("usage", {}).get("new_repair_calls") or [])
    try:
        while not result["admitted"]:
            failed = next((t for t in result["tasks"] if t["status"] == "fail"), None)
            if failed is None or per_seed[failed["seed"]] >= m_per_seed:
                result["terminal_status"] = "complete"
                result["failure_kind"] = (failed or {}).get("failure_kind", "program_failure")
                break
            per_seed[failed["seed"]] += 1
            replay_record = failed.get("replay") or {}
            failure = {
                "instance": {
                    "seed": failed["seed"],
                    "params": failed["judge_params"],
                    "binding": failed["extraction"]["binding"],
                    "goal": failed["prompt"],
                },
                "error": replay_record.get("error"),
                "trace": replay_record.get("trace") or [],
                "screen": replay_record.get("screen") or "",
            }
            analysis = analyze(source_record["model"], source_record["family"],
                               failure, client=client)
            analysis_call = call_record(1, 1, analysis["usage"], stage="analyzer")
            repair_calls.append(analysis_call)
            result["usage"]["new_repair_calls"] = list(repair_calls)
            if checkpoint:
                checkpoint(result, current_source)
            def resume_checkpoint(call):
                repair_calls.append(call)
                result["usage"]["new_repair_calls"] = list(repair_calls)
                if checkpoint:
                    checkpoint(result, current_source)

            resume_kwargs = {"client": client}
            if "call_checkpoint" in inspect.signature(react_resume).parameters:
                resume_kwargs["call_checkpoint"] = resume_checkpoint
            resume = react_resume(source_record["model"], env, source_record["family"],
                                  failure, analysis, **resume_kwargs)
            if "call_checkpoint" not in resume_kwargs:
                repair_calls.extend(resume.get("calls_detail") or [])
                result["usage"]["new_repair_calls"] = list(repair_calls)
            result["usage"]["new_repair_calls"] = list(repair_calls)
            if checkpoint:
                checkpoint(result, current_source)
            hybrid = {
                "binding": failure["instance"]["binding"],
                "goal": failure["instance"]["goal"],
                "failure": failure["error"],
                "program_trace": failure["trace"],
                "breakpoint_screen": failure["screen"],
                "analysis": analysis.get("text", ""),
                "resume_actions": resume["actions"],
                "resume_success": resume["success"],
            }
            def refine_checkpoint(call):
                repair_calls.append(call)
                result["usage"]["new_repair_calls"] = list(repair_calls)
                if checkpoint:
                    checkpoint(result, current_source)

            refine_kwargs = {"artifact": "code", "client": client}
            if "call_checkpoint" in inspect.signature(refine_artifact).parameters:
                refine_kwargs["call_checkpoint"] = refine_checkpoint
            refined = refine_artifact(source_record["model"], source_record["family"],
                                      current_source, hybrid, [], **refine_kwargs)
            if "call_checkpoint" not in refine_kwargs:
                repair_calls.extend(refined.get("calls_detail") or [])
                result["usage"]["new_repair_calls"] = list(repair_calls)
            refine_record = call_record(1, 1, refined["usage"], stage="builder_refine",
                                        attempts=refined.get("attempts"),
                                        calls_detail=refined.get("calls_detail") or [])
            if not refined.get("calls_detail"):
                repair_calls.append(refine_record)
            result["usage"]["new_repair_calls"] = list(repair_calls)
            current_source = refined["artifact_text"]
            version = f"repair-{len(result['repairs']) + 1}"
            version_tasks = []
            for original in sorted(result["tasks"], key=lambda x: x["seed"]):
                outcome = replay(current_source, original["extraction"]["binding"],
                                 source_record["family"], original["judge_params"],
                                 timeout_s=timeout_s)
                version_tasks.append({"seed": original["seed"],
                                      "status": "pass" if outcome.get("passed") else "fail",
                                      "replay": outcome})
                if not outcome.get("passed"):
                    break
            tested = {t["seed"] for t in version_tasks}
            for original in result["tasks"]:
                if original["seed"] not in tested:
                    version_tasks.append({"seed": original["seed"], "status": "untested"})
            result["repairs"].append({
                "version": version, "failure_seed": failed["seed"],
                "round_for_seed": per_seed[failed["seed"]],
                "analysis": {k: analysis.get(k) for k in ("cause", "done", "plan", "decision")},
                "resume": {"restarted": resume["restarted"], "steps": resume["steps"],
                           "success": resume["success"]},
                "refinement_usage": refine_record,
                "tasks": version_tasks,
            })
            result["repair_rounds"] = len(result["repairs"])
            result["candidate_history"].append({
                "version": version, "sha256": sha256_text(current_source),
                "source": "builder_refine", "tasks": version_tasks,
            })
            for task in result["tasks"]:
                updated = next(x for x in version_tasks if x["seed"] == task["seed"])
                task["status"] = updated["status"]
                if "replay" in updated:
                    task["replay"] = updated["replay"]
                    if updated["replay"].get("status") == "timeout":
                        task["failure_kind"] = "replay_timeout"
            result["admitted"] = all(t["status"] == "pass" for t in result["tasks"])
            result["final_program_sha256"] = sha256_text(current_source)
            result["terminal_status"] = "complete" if result["admitted"] else "provisional_repair"
            if checkpoint:
                checkpoint(result, current_source)
            if result["admitted"]:
                break
    finally:
        env.close()
    weights = weights_for(source_record["model"])
    repair_pw = sum(price_weighted_call(c.get("prompt_tokens") or 0, c.get("cached_tokens"),
                                        c.get("completion_tokens") or 0,
                                        weights["r_c"], weights["r_o"])
                    for c in repair_calls)
    result["usage"]["new_repair_calls"] = repair_calls
    result["charges_pw"]["new_repair"] = repair_pw
    result["charges_pw"]["references"]["new_repair"] = [
        f"#/usage/new_repair_calls/{i}" for i in range(len(repair_calls))]
    result["charges_pw"]["C"] += repair_pw
    result["charges"]["new_repair"] = round(sum(c.get("cost_usd") or 0 for c in repair_calls), 8)
    result["charges"]["C"] = round(result["charges"]["C"] + result["charges"]["new_repair"], 8)
    result["wall_s"] = round(result["wall_s"] + time.monotonic() - started, 3)
    if not result["admitted"] and result["terminal_status"] == "provisional_repair":
        result["terminal_status"] = "complete"
        result["failure_kind"] = "repair_budget_exhausted"
    return result, current_source


def run_protocol_deployment(result: dict, program_source: str, client,
                            replay: Callable[..., dict] = hard_replay,
                            timeout_s: float = REPLAY_TIMEOUT_S,
                            n: int = 30,
                            checkpoint: Callable[[dict, str | None], None] | None = None) -> dict:
    """Run the established natural-language deployment uses for an admitted program."""
    from .deploy_runner import deploy_uses

    if not result.get("admitted"):
        return result
    records = []
    for index, (goal, expected) in enumerate(deploy_uses(result["family"], n), 1):
        extraction = extract_binding(client, result["model"], result["family"], goal)
        record = {"use": index, "goal": goal, "expected": expected,
                  "extraction": extraction, "status": "fail"}
        if extraction["binding"] is None:
            record["failure_kind"] = "extraction_failure"
        else:
            judge_params = families.binding_to_params(result["family"], expected)
            outcome = replay(program_source, extraction["binding"], result["family"],
                             judge_params, timeout_s=timeout_s)
            record["replay"] = outcome
            record["status"] = "pass" if outcome.get("passed") else "fail"
            if outcome.get("status") == "timeout":
                record["failure_kind"] = "replay_timeout"
        records.append(record)
        result["deployment"] = {
            "status": "running" if index < n else "complete",
            "n": n, "uses": records,
            "success_count": sum(r["status"] == "pass" for r in records),
            "byte_identical_program": False,
            "unchanged_execution_protocol": False,
            "unchanged_input_protocol": False,
            "reuse_justification": "new verifier input protocol requires a new deployment",
        }
        if checkpoint:
            checkpoint(result, program_source)
    result["deployment"]["success_rate"] = result["deployment"]["success_count"] / n
    return result
