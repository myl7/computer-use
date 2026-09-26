"""Small local supervisor for the provider-v3 selective explore tranche.

The explore harness is intentionally selected by this wrapper instead of by
the older pilot supervisor.  A phase is started once with the canonical
relative interpreter, observed through local files, and left terminal when it
exits.  This module never retries, kills, or starts the next phase.
"""

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
from typing import Any, Callable, Mapping, Sequence

from . import selective_supervisor as base


MODULE = "guiexp_android.selective_explore"
PYTHON = "../.venv-android/bin/python"
PHASES = ("build", "run")
STALE_SECONDS = base.STALE_SECONDS
DEFAULT_INTERVAL = 30.0
AUTHORIZATION_PATH = base.OUT / "budget_authorization_request_20260915.json"
TOTAL_OCCUPIED_CEILING_USD = "20"
NEW_TRANCHE_CEILING_USD = "10"


def explore_command(
    phase: str,
    out: Path | str,
    *,
    max_families: int | None = None,
    max_episodes: int | None = None,
) -> list[str]:
    """Build the canonical explore command and its explicit output path."""

    if phase not in PHASES:
        raise ValueError(f"unsupported explore phase: {phase!r}")
    if max_families is not None:
        if phase != "build" or isinstance(max_families, bool) or max_families < 1:
            raise ValueError("max_families is supported only for build")
    if max_episodes is not None:
        if phase != "run" or isinstance(max_episodes, bool) or max_episodes < 1:
            raise ValueError("max_episodes is supported only for run")
    command = [PYTHON, "-m", MODULE, f"--{phase}"]
    if max_families is not None:
        command.extend(["--max-families", str(max_families)])
    if max_episodes is not None:
        command.extend(["--max-episodes", str(max_episodes)])
    command.extend(["--out", str(Path(out).absolute())])
    return command


def _status_path(out: Path, phase: str) -> Path:
    return Path(out) / f"batch_status.{phase}.json"


def _history_path(out: Path, phase: str) -> Path:
    return Path(out) / f"batch_status.{phase}.history.jsonl"


def _event_path(out: Path, key: str) -> Path:
    return Path(out) / "events" / f"{key}.json"


def _fresh(path: Path, started: float) -> bool:
    try:
        return path.stat().st_mtime >= started - 1.0
    except OSError:
        return False


