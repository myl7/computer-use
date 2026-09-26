"""Model-free process supervision for the selective Android pilot.

The supervisor starts one frozen harness phase, records its real PID and
command, and watches only local files and the shared billing ledger.  It does
not restart a child.  A stalled child remains alive while an optional
one-shot Codex queue event is recorded for recovery.  The harness owns the
old revision's ``run.lock`` and its state-aware episode skipping policy.

Normal use (from ``/Users/myl/app/computer-use/computer-use``)::

    ../.venv-android/bin/python -m guiexp_android.selective_supervisor \
        --phase train --max-episodes 1 --notify-events

The ``pipeline`` phase runs ``train``, ``build``, and ``run`` sequentially.
Each stage has its own status and append-only log.  ``--analyze`` is run
locally after every terminal ``run`` state, including a partial or failed
state, so preserved evidence remains reportable.  Analysis is a child
process, but it is an offline harness operation and never queues a model turn.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence


# ``selective_supervisor.py`` is .../app/computer-use/computer-use/guiexp_android.
ROOT = Path(__file__).resolve().parents[2]
WORKDIR = ROOT / "computer-use"
OUT = ROOT / "experimental-results/guiexp_android/selective_20260915"
OLD_OUT = ROOT / "experimental-results/guiexp_android/revision_20260913"
SHARED_LEDGER = OLD_OUT / "budget.sqlite3"
THREAD = "01a0955d-6a11-7a90-a5f6-857c346d5413"
CODEX = "/opt/homebrew/bin/codex"
PHASES = ("train", "build", "run")
FAMILIES = ("MarkorCreateNote", "FilesMoveFile", "MarkorDeleteNote")
PILOT_MODULE = "guiexp_android.selective_pilot"
REVISION_MODULE = "guiexp_android.selective_revision"
REVISION = "compiler_v2"
REVISION_SCHEMA = "android-selective-revision/1"
STALE_SECONDS = 900.0
DEFAULT_INTERVAL = 30.0
ANALYZE_COMMAND = [
    "../.venv-android/bin/python",
    "-m",
    "guiexp_android.selective_pilot",
    "--analyze",
]


class SupervisorError(RuntimeError):
    """A local guard prevented a phase from being started or monitored."""


class AlreadySupervised(SupervisorError):
    """Another supervisor owns the supervision lock."""


def phase_command(
    phase: str,
    max_episodes: int | None = None,
    *,
    out: Path | str | None = None,
    max_families: int | None = None,
) -> list[str]:
    """Return the exact frozen relative command for one harness phase."""

    if phase not in PHASES:
        raise ValueError(f"unsupported phase: {phase!r}")
    output = Path(out) if out is not None else OUT
    module = harness_module(output)
    if module == REVISION_MODULE and phase == "train":
        raise ValueError("compiler_v2 has no train phase")
    if max_families is not None:
        if phase != "build" or module != REVISION_MODULE:
            raise ValueError("--max-families is supported only for compiler_v2 build")
        if isinstance(max_families, bool) or int(max_families) < 1:
            raise ValueError("max_families must be a positive integer")
    command = [
        "../.venv-android/bin/python",
        "-m",
        module,
        f"--{phase}",
    ]
    if max_episodes is not None:
        if isinstance(max_episodes, bool) or int(max_episodes) < 1:
            raise ValueError("max_episodes must be a positive integer")
        command.extend(["--max-episodes", str(int(max_episodes))])
    if max_families is not None:
        command.extend(["--max-families", str(int(max_families))])
    if out is not None and Path(out).resolve() != OUT.resolve():
        command.extend(["--out", str(Path(out).absolute())])
    return command


def harness_module(out: Path | str = OUT) -> str:
    """Select the frozen harness from the output spec's revision marker."""

    spec = read_json(Path(out) / "spec.json")
    if isinstance(spec, Mapping) and (
        spec.get("revision") == REVISION
        and spec.get("revision_schema") == REVISION_SCHEMA
    ):
        return REVISION_MODULE
    return PILOT_MODULE


def pipeline_phases(out: Path | str = OUT) -> tuple[str, ...]:
    """Return supported stages for the selected harness revision."""

    return ("build", "run") if harness_module(out) == REVISION_MODULE else PHASES


def analysis_command(out: Path | str | None = None) -> list[str]:
    """Return the offline analyzer command for the selected output tree."""

    output = Path(out) if out is not None else OUT
    command = ["../.venv-android/bin/python", "-m", harness_module(output), "--analyze"]
    if out is not None and Path(out).resolve() != OUT.resolve():
        command.extend(["--out", str(Path(out).absolute())])
    return command


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON without making a malformed optional progress file fatal."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, value: Any) -> None:
    """Durably replace one JSON file in the same directory."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    """Append one flushed JSON record to an invocation history file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def status_path(out: Path, phase: str) -> Path:
    return Path(out) / f"batch_status.{phase}.json"


def log_path(out: Path, phase: str) -> Path:
    return Path(out) / f"{phase}.log"


def history_path(out: Path, phase: str) -> Path:
    return Path(out) / f"batch_status.{phase}.history.jsonl"


def append_log(path: Path, message: str, *, now: float | None = None) -> None:
    """Append a short supervisor record without truncating child output."""

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.time() if now is None else now
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp:.6f}] {message}\n")
        handle.flush()
        os.fsync(handle.fileno())


def pid_alive(pid: Any) -> bool:
    """Return whether *pid* currently exists, without signalling it."""

    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _proc_command(pid: int) -> list[str] | None:
    """Read a process command using procfs or the local ``ps`` utility."""

    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        raw = proc_cmdline.read_bytes()
    except OSError:
        raw = b""
    if raw:
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]

    # macOS does not expose procfs.  ``ps`` is a local identity check, not a
    # model/API call.  Keep this helper separate so tests can inject it.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    if not line:
        return None
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


