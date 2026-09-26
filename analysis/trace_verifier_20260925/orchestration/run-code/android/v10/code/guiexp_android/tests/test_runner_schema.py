"""Runner-loop tests with the canned model stub (no emulator, no LLM) and the
trajectory-schema equality check against the web-side guiexp harness.

The web side is the source of truth for the JSONL schema: the record keys are
extracted from the LIVE guiexp/runner.py and guiexp/agent.py sources (not a
frozen copy), and additionally cross-checked against any real web trajectory
left by the mock runs under experimental-results/guiexp.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from guiexp_android import android_env, runner
from guiexp_android.mock_model import MockOpenAI

PKG_PARENT = Path(__file__).resolve().parents[2]  # .../computer-use
WEB_RUNNER = PKG_PARENT / "guiexp" / "runner.py"
WEB_AGENT = PKG_PARENT / "guiexp" / "agent.py"
WEB_TRAJ_DIR = PKG_PARENT.parent / "experimental-results" / "guiexp"

# 1x1 transparent PNG
TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhf"
    "DwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeEnv:
    """Duck-typed stand-in for AndroidWorldEnv (reset/step/close)."""

    def __init__(self):
        self.steps: list[str] = []
        self.closed = False

    @staticmethod
    def _obs(**extra):
        obs = {
            "screenshot_b64": TINY_PNG,
            "som_screenshot_b64": TINY_PNG,
            "ax_tree_text": 'UI element 0: {"index": 0, "text": "Save", "is_clickable": true}',
            "url": "com.example/.FakeActivity",
            "goal_text": "fake goal",
        }
        obs.update(extra)
        return obs

    def reset(self, task):
        return self._obs()

    def step(self, action_text):
        self.steps.append(action_text)
        try:
            action = runner.parse_action(action_text)
        except runner.ActionError as exc:  # same shape as the real env
            return self._obs(last_action_error=f"ActionError: {exc}"), False, 0.0
        if action.action_type == "status":
            return self._obs(), True, 1.0
        return self._obs(), False, 0.0

    def close(self):
        self.closed = True


def _episode(tmp_path, condition="discover", script=None, family="ContactsAddContact"):
    env = FakeEnv()
    final = runner.run_episode(
        family=family,
        condition=condition,
        seed=0,
        model="mock",
        obs_mode="screenshot+ax",
        max_steps=10,
        out_dir=tmp_path / "traj",
        client=MockOpenAI(script=script),
        env=env,
    )
    records = [json.loads(line)
               for line in (tmp_path / "traj" / "trajectory.jsonl").read_text().splitlines()]
    return env, final, records


# -- the loop --------------------------------------------------------------


def test_episode_with_canned_model_including_retry(tmp_path):
    script = [
        "I will first look around the screen.",  # malformed -> one re-ask
        '{"action_type": "open_app", "app_name": "contacts"}',
        'action: {"action_type": "status", "goal_status": "complete"}',
    ]
    env, final, records = _episode(tmp_path, script=script)

    steps = [r for r in records if r.get("record_type") != "final"]
    retries = [r for r in steps if "retry" in r]
    assert len(retries) == 1 and retries[0]["retry"] == 1
    assert len(env.steps) == 2  # the malformed reply never reached the env
    assert "open_app" in env.steps[0] and "contacts" in env.steps[0]
    assert env.closed

    final_rec = records[-1]
    assert final_rec["success"] is True
    assert final_rec["steps"] == 2          # env steps, not model calls
    assert final_rec["model_calls"] == 3    # retry re-ask included
    assert final_rec["task_id"] == "ContactsAddContact__discover__s0"

    # the runner wrote the raw (unmarked) screenshot, one file per step
    out_dir = tmp_path / "traj"
    shots = sorted(p.name for p in out_dir.glob("step_*.png"))
    assert shots == ["step_001.png", "step_002.png"]
    assert (out_dir / shots[0]).read_bytes()[:4] == b"\x89PNG"


def test_floor_episode_is_one_call(tmp_path):
    _, final, records = _episode(tmp_path, condition="floor")
    assert len(records) == 2
    assert records[0]["step"] == 0
    assert final["success"] is None
    assert final["steps"] == 1 and final["condition"] == "floor"


def test_failed_parse_retries_are_bounded(tmp_path):
    script = ["no action here"] * 100  # always malformed
    _, final, records = _episode(tmp_path, script=script)
    retries = [r for r in records if "retry" in r]
    # per env step, the re-ask is capped at MAX_PARSE_RETRIES (the episode
    # itself runs to max_steps, exactly like the web runner)
    steps = [r for r in records if r.get("record_type") != "final" and "retry" not in r]
    assert len(steps) == 10  # max_steps
    index = [i for i, r in enumerate(records) if "retry" not in r and r.get("record_type") != "final"]
    for prev, cur in zip(index, index[1:]):
        assert cur - prev - 1 <= runner.MAX_PARSE_RETRIES
    assert len(retries) == 10 * runner.MAX_PARSE_RETRIES
    assert final["success"] is False


# -- schema equality with the web side -------------------------------------


def _extract_dict_keys(source: str, start_marker: str) -> set[str]:
    """TOP-LEVEL keys of the first dict literal beginning at ``start_marker``.

    Depth-aware so nested dict values (e.g. ``obs_meta`` inside the step
    record) are not mixed into the outer record's key set.
    """
    start = source.index(start_marker)
    i = source.index("{", start)
    depth = 0
    keys: set[str] = set()
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return keys
        elif ch == '"' and depth == 1:
            j = i + 1
            while source[j] != '"':  # keys here contain no escapes
                j += 1
            key = source[i + 1:j]
            k = j + 1
            while source[k] in " \t":
                k += 1
            if source[k] == ":":
                keys.add(key)
            i = k
        i += 1
    raise AssertionError(f"unbalanced braces after {start_marker!r}")


WEB_SOURCE = WEB_RUNNER.read_text()
WEB_REC_KEYS = _extract_dict_keys(WEB_SOURCE, "rec = {")
WEB_OBS_META_KEYS = _extract_dict_keys(WEB_SOURCE, '"obs_meta": {')
WEB_FINAL_KEYS = _extract_dict_keys(WEB_SOURCE, "final = {")
WEB_USAGE_KEYS = _extract_dict_keys(WEB_AGENT.read_text(), "def _usage")  # first dict in _usage


def test_web_sources_were_found():
    assert {"step", "action_raw", "action", "usage", "obs_meta"} <= WEB_REC_KEYS
    assert {"url", "screenshot_file", "ax_chars", "last_action_error"} == WEB_OBS_META_KEYS
    assert {"success", "total_tokens", "total_cost_usd", "condition", "layout",
            "seed", "model", "obs_mode", "record_type"} <= WEB_FINAL_KEYS
    assert {"prompt_tokens", "completion_tokens", "cost_usd"} <= WEB_USAGE_KEYS


def test_trajectory_schema_matches_web_side(tmp_path):
    _, _, records = _episode(tmp_path)
    steps = [r for r in records if r.get("record_type") != "final"]
    final = records[-1]

    # step records: identical field names (the web side adds 'retry' to the
    # record literal's keys only when a re-ask was needed; ours does too)
    for record in steps:
        expected = set(WEB_REC_KEYS)
        if "retry" in record:
            expected |= {"retry"}
        assert set(record) == expected, set(record) ^ expected
        assert set(record["obs_meta"]) == WEB_OBS_META_KEYS
        assert set(record["usage"]) == WEB_USAGE_KEYS
        # same value shapes as the web records
        assert isinstance(record["step"], int)
        assert record["action_raw"] and isinstance(record["action_raw"], str)
        assert record["action"] is None or record["action"].startswith('{"action_type"')
        assert isinstance(record["obs_meta"]["ax_chars"], int)
        assert record["obs_meta"]["screenshot_file"] is None or \
            record["obs_meta"]["screenshot_file"].endswith(".png")

    # final record: identical field names except layout -> family. The web
    # side's literal later gained two optional manifest keys (the m_library
    # routing experiment; written only when --manifest is passed, which no
    # Android run ever does) -- excluded from the parity comparison.
    WEB_OPTIONAL_FINAL_KEYS = {"manifest", "manifest_entries"}
    assert set(final) == (WEB_FINAL_KEYS - {"layout"} - WEB_OPTIONAL_FINAL_KEYS) | {"family"}
    assert final["record_type"] == "final"


def test_schema_matches_real_web_trajectory_on_disk():
    """Cross-check against actual web mock trajectories, if present."""
    web_traj = WEB_TRAJ_DIR / "wizard_floor_s0_mock" / "trajectory.jsonl"
    if not web_traj.exists():
        pytest.skip("no on-disk web trajectory")
    web_final = None
    web_step = None
    for line in web_traj.read_text().splitlines():
        record = json.loads(line)
        if record.get("record_type") == "final":
            web_final = record
        elif web_step is None:
            web_step = record
    assert web_final is not None and web_step is not None

    # The on-disk file predates the web runner's 'model_calls' field, so the
    # live-source comparison above is the strict one; here we check that every
    # key the web side actually wrote exists under the same name on our side.
    mapped = {("family" if k == "layout" else k) for k in web_final}
    assert mapped <= (WEB_FINAL_KEYS - {"layout"}) | {"family"}
    assert set(web_step) <= WEB_REC_KEYS


# -- agent observation parts -------------------------------------------------


def test_agent_observation_parts(tmp_path):
    from guiexp_android.agent import AndroidAgent

    agent = AndroidAgent(model="mock", obs_mode="screenshot+ax", client=MockOpenAI())
    obs = FakeEnv._obs(last_action_error="ValueError: bad index")
    parts = agent._observation_parts("GOAL TEXT", obs)
    texts = [p["text"] for p in parts if p["type"] == "text"]
    images = [p for p in parts if p["type"] == "image_url"]
    assert texts[0].startswith("GOAL TEXT")
    assert "Current app: com.example/.FakeActivity" in texts[0]
    assert "Your previous action failed" in texts[0]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert any("UI element 0" in t for t in texts)  # +ax part carries the list

    agent_som = AndroidAgent(model="mock", obs_mode="screenshot", client=MockOpenAI())
    parts = agent_som._observation_parts(None, obs)
    assert len(parts) == 2  # text + SoM image, no element list in this mode
    assert not any("UI element" in p.get("text", "") for p in parts)


def test_seed_binds_the_same_instance_across_calls():
    for family in ("ContactsAddContact", "MarkorCreateNote"):
        a = android_env.instance_params(family, 7)
        b = android_env.instance_params(family, 7)
        assert a == b
        assert a != android_env.instance_params(family, 8)
