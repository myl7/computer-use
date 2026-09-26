"""Unit tests for the told admissibility gate (docs/android-told-diagnosis.md
section 7.2, rules R1 and R2).

Zero LLM calls and zero emulator use: the scripted replay runs against a fake
device that serves canned a11y dumps and records the actions, and the
minimality half reads recorded-shaped trajectories written into tmp_path. The
real ``experimental-results`` tree is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guiexp_android import told_check
from guiexp_android.conditions import FAMILIES, TOLD_PROCEDURE

MODEL = "z-ai/glm-5.3-flash"


# --------------------------------------------------------------- fake device


def element(index, text="", hint="", description="", editable=False,
            clickable=True, long_clickable=False, scrollable=False):
    return {"index": index, "text": text, "hint": hint, "description": description,
            "tooltip": "", "editable": editable, "clickable": clickable,
            "long_clickable": long_clickable, "scrollable": scrollable,
            "focusable": False, "selected": False, "checked": False}


class FakeDevice:
    """Serves one canned screen per action and records what was done."""

    def __init__(self, screens: list[list[dict]]):
        self._screens = screens
        self._at = 0
        self.actions: list[tuple] = []

    def elements(self) -> list[dict]:
        return self._screens[min(self._at, len(self._screens) - 1)]

    def _advance(self, record) -> None:
        self.actions.append(record)
        self._at += 1

    def open_app(self, app_name):
        self._advance(("open_app", app_name))

    def click(self, index=None, **_kw):
        self._advance(("click", index))

    def long_press(self, index=None, **_kw):
        self._advance(("long_press", index))

    def input_text(self, text, index=None, **_kw):
        self._advance(("input_text", index, text))

    def navigate_back(self):
        self._advance(("navigate_back",))

    def scroll(self, direction="down"):
        self._advance(("scroll", direction))


class FakeRunner:
    """ProgramRunner stand-in: runs the program on a fake device and returns a
    caller-chosen oracle verdict per binding.

    ``screens`` may be a callable taking the binding, so a canned screen can
    carry the binding's own file name the way the real phone would.
    """

    def __init__(self, screens, passes: list[bool]):
        self._screens = screens
        self._passes = list(passes)
        self.devices: list[FakeDevice] = []

    def run(self, program, binding, family, judge_params=None, **_kw):
        screens = self._screens(binding) if callable(self._screens) else self._screens
        device = FakeDevice([list(s) for s in screens])
        self.devices.append(device)
        error = None
        try:
            program(device, dict(binding))
        except Exception as exc:  # noqa: BLE001 - the failure shape is data
            error = f"{type(exc).__name__}: {exc}"
        passed = bool(self._passes.pop(0)) and error is None
        return {"passed": passed, "error": error, "reward": 1.0 if passed else 0.0,
                "device": device}


def write_discover_run(root: Path, model: str, family: str, seed: int,
                       steps: int, success: bool) -> Path:
    """A t12_grid-shaped discover trajectory (final record is what we read)."""
    directory = root / model.replace("/", "_") / f"discover__{family}__s{seed}"
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {"step": i, "action_raw": "", "action": '{"action_type": "click", "index": 1}',
         "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 0.0},
         "obs_meta": {"url": "com.example/.A", "screenshot_file": None,
                      "ax_chars": 100, "last_action_error": None}}
        for i in range(1, steps + 1)
    ]
    records.append({
        "success": success, "total_tokens": 100 * steps, "total_cost_usd": 0.0,
        "condition": "discover", "family": family, "seed": seed, "model": model,
        "obs_mode": "screenshot+ax", "task_id": f"{family}__discover__s{seed}",
        "steps": steps, "model_calls": steps, "record_type": "final",
    })
    path = directory / "trajectory.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


# ------------------------------------------------- A. the script table


@pytest.mark.parametrize("family", FAMILIES)
def test_every_family_has_a_scripted_told_procedure(family):
    steps = told_check.TOLD_SCRIPTS[family]
    assert steps, family
    assert steps[0]["op"] == "open_app"
    for step in steps:
        assert step["op"] in {"open_app", "click", "long_press", "input_text",
                              "navigate_back", "scroll"}
        if step["op"] in {"click", "long_press", "input_text"}:
            assert step.get("find"), step
        if step["op"] == "input_text":
            assert "value" in step, step


@pytest.mark.parametrize("family", FAMILIES)
def test_script_types_only_binding_derived_values(family):
    """No instance value may be hard-coded into a step; every typed string
    resolves out of the binding, exactly as told's text promises."""
    from guiexp_android import android_env, compiler

    binding = compiler.params_to_binding(family, android_env.instance_params(family, 0))
    for step in told_check.TOLD_SCRIPTS[family]:
        if step["op"] != "input_text":
            continue
        assert isinstance(step["value"], dict), step
        value = told_check.resolve(step["value"], binding)
        assert str(value).strip(), step