def _normalise_executable(token: str, cwd: Path) -> str:
    path = Path(token)
    if not path.is_absolute():
        path = cwd / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def command_matches(
    actual: Sequence[str] | str | None,
    expected: Sequence[str],
    *,
    cwd: Path = WORKDIR,
) -> bool:
    """Match a real command while allowing the launcher to resolve a path."""

    if actual is None:
        return False
    if isinstance(actual, str):
        try:
            actual_tokens = shlex.split(actual)
        except ValueError:
            actual_tokens = actual.split()
    else:
        actual_tokens = list(actual)
    if len(actual_tokens) < len(expected) or not actual_tokens:
        return False
    expected_exe = _normalise_executable(expected[0], Path(cwd))
    actual_exe = _normalise_executable(actual_tokens[0], Path(cwd))
    if actual_exe != expected_exe and Path(actual_exe).name != Path(expected_exe).name:
        return False
    return actual_tokens[1 : len(expected)] == list(expected[1:])


def verify_pid_command(
    pid: Any,
    expected: Sequence[str],
    *,
    cwd: Path = WORKDIR,
    command_reader: Callable[[int], Sequence[str] | str | None] = _proc_command,
) -> bool:
    """Verify liveness and command identity for the actual child PID."""

    if not pid_alive(pid):
        return False
    actual = command_reader(pid)
    return command_matches(actual, expected, cwd=cwd)


