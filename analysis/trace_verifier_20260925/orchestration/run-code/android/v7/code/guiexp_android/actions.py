"""The Android action space: android_world's JSON actions in the guiexp contract.

The action space is the one android_world's M3A agent uses
(``android_world.env.json_action.JSONAction``): element-index based, where
the index ("bid") is the position of the element in the a11y ``ui_elements``
list -- the same number that is drawn on the SoM-annotated screenshot and
printed in the numbered element list. This mirrors the web side, where a
BrowserGym ``bid`` both marks the observation and addresses the action.

Reply contract is the web-side one (actions.py over there): exactly ONE
action per reply, either ``action: <action>`` or the bare action string --
here the action string is a single JSON object, e.g.::

    action: {"action_type": "click", "index": 12}
    {"action_type": "input_text", "text": "hello", "index": 3}

Parsing reuses android_world's own ``agent_utils.extract_json`` so anything
M3A accepts, we accept.
"""

from __future__ import annotations

import re

# Action types exposed to the model (subset of json_action._ACTION_TYPES that
# M3A's prompt describes; the remaining ones (swipe/double_tap/unknown) are
# still parseable, they are just not advertised).
ACTION_TYPES = (
    "click",
    "long_press",
    "input_text",
    "keyboard_enter",
    "navigate_home",
    "navigate_back",
    "open_app",
    "scroll",
    "wait",
    "answer",
    "status",
)

ACTION_SPACE_DESCRIPTION = """You act on an Android phone. Each turn you receive an observation of the current screen: a screenshot (interactive elements carry numbered marks) and, when provided, a numbered list of UI elements where every element is one line like:

    UI element 12: {"index": 12, "text": "...", "is_clickable": true, ...}

The numbers on the screenshot marks and in the list are the same element indexes.

Actions (exactly one per turn), given as ONE JSON object:

    {"action_type": "click", "index": <i>}                     tap element <i>
    {"action_type": "long_press", "index": <i>}                long-press element <i>
    {"action_type": "input_text", "text": "<s>", "index": <i>} tap field <i>, replace its text with <s>, press Enter
    {"action_type": "keyboard_enter"}                          press the Enter key
    {"action_type": "scroll", "direction": "<down|up|left|right>"}  scroll the screen
    {"action_type": "navigate_home"}                           go to the home screen
    {"action_type": "navigate_back"}                           go back
    {"action_type": "open_app", "app_name": "<name>"}          launch an app by name
    {"action_type": "wait"}                                    wait for the screen to update
    {"action_type": "answer", "text": "<s>"}                   answer a question
    {"action_type": "status", "goal_status": "complete"}       you believe the task is complete
    {"action_type": "status", "goal_status": "infeasible"}     you cannot do the task

Output format (strict): reply with exactly ONE line, either `action: <json>` or the bare JSON object, e.g. `action: {"action_type": "click", "index": 12}`. No prose, explanation, or markdown before or after it. A malformed reply is rejected and you will be asked again.

General rules (the same for every task): act only through the phone's touchscreen UI with the actions above; do not try to call the app's internals. Use element indexes only from the most recent observation; stale indexes fail. If an element you need is not visible, scroll to reveal it first. Malformed actions or execution errors are reported back to you as errors."""

_SYSTEM_PREFIX = "You are a GUI agent operating an Android phone.\n\n"


def system_prompt() -> str:
    """The system message (action space + reply format), web-side shape."""
    return _SYSTEM_PREFIX + ACTION_SPACE_DESCRIPTION


class ActionError(ValueError):
    """A reply that does not contain exactly one valid JSON action."""


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.S)


def parse_action(reply: str):
    """Extract the first valid JSONAction from a (possibly chatty) reply.

    Returns an ``android_world.env.json_action.JSONAction``; raises
    ActionError when no JSON object in the reply converts to one.
    """
    from android_world.agents import agent_utils
    from android_world.env import json_action

    text = reply or ""
    # strip the guiexp "action: " prefix shape and M3A's "Action: " shape;
    # extract_json then finds the first {...} either way.
    for candidate in _JSON_OBJECT_RE.finditer(text):
        snippet = candidate.group(0)
        try:
            obj = agent_utils.extract_json(snippet)
        except Exception:  # noqa: BLE001 - fall through to next candidate
            continue
        if not isinstance(obj, dict) or "action_type" not in obj:
            continue
        try:
            return json_action.JSONAction(**obj)
        except Exception:  # noqa: BLE001 - wrong args; try the next object
            continue
    raise ActionError(f"no valid JSON action found in: {text!r}")


def render(action) -> str:
    """Canonical action string for the trajectory ('action' field)."""
    return action.json_str()


def is_done(action) -> bool:
    """A status action (complete or infeasible) terminates the episode."""
    return getattr(action, "action_type", None) == "status"
