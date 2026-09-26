import dataclasses
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from guiexp_android.recovery_validation_device import PUBLIC_ROOTS, RecoveryDevice


@dataclasses.dataclass
class Element:
    text: str = "Save"
    resource_id: str = "app:id/save"
    content_description: str = "Commit"
    class_name: str = "Button"
    hint_text: str = "hint"
    bbox_pixels: dict = dataclasses.field(default_factory=lambda: {"x_min": 3, "x_max": 8})
    is_clickable: bool = True
    is_editable: bool = False
    is_selected: bool = True
    is_enabled: bool = True


def state(label="before", elements=None):
    return SimpleNamespace(
        pixels=np.zeros((3, 4, 3), dtype=np.uint8),
        forest={"nodes": [{"text": label}]},
        ui_elements=[Element(text=label)] if elements is None else elements,
    )


class Env:
    wait_after_action_seconds = 0

    def __init__(self, states):
        self.states = list(states)
        self.reads = 0
        self.aw_env = SimpleNamespace(
            get_state=self.read_state,
            foreground_activity_name="app/Activity",
            logical_screen_size=(4, 3), controller=object(),
            execute_action=lambda *_: pytest.fail("must use observed-state actuator"),
        )

    def read_state(self, **kwargs):
        self.reads += 1
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    def __getattr__(self, name):
        if name in {"reset", "reward", "_task_impl", "task", "close", "step"}:
            pytest.fail(f"forbidden environment access: {name}")
        raise AttributeError(name)


def make(tmp_path, states=None, **kwargs):
    env = Env(states or [state()])
    calls = []
    kwargs.setdefault("actuator", lambda *args: calls.append(args))
    return RecoveryDevice(env, tmp_path, sleeper=lambda _: None, **kwargs), env, calls


def test_observe_saves_one_lossless_state_without_oracle(tmp_path):
    original = state(elements=[Element(text='line 1\n"line 2"\\tail')])
    device, env, _ = make(tmp_path, [original])
    observation = device.observe()
    assert env.reads == 1
    assert observation["ax_forest"] == original.forest
    assert json.loads(observation["ax_tree_text"])["forest"] == original.forest
    assert observation["action_elements"][0] == {**dataclasses.asdict(original.ui_elements[0]), "index": 0}
    assert Path(observation["screenshot_path"]).read_bytes().startswith(b"\x89PNG")
    saved = json.loads(Path(observation["ax_path"]).read_text())
    assert saved["action_elements"] == observation["action_elements"]
    observation["ax_forest"]["nodes"].clear()
    assert device.last_observation["ax_forest"]["nodes"]


def test_index_mapping_is_the_observed_mapping_and_input_clears(tmp_path):
    before, after = state("before"), state("after")
    device, env, calls = make(tmp_path, [before, after])
    device.observe()
    result = device.input_text("value", index=0)
    assert calls[0][0]["clear_text"] is True
    assert calls[0][1] is before.ui_elements
    assert result["ax_forest"] == after.forest
    assert env.reads == 2 and device.actions == 1


def test_intent_is_durable_before_send_and_retained_on_exception(tmp_path):
    def fail(*args):
        events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "intent"
        raise RuntimeError("connection lost after send")

    device, env, _ = make(tmp_path, [state(), state("possibly changed")], actuator=fail)
    with pytest.raises(RuntimeError, match="after send"):
        device.click(index=0)
    assert device.actions == 1 and env.reads == 2
    error = next(e for e in device.events if e["event"] == "exception")
    assert error["effect_unknown"] and error["action"]["index"] == 0
    assert device.last_observation["ax_forest"]["nodes"][0]["text"] == "possibly changed"


def test_hook_runs_after_saved_poststate_then_observation_is_refreshed(tmp_path):
    order = []

    def hook(device, action):
        order.append((device.actions, action["action_type"], device.last_observation["ax_forest"]))
        assert device.last_action_target["text"] == "before"
        previous = device.last_action_pre_observation
        assert previous["ax_forest"] == {"nodes": [{"text": "before"}]}
        assert previous["action_elements"][0]["text"] == "before"
        assert "screenshot_b64" not in previous
        previous["action_elements"][0]["text"] = "changed externally"
        assert device.last_action_pre_observation["action_elements"][0]["text"] == "before"
        target = device.last_action_target
        target["text"] = "changed externally"
        assert device.last_action_target["text"] == "before"

    device, env, _ = make(tmp_path, [state("before"), state("native"), state("perturbed")], after_action_hook=hook)
    result = device.click(index=0)
    assert order == [(1, "click", {"nodes": [{"text": "native"}]})]
    assert result["ax_forest"]["nodes"][0]["text"] == "perturbed"
    assert env.reads == 3


