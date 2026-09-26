import json
import pickle
from pathlib import Path
from types import SimpleNamespace

from guiexp_android import trace_verifier_v1 as tv


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        reply = next(self.replies)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=2,
                                prompt_tokens_details=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                               usage=usage)


class MetadataClient:
    def __init__(self, content, finish_reason, reasoning=""):
        self.calls = 0
        self.content = content
        self.finish_reason = finish_reason
        self.reasoning = reasoning
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(prompt_tokens=8, completion_tokens=4,
                                prompt_tokens_details=None, cost=0.02)
        message = SimpleNamespace(content=self.content, reasoning=self.reasoning)
        choice = SimpleNamespace(message=message, finish_reason=self.finish_reason)
        return SimpleNamespace(id="response-1", choices=[choice], usage=usage)


def task(seed=1):
    return {"seed": seed, "prompt": "Add Ada Lovelace, number 123456.",
            "truth_params": {"name": "Ada Lovelace", "number": "123456"}}


def test_extraction_records_input_output_retry_and_cost():
    result = tv.extract_binding(FakeClient(['{"name": 4}',
                                           '{"name":"Ada Lovelace","number":"123456"}']),
                                "model", "ContactsAddContact", task())
    assert result["status"] == "ok"
    assert result["retry_used"] is True
    assert result["binding"] == {"name": "Ada Lovelace", "number": "123456"}
    assert len(result["outputs"]) == len(result["calls"]) == 2
    assert tv.usage_total([result])["total_tokens"] == 24


def test_extracted_binding_drives_program_but_truth_drives_oracle():
    seen = []
    tasks = [task(i) for i in (1, 2, 3)]
    extracts = [{"status": "ok", "binding": {"name": f"Extracted {i}", "number": str(i)}}
                for i in (1, 2, 3)]
    def replay(source, binding, truth, timeout):
        seen.append((binding, truth, timeout))
        return {"passed": True, "error": None}
    gate = tv.evaluate_candidate("source", tasks, extracts, replay)
    assert gate["admitted"] is True and gate["passed"] == 3
    assert seen[0][0]["name"] == "Extracted 1"
    assert seen[0][1]["name"] == "Ada Lovelace"


def test_first_failure_marks_remaining_tasks_untested():
    tasks = [task(i) for i in (1, 2, 3)]
    extracts = [{"status": "ok", "binding": {}}] * 3
    calls = []
    def replay(*args):
        calls.append(1)
        return {"passed": len(calls) == 1, "error": "bad" if len(calls) == 2 else None}
    gate = tv.evaluate_candidate("source", tasks, extracts, replay)
    assert [row["status"] for row in gate["tasks"]] == ["pass", "fail", "untested"]
    assert len(calls) == 2 and not gate["admitted"]


def test_timeout_state_is_distinct():
    tasks = [task(i) for i in (1, 2, 3)]
    extracts = [{"status": "ok", "binding": {}}] * 3
    gate = tv.evaluate_candidate(
        "source", tasks, extracts,
        lambda *args: {"passed": False, "error": "deadline", "error_type": "timeout"},
    )
    assert gate["tasks"][0]["error_type"] == "timeout"
    assert gate["tasks"][1]["status"] == "untested"


def test_base_result_starts_from_cached_k3_and_tracks_final_separately(tmp_path):
    program = tmp_path / "artifact_k3_code.py"
    program.write_text("def program(device, binding): return True\n")
    record = tmp_path / "build.json"
    record.write_text("{}")
    result = tv.base_result(model="m", family="ContactsAddContact", attempt_id="a",
                            attempt_role="initial", price_sheet_id="prices-v1",
                            source_path=program, source_record_paths=[record], tasks=[])
    assert result["source"]["initial_program"]["path"].endswith("artifact_k3_code.py")
    assert result["initial_program_sha256"] != result["final_program_sha256"]
    assert result["status"] == "running"
    assert result["attempt_role"] == "initial"
    assert result["deployment"]["program_hash_equal"] is None


def test_missing_trajectories_use_explicit_seed_reconstruction(monkeypatch, tmp_path):
    monkeypatch.setattr(tv.android_env, "instance_params",
                        lambda family, seed: {"name": f"N{seed} X", "number": str(seed)})
    monkeypatch.setattr(tv.android_env, "get_task",
                        lambda family, condition, seed: SimpleNamespace(seed=seed))
    monkeypatch.setattr(tv.android_env, "goal_text", lambda task: f"goal {task.seed}")
    paths = [tmp_path / f"missing-s{seed}.jsonl" for seed in (1, 2, 3)]
    tasks = tv.original_building_tasks(
        "ContactsAddContact", paths,
        source_identity={"family": "ContactsAddContact", "seeds": [1, 2, 3],
                         "path": "build.json", "sha256": "abc"},
    )
    assert [row["prompt"] for row in tasks] == ["goal 1", "goal 2", "goal 3"]
    assert all(row["prompt_provenance"]["source_identity_match"] for row in tasks)
    assert all(row["prompt_provenance"]["recorded_prompt_match"].startswith("not_checkable")
               for row in tasks)


