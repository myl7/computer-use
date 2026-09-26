"""Run a compiled CommentPost program against the browser, judged by the DB checker.

The WebArena mirror of guiexp_android/program_runtime.py:

  * program_from_source execs the builder output into a callable
    ``program(device, binding: dict)``;
  * the ``device`` is a WebDevice over the same Playwright env the reactive
    harness uses -- same action semantics, but addresses elements through
    ``find(...)`` on semantic attributes instead of indexes;
  * ProgramRunner.run(program, binding, family) resets the episode state,
    runs the program, and asks the family checker (DB truth).

Program localization contract (same rule as android): DOM text/attributes,
never coordinates or CSS positions.
"""

from __future__ import annotations

import time
import types
from pathlib import Path
from typing import Any, Callable

Program = Callable[[Any, dict], bool]

_SETTLE_S = 0.6
REPLAY_TIMEOUT_S = 900
MAX_ACTIONS = 120


def program_from_source(source: str, name: str = "family_program") -> tuple[types.ModuleType, Program]:
    compile(source, f"<{name}>", "exec")  # syntax gate before any exec
    module = types.ModuleType(name)
    module.__dict__["__file__"] = f"<{name}>"
    exec(source, module.__dict__)  # noqa: S102 - builder-produced source
    program = getattr(module, "program", None)
    if not callable(program):
        raise ValueError(f"{name}: no callable 'program(device, binding)' defined")
    return module, program


def program_from_path(path: Path | str) -> tuple[types.ModuleType, Program]:
    path = Path(path)
    return program_from_source(path.read_text(), name=path.stem)


class WebDevice:
    """The device contract compiled programs get: semantic find + actions."""

    def __init__(self, env, settle_s: float | None = None, max_actions: int = MAX_ACTIONS):
        self._env = env
        self._settle_s = settle_s if settle_s is not None else _SETTLE_S
        self._max_actions = max_actions
        self._actions = 0
        self.trace: list[str] = []  # what ran, for the repair stage

    # -- observation ---------------------------------------------------------

    def elements(self) -> list[dict]:
        return self._env._elements()

    def find(
        self,
        text: str | None = None,
        contains: str | None = None,
        tag: str | None = None,
        href: str | None = None,
        href_contains: str | None = None,
        placeholder: str | None = None,
        aria: str | None = None,
        name: str | None = None,
        editable: bool | None = None,
    ) -> int | None:
        """First element id matching every given criterion. Text compares
        case-insensitively; contains is a substring test on text+placeholder+
        aria+href+value; href_contains is a substring test on href; tag
        matches the element's tag name."""
        def eq(got, want):
            return (got or "").strip().lower() == (want or "").strip().lower()

        for element in self.elements():
            if tag is not None and not eq(element["tag"], tag):
                continue
            if editable is not None and bool(element.get("editable")) != bool(editable):
                continue
            if href is not None and not eq(element.get("href"), href):
                continue
            if href_contains is not None and href_contains.lower() not in (element.get("href") or "").lower():
                continue
            if placeholder is not None and not eq(element.get("placeholder"), placeholder):
                continue
            if aria is not None and not eq(element.get("aria"), aria):
                continue
            if name is not None and not eq(element.get("name"), name):
                continue
            if text is not None and not eq(element.get("text"), text):
                continue
            if contains is not None:
                blob = " ".join(str(element.get(key) or "") for key in
                                ("text", "placeholder", "aria", "href", "value")).lower()
                if contains.lower() not in blob:
                    continue
            return element["index"]
        return None

    def current_url(self) -> str:
        return self._env.page.url

    def page_text(self) -> str:
        return self._env.page.inner_text("body") or ""

    # -- actuation -----------------------------------------------------------

    def _execute(self, action: dict) -> None:
        self._actions += 1
        if self._actions > self._max_actions:
            raise RuntimeError(f"program exceeded the {self._max_actions}-action budget")
        self.trace.append({k: (v[:80] if isinstance(v, str) else v) for k, v in action.items()}.__str__())
        obs, _done, _reward = self._env.step(action)
        err = obs.get("last_action_error")
        if err:
            raise RuntimeError(f"action failed: {err}")

    def click(self, index: int | None = None, **find) -> None:
        index = self._require(index, find, "click")
        self._execute({"action_type": "click", "index": index})

    def input_text(self, text: str, index: int | None = None, **find) -> None:
        index = self._require(index, find, "input_text")
        self._execute({"action_type": "input_text", "text": str(text), "index": index})

    def keyboard_enter(self) -> None:
        self._execute({"action_type": "keyboard_enter"})

    def scroll(self, direction: str = "down") -> None:
        self._execute({"action_type": "scroll", "direction": direction})

    def navigate_back(self) -> None:
        self._execute({"action_type": "navigate_back"})

    def goto(self, url: str) -> None:
        self._execute({"action_type": "goto", "url": url})

    def wait(self) -> None:
        self._execute({"action_type": "wait"})

    def settle(self, seconds: float = _SETTLE_S) -> None:
        time.sleep(seconds)

    def _require(self, index, find, what):
        if index is None:
            index = self.find(**find)
        if index is None:
            raise ValueError(f"{what}: no element matches {find or 'index=None'}")
        return index


