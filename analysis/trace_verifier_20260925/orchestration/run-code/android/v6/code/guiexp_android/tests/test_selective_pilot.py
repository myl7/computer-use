"""Offline protocol tests for the selective pilot harness."""

from __future__ import annotations

import contextlib
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot


def _plan() -> dict:
    return {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "the value supplied by the task request"},
        "steps": [
            {
                "id": "save",
                "intent": "save the entered value",
                "action": {"action_type": "click"},
                "target": {"text": "Save", "clickable": True},
                "before": [{"text": "Save", "clickable": True}],
                "after": [{"text": "Saved", "clickable": True}],
            }
        ],
    }


def test_prepare_has_18_balanced_rows_and_private_bindings(tmp_path, monkeypatch):
    def params(family, seed):
        return {"value": f"private-{family}-{seed}"}

    monkeypatch.setattr(pilot, "_test_params", params)
    monkeypatch.setattr(pilot, "historical_inventory", lambda *args, **kwargs: {
        "historical_hashes": [],
        "historical_binding_hashes": [],
        "raw_value_sha256": [],
    })
    monkeypatch.setattr(pilot, "_find_complete_historical_demos", lambda *args, **kwargs: [])

    out = tmp_path / "selective"
    spec = pilot.prepare(out, historical_root=tmp_path / "old", t16_root=tmp_path / "t16")
    assert len(spec["episodes"]) == 18
    assert {row["arm"] for row in spec["episodes"]} == set(pilot.ARMS)
    assert all(row["id"].startswith("selective_20260915/") for row in spec["episodes"])
    private = out / spec["private_bindings"]
    assert private.stat().st_mode & 0o077 == 0
    public = (out / "spec.json").read_text()
    assert "private-MarkorCreateNote" not in public
    assert pilot.load_spec(out)["spec_sha256"] == spec["spec_sha256"]


def test_episode_order_is_position_balanced_and_reverses_within_family():
    refs = {
        family: [{"seed": 915201}, {"seed": 915202}]
        for family in pilot.FAMILIES
    }
    rows = pilot._episode_rows(refs)
    triplets = [
        [row["arm"] for row in rows[index : index + 3]]
        for index in range(0, len(rows), 3)
    ]
    assert triplets == [
        ["reactive", "full_fallback", "local_rejoin"],
        ["local_rejoin", "full_fallback", "reactive"],
        ["full_fallback", "local_rejoin", "reactive"],
        ["reactive", "local_rejoin", "full_fallback"],
        ["local_rejoin", "reactive", "full_fallback"],
        ["full_fallback", "reactive", "local_rejoin"],
    ]
    for family_index in range(3):
        assert triplets[family_index * 2 + 1] == list(reversed(triplets[family_index * 2]))
    assert all(sum(triplet[position] == arm for triplet in triplets) == 2 for arm in pilot.ARMS for position in range(3))


def test_semantic_binding_hash_ignores_setup_noise_but_params_hash_keeps_it():
    left = {
        "file_name": "clip.mp3",
        "source_folder": "Music",
        "destination_folder": "Podcasts",
        "noise_candidates": ["one.mp3"],
    }
    right = dict(left, noise_candidates=["different.mp3", "other.mp3"])
    first = pilot.binding_hashes(left, "FilesMoveFile")
    second = pilot.binding_hashes(right, "FilesMoveFile")
    assert first["binding_sha256"] == second["binding_sha256"]
    assert first["params_sha256"] != second["params_sha256"]
    inventory = {"historical_hashes": [], "historical_binding_hashes": [first["binding_sha256"]]}
    assert pilot._is_old_binding(right, inventory)


def _training_fakes(tmp_path, monkeypatch):
    import guiexp_android.android_env as android_env
    import guiexp_android.conditions as conditions

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family, "seed": seed})
    monkeypatch.setattr(android_env, "goal_text", lambda task: f"goal-{task['family']}-{task['seed']}")
    monkeypatch.setattr(conditions, "build_prompt", lambda *args: "goal")

    class FakeEnv:
        def __init__(self):
            self.closed = 0
            self.reset_calls = 0

        def reset(self, task):
            self.reset_calls += 1

        def observe(self, goal):
            return {"url": "fake", "ax_tree_text": "", "goal_text": goal}

        def reward(self):
            return 1.0

        def close(self):
            self.closed += 1

    class Ledger:
        path = pilot.SHARED_LEDGER

        def __init__(self):
            self.receipts = set()

        def has_episode(self, episode):
            return episode in self.receipts

        def summary(self):
            return {"actual_usd": "0", "unresolved_reserved_usd": "0"}

    ledger = Ledger()
    envs = []
    clients = []

    class FakeClient:
        def __init__(self, episode):
            self.episode = None
            self.episode_name = episode
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.calls += 1
            ledger.receipts.add(self.episode)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"action_type":"status","goal_status":"complete"}'))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.001),
            )

    class FakeBudget:
        def make_ledger(self):
            return ledger

        def validate_locks(self, locks):
            return dict(locks)

        def exclusive_run(self):
            return contextlib.nullcontext()

        def before_ui_action(self):
            return True

        def load_manifest(self, path):
            return json.loads(Path(path).read_text())

    def client_factory(_spec, episode):
        client = FakeClient(episode)
        clients.append(client)
        return client

    def env_factory():
        env = FakeEnv()
        envs.append(env)
        return env

    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: FakeBudget())
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: SimpleNamespace())
    return client_factory, env_factory, clients, envs, ledger


