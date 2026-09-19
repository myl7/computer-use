"""Integration tests: real app + Chromium + the offline mock model.

Every model call goes through MockOpenAI, so these tests make ZERO network
LLM calls (the only HTTP traffic is to the local OpenApps server).
"""

import base64
import json
from types import SimpleNamespace

import pytest

from guiexp.actions import Action, ActionError, parse_action, parse_first_action
from guiexp.agent import GuiAgent
from guiexp.app_server import AppServer, LAYOUTS, detect_layout
from guiexp.env import GuiEnv, get_task
from guiexp.mock_model import MockOpenAI, policy_reply
from guiexp.runner import run_episode


@pytest.fixture(scope="session")
def servers():
    """One server per layout for the whole session; AppServer.start() is
    idempotent, so run_episode reuses them via the registry."""
    started = [AppServer(layout) for layout in LAYOUTS]
    try:
        yield {s.layout: s.start() for s in started}
    finally:
        for server in started:
            server.stop()


# ---------------------------------------------------------------- parser


def test_parse_action_forms():
    assert parse_action('fill("12", "Dennis")') == Action("fill", ("12", "Dennis"))
    assert parse_action("fill(12, 'Hello, world')") == Action("fill", ("12", "Hello, world"))
    assert parse_action("click(a7)") == Action("click", ("a7",))
    assert parse_action("scroll(down)") == Action("scroll", ("down",))
    assert parse_action("done()") == Action("done", ())
    with pytest.raises(ActionError):
        parse_action("click()")
    with pytest.raises(ActionError):
        parse_action("explode(1)")
    with pytest.raises(ActionError):
        parse_first_action("no action here")


def test_parse_first_action_from_prose():
    action = parse_first_action('I will fill the title.\n```\nfill("3", "X")\n```')
    assert action == Action("fill", ("3", "X"))


# ------------------------------------------------------------ mock policy


def _msg(role, text):
    return {"role": role, "content": [{"type": "text", "text": text}]}


GOAL = (
    "You are operating the web application at http://localhost:59999.\n\n"
    "Add an event to the calendar with exactly these values:\n"
    "  title: Dennis-Bob\n  date: 2026-04-01\n  description: Quarterly sync\n"
    "  location: Room 3\n  url: https://example.com/sync\n  invitees: Dennis\n"
)
AX_SCREEN1 = (
    "Current URL: http://localhost:59999/calendar/create_event\n"
    "[1] heading 'Create New Event (1 of 3)'\n"
    "[4] textbox 'Event title', clickable, visible\n"
    "[6] textbox 'Event date, YYYY-MM-DD', clickable, visible\n"
    "[8] button 'Next', clickable, visible\n"
)


def test_mock_policy_navigates_fills_and_clicks_through():
    messages = [_msg("user", GOAL + "\nCurrent URL: http://localhost:59999/\n[1] link 'Calendar'")]
    assert policy_reply(messages) == 'goto("http://localhost:59999/calendar/create_event")'

    messages = [_msg("user", GOAL + "\n" + AX_SCREEN1)]
    assert policy_reply(messages) == 'fill("4", "Dennis-Bob")'

    messages += [
        _msg("assistant", 'fill("4", "Dennis-Bob")'),
    ]
    assert policy_reply(messages) == 'fill("6", "2026-04-01")'

    messages += [_msg("assistant", 'fill("6", "2026-04-01")')]
    assert policy_reply(messages) == 'click("8")'

    messages += [_msg("user", AX_SCREEN1.replace("(1 of 3)", "(3 of 3)")
                      .replace("'Event title'", "'Event URL'")
                      .replace("'Event date, YYYY-MM-DD'", "'Event invitees'"))]
    assert policy_reply(messages) == 'fill("4", "https://example.com/sync")'


