"""Run a compiled OSWorld family program on the guest, judged by the checker.

The OSWorld mirror of guiexp_android/program_runtime.py. ``program_from_source``
/ ``program_from_path`` exec the compiler output into ``program(device,
binding)``; the ``device`` handed to programs is a :class:`ProgramDevice`:

  * ``elements()``      fresh filtered a11y dump as plain dicts (the SAME
                        numbered list the reactive agent saw: index, role,
                        name, text, description, clickable, editable, ...)
  * ``find(name=..., contains=..., role=..., description=..., editable=...)``
                        -> element index or None (a11y text/name/role -- the
                        location contract; never coordinates)
  * ``click(index=None, **find)`` / ``input_text(text, index=None, **find)``
  * ``press(key)`` / ``hotkey(*keys)`` / ``scroll(direction)``
  * ``open_app(name)`` / ``settle(s)`` / ``wait()``
  * ``execute({...})``  raw action-space dict passthrough
  * ``active_window()`` the where-am-I string the agent observed

Judging: :class:`ProgramRunner` reuses ONE :class:`guest_env.OSWorldEnv` for
many bindings; ``run(program, binding, family)`` resets the task with the
JUDGE params, runs the program with ``binding``, then the family checker.
"""

from __future__ import annotations

import contextlib
import signal
import threading
import time
import types
from pathlib import Path
from typing import Any, Callable

from . import families, guest_env
from .guest_env import OSWorldEnv

Program = Callable[[Any, dict], bool]

REPLAY_TIMEOUT_S = 1200  # wall-clock cap on ONE program replay (reset+run+judge)


class ReplayTimeout(Exception):
    """One program replay outran its wall-clock deadline."""


_DEADLINE = {"armed": False}


