"""Unit tests: each condition's prompt contains/lacks exactly the right pieces."""

from guiexp.conditions import (
    CONDITIONS,
    FLOOR_PROMPT,
    SKILL_DOC,
    approx_tokens,
    build_prompt,
    mid_steps,
    told_procedure,
)
from guiexp.env import INSTANCE_POOL, instance_for_seed

EVENT = instance_for_seed(0)
BASE = "http://localhost:59999"
FIELDS = ("title", "date", "description", "location", "url", "invitees")


def test_condition_set():
    assert CONDITIONS == ("discover", "told", "mid", "skill", "floor")


def test_instance_binding_is_seed_deterministic():
    assert instance_for_seed(0) == instance_for_seed(0)
    assert instance_for_seed(0) in INSTANCE_POOL
    assert len({instance_for_seed(s)["title"] for s in range(40)}) > 1


def test_discover_is_goal_and_url_only():
    prompt = build_prompt("discover", "wizard", EVENT, BASE)
    assert BASE in prompt
    for field in FIELDS:
        assert EVENT[field] in prompt
    # No interface knowledge leaks in.
    for absent in (
        "create_event",
        "Next",
        "More details",
        "Submit",
        "Create button",
        "screen",
        "Skill",
        "skill entry",
    ):
        assert absent not in prompt, absent


def test_told_has_procedure_without_parameter_values():
    prompt = build_prompt("told", "wizard", EVENT, BASE)
    procedure = told_procedure("wizard", BASE)
    assert procedure in prompt
    # Procedure is at screen/field/button granularity.
    assert "three screens" in procedure
    assert "screen 1" in procedure and "screen 3" in procedure
    assert "Title" in procedure and "Invitees" in procedure
    assert "Next button" in procedure and "Create button" in procedure
    # But it never contains the instance's parameter values.
    for field in FIELDS:
        assert EVENT[field] not in procedure
    # The values are still in the prompt, once, in the shared goal block.
    assert EVENT["title"] in prompt


def test_told_sectioned_and_single_page():
    sectioned = told_procedure("sectioned", BASE)
    assert "More details" in sectioned
    single = told_procedure("single_page", BASE)
    assert "All six fields are on one screen" in single
    for procedure in (sectioned, single):
        for field in FIELDS:
            assert EVENT[field] not in procedure


def test_mid_is_structure_without_controls():
    prompt = build_prompt("mid", "wizard", EVENT, BASE)
    assert mid_steps("wizard") in prompt
    assert "three screens" in prompt
    assert "title and date first" in prompt  # field order is given
    assert "controls are not" in prompt
    # No controls, labels or selectors anywhere in the prompt.
    for absent in ("Next", "More details", "Submit", "Create button", "#", "wizard-next"):
        assert absent not in prompt, absent


def test_skill_doc_is_compressed_and_selector_free():
    assert 200 <= approx_tokens(SKILL_DOC) <= 300
    for absent in (
        "#",
        "http",
        "/calendar",
        "wizard-next",
        "three screens",
        "1 of 3",
        "screen 1",
    ):
        assert absent not in SKILL_DOC, absent
    # Family-level content: six fields incl. the invitees caution, the honest
    # layout-variety note, and the drive-the-UI / date-format cautions.
    for present in (
        "invitees",
        "invitees is one string of names",
        "layout differs between installations",
        "drive the browser UI",
        "date field expects the format",
    ):
        assert present in SKILL_DOC, present
    prompt = build_prompt("skill", "wizard", EVENT, BASE)
    assert SKILL_DOC in prompt
    assert "three screens" not in prompt  # skill does not reveal the layout


def test_floor_has_no_task():
    prompt = build_prompt("floor", "wizard", EVENT, BASE)
    assert prompt == FLOOR_PROMPT
    assert BASE not in prompt
    for field in FIELDS:
        assert EVENT[field] not in prompt
    assert "calendar" not in prompt
