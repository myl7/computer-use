"""Unit tests for the JSON action space parsing (android_world's json_action)."""

from __future__ import annotations

import pytest

from guiexp_android.actions import ActionError, is_done, parse_action, render, system_prompt


def test_parses_bare_json():
    action = parse_action('{"action_type": "click", "index": 12}')
    assert action.action_type == "click"
    assert action.index == 12
    assert render(action) == '{"action_type":"click","index":12}'


def test_parses_action_prefix_shape():
    action = parse_action('action: {"action_type": "input_text", "text": "hi there", "index": 3}')
    assert action.action_type == "input_text"
    assert action.text == "hi there" and action.index == 3


def test_parses_m3a_reason_action_shape():
    reply = (
        "Reason: The Save button is visible at the top right, so I tap it.\n"
        'Action: {"action_type": "click", "index": 2}'
    )
    action = parse_action(reply)
    assert action.action_type == "click" and action.index == 2


def test_parses_after_chatty_prose_and_skips_non_action_objects():
    reply = (
        "Let me think {about it}. "
        'Final answer: {"action_type": "scroll", "direction": "down"}'
    )
    action = parse_action(reply)
    assert action.action_type == "scroll" and action.direction == "down"


def test_status_actions_terminate():
    assert is_done(parse_action('{"action_type": "status", "goal_status": "complete"}'))
    assert is_done(parse_action('{"action_type": "status", "goal_status": "infeasible"}'))
    assert not is_done(parse_action('{"action_type": "wait"}'))


@pytest.mark.parametrize("bad", [
    "",
    "I will click the save button now.",
    '{"no_action_type": true}',
    '{"action_type": "teleport", "index": 1}',
])
def test_bad_replies_raise(bad):
    with pytest.raises(ActionError):
        parse_action(bad)


def test_system_prompt_describes_reply_contract():
    text = system_prompt()
    assert "exactly ONE line" in text
    assert '"action_type"' in text
    assert "one per turn" in text
