"""Minimal action space: an action string in, a Playwright effect out.

Grammar (exactly one action per reply, as described to the agent):

    click(bid)             click the element with this bid
    fill(bid, "text")      replace a text field's value
    select(bid, "value")   choose an option of a select/combobox
    scroll(up|down)        scroll the page
    goto("url")            navigate (absolute URL, or a path on the app)
    press("Enter")         press a key
    done()                 declare the task finished

``bid`` is the BrowserGym element id shown next to each interactive element
in the AX-tree observation, e.g. ``[42] textbox 'Title'`` means the Title
field's bid is 42. The action/observation contract is kept aligned with
BrowserGym: observations are marked with ``bid`` attributes and rendered as
``[bid] role 'name'`` lines, and actions locate elements by that same bid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

ACTION_NAMES = ("click", "fill", "select", "scroll", "goto", "press", "done")
SCROLL_DIRS = ("up", "down")

ACTION_SPACE_DESCRIPTION = """You act in a web application. Each turn you receive an observation of the current page: a screenshot (interactive elements carry [bid] number badges) and, when provided, an accessibility-tree text view where every interactive element is one line:

    [bid] role 'name'

Actions (exactly one per turn):

    click(<bid>)              click the element with this bid
    fill(<bid>, "<text>")     replace a text field's content (use this for text inputs -- do not click them and press keys)
    select(<bid>, "<option>") choose an option of a dropdown
    scroll(up) / scroll(down) scroll the page
    goto("<url>")             navigate to a URL
    press("<key>")            press a keyboard key (e.g. Enter)
    done()                    you believe the task is complete

Output format (strict): reply with exactly ONE line, either `action: <action>` or the bare action string, e.g. `action: click(12)` or `click(12)`. No prose, explanation, or markdown before or after it. A malformed reply is rejected and you will be asked again.

General rules (the same for every task): navigate by clicking visible controls; do not guess URL paths -- use goto only with a URL that appeared in an observation. Use bids only from the most recent observation; stale bids fail. Malformed actions or execution errors are reported back to you as errors."""


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    name: str
    args: tuple[str, ...]

    def render(self) -> str:
        if not self.args:
            return f"{self.name}()"
        return f"{self.name}({', '.join(repr(a) for a in self.args)})"


def _split_args(raw: str) -> list[str]:
    """Split on top-level commas, respecting quotes."""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in raw:
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _build(name: str, raw_args: str) -> Action:
    args = tuple(_unquote(p) for p in _split_args(raw_args.strip()))
    expected = {"click": 1, "fill": 2, "select": 2, "scroll": 1, "goto": 1, "press": 1, "done": 0}
    if len(args) != expected[name]:
        raise ActionError(f"{name}(...) takes {expected[name]} argument(s), got {len(args)}")
    if name == "scroll" and args[0] not in SCROLL_DIRS:
        raise ActionError(f"scroll direction must be one of {SCROLL_DIRS}, got {args[0]!r}")
    if name in ("click", "fill", "select") and not args[0]:
        raise ActionError(f"{name}(...) needs a bid as its first argument")
    return Action(name, args)


# First occurrence of any action name followed by an argument list.
_FIRST_ACTION_RE = re.compile(r"\b(" + "|".join(ACTION_NAMES) + r")\s*\(")


def parse_action(text: str) -> Action:
    """Parse a strict single-action string like ``fill("42", "Dennis")``."""
    if not text or not text.strip():
        raise ActionError("empty action")
    match = re.fullmatch(r"\s*([a-zA-Z_]\w*)\s*\((.*)\)\s*", text, re.S)
    if not match:
        raise ActionError(f"not a single action call: {text!r}")
    name = match.group(1)
    if name not in ACTION_NAMES:
        raise ActionError(f"unknown action {name!r}; expected one of {ACTION_NAMES}")
    return _build(name, match.group(2))


def parse_first_action(text: str) -> Action:
    """Extract the first well-formed action from a (possibly chatty) reply."""
    for match in _FIRST_ACTION_RE.finditer(text or ""):
        start = match.end()  # just past the '('
        quote: str | None = None
        i = start
        end = None
        while i < len(text):
            ch = text[i]
            if quote is not None:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == ")":
                end = i
                break
            i += 1
        if end is None:
            continue  # unterminated; try the next candidate
        try:
            return _build(match.group(1), text[start:end])
        except ActionError:
            continue
    raise ActionError(f"no valid action found in: {text!r}")


def execute(page, action: Action, base_url: str | None = None) -> None:
    """Run one action on a Playwright page. ``done()`` is a no-op here.

    Elements are located by their bid via BrowserGym's test-id machinery
    (GuiEnv sets the test-id attribute to "bid"). Playwright's own action
    waiting scrolls elements into view, so no explicit scroll is attempted.
    """
    from browsergym.core.action.utils import get_elem_by_bid

    name, args = action.name, action.args
    if name == "done":
        return
    if name == "click":
        get_elem_by_bid(page, args[0]).click()
    elif name == "fill":
        get_elem_by_bid(page, args[0]).fill(args[1])
    elif name == "select":
        get_elem_by_bid(page, args[0]).select_option(args[1])
    elif name == "scroll":
        page.mouse.wheel(0, 600 if args[0] == "down" else -600)
    elif name == "goto":
        url = args[0]
        if not url.startswith(("http://", "https://")):
            url = urljoin((base_url or "").rstrip("/") + "/", url.lstrip("/"))
        page.goto(url)
    elif name == "press":
        page.keyboard.press(args[0])
    else:  # pragma: no cover - guarded by parse
        raise ActionError(f"unknown action {name!r}")
