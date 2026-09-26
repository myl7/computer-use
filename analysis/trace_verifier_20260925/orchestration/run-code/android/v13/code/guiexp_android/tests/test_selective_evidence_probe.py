"""Focused offline checks for the build-only evidence probe."""

from __future__ import annotations

import contextlib
import copy
import json

import pytest

from guiexp_android import selective_evidence_probe as probe
from guiexp_android import selective_evidence_projection as projection
from guiexp_android import selective_plan_contract as contract
from guiexp_android.budget_client import BudgetStop
from guiexp_android.selective_evidence_store import build_store


def _ax(index: int, text: str, **fields: object) -> str:
    return f"UI element {index}: " + json.dumps({"index": index, "text": text, **fields})


def _demo() -> dict:
    return {
        "family": "MarkorDeleteNote",
        "goal_text": "Delete the note.",
        "source": "recorded_training",
        "steps": [
            {
                "step": 1,
                "action": {"action_type": "open_app", "app_name": "Markor"},
                "pre_obs": {"ax_tree_text": "", "url": "home"},
                "post_obs": {"ax_tree_text": _ax(0, "Markor"), "url": "markor"},
            },
            {
                "step": 2,
                "action": {"action_type": "click", "index": 0},
                "pre_obs": {"ax_tree_text": _ax(0, "Markor"), "url": "markor"},
                "post_obs": {"ax_tree_text": _ax(0, "Deleted"), "url": "markor"},
            },
        ],
    }


def _candidate() -> dict:
    return {
        "schema": probe.PREFIX_SCHEMA,
        "plan": {
            "schema": contract.PLAN_SCHEMA,
            "slots": {},
            "steps": [
                {
                    "id": "open",
                    "intent": "open Markor",
                    "action": {"action_type": "open_app", "app_name": "Markor"},
                    "target": None,
                    "before": [],
                    "after": [{"text": "Markor"}],
                },
                {
                    "id": "delete",
                    "intent": "delete the note",
                    "action": {"action_type": "click"},
                    "target": {"text": "Markor"},
                    "before": [],
                    "after": [{"text": "Deleted"}],
                },
            ],
        },
        "source_steps": {"open": 1, "delete": 2},
        "terminal_source_step": 2,
        "handoff_policy": "reactive",
    }


def test_query_validation_supports_single_and_bounded_batch_without_paths():
    single = {"schema": probe.QUERY_SCHEMA, "query": "target", "source_step": 1}
    assert probe.validate_query_request(single, max_source_step=2)["valid"] is True
    batch = {
        "schema": probe.QUERY_BATCH_SCHEMA,
        "queries": [
            {"query": "target", "source_step": 1},
            {"query": "effect", "source_step": 2},
        ],
    }
    checked = probe.validate_query_request(batch, max_source_step=2)
    assert checked["valid"] is True
    assert len(checked["requests"]) == 2
    for bad in (
        {"schema": probe.QUERY_SCHEMA, "query": "target", "source_step": 0},
        {"schema": probe.QUERY_SCHEMA, "query": 1, "source_step": 1},
        {"schema": probe.QUERY_SCHEMA, "query": "state_pre", "source_step": 3},
        {"schema": probe.QUERY_SCHEMA, "query": "target", "source_step": 1, "path": "/tmp"},
        {"schema": probe.QUERY_BATCH_SCHEMA, "queries": [{"query": "target", "source_step": 1}] * 2},
        {"schema": probe.QUERY_BATCH_SCHEMA, "queries": [{"query": "target", "source_step": i} for i in range(1, 10)]},
    ):
        assert probe.validate_query_request(bad, max_source_step=2)["valid"] is False


def test_static_and_on_demand_views_share_packet_encoding_and_initial_on_demand_has_no_raw():
    demo = _demo()
    demo["evaluator_answer"] = "held-out-secret"
    demo["steps"][0]["oracle"] = "held-out-secret"
    store = build_store(demo["goal_text"], demo["steps"])
    full = probe.build_messages("full_demo", demo, store)[1]["content"]
    static = probe.build_messages("static_projection", demo, store)[1]["content"]
    on_demand = probe.build_messages("on_demand", demo, store)[1]["content"]
    assert "PREFIX OUTPUT CONTRACT" in full
    assert "evidence_packets" in static
    assert "target" in static and "effect" in static
    assert "retrieved_evidence_packets" in on_demand
    assert "raw_evidence" not in on_demand
    assert "initial response receives the skeleton only" in on_demand
    assert "held-out-secret" not in full
    escalated = probe.build_messages(
        "static_projection", demo, store, static_escalated=True
    )[1]["content"]
    assert "full_raw_training_trace" not in escalated
    assert "evidence_packets" not in escalated
    assert "static_projection_repair_full_raw" in escalated
    assert "ax_tree_text" in escalated
    assert "Markor" in escalated