def test_mock_policy_scrolls_and_does_not_reclick_reveal():
    # single_page-like screen: everything visible is filled, the submit
    # button is below the fold -> scroll down rather than give up.
    ax = (
        "Current URL: http://localhost:59999/calendar/create_event\n"
        "[4] textbox 'Event title', clickable, visible\n"
        "[6] textbox 'Event date, YYYY-MM-DD', clickable, visible\n"
        "[8] textbox 'Event description', clickable, visible\n"
    )
    messages = [
        _msg("user", GOAL + "\n" + ax),
        _msg("assistant", 'fill("4", "Dennis-Bob")'),
        _msg("assistant", 'fill("6", "2026-04-01")'),
        _msg("assistant", 'fill("8", "Quarterly sync")'),
        _msg("user", ax),
    ]
    assert policy_reply(messages) == "scroll(down)"

    # sectioned, after the reveal: optional fields are on screen, so the
    # still-present "More details" button must NOT be clicked again.
    ax_revealed = (
        "Current URL: http://localhost:59999/calendar/create_event\n"
        "[9] button 'More details', clickable, visible\n"
        "[12] textbox 'Event description', clickable, visible\n"
        "[14] textbox 'Event location', clickable, visible\n"
    )
    messages = [
        _msg("user", GOAL + "\n" + ax_revealed),
        _msg("assistant", 'fill("12", "Quarterly sync")'),
        _msg("assistant", 'fill("14", "Room 3")'),
        _msg("user", ax_revealed),
    ]
    # url/invitees and Submit are below the fold: scroll, not re-reveal.
    assert policy_reply(messages) == "scroll(down)"

    # bounded scrolling: after three scroll(down)s with nothing new, stop.
    messages.append(_msg("assistant", "scroll(down)"))
    messages.append(_msg("user", ax_revealed))
    messages.append(_msg("assistant", "scroll(down)"))
    messages.append(_msg("user", ax_revealed))
    messages.append(_msg("assistant", "scroll(down)"))
    messages.append(_msg("user", ax_revealed))
    assert policy_reply(messages) == "done()"


# ------------------------------------------------------------ environment