@contextlib.contextmanager
def replay_deadline(seconds: float | None = None):
    """Raise ReplayTimeout if the block outruns ``seconds`` (0/negative off).

    Identical guard to the Android arm: a wedged guest stalls one binding,
    not the whole cell; no model call is inside the deadline.
    """
    if seconds is None:
        seconds = REPLAY_TIMEOUT_S
    usable = (
        seconds is not None and seconds > 0 and not _DEADLINE["armed"]
        and hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
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


class ProgramDevice:
    """The device contract compiled programs program(device, binding) get."""

    def __init__(self, env: OSWorldEnv, settle_s: float | None = None,
                 max_actions: int = 120):
        self._env = env
        self._settle_s = settle_s if settle_s is not None else env.settle_s
        self._max_actions = max_actions
        self._actions = 0

    # -- observation ---------------------------------------------------------

    def elements(self) -> list[dict]:
        return guest_env.element_records(guest_env.get_accessibility_xml())

    def find(
        self,
        name: str | None = None,
        contains: str | None = None,
        description: str | None = None,
        role: str | None = None,
        editable: bool | None = None,
        clickable: bool | None = None,
    ) -> int | None:
        """First element index matching every given criterion (name and
        description compare case-insensitively; contains is a substring test
        on name+text+description)."""
        want = {"editable": editable, "clickable": clickable}

        def eq(got: str, want_: str) -> bool:
            return (got or "").strip().lower() == (want_ or "").strip().lower()

        for element in self.elements():
            if any(v is not None and element[k] is not v for k, v in want.items()):
                continue
            if role is not None and not eq(element["role"], role):
                continue
            if name is not None and not eq(element["name"], name):
                continue
            if description is not None and not eq(element["description"], description):
                continue
            if contains is not None:
                blob = " ".join(
                    (element["name"], element["text"], element["description"])
                ).lower()
                if contains.lower() not in blob:
                    continue
            return element["index"]
        return None

    def active_window(self) -> str:
        return guest_env.active_window_title(guest_env.get_accessibility_xml())

    # -- actuation (every action settles like the reactive env) --------------

    def execute(self, action: dict) -> None:
        self._actions += 1
        if self._actions > self._max_actions:
            raise RuntimeError(
                f"program exceeded the {self._max_actions}-action budget")
        self._env.execute_action(action)

    def click(self, index: int | None = None, **find_kwargs) -> None:
        index = self._require_index(index, find_kwargs, "click")
        self.execute({"action_type": "click", "index": index})

    def long_press(self, index: int | None = None, **find_kwargs) -> None:
        index = self._require_index(index, find_kwargs, "long_press")
        self.execute({"action_type": "long_press", "index": index})

    def input_text(self, text: str, index: int | None = None, **find_kwargs) -> None:
        index = self._require_index(index, find_kwargs, "input_text")
        self.execute({"action_type": "input_text", "text": str(text), "index": index})

    def type_at_caret(self, text: str) -> None:
        """Type at the current caret (no click, no select-all)."""
        self.execute({"action_type": "input_text", "text": str(text)})

    def press(self, key: str) -> None:
        self.execute({"action_type": "press", "key": key})

    def hotkey(self, *keys: str) -> None:
        self.execute({"action_type": "hotkey", "keys": list(keys)})

    def scroll(self, direction: str = "down") -> None:
        self.execute({"action_type": "scroll", "direction": direction})

    def open_app(self, app_name: str) -> None:
        self.execute({"action_type": "open_app", "app_name": app_name})

    def wait(self) -> None:
        self.execute({"action_type": "wait"})

    def settle(self, seconds: float = 2.0) -> None:
        time.sleep(seconds)

    # -- internal -------------------------------------------------------------

    def _require_index(self, index: int | None, find_kwargs: dict, what: str) -> int:
        if index is None:
            index = self.find(**find_kwargs)
        if index is None:
            raise ValueError(f"{what}: no element matches {find_kwargs or 'index=None'}")
        return index


class ProgramRunner:
    """One guest, many bindings; success is the family's own checker."""

    def __init__(self, env: OSWorldEnv):
        self.env = env

    def run(
        self,
        program: Program,
        binding: dict,
        family: str,
        judge_params: dict | None = None,
        reset_attempts: int = 2,
        device_factory: Callable[[OSWorldEnv], Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict:
        """Run ``program`` with ``binding``; judge with the family checker over
        ``judge_params`` (defaults to params built from ``binding``).

        ``device_factory`` replaces the default :class:`ProgramDevice`
        construction (the verification stage swaps in a tracing device); the
        device that ran is handed back in the result.
        """
        holder: dict = {}
        try:
            with replay_deadline(timeout_s):
                return self._run_once(program, binding, family, judge_params,
                                      reset_attempts, device_factory, holder)
        except ReplayTimeout as exc:
            outcome = {"passed": False, "error": str(exc), "reward": 0.0}
            if holder.get("device") is not None:
                outcome["device"] = holder["device"]
            return outcome

    def _run_once(self, program, binding, family, judge_params, reset_attempts,
                  device_factory, holder) -> dict:
        params = judge_params if judge_params is not None else \
            families.binding_to_params(family, binding)
        task = guest_env.OSWorldTask(family=family, condition="compile", seed=0,
                                     params=params)
        reset_error: str | None = None
        for attempt in range(reset_attempts):
            try:
                self.env.reset(task)
                reset_error = None
                break
            except ReplayTimeout:
                raise
            except Exception as exc:  # noqa: BLE001 - transient guest noise
                reset_error = f"reset: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
                time.sleep(3.0)
        if reset_error is not None:
            return {"passed": False, "error": reset_error, "reward": 0.0}
        device = device_factory(self.env) if device_factory else ProgramDevice(self.env)
        holder["device"] = device
        error: str | None = None
        try:
            program(device, dict(binding))
        except ReplayTimeout:
            raise
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        try:
            reward = self.env.reward()
        except ReplayTimeout:
            raise
        except Exception as exc:  # noqa: BLE001 - checker-side noise
            reward = 0.0
            error = error or f"checker: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        return {"passed": reward >= 1.0, "error": error, "reward": reward,
                "device": device}