class ActiveWallTimer:
    """Accumulate awake wall time while tolerating host suspend intervals.

    ``time.monotonic`` is the active-clock source on macOS and Linux.  When a
    platform's wall clock advances farther than its monotonic clock, the gap
    is treated as host sleep.  Tests can inject both clocks and inspect the
    counters without waiting.
    """

    def __init__(
        self,
        *,
        wall: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.wall = wall
        self.monotonic = monotonic
        self.last_wall: float | None = None
        self.last_monotonic: float | None = None
        self.active_seconds = 0.0
        self.sleep_seconds = 0.0

    def sample(self, *, awake: bool = True) -> float:
        current_wall = float(self.wall())
        current_monotonic = float(self.monotonic())
        if self.last_wall is not None and self.last_monotonic is not None:
            wall_delta = max(0.0, current_wall - self.last_wall)
            monotonic_delta = max(0.0, current_monotonic - self.last_monotonic)
            # A small difference is scheduler/clock noise.  Larger wall-only
            # time is a sleep interval.  The monotonic delta remains active.
            sleep_delta = max(0.0, wall_delta - monotonic_delta)
            if awake:
                self.sleep_seconds += sleep_delta
                self.active_seconds += monotonic_delta
            else:
                # A host-state reader can identify sleep even on a platform
                # whose monotonic clock includes suspend.  Count this whole
                # sample interval as inactive and retain it for diagnostics.
                self.sleep_seconds += max(wall_delta, monotonic_delta)
        self.last_wall = current_wall
        self.last_monotonic = current_monotonic
        return self.active_seconds


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _timestamp_values(value: Any, *, keys: Iterable[str]) -> Iterator[float]:
    if isinstance(value, Mapping):
        for key in keys:
            number = _number(value.get(key))
            if number is not None:
                yield number


def _progress_paths(out: Path, phase: str) -> list[Path]:
    """Known phase progress names, including the harness's nested layout."""

    out = Path(out)
    return [
        out / f"progress.{phase}.json",
        out / f"{phase}_progress.json",
        out / phase / "progress.json",
        out / "progress.json",
    ]


def _phase_ledger_prefixes(phase: str) -> tuple[str, ...]:
    return (
        f"selective_20260915/{phase}/%",
        f"selective_20260915/{phase}:%",
    )


def _ledger_progress(phase: str, *, ledger: Path = SHARED_LEDGER) -> tuple[float | None, str | None]:
    """Read only the newest receipt timestamp for this selective phase."""

    ledger = Path(ledger)
    if not ledger.exists():
        return None, None
    try:
        with sqlite3.connect(
            f"file:{ledger}?mode=ro", uri=True, timeout=2
        ) as database:
            clauses = []
            params: list[str] = []
            for prefix in _phase_ledger_prefixes(phase):
                clauses.append("(id LIKE ? OR episode LIKE ?)")
                params.extend([prefix, prefix])
            row = database.execute(
                "SELECT created, id FROM calls WHERE "
                + " OR ".join(clauses)
                + " ORDER BY created DESC LIMIT 1",
                params,
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None, None
    if not row:
        return None, None
    timestamp = _number(row[0])
    return timestamp, str(row[1]) if row[1] is not None else None


def progress_evidence(
    out: Path,
    phase: str,
    *,
    started_unix: float,
    now: float | None = None,
    ledger: Path = SHARED_LEDGER,
) -> dict[str, Any]:
    """Return the latest valid child progress timestamp and its source.

    The supervisor never treats its own status mtime as progress.  A valid
    phase progress file, child log append, or receipt under the phase prefix
    can advance the indicator.  Existing artifacts from before this
    invocation are ignored.
    """

    out = Path(out)
    current = time.time() if now is None else float(now)
    candidates: list[tuple[float, str, str | None]] = []
    progress_keys = (
        "updated_unix",
        "last_progress_unix",
        "last_output_unix",
        "timestamp_unix",
        "updated_at_unix",
        "created_unix",
    )
    for path in _progress_paths(out, phase):
        try:
            stat = path.stat()
        except OSError:
            continue
        record = read_json(path)
        if not isinstance(record, Mapping):
            continue
        timestamps = list(_timestamp_values(record, keys=progress_keys))
        # mtime is useful for JSON progress records that carry only counters.
        timestamps.append(float(stat.st_mtime))
        for timestamp in timestamps:
            if timestamp >= started_unix and timestamp <= current + 1.0:
                candidates.append((timestamp, "progress", str(path)))

    phase_log = log_path(out, phase)
    try:
        mtime = float(phase_log.stat().st_mtime)
    except OSError:
        mtime = None
    if mtime is not None and mtime >= started_unix and mtime <= current + 1.0:
        candidates.append((mtime, "log", str(phase_log)))

    receipt_timestamp, receipt_id = _ledger_progress(phase, ledger=ledger)
    if (
        receipt_timestamp is not None
        and receipt_timestamp >= started_unix
        and receipt_timestamp <= current + 1.0
    ):
        candidates.append((receipt_timestamp, "receipt", receipt_id))

    if not candidates:
        return {"timestamp_unix": None, "source": None, "detail": None}
    timestamp, source, detail = max(candidates, key=lambda item: item[0])
    return {"timestamp_unix": timestamp, "source": source, "detail": detail}


def _status_counts(rows: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("status") if isinstance(row, Mapping) else None
        status = str(status) if status else "missing"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _terminal_training_rows(rows: Any) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    terminal = {"done", "budget_stopped", "interrupted", "prior_receipt", "prior_action_evidence", "prior_artifact"}
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        # Successful training result rows historically omitted ``status``.
        if row.get("status") is None and "success" in row:
            continue
        if row.get("status") not in terminal:
            return False
    return True


def _expected_families(out: Path) -> tuple[str, ...]:
    """Use a frozen selected-family list when one is present."""

    spec = read_json(Path(out) / "spec.json")
    if isinstance(spec, Mapping):
        for key in ("families", "selected_families"):
            values = spec.get(key)
            if isinstance(values, list) and values and all(isinstance(item, str) for item in values):
                return tuple(values)
    return FAMILIES


def phase_artifact_assessment(
    out: Path,
    phase: str,
    *,
    max_families: int | None = None,
) -> dict[str, Any]:
    """Check whether a zero exit code produced a complete phase artifact.

    Older harness entry points returned zero after recording partial, budget,
    or unbuildable outcomes.  A process supervisor cannot rely on that
    compatibility behavior, so this read-only check inspects the durable
    manifest/progress written by the child.  It never changes those artifacts.
    """

    out = Path(out)
    if phase == "train":
        path = out / "training_manifest.json"
        record = read_json(path)
        if not isinstance(record, Mapping):
            return {
                "complete": False,
                "artifact": str(path),
                "reason": "missing_training_manifest",
                "message": f"train exit 0 but {path.name} is missing or unreadable",
            }
        families = record.get("families")
        expected = _expected_families(out)
        missing = [family for family in expected if not isinstance(families, Mapping) or family not in families]
        unsuccessful = []
        incomplete_families = []
        if isinstance(families, Mapping):
            for family in expected:
                rows = families.get(family)
                if not isinstance(rows, list) or not any(
                    isinstance(row, Mapping) and row.get("success") is True for row in rows
                ):
                    unsuccessful.append(family)
                if not _terminal_training_rows(rows):
                    incomplete_families.append(family)
        complete_rows = not missing and not incomplete_families
        manifest_ready = record.get("status") == "ready"
        scientific_failures = [family for family in unsuccessful if family not in missing]
        # A partial manifest with terminal failed families is useful
        # evidence for a later build.  A partial manifest whose rows all
        # succeeded is contradictory and remains incomplete until repaired.
        terminal_scientific_failure = complete_rows and not manifest_ready and bool(scientific_failures)
        complete = complete_rows and (manifest_ready or terminal_scientific_failure)
        if complete:
            completion_status = "completed_with_failures" if scientific_failures else "completed"
        elif terminal_scientific_failure:
            completion_status = "completed_with_failures"
        else:
            completion_status = "incomplete"
        reason = None if completion_status == "completed" else (
            "training_scientific_failures" if completion_status == "completed_with_failures"
            else "training_manifest_not_ready"
        )
        return {
            "complete": complete,
            "completion_status": completion_status,
            "artifact": str(path),
            "record_type": record.get("record_type"),
            "manifest_status": record.get("status"),
            "families": list(expected),
            "missing_families": missing,
            "unsuccessful_families": unsuccessful,
            "incomplete_families": incomplete_families,
            "scientific_failures": scientific_failures,
            "reason": reason,
            "message": (
                f"train exit 0 but {path.name} is incomplete: "
                f"status={record.get('status')!r}, "
                f"incomplete_families={incomplete_families or 'none'}"
            ) if completion_status == "incomplete" else (
                f"train completed with failed families: {scientific_failures}"
                if completion_status == "completed_with_failures" else None
            ),
        }

    if phase == "build":
        path = out / "build_manifest.json"
        record = read_json(path)
        if not isinstance(record, Mapping):
            return {
                "complete": False,
                "artifact": str(path),
                "reason": "missing_build_manifest",
                "message": f"build exit 0 but {path.name} is missing or unreadable",
            }
        if (
            max_families is not None
            and harness_module(out) == REVISION_MODULE
            and record.get("status") == "pilot_limit"
        ):
            return {
                "complete": False,
                "completion_status": "bounded",
                "bounded": True,
                "artifact": str(path),
                "record_type": record.get("record_type"),
                "manifest_status": record.get("status"),
                "max_families": max_families,
                "families": list(_expected_families(out)),
                "reason": "build_pilot_limit",
                "message": f"build stopped at requested max_families={max_families}",
            }
        families = record.get("families")
        expected = _expected_families(out)
        missing = [family for family in expected if not isinstance(families, Mapping) or family not in families]
        statuses: dict[str, str] = {}
        if isinstance(families, Mapping):
            for family in expected:
                item = families.get(family)
                statuses[family] = str(item.get("status")) if isinstance(item, Mapping) else "missing"
        terminal_statuses = {"built", "already_built", "unbuildable"}
        incomplete = [family for family, status in statuses.items() if status not in terminal_statuses]
        scientific_failures = [family for family, status in statuses.items() if status == "unbuildable"]
        complete = not missing and not incomplete
        completion_status = (
            "completed" if complete and not scientific_failures else
            "completed_with_failures" if complete else "incomplete"
        )
        return {
            "complete": complete,
            "completion_status": completion_status,
            "artifact": str(path),
            "record_type": record.get("record_type"),
            "families": list(expected),
            "family_statuses": statuses,
            "missing_families": missing,
            "incomplete_families": incomplete,
            "scientific_failures": scientific_failures,
            "reason": None if completion_status == "completed" else (
                "build_scientific_failures" if completion_status == "completed_with_failures"
                else "build_manifest_incomplete"
            ),
            "message": (
                f"build exit 0 but {path.name} is incomplete: "
                f"family_statuses={statuses}"
            ) if completion_status == "incomplete" else (
                f"build completed with unbuildable families: {scientific_failures}"
                if completion_status == "completed_with_failures" else None
            ),
        }

    if phase == "run":
        path = out / "progress.json"
        record = read_json(path)
        if not isinstance(record, Mapping):
            return {
                "complete": False,
                "artifact": str(path),
                "reason": "missing_progress",
                "message": f"run exit 0 but {path.name} is missing or unreadable",
            }
        rows = record.get("episodes")
        rows = rows if isinstance(rows, list) else []
        planned = record.get("planned")
        planned_count = int(planned) if isinstance(planned, int) and planned >= 0 else len(rows)
        counts = record.get("counts")
        counts = dict(counts) if isinstance(counts, Mapping) else _status_counts(rows)
        row_counts = _status_counts(rows)
        terminal_statuses = {"done", "skipped_unbuildable"}
        non_terminal = {
            status: count for status, count in row_counts.items() if status not in terminal_statuses
        }
        scientific_failures = {
            status: count for status, count in row_counts.items() if status == "skipped_unbuildable"
        }
        complete = (
            record.get("batch_status") == "complete"
            and planned_count > 0
            and len(rows) == planned_count
            and not non_terminal
        )
        completion_status = (
            "completed_with_failures" if complete and scientific_failures else
            "completed" if complete else "incomplete"
        )
        return {
            "complete": complete,
            "completion_status": completion_status,
            "artifact": str(path),
            "record_type": record.get("record_type"),
            "batch_status": record.get("batch_status"),
            "planned": planned_count,
            "observed": len(rows),
            "counts": counts,
            "row_status_counts": row_counts,
            "incomplete_statuses": non_terminal,
            "scientific_failures": scientific_failures,
            "reason": None if completion_status == "completed" else (
                "run_scientific_failures" if completion_status == "completed_with_failures"
                else "progress_incomplete"
            ),
            "message": (
                f"run exit 0 but {path.name} is incomplete: "
                f"batch_status={record.get('batch_status')!r}, "
                f"planned={planned_count}, observed={len(rows)}, "
                f"statuses={row_counts}"
            ) if completion_status == "incomplete" else (
                f"run completed with skipped unbuildable rows: {scientific_failures}"
                if completion_status == "completed_with_failures" else None
            ),
        }

    raise ValueError(f"unsupported phase: {phase!r}")


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _new_invocation_id(phase: str, pid: int, now: float) -> str:
    return f"{phase}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}-{pid}-{time.time_ns()}"


def _history_from_status(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    history = status.get("invocation_history")
    if not isinstance(history, list):
        return []
    return [item for item in history if isinstance(item, Mapping)]


def _history_append(
    out: Path,
    phase: str,
    history: list[dict[str, Any]],
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    item = dict(summary)
    if not any(existing.get("invocation_id") == item.get("invocation_id") for existing in history):
        history.append(item)
        append_jsonl(history_path(out, phase), item)
    return history


def _summary(state: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "invocation_id",
        "phase",
        "status",
        "pid",
        "worker_pid",
        "child_pid",
        "command",
        "cwd",
        "log_path",
        "started_unix",
        "ended_unix",
        "exit_code",
        "analysis_exit_code",
        "semantic_complete",
        "scientific_failures",
        "bounded_phase",
        "incomplete_reason",
        "artifact_assessment",
        "error_type",
        "error",
        "stalled",
        "event_key",
    )
    return {key: state[key] for key in keys if key in state}


@contextmanager
def supervision_lock(out: Path = OUT) -> Iterator[Any]:
    """Hold the supervisor-only lock for the complete invocation.

    The worker's old ``revision_20260913/run.lock`` is deliberately never
    opened here.  The child harness remains the sole owner of that lock.
    """

    path = Path(out) / "supervisor.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadySupervised("A selective supervisor is already running.") from exc
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise AlreadySupervised("A selective supervisor is already running.") from exc
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _active_worker_status(
    out: Path,
    *,
    verifier: Callable[[Any, Sequence[str]], bool] | None = None,
) -> Mapping[str, Any] | None:
    """Find a live selective child left by an earlier supervisor."""

    for path in sorted(Path(out).glob("batch_status.*.json")):
        phase = path.name[len("batch_status.") : -len(".json")]
        if phase not in PHASES:
            continue
        state = read_json(path, {})
        if not isinstance(state, Mapping) or state.get("status") not in {
            "starting",
            "running",
        }:
            continue
        command = state.get("command")
        pid = state.get("worker_pid")
        if not isinstance(command, list) or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            continue
        alive = verifier(pid, command) if verifier is not None else verify_pid_command(pid, command)
        if alive:
            return state
    return None


def _event_prompt(event: Mapping[str, Any], event_path: Path) -> str:
    phase = event.get("phase", "unknown")
    reason = event.get("reason", "unknown")
    return (
        f"Local selective pilot event: phase={phase}, reason={reason}. "
        f"Read {event_path}, batch_status.{phase}.json, and {phase}.log before acting. "
        "Inspect the real child PID and the shared revision_20260913/budget.sqlite3 "
        "in read-only mode. Preserve receipts, action evidence, and immutable state. "
        "Use the same USD 10 occupied-budget ceiling and the existing worker run.lock. "
        "Do not replay UI episodes or resend an ambiguous request. If a newphase is "
        "needed, diagnose the exception, make the smallest tested/versioned repair, "
        "and start it only with the canonical relative interpreter after freezing its "
        "state. Normal completion is a local record. Do not create a scheduled "
        "message or heartbeat and do not perform periodic model polling."
    )


def dispatch_event(
    event: Mapping[str, Any],
    out: Path = OUT,
    *,
    sender: Callable[..., Any] = subprocess.run,
    codex: str = CODEX,
) -> bool:
    """Persist an event before optionally queueing one recovery message.

    The event path is the deduplication key.  An uncertain sender timeout is
    durable and is never retried automatically.
    """

    out = Path(out)
    events = out / "events"
    events.mkdir(parents=True, exist_ok=True)
    key = str(event.get("event_key") or event.get("key") or "unknown")
    path = events / f"{key}.json"
    if path.exists():
        return False
    record = dict(event)
    record["delivery"] = "dispatching"
    atomic_json(path, record)
    try:
        result = sender(
            [codex, "queue", "--thread", THREAD, "--message", _event_prompt(record, path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        record["delivery"] = "queued" if getattr(result, "returncode", 1) == 0 else "failed"
        record["queue_exit_code"] = getattr(result, "returncode", None)
    except subprocess.TimeoutExpired:
        record["delivery"] = "unknown_timeout"
    except OSError as exc:
        record["delivery"] = "failed"
        record["error_type"] = type(exc).__name__
    except Exception as exc:
        # A test sender or a local CLI wrapper can fail with a non-OSError.
        # Persist that outcome so an uncertain/failed delivery is never
        # retried implicitly by the polling loop.
        record["delivery"] = "failed"
        record["error_type"] = type(exc).__name__
    atomic_json(path, record)
    return True


def _event_record(
    state: Mapping[str, Any],
    *,
    reason: str,
    source: str,
    now: float,
) -> dict[str, Any]:
    existing = state.get("event_key")
    if isinstance(existing, str) and existing:
        key = existing
    else:
        kind = "stall" if reason == "no_progress_15_minutes" else "terminal_failure"
        key = f"{state.get('phase', 'phase')}-{_safe_id(str(state.get('invocation_id')) + '|' + kind)}"
    return {
        "event_key": key,
        "phase": state.get("phase"),
        "invocation_id": state.get("invocation_id"),
        "reason": reason,
        "source": source,
        "detected_unix": now,
        "worker_pid": state.get("worker_pid"),
        "command": state.get("command"),
        "active_wall_seconds": state.get("active_wall_seconds"),
        "active_since_progress_seconds": state.get("active_since_progress_seconds"),
        "progress": state.get("progress"),
        "exit_code": state.get("exit_code"),
        "error_type": state.get("error_type"),
        "error": state.get("error"),
        "incomplete_reason": state.get("incomplete_reason"),
        "artifact_assessment": state.get("artifact_assessment"),
    }


def _emit_event(
    event: Mapping[str, Any],
    out: Path,
    *,
    notify_events: bool,
    sender: Callable[..., Any],
) -> bool:
    """Write one event and optionally request one queue delivery."""

    key = str(event["event_key"])
    path = Path(out) / "events" / f"{key}.json"
    if path.exists():
        return False
    if notify_events:
        return dispatch_event(event, out, sender=sender)
    atomic_json(path, dict(event, delivery="not_requested"))
    return True


def _clock_functions(clock: Any | None) -> tuple[Callable[[], float], Callable[[], float]]:
    if clock is None:
        return time.time, time.monotonic
    wall = getattr(clock, "time", None) or getattr(clock, "wall", None)
    monotonic = getattr(clock, "monotonic", None)
    if not callable(wall) or not callable(monotonic):
        raise TypeError("clock must provide callable time/wall and monotonic methods")
    return wall, monotonic


def _default_host_state() -> Mapping[str, Any] | None:
    """Load the pilot's bounded read-only host-power snapshot lazily."""

    try:
        from .selective_budget import read_host_state

        result = read_host_state()
    except Exception:
        return None
    return result if isinstance(result, Mapping) else None


def _host_awake(
    reader: Callable[[], Mapping[str, Any] | bool | None] | None,
) -> tuple[bool, Mapping[str, Any] | bool | None]:
    if reader is None:
        return True, None
    try:
        evidence = reader()
    except Exception as exc:
        return True, {"ready": None, "normal_full_wake": None, "error_type": type(exc).__name__}
    if isinstance(evidence, Mapping):
        ready = evidence.get("ready")
        full_wake = evidence.get("normal_full_wake")
        if ready is None and full_wake is None:
            return True, evidence
        return bool(ready if ready is not None else full_wake) and bool(
            full_wake if full_wake is not None else ready
        ), evidence
    if evidence is None:
        return True, evidence
    return bool(evidence), evidence


def _analysis(
    *,
    out: Path,
    log: Path,
    command: Sequence[str],
    interval: float,
    popen: Callable[..., Any],
    sleep: Callable[[float], Any],
    now: Callable[[], float],
) -> dict[str, Any]:
    """Run the offline analyzer once and return its terminal metadata."""

    command = list(command)
    append_log(log, "analysis launch: " + shlex.join(command), now=now())
    log_handle = log.open("ab", buffering=0)
    try:
        process = popen(
            command,
            cwd=WORKDIR,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        pid = getattr(process, "pid", None)
        while True:
            code = process.poll()
            if code is not None:
                break
            sleep(max(0.0, interval))
        result = {
            "pid": pid,
            "command": command,
            "cwd": str(WORKDIR),
            "exit_code": code,
            "ended_unix": now(),
        }
        append_log(log, f"analysis exit={code}", now=result["ended_unix"])
        return result
    except BaseException as exc:
        append_log(log, f"analysis exception={type(exc).__name__}", now=now())
        return {
            "pid": locals().get("pid"),
            "command": command,
            "cwd": str(WORKDIR),
            "exit_code": None,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "ended_unix": now(),
        }
    finally:
        log_handle.close()


def _supervise_phase_locked(
    phase: str,
    *,
    out: Path,
    max_episodes: int | None,
    max_families: int | None,
    interval: float,
    notify_events: bool,
    popen: Callable[..., Any],
    sleep: Callable[[float], Any],
    clock: Any | None,
    verifier: Callable[[Any, Sequence[str]], bool] | None,
    sender: Callable[..., Any],
    once: bool,
    host_state: Callable[[], Mapping[str, Any] | bool | None] | None,
) -> dict[str, Any]:
    if phase not in PHASES:
        raise ValueError(f"unsupported phase: {phase!r}")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    current_path = status_path(out, phase)
    phase_log = log_path(out, phase)
    previous = read_json(current_path, {})
    history = _history_from_status(previous) if isinstance(previous, Mapping) else []
    wall, monotonic = _clock_functions(clock)
    if host_state is None and clock is None:
        host_state = _default_host_state
    started_unix = float(wall())
    timer = ActiveWallTimer(wall=wall, monotonic=monotonic)
    awake, initial_host_state = _host_awake(host_state)
    timer.sample(awake=awake)
    invocation_id = _new_invocation_id(phase, os.getpid(), started_unix)
    command = phase_command(phase, max_episodes, out=out, max_families=max_families)
    state: dict[str, Any] = {
        "schema_version": 1,
        "phase": phase,
        "invocation_id": invocation_id,
        "status": "starting",
        "pid": os.getpid(),
        "supervisor_pid": os.getpid(),
        "worker_pid": None,
        "child_pid": None,
        "actual_worker_pid": None,
        "command": list(command),
        "cmd": list(command),
        "cwd": str(WORKDIR),
        "log_path": str(phase_log),
        "phase_log": str(phase_log),
        "started_unix": started_unix,
        "updated_unix": started_unix,
        "poll_interval_seconds": interval,
        "stale_after_active_seconds": STALE_SECONDS,
        "bounded_phase": max_episodes is not None or max_families is not None,
        "max_families": max_families,
        "notify_events": notify_events,
        "shared_worker_lock": str(OLD_OUT / "run.lock"),
        "shared_ledger": str(SHARED_LEDGER),
        "active_wall_seconds": 0.0,
        "sleep_seconds": 0.0,
        "active_since_progress_seconds": 0.0,
        "last_progress_unix": None,
        "progress": {"timestamp_unix": None, "source": None, "detail": None},
        "host_state": initial_host_state,
        "invocation_history": history,
        "history": history,
    }
    atomic_json(current_path, state)
    append_log(phase_log, "supervisor start command=" + shlex.join(command), now=started_unix)

    active = _active_worker_status(out, verifier=verifier)
    if active is not None:
        state.update(
            status="refused_existing_child",
            ended_unix=float(wall()),
            error_type="SupervisorError",
            error=f"existing child PID {active.get('worker_pid')} is still running",
        )
        _history_append(out, phase, history, _summary(state))
        state["invocation_history"] = history
        atomic_json(current_path, state)
        append_log(phase_log, "refused existing child", now=state["ended_unix"])
        return state

    process: Any = None
    log_handle: Any = None
    try:
        log_handle = phase_log.open("ab", buffering=0)
        try:
            process = popen(
                list(command),
                cwd=WORKDIR,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException as exc:
            state.update(
                status="launch_failed",
                ended_unix=float(wall()),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            event = _event_record(state, reason="child_launch_failed", source="launch", now=state["ended_unix"])
            state["event_key"] = event["event_key"]
            _emit_event(event, out, notify_events=notify_events, sender=sender)
            _history_append(out, phase, history, _summary(state))
            state["invocation_history"] = history
            atomic_json(current_path, state)
            append_log(phase_log, f"launch exception={type(exc).__name__}", now=state["ended_unix"])
            return state

        worker_pid = getattr(process, "pid", None)
        state.update(
            status="running",
            worker_pid=worker_pid,
            child_pid=worker_pid,
            actual_worker_pid=worker_pid,
            worker_command_verified=None,
            updated_unix=float(wall()),
        )
        initial_code = process.poll()
        if initial_code is None:
            verified = verifier(worker_pid, command) if verifier is not None else verify_pid_command(worker_pid, command)
            state["worker_command_verified"] = bool(verified)
            if not verified:
                # The child may have exited between this poll and the PID
                # identity query.  Re-read its return code once before
                # declaring an identity failure.
                raced_code = process.poll()
                if raced_code is not None:
                    initial_code = raced_code
                    state["worker_command_verified"] = "exited_before_verification"
                    state["identity_check_race"] = True
                else:
                    state.update(
                        status="child_identity_failed",
                        ended_unix=float(wall()),
                        error_type="ChildIdentityError",
                        error="child PID or command identity could not be verified",
                    )
                    event = _event_record(state, reason="child_pid_command_mismatch", source="identity", now=state["ended_unix"])
                    state["event_key"] = event["event_key"]
                    _emit_event(event, out, notify_events=notify_events, sender=sender)
                    _history_append(out, phase, history, _summary(state))
                    state["invocation_history"] = history
                    atomic_json(current_path, state)
                    append_log(phase_log, "child identity verification failed", now=state["ended_unix"])
                    return state
        else:
            # A short-lived child may have exited before the first identity
            # sample.  Its Popen return code is authoritative in that case.
            state["worker_command_verified"] = None

        progress_baseline_active = 0.0
        previous_progress_timestamp: float | None = None
        first_poll = True
        while True:
            code = initial_code if first_poll else process.poll()
            # Consume the initial result only once.  Subsequent iterations
            # always query the live process again.
            first_poll = False
            awake, current_host_state = _host_awake(host_state)
            active_seconds = timer.sample(awake=awake)
            now = float(wall())
            progress = progress_evidence(out, phase, started_unix=started_unix, now=now)
            progress_timestamp = progress.get("timestamp_unix")
            if progress_timestamp is not None and (
                previous_progress_timestamp is None or progress_timestamp > previous_progress_timestamp
            ):
                previous_progress_timestamp = float(progress_timestamp)
                progress_baseline_active = active_seconds
            active_since_progress = max(0.0, active_seconds - progress_baseline_active)
            state.update(
                updated_unix=now,
                active_wall_seconds=active_seconds,
                sleep_seconds=timer.sleep_seconds,
                active_since_progress_seconds=active_since_progress,
                last_progress_unix=progress_timestamp,
                progress=progress,
                host_state=current_host_state,
            )
            if code is None:
                verified = verifier(worker_pid, command) if verifier is not None else verify_pid_command(worker_pid, command)
                state["worker_command_verified"] = bool(verified)
                if not verified:
                    # A just-exited child has a definitive return code.  Do
                    # not confuse that race with PID reuse or a wrong command.
                    raced_code = process.poll()
                    if raced_code is None:
                        state.update(
                            status="child_identity_failed",
                            ended_unix=now,
                            error_type="ChildIdentityError",
                            error="child PID or command identity changed while running",
                        )
                        event = _event_record(state, reason="child_pid_command_mismatch", source="poll", now=now)
                        state["event_key"] = event["event_key"]
                        _emit_event(event, out, notify_events=notify_events, sender=sender)
                        atomic_json(current_path, state)
                        append_log(phase_log, "child identity lost during poll", now=now)
                        break
                    code = raced_code
                    state["worker_command_verified"] = "exited_before_verification"
                    state["identity_check_race"] = True
                if code is None:
                    if active_since_progress > STALE_SECONDS and not state.get("stalled"):
                        state["stalled"] = True
                        event = _event_record(state, reason="no_progress_15_minutes", source="poll", now=now)
                        state["event_key"] = event["event_key"]
                        _emit_event(event, out, notify_events=notify_events, sender=sender)
                        append_log(phase_log, "stall event recorded; child left running", now=now)
                    atomic_json(current_path, state)
                    if once:
                        break
                    sleep(max(0.0, float(interval)))
                    continue

            state["exit_code"] = code
            state["runner_exit_code"] = code
            state["worker_exit_code"] = code
            state["ended_unix"] = now
            assessment = None
            # Build/train can expose scientifically failed families while
            # still producing a complete terminal manifest.  Read the
            # manifest on a nonzero exit too, so valid families can proceed.
            if code == 0 or phase in {"train", "build"}:
                assessment = phase_artifact_assessment(out, phase, max_families=max_families)
            if code == 0:
                state["artifact_assessment"] = assessment
                state["semantic_complete"] = bool(assessment.get("complete"))
                completion_status = assessment.get("completion_status")
                state["status"] = (
                    completion_status if assessment.get("complete") or completion_status == "bounded"
                    else "bounded" if state["bounded_phase"] else "incomplete"
                )
                if state["status"] in {"bounded", "incomplete"}:
                    state["incomplete_reason"] = assessment.get("reason")
                    state["error"] = assessment.get("message")
                elif assessment.get("scientific_failures"):
                    state["scientific_failures"] = assessment.get("scientific_failures")
                    state["error"] = assessment.get("message")
                append_log(
                    phase_log,
                    "child exit=0 semantic_status=" + state["status"],
                    now=now,
                )
            else:
                if assessment and assessment.get("completion_status") in {"completed_with_failures", "bounded"}:
                    state["artifact_assessment"] = assessment
                    state["semantic_complete"] = assessment.get("completion_status") != "bounded"
                    state["status"] = assessment.get("completion_status")
                    state["scientific_failures"] = assessment.get("scientific_failures")
                    state["error"] = assessment.get("message")
                else:
                    state["status"] = "failed"
                append_log(phase_log, f"child exit={code}", now=now)
            # Persist the child's terminal result before running offline
            # analysis.  If analysis itself is interrupted, the child outcome
            # and the semantic assessment remain durable.
            atomic_json(current_path, state)
            if phase == "run":
                # Analysis is useful for complete, partial, and failed runs.
                # It is local evidence processing and never queues a model
                # turn.  Its failure is recorded without hiding the child
                # outcome or converting an incomplete run into completion.
                analysis = _analysis(
                    out=out,
                    log=phase_log,
                    command=analysis_command(out),
                    interval=interval,
                    popen=popen,
                    sleep=sleep,
                    now=wall,
                )
                state["analysis"] = analysis
                state["analysis_pid"] = analysis.get("pid")
                state["analysis_command"] = analysis.get("command")
                state["analysis_exit_code"] = analysis.get("exit_code")
                if analysis.get("exit_code") != 0 and state["status"] == "completed":
                    state["status"] = "analysis_failed"
                    if not state.get("event_key"):
                        event = _event_record(state, reason="offline_analysis_failed", source="analysis", now=float(wall()))
                        state["event_key"] = event["event_key"]
                        _emit_event(event, out, notify_events=notify_events, sender=sender)
            # A prior stall event identifies the same invocation.  Do not
            # create a second incident when a stalled child later exits.
            if not state.get("event_key"):
                if state["status"] == "incomplete":
                    if state.get("bounded_phase") is not True:
                        event = _event_record(state, reason="phase_incomplete", source="artifact_assessment", now=now)
                        state["event_key"] = event["event_key"]
                        _emit_event(event, out, notify_events=notify_events, sender=sender)
                elif state["status"] == "completed_with_failures":
                    event = _event_record(state, reason="phase_scientific_failure", source="artifact_assessment", now=now)
                    state["event_key"] = event["event_key"]
                    # Scientific unbuildable/skip outcomes are local records.
                    # They do not wake the model or request a repair turn.
                    _emit_event(event, out, notify_events=False, sender=sender)
                elif state["status"] == "failed":
                    event = _event_record(state, reason="child_exit_nonzero", source="terminal", now=now)
                    state["event_key"] = event["event_key"]
                    _emit_event(event, out, notify_events=notify_events, sender=sender)
            atomic_json(current_path, state)
            break
    except BaseException as exc:
        now = float(wall())
        state.update(
            status="supervisor_failed",
            ended_unix=now,
            error_type=type(exc).__name__,
            error=str(exc),
            active_wall_seconds=timer.sample(),
        )
        if not state.get("event_key"):
            event = _event_record(state, reason="supervisor_exception", source="supervisor", now=now)
            state["event_key"] = event["event_key"]
            _emit_event(event, out, notify_events=notify_events, sender=sender)
        append_log(phase_log, f"supervisor exception={type(exc).__name__}", now=now)
        atomic_json(current_path, state)
    finally:
        if log_handle is not None:
            log_handle.close()

    _history_append(out, phase, history, _summary(state))
    state["invocation_history"] = history
    atomic_json(current_path, state)
    return state


def run_phase(
    phase: str,
    *,
    out: Path = OUT,
    max_episodes: int | None = None,
    max_families: int | None = None,
    interval: float = DEFAULT_INTERVAL,
    notify_events: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], Any] = time.sleep,
    clock: Any | None = None,
    verifier: Callable[[Any, Sequence[str]], bool] | None = None,
    sender: Callable[..., Any] = subprocess.run,
    once: bool = False,
    host_state: Callable[[], Mapping[str, Any] | bool | None] | None = None,
) -> dict[str, Any]:
    """Run and supervise one phase under the singleton supervisor lock."""

    with supervision_lock(out):
        active = _active_worker_status(Path(out), verifier=verifier)
        if active is not None:
            raise SupervisorError(
                f"existing selective child PID {active.get('worker_pid')} is still running"
            )
        return _supervise_phase_locked(
            phase,
            out=Path(out),
            max_episodes=max_episodes,
            max_families=max_families,
            interval=interval,
            notify_events=notify_events,
            popen=popen,
            sleep=sleep,
            clock=clock,
            verifier=verifier,
            sender=sender,
            once=once,
            host_state=host_state,
        )


def _pipeline_status(out: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = status_path(out, "pipeline")
    old = read_json(path, {})
    return path, _history_from_status(old) if isinstance(old, Mapping) else []


def run_pipeline(
    *,
    out: Path = OUT,
    max_episodes: int | None = None,
    max_families: int | None = None,
    interval: float = DEFAULT_INTERVAL,
    notify_events: bool = False,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], Any] = time.sleep,
    clock: Any | None = None,
    verifier: Callable[[Any, Sequence[str]], bool] | None = None,
    sender: Callable[..., Any] = subprocess.run,
    once: bool = False,
    host_state: Callable[[], Mapping[str, Any] | bool | None] | None = None,
) -> dict[str, Any]:
    """Run train, build, and run in sequence, stopping on the first error."""

    out = Path(out)
    with supervision_lock(out):
        active = _active_worker_status(out, verifier=verifier)
        if active is not None:
            raise SupervisorError(
                f"existing selective child PID {active.get('worker_pid')} is still running"
            )
        wall, _ = _clock_functions(clock)
        started = float(wall())
        path, history = _pipeline_status(out)
        invocation_id = _new_invocation_id("pipeline", os.getpid(), started)
        state: dict[str, Any] = {
            "schema_version": 1,
            "phase": "pipeline",
            "invocation_id": invocation_id,
            "status": "running",
            "pid": os.getpid(),
            "supervisor_pid": os.getpid(),
            "command": [sys.executable, "-m", "guiexp_android.selective_supervisor", "--phase", "pipeline"],
            "cmd": [sys.executable, "-m", "guiexp_android.selective_supervisor", "--phase", "pipeline"],
            "cwd": str(WORKDIR),
            "log_path": str(log_path(out, "pipeline")),
            "phase_log": str(log_path(out, "pipeline")),
            "started_unix": started,
            "updated_unix": started,
            "stages": [],
            "invocation_history": history,
            "history": history,
        }
        atomic_json(path, state)
        pipeline_log = log_path(out, "pipeline")
        append_log(pipeline_log, "pipeline start stages=train,build,run", now=started)
        scientific_failures: list[Any] = []
        try:
            for stage in PHASES:
                result = _supervise_phase_locked(
                    stage,
                    out=out,
                    max_episodes=max_episodes,
                    max_families=max_families if stage == "build" else None,
                    interval=interval,
                    notify_events=notify_events,
                    popen=popen,
                    sleep=sleep,
                    clock=clock,
                    verifier=verifier,
                    sender=sender,
                    once=once,
                    host_state=host_state,
                )
                stage_summary = {
                    "phase": stage,
                    "invocation_id": result.get("invocation_id"),
                    "status": result.get("status"),
                    "worker_pid": result.get("worker_pid"),
                    "command": result.get("command"),
                    "exit_code": result.get("exit_code"),
                    "analysis_exit_code": result.get("analysis_exit_code"),
                    "semantic_complete": result.get("semantic_complete"),
                    "incomplete_reason": result.get("incomplete_reason"),
                    "artifact_assessment": result.get("artifact_assessment"),
                }
                state["stages"].append(stage_summary)
                state["updated_unix"] = float(wall())
                atomic_json(path, state)
                append_log(pipeline_log, f"stage {stage} status={result.get('status')}", now=state["updated_unix"])
                if result.get("status") == "completed_with_failures":
                    failures = result.get("scientific_failures")
                    if failures:
                        scientific_failures.append({"phase": stage, "families_or_statuses": failures})
                    continue
                if result.get("status") not in {"completed"}:
                    state["status"] = "bounded" if result.get("status") == "bounded" else (
                        "incomplete" if result.get("status") == "incomplete" else "failed"
                    )
                    state["failed_stage"] = stage
                    state["error"] = result.get("error") or result.get("incomplete_reason")
                    state["ended_unix"] = float(wall())
                    break
            else:
                state["status"] = "completed_with_failures" if scientific_failures else "completed"
                if scientific_failures:
                    state["scientific_failures"] = scientific_failures
                state["ended_unix"] = float(wall())
        except BaseException as exc:
            state.update(
                status="supervisor_failed",
                ended_unix=float(wall()),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            append_log(pipeline_log, f"pipeline exception={type(exc).__name__}", now=state["ended_unix"])
        _history_append(out, "pipeline", history, _summary(state))
        state["invocation_history"] = history
        atomic_json(path, state)
        append_log(pipeline_log, f"pipeline terminal status={state['status']}", now=state.get("ended_unix", float(wall())))
        return state


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=(*PHASES, "pipeline"), required=True)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--max-families", type=int)
    parser.add_argument("--interval", "--poll-seconds", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--notify-events", action="store_true")
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.max_episodes is not None and args.max_episodes < 1:
        parser.error("--max-episodes must be positive")
    if args.max_families is not None and args.max_families < 1:
        parser.error("--max-families must be positive")
    if args.interval < 0:
        parser.error("--interval must be non-negative")
    try:
        if args.phase == "pipeline":
            state = run_pipeline(
                out=args.out,
                max_episodes=args.max_episodes,
                max_families=args.max_families,
                interval=args.interval,
                notify_events=args.notify_events,
                once=args.once,
            )
        else:
            state = run_phase(
                args.phase,
                out=args.out,
                max_episodes=args.max_episodes,
                max_families=args.max_families,
                interval=args.interval,
                notify_events=args.notify_events,
                once=args.once,
            )
    except (AlreadySupervised, SupervisorError) as exc:
        print(f"SUPERVISOR STOPPED: {exc}", file=sys.stderr)
        return 2
    if state.get("status") in {"completed", "bounded"}:
        return 0
    if state.get("status") == "completed_with_failures":
        return int(state.get("exit_code") or 3)
    return int(state.get("exit_code") or 2)


if __name__ == "__main__":
    raise SystemExit(main())
