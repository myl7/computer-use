"""One booted AndroidWorldAvd emulator wrapped in the guiexp reset/step contract.

Everything device-facing is delegated to android_world's own interfaces
(``env_launcher.load_and_setup_env`` -> ``AsyncAndroidEnv`` -> controller,
``TaskEval.initialize_task`` / ``is_successful``, ``json_action.JSONAction``
executed via ``AsyncAndroidEnv.execute_action``); this module only adds

  * emulator lifecycle (boot per guiexp/ANDROID_SETUP.md when no device is
    attached; ``adb emu kill`` only for emulators we started ourselves),
  * seed-deterministic task instances (a seed picks one
    ``generate_random_params()`` draw, stable across conditions and runs),
  * per-step observations in the web-side shape: raw screenshot PNG (b64),
    a Set-of-Marks screenshot with the same numeric element indexes M3A
    draws (``m3a_utils.add_ui_element_mark``), and the M3A-text-variant
    numbered element list (``ax_tree_text``) whose indexes are exactly the
    indexes the JSON action space addresses.

Observation keys match web guiexp's ``GuiEnv`` where the concept exists:
``screenshot_b64``, ``som_screenshot_b64``, ``ax_tree_text``, ``url``,
``goal_text``, plus ``last_action``/``last_action_error`` after a step.
On Android the web's "url" is the foreground activity (the closest analogue
to "where am I"); the name is kept so trajectories stay schema-identical.

Emulator notes (from guiexp/ANDROID_SETUP.md): boot with ``-grpc 8554``
(the a11y forwarding app needs it), keep console port 5554, and hand
android_world the ``adb-monkey-shim`` path as its adb so the broken
``monkey`` launcher invocations are rewritten (gotcha #2). First-run app
dialogs are neutralised deterministically at construction by pre-granting
permissions (notifications for Contacts/Calendar, file access for Markor)
-- upstream only avoids these because its AVD stays up for a whole
benchmark; on a fresh boot the first episode would otherwise be noisier
than the rest.
"""

from __future__ import annotations

import base64
import random
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import forksafe

# Every runner imports this module before it launches adb, so this is the one
# place to route subprocess launches through posix_spawn. See forksafe.py for
# the crash and hang this prevents.
forksafe.install()

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../computer-use/guiexp_android -> repo root
SDK_DIR = REPO_ROOT / "third-party" / "android-sdk"
ADB_PATH = SDK_DIR / "platform-tools" / "adb"
ADB_SHIM_PATH = SDK_DIR / "adb-monkey-shim"  # transparent adb wrapper; rewrites broken `monkey` launches
EMULATOR_PATH = SDK_DIR / "emulator" / "emulator"

AVD_NAME = "AndroidWorldAvd"
CONSOLE_PORT = 5554
GRPC_PORT = 8554
SERIAL = f"emulator-{CONSOLE_PORT}"

# Mirrors ANDROID_SETUP.md's boot command; -grpc is REQUIRED by the a11y app.
BOOT_CMD = (
    str(EMULATOR_PATH),
    "-avd", AVD_NAME,
    "-no-window", "-no-audio", "-no-boot-anim", "-no-snapshot", "-no-metrics",
    "-grpc", str(GRPC_PORT),
)

# Deterministic first-run state: grant the permissions whose dialogs would
# otherwise fire on the first episode after a cold boot. All calls are
# idempotent and failures are ignored (e.g. permission not declared).
STARTUP_GRANTS = (
    ("shell", "pm", "grant", "com.google.android.contacts",
     "android.permission.POST_NOTIFICATIONS"),
    ("shell", "pm", "grant", "com.simplemobiletools.calendar.pro",
     "android.permission.POST_NOTIFICATIONS"),
    ("shell", "pm", "grant", "net.gsantner.markor",
     "android.permission.READ_EXTERNAL_STORAGE"),
    ("shell", "pm", "grant", "net.gsantner.markor",
     "android.permission.WRITE_EXTERNAL_STORAGE"),
    ("shell", "appops", "set", "net.gsantner.markor",
     "MANAGE_EXTERNAL_STORAGE", "allow"),
    ("shell", "pm", "grant", "net.osmand",
     "android.permission.POST_NOTIFICATIONS"),
    ("shell", "pm", "grant", "net.osmand",
     "android.permission.ACCESS_FINE_LOCATION"),
    ("shell", "pm", "grant", "net.osmand",
     "android.permission.ACCESS_COARSE_LOCATION"),
    ("shell", "pm", "grant", "net.osmand",
     "android.permission.READ_EXTERNAL_STORAGE"),
    ("shell", "pm", "grant", "net.osmand",
     "android.permission.WRITE_EXTERNAL_STORAGE"),
)