def test_resume_reuses_extractions_and_admitted_candidate(monkeypatch, tmp_path):
    program = tmp_path / "artifact_k3_code.py"
    program.write_text("def program(device, binding): return True\n")
    record = tmp_path / "build.json"
    record.write_text("{}")
    tasks = [task(i) for i in (1, 2, 3)]
    monkeypatch.setattr(tv, "original_building_tasks", lambda *a, **k: tasks)
    base = tv.base_result(model="m", family="ContactsAddContact", attempt_id="a",
                          attempt_role="initial", price_sheet_id="p",
                          source_path=program, source_record_paths=[record], tasks=tasks)
    base["extractions"] = [{"status": "ok", "binding": {"name": "Ada Lovelace", "number": "1"},
                            "calls": []} for _ in tasks]
    base["candidate_history"] = [{"version": 0,
        "program_sha256": tv.sha256_path(program), "interface_sha256": tv.interface_signature("ContactsAddContact"),
        "tasks": [{"seed": i, "status": "pass"} for i in (1, 2, 3)],
        "passed": 3, "admitted": True, "wall_s": 1.0}]
    class NoCalls:
        @property
        def chat(self):
            raise AssertionError("resume must not repeat paid extraction")
    result = tv.run_attempt(model="m", family="ContactsAddContact", attempt_id="a",
                            attempt_role="initial", price_sheet_id="p", source_path=program,
                            source_record_paths=[record], trajectory_paths=["x", "y", "z"],
                            client=NoCalls(), replay=lambda *a: (_ for _ in ()).throw(
                                AssertionError("admitted candidate must not replay")),
                            resume_result=base)
    assert result["admitted"] and result["terminal_status"] == "complete"
    assert len(result["extractions"]) == 3 and len(result["candidate_history"]) == 1


def test_typed_worker_payload_preserves_non_json_truth():
    truth = {"row_objects": [SimpleNamespace(start_ts=1, title="meeting")]}
    restored = pickle.loads(pickle.dumps({"binding": {"year": 2023}, "truth": truth}, protocol=5))
    assert restored["truth"]["row_objects"][0].title == "meeting"


def test_outer_watchdog_leaves_cleanup_grace_after_semantic_timeout():
    state = tv.replay_watchdog_state(1000.0, 1200.0)
    assert state["semantic_deadline_epoch"] == 2200.0
    assert state["deadline_epoch"] == 2260.0
    assert state["cleanup_grace_s"] == 60
    assert not tv.outer_watchdog_expired(state, 2200.0)  # child semantic timeout
    assert not tv.outer_watchdog_expired(state, 2259.9)  # cleanup/checkpoint grace
    assert tv.outer_watchdog_expired(state, 2260.1)


def test_outer_watchdog_ignores_preserved_heartbeat_from_prior_launch():
    stale = tv.replay_watchdog_state(1000.0, 1200.0)
    assert stale["deadline_epoch"] == 2260.0
    assert not tv.outer_watchdog_expired(
        stale, now_epoch=5000.0, supervisor_started_epoch=4000.0)
    fresh = tv.replay_watchdog_state(4001.0, 1200.0)
    assert tv.outer_watchdog_expired(
        fresh, now_epoch=fresh["deadline_epoch"] + 0.1,
        supervisor_started_epoch=4000.0)


def test_trajectory_resolver_maps_stale_absolute_path_into_live_repo(tmp_path):
    repo = tmp_path / "repo"
    source_cell = repo / "experimental-results" / "guiexp_android" / "t16_build" / "m" / "F"
    actual = repo / "experimental-results" / "guiexp_android" / "t12_grid" / "m" / "s1" / "trajectory.jsonl"
    source_cell.mkdir(parents=True)
    actual.parent.mkdir(parents=True)
    actual.write_text("{}\n")
    stale = "/Users/old/app/computer-use/experimental-results/guiexp_android/t12_grid/m/s1/trajectory.jsonl"
    resolved, provenance = tv.resolve_recorded_trajectory(
        stale, source_cell, 1, tmp_path / "bundle-miss.jsonl")
    assert resolved == actual.resolve()
    assert provenance["kind"] == "live_repo_absolute_suffix"


def test_three_empty_responses_retain_all_failed_stage_usage():
    responses = []
    for i in range(3):
        usage = SimpleNamespace(prompt_tokens=100 + i, completion_tokens=1,
                                prompt_tokens_details=None, cost=0.01 + i * 0.001)
        choice = SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")
        responses.append(SimpleNamespace(id=f"resp-{i}", choices=[choice], usage=usage))
    ledger = [tv.response_ledger_entry(response, "model", i + 1)
              for i, response in enumerate(responses)]
    totals = tv.response_ledger_totals(ledger)
    assert len(ledger) == 3 and totals["calls"] == 3
    assert totals["prompt_tokens"] == 303 and totals["completion_tokens"] == 3
    assert totals["cost_usd"] == 0.033
    assert [row["response_id"] for row in ledger] == ["resp-0", "resp-1", "resp-2"]
    assert all(row["finish_reason"] == "stop" and row["empty_response"] for row in ledger)
    accounting = tv.failed_stage_accounting(ledger)
    assert accounting["failure_kind"] == "empty_artifact_responses_exhausted"
    assert accounting["totals"] == totals
    assert accounting["max_attempts_exceeded_without_extra_retry"] is True