def test_unique_find_and_full_aliases(tmp_path):
    device, _, _ = make(tmp_path, [state(elements=[Element(), Element(text="Other", resource_id="other")])])
    elements = device.elements()
    assert elements[0]["bounds"] == elements[0]["bbox_pixels"]
    assert elements[0]["description"] == "Commit"
    assert elements[0]["selected"] and elements[0]["enabled"]
    assert device.find(text=" SAVE ", resource_id="APP:ID/SAVE") == 0
    assert device.find(contains="other") == 1
    assert device.find(text="missing") is None
    with pytest.raises(ValueError, match="ambiguous"):
        device.find(clickable=True)


def test_checks_log_and_raise(tmp_path):
    device, _, _ = make(tmp_path)
    assert device.check(True, "file visible", {"name": "note"})
    with pytest.raises(AssertionError, match="wrong value"):
        device.check(False, "wrong value")
    assert [e["event"] for e in device.events] == ["effect_verified", "check_failed"]


def test_budget_and_terminal_status_do_not_call_oracle(tmp_path):
    device, _, calls = make(tmp_path, max_actions=1)
    device.wait()
    with pytest.raises(RuntimeError, match="budget"):
        device.navigate_back()
    device.execute({"action_type": "status", "goal_status": "complete"})
    assert len(calls) == 1 and device.actions == 1 and device.terminated
    assert device.events[-1]["verified"] is False


@pytest.mark.parametrize("action", [
    {"action_type": "adb_shell", "command": "ls"},
    {"action_type": "click", "index": -1},
    {"action_type": "click", "index": True},
    {"action_type": "click", "index": 0, "x": 2, "y": 2},
    {"action_type": "click", "x": 2},
    {"action_type": "input_text", "index": 0, "text": ""},
    {"action_type": "scroll", "direction": "sideways"},
])
def test_invalid_actions_are_not_sent(tmp_path, action):
    device, env, calls = make(tmp_path)
    with pytest.raises(ValueError):
        device.execute(action)
    assert calls == [] and env.reads == 0


@pytest.mark.parametrize("path", [
    "/data/data/app/databases/private.db", "/sdcard/Android/data/app/db",
    "/sdcard/Documents/../Android/file", "Documents/file", "/sdcard/Documents/a;rm -rf /",
    "/sdcard/Documents/$(cat secret)", "/sdcard/Documents/a\nb", "/sdcard",
])
def test_read_paths_are_confined_before_any_command(tmp_path, path):
    commands = []
    device, _, _ = make(tmp_path, command_runner=lambda argv: commands.append(argv) or b"")
    with pytest.raises(ValueError):
        device.read_file(path)
    assert commands == []


def test_resolved_symlink_cannot_escape_public_roots(tmp_path):
    commands = []

    def reader(argv):
        commands.append(argv)
        return b"/data/data/private/database\n"

    device, _, _ = make(tmp_path, command_runner=reader)
    with pytest.raises(ValueError, match="outside"):
        device.read_file("/sdcard/Documents/link")
    assert len(commands) == 1 and commands[0][0] == "readlink"


