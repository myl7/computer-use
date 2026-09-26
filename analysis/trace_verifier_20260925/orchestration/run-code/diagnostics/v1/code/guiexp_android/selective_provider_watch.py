"""Watch provider recovery without starting another paid experiment.

This process performs bounded, free provider metadata checks and local safety
checks for a frozen selective build.  Three consecutive valid checks create
one durable ``provider_recovered`` event.  With ``--notify-events`` the event
is handed to the existing Codex queue adapter.  The watcher never starts a
build, run, emulator, or model request.

Run from the canonical project directory::

    ../.venv-android/bin/python -m guiexp_android.selective_provider_watch \
        --out /Users/myl/app/computer-use/experimental-results/guiexp_android/selective_20260915/versions/compiler_v2 \
        --notify-events
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from typing import Any, Callable, Iterator, Mapping, Sequence

from .selective_supervisor import (
    OLD_OUT,
    OUT,
    REVISION,
    REVISION_SCHEMA,
    SHARED_LEDGER,
    atomic_json,
    dispatch_event,
    pid_alive,
    read_json,
)


DEFAULT_INTERVAL = 60.0
REQUIRED_VALID_CHECKS = 3
MIN_HEADROOM_USD = Decimal("0.09928704")
EXPECTED_LIMIT_NANO = Decimal("10000000000")
KNOWN_LEDGER_STATES = frozenset({"settled", "reserved", "inflight", "in_flight", "uncertain", "overrun"})
IN_FLIGHT_STATES = frozenset({"reserved", "inflight", "in_flight"})
PILOT_MODULE = "guiexp_android.selective_pilot"
REVISION_MODULE = "guiexp_android.selective_revision"
BUILD_PHASE = "build"


class WatchError(RuntimeError):
    """A local watcher guard failed before recovery notification."""


def _safe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


def _out_paths(out: Path) -> tuple[Path, Path, Path]:
    out = Path(out)
    watch_dir = out / "provider_watch"
    return watch_dir, watch_dir / "status.json", watch_dir / "watch.lock"


def _marker_path(out: Path) -> Path:
    return _out_paths(out)[0] / "provider_recovered.json"


def source_sha256() -> str | None:
    """Return the watcher source hash for local auditability."""

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return None


def _spec(out: Path) -> dict[str, Any]:
    path = Path(out) / "spec.json"
    record = read_json(path)
    if not isinstance(record, Mapping):
        raise WatchError(f"missing or unreadable {path}")
    return dict(record)


def _revision_module(out: Path, spec: Mapping[str, Any]) -> str:
    if (
        spec.get("revision") == REVISION
        and spec.get("revision_schema") == REVISION_SCHEMA
    ):
        return REVISION_MODULE
    return PILOT_MODULE


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def read_ledger_snapshot(
    ledger: Path = SHARED_LEDGER,
    *,
    minimum_headroom: Decimal = MIN_HEADROOM_USD,
) -> dict[str, Any]:
    """Read the shared ledger through SQLite's read-only URI mode."""

    ledger = Path(ledger)
    if not ledger.is_file():
        return {"ready": False, "reason": "shared ledger is missing", "path": str(ledger)}
    try:
        with sqlite3.connect(f"file:{ledger.resolve()}?mode=ro", uri=True, timeout=3) as db:
            rows = db.execute(
                "SELECT state, reserved_nano, actual_nano FROM calls"
            ).fetchall()
            settings = dict(db.execute("SELECT key, value FROM settings").fetchall())
    except (OSError, sqlite3.Error) as exc:
        return {"ready": False, "reason": _safe_error(exc), "path": str(ledger)}

    try:
        limit = _decimal(settings.get("limit_nano"))
        if limit != EXPECTED_LIMIT_NANO:
            return {
                "ready": False,
                "reason": "shared ledger limit is not the configured USD 10 ceiling",
                "path": str(ledger),
            }
        limit_usd = limit / Decimal("1000000000")
        states = {str(row[0]) for row in rows}
        unknown_states = sorted(states - KNOWN_LEDGER_STATES)
        if unknown_states:
            return {
                "ready": False,
                "reason": f"shared ledger has unknown state(s): {unknown_states}",
                "path": str(ledger),
            }
        actual_nano = sum(int(row[2] or 0) for row in rows)
        reserved_nano = sum(int(row[1] or 0) for row in rows if row[0] != "settled")
    except (TypeError, ValueError, InvalidOperation):
        return {"ready": False, "reason": "shared ledger contains invalid billing values", "path": str(ledger)}
    occupied = (Decimal(actual_nano) + Decimal(reserved_nano)) / Decimal("1000000000")
    headroom = limit_usd - occupied
    in_flight = sum(row[0] in IN_FLIGHT_STATES for row in rows)
    overrun = sum(row[0] == "overrun" for row in rows)
    ready = in_flight == 0 and overrun == 0 and headroom >= minimum_headroom
    return {
        "ready": ready,
        "path": str(ledger),
        "limit_usd": str(limit_usd),
        "actual_usd": str(Decimal(actual_nano) / Decimal("1000000000")),
        "reserved_usd": str(Decimal(reserved_nano) / Decimal("1000000000")),
        "occupied_usd": str(occupied),
        "headroom_usd": str(headroom),
        "in_flight": in_flight,
        "reason": None if ready else (
            "reserved or in-flight ledger rows remain" if in_flight else
            "shared ledger contains an overrun" if overrun else
            f"headroom below USD {minimum_headroom}"
        ),
        "overrun": overrun,
    }


