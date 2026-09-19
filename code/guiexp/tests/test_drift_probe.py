"""Unit tests for guiexp.drift_probe (E7): arm definitions, binding
selection over the drift namespace, and outcome coding. No server, no
browser, no program execution -- the live probe is run by hand (and its
30-run result committed under experimental-results/guiexp/e7_drift/).
"""

from __future__ import annotations

import hashlib

from guiexp import drift_probe as dp
from guiexp.env import INSTANCE_POOL
from guiexp.gate_runner import GATE_BINDING_POOL, heldout_bindings


# -- arms -------------------------------------------------------------------


def test_arms_shape_matches_old_probe():
    assert [a.name for a in dp.ARMS] == [
        "wizard", "wizard+dark_theme", "wizard+black_and_white",
        "wizard+challenging_font", "single_page", "sectioned"]
    axes = [a.axis for a in dp.ARMS]
    assert axes.count("baseline") == 1
    assert axes.count("appearance") == 3
    assert axes.count("protocol") == 2


def test_arm_overrides_are_the_old_probe_strings():
    """The hydra overrides must be verbatim the old probe's, including the
    ``+`` on the flow key (the theme yamls leave it unset, and the app
    defaults the flow to single_page)."""
    by_name = {a.name: a for a in dp.ARMS}
    assert by_name["wizard"].overrides == ("apps/calendar/appearance=wizard",)
    assert by_name["wizard+dark_theme"].overrides == (
        "apps/calendar/appearance=dark_theme", dp.FLOW_WIZARD)
    assert by_name["wizard+black_and_white"].overrides == (
        "apps/calendar/appearance=black_and_white", dp.FLOW_WIZARD)
    assert by_name["wizard+challenging_font"].overrides == (
        "apps/calendar/appearance=challenging_font", dp.FLOW_WIZARD)
    assert by_name["single_page"].overrides == ("apps/calendar/appearance=default",)
    assert by_name["sectioned"].overrides == ("apps/calendar/appearance=sectioned",)
    assert dp.FLOW_WIZARD == "+apps.calendar.form_flow=wizard"


def test_arm_markers_separate_the_axes():
    """Baseline and appearance arms keep the wizard flow (marker present);
    protocol arms lose it (single_page asserts absence, sectioned swaps it)."""
    by_name = {a.name: a for a in dp.ARMS}
    for name in ("wizard", "wizard+dark_theme", "wizard+black_and_white",
                 "wizard+challenging_font"):
        assert by_name[name].flow_marker == "wizard-next-1"
        assert by_name[name].layout == "wizard"
    assert by_name["single_page"].flow_marker is None      # wizard marker must vanish
    assert by_name["single_page"].layout == "single_page"
    assert by_name["sectioned"].flow_marker == "reveal-details"
    assert by_name["sectioned"].layout == "sectioned"


def test_arm_style_fingerprints_separate_themes_from_default():
    """The three theme arms fingerprint their theme (and none of them
    fingerprints the default look); the baseline and protocol arms share the
    default look by design, so their fingerprints coincide."""
    by_name = {a.name: a for a in dp.ARMS}
    default = by_name["wizard"].style_fingerprint
    assert default == "#1095c1"
    assert by_name["single_page"].style_fingerprint == default
    assert by_name["sectioned"].style_fingerprint == default
    theme_fps = [by_name[n].style_fingerprint
                 for n in ("wizard+dark_theme", "wizard+black_and_white",
                           "wizard+challenging_font")]
    assert len(set(theme_fps)) == 3
    for fp in theme_fps:
        assert fp != default
    # The three theme arms must fingerprint their theme, not the default look.
    assert by_name["wizard+dark_theme"].style_fingerprint == "#121212"
    assert by_name["wizard+black_and_white"].style_fingerprint == "#000000"
    assert by_name["wizard+challenging_font"].style_fingerprint == "Brush Script MT"


# -- held-out binding selection ---------------------------------------------


def test_drift_bindings_are_deterministic_and_full_pool():
    first = dp.drift_bindings()
    assert dp.drift_bindings() == first
    assert len(first) == 5
    titles = [b["title"] for b in first]
    assert len(set(titles)) == 5  # no repeats
    assert {b["title"] for b in first} == {b["title"] for b in GATE_BINDING_POOL}


def test_drift_seed0_rotation_is_the_hash_rotation():
    """Seed 0 selects the sha256 rotation of the pool under the drift
    namespace: start index 3 -> Delta Audit first. Guards against the
    namespace string silently changing."""
    digest = hashlib.sha256(f"{dp.DRIFT_SEED_NAMESPACE}0".encode()).digest()
    start = int.from_bytes(digest[:8], "big") % len(GATE_BINDING_POOL)
    expected = [GATE_BINDING_POOL[(start + i) % len(GATE_BINDING_POOL)]["title"]
                for i in range(5)]
    assert dp.DRIFT_SEED_NAMESPACE == "guiexp:drift:"
    assert [b["title"] for b in dp.drift_bindings()] == expected
    assert expected[0] == "Delta Audit"


def test_drift_rotation_differs_from_gate_rotation():
    """Different namespace, different selection: the probe must not replay the
    gate's exact ordering."""
    assert ([b["title"] for b in dp.drift_bindings()]
            != [b["title"] for b in heldout_bindings(5)])


