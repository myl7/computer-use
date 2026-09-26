"""Focused tests for the optional verified-prefix completion hook."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot


def _plan() -> dict:
    return {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "the task value"},
        "steps": [
            {
                "id": "save",
                "intent": "save the value",
                "action": {"action_type": "click"},
                "target": {"text": "Save"},
                "before": [],
                "after": [{"text": "Saved"}],
            }
        ],
    }


class _Env:
    def __init__(self, reward: float = 1.0):
        self.reward_value = reward
        self.reset_calls = 0
        self.observe_calls = 0
        self.reward_calls = 0
        self.closed = 0

    def reset(self, task):
        self.reset_calls += 1

    def observe(self, goal):
        self.observe_calls += 1
        return {"url": "fake", "goal_text": goal, "observation": self.observe_calls}

    def reward(self):
        self.reward_calls += 1
        return self.reward_value

    def close(self):
        self.closed += 1


class _Client:
    def __init__(self):
        self.episode = None
        self.begin_calls = []

    def begin_episode(self, episode):
        self.begin_calls.append(episode)
        self.episode = episode


class _Budget:
    def before_ui_action(self):
        return True


def _setup(monkeypatch, tmp_path, *, action_budget=3, plan_result=None):
    from guiexp_android import android_env

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family})
    monkeypatch.setattr(android_env, "goal_text", lambda task: "full goal")
    monkeypatch.setattr("guiexp_android.conditions.build_prompt", lambda *args: "full goal prompt")
    monkeypatch.setattr(pilot, "_extract_binding", lambda *args: ({}, {"status": "valid"}))

    result = plan_result or {
        "status": "complete",
        "verified": True,
        "pc": 1,
        "actions": 2,
    }
    captured = {}

    def fake_run_plan(plan, bindings, executor, decision, mode):
        captured["executor"] = executor
        executor.actions_sent = int(result.get("actions", 0))
        return dict(result)

    monkeypatch.setattr(pilot, "_run_plan", fake_run_plan)
    spec = {"action_budget": action_budget, "spec_sha256": "test"}
    row = {
        "id": "selective_20260915/MarkorCreateNote/b01/full_fallback",
        "family": "MarkorCreateNote",
        "arm": "full_fallback",
        "binding_id": "b01",
        "seed": 915201,
    }
    return spec, row, captured


def _trajectory(out, row):
    path = out / "episodes" / row["id"] / "trajectory.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_verified_complete_writes_marker_then_reacts_on_same_episode(tmp_path, monkeypatch):
    spec, row, captured = _setup(monkeypatch, tmp_path)
    env = _Env()
    client = _Client()
    oracle_calls = []
    reactive_calls = []

    monkeypatch.setattr(pilot, "_terminal_oracle", lambda value: oracle_calls.append(value) or (True, None))

    def fake_reactive(executor, goal_prompt, episode_id, passed_client, out_dir, initial_obs, **kwargs):
        reactive_calls.append((executor, goal_prompt, episode_id, passed_client, initial_obs, kwargs))
        assert executor is captured["executor"]
        assert passed_client is client
        assert initial_obs["observation"] == 2
        assert "full goal prompt" in kwargs["scoped_prompt"]
        assert "prefix complete != task complete" in kwargs["scoped_prompt"]
        assert "save the value" in kwargs["scoped_prompt"]
        executor.actions_sent += 1
        return {"status": "model_terminal", "actions": 1}

    monkeypatch.setattr(pilot, "_reactive_loop", fake_reactive)
    final = pilot._run_one_episode(
        tmp_path,
        spec,
        row,
        {},
        env=env,
        client=client,
        budget=_Budget(),
        plan=_plan(),
        completion_policy="reactive_handoff",
    )

    records = _trajectory(tmp_path, row)
    marker = next(item for item in records if item.get("record_type") == "prefix_verified")
    assert marker["compiled_plan_step_boundary"] == 1
    assert marker["action_count"] == 2
    assert records.index(marker) < next(i for i, item in enumerate(records) if item.get("record_type") == "final")
    assert final["actions"] == 3
    assert len(reactive_calls) == 1
    assert client.begin_calls == [row["id"]]
    assert env.reset_calls == 1
    assert len(oracle_calls) == 1


def test_reactive_handoff_uses_only_remaining_action_cap(tmp_path, monkeypatch):
    spec, row, captured = _setup(monkeypatch, tmp_path, action_budget=3)
    env = _Env()
    client = _Client()
    seen = {}

    def fake_reactive(executor, goal_prompt, episode_id, passed_client, out_dir, initial_obs, **kwargs):
        seen["remaining"] = executor.max_actions - executor.actions_sent
        seen["prompt"] = kwargs["scoped_prompt"]
        for _ in range(5):
            if executor.actions_sent >= executor.max_actions:
                break
            executor.actions_sent += 1
        return {"status": "action_budget_stop", "actions": executor.actions_sent - 2}

    monkeypatch.setattr(pilot, "_reactive_loop", fake_reactive)
    final = pilot._run_one_episode(
        tmp_path,
        spec,
        row,
        {},
        env=env,
        client=client,
        budget=_Budget(),
        plan=_plan(),
        completion_policy="reactive_handoff",
    )

    assert captured["executor"].actions_sent == 3
    assert seen["remaining"] == 1
    assert "Remaining shared UI action budget: 1." in seen["prompt"]
    assert final["actions"] == 3


def test_complete_without_verified_final_guards_does_not_handoff(tmp_path, monkeypatch):
    spec, row, _captured = _setup(
        monkeypatch,
        tmp_path,
        plan_result={"status": "complete", "verified": False, "pc": 1, "actions": 1},
    )
    env = _Env()
    client = _Client()
    reactive_calls = []
    monkeypatch.setattr(pilot, "_reactive_loop", lambda *args, **kwargs: reactive_calls.append(True))
    oracle_calls = []
    monkeypatch.setattr(pilot, "_terminal_oracle", lambda value: oracle_calls.append(value) or (False, None))

    final = pilot._run_one_episode(
        tmp_path,
        spec,
        row,
        {},
        env=env,
        client=client,
        budget=_Budget(),
        plan=_plan(),
        completion_policy="reactive_handoff",
    )

    assert reactive_calls == []
    assert "prefix_verified" not in {item.get("record_type") for item in _trajectory(tmp_path, row)}
    assert final["success"] is False
    assert len(oracle_calls) == 1


def test_default_completion_policy_preserves_complete_path(tmp_path, monkeypatch):
    spec, row, _captured = _setup(monkeypatch, tmp_path)
    env = _Env()
    client = _Client()
    monkeypatch.setattr(pilot, "_reactive_loop", lambda *args, **kwargs: pytest.fail("default path handed off"))
    oracle_calls = []
    monkeypatch.setattr(pilot, "_terminal_oracle", lambda value: oracle_calls.append(value) or (True, None))

    final = pilot._run_one_episode(
        tmp_path,
        spec,
        row,
        {},
        env=env,
        client=client,
        budget=_Budget(),
        plan=_plan(),
    )

    assert final["success"] is True
    assert final["run"]["status"] == "complete"
    assert "prefix_verified" not in {item.get("record_type") for item in _trajectory(tmp_path, row)}
    assert len(oracle_calls) == 1


def test_handoff_failure_leaves_durable_terminal_episode_state(tmp_path, monkeypatch):
    spec, row, _captured = _setup(monkeypatch, tmp_path)
    env = _Env()
    client = _Client()

    def fail_handoff(*args, **kwargs):
        raise pilot.PilotStop("handoff failed")

    monkeypatch.setattr(pilot, "_reactive_loop", fail_handoff)
    with pytest.raises(pilot.PilotStop, match="handoff failed"):
        pilot._run_one_episode(
            tmp_path,
            spec,
            row,
            {},
            env=env,
            client=client,
            budget=_Budget(),
            plan=_plan(),
            completion_policy="reactive_handoff",
        )

    state = json.loads((tmp_path / "episodes" / row["id"] / "state.json").read_text())
    assert state["status"] == "interrupted"
    assert any(item.get("record_type") == "prefix_verified" for item in _trajectory(tmp_path, row))


def test_unknown_completion_policy_is_rejected_before_ui(tmp_path, monkeypatch):
    spec, row, _captured = _setup(monkeypatch, tmp_path)
    env = _Env()
    client = _Client()

    with pytest.raises(ValueError, match="unknown completion_policy"):
        pilot._run_one_episode(
            tmp_path,
            spec,
            row,
            {},
            env=env,
            client=client,
            budget=_Budget(),
            plan=_plan(),
            completion_policy="unexpected",
        )

    assert env.reset_calls == 0
    assert client.begin_calls == []


def test_empty_slot_binding_is_deterministic_while_nonempty_still_calls_sdk(tmp_path):
    class SDKClient:
        def __init__(self, content):
            self.content = content
            self.calls = 0
            self.episode = None
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.calls += 1
            return {
                "choices": [{"message": {"content": self.content}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0},
            }

    empty_plan = dict(_plan(), slots={})
    empty_client = SDKClient('{"value":"should not be used"}')
    empty_out = tmp_path / "empty"
    binding, result = pilot._extract_binding(
        empty_plan,
        "goal",
        empty_client,
        "selective_20260915/test/empty",
        empty_out,
    )
    assert binding == {}
    assert result["provenance"] == "deterministic_empty_slots"
    assert result["no_model_call"] is True
    assert result["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0,
    }
    assert empty_client.calls == 0
    assert not (empty_out / "trajectory.jsonl").exists()

    normal_client = SDKClient('{"value":"from-sdk"}')
    binding, result = pilot._extract_binding(
        _plan(),
        "goal",
        normal_client,
        "selective_20260915/test/normal",
        tmp_path / "normal",
    )
    assert binding == {"value": "from-sdk"}
    assert result["status"] == "valid"
    assert normal_client.calls == 1