def test_safe_file_reads_lists_aliases_and_root_filter(tmp_path):
    commands = []

    def reader(argv):
        commands.append(argv)
        if argv[0] == "readlink":
            return (argv[-1] + "\n").encode()
        if argv[0] == "head":
            return b"hello"
        path = argv[1]
        if "-mindepth" not in argv:
            return (path + "\0").encode()
        if path == "/storage/emulated/0":
            return b"/storage/emulated/0/Documents\0/storage/emulated/0/Android\0"
        if "-type" in argv:
            return b""
        return (path + "/author's note.txt\0").encode()

    device, _, _ = make(tmp_path, command_runner=reader)
    assert device.read_file("/sdcard/Documents/author's note.txt") == "hello"
    assert device.exists("/sdcard/Documents/author's note.txt")
    assert device.list_files("/sdcard/Documents")[0]["name"] == "author's note.txt"
    assert device.list_files("/sdcard/Documents")[0]["is_dir"] is False
    assert device.list_files("/sdcard") == [{"name": "Documents", "path": "/storage/emulated/0/Documents", "is_dir": True}]
    assert {argv[0] for argv in commands} <= {"readlink", "head", "find"}
    for root in PUBLIC_ROOTS:
        assert device._path("/sdcard/" + root).endswith("/" + root)


def test_file_size_bound_and_missing_file(tmp_path):
    device, _, _ = make(tmp_path, command_runner=lambda argv: (
        (argv[-1] + "\n").encode() if argv[0] == "readlink" else b"long"
    ))
    with pytest.raises(ValueError, match="exceeds"):
        device.read_file("/sdcard/Documents/file", max_bytes=3)
    device._command_runner = lambda argv: b""
    assert device.exists("/sdcard/Documents/missing") is False
    with pytest.raises(FileNotFoundError):
        device.read_file("/sdcard/Documents/missing")


def test_injected_state_reader_never_calls_environment_reader(tmp_path):
    device, env, _ = make(tmp_path, state_reader=lambda: state("injected"))
    assert device.observe()["ax_forest"]["nodes"][0]["text"] == "injected"
    assert env.reads == 0


def test_pre_action_observation_is_readonly_and_retained_on_failure(tmp_path):
    def fail(*_):
        raise RuntimeError("native failure")

    device, _, _ = make(tmp_path, [state("dialog"), state("unknown")], actuator=fail)
    assert device.last_action_pre_observation is None
    with pytest.raises(RuntimeError, match="native failure"):
        device.click(index=0)
    assert device.last_action_pre_observation["action_elements"][0]["text"] == "dialog"
    with pytest.raises(AttributeError):
        device.last_action_pre_observation = {}


def test_directory_listing_distinguishes_files_and_directories(tmp_path):
    folder = "/storage/emulated/0/Documents"

    def reader(argv):
        if argv[0] == "readlink":
            return (folder + "\n").encode()
        if "-type" in argv:
            return (folder + "/subfolder\0").encode()
        return (folder + "/subfolder\0" + folder + "/note.txt\0").encode()

    device, _, _ = make(tmp_path, command_runner=reader)
    assert device.list_files(folder) == [
        {"name": "subfolder", "path": folder + "/subfolder", "is_dir": True},
        {"name": "note.txt", "path": folder + "/note.txt", "is_dir": False},
    ]


def test_empty_ui_find_returns_none(tmp_path):
    device, _, _ = make(tmp_path, [state(elements=[])])
    assert device.find(text="Save") is None


@pytest.mark.parametrize("output, expected_error", [
    (b"content\n__recovery_read_exit__=0\n", None),
    (b"No such file or directory\n__recovery_read_exit__=1\n", FileNotFoundError),
    (b"Permission denied\n__recovery_read_exit__=1\n", OSError),
    (b"unframed output", OSError),
])
def test_real_reader_quotes_filenames_and_checks_remote_exit(tmp_path, monkeypatch, output, expected_error):
    requests = []

    def issue(args, controller, timeout_sec):
        requests.append(args)
        return SimpleNamespace(generic=SimpleNamespace(output=output))

    fake = SimpleNamespace(issue_generic_request=issue, check_ok=lambda *_: None)
    monkeypatch.setitem(sys.modules, "android_world.env.adb_utils", fake)
    import android_world.env
    monkeypatch.setattr(android_world.env, "adb_utils", fake, raising=False)
    device, _, _ = make(tmp_path)
    path = "/storage/emulated/0/Documents/author's note.txt"
    argv = ["head", "-c", "12", "--", path]
    if expected_error:
        with pytest.raises(expected_error):
            device._read_command(argv)
    else:
        assert device._read_command(argv) == b"content"
    assert requests[0][0] == "shell"
    assert requests[0][1].startswith(shlex.join(argv) + ";")