def probe_shared_run_lock(path: Path = OLD_OUT / "run.lock") -> dict[str, Any]:
    """Try and immediately release the existing worker lock."""

    path = Path(path)
    if not path.is_file():
        return {"free": False, "path": str(path), "reason": "shared run.lock is missing"}
    handle = None
    try:
        handle = path.open("r+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"free": False, "path": str(path), "reason": "shared run.lock is held"}
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        return {"free": True, "path": str(path), "reason": None}
    except OSError as exc:
        return {"free": False, "path": str(path), "reason": _safe_error(exc)}
    finally:
        if handle is not None:
            handle.close()


def _metadata_check(
    out: Path,
    spec: Mapping[str, Any],
    *,
    metadata_fetcher: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Validate the frozen builder endpoint with free metadata only."""

    try:
        from . import selective_budget

        locks = spec.get("model_locks")
        fetcher = metadata_fetcher
        result = selective_budget.validate_builder_locks(
            locks,
            profile="builder_16384",
            metadata_fetcher=fetcher,
            sleep=lambda _seconds: None,
        )
        return {"valid": True, "models": sorted(result)}
    except Exception as exc:
        return {"valid": False, "reason": _safe_error(exc)}


def readiness_check(
    out: Path,
    *,
    spec: Mapping[str, Any] | None = None,
    metadata_fetcher: Callable[[str], Any] | None = None,
    host_state_reader: Callable[[], Mapping[str, Any] | bool | None] | None = None,
    ledger_reader: Callable[[], Mapping[str, Any]] | None = None,
    run_lock_reader: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate provider, host, billing, and runner-lock prerequisites."""

    out = Path(out)
    spec = dict(spec) if isinstance(spec, Mapping) else _spec(out)
    module = _revision_module(out, spec)
    metadata = _metadata_check(out, spec, metadata_fetcher=metadata_fetcher)
    if host_state_reader is None:
        from .selective_budget import read_host_state

        host_state_reader = read_host_state
    try:
        host_value = host_state_reader()
    except Exception as exc:
        host_value = {"ready": False, "reason": _safe_error(exc)}
    if isinstance(host_value, Mapping):
        host_ready = bool(host_value.get("ready")) and bool(
            host_value.get("normal_full_wake", host_value.get("ready"))
        )
    else:
        host_ready = bool(host_value)
    host = dict(host_value) if isinstance(host_value, Mapping) else {"ready": host_ready}
    host["ready"] = host_ready
    ledger = dict(ledger_reader() if ledger_reader is not None else read_ledger_snapshot())
    lock = dict(run_lock_reader() if run_lock_reader is not None else probe_shared_run_lock())
    valid = bool(metadata.get("valid")) and host_ready and bool(ledger.get("ready")) and bool(lock.get("free"))
    reasons = []
    if not metadata.get("valid"):
        reasons.append("provider metadata invalid")
    if not host_ready:
        reasons.append("host is not in normal full wake")
    if not ledger.get("ready"):
        reasons.append(str(ledger.get("reason") or "shared ledger guard failed"))
    if not lock.get("free"):
        reasons.append(str(lock.get("reason") or "shared runner lock is held"))
    return {
        "valid": valid,
        "module": module,
        "spec_sha256": spec.get("spec_sha256"),
        "metadata": metadata,
        "host": host,
        "ledger": ledger,
        "run_lock": lock,
        "reason": None if valid else "; ".join(reasons),
    }


def _event_key(out: Path, spec: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        f"provider_recovered|{Path(out).resolve()}|{spec.get('spec_sha256', '')}".encode()
    ).hexdigest()[:24]
    return f"provider_recovered-{digest}"


def _live_status(out: Path, phase: str = BUILD_PHASE) -> dict[str, Any]:
    status = read_json(Path(out) / f"batch_status.{phase}.json", {})
    if not isinstance(status, Mapping):
        return {}
    result = {
        key: status.get(key)
        for key in (
            "phase", "status", "pid", "supervisor_pid", "worker_pid", "actual_worker_pid",
            "command", "exit_code", "runner_exit_code", "error_type", "error", "updated_unix",
        )
        if key in status
    }
    return result


def _latest_log(out: Path, phase: str = BUILD_PHASE) -> dict[str, Any]:
    path = Path(out) / f"{phase}.log"
    try:
        stat = path.stat()
    except OSError:
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "mtime_unix": stat.st_mtime,
        "size_bytes": stat.st_size,
    }


def _recovery_event(out: Path, spec: Mapping[str, Any], check: Mapping[str, Any], now: float) -> dict[str, Any]:
    return {
        "event_key": _event_key(out, spec),
        "phase": BUILD_PHASE,
        "reason": "provider_recovered",
        "source": "selective_provider_watch",
        "detected_unix": now,
        "output_dir": str(Path(out).resolve()),
        "spec_path": str(Path(out) / "spec.json"),
        "spec_sha256": spec.get("spec_sha256"),
        "revision": spec.get("revision"),
        "revision_schema": spec.get("revision_schema"),
        "watch_source_sha256": source_sha256(),
        "latest_batch_status": _live_status(out),
        "latest_log": _latest_log(out),
        "check": dict(check),
        "prerequisites": {
            "no_ui_replay": True,
            "same_shared_ledger": str(SHARED_LEDGER),
            "same_shared_run_lock": str(OLD_OUT / "run.lock"),
        },
    }


def _record_local_event(out: Path, event: Mapping[str, Any]) -> bool:
    events = Path(out) / "events"
    events.mkdir(parents=True, exist_ok=True)
    path = events / f"{event['event_key']}.json"
    if path.exists():
        return False
    atomic_json(path, dict(event, delivery="not_requested"))
    return True


def _write_status(path: Path, value: Mapping[str, Any]) -> None:
    atomic_json(path, dict(value))


@contextmanager
def watch_lock(out: Path) -> Iterator[Path]:
    """Hold a watcher-only lock, independent of the worker run lock."""

    watch_dir, _status, lock_path = _out_paths(out)
    watch_dir.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WatchError("provider watcher is already running") from exc
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def watch(
    out: Path,
    *,
    notify_events: bool = False,
    interval: float = DEFAULT_INTERVAL,
    once: bool = False,
    metadata_fetcher: Callable[[str], Any] | None = None,
    host_state_reader: Callable[[], Mapping[str, Any] | bool | None] | None = None,
    ledger_reader: Callable[[], Mapping[str, Any]] | None = None,
    run_lock_reader: Callable[[], Mapping[str, Any]] | None = None,
    sender: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    """Poll until recovery is proven or one bounded check is requested."""

    out = Path(out).resolve()
    spec = _spec(out)
    watch_dir, status_path, _lock_path = _out_paths(out)
    marker_path = _marker_path(out)
    with watch_lock(out):
        if marker_path.is_file():
            state = {
                "pid": os.getpid(),
                "status": "already_recorded",
                "output_dir": str(out),
                "spec_sha256": spec.get("spec_sha256"),
                "marker": str(marker_path),
                "watch_source_sha256": source_sha256(),
                "updated_unix": clock(),
            }
            _write_status(status_path, state)
            return state

        state: dict[str, Any] = {
            "pid": os.getpid(),
            "status": "watching",
            "output_dir": str(out),
            "spec_sha256": spec.get("spec_sha256"),
            "watch_lock": str(_lock_path),
            "worker_lock": str(OLD_OUT / "run.lock"),
            "ledger": str(SHARED_LEDGER),
            "watch_source_sha256": source_sha256(),
            "interval_seconds": interval,
            "required_valid_checks": REQUIRED_VALID_CHECKS,
            "valid_streak": 0,
            "notify_events": notify_events,
            "updated_unix": clock(),
        }
        _write_status(status_path, state)
        atomic_json(watch_dir / "pid.json", {
            "pid": os.getpid(),
            "started_unix": state["updated_unix"],
            "watch_source_sha256": state["watch_source_sha256"],
        })
        while True:
            checked = float(clock())
            check = readiness_check(
                out,
                spec=spec,
                metadata_fetcher=metadata_fetcher,
                host_state_reader=host_state_reader,
                ledger_reader=ledger_reader,
                run_lock_reader=run_lock_reader,
            )
            if check.get("valid"):
                state["valid_streak"] = int(state.get("valid_streak", 0)) + 1
            else:
                # Network/provider flapping is a local observation.  Reset
                # the streak and never wake the model for it.
                state["valid_streak"] = 0
            state.update(
                status="checking",
                last_check_unix=checked,
                last_check=check,
                updated_unix=checked,
            )
            if state["valid_streak"] >= REQUIRED_VALID_CHECKS:
                event = _recovery_event(out, spec, check, checked)
                if notify_events:
                    delivered = dispatch_event(event, out, sender=sender)
                    state["event_delivery_attempted"] = True
                    state["event_recorded"] = delivered or (Path(out) / "events" / f"{event['event_key']}.json").is_file()
                else:
                    state["event_delivery_attempted"] = False
                    state["event_recorded"] = _record_local_event(out, event)
                state.update(
                    status="recovery_event_attempted",
                    recovery_event_key=event["event_key"],
                    ended_unix=float(clock()),
                )
                _write_status(status_path, state)
                marker = dict(
                    event_key=event["event_key"],
                    status=state["status"],
                    delivery=(read_json(Path(out) / "events" / f"{event['event_key']}.json", {}) or {}).get("delivery"),
                    ended_unix=state["ended_unix"],
                    output_dir=str(out),
                    spec_sha256=spec.get("spec_sha256"),
                )
                atomic_json(marker_path, marker)
                return state
            _write_status(status_path, state)
            if once:
                state["status"] = "check_complete_no_recovery"
                _write_status(status_path, state)
                return state
            sleep(max(0.0, interval))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--notify-events", action="store_true")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.interval < 0:
        parser.error("--interval must be non-negative")
    try:
        state = watch(
            args.out,
            notify_events=args.notify_events,
            interval=args.interval,
            once=args.once,
        )
    except WatchError as exc:
        print(f"WATCH STOPPED: {exc}")
        return 2
    print(json.dumps({key: state.get(key) for key in ("status", "valid_streak", "recovery_event_key", "event_recorded")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