def test_step_counts_match_the_corrected_told_texts():
    """The two rewrites target the cheapest successful discover run:
    14 steps for the calendar, 6 for markor (proposal sections 1 and 2)."""
    assert told_check.told_step_count("SimpleCalendarAddOneEvent") == 14
    assert told_check.told_step_count("MarkorCreateNote") == 6
    assert told_check.told_step_count("ContactsAddContact") == 6


def test_new_family_step_counts_sit_in_the_survey_ranges():
    # docs/harder-families-and-routers.md A2.2 (8-15 each) and A2.3 (10-18)
    assert told_check.told_step_count("OsmAndFavorite") <= 15
    assert told_check.told_step_count("OsmAndMarker") <= 15
    assert 10 <= told_check.told_step_count("FilesMoveFile") <= 18
    # the marker family is one step shorter: it has no naming dialog
    assert (told_check.told_step_count("OsmAndMarker")
            < told_check.told_step_count("OsmAndFavorite"))


def test_optional_steps_are_excluded_from_the_counted_total():
    family = "MarkorDeleteNote"
    assert told_check.told_step_count(family) < told_check.told_step_count_max(family)
    assert any(s.get("optional") for s in told_check.TOLD_SCRIPTS[family])


# ------------------------------------------------- B. value resolution


def test_resolve_transforms_and_computed_values():
    assert told_check.resolve({"field": "name", "transform": "first_word"},
                              {"name": "Ada Lovelace"}) == "Ada"
    assert told_check.resolve({"field": "name", "transform": "last_word"},
                              {"name": "Ada Lovelace"}) == "Lovelace"
    assert told_check.resolve({"field": "file_name", "transform": "basename"},
                              {"file_name": "trip.notes.md"}) == "trip.notes"
    assert told_check.resolve({"field": "file_name", "transform": "extension"},
                              {"file_name": "trip.notes.md"}) == ".md"
    assert told_check.resolve("Cut", {}) == "Cut"


def test_end_time_is_start_plus_duration_not_a_field():
    binding = {"hour": 9, "duration_mins": 45, "day": 27}
    assert told_check.resolve({"compute": "start_hour"}, binding) == "9"
    assert told_check.resolve({"compute": "end_hour"}, binding) == "9"
    assert told_check.resolve({"compute": "end_minute"}, binding) == "45"
    binding = {"hour": 23, "duration_mins": 90, "day": 1}
    assert told_check.resolve({"compute": "end_hour"}, binding) == "0"
    assert told_check.resolve({"compute": "end_minute"}, binding) == "30"


def test_search_head_handles_both_osmand_spellings():
    assert told_check.resolve({"compute": "search_head"},
                              {"location": "Schaan, Liechtenstein"}) == "Schaan"
    assert told_check.resolve({"compute": "search_head"},
                              {"location": "47.1663432, 9.5103085"}) == "47.1663432"


# ------------------------------------------------- C. the scripted program


def markor_create_screens(extension_shown: str = ".txt",
                          dialog_screens: int = 2) -> list[list[dict]]:
    """One screen per action, in the order the script produces them: home,
    the file list after open_app, the New File dialog after the + button, the
    editor after OK. The dialog is on screen for one action more when the
    extension field also has to be edited (the trailing screen is reused for
    every later action)."""
    home = [element(0, text="Home")]
    listing = [element(0, text="Markor"),
               element(3, description="Create a new file or folder")]
    dialog = [element(1, text="my_note", editable=True),
              element(2, text=extension_shown, editable=True),
              element(5, text="OK")]
    editor = [element(7, editable=True), element(9, description="Save")]
    return [home, listing] + [dialog] * dialog_screens + [editor]


