"""Offline checks for the optional compiled-binding rendering hook."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot


def _plan():
    return {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "the user supplied value"},
        "steps": [{
            "id": "save",
            "intent": "save the supplied value",
            "action": {"action_type": "click"},
            "target": {"text": "Save"},
            "before": [{"text": "Save"}],
            "after": [{"text": "Saved"}],
        }],
    }


class _Env:
    def __init__(self):
        self.events = []

    def reset(self, task):
        self.events.append("reset")

    def observe(self, goal):
        return {"url": "fake", "goal_text": goal, "ax_tree_text": ""}

    def reward(self):
        return 1.0

    def close(self):
        self.events.append("close")


class _Budget:
    def before_ui_action(self):
        return None


class _Client:
    def __init__(self, response=None):
        self.episode = None
        self.response = response
        self.begin_calls = []

    def begin_episode(self, episode):
        self.begin_calls.append(episode)
        self.episode = episode

    def create(self, **kwargs):
        text = self.response or '{"value":"RAW_MODEL_VALUE"}'
        return {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0},
        }


class _Runtime:
    def __init__(self):
        self.bindings = []

    def run_plan(self, **kwargs):
        self.bindings.append(dict(kwargs["bindings"]))
        return {"status": "complete", "trace": [], "actions": 0}


def _run(tmp_path, monkeypatch, *, rules=None, extraction=None, client=None):
    import guiexp_android.android_env as android_env
    import guiexp_android.conditions as conditions

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family})
    monkeypatch.setattr(android_env, "goal_text", lambda task: "use the public goal")
    monkeypatch.setattr(conditions, "build_prompt", lambda *args: "goal")
    runtime = _Runtime()
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: runtime)
    if extraction is not None:
        monkeypatch.setattr(
            pilot,
            "_extract_binding",
            lambda plan, goal, client, episode, out: (
                dict(extraction), {"status": "valid", "raw_marker": "MODEL_RECORD"}
            ),
        )
    env = _Env()
    row = {
        "id": "selective_20260915/MarkorCreateNote/b01/full_fallback",
        "family": "MarkorCreateNote",
        "arm": "full_fallback",
        "binding_id": "b01",
        "seed": 915201,
    }
    final = pilot._run_one_episode(
        tmp_path,
        {"action_budget": 3},
        row,
        {"params": {"value": "EVALUATOR_ORACLE_POISON"}},
        env=env,
        client=client or _Client(),
        budget=_Budget(),
        plan=_plan(),
        binding_render_rules=rules,
    )
    return final, runtime, env, row


def test_default_hook_is_byte_for_byte_raw_binding_behavior(tmp_path, monkeypatch):
    final, runtime, _env, _row = _run(
        tmp_path, monkeypatch, extraction={"value": "raw-value"}
    )
    assert final["run"]["status"] == "complete"
    assert runtime.bindings == [{"value": "raw-value"}]
    assert "binding_rendering" not in final["run"]
    assert "binding_rendering" not in final


def test_rendered_binding_reaches_runtime_while_raw_extraction_record_stays_separate(tmp_path, monkeypatch):
    rules = {"value": {"prefix": "pre-", "suffix": "-suf"}}
    final, runtime, _env, row = _run(
        tmp_path, monkeypatch, rules=rules, extraction={"value": "raw-value"}
    )
    expected = {"value": "pre-raw-value-suf"}
    assert runtime.bindings == [expected]
    assert final["run"]["extraction"] == {"status": "valid", "raw_marker": "MODEL_RECORD"}
    assert final["run"]["binding_rendering"] == {
        "raw": {"value": "raw-value"}, "rendered": expected, "rules": rules
    }
    state = json.loads(
        (tmp_path / "episodes" / row["id"] / "state.json").read_text(encoding="utf-8")
    )
    assert state["result"]["run"]["binding_rendering"]["raw"]["value"] == "raw-value"
    assert "EVALUATOR_ORACLE_POISON" not in json.dumps(state)


def test_already_prefixed_user_filename_gets_one_render_application(tmp_path, monkeypatch):
    final, runtime, _env, _row = _run(
        tmp_path,
        monkeypatch,
        rules={"value": {"prefix": "pre-", "suffix": ".txt"}},
        extraction={"value": "pre-report"},
    )
    assert runtime.bindings == [{"value": "pre-pre-report.txt"}]
    assert final["run"]["binding_rendering"]["rendered"]["value"] == "pre-pre-report.txt"


def test_actual_model_response_record_is_retained_when_runtime_uses_rendered_value(tmp_path, monkeypatch):
    client = _Client('{"value":"MODEL_RAW_VALUE"}')
    final, runtime, _env, row = _run(
        tmp_path,
        monkeypatch,
        rules={"value": {"prefix": "p-", "suffix": ""}},
        client=client,
    )
    assert runtime.bindings == [{"value": "p-MODEL_RAW_VALUE"}]
    records = [
        json.loads(line)
        for line in (tmp_path / "episodes" / row["id"] / "trajectory.jsonl").read_text().splitlines()
        if line.strip()
    ]
    calls = [record for record in records if record.get("record_type") == "model_call"]
    assert len(calls) == 1
    assert calls[0]["response"] == '{"value":"MODEL_RAW_VALUE"}'


def test_invalid_rules_are_rejected_before_ui_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pilot,
        "_begin_episode",
        lambda *args: pytest.fail("render-rule rejection must precede model/UI setup"),
    )
    for suffix, rules in (
        ("unknown", {"missing": {"prefix": "", "suffix": ""}}),
        ("malformed", {"value": {"prefix": 1, "suffix": ""}}),
    ):
        with pytest.raises(pilot.PlanInvalid):
            _run(
                tmp_path / suffix,
                monkeypatch,
                rules=rules,
                extraction={"value": "raw"},
            )
