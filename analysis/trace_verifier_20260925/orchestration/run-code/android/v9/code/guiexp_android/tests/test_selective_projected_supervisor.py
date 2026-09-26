"""Fake-process checks for the projected-v4 supervisor."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_projected_supervisor as supervisor


class Clock:
    def __init__(self):
        self.wall_now = 1000.0
        self.mono_now = 1000.0

    def time(self):
        return self.wall_now

    def monotonic(self):
        return self.mono_now


class Process:
    next_pid = 51000

    def __init__(self, codes):
        self.codes = list(codes)
        self.pid = Process.next_pid
        Process.next_pid += 1

    def poll(self):
        if len(self.codes) > 1:
            return self.codes.pop(0)
        return self.codes[0]


def popen_factory(code_sets, calls):
    def popen(command, **kwargs):
        calls.append((list(command), kwargs))
        return Process(code_sets[len(calls) - 1])

    return popen


def verified(pid, command):
    return True


def test_explore_command_forwards_phase_bounds_and_out():
    command = supervisor.explore_command(
        "build", "/tmp/provider_v3", max_families=1
    )
    assert command == [
        "../.venv-android/bin/python",
        "-m",
        "guiexp_android.selective_projected",
        "--build",
        "--max-families",
        "1",
        "--out",
        "/tmp/provider_v3",
    ]
    with pytest.raises(ValueError):
        supervisor.explore_command("build", "/tmp/x", max_episodes=2)
    repair = supervisor.explore_command(
        "run", "/tmp/repair", harness_module=supervisor.REPAIR_DIAGNOSTIC_MODULE,
    )
    assert repair[2] == supervisor.REPAIR_DIAGNOSTIC_MODULE
    with pytest.raises(ValueError):
        supervisor.explore_command(
            "build", "/tmp/repair", harness_module=supervisor.REPAIR_DIAGNOSTIC_MODULE,
        )


def test_normal_build_exit_is_local_completion(tmp_path):
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "record_type": "projected_v4-build",
        "status": "complete",
        "families": {"MarkorDeleteNote": {"status": "built"}},
    }))
    calls = []
    state = supervisor.supervise(
        "build",
        tmp_path,
        interval=0,
        popen=popen_factory([[0]], calls),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "completed"
    assert state["exit_code"] == 0
    assert calls[0][0][2] == "guiexp_android.selective_projected"
    assert state["version"] == "projected_v4"
    assert json.loads((tmp_path / "batch_status.build.json").read_text())["worker_pid"] == state["worker_pid"]
    assert not (tmp_path / "events").exists()


def test_zero_exit_without_build_artifact_is_incomplete(tmp_path):
    calls = []
    state = supervisor.supervise(
        "build",
        tmp_path,
        interval=0,
        popen=popen_factory([[0]], calls),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "incomplete"
    assert state["artifact_assessment"]["reason"] == "missing_build_manifest"
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["reason"] == "phase_incomplete"


def test_build_pilot_limit_exit3_is_bounded_without_event(tmp_path):
    (tmp_path / "build_manifest.json").write_text(json.dumps({
        "record_type": "projected_v4-build",
        "status": "pilot_limit",
        "families": {"MarkorDeleteNote": {"status": "built"}},
    }))
    calls = []
    state = supervisor.supervise(
        "build",
        tmp_path,
        max_families=1,
        interval=0,
        popen=popen_factory([[3]], calls),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "bounded"
    assert state["exit_code"] == 3
    assert state["bounded_artifact"]["status"] == "pilot_limit"
    assert not (tmp_path / "events").exists()


def test_stale_pilot_limit_is_not_treated_as_current_bound(tmp_path):
    path = tmp_path / "build_manifest.json"
    path.write_text(json.dumps({"status": "pilot_limit", "families": {"old": {"status": "built"}}}))
    os.utime(path, (900.0, 900.0))
    state = supervisor.supervise(
        "build",
        tmp_path,
        max_families=1,
        interval=0,
        popen=popen_factory([[3]], []),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["artifact_assessment"]["reason"] == "build_manifest_incomplete"


def test_requested_episode_quota_is_bounded_with_deferred_rows(tmp_path):
    (tmp_path / "progress.json").write_text(json.dumps({
        "record_type": "projected_v6-progress",
        "batch_status": "incomplete",
        "planned": 15,
        "completed_in_invocation": 3,
        "counts": {"done": 6, "pending": 9},
        "episodes": [{"id": f"e{i}", "status": "done"} for i in range(6)],
    }))
    state = supervisor.supervise(
        "run",
        tmp_path,
        max_episodes=3,
        interval=0,
        popen=popen_factory([[2]], []),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "bounded"
    assert state["artifact_assessment"]["reason"] == "run_quota_fulfilled"
    assert not (tmp_path / "events").exists()


def test_budget_stop_is_not_masked_as_quota_completion(tmp_path):
    (tmp_path / "progress.json").write_text(json.dumps({
        "record_type": "projected_v6-progress",
        "batch_status": "incomplete",
        "planned": 15,
        "completed_in_invocation": 3,
        "counts": {"done": 6, "pending": 8, "budget_stopped": 1},
        "episodes": [{"id": f"e{i}", "status": "done"} for i in range(6)]
        + [{"id": "e-stop", "status": "budget_stopped"}],
    }))
    state = supervisor.supervise(
        "run",
        tmp_path,
        max_episodes=3,
        interval=0,
        popen=popen_factory([[2]], []),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["artifact_assessment"]["reason"] == "progress_incomplete"
    assert len(list((tmp_path / "events").glob("*.json"))) == 1


def test_failure_event_uses_existing_sender_once(tmp_path):
    calls = []
    sent = []

    def sender(command, **kwargs):
        sent.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    state = supervisor.supervise(
        "run",
        tmp_path,
        max_episodes=2,
        interval=0,
        notify_events=True,
        popen=popen_factory([[2]], calls),
        verifier=verified,
        sender=sender,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 2
    assert len(sent) == 1
    assert sent[0][0][:4] == [
        supervisor.base.CODEX,
        "queue",
        "--thread",
        supervisor.base.THREAD,
    ]
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["reason"] == "child_exit_nonzero"
    assert event["total_occupied_ceiling_usd"] == "20"
    assert event["new_tranche_occupied_ceiling_usd"] == "10"
    assert event["authorization_request"].endswith("budget_authorization_request_20260915.json")
    assert event["output_dir"] == str(tmp_path.resolve())


def test_identity_race_keeps_child_exit_code(tmp_path):
    calls = []
    state = supervisor.supervise(
        "run",
        tmp_path,
        interval=0,
        popen=popen_factory([[None, 2]], calls),
        verifier=lambda pid, command: False,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["status"] == "failed"
    assert state["exit_code"] == 2
    assert state["identity_check_race"] is True
    assert state.get("error_type") != "ChildIdentityError"


def test_main_accepts_required_out_and_bounds(monkeypatch, tmp_path):
    captured = {}

    def fake_supervise(phase, out, **kwargs):
        captured.update(phase=phase, out=out, kwargs=kwargs)
        return {"status": "bounded", "exit_code": 3}

    monkeypatch.setattr(supervisor, "supervise", fake_supervise)
    assert supervisor.main([
        "--phase", "build", "--out", str(tmp_path), "--max-families", "1",
    ]) == 0
    assert captured["phase"] == "build"
    assert captured["out"] == tmp_path
    assert captured["kwargs"]["max_families"] == 1


def test_frozen_spec_version_drives_status_and_event_identity(tmp_path):
    (tmp_path / "spec.json").write_text(json.dumps({
        "revision": "projected_v5",
        "version": "projected_v5",
        "spec_sha256": "v5-spec-sha",
    }))
    calls = []
    state = supervisor.supervise(
        "run",
        tmp_path,
        interval=0,
        popen=popen_factory([[2]], calls),
        verifier=verified,
        clock=Clock(),
        sleep=lambda seconds: None,
    )
    assert state["version"] == "projected_v5"
    event = json.loads(next((tmp_path / "events").glob("*.json")).read_text())
    assert event["version"] == "projected_v5"
    assert event["module"] == "guiexp_android.selective_projected"
