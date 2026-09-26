"""Offline integration checks across the selective pilot boundaries.

These tests use the real pilot, runtime, and budget modules.  The phone,
provider, and evaluator are replaced by small in-memory fakes.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_budget as sb
from guiexp_android import selective_pilot as pilot
from guiexp_android.budget_client import BudgetStop, OFFICIAL_BASE
from guiexp_android.budget_client_v9 import validate_metadata


MODEL = "z-ai/glm-5.3-flash"


def _plan(*, text_ref=False):
    """A real selective-plan object with one verified UI node."""
    action = {"action_type": "click"}
    slots = {"value": "the user supplied value"} if text_ref else {}
    target = {"text": "Save"} if not text_ref else {"hint": "Title", "editable": True}
    if text_ref:
        action = {
            "action_type": "input_text",
            "text": {"slot": "value", "transform": "identity"},
        }
    return {
        "schema": "selective-plan/1",
        "slots": slots,
        "steps": [
            {
                "id": "save",
                "intent": "save the supplied value",
                "action": action,
                "target": target,
                "before": [],
                "after": [{"text": "Done"}],
            }
        ],
    }


class FakeAdapter:
    """The exact adapter consumed by selective_runtime.run_plan."""

    def __init__(self, screens, *, events=None, max_actions=40):
        self.screens = [copy.deepcopy(screen) for screen in screens]
        self.events = events if events is not None else []
        self.executed = []
        self.max_actions = max_actions
        self.actions_sent = 0
        self.source = "unknown"

    def elements(self):
        return copy.deepcopy(self.screens[0])

    def observe(self):
        self.events.append("observe")
        return {"screen": [item.get("text", "") for item in self.screens[0]]}

    def execute(self, action):
        self.events.append("action")
        self.executed.append(copy.deepcopy(action))
        self.actions_sent += 1
        if len(self.screens) > 1:
            self.screens.pop(0)


def test_wrong_extraction_sentinel_reaches_runtime_and_never_evaluator_params(tmp_path):
    plan = _plan(text_ref=True)

    class ExtractionClient:
        episode = None

        def __init__(self):
            self.calls = []

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "choices": [{"message": {"content": '{"value":"WRONG_SENTINEL"}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0},
            }

    client = ExtractionClient()
    extracted, extraction_result = pilot._extract_binding(
        plan, "enter the requested value", client, "selective_20260915/test", tmp_path
    )
    assert extraction_result["status"] == "valid"
    assert extracted == {"value": "WRONG_SENTINEL"}
    evaluator_params = {"value": "RIGHT_EVALUATOR_VALUE"}

    adapter = FakeAdapter(
        [
            [{"index": 0, "text": "", "hint": "Title", "editable": True}],
            [{"index": 1, "text": "Done"}],
        ]
    )
    result = pilot._run_plan(plan, extracted, adapter, None, "full_fallback")
    assert evaluator_params["value"] != extracted["value"]
    assert result["status"] == "complete"
    assert adapter.executed == [{
        "action_type": "input_text",
        "text": "WRONG_SENTINEL",
        "index": 0,
    }]
    assert "reward" not in result


def test_local_verified_rejoin_runs_suffix_with_one_shared_action_cap():
    plan = {
        "schema": "selective-plan/1",
        "slots": {},
        "steps": [
            {
                "id": "first",
                "intent": "open the first screen",
                "action": {"action_type": "click"},
                "target": {"text": "First"},
                "before": [],
                "after": [{"text": "Done"}],
            },
            {
                "id": "suffix",
                "intent": "finish the task",
                "action": {"action_type": "click"},
                "target": {"text": "Continue"},
                "before": [{"text": "Done"}],
                "after": [{"text": "Final"}],
            },
        ],
    }
    adapter = FakeAdapter([
        [{"index": 1, "text": "First", "clickable": True}],
        [{"index": 2, "text": "Retry", "clickable": True}],
        [{"index": 3, "text": "Done"}, {"index": 4, "text": "Continue", "clickable": True}],
        [{"index": 5, "text": "Final"}],
    ])
    payloads = []

    def decision(payload):
        payloads.append(payload)
        return {
            "action": {"action_type": "click", "index": 2},
            "stop": False,
            "usage": {"completion_tokens": 1},
        }

    result = pilot._run_plan(plan, {}, adapter, decision, "local_rejoin")
    assert result["status"] == "complete"
    assert result["pc"] == 2
    assert result["actions"] == 3
    assert len(adapter.executed) == 3
    assert [item["action_type"] for item in adapter.executed] == ["click"] * 3
    assert payloads[0]["previous_action"] == {"action_type": "click", "index": 1}
    assert result["trace"][1]["kind"] == "local"
    assert result["trace"][2]["pc"] == 1

    capped = FakeAdapter([
        [{"index": 1, "text": "First", "clickable": True}],
        [{"index": 2, "text": "Retry", "clickable": True}],
        [{"index": 3, "text": "Done"}, {"index": 4, "text": "Continue", "clickable": True}],
        [{"index": 5, "text": "Final"}],
    ], max_actions=2)
    capped_result = pilot._run_plan(plan, {}, capped, decision, "local_rejoin")
    assert capped_result["status"] == "budget_exhausted"
    assert capped_result["actions"] == 2
    assert capped_result["pc"] == 1
    assert len(capped.executed) == 2


def test_model_budget_stop_propagates_without_fallback_or_retry(tmp_path):
    plan = _plan()
    adapter = FakeAdapter([
        [{"index": 1, "text": "Save", "clickable": True}],
        [{"index": 2, "text": "Retry", "clickable": True}],
    ])

    class StopClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            raise BudgetStop("model budget stop")

    client = StopClient()
    decision = pilot._make_local_decision(
        "goal", adapter, client, tmp_path, "selective_20260915/test"
    )
    with pytest.raises(BudgetStop, match="model budget stop"):
        pilot._run_plan(plan, {}, adapter, decision, "local_rejoin")
    assert client.calls == 1
    assert len(adapter.executed) == 1


class _Element:
    def __init__(self, *, text="", hint="", editable=False, clickable=False):
        self.text = text
        self.hint_text = hint
        self.content_description = ""
        self.is_editable = editable
        self.is_clickable = clickable


class _FakeAndroidWorld:
    def __init__(self, env):
        self.env = env

    def get_state(self, wait_to_stabilize=False):
        return SimpleNamespace(ui_elements=self.env.current_elements)

    def execute_action(self, action):
        self.env.events.append("action")
        self.env.current_elements = [_Element(text="Done")]


class _FakeEnv:
    wait_after_action_seconds = 0.0

    def __init__(self):
        self.events = []
        self.current_elements = []
        self.aw_env = _FakeAndroidWorld(self)

    def reset(self, task):
        self.events.append("reset")
        self.current_elements = [_Element(hint="Title", editable=True)]

    def observe(self, goal):
        return {"url": "fake.app", "goal_text": goal}

    def reward(self):
        return 1.0 if any(item.text == "Done" for item in self.current_elements) else 0.0

    def close(self):
        self.events.append("close")


class _GuardBudget:
    def __init__(self, events):
        self.events = events

    def before_ui_action(self):
        sb.before_ui_action(lambda: self.events.append("guard") or True)


class _ExtractionResponseClient:
    def __init__(self, text):
        self.text = text
        self.episode = None
        self.calls = []

    def begin_episode(self, episode):
        self.episode = episode

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "choices": [{"message": {"content": self.text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0},
        }


def test_pilot_reset_and_executor_action_call_host_guard_in_order(tmp_path, monkeypatch):
    import guiexp_android.android_env as android_env
    import guiexp_android.conditions as conditions

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family})
    monkeypatch.setattr(android_env, "goal_text", lambda task: "enter a value")
    monkeypatch.setattr(conditions, "build_prompt", lambda *args: "goal")

    events = []
    env = _FakeEnv()
    budget = _GuardBudget(events)
    env.events = events
    client = _ExtractionResponseClient('{"value":"WRONG_SENTINEL"}')
    plan = _plan(text_ref=True)
    row = {
        "id": "selective_20260915/integration/full_fallback",
        "family": "MarkorCreateNote",
        "arm": "full_fallback",
        "binding_id": "b01",
        "seed": 0,
    }
    binding_row = {"params": {"value": "EVALUATOR_VALUE"}}
    final = pilot._run_one_episode(
        tmp_path,
        {"action_budget": 3},
        row,
        binding_row,
        env=env,
        client=client,
        budget=budget,
        plan=plan,
    )
    assert final["success"] is True
    assert events[:4] == ["guard", "reset", "guard", "action"]
    assert client.calls and client.calls[0]["model"] == MODEL
    steps = [
        json.loads(line)
        for line in (tmp_path / "episodes" / row["id"] / "trajectory.jsonl").read_text().splitlines()
        if line.strip()
    ]
    action_steps = [item for item in steps if item.get("record_type") == "step"]
    assert action_steps[0]["action"]["text"] == "WRONG_SENTINEL"


def _metadata(model=MODEL):
    policy = sb.MODEL_LOCKS[model]
    return {
        "data": {
            "id": model,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [{
                "tag": policy["provider"],
                "status": 0,
                "context_length": 1048576,
                "max_completion_tokens": 131072,
                "supported_parameters": ["max_tokens", "temperature"],
                "pricing": {
                    "prompt": str(float(policy["prompt_per_m"]) / 1000000),
                    "completion": str(float(policy["completion_per_m"]) / 1000000),
                },
            }],
        }
    }


class _Clock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class _SDKResponse:
    def __init__(self, content='{"action_type":"wait"}'):
        self.raw = {
            "id": "gen-selective-integration",
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": "0.001"},
        }
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(prompt_tokens=2, completion_tokens=1, cost=0.001)

    def model_dump(self, mode="json"):
        return self.raw


class _SDK:
    max_retries = 0
    base_url = OFFICIAL_BASE

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _SDKResponse(self.outcomes.pop(0) if self.outcomes else '{"action_type":"wait"}')


def test_real_selective_budget_client_keeps_one_episode_identity_across_agent_calls(tmp_path):
    clock = _Clock()
    guards = []

    def host_guard():
        guards.append("guard")
        return True

    lock = validate_metadata(MODEL, _metadata())
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", now=clock.now, host_guard=host_guard)
    sdk = _SDK()
    client = sb.make_client(
        {MODEL: lock},
        ledger=ledger,
        sdk=sdk,
        host_guard=host_guard,
        metadata_fetcher=_metadata,
        sleep=clock.sleep,
    )
    episode = "selective_20260915/train/integration"
    client.begin_episode(episode)
    agent = pilot._agent(MODEL, client)
    obs = {"url": "fake", "ax_tree_text": ""}
    agent.act("goal", obs)
    agent.act(None, obs)
    assert len(sdk.calls) == 2
    with ledger.connect() as database:
        rows = database.execute("SELECT id,episode FROM calls ORDER BY created").fetchall()
    assert len(rows) == 2
    assert all(call_id.startswith("selective_20260915/") for call_id, _ in rows)
    assert {episode_value for _, episode_value in rows} == {episode}
    assert guards
    with pytest.raises(BudgetStop, match="earlier request"):
        client.begin_episode(episode)
    assert len(sdk.calls) == 2


def test_reactive_pilot_begins_real_budget_episode_before_agent_act(tmp_path, monkeypatch):
    """AndroidAgent.act bypasses pilot._call_client, so the phase must begin it."""
    import guiexp_android.android_env as android_env
    import guiexp_android.conditions as conditions

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family})
    monkeypatch.setattr(android_env, "goal_text", lambda task: "wait for completion")
    monkeypatch.setattr(conditions, "build_prompt", lambda *args: "goal")

    clock = _Clock()
    lock = validate_metadata(MODEL, _metadata())
    sdk = _SDK([
        '{"action_type":"wait"}',
        '{"action_type":"status","goal_status":"complete"}',
    ])
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", now=clock.now, host_guard=lambda: True)
    client = sb.make_client(
        {MODEL: lock},
        ledger=ledger,
        sdk=sdk,
        host_guard=lambda: True,
        metadata_fetcher=_metadata,
        sleep=clock.sleep,
    )
    episode = "selective_20260915/reactive/integration"
    begun = []
    original_begin = client.begin_episode

    def begin_once(value):
        begun.append(value)
        return original_begin(value)

    client.begin_episode = begin_once
    env = _FakeEnv()
    events = env.events
    budget = _GuardBudget(events)
    row = {
        "id": episode,
        "family": "MarkorCreateNote",
        "arm": "reactive",
        "binding_id": "b01",
        "seed": 0,
    }
    final = pilot._run_one_episode(
        tmp_path,
        {"action_budget": 3},
        row,
        {"params": {"value": "private evaluator value"}},
        env=env,
        client=client,
        budget=budget,
        plan=None,
    )
    assert final["success"] is True
    assert begun == [episode]
    assert len(sdk.calls) == 2
    with ledger.connect() as database:
        rows = database.execute("SELECT id,episode FROM calls ORDER BY created").fetchall()
    assert len(rows) == 2
    assert {row[1] for row in rows} == {episode}
    with pytest.raises(BudgetStop, match="earlier request"):
        client.begin_episode(episode)
    assert len(sdk.calls) == 2


def test_runtime_source_has_no_dynamic_execution_or_evaluator_calls():
    source_path = Path(__file__).parents[1] / "selective_runtime.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    forbidden_calls = {"eval", "exec", "compile", "open", "reward", "is_successful"}
    called_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called_names.add(node.func.attr)
    assert called_names.isdisjoint(forbidden_calls)
    imported_modules = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported_modules.isdisjoint({"os", "subprocess", "sqlite3", "android_world"})
