"""forksafe: eligible launches go through posix_spawn, others are untouched."""
import os
import subprocess
import sys

import pytest

from guiexp_android import forksafe

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only hook")


@pytest.fixture
def spawn_calls(monkeypatch):
    calls = []
    real = os.posix_spawn

    def recording(path, argv, env, **kw):
        calls.append(path)
        return real(path, argv, env, **kw)

    monkeypatch.setattr(os, "posix_spawn", recording)
    forksafe.install()
    yield calls
    forksafe.uninstall()


def test_plain_run_uses_posix_spawn(spawn_calls):
    out = subprocess.run(["/bin/echo", "hi"], capture_output=True, text=True, timeout=10)
    assert out.stdout.strip() == "hi"
    assert spawn_calls == ["/bin/echo"]


def test_devnull_stdin_still_uses_posix_spawn(spawn_calls):
    out = subprocess.run(["/bin/echo", "x"], stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=10)
    assert out.returncode == 0
    assert spawn_calls == ["/bin/echo"]


def test_pass_fds_is_left_on_fork_path(spawn_calls):
    r, w = os.pipe()
    try:
        out = subprocess.run(["/bin/echo", "y"], pass_fds=(w,), capture_output=True,
                             text=True, timeout=10)
    finally:
        os.close(r)
        os.close(w)
    assert out.returncode == 0
    assert spawn_calls == []


def test_explicit_cwd_is_left_on_fork_path(spawn_calls):
    out = subprocess.run(["/bin/pwd"], cwd="/", capture_output=True, text=True, timeout=10)
    assert out.stdout.strip() == "/"
    assert spawn_calls == []


def test_opt_out_env(monkeypatch):
    forksafe.uninstall()
    monkeypatch.setenv("ANDROID_EXP_FORK_SAFE", "0")
    assert forksafe.install() is False
    assert subprocess.Popen.__init__ is forksafe._ORIGINAL_INIT
