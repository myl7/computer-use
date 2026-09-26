"""Unit tests for the five prompt conditions (no emulator, no LLM)."""

from __future__ import annotations

import pytest

from guiexp_android.conditions import (
    COMMON_TEMPLATE,
    CONDITIONS,
    FAMILIES,
    FLOOR_PROMPT,
    TOLD_LEGACY,
    TOLD_PROCEDURE,
    MID_STEPS,
    SKILL_DOC,
    approx_tokens,
    build_prompt,
)
from guiexp_android import android_env

# Sample instance values, drawn with the real generator so the "no values in
# told/mid/skill" rules are checked against actual task parameters.
SAMPLE_PARAMS = {family: android_env.instance_params(family, seed)
                 for family in FAMILIES for seed in (0, 1, 2, 3)}

GOALS = {
    "ContactsAddContact": "Create a new contact for Isla Hernandez.",
    "SimpleCalendarAddOneEvent": "In Simple Calendar Pro, create a calendar event",
    "MarkorCreateNote": "Create a new note in Markor named",
    "MarkorDeleteNote": "Delete the note in Markor named",
    "OsmAndFavorite": "Add a favorite location marker for Schaan, Liechtenstein",
    "OsmAndMarker": "Add a location marker for Schaan, Liechtenstein",
    "FilesMoveFile": "Move the file holiday_photos.jpg from DCIM",
}


def instance_value_strings(family: str) -> list[str]:
    """String parameter values of the family's instances (values only)."""
    values = []
    for params in [SAMPLE_PARAMS[family]]:
        for key, value in params.items():
            if key in ("row_objects", "noise_row_objects"):  # calendar internals
                continue
            text = str(value)
            if len(text) >= 4:  # skip tiny numerics like day/hour/duration
                values.append(text)
    return values


@pytest.mark.parametrize("family", FAMILIES)
def test_discover_is_goal_only(family):
    prompt = build_prompt("discover", family, GOALS[family])
    assert GOALS[family] in prompt
    assert COMMON_TEMPLATE.splitlines()[0] in prompt
    for absent in ("Perform exactly", "skill entry", "structure of this flow"):
        assert absent not in prompt


def test_floor_has_no_task():
    prompt = build_prompt("floor", "ContactsAddContact", "whatever goal")
    assert prompt == FLOOR_PROMPT
    assert "goal_status" in prompt and "complete" in prompt


@pytest.mark.parametrize("family", FAMILIES)
def test_told_has_procedure_without_values(family):
    text = TOLD_PROCEDURE[family]
    prompt = build_prompt("told", family, GOALS[family])
    assert text in prompt
    # exact, ordered procedure
    assert "Perform exactly" in text and "in this order" in text
    # but never the instance's parameter values
    for value in instance_value_strings(family):
        assert value not in text, f"told leaks instance value {value!r}"
        assert value not in MID_STEPS[family]
        assert value not in SKILL_DOC[family]


def test_told_contacts_field_button_granularity():
    text = TOLD_PROCEDURE["ContactsAddContact"]
    for label in ("First name", "Last name", "Phone", "Create contact", "Save"):
        assert label in text, f"contacts told must name {label!r}"
    assert "Don't allow" in text  # the real first-run dialog's button


def test_told_calendar_field_button_granularity():
    text = TOLD_PROCEDURE["SimpleCalendarAddOneEvent"]
    for label in ("month\n   grid", "New Event", "Title", "Location",
                  "Description", "Select time", "OK", "check mark", "24-hour"):
        assert label in text, f"calendar told must name {label!r}"
    # the duration is end-time-minus-start, as the validator checks
    assert "end row" in text and "plus the number of minutes" in text
    # the corrected path reaches the date through the month grid, so the date
    # picker is explicitly ruled out rather than walked (proposal section 1)
    assert "the date picker is never needed" in text
    assert "Previous month" not in text and "Next month" not in text


def test_told_markor_field_button_granularity():
    text = TOLD_PROCEDURE["MarkorCreateNote"]
    for label in ("New File dialog", "Name", "OK", "editor", "Save icon",
                  "extension"):
        assert label in text, f"markor told must name {label!r}"
    # the corrected path saves with the toolbar icon; Back needed two presses
    assert "navigate_back" not in text
    assert "you do not have to leave the editor" in text


