"""Unit tests for guiexp.estimator (discovery/execution token split).

Synthetic trajectories exercise the cut rule (fill-first, select,
click-submit-first, navigation-only clicks, no state change, failed actions)
and the floor semantics (subtraction, clamp, told-run negative-control
invariant, floor-condition trajectories). One integration test runs the
estimator on the committed mock discover trajectory and prints the share,
asserting only structural validity (per T0.7).
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from guiexp.actions import ActionError, parse_action
from guiexp.estimator import SUBMIT_KEYWORDS, split_trajectory

REPO_ROOT = Path(__file__).resolve().parents[3]
MOCK_DISCOVER = REPO_ROOT / "experimental-results" / "guiexp" / "wizard_discover_s0_mock" / "trajectory.jsonl"
MOCK_FLOOR = REPO_ROOT / "experimental-results" / "guiexp" / "wizard_floor_s0_mock" / "trajectory.jsonl"


# -- synthetic trajectory helpers -----------------------------------------


def make_step(step, action_raw, prompt, completion, error=None, action="auto"):
    """One step record in the runner's trajectory format."""
    if action == "auto":
        try:
            action = parse_action(action_raw).render()
        except ActionError:
            action = None
    return {
        "step": step,
        "action_raw": action_raw,
        "action": action,
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "cost_usd": 0.0},
        "obs_meta": {
            "url": "http://localhost:5001/calendar/create_event",
            "screenshot_file": f"step_{step:03d}.png",
            "ax_chars": 500,
            "last_action_error": error,
        },
    }


def make_final(condition="discover", steps=0, total_tokens=0):
    return {
        "success": True,
        "total_tokens": total_tokens,
        "total_cost_usd": 0.0,
        "condition": condition,
        "layout": "wizard",
        "seed": 0,
        "model": "mock",
        "obs_mode": "screenshot+ax",
        "task_id": f"wizard__{condition}__s0",
        "steps": steps,
        "record_type": "final",
    }


def write_traj(tmp_path, step_records, condition="discover", name="trajectory.jsonl"):
    total = sum(r["usage"]["prompt_tokens"] + r["usage"]["completion_tokens"] for r in step_records)
    records = step_records + [make_final(condition, len(step_records), total)]
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def fill_first_steps():
    """goto, scroll, then the first fill; tokens 11/21/31/41/51 per step."""
    return [
        make_step(1, 'goto("http://localhost:5001/calendar/create_event")', 10, 1),
        make_step(2, "scroll(down)", 20, 1),
        make_step(3, "fill('36', 'Alice-Carol')", 30, 1),
        make_step(4, "fill('38', '2026-05-12')", 40, 1),
        make_step(5, "click('39')", 50, 1),  # wizard "Next": unlabeled here
        make_step(6, "done()", 60, 1),
    ]


# -- cut rule ---------------------------------------------------------------


def test_fill_first_cut(tmp_path):
    traj = write_traj(tmp_path, fill_first_steps())
    out = split_trajectory(traj)
    assert out["cut_step"] == 3
    assert out["cut_action"] == "fill('36', 'Alice-Carol')"
    assert out["discovery_tokens"] == 11 + 21  # steps 1-2
    assert out["execution_tokens"] == 31 + 41 + 51 + 61  # cut step 3 onward
    assert out["discovery_tokens"] + out["execution_tokens"] == out["total_tokens"] == 216
    assert out["estimated_share"] == pytest.approx(32 / 216)
    assert out["floor_subtracted"] is False
    assert out["condition"] == "discover"


def test_select_is_state_changing(tmp_path):
    steps = [
        make_step(1, 'goto("http://localhost:5001/calendar/create_event")', 10, 1),
        make_step(2, "select('50', 'weekly')", 20, 1),
        make_step(3, 'fill(\'36\', "Alice-Carol\')', 30, 1),
    ]
    out = split_trajectory(write_traj(tmp_path, steps))
    assert out["cut_step"] == 2
    assert out["cut_action"] == "select('50', 'weekly')"
    assert out["discovery_tokens"] == 11
    assert out["execution_tokens"] == 21 + 31


@pytest.mark.parametrize(
    "label",
    [
        "[49] button 'Create'",  # full AX line as it appears in observations
        "button 'Submit'",
        "SAVE EVENT",  # bare name, case-insensitive
    ],
)
def test_click_submit_first_cuts(tmp_path, label):
    steps = [
        make_step(1, "scroll(down)", 10, 1),
        make_step(2, "click('49')", 20, 1),
        make_step(3, "done()", 30, 1),
    ]
    traj = write_traj(tmp_path, steps)
    out = split_trajectory(traj, element_labels={"49": label})
    assert out["cut_step"] == 2
    assert out["cut_action"] == "click('49')"
    assert out["discovery_tokens"] == 11
    assert out["execution_tokens"] == 21 + 31
    assert out["unresolved_clicks"] == 0


