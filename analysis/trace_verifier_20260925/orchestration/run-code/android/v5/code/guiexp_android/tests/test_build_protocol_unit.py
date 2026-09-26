"""Unit tests for the AutoRPA-style build protocol: translator input
construction, k>1 builder prompt assembly for both artifacts, the
verify-and-repair control flow, and the doc condition injection.

Zero LLM calls (MockOpenAI stands in for every model call), zero emulator
use (a fake device and a fake runner stand in for the live phone).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import (
    accounting_check, build_protocol, compiler, explore, translator, verify_runner,
)
from guiexp_android.conditions import CONDITIONS, build_prompt, told_procedure
from guiexp_android.mock_model import MockOpenAI

FAMILY = "ContactsAddContact"

# The GLM discover run for Contacts seed 0, replayed as a script (same
# sequence test_compile_unit.py uses).
CONTACTS_SCRIPT = [
    '{"action_type": "click", "index": 2}',
    '{"action_type": "click", "index": 6}',
    '{"action_type": "input_text", "index": 7, "text": "Louis"}',
    '{"action_type": "input_text", "index": 10, "text": "+10487647593"}',
    '{"action_type": "click", "index": 2}',
]


def write_trajectory(tmp_path: Path, seed: int, script=None, family: str = FAMILY) -> Path:
    """A runner-shaped trajectory for ``script`` on family/seed."""
    from guiexp_android import android_env

    params = android_env.instance_params(family, seed)
    script = script or [
        '{"action_type": "click", "index": 2}',
        '{"action_type": "input_text", "index": 7, "text": %s}' % json.dumps(params["name"].split()[0]),
        '{"action_type": "click", "index": 2}',
    ]
    records = []
    for i, action in enumerate(script, start=1):
        records.append({
            "step": i,
            "action_raw": "action: " + action,
            "action": action,
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "cached_tokens": 40, "cost_usd": 1e-5},
            "obs_meta": {"url": "com.example/.Screen", "screenshot_file": None,
                         "ax_chars": 500, "last_action_error": None},
        })
    records.append({
        "success": True, "total_tokens": 110 * len(script), "total_cost_usd": 1e-5 * len(script),
        "condition": "discover", "family": family, "seed": seed, "model": "mock",
        "obs_mode": "screenshot+ax", "task_id": f"{family}__discover__s{seed}",
        "steps": len(script), "model_calls": len(script), "record_type": "final",
    })
    path = tmp_path / f"s{seed}"
    path.mkdir(parents=True, exist_ok=True)
    out = path / "trajectory.jsonl"
    out.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return out


def element(index, text="", hint="", description="", clickable=False, editable=False,
            class_name="Button"):
    return {"index": index, "text": text, "hint": hint, "description": description,
            "class_name": class_name, "clickable": clickable, "editable": editable}


def fake_observations(steps: list[int]) -> dict:
    """One capture_observations-shaped record per step, each with a real
    screen change so every step counts as an effective action."""
    out = []
    for i, step in enumerate(steps):
        before = [element(0, text="Contacts"), element(1, text=f"before{i}", clickable=True)]
        after = [element(0, text="Contacts"), element(1, text=f"after{i}", editable=True)]
        out.append({
            "step": step,
            "activity_before": "com.example/.A",
            "activity_after": "com.example/.B",
            "target": element(2, text="Create contact", description="Create contact",
                              clickable=True),
            "before": before,
            "after": after,
        })
    return {"steps": out, "replay_stopped_at": None}


# ------------------------------------------------- A. translator input


def test_element_diff_names_what_left_and_arrived():
    before = [element(0, text="Save"), element(1, text="Cancel")]
    after = [element(0, text="Save"), element(1, text="OK")]
    diff = translator.element_diff(before, after)
    assert "- Button 'Cancel'" in diff.replace("text=", "")
    assert "+ Button 'OK'" in diff.replace("text=", "")
    assert "Save" not in diff  # unchanged elements are not in the diff


def test_element_diff_ignores_pure_renumbering():
    before = [element(0, text="A"), element(1, text="B")]
    after = [element(5, text="B"), element(9, text="A")]
    assert translator.element_diff(before, after).strip() == ""


def test_effective_steps_drops_actions_that_changed_nothing():
    same = [element(0, text="A")]
    observations = {"steps": [
        {"step": 1, "activity_before": "x", "activity_after": "x",
         "target": None, "before": same, "after": same},
        {"step": 2, "activity_before": "x", "activity_after": "y",
         "target": None, "before": same, "after": same},
    ]}
    effective = translator.effective_steps(observations)
    assert [s["step"] for s in effective] == [2]  # only the activity change survives


def test_translator_prompt_is_text_shaped_and_carries_the_three_inputs(tmp_path):
    traj = write_trajectory(tmp_path, seed=1, script=CONTACTS_SCRIPT)
    steps, _final = compiler.load_trajectory(traj, FAMILY)
    observation = {**fake_observations([steps[0]["step"]])["steps"][0], "diff": "  + Button 'OK'"}
    messages = translator.build_translator_prompt(
        FAMILY, steps[0], observation, "Create a new contact for {name}."
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    # text-shaped: no image part anywhere (the deliberate deviation)
    assert all(isinstance(m["content"], str) for m in messages)
    user = messages[1]["content"]
    assert "image" not in user and "base64" not in user
    # the three inputs: the action JSON, the target element record, the diff
    assert '"action_type": "click"' in user
    assert "Create contact" in user  # the target element's a11y record
    assert "+ Button 'OK'" in user
    assert "device.find(" in user  # the vocabulary program_runtime exposes


def test_translator_prompt_maps_a_typed_value_to_its_binding_expression(tmp_path):
    # seed 0 is the instance CONTACTS_SCRIPT was recorded on, so its typed
    # values are the ones map_value has to recognise
    traj = write_trajectory(tmp_path, seed=0, script=CONTACTS_SCRIPT)
    steps, _final = compiler.load_trajectory(traj, FAMILY)
    typed = [s for s in steps if s["action"].get("action_type") == "input_text"]
    observation = {**fake_observations([typed[0]["step"]])["steps"][0], "diff": ""}
    user = translator.build_translator_prompt(
        FAMILY, typed[0], observation, "Create a new contact for {name}."
    )[1]["content"]
    assert "binding[" in user  # the placeholder expression, not the literal


def test_translate_trajectory_bills_one_call_per_effective_action(tmp_path):
    traj = write_trajectory(tmp_path, seed=1, script=CONTACTS_SCRIPT)
    steps, _final = compiler.load_trajectory(traj, FAMILY)
    effective = [s["step"] for s in steps][:3]  # only three of the five change the screen
    client = MockOpenAI(script=["ANALYSIS: it depends on the label.\n```python\n"
                                "device.click(text='Create contact')\n```"] * 10)
    result = translator.translate_trajectory(
        "mock", traj, FAMILY, observations=fake_observations(effective), client=client
    )
    assert result["effective_actions"] == len(effective)
    assert result["totals"]["calls"] == len(effective)
    assert result["totals"]["prompt_tokens"] > 0
    assert all(item["usage"]["prompt_tokens"] > 0 for item in result["steps"])
    assert all("device." in item["snippet"] for item in result["steps"])


def test_parse_translation_splits_analysis_from_snippet():
    parsed = translator.parse_translation(
        "ANALYSIS: the Save button is the only clickable in the top bar.\n"
        "```python\ndevice.click(description='Save')\n```"
    )
    assert parsed["analysis"].startswith("the Save button")
    assert parsed["snippet"] == "device.click(description='Save')"


# ------------------------------------------------- B. builder, k > 1


@pytest.mark.parametrize("artifact", ["code", "doc"])
@pytest.mark.parametrize("k", [1, 2, 3])
def test_builder_prompt_carries_every_building_trajectory(tmp_path, k, artifact):
    entries = []
    for seed in (1, 2, 3)[:k]:
        entries.append({
            "trajectory": write_trajectory(tmp_path, seed=seed),
            "translation": {"steps": [{"step": 1, "snippet": f"device.click(text='step{seed}')",
                                       "analysis": f"analysis for seed {seed}"}]},
            "label": f"seed {seed}",
        })
    messages = compiler.build_builder_prompt(entries, FAMILY, artifact=artifact)
    user = messages[1]["content"]
    assert user.count("=== building instance ") == k
    for seed in (1, 2, 3)[:k]:
        assert f"(seed {seed})" in user
        assert f"device.click(text='step{seed}')" in user  # the translation is in
    assert "FAMILY GOAL TEMPLATE" in user
    assert "{name}" in user and "{number}" in user  # placeholders, not values


def test_builder_prompt_asks_for_code_or_for_a_document(tmp_path):
    entry = {"trajectory": write_trajectory(tmp_path, seed=1)}
    code = compiler.build_builder_prompt([entry], FAMILY, artifact="code")
    doc = compiler.build_builder_prompt([entry], FAMILY, artifact="doc")
    assert "def program(device, binding: dict) -> bool" in code[1]["content"]
    assert "OPERATION DOCUMENT" in doc[1]["content"]
    assert "Write NO code" in doc[1]["content"]
    assert "def program(" not in doc[1]["content"]
    # same evidence, different ask: the trajectory block is byte-identical
    marker = "=== building instance 1 of 1"
    assert code[1]["content"].split(marker)[1].split("Write ONE")[0] == \
        doc[1]["content"].split(marker)[1].split("Write ONE")[0]
    assert code[0]["content"] != doc[0]["content"]  # different system prompt


def test_single_trajectory_compile_path_is_unchanged(tmp_path):
    """The k=1 addition must not have moved the original compile prompt."""
    traj = write_trajectory(tmp_path, seed=1)
    messages = compiler.build_compile_prompt(traj, FAMILY)
    user = messages[1]["content"]
    assert messages[0]["content"] == compiler.COMPILE_SYSTEM_PROMPT
    assert "RECORDED TRAJECTORY of one instance" in user
    assert user.rstrip().endswith("no explanation.")
    assert "=== building instance" not in user


def test_unknown_artifact_is_rejected(tmp_path):
    entry = {"trajectory": write_trajectory(tmp_path, seed=1)}
    with pytest.raises(ValueError):
        compiler.build_builder_prompt([entry], FAMILY, artifact="diagram")


def test_refine_prompt_carries_trace_analysis_and_prior_conclusions():
    hybrid = {
        "binding": {"name": "Ada Byron", "number": "+1"},
        "goal": "Create a new contact for Ada Byron.",
        "failure": "ValueError: click: no element matches {'text': 'Save'}",
        "program_trace": ['{"action_type": "click", "index": 2} -> com.example/.A',
                          "find({'text': 'Save'}) -> NOT FOUND"],
        "breakpoint_screen": "  0: text='First name' [editable]",
        "analysis": "CAUSE: the Save control is an icon, not a text button.",
        "resume_actions": ["step 1: {\"action_type\": \"click\", \"index\": 3} -> com.example/.B"],
        "resume_success": True,
    }
    messages = compiler.build_refine_prompt(
        FAMILY, "code", "def program(device, binding):\n    return True\n",
        hybrid, conclusions=["seed 1: the extension field is separate"],
    )
    user = messages[1]["content"]
    assert "CURRENT ARTIFACT" in user and "def program" in user
    assert "NOT FOUND" in user
    assert "the Save control is an icon" in user
    assert "goal reached" in user
    assert "the extension field is separate" in user
    assert "def program(device, binding: dict) -> bool" in user  # the code contract


def test_call_builder_charges_and_syntax_checks_the_code(tmp_path):
    entry = {"trajectory": write_trajectory(tmp_path, seed=1)}
    source = "def program(device, binding):\n    return True\n"
    client = MockOpenAI(script=["```python\n" + source + "```"])
    result = compiler.compile_trajectories("mock", [entry], FAMILY, artifact="code", client=client)
    assert result["program_source"].strip() == source.strip()
    assert result["k"] == 1 and result["stage"] == "builder_initial"
    assert result["usage"]["prompt_tokens"] > 0


def test_call_builder_keeps_a_document_as_prose(tmp_path):
    entry = {"trajectory": write_trajectory(tmp_path, seed=1)}
    client = MockOpenAI(script=["1. Open Contacts.\n2. Type {name} into First name.\n"])
    result = compiler.compile_trajectories("mock", [entry], FAMILY, artifact="doc", client=client)
    assert "program_source" not in result
    assert "{name}" in result["artifact_text"]


# ------------------------------------------------- C. repair loop flow


class FakeRunner:
    """A ProgramRunner stand-in: a scripted pass/fail per replay, no device."""

    def __init__(self, outcomes: list[bool]):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def run(self, program, binding, family, judge_params=None, device_factory=None, **_kw):
        passed = self.outcomes.pop(0) if self.outcomes else True
        device = SimpleNamespace(
            trace=["{\"action_type\": \"click\", \"index\": 2} -> com.example/.A"],
            elements=lambda: [element(0, text="First name", editable=True)],
        )
        self.calls.append({"binding": dict(binding), "passed": passed})
        return {"passed": passed, "error": None if passed else "oracle mismatch",
                "reward": 1.0 if passed else 0.0, "device": device}


def install_fakes(monkeypatch, outcomes: list[bool], analyses=None, resumes=None,
                  refinements=None, gates=(4,)):
    """Point the verifier's four collaborators at scripted stand-ins.

    ``gates`` is the passed count of each successive gate call, in the order
    the verifier gates versions (initial, refine1, refine2, ...); the last
    entry repeats. The default 4 of 5 admits every version without ever
    reaching the 5/5 early stop, so the repair loop runs to its own end.
    """
    runner = FakeRunner(outcomes)
    monkeypatch.setattr(verify_runner, "ProgramRunner", lambda env: runner)
    monkeypatch.setattr(
        verify_runner, "program_from_source",
        lambda source, name="family_program": (None, lambda device, binding: True),
    )
    calls = {"analyze": 0, "resume": 0, "refine": 0, "gate": 0}

    def fake_analyze(model, family, failure, client=None, temperature=0.0):
        calls["analyze"] += 1
        canned = (analyses or [{}])[min(calls["analyze"] - 1, len(analyses or [{}]) - 1)]
        return {
            "cause": canned.get("cause", "the Save control moved"),
            "done": "", "plan": "", "decision": canned.get("decision", "continue"),
            "text": "CAUSE: the Save control moved",
            "usage": {"prompt_tokens": 500, "completion_tokens": 50, "cached_tokens": 100,
                      "cost_usd": 0.0001},
            "cost_usd": 0.0001, "stage": "analyzer",
        }

    def fake_resume(model, env, family, failure, analysis, obs_mode="screenshot+ax",
                    client=None, max_steps=None):
        calls["resume"] += 1
        canned = (resumes or [{}])[min(calls["resume"] - 1, len(resumes or [{}]) - 1)]
        per_call = [
            {"prompt_tokens": 1500, "cached_tokens": 0, "completion_tokens": 40,
             "cost_usd": 0.001},
            {"prompt_tokens": 2500, "cached_tokens": 1000, "completion_tokens": 60,
             "cost_usd": 0.001},
        ]
        return {
            "restarted": analysis.get("decision") != "continue",
            "actions": ["step 1: click -> com.example/.B"],
            "calls_detail": [
                accounting_check.call_record(i + 1, i + 1, u, stage="react_resume")
                for i, u in enumerate(per_call)
            ],
            "steps": 1, "success": canned.get("success", True),
            "usage": {"calls": 2, "prompt_tokens": 4000, "cached_tokens": 1000,
                      "completion_tokens": 100, "total_tokens": 4100, "cost_usd": 0.002},
            "stage": "react_resume",
        }

    def fake_refine(model, family, current, hybrid, conclusions=None, artifact="code",
                    client=None, **_kw):
        calls["refine"] += 1
        return {
            "artifact": artifact,
            "artifact_text": f"# refinement {calls['refine']}\ndef program(device, binding):\n    return True\n",
            "program_source": "def program(device, binding):\n    return True\n",
            "usage": {"prompt_tokens": 2000, "completion_tokens": 300, "cached_tokens": 500,
                      "cost_usd": 0.0005},
            "cost_usd": 0.0005, "model": model, "attempts": 1, "api_failures": [],
            "stage": "builder_refine",
        }

    monkeypatch.setattr(verify_runner, "analyze", fake_analyze)
    monkeypatch.setattr(verify_runner, "react_resume", fake_resume)
    monkeypatch.setattr(verify_runner, "refine_artifact", fake_refine)
    draws = [{"params": {"i": i}, "binding": {"i": i}} for i in range(5)]
    monkeypatch.setattr(verify_runner, "heldout_bindings", lambda *a, **k: list(draws))

    def fake_gate(program, family, draws_, runner_):
        index = calls["gate"]
        calls["gate"] += 1
        passed = gates[index] if index < len(gates) else gates[-1]
        return {"bindings_passed": passed, "bindings_total": len(draws_), "detail": []}

    monkeypatch.setattr(verify_runner, "run_gate", fake_gate)
    return runner, calls


SOURCE = "def program(device, binding):\n    return True\n"


def test_clean_build_replays_1_2_3_and_never_repairs(monkeypatch):
    # seen grows 1 -> 2 -> 3, so a clean build replays 1 + 2 + 3 = 6 instances
    _runner, calls = install_fakes(monkeypatch, [True] * 6 + [True])
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None)
    assert record["replays"] == 6
    assert record["refinements"] == 0
    assert calls["analyze"] == 0 and calls["resume"] == 0 and calls["refine"] == 0
    assert record["unautomatable"] is False
    assert record["gate"]["bindings_passed"] == 4
    assert record["admitted"] is True
    assert record["admitted_version"] == "initial"
    assert record["gate_history"] == [{"version": "initial", "passed": 4, "total": 5}]


def test_an_initial_five_of_five_is_kept_and_never_repaired(monkeypatch):
    """The GLM FilesMoveFile case: a program that already passes every
    held-out binding is not handed to the repair loop at all."""
    _runner, calls = install_fakes(monkeypatch, [False] * 40, gates=(5,))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None)
    assert calls["gate"] == 1  # only the initial version was ever gated
    assert record["replays"] == 0 and record["refinements"] == 0
    assert calls["analyze"] == 0 and calls["resume"] == 0 and calls["refine"] == 0
    assert record["admitted"] is True and record["admitted_version"] == "initial"
    assert record["final_artifact"] == SOURCE
    assert record["unautomatable"] is False


def test_a_five_of_five_refinement_stops_the_loop(monkeypatch):
    _runner, calls = install_fakes(monkeypatch, [False] * 40, gates=(2, 5))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None)
    assert record["refinements"] == 1  # M = 3 was available; 5/5 ended it
    assert calls["refine"] == 1
    assert record["admitted"] is True and record["admitted_version"] == "refine1"
    assert record["final_artifact"].startswith("# refinement 1")
    assert record["unautomatable"] is False


def test_the_best_version_survives_refinements_that_make_it_worse(monkeypatch):
    """The initial program gates 4/5, every repair is worse, and the loop
    then gives up: the 4/5 program is what comes back, not the last one."""
    _runner, _calls = install_fakes(monkeypatch, [False] * 40, gates=(4, 2, 1, 0))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert record["refinements"] == 3
    assert record["unautomatable"] is True  # the loop's verdict, reported only
    assert record["admitted"] is True and record["admitted_version"] == "initial"
    assert record["final_artifact"] == SOURCE
    assert record["last_artifact"].startswith("# refinement 3")
    assert record["gate"]["bindings_passed"] == 4
    assert [g["passed"] for g in record["gate_history"]] == [4, 2, 1, 0]


def test_a_tie_on_the_gate_keeps_the_earlier_version(monkeypatch):
    _runner, _calls = install_fakes(monkeypatch, [False] * 40, gates=(4, 4, 4, 4))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert record["admitted_version"] == "initial"
    assert record["final_artifact"] == SOURCE


def test_three_of_five_is_not_admitted_and_the_last_version_comes_back(monkeypatch):
    _runner, _calls = install_fakes(monkeypatch, [False] * 40, gates=(3,))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert record["admitted"] is False
    assert record["gate_min_pass"] == 4
    assert record["admitted_version"] == "refine3"  # the last version, not the best
    assert record["final_artifact"].startswith("# refinement 3")
    assert record["gate"]["bindings_passed"] == 3


def test_every_version_is_gated_once(monkeypatch):
    _runner, calls = install_fakes(monkeypatch, [False] * 40, gates=(1,))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert calls["gate"] == 1 + record["refinements"] == 4
    assert [g["version"] for g in record["gate_history"]] == [
        "initial", "refine1", "refine2", "refine3"
    ]
    assert all(g["total"] == 5 for g in record["gate_history"])


def test_a_failure_runs_analyzer_resume_and_refine_once_each(monkeypatch):
    # instance 1 fails, is repaired, then everything passes; the repair also
    # improves the gate, so the refined program is the one kept
    outcomes = [False] + [True] * 10
    _runner, calls = install_fakes(monkeypatch, outcomes, gates=(3, 4))
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None)
    assert calls["analyze"] == 1 and calls["resume"] == 1 and calls["refine"] == 1
    assert record["refinements"] == 1
    assert record["unautomatable"] is False
    assert record["rounds"][0]["failure_seed"] == 1
    assert record["final_artifact"].startswith("# refinement 1")
    # every repair stage is charged
    assert record["totals"]["analyzer"]["total_tokens"] > 0
    assert record["totals"]["resume_episodes"]["total_tokens"] > 0
    assert record["totals"]["builder_refinements"]["total_tokens"] > 0
    # program replays cost nothing
    assert record["totals"]["program_replays"]["total_tokens"] == 0


def test_the_loop_stops_at_the_first_failure_in_the_seen_set(monkeypatch):
    # seen = [1] passes; seen = [1,2]: 1 passes, 2 fails -> stop there
    runner, calls = install_fakes(monkeypatch, [True, True, False] + [True] * 10)
    verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None)
    assert calls["analyze"] == 1
    # the third replay is the one that failed; no fourth ran in that scan
    assert [c["passed"] for c in runner.calls[:3]] == [True, True, False]


def test_m_refinements_then_unautomatable(monkeypatch):
    _runner, calls = install_fakes(monkeypatch, [False] * 40)
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert record["unautomatable"] is True
    assert record["refinements"] == 3  # M modifications spent, then it stops
    assert calls["analyze"] == 3 and calls["resume"] == 3
    # the gate still ran on every version: unautomatable is a report, not a veto
    assert record["gate"]["bindings_passed"] == 4
    assert record["admitted"] is True


def test_prior_conclusions_accumulate_across_refinements(monkeypatch):
    _runner, _calls = install_fakes(monkeypatch, [False] * 40)
    record = verify_runner.verify_and_repair("mock", FAMILY, SOURCE, env=None, m_max=3)
    assert len(record["conclusions"]) == 3
    assert all(c.startswith("seed 1:") for c in record["conclusions"])


def test_analysis_parser_reads_the_continue_or_restart_decision():
    parsed = verify_runner.parse_analysis(
        "CAUSE: the dialog was never confirmed.\n"
        "DONE: the name fields are filled.\n"
        "PLAN: tap OK, then Save.\n"
        "DECISION: continue"
    )
    assert parsed["decision"] == "continue"
    assert parsed["cause"] == "the dialog was never confirmed."
    assert "tap OK" in parsed["plan"]
    # an unreadable decision is the conservative one
    assert verify_runner.parse_analysis("CAUSE: unclear")["decision"] == "restart"


def test_tracing_device_records_actions_and_failed_lookups():
    class FakeEnv:
        wait_after_action_seconds = 0.0
        aw_env = SimpleNamespace(
            get_state=lambda wait_to_stabilize=False: SimpleNamespace(ui_elements=[]),
            foreground_activity_name="com.example/.A",
        )

    device = verify_runner.TracingDevice(FakeEnv())
    assert device.find(text="Save") is None
    assert device.trace == ["find({'text': 'Save'}) -> NOT FOUND"]


# ------------------------------------------------- D. doc condition


def test_doc_condition_injects_exactly_where_told_injects():
    goal = "Create a new contact for Isla Hernandez."
    doc = "1. Open Contacts.\n2. Type {name} into the First name field.\n"
    told_prompt = build_prompt("told", FAMILY, goal)
    doc_prompt = build_prompt("doc", FAMILY, goal, doc_text=doc)
    # same goal block, same slot: replacing one text with the other is the
    # ONLY difference between the two prompts
    assert told_prompt.replace(told_procedure(FAMILY), doc.strip("\n")) == doc_prompt
    assert doc.strip("\n") in doc_prompt
    assert told_procedure(FAMILY) not in doc_prompt


def test_doc_condition_needs_a_document():
    with pytest.raises(ValueError):
        build_prompt("doc", FAMILY, "some goal")
    with pytest.raises(ValueError):
        build_prompt("doc", FAMILY, "some goal", doc_text="   ")


def test_doc_is_a_condition_and_discover_still_carries_no_knowledge():
    assert "doc" in CONDITIONS
    plain = build_prompt("discover", FAMILY, "Create a new contact for X.")
    assert "Open Contacts" not in plain


def test_runner_passes_the_document_into_the_episode(tmp_path, monkeypatch):
    from guiexp_android import runner as runner_mod

    seen = {}

    class FakeEnv:
        def reset(self, task):
            return {"url": "com.example/.A", "ax_tree_text": "UI element 0: {}"}

        def step(self, action_text):
            return {"url": "com.example/.A", "ax_tree_text": ""}, True, 1.0

        def close(self):
            pass

    class Recorder(MockOpenAI):
        def __init__(self):
            super().__init__()
            create = self.chat.completions.create

            def wrapped(*, model=None, messages=None, **kw):
                seen.setdefault("first_user", messages[-1]["content"])
                return create(model=model, messages=messages, **kw)

            self.chat.completions.create = wrapped

    doc = "1. Open Contacts.\n2. Type {name} into First name.\n"
    final = runner_mod.run_episode(
        family=FAMILY, condition="doc", seed=0, model="mock", obs_mode="screenshot+ax",
        max_steps=2, out_dir=tmp_path / "traj", client=Recorder(), env=FakeEnv(),
        doc_text=doc,
    )
    text = "".join(part.get("text", "") for part in seen["first_user"])
    assert "1. Open Contacts." in text
    assert final["condition"] == "doc" and final["doc_chars"] == len(doc)


# ------------------------------------------------- E. accounting helpers


def test_step_cap_is_ten_times_complexity_capped_at_fifty():
    assert explore.step_cap("MarkorDeleteNote") == 10       # complexity 1.0
    assert explore.step_cap("ContactsAddContact") == 12     # 1.2
    assert explore.step_cap("MarkorMoveNote") == 14         # 1.4
    assert explore.step_cap("MarkorCreateNote") == 16       # 1.6
    assert explore.step_cap("SimpleCalendarAddOneEvent") == 34  # 3.4
    assert explore.step_cap("SimpleCalendarAddOneEvent") <= explore.STEP_CAP_MAX


def test_building_seeds_leave_seed_zero_to_the_test_role():
    assert explore.BUILDING_SEEDS == (1, 2, 3)
    assert explore.TEST_SEED == 0
    assert explore.TEST_SEED not in explore.BUILDING_SEEDS


def test_cache_adjusted_is_fresh_plus_r_times_cached_plus_completion():
    totals = {"prompt_tokens": 1000, "cached_tokens": 500, "completion_tokens": 100}
    assert build_protocol.cache_adjusted(totals, 0.20) == 500 + 100 + 100
    assert build_protocol.cache_adjusted(totals, 1.0) == 1100


def test_break_even_reports_both_accountings():
    record = {
        "exploration": {
            "per_episode": [
                {"attempt": 0, "total_tokens": 100_000, "prompt_tokens": 95_000,
                 "cached_tokens": 0, "completion_tokens": 5_000},
            ],
            "totals": {"prompt_tokens": 95_000, "cached_tokens": 0,
                       "completion_tokens": 5_000, "total_tokens": 100_000, "cost_usd": 0.0},
        },
        "translator": {"totals": {"prompt_tokens": 18_000, "cached_tokens": 0,
                                  "completion_tokens": 2_000, "total_tokens": 20_000,
                                  "cost_usd": 0.0}},
        "builder": {"selected_arm": {"initial_plus_refinements": {
            "prompt_tokens": 9_000, "cached_tokens": 0, "completion_tokens": 1_000,
            "total_tokens": 10_000, "cost_usd": 0.0}}},
        "verification": {"totals_excluding_refinements": {
            "prompt_tokens": 4_500, "cached_tokens": 0, "completion_tokens": 500,
            "total_tokens": 5_000, "cost_usd": 0.0}},
        "deploy": {"d_tokens_mean": 225.0, "success_rate": 1.0},
    }
    out = build_protocol.break_even(record, "z-ai/glm-5.3-flash")
    s = 100_000 - 225.0
    assert out["q"] == 0.0 and out["s_raw"] == pytest.approx(s, abs=0.1)
    # marginal: only the builder calls; autorpa: all four build stages
    assert out["nstar_raw"]["marginal"] == pytest.approx(10_000 / s, rel=1e-3)
    assert out["nstar_raw"]["autorpa_build"] == pytest.approx(135_000 / s, rel=1e-3)
    assert out["build_totals_raw"]["autorpa_build"] == 135_000


def test_break_even_is_undefined_without_a_deploy_arm():
    record = {
        "exploration": {"per_episode": [], "totals": build_protocol.zero()},
        "translator": {"totals": build_protocol.zero()},
        "builder": {"selected_arm": {"initial_plus_refinements": build_protocol.zero()}},
        "verification": {"totals_excluding_refinements": build_protocol.zero()},
        "deploy": {"skipped": "the type was declared unautomatable"},
    }
    out = build_protocol.break_even(record, "z-ai/glm-5.3-flash")
    assert "nstar_raw" not in out and "note" in out


# ------------------------------------------------- F. MarkorDeleteNote


def test_markor_delete_note_is_wired_end_to_end():
    from guiexp_android import android_env, deploy_runner
    from guiexp_android.conditions import FAMILIES

    family = "MarkorDeleteNote"
    assert family in FAMILIES
    assert compiler.binding_fields(family) == ("file_name",)
    params = android_env.instance_params(family, 1)
    assert "file_name" in params and "noise_candidates" in params
    binding = compiler.params_to_binding(family, params)
    assert set(binding) == {"file_name"}
    # the oracle needs the noise pool back, which the binding does not carry
    judged = compiler.binding_to_params(family, binding)
    assert judged["file_name"] == params["file_name"]
    assert judged["noise_candidates"]
    # gate bindings are held out from the instance
    from guiexp_android.gate_runner import heldout_bindings

    draws = heldout_bindings(family, k=5, exclude_params=params)
    assert len(draws) == 5
    assert all(d["params"] != params for d in draws)
    # deploy uses and their extraction round-trip
    uses = deploy_runner.deploy_uses(family, 5)
    assert len(uses) == 5
    for goal, expected in uses:
        got = deploy_runner.mock_extract_fields(goal, family)
        assert got == expected, (goal, got)
        assert deploy_runner.binding_type_errors(got, family) == []


# ------------------------------------------------- G. the three new families
#
# docs/harder-families-and-routers.md A2.2 (the OsmAnd pair) and A2.3
# (FilesMoveFile). The same end-to-end wiring check MarkorDeleteNote gets
# above, plus the two things that are new here: a family whose oracle is
# picked by ONE control among several on the same screen, and a family with
# three string parameters.

NEW_FAMILIES = ("OsmAndFavorite", "OsmAndMarker", "FilesMoveFile")
NEW_FIELDS = {
    "OsmAndFavorite": ("location",),
    "OsmAndMarker": ("location",),
    "FilesMoveFile": ("file_name", "source_folder", "destination_folder"),
}


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_new_family_is_wired_end_to_end(family):
    from guiexp_android import android_env, deploy_runner
    from guiexp_android.conditions import FAMILIES
    from guiexp_android.gate_runner import heldout_bindings

    assert family in FAMILIES
    assert compiler.binding_fields(family) == NEW_FIELDS[family]
    params = android_env.instance_params(family, 1)
    binding = compiler.params_to_binding(family, params)
    assert set(binding) == set(NEW_FIELDS[family])
    # held-out bindings are deterministic, distinct and disjoint from seed 1
    draws = heldout_bindings(family, k=5, exclude_params=params)
    assert len(draws) == 5
    assert all(d["params"] != params for d in draws)
    assert draws == heldout_bindings(family, k=5, exclude_params=params)
    # deploy uses round-trip through the offline extractor and the type check
    uses = deploy_runner.deploy_uses(family, 30)
    assert len(uses) == 30
    for goal, expected in uses:
        got = deploy_runner.mock_extract_fields(goal, family)
        assert got == expected, (goal, got)
        assert deploy_runner.binding_type_errors(got, family) == []


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_new_family_instances_are_seed_deterministic(family):
    from guiexp_android import android_env

    assert android_env.instance_params(family, 3) == android_env.instance_params(family, 3)
    assert android_env.instance_params(family, 3) != android_env.instance_params(family, 4)


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_new_family_oracle_is_the_android_world_evaluator(family, monkeypatch):
    """The judge is the family's own is_successful over the params
    binding_to_params rebuilds, never a re-implementation. The env is mocked:
    no emulator is touched."""
    from guiexp_android import android_env
    from guiexp_android.program_runtime import ProgramRunner

    seen = {}

    class FakeEnv:
        wait_after_action_seconds = 0.0

        def reset(self, task):
            seen["task"] = task

        def reward(self):
            return 1.0

    runner = ProgramRunner(FakeEnv())
    binding = compiler.params_to_binding(family, android_env.instance_params(family, 2))
    outcome = runner.run(lambda device, b: True, binding, family,
                         device_factory=lambda env: object())
    assert outcome["passed"]
    task = seen["task"]
    assert task.family == family
    # the params handed to the evaluator carry every schema key it needs
    cls = android_env._task_classes()[family]
    for key in cls.schema["required"]:
        assert key in task.params, key


def test_files_move_params_carry_the_source_folder_noise_pool():
    """The binding names three folders' worth of nothing; the task setup needs
    the noise files android_world fills the SOURCE folder with."""
    from android_world.task_evals.utils import user_data_generation

    binding = {"file_name": "holiday_photos.jpg", "source_folder": "Download",
               "destination_folder": "DCIM"}
    params = compiler.binding_to_params("FilesMoveFile", binding)
    assert params["noise_candidates"] == list(
        user_data_generation.EMULATOR_DIRECTORIES["Download"])
    assert params["source_folder"] == "Download"
    assert params["destination_folder"] == "DCIM"


def test_osmand_bindings_survive_both_location_spellings():
    """The generator draws either a place name or a 'lat, lon' pair, and the
    oracle matches coordinates to 0.001 degrees, so neither may be reshaped."""
    from guiexp_android import deploy_runner

    for family in ("OsmAndFavorite", "OsmAndMarker"):
        locations = {b["location"] for _g, b in deploy_runner.deploy_uses(family, 30)}
        assert any("," in loc and loc.replace(",", "").replace(".", "").replace(" ", "").isdigit()
                   for loc in locations), "no coordinate spelling in the pool"
        assert any(any(c.isalpha() for c in loc) for loc in locations), "no name spelling"
        for location in locations:
            binding = {"location": location}
            assert compiler.binding_to_params(family, binding) == binding


@pytest.mark.parametrize("family", NEW_FAMILIES)
def test_told_and_doc_inject_at_the_same_slot_for_the_new_families(family):
    goal = "GOAL LINE"
    told = build_prompt("told", family, goal)
    doc = build_prompt("doc", family, goal, doc_text="COMPILED DOCUMENT")
    discover = build_prompt("discover", family, goal)
    assert told_procedure(family) in told
    assert "COMPILED DOCUMENT" in doc
    # same slot: everything before and after the injected block is identical
    assert told.replace(told_procedure(family), "X") == doc.replace("COMPILED DOCUMENT", "X")
    # and discover still carries neither
    assert told_procedure(family) not in discover and "COMPILED DOCUMENT" not in discover


def test_step_caps_of_the_new_families_come_from_android_world_complexity():
    from android_world.task_evals.single.files import FilesMoveFile
    from android_world.task_evals.single.osmand import OsmAndFavorite, OsmAndMarker

    for family, cls in (("OsmAndFavorite", OsmAndFavorite),
                        ("OsmAndMarker", OsmAndMarker),
                        ("FilesMoveFile", FilesMoveFile)):
        assert explore.COMPLEXITY[family] == float(cls.complexity)
    assert explore.step_cap("OsmAndFavorite") == 13
    assert explore.step_cap("OsmAndMarker") == 20
    assert explore.step_cap("FilesMoveFile") == 20


def test_the_batch_registry_covers_every_family_exactly_once():
    from guiexp_android.conditions import FAMILIES

    assert len(build_protocol.BATCH_FAMILIES) == len(set(build_protocol.BATCH_FAMILIES)) == 7
    assert set(build_protocol.BATCH_FAMILIES) == set(FAMILIES)
    cells = build_protocol.batch_cells()
    assert len(cells) == 7 * len(build_protocol.BATCH_MODELS)
    assert all(cap == explore.step_cap(family) for family, _model, cap in cells)
    # cheapest first, so a budget stop loses the least work
    caps = [cap for family, model, cap in cells
            if model == build_protocol.BATCH_MODELS[0]]
    assert caps == sorted(caps)


# ------------------------------------------------- F. resume and the replay deadline


def install_build_fakes(monkeypatch):
    """Point every stage of run_build at a deterministic offline stand-in.

    Each fake writes the same artifacts the real stage writes, because those
    files are exactly what --resume reads back. Returns (calls, seen): a call
    counter per stage, and the inputs the later stages were handed, so a
    resumed run can be compared against a fresh one input by input.
    """
    from guiexp_android import runner as runner_mod

    calls = {"explore": 0, "translate": 0, "compile": 0, "verify": 0,
             "deploy": 0, "doc": 0}
    seen = {"builder_entries": [], "verify_source": [], "deploy_source": [],
            "doc_text": []}
    # what the verification stage reports back; a test flips these to drive
    # the deployment decision
    verify_gate = {"passed": 5, "unautomatable": False}
    seen["verify_gate"] = verify_gate

    def fake_run_exploration(family, model, out_dir, seeds, obs_mode=None, client=None,
                             env=None, keep_emulator=True, reuse_grid=True, **_kw):
        calls["explore"] += 1
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        instances = []
        for seed in seeds:
            traj = write_trajectory(out_dir, seed=seed)
            instances.append({
                "family": family, "seed": seed, "goal": f"goal {seed}", "step_cap": 12,
                "attempts": [{
                    "attempt": 0, "trajectory": str(traj), "reused": False,
                    "success": True, "steps": 3,
                    "usage": {"prompt_tokens": 300, "cached_tokens": 120,
                              "completion_tokens": 30, "total_tokens": 330,
                              "cost_usd": 3e-05, "model_calls": 3},
                }],
                "reflections": [],
                "success": True,
                "best_trajectory": str(traj),
            })
        record = {
            "family": family, "model": model, "seeds": list(seeds), "test_seed": 0,
            "n_ref": 2, "step_cap": 12, "instances": instances,
            "totals": explore.stage_totals(instances), "record_type": "exploration",
        }
        (out_dir / "exploration.json").write_text(json.dumps(record, indent=1))
        return record

    def fake_translate(model, trajectory, family, client=None, env=None, **_kw):
        calls["translate"] += 1
        return {
            "family": family, "model": model, "trajectory": str(trajectory), "seed": 1,
            "steps": [{"step": 1, "action": '{"action_type": "click", "index": 2}',
                       "field": None, "analysis": "opens the editor",
                       "snippet": "device.click(text='Save')", "usage": {}}],
            "effective_actions": 1, "recorded_actions": 3, "replay_stopped_at": None,
            "totals": {"calls": 1, "prompt_tokens": 700, "cached_tokens": 200,
                       "completion_tokens": 40, "total_tokens": 740, "cost_usd": 7e-05},
            "record_type": "translation",
        }

    def fake_compile(model, entries, family=None, artifact="code", client=None, **_kw):
        calls["compile"] += 1
        seen["builder_entries"].append([
            {"label": e["label"], "trajectory": str(e["trajectory"]),
             "translation": e["translation"]}
            for e in entries
        ])
        k = len(entries)
        text = (
            f"# k={k} code\ndef program(device, binding):\n    return True\n"
            if artifact == "code"
            else f"doc for k={k}\n1. Open the app.\n"
        )
        result = {
            "artifact": artifact, "artifact_text": text,
            "usage": {"prompt_tokens": 1000 * k, "cached_tokens": 100 * k,
                      "completion_tokens": 200, "cost_usd": 0.0002},
            "cost_usd": 0.0002, "model": model, "attempts": 1, "api_failures": [],
            "k": k, "stage": "builder_initial",
        }
        if artifact == "code":
            result["program_source"] = text
        return result

    def fake_verify(model, family, program_source, env, seeds=None, client=None,
                    obs_mode=None, artifact="code", out_dir=None, **_kw):
        calls["verify"] += 1
        seen["verify_source"].append(program_source)
        gate_passed = verify_gate["passed"]
        final = "# verified\ndef program(device, binding):\n    return True\n"
        return {
            "family": family, "model": model, "artifact": artifact, "m_max": 3,
            "seeds": list(seeds or []), "rounds": [], "refinements": 1, "replays": 6,
            "unautomatable": verify_gate["unautomatable"],
            "admitted": gate_passed >= 4,
            "admitted_version": "refine1",
            "gate_min_pass": 4,
            "gate_history": [{"version": "initial", "passed": 0, "total": 5},
                             {"version": "refine1", "passed": gate_passed, "total": 5}],
            "final_artifact": final,
            "last_artifact": final,
            "conclusions": [],
            "totals": {
                "analyzer": {"calls": 1, "prompt_tokens": 500, "cached_tokens": 100,
                             "completion_tokens": 50, "total_tokens": 550,
                             "cost_usd": 0.0001},
                "resume_episodes": {"calls": 2, "prompt_tokens": 4000, "cached_tokens": 1000,
                                    "completion_tokens": 100, "total_tokens": 4100,
                                    "cost_usd": 0.002},
                "builder_refinements": {"calls": 1, "prompt_tokens": 2000, "cached_tokens": 500,
                                        "completion_tokens": 300, "total_tokens": 2300,
                                        "cost_usd": 0.0005},
                "program_replays": {"count": 6, "total_tokens": 0, "cost_usd": 0.0},
            },
            "gate": {"bindings_passed": gate_passed, "bindings_total": 5, "detail": []},
            "test_seed0": {"passed": True, "error": None},
            "record_type": "verification",
        }

    def fake_run_deployment(program, family, uses, client, model, runner, role=None):
        calls["deploy"] += 1
        n = len(uses)
        return {"model": model, "family": family, "n": n, "uses": [],
                "success_count": n - 1, "success_rate": (n - 1) / n,
                "d_tokens_mean": 900.0, "total_tokens": 900 * n,
                "total_cost_usd": 0.009, "record_type": "deploy", "role": role}

    def fake_run_episode(*, family, condition, seed, model, obs_mode, max_steps,
                         out_dir, client=None, env=None, keep_emulator=True,
                         doc_text=None, close_env=False, **_kw):
        calls["doc"] += 1
        seen["doc_text"].append(doc_text)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        records = [
            {"step": 1, "action_raw": "action: {}", "action": "{}",
             "usage": {"prompt_tokens": 500, "cached_tokens": 100,
                       "completion_tokens": 40, "cost_usd": 5e-05},
             "obs_meta": {"url": "com.example/.A"}},
            {"success": True, "steps": 1, "record_type": "final", "family": family,
             "seed": seed, "condition": condition, "model": model},
        ]
        (out / "trajectory.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n"
        )
        return records[-1]

    monkeypatch.setattr(build_protocol, "run_exploration", fake_run_exploration)
    monkeypatch.setattr(build_protocol, "translate_trajectory", fake_translate)
    monkeypatch.setattr(build_protocol, "compile_trajectories", fake_compile)
    monkeypatch.setattr(build_protocol, "verify_and_repair", fake_verify)
    monkeypatch.setattr(build_protocol, "run_deployment", fake_run_deployment)
    monkeypatch.setattr(build_protocol, "deploy_uses",
                        lambda family, n, offset=0: [(f"goal {i}", {}) for i in range(n)])
    monkeypatch.setattr(build_protocol, "ProgramRunner", lambda env: FakeRunner([True] * 50))
    monkeypatch.setattr(build_protocol, "program_from_source",
                        lambda source, name="family_program": (None, lambda d, b: True))
    monkeypatch.setattr(build_protocol, "heldout_bindings",
                        lambda family, k=5, **kw: [{"params": {}, "binding": {}}] * k)
    gate_result = {"passed": 5}
    seen["gate_result"] = gate_result
    monkeypatch.setattr(build_protocol, "run_gate",
                        lambda program, family, draws, runner: {
                            "bindings_passed": gate_result["passed"],
                            "bindings_total": 5, "detail": []})
    monkeypatch.setattr(runner_mod, "run_episode", fake_run_episode)
    return calls, seen


def run_mock_build(out_dir, resume=False):
    return build_protocol.run_build(
        family=FAMILY, model="z-ai/glm-5.3-flash", out_dir=out_dir,
        env=SimpleNamespace(), seeds=(1, 2, 3), k_values=(1, 2, 3), deploy_n=5,
        doc_seeds=(4, 5), client=MockOpenAI(), resume=resume,
    )


def truncate_stages(out_dir, stages):
    """Leave build.json in the state a kill after ``stages`` would leave it."""
    path = Path(out_dir) / "build.json"
    record = json.loads(path.read_text())
    record["stages_done"] = list(stages)
    path.write_text(json.dumps(record, indent=1))
    return record


VOLATILE = {"wall_s", "resumed_from_stages"}  # wall time, and the resume marker


def test_resume_rebuilds_the_same_record_without_re_running_paid_stages(tmp_path, monkeypatch):
    calls, _seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    fresh = run_mock_build(out, resume=False)
    assert calls["explore"] == 1 and calls["translate"] == 3 and calls["compile"] == 6

    # the killed cell: exploration, translator and builder recorded, the rest not
    truncate_stages(out, ["exploration", "translator", "builder"])
    for key in calls:
        calls[key] = 0

    resumed = run_mock_build(out, resume=True)

    # no model-token stage was paid for twice
    assert calls["explore"] == 0 and calls["translate"] == 0 and calls["compile"] == 0
    # everything after the kill point did run
    assert calls["verify"] == 1 and calls["deploy"] == 1 and calls["doc"] == 2
    assert resumed["resumed_from_stages"] == ["exploration", "translator", "builder"]
    assert resumed["stages_done"] == fresh["stages_done"]
    assert resumed["total_cost_usd"] == fresh["total_cost_usd"]
    assert {k: v for k, v in resumed.items() if k not in VOLATILE} == \
           {k: v for k, v in fresh.items() if k not in VOLATILE}


def test_resumed_objects_reach_the_later_stages_unchanged(tmp_path, monkeypatch):
    """The builder sees the same entries, and verify/doc the same artifacts."""
    calls, seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    fresh_entries = list(seen["builder_entries"])
    fresh_verify = list(seen["verify_source"])
    fresh_doc = list(seen["doc_text"])

    truncate_stages(out, ["exploration", "translator"])
    seen["builder_entries"].clear()
    seen["verify_source"].clear()
    seen["doc_text"].clear()
    run_mock_build(out, resume=True)

    assert calls["explore"] == 1 and calls["translate"] == 3  # not called again
    assert seen["builder_entries"] == fresh_entries
    assert seen["verify_source"] == fresh_verify
    assert seen["doc_text"] == fresh_doc


def test_resume_skipping_only_the_gate_keeps_the_recorded_gate(tmp_path, monkeypatch):
    calls, _seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    fresh = run_mock_build(out, resume=False)
    truncate_stages(out, ["exploration", "translator", "builder", "gate_per_k",
                          "verification"])
    for key in calls:
        calls[key] = 0
    resumed = run_mock_build(out, resume=True)
    assert calls["verify"] == 0  # verify.json was read back, not re-run
    assert resumed["gate_per_k"] == fresh["gate_per_k"]
    assert resumed["verification"] == fresh["verification"]
    assert resumed["builder"]["selected_arm"] == fresh["builder"]["selected_arm"]
    assert resumed["total_cost_usd"] == fresh["total_cost_usd"]


def test_resume_without_a_previous_record_runs_every_stage(tmp_path, monkeypatch):
    calls, _seen = install_build_fakes(monkeypatch)
    record = run_mock_build(tmp_path / "cell", resume=True)
    assert calls["explore"] == 1 and calls["compile"] == 6 and calls["doc"] == 2
    assert "resumed_from_stages" not in record


def test_resume_refuses_a_done_stage_whose_artifact_is_gone(tmp_path, monkeypatch):
    install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    (out / "artifact_k2_doc.txt").unlink()
    truncate_stages(out, ["exploration", "translator", "builder"])
    with pytest.raises(build_protocol.ResumeError):
        run_mock_build(out, resume=True)


def test_resume_refuses_a_build_json_from_another_cell(tmp_path, monkeypatch):
    install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    record = json.loads((out / "build.json").read_text())
    record["family"] = "MarkorDeleteNote"
    (out / "build.json").write_text(json.dumps(record, indent=1))
    with pytest.raises(build_protocol.ResumeError):
        run_mock_build(out, resume=True)


# -- the gate as the admission criterion for deployment -----------------------


def test_a_four_of_five_gate_is_deployed(tmp_path, monkeypatch):
    calls, seen = install_build_fakes(monkeypatch)
    seen["verify_gate"]["passed"] = 4
    record = run_mock_build(tmp_path / "cell", resume=False)
    assert calls["deploy"] == 1
    assert record["verification"]["admitted"] is True
    assert record["verification"]["p_after"] == 0.8
    assert "skipped" not in record["deploy"]


def test_a_three_of_five_gate_is_not_deployed(tmp_path, monkeypatch):
    calls, seen = install_build_fakes(monkeypatch)
    seen["verify_gate"]["passed"] = 3
    record = run_mock_build(tmp_path / "cell", resume=False)
    assert calls["deploy"] == 0
    assert record["verification"]["admitted"] is False
    assert record["verification"]["p_after"] == 0.6
    assert "gate" in record["deploy"]["skipped"]
    # N* is undefined without a deploy arm, and the run still finishes
    assert "nstar_raw" not in record["break_even"]


def test_unautomatable_is_reported_but_a_passing_gate_still_deploys(tmp_path, monkeypatch):
    """The GLM FilesMoveFile case at the build level: the AutoRPA loop gave
    up, the kept version still passes the gate, so it is deployed."""
    calls, seen = install_build_fakes(monkeypatch)
    seen["verify_gate"].update(passed=5, unautomatable=True)
    record = run_mock_build(tmp_path / "cell", resume=False)
    assert calls["deploy"] == 1
    assert record["verification"]["unautomatable"] is True
    assert record["verification"]["admitted"] is True


def test_the_resume_reader_derives_admission_from_a_legacy_verify_json(tmp_path, monkeypatch):
    """A verify.json written before the gate decided anything carries no
    ``admitted`` field: the reader fills it in from the recorded gate."""
    install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    record = json.loads((out / "verify.json").read_text())
    for key in ("admitted", "admitted_version", "gate_min_pass", "gate_history"):
        record.pop(key, None)
    record["gate"] = {"bindings_passed": 4, "bindings_total": 5, "detail": []}
    (out / "verify.json").write_text(json.dumps(record, indent=1))

    resumed = build_protocol.resumed_verification(out)
    assert resumed["admitted"] is True
    assert resumed["gate_min_pass"] == 4
    assert resumed["gate_history"] == [{"version": "final", "passed": 4, "total": 5}]

    record["gate"] = None  # the old unautomatable shape: no gate was ever run
    (out / "verify.json").write_text(json.dumps(record, indent=1))
    absent = build_protocol.resumed_verification(out)
    assert absent["admitted"] is False and absent["gate_history"] == []


def test_a_resumed_verification_below_the_threshold_stops_the_deploy(tmp_path, monkeypatch):
    calls, _seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    record = json.loads((out / "verify.json").read_text())
    record.pop("admitted")
    record["gate"] = {"bindings_passed": 3, "bindings_total": 5, "detail": []}
    (out / "verify.json").write_text(json.dumps(record, indent=1))
    truncate_stages(out, ["exploration", "translator", "builder", "gate_per_k",
                          "verification"])
    for key in calls:
        calls[key] = 0
    resumed = run_mock_build(out, resume=True)
    assert calls["verify"] == 0 and calls["deploy"] == 0
    assert "skipped" in resumed["deploy"]


# -- redeploy from an existing artifact ---------------------------------------


def redeploy(out, artifact="artifact_k3_code.py", **kw):
    return build_protocol.redeploy_from_artifact(
        family=FAMILY, model="z-ai/glm-5.3-flash", out_dir=out,
        env=SimpleNamespace(), artifact=artifact, deploy_n=5,
        client=MockOpenAI(), **kw
    )


def test_redeploy_gates_the_artifact_and_deploys_it_when_admitted(tmp_path, monkeypatch):
    calls, seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    fresh = run_mock_build(out, resume=False)
    for key in calls:
        calls[key] = 0
    seen["gate_result"]["passed"] = 4

    record = redeploy(out)

    # only the deploy stage ran again
    assert calls["deploy"] == 1
    assert calls["explore"] == calls["translate"] == calls["compile"] == 0
    assert calls["verify"] == 0 and calls["doc"] == 0
    note = record["redeploy"]
    assert note["source_artifact"].endswith("artifact_k3_code.py")
    assert note["admitted"] is True and note["p_after"] == 0.8
    assert note["gate"]["bindings_passed"] == 4 and note["timestamp"]
    assert "skipped" not in record["deploy"]
    # every other stage is byte-identical to the finished cell
    for stage in ("exploration", "translator", "builder", "gate_per_k",
                  "verification", "doc_arm"):
        assert record[stage] == fresh[stage]
    assert record["stages_done"] == fresh["stages_done"]
    # the uses the old decision produced are moved aside, not lost
    assert (out / "deploy_before_redeploy.json").is_file()
    assert json.loads((out / "deploy.json").read_text())["role"].endswith("redeploy_k3")


def test_redeploy_refuses_to_deploy_an_artifact_below_the_threshold(tmp_path, monkeypatch):
    calls, seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    for key in calls:
        calls[key] = 0
    seen["gate_result"]["passed"] = 3

    record = redeploy(out)
    assert calls["deploy"] == 0
    assert record["redeploy"]["admitted"] is False
    assert "skipped" in record["deploy"]


def test_redeploy_needs_a_finished_cell_and_an_artifact_that_exists(tmp_path, monkeypatch):
    install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    with pytest.raises(build_protocol.ResumeError):  # no build.json at all
        redeploy(out)
    run_mock_build(out, resume=False)
    with pytest.raises(build_protocol.ResumeError):
        redeploy(out, artifact="artifact_k9_code.py")
    record = json.loads((out / "build.json").read_text())
    record["family"] = "MarkorDeleteNote"
    (out / "build.json").write_text(json.dumps(record, indent=1))
    with pytest.raises(build_protocol.ResumeError):
        redeploy(out)


def test_the_redeploy_cli_reaches_redeploy_and_nothing_else(monkeypatch):
    """The exact command a finished cell is redeployed with, parsed and
    dispatched. No emulator, no build."""
    import sys

    from guiexp_android import android_env

    seen = {}

    class FakeEnv:
        def close(self):
            seen["closed"] = True

        def stop_emulator(self):
            seen["stopped"] = True

    monkeypatch.setattr(android_env, "AndroidWorldEnv", FakeEnv)
    monkeypatch.setattr(build_protocol, "run_build",
                        lambda **kw: pytest.fail("run_build must not run"))

    def fake_redeploy(**kw):
        seen.update(kw)
        return {"redeploy": {"gate": {"bindings_passed": 4, "bindings_total": 5},
                             "admitted": True, "source_artifact": "artifact_k3_code.py"}}

    monkeypatch.setattr(build_protocol, "redeploy_from_artifact", fake_redeploy)
    monkeypatch.setattr(sys, "argv", [
        "build_protocol", "--family", "FilesMoveFile",
        "--model", "z-ai/glm-5.3-flash", "--out", "cells/FilesMoveFile",
        "--redeploy-from", "artifact_k3_code.py", "--deploy-uses", "30",
        "--keep-emulator",
    ])
    assert build_protocol.main() == 0
    assert seen["family"] == "FilesMoveFile"
    assert seen["model"] == "z-ai/glm-5.3-flash"
    assert seen["artifact"] == "artifact_k3_code.py"
    assert seen["deploy_n"] == 30 and seen["doc_arm"] is False
    assert seen["out_dir"] == "cells/FilesMoveFile"
    assert seen["closed"] is True and "stopped" not in seen  # --keep-emulator


def test_redeploy_can_also_re_run_the_doc_arm(tmp_path, monkeypatch):
    calls, _seen = install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    run_mock_build(out, resume=False)
    for key in calls:
        calls[key] = 0
    record = redeploy(out, doc_arm=True, doc_seeds=(4, 5))
    assert calls["doc"] == 2 and calls["deploy"] == 1
    assert record["redeploy"]["doc_arm"] is True


# -- the per-replay wall-clock deadline ---------------------------------------


class DeadlineEnv:
    """The smallest env a ProgramRunner replay needs: reset, then a verdict."""

    wait_after_action_seconds = 0.0

    def reset(self, task):
        return None

    def reward(self):
        return 1.0


def test_a_replay_that_outruns_the_deadline_is_a_timeout_failure():
    import time

    from guiexp_android import android_env, program_runtime

    runner = program_runtime.ProgramRunner(DeadlineEnv())
    params = android_env.instance_params(FAMILY, 1)

    def slow(device, binding):
        time.sleep(30)
        return True

    outcome = runner.run(slow, {"name": "A"}, FAMILY, judge_params=params, timeout_s=0.4)
    assert outcome["passed"] is False
    assert outcome["error"] == "replay timeout after 0.4s"
    assert outcome["reward"] == 0.0

    # and the next replay still runs
    ok = runner.run(lambda device, binding: True, {"name": "B"}, FAMILY,
                    judge_params=params, timeout_s=0.4)
    assert ok["passed"] is True and ok["error"] is None


def test_the_gate_records_a_timeout_and_goes_on_to_the_next_binding(monkeypatch):
    import time

    from guiexp_android import gate_runner, program_runtime

    monkeypatch.setattr(program_runtime, "REPLAY_TIMEOUT_S", 0.4)
    runner = program_runtime.ProgramRunner(DeadlineEnv())
    draws = gate_runner.heldout_bindings(FAMILY, k=2)
    state = {"n": 0}

    def program(device, binding):
        state["n"] += 1
        if state["n"] == 1:
            time.sleep(30)
        return True

    gate = gate_runner.run_gate(program, FAMILY, draws, runner)
    assert gate["bindings_total"] == 2
    assert gate["bindings_passed"] == 1
    assert gate["detail"][0]["error"] == "replay timeout after 0.4s"
    assert gate["detail"][1]["passed"] is True


def test_the_deadline_is_on_by_default_and_cancelled_when_the_block_ends():
    import time

    from guiexp_android import program_runtime

    assert program_runtime.REPLAY_TIMEOUT_S == 1200
    with program_runtime.replay_deadline(0.3):
        pass
    time.sleep(0.5)  # the cancelled alarm must not fire into the next test


def test_a_nested_deadline_leaves_the_outer_one_in_charge():
    import time

    from guiexp_android import program_runtime

    fired = []
    try:
        with program_runtime.replay_deadline(0.4):
            with program_runtime.replay_deadline(30):  # a no-op, not a re-arm
                time.sleep(30)
    except program_runtime.ReplayTimeout as exc:
        fired.append(str(exc))
    assert fired == ["replay timeout after 0.4s"]


def test_the_deadline_is_a_no_op_where_it_cannot_be_armed():
    import threading
    import time

    from guiexp_android import program_runtime

    raised = []

    def body():
        try:
            with program_runtime.replay_deadline(0.2):
                time.sleep(0.5)
        except BaseException as exc:  # noqa: BLE001 - any escape is the failure
            raised.append(exc)

    thread = threading.Thread(target=body)  # no handler off the main thread
    thread.start()
    thread.join()
    assert raised == []