def test_pre_model_implementation_failure_reconciles_without_name_scope():
    accounting = tv.failed_stage_accounting([])
    assert accounting["failure_kind"] == "implementation_error"
    assert accounting["totals"]["calls"] == 0
    assert accounting["entries"] == []


def test_ambiguous_empty_extraction_is_not_format_retry():
    client = MetadataClient("", "stop", reasoning="internal")
    result = tv.extract_binding(client, "model", "ContactsAddContact", task())
    assert client.calls == 1
    assert result["status"] == "provider_output_undetermined"
    assert result["retry_used"] is False
    assert result["response_metadata"] == {
        "response_id": "response-1", "finish_reason": "stop",
        "raw_output_length": 0, "reasoning_length": 8,
    }


def test_length_finished_empty_extraction_is_generation_failure():
    client = MetadataClient("", "length")
    result = tv.extract_binding(client, "model", "ContactsAddContact", task())
    assert client.calls == 1
    assert result["status"] == "model_output_generation_failure"


def test_deployment_requirement_distinguishes_main_and_repeat_roles():
    assert tv.deployment_requirement("repeat", True) == "not_required_repeat"
    assert tv.deployment_requirement("repeat", False) == "not_required_repeat"
    assert tv.deployment_requirement("initial", True) == "required"
    assert tv.deployment_requirement("initial", False) == "skipped_rejected"


def test_completed_output_is_byte_preserved_and_skipped_without_api(tmp_path):
    path = tmp_path / "result.json"
    path.write_bytes(b'{"terminal_status":"complete","evidence":"keep"}\n')
    before = path.read_bytes()
    assert tv.output_entry_decision(path, resume=False) == "skip_complete"
    assert path.read_bytes() == before


def test_partial_output_requires_explicit_resume(tmp_path):
    path = tmp_path / "result.json"
    path.write_text('{"terminal_status":"running"}')
    try:
        tv.output_entry_decision(path, resume=False)
    except FileExistsError as exc:
        assert "--resume" in str(exc)
    else:
        raise AssertionError("partial result was accepted without explicit resume")
    assert tv.output_entry_decision(path, resume=True) == "resume"


def test_whole_attempt_timeout_requires_narrow_interruption_resume(tmp_path):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"terminal_status": "timeout",
                                "timeout_scope": "whole_attempt_guard",
                                "failure_kind": "replay_timeout"}))
    try:
        tv.output_entry_decision(path, resume=False)
    except FileExistsError as exc:
        assert "--resume-interrupted-attempt" in str(exc)
    else:
        raise AssertionError("whole-attempt timeout was accepted without explicit validation")
    assert tv.output_entry_decision(
        path, resume=False, resume_interrupted_attempt=True) == "resume_interrupted_attempt"


def test_guard_interruption_recovers_failed_candidate_without_replay(tmp_path):
    result = {
        "terminal_status": "timeout", "timeout_scope": "whole_attempt_guard",
        "family": "ContactsAddContact", "initial_program_sha256": "abc",
        "candidate_history": [], "repairs": [],
        "extractions": [{"status": "ok"}] * 3,
        "tasks": [{"seed": 1}, {"seed": 2}, {"seed": 3}],
        "timeout": {"started_epoch": 10, "semantic_deadline_epoch": 1210},
    }
    candidate = tv.recover_guarded_first_replay_candidate(result, ["action"], "abc")
    assert candidate["recovered_from_whole_attempt_guard"] is True
    assert candidate["tasks"][0]["error_type"] == "replay_timeout"
    assert [row["status"] for row in candidate["tasks"]] == ["fail", "untested", "untested"]


def test_official_timeout_requires_explicit_availability_resume(tmp_path):
    path = tmp_path / "result.json"
    record = {
        "terminal_status": "provider_error", "attempt_role": "repeat",
        "provider_route": {"route": "qwen_official_payg"},
        "failed_stage": {"error_type": "APITimeoutError"},
        "extractions": [{"status": "ok"}] * 3,
        "candidate_history": [{"passed": 0}],
    }
    path.write_text(json.dumps(record))
    try:
        tv.output_entry_decision(path, resume=False)
    except FileExistsError as exc:
        assert "--resume-availability-failure" in str(exc)
    else:
        raise AssertionError("provider timeout resumed without explicit flag")
    assert tv.output_entry_decision(
        path, resume=False, resume_availability_failure=True) == "resume_availability_failure"
