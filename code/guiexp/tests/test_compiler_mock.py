"""End-to-end compile -> gate -> deploy, offline at the model level.

Real app + real Chromium, but every model call is deterministic (MockCompiler
for the compile step, MockExtractor for deployment extraction), so these
tests make ZERO network LLM calls -- the only HTTP traffic is the local
OpenApps server. The source trajectory is the checked-in mock discovery run.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp.app_server import AppServer
from guiexp.compiler import (
    MockCompiler,
    build_compile_prompt,
    extract_python_source,
    load_trajectory,
)
from guiexp.deploy_runner import (
    DEPLOY_USES,
    MockExtractor,
    binding_type_errors,
    build_extraction_prompt,
    mock_extract_fields,
    parse_binding_json,
    run_deployment,
    run_single_use,
)
from guiexp.env import FIELDS, instance_for_seed
from guiexp.gate_runner import GATE_BINDING_POOL, heldout_bindings, run_gate
from guiexp.program_runtime import BindingRunner, program_from_source

REPO_ROOT = Path(__file__).resolve().parents[3]
TRAJECTORY = REPO_ROOT / "experimental-results" / "guiexp" / "wizard_discover_s0_mock" / "trajectory.jsonl"


@pytest.fixture(scope="session")
def wizard_server():
    server = AppServer("wizard")
    try:
        yield server.start()
    finally:
        server.stop()


@pytest.fixture(scope="session")
def compiled():
    result = MockCompiler().compile(TRAJECTORY, layout="wizard")
    module, program = program_from_source(result["program_source"])
    return {"result": result, "module": module, "program": program}


# ------------------------------------------------------------------ compile


def test_mock_compiler_emits_parameterized_program(compiled):
    result = compiled["result"]
    assert set(result) >= {"program_source", "usage", "cost_usd"}
    assert result["usage"]["prompt_tokens"] > 0
    assert result["usage"]["completion_tokens"] > 0
    assert result["cost_usd"] >= 0.0
    source = result["program_source"]
    assert "def program(page, binding: dict, base_url: str)" in source
    assert set(compiled["module"].PARAMS_SCHEMA) == set(FIELDS)
    # parameterized, not hard-coded: none of the recorded instance's values
    _steps, final = load_trajectory(TRAJECTORY)
    for value in instance_for_seed(final["seed"]).values():
        assert value not in source


def test_extract_python_source_fence_handling():
    fenced = "Sure.\n```python\ndef program(page, binding, base_url):\n    return True\n```\n"
    assert "def program" in extract_python_source(fenced)
    bare = "```\nX = 1\n```"
    assert extract_python_source(bare).strip() == "X = 1"
    assert extract_python_source("def program():\n    pass\n").startswith("def program")


def test_build_compile_prompt_offline():
    messages = build_compile_prompt(TRAJECTORY, layout="wizard")
    assert [m["role"] for m in messages] == ["system", "user"]
    user = messages[1]["content"]
    # the goal template carries named placeholders for the six fields
    for field in FIELDS:
        assert "{" + field + "}" in user, field
    # ...but never the recorded instance's concrete values
    _steps, final = load_trajectory(TRAJECTORY)
    for value in instance_for_seed(final["seed"]).values():
        assert value not in user, value
    # the compact trajectory view: actions, bids, placeholders, screens
    assert "goto('/calendar/create_event')" in user
    assert "bid=36" in user and "value={title}" in user
    assert "-> /calendar/create_event/step3" in user
    assert "layout: wizard" in user
    assert "def program(page, binding: dict, base_url: str)" in user


# -------------------------------------------------------------------- gate


def test_heldout_bindings_are_deterministic_and_disjoint():
    _steps, final = load_trajectory(TRAJECTORY)
    instance = instance_for_seed(final["seed"])
    first = heldout_bindings(5, exclude=instance, seed=0)
    assert first == heldout_bindings(5, exclude=instance, seed=0)  # deterministic
    assert len(first) == 5
    assert len({b["title"] for b in first}) == 5  # distinct bindings
    for seed in range(8):  # any gate seed stays disjoint from the instance
        assert all(b != instance for b in heldout_bindings(3, exclude=instance, seed=seed))
    with pytest.raises(ValueError):  # pool too small once the instance is excluded
        heldout_bindings(len(GATE_BINDING_POOL) + 1, exclude=instance)


def test_gate_five_heldout_bindings(wizard_server, compiled):
    _steps, final = load_trajectory(TRAJECTORY)
    instance = instance_for_seed(final["seed"])
    bindings = heldout_bindings(5, exclude=instance, seed=0)
    gate = run_gate(compiled["program"], bindings, wizard_server, "wizard")
    assert gate["bindings_total"] == 5
    assert gate["bindings_passed"] == 5, gate["detail"]
    assert all(d["passed"] and not d["error"] for d in gate["detail"])

    # the gate is not vacuous: a program that does nothing must fail it
    def noop(page, binding, base_url):
        page.goto(base_url)

    empty = run_gate(noop, bindings[:1], wizard_server, "wizard")
    assert empty["bindings_passed"] == 0


# ---------------------------------------------------- popup adoption (T1.3)


POPUP_PROGRAM_SRC = '''
PARAMS_SCHEMA = {
    "title": "event title",
    "date": "YYYY-MM-DD",
    "description": "description",
    "location": "location",
    "url": "url",
    "invitees": "one string of names",
}

ADD_EVENT = "a[href='/calendar/create_event/']"

def program(page, binding, base_url):
    """The realistic model-written shape: navigate to the calendar, click the
    real Add Event link (a target="_blank" anchor), wait for the form URL,
    fill, submit. Plain Playwright -- no harness help inside the program."""
    page.goto(base_url + "/calendar")
    link = page.locator(ADD_EVENT).first
    link.scroll_into_view_if_needed()
    link.click()
    page.wait_for_url("**/calendar/create_event**", timeout=8000)
    for key, sel in (("title", "#title"), ("date", "#date"),
                     ("description", "#description"), ("url", "#url"),
                     ("invitees", "#invitees"), ("location", "#location")):
        page.fill(sel, binding[key])
    page.click("button[type=submit]")
    page.wait_for_load_state("domcontentloaded")
    return True
'''


def test_program_click_add_event_link_passes(wizard_server):
    """A program that clicks the real Add Event link and waits for the URL
    must work: the runner adopts the target=_blank popup as the active page,
    the same capability the reactive env has."""
    from guiexp.app_server import AppServer

    server = AppServer("single_page")
    base = server.start()
    try:
        _module, program = program_from_source(POPUP_PROGRAM_SRC)
        with BindingRunner(base, "single_page") as runner:
            outcome = runner.run(program, heldout_bindings(1, seed=0)[0])
    finally:
        server.stop()
    assert outcome["error"] is None, outcome["error"]
    assert outcome["passed"] is True


def test_annotate_trajectory_completes(wizard_server):
    """annotate replays through the env's popup-aware execution and returns
    per-step role/name notes for the checked-in mock discovery trajectory."""
    from guiexp.compiler import annotate_trajectory

    notes = annotate_trajectory(wizard_server, TRAJECTORY)
    assert notes, "no annotations recovered"
    roles = {v.get("role") for v in notes.values()}
    names = {v.get("name") for v in notes.values()}
    assert "textbox" in roles
    assert any("title" in (n or "").lower() for n in names)


# ---------------------------------------------------------------- deployment


def test_mock_extractor_matches_ground_truth():
    for goal, expected in DEPLOY_USES:
        assert mock_extract_fields(goal) == expected, goal[:50]


def test_binding_type_check_and_json_parsing():
    good = dict(DEPLOY_USES[0][1])
    assert binding_type_errors(good) == []
    assert binding_type_errors({**good, "invitees": ["Heidi"]}) != []
    assert binding_type_errors({**good, "title": ""}) != []
    assert binding_type_errors({k: v for k, v in good.items() if k != "url"}) != []
    assert binding_type_errors("not a dict") != []
    fenced = "```json\n" + json.dumps(good) + "\n```"
    assert parse_binding_json(fenced) == good
    assert parse_binding_json("no json here") is None


def test_deployment_mock_chain(wizard_server, compiled):
    summary = run_deployment(
        compiled["program"], DEPLOY_USES[:10], MockExtractor(), "mock", wizard_server, "wizard"
    )
    assert summary["n"] == 10
    assert summary["success_count"] >= 8, [u["error_type"] for u in summary["uses"]]
    assert summary["total_tokens"] > 0
    for use in summary["uses"]:
        assert use["tokens"] > 0  # the per-use extraction price d
        assert use["retries"] in (0, 1)
        assert use["error_type"] in (None, "extraction_json", "type_check", "program_error", "oracle_fail")
        if use["success"]:
            assert use["extracted"] == use["expected"]
            assert use["error_type"] is None


def test_deployment_type_error_triggers_one_bounded_retry(wizard_server, compiled):
    """A first reply with invitees as a list is fixed by the retry prompt."""

    class RetryThenGood:
        def __init__(self):
            self._inner = MockExtractor()
            self.calls = 0

        def create(self, *, model=None, messages=None, **kwargs):
            self.calls += 1
            response = self._inner.chat.completions.create(
                model=model, messages=messages, **kwargs
            )
            if self.calls == 1:  # the classic boundary failure: a list
                good = parse_binding_json(response.choices[0].message.content)
                good["invitees"] = [good["invitees"]]
                response.choices[0].message.content = (
                    "```json\n" + json.dumps(good) + "\n```"
                )
            return response

    client = SimpleNamespace(chat=SimpleNamespace(completions=RetryThenGood()))
    goal, expected = DEPLOY_USES[0]
    with BindingRunner(wizard_server, "wizard") as runner:
        record = run_single_use(compiled["program"], goal, expected, client, "mock", runner)
    assert record["success"] is True
    assert record["retries"] == 1
    assert client.chat.completions.calls == 2
    # the retry prompt carries the type errors back to the model
    errs = binding_type_errors({"invitees": ["Heidi"]})
    from guiexp.deploy_runner import build_retry_prompt

    assert any("invitees" in e for e in errs)
    assert "invitees" in build_retry_prompt(goal, errs)[1]["content"]