def _phase_assessment(
    out: Path,
    phase: str,
    *,
    started: float,
    max_families: int | None,
    max_episodes: int | None,
) -> dict[str, Any]:
    """Read explore artifacts so exit 0 cannot imply missing work."""

    if phase == "build":
        path = Path(out) / "build_manifest.json"
        record = base.read_json(path)
        if not isinstance(record, Mapping):
            return {
                "complete": False,
                "completion_status": "incomplete",
                "reason": "missing_build_manifest",
                "message": f"{path.name} is missing or unreadable",
            }
        status = record.get("status")
        if status == "pilot_limit" and max_families is not None and _fresh(path, started):
            return {
                "complete": False,
                "completion_status": "bounded",
                "reason": "build_pilot_limit",
                "artifact": str(path),
                "status": status,
                "max_families": max_families,
            }
        families = record.get("families")
        if not isinstance(families, Mapping) or not families:
            return {
                "complete": False,
                "completion_status": "incomplete",
                "reason": "empty_build_manifest",
                "message": f"{path.name} has no family results",
            }
        statuses = {
            str(family): str(item.get("status")) if isinstance(item, Mapping) else "missing"
            for family, item in families.items()
        }
        terminal = {"built", "already_built", "unbuildable"}
        incomplete = {family: value for family, value in statuses.items() if value not in terminal}
        if incomplete or status not in {"complete", "partial"} or not _fresh(path, started):
            return {
                "complete": False,
                "completion_status": "incomplete",
                "reason": "build_manifest_incomplete",
                "artifact": str(path),
                "manifest_status": status,
                "family_statuses": statuses,
                "incomplete_families": incomplete,
            }
        failures = {family: value for family, value in statuses.items() if value == "unbuildable"}
        return {
            "complete": True,
            "completion_status": "completed_with_failures" if failures else "completed",
            "artifact": str(path),
            "manifest_status": status,
            "family_statuses": statuses,
            "scientific_failures": failures,
        }

    path = Path(out) / "progress.json"
    record = base.read_json(path)
    if not isinstance(record, Mapping):
        return {
            "complete": False,
            "completion_status": "incomplete",
            "reason": "missing_progress",
            "message": f"{path.name} is missing or unreadable",
        }
    status = record.get("batch_status") or record.get("status")
    if status == "pilot_limit" and max_episodes is not None and _fresh(path, started):
        return {
            "complete": False,
            "completion_status": "bounded",
            "reason": "run_pilot_limit",
            "artifact": str(path),
            "status": status,
            "max_episodes": max_episodes,
        }
    rows = record.get("episodes")
    rows = rows if isinstance(rows, list) else []
    counts = record.get("counts")
    counts = dict(counts) if isinstance(counts, Mapping) else {}
    pending = {key: value for key, value in counts.items() if key in {"pending", "running"} and value}
    if status != "complete" or not rows or pending or not _fresh(path, started):
        return {
            "complete": False,
            "completion_status": "incomplete",
            "reason": "progress_incomplete",
            "artifact": str(path),
            "batch_status": status,
            "planned": record.get("planned"),
            "observed": len(rows),
            "counts": counts,
        }
    row_statuses: dict[str, int] = {}
    for row in rows:
        value = row.get("status") if isinstance(row, Mapping) else None
        value = str(value) if value else "missing"
        row_statuses[value] = row_statuses.get(value, 0) + 1
    bad = {key: value for key, value in row_statuses.items() if key not in {"done", "skipped_unbuildable"}}
    skipped = {key: value for key, value in row_statuses.items() if key == "skipped_unbuildable"}
    if bad:
        return {
            "complete": False,
            "completion_status": "incomplete",
            "reason": "progress_incomplete",
            "artifact": str(path),
            "batch_status": status,
            "row_statuses": row_statuses,
            "incomplete_statuses": bad,
        }
    return {
        "complete": True,
        "completion_status": "completed_with_failures" if skipped else "completed",
        "artifact": str(path),
        "batch_status": status,
        "planned": record.get("planned"),
        "observed": len(rows),
        "row_statuses": row_statuses,
        "scientific_failures": skipped,
    }