# -- task registry ---------------------------------------------------------


def _task_classes() -> dict:
    from android_world.task_evals.single.contacts import ContactsAddContact
    from android_world.task_evals.single.calendar.calendar import SimpleCalendarAddOneEvent
    from android_world.task_evals.single.files import FilesMoveFile
    from android_world.task_evals.single.markor import MarkorCreateNote, MarkorDeleteNote
    from android_world.task_evals.single.osmand import OsmAndFavorite, OsmAndMarker

    return {
        "ContactsAddContact": ContactsAddContact,
        "SimpleCalendarAddOneEvent": SimpleCalendarAddOneEvent,
        "MarkorCreateNote": MarkorCreateNote,
        "MarkorDeleteNote": MarkorDeleteNote,
        "OsmAndFavorite": OsmAndFavorite,
        "OsmAndMarker": OsmAndMarker,
        "FilesMoveFile": FilesMoveFile,
    }


def instance_params(family: str, seed: int) -> dict:
    """Seed-deterministic instance binding via android_world's own generator.

    ``generate_random_params`` draws from the global ``random`` module, so we
    seed it privately and restore the outer state: a seed maps to the same
    instance on every machine and across conditions (discover vs told see the
    same task), exactly like web guiexp's sha256 instance_for_seed.
    """
    cls = _task_classes()[family]
    state = random.getstate()
    try:
        random.seed(seed)
        return cls.generate_random_params()
    finally:
        random.setstate(state)


@dataclass(frozen=True)
class AndroidTask:
    """One cell of the experiment: family x condition x seed."""

    family: str
    condition: str
    seed: int
    params: dict

    @property
    def task_id(self) -> str:
        return f"{self.family}__{self.condition}__s{self.seed}"


def get_task(family: str, condition: str, seed: int) -> AndroidTask:
    from .conditions import CONDITIONS, FAMILIES

    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    return AndroidTask(family=family, condition=condition, seed=seed,
                       params=instance_params(family, seed))


def goal_text(task: AndroidTask) -> str:
    """The goal block shared by every non-floor condition."""
    cls = _task_classes()[task.family]
    return cls.template.format(**task.params)


# -- emulator lifecycle ----------------------------------------------------


def _adb(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run([str(ADB_PATH), *args], capture_output=True, text=True,
                          timeout=timeout)


def emulator_is_running() -> bool:
    try:
        out = _adb("devices").stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.startswith(SERIAL) and line.strip().endswith("device")
               for line in out.splitlines())


