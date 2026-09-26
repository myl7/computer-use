import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from guiexp_android import recovery_validation as rv


def test_generated_program_requires_checker_and_rejects_private_escape():
    with pytest.raises(ValueError, match="Both program and verify"):
        rv.load_program("def program(device, params): return True")
    with pytest.raises(ValueError, match="Private attribute"):
        rv.load_program("def program(device, params): return device._env.reward()\ndef verify(device, params): return True")
    with pytest.raises(ValueError, match="Forbidden program name"):
        rv.load_program("def program(device, params): return open('/tmp/file')\ndef verify(device, params): return True")


def test_readonly_check_program_can_use_branches_and_loops():
    _, namespace = rv.load_program("""
def program(device, params):
    for _ in range(2):
        if device.exists(params['path']):
            return True
    raise AssertionError('not found')
def verify(device, params):
    return device.exists(params['path'])
""")
    assert namespace["program"](SimpleNamespace(exists=lambda p: True), {"path":"/sdcard/Download/a"})
    assert namespace["verify"](SimpleNamespace(exists=lambda p: True), {"path":"/sdcard/Download/a"})


def test_focus_injection_requires_confirmation_context():
    target={"text":"Delete","class_name":"android.widget.Button"}
    d=SimpleNamespace(last_action_target=target,last_action_pre_observation={"action_elements":[]},actions=3)
    p=rv.Perturbation("focus_after_confirmation","MarkorDeleteNote")
    with patch.object(rv,"shell") as mocked:
        p.hook(d,{"action_type":"click","index":2})
        assert not p.triggered
        mocked.assert_not_called()
        d.last_action_pre_observation={"action_elements":[{"text":"Cancel"}]}
        p.hook(d,{"action_type":"click","index":2})
        assert p.triggered
        mocked.assert_called_once_with("input","keyevent","3")


def test_paste_description_triggers_once_without_text():
    d=SimpleNamespace(last_action_target={"content_description":"Paste","class_name":"android.widget.ImageButton"},
                      last_action_pre_observation={},actions=9)
    p=rv.Perturbation("focus_after_confirmation","FilesMoveFile")
    with patch.object(rv,"shell") as mocked:
        p.hook(d,{"action_type":"click","index":5})
        p.hook(d,{"action_type":"click","index":5})
        assert p.triggered
        assert mocked.call_count==1


def test_existing_freeze_cannot_be_overwritten():
    with tempfile.TemporaryDirectory() as directory:
        out=Path(directory)
        (out/"spec.json").write_text('{"preserve":true}')
        with patch.object(rv,"OUT",out):
            with pytest.raises(RuntimeError,match="cannot be overwritten"):
                rv.prepare()
        assert (out/"spec.json").read_text()=='{"preserve":true}'
