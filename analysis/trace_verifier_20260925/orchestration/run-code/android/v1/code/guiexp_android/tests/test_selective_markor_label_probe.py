"""Offline fake-subprocess checks for the bounded Markor label probe."""
from __future__ import annotations

import hashlib
import json
import contextlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_markor_label_probe as probe


def _ledger(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO settings VALUES ('limit_nano','10000000000')")
        db.execute("CREATE TABLE calls (id TEXT PRIMARY KEY, state TEXT, reserved_nano INTEGER, actual_nano INTEGER)")
        db.execute("INSERT INTO calls VALUES ('old','settled',0,2378358288)")


class FakeSubprocess:
    def __init__(self, spec, *, fail_xml=False, malformed_xml=False, mutate=None, two_visible=False,
                 wrong_focus_after_open=False, lose_focus_after_swipe=False, collapsed=False, always_two=False):
        self.spec, self.fail_xml, self.malformed_xml = spec, fail_xml, malformed_xml
        self.mutate, self.two_visible, self.wrong_focus_after_open = mutate, two_visible, wrong_focus_after_open
        self.lose_focus_after_swipe, self.collapsed, self.always_two = lose_focus_after_swipe, collapsed, always_two
        self.opened, self.swipes, self.files, self.calls = False, 0, {}, []
        self.cleanup_lock_samples, self.lock_active = [], lambda: False

    def __call__(self, command, **kwargs):
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == probe.TIMEOUT
        args = command[3:]
        self.calls.append(args)
        if args[:2] in (["shell", "sha256sum"], ["shell", "rm"]):
            self.cleanup_lock_samples.append(self.lock_active())
        out, err, code = b"", b"", 0
        names = self.spec["filenames"]
        if args == ["get-state"]:
            out = b"device\n"
        elif args[:4] == ["shell", "dumpsys", "package", probe.PACKAGE]:
            out = b"versionCode=146 minSdk=23\nversionName=2.10.9\n"
        elif args[:3] == ["shell", "dumpsys", "window"]:
            if self.wrong_focus_after_open and self.opened or self.lose_focus_after_swipe and self.swipes:
                activity = "com.android.launcher3/.Launcher"
            else:
                activity = probe.PACKAGE + ("/.MainActivity" if self.opened else "/.Launcher")
            out = f"mCurrentFocus=Window{{u0 {activity}}}\n".encode()
        elif args[:3] == ["exec-out", "uiautomator", "dump"]:
            if self.fail_xml:
                out = b""
                self.fail_xml = False
            elif self.malformed_xml:
                out = f'<hierarchy package="{probe.PACKAGE}"><node></hierarchy>'.encode()
                self.malformed_xml = False
            else:
                visible = names[:2] if self.always_two or self.two_visible and not self.swipes else names
                labels = "".join(f'<node class="android.widget.TextView" text="{self.spec["basename"] if self.collapsed else n}" content-desc="desc {self.spec["basename"] if self.collapsed else n}" bounds="[0,0][10,10]"/>' for n in visible)
                out = (f'<hierarchy package="{probe.PACKAGE}"><node class="androidx.recyclerview.widget.RecyclerView" text="{probe.MARKOR_DATA}"/>{labels}</hierarchy>').encode()
        elif args[:3] == ["exec-out", "screencap", "-p"]:
            out = b"PNG-fake"
        elif args[:4] == ["shell", "cmd", "package", "resolve-activity"]:
            out = f"priority=0\n{probe.PACKAGE}/.MainActivity\n".encode()
        elif args[:3] == ["shell", "am", "start"]:
            self.opened = True
            out = b"Starting: Intent\n"
        elif args[:3] == ["shell", "ls", "-ld"]:
            target = args[3]
            if target.rsplit("/", 1)[-1] in self.files:
                out = f"-rw------- 1 u0_a 0 {target}\n".encode()
            else:
                code, err = 1, f"ls: {target}: No such file or directory\n".encode()
        elif args[:2] == ["push", self.spec["fixtures"][names[0]]["path"]][:2] or args and args[0] == "push":
            local, target = args[1], args[2]
            self.files[target.rsplit("/", 1)[-1]] = Path(local).read_bytes()
            out = f"{local}: 1 file pushed\n".encode()
        elif args[:3] == ["shell", "wm", "size"]:
            out = b"Physical size: 1080x1920\n"
        elif args[:3] == ["shell", "input", "swipe"]:
            self.swipes += 1
        elif args[:2] == ["shell", "sha256sum"]:
            name = args[2].rsplit("/", 1)[-1]
            body = self.files.get(name)
            if body is None:
                code, err = 1, b"sha256sum: No such file or directory\n"
            elif name == self.mutate:
                out = ("0" * 64 + "  " + args[2] + "\n").encode()
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


def test_prepare_freezes_four_names_bodies_hashes_and_exact_command(tmp_path):
    out = tmp_path / "probe"
    spec = probe.prepare(out, suffix="abc12345")
    base = "000_cua_probe_abc12345"
    assert spec["basename"] == base
    assert spec["filenames"] == [base, base + ".txt", base + ".md", base + ".txt.md"]
    assert "../.venv-android/bin/python -m guiexp_android.selective_markor_label_probe --run" in spec["cli"]["command"]
    assert all((out / "fixtures" / name).read_text() == f"cua_probe_filename={name}\n" for name in spec["filenames"])
    assert spec["execution"]["model_calls"] == 0 and spec["expected"]["version_code"] == 146


def test_extract_rows_preserves_duplicate_nodes_raw_attrs_and_literal_counts():
    base = "000_cua_probe_abc12345"
    names = probe._names(base)
    xml = f'<hierarchy><node text="{base}.txt" content-desc="{base}.txt" bounds="[0,0][1,1]"/><node text="{base}.txt" content-desc="{base}.txt" bounds="[1,1][2,2]"/></hierarchy>'
    result = probe.extract_rows(xml, base, names)
    assert len(result["rows"]) == 2
    assert result["rows"][0]["raw_attributes"].count("bounds") == 1
    assert [r["node_index"] for r in result["rows"]] == [0, 1]
    assert result["exact_filename_counts"][base + ".txt"] == 4
    assert result["exact_filename_counts"][base] == 0
    assert result["visibility"] == "incomplete_or_collapsed"


def test_foreground_parser_ignores_background_markor_text():
    text = "mCurrentFocus=Window{u0 com.android.launcher3/.Launcher}\n" \
           "background token net.gsantner.markor/.DocumentActivity\n"
    assert probe._activity(text) == "com.android.launcher3/.Launcher"
    assert probe._activity("mCurrentFocus=Window{u0 com.android.launcher3/.Launcher}\n"
                           "mFocusedApp=AppWindowToken{u0 net.gsantner.markor/.MainActivity}") == "com.android.launcher3/.Launcher"


def test_capture_failure_stops_before_any_push(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, fail_xml=True)
    with pytest.raises(probe.ProbeStop, match="XML capture"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: {"ready": True, "normal_full_wake": True})
    assert not any(args and args[0] == "push" for args in fake.calls)
    assert json.loads((out / "summary.json").read_text())["status"] == "stopped"


@pytest.mark.parametrize("kwargs,match", [
    ({"malformed_xml": True}, "malformed"),
    ({"wrong_focus_after_open": True}, "recognizable Markor"),
])
def test_malformed_xml_or_background_foreground_stops_before_push(tmp_path, monkeypatch, kwargs, match):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, **kwargs)
    with pytest.raises(probe.ProbeStop, match=match):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert not any(args and args[0] == "push" for args in fake.calls)


