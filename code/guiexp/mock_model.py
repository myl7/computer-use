"""A deterministic scripted model for tests: a fake `openai` client.

Zero network. ``MockOpenAI`` quacks like the OpenAI client the agent uses
(``client.chat.completions.create``), and every reply comes from a tiny
rule-based policy that actually solves the calendar task like a GUI agent
would: it reads the AX-tree from the last user message to pick bids, reads
the goal text for the field values, clicks through the wizard/disclosure
controls, and answers done() when the form is behind it. The point is to
exercise the whole harness -- server, browser, bids, actions, oracle,
trajectory writing -- without any LLM API call.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

# Form-field key -> keyword its AX accessible name contains. The app's
# content config sets aria-labels ("Event title", "Event date, YYYY-MM-DD",
# "Event URL", ...), so keyword matching is robust to label wording.
FIELD_KEYWORDS = {
    "title": "title",
    "date": "date",
    "description": "description",
    "location": "location",
    "url": "url",
    "invitees": "invitees",
}
FIELD_ORDER = ("title", "date", "description", "location", "url", "invitees")
DETAIL_KEYWORDS = ("description", "location", "url", "invitees")

_LINE_RE = re.compile(r"\[([A-Za-z0-9]+)\]\s+([a-zA-Z][a-zA-Z ]*?)\s+'([^']*)'")
_URL_RE = re.compile(r"Current (?:page )?URL: (\S+)")
_FILL_RE = re.compile(r"fill\(\s*['\"]?([A-Za-z0-9]+)['\"]?\s*,\s*['\"](.*)['\"]\s*\)")
_GOAL_URL_RE = re.compile(r"operating the web application at (http://\S+)")
_GOAL_FIELD_RE = re.compile(r"^\s{2}(\w+): (.+)$", re.M)


def _text_parts(message) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _last_user_text(messages) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _text_parts(message)
    return ""


def _first_user_text(messages) -> str:
    for message in messages:
        if message.get("role") == "user":
            return _text_parts(message)
    return ""


def _goal(messages) -> tuple[str | None, dict]:
    """(base_url, {field: value}) from the first user message (goal text)."""
    text = _first_user_text(messages)
    base = _GOAL_URL_RE.search(text)
    values = {m.group(1): m.group(2) for m in _GOAL_FIELD_RE.finditer(text)}
    # \S+ can swallow the sentence's trailing period; strip it.
    return (base.group(1).rstrip("./") if base else None), values


def _filled_fields(messages, values: dict) -> set[str]:
    """Which fields the agent has already filled, from its own past replies."""
    value_to_field = {v: k for k, v in values.items()}
    filled = set()
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for match in _FILL_RE.finditer(_text_parts(message)):
            field = value_to_field.get(match.group(2))
            if field:
                filled.add(field)
    return filled


def _elements(ax_text: str) -> list[tuple[str, str, str]]:
    out = []
    for line in ax_text.splitlines():
        match = _LINE_RE.search(line)
        if match:
            out.append((match.group(1), match.group(2).lower(), match.group(3)))
    return out


def _trailing_scrolls(messages) -> int:
    """How many consecutive scroll(down) replies end the history so far.

    User (observation) messages are interleaved between assistant turns, so
    they are skipped rather than treated as the end of the run.
    """
    count = 0
    for message in reversed(messages):
        if message.get("role") == "user":
            continue
        if message.get("role") != "assistant":
            break
        if _text_parts(message).strip() == "scroll(down)":
            count += 1
        else:
            break
    return count


def policy_reply(messages) -> str:
    """One deterministic action, chosen from the conversation so far."""
    last_text = _last_user_text(messages)
    url_match = _URL_RE.search(last_text)
    ax_start = last_text.find("[")
    ax_text = last_text[ax_start:] if ax_start >= 0 else ""
    base_url, values = _goal(messages)

    url = url_match.group(1) if url_match else ""
    if not base_url:
        return "done()"
    if "/calendar/create_event" not in url:
        # Start page -> go to the form; calendar view -> we are done (the
        # env auto-terminates on reward, this is just belt and braces).
        if "/calendar" in url:
            return "done()"
        return f'goto("{base_url}/calendar/create_event")'
    if not ax_text:
        return "done()"

    elements = _elements(ax_text)
    filled = _filled_fields(messages, values)

    # 1) fill the next field that is visible on this screen and still empty
    for field in FIELD_ORDER:
        if field in filled or field not in values:
            continue
        keyword = FIELD_KEYWORDS[field]
        for bid, role, name in elements:
            # NB: match "textbox" exactly-ish; "StaticText" labels also
            # contain the word "text" and are NOT fillable.
            if keyword in name.lower() and "textbox" in role:
                return f'fill("{bid}", "{values[field]}")'

    # 2) no visible field left: click through (reveal, continue, submit).
    #    The "More details" reveal stays in the DOM after it has fired, so
    #    only click it while no optional field is on screen yet.
    def find_button(label: str) -> str | None:
        for bid, role, name in elements:
            if "button" in role and name.strip().lower() == label.lower():
                return bid
        return None

    details_visible = any(
        any(kw in name.lower() for kw in DETAIL_KEYWORDS)
        for _, role, name in elements
        if "textbox" in role
    )
    if not details_visible:
        bid = find_button("More details")
        if bid:
            return f'click("{bid}")'
    for label in ("Create", "Submit", "Next"):
        bid = find_button(label)
        if bid:
            return f'click("{bid}")'

    # 3) what we need may simply be below the fold (the AX tree only shows
    #    visible elements); scroll a bounded number of times, then give up.
    if _trailing_scrolls(messages) < 3:
        return "scroll(down)"
    return "done()"


def _approx_prompt_tokens(messages) -> int:
    total = 50  # system/action-space overhead
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += len(part.get("text", "")) // 4
                    else:  # image parts: flat placeholder, keeps usage deterministic
                        total += 1000
    return total


class _Completions:
    def create(self, *, model=None, messages=None, **_kwargs):
        reply = policy_reply(messages or [])
        prompt_tokens = _approx_prompt_tokens(messages or [])
        completion_tokens = max(1, len(reply) // 4)
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(prompt_tokens * 1e-6 + completion_tokens * 2e-6, 8),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
            model=model or "mock",
        )


class MockOpenAI:
    """Drop-in stand-in for the OpenAI client; deterministic, offline."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_Completions())
