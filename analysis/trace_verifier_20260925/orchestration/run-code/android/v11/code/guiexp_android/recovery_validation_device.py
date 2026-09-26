"""GUI execution with observable checks and durable recovery evidence.

The experiment explicitly grants every arm read-only inspection of public shared
storage. This strengthens the baseline beyond screenshot/AX-only interaction.
It does not expose task parameters, evaluator state, private databases or writes.
The caller owns task initialization, scoring, lifecycle and the emulator lock.
"""

from __future__ import annotations

import base64
import copy
import dataclasses
import json
import os
import re
import shlex
import time
from pathlib import Path, PurePosixPath

PUBLIC_ROOTS = frozenset({
    "Alarms", "Audiobooks", "DCIM", "Documents", "Download", "Movies",
    "Music", "Notifications", "Pictures", "Podcasts", "Recordings", "Ringtones",
})
STORAGE = "/storage/emulated/0"
_ACTION_FIELDS = {
    "click": {"index", "x", "y"},
    "long_press": {"index", "x", "y"},
    "input_text": {"index", "x", "y", "text", "clear_text"},
    "scroll": {"index", "direction"},
    "open_app": {"app_name"},
    "navigate_back": set(), "navigate_home": set(), "keyboard_enter": set(),
    "wait": set(), "status": {"goal_status"},
}


def _native_action(action, elements, screen_size, controller):
    from android_world.env import actuation, json_action
    actuation.execute_adb_action(
        json_action.JSONAction(**action), elements, screen_size, controller
    )


def _plain(value):
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return copy.deepcopy(value)
    from google.protobuf.json_format import MessageToDict
    return MessageToDict(value, preserving_proto_field_name=True)


