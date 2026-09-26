"""Offline fake-subprocess checks for the bounded Markor label probe v2."""

from __future__ import annotations

import hashlib
import contextlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_markor_label_probe_v2 as probe


def _ledger(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('limit_nano','10000000000')")
        db.execute(
            "CREATE TABLE calls (id TEXT PRIMARY KEY, state TEXT, reserved_nano INTEGER, actual_nano INTEGER)"
        )
        db.execute("INSERT INTO calls VALUES ('old','settled',0,2378358288)")


class FakeSubprocess:
    def __init__(
        self,
        spec,
        *,
        visible_after_relaunch=True,
        bad_png=False,
        initial_services="null",
        initial_accessibility_enabled="0",
        restore_mismatch=False,
        fail_on_push=False,
    ):
        self.spec = spec
        self.visible_after_relaunch = visible_after_relaunch
        self.bad_png = bad_png
        self.settings = {
            probe.SECURE_ENABLED_SERVICES: initial_services,
            probe.SECURE_ACCESSIBILITY_ENABLED: initial_accessibility_enabled,
        }
        self.restore_mismatch = restore_mismatch
        self.fail_on_push = fail_on_push
        self.settings_puts = 0
        self.restoration_phase = False
        self.opened = False
        self.force_stop_seen = False
        self.relaunched = False
        self.swipes = 0
        self.files: dict[str, bytes] = {}
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == probe.TIMEOUT
        args = command[3:]
        self.calls.append(args)
        out, err, code = b"", b"", 0
        names = self.spec["filenames"]

        if args == ["get-state"]:
            out = b"device\n"
        elif args[:4] == ["shell", "dumpsys", "package", probe.PACKAGE]:
            out = b"versionCode=146 minSdk=23\nversionName=2.10.9\n"
        elif args[:4] == ["shell", "settings", "get", "secure"]:
            key = args[4]
            value = self.settings[key]
            if (
                self.restore_mismatch
                and self.restoration_phase
                and key == probe.SECURE_ACCESSIBILITY_ENABLED
            ):
                value = "1"
            out = ("null\n" if value == "null" else f"{value}\n").encode()
        elif args[:4] == ["shell", "settings", "put", "secure"]:
            key, value = args[4], args[5]
            self.settings[key] = "" if value == '""' else value
            self.settings_puts += 1
        elif args[:4] == ["shell", "settings", "delete", "secure"]:
            self.settings[args[4]] = "null"
        elif args[:4] == ["shell", "dumpsys", "package", probe.FORWARDER_PACKAGE]:
            out = (
                f"Package [{probe.FORWARDER_PACKAGE}]\n"
                f"versionCode=0 minSdk=28 targetSdk=24 versionName=null\n"
                f"{probe.FORWARDER_PACKAGE}/.AccessibilityForwarder\n"
            ).encode()
        elif args[:3] == ["shell", "dumpsys", "window"]:
            activity = probe.PACKAGE + ("/.MainActivity" if self.opened else "/.Launcher")
            out = f"mCurrentFocus=Window{{u0 {activity}}}\n".encode()
        elif args[:3] == ["exec-out", "uiautomator", "dump"]:
            if self.bad_png:
                out = (
                    f'<hierarchy package="{probe.PACKAGE}"><node '
                    f'class="androidx.recyclerview.widget.RecyclerView" '
                    f'text="{probe.MARKOR_DATA}"/></hierarchy>'
                ).encode()
            else:
                visible = (
                    names
                    if self.relaunched and self.visible_after_relaunch
                    else names[:2]
                    if self.relaunched
                    else []
                )
                labels = "".join(
                    f'<node class="android.widget.TextView" text="{name}" '
                    f'content-desc="desc {name}" bounds="[0,0][10,10]"/>'
                    for name in visible
                )
                out = (
                    f'<hierarchy package="{probe.PACKAGE}"><node '
                    f'class="androidx.recyclerview.widget.RecyclerView" '
                    f'text="{probe.MARKOR_DATA}"/>{labels}</hierarchy>'
                ).encode()
        elif args[:3] == ["exec-out", "screencap", "-p"]:
            out = b"bad" if self.bad_png else probe.PNG_MAGIC + b"fake"
        elif args[:4] == ["shell", "cmd", "package", "resolve-activity"]:
            out = f"priority=0\n{probe.PACKAGE}/.MainActivity\n".encode()
        elif args[:3] == ["shell", "am", "start"]:
            self.opened = True
            if self.force_stop_seen:
                self.relaunched = True
            out = b"Starting: Intent\n"
        elif args[:4] == ["shell", "am", "force-stop", probe.PACKAGE]:
            self.force_stop_seen = True
            self.opened = False
        elif args[:3] == ["shell", "am", "force-stop"]:
            assert args[3] == probe.FORWARDER_PACKAGE
        elif args[:3] == ["shell", "ls", "-ld"]:
            target = args[3]
            name = target.rsplit("/", 1)[-1]
            if name in self.files:
                out = f"-rw------- 1 u0_a 0 {target}\n".encode()
            else:
                code, err = 1, f"ls: {target}: No such file or directory\n".encode()
        elif args and args[0] == "push":
            local, target = args[1], args[2]
            if self.fail_on_push:
                return SimpleNamespace(
                    returncode=1,
                    stdout=b"",
                    stderr=b"push failed\n",
                )
            self.files[target.rsplit("/", 1)[-1]] = Path(local).read_bytes()
            out = f"{local}: 1 file pushed\n".encode()
        elif args[:3] == ["shell", "wm", "size"]:
            out = b"Physical size: 1080x1920\n"
        elif args[:3] == ["shell", "input", "swipe"]:
            self.swipes += 1
        elif args[:2] == ["shell", "sha256sum"]:
            self.restoration_phase = True
            name = args[2].rsplit("/", 1)[-1]
            body = self.files.get(name)
            if body is None:
                code, err = 1, b"sha256sum: No such file or directory\n"
            else:
                out = (hashlib.sha256(body).hexdigest() + "  " + args[2] + "\n").encode()
        elif args[:2] == ["shell", "rm"]:
            self.files.pop(args[2].rsplit("/", 1)[-1], None)
        else:
            raise AssertionError(f"unexpected fake adb command: {args}")
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)