def _id(phase: str, now: float) -> str:
    raw = f"{phase}|{os.getpid()}|{now}|{time.time_ns()}"
    return f"{phase}-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def _append_history(out: Path, phase: str, item: Mapping[str, Any]) -> None:
    path = _history_path(out, phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(dict(item), handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _emit_event(
    event: Mapping[str, Any],
    out: Path,
    *,
    notify_events: bool,
    sender: Callable[..., Any],
) -> bool:
    """Record one event, with an optional delivery through the new auth path."""

    path = _event_path(out, str(event["event_key"]))
    if path.exists():
        return False
    if notify_events:
        return dispatch_event(event, out, sender=sender)
    base.atomic_json(path, dict(event, delivery="not_requested"))
    return True


def _event_prompt(event: Mapping[str, Any], path: Path) -> str:
    return (
        f"Provider-v3 exception: phase={event.get('phase')}, "
        f"reason={event.get('reason')}. Read {path}, the latest status and "
        f"{event.get('latest_log', {}).get('path', 'phase log')} before acting. "
        f"The exact output directory is {event.get('output_dir')}; verify its "
        f"frozen spec SHA256 {event.get('spec_sha256')} and the real supervisor "
        "and worker PIDs. Check the shared runner lock and read the shared "
        f"ledger at {base.SHARED_LEDGER} in read-only mode. The authorization "
        f"record is {AUTHORIZATION_PATH}. The current authorization is a USD "
        f"{TOTAL_OCCUPIED_CEILING_USD} occupied ceiling with a USD "
        f"{NEW_TRANCHE_CEILING_USD} new-tranche ceiling. Preserve all receipts "
        "and ambiguous outcomes. Do not replay UI episodes, blindly resume, "
        "start a replacement automatically, or create a scheduled message, "
        "heartbeat, or periodic model poll."
    )


def dispatch_event(
    event: Mapping[str, Any],
    out: Path,
    *,
    sender: Callable[..., Any] = subprocess.run,
    codex: str = base.CODEX,
) -> bool:
    """Persist one deduplicated provider-v3 exception before queue delivery."""

    path = _event_path(out, str(event.get("event_key")))
    if path.exists():
        return False
    record = dict(event, delivery="dispatching")
    base.atomic_json(path, record)
    try:
        result = sender(
            [codex, "queue", "--thread", base.THREAD, "--message", _event_prompt(record, path)],
            cwd=base.ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        record["delivery"] = "queued" if getattr(result, "returncode", 1) == 0 else "failed"
        record["queue_exit_code"] = getattr(result, "returncode", None)
    except subprocess.TimeoutExpired:
        record["delivery"] = "unknown_timeout"
    except Exception as exc:
        record["delivery"] = "failed"
        record["error_type"] = type(exc).__name__
    base.atomic_json(path, record)
    return True


def _latest_log(out: Path, phase: str) -> dict[str, Any]:
    path = Path(out) / f"{phase}.log"
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {"path": str(path), "exists": True, "mtime_unix": stat.st_mtime, "size_bytes": stat.st_size}


def _event_payload(
    state: Mapping[str, Any],
    out: Path,
    *,
    reason: str,
    source: str,
    now: float,
    **extra: Any,
) -> dict[str, Any]:
    """Build a recovery record with the exact new-tranche context."""

    payload = {
        "event_key": f"{state['phase']}-{hashlib.sha256((state['invocation_id'] + '|' + reason).encode()).hexdigest()[:24]}",
        "phase": state.get("phase"),
        "reason": reason,
        "source": source,
        "output_dir": str(Path(out).resolve()),
        "spec_path": str(Path(out) / "spec.json"),
        "spec_sha256": state.get("spec_sha256"),
        "module": MODULE,
        "command": state.get("command"),
        "worker_pid": state.get("worker_pid"),
        "supervisor_pid": state.get("pid"),
        "latest_batch_status": base.read_json(_status_path(out, str(state.get("phase"))), {}),
        "latest_log": _latest_log(out, str(state.get("phase"))),
        "shared_ledger": str(base.SHARED_LEDGER),
        "shared_worker_lock": str(base.OLD_OUT / "run.lock"),
        "authorization_request": str(AUTHORIZATION_PATH),
        "total_occupied_ceiling_usd": TOTAL_OCCUPIED_CEILING_USD,
        "new_tranche_occupied_ceiling_usd": NEW_TRANCHE_CEILING_USD,
        "no_ui_replay": True,
        "detected_unix": now,
    }
    payload.update(extra)
    return payload


def supervise(
    phase: str,
    out: Path | str,
    *,
    max_families: int | None = None,
    max_episodes: int | None = None,
    interval: float = DEFAULT_INTERVAL,
    notify_events: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], Any] = time.sleep,
    clock: Any | None = None,
    verifier: Callable[[Any, Sequence[str]], bool] | None = None,
    sender: Callable[..., Any] = subprocess.run,
    once: bool = False,
) -> dict[str, Any]:
    """Start one explore phase and observe it without automatic recovery."""

    if phase not in PHASES:
        raise ValueError(f"unsupported explore phase: {phase!r}")
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frozen_spec = base.read_json(out / "spec.json", {})
    spec_sha256 = frozen_spec.get("spec_sha256") if isinstance(frozen_spec, Mapping) else None
    command = explore_command(
        phase,
        out,
        max_families=max_families,
        max_episodes=max_episodes,
    )
    wall, monotonic = base._clock_functions(clock)
    with base.supervision_lock(out):
        started = float(wall())
        status_file = _status_path(out, phase)
        log_file = base.log_path(out, phase)
        previous = base.read_json(status_file, {})
        history = previous.get("invocation_history", []) if isinstance(previous, Mapping) else []
        if not isinstance(history, list):
            history = []
        if isinstance(previous, Mapping) and previous.get("status") == "running":
            previous_pid = previous.get("worker_pid")
            previous_command = previous.get("command")
            if isinstance(previous_command, list):
                active = verifier(previous_pid, previous_command) if verifier else base.verify_pid_command(previous_pid, previous_command)
                if active:
                    raise base.SupervisorError(
                        f"existing explore child PID {previous_pid} is still running"
                    )
        state: dict[str, Any] = {
            "schema_version": 1,
            "phase": phase,
            "module": MODULE,
            "spec_sha256": spec_sha256,
            "invocation_id": _id(phase, started),
            "status": "starting",
            "pid": os.getpid(),
            "supervisor_pid": os.getpid(),
            "worker_pid": None,
            "actual_worker_pid": None,
            "command": command,
            "cmd": command,
            "cwd": str(base.WORKDIR),
            "log_path": str(log_file),
            "started_unix": started,
            "updated_unix": started,
            "poll_interval_seconds": interval,
            "stale_after_active_seconds": STALE_SECONDS,
            "notify_events": notify_events,
            "bounded_phase": max_families is not None or max_episodes is not None,
            "max_families": max_families,
            "max_episodes": max_episodes,
            "shared_worker_lock": str(base.OLD_OUT / "run.lock"),
            "shared_ledger": str(base.SHARED_LEDGER),
            "invocation_history": history,
        }
        base.atomic_json(status_file, state)
        base.append_log(log_file, "supervisor start command=" + shlex.join(command), now=started)
        log_handle = None
        try:
            log_handle = log_file.open("ab", buffering=0)
            process = popen(
                command,
                cwd=base.WORKDIR,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            worker_pid = getattr(process, "pid", None)
            state.update(worker_pid=worker_pid, actual_worker_pid=worker_pid, status="running")
            initial_code = process.poll()
            if initial_code is None:
                verified = verifier(worker_pid, command) if verifier else base.verify_pid_command(worker_pid, command)
                state["worker_command_verified"] = bool(verified)
                if not verified:
                    # Recheck once because the child can exit between poll and
                    # the identity query.  Never kill or restart it.
                    raced_code = process.poll()
                    if raced_code is None:
                        state.update(
                            status="child_identity_failed",
                            ended_unix=float(wall()),
                            error_type="ChildIdentityError",
                            error="child PID or command identity could not be verified",
                        )
                        event = _event_payload(
                            state,
                            out,
                            reason="child_pid_command_mismatch",
                            source="identity",
                            now=state["ended_unix"],
                        )
                        state["event_key"] = event["event_key"]
                        _emit_event(event, out, notify_events=notify_events, sender=sender)
                        return state
                    initial_code = raced_code
                    state["identity_check_race"] = True
                    state["worker_command_verified"] = "exited_before_verification"
            timer = base.ActiveWallTimer(wall=wall, monotonic=monotonic)
            timer.sample()
            progress_baseline = 0.0
            last_progress = None
            first_poll = True
            while True:
                code = initial_code if first_poll else process.poll()
                first_poll = False
                active = timer.sample()
                now = float(wall())
                progress = base.progress_evidence(out, phase, started_unix=started, now=now)
                marker = progress.get("timestamp_unix")
                if marker is not None and marker != last_progress:
                    last_progress = marker
                    progress_baseline = active
                stale = max(0.0, active - progress_baseline)
                state.update(
                    updated_unix=now,
                    active_wall_seconds=active,
                    sleep_seconds=timer.sleep_seconds,
                    active_since_progress_seconds=stale,
                    progress=progress,
                )
                if code is None:
                    verified = verifier(worker_pid, command) if verifier else base.verify_pid_command(worker_pid, command)
                    state["worker_command_verified"] = bool(verified)
                    if not verified:
                        raced_code = process.poll()
                        if raced_code is None:
                            state.update(status="child_identity_failed", ended_unix=now, error_type="ChildIdentityError")
                            event = _event_payload(
                                state,
                                out,
                                reason="child_pid_command_mismatch",
                                source="poll",
                                now=now,
                            )
                            state["event_key"] = event["event_key"]
                            _emit_event(event, out, notify_events=notify_events, sender=sender)
                            break
                        code = raced_code
                        state["identity_check_race"] = True
                        state["worker_command_verified"] = "exited_before_verification"
                    if code is None:
                        if stale > STALE_SECONDS and not state.get("stalled"):
                            state["stalled"] = True
                            event = _event_payload(
                                state,
                                out,
                                reason="no_progress_15_minutes",
                                source="poll",
                                now=now,
                                active_since_progress_seconds=stale,
                            )
                            state["event_key"] = event["event_key"]
                            _emit_event(event, out, notify_events=notify_events, sender=sender)
                        base.atomic_json(status_file, state)
                        if once:
                            break
                        sleep(max(0.0, interval))
                        continue
                state["exit_code"] = code
                state["runner_exit_code"] = code
                state["ended_unix"] = now
                assessment = _phase_assessment(
                    out,
                    phase,
                    started=started,
                    max_families=max_families,
                    max_episodes=max_episodes,
                )
                state["artifact_assessment"] = assessment
                if assessment.get("completion_status") == "bounded":
                    state["status"] = "bounded"
                elif assessment.get("complete"):
                    state["status"] = assessment.get("completion_status", "completed")
                else:
                    state["status"] = "incomplete" if code == 0 else "failed"
                if state["status"] in {"incomplete", "failed"} and not state.get("event_key"):
                    event = _event_payload(
                        state,
                        out,
                        reason="phase_incomplete" if state["status"] == "incomplete" else "child_exit_nonzero",
                        source="artifact_assessment" if state["status"] == "incomplete" else "terminal",
                        now=now,
                        exit_code=code,
                        artifact_assessment=assessment,
                    )
                    state["event_key"] = event["event_key"]
                    _emit_event(event, out, notify_events=notify_events, sender=sender)
                elif state["status"] == "bounded":
                    state["bounded_artifact"] = assessment
                elif state["status"] == "completed_with_failures" and not state.get("event_key"):
                    event = _event_payload(
                        state,
                        out,
                        reason="phase_scientific_failure",
                        source="artifact_assessment",
                        now=now,
                        exit_code=code,
                        artifact_assessment=assessment,
                    )
                    state["event_key"] = event["event_key"]
                    # A recorded unbuildable family is a scientific result,
                    # not an infrastructure exception.  Keep it local even
                    # when exception notifications were enabled.
                    _emit_event(event, out, notify_events=False, sender=sender)
                base.append_log(log_file, f"child exit={code} status={state['status']}", now=now)
                base.atomic_json(status_file, state)
                break
        except BaseException as exc:
            now = float(wall())
            state.update(status="supervisor_failed", ended_unix=now, error_type=type(exc).__name__, error=str(exc))
            base.atomic_json(status_file, state)
            raise
        finally:
            if log_handle is not None:
                log_handle.close()
        summary = {
            key: state[key]
            for key in (
                "invocation_id", "phase", "module", "status", "pid", "worker_pid", "command",
                "started_unix", "ended_unix", "exit_code", "runner_exit_code", "event_key",
                "bounded_artifact", "identity_check_race",
            )
            if key in state
        }
        history.append(summary)
        state["invocation_history"] = history
        base.atomic_json(status_file, state)
        _append_history(out, phase, summary)
        return state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-families", type=int)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--notify-events", action="store_true")
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.max_families is not None and args.max_families < 1:
        parser.error("--max-families must be positive")
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    if args.interval < 0:
        parser.error("--interval must be non-negative")
    try:
        state = supervise(
            args.phase,
            args.out,
            max_families=args.max_families,
            max_episodes=args.max_episodes,
            interval=args.interval,
            notify_events=args.notify_events,
            once=args.once,
        )
    except (base.AlreadySupervised, base.SupervisorError, ValueError) as exc:
        print(f"SUPERVISOR STOPPED: {exc}", file=sys.stderr)
        return 2
    if state.get("status") in {"completed", "bounded"}:
        return 0
    return int(state.get("exit_code") or 2)


# Keep the phase-oriented API familiar to callers of the original supervisor.
run_phase = supervise
phase_command = explore_command


if __name__ == "__main__":
    raise SystemExit(main())
