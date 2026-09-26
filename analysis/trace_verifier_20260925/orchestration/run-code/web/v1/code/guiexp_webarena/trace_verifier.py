"""Three-building-task verifier introduced by the 2026-09-25 revision.

This module is deliberately separate from the historical five-binding gate.
It starts from ``artifact_k3_code.py``, extracts the three building bindings
once, and judges executions against the original task parameters.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import time
from pathlib import Path

from .compiler import _openai_client, refine_artifact
from .deploy_runner import (
    build_extraction_prompt,
    build_retry_prompt,
    binding_type_errors,
    deploy_uses,
    parse_binding_json,
    run_deployment,
)
from .family import BINDING_FIELDS, FAMILY, instance_params
from .program_runtime import ProgramRunner, program_from_source
from .verify_runner import TracingDevice, analyze, react_resume, screen_text

PROTOCOL_ID = "three-building-model-extracted-v1"
SEEDS = (1, 2, 3)
M_DEFAULT = 3


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zero_usage() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def add_usage(total: dict, usage: dict, calls: int = 1) -> None:
    total["calls"] += calls
    for key in ("prompt_tokens", "cached_tokens", "completion_tokens"):
        total[key] += usage.get(key) or 0
    total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
    total["cost_usd"] = round(total["cost_usd"] + (usage.get("cost_usd") or 0.0), 8)


def load_original_tasks(source_dir: Path, env) -> list[dict]:
    """Load prompts from source evidence and reconstruct only their params."""
    exploration_path = source_dir / "explore" / "exploration.json"
    data = json.loads(exploration_path.read_text())
    by_seed = {int(item["seed"]): item for item in data["instances"]}
    tasks = []
    for seed in SEEDS:
        if seed not in by_seed or not by_seed[seed].get("goal"):
            raise ValueError(f"source exploration is missing the prompt for seed {seed}")
        params = instance_params(FAMILY, seed, env=env)
        from .family import goal_text

        reconstructed = goal_text(type("Task", (), {"params": params})())
        if reconstructed != by_seed[seed]["goal"]:
            raise ValueError(
                f"seed {seed} reconstructed prompt does not match recorded source prompt"
            )
        tasks.append({
            "seed": seed,
            "prompt": by_seed[seed]["goal"],
            "params": params,
            "prompt_provenance": {
                "kind": "recorded_source_exploration",
                "path": str(exploration_path),
                "sha256": sha256_file(exploration_path),
            },
            "params_provenance": {
                "kind": "reconstructed_from_recorded_seed",
                "seed": seed,
                "generator": "guiexp_webarena.family.instance_params",
            },
        })
    return tasks


def extract_binding(task: dict, model: str, client) -> dict:
    """Use deployment extraction and its single type-check retry."""
    from .agent import WebAgent, _with_backoff

    started = time.monotonic()
    calls = []
    prompts = [build_extraction_prompt(task["prompt"], FAMILY)]
    got = None
    errors = []
    for attempt in range(2):
        messages = prompts[-1]
        call_started = time.monotonic()
        response = _with_backoff(
            client.chat.completions.create,
            model=model, messages=messages, temperature=0.0,
        )
        raw = response.choices[0].message.content or ""
        usage = WebAgent._usage(response)
        got = parse_binding_json(raw)
        errors = binding_type_errors(got, FAMILY)
        calls.append({
            "call": attempt + 1,
            "kind": "extract" if attempt == 0 else "retry",
            "input": messages,
            "output": raw,
            "type_errors": list(errors),
            "usage": usage,
            "wall_s": round(time.monotonic() - call_started, 3),
        })
        if not errors:
            break
        if attempt == 0:
            prompts.append(build_retry_prompt(task["prompt"], FAMILY, errors))
    extracted = None
    if isinstance(got, dict) and not errors:
        extracted = {field: str(got[field]).strip() for field in BINDING_FIELDS}
    return {
        "seed": task["seed"], "input_prompt": task["prompt"],
        "calls": calls, "retries": max(0, len(calls) - 1),
        "extracted_binding": extracted, "type_errors": errors,
        "type_check": "pass" if extracted is not None else "fail",
        "wall_s": round(time.monotonic() - started, 3),
    }


def _replay_worker(connection, source: str, binding: dict, params: dict, reddit_url: str) -> None:
    """Fresh, killable browser process for one replay."""
    from .env import WebArenaEnv

    env = WebArenaEnv(reddit_url=reddit_url)
    try:
        _module, program = program_from_source(source)
        holder = {}

        def factory(worker_env):
            holder["device"] = TracingDevice(worker_env)
            return holder["device"]

        outcome = ProgramRunner(env).run(
            program, binding, FAMILY, judge_params=params,
            device_factory=factory, timeout_s=0,
        )
        device = outcome.get("device") or holder.get("device")
        connection.send({"passed": outcome["passed"], "error": outcome.get("error"),
                         "trace": list(getattr(device, "trace", [])),
                         "screen": screen_text(device) if device is not None else ""})
    except BaseException as exc:  # process boundary converts every failure to data
        connection.send({"passed": False,
                         "error": f"worker: {type(exc).__name__}: {str(exc)[:240]}",
                         "trace": [], "screen": ""})
    finally:
        env.close()
        connection.close()


def _receive_worker(process, connection, timeout_s: float):
    """Read before join so a large child payload cannot fill and block its pipe."""
    if connection.poll(timeout_s):
        payload = connection.recv()
        process.join(5)
        return payload
    return None


def _hard_replay(source: str, binding: dict, params: dict, env,
                 timeout_s: float) -> dict:
    ctx = multiprocessing.get_context("spawn")
    parent_connection, child_connection = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_replay_worker,
                          args=(child_connection, source, binding, params, env.reddit_url))
    process.start()
    child_connection.close()
    payload = _receive_worker(process, parent_connection, timeout_s)
    parent_connection.close()
    if payload is None:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join()
        # Recover the persistent coordinator environment before returning.
        try:
            from .env import WebArenaTask

            env.reset(WebArenaTask(FAMILY, "compile", 0, params))
        except Exception:  # noqa: BLE001
            pass
        return {"passed": False, "error": f"replay timeout after {timeout_s:g}s",
                "trace": [], "screen": "", "timed_out": True}
    return {**payload, "timed_out": False}


class HardReplayRunner:
    """Deployment-compatible runner with the same external 900 s guard."""

    def __init__(self, source: str, env, timeout_s: float = 900.0):
        self.source = source
        self.env = env
        self.timeout_s = timeout_s

    def run(self, _program, binding, _family, judge_params=None, **_kwargs):
        outcome = _hard_replay(self.source, binding, judge_params, self.env,
                               self.timeout_s)
        return {"passed": outcome["passed"], "error": outcome.get("error"),
                "reward": 1.0 if outcome["passed"] else 0.0,
                "timed_out": bool(outcome.get("timed_out"))}


def evaluate(program, tasks: list[dict], bindings: dict[int, dict], runner,
             stop_first: bool = True, *, program_source: str | None = None,
             env=None, hard_timeout_s: float = 900.0) -> dict:
    detail = []
    failure = None
    for index, task in enumerate(tasks):
        holder = {}

        def factory(env, target=holder):
            target["device"] = TracingDevice(env)
            return target["device"]

        started = time.monotonic()
        if program_source is not None:
            if env is None:
                raise ValueError("env is required for hard replay")
            outcome = _hard_replay(program_source, bindings[task["seed"]],
                                   task["params"], env, hard_timeout_s)
        else:
            outcome = runner.run(
                program, bindings[task["seed"]], FAMILY,
                judge_params=task["params"], device_factory=factory,
            )
        row = {"seed": task["seed"], "status": "pass" if outcome["passed"] else "fail",
               "error": outcome.get("error"),
               "timed_out": bool(outcome.get("timed_out")),
               "wall_s": round(time.monotonic() - started, 3)}
        detail.append(row)
        if not outcome["passed"]:
            device = outcome.get("device") or holder.get("device")
            failure = {
                "instance": {"seed": task["seed"], "params": task["params"],
                             "binding": bindings[task["seed"]], "goal": task["prompt"]},
                "error": outcome.get("error"),
                "trace": outcome.get("trace", list(getattr(device, "trace", []))),
                "screen": outcome.get("screen", screen_text(device) if device is not None else ""),
            }
            if stop_first:
                detail.extend({"seed": rest["seed"], "status": "untested",
                               "error": None, "wall_s": 0.0}
                              for rest in tasks[index + 1:])
                break
    return {"passed": sum(row["status"] == "pass" for row in detail),
            "total": len(tasks), "detail": detail, "failure": failure}


def reused_initial_charges(build: dict) -> dict:
    selected = ((build.get("builder") or {}).get("initial") or {}).get("k3_code") or {}
    return {
        "reused_initial_translation": ((build.get("translator") or {}).get("totals") or {}),
        "reused_initial_builder": selected.get("usage") or {},
    }


def run_revision(source_dir: Path, out_dir: Path, env, client=None,
                 deploy_n: int = 30, m_max: int = M_DEFAULT,
                 attempt_role: str = "initial") -> dict:
    started = time.monotonic()
    source_dir, out_dir = Path(source_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    build_path = source_dir / "build.json"
    build = json.loads(build_path.read_text())
    model = build["model"]
    attempt_id = source_dir.parent.name
    artifact_path = source_dir / "artifact_k3_code.py"
    source_paths = {"build": str(build_path), "artifact_k3_code": str(artifact_path),
                    "exploration": str(source_dir / "explore" / "exploration.json"),
                    "translation": str(source_dir / "translation.json")}
    source_hashes = {key: sha256_file(Path(path)) for key, path in source_paths.items()
                     if Path(path).is_file()}
    base = {
        "protocol_id": PROTOCOL_ID, "platform": "web", "model": model,
        "family": FAMILY, "attempt_id": attempt_id,
        "attempt_role": attempt_role,
        "source_paths": source_paths, "source_sha256": source_hashes,
        "reused_initial_charges": reused_initial_charges(build),
        "terminal_status": "running", "record_type": "trace_verifier_attempt",
    }

    def checkpoint() -> None:
        base["wall_s"] = round(time.monotonic() - started, 3)
        (out_dir / "result.json").write_text(json.dumps(base, indent=1, default=str))

    if not artifact_path.is_file():
        refusal = source_dir / "provider_refused.md"
        base.update({"terminal_status": "refused" if refusal.is_file() else "incomplete_source",
                     "admitted": False, "reason": "artifact_k3_code.py is absent",
                     "wall_s": round(time.monotonic() - started, 3)})
        (out_dir / "result.json").write_text(json.dumps(base, indent=1))
        return base

    client = client or _openai_client()
    tasks = load_original_tasks(source_dir, env)
    base["tasks"] = tasks
    extraction_usage = zero_usage()
    extractions = []
    try:
        for task in tasks:
            base["active_call"] = {"stage": "extraction", "seed": task["seed"],
                                   "started_at_unix": time.time(), "request_timeout_s": 300.0}
            checkpoint()
            record = extract_binding(task, model, client)
            base["active_call"] = None
            extractions.append(record)
            for call in record["calls"]:
                add_usage(extraction_usage, call["usage"])
            base.update({"extractions": extractions,
                         "new_charges": {"extraction": extraction_usage,
                                         "repair": zero_usage()}})
            checkpoint()
            if record["type_check"] != "pass":
                base.update({"extractions": extractions, "admitted": False,
                             "terminal_status": "extraction_failure",
                             "new_charges": {"extraction": extraction_usage,
                                             "repair": zero_usage()},
                             "wall_s": round(time.monotonic() - started, 3)})
                (out_dir / "result.json").write_text(json.dumps(base, indent=1, default=str))
                return base
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        base.update({"extractions": extractions, "admitted": False,
                     "terminal_status": "insufficient_credit" if status_code == 402 else "provider_error",
                     "provider_status_code": status_code,
                     "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                     "new_charges": {"extraction": extraction_usage,
                                     "repair": zero_usage()},
                     "wall_s": round(time.monotonic() - started, 3)})
        (out_dir / "result.json").write_text(json.dumps(base, indent=1, default=str))
        return base

    bindings = {item["seed"]: item["extracted_binding"] for item in extractions}
    initial_source = artifact_path.read_text()
    candidate_source = initial_source
    _module, program = program_from_source(candidate_source)
    runner = ProgramRunner(env)
    history = []
    repair_usage = zero_usage()
    rounds = []
    result = evaluate(program, tasks, bindings, runner,
                      program_source=candidate_source, env=env)
    history.append({"version": "initial", "sha256": sha256_text(candidate_source),
                    "tasks": result["detail"], "passed": result["passed"]})

    failure_rounds: dict[int, int] = {}
    round_no = 0
    while result["passed"] != 3 and result["failure"] is not None:
        failed_seed = result["failure"]["instance"]["seed"]
        if failure_rounds.get(failed_seed, 0) >= m_max:
            break
        failure_rounds[failed_seed] = failure_rounds.get(failed_seed, 0) + 1
        round_no += 1
        failure = result["failure"]
        base["active_call"] = {"stage": "analyzer", "seed": failed_seed,
                               "round": round_no, "started_at_unix": time.time(),
                               "request_timeout_s": 300.0}
        checkpoint()
        call_started = time.monotonic()
        analysis = analyze(model, FAMILY, failure, client=client)
        analysis_wall_s = round(time.monotonic() - call_started, 3)
        base["active_call"] = None
        add_usage(repair_usage, analysis["usage"])
        rounds.append({"round": round_no, "failure_seed": failed_seed,
                       "instance_round": failure_rounds[failed_seed],
                       "analysis": {k: analysis.get(k) for k in
                                    ("cause", "done", "plan", "decision")},
                       "analysis_usage": analysis["usage"],
                       "analysis_wall_s": analysis_wall_s})
        base.update({"candidate_history": history, "repairs": rounds,
                     "new_charges": {"extraction": extraction_usage,
                                     "repair": repair_usage}})
        checkpoint()
        # The failed replay's worker browser is closed after evidence capture,
        # so continuation must restart in the coordinator browser.
        resume_analysis = {**analysis, "decision": "restart"}
        base["active_call"] = {"stage": "react_resume", "seed": failed_seed,
                               "round": round_no, "started_at_unix": time.time(),
                               "request_timeout_s": 300.0}
        checkpoint()
        call_started = time.monotonic()
        resume = react_resume(model, env, FAMILY, failure, resume_analysis, client=client)
        resume_wall_s = round(time.monotonic() - call_started, 3)
        base["active_call"] = None
        add_usage(repair_usage, resume["usage"], calls=resume["usage"].get("calls", 0))
        rounds[-1].update({"resume_success": resume["success"],
                           "resume_usage": resume["usage"],
                           "resume_wall_s": resume_wall_s,
                           "resume_calls_detail": resume.get("calls_detail") or [],
                           "resume_forced_restart": True,
                           "resume_restart_reason": "replay worker browser closed after evidence capture"})
        checkpoint()
        hybrid = {
            "binding": failure["instance"]["binding"], "goal": failure["instance"]["goal"],
            "failure": failure["error"], "program_trace": failure["trace"],
            "breakpoint_screen": failure["screen"], "analysis": analysis.get("text", ""),
            "resume_actions": resume["actions"], "resume_success": resume["success"],
        }
        base["active_call"] = {"stage": "builder_refine", "seed": failed_seed,
                               "round": round_no, "started_at_unix": time.time(),
                               "request_timeout_s": 300.0}
        checkpoint()
        call_started = time.monotonic()
        refined = refine_artifact(model, FAMILY, candidate_source, hybrid, [], client=client)
        refine_wall_s = round(time.monotonic() - call_started, 3)
        base["active_call"] = None
        add_usage(repair_usage, refined["usage"], calls=refined["usage"].get("calls", 1))
        rounds[-1]["refinement_usage"] = refined["usage"]
        rounds[-1]["refinement_wall_s"] = refine_wall_s
        rounds[-1]["refinement_calls_detail"] = refined.get("calls_detail") or []
        checkpoint()
        candidate_source = refined["artifact_text"]
        (out_dir / f"candidate_refine{round_no}.py").write_text(candidate_source)
        _module, program = program_from_source(candidate_source)
        result = evaluate(program, tasks, bindings, runner,
                          program_source=candidate_source, env=env)
        history.append({"version": f"refine{round_no}", "sha256": sha256_text(candidate_source),
                        "tasks": result["detail"], "passed": result["passed"]})
        checkpoint()

    admitted = result["passed"] == 3
    final_path = out_dir / "verified_program.py"
    final_path.write_text(candidate_source)
    deployment = {"status": "skipped", "reason": "candidate was not admitted"}
    if admitted and deploy_n == 0:
        deployment = {"status": "not_run", "reason": "pilot requested zero deployment uses"}
    if admitted and deploy_n:
        historical_verified = source_dir / "verified_program.py"
        historical_deploy = source_dir / "deploy.json"
        same_program = historical_verified.is_file() and historical_verified.read_bytes() == final_path.read_bytes()
        if same_program and historical_deploy.is_file():
            historical_program_hash = sha256_file(historical_verified)
            deployment = {"status": "reused", "path": str(historical_deploy),
                          "sha256": sha256_file(historical_deploy),
                          "program_byte_identical": True,
                          "historical_deployed_program_sha256": historical_program_hash,
                          "final_program_sha256": sha256_text(candidate_source),
                          "program_hash_equal": historical_program_hash == sha256_text(candidate_source),
                          "task_generator_identical": True,
                          "input_protocol_identical": True,
                          "justification": "byte-identical program and unchanged deployment input protocol"}
        else:
            deployed = run_deployment(program, FAMILY, deploy_uses(FAMILY, deploy_n, env),
                                      client, model, HardReplayRunner(candidate_source, env),
                                      role="three-building-model-extracted-v1",
                                      progress_path=str(out_dir / "deploy_progress.jsonl"))
            (out_dir / "deploy.json").write_text(json.dumps(deployed, indent=1))
            deployment = {"status": "new", "path": str(out_dir / "deploy.json"),
                          "program_byte_identical": same_program,
                          "historical_deployed_program_sha256": (
                              sha256_file(historical_verified) if historical_verified.is_file() else None
                          ),
                          "final_program_sha256": sha256_text(candidate_source),
                          "program_hash_equal": same_program,
                          "task_generator_identical": True,
                          "input_protocol_identical": True, **deployed}

    base.update({
        "extractions": extractions,
        "initial_program_sha256": sha256_text(initial_source),
        "final_program_sha256": sha256_text(candidate_source),
        "candidate_history": history, "repairs": rounds,
        "admitted": admitted, "final_tasks": result["detail"],
        "terminal_status": "complete",
        "failure_kind": (
            "replay_timeout" if not admitted and any(row.get("timed_out") for row in result["detail"])
            else ("task_failure" if not admitted else None)
        ), "deployment": deployment,
        "new_charges": {"extraction": extraction_usage, "repair": repair_usage},
        "wall_s": round(time.monotonic() - started, 3),
    })
    (out_dir / "result.json").write_text(json.dumps(base, indent=1, default=str))
    return base