def _setup(monkeypatch, tmp_path):
    ledger, lock, out = tmp_path / "budget.sqlite3", tmp_path / "run.lock", tmp_path / "probe"
    _ledger(ledger)
    monkeypatch.setattr(probe, "SHARED_LEDGER_PATH", ledger)
    monkeypatch.setattr(probe, "SHARED_RUN_LOCK_PATH", lock)
    monkeypatch.setattr(probe.explore_budget, "SHARED_RUN_LOCK_PATH", lock)
    spec = probe.prepare(out, suffix="abc12345")
    return spec, ledger, out


def test_prepare_freezes_four_names_refresh_bound_and_imported_sources(tmp_path):
    spec = probe.prepare(tmp_path / "probe", suffix="abc12345")
    base = "000_cua_probe_abc12345"
    assert spec["schema"] == "selective-markor-label-probe/2"
    assert spec["version"] == "markor_label_probe_v2"
    assert spec["filenames"] == [base, base + ".txt", base + ".md", base + ".txt.md"]
    assert spec["execution"]["model_calls"] == 0
    assert spec["execution"]["max_relaunches"] == 1
    assert spec["execution"]["max_swipes"] == 3
    assert spec["prior_v1"]["spec_sha256"] == probe.FROZEN_V1_SPEC_SHA256
    assert spec["source_hashes"]["probe_v1"] == probe.FROZEN_IMPORTED_SOURCE_SHA256["probe_v1"]
    assert spec["source_hashes"]["explore_budget"] == probe.FROZEN_IMPORTED_SOURCE_SHA256["explore_budget"]
    assert spec["source_hashes"]["selective_budget"]
    assert spec["runtime"]["runtime_manifest"]["interpreter"] == "../.venv-android/bin/python"
    assert "selective_markor_label_probe_v2 --run" in spec["cli"]["command"]