def test_v2_prompt_contains_valid_query_and_prefix_examples_and_reuse_objective():
    demo = _demo()
    store = build_store(demo["goal_text"], demo["steps"])
    prompt = probe.build_messages("on_demand", demo, store)[1]["content"]
    assert json.dumps(probe.VALID_SINGLE_QUERY_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2) in prompt
    assert json.dumps(probe.VALID_BATCH_QUERY_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2) in prompt
    assert json.dumps(probe.VALID_PREFIX_WRAPPER_EXAMPLE, ensure_ascii=True, sort_keys=True, indent=2) in prompt
    assert probe.validate_query_request(probe.VALID_SINGLE_QUERY_EXAMPLE, max_source_step=2)["valid"] is True
    assert probe.validate_query_request(probe.VALID_BATCH_QUERY_EXAMPLE, max_source_step=2)["valid"] is True
    assert probe.prefix_contract.validate_prefix_response(probe.VALID_PREFIX_WRAPPER_EXAMPLE)["valid"] is True
    assert "semantic role" in prompt
    assert "Preserve all declared task-value slots and parameters during repair" in prompt
    assert "later goal-only extractor" in prompt
    assert "prefix" in prompt and "complete_plan_contract" in prompt
    assert "post-only visible value witnesses" in prompt
    assert "request a raw state packet or stop before extending the verified boundary" in prompt
    assert "every selector is true in post" in prompt


def test_candidate_requires_contiguous_source_steps_and_full_raw_guard_evidence():
    demo = _demo()
    valid = probe.validate_prefix_candidate(_candidate(), [demo])
    assert valid["valid"] is True
    assert valid["guard_evidence"]["status"] == "valid"
    gap = copy.deepcopy(_candidate())
    gap["source_steps"]["delete"] = 3
    gap["terminal_source_step"] = 3
    assert probe.validate_prefix_candidate(gap, [demo])["valid"] is False

    projected = projection.project_demonstrations([demo])[0]
    projected["steps"][0]["pre_obs"]["ax_tree_text"] = ""
    projected["steps"][0]["post_obs"]["ax_tree_text"] = ""
    assert probe.validate_prefix_candidate(_candidate(), [projected])["valid"] is False


def test_precise_feedback_keeps_step_and_source_context():
    text = probe.precise_feedback(
        {
            "valid": False,
            "errors": ["candidate source_steps must be contiguous source 1..k"],
            "guard_evidence": {
                "steps": [{"step_id": "delete", "status": "unknown", "source_indices": [2], "errors": ["post tree incomplete"]}]
            },
        }
    )
    assert "source 2" in text
    assert "delete" in text
    assert len(text) <= 2400


class _Ledger:
    def __init__(self):
        self.receipts: list[str] = []

    def has_episode(self, episode: str) -> bool:
        return False

    def receipt_ids(self, episode: str) -> list[str]:
        return [receipt for receipt in self.receipts if episode in receipt]


