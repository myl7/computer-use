"""Integration test against the real emulator (LLM-free).

The simpler acceptable variant from the protocol: verify the reset ->
observation -> step plumbing on a real ContactsAddContact instance, then
complete the task at the adb level (contacts_utils.add_contact, the same
helper guiexp/ANDROID_SETUP.md used) and check the initialize_task /
is_successful oracle round-trip flips 0.0 -> 1.0 and auto-terminates.

Emulator etiquette: the fixture boots AndroidWorldAvd only if no emulator
is attached, and kills it at session end ONLY if this session started it
(``adb emu kill``; never touches someone else's emulator). Set
ANDROID_EXP_SKIP_EMULATOR=1 to skip.
"""

from __future__ import annotations

import base64
import os

import pytest

from guiexp_android import android_env

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("ANDROID_EXP_SKIP_EMULATOR") == "1",
        reason="ANDROID_EXP_SKIP_EMULATOR=1",
    ),
    pytest.mark.skipif(
        not android_env.SDK_DIR.exists(),
        reason=f"Android SDK not found at {android_env.SDK_DIR}",
    ),
]


@pytest.fixture(scope="module")
def env():
    env_ = android_env.AndroidWorldEnv()
    yield env_
    env_.close()
    env_.stop_emulator()  # adb emu kill, only if this session booted it


def test_contacts_reset_step_and_oracle_roundtrip(env):
    task = android_env.get_task("ContactsAddContact", "discover", seed=0)

    # -- reset: initialization + first observation
    obs = env.reset(task)
    assert obs["screenshot_b64"]
    assert base64.b64decode(obs["screenshot_b64"])[:4] == b"\x89PNG"
    assert obs["som_screenshot_b64"]  # SoM marks were rendered
    assert "UI element 0:" in obs["ax_tree_text"]  # M3A text variant
    assert '"index": 0' in obs["ax_tree_text"]
    assert obs["url"]  # foreground activity
    assert obs["goal_text"].startswith("Create a new contact for")
    assert env.reward() == 0.0  # initialize_task cleared the contacts

    # -- step: one benign JSON action through android_world's actuation
    obs2, done, reward = env.step('action: {"action_type": "wait"}')
    assert obs2["last_action_error"] is None
    assert done is False and reward == 0.0
    assert obs2["screenshot_b64"]

    # a malformed reply is reported, not executed
    obs3, done3, reward3 = env.step("I will tap the save button.")
    assert obs3["last_action_error"] and obs3["last_action_error"].startswith("ActionError")
    assert done3 is False

    # -- adb-level completion + oracle round-trip (LLM-free, as in T0.5)
    from android_world.utils import contacts_utils

    contacts_utils.add_contact(
        task.params["name"], task.params["number"], env.aw_env.controller,
        ui_delay_sec=3.0,  # cold Google Contacts first launch needs the delay
    )
    assert env.reward() == 1.0
    assert env._task_impl.is_successful(env.aw_env) == 1.0

    # -- auto-termination on reward >= 1.0 (web guiexp rule)
    _, done4, reward4 = env.step('{"action_type": "wait"}')
    assert done4 is True and reward4 == 1.0
