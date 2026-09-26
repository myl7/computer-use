"""Offline tests for the selective pilot's process supervisor."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from guiexp_android import selective_supervisor as supervisor


class FakeClock:
    def __init__(self, wall: float = 1000.0, monotonic: float = 1000.0):
        self.wall_now = wall
        self.monotonic_now = monotonic

    def time(self) -> float:
        return self.wall_now

    def monotonic(self) -> float:
        return self.monotonic_now

    def advance(self, seconds: float, *, sleeping: float = 0.0) -> None:
        self.wall_now += seconds + sleeping
        self.monotonic_now += seconds


class FakeProcess:
    _next_pid = 41000

    def __init__(self, codes, *, pid: int | None = None):
        self.codes = list(codes)
        if pid is None:
            pid = FakeProcess._next_pid
            FakeProcess._next_pid += 1
        self.pid = pid
        self.poll_count = 0

    def poll(self):
        self.poll_count += 1
        if len(self.codes) > 1:
            return self.codes.pop(0)
        return self.codes[0]


def fake_popen_factory(codes_by_call, calls):
    def fake_popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return FakeProcess(codes_by_call[len(calls) - 1])

    return fake_popen


def always_verified(pid, command):
    return True


def write_complete_train(out: Path):
    (out / "training_manifest.json").write_text(json.dumps({
        "record_type": "selective-training",
        "status": "ready",
        "families": {family: [{"success": True}] for family in supervisor.FAMILIES},
    }))


def write_complete_build(out: Path):
    (out / "build_manifest.json").write_text(json.dumps({
        "record_type": "selective-build",
        "families": {family: {"status": "built"} for family in supervisor.FAMILIES},
    }))


def write_complete_run(out: Path):
    (out / "progress.json").write_text(json.dumps({
        "record_type": "selective-progress",
        "batch_status": "complete",
        "planned": 1,
        "counts": {"done": 1},
        "episodes": [{"id": "selective_20260915/run/e0", "status": "done"}],
    }))


def test_phase_command_is_frozen_and_forwards_max_episodes():
    assert supervisor.phase_command("train") == [
        "../.venv-android/bin/python",
        "-m",
        "guiexp_android.selective_pilot",
        "--train",
    ]
    assert supervisor.phase_command("build", 3)[-2:] == ["--max-episodes", "3"]
    custom = supervisor.phase_command("run", 2, out="/tmp/selective-v2")
    assert custom[:4] == [
        "../.venv-android/bin/python",
        "-m",
        "guiexp_android.selective_pilot",
        "--run",
    ]
    assert custom[-2:] == ["--out", "/tmp/selective-v2"]
    with pytest.raises(ValueError):
        supervisor.phase_command("pipeline")


def test_active_timer_excludes_host_sleep():
    clock = FakeClock()
    timer = supervisor.ActiveWallTimer(wall=clock.time, monotonic=clock.monotonic)
    timer.sample()
    clock.advance(20)
    timer.sample()
    clock.advance(4, sleeping=1000)
    timer.sample()
    assert timer.active_seconds == pytest.approx(24)
    assert timer.sleep_seconds == pytest.approx(1000)


def test_command_identity_accepts_resolved_executable():
    expected = supervisor.phase_command("run", 2)
    actual = [
        "/Users/myl/app/computer-use/.venv-android/bin/python",
        "-m",
        "guiexp_android.selective_pilot",
        "--run",
        "--max-episodes",
        "2",
    ]
    assert supervisor.command_matches(actual, expected, cwd=supervisor.WORKDIR)
    assert not supervisor.command_matches(actual[:-2] + ["9"], expected, cwd=supervisor.WORKDIR)


def test_train_launch_writes_durable_status_log_and_history(tmp_path):
    write_complete_train(tmp_path)
    calls = []
    popen = fake_popen_factory([[0]], calls)
    state = supervisor.run_phase(
        "train",
        out=tmp_path,
        max_episodes=1,
        interval=0,
        popen=popen,
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    status = json.loads((tmp_path / "batch_status.train.json").read_text())
    assert state["status"] == "completed"
    assert status["worker_pid"] == state["worker_pid"]
    assert status["actual_worker_pid"] == state["worker_pid"]
    assert status["command"] == supervisor.phase_command("train", 1, out=tmp_path)
    assert status["cmd"] == status["command"]
    assert status["cwd"] == str(supervisor.WORKDIR)
    assert len(status["invocation_history"]) == 1
    assert len((tmp_path / "batch_status.train.history.jsonl").read_text().splitlines()) == 1
    assert "supervisor start" in (tmp_path / "train.log").read_text()
    assert calls[0][0] == supervisor.phase_command("train", 1, out=tmp_path)
    assert calls[0][1]["cwd"] == supervisor.WORKDIR


def test_run_normal_exit_invokes_offline_analyze_once(tmp_path):
    write_complete_run(tmp_path)
    calls = []
    popen = fake_popen_factory([[0], [0]], calls)
    state = supervisor.run_phase(
        "run",
        out=tmp_path,
        interval=0,
        popen=popen,
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "completed"
    assert state["analysis_exit_code"] == 0
    assert calls[0][0] == supervisor.phase_command("run", out=tmp_path)
    assert calls[1][0] == supervisor.analysis_command(tmp_path)
    assert "analysis launch" in (tmp_path / "run.log").read_text()


def test_nonzero_exit_records_one_deduplicated_event_without_restart(tmp_path):
    calls = []
    popen = fake_popen_factory([[17]], calls)
    state = supervisor.run_phase(
        "build",
        out=tmp_path,
        interval=0,
        popen=popen,
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 17
    assert len(calls) == 1
    events = list((tmp_path / "events").glob("*.json"))
    assert len(events) == 1
    event = json.loads(events[0].read_text())
    assert event["reason"] == "child_exit_nonzero"
    assert event["delivery"] == "not_requested"


def test_child_exit_between_poll_and_identity_check_uses_real_exit_code(tmp_path):
    calls = []
    process = FakeProcess([None, 2])

    def popen(command, **kwargs):
        calls.append(list(command))
        return process

    state = supervisor.run_phase(
        "build",
        out=tmp_path,
        interval=0,
        popen=popen,
        verifier=lambda pid, command: False,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 2
    assert state["identity_check_race"] is True
    assert state.get("error_type") != "ChildIdentityError"
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["reason"] == "child_exit_nonzero"
    assert len(calls) == 1


def test_zero_exit_with_partial_build_is_terminal_scientific_failure(tmp_path):
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "record_type": "selective-build",
        "families": {
            "MarkorCreateNote": {"status": "unbuildable"},
            "FilesMoveFile": {"status": "built"},
            "MarkorDeleteNote": {"status": "unbuildable"},
        },
    }))
    state = supervisor.run_phase(
        "build",
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[0]], []),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["exit_code"] == 0
    assert state["status"] == "completed_with_failures"
    assert state["semantic_complete"] is True
    assert "unbuildable" in state["error"]
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["reason"] == "phase_scientific_failure"
    assert event["delivery"] == "not_requested"
    assert event["artifact_assessment"]["family_statuses"]["MarkorCreateNote"] == "unbuildable"


def test_zero_exit_with_pending_run_rows_analyzes_but_stays_incomplete(tmp_path):
    (tmp_path / "progress.json").write_text(json.dumps({
        "record_type": "selective-progress",
        "batch_status": "complete",
        "planned": 3,
        "counts": {"done": 1, "pending": 2},
        "episodes": [
            {"id": "selective_20260915/run/e0", "status": "done"},
            {"id": "selective_20260915/run/e1", "status": "pending"},
            {"id": "selective_20260915/run/e2", "status": "pending"},
        ],
    }))
    calls = []
    state = supervisor.run_phase(
        "run",
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[0], [0]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "incomplete"
    assert state["analysis_exit_code"] == 0
    assert len(calls) == 2
    assert calls[1][0] == supervisor.analysis_command(tmp_path)
    assert json.loads(next((tmp_path / "events").glob("*.json")).read_text())["reason"] == "phase_incomplete"


def test_nonzero_run_exit_analyzes_and_preserves_child_failure(tmp_path):
    calls = []
    state = supervisor.run_phase(
        "run",
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[7], [0]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 7
    assert state["analysis_exit_code"] == 0
    assert len(calls) == 2 and calls[1][0] == supervisor.analysis_command(tmp_path)
    assert json.loads(next((tmp_path / "events").glob("*.json")).read_text())["reason"] == "child_exit_nonzero"


def test_main_returns_failure_for_semantically_incomplete_zero_exit(monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "run_phase",
        lambda *args, **kwargs: {"status": "incomplete", "exit_code": 0},
    )
    assert supervisor.main(["--phase", "build"]) == 2


def test_main_returns_success_for_requested_bounded_phase(monkeypatch):
    monkeypatch.setattr(
        supervisor,
        "run_phase",
        lambda *args, **kwargs: {"status": "bounded", "exit_code": 0},
    )
    assert supervisor.main(["--phase", "train", "--max-episodes", "1"]) == 0


def test_compiler_v2_revision_dispatches_and_recognizes_max_family_bound(tmp_path):
    (tmp_path / "spec.json").write_text(json.dumps({
        "revision": "compiler_v2",
        "revision_schema": "android-selective-revision/1",
        "families": list(supervisor.FAMILIES),
    }))
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "record_type": "compiler_v2-build",
        "status": "pilot_limit",
        "families": {"MarkorCreateNote": {"status": "built"}},
    }))
    calls = []
    state = supervisor.run_phase(
        "build",
        out=tmp_path,
        max_families=1,
        interval=0,
        popen=fake_popen_factory([[3]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert supervisor.harness_module(tmp_path) == supervisor.REVISION_MODULE
    assert calls[0][0][:4] == [
        "../.venv-android/bin/python",
        "-m",
        "guiexp_android.selective_revision",
        "--build",
    ]
    assert calls[0][0][-4:] == ["--max-families", "1", "--out", str(tmp_path)]
    assert state["status"] == "bounded"
    assert state["exit_code"] == 3
    assert not (tmp_path / "events").exists()
    assert supervisor.analysis_command(tmp_path)[2] == supervisor.REVISION_MODULE


def test_main_parses_and_forwards_custom_out_and_max_families(monkeypatch, tmp_path):
    captured = {}

    def fake_run_phase(phase, **kwargs):
        captured.update(phase=phase, kwargs=kwargs)
        return {"status": "bounded", "exit_code": 3}

    monkeypatch.setattr(supervisor, "run_phase", fake_run_phase)
    assert supervisor.main([
        "--phase", "build",
        "--out", str(tmp_path),
        "--max-families", "1",
    ]) == 0
    assert captured["phase"] == "build"
    assert captured["kwargs"]["out"] == tmp_path
    assert captured["kwargs"]["max_families"] == 1


def test_stall_event_keeps_child_alive_and_is_written_once(tmp_path):
    write_complete_train(tmp_path)
    clock = FakeClock()
    calls = []
    process = FakeProcess([None, None, 0])

    def popen(command, **kwargs):
        calls.append(list(command))
        return process

    def sleep(seconds):
        clock.advance(901)

    state = supervisor.run_phase(
        "train",
        out=tmp_path,
        interval=30,
        popen=popen,
        verifier=always_verified,
        clock=clock,
        sleep=sleep,
    )
    assert state["status"] == "completed"
    assert state["stalled"] is True
    assert process.poll_count >= 3
    assert len(list((tmp_path / "events").glob("*.json"))) == 1
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["reason"] == "no_progress_15_minutes"
    assert state["event_key"] == event["event_key"]


def test_notify_events_queues_failure_once_and_contains_recovery_bounds(tmp_path):
    calls = []
    queue_calls = []

    def sender(command, **kwargs):
        queue_calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    state = supervisor.run_phase(
        "build",
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[2]], calls),
        verifier=always_verified,
        sender=sender,
        notify_events=True,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert len(queue_calls) == 1
    command = queue_calls[0][0]
    assert command[:4] == [supervisor.CODEX, "queue", "--thread", supervisor.THREAD]
    prompt = command[-1]
    assert "USD 10" in prompt
    assert "revision_20260913/budget.sqlite3" in prompt
    assert "Do not replay UI episodes" in prompt
    assert "scheduled message or heartbeat" in prompt

    # Re-dispatching the same durable event does not call the sender again.
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert not supervisor.dispatch_event(event, tmp_path, sender=sender)
    assert len(queue_calls) == 1


def test_pipeline_stops_after_first_nonzero_stage(tmp_path):
    write_complete_train(tmp_path)
    calls = []
    state = supervisor.run_pipeline(
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[0], [9]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["failed_stage"] == "build"
    assert [item["phase"] for item in state["stages"]] == ["train", "build"]
    assert len(calls) == 2
    assert "--train" in calls[0][0]
    assert "--build" in calls[1][0]
    assert not (tmp_path / "batch_status.run.json").exists()


def test_pipeline_success_runs_all_stages_then_analysis(tmp_path):
    write_complete_train(tmp_path)
    write_complete_build(tmp_path)
    write_complete_run(tmp_path)
    calls = []
    state = supervisor.run_pipeline(
        out=tmp_path,
        max_episodes=2,
        interval=0,
        popen=fake_popen_factory([[0], [0], [0], [0]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "completed"
    assert [item["phase"] for item in state["stages"]] == ["train", "build", "run"]
    assert [call[0] for call in calls[:3]] == [
        supervisor.phase_command("train", 2, out=tmp_path),
        supervisor.phase_command("build", 2, out=tmp_path),
        supervisor.phase_command("run", 2, out=tmp_path),
    ]
    assert calls[3][0] == supervisor.analysis_command(tmp_path)
    assert json.loads((tmp_path / "batch_status.pipeline.json").read_text())["status"] == "completed"


def test_pipeline_continues_after_terminal_unbuildable_family(tmp_path):
    write_complete_train(tmp_path)
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "record_type": "selective-build",
        "families": {
            "MarkorCreateNote": {"status": "built"},
            "FilesMoveFile": {"status": "unbuildable"},
            "MarkorDeleteNote": {"status": "built"},
        },
    }))
    write_complete_run(tmp_path)
    calls = []
    state = supervisor.run_pipeline(
        out=tmp_path,
        interval=0,
        popen=fake_popen_factory([[0], [3], [0], [0]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "completed_with_failures"
    assert [item["phase"] for item in state["stages"]] == ["train", "build", "run"]
    assert len(calls) == 4
    assert calls[2][0][3] == "--run"
    assert state["scientific_failures"][0]["phase"] == "build"


def test_max_episodes_is_bounded_without_failure_event(tmp_path):
    (tmp_path / "training_manifest.json").write_text(json.dumps({
        "record_type": "selective-training",
        "status": "partial",
        "families": {"MarkorCreateNote": [{"success": True}]},
    }))
    calls = []
    state = supervisor.run_phase(
        "train",
        out=tmp_path,
        max_episodes=1,
        interval=0,
        popen=fake_popen_factory([[0]], calls),
        verifier=always_verified,
        clock=FakeClock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "bounded"
    assert state["semantic_complete"] is False
    assert not (tmp_path / "events").exists()


def test_readonly_receipt_is_progress_evidence(tmp_path):
    ledger = tmp_path / "budget.sqlite3"
    with sqlite3.connect(ledger) as database:
        database.execute(
            "CREATE TABLE calls (id TEXT, episode TEXT, created REAL)"
        )
        database.execute(
            "INSERT INTO calls VALUES (?, ?, ?)",
            ("selective_20260915/build/call-1", "selective_20260915/build/ep-1", 1234.0),
        )
    evidence = supervisor.progress_evidence(
        tmp_path,
        "build",
        started_unix=1200.0,
        now=1300.0,
        ledger=ledger,
    )
    assert evidence["source"] == "receipt"
    assert evidence["timestamp_unix"] == 1234.0


def test_running_child_from_previous_supervisor_blocks_new_launch(tmp_path):
    old = {
        "phase": "train",
        "status": "running",
        "worker_pid": 9001,
        "command": supervisor.phase_command("train"),
    }
    (tmp_path / "batch_status.train.json").write_text(json.dumps(old))
    with pytest.raises(supervisor.SupervisorError, match="still running"):
        supervisor.run_phase(
            "build",
            out=tmp_path,
            popen=lambda *args, **kwargs: pytest.fail("must not launch"),
            verifier=always_verified,
        )