def test_script_program_drives_the_named_controls_in_order():
    device = FakeDevice(markor_create_screens())
    program = told_check.script_program("MarkorCreateNote")
    assert program(device, {"file_name": "trip.txt", "text": "pack the charger"})
    ops = [a[0] for a in device.actions]
    # 6 steps: the extension field already reads .txt, so that step is skipped
    assert ops == ["open_app", "click", "input_text", "click", "input_text", "click"]
    assert device.actions[2] == ("input_text", 1, "trip")  # base name, no extension
    assert device.actions[4] == ("input_text", 7, "pack the charger")
    assert len(device.actions) == told_check.told_step_count("MarkorCreateNote")


def test_the_extension_step_fires_only_when_the_field_disagrees():
    """Retyping a field with what it already holds is a state-preserving, and
    therefore dead, step (diagnosis section 7.2, R1)."""
    device = FakeDevice(markor_create_screens(dialog_screens=3))
    told_check.script_program("MarkorCreateNote")(
        device, {"file_name": "trip.md", "text": "x"})
    typed = [a for a in device.actions if a[0] == "input_text"]
    assert (".md" in [t[2] for t in typed]), device.actions
    assert len(device.actions) == told_check.told_step_count_max("MarkorCreateNote")


def test_an_optional_step_that_finds_nothing_is_skipped():
    """MarkorDeleteNote's scroll only fires when the list actually scrolls."""
    rows = [element(0, text="Markor"),
            element(2, description="File note.txt ", long_clickable=True),
            element(4, description="Delete"), element(6, text="OK")]
    device = FakeDevice([rows] * 8)  # nothing scrollable on any screen
    told_check.script_program("MarkorDeleteNote")(device, {"file_name": "note.txt"})
    assert [a[0] for a in device.actions] == ["open_app", "long_press", "click", "click"]
    assert len(device.actions) == told_check.told_step_count("MarkorDeleteNote")


def test_a_required_step_that_finds_nothing_raises():
    device = FakeDevice([[element(0, text="Home")]])
    program = told_check.script_program("FilesMoveFile")
    with pytest.raises(ValueError, match="told step 2"):
        program(device, {"file_name": "a.jpg", "source_folder": "DCIM",
                         "destination_folder": "Download"})


def test_nth_picks_the_second_match_not_the_first():
    elements = [element(1, editable=True), element(2, editable=True)]
    assert told_check.matching_indexes(elements, {"editable": True}, {}) == [1, 2]
    step = {"op": "click", "find": {"editable": True, "nth": 1}}
    assert told_check.resolve_step_index(FakeDevice([elements]), step, {}) == 2


# ------------------------------------------------- D. the recorded grid


def test_cheapest_successful_discover_ignores_failed_and_longer_runs(tmp_path):
    family = "MarkorCreateNote"
    write_discover_run(tmp_path, MODEL, family, 0, steps=9, success=True)
    write_discover_run(tmp_path, MODEL, family, 1, steps=6, success=True)
    write_discover_run(tmp_path, MODEL, family, 2, steps=4, success=False)
    cheapest = told_check.cheapest_successful_discover(family, MODEL, tmp_path)
    assert cheapest["seed"] == 1 and cheapest["steps"] == 6


def test_no_recorded_run_gives_no_baseline(tmp_path):
    assert told_check.cheapest_successful_discover("OsmAndMarker", MODEL, tmp_path) is None
    assert told_check.discover_runs("OsmAndMarker", MODEL, tmp_path) == []


def test_the_real_grid_is_only_read(tmp_path):
    """The tool must never write under experimental-results."""
    before = sorted(p.name for p in told_check.GRID_ROOT.glob("*")) if told_check.GRID_ROOT.exists() else []
    told_check.discover_runs("MarkorCreateNote", MODEL)
    after = sorted(p.name for p in told_check.GRID_ROOT.glob("*")) if told_check.GRID_ROOT.exists() else []
    assert before == after


