"""Offline tests for the provider recovery watcher."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_provider_watch as watch


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def spec_file(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    (out / "spec.json").write_text(json.dumps({
        "revision": "compiler_v2",
        "revision_schema": "android-selective-revision/1",
        "spec_sha256": "frozen-spec-sha",
    }))


def ready_parts():
    return (
        lambda: {"ready": True, "normal_full_wake": True, "lid_open": True},
        lambda: {"ready": True, "headroom_usd": "1", "in_flight": 0},
        lambda: {"free": True, "path": "run.lock"},
    )


def test_read_ledger_snapshot_is_readonly_and_requires_empty_inflight(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('limit_nano', '10000000000')")
        db.execute("CREATE TABLE calls (state TEXT, reserved_nano INTEGER, actual_nano INTEGER)")
        db.execute("INSERT INTO calls VALUES ('settled', 0, 9900000000)")
    snapshot = watch.read_ledger_snapshot(ledger)
    assert snapshot["ready"] is True
    assert snapshot["headroom_usd"] == "0.1"
    with sqlite3.connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1

    with sqlite3.connect(ledger) as db:
        db.execute("INSERT INTO calls VALUES ('reserved', 1, NULL)")
    assert watch.read_ledger_snapshot(ledger)["ready"] is False


def test_uncertain_history_uses_headroom_but_does_not_block_recovery(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE settings (key TEXT, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('limit_nano', '10000000000')")
        db.execute("CREATE TABLE calls (state TEXT, reserved_nano INTEGER, actual_nano INTEGER)")
        db.executemany(
            "INSERT INTO calls VALUES ('uncertain', 1000000, NULL)",
            [()] * 53,
        )
        db.execute("INSERT INTO calls VALUES ('settled', 0, 9300000000)")
    snapshot = watch.read_ledger_snapshot(ledger)
    assert snapshot["in_flight"] == 0
    assert snapshot["reserved_usd"] == "0.053"
    assert snapshot["ready"] is True
    with sqlite3.connect(ledger) as db:
        db.execute("INSERT INTO calls VALUES ('reserved', 1, NULL)")
    blocked = watch.read_ledger_snapshot(ledger)
    assert blocked["in_flight"] == 1
    assert blocked["ready"] is False


def test_three_valid_checks_dispatch_one_event_and_write_marker(tmp_path):
    spec_file(tmp_path)
    host, ledger, lock = ready_parts()
    metadata_calls = []
    sender_calls = []

    def metadata(model):
        metadata_calls.append(model)
        return {}

    def fake_metadata_check(out, spec, *, metadata_fetcher=None):
        metadata_fetcher("z-ai/glm-5.3-flash")
        return {"valid": True, "models": ["z-ai/glm-5.3-flash"]}

    def sender(command, **kwargs):
        sender_calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(watch, "_metadata_check", fake_metadata_check)
    try:
        state = watch.watch(
            tmp_path,
            notify_events=True,
            interval=60,
            metadata_fetcher=metadata,
            host_state_reader=host,
            ledger_reader=ledger,
            run_lock_reader=lock,
            sender=sender,
            clock=Clock(),
            sleep=lambda seconds: None,
        )
    finally:
        monkeypatch.undo()
    assert state["status"] == "recovery_event_attempted"
    assert state["valid_streak"] == 3
    assert len(metadata_calls) == 3
    assert len(sender_calls) == 1
    event_files = list((tmp_path / "events").glob("*.json"))
    assert len(event_files) == 1
    event = json.loads(event_files[0].read_text())
    assert event["reason"] == "provider_recovered"
    assert event["output_dir"] == str(tmp_path.resolve())
    assert event["spec_sha256"] == "frozen-spec-sha"
    assert event["prerequisites"]["no_ui_replay"] is True
    marker = json.loads((tmp_path / "provider_watch/provider_recovered.json").read_text())
    assert marker["event_key"] == event["event_key"]


def test_bad_check_resets_streak_before_later_recovery(tmp_path):
    spec_file(tmp_path)
    sequence = iter([True, False, True, True, True])
    sender_calls = []

    def fake_readiness(*args, **kwargs):
        return {"valid": next(sequence), "reason": "fake"}

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(watch, "readiness_check", fake_readiness)
    try:
        state = watch.watch(
            tmp_path,
            notify_events=True,
            interval=0,
            sender=lambda *args, **kwargs: sender_calls.append(args),
            clock=Clock(),
            sleep=lambda seconds: None,
        )
    finally:
        monkeypatch.undo()
    assert state["status"] == "recovery_event_attempted"
    assert state["valid_streak"] == 3
    assert len(sender_calls) == 1
    assert len(list((tmp_path / "events").glob("*.json"))) == 1


def test_bad_network_check_is_local_only(tmp_path):
    spec_file(tmp_path)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(watch, "readiness_check", lambda *args, **kwargs: {"valid": False, "reason": "network"})
    sender_calls = []
    try:
        state = watch.watch(
            tmp_path,
            notify_events=True,
            once=True,
            sender=lambda *args, **kwargs: sender_calls.append(args),
            clock=Clock(),
        )
    finally:
        monkeypatch.undo()
    assert state["status"] == "check_complete_no_recovery"
    assert state["valid_streak"] == 0
    assert not sender_calls
    assert not (tmp_path / "events").exists()


def test_existing_marker_prevents_duplicate_recovery_check(tmp_path):
    spec_file(tmp_path)
    marker = tmp_path / "provider_watch/provider_recovered.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"event_key": "already-once"}))
    state = watch.watch(
        tmp_path,
        once=True,
        metadata_fetcher=lambda model: pytest.fail("marker must short-circuit"),
        clock=Clock(),
    )
    assert state["status"] == "already_recorded"


def test_main_requires_out_and_forwards_notify(monkeypatch, tmp_path):
    captured = {}

    def fake_watch(out, **kwargs):
        captured["out"] = out
        captured.update(kwargs)
        return {"status": "check_complete_no_recovery", "valid_streak": 0}

    monkeypatch.setattr(watch, "watch", fake_watch)
    assert watch.main(["--out", str(tmp_path), "--notify-events", "--once"]) == 0
    assert captured["out"] == tmp_path
    assert captured["notify_events"] is True
    assert captured["once"] is True