def test_transitive_host_guard_drift_stops_spec_load(tmp_path, monkeypatch):
    out = tmp_path / "probe"
    probe.prepare(out, suffix="abc12345")
    changed_guard = tmp_path / "selective_budget.py"
    changed_guard.write_text("# changed host guard\n", encoding="utf-8")
    monkeypatch.setitem(probe._IMPORTED_SOURCE_PATHS, "selective_budget", changed_guard)
    with pytest.raises(probe.ProbeStop, match="imported source hashes"):
        probe.load_spec(out)


def test_relaunch_refreshes_list_and_cleanup_is_hash_guarded(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec)
    before = probe.ledger_snapshot(ledger)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)

    labels = [args for args in fake.calls if args and args[0] == "push"]
    force_stop = [args for args in fake.calls if args[:4] == ["shell", "am", "force-stop", probe.PACKAGE]]
    starts = [
        args
        for index, args in enumerate(fake.calls)
        if args[:3] == ["shell", "am", "start"]
        and index > fake.calls.index(force_stop[0])
    ]
    assert result["status"] == "observed"
    assert len(labels) == 4 and len(force_stop) == len(starts) == 1
    assert fake.calls.index(force_stop[0]) > fake.calls.index(labels[-1])
    assert result["observations"]["relaunches"][0]["screenshot_sha256"]
    assert result["extraction"]["visible_exact_filenames"] == spec["filenames"]
    assert result["extraction"]["swipes"] == 0
    assert fake.swipes == 0 and fake.files == {}
    assert probe.ledger_snapshot(ledger) == before
    assert all(item["status"] == "removed" for item in result["cleanup"])
    assert json.loads((out / "run_claim.json").read_text())["status"] == "observed"