# ------------------------------------------------- E. the verdict


def test_pass_needs_both_the_oracle_and_the_step_count(tmp_path):
    family = "MarkorCreateNote"
    write_discover_run(tmp_path, MODEL, family, 1, steps=6, success=True)
    runner = FakeRunner(screens=[[element(0)]], passes=[True, True, True])
    result = told_check.verdict(
        family, MODEL,
        told_check.cheapest_successful_discover(family, MODEL, tmp_path),
        replay=[{"seed": s, "passed": True, "error": None} for s in (0, 1, 2)],
    )
    assert result["status"] == told_check.PASS
    assert result["told_steps"] == 6 and result["discover_steps"] == 6
    assert result["reasons"] == []
    assert runner.devices == []  # verdict itself touches no device


def test_one_step_too_many_fails_even_with_a_clean_replay(tmp_path):
    family = "MarkorCreateNote"
    write_discover_run(tmp_path, MODEL, family, 1, steps=5, success=True)
    result = told_check.verdict(
        family, MODEL,
        told_check.cheapest_successful_discover(family, MODEL, tmp_path),
        replay=[{"seed": 0, "passed": True, "error": None}],
    )
    assert result["status"] == told_check.FAIL
    assert not result["minimal"] and result["replay_passed"]
    assert "more than the cheapest" in result["reasons"][0]


def test_one_failed_seed_fails_even_at_the_minimal_length(tmp_path):
    family = "MarkorCreateNote"
    write_discover_run(tmp_path, MODEL, family, 1, steps=6, success=True)
    result = told_check.verdict(
        family, MODEL,
        told_check.cheapest_successful_discover(family, MODEL, tmp_path),
        replay=[{"seed": 0, "passed": True, "error": None},
                {"seed": 1, "passed": False, "error": "oracle mismatch"}],
    )
    assert result["status"] == told_check.FAIL
    assert result["minimal"] and result["replay_passed"] is False
    assert "seed 1" in result["reasons"][0] and "oracle mismatch" in result["reasons"][0]


def test_without_a_replay_the_verdict_is_pending(tmp_path):
    family = "MarkorCreateNote"
    write_discover_run(tmp_path, MODEL, family, 1, steps=6, success=True)
    result = told_check.verdict(
        family, MODEL,
        told_check.cheapest_successful_discover(family, MODEL, tmp_path), replay=None)
    assert result["status"] == told_check.PENDING
    assert result["replay_passed"] is None


def test_a_family_with_no_discover_baseline_cannot_pass(tmp_path):
    result = told_check.verdict(
        "OsmAndMarker", MODEL,
        told_check.cheapest_successful_discover("OsmAndMarker", MODEL, tmp_path),
        replay=[{"seed": 0, "passed": True, "error": None}])
    assert result["status"] == told_check.FAIL
    assert "no successful discover run" in result["reasons"][0]


# ------------------------------------------------- F. end to end, mocked


def markor_delete_screens(binding: dict) -> list[list[dict]]:
    """Canned screens carrying this binding's own file name, so the scripted
    long-press has to match the row by title rather than by position."""
    # a Markor file row carries no text, only the a11y label "File <name> ";
    # the decoy's label contains the target name, so a substring match on it
    # would press the wrong row
    listing = [element(0, text="Markor"),
               element(1, description=f"File a0pf_{binding['file_name']}.md ",
                       long_clickable=True),
               element(2, description=f"File {binding['file_name']} ",
                       long_clickable=True)]
    selection = listing + [element(4, description="Delete")]
    confirm = [element(6, text="OK"), element(7, text="CANCEL")]
    return [[element(0, text="Home")], listing, selection, confirm, listing]


def test_check_family_replays_every_seed_and_passes(tmp_path):
    family = "MarkorDeleteNote"
    write_discover_run(tmp_path, MODEL, family, 0, steps=7, success=True)
    runner = FakeRunner(markor_delete_screens, passes=[True, True, True])
    result = told_check.check_family(family, MODEL, seeds=(0, 1, 2), runner=runner,
                                     grid_root=tmp_path)
    assert result["status"] == told_check.PASS
    assert len(result["replay"]) == 3
    assert len(runner.devices) == 3
    # the scripted replay drove the phone, it did not merely count steps
    assert [a[0] for a in runner.devices[0].actions][:2] == ["open_app", "long_press"]