def test_navigation_clicks_do_not_cut(tmp_path):
    """Next / More details / Add Event clicks and unlabeled clicks are not
    cuts; the cut falls on the later fill, and unlabeled clicks are counted."""
    steps = [
        make_step(1, 'goto("http://localhost:5001/calendar")', 10, 1),
        make_step(2, "click('9')", 20, 1),  # "Add Event" navigation link
        make_step(3, "click('21')", 30, 1),  # "More details" reveal
        make_step(4, "click('77')", 40, 1),  # label unknown
        make_step(5, "click('39')", 50, 1),  # wizard "Next" (type=submit!)
        make_step(6, "fill('36', 'Alice-Carol')", 60, 1),
        make_step(7, "done()", 70, 1),
    ]
    traj = write_traj(tmp_path, steps)
    labels = {
        "9": "link 'Add Event'",
        "21": "button 'More details'",
        "39": "[39] button 'Next'",
    }
    out = split_trajectory(traj, element_labels=labels)
    assert out["cut_step"] == 6
    assert out["cut_action"] == "fill('36', 'Alice-Carol')"
    assert out["discovery_tokens"] == 11 + 21 + 31 + 41 + 51
    assert out["execution_tokens"] == 61 + 71
    assert out["unresolved_clicks"] == 1  # bid 77 had no label


def test_no_state_change_cuts_at_end(tmp_path):
    steps = [
        make_step(1, 'goto("http://localhost:5001/calendar/create_event")', 10, 1),
        make_step(2, "scroll(down)", 20, 1),
        make_step(3, "click('39')", 30, 1),  # labeled "Next": still no cut
        make_step(4, "press('Enter')", 40, 1),
        make_step(5, "done()", 50, 1),
    ]
    traj = write_traj(tmp_path, steps, condition="discover")
    out = split_trajectory(traj, element_labels={"39": "button 'Next'"})
    assert out["cut_step"] is None
    assert out["cut_action"] is None
    assert out["execution_tokens"] == 0
    assert out["discovery_tokens"] == out["total_tokens"] == 11 + 21 + 31 + 41 + 51
    assert out["estimated_share"] == 1.0


def test_failed_action_does_not_cut(tmp_path):
    """A step whose action errored (stale bid) changed no state; the split
    looks past it to the next effective action."""
    steps = [
        make_step(1, "scroll(down)", 10, 1),
        make_step(2, "fill('99', 'nope')", 20, 1, error="TimeoutError: bid 99 not found"),
        make_step(3, "fill('36', 'Alice-Carol')", 30, 1),
        make_step(4, "done()", 40, 1),
    ]
    out = split_trajectory(write_traj(tmp_path, steps))
    assert out["cut_step"] == 3
    assert out["discovery_tokens"] == 11 + 21  # the failed fill is still discovery
    assert out["execution_tokens"] == 31 + 41


def test_malformed_reply_is_not_a_cut(tmp_path):
    steps = [
        make_step(1, "I will fill the form now", 10, 1, action=None),
        make_step(2, "fill('36', 'Alice-Carol')", 20, 1),
    ]
    out = split_trajectory(write_traj(tmp_path, steps))
    assert out["cut_step"] == 2
    assert out["discovery_tokens"] == 11


# -- floor semantics ---------------------------------------------------------


def test_floor_subtraction_and_clamp(tmp_path):
    traj = write_traj(tmp_path, fill_first_steps())
    raw = split_trajectory(traj)
    floored = split_trajectory(traj, floor_tokens=12)
    assert raw["discovery_tokens"] == raw["discovery_tokens_raw"] == 32
    assert floored["floor_subtracted"] is True
    assert floored["discovery_tokens"] == 32 - 12
    assert floored["execution_tokens"] == raw["execution_tokens"]  # floor hits discovery only
    assert floored["estimated_share"] == pytest.approx(20 / (20 + 184))
    # Floor larger than discovery (told-like run): clamps at zero, share -> 0.
    clamped = split_trajectory(traj, floor_tokens=1000)
    assert clamped["discovery_tokens"] == 0
    assert clamped["estimated_share"] == 0.0