def test_train_second_invocation_skips_completed_trace_and_persists_between_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "_test_params", lambda family, seed: {"file_name": f"f{seed}.txt", "text": f"t{seed}"} if family == "MarkorCreateNote" else {"file_name": f"f{seed}.txt"})
    monkeypatch.setattr(pilot, "historical_inventory", lambda *args, **kwargs: {"historical_hashes": [], "historical_binding_hashes": [], "raw_value_sha256": []})
    monkeypatch.setattr(pilot, "_find_complete_historical_demos", lambda *args, **kwargs: [])
    out = tmp_path / "selective"
    pilot.prepare(out, historical_root=tmp_path / "old", t16_root=tmp_path / "t16")
    client_factory, env_factory, clients, envs, _ledger = _training_fakes(tmp_path, monkeypatch)

    first = pilot.train(out, max_episodes=1, client_factory=client_factory, env_factory=env_factory)
    assert first["families"]["MarkorCreateNote"][0]["success"] is True
    first_trace = out / first["families"]["MarkorCreateNote"][0]["trajectory"]
    first_bytes = first_trace.read_bytes()
    first_client_calls = clients[0].calls
    first_env_count = len(envs)

    second = pilot.train(out, max_episodes=1, client_factory=client_factory, env_factory=env_factory)
    assert first_trace.read_bytes() == first_bytes
    assert clients[0].calls == first_client_calls
    assert len(envs) == first_env_count + 1
    assert second["families"]["FilesMoveFile"][0]["success"] is True
    assert len(clients) == 2


def test_train_prior_receipt_without_state_or_trace_is_terminal_before_new_io(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "_test_params", lambda family, seed: {"file_name": f"f{seed}.txt", "text": f"t{seed}"} if family == "MarkorCreateNote" else {"file_name": f"f{seed}.txt"})
    monkeypatch.setattr(pilot, "historical_inventory", lambda *args, **kwargs: {"historical_hashes": [], "historical_binding_hashes": [], "raw_value_sha256": []})
    monkeypatch.setattr(pilot, "_find_complete_historical_demos", lambda *args, **kwargs: [])
    out = tmp_path / "selective"
    pilot.prepare(out, historical_root=tmp_path / "old", t16_root=tmp_path / "t16")
    client_factory, env_factory, clients, envs, ledger = _training_fakes(tmp_path, monkeypatch)
    prior = f"{pilot.NAMESPACE}/train/MarkorCreateNote/s{pilot.TRAIN_SEED_START}/a0"
    ledger.receipts.add(prior)
    result = pilot.train(out, max_episodes=1, client_factory=client_factory, env_factory=env_factory)
    state_path = out / "training" / "MarkorCreateNote" / f"s{pilot.TRAIN_SEED_START}_a0" / "state.json"
    trace_path = state_path.parent / "trajectory.jsonl"
    assert result["families"]["MarkorCreateNote"][0]["status"] == "prior_receipt"
    assert json.loads(state_path.read_text())["status"] == "prior_receipt"
    assert not trace_path.exists()
    assert clients == []
    assert envs == []


def test_plan_schema_is_runtime_compatible_and_unsafe_templates_fail():
    plan = _plan()
    assert pilot.validate_plan(plan)["schema"] == pilot.PLAN_SCHEMA
    bad = json.loads(json.dumps(plan))
    bad["steps"][0]["action"]["text"] = {"slot": "value", "transform": "eval"}
    with pytest.raises(pilot.PlanInvalid):
        pilot.validate_plan(bad)


