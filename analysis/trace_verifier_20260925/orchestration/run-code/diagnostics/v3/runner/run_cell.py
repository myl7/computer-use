#!/usr/bin/env python3
"""Incremental Android t18/t20 diagnostics for one revised initial program."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
_PYTHONPATH_ENTRIES = [Path(item) for item in os.environ.get("PYTHONPATH", "").split(os.pathsep)
                       if item]
CODE = _PYTHONPATH_ENTRIES[0] if _PYTHONPATH_ENTRIES else REPO / "code"
sys.path.insert(0, str(CODE))

PROTOCOL = "three-building-diagnostics-v1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_one(args, mode: str, arm: str | None, index: int, program: Path,
            out: Path) -> dict:
    command = [sys.executable, str(Path(__file__).resolve()), "--worker",
               "--mode", mode, "--family", args.family, "--model", args.model,
               "--program", str(program), "--index", str(index),
               "--console-port", str(args.console_port), "--grpc-port", str(args.grpc_port),
               "--avd", args.avd]
    if arm:
        command += ["--arm", arm]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=args.replay_timeout_s,
                                   env=dict(os.environ))
        if completed.returncode:
            row = {"status": "worker_error", "returncode": completed.returncode,
                    "error": completed.stderr[-1000:], "wall_s": time.monotonic() - started}
        else:
            lines = [line for line in completed.stdout.splitlines() if line.strip()]
            if lines:
                parsed = json.loads(lines[-1])
                reverted = (((parsed.get("entry") or {}).get("fingerprint") or {})
                            .get("reverted_clean"))
                if mode != "t18" or reverted is not False:
                    return parsed
                row = {"status": "restoration_failed", "invalidated_result": parsed,
                       "error": "perturbation fingerprint did not return to pre-run state"}
            row = {"status": "worker_error", "returncode": completed.returncode,
                    "error": "worker produced no JSON result",
                    "wall_s": round(time.monotonic() - started, 3)}
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for the worker before returning.
        row = {"status": "replay_timeout", "error":
                f"external replay timeout after {args.replay_timeout_s}s",
                "wall_s": round(time.monotonic() - started, 3)}
    if not args.recovery_command:
        row["recovery"] = {"status": "missing_recovery_command"}
        return row
    recovered = subprocess.run([args.recovery_command], capture_output=True, text=True,
                               timeout=300)
    row["recovery"] = {"status": "complete" if recovered.returncode == 0 else "failed",
                       "returncode": recovered.returncode,
                       "output_tail": (recovered.stdout + recovered.stderr)[-1000:]}
    return row


def worker(args) -> int:
    from guiexp_android.expa.lane import configure_lane, load_env_file, make_client

    configure_lane(args.console_port, args.grpc_port, args.avd)
    from guiexp_android import android_env, perturb
    from guiexp_android.fragility_probe import probe_bindings, run_arm
    from guiexp_android.gate_runner import GATE_K, heldout_bindings
    from guiexp_android.program_runtime import ProgramRunner, program_from_path

    _module, program = program_from_path(args.program)
    env = android_env.AndroidWorldEnv(console_port=args.console_port, grpc_port=args.grpc_port)
    started = time.monotonic()
    try:
        runner = ProgramRunner(env)
        if args.mode == "t18":
            draw = probe_bindings(args.family, 5, 0, "gate")[args.index]
            arm = perturb.select_arms([args.arm])[0]
            adb = perturb.Adb(serial=os.environ.get("ANDROID_SERIAL"))
            entry = run_arm(program, args.family, [draw], runner, arm, adb, verbose=False)
            result = {"status": "complete", "mode": "t18", "arm": args.arm,
                      "binding_index": args.index, "entry": entry,
                      "model_calls": 0, "cost_usd": 0.0}
        else:
            load_env_file()
            from guiexp_android.deploy_runner import run_single_use

            exclude = android_env.instance_params(args.family, 1)
            draw = heldout_bindings(args.family, k=GATE_K, exclude_params=exclude)[args.index]
            task = android_env.AndroidTask(args.family, "discover", 0, draw["params"])
            goal = android_env.goal_text(task)
            direct = runner.run(program, draw["binding"], args.family,
                                judge_params=draw["params"])
            use = run_single_use(program, args.family, goal, draw["binding"],
                                 draw["params"], make_client(), args.model, runner)
            result = {"status": "complete", "mode": "t20", "binding_index": args.index,
                      "goal": goal, "binding": draw["binding"],
                      "direct": {"passed": direct["passed"], "error": direct.get("error")},
                      "extraction": use, "model_calls": len(use.get("calls_detail") or []),
                      "cost_usd": use.get("cost_usd") or 0.0}
    finally:
        env.close()
    result["wall_s"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result")
    parser.add_argument("--out")
    parser.add_argument("--mode", choices=("t18", "t20"))
    parser.add_argument("--family")
    parser.add_argument("--model")
    parser.add_argument("--program")
    parser.add_argument("--arm")
    parser.add_argument("--index", type=int)
    parser.add_argument("--console-port", type=int, default=8644)
    parser.add_argument("--grpc-port", type=int, default=8646)
    parser.add_argument("--avd", default="traceVerifier-w8")
    parser.add_argument("--replay-timeout-s", type=int, default=1200)
    parser.add_argument("--recovery-command")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        return worker(args)

    source_result = Path(args.result)
    record = json.loads(source_result.read_text())
    if record.get("terminal_status") != "complete" or not record.get("admitted"):
        raise SystemExit("diagnostics require a completed admitted initial result")
    args.family, args.model = record["family"], record["model"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    program = out / "selected_program.py"
    program.write_text(record["final_program_source"])
    if digest(program.read_bytes()) != record["final_program_sha256"]:
        raise SystemExit("materialized program hash mismatch")
    manifest = {"protocol_id": PROTOCOL, "platform": "android",
                "model": args.model, "family": args.family,
                "source_result": str(source_result),
                "source_result_sha256": digest(source_result.read_bytes()),
                "program_sha256": digest(program.read_bytes()),
                "replay_timeout_s": args.replay_timeout_s,
                "reset_policy": "fresh worker and family reset for every binding/condition",
                "judge_policy": "original draw params, independent of extracted binding",
                "results": {"t18": [], "t20": []}, "infrastructure_events": [],
                "record_type": "diagnostics"}
    if args.resume and (out / "result.json").is_file():
        previous = json.loads((out / "result.json").read_text())
        if previous.get("program_sha256") != manifest["program_sha256"]:
            raise SystemExit("resume program hash mismatch")
        for row in (previous.get("results") or {}).get("t18") or []:
            reverted = (((row.get("entry") or {}).get("fingerprint") or {})
                        .get("reverted_clean"))
            if row.get("status") == "complete" and reverted is not False:
                manifest["results"]["t18"].append(row)
            else:
                manifest["infrastructure_events"].append(
                    {"status": "invalidated_on_resume", "invalidated_result": row})
        manifest["results"]["t20"] = list(
            (previous.get("results") or {}).get("t20") or [])
        manifest["infrastructure_events"].extend(previous.get("infrastructure_events") or [])
    (out / "result.json").write_text(json.dumps(manifest, indent=1))
    from guiexp_android import perturb
    for arm in [item.name for item in perturb.ARMS]:
        for index in range(5):
            if any(row.get("arm") == arm and row.get("binding_index") == index
                   for row in manifest["results"]["t18"]):
                continue
            row = run_one(args, "t18", arm, index, program, out)
            if row.get("status") == "restoration_failed":
                manifest["infrastructure_events"].append(row)
                (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
                if (row.get("recovery") or {}).get("status") != "complete":
                    manifest["terminal_status"] = "environment_recovery_failed"
                    (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
                    return 5
                row = run_one(args, "t18", arm, index, program, out)
            manifest["results"]["t18"].append(row)
            (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
            if row.get("status") == "replay_timeout":
                manifest["terminal_status"] = "timeout_requires_clean_avd_restore"
                (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
                return 4
    for index in range(5):
        if any(row.get("binding_index") == index for row in manifest["results"]["t20"]):
            continue
        row = run_one(args, "t20", None, index, program, out)
        manifest["results"]["t20"].append(row)
        (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
        if row.get("status") == "replay_timeout":
            manifest["terminal_status"] = "timeout_requires_clean_avd_restore"
            (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
            return 4
    manifest["terminal_status"] = "complete"
    manifest["t18_model_calls"] = 0
    manifest["t20_model_calls"] = sum(r.get("model_calls", 0) for r in manifest["results"]["t20"])
    manifest["t20_cost_usd"] = round(sum(r.get("cost_usd", 0.0) for r in manifest["results"]["t20"]), 10)
    (out / "result.json").write_text(json.dumps(manifest, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