def test_told_legacy_keeps_the_superseded_texts():
    """Runs recorded before 2026-09-09 saw these; keep them readable."""
    assert set(TOLD_LEGACY) == {"SimpleCalendarAddOneEvent", "MarkorCreateNote"}
    for family, legacy in TOLD_LEGACY.items():
        assert legacy != TOLD_PROCEDURE[family]
        assert "Perform exactly" in legacy
    assert "Previous month" in TOLD_LEGACY["SimpleCalendarAddOneEvent"]
    assert "navigate_back" in TOLD_LEGACY["MarkorCreateNote"]


def test_told_osmand_pair_names_the_one_control_that_differs():
    favorite = TOLD_PROCEDURE["OsmAndFavorite"]
    marker = TOLD_PROCEDURE["OsmAndMarker"]
    for text in (favorite, marker):
        for label in ("Search", "Add to favorites", "Mark", "Directions"):
            assert label in text, f"osmand told must name {label!r}"
    # each names its own control as the one to press and the other as the trap
    flat_favorite = " ".join(favorite.split())
    flat_marker = " ".join(marker.split())
    assert "Tap the Add to favorites button" in flat_favorite
    assert "do not tap Mark" in flat_favorite
    assert "Tap the Mark button" in flat_marker
    assert "do not tap Add to favorites" in flat_marker
    # and the marker family has no naming dialog to confirm
    assert "no naming dialog opens" in flat_marker


def test_told_files_move_names_the_hidden_clipboard_path():
    text = TOLD_PROCEDURE["FilesMoveFile"]
    # the storage row is named after the emulator image, so the told text
    # names the sdk_gphone prefix rather than the goal template's x86_64 name
    for label in ("Show roots", "sdk_gphone", "long_press",
                  "More options", "Cut", "Paste"):
        assert label in text, f"files told must name {label!r}"
    # the invisible intermediate state is stated, not left to be discovered
    assert "Nothing on screen now says a file is waiting" in text


def test_the_osmand_pair_has_one_mid_description():
    """The pair's point: a mid text written for one is exactly right for the
    other, so mid provably carries no information about which to run."""
    assert MID_STEPS["OsmAndFavorite"] == MID_STEPS["OsmAndMarker"]
    assert TOLD_PROCEDURE["OsmAndFavorite"] != TOLD_PROCEDURE["OsmAndMarker"]


@pytest.mark.parametrize("family", FAMILIES)
def test_mid_is_structure_only(family):
    text = MID_STEPS[family]
    prompt = build_prompt("mid", family, GOALS[family])
    assert text in prompt
    assert "The structure of this flow is known" in prompt
    # no controls, no labels: only generic save/picker words, no button names
    for label in ("Save button", "OK button", "Previous month", "Next month",
                  "Create contact", "New Event", "CANCEL", "FOLDER",
                  "navigate_back", "First name"):
        assert label not in text, f"mid names the control {label!r}"
    # and no field ORDER (that is told's job)
    assert "in this order" not in text


@pytest.mark.parametrize("family", FAMILIES)
def test_skill_doc_is_compressed_family_level(family):
    doc = SKILL_DOC[family]
    prompt = build_prompt("skill", family, GOALS[family])
    assert doc in prompt and "skill entry from memory" in prompt
    # ~200-260 tokens by the shared ~4 chars/token estimate
    assert 200 <= approx_tokens(doc) <= 260, approx_tokens(doc)
    # honest cautions present
    assert "Cautions:" in doc
    # no selectors, no element indexes, no screen order
    for absent in ("UI element", '"index"', "bid", "in this order",
                  "Perform exactly"):
        assert absent not in doc, f"skill doc contains {absent!r}"
    # family-level trigger named
    assert doc.startswith("Skill:")


def test_condition_ordering_and_ladder_tokens():
    assert CONDITIONS == ("discover", "told", "mid", "skill", "doc", "floor")
    for family in FAMILIES:
        sizes = [approx_tokens(build_prompt(c, family, GOALS[family]))
                 for c in ("discover", "told", "mid", "skill")]
        assert sizes[0] < sizes[1], (family, sizes)  # told adds knowledge
