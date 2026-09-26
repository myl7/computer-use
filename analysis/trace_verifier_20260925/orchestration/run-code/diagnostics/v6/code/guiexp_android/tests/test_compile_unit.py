"""Unit tests for the compile path: prompt/placeholder rules, held-out
disjointness, fence handling, MockCompiler shape, deploy boundary and the
mock extractor. Zero LLM calls, zero emulator use."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guiexp_android import android_env, deploy_runner, gate_runner
from guiexp_android.compiler import (
    FAMILY_BINDINGS,
    MockCompiler,
    binding_fields,
    binding_to_params,
    build_compile_prompt,
    extract_python_source,
    load_trajectory,
    map_value,
    params_to_binding,
    placeholder_expr,
    trajectory_instance,
)
from guiexp_android.program_runtime import program_from_source

FAMILIES = ("ContactsAddContact", "SimpleCalendarAddOneEvent", "MarkorCreateNote")

# The GLM discover run for Contacts seed 0 (t12_grid), replayed as a script:
# the exact action sequence that episode executed, used to fabricate a
# trajectory without any model or emulator.
CONTACTS_SCRIPT = [
    '{"action_type": "click", "index": 2}',
    '{"action_type": "click", "index": 6}',
    '{"action_type": "click", "index": 1}',
    '{"action_type": "input_text", "index": 7, "text": "Louis"}',
    '{"action_type": "input_text", "index": 8, "text": "Lopez"}',
    '{"action_type": "input_text", "index": 10, "text": "+10487647593"}',
    '{"action_type": "click", "index": 2}',
]


def _write_trajectory(tmp_path: Path, family: str, script: list[str], seed: int = 0) -> Path:
    """A minimal runner-shaped trajectory for ``script`` on ``family``/seed."""
    records = []
    for i, action in enumerate(script, start=1):
        records.append(
            {
                "step": i,
                "action_raw": "action: " + action,
                "action": action,
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost_usd": 1e-6},
                "obs_meta": {
                    "url": "com.example/.SomeActivity",
                    "screenshot_file": f"step_{i:03d}.png",
                    "ax_chars": 500,
                    "last_action_error": None,
                },
            }
        )
    records.append(
        {
            "success": True, "total_tokens": 84, "total_cost_usd": 1e-5,
            "condition": "discover", "family": family, "seed": seed,
            "model": "mock", "obs_mode": "screenshot+ax",
            "task_id": f"{family}__discover__s{seed}", "steps": len(script),
            "model_calls": len(script), "record_type": "final",
        }
    )
    path = tmp_path / "trajectory.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


@pytest.fixture()
def contacts_trajectory(tmp_path):
    return _write_trajectory(tmp_path, "ContactsAddContact", CONTACTS_SCRIPT)


# ------------------------------------------------------------------ prompt


def test_prompt_has_placeholders_not_instance_values(contacts_trajectory):
    messages = build_compile_prompt(contacts_trajectory, "ContactsAddContact")
    assert [m["role"] for m in messages] == ["system", "user"]
    user = messages[1]["content"]
    # the goal template carries named placeholders for both binding fields
    for field in binding_fields("ContactsAddContact"):
        assert "{" + field + "}" in user, field
    # ...but never the recorded instance's concrete values
    for value in trajectory_instance("ContactsAddContact", load_trajectory(contacts_trajectory)[1]).values():
        assert str(value) not in user, value
    # the code contract and the device API are spelled out
    assert "def program(device, binding: dict) -> bool" in user
    assert "device.find(" in user
    # no screenshots: the prompt is plain text, never multimodal parts
    assert isinstance(messages[1]["content"], str)


def test_prompt_maps_typed_values_to_binding_expressions(contacts_trajectory):
    user = build_compile_prompt(contacts_trajectory, "ContactsAddContact")[1]["content"]
    assert "\"text\": binding['name'].split()[0]" in user  # Louis -> first name
    assert "\"text\": binding['name'].split()[1]" in user  # Lopez -> last name
    assert "\"text\": binding['number']" in user  # the phone number, whole
    assert "-> com.example/.SomeActivity" in user  # post-action activity per step


def test_prompt_annotations_add_element_hints(contacts_trajectory):
    annotations = {4: {"text": "", "hint": "First name", "description": ""}}
    user = build_compile_prompt(
        contacts_trajectory, "ContactsAddContact", annotations=annotations
    )[1]["content"]
    assert "[hint='First name']" in user


@pytest.mark.parametrize("family", FAMILIES)
def test_prompt_placeholders_for_every_family(tmp_path, family):
    # fabricate a one-step trajectory that only opens the app
    path = _write_trajectory(tmp_path, family, ['{"action_type": "open_app", "app_name": "X"}'])
    user = build_compile_prompt(path, family)[1]["content"]
    for field in binding_fields(family):
        assert "{" + field + "}" in user, field
    assert "def program(device, binding: dict) -> bool" in user


def test_extract_python_source_fence_handling():
    fenced = "Sure.\n```python\ndef program(device, binding):\n    return True\n```\n"
    assert "def program" in extract_python_source(fenced)
    bare = "```\nX = 1\n```"
    assert extract_python_source(bare).strip() == "X = 1"
    assert extract_python_source("def program():\n    pass\n").startswith("def program")
    with pytest.raises(ValueError):
        extract_python_source("   ")


# -------------------------------------------------------------- value mapping


def test_map_value_fragments():
    params = {"name": "Louis Lopez", "number": "+10487647593"}
    assert map_value("Louis Lopez", params, "ContactsAddContact") == ("name", ("whole",))
    assert map_value("Louis", params, "ContactsAddContact") == ("name", ("token", 0))
    assert map_value("Lopez", params, "ContactsAddContact") == ("name", ("token", 1))
    # a differently formatted phone number is still the number
    assert map_value("+1 048 764 7593", params, "ContactsAddContact") == ("number", ("whole",))
    assert map_value(".constant", params, "ContactsAddContact") == (None, None)

    markor = {"file_name": "qFzW_polite_wolf.txt", "text": "Actions speak louder than words."}
    assert map_value("qFzW_polite_wolf", markor, "MarkorCreateNote") == ("file_name", ("base",))
    assert map_value(".txt", markor, "MarkorCreateNote") == ("file_name", ("ext",))
    assert map_value(markor["text"], markor, "MarkorCreateNote") == ("text", ("whole",))

    expr = placeholder_expr("file_name", ("ext",))
    assert expr == "'.' + binding['file_name'].rsplit('.', 1)[1]"


# ---------------------------------------------------------------- MockCompiler


def test_mock_compiler_emits_parameterized_program(contacts_trajectory):
    result = MockCompiler().compile(contacts_trajectory, "ContactsAddContact")
    assert set(result) >= {"program_source", "usage", "cost_usd"}
    assert result["usage"]["prompt_tokens"] > 0
    assert result["usage"]["completion_tokens"] > 0
    assert result["cost_usd"] >= 0.0
    assert result["covered_fields"] == ["name", "number"]

    module, program = program_from_source(result["program_source"])
    assert set(module.PARAMS_SCHEMA) == set(binding_fields("ContactsAddContact"))
    assert callable(program)

    # parameterized, not hard-coded: none of the recorded instance's values
    _steps, final = load_trajectory(contacts_trajectory)
    for value in trajectory_instance("ContactsAddContact", final).values():
        assert str(value) not in result["program_source"], value

    # fragments rebuild the recorded texts from a FRESH binding
    binding = {"name": "Priya Ashford", "number": "+1 302 555 0143"}
    rows = {(row[0], row[1]["index"]): row for row in module._STEPS}
    assert module._value(binding, "name", ("token", 0), None) == "Priya"
    assert module._value(binding, "name", ("token", 1), None) == "Ashford"
    assert module._value(binding, "number", ("whole",), None) == binding["number"]
    assert module._value(binding, None, None, ".const") == ".const"
    # input rows carry the placeholder, click rows replay verbatim
    assert rows[("input_text", 7)] == ("input_text", {"index": 7}, "name", ("token", 0), None)
    assert rows[("click", 2)] == ("click", {"index": 2}, None, None, None)


def test_mock_compiler_requires_parseable_actions(tmp_path):
    path = tmp_path / "bad.jsonl"
    final = {
        "success": True, "condition": "discover", "family": "ContactsAddContact",
        "seed": 0, "record_type": "final",
    }
    path.write_text(json.dumps(final) + "\n")  # no parsable steps at all
    with pytest.raises(ValueError):
        MockCompiler().compile(path, "ContactsAddContact")


# ------------------------------------------------------------------ held-out


@pytest.mark.parametrize("family", FAMILIES)
def test_heldout_bindings_deterministic_distinct_disjoint(family):
    instance = android_env.instance_params(family, 0)  # the trajectory's seed
    first = gate_runner.heldout_bindings(family, 5, exclude_params=instance, seed=0)
    assert first == gate_runner.heldout_bindings(family, 5, exclude_params=instance, seed=0)
    assert len(first) == 5
    bindings = [d["binding"] for d in first]
    assert len({json.dumps(b, sort_keys=True, default=str) for b in bindings}) == 5
    for gate_seed in range(4):  # any gate seed stays disjoint from the instance
        for draw in gate_runner.heldout_bindings(family, 3, exclude_params=instance, seed=gate_seed):
            assert draw["params"] != instance
    with pytest.raises(ValueError):  # more bindings than the walk can find
        gate_runner.heldout_bindings(family, 1_000, exclude_params=instance)


@pytest.mark.parametrize("family", FAMILIES)
def test_binding_roundtrip_via_params(family):
    """binding_to_params/params_to_binding are inverses on the binding part."""
    params = android_env.instance_params(family, 7)
    binding = params_to_binding(family, params)
    rebuilt = params_to_binding(family, binding_to_params(family, binding))
    assert rebuilt == binding


def test_calendar_params_carry_the_reference_row():
    binding = {
        "year": 2023, "month": 10, "day": 20, "hour": 18, "duration_mins": 45,
        "event_title": "T", "event_description": "D",
    }
    params = binding_to_params("SimpleCalendarAddOneEvent", binding)
    (row,) = params["row_objects"]
    assert row.title == "T" and row.description == "D"
    assert (row.end_ts - row.start_ts) == 45 * 60
    # start_ts is the UTC midnight-based timestamp of 2023-10-20T18:00
    import datetime

    assert row.start_ts == int(
        datetime.datetime(2023, 10, 20, 18, tzinfo=datetime.timezone.utc).timestamp()
    )


# ----------------------------------------------------------- deploy boundary


@pytest.mark.parametrize("family", FAMILIES)
def test_deploy_uses_pool_shape(family):
    uses = deploy_runner.deploy_uses(family, 30)
    assert len(uses) == 30
    bindings = [dict(expected) for _goal, expected in uses]
    assert len({json.dumps(b, sort_keys=True) for b in bindings}) == 30  # distinct
    # disjoint from the gate draws and from the seed-0 instance the
    # trajectory ran (comparisons on the binding projection)
    instance = params_to_binding(family, android_env.instance_params(family, 0))
    gate = [d["binding"] for d in gate_runner.heldout_bindings(family, 5)]
    for binding in bindings:
        assert binding != instance
        assert binding not in gate
    # offset windows into the same pool
    assert deploy_runner.deploy_uses(family, 2, offset=3) == uses[3:5]


def test_binding_type_check_and_normalization():
    good = {"name": "Priya Ashford", "number": "+1 302 555 0143"}
    assert deploy_runner.binding_type_errors(good, "ContactsAddContact") == []
    assert deploy_runner.binding_type_errors({"name": "One"}, "ContactsAddContact") != []
    assert deploy_runner.binding_type_errors({**good, "number": ["302"]}, "ContactsAddContact") != []
    # the classic boundary failure: the name split into first/last keys
    errs = deploy_runner.binding_type_errors(
        {"first": "Priya", "last": "Ashford", "number": "302"}, "ContactsAddContact"
    )
    assert any("name" in e for e in errs)
    assert any("first" not in e for e in errs)  # unknown keys are simply ignored

    cal = {
        "year": "2023", "month": "10", "day": "20", "hour": "18",
        "duration_mins": "45", "event_title": "T", "event_description": "D",
    }
    assert deploy_runner.binding_type_errors(cal, "SimpleCalendarAddOneEvent") == []
    normalized = deploy_runner.normalize_binding(cal, "SimpleCalendarAddOneEvent")
    assert all(isinstance(normalized[k], int) for k in ("year", "month", "day", "hour", "duration_mins"))
    assert deploy_runner.binding_type_errors({**cal, "hour": "25"}, "SimpleCalendarAddOneEvent") != []
    assert deploy_runner.binding_type_errors({**cal, "month": "October"}, "SimpleCalendarAddOneEvent") != []

    markor = {"file_name": "notes", "text": "hello"}
    assert any("extension" in e for e in deploy_runner.binding_type_errors(markor, "MarkorCreateNote"))
    assert deploy_runner.binding_type_errors({"file_name": "notes.txt", "text": "hello"}, "MarkorCreateNote") == []
    assert deploy_runner.binding_type_errors("not a dict", "MarkorCreateNote") != []


def test_parse_binding_json():
    good = {"name": "A B", "number": "1"}
    fenced = "```json\n" + json.dumps(good) + "\n```"
    assert deploy_runner.parse_binding_json(fenced) == good
    assert deploy_runner.parse_binding_json(json.dumps(good)) == good
    assert deploy_runner.parse_binding_json("no json here") is None
    assert deploy_runner.parse_binding_json("[1, 2]") is None


@pytest.mark.parametrize("family", FAMILIES)
def test_mock_extractor_matches_ground_truth(family):
    for goal, expected in deploy_runner.deploy_uses(family, 30):
        got = deploy_runner.mock_extract_fields(goal, family)
        assert got == expected, (goal, expected, got)


def test_retry_prompt_carries_the_errors():
    goal = "Please add X Y to my contacts. Their number is 1."
    errs = deploy_runner.binding_type_errors({"first": "X", "last": "Y", "number": "1"}, "ContactsAddContact")
    messages = deploy_runner.build_retry_prompt(goal, "ContactsAddContact", errs)
    assert any("name" in e for e in errs)
    assert "name" in messages[1]["content"]
    assert "Request: " + goal in messages[1]["content"]