def boot_emulator(timeout: float = 300.0) -> None:
    """Start the headless emulator and block until boot completes."""
    if not EMULATOR_PATH.exists():
        raise FileNotFoundError(f"emulator binary not found at {EMULATOR_PATH}")
    subprocess.Popen(BOOT_CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if emulator_is_running():
            out = _adb("shell", "getprop", "sys.boot_completed").stdout.strip()
            if out == "1":
                return
        time.sleep(5.0)
    raise TimeoutError(f"emulator {AVD_NAME} did not finish booting within {timeout:.0f}s")


def shutdown_emulator() -> None:
    """``adb emu kill`` and wait for the device to disappear (clean exit)."""
    if not emulator_is_running():
        return
    try:
        _adb("-s", SERIAL, "emu", "kill")
    except (OSError, subprocess.SubprocessError):
        return
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if not emulator_is_running():
            return
        time.sleep(2.0)


def _apply_startup_grants() -> None:
    for cmd in STARTUP_GRANTS:
        try:
            _adb("-s", SERIAL, *cmd)
        except (OSError, subprocess.SubprocessError):
            pass  # best-effort; a leftover dialog is data, not a crash


# -- observation rendering (M3A text variant + SoM) ------------------------


def _ui_element_line(ui_element, index: int) -> str:
    """One numbered element line, M3A's text format (m3a.py parity)."""
    parts = [f'"index": {index}']
    if ui_element.text:
        parts.append(f'"text": "{ui_element.text}"')
    if ui_element.content_description:
        parts.append(f'"content_description": "{ui_element.content_description}"')
    if ui_element.hint_text:
        parts.append(f'"hint_text": "{ui_element.hint_text}"')
    if ui_element.tooltip:
        parts.append(f'"tooltip": "{ui_element.tooltip}"')
    parts.append(f'"is_clickable": {str(ui_element.is_clickable).lower()}')
    parts.append(f'"is_long_clickable": {str(ui_element.is_long_clickable).lower()}')
    parts.append(f'"is_editable": {str(ui_element.is_editable).lower()}')
    if ui_element.is_scrollable:
        parts.append('"is_scrollable": true')
    if ui_element.is_focusable:
        parts.append('"is_focusable": true')
    parts.append(f'"is_selected": {str(ui_element.is_selected).lower()}')
    parts.append(f'"is_checked": {str(ui_element.is_checked).lower()}')
    return "UI element " + str(index) + ": {" + ", ".join(parts) + "}"


def element_list_text(ui_elements, logical_screen_size) -> str:
    """The numbered element list: indexes are positions in the FULL list
    (same convention as M3A and as the marks on the SoM screenshot)."""
    from android_world.agents import m3a_utils

    lines = []
    for index, ui_element in enumerate(ui_elements):
        if m3a_utils.validate_ui_element(ui_element, logical_screen_size):
            lines.append(_ui_element_line(ui_element, index))
    return "\n".join(lines)


def _encode_png_b64(rgb_array) -> str:
    import cv2
    import numpy as np

    if rgb_array is None or not isinstance(rgb_array, np.ndarray) or rgb_array.size == 0:
        return ""
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgb_array, cv2.COLOR_RGB2BGR))
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode("ascii")


def som_screenshot_b64(pixels, ui_elements, logical_screen_size,
                       physical_frame_boundary, orientation) -> str:
    """Screenshot with M3A's marks: a box + numeric index per valid element."""
    import cv2

    from android_world.agents import m3a_utils

    marked = pixels.copy()
    for index, ui_element in enumerate(ui_elements):
        if m3a_utils.validate_ui_element(ui_element, logical_screen_size):
            m3a_utils.add_ui_element_mark(
                marked, ui_element, index, logical_screen_size,
                physical_frame_boundary, orientation,
            )
    ok, buf = cv2.imencode(".png", cv2.cvtColor(marked, cv2.COLOR_RGB2BGR))
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


# -- the environment -------------------------------------------------------