def test_foreign_service_refuses_before_any_mutation(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    foreign = "com.example.foreign/.ForeignService"
    fake = FakeSubprocess(
        spec,
        initial_services=foreign,
        initial_accessibility_enabled="1",
    )
    with pytest.raises(probe.ProbeStop, match="foreign accessibility service"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert fake.settings == {
        probe.SECURE_ENABLED_SERVICES: foreign,
        probe.SECURE_ACCESSIBILITY_ENABLED: "1",
    }
    assert not any(args[:3] == ["shell", "settings", "put"] for args in fake.calls)
    assert not any(args[:3] == ["shell", "settings", "delete"] for args in fake.calls)
    assert not any(args[:3] == ["shell", "am", "force-stop"] for args in fake.calls)
    assert not any(args and args[0] == "push" for args in fake.calls)
    assert json.loads((out / "summary.json").read_text())["status"] == "stopped"


def test_exact_helper_is_suspended_then_restored_inside_one_lock(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(
        spec,
        initial_services=probe.FORWARDER_COMPONENT,
        initial_accessibility_enabled="1",
    )
    lock_events = []
    active = []

    @contextlib.contextmanager
    def one_lock(**_kwargs):
        lock_events.append("enter")
        active.append(True)
        try:
            yield
        finally:
            active.pop()
            lock_events.append("exit")

    monkeypatch.setattr(probe.explore_budget, "exclusive_run", one_lock)
    original_call = fake.__call__

    def locked_call(command, **kwargs):
        fake.lock_samples = getattr(fake, "lock_samples", [])
        fake.lock_samples.append(bool(active))
        return original_call(command, **kwargs)

    fake_runner = locked_call
    result = probe.run(
        out,
        adb=probe.Adb(runner=fake_runner),
        ledger_path=ledger,
        host_guard=lambda: True,
    )
    calls = fake.calls
    first_mutation = next(
        index
        for index, args in enumerate(calls)
        if args[:3] in (["shell", "settings", "delete"], ["shell", "settings", "put"])
    )
    first_cleanup = next(
        index for index, args in enumerate(calls) if args[:2] == ["shell", "sha256sum"]
    )
    first_restore = next(
        index
        for index, args in enumerate(calls)
        if index > first_cleanup and args[:4] == ["shell", "settings", "put", "secure"]
    )
    assert result["status"] == "observed"
    assert lock_events == ["enter", "exit"]
    assert fake.lock_samples and all(fake.lock_samples)
    assert first_mutation < first_cleanup < first_restore
    assert fake.settings == {
        probe.SECURE_ENABLED_SERVICES: probe.FORWARDER_COMPONENT,
        probe.SECURE_ACCESSIBILITY_ENABLED: "1",
    }
    assert result["observations"]["auxiliary"]["restored"]["status"] == "restored"


def test_exception_after_suspend_still_restores_settings(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(
        spec,
        initial_services=probe.FORWARDER_COMPONENT,
        initial_accessibility_enabled="1",
        fail_on_push=True,
    )
    with pytest.raises(probe.ProbeStop, match="push_.*failed"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert fake.settings == {
        probe.SECURE_ENABLED_SERVICES: probe.FORWARDER_COMPONENT,
        probe.SECURE_ACCESSIBILITY_ENABLED: "1",
    }
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "stopped"
    assert summary["observations"]["auxiliary"]["restored"]["status"] == "restored"


def test_unset_secure_setting_is_restored_with_delete(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(
        spec,
        initial_services=probe.FORWARDER_COMPONENT,
        initial_accessibility_enabled="null",
    )
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert result["status"] == "observed"
    assert fake.settings[probe.SECURE_ACCESSIBILITY_ENABLED] == "null"
    assert any(
        args[:5]
        == ["shell", "settings", "delete", "secure", probe.SECURE_ACCESSIBILITY_ENABLED]
        for args in fake.calls
    )
    assert not any(
        args[:5]
        == ["shell", "settings", "put", "secure", probe.SECURE_ACCESSIBILITY_ENABLED]
        and args[5] == "null"
        for args in fake.calls
    )


def test_restore_readback_mismatch_stops_after_cleanup(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(
        spec,
        initial_services=probe.FORWARDER_COMPONENT,
        initial_accessibility_enabled="0",
        restore_mismatch=True,
    )
    with pytest.raises(probe.ProbeStop, match="restoration mismatch"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    calls = fake.calls
    last_cleanup = max(
        index
        for index, args in enumerate(calls)
        if args[:2] in (["shell", "sha256sum"], ["shell", "rm"])
    )
    first_restore = next(
        index
        for index, args in enumerate(calls)
        if args[:4] == ["shell", "settings", "put", "secure"]
        and index > last_cleanup
    )
    assert first_restore > last_cleanup
    summary = json.loads((out / "summary.json").read_text())
    assert summary["status"] == "stopped"
    assert summary["observations"]["auxiliary"]["restored"]["status"] == "failed"


def test_explicit_empty_setting_is_quoted_on_restore(tmp_path):
    spec = {"filenames": []}
    fake = FakeSubprocess(spec)
    out = tmp_path / "out"
    out.mkdir()
    run_state = probe._Run(out, probe.Adb(runner=fake), lambda: True)
    setting = {
        "key": probe.SECURE_ENABLED_SERVICES,
        "present": True,
        "value": "",
    }
    readback = probe._restore_setting(run_state, setting)
    assert fake.calls[-2] == [
        "shell",
        "settings",
        "put",
        "secure",
        probe.SECURE_ENABLED_SERVICES,
        '""',
    ]
    assert readback["present"] is True and readback["value"] == ""


def test_v2_never_invokes_v1_runner(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec)
    monkeypatch.setattr(
        probe.v1,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("v1 runner must remain untouched")
        ),
    )
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert result["status"] == "observed"


def test_bad_screenshot_stops_before_push(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, bad_png=True)
    with pytest.raises(probe.ProbeStop, match="screenshot is not a PNG"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert not any(args and args[0] == "push" for args in fake.calls)
    assert json.loads((out / "summary.json").read_text())["status"] == "stopped"


def test_swipes_are_bounded_after_relaunch(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, visible_after_relaunch=False)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert result["status"] == "observed"
    assert result["extraction"]["swipes"] == probe.MAX_SWIPES
    assert fake.swipes == probe.MAX_SWIPES
    assert len([args for args in fake.calls if args[:3] == ["shell", "input", "swipe"]]) == 3


def test_claim_refuses_second_run(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec)
    probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    with pytest.raises(probe.ProbeStop, match="run state"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