def test_check_family_reports_the_failing_seed(tmp_path):
    family = "MarkorDeleteNote"
    write_discover_run(tmp_path, MODEL, family, 0, steps=7, success=True)
    screens = [[element(0, text="Markor"),
                element(2, description="File x ", long_clickable=True)]] * 5
    runner = FakeRunner(screens, passes=[False, False])  # no row matches the binding
    result = told_check.check_family(family, MODEL, seeds=(0, 1), runner=runner,
                                     grid_root=tmp_path)
    assert result["status"] == told_check.FAIL
    assert len(result["reasons"]) == 2


def test_steps_only_needs_no_runner_at_all(tmp_path):
    write_discover_run(tmp_path, MODEL, "ContactsAddContact", 1, steps=6, success=True)
    result = told_check.check_family("ContactsAddContact", MODEL, grid_root=tmp_path,
                                     steps_only=True)
    assert result["status"] == told_check.PENDING
    assert result["replay"] is None


def test_format_verdict_prints_the_rule_both_sides(tmp_path):
    write_discover_run(tmp_path, MODEL, "ContactsAddContact", 1, steps=6, success=True)
    text = told_check.format_verdict(
        told_check.check_family("ContactsAddContact", MODEL, grid_root=tmp_path,
                                steps_only=True))
    assert "ContactsAddContact" in text and "told   6 steps" in text
    assert "cheapest discover 6 (seed 1)" in text


# ------------------------------------------------- G. text and script agree


@pytest.mark.parametrize("family", FAMILIES)
def test_the_script_only_names_controls_the_told_text_or_a_note_names(family):
    """Every literal label a step matches on is accounted for: it is either in
    the told prose, or the step carries a note saying which control it is.

    Some a11y labels differ from what the screen shows a reader (Markor's
    round + button reports itself as "Create a new file or folder"), and the
    calendar and markor told texts are copied verbatim from
    docs/android-told-prompts-proposal.md, so the prose cannot be bent to fit
    the tree. An unexplained label is still a bug: it means the script drove a
    control the procedure never documented."""
    flat = " ".join(TOLD_PROCEDURE[family].split()).lower()
    for step in told_check.TOLD_SCRIPTS[family]:
        note = " ".join((step.get("note") or "").split()).lower()
        for key, value in (step.get("find") or {}).items():
            if key not in ("text", "contains") or not isinstance(value, str):
                continue
            if value.replace(".", "").isdigit():
                continue
            assert value.lower() in flat or note, (family, value)


# ------------------------------------------- E. late screens versus wrong labels


class LateDevice:
    """Serves empty screens for the first ``blank`` reads, then the real one.

    This is what a mid-transition a11y dump looks like: ``ProgramDevice``
    reads the tree with ``wait_to_stabilize=False``, so a screen that is still
    being built comes back empty.
    """

    def __init__(self, rows, blank: int):
        self._rows = rows
        self._blank = blank
        self.reads = 0

    def elements(self):
        self.reads += 1
        return [] if self.reads <= self._blank else self._rows


def test_a_late_control_is_found_on_a_later_read():
    rows = [element(3, hint="First name", editable=True)]
    device = LateDevice(rows, blank=told_check.RESOLVE_ATTEMPTS - 1)
    step = {"op": "input_text", "find": {"hint": "First name"}}
    assert told_check.resolve_step_index(device, step, {}) == 3
    assert device.reads == told_check.RESOLVE_ATTEMPTS


def test_a_wrong_label_is_still_absent_after_every_read():
    rows = [element(3, hint="Given name", editable=True)]
    device = LateDevice(rows, blank=0)
    step = {"op": "input_text", "find": {"hint": "First name"}}
    assert told_check.resolve_step_index(device, step, {}) is None
    assert device.reads == told_check.RESOLVE_ATTEMPTS
