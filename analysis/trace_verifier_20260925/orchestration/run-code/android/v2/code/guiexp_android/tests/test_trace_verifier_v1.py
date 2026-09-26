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