def test_reset_returns_aligned_observation(servers):
    base = servers["wizard"]
    assert detect_layout(base) == "wizard"
    env = GuiEnv(base, layout="wizard")
    try:
        task = get_task("wizard", "discover", 0, base)
        obs = env.reset(task)
        assert obs["url"].startswith(base)
        assert task.event["title"] in obs["goal_text"]
        # screenshot is a decodable PNG
        assert base64.b64decode(obs["screenshot_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
        # the start page shows interactive elements in [bid] role 'name' form
        assert "link" in obs["ax_tree_text"] and "[" in obs["ax_tree_text"]
        # step to the form: the AX tree stays aligned with the action space
        obs, done, reward = env.step('goto("/calendar/create_event")')
        assert not done and reward == 0.0 and obs["last_action_error"] is None
        assert "textbox" in obs["ax_tree_text"]
        assert "'Event title'" in obs["ax_tree_text"]
        assert "button 'Next'" in obs["ax_tree_text"]
    finally:
        env.close()


# ------------------------------------------------------------- episodes


@pytest.mark.parametrize("layout", LAYOUTS)
def test_discover_style_run_succeeds(servers, tmp_path, layout):
    out = tmp_path / layout
    final = run_episode(
        layout=layout,
        condition="discover",
        seed=0,
        model="mock",
        obs_mode="screenshot+ax",
        max_steps=30,
        out_dir=out,
        client=MockOpenAI(),
    )
    assert final["success"] is True
    records = [json.loads(line) for line in (out / "trajectory.jsonl").read_text().splitlines()]
    assert records[-1] == final
    steps = records[:-1]
    assert 1 <= len(steps) <= 30
    for i, rec in enumerate(steps):
        assert set(rec) == {"step", "action_raw", "action", "usage", "obs_meta"}
        assert rec["step"] == i + 1
        assert rec["action"], rec["action_raw"]
        assert rec["usage"]["prompt_tokens"] > 0
        assert rec["usage"]["completion_tokens"] > 0
        shot = rec["obs_meta"]["screenshot_file"]
        assert shot and (out / shot).read_bytes()[:4] == b"\x89PNG"
    for key in ("success", "total_tokens", "total_cost_usd", "condition", "layout", "seed", "model", "obs_mode"):
        assert key in final
    assert final["model"] == "mock"  # offline: mock model, no LLM API
    assert final["total_tokens"] > 0


# ------------------------------------------------- observation plumbing


def test_agent_observation_parts_url_line_and_image_choice():
    obs = {
        "url": "http://x/",
        "screenshot_b64": "cmF3",          # "raw"
        "som_screenshot_b64": "c29t",      # "som"
        "ax_tree_text": "[1] link 'Calendar'",
    }
    screen_only = GuiAgent(model="m", obs_mode="screenshot", client=MockOpenAI())
    parts = screen_only._observation_parts(None, obs)
    assert "Current URL: http://x/" in parts[0]["text"]  # URL line in BOTH modes
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"].endswith("c29t")  # SoM-annotated image sent

    dual = GuiAgent(model="m", obs_mode="screenshot+ax", client=MockOpenAI())
    parts = dual._observation_parts(None, obs)
    assert "Current URL: http://x/" in parts[0]["text"]
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"].endswith("cmF3")  # unannotated in dual mode

    # without a SoM image, screenshot mode falls back to the raw one
    parts = screen_only._observation_parts(None, {**obs, "som_screenshot_b64": None})
    image = next(p for p in parts if p["type"] == "image_url")
    assert image["image_url"]["url"].endswith("cmF3")


def test_som_annotation_only_when_requested(servers):
    base = servers["wizard"]
    task = get_task("wizard", "discover", 0, base)
    # sync Playwright allows one instance per thread: sequence the envs.
    plain = GuiEnv(base, layout="wizard")
    try:
        obs_plain = plain.reset(task)
        assert obs_plain["som_screenshot_b64"] is None  # default: no overlay
    finally:
        plain.close()
    som = GuiEnv(base, layout="wizard", annotate_som=True)
    try:
        obs_som = som.reset(task)
        annotated = obs_som["som_screenshot_b64"]
        assert annotated
        assert base64.b64decode(annotated)[:8] == b"\x89PNG\r\n\x1a\n"
        assert annotated != obs_som["screenshot_b64"]  # badges change the bytes
    finally:
        som.close()


# ------------------------------------------------------- parse retry loop


class _FlakyCompletions:
    """First reply is prose with no action, everything after is the policy."""

    def __init__(self):
        self.calls = 0

    def create(self, *, model=None, messages=None, **_kw):
        self.calls += 1
        if self.calls == 1:
            reply = "Looking at the page, I think I should explore first."
        else:
            reply = policy_reply(messages)
        usage = SimpleNamespace(prompt_tokens=101, completion_tokens=7, cost=0.0001)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
        )


# --------------------------------------------------- popup adoption


def _add_event_bid(obs) -> str | None:
    import re

    for line in obs["ax_tree_text"].splitlines():
        if "Add Event" in line:
            return re.search(r"\[(\w+)\]", line).group(1)
    return None


def test_click_adopts_popup_for_add_event(servers):
    """The real 'Add Event' control is an <a target="_blank">: a plain
    Playwright click on it is a silent no-op. The env must adopt the popup
    as the single active page so the click actually navigates."""
    base = servers["single_page"]
    env = GuiEnv(base, layout="single_page")
    try:
        task = get_task("single_page", "discover", 0, base)
        env.reset(task)
        obs, done, reward = env.step('goto("/calendar")')
        # the footer control is below the fold: scroll until it is visible
        bid = None
        for _ in range(6):
            bid = _add_event_bid(obs)
            if bid:
                break
            obs, _, _ = env.step("scroll(down)")
        assert bid, "Add Event never became visible in the AX tree"
        obs, done, reward = env.step(f'click("{bid}")')
        assert "/calendar/create_event" in env.page.url
        assert "/calendar/create_event" in obs["url"]
        assert "textbox" in obs["ax_tree_text"]  # observing the form now
        assert len(env.page.context.pages) == 1  # opener closed, popup adopted

        # fallback: if the active page closes, the env switches to a live one
        # (here: opens a fresh page, since the opener was already closed)
        env.page.close()
        obs = env._observe()
        assert not env.page.is_closed()
        assert obs["url"] == "about:blank"
    finally:
        env.close()


# ------------------------------------------------------- flail nudging


class _FlailCompletions:
    """Clicks a dead bid three times (page never changes), then recovers.

    Records the user messages it saw so the test can assert the nudge text
    arrived on exactly the right turn and only there.
    """

    def __init__(self):
        self.calls = 0
        self.user_texts: list[str] = []

    def create(self, *, model=None, messages=None, **_kw):
        self.calls += 1
        # only the CURRENT turn's user message (history would repeat old notes)
        last_user = next(m for m in reversed(messages) if m.get("role") == "user")
        self.user_texts.append(last_user["content"][0]["text"])
        if self.calls <= 3:
            reply = 'action: click("99999")'  # no such bid -> error, no page change
        else:
            reply = policy_reply(messages)
        usage = SimpleNamespace(prompt_tokens=101, completion_tokens=7, cost=0.0001)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
        )


