"""Run a compiled family program against the real app, judged by env's oracle.

Loading: ``program_from_source`` / ``program_from_path`` turn the compiler's
output into a callable ``program(page, binding, base_url)`` (syntax-checked;
``PARAMS_SCHEMA`` is read when present).

Judging: :class:`BindingRunner` holds one :class:`guiexp.env.GuiEnv` (one
Chromium) for many bindings. For each binding it sets the env's task to the
binding's six fields, snapshots how many matching events already exist (the
app carries earlier episodes' state), runs the program on the env's own
page, and asks ``env.reward()`` -- literally the same six-field oracle the
reactive conditions are judged by, not a re-implementation.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Callable

from .env import GuiEnv, Task

Program = Callable[[Any, dict, str], bool]


_POPUP_WAIT_MS = 1500

# page methods whose result is a Locator: those get popup-aware wrapping so a
# program's own .click() on a target="_blank" element arms a popup waiter
# (without one, headless Chromium suppresses the popup entirely).
_LOCATOR_RETURNING = (
    "locator",
    "get_by_role",
    "get_by_test_id",
    "get_by_label",
    "get_by_text",
    "get_by_placeholder",
    "get_by_alt_text",
    "get_by_title",
)


class _PopupAwareLocator:
    """Locator wrapper: ``click()`` on a target="_blank" element waits for
    and adopts the popup (opener stays open until the binding ends)."""

    def __init__(self, locator, holder: dict, timeout_ms: int = _POPUP_WAIT_MS):
        object.__setattr__(self, "_loc", locator)
        object.__setattr__(self, "_holder", holder)
        object.__setattr__(self, "_timeout_ms", timeout_ms)

    def __getattr__(self, name):
        return getattr(self._loc, name)

    @property
    def first(self):
        return _PopupAwareLocator(self._loc.first, self._holder, self._timeout_ms)

    @property
    def last(self):
        return _PopupAwareLocator(self._loc.last, self._holder, self._timeout_ms)

    def nth(self, index):
        return _PopupAwareLocator(self._loc.nth(index), self._holder, self._timeout_ms)

    def filter(self, *args, **kwargs):
        return _PopupAwareLocator(self._loc.filter(*args, **kwargs), self._holder, self._timeout_ms)

    def click(self, *args, **kwargs):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            opens_popup = self._loc.get_attribute("target") == "_blank"
        except Exception:
            opens_popup = False
        if not opens_popup:
            return self._loc.click(*args, **kwargs)
        try:
            with self._loc.page.context.expect_page(timeout=self._timeout_ms) as popup_info:
                self._loc.click(*args, **kwargs)
            popup = popup_info.value
        except PlaywrightTimeout:
            return  # the click ran; no popup fired
        self._holder["page"] = popup  # adopt (same swap the context listener makes)
        try:
            popup.wait_for_load_state("domcontentloaded")
        except Exception:
            pass


class _ActivePageProxy:
    """Forwards every attribute access to the CURRENTLY active page.

    Compiled programs receive this in place of a raw Playwright ``page``.
    When a click opens a popup (``target="_blank"``), the runner swaps the
    active page and the program's handle follows -- the program's plain
    ``click(...)`` + ``wait_for_url(...)`` just works, matching the popup
    adoption the reactive env gives the reactive arm for free. Locator-
    returning calls are wrapped so the program's own clicks get the same
    treatment (see _PopupAwareLocator).
    """

    def __init__(self, holder: dict):
        object.__setattr__(self, "_holder", holder)

    def __getattr__(self, name):
        value = getattr(self._holder["page"], name)
        if name in _LOCATOR_RETURNING:
            def locator_returning(*args, **kwargs):
                return _PopupAwareLocator(value(*args, **kwargs), self._holder)

            return locator_returning
        return value


def program_from_source(source: str, name: str = "family_program") -> tuple[types.ModuleType, Program]:
    """Exec the compiled source; return (module, program callable)."""
    compile(source, f"<{name}>", "exec")  # syntax gate before any exec
    module = types.ModuleType(name)
    module.__dict__["__file__"] = f"<{name}>"
    exec(source, module.__dict__)  # noqa: S102 - compiled-by-us program source
    program = getattr(module, "program", None)
    if not callable(program):
        raise ValueError(f"{name}: no callable 'program(page, binding, base_url)' defined")
    return module, program


def program_from_path(path: Path | str) -> tuple[types.ModuleType, Program]:
    path = Path(path)
    return program_from_source(path.read_text(), name=path.stem)


class BindingRunner:
    """One browser, many bindings; success is GuiEnv.reward() >= 1.0.

    ``run(program, binding, judge_event=None)``: the program is executed with
    ``binding`` (e.g. what an extraction model produced at deployment time),
    while the oracle judges ``judge_event`` (the ground truth, defaults to
    ``binding``) -- the same rule as an episode: a NEW matching event exists.
    """

    def __init__(self, base_url: str, layout: str, headless: bool = True):
        self.base_url = base_url.rstrip("/")
        self.layout = layout
        self.env = GuiEnv(self.base_url, layout=layout, headless=headless)
        # Context-level auto popup adoption: a program click that opens a new
        # page (target="_blank" etc.) makes that page the active one; the
        # program's `page` handle follows via _ActivePageProxy, and the opener
        # is closed exactly as the reactive env does.
        self._active: dict = {"page": self.env.page}

        def _adopt(new_page):
            # Swap the active page; closing the opener HERE would abort the
            # in-flight click() that opened the popup ("Target page ... has
            # been closed"), so openers are reaped after the program returns.
            self._active["page"] = new_page

        self._on_page = _adopt
        self.env.page.context.on("page", self._on_page)

    def _reap_extra_pages(self) -> None:
        """Keep exactly one page: the active one (openers of adopted popups)."""
        active = self._active["page"]
        for page in list(active.context.pages):
            if page != active and not page.is_closed():
                try:
                    page.close()
                except Exception:
                    pass

    def run(self, program: Program, binding: dict, judge_event: dict | None = None) -> dict:
        judge_event = dict(judge_event or binding)
        # The judge is the environment's own: task set to the six target
        # fields, baseline snapshot, then reward() after the program ran.
        self.env.task = Task(
            layout=self.layout,
            condition="compile",  # not one of the reactive conditions
            seed=0,
            event=judge_event,
            start_url=self.base_url,
        )
        self.env._matches_at_reset = self.env._matching_event_count()
        error: str | None = None
        try:
            # Recover from a previous binding whose popup left a closed page.
            self.env._active_page_check()
            self._active["page"] = self.env.page
            self.env.page.goto(self.base_url)  # neutral start, as reset() would
            self.env.page.wait_for_load_state("domcontentloaded")
            program(_ActivePageProxy(self._active), dict(binding), self.base_url)
            active = self._active["page"]
            active.wait_for_load_state("domcontentloaded")
            active.wait_for_timeout(300)  # env.step's settle convention
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        finally:
            # Keep the env pointing at whatever page the program ended on,
            # with exactly one page left for the next binding.
            self._reap_extra_pages()
            self.env.page = self._active["page"]
            self.env._active_page_check()
            self._active["page"] = self.env.page
        return {"passed": self.env.reward() >= 1.0, "error": error}

    def close(self) -> None:
        try:
            self.env.page.context.remove_listener("page", self._on_page)
        except Exception:
            pass
        self.env.close()

    def __enter__(self) -> "BindingRunner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
