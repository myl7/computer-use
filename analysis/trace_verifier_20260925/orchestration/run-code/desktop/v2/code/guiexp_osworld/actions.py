"""The OSWorld action space: element-index JSON actions over the desktop.

Same reply contract as the web and Android arms: exactly ONE action per reply,
``action: <json>`` or a bare JSON object. The element index ("bid" analogue)
is the element's position in the numbered a11y list of the CURRENT
observation; the harness resolves it to the element's bbox centre and clicks
through pyautogui inside the guest (OSWorld's own actuation path).

Programs never click coordinates: the location contract on this arm is
a11y text/name/role/index (the compile path's ``device.find``), matching the
Android arm's index-based M3A contract.
"""

from __future__ import annotations

import json
import re

ACTION_TYPES = (
    "click",
    "long_press",
    "input_text",
    "press",
    "hotkey",
    "scroll",
    "open_app",
    "wait",
    "status",
)

ACTION_SPACE_DESCRIPTION = """You act on a Linux desktop (LibreOffice apps). Each turn you receive an observation: an unmarked screenshot and a numbered list of the UI elements on the current screen, one element per line like:

    Element 12: {"index": 12, "role": "push-button", "name": "Save", "clickable": true, ...}

Actions (exactly one per turn), given as ONE JSON object:

    {"action_type": "click", "index": <i>}                        click element <i> (menu items, buttons, cells, fields)
    {"action_type": "long_press", "index": <i>}                   press-and-hold on element <i>
    {"action_type": "input_text", "text": "<s>", "index": <i>}    click field <i>, select its content, type <s>
    {"action_type": "press", "key": "<k>"}                        press one key (enter, tab, esc, delete, right, down, ...)
    {"action_type": "hotkey", "keys": ["ctrl", "s"]}              press a key combination
    {"action_type": "scroll", "direction": "<down|up|left|right>"}  scroll the screen
    {"action_type": "open_app", "app_name": "<name>"}             launch an app (e.g. "libreoffice calc")
    {"action_type": "wait"}                                       wait for the screen to update
    {"action_type": "status", "goal_status": "complete"}          you believe the task is complete
    {"action_type": "status", "goal_status": "infeasible"}        you cannot do the task

Notes: to type into a spreadsheet cell, click the cell (or use press/tab to reach it), then use input_text with the cell's index while it is focused -- input_text without an index types at the current caret. Typing into a cell starts editing it; press enter to commit and move down, tab to commit and move right. Dialogs (e.g. Save) are navigated with their own elements.

Output format (strict): reply with exactly ONE line, either `action: <json>` or the bare JSON object, e.g. `action: {"action_type": "click", "index": 12}`. No prose, explanation, or markdown before or after it. A malformed reply is rejected and you will be asked again.

General rules (the same for every task): act only through the desktop's GUI with the actions above; do not try to call the apps' internals. Use element indexes only from the most recent observation; stale indexes fail. If an element you need is not visible, scroll to reveal it first. Malformed actions or execution errors are reported back to you as errors."""

_SYSTEM_PREFIX = "You are a GUI agent operating a Linux desktop.\n\n"


def system_prompt() -> str:
    """The system message (action space + reply format), the shared shape."""
    return _SYSTEM_PREFIX + ACTION_SPACE_DESCRIPTION


class ActionError(ValueError):
    """A reply that does not contain exactly one valid action."""


_ALLOWED_TYPES = set(ACTION_TYPES)
_INT_FIELDS = {"index"}


def parse_action(reply: str) -> dict:
    """Extract the first valid action object from a (possibly chatty) reply.

    Returns a plain dict (the harness's own action space, the same object
    guest_env.execute_action and program_runtime consume); raises ActionError
    when no JSON object in the reply converts to one.
    """
    text = reply or ""
    for candidate in re.finditer(r"\{.*?\}", text, re.S):
        snippet = candidate.group(0)
        try:
            obj = json.loads(snippet)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("action_type") not in _ALLOWED_TYPES:
            continue
        action = dict(obj)
        for field in _INT_FIELDS:
            if field in action and not isinstance(action[field], int):
                try:
                    action[field] = int(action[field])
                except (TypeError, ValueError):
                    action = None
                    break
        if action is not None:
            return action
    raise ActionError(f"no valid JSON action found in: {text!r}")


def render(action: dict) -> str:
    """Canonical action string for the trajectory ('action' field)."""
    return json.dumps(action, sort_keys=True, separators=(",", ":"))


def is_done(action: dict) -> bool:
    return action.get("action_type") == "status"
