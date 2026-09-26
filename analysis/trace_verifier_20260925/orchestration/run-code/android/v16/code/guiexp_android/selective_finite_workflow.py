"""Run a frozen, finite list of local selective Android jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from .selective_supervisor import atomic_json

ROOT = Path(__file__).resolve().parents[2]
WORKDIR = ROOT / "computer-use"
PYTHON = "../.venv-android/bin/python"
SUPERVISOR_MODULE = "guiexp_android.selective_projected_supervisor"
DEFAULT_HARNESS_MODULE = "guiexp_android.selective_projected"
REPAIR_DIAGNOSTIC_MODULE = "guiexp_android.selective_repair_diagnostic"
HARNESS_MODULES = frozenset((DEFAULT_HARNESS_MODULE, REPAIR_DIAGNOSTIC_MODULE))
SCHEMA = "selective-finite-workflow/1"


class WorkflowError(RuntimeError):
    """A local guard stopped preparation or execution."""


def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"cannot read JSON {path.resolve()}") from exc


def supervisor_command(job: Mapping[str, str]) -> list[str]:
    return [PYTHON, "-m", SUPERVISOR_MODULE, "--phase", "run", "--harness-module",
            job["harness_module"], "--out", str(Path(job["out"]).resolve()), "--notify-events"]


def analysis_command(job: Mapping[str, str]) -> list[str]:
    return [PYTHON, "-m", job["harness_module"], "--analyze", "--out", str(Path(job["out"]).resolve())]


def _jobs(value: Any, workflow: Path) -> list[dict[str, str]]:
    if isinstance(value, Mapping) and isinstance(value.get("jobs"), list):
        value = value["jobs"]
    if not isinstance(value, list) or not value:
        raise WorkflowError("jobs-json must contain a nonempty list")
    result, seen = [], set()
    for number, item in enumerate(value, 1):
        if not isinstance(item, Mapping) or set(item) - {"out", "harness_module"}:
            raise WorkflowError(f"job {number} has invalid fields")
        value = item.get("out")
        if not isinstance(value, str) or not value:
            raise WorkflowError(f"job {number} out is required")
        out = Path(value).expanduser().resolve()
        if out == workflow or workflow in out.parents or not out.is_dir() or not (out / "spec.json").is_file():
            raise WorkflowError(f"job {number} out is not an existing version directory: {out}")
        harness = item.get("harness_module", DEFAULT_HARNESS_MODULE)
        if not isinstance(harness, str) or harness not in HARNESS_MODULES:
            raise WorkflowError(f"job {number} has an unsupported harness module")
        if str(out) in seen:
            raise WorkflowError(f"job {number} repeats a version directory")
        seen.add(str(out)); result.append({"out": str(out), "harness_module": harness})
    return result


def _source_paths(jobs: Sequence[Mapping[str, str]]) -> list[Path]:
    here = Path(__file__).resolve()
    paths = [here, here.with_name("selective_projected_supervisor.py"), here.with_name("selective_projected.py")]
    if any(job["harness_module"] == REPAIR_DIAGNOSTIC_MODULE for job in jobs):
        paths.append(here.with_name("selective_repair_diagnostic.py"))
    return paths


def _files(jobs_path: Path, jobs: Sequence[Mapping[str, str]], workflow: Path) -> list[dict[str, str]]:
    paths = [("job_list", jobs_path)]
    for number, job in enumerate(jobs, 1):
        out = Path(job["out"])
        paths.append((f"job_{number}_spec", out / "spec.json"))
        paths += [(f"job_{number}_phase", p) for p in sorted(out.glob("phase_*.json"))]
    paths += [("workflow_jobs", workflow / "jobs.json"), ("phase_configs", workflow / "phase_configs.json")]
    paths += [("source", p) for p in _source_paths(jobs)]
    entries, seen = [], set()
    for role, path in paths:
        path = path.resolve()
        if str(path) in seen:
            continue
        if not path.is_file():
            raise WorkflowError(f"required frozen file is missing: {path}")
        seen.add(str(path)); entries.append({"role": role, "path": str(path), "sha256": _hash(path)})
    return entries


def _phase_configs(jobs: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    return {"schema": f"{SCHEMA}/phases", "cwd": str(WORKDIR.resolve()), "jobs": [
        {"job_number": i, "run": supervisor_command(job), "analyze": analysis_command(job)}
        for i, job in enumerate(jobs, 1)]}


def prepare(jobs_json: Path | str, out: Path | str) -> dict[str, Any]:
    jobs_path, workflow = Path(jobs_json).expanduser().resolve(), Path(out).expanduser().resolve()
    if not jobs_path.is_file():
        raise WorkflowError(f"jobs-json does not exist: {jobs_path}")
    if workflow.exists() and (not workflow.is_dir() or any(workflow.iterdir())):
        raise WorkflowError("workflow out is not empty")
    if jobs_path == workflow or workflow in jobs_path.parents:
        raise WorkflowError("jobs-json must be outside workflow out")
    workflow.mkdir(parents=True, exist_ok=True)
    jobs = _jobs(_read(jobs_path), workflow)
    atomic_json(workflow / "jobs.json", jobs)
    configs = _phase_configs(jobs); atomic_json(workflow / "phase_configs.json", configs)
    body = {"schema": SCHEMA, "version": 1, "jobs": jobs,
            "jobs_json": {"path": str(jobs_path), "sha256": _hash(jobs_path)},
            "phase_configs": configs, "frozen_files": _files(jobs_path, jobs, workflow),
            "canonical": {"cwd": str(WORKDIR.resolve()), "interpreter": PYTHON,
                          "normal_completion": "local_record_only", "halt_without_retry_or_kill": True}}
    spec = dict(body, workflow_spec_sha256=hashlib.sha256(_canon(body)).hexdigest())
    atomic_json(workflow / "workflow_spec.json", spec)
    status = {"schema": SCHEMA, "status": "prepared", "workflow_pid": None, "started_unix": None,
              "job_number": None, "current_job_number": None, "next_job_number": 1,
              "supervisor_pid": None, "analysis_pid": None, "supervisor_exit_code": None, "analysis_exit_code": None,
              "jobs_total": len(jobs), "completed_jobs": 0,
              "jobs": [{"job_number": i, **job, "status": "pending", "supervisor_pid": None,
                        "supervisor_exit_code": None, "analysis_pid": None, "analysis_exit_code": None}
                       for i, job in enumerate(jobs, 1)]}
    atomic_json(workflow / "workflow_status.json", status)
    (workflow / "workflow.log").write_text(f"prepared jobs={len(jobs)}\n", encoding="utf-8")
    return spec


def _verify(spec: Mapping[str, Any]) -> None:
    for entry in spec.get("frozen_files", []):
        path = Path(entry["path"])
        if not path.is_file() or _hash(path) != entry["sha256"]:
            raise WorkflowError(f"frozen file changed: {path}")


def _fresh(path: Path, started: float, before: str | None) -> bool:
    return path.is_file() and path.stat().st_mtime >= started - 1 and (before is None or _hash(path) != before)


def _claim(workflow: Path) -> None:
    try:
        fd = os.open(workflow / ".run.started", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
    except FileExistsError as exc:
        raise WorkflowError("workflow was already started and cannot be resumed") from exc


def _child(command: Sequence[str], log: Path, runner: Any) -> dict[str, Any]:
    try:
        result = runner(list(command), cwd=WORKDIR, capture_output=True, text=True, check=False)
        output = (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")
        log.parent.mkdir(parents=True, exist_ok=True); log.write_text("$ " + shlex.join(list(command)) + "\n" + output, encoding="utf-8")
        return {"pid": getattr(result, "pid", None), "exit_code": getattr(result, "returncode", None)}
    except BaseException as exc:
        log.parent.mkdir(parents=True, exist_ok=True); log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return {"pid": None, "exit_code": None, "error": str(exc), "error_type": type(exc).__name__}


def run(out: Path | str, *, runner: Any = subprocess.run) -> dict[str, Any]:
    workflow = Path(out).expanduser().resolve(); spec = _read(workflow / "workflow_spec.json"); status = _read(workflow / "workflow_status.json")
    if not isinstance(spec, Mapping) or hashlib.sha256(_canon({k: v for k, v in spec.items() if k != "workflow_spec_sha256"})).hexdigest() != spec.get("workflow_spec_sha256"):
        raise WorkflowError("workflow specification hash changed")
    if not isinstance(status, Mapping) or status.get("status") != "prepared":
        raise WorkflowError("workflow was already started and cannot be resumed")
    try:
        _verify(spec)
    except WorkflowError as exc:
        status = dict(status, status="halted", halt_reason="frozen_input_changed", error=str(exc), started_unix=time.time())
        atomic_json(workflow / "workflow_status.json", status); raise
    _claim(workflow)
    jobs = spec["jobs"]
    state = dict(status, status="running", workflow_pid=os.getpid(), started_unix=time.time(), jobs=[
        dict(job, job_number=i, status="pending", supervisor_pid=None, supervisor_exit_code=None,
             analysis_pid=None, analysis_exit_code=None) for i, job in enumerate(jobs, 1)])
    atomic_json(workflow / "workflow_status.json", state)
    for index, job in enumerate(jobs, 1):
        try:
            _verify(spec)
        except WorkflowError as exc:
            state.update(status="halted", halt_reason="frozen_input_changed", error=str(exc)); atomic_json(workflow / "workflow_status.json", state); raise
        row = state["jobs"][index - 1]; state.update(job_number=index, current_job_number=index, next_job_number=index)
        status_path = Path(job["out"]) / "batch_status.run.json"
        row.update(status="supervisor_running", batch_status_path=str(status_path.resolve()))
        atomic_json(workflow / "workflow_status.json", state)
        before = _hash(status_path) if status_path.is_file() else None; started = time.time()
        supervisor = _child(supervisor_command(job), workflow / "logs" / f"job-{index:04d}.supervisor.log", runner)
        try:
            record = _read(status_path) if _fresh(status_path, started, before) else None
        except WorkflowError as exc:
            record = None; supervisor["error"] = str(exc); supervisor["error_type"] = "MalformedSupervisorStatus"
        row.update(supervisor_pid=(record or {}).get("pid") or (record or {}).get("supervisor_pid") or supervisor["pid"], supervisor_exit_code=supervisor["exit_code"])
        state.update(supervisor_pid=row["supervisor_pid"], supervisor_exit_code=row["supervisor_exit_code"])
        event_files = list((Path(job["out"]) / "events").glob("*.json"))
        bad = isinstance(record, Mapping) and (any(record.get(key) for key in ("error", "error_type", "budget_stopped", "unknown", "interrupted")) or record.get("exit_code") not in (None, 0))
        good = isinstance(record, Mapping) and record.get("status") == "completed" and not bad and not record.get("event_key") and not event_files
        if supervisor.get("error_type") or supervisor.get("exit_code") != 0 or not good:
            state.update(status="halted", halt_reason="supervisor_failure_event" if event_files or (record or {}).get("event_key") else "supervisor_failure", error=supervisor.get("error") or "nonzero, unknown, or invalid supervisor result"); row["status"] = "halted"; atomic_json(workflow / "workflow_status.json", state); return state
        row["status"] = "analyzing"; atomic_json(workflow / "workflow_status.json", state)
        analysis = _child(analysis_command(job), workflow / "logs" / f"job-{index:04d}.analysis.log", runner)
        row.update(analysis_pid=analysis["pid"], analysis_exit_code=analysis["exit_code"])
        state.update(analysis_pid=row["analysis_pid"], analysis_exit_code=row["analysis_exit_code"])
        if analysis.get("error_type") or analysis.get("exit_code") != 0:
            state.update(status="halted", halt_reason="analysis_failure", error=analysis.get("error") or "nonzero analyzer result"); row["status"] = "halted"; atomic_json(workflow / "workflow_status.json", state); return state
        row["status"] = "completed"; state["completed_jobs"] = index; atomic_json(workflow / "workflow_status.json", state)
    state.update(status="completed", ended_unix=time.time()); atomic_json(workflow / "workflow_status.json", state); return state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true"); group.add_argument("--run", action="store_true")
    parser.add_argument("--jobs-json", type=Path); parser.add_argument("--out", type=Path, required=True); args = parser.parse_args(argv)
    if args.prepare and args.jobs_json is None: parser.error("--prepare requires --jobs-json")
    if args.run and args.jobs_json is not None: parser.error("--jobs-json is supported only with --prepare")
    try:
        result = prepare(args.jobs_json, args.out) if args.prepare else run(args.out); print(json.dumps({"status": result.get("status", "prepared")})); return 0 if result.get("status", "prepared") in {"prepared", "completed"} else 2
    except WorkflowError as exc:
        print(f"WORKFLOW STOPPED: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
