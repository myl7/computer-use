#!/usr/bin/env python3
"""Supervisor and worker for one Android three-building verifier cell."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import signal
import subprocess
import sys
import time
from pathlib import Path

REPLAY_TIMEOUT_S = 1200


def load_private_openrouter_env(repo: Path, explicit_path: str | None = None) -> None:
    """Load only the two authorized OpenRouter variables, without logging."""
    path = Path(explicit_path).resolve() if explicit_path else repo / ".trace_verifier_openrouter.env"
    if not path.exists():
        raise FileNotFoundError(path)
    allowed = {"OPENROUTER_BASE_URL", "OPENROUTER_API_KEY"}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in allowed and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")
    missing = sorted(key for key in allowed if not os.environ.get(key))
    if missing:
        raise RuntimeError("private OpenRouter environment is missing required names")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, default=str))
    tmp.replace(path)


def supervisor(args) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    heartbeat = out / "heartbeat.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker",
           "--source-cell", args.source_cell, "--model", args.model,
           "--family", args.family, "--attempt-id", args.attempt_id,
           "--attempt-role", args.attempt_role, "--price-sheet-id", args.price_sheet_id,
           "--code-bundle-id", args.code_bundle_id,
           "--out", str(out), "--console-port", str(args.console_port),
           "--grpc-port", str(args.grpc_port), "--avd", args.avd]
    if args.private_env_file:
        cmd += ["--private-env-file", args.private_env_file]
    proc = subprocess.Popen(cmd, start_new_session=True)
    operator_abort = False
    try:
        while proc.poll() is None:
            time.sleep(2)
            if not heartbeat.exists():
                continue
            state = json.loads(heartbeat.read_text())
            if state.get("phase") == "replay" and time.time() > state["deadline_epoch"]:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                partial = json.loads(result_path.read_text()) if result_path.exists() else {}
                partial.update({"protocol_id": "three-building-model-extracted-v1",
                                "platform": "Android", "model": args.model,
                                "family": args.family, "attempt_id": args.attempt_id,
                                "attempt_role": args.attempt_role,
                                "price_sheet_id": args.price_sheet_id,
                                "status": "timeout", "terminal_status": "timeout",
                                "failure_kind": "replay_timeout",
                                "timeout_scope": "whole_attempt_guard", "admitted": False,
                                "timeout": state})
                atomic_json(result_path, partial)
                return 124
    except KeyboardInterrupt:
        operator_abort = True
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    if operator_abort:
        partial = json.loads(result_path.read_text()) if result_path.exists() else {}
        partial.update({"protocol_id": "three-building-model-extracted-v1",
                        "platform": "Android", "model": args.model,
                        "family": args.family, "attempt_id": args.attempt_id,
                        "attempt_role": args.attempt_role,
                        "price_sheet_id": args.price_sheet_id,
                        "status": "operator_aborted", "terminal_status": "operator_aborted",
                        "admitted": False})
        atomic_json(result_path, partial)
        return 130
    return proc.returncode or 0


def worker(args) -> int:
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "code"))
    from guiexp_android import android_env
    from guiexp_android.compiler import refine_artifact
    from guiexp_android.deploy_runner import deploy_uses, run_deployment
    from guiexp_android.expa.lane import (configure_lane, make_client,
                                          remap_trajectory_path)
    from guiexp_android.program_runtime import ProgramRunner, program_from_source
    from guiexp_android.trace_verifier_v1 import run_attempt
    from guiexp_android.verify_runner import analyze, first_failure, react_resume

    source_cell = Path(args.source_cell).resolve()
    out = Path(args.out).resolve()
    result_path = out / "result.json"
    heartbeat = out / "heartbeat.json"
    build_path = source_cell / "build.json"
    compile_path = source_cell / "compile.json"
    source_record = build_path if build_path.exists() else compile_path
    if not source_record.exists():
        raise FileNotFoundError("source cell has neither build.json nor compile.json")
    record = json.loads(source_record.read_text())
    if record.get("family") != args.family or list(record.get("seeds", [])) != [1, 2, 3]:
        raise ValueError("source record family/seeds do not match requested original tasks")
    translation_path = source_cell / "translation.json"
    if translation_path.exists():
        translation = json.loads(translation_path.read_text())
        trajectories = [Path(translation[str(seed)]["trajectory"]) for seed in (1, 2, 3)]
    else:
        trajectories = [Path(p) for p in record["building_trajectories"]]
    resolved_trajectories = []
    trajectory_resolution = {}
    for seed, raw_path in zip((1, 2, 3), trajectories):
        resolved = remap_trajectory_path(raw_path)
        if not resolved.exists():
            alias = source_cell / "explore" / f"s{seed}_a0" / "trajectory.jsonl"
            if alias.exists():
                trajectory_resolution[str(seed)] = {
                    "kind": "source_cell_relative_alias",
                    "recorded_path": str(raw_path),
                    "resolved_path": str(alias.resolve()),
                }
                resolved = alias.resolve()
        resolved_trajectories.append(resolved)
    trajectories = resolved_trajectories
    program_path = source_cell / "artifact_k3_code.py"
    if not program_path.exists():
        raise FileNotFoundError(program_path)

    load_private_openrouter_env(repo, args.private_env_file)
    configure_lane(args.console_port, args.grpc_port, args.avd)
    env = android_env.AndroidWorldEnv(console_port=args.console_port, grpc_port=args.grpc_port)
    runner = ProgramRunner(env)
    raw_client = make_client()

    class _CheckpointingCompletions:
        def create(self, **kwargs):
            try:
                response = raw_client.chat.completions.create(**kwargs)
            except Exception as exc:
                if result_path.exists():
                    partial = json.loads(result_path.read_text())
                    status_code = getattr(exc, "status_code", None)
                    partial.setdefault("inflight_model_calls", []).append({
                        "sequence": len(partial.get("inflight_model_calls", [])) + 1,
                        "completed": False,
                        "provider_status_code": status_code,
                        "error_type": type(exc).__name__,
                    })
                    terminal = "insufficient_credit" if status_code == 402 else "provider_error"
                    partial["status"] = partial["terminal_status"] = terminal
                    atomic_json(result_path, partial)
                raise
            if result_path.exists():
                from guiexp_android.agent import AndroidAgent
                partial = json.loads(result_path.read_text())
                usage = AndroidAgent._usage(response)
                partial.setdefault("inflight_model_calls", []).append({
                    "sequence": len(partial.get("inflight_model_calls", [])) + 1,
                    "completed": True,
                    "model": kwargs.get("model"),
                    "usage": usage,
                    "cost_usd": usage.get("cost_usd"),
                })
                atomic_json(result_path, partial)
            return response

    class _CheckpointingClient:
        class _Chat:
            completions = _CheckpointingCompletions()
        chat = _Chat()

    client = _CheckpointingClient()
    active_source = {"text": program_path.read_text()}

    def checkpoint(record):
        if result_path.exists():
            existing = json.loads(result_path.read_text())
            if existing.get("inflight_model_calls"):
                record["inflight_model_calls"] = existing["inflight_model_calls"]
        atomic_json(result_path, record)

    replay_number = {"value": 0}

    def replay(source, binding, truth, timeout_s):
        active_source["text"] = source
        replay_number["value"] += 1
        work = out / "_replay_work" / f"replay_{replay_number['value']:04d}"
        work.mkdir(parents=True, exist_ok=True)
        source_file = work / "program.py"
        input_file = work / "input.json"
        output_file = work / "outcome.json"
        source_file.write_text(source)
        input_file.write_bytes(pickle.dumps({"binding": binding, "truth": truth}, protocol=5))
        atomic_json(heartbeat, {"phase": "replay", "started_epoch": time.time(),
                                "deadline_epoch": time.time() + timeout_s})
        command = [sys.executable, str(Path(__file__).resolve()), "--replay-worker",
                   "--source-cell", str(source_cell), "--model", args.model,
                   "--family", args.family, "--attempt-id", args.attempt_id,
                   "--attempt-role", args.attempt_role, "--price-sheet-id", args.price_sheet_id,
                   "--code-bundle-id", args.code_bundle_id,
                   "--out", str(out), "--console-port", str(args.console_port),
                   "--grpc-port", str(args.grpc_port), "--avd", args.avd,
                   "--replay-source", str(source_file), "--replay-input", str(input_file),
                   "--replay-output", str(output_file)]
        replay_proc = subprocess.Popen(command, start_new_session=True)
        try:
            replay_proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(replay_proc.pid, signal.SIGKILL)
            replay_proc.wait()
            trace_path = output_file.with_suffix(".trace.json")
            trace = json.loads(trace_path.read_text()) if trace_path.exists() else []
            # The next replay/repair resets the original task through
            # ProgramRunner before touching the generated program again.
            outcome = {"passed": False, "error": f"replay timeout after {timeout_s:g}s",
                       "error_type": "replay_timeout", "trace": trace, "screen": "",
                       "wall_s": timeout_s}
        else:
            if replay_proc.returncode != 0 or not output_file.exists():
                outcome = {"passed": False, "error": "replay worker failed",
                           "error_type": "worker_error", "trace": [], "screen": ""}
            else:
                outcome = json.loads(output_file.read_text())
        atomic_json(heartbeat, {"phase": "idle", "updated_epoch": time.time()})
        return outcome

    def repair(source, task, conclusions):
        instance = {"seed": task["seed"], "params": task["truth_params"],
                    "binding": task["binding"], "goal": task["prompt"]}
        atomic_json(heartbeat, {"phase": "repair", "updated_epoch": time.time()})
        failure = {"instance": instance, "error": task["failure"].get("error"),
                   "trace": task["failure"].get("trace", []),
                   "screen": task["failure"].get("screen", "")}
        analysis = analyze(args.model, args.family, failure, client=client)
        # The guarded replay ran in a child attached to the same AVD.  The
        # coordinator environment has no current task, so restart the exact
        # original task before react_resume.  Continuation from a child-owned
        # breakpoint is not valid across this process boundary.
        original_task = android_env.AndroidTask(
            family=args.family, condition="discover", seed=task["seed"],
            params=task["truth_params"])
        env.reset(original_task)
        analysis["decision"] = "restart"
        resume = react_resume(args.model, env, args.family, failure, analysis,
                              client=client)
        hybrid = {"binding": task["binding"], "goal": task["prompt"],
                  "failure": failure["error"],
                  "program_trace": failure["trace"],
                  "breakpoint_screen": failure["screen"],
                  "analysis": analysis.get("text", ""),
                  "resume_actions": resume["actions"], "resume_success": resume["success"]}
        refined = refine_artifact(args.model, args.family, source, hybrid, conclusions,
                                  client=client)
        calls = [{"usage": analysis["usage"]}, *resume.get("calls_detail", []),
                 {"usage": refined["usage"]}]
        return {"status": "ok", "source": refined["artifact_text"],
                "cause": analysis.get("cause"), "calls": calls,
                "resume_success": resume["success"]}

    try:
        records = [source_record]
        if translation_path.exists():
            records.append(translation_path)
        resume_result = None
        if result_path.exists():
            candidate_resume = json.loads(result_path.read_text())
            if candidate_resume.get("terminal_status") == "running":
                prior_bundle = candidate_resume.get("code_bundle_id") or "mutable-v1"
                if prior_bundle != args.code_bundle_id and candidate_resume.get("candidate_history"):
                    candidate_resume.setdefault("invalidated_candidate_history", []).append({
                        "source_bundle_id": prior_bundle,
                        "transition_bundle_id": args.code_bundle_id,
                        "reason": "candidate executed before typed replay payload and repair-state initialization fixes",
                        "candidates": candidate_resume["candidate_history"],
                        "promotable": False,
                    })
                    candidate_resume["candidate_history"] = []
                resume_result = candidate_resume
        result = run_attempt(
            model=args.model, family=args.family, attempt_id=args.attempt_id,
            attempt_role=args.attempt_role, price_sheet_id=args.price_sheet_id,
            code_bundle_id=args.code_bundle_id,
            source_path=program_path, source_record_paths=records,
            trajectory_paths=trajectories, client=client, replay=replay, repair=repair,
            reused_charges={"source_record": str(source_record),
                            "translator": record.get("translator"),
                            "builder_initial": (record.get("builder") or {}).get("initial")},
            checkpoint=checkpoint,
            source_identity={"family": record["family"], "seeds": record["seeds"],
                             "path": str(source_record),
                             "sha256": __import__("hashlib").sha256(source_record.read_bytes()).hexdigest(),
                             "trajectory_resolution": trajectory_resolution},
            resume_result=resume_result,
        )
        if result.get("final_program_source"):
            (out / "final_program.py").write_text(result["final_program_source"])
        if result.get("admitted"):
            historical_program = source_cell / "verified_program.py"
            historical_deploy = source_cell / "deploy.json"
            historical_hash = None
            if historical_program.exists():
                import hashlib
                historical_hash = hashlib.sha256(historical_program.read_bytes()).hexdigest()
            equal = historical_hash == result.get("final_program_sha256")
            result["deployment"].update({
                "historical_program_sha256": historical_hash,
                "final_program_sha256": result.get("final_program_sha256"),
                "program_hash_equal": equal,
                # The historical Android deploy runner uses the same extraction
                # prompt/type boundary and ProgramRunner/oracle as this revision.
                "execution_protocol_equal": True,
                "input_protocol_equal": True,
            })
            if equal and historical_deploy.exists():
                result["deployment"].update({
                    "status": "reused",
                    "reuse_justification": "byte-identical historical deployed program; unchanged execution and input protocols",
                    "record_paths": [str(historical_deploy)],
                })
            else:
                _module, deployed_program = program_from_source(result["final_program_source"])
                class GuardedDeploymentRunner:
                    def run(self, _program, binding, family, judge_params=None, **_kwargs):
                        if family != args.family:
                            raise ValueError("deployment family mismatch")
                        return replay(result["final_program_source"], binding, judge_params,
                                      REPLAY_TIMEOUT_S)
                deployment = run_deployment(
                    deployed_program, args.family, deploy_uses(args.family, 30), client,
                    args.model, GuardedDeploymentRunner(),
                    role="trace-verifier-v1-changed-program",
                )
                deployment_path = out / "deploy30.json"
                atomic_json(deployment_path, deployment)
                result["deployment"].update({
                    "status": "new_30_use",
                    "reuse_justification": None,
                    "record_paths": [str(deployment_path)],
                    "n": deployment["n"],
                    "success_count": deployment["success_count"],
                })
            checkpoint(result)
        return 0 if result["status"] == "complete" else 2
    finally:
        env.close()


def replay_worker(args) -> int:
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "code"))
    from guiexp_android import android_env
    from guiexp_android.expa.lane import configure_lane
    from guiexp_android.program_runtime import ProgramRunner, program_from_path
    from guiexp_android.verify_runner import TracingDevice, screen_text

    configure_lane(args.console_port, args.grpc_port, args.avd)
    payload = pickle.loads(Path(args.replay_input).read_bytes())
    env = android_env.AndroidWorldEnv(console_port=args.console_port, grpc_port=args.grpc_port)
    holder = {}
    trace_path = Path(args.replay_output).with_suffix(".trace.json")
    try:
        _module, program = program_from_path(args.replay_source)
        class PersistentTracingDevice(TracingDevice):
            def _persist(self):
                trace_path.write_text(json.dumps(self.trace))
            def find(self, **kwargs):
                try:
                    return super().find(**kwargs)
                finally:
                    self._persist()
            def execute(self, action):
                try:
                    return super().execute(action)
                finally:
                    self._persist()
        def factory(inner_env):
            holder["device"] = PersistentTracingDevice(inner_env)
            return holder["device"]
        started = time.monotonic()
        outcome = ProgramRunner(env).run(program, payload["binding"], args.family,
                                         judge_params=payload["truth"],
                                         device_factory=factory, timeout_s=0)
        device = holder.get("device")
        serial = {"passed": outcome["passed"], "error": outcome.get("error"),
                  "error_type": "program_error" if outcome.get("error") else
                                (None if outcome["passed"] else "oracle_fail"),
                  "trace": list(getattr(device, "trace", [])),
                  "screen": screen_text(device) if device is not None else "",
                  "wall_s": round(time.monotonic() - started, 3)}
        atomic_json(Path(args.replay_output), serial)
        return 0
    finally:
        env.close()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--worker", action="store_true")
    p.add_argument("--replay-worker", action="store_true")
    p.add_argument("--source-cell", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--family", required=True)
    p.add_argument("--attempt-id", required=True)
    p.add_argument("--attempt-role", choices=("initial", "repeat"), required=True)
    p.add_argument("--price-sheet-id", required=True)
    p.add_argument("--code-bundle-id", required=True)
    p.add_argument("--private-env-file")
    p.add_argument("--out", required=True)
    p.add_argument("--console-port", type=int, required=True)
    p.add_argument("--grpc-port", type=int, required=True)
    p.add_argument("--avd", required=True)
    p.add_argument("--replay-source")
    p.add_argument("--replay-input")
    p.add_argument("--replay-output")
    return p.parse_args()


if __name__ == "__main__":
    parsed = parse_args()
    if parsed.replay_worker:
        raise SystemExit(replay_worker(parsed))
    raise SystemExit(worker(parsed) if parsed.worker else supervisor(parsed))
