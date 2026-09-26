"""The WebArena action space: element-id based DOM actions.

Mirrors guiexp_android/actions.py in message contract: exactly ONE action
per reply, either ``action: <json>`` or the bare JSON object. The "index"
the actions address is the position in the numbered element list of the
current observation -- the same number the DOM-tree text carries:

    element 12: <button> "Submit" [button, clickable]

Actions are executed by env.py against Playwright handles, so the ids stay
valid only within one observation (stale ids fail, same as Android).

Action set (BrowserGym-style, kept small and deterministic):

    {"action_type": "click", "index": <i>}
    {"action_type": "input_text", "text": "<s>", "index": <i>}
    {"action_type": "keyboard_enter"}
    {"action_type": "scroll", "direction": "<down|up>", "target": <i>}
    {"action_type": "navigate_back"}
    {"action_type": "navigate_forward"}
    {"action_type": "goto", "url": "<u>"}
    {"action_type": "wait"}
    {"action_type": "status", "goal_status": "complete"|"infeasible"}

input_text fills the field at index <i> with <s> (clears it first, no Enter
-- web forms submit on their own button). The program side
(program_runtime.WebDevice) exposes the same actions with semantic find.
"""

from __future__ import annotations

import json
import re

ACTION_TYPES = (
    "click",
    "input_text",
    "keyboard_enter",
    "scroll",
    "navigate_back",
    "navigate_forward",
    "goto",
    "wait",
    "status",
)

ACTION_SPACE_DESCRIPTION = """You act on a website in a desktop browser. Each turn you receive an observation of the current page: a screenshot and a numbered list of the page's visible DOM elements, one line per element, e.g.:

    element 12: <button> "Submit" [button, clickable]
    element 13: <input> "Search" [input, editable, text]
    element 14: <a> "f/books" [link]

The numbers are element ids. The same numbers appear as small numbered tags on the screenshot.

Actions (exactly one per turn), given as ONE JSON object:

    {"action_type": "click", "index": <i>}                  click element <i>
    {"action_type": "input_text", "text": "<s>", "index": <i>}  clear field <i> and type <s> into it (no Enter)
    {"action_type": "keyboard_enter"}                       press the Enter key
    {"action_type": "scroll", "direction": "<down|up>"}     scroll the page
    {"action_type": "navigate_back"}                        browser back
    {"action_type": "navigate_forward"}                     browser forward
    {"action_type": "goto", "url": "<url>"}                  navigate to an absolute URL
    {"action_type": "wait"}                                  wait for the page to update
    {"action_type": "status", "goal_status": "complete"}    you believe the task is complete
    {"action_type": "status", "goal_status": "infeasible"}  you cannot do the task

Output format (strict): reply with exactly ONE line, either `action: <json>` or the bare JSON object, e.g. `action: {"action_type": "click", "index": 12}`. No prose, explanation, or markdown before or after it. A malformed reply is rejected and you will be asked again.

General rules (the same for every task): act only through the website's own UI with the actions above; do not try to reach the site's files or databases by other means. Use element ids only from the most recent observation; stale ids fail. If an element you need is not visible, scroll to reveal it first. You are already logged in where the task needs it. Malformed actions or execution errors are reported back to you as errors."""

_SYSTEM_PREFIX = "You are a GUI agent operating a website in a browser.\n\n"


def system_prompt() -> str:
    """The system message (action space + reply format)."""
    return _SYSTEM_PREFIX + ACTION_SPACE_DESCRIPTION


class ActionError(ValueError):
    """A reply that does not contain exactly one valid action."""


_OBJECT_RE = re.compile(r"\{.*?\}", re.S)


def parse_action(reply: str) -> dict:
    """Extract the first valid action dict from a (possibly chatty) reply.

    Returns a plain dict (env executes it); raises ActionError when no JSON
    object in the reply carries a known action_type.
    """
    text = reply or ""
    for match in _OBJECT_RE.finditer(text):
        snippet = match.group(0)
        try:
            obj = json.loads(snippet)
        except Exception:  # noqa: BLE001 - try the next {...}
            continue
        if not isinstance(obj, dict) or obj.get("action_type") not in ACTION_TYPES:
            continue
        return _validate(obj)
    raise ActionError(f"no valid JSON action found in: {text!r}")


def _validate(obj: dict) -> dict:
    at = obj["action_type"]
    if at in ("click", "input_text"):
        if not isinstance(obj.get("index"), int):
            raise ActionError(f"{at} needs an integer 'index'")
    if at == "input_text" and not isinstance(obj.get("text"), str):
        raise ActionError("input_text needs a string 'text'")
    if at == "goto" and not isinstance(obj.get("url"), str):
        raise ActionError("goto needs a string 'url'")
    if at == "status" and obj.get("goal_status") not in ("complete", "infeasible"):
        raise ActionError("status needs goal_status complete|infeasible")
    if at == "scroll" and obj.get("direction") not in ("down", "up"):
        obj["direction"] = "down"
    return obj


def render(action: dict) -> str:
    """Canonical action string for the trajectory."""
    return json.dumps(action, ensure_ascii=False, sort_keys=False)


def is_done(action: dict) -> bool:
    return (action or {}).get("action_type") == "status"