def test_flail_nudge_injected_once_per_streak(servers, tmp_path):
    out = tmp_path / "flail"
    flail = _FlailCompletions()
    client = MockOpenAI()
    client.chat = SimpleNamespace(completions=flail)
    final = run_episode(
        layout="wizard",
        condition="discover",
        seed=0,
        model="mock",
        obs_mode="screenshot+ax",
        max_steps=30,
        out_dir=out,
        client=client,
    )
    assert final["success"] is True  # recovery after the dead-bid streak
    nudged_texts = [t for t in flail.user_texts if "did not change the page" in t]
    assert nudged_texts, "nudge never injected"
    assert len(nudged_texts) == 1, "nudge must fire once per streak"
    assert flail.user_texts[3] is nudged_texts[0]  # the call right after the streak
    records = [json.loads(line) for line in (out / "trajectory.jsonl").read_text().splitlines()]
    flagged = [r for r in records if r.get("obs_meta", {}).get("flail_nudge")]
    assert [r["step"] for r in flagged] == [4]  # the nudged action is step 4


def test_parse_failure_reasks_same_step(servers, tmp_path):
    out = tmp_path / "flaky"
    client = MockOpenAI()
    client.chat = SimpleNamespace(completions=_FlakyCompletions())
    final = run_episode(
        layout="wizard",
        condition="discover",
        seed=0,
        model="mock",
        obs_mode="screenshot+ax",
        max_steps=30,
        out_dir=out,
        client=client,
    )
    assert final["success"] is True
    records = [json.loads(line) for line in (out / "trajectory.jsonl").read_text().splitlines()]
    # the malformed first reply is recorded as a retry of step 1, not a new step
    retry_records = [r for r in records if "retry" in r]
    assert len(retry_records) == 1
    assert retry_records[0]["step"] == 1 and retry_records[0]["action"] is None
    assert "usage" in retry_records[0]  # the wasted call's tokens are kept
    # env steps counted once; the extra model call shows up in model_calls
    assert final["steps"] == final["model_calls"] - 1
    assert final["total_tokens"] >= final["model_calls"] * 108


def test_floor_run_exits_after_one_observation(servers, tmp_path):
    out = tmp_path / "floor"
    final = run_episode(
        layout="wizard",
        condition="floor",
        seed=0,
        model="mock",
        obs_mode="screenshot+ax",
        max_steps=10,
        out_dir=out,
        client=MockOpenAI(),
    )
    records = [json.loads(line) for line in (out / "trajectory.jsonl").read_text().splitlines()]
    assert len(records) == 2  # one step record + the final record
    assert records[0]["step"] == 0
    assert records[0]["action_raw"] == "done()"
    assert records[-1] == final
    assert final["success"] is None  # no task under the floor condition
    assert final["total_tokens"] > 0