def test_told_negative_control_invariant(tmp_path):
    """Design note made executable: on a told run the first action is already
    state-changing, so after subtracting the paired floor the discovery share
    must be ~0 (the Phase-1 validation invariant)."""
    told_steps = [
        make_step(1, "fill('36', 'Alice-Carol')", 1600, 10),
        make_step(2, "fill('38', '2026-05-12')", 2800, 8),
        make_step(3, "click('49')", 4000, 2),
    ]
    traj = write_traj(tmp_path, told_steps, condition="told")
    out = split_trajectory(traj, floor_tokens=1610)  # paired floor ~= step 1
    assert out["condition"] == "told"
    assert out["cut_step"] == 1
    assert out["discovery_tokens"] == 0  # 1610 - 1610, clamped
    assert out["estimated_share"] == 0.0


def test_floor_condition_trajectory(tmp_path):
    """A floor run is a single no-goal step; its own usage IS the floor."""
    steps = [make_step(0, "done()", 1564, 1)]
    traj = write_traj(tmp_path, steps, condition="floor")
    out = split_trajectory(traj)
    assert out["floor_estimate"] == 1565
    assert out["cut_step"] is None  # done() changes nothing
    assert out["estimated_share"] == 1.0  # nothing was ever executed
    assert out["floor_subtracted"] is False  # no explicit floor -> no subtraction


def test_floor_estimate_defaults_to_step1_usage(tmp_path):
    out = split_trajectory(write_traj(tmp_path, fill_first_steps()))
    assert out["floor_estimate"] == 11  # step 1 usage, upper bound (incl. goal)
    out_explicit = split_trajectory(write_traj(tmp_path, fill_first_steps()), floor_tokens=7)
    assert out_explicit["floor_estimate"] == 7


def test_empty_trajectory(tmp_path):
    traj = write_traj(tmp_path, [], condition="discover")
    out = split_trajectory(traj)
    assert out["n_steps"] == 0
    assert out["cut_step"] is None
    assert out["total_tokens"] == 0
    assert out["estimated_share"] is None


# -- integration: committed mock discover trajectory -------------------------


def _click_labels_from_urls(records):
    """bid -> label for click steps, derived from where each click landed:
    the wizard save redirects to /calendar; Next lands on /step2 or /step3."""
    labels = {}
    for rec in records:
        action = rec.get("action") or ""
        if not action.startswith("click("):
            continue
        bid = action[len("click(") :].rstrip(")").strip("'\"")
        path = urlparse((rec.get("obs_meta") or {}).get("url") or "").path
        labels[bid] = "button 'Create'" if path == "/calendar" else "button 'Next'"
    return labels


def test_integration_mock_discover_trajectory():
    if not MOCK_DISCOVER.exists():
        pytest.skip(f"mock trajectory not found: {MOCK_DISCOVER}")
    records = [json.loads(line) for line in MOCK_DISCOVER.read_text().splitlines() if line.strip()]

    out = split_trajectory(MOCK_DISCOVER)
    print("mock discover share (no floor):", json.dumps(out, indent=1))

    # Structural validity only, not the value.
    assert out["condition"] == "discover"
    assert out["n_steps"] == len([r for r in records if "step" in r]) == 10
    assert out["discovery_tokens"] + out["execution_tokens"] == out["total_tokens"]
    assert 0.0 <= out["estimated_share"] <= 1.0
    assert out["cut_action"] is not None and out["cut_action"].startswith("fill(")
    assert out["cut_step"] is not None and 1 <= out["cut_step"] <= out["n_steps"]

    # With clicks resolved via the URL-transition labels: the two wizard
    # "Next" clicks (HTML type=submit) must NOT move the cut off the fill.
    labeled = split_trajectory(MOCK_DISCOVER, element_labels=_click_labels_from_urls(records))
    print("mock discover share (clicks resolved):", labeled["estimated_share"])
    assert labeled["unresolved_clicks"] == 0
    assert labeled["cut_step"] == out["cut_step"]

    # Floor-subtracted share using the paired floor run, when present.
    if MOCK_FLOOR.exists():
        floor_rec = json.loads(MOCK_FLOOR.read_text().splitlines()[0])
        floor = (
            floor_rec["usage"]["prompt_tokens"] + floor_rec["usage"]["completion_tokens"]
        )
        floored = split_trajectory(MOCK_DISCOVER, floor_tokens=floor)
        print(
            f"mock discover share (floor={floor} subtracted):",
            floored["estimated_share"],
            "discovery:", floored["discovery_tokens"],
        )
        assert floored["floor_subtracted"] is True
        assert 0.0 <= floored["estimated_share"] <= out["estimated_share"]
