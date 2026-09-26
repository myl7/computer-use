"""Run a compiled Android family program on the device, judged by the oracle.

The Android mirror of ``guiexp/program_runtime.py``. Loading is identical
in spirit: ``program_from_source`` / ``program_from_path`` exec the compiler
output into a callable ``program(device, binding: dict)`` (syntax-checked,
``PARAMS_SCHEMA`` read when present).

The ``device`` handed to programs is a :class:`ProgramDevice`, a thin safe
wrapper over the same android_world controller the reactive harness uses:

  * ``elements()``           fresh a11y dump as plain dicts (index, text,
                             hint, description, clickable, editable, ...)
  * ``find(text=..., contains=..., hint=..., description=..., ...)`` ->
                             element index (position in the full list, i.e.
                             exactly the index the action space addresses)
  * ``click(index=None, **find)`` / ``input_text(text, index=None, **find)``
  * ``scroll(direction)`` / ``open_app(name)`` / ``navigate_back()`` /
     ``navigate_home()`` / ``keyboard_enter()`` / ``wait()``
  * ``execute({...})``       raw android_world JSONAction passthrough
  * ``adb_shell(*args)``     raw adb for uiautomator-dump style programs
  * ``current_activity()``   where-am-I, same string the agent observed

Every action settles ~2 s, the same wait the reactive env applies, so a
program's next ``elements()`` sees the post-action screen.

Judging: :class:`ProgramRunner` reuses ONE :class:`AndroidWorldEnv` for many
bindings. ``run(program, binding, family, judge_params)`` resets the task
with the JUDGE params (ground truth at deploy time; defaults to params built
from ``binding``), runs the program with ``binding`` (e.g. what an extraction
model produced), then asks ``env.reward()`` -- literally the family's own
``is_successful`` oracle, not a re-implementation.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable

from . import android_env
from .android_env import AndroidTask, AndroidWorldEnv

Program = Callable[[Any, dict], bool]

_SETTLE_S = 2.0  # env.wait_after_action_seconds parity

# Wall-clock cap on ONE program replay (reset, program, oracle). Replays cost
# no model tokens, so cutting one short changes no measurement; what it buys
# is that a wedged emulator stalls one binding instead of the whole cell. The
# cap is deliberately far above any healthy replay: the slowest family's
# 120-action budget at ~2 s of settle per action is ~4 min.
REPLAY_TIMEOUT_S = 1200


class ReplayTimeout(Exception):
    """One program replay outran its wall-clock deadline."""


_DEADLINE = {"armed": False}  # one deadline at a time; the outermost governs


@contextlib.contextmanager
def replay_deadline(seconds: float | None = None):
    """Raise :class:`ReplayTimeout` if the block outruns ``seconds``.

    ``seconds=None`` means :data:`REPLAY_TIMEOUT_S` (read at call time, so a
    test can lower it); ``0`` or a negative value turns the guard off.

    The alarm is cancelled and the previous handler restored in a ``finally``,
    always. The block is entered unguarded -- never skipped -- when the guard
    cannot be armed: a platform without ``SIGALRM``, a call off the main
    thread (only the main thread may install a handler), or a deadline
    already armed further out, so nesting is safe and the outermost deadline
    is the one that governs.
    """
    if seconds is None:
        seconds = REPLAY_TIMEOUT_S
    usable = (
        seconds is not None
        and seconds > 0
        and not _DEADLINE["armed"]
        and hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not usable:
        yield
        return

    def _fire(_signum, _frame):
        raise ReplayTimeout(f"replay timeout after {seconds:g}s")

    previous = signal.signal(signal.SIGALRM, _fire)
    _DEADLINE["armed"] = True
    try:
        signal.setitimer(signal.ITIMER_REAL, float(seconds))
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)
        _DEADLINE["armed"] = False


def program_from_source(source: str, name: str = "family_program") -> tuple[types.ModuleType, Program]:
    """Exec the compiled source; return (module, program callable)."""
    compile(source, f"<{name}>", "exec")  # syntax gate before any exec
    module = types.ModuleType(name)
    module.__dict__["__file__"] = f"<{name}>"
    exec(source, module.__dict__)  # noqa: S102 - compiled-by-us program source
    program = getattr(module, "program", None)
    if not callable(program):
        raise ValueError(f"{name}: no callable 'program(device, binding)' defined")
    return module, program


def program_from_path(path: Path | str) -> tuple[types.ModuleType, Program]:
    path = Path(path)
    return program_from_source(path.read_text(), name=path.stem)


def _element_dict(ui_element, index: int) -> dict:
    """One a11y element as a plain dict, keys mirroring the M3A list."""
    return {
        "index": index,
        "text": ui_element.text or "",
        "hint": ui_element.hint_text or "",
        "description": ui_element.content_description or "",
        "tooltip": ui_element.tooltip or "",
        "clickable": bool(ui_element.is_clickable),
        "long_clickable": bool(ui_element.is_long_clickable),
        "editable": bool(ui_element.is_editable),
        "scrollable": bool(ui_element.is_scrollable),
        "focusable": bool(ui_element.is_focusable),
        "selected": bool(ui_element.is_selected),
        "checked": bool(ui_element.is_checked),
    }


class ProgramDevice:
    """The device contract compiled programs program(device, binding) get."""

    def __init__(self, env: AndroidWorldEnv, settle_s: float | None = None, max_actions: int = 120):
        self._env = env
        # Default to the env's own per-action settle so programs see screens
        # exactly as settled as the reactive harness left them.
        self._settle_s = settle_s if settle_s is not None else env.wait_after_action_seconds
        self._max_actions = max_actions
        self._actions = 0

    # -- observation -------------------------------------------------------

    def elements(self) -> list[dict]:
        """Fresh a11y dump; ``index`` is the position in the FULL element
        list, the same convention the reactive action space addresses."""
        state = self._env.aw_env.get_state(wait_to_stabilize=False)
        return [_element_dict(e, i) for i, e in enumerate(state.ui_elements)]

    def find(
        self,
        text: str | None = None,
        contains: str | None = None,
        hint: str | None = None,
        description: str | None = None,
        clickable: bool | None = None,
        editable: bool | None = None,
        scrollable: bool | None = None,
    ) -> int | None:
        """First element index matching every given criterion (text / hint /
        description compare case-insensitively; contains is a substring
        test on text+hint+description), else None."""
        want = {"clickable": clickable, "editable": editable, "scrollable": scrollable}

        def eq(got: str, want_: str) -> bool:
            return (got or "").strip().lower() == (want_ or "").strip().lower()

        for element in self.elements():
            if any(v is not None and element[k] is not v for k, v in want.items()):
                continue
            if text is not None and not eq(element["text"], text):
                continue
            if hint is not None and not eq(element["hint"], hint):
                continue
            if description is not None and not eq(element["description"], description):
                continue
            if contains is not None:
                blob = " ".join((element["text"], element["hint"], element["description"])).lower()
                if contains.lower() not in blob:
                    continue
            return element["index"]
        return None

    def current_activity(self) -> str:
        return self._env.aw_env.foreground_activity_name

    # -- actuation (every action settles first) ----------------------------

    def execute(self, action: dict) -> None:
        """Run one android_world JSONAction (dict form) on the device.

        Bounded: a program that never converges (e.g. an unbounded
        scroll-and-retry loop) is stopped at ``max_actions`` actions rather
        than running forever.
        """
        from android_world.env import json_action

        self._actions += 1
        if self._actions > self._max_actions:
            raise RuntimeError(
                f"program exceeded the {self._max_actions}-action budget"
            )
        self._env.aw_env.execute_action(json_action.JSONAction(**action))
        time.sleep(self._settle_s)

    def click(self, index: int | None = None, **find_kwargs) -> None:
        index = self._require_index(index, find_kwargs, "click")
        self.execute({"action_type": "click", "index": index})

    def long_press(self, index: int | None = None, **find_kwargs) -> None:
        index = self._require_index(index, find_kwargs, "long_press")
        self.execute({"action_type": "long_press", "index": index})

    def input_text(self, text: str, index: int | None = None, **find_kwargs) -> None:
        """Focus the field (by index or find-criteria) and type ``text``."""
        index = self._require_index(index, find_kwargs, "input_text")
        self.execute({"action_type": "input_text", "text": str(text), "index": index})

    def keyboard_enter(self) -> None:
        self.execute({"action_type": "keyboard_enter"})

    def scroll(self, direction: str = "down") -> None:
        self.execute({"action_type": "scroll", "direction": direction})

    def open_app(self, app_name: str) -> None:
        self.execute({"action_type": "open_app", "app_name": app_name})

    def navigate_back(self) -> None:
        self.execute({"action_type": "navigate_back"})

    def navigate_home(self) -> None:
        self.execute({"action_type": "navigate_home"})

    def wait(self) -> None:
        self.execute({"action_type": "wait"})

    def settle(self, seconds: float = _SETTLE_S) -> None:
        time.sleep(seconds)

    # -- raw escape hatch ----------------------------------------------------

    def adb_shell(self, *args: str, timeout: float = 30.0) -> str:
        """Raw adb passthrough (e.g. for a uiautomator-dump style program)."""
        cmd = [str(android_env.ADB_PATH), "-s", android_env.SERIAL, "shell", *args]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout

    # -- internal ------------------------------------------------------------

    def _require_index(self, index: int | None, find_kwargs: dict, what: str) -> int:
        if index is None:
            index = self.find(**find_kwargs)
        if index is None:
            raise ValueError(f"{what}: no element matches {find_kwargs or 'index=None'}")
        return index


class ProgramRunner:
    """One emulator, many bindings; success is the family's own oracle."""

    def __init__(self, env: AndroidWorldEnv):
        self.env = env

    def run(
        self,
        program: Program,
        binding: dict,
        family: str,
        judge_params: dict | None = None,
        reset_attempts: int = 2,
        device_factory: Callable[["AndroidWorldEnv"], Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        """Run ``program`` with ``binding``; judge with the family evaluator
        over ``judge_params`` (defaults to params built from ``binding``).

        ``reset`` gets a bounded retry: android_world's task setup does raw
        adb filesystem clears whose glob-based ``rm`` can transiently fail
        (e.g. the app flushing a file between the emptiness check and the
        remove); that is harness noise, not a program failure, so it is
        retried once before the binding is scored as failed.

        ``device_factory`` replaces the default :class:`ProgramDevice`
        construction, which is how the verification stage swaps in a device
        that records the executed trace for its repair loop. The device that
        ran is handed back in the result so the caller can read that trace.

        The whole replay runs under a wall-clock deadline (``timeout_s``,
        default :data:`REPLAY_TIMEOUT_S`; ``0`` turns it off). A replay that
        outruns it is scored as a failed binding with the error ``replay
        timeout after <n>s``, in the same shape as any other failure, and the
        caller goes on to the next binding. No model call is inside this
        deadline, so no token measurement can be cut short by it.
        """
        holder: dict = {}
        try:
            with replay_deadline(timeout_s):
                return self._run_once(
                    program, binding, family, judge_params, reset_attempts,
                    device_factory, holder,
                )
        except ReplayTimeout as exc:
            outcome = {"passed": False, "error": str(exc), "reward": 0.0}
            if holder.get("device") is not None:
                outcome["device"] = holder["device"]
            return outcome

    def _run_once(
        self,
        program: Program,
        binding: dict,
        family: str,
        judge_params: dict | None,
        reset_attempts: int,
        device_factory: Callable[["AndroidWorldEnv"], Any] | None,
        holder: dict,
    ) -> dict:
        """One replay, no deadline of its own: reset, program, oracle.

        ``holder`` is where the device is parked as soon as it exists, so a
        caller whose replay is cut short can still read the trace of what the
        program managed to do.
        """
        import time as _time

        from .compiler import binding_to_params

        params = judge_params if judge_params is not None else binding_to_params(family, binding)
        task = AndroidTask(family=family, condition="compile", seed=0, params=params)
        reset_error: str | None = None
        for attempt in range(reset_attempts):
            try:
                self.env.reset(task)  # go home + initialize_task (clean app state)
                reset_error = None
                break
            except ReplayTimeout:  # the deadline, not a task-setup failure
                raise
            except Exception as exc:  # noqa: BLE001 - transient adb/task-setup noise
                reset_error = f"reset: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
                _time.sleep(3.0)
        if reset_error is not None:
            return {"passed": False, "error": reset_error, "reward": 0.0}
        device = device_factory(self.env) if device_factory else ProgramDevice(self.env)
        holder["device"] = device
        error: str | None = None
        try:
            program(device, dict(binding))
        except ReplayTimeout:  # the deadline, not a program failure
            raise
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        try:
            reward = self.env.reward()
        except ReplayTimeout:  # the deadline, not oracle noise
            raise
        except Exception as exc:  # noqa: BLE001 - oracle-side adb noise
            reward = 0.0
            error = error or f"oracle: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        return {"passed": reward >= 1.0, "error": error, "reward": reward, "device": device}