def test_drift_bindings_disjoint_from_instance_pool():
    """Held-out bindings are never a trajectory instance (the pool itself is
    disjoint from env.INSTANCE_POOL)."""
    for b in dp.drift_bindings():
        for instance in INSTANCE_POOL:
            assert b != instance


def test_drift_bindings_exclude_and_k():
    exclude = GATE_BINDING_POOL[0]
    got = dp.drift_bindings(4, exclude=exclude)
    assert len(got) == 4
    assert all(b != exclude for b in got)
    try:
        dp.drift_bindings(5, exclude=exclude)
    except ValueError:
        pass
    else:
        raise AssertionError("k=5 with one excluded must not fit the 4 left")


# -- outcome coding -----------------------------------------------------------


def _truth(before=0, after=0, exists=False, dup=False, correct=False):
    return {"events_before": before, "events_after": after,
            "record_exists": exists, "record_duplicated": dup,
            "record_correct": correct, "mismatched_fields": []}


def _prog(returned=True, raised=False):
    return {"returned": returned, "raised": raised,
            "exception_class": "none" if not raised else "RuntimeError",
            "exception": "", "error": None}


def test_outcome_pass():
    truth = _truth(before=2, after=3, exists=True, correct=True)
    assert dp.outcome_of(_prog(returned=True), truth) == "PASS"


def test_outcome_loud_on_raise_with_clean_state():
    truth = _truth(before=2, after=2, exists=False)
    assert dp.outcome_of(_prog(raised=True), truth) == "LOUD"


def test_outcome_silent_when_return_lies_over_missing_record():
    truth = _truth(before=2, after=2, exists=False)
    assert dp.outcome_of(_prog(returned=True), truth) == "SILENT_WRONG"


def test_outcome_silent_when_return_lies_over_wrong_record():
    truth = _truth(before=2, after=3, exists=True, correct=False)
    assert dp.outcome_of(_prog(returned=True), truth) == "SILENT_WRONG"


def test_outcome_loud_on_false_return_with_clean_state():
    """Returned-but-False with an untouched app: the signal (failure) and the
    state agree, so the caller can fall back -- LOUD, exactly like the old
    probe's coding (loud is any accurate failure signal, raise or False)."""
    truth = _truth(before=2, after=2, exists=False)
    assert dp.outcome_of(_prog(returned=False), truth) == "LOUD"


def test_outcome_silent_when_raise_wrote_a_record():
    truth = _truth(before=2, after=3, exists=True, correct=False)
    assert dp.outcome_of(_prog(raised=True), truth) == "SILENT_WRONG"


def test_outcome_silent_on_duplicate():
    truth = _truth(before=2, after=4, exists=True, dup=True, correct=True)
    assert dp.outcome_of(_prog(returned=True), truth) == "SILENT_WRONG"


def test_outcome_silent_on_two_new_events():
    truth = _truth(before=2, after=4, exists=True, correct=True)
    assert dp.outcome_of(_prog(returned=True), truth) == "SILENT_WRONG"


# -- rollup -------------------------------------------------------------------


def test_counts_and_hazard_maths():
    runs = [{"outcome": "PASS"}, {"outcome": "PASS"}, {"outcome": "LOUD"},
            {"outcome": "SILENT_WRONG"}, {"outcome": "PASS"}]
    assert dp._counts(runs) == {"pass": 3, "loud": 1, "silent": 1}
    per_arm = {
        "wizard": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+dark_theme": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+black_and_white": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+challenging_font": {"pass": 5, "loud": 0, "silent": 0},
        "single_page": {"pass": 0, "loud": 5, "silent": 0},
        "sectioned": {"pass": 0, "loud": 5, "silent": 0},
    }
    haz = dp._hazard(per_arm)
    assert haz["break_prob_per_change_event"] == 0.4
    assert haz["silent_prob_per_change_event"] == 0.0
    assert haz["per_use_hazard_by_change_rate"]["0.02"] == 0.008


def test_comparison_against_old_probe_table():
    """The embedded old-probe reference is the paper's tab:drift exactly."""
    assert dp.OLD_PROBE_RESULT == {
        "wizard": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+dark_theme": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+black_and_white": {"pass": 5, "loud": 0, "silent": 0},
        "wizard+challenging_font": {"pass": 5, "loud": 0, "silent": 0},
        "single_page": {"pass": 0, "loud": 5, "silent": 0},
        "sectioned": {"pass": 0, "loud": 5, "silent": 0},
    }
    same = dp._comparison(dp.OLD_PROBE_RESULT)
    assert all(row["same_outcome_mix"] for row in same["arms"].values())


def test_ground_truth_matches_on_all_six_fields():
    binding = GATE_BINDING_POOL[0]
    good = {f: binding[f] for f in
            ("title", "date", "description", "location", "url", "invitees")}
    other = dict(good, title="Unrelated", date="2020-01-01", id=1)
    wrong = dict(good, invitees="Nobody")
    before = [other]  # a pre-existing, non-matching event
    after_ok = [other, dict(good, id=2)]
    after_wrong = [other, dict(wrong, id=2)]
    ok = dp._ground_truth(before, after_ok, binding)
    bad = dp._ground_truth(before, after_wrong, binding)
    assert ok["record_correct"] is True
    assert ok["record_duplicated"] is False
    assert ok["events_before"] == 1 and ok["events_after"] == 2
    assert bad["record_correct"] is False
    assert bad["mismatched_fields"] == ["invitees"]
    assert dp._ground_truth(before, before, binding)["record_exists"] is False