def _encode_png(pixels):
    import cv2
    if pixels is None or not getattr(pixels, "size", 0):
        raise ValueError("observation has no screenshot pixels")
    ok, data = cv2.imencode(".png", cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError("screenshot PNG encoding failed")
    return data.tobytes()


class RecoveryDevice:
    def __init__(self, env, episode_dir, max_actions=50, after_action_hook=None,
                 *, actuator=None, state_reader=None, command_runner=None,
                 sleeper=time.sleep):
        if type(max_actions) is not int or max_actions < 0:
            raise ValueError("max_actions must be a nonnegative integer")
        self._env = env
        self.episode_dir = Path(episode_dir).resolve()
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self._event_path = self.episode_dir / "events.jsonl"
        if self._event_path.exists() and self._event_path.stat().st_size:
            raise FileExistsError("episode already has device events")
        self.max_actions, self.actions = max_actions, 0
        self.events = []
        self.terminated = False
        self.after_action_hook = after_action_hook
        self._actuator = actuator or _native_action
        self._state_reader = state_reader
        self._command_runner = command_runner
        self._sleep = sleeper
        self._state = None
        self.last_observation = None
        self._last_action_target = None
        self._last_action_pre_observation = None
        self._captures = 0

    @property
    def last_action_target(self):
        """Pre-action target for instrumentation; mutations cannot change it."""
        return copy.deepcopy(self._last_action_target)

    @property
    def last_action_pre_observation(self):
        """Pre-action observation for instrumentation, without image bytes."""
        return copy.deepcopy(self._last_action_pre_observation)

    def _event(self, kind, **details):
        event = {"event": kind, "timestamp": time.time(), "actions": self.actions,
                 **copy.deepcopy(details)}
        line = json.dumps(event, ensure_ascii=False, allow_nan=False)
        with self._event_path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(event)
        return event

    def observe(self):
        started = time.time()
        state = (self._state_reader() if self._state_reader else
                 self._env.aw_env.get_state(wait_to_stabilize=False))
        if state.forest is None:
            raise ValueError("observation has no full AX forest")
        forest = _plain(state.forest)
        elements = [{**_plain(e), "index": i} for i, e in enumerate(state.ui_elements)]
        png = _encode_png(state.pixels)
        self._captures += 1
        stem = f"observation_{self._captures:04d}"
        screenshot_path = self.episode_dir / f"{stem}.png"
        ax_path = self.episode_dir / f"{stem}.json"
        activity = self._env.aw_env.foreground_activity_name
        observation = {
            "screenshot_b64": base64.b64encode(png).decode("ascii"),
            "ax_forest": forest, "action_elements": elements,
            "ax_tree_text": json.dumps({"forest": forest, "action_elements": elements}, ensure_ascii=False),
            "activity": activity, "url": activity, "timestamp": time.time(),
            "capture_started_at": started,
            "screenshot_path": str(screenshot_path), "ax_path": str(ax_path),
        }
        saved = {k: v for k, v in observation.items() if k != "screenshot_b64"}
        with screenshot_path.open("xb") as stream:
            stream.write(png)
        with ax_path.open("x", encoding="utf-8") as stream:
            json.dump(saved, stream, ensure_ascii=False, allow_nan=False)
        self._state, self.last_observation = state, observation
        self._event("observation", screenshot_path=str(screenshot_path),
                    ax_path=str(ax_path), activity=activity)
        return copy.deepcopy(observation)

    def elements(self):
        result = self.observe()["action_elements"]
        for element in result:
            for alias, field in {
                "hint": "hint_text", "description": "content_description",
                "class": "class_name", "bounds": "bbox_pixels",
                **{key: "is_" + key for key in (
                    "editable", "clickable", "long_clickable", "scrollable",
                    "focusable", "focused", "selected", "checked", "enabled", "visible",
                )},
            }.items():
                element[alias] = element.get(field)
        return result

    def find(self, **criteria):
        elements = self.elements()
        allowed = {key for e in elements for key in e} | {
            "contains", "text", "hint", "description", "resource_id", "class",
            "editable", "clickable", "selected", "checked", "enabled", "visible",
        }
        if set(criteria) - allowed or not criteria:
            raise ValueError("unknown or empty selector criteria")

        def equal(actual, expected):
            if isinstance(expected, str):
                return str(actual or "").strip().casefold() == expected.strip().casefold()
            return actual == expected

        hits = []
        for element in elements:
            blob = " ".join(str(element.get(k) or "") for k in
                            ("text", "hint", "description")).casefold()
            if all(str(value).casefold() in blob if key == "contains" else
                   equal(element.get(key), value) for key, value in criteria.items()):
                hits.append(element["index"])
        if len(hits) > 1:
            raise ValueError(f"selector is ambiguous: {len(hits)} matches")
        return hits[0] if hits else None

    def _action(self, value):
        if not isinstance(value, dict):
            raise TypeError("GUI action must be an object")
        action = copy.deepcopy(value)
        kind = action.get("action_type")
        if not isinstance(kind, str) or kind not in _ACTION_FIELDS or set(action) - (_ACTION_FIELDS[kind] | {"action_type"}):
            raise ValueError("unsupported GUI action or fields")
        for key in ("index", "x", "y"):
            if key in action and (type(action[key]) is not int or action[key] < 0):
                raise ValueError(f"{key} must be a nonnegative integer")
        if kind in {"click", "long_press", "input_text"}:
            if ("index" in action) == ("x" in action or "y" in action):
                raise ValueError("target requires either an index or x and y")
            if "index" not in action and not {"x", "y"} <= action.keys():
                raise ValueError("both x and y are required")
        if kind == "input_text":
            if not isinstance(action.get("text"), str) or not action["text"]:
                raise ValueError("input_text requires nonempty text")
            action.setdefault("clear_text", True)
            if type(action["clear_text"]) is not bool:
                raise ValueError("clear_text must be a boolean")
        if kind == "open_app" and (not isinstance(action.get("app_name"), str) or not action["app_name"].strip()):
            raise ValueError("open_app requires app_name")
        if kind == "scroll" and action.get("direction") not in {"up", "down", "left", "right"}:
            raise ValueError("invalid scroll direction")
        if kind == "status" and action.get("goal_status") not in {"complete", "infeasible"}:
            raise ValueError("invalid terminal status")
        return action

    def execute(self, action_dict):
        action = self._action(action_dict)
        if self.terminated:
            raise RuntimeError("episode already terminated")
        if action["action_type"] == "status":
            self.terminated = True
            self._event("status", action=action, verified=False)
            return self.last_observation
        if self.actions >= self.max_actions:
            raise RuntimeError("GUI action budget exhausted")
        if self._state is None:
            self.observe()
        state = self._state
        if "index" in action and action["index"] >= len(state.ui_elements):
            raise ValueError("index is outside the observed element list")
        self._last_action_pre_observation = copy.deepcopy({
            key: value for key, value in self.last_observation.items()
            if key != "screenshot_b64"
        })
        self._last_action_target = (copy.deepcopy(self.last_observation["action_elements"][action["index"]])
                                    if "index" in action else None)
        self._event("intent", action=action,
                    before_ax_path=self.last_observation["ax_path"],
                    before_screenshot_path=self.last_observation["screenshot_path"])
        self.actions += 1
        try:
            self._actuator(action, state.ui_elements,
                           self._env.aw_env.logical_screen_size, self._env.aw_env.controller)
        except Exception as error:
            self._event("exception", action=action, phase="native_action",
                        effect_unknown=True, error=f"{type(error).__name__}: {error}")
            try:
                self.observe()
            except Exception as capture_error:  # noqa: BLE001 - preserve the original native failure.
                self._event("observation_error", error=str(capture_error), effect_unknown=True)
            raise
        self._event("issued", action=action, effect_unknown=True)
        try:
            self.settle()
            observation = self.observe()
            if self.after_action_hook is not None:
                self.after_action_hook(self, copy.deepcopy(action))
                observation = self.observe()
            return observation
        except Exception as error:
            self._event("exception", action=action, phase="post_action",
                        effect_unknown=True, error=f"{type(error).__name__}: {error}")
            raise

    def _target(self, index, criteria):
        index = self.find(**criteria) if index is None else index
        if index is None:
            raise ValueError("no matching UI element")
        return index

    def click(self, index=None, **criteria):
        return self.execute({"action_type": "click", "index": self._target(index, criteria)})

    def long_press(self, index=None, **criteria):
        return self.execute({"action_type": "long_press", "index": self._target(index, criteria)})

    def input_text(self, text, index=None, clear_text=True, **criteria):
        return self.execute({"action_type": "input_text", "text": text,
                             "index": self._target(index, criteria), "clear_text": clear_text})

    def scroll(self, direction="down"):
        return self.execute({"action_type": "scroll", "direction": direction})

    def open_app(self, app_name):
        return self.execute({"action_type": "open_app", "app_name": app_name})

    def navigate_back(self):
        return self.execute({"action_type": "navigate_back"})

    def navigate_home(self):
        return self.execute({"action_type": "navigate_home"})

    def wait(self):
        return self.execute({"action_type": "wait"})

    def keyboard_enter(self):
        return self.execute({"action_type": "keyboard_enter"})

    def settle(self, seconds=None):
        seconds = getattr(self._env, "wait_after_action_seconds", 0) if seconds is None else seconds
        if not isinstance(seconds, (int, float)) or not 0 <= seconds <= 30:
            raise ValueError("settle must be between 0 and 30 seconds")
        self._sleep(seconds)

    def current_activity(self):
        return self.observe()["activity"]

    def check(self, condition, label, details=None):
        passed = bool(condition)
        self._event("effect_verified" if passed else "check_failed",
                    label=str(label), details=details)
        if not passed:
            raise AssertionError(str(label))
        return True

    @staticmethod
    def _path(path, allow_root=False):
        if not isinstance(path, str) or re.search(r"[\x00-\x1f\x7f;$`|&<>]", path):
            raise ValueError("invalid public-storage path")
        if ".." in path.split("/") or not path.startswith("/"):
            raise ValueError("path traversal or relative path is forbidden")
        if path == "/sdcard" or path.startswith("/sdcard/"):
            path = STORAGE + path[len("/sdcard"):]
        normalized = str(PurePosixPath(path))
        if allow_root and normalized == STORAGE:
            return normalized
        if not any(normalized == f"{STORAGE}/{root}" or
                   normalized.startswith(f"{STORAGE}/{root}/") for root in PUBLIC_ROOTS):
            raise ValueError("path is outside permitted public storage")
        return normalized

    def _read_command(self, argv):
        if self._command_runner is not None:
            output = self._command_runner(list(argv))
        else:
            from android_world.env import adb_utils
            marker = b"\n__recovery_read_exit__="
            script = shlex.join(argv) + '; rv_read_rc=$?; printf "\\n__recovery_read_exit__=%s\\n" "$rv_read_rc"'
            response = adb_utils.issue_generic_request(
                ["shell", script], self._env.aw_env.controller, timeout_sec=15
            )
            adb_utils.check_ok(response, "public-storage read failed")
            output, found, code = response.generic.output.rpartition(marker)
            if not found or not code.strip().isdigit():
                raise OSError("public-storage read has no exit-status evidence")
            if int(code.strip()):
                if b"No such file or directory" in output or (argv[0] == "readlink" and not output.strip()):
                    raise FileNotFoundError(argv[-1])
                raise OSError("public-storage read command failed")
        return output.encode() if isinstance(output, str) else bytes(output)

    def _resolved(self, path, allow_root=False):
        path = self._path(path, allow_root)
        try:
            resolved = self._read_command(["readlink", "-f", "--", path]).decode("utf-8").strip()
        except FileNotFoundError:
            return None
        return self._path(resolved, allow_root) if resolved else None

    def exists(self, path):
        resolved = self._resolved(path)
        try:
            value = bool(resolved and self._read_command(["find", resolved, "-maxdepth", "0", "-print0"]))
        except FileNotFoundError:
            value = False
        self._event("storage_exists", path=self._path(path), exists=value)
        return value

    def list_files(self, path):
        original = self._path(path, allow_root=True)
        resolved = self._resolved(original, allow_root=True)
        if resolved is None:
            raise FileNotFoundError(original)
        output = self._read_command(["find", resolved, "-mindepth", "1", "-maxdepth", "1", "-print0"])
        directory_output = self._read_command([
            "find", resolved, "-mindepth", "1", "-maxdepth", "1", "-type", "d", "-print0"
        ])
        directories = {raw.decode("utf-8") for raw in directory_output.split(b"\0") if raw}
        result = []
        for raw in output.split(b"\0"):
            if not raw:
                continue
            child = raw.decode("utf-8")
            name = PurePosixPath(child).name
            if original == STORAGE and name not in PUBLIC_ROOTS:
                continue
            child = self._path(child)
            if str(PurePosixPath(child).parent) != resolved:
                raise ValueError("directory listing escaped its parent")
            result.append({"name": name, "path": child, "is_dir": child in directories})
        self._event("storage_list", path=original, entries=result)
        return result

    def read_file(self, path, max_bytes=65536):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
            raise ValueError("max_bytes must be between 1 and 1048576")
        resolved = self._resolved(path)
        if resolved is None:
            raise FileNotFoundError(path)
        content = self._read_command(["head", "-c", str(max_bytes + 1), "--", resolved])
        if len(content) > max_bytes:
            self._event("storage_read", path=self._path(path), over_limit=True, max_bytes=max_bytes)
            raise ValueError("file exceeds max_bytes")
        self._event("storage_read", path=self._path(path), content_b64=base64.b64encode(content).decode())
        return content.decode("utf-8")
