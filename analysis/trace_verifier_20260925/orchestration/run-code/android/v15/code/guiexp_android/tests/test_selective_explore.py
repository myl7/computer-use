"""Offline preparation and bounded build checks for provider_v3."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

from guiexp_android import selective_explore as explore
from guiexp_android import selective_pilot as pilot
from guiexp_android import selective_explore_budget as sb


def test_prepare_provider_v3_keeps_origin_and_private_binding_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(explore, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(explore, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    out = tmp_path / "provider_v3"
    spec = explore.prepare(pilot.DEFAULT_OUT, out)
    loaded = explore._load(out)
    origin = json.loads((out / "origin.json").read_text())
    assert loaded["spec_sha256"] == spec["spec_sha256"]
    assert loaded["provider"] == "wafer"
    assert loaded["builder_profile"]["max_tokens"] == 16384
    assert loaded["builder_profile"]["reasoning"] == {"effort": "low"}
    assert len(loaded["episodes"]) == 15
    assert len(loaded["imported_rows"]) == 9
    assert origin["training"]["readonly"] is True
    assert (out / loaded["private_bindings"]).stat().st_mode & 0o077 == 0
    assert all(row["id"].startswith("selective_20260915/provider_v3/") for row in loaded["episodes"])


def test_build_uses_provider_v3_profile_and_one_initial_call(tmp_path, monkeypatch):
    monkeypatch.setattr(explore, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(explore, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    out = tmp_path / "provider_v3"
    explore.prepare(pilot.DEFAULT_OUT, out)
    demo = {
        "family": "MarkorCreateNote",
        "goal_text": "training goal",
        "steps": [{
            "step": 1,
            "action": {"action_type": "click"},
            "pre_obs": {"url": "form", "ax_tree_text": "Save"},
            "post_obs": {"url": "form", "ax_tree_text": "Saved"},
        }],
    }
    monkeypatch.setattr(explore, "_build_demos", lambda _out, _spec, family: [demo] if family == "MarkorCreateNote" else [])

    class Ledger:
        path = pilot.SHARED_LEDGER

    class Client:
        def __init__(self):
            self.episode = None
            self.calls = []

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.calls.append(kwargs)
            plan = {
                "schema": pilot.PLAN_SCHEMA,
                "slots": {"value": "unused"},
                "steps": [{
                    "id": "save",
                    "intent": "save the form",
                    "action": {"action_type": "click"},
                    "target": {"text": "Save"},
                    "before": [{"text": "Save"}],
                    "after": [{"text": "Saved"}],
                }],
            }
            return {"choices": [{"message": {"content": json.dumps(plan)}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": "0.001"}}

    client = Client()

    class Budget:
        def make_ledger(self):
            return Ledger()

        def validate_locks(self, locks, provider_profile="wafer", request_profile="builder_16384", **kwargs):
            assert provider_profile == "wafer"
            assert request_profile == "builder_16384"
            return locks

        def make_builder_client(self, locks, **kwargs):
            assert kwargs["profile"] == "builder_16384"
            assert kwargs["episode"].startswith("selective_20260915/provider_v3/")
            return client

        def exclusive_run(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(explore, "_budget", lambda required=True: Budget())
    monkeypatch.setattr(pilot, "_runtime_module", lambda required=True: SimpleNamespace(validate_plan=lambda plan: {"valid": True, "plan": plan}))
    monkeypatch.setattr(pilot, "_make_ledger", lambda budget, spec: Ledger())
    result = explore.build(out, max_families=1)
    assert result["families"]["MarkorCreateNote"]["status"] == "built"
    assert len(client.calls) == 1
    assert (out / "plans/MarkorCreateNote.json").is_file()


def test_provider_v3_run_leaves_unbuilt_families_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(explore, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(explore, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    out = tmp_path / "provider_v3"
    spec = explore.prepare(pilot.DEFAULT_OUT, out)
    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "unused"},
        "steps": [{"id": "x", "intent": "x", "action": {"action_type": "open_app", "app_name": "Markor"}, "target": None, "before": [], "after": [{"text": "Markor"}]}],
    }
    pilot.atomic_json(out / spec["plans"]["MarkorCreateNote"], plan)
    monkeypatch.setattr(explore, "_load", lambda value: spec)
    monkeypatch.setattr(explore, "_budget", lambda required=True: SimpleNamespace(make_ledger=lambda: SimpleNamespace(has_episode=lambda episode: False), validate_locks=lambda *args, **kwargs: {}, exclusive_run=lambda: contextlib.nullcontext()))
    result = explore.run(out, max_episodes=0)
    assert result["batch_status"] == "pilot_limit"


def test_real_explore_budget_client_runs_compiled_extraction_through_fake_ui(tmp_path, monkeypatch):
    import guiexp_android.android_env as android_env
    import guiexp_android.conditions as conditions

    ledger_path = tmp_path / "budget.sqlite3"
    lock_path = tmp_path / "run.lock"
    auth_path = tmp_path / "authorization.json"
    monkeypatch.setattr(sb, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", lock_path.resolve())
    monkeypatch.setattr(sb, "AUTHORIZATION_PATH", auth_path.resolve())
    unsigned = {
        "schema": "selective-explore-budget-authorization/1",
        "namespace": sb.EXPLORE_NAMESPACE,
        "shared_ledger": str(ledger_path.resolve()),
        "shared_run_lock": str(lock_path.resolve()),
        "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10",
        "legacy_limit_usd": "10",
        "baseline_call_ids": [],
        "baseline_max_rowid": 0,
        "baseline_max_created": None,
        "baseline_calls": 0,
        "baseline_actual_nano": 0,
        "baseline_unresolved_reserved_nano": 0,
    }
    auth = dict(unsigned, authorization_sha256=sb.hashlib.sha256(sb.canonical(unsigned).encode()).hexdigest())
    auth_path.write_text(json.dumps(auth) + "\n")
    ledger = sb.SelectiveExploreLedger(ledger_path, authorization_path=auth_path, host_guard=lambda: True)

    class Element:
        def __init__(self, text="", hint="", editable=False):
            self.text = text
            self.hint_text = hint
            self.content_description = ""
            self.is_editable = editable
            self.is_clickable = True

    class AW:
        def __init__(self, env):
            self.env = env

        def get_state(self, wait_to_stabilize=False):
            return SimpleNamespace(ui_elements=self.env.elements_now)

        def execute_action(self, action):
            self.env.elements_now = [Element(text="Done")]

    class Env:
        wait_after_action_seconds = 0.0

        def __init__(self):
            self.elements_now = []
            self.aw_env = AW(self)

        def reset(self, task):
            self.elements_now = [Element(hint="Title", editable=True)]

        def observe(self, goal):
            return {"url": "fake", "ax_tree_text": "Title", "goal_text": goal}

        def reward(self):
            return float(any(item.text == "Done" for item in self.elements_now))

        def close(self):
            return None

    class Response:
        def __init__(self):
            self.content = '{"value":"EXTRACTED"}'
            self.raw = {"choices": [{"message": {"content": self.content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": "0.001"}}
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=self.content))]
            self.usage = SimpleNamespace(prompt_tokens=2, completion_tokens=1, cost=0.001)

        def model_dump(self, mode="json"):
            return self.raw

    class SDK:
        max_retries = 0
        base_url = sb.OFFICIAL_BASE

        def __init__(self):
            self.calls = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    monkeypatch.setattr(android_env, "get_task", lambda family, condition, seed: {"family": family, "seed": seed})
    monkeypatch.setattr(android_env, "goal_text", lambda task: "task goal")
    monkeypatch.setattr(conditions, "build_prompt", lambda *args: "goal constraints")
    monkeypatch.setattr(sb, "before_ui_action", lambda guard=None: True)
    sdk = SDK()
    client = sb.make_client(None, ledger=ledger, sdk=sdk, episode="selective_20260915/provider_v3/MarkorCreateNote/b01/full_fallback", profile="serving_4096", provider_profile="wafer", metadata_fetcher=lambda _model: {"data": {"id": explore.MODEL, "architecture": {"output_modalities": ["text"]}, "endpoints": [{"tag": "wafer", "status": 0, "context_length": 1048576, "max_completion_tokens": 4096, "supported_parameters": ["max_tokens"], "pricing": {"prompt": "0.0000001", "completion": "0.00000035"}}]}}, host_guard=lambda: True)
    plan = {"schema": pilot.PLAN_SCHEMA, "slots": {"value": "task value"}, "steps": [{"id": "type", "intent": "type the value", "action": {"action_type": "input_text", "text": {"slot": "value", "transform": "identity"}}, "target": {"hint": "Title", "editable": True}, "before": [], "after": [{"text": "Done"}]}]}
    row = {"id": "selective_20260915/provider_v3/MarkorCreateNote/b01/full_fallback", "family": "MarkorCreateNote", "arm": "full_fallback", "binding_id": "b01", "seed": 915201}
    final = pilot._run_one_episode(tmp_path, {"action_budget": 3}, row, {"params": {"value": "EVALUATOR_ONLY"}}, env=Env(), client=client, budget=sb, plan=plan)
    assert final["success"] is True
    assert final["run"]["plan"]["status"] == "complete"
    assert sdk.calls[0]["model"] == explore.MODEL
    assert len(sdk.calls) == 1
