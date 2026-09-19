"""Focused tests for the optional compact recovery context."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot
from guiexp_android.budget_client import BudgetStop
from guiexp_android.selective_recovery_context import (
    CURRENT_PLUS_ACTIONS,
    RecoveryContextError,
    build_context,
    validate_policy,
)


def _records():
    return [
        {"action": {"action_type": "open_app", "app_name": "Markor"}},
        {"action": {"action_type": "click", "index": 7}},
    ]


def test_default_policy_returns_no_context_packet():
    assert build_context(
        None,
        goal_text="full goal",
        goal_prompt="all constraints",
        observation={"url": "app", "screenshot_b64": "IMAGE", "ax_tree_text": "AX"},
        issued_records=_records(),
        node_intent="continue",
        remaining_actions=38,
    ) is None


def test_current_plus_actions_is_deterministic_and_retains_all_semantic_actions():
    kwargs = {
        "goal_text": "full goal with every constraint",
        "goal_prompt": "do not change the destination",
        "observation": {
            "url": "net.gsantner.markor/DocumentActivity",
            "screenshot_b64": "CURRENT_IMAGE",
            "ax_tree_text": "CURRENT_AX",
        },
        "issued_records": _records(),
        "node_intent": "finish the current guarded node",
        "remaining_actions": 38,
    }
    first = build_context(CURRENT_PLUS_ACTIONS, **kwargs)
    second = build_context(CURRENT_PLUS_ACTIONS, **kwargs)
    assert first is not None and second is not None
    assert first.digest == second.digest
    assert first.byte_length == second.byte_length
    assert first.packet["already_issued_actions"] == [
        {"action_type": "open_app", "app_name": "Markor"},
        {"action_type": "click", "index": 7},
    ]
    assert first.packet["goal_text"] == kwargs["goal_text"]
    assert kwargs["goal_text"] not in first.prompt
    assert kwargs["goal_prompt"] in first.prompt
    assert kwargs["node_intent"] in first.prompt
    assert "CURRENT_IMAGE" not in first.prompt
    assert "CURRENT_AX" not in first.prompt


def test_unknown_policy_fails_before_building_a_packet():
    with pytest.raises(RecoveryContextError, match="unknown recovery_context_policy"):
        validate_policy("old_history")
    with pytest.raises(RecoveryContextError, match="unknown recovery_context_policy"):
        build_context(
            "old_history",
            goal_text="goal",
            goal_prompt="constraints",
            observation={},
            issued_records=[],
            node_intent=None,
            remaining_actions=40,
        )


def test_action_history_cannot_exceed_the_shared_cap():
    with pytest.raises(RecoveryContextError, match="40-action cap"):
        build_context(
            CURRENT_PLUS_ACTIONS,
            goal_text="goal",
            goal_prompt="constraints",
            observation={},
            issued_records=[{"action": {"action_type": "wait"}} for _ in range(41)],
            node_intent=None,
            remaining_actions=0,
        )


def test_reactive_continuation_uses_fresh_agents_and_current_observation_each_time(tmp_path, monkeypatch):
    class Executor:
        goal_text = "Complete the full original task with all constraints."
        max_actions = 40

        def __init__(self):
            self.actions_sent = 2
            self.records = [
                {"action": {"action_type": "open_app", "app_name": "Markor"}},
                {"action": {"action_type": "click", "index": 4}},
            ]

        def observe(self):
            return {
                "url": "net.gsantner.markor/DocumentActivity",
                "screenshot_b64": "CURRENT_IMAGE",
                "ax_tree_text": "CURRENT_AX",
            }

        def execute(self, action):
            self.actions_sent += 1
            self.records.append({"action": dict(action)})

    executor = Executor()
    agents = []
    requests = []

    class Agent:
        def __init__(self):
            agents.append(self)

        def act(self, prompt, observation):
            requests.append((self, prompt, observation))
            if len(requests) == 1:
                return '{"action_type":"wait"}', {"prompt_tokens": 1, "completion_tokens": 1}
            return '{"action_type":"status","goal_status":"complete"}', {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(pilot, "_agent", lambda model, client: Agent())
    out = tmp_path / "episode"
    out.mkdir()
    result = pilot._reactive_loop(
        executor,
        "Complete the full original task with all constraints.",
        "selective_20260915/test/current_plus_actions",
        object(),
        out,
        executor.observe(),
        purpose="full_fallback",
        recovery_context_policy=CURRENT_PLUS_ACTIONS,
        node_intent="continue the current guarded task",
    )
    assert result["status"] == "model_terminal"
    assert len(agents) == 2
    assert requests[0][0] is not requests[1][0]
    assert all(item[2]["screenshot_b64"] == "CURRENT_IMAGE" for item in requests)
    assert all(item[2]["ax_tree_text"] == "CURRENT_AX" for item in requests)
    assert all("CURRENT_IMAGE" not in item[1] and "CURRENT_AX" not in item[1] for item in requests)
    assert "Complete the full original task" in requests[0][1]
    assert "continue the current guarded task" in requests[0][1]
    assert "continue the current guarded task" not in requests[1][1]
    assert "Full task request and all constraints" in requests[1][1]
    assert '"action_type":"open_app"' in requests[1][1]
    assert '"action_type":"wait"' in requests[1][1]
    calls = [
        __import__("json").loads(line)
        for line in (out / "trajectory.jsonl").read_text().splitlines()
        if line.strip()
    ]
    model_calls = [item for item in calls if item.get("record_type") == "model_call"]
    assert len(model_calls) == 2
    assert all(item["recovery_context_policy"] == CURRENT_PLUS_ACTIONS for item in model_calls)
    assert all(isinstance(item["recovery_context_digest"], str) for item in model_calls)
    assert all(isinstance(item["recovery_context_bytes"], int) for item in model_calls)


def test_real_android_agent_requests_have_only_the_current_image_and_ax(tmp_path):
    class Executor:
        goal_text = "full goal"
        max_actions = 40

        def __init__(self):
            self.actions_sent = 0
            self.records = []
            self.image = "IMAGE_ONE"
            self.ax = "AX_ONE"

        def observe(self):
            return {"url": "app", "screenshot_b64": self.image, "ax_tree_text": self.ax}

        def execute(self, action):
            self.actions_sent += 1
            self.records.append({"action": dict(action)})
            self.image = "IMAGE_TWO"
            self.ax = "AX_TWO"

    class Response:
        def __init__(self, text):
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=text))]
            self.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.001)

    class Client:
        def __init__(self):
            self.calls = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            self.calls.append(kwargs)
            text = '{"action_type":"wait"}' if len(self.calls) == 1 else '{"action_type":"status","goal_status":"complete"}'
            return Response(text)

    executor = Executor()
    client = Client()
    out = tmp_path / "episode"
    out.mkdir()
    result = pilot._reactive_loop(
        executor,
        "all constraints",
        "selective_20260915/test/current",
        client,
        out,
        executor.observe(),
        recovery_context_policy=CURRENT_PLUS_ACTIONS,
    )
    assert result["status"] == "model_terminal"
    assert len(client.calls) == 2
    first_content = client.calls[0]["messages"][1]["content"]
    second_content = client.calls[1]["messages"][1]["content"]
    assert any(item.get("image_url", {}).get("url", "").endswith("IMAGE_ONE") for item in first_content if isinstance(item, dict))
    assert any(item.get("image_url", {}).get("url", "").endswith("IMAGE_TWO") for item in second_content if isinstance(item, dict))
    assert "IMAGE_ONE" not in json.dumps(second_content)
    assert "AX_ONE" not in json.dumps(second_content)
    assert "AX_TWO" in json.dumps(second_content)
    assert all(len(call["messages"]) == 2 for call in client.calls)


def test_local_decision_uses_the_same_context_policy_and_all_issued_actions(tmp_path, monkeypatch):
    class Executor:
        goal_text = "Keep the full goal."
        max_actions = 40

        def __init__(self):
            self.actions_sent = 1
            self.records = [{"action": {"action_type": "click", "index": 1}}]

        def observe(self):
            return {"url": "app", "screenshot_b64": "IMAGE", "ax_tree_text": "AX"}

    prompts = []
    agents = []

    class Agent:
        def __init__(self):
            agents.append(self)

        def act(self, prompt, observation):
            prompts.append((prompt, observation))
            return '{"action_type":"wait"}', {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setattr(pilot, "_agent", lambda model, client: Agent())
    out = tmp_path / "episode"
    out.mkdir()
    executor = Executor()
    decide = pilot._make_local_decision(
        "full constraints",
        executor,
        object(),
        out,
        "selective_20260915/test/local",
        recovery_context_policy=CURRENT_PLUS_ACTIONS,
    )
    decide({"intent": "repair this node", "pc": 1, "observation": executor.observe()})
    executor.records.append({"action": {"action_type": "wait"}})
    # The callback owns the original executor. A second call confirms it grows
    # its packet from that same action history.
    decide({"intent": "repair this node", "pc": 1, "observation": executor.observe()})
    assert len(agents) == 2
    assert agents[0] is not agents[1]
    assert '"action_type":"click"' in prompts[0][0]
    assert '"action_type":"wait"' in prompts[1][0]
    assert "full constraints" in prompts[0][0]
    assert prompts[0][1]["screenshot_b64"] == "IMAGE"
    assert "IMAGE" not in prompts[0][0] and "AX" not in prompts[0][0]


def test_unknown_recovery_policy_fails_before_episode_ui_or_client_use(tmp_path):
    class Env:
        reset_called = False

        def reset(self, task):
            self.reset_called = True

        def close(self):
            return None

    class Client:
        begin_called = False

        def begin_episode(self, episode):
            self.begin_called = True

    env, client = Env(), Client()
    with pytest.raises(RecoveryContextError):
        pilot._run_one_episode(
            tmp_path,
            {"action_budget": 40},
            {
                "id": "selective_20260915/test/episode",
                "family": "MarkorCreateNote",
                "arm": "reactive",
                "binding_id": "b01",
                "seed": 915201,
            },
            {},
            env=env,
            client=client,
            budget=object(),
            plan=None,
            recovery_context_policy="unsupported",
        )
    assert env.reset_called is False
    assert client.begin_called is False
    assert not (tmp_path / "episodes").exists()


def test_budget_stop_from_a_recovery_decision_propagates_without_a_retry(tmp_path, monkeypatch):
    class Executor:
        goal_text = "full goal"
        max_actions = 40

        def __init__(self):
            self.actions_sent = 0
            self.records = []

        def observe(self):
            return {"url": "app", "screenshot_b64": "IMAGE", "ax_tree_text": "AX"}

    calls = []

    class Agent:
        def act(self, prompt, observation):
            calls.append((prompt, observation))
            raise BudgetStop("missing settled billing evidence")

    monkeypatch.setattr(pilot, "_agent", lambda model, client: Agent())
    executor = Executor()
    out = tmp_path / "episode"
    out.mkdir()
    with pytest.raises(BudgetStop, match="missing settled billing"):
        pilot._reactive_loop(
            executor,
            "full constraints",
            "selective_20260915/test/budget",
            object(),
            out,
            executor.observe(),
            recovery_context_policy=CURRENT_PLUS_ACTIONS,
        )
    assert len(calls) == 1
    assert executor.actions_sent == 0