class _Client:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.episode = None

    def begin_episode(self, episode):
        self.episode = episode

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        return {"id": f"generation-{len(self.calls)}", "choices": [{"message": {"content": value}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": "0.001"}}


def _spec() -> dict:
    return {
        "version": probe.VERSION,
        "model": probe.MODEL,
        "provider": probe.PROVIDER,
        "provider_profile": probe.PROVIDER_PROFILE,
        "builder_profile": {"name": probe.BUILDER_PROFILE_NAME},
        "diagnostic_prefix": probe.DIAGNOSTIC_PREFIX,
        "model_locks": {probe.MODEL: {"provider": probe.PROVIDER}},
    }


def _row(condition="full_demo", key="d00_full_demo") -> dict:
    return {
        "key": key,
        "id": f"{probe.DIAGNOSTIC_PREFIX}/{probe.VERSION}/{key}",
        "family": "MarkorDeleteNote",
        "demo_index": 0,
        "source_sha256": "trace-sha",
        "condition": condition,
        "condition_order": 0,
    }


def test_build_row_saves_raw_before_parse_and_never_claims_task_success(tmp_path):
    client = _Client([json.dumps(_candidate())])
    result = probe._build_row(
        tmp_path,
        _spec(),
        _row(),
        _demo(),
        ledger=_Ledger(),
        client_factory=lambda spec, row, ledger: client,
    )
    assert result["status"] == "built"
    assert result["calls_made"] == 1
    call = json.loads((tmp_path / "build/d00_full_demo/call_01.json").read_text())
    assert call["saved_before_parse_or_validation"] is True
    assert call["raw_char_count"] > 0
    assert "task_success" not in result


def test_on_demand_candidate_query_candidate_keeps_latest_candidate_context(tmp_path):
    invalid = copy.deepcopy(_candidate())
    invalid["source_steps"]["delete"] = 3
    invalid["terminal_source_step"] = 3
    query = {"schema": probe.QUERY_SCHEMA, "query": "effect", "source_step": 2}
    client = _Client([json.dumps(invalid), json.dumps(query), json.dumps(_candidate())])
    result = probe._build_row(
        tmp_path,
        _spec(),
        _row("on_demand", "d00_on_demand"),
        _demo(),
        ledger=_Ledger(),
        client_factory=lambda spec, row, ledger: client,
    )
    assert result["status"] == "built"
    assert result["query_rounds"] == 1
    assert result["query_packets"][0]["evidence_source"] == "recorded_training_trace"
    third_prompt = client.calls[2]["messages"][1]["content"]
    assert "PREVIOUS CANDIDATE RAW TEXT" in third_prompt
    assert "Retrieved query packet" in third_prompt


def test_on_demand_malformed_batch_gets_precise_query_feedback(tmp_path):
    malformed = {
        "schema": probe.QUERY_BATCH_SCHEMA,
        "queries": [{"query": "effect", "source_step": 1}] * 2,
    }
    client = _Client([json.dumps(malformed), json.dumps(_candidate())])
    result = probe._build_row(
        tmp_path,
        _spec(),
        _row("on_demand", "d00_on_demand_batch"),
        _demo(),
        ledger=_Ledger(),
        client_factory=lambda spec, row, ledger: client,
    )
    assert result["status"] == "built"
    feedback = json.loads((tmp_path / "build/d00_on_demand_batch/feedback_01.json").read_text())
    assert "duplicate" in feedback["feedback"]


def test_on_demand_last_logical_slot_must_be_candidate(tmp_path):
    query = json.dumps({"schema": probe.QUERY_SCHEMA, "query": "effect", "source_step": 2})
    client = _Client([query, query, query, query, json.dumps(_candidate())])
    result = probe._build_row(
        tmp_path,
        _spec(),
        _row("on_demand", "d00_on_demand_final"),
        _demo(),
        ledger=_Ledger(),
        client_factory=lambda spec, row, ledger: client,
    )
    assert result["status"] == "failed_build"
    assert result["calls_made"] == probe.MAX_LOGICAL_CALLS
    assert "final logical call" in result["error"]


def test_existing_terminal_state_prevents_duplicate_rerun(tmp_path):
    client = _Client([json.dumps(_candidate())])
    spec = _spec()
    row = _row()
    ledger = _Ledger()
    first = probe._build_row(tmp_path, spec, row, _demo(), ledger=ledger, client_factory=lambda *_: client)
    assert first["status"] == "built"
    prior = probe._existing_row(tmp_path, row, ledger, spec)
    assert prior["status"] == "built"
    assert len(client.calls) == 1


def test_run_halts_and_preserves_pending_rows_on_budget_stop(tmp_path, monkeypatch):
    spec = _spec()
    spec["episodes"] = [_row(), _row("static_projection", "d00_static_projection")]
    training = _demo()
    training["_source_sha256"] = "trace-sha"
    monkeypatch.setattr(probe, "_load_spec", lambda out: spec)
    monkeypatch.setattr(probe, "_load_training_artifact", lambda out, value: [training])
    monkeypatch.setattr(probe, "_exclusive_run", lambda out: contextlib.nullcontext())
    monkeypatch.setattr(probe, "_provider_validate", lambda *args: None)
    result = probe.run(
        tmp_path,
        ledger=_Ledger(),
        client_factory=lambda *_: (_ for _ in ()).throw(BudgetStop("diagnostic cap")),
    )
    assert result["status"] == "budget_stopped"
    assert result["pending_rows"] == 1
    assert json.loads((tmp_path / "build/d00_full_demo/state.json").read_text())["status"] == "budget_stopped"


def test_analyze_marks_missing_rows_without_zeroing_tokens(tmp_path, monkeypatch):
    spec = _spec()
    spec["episodes"] = [_row()]
    monkeypatch.setattr(probe, "_load_spec", lambda out: spec)
    monkeypatch.setattr(probe, "_load_training_artifact", lambda out, value: [_demo()])
    report = probe.analyze(tmp_path)
    assert report["status"] == "incomplete"
    assert report["rows"][0]["status"] == "missing"
    assert report["rows"][0]["usage"]["prompt_tokens"] is None


def test_training_source_drift_is_rejected(tmp_path):
    source = tmp_path / "trace.jsonl"
    source.write_text("original")
    demo = _demo()
    demo["_source_path"] = str(source)
    demo["_source_sha256"] = probe._hash_file(source)
    artifact = probe._training_artifact([demo])
    artifact_path = tmp_path / probe.TRAINING_INPUTS_NAME
    probe._write(artifact_path, artifact)
    spec = {"training_inputs": probe.TRAINING_INPUTS_NAME, "training_inputs_sha256": probe._hash_file(artifact_path)}
    source.write_text("drifted")
    with pytest.raises(probe.ProbeStop, match="drifted"):
        probe._load_training_artifact(tmp_path, spec)


def test_indirect_runtime_dependency_drift_is_rejected_before_build(tmp_path, monkeypatch):
    frozen = probe._source_manifest()
    runtime_path = probe.Path(probe.__file__).with_name("selective_runtime.py").resolve()
    original_hash = probe._hash_file

    def tampered_hash(path):
        return "tampered-runtime" if path.resolve() == runtime_path else original_hash(path)

    monkeypatch.setattr(probe, "_hash_file", tampered_hash)
    with pytest.raises(probe.ProbeStop, match="source or runtime"):
        probe._verify_source_manifest(frozen)


def test_prepare_freezes_nine_counterbalanced_rows_without_constructing_client(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    origin_body = {"schema": "fixture-origin/1", "episodes": []}
    (origin / "spec.json").write_text(
        json.dumps({**origin_body, "spec_sha256": probe._hash_value(origin_body)})
    )
    source = tmp_path / "trace.jsonl"
    source.write_text("frozen-source")
    demos = []
    for index in range(3):
        demo = _demo()
        demo["_source_path"] = str(source)
        demo["_source_sha256"] = probe._hash_file(source)
        demo["goal_text"] = f"Delete note {index}."
        demos.append(demo)
    monkeypatch.setattr(probe, "_load_demos", lambda origin, spec, families: demos)
    monkeypatch.setattr(probe, "_training_snapshot", lambda origin: {"manifest_path": None, "manifest_sha256": None})

    def fake_prepare_config(prefix, *, path, ledger_path, authorization_path):
        value = {
            "diagnostic_prefix": prefix,
            "shared_ledger": str(ledger_path),
            "shared_run_lock": str(tmp_path / "run.lock"),
            "authorization_path": str(authorization_path),
        }
        probe._write(path, value)
        return value

    monkeypatch.setattr(probe.diagnostic_budget, "prepare_diagnostic_config", fake_prepare_config)
    out = tmp_path / probe.VERSION
    spec = probe.prepare(origin, out)
    assert len(spec["episodes"]) == 9
    assert {row["condition"] for row in spec["episodes"]} == set(probe.CONDITIONS)
    assert [row["condition"] for row in spec["episodes"][:3]] == list(probe.CONDITIONS)
    assert all(row["id"].startswith(f"{spec['diagnostic_prefix']}/{spec['version']}/") for row in spec["episodes"])
    assert spec["diagnostic_prefix"] == f"{probe.NAMESPACE}/{probe.LEGACY_VERSION}"
    assert spec["cli"]["interpreter"] == "../.venv-android/bin/python"
    assert spec["execution"]["ui_actions"] is False


def test_literal_baseline_is_built_from_training_and_checked_against_development(tmp_path, monkeypatch):
    training = _demo()
    training_source = tmp_path / "training.jsonl"
    training_source.write_text("training-source")
    training["_source_path"] = str(training_source)
    training["_source_sha256"] = probe._hash_file(training_source)
    development = copy.deepcopy(training)
    development["steps"][1]["post_obs"]["ax_tree_text"] = _ax(0, "Renamed")
    development_path = tmp_path / "development.jsonl"
    rows = [{"record_type": "initial", "goal_text": development["goal_text"]}]
    rows.extend({"record_type": "step", **step} for step in development["steps"])
    development_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    monkeypatch.setattr(probe.literal_control, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        probe,
        "COMPATIBILITY_TRACES",
        ({"family": "MarkorDeleteNote", "version": "dev", "relative_path": "development.jsonl"},),
    )
    artifact = probe._load_compatibility_inputs([training])
    item = artifact["rows"][0]
    assert item["training_source_sha256"] == training["_source_sha256"]
    assert item["validation_source_sha256"] == item["source_sha256"]
    assert item["literal_control"]["bindings"] == {}
    assert item["literal_control"]["compatibility"]["status"] == "diverged"


def test_compatibility_does_not_infer_wrong_binding_from_recorded_action(tmp_path):
    plan = {
        "schema": contract.PLAN_SCHEMA,
        "slots": {"name": "public name"},
        "steps": [{
            "id": "type",
            "intent": "type the name",
            "action": {"action_type": "input_text", "text": {"slot": "name", "transform": "identity"}},
            "target": {"hint": "Name", "editable": True},
            "before": [],
            "after": [{"text": {"slot": "name", "transform": "identity"}, "editable": True}],
        }],
    }
    compatibility = {"status": "available", "family": "MarkorDeleteNote", "version": "dev", "demo": {
        "goal_text": "Type correct.",
        "steps": [{"step": 1, "action": {"action_type": "input_text", "text": "correct", "index": 0}, "pre_obs": {"ax_tree_text": _ax(0, "", hint="Name", editable=True)}, "post_obs": {"ax_tree_text": _ax(0, "correct", hint="Name", editable=True)}}],
    }, "training_source_sha256": None}
    client = _Client([json.dumps({"name": "wrong"})])
    result, binding, _call = probe._run_compatibility(
        plan,
        compatibility,
        client,
        _row(),
        _spec(),
        tmp_path,
        _Ledger(),
    )
    assert binding == {"name": "wrong"}
    assert result["status"] == "diverged"
    assert result["action_divergence"]["kind"] == "action_argument_mismatch"


def test_nonempty_binding_missing_billing_stops_after_persisting_raw_call(tmp_path):
    class NoBillClient(_Client):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return {"id": "generation-no-bill", "choices": [{"message": {"content": json.dumps({"name": "value"})}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    plan = {
        "schema": contract.PLAN_SCHEMA,
        "slots": {"name": "public name"},
        "steps": [{"id": "type", "intent": "type", "action": {"action_type": "input_text", "text": {"slot": "name", "transform": "identity"}}, "target": {"hint": "Name", "editable": True}, "before": [], "after": [{"text": {"slot": "name", "transform": "identity"}, "editable": True}]}],
    }
    with pytest.raises(BudgetStop, match="billing"):
        probe._extract_compatibility_binding(plan, "Type value.", NoBillClient([]), _row(), _spec(), tmp_path, _Ledger())
    assert (tmp_path / "build/d00_full_demo/binding_01.json").is_file()


def test_validated_candidate_checkpoint_survives_binding_budget_stop(tmp_path):
    demo = _demo()
    demo["steps"][1]["action"] = {"action_type": "input_text", "text": "Deleted", "index": 0}
    candidate = _candidate()
    candidate["plan"]["slots"] = {"name": "public name"}
    candidate["plan"]["steps"][1]["action"] = {"action_type": "input_text", "text": {"slot": "name", "transform": "identity"}}
    candidate["plan"]["steps"][1]["after"] = [{"text": {"slot": "name", "transform": "identity"}}]

    class BindingNoBill(_Client):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return {"id": "builder", "choices": [{"message": {"content": json.dumps(candidate)}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": "0.001"}}
            return {"id": "binding", "choices": [{"message": {"content": '{"name":"Deleted"}'}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    compatibility = {"status": "available", "family": "MarkorDeleteNote", "version": "dev", "demo": _demo(), "training_source_sha256": None}
    with pytest.raises(BudgetStop, match="billing"):
        probe._build_row(
            tmp_path,
            _spec(),
            _row(),
            demo,
            ledger=_Ledger(),
            client_factory=lambda *_: BindingNoBill([]),
            compatibility=compatibility,
        )
    checkpoint = json.loads((tmp_path / "build/d00_full_demo/build.json").read_text())
    assert checkpoint["status"] == "built_pending_compatibility"
    assert checkpoint["candidate_valid"] is True
    assert json.loads((tmp_path / "build/d00_full_demo/candidate.json").read_text())["valid"] is True