class ProgramRunner:
    """One browser env, many bindings; success is the family checker."""

    def __init__(self, env):
        self.env = env

    def run(
        self,
        program: Program,
        binding: dict,
        family: str,
        judge_params: dict | None = None,
        device_factory=None,
        timeout_s: float | None = None,
    ) -> dict:
        import signal
        import threading

        seconds = timeout_s if timeout_s is not None else REPLAY_TIMEOUT_S
        usable = (
            seconds and seconds > 0
            and hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        )
        holder: dict = {}

        class ReplayDeadline(BaseException):
            """Outside-code deadline that generated ``except Exception`` cannot catch."""

        def _fire(_signum, _frame):
            raise ReplayDeadline(f"replay timeout after {seconds:g}s")

        try:
            if usable:
                previous = signal.signal(signal.SIGALRM, _fire)
                signal.setitimer(signal.ITIMER_REAL, float(seconds))
            try:
                return self._run_once(program, binding, family, judge_params, device_factory, holder)
            finally:
                if usable:
                    signal.setitimer(signal.ITIMER_REAL, 0.0)
                    signal.signal(signal.SIGALRM, previous)
        except ReplayDeadline as exc:
            outcome = {"passed": False, "error": str(exc), "reward": 0.0}
            # Best-effort browser recovery. The next replay still performs its
            # normal task reset, which also clears any partial task effect.
            try:
                self.env.page.goto(f"{self.env.reddit_url}/", timeout=30000)
            except Exception:  # noqa: BLE001
                pass
            if holder.get("device") is not None:
                outcome["device"] = holder["device"]
            return outcome

    def _run_once(self, program, binding, family, judge_params, device_factory, holder) -> dict:
        from .family import binding_to_params, task_for

        params = judge_params if judge_params is not None else binding_to_params(family, binding)
        task = task_for(family, "compile", seed=0, env=self.env)
        task.params = params  # the JUDGE params (ground truth at deploy)
        reset_error = None
        for attempt in range(2):
            try:
                self.env.reset(task)
                reset_error = None
                break
            except TimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001 - env noise, retry once
                reset_error = f"reset: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
                time.sleep(3.0)
        if reset_error is not None:
            return {"passed": False, "error": reset_error, "reward": 0.0}
        device = device_factory(self.env) if device_factory else WebDevice(self.env)
        holder["device"] = device
        error = None
        try:
            program(device, dict(binding))
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        try:
            reward = self.env.reward(task)
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            reward = 0.0
            error = error or f"oracle: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        return {"passed": reward >= 1.0, "error": error, "reward": reward, "device": device}