def test_local_plan_validator_accepts_wrapped_refs_and_rejects_bad_wrapper_fields():
    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"name": "the requested name"},
        "steps": [{
            "id": "type_name",
            "intent": "type the requested name",
            "action": {
                "action_type": "input_text",
                "text": {"slot": "name", "transform": "identity"},
            },
            "target": {
                "text": {
                    "slot": "name",
                    "transform": "identity",
                    "prefix": "Delete ",
                    "suffix": "",
                },
                "editable": True,
            },
            "before": [],
            "after": [{"text": "Done"}],
        }],
    }
    assert pilot.validate_plan_local(plan)["slots"] == {"name": "the requested name"}
    assert pilot.validate_plan(plan)["schema"] == pilot.PLAN_SCHEMA

    malformed = copy.deepcopy(plan)
    malformed["steps"][0]["target"]["text"]["suffix"] = 7
    with pytest.raises(pilot.PlanInvalid, match="suffix"):
        pilot.validate_plan_local(malformed)

    unknown = copy.deepcopy(plan)
    unknown["steps"][0]["target"]["text"]["unsafe"] = "field"
    with pytest.raises(pilot.PlanInvalid, match="slot reference keys"):
        pilot.validate_plan_local(unknown)


def test_effect_guard_requires_matching_training_evidence():
    with pytest.raises(pilot.PlanInvalid, match="unknown"):
        pilot.validate_effect_guards(_plan(), [{"steps": []}])


def test_ledger_receipt_lookup_fails_closed():
    class BrokenLedger:
        def has_episode(self, episode):
            raise OSError("ledger unavailable")

    with pytest.raises(pilot.BudgetStop):
        pilot._ledger_has_episode(BrokenLedger(), "selective_20260915/train/x")


def test_run_refuses_reactive_serving_when_no_family_plan_exists(tmp_path, monkeypatch):
    class Ledger:
        path = pilot.SHARED_LEDGER

        def summary(self):
            return {"actual_usd": "0", "unresolved_reserved_usd": "0"}

    class Budget:
        def make_ledger(self):
            return Ledger()

        def exclusive_run(self):
            raise AssertionError("no-plan check must happen before the paid run lock")

    spec = {
        "spec_sha256": "test",
        "action_budget": pilot.MAX_ACTIONS,
        "ledger_absolute": str(pilot.SHARED_LEDGER),
        "episodes": [{
            "id": "selective_20260915/MarkorCreateNote/b01/reactive",
            "family": "MarkorCreateNote",
            "arm": "reactive",
            "binding_id": "b01",
            "seed": 915201,
        }],
        "plans": {family: f"plans/{family}.json" for family in pilot.FAMILIES},
    }
    monkeypatch.setattr(pilot, "load_spec", lambda out: spec)
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: Budget())
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: SimpleNamespace())
    monkeypatch.setattr(pilot, "_new_env", lambda: (_ for _ in ()).throw(AssertionError("env must not start")))
    result = pilot.run(tmp_path)
    assert result["batch_status"] == "no_plans"
    assert "reactive serving was not started" in result["stop_reason"]


def test_cli_exit_code_distinguishes_unbuildable_and_budget_stops():
    assert pilot._cli_exit_code("build", {"MarkorCreateNote": {"status": "unbuildable"}}) == 3
    assert pilot._cli_exit_code("build", {"MarkorCreateNote": {"status": "budget_stopped"}}) == 2
    assert pilot._cli_exit_code("run", {"batch_status": "no_plans", "counts": {"pending": 18}}) == 3
    assert pilot._cli_exit_code("run", {"batch_status": "budget_stopped", "counts": {"pending": 17}}) == 2


def test_build_saves_first_invalid_response_and_repairs_once(tmp_path, monkeypatch):
    # Keep preparation deterministic and independent of android_world.
    monkeypatch.setattr(pilot, "_test_params", lambda family, seed: {"value": f"private-{seed}"})
    monkeypatch.setattr(pilot, "historical_inventory", lambda *args, **kwargs: {
        "historical_hashes": [], "historical_binding_hashes": [], "raw_value_sha256": []
    })
    monkeypatch.setattr(pilot, "_find_complete_historical_demos", lambda *args, **kwargs: [])
    out = tmp_path / "selective"
    spec = pilot.prepare(out, historical_root=tmp_path / "old", t16_root=tmp_path / "t16")

    demo_dir = out / "training" / "MarkorCreateNote" / "s915101_a0"
    demo_dir.mkdir(parents=True)
    demo = {
        "record_type": "initial",
        "goal_text": "Use training-only-value in the app.",
        "obs": {"url": "home", "ax_tree_text": "Save", "screenshot_files": {}},
    }
    step = {
        "record_type": "step",
        "step": 1,
        "action": {"action_type": "click", "index": 4},
        "pre_obs": {"url": "form", "ax_tree_text": "Save", "screenshot_files": {}},
        "post_obs": {"url": "form", "ax_tree_text": "Saved", "screenshot_files": {}},
        "screenshot_files": {},
    }
    (demo_dir / "trajectory.jsonl").write_text(
        "\n".join(json.dumps(value) for value in (demo, step, {"record_type": "final", "success": True})) + "\n"
    )
    manifest = {
        "record_type": "selective-training",
        "status": "partial",
        "families": {
            "MarkorCreateNote": [{
                "success": True,
                "goal_text": "Use training-only-value in the app.",
                "trajectory": "training/MarkorCreateNote/s915101_a0/trajectory.jsonl",
            }],
            "FilesMoveFile": [],
            "MarkorDeleteNote": [],
        },
    }
    pilot.atomic_json(out / "training_manifest.json", manifest, private=True)

    class Ledger:
        path = pilot.SHARED_LEDGER

    class FakeBudget:
        def make_ledger(self):
            return Ledger()

        def validate_locks(self, locks):
            return dict(locks)

        def exclusive_run(self):
            return contextlib.nullcontext()

        def load_manifest(self, path):
            return json.loads(Path(path).read_text())

    class Client:
        def __init__(self):
            self.calls = []
            self.episode = None

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                text = "this is not JSON"
            else:
                text = json.dumps(_plan())
            return {
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": None, "cost": "0.001"},
            }

    client = Client()
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: FakeBudget())
    result = pilot.build(out, client_factory=lambda _spec, _episode: client)
    assert result["MarkorCreateNote"]["status"] == "built"
    assert result["MarkorCreateNote"]["calls"] == 2
    assert (out / "build/MarkorCreateNote/first_response.json").is_file()
    assert (out / "build/MarkorCreateNote/repair_response.json").is_file()
    prompt = client.calls[0]["messages"][1]["content"]
    assert "training-only-value" in prompt
    assert "private-" not in prompt
    assert len(client.calls) == 2