def test_collapsed_labels_are_observed_after_bounded_swipes(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, collapsed=True)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert result["status"] == "observed"
    assert result["extraction"]["visibility"] == "incomplete_or_collapsed"
    assert result["extraction"]["swipes"] == 0
    assert not any(args[:3] == ["shell", "input", "swipe"] for args in fake.calls)


def test_foreground_is_refreshed_before_each_bounded_swipe(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, always_two=True, lose_focus_after_swipe=True)
    with pytest.raises(probe.ProbeStop, match="pre-swipe foreground"):
        probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    focus_reads = [args for args in fake.calls if args == ["shell", "dumpsys", "window"]]
    swipe_actions = [args for args in fake.calls if args[:3] == ["shell", "input", "swipe"]]
    assert len(focus_reads) >= 2 and len(swipe_actions) == 1


def test_run_uses_only_bounded_fake_actions_and_hash_guarded_cleanup(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec, two_visible=True)
    before = probe.ledger_snapshot(ledger)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: {"ready": True, "normal_full_wake": True})
    assert result["status"] == "observed" and result["extraction"]["swipes"] == 1
    assert len([args for args in fake.calls if args and args[0] == "push"]) == 4
    assert fake.files == {}
    assert not any("click" in args or "delete" in args for args in fake.calls)
    assert probe.ledger_snapshot(ledger) == before
    assert (out / "xml" / "initial.xml").is_file() and (out / "screenshots" / "after_open.png").is_file()
    assert all(item["status"] == "removed" for item in result["cleanup"])


def test_changed_owned_file_is_reported_and_left_in_place(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    changed = spec["filenames"][0]
    fake = FakeSubprocess(spec, mutate=changed)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    item = next(row for row in result["cleanup"] if row["filename"] == changed)
    assert item["status"] == "changed" and changed in fake.files


def test_cleanup_and_authoritative_ledger_snapshots_stay_inside_lock(tmp_path, monkeypatch):
    spec, ledger, out = _setup(monkeypatch, tmp_path)
    fake = FakeSubprocess(spec)
    active, snapshots = [], []
    original_snapshot = probe.ledger_snapshot

    def snapshot(path):
        snapshots.append(bool(active))
        return original_snapshot(path)

    @contextlib.contextmanager
    def lock(**_kwargs):
        active.append(True)
        try:
            yield
        finally:
            active.pop()

    monkeypatch.setattr(probe, "ledger_snapshot", snapshot)
    monkeypatch.setattr(probe.explore_budget, "exclusive_run", lock)
    fake.lock_active = lambda: bool(active)
    result = probe.run(out, adb=probe.Adb(runner=fake), ledger_path=ledger, host_guard=lambda: True)
    assert result["status"] == "observed"
    assert snapshots[:3] == [False, True, True]
    cleanup_calls = [args for args in fake.calls if args[:2] == ["shell", "sha256sum"] or args[:2] == ["shell", "rm"]]
    assert cleanup_calls and all(fake.cleanup_lock_samples)
