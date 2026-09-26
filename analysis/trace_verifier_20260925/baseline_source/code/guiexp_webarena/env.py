"""The WebArena browser env: Playwright chromium against the postmill forum.

One persistent browser context (logged-in Reddit account MarvelsGrantMan136),
one page per env. ``reset(task)`` wipes cookies of the session we cannot
reuse cleanly -- in practice the forum reset is: go to the forum home and,
for the CommentPost family, remove any comment the account itself left on
the target posts during a previous episode (the checker's own DB query is
the judge, so a stale comment from an earlier attempt would poison it).

Observation (both text and image, the "unmarked screenshot + numbered DOM
tree" contract):

    {
      "url": <current URL>,
      "screenshot_b64": <PNG of the viewport>,
      "ax_tree_text": "element 0: <html> ...\nelement 12: ...",
      "elements": [ {index, tag, text, attrs...}, ... ],   # same order
      "last_action_error": None,
    }

The element list is built from the visible, interactive-or-textual DOM
(subset: a/button/input/select/textarea/label/h1..h6/p/td/th/option and
anything with a role), numbered 0..n in document order; indexes are the ids
the action space and the program runtime share.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .family import FAMILY, task_for

REDDIT_URL = os.environ.get("REDDIT", "http://localhost:9999")
ACCOUNTS = {"reddit": {"username": "MarvelsGrantMan136", "password": "test1234"}}

# Selectors for the interactive/textual DOM subset (postmill is server-side
# rendered Thymeleaf + minimal JS, so this is stable).
_ELEMENT_SELECTOR = ",".join(
    [
        "a[href]",
        "button",
        "input:not([type=hidden])",
        "select",
        "textarea",
        "label",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "p",
        "td", "th",
        "option",
        "[role]",
        "li",
        "form",
        "img",
    ]
)

_MAX_ELEMENTS = 220  # keep one observation prompt-bounded


class WebArenaTask:
    """One task instance: family + condition + seed + params."""

    def __init__(self, family: str, condition: str, seed: int, params: dict):
        self.family = family
        self.condition = condition
        self.seed = seed
        self.params = params
        self.task_id = f"{family}__{condition}__s{seed}"

    @property
    def intent(self) -> str:
        from .family import goal_text

        return goal_text(self)


class WebArenaEnv:
    """One chromium instance; episodes reset state through the forum itself."""

    def __init__(
        self,
        headless: bool = True,
        reddit_url: str | None = None,
        viewport: dict | None = None,
        slow_mo: float = 0.0,
    ):
        from playwright.sync_api import sync_playwright

        self.reddit_url = (reddit_url or REDDIT_URL).rstrip("/")
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=headless, args=["--disable-dev-shm-usage"]
        )
        self.context = self.browser.new_context(
            viewport=viewport or {"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self.page = self.context.new_page()
        self._logged_in = False
        self._ensure_login()
        self.slow_mo = slow_mo

    # -- login ------------------------------------------------------------

    def _ensure_login(self) -> None:
        """Log the Reddit account in once (cookie persists in the context).

        Postmill's login form: #login-username / #login-password and the
        submit button (postmill redirects to /login?_cookie_check=... first
        and sets the session cookie, so we land there, fill, and submit).
        """
        try:
            self.page.goto(f"{self.reddit_url}/login", timeout=30000)
            self.page.wait_for_load_state("domcontentloaded")
            try:
                self.page.fill("#login-username", ACCOUNTS["reddit"]["username"], timeout=8000)
                self.page.fill("#login-password", ACCOUNTS["reddit"]["password"], timeout=8000)
                self.page.click("button[type='submit']", timeout=8000)
                self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception as exc:  # noqa: BLE001
                print(f"[env] login form interaction failed: {exc}")
            self._logged_in = self._is_logged_in()
            if not self._logged_in:
                print("[env] login not confirmed; proceeding (some tasks need no login)")
        except Exception as exc:  # noqa: BLE001
            print(f"[env] login goto failed: {exc}; proceeding")

    def _is_logged_in(self) -> bool:
        try:
            self.page.goto(f"{self.reddit_url}/", timeout=30000)
            self.page.wait_for_load_state("domcontentloaded")
            content = self.page.content()
            return ACCOUNTS["reddit"]["username"].lower() in content.lower() or "log out" in content.lower()
        except Exception:  # noqa: BLE001
            return False

    # -- observation --------------------------------------------------------

    def observe(self) -> dict:
        """Screenshot + numbered DOM tree of the current page."""
        screenshot_b64 = None
        try:
            raw = self.page.screenshot(type="png", timeout=15000)
            if raw:
                import base64

                screenshot_b64 = base64.b64encode(raw).decode("ascii")
        except Exception:  # noqa: BLE001 - observation must never die
            pass
        elements = self._elements()
        ax_lines = []
        for element in elements:
            bits = [f"<{element['tag']}>"]
            if element["text"]:
                text = element["text"].replace("\n", " ")[:120]
                bits.append(f'"{text}"')
            roles = []
            for key in ("link", "button", "input", "editable", "image", "dropdown"):
                if element.get(key):
                    roles.append(key)
            if roles:
                bits.append("[" + ", ".join(roles) + "]")
            ax_lines.append(f"element {element['index']}: {' '.join(bits)}")
        return {
            "url": self.page.url,
            "screenshot_b64": screenshot_b64,
            "ax_tree_text": "\n".join(ax_lines),
            "elements": elements,
            "last_action_error": None,
        }

    def _elements(self) -> list[dict]:
        """The numbered DOM element list, in document order.

        One atomic in-page pass (all visible matches of the selector, in
        document order, capped at _MAX_ELEMENTS collected) -- much faster
        than per-handle roundtrips and immune to mid-extraction DOM change.
        Indexes are dense over the returned (visible) list.
        """
        try:
            raw = self.page.evaluate(
                """([sel, maxOut]) => {
                    const all = Array.from(document.querySelectorAll(sel));
                    const out = [];
                    const style = (e) => window.getComputedStyle(e);
                    for (const e of all) {
                        if (out.length >= maxOut) break;
                        const r = e.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) continue;
                        const s = style(e);
                        if (s.visibility === 'hidden' || s.display === 'none') continue;
                        const tag = e.tagName.toLowerCase();
                        let text = (e.innerText || '').trim();
                        if (text) text = text.replace(/\\s+/g, ' ');
                        let value = '';
                        if (e.value !== undefined && ['INPUT','TEXTAREA','SELECT'].includes(e.tagName)) {
                            value = String(e.value).slice(0, 80);
                        }
                        out.push({
                            tag: tag,
                            text: text.slice(0, 200),
                            role: e.getAttribute('role') || '',
                            placeholder: e.getAttribute('placeholder') || '',
                            aria: e.getAttribute('aria-label') || '',
                            name: e.getAttribute('name') || '',
                            id: e.id || '',
                            href: (e.getAttribute('href') || '').slice(0, 80),
                            value: value,
                            type: e.getAttribute('type') || '',
                        });
                    }
                    return out;
                }""",
                [_ELEMENT_SELECTOR, _MAX_ELEMENTS],
            )
        except Exception:  # noqa: BLE001 - observation must never die
            return []
        out = []
        for record in raw or []:
            record["index"] = len(out)
            record["link"] = record["tag"] == "a"
            record["button"] = record["tag"] == "button"
            record["input"] = record["tag"] in ("input", "textarea", "select")
            record["editable"] = record["tag"] in ("input", "textarea")
            record["image"] = record["tag"] == "img"
            record["dropdown"] = record["tag"] == "select"
            out.append(record)
        return out

    # -- acting -------------------------------------------------------------

    def step(self, action: dict) -> tuple[dict, bool, float]:
        """Execute one action; returns (obs, done, reward)."""
        obs_error = None
        done = False
        reward = 0.0
        try:
            action = dict(action)
            at = action.get("action_type")
            if at == "click":
                loc = self._visible_locator(action["index"])
                if loc is None:
                    raise ValueError(f"no element with id {action['index']} on this screen")
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.click(timeout=10000)
            elif at == "input_text":
                loc = self._visible_locator(action["index"])
                if loc is None:
                    raise ValueError(f"no element with id {action['index']} on this screen")
                loc.scroll_into_view_if_needed(timeout=5000)
                loc.fill(action["text"], timeout=10000)
            elif at == "keyboard_enter":
                self.page.keyboard.press("Enter")
            elif at == "scroll":
                if action.get("direction", "down") == "up":
                    self.page.mouse.wheel(0, -1200)
                else:
                    self.page.mouse.wheel(0, 1200)
            elif at == "navigate_back":
                self.page.go_back(timeout=30000)
            elif at == "navigate_forward":
                self.page.go_forward(timeout=30000)
            elif at == "goto":
                url = action["url"]
                if url.startswith("/"):
                    url = self.reddit_url + url
                self.page.goto(url, timeout=30000)
            elif at == "wait":
                self.page.wait_for_timeout(1500)
            elif at == "status":
                done = True
                reward = 0.0  # the checker decides; runner sets success
            else:
                raise ValueError(f"unknown action_type {at!r}")
            if not done:
                self.page.wait_for_timeout(400)
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            obs_error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        obs = self.observe()
        if obs_error:
            obs["last_action_error"] = obs_error
        return obs, done, reward

    def _visible_locator(self, index):
        """The Locator for dense visible-element id ``index``.

        Uses Playwright's own visibility filter over the same selector, in
        document order -- the exact set _elements() numbers, so id N here is
        element N there.
        """
        if not isinstance(index, int) or index < 0:
            return None
        try:
            loc = self.page.locator(f"{_ELEMENT_SELECTOR} >> visible=true")
            if index >= loc.count():
                return None
            return loc.nth(index)
        except Exception:  # noqa: BLE001
            return None

    # -- episode boundary ---------------------------------------------------

    def reset(self, task: WebArenaTask) -> dict:
        """Start one episode: clean the account's own writes, land on start."""
        from .family import reset_for_episode

        reset_for_episode(self, task)
        obs = self.observe()
        return obs

    def reward(self, task: WebArenaTask) -> float:
        """The family checker over the task's own params: 1.0 iff the
        parameterized effect exists in the forum with matching values."""
        from .family import check

        return 1.0 if check(self, task) else 0.0

    # -- admin DB access (checker + reset; NOT the agent) --------------------

    def db_rows(self, sql: str) -> list[list[str]]:
        """Run a SQL query in the postmill container's PostgreSQL through
        docker exec (su postgres -> psql, | separator). Only the family/
        checker uses this, never the agent (the rules forbid it)."""
        import os
        import subprocess

        inner = sql.replace("'", "'\"'\"'")
        cmd = [
            "docker", "exec", "guiexp-forum", "su", "postgres", "-c",
            "psql postmill -A -t -F'|' -c '" + inner + "'",
        ]
        env = dict(os.environ)
        env["DOCKER_HOST"] = "unix:///run/guiexp-docker.sock"
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
        if out.returncode != 0 or "ERROR" in (out.stderr or ""):
            raise RuntimeError(f"db query failed: {(out.stderr or '')[:300]}")
        rows = []
        for line in out.stdout.splitlines():
            if line.strip() and not line.startswith("("):
                rows.append(line.split("|"))
        return rows

    def db_rows_typed(self, sql: str, columns: list[str]) -> list[dict]:
        """Same channel, columns mapped to the given names."""
        return [dict(zip(columns, row)) for row in self.db_rows(sql)]

    def close(self) -> None:
        try:
            self.context.close()
            self.browser.close()
        finally:
            self._pw.stop()