def test_compiled_arm_passes_extracted_binding_only_to_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "_begin_episode", lambda client, episode: None)

    class FakeEnv:
        def __init__(self):
            self.aw_env = SimpleNamespace()
            self.wait_after_action_seconds = 0

        def reset(self, task):
            return None

        def observe(self, goal):
            return {"url": "home", "ax_tree_text": "", "goal_text": goal}

        def reward(self):
            return 1.0

        def close(self):
            return None

    class FakeBudget:
        def before_ui_action(self):
            return True

    class FakeRuntime:
        def __init__(self):
            self.seen = None

        def validate_plan(self, plan):
            return {"valid": True, "plan": plan}

        def run_plan(self, **kwargs):
            self.seen = kwargs
            return {"status": "complete", "trace": [], "actions": 0}

    runtime = FakeRuntime()
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: runtime)
    fake_android = SimpleNamespace(
        get_task=lambda family, condition, seed: SimpleNamespace(family=family, seed=seed),
        goal_text=lambda task: "goal text",
    )
    monkeypatch.setitem(__import__("sys").modules, "guiexp_android.android_env", fake_android)
    monkeypatch.setattr("guiexp_android.conditions.build_prompt", lambda *args: "goal prompt")
    monkeypatch.setattr(
        pilot,
        "_extract_binding",
        lambda plan, goal, client, episode, out: ({"value": "EXTRACTED"}, {"status": "valid"}),
    )
    spec = {
        "action_budget": pilot.MAX_ACTIONS,
        "spec_sha256": "test",
    }
    row = {
        "id": "selective_20260915/MarkorCreateNote/b01/full_fallback",
        "family": "MarkorCreateNote",
        "arm": "full_fallback",
        "binding_id": "b01",
        "seed": 915201,
    }
    private = {"params": {"value": "PRIVATE_EVALUATOR_SENTINEL"}}
    captured = {}

    def fake_plan(plan, bindings, executor, decision, mode):
        captured["bindings"] = bindings
        return {"status": "complete"}

    monkeypatch.setattr(pilot, "_run_plan", fake_plan)
    result = pilot._run_one_episode(
        tmp_path,
        spec,
        row,
        private,
        env=FakeEnv(),
        client=SimpleNamespace(episode=None, begin_episode=lambda episode: None),
        budget=FakeBudget(),
        plan=_plan(),
    )
    assert result["success"] is True
    assert captured["bindings"] == {"value": "EXTRACTED"}
    assert "PRIVATE_EVALUATOR_SENTINEL" not in json.dumps(captured)


def test_runtime_call_uses_real_exact_plan_api(monkeypatch):
    class FakeRuntime:
        def __init__(self):
            self.kwargs = None

        def run_plan(self, **kwargs):
            self.kwargs = kwargs
            return {"status": "complete"}

    runtime = FakeRuntime()
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: runtime)
    adapter = object.__new__(pilot.AndroidActionExecutor)
    adapter.max_actions = 40
    adapter.actions_sent = 7
    adapter.source = ""
    result = pilot._run_plan(_plan(), {"value": "safe"}, adapter, None, "full_fallback")
    assert result["status"] == "complete"
    assert runtime.kwargs["bindings"] == {"value": "safe"}
    assert runtime.kwargs["adapter"] is adapter
    assert runtime.kwargs["max_actions"] == 33