class AndroidWorldEnv:
    """reset(task) -> observation dict; step(action_text) -> (obs, done, reward).

    ``reward`` is android_world's own oracle (``task.is_successful``), 1.0
    when the task's programmatic validator passes; the episode auto-
    terminates at reward >= 1.0 exactly like web guiexp.
    """

    def __init__(
        self,
        console_port: int = CONSOLE_PORT,
        grpc_port: int = GRPC_PORT,
        adb_path: str | Path = ADB_SHIM_PATH,
        boot_if_needed: bool = True,
        wait_after_action_seconds: float = 2.0,
    ):
        self.booted_by_us = False
        if not emulator_is_running():
            if not boot_if_needed:
                raise RuntimeError(
                    f"no emulator at {SERIAL}; start it or pass boot_if_needed=True")
            boot_emulator()
            self.booted_by_us = True
        from android_world.env import env_launcher

        self.aw_env = env_launcher.load_and_setup_env(
            console_port=console_port,
            emulator_setup=False,   # AVD was set up once (ANDROID_SETUP.md #4)
            freeze_datetime=True,
            adb_path=str(adb_path),
            grpc_port=grpc_port,
        )
        self.wait_after_action_seconds = wait_after_action_seconds
        self.task: AndroidTask | None = None
        self._task_impl = None  # the android_world TaskEval instance
        _apply_startup_grants()

    # -- lifecycle ---------------------------------------------------------

    def reset(self, task: AndroidTask) -> dict:
        impl_cls = _task_classes()[task.family]
        self._task_impl = impl_cls(task.params)
        # Canonical android_world ordering (minimal_task_runner.py):
        # go home, then initialize the task (device time + app state).
        self.aw_env.reset(go_home=True)
        self.aw_env.hide_automation_ui()
        self._task_impl.initialize_task(self.aw_env)
        self.task = task
        time.sleep(1.0)  # let the home screen settle before the first obs
        goal = "" if task.condition == "floor" else goal_text(task)
        return self._observe(goal)

    def observe(self, goal: str | None = None) -> dict:
        """One observation of the CURRENT screen, without resetting the task.

        The verification stage's reactive repair resumes from wherever a
        failed program left the device, so it needs the observation shape the
        agent loop expects without the state being wiped first.
        """
        if goal is None:
            goal = "" if (self.task is None or self.task.condition == "floor") else goal_text(self.task)
        return self._observe(goal)

    def close(self) -> None:
        if self._task_impl is not None:
            try:
                self._task_impl.tear_down(self.aw_env)
            except Exception:  # noqa: BLE001 - teardown is best-effort cleanup
                pass
        try:
            self.aw_env.close()
        except Exception:  # noqa: BLE001
            pass
        self.task = None
        self._task_impl = None

    def stop_emulator(self, force: bool = False) -> None:
        """Kill the emulator, but never one we did not start ourselves."""
        if self.booted_by_us or force:
            shutdown_emulator()

    # -- observation -------------------------------------------------------

    def _observe(self, goal: str = None) -> dict:
        state = self.aw_env.get_state(wait_to_stabilize=False)
        logical = self.aw_env.logical_screen_size
        screenshot_b64 = _encode_png_b64(state.pixels)
        obs = {
            "screenshot_b64": screenshot_b64,
            "som_screenshot_b64": som_screenshot_b64(
                state.pixels, state.ui_elements, logical,
                self.aw_env.physical_frame_boundary, self.aw_env.orientation,
            ) if screenshot_b64 else "",
            "ax_tree_text": element_list_text(state.ui_elements, logical),
            # web guiexp's "url": where we are -- on Android, the foreground
            # activity (kept under the same key for trajectory-schema parity).
            "url": self.aw_env.foreground_activity_name,
            "goal_text": goal if goal is not None else "",
        }
        return obs

    # -- acting and reward -------------------------------------------------

    def step(self, action_text: str) -> tuple[dict, bool, float]:
        """Execute one action reply; return (obs, done, reward)."""
        from .actions import ActionError, is_done, parse_action

        if self._task_impl is None:
            raise RuntimeError("call reset(task) before step()")

        error: str | None = None
        action = None
        try:
            action = parse_action(action_text)
        except ActionError as exc:
            error = f"{type(exc).__name__}: {exc}"

        done = False
        reward = self.reward()
        if action is not None and not is_done(action):
            try:
                self.aw_env.execute_action(action)
                time.sleep(self.wait_after_action_seconds)
                reward = self.reward()
            except Exception as exc:  # noqa: BLE001 - the failure shape is data
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        elif action is not None and is_done(action):
            done = True  # agent-declared termination (complete or infeasible)
        if reward >= 1.0:
            done = True  # same auto-terminate rule as web guiexp

        obs = self._observe()
        obs["last_action"] = action_text
        obs["last_action_error"] = error
        return obs, done, reward

    def reward(self) -> float:
        """1.0 iff android_world's programmatic validator passes."""
        if self._task_impl is None:
            return 0.0
        return float(self._task_impl.is_successful(self.aw_env))
