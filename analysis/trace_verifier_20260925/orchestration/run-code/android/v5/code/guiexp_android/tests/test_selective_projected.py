"""Focused checks for the projected_v4 DeepInfra driver."""

from __future__ import annotations

import contextlib
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot
from guiexp_android import selective_explore_budget as sb
from guiexp_android import selective_projected as projected
from guiexp_android.budget_client import BudgetStop


def _write_json(path: Path, value: object, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pilot.atomic_json(path, value, private=private)


def _demo_trace(path: Path) -> None:
    records = [
        {"record_type": "initial", "obs": {"ax_tree_text": "Before 1"}},
    ]
    for index in range(1, 5):
        records.append(
            {
                "record_type": "step",
                "step": index,
                "action": {"action_type": "click", "index": index - 1},
                "pre_obs": {
                    "url": "markor",
                    "ax_tree_text": (
                        f'UI element {index - 1}: '
                        + json.dumps(
                            {"index": index - 1, "text": f"Before {index}", "is_clickable": True}
                        )
                        + '\nUI element 99: {"index":99,"text":"PRIVATE_NOISE"}'
                    ),
                },
                "post_obs": {
                    "url": "markor",
                    "ax_tree_text": (
                        f'UI element {index - 1}: '
                        + json.dumps(
                            {"index": index - 1, "text": f"After {index}", "is_clickable": True}
                        )
                        + '\nUI element 99: {"index":99,"text":"PRIVATE_NOISE"}'
                    ),
                },
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def _origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    trace = origin / "training" / "MarkorDeleteNote" / "s915101_a0" / "trajectory.jsonl"
    _demo_trace(trace)
    training = {
        "record_type": "training-manifest",
        "status": "ready",
        "families": {
            "MarkorDeleteNote": [
                {
                    "family": "MarkorDeleteNote",
                    "seed": 915101,
                    "attempt": 0,
                    "success": True,
                    "status": "done",
                    "goal_text": "Delete the note named winter_note.",
                    "trajectory": "training/MarkorDeleteNote/s915101_a0/trajectory.jsonl",
                    "source": "fresh_train",
                }
            ],
            "MarkorCreateNote": [],
            "FilesMoveFile": [],
        },
    }
    _write_json(origin / "training_manifest.json", training)
    _write_json(
        origin / "private" / "evaluator_bindings.json",
        {
            "families": {
                "MarkorDeleteNote": [{"params": {"file_name": "winter_note"}}],
                "MarkorCreateNote": [],
                "FilesMoveFile": [],
            }
        },
        private=True,
    )
    old_done = "selective_20260915/MarkorDeleteNote/b01/reactive"
    old_skipped = "selective_20260915/MarkorDeleteNote/b01/full_fallback"
    old_pending = "selective_20260915/MarkorDeleteNote/b01/local_rejoin"
    _write_json(
        origin / "episodes" / old_done / "state.json",
        {"record_type": "episode-state", "status": "done", "result": {"success": True}},
        private=True,
    )
    (origin / "episodes" / old_done / "trajectory.jsonl").write_text("done\n", encoding="utf-8")
    _write_json(
        origin / "episodes" / old_skipped / "state.json",
        {"record_type": "episode-state", "status": "skipped_unbuildable"},
        private=True,
    )
    rows = [
        {
            "order": 1,
            "id": old_done,
            "family": "MarkorDeleteNote",
            "binding_id": "b01",
            "arm": "reactive",
            "seed": 915201,
            "condition": "discover",
            "obs_mode": "screenshot+ax",
            "max_actions": 40,
        },
        {
            "order": 2,
            "id": old_skipped,
            "family": "MarkorDeleteNote",
            "binding_id": "b01",
            "arm": "full_fallback",
            "seed": 915201,
            "condition": "discover",
            "obs_mode": "screenshot+ax",
            "max_actions": 40,
        },
        {
            "order": 3,
            "id": old_pending,
            "family": "MarkorDeleteNote",
            "binding_id": "b01",
            "arm": "local_rejoin",
            "seed": 915201,
            "condition": "discover",
            "obs_mode": "screenshot+ax",
            "max_actions": 40,
        },
    ]
    body = {
        "schema": "android-selective-pilot/1",
        "version": "origin",
        "namespace": "selective_20260915",
        "model": pilot.MODEL,
        "provider": "relace",
        "families": list(pilot.FAMILIES),
        "arms": list(pilot.ARMS),
        "episodes": rows,
        "ledger_absolute": str(tmp_path / "budget.sqlite3"),
        "run_lock_absolute": str(tmp_path / "run.lock"),
        "training": {},
        "source_manifest": {},
    }
    body["spec_sha256"] = pilot.hash_json(body)
    _write_json(origin / "spec.json", body)
    return origin


def _authorization(path: Path, ledger: Path, run_lock: Path) -> None:
    unsigned = {
        "schema": "selective-explore-budget-authorization/1",
        "namespace": sb.EXPLORE_NAMESPACE,
        "shared_ledger": str(ledger.resolve()),
        "shared_run_lock": str(run_lock.resolve()),
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
    signed = dict(unsigned, authorization_sha256=sb.hashlib.sha256(sb.canonical(unsigned).encode()).hexdigest())
    _write_json(path, signed)


def _metadata(model: str, provider: str = "deepinfra/fp4") -> dict:
    return {
        "data": {
            "id": model,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [
                {
                    "tag": provider,
                    "status": 0,
                    "context_length": 1048576,
                    "max_completion_tokens": 16384,
                    "supported_parameters": ["max_tokens", "reasoning"],
                    "pricing": {"prompt": "0.00000015", "completion": "0.00000050"},
                }
            ],
        }
    }


def test_prepare_real_phase_freeze_and_load_is_readonly(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    ledger_path = tmp_path / "budget.sqlite3"
    run_lock = tmp_path / "run.lock"
    auth_path = tmp_path / "authorization.json"
    monkeypatch.setattr(sb, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", run_lock.resolve())
    monkeypatch.setattr(sb, "AUTHORIZATION_PATH", auth_path.resolve())
    monkeypatch.setattr(projected, "AUTHORIZATION_PATH", auth_path.resolve())
    _authorization(auth_path, ledger_path, run_lock)
    sb.SelectiveExploreLedger(ledger_path, authorization_path=auth_path, host_guard=lambda: True)
    out = tmp_path / "projected_v4"
    spec = projected.prepare(origin, out)
    loaded = projected._load(out)
    assert loaded["spec_sha256"] == spec["spec_sha256"]
    assert loaded["provider"] == "deepinfra/fp4"
    assert loaded["provider_profile"] == "deepinfra_fp4"
    assert loaded["phase_manifests"]["build"]["path"] == "phase_build.json"
    assert len(loaded["episodes"]) == 2
    assert len(loaded["imported_rows"]) == 2
    assert loaded["plan_contract"]["source_sha256"] == projected._hash(projected.PLAN_CONTRACT_PATH)
    assert all(item["id"].startswith("selective_20260915/projected_v4/") for item in loaded["episodes"])


@pytest.mark.parametrize(
    ("profile", "provider"),
    [("wafer", "wafer"), ("deepinfra_fp4", "deepinfra/fp4")],
)
def test_prepare_selects_provider_and_version_without_global_rebinding(
    tmp_path, monkeypatch, profile, provider
):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = f"projected_v5_{profile.replace('-', '_')}"
    out = tmp_path / version
    spec = projected.prepare(origin, out, version=version, provider_profile=profile)
    loaded = projected._load(out, expected_version=version, expected_provider_profile=profile)
    assert loaded["revision"] == version
    assert loaded["provider"] == provider
    assert loaded["provider_profile"] == profile
    assert loaded["episodes"][0]["id"].startswith(f"selective_20260915/{version}/")


@pytest.mark.parametrize(
    ("profile", "provider"),
    [("wafer", "wafer"), ("z_ai_fp8", "z-ai/fp8")],
)
def test_real_phase_freeze_loads_each_registered_provider(tmp_path, monkeypatch, profile, provider):
    origin = _origin(tmp_path)
    ledger_path = tmp_path / f"{profile}.sqlite3"
    run_lock = tmp_path / f"{profile}.run.lock"
    auth_path = tmp_path / f"{profile}.authorization.json"
    monkeypatch.setattr(sb, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", run_lock.resolve())
    monkeypatch.setattr(sb, "AUTHORIZATION_PATH", auth_path.resolve())
    monkeypatch.setattr(projected, "AUTHORIZATION_PATH", auth_path.resolve())
    _authorization(auth_path, ledger_path, run_lock)
    sb.SelectiveExploreLedger(ledger_path, authorization_path=auth_path, host_guard=lambda: True)
    version = f"projected_v5_real_{profile}"
    out = tmp_path / version
    spec = projected.prepare(origin, out, version=version, provider_profile=profile)
    loaded = projected._load(out, expected_version=version, expected_provider_profile=profile)
    assert loaded["provider"] == provider
    assert loaded["phase_manifests"]["run"]["phase_manifest_sha256"]


def test_prepare_v7_selected_families_excludes_completed_delete_and_file_b01(tmp_path, monkeypatch):
    origin = Path(
        "/Users/myl/app/computer-use/experimental-results/guiexp_android/"
        "selective_20260915/versions/projected_v6_schema"
    )
    if not (origin / "spec.json").is_file():
        pytest.skip("projected_v6_schema origin is unavailable")
    ledger_path = tmp_path / "budget.sqlite3"
    run_lock = tmp_path / "run.lock"
    auth_path = tmp_path / "authorization.json"
    monkeypatch.setattr(sb, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", run_lock.resolve())
    monkeypatch.setattr(sb, "AUTHORIZATION_PATH", auth_path.resolve())
    monkeypatch.setattr(projected, "AUTHORIZATION_PATH", auth_path.resolve())
    _authorization(auth_path, ledger_path, run_lock)
    sb.SelectiveExploreLedger(ledger_path, authorization_path=auth_path, host_guard=lambda: True)
    version = "projected_v7_evidence"
    out = tmp_path / version
    spec = projected.prepare(
        origin,
        out,
        version=version,
        provider_profile="z_ai_fp8",
        families=("MarkorCreateNote", "FilesMoveFile"),
    )
    loaded = projected._load(out, expected_version=version, expected_provider_profile="z_ai_fp8")
    assert tuple(loaded["families"]) == ("MarkorCreateNote", "FilesMoveFile")
    assert len(loaded["episodes"]) == 7
    assert sum(row["family"] == "MarkorCreateNote" for row in loaded["episodes"]) == 4
    assert sum(row["family"] == "FilesMoveFile" for row in loaded["episodes"]) == 3
    assert all(row["binding_id"] != "b01" for row in loaded["episodes"] if row["family"] == "FilesMoveFile")
    assert len(loaded["excluded_families"]) == 8
    excluded = loaded["excluded_bindings"]
    assert any(item["family"] == "FilesMoveFile" and item["binding_id"] == "b01" for item in excluded)
    assert any(
        item.startswith("selective_20260915/MarkorCreateNote/b01/reactive")
        for item in loaded["imported_rows"]
    )
    assert all("projected_v6_schema" not in row["id"] for row in loaded["episodes"])


def test_load_rejects_a_rebound_provider_profile_even_with_a_recomputed_spec_hash(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = "projected_v5_profile_guard"
    out = tmp_path / version
    projected.prepare(origin, out, version=version, provider_profile="wafer")
    tampered = json.loads((out / "spec.json").read_text())
    tampered["provider_profile"] = "deepinfra_fp4"
    tampered["spec_sha256"] = pilot.hash_json({key: value for key, value in tampered.items() if key != "spec_sha256"})
    (out / "spec.json").write_text(json.dumps(tampered) + "\n")
    with pytest.raises(BudgetStop, match="provider|profile|lock"):
        projected._load(out)


def test_prepare_prefix_scope_freezes_completion_policy_and_supporting_hashes(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = "projected_v8_prefix"
    out = tmp_path / version
    spec = projected.prepare(
        origin,
        out,
        version=version,
        provider_profile="z_ai_fp8",
        compilation_scope="prefix",
    )
    loaded = projected._load(
        out,
        expected_version=version,
        expected_provider_profile="z_ai_fp8",
        expected_compilation_scope="prefix",
    )
    assert spec["compilation_scope"] == loaded["compilation_scope"] == "prefix"
    assert loaded["completion_policy"] == "reactive_handoff"
    assert loaded["prefix_handoff_policy"] == "reactive"
    assert loaded["prefix_policy"]["source_sha256"] == projected._hash(projected.PREFIX_MODULE_PATH)
    assert loaded["ax_parser"]["source_sha256"] == projected._hash(projected.AX_PARSER_PATH)


@pytest.mark.parametrize(
    ("profile", "provider"),
    [("deepinfra_fp4", "deepinfra/fp4"), ("z_ai_fp8", "z-ai/fp8")],
)
def test_build_uses_actual_provider_budget_client_and_projected_prompt(
    tmp_path, monkeypatch, profile, provider
):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = f"projected_v5_{profile}"
    out = tmp_path / version
    spec = projected.prepare(origin, out, version=version, provider_profile=profile)
    ledger_path = tmp_path / "budget.sqlite3"
    run_lock = tmp_path / "run.lock"
    auth_path = tmp_path / "authorization.json"
    monkeypatch.setattr(sb, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", run_lock.resolve())
    monkeypatch.setattr(sb, "AUTHORIZATION_PATH", auth_path.resolve())
    monkeypatch.setattr(sb, "EXPLORE_OUT", tmp_path / "budget-output")
    _authorization(auth_path, ledger_path, run_lock)
    ledger = sb.SelectiveExploreLedger(ledger_path, authorization_path=auth_path, host_guard=lambda: True)

    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"note": "note name from the task"},
        "steps": [
            {
                "id": f"delete_{index}",
                "intent": f"delete step {index}",
                    "action": {"action_type": "click"},
                    "target": {"text": f"Before {index}"},
                "before": [{"text": f"Before {index}"}],
                "after": [{"text": f"After {index}"}],
            }
            for index in range(1, 5)
        ],
    }

    class Response:
        def __init__(self):
            self.content = json.dumps(
                {
                    "schema": "selective-compilation/1",
                    "plan": plan,
                    "source_steps": {step["id"]: index for index, step in enumerate(plan["steps"], 1)},
                }
            )
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=self.content))]
            self.usage = SimpleNamespace(prompt_tokens=17, completion_tokens=23, cost=0.001)

        def model_dump(self, mode="json"):
            return {
                "choices": [{"message": {"content": self.content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 23, "cost": "0.001"},
            }

    class SDK:
        max_retries = 0
        base_url = sb.OFFICIAL_BASE

        def __init__(self):
            self.calls = []
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return Response()

    sdk = SDK()
    result = projected.build(
        out,
        max_families=1,
        ledger=ledger,
        sdk=sdk,
        metadata_fetcher=lambda model: _metadata(model, provider),
        host_guard=lambda: True,
    )
    family = result["families"]["MarkorDeleteNote"]
    assert family["status"] == "built"
    assert family["projection"]["guard_validation"] == "full_original_demonstrations"
    assert len(sdk.calls) == 1
    request = sdk.calls[0]
    assert request["max_tokens"] == 16384
    assert request["extra_body"]["reasoning"] == {"effort": "low"}
    assert request["extra_body"]["provider"]["only"] == [provider]
    assert "PRIVATE_NOISE" not in request["messages"][1]["content"]
    assert (out / spec["plans"]["MarkorDeleteNote"]).is_file()
    alignment = json.loads((out / "build/MarkorDeleteNote/alignment.json").read_text())
    guard_report = json.loads((out / "build/MarkorDeleteNote/guard_evidence.json").read_text())
    assert alignment["steps"] == {
        f"delete_{index}": [{"source_step": index}] for index in range(1, 5)
    }
    assert guard_report["status"] == "valid"
    assert (out / "run_identity.json").is_file()
    assert not (tmp_path / "budget-output" / "run_identity.json").exists()
    response_record = json.loads((out / "build/MarkorDeleteNote/first_response.json").read_text())
    assert response_record["provider"] == provider


def test_prefix_build_saves_boundary_and_uses_prefix_wrapper(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = "projected_v8_prefix_build"
    out = tmp_path / version
    spec = projected.prepare(
        origin,
        out,
        version=version,
        provider_profile="z_ai_fp8",
        compilation_scope="prefix",
    )
    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"note": "note name from the task"},
        "steps": [
            {
                "id": f"delete_{index}",
                "intent": f"delete step {index}",
                "action": {"action_type": "click"},
                "target": {"text": f"Before {index}"},
                "before": [{"text": f"Before {index}"}],
                "after": [{"text": f"After {index}"}],
            }
            for index in range(1, 5)
        ],
    }
    wrapper = {
        "schema": "selective-prefix/1",
        "plan": plan,
        "source_steps": {step["id"]: index for index, step in enumerate(plan["steps"], 1)},
        "terminal_source_step": 4,
        "handoff_policy": "reactive",
    }

    class Response:
        def __init__(self):
            self.content = json.dumps(wrapper)
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=self.content))]
            self.usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4, cost=0.001)

        def model_dump(self, mode="json"):
            return {
                "choices": [{"message": {"content": self.content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": "0.001"},
            }

    class Client:
        episode = None

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.messages = kwargs["messages"]
            return Response()

    client = Client()

    class Budget:
        def make_ledger(self, **kwargs):
            return object()

        def validate_locks(self, locks, **kwargs):
            return locks

        def make_builder_client(self, locks, **kwargs):
            return client

        def exclusive_run(self, **kwargs):
            return contextlib.nullcontext()

    monkeypatch.setattr(projected, "_budget", lambda required=True: Budget())
    result = projected.build(out, max_families=1)
    family = result["families"]["MarkorDeleteNote"]
    assert family["status"] == "built"
    assert family["compilation_scope"] == "prefix"
    boundary = json.loads((out / "build/MarkorDeleteNote/prefix_boundary.json").read_text())
    assert boundary["terminal_source_step"] == 4
    assert boundary["completion_policy"] == "reactive_handoff"
    assert "selective-prefix/1" in client.messages[1]["content"]
    assert "remaining original user request" in client.messages[1]["content"]
    assert (out / spec["plans"]["MarkorDeleteNote"]).is_file()


def test_prefix_run_passes_reactive_handoff_policy_to_pilot(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = "projected_v8_prefix_run"
    out = tmp_path / version
    spec = projected.prepare(
        origin,
        out,
        version=version,
        provider_profile="z_ai_fp8",
        compilation_scope="prefix",
    )
    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"note": "note name from the task"},
        "steps": [
            {
                "id": "delete_1",
                "intent": "delete the note",
                "action": {"action_type": "click"},
                "target": {"text": "Before 1"},
                "before": [{"text": "Before 1"}],
                "after": [{"text": "After 1"}],
            }
        ],
    }
    plan_path = out / spec["plans"]["MarkorDeleteNote"]
    _write_json(plan_path, plan)
    boundary = {
        "schema": "selective-prefix/1",
        "compilation_scope": "prefix",
        "version": version,
        "provider": "z-ai/fp8",
        "provider_profile": "z_ai_fp8",
        "plan_sha256": pilot.hash_json(plan),
        "source_steps": {"delete_1": 1},
        "terminal_source_step": 1,
        "handoff_policy": "reactive",
        "completion_policy": "reactive_handoff",
        "guard_evidence_status": "valid",
        "alignment": {"steps": {"delete_1": [{"source_step": 1}]}},
    }
    _write_json(out / "build/MarkorDeleteNote/prefix_boundary.json", boundary)

    calls = []

    class Budget:
        def make_ledger(self, **kwargs):
            return SimpleNamespace(has_episode=lambda _episode: False)

        def validate_locks(self, locks, **kwargs):
            return locks

        def make_client(self, locks, **kwargs):
            calls.append(kwargs)
            return object()

        def exclusive_run(self, **kwargs):
            return contextlib.nullcontext()

    class Env:
        def close(self):
            return None

    pilot_calls = []

    def fake_run_one_episode(*args, **kwargs):
        pilot_calls.append(kwargs)
        return {"success": False}

    monkeypatch.setattr(projected, "_budget", lambda required=True: Budget())
    monkeypatch.setattr(projected.pilot, "_new_env", lambda: Env())
    monkeypatch.setattr(projected.pilot, "_run_one_episode", fake_run_one_episode)
    result = projected.run(out, max_episodes=1)
    assert result["batch_status"] == "pilot_limit"
    assert pilot_calls and pilot_calls[0]["completion_policy"] == "reactive_handoff"
    assert calls and calls[0]["profile"] == "serving_4096"


def test_derive_prefix_version_installs_readonly_dense_artifact_without_paid_build(
    tmp_path, monkeypatch
):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"value": "fictional value"},
        "steps": [
            {
                "id": f"step_{index}",
                "intent": f"fictional step {index}",
                "action": {"action_type": "click"},
                "target": {"text": f"Before {index}"},
                "before": [{"text": f"Before {index}"}],
                "after": [{"text": f"After {index}"}],
            }
            for index in range(1, 5)
        ],
    }
    candidate = {
        "schema": "selective-prefix/1",
        "plan": plan,
        "source_steps": {f"step_{index}": value for index, value in enumerate((1, 2, 3, 13), 1)},
        "terminal_source_step": 13,
        "handoff_policy": "reactive",
    }
    response = tmp_path / "first_response.json"
    response.write_text(json.dumps({"raw_text": json.dumps(candidate)}), encoding="utf-8")
    version = "projected_v8_dense_derived"
    out = tmp_path / version
    result = projected.derive_prefix_version(
        origin,
        out,
        response,
        "MarkorDeleteNote",
        version=version,
        provider_profile="z_ai_fp8",
    )
    spec = projected._load(out, expected_version=version, expected_provider_profile="z_ai_fp8")
    record = json.loads((out / "build/MarkorDeleteNote/build.json").read_text())
    boundary = json.loads((out / "build/MarkorDeleteNote/prefix_boundary.json").read_text())
    assert result["status"] == "derived"
    assert spec["derived_provenance"]["source_response_sha256"] == result["source_response_sha256"]
    assert record["status"] == "derived"
    assert record["calls"] == 0
    assert record["terminal_source_step"] == 3
    assert boundary["terminal_source_step"] == 3
    assert len(json.loads((out / spec["plans"]["MarkorDeleteNote"]).read_text())["steps"]) == 3
    response.write_text(response.read_text() + "\n", encoding="utf-8")
    with pytest.raises(pilot.PilotStop, match="source response changed"):
        projected._load(out)


def test_guard_failure_sidecars_and_repair_feedback_include_step_errors(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    version = "projected_v8_guard_feedback"
    out = tmp_path / version
    projected.prepare(origin, out, version=version, provider_profile="z_ai_fp8")

    valid_plan = {
        "schema": pilot.PLAN_SCHEMA,
        "slots": {"note": "note name from the task"},
        "steps": [
            {
                "id": f"delete_{index}",
                "intent": f"delete step {index}",
                "action": {"action_type": "click"},
                "target": {"text": f"Before {index}"},
                "before": [{"text": f"Before {index}"}],
                "after": [{"text": f"After {index}"}],
            }
            for index in range(1, 5)
        ],
    }
    invalid_plan = copy.deepcopy(valid_plan)
    invalid_plan["steps"][0]["after"] = [{"text": "PRIVATE_NOISE"}]

    def wrapper(plan):
        return {
            "schema": "selective-compilation/1",
            "plan": plan,
            "source_steps": {step["id"]: index for index, step in enumerate(plan["steps"], 1)},
        }

    class Response:
        def __init__(self, value):
            self.content = json.dumps(value)
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=self.content))]
            self.usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4, cost=0.001)

        def model_dump(self, mode="json"):
            return {
                "choices": [{"message": {"content": self.content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": "0.001"},
            }

    class Client:
        def __init__(self):
            self.episode = None
            self.requests = []
            self.responses = iter((wrapper(invalid_plan), wrapper(valid_plan)))

        def begin_episode(self, episode):
            self.episode = episode

        def create(self, **kwargs):
            self.requests.append(kwargs)
            return Response(next(self.responses))

    client = Client()

    class Budget:
        def make_ledger(self, **kwargs):
            return object()

        def make_builder_client(self, locks, **kwargs):
            return client

        def validate_locks(self, locks, **kwargs):
            return locks

        def exclusive_run(self, **kwargs):
            return contextlib.nullcontext()

    monkeypatch.setattr(projected, "_budget", lambda required=True: Budget())
    result = projected.build(out, max_families=1)
    assert result["families"]["MarkorDeleteNote"]["status"] == "built"
    first = json.loads((out / "build/MarkorDeleteNote/first_response.guard_evidence.json").read_text())
    repair = json.loads((out / "build/MarkorDeleteNote/repair_response.guard_evidence.json").read_text())
    assert first["status"] == "invalid"
    assert "delete_1" in first["failure_summary"]
    assert "after guards are unchanged context" in first["failure_summary"]
    assert repair["status"] == "valid"
    feedback = client.requests[1]["messages"][-1]["content"]
    assert "delete_1" in feedback
    assert "after guards are unchanged context" in feedback


def test_run_without_plan_is_semantic_no_plans_and_does_not_create_client(tmp_path, monkeypatch):
    origin = _origin(tmp_path)
    monkeypatch.setattr(projected, "_execution_manifest", lambda: {"test": "frozen"})
    monkeypatch.setattr(projected, "_freeze_phase_manifests", lambda *args, **kwargs: {})
    out = tmp_path / "projected_v4"
    projected.prepare(origin, out)
    called = []

    class Budget:
        def make_ledger(self, **kwargs):
            return SimpleNamespace(has_episode=lambda _episode: called.append("receipt") or False)

    monkeypatch.setattr(projected, "_budget", lambda required=True: Budget())
    result = projected.run(out)
    assert result["batch_status"] == "no_plans"
    assert called == []
