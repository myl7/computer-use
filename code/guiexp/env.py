"""The environment: a Playwright Chromium page against the app server.

BrowserGym supplies the observation machinery (bid marking, merged AX tree
flattened to ``[bid] role 'name'`` lines, screenshot); the reset/step loop,
the oracle and the task registry are ours. This is deliberately a plain
Playwright loop rather than a ``gym.make`` wrapper: browsergym-core's pinned
playwright cannot be installed in the experiment venv, and the action /
observation contract below is identical to BrowserGym's (same bids, same AX
format, same per-step reward).

Oracle: an episode is successful iff the app's own state endpoint
``/calendar_all`` contains an event whose six calendar fields all equal the
task instance's -- the same check openapps-exp's discovery_cost.py makes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from dataclasses import dataclass
from urllib.request import urlopen

import numpy as np
import PIL.Image
from playwright.sync_api import sync_playwright

from browsergym.core.observation import (
    _post_extract,
    _pre_extract,
    extract_dom_extra_properties,
    extract_dom_snapshot,
    extract_merged_axtree,
)
from browsergym.utils.obs import flatten_axtree_to_str, overlay_som

from .actions import ActionError, execute, parse_first_action
from .conditions import goal_text

LAYOUTS = ("single_page", "sectioned", "wizard")
CONDITIONS = ("discover", "told", "mid", "skill", "floor")
FIELDS = ("title", "date", "description", "location", "url", "invitees")

# The instance pool. Instance 0 is the EVENT used by openapps-exp's protocol
# ladder (protocols.py), so old and new numbers stay comparable. Instances
# are chosen by a hash of the seed, so a seed maps to the same instance on
# every machine and across conditions (discover vs told see the same task).
INSTANCE_POOL = (
    {"title": "Dennis-Bob", "date": "2026-04-01", "description": "Quarterly sync",
     "location": "Room 3", "url": "https://example.com/sync", "invitees": "Dennis"},
    {"title": "Alice-Carol", "date": "2026-05-12", "description": "Design review",
     "location": "Room 7", "url": "https://example.com/design", "invitees": "Alice"},
    {"title": "Mallory-Eve", "date": "2026-06-20", "description": "Security audit",
     "location": "Bldg 2", "url": "https://example.com/audit", "invitees": "Mallory"},
    {"title": "Frank-Grace", "date": "2026-07-04", "description": "Onboarding session",
     "location": "Room 1", "url": "https://example.com/onboard", "invitees": "Grace"},
)


def instance_for_seed(seed: int) -> dict:
    """Seed-deterministic instance binding (sha256, not random.Random, so it
    can never drift with a Python upgrade)."""
    digest = hashlib.sha256(f"guiexp:{seed}".encode()).digest()
    return dict(INSTANCE_POOL[int.from_bytes(digest[:8], "big") % len(INSTANCE_POOL)])


@dataclass(frozen=True)
class Task:
    """One cell of the experiment: layout x condition x instance."""

    layout: str
    condition: str
    seed: int
    event: dict
    start_url: str

    @property
    def task_id(self) -> str:
        return f"{self.layout}__{self.condition}__s{self.seed}"


def get_task(layout: str, condition: str, seed: int, base_url: str) -> Task:
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}")
    return Task(
        layout=layout,
        condition=condition,
        seed=seed,
        event=instance_for_seed(seed),
        start_url=base_url.rstrip("/"),
    )


def _matches(stored: dict, target: dict) -> bool:
    return all((stored.get(f) or "") == target[f] for f in FIELDS)


class GuiEnv:
    """reset(task) -> observation; step(action) -> (obs, done, reward)."""

    # 1024x640 (the pre-1080p value; 1080p quadrupled per-step tokens and the
    # visibility concern turned out to be a misdiagnosis -- the real blocker
    # was the inert target=_blank click, fixed by popup adoption below).
    VIEWPORT = {"width": 1024, "height": 640}
    # How long a click on a target=_blank element waits for its popup.
    POPUP_TIMEOUT_MS = 1500

    def __init__(
        self,
        base_url: str,
        layout: str | None = None,
        headless: bool = True,
        annotate_som: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.layout = layout
        self.annotate_som = annotate_som  # screenshot-only mode: badge bids on the image
        self.task: Task | None = None
        self._matches_at_reset = 0
        self._pw = sync_playwright().start()
        # BrowserGym marks elements with a "bid" attribute; make Playwright's
        # test-id machinery look at that attribute so actions resolve bids.
        self._pw.selectors.set_test_id_attribute("bid")
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context(viewport=self.VIEWPORT)
        self.page = self._context.new_page()
        self.page.set_default_timeout(10_000)

    # -- lifecycle ---------------------------------------------------------

    def reset(self, task: Task) -> dict:
        self.task = task
        # Snapshot how many matching events already exist (a reused server
        # carries earlier episodes' state); success means ADDING one more.
        self._matches_at_reset = self._matching_event_count()
        self.page.goto(task.start_url)
        return self._observe()

    def close(self) -> None:
        for closer in (self._context.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:
                pass

    # -- observation -------------------------------------------------------

    def _observe(self) -> dict:
        self._active_page_check()
        _pre_extract(self.page)
        try:
            dom = extract_dom_snapshot(self.page)
            axtree = extract_merged_axtree(self.page)
            # Visible/clickable flags come from DOM properties, as in BrowserGym.
            extra = extract_dom_extra_properties(
                dom, scale_factor=getattr(self.page, "_bgym_scale_factor", 1.0)
            )
            ax_tree_text = flatten_axtree_to_str(
                axtree,
                extra_properties=extra,
                with_visible=True,
                with_clickable=True,
                filter_visible_only=True,
                filter_with_bid_only=True,
            )
            screenshot_b64 = base64.b64encode(self.page.screenshot()).decode("ascii")
            som_screenshot_b64 = None
            if self.annotate_som:
                # Set-of-Marks: draw dashed boxes + [bid] badges on the raw
                # screenshot so a screen-only model can reference elements.
                raw = np.array(PIL.Image.open(io.BytesIO(base64.b64decode(screenshot_b64))))
                som_arr = overlay_som(raw, extra)
                buf = io.BytesIO()
                PIL.Image.fromarray(som_arr).save(buf, format="PNG")
                som_screenshot_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        finally:
            _post_extract(self.page)
        obs = {
            "screenshot_b64": screenshot_b64,
            "som_screenshot_b64": som_screenshot_b64,
            "ax_tree_text": ax_tree_text,
            "url": self.page.url,
            "goal_text": "" if self.task is None or self.task.condition == "floor"
                         else goal_text(self.task.event),
        }
        return obs

    # -- acting and reward -------------------------------------------------

    def _execute_action(self, action) -> None:
        """Execute one parsed action on the active page.

        A click on a ``target="_blank"`` element opens the target in a NEW
        page; Playwright's plain click silently swallows that navigation.
        Standard BrowserGym semantics: adopt the popup as the single active
        page (all env state -- AX extraction, screenshots, URL -- moves to
        it) and close the opener. Clicks without a popup behave as before.
        """
        from browsergym.core.action.utils import get_elem_by_bid

        opens_popup = False
        if action.name == "click":
            try:
                opens_popup = (
                    get_elem_by_bid(self.page, action.args[0]).get_attribute("target")
                    == "_blank"
                )
            except Exception:
                opens_popup = False
        if not opens_popup:
            execute(self.page, action, base_url=self.base_url)
            return

        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            with self.page.context.expect_page(timeout=self.POPUP_TIMEOUT_MS) as popup_info:
                execute(self.page, action, base_url=self.base_url)
            popup = popup_info.value
        except PlaywrightTimeout:
            return  # no popup fired; the click already ran
        opener = self.page
        self.page = popup
        try:
            popup.wait_for_load_state("domcontentloaded")
        except Exception:
            pass
        try:
            opener.close()
        except Exception:
            pass

    def _active_page_check(self) -> None:
        """If the active page was closed, fall back to another open page (or
        open a fresh one) so observations and actions always have a page."""
        if not self.page.is_closed():
            return
        pages = [p for p in self.page.context.pages if not p.is_closed()]
        self.page = pages[-1] if pages else self.page.context.new_page()
        try:
            self.page.set_default_timeout(10_000)
        except Exception:
            pass

    def step(self, action_text: str) -> tuple[dict, bool, float]:
        """Execute one action string; return (obs, done, reward)."""
        if self.task is None:
            raise RuntimeError("call reset(task) before step()")
        error: str | None = None
        action = None
        try:
            action = parse_first_action(action_text)
        except ActionError as exc:
            error = f"{type(exc).__name__}: {exc}"

        done = False
        reward = self.reward()
        if action is not None:
            try:
                self._execute_action(action)
                self.page.wait_for_load_state("domcontentloaded")
                time.sleep(0.3)  # let htmx swaps / redirects settle
                reward = self.reward()
            except Exception as exc:  # noqa: BLE001 - the failure shape is data
                error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
        if action is not None and action.name == "done":
            done = True
        elif reward >= 1.0:
            done = True  # same auto-terminate rule as OpenAppsTask.validate

        obs = self._observe()
        obs["last_action"] = action_text
        obs["last_action_error"] = error
        return obs, done, reward

    def reward(self) -> float:
        """1.0 iff the app state holds MORE matching events than at reset."""
        if self.task is None:
            return 0.0
        return 1.0 if self._matching_event_count() > self._matches_at_reset else 0.0

    def _matching_event_count(self) -> int:
        if self.task is None:
            return 0
        return sum(1 for e in self.calendar_events() if _matches(e, self.task.event))

    def calendar_events(self) -> list[dict]:
        try:
            with urlopen(self.base_url + "/calendar_all", timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return []
