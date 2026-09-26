import json
import multiprocessing as mp
import time
from pathlib import Path
from types import SimpleNamespace

from guiexp_osworld import families
from guiexp_osworld.three_building_verifier import (
    PROTOCOL_ID,
    atomic_json_write,
    canonical_launch_state,
    hard_replay,
    load_initial_attempt,
    verify_initial,
    verify_and_repair,
)


PROGRAM = "def program(device, binding):\n    return True\n"


class FakeCompletions:
    def __init__(self, family):
        self.family = family
        self.index = 0

    def create(self, **_kwargs):
        self.index += 1
        binding = families.params_to_binding(
            self.family, families.instance_params(self.family, self.index))
        message = SimpleNamespace(content=json.dumps(binding))
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                                prompt_tokens_details=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class FakeClient:
    def __init__(self, family):
        self.chat = SimpleNamespace(completions=FakeCompletions(family))


def make_build(tmp_path: Path, family="WriterMemoSave") -> Path:
    instances = []
    for seed in (1, 2, 3):
        params = families.instance_params(family, seed)
        instances.append({"seed": seed, "goal": families.goal_text(family, seed, params)})
    build = {
        "family": family,
        "model": "mock",
        "exploration": {"instances": instances},
        "translator": {"totals": {"calls": 3, "total_tokens": 30, "cost_usd": 0.01}},
        "builder": {"initial": {"k3_code": {
            "artifact": "code",
            "usage": {"calls": 1, "total_tokens": 20, "cost_usd": 0.02},
        }}},
    }
    path = tmp_path / "build.json"
    path.write_text(json.dumps(build))
    (tmp_path / "artifact_k3_code.py").write_text(PROGRAM)
    return path


def test_loads_initial_k3_and_recorded_prompts(tmp_path):
    loaded = load_initial_attempt(make_build(tmp_path))
    assert loaded["program_source"] == PROGRAM
    assert [x["seed"] for x in loaded["instances"]] == [1, 2, 3]
    assert all(x["prompt_provenance"]["generator_match"] for x in loaded["instances"])


def test_requires_three_of_three_and_keeps_judge_independent(tmp_path):
    seen = []

    def replay(_source, binding, _family, judge_params, timeout_s):
        seen.append((binding, judge_params, timeout_s))
        return {"passed": True, "status": "pass", "wall_s": 0.1, "error": None}

    result = verify_initial(make_build(tmp_path), FakeClient("WriterMemoSave"), replay=replay)
    assert result["protocol_id"] == PROTOCOL_ID
    assert result["attempt_id"] == "initial"
    assert result["attempt_role"] == "initial"
    assert result["terminal_status"] == "complete"
    assert result["admitted"] is True
    assert [t["status"] for t in result["tasks"]] == ["pass", "pass", "pass"]
    assert len(result["usage"]["new_extraction_calls"]) == 3
    assert all(binding == families.params_to_binding("WriterMemoSave", judge)
               for binding, judge, _timeout in seen)
    assert all(binding is not judge for binding, judge, _timeout in seen)


def test_first_failure_marks_remaining_untested(tmp_path):
    calls = 0

    def replay(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"passed": False, "status": "fail", "wall_s": 0.1, "error": "no"}

    result = verify_initial(make_build(tmp_path), FakeClient("WriterMemoSave"), replay=replay)
    assert calls == 1
    assert result["admitted"] is False
    assert [t["status"] for t in result["tasks"]] == ["fail", "untested", "untested"]


def test_all_bindings_are_extracted_before_first_replay(tmp_path):
    client = FakeClient("WriterMemoSave")

    def replay(*_args, **_kwargs):
        assert client.chat.completions.index == 3
        return {"passed": False, "status": "fail", "wall_s": 0.1, "error": "no"}

    result = verify_initial(make_build(tmp_path), client, replay=replay)
    assert len(result["usage"]["new_extraction_calls"]) == 3


def sleeping_worker(_queue, *_args):
    try:
        while True:
            time.sleep(1)
    except Exception:
        # A swallowed Python exception cannot disable the parent deadline.
        sleeping_worker(_queue, *_args)


def large_result_worker(result_queue, *_args):
    result_queue.put({"passed": False, "reward": 0.0, "error": "x" * 1_000_000})


def test_hard_timeout_is_external_to_generated_worker():
    result = hard_replay(PROGRAM, {}, "WriterMemoSave", {}, timeout_s=0.05,
                         context=mp.get_context("fork"), _worker=sleeping_worker)
    assert result["status"] == "timeout"
    assert result["passed"] is False


def test_large_worker_result_is_drained_before_join():
    result = hard_replay(PROGRAM, {}, "WriterMemoSave", {}, timeout_s=2,
                         context=mp.get_context("fork"), _worker=large_result_worker)
    assert result["status"] == "fail"
    assert len(result["error"]) == 1_000_000


def test_repair_rechecks_all_three_with_cached_bindings(tmp_path, monkeypatch):
    from guiexp_osworld import compiler, verify_runner

    source_versions = []

    def replay(source, *_args, **_kwargs):
        source_versions.append(source)
        if source == PROGRAM:
            return {"passed": False, "status": "fail", "wall_s": 0.1,
                    "error": "program fail", "trace": [], "screen": ""}
        return {"passed": True, "status": "pass", "wall_s": 0.1,
                "error": None, "trace": [], "screen": ""}

    usage = {"prompt_tokens": 10, "cached_tokens": 0,
             "completion_tokens": 5, "cost_usd": 0.001}
    monkeypatch.setattr(verify_runner, "analyze", lambda *_a, **_k: {
        "usage": usage, "text": "CAUSE: x", "cause": "x", "done": "",
        "plan": "fix", "decision": "restart"})
    monkeypatch.setattr(verify_runner, "react_resume", lambda *_a, **_k: {
        "calls_detail": [], "actions": [], "success": False,
        "restarted": True, "steps": 0})
    monkeypatch.setattr(compiler, "refine_artifact", lambda *_a, **_k: {
        "artifact_text": PROGRAM + "# repaired\n", "usage": usage,
        "attempts": 1, "calls_detail": []})
    result, _source = verify_and_repair(
        make_build(tmp_path), FakeClient("WriterMemoSave"), replay=replay)
    assert result["admitted"] is True
    assert result["repair_rounds"] == 1
    assert [t["status"] for t in result["tasks"]] == ["pass", "pass", "pass"]
    assert len(source_versions) == 4  # one failed initial replay, then three rechecks


def test_extraction_retries_429_only(monkeypatch):
    from guiexp_osworld.three_building_verifier import extract_binding

    client = FakeClient("WriterMemoSave")
    original = client.chat.completions.create
    attempts = 0

    class RateLimit(Exception):
        status_code = 429

    def flaky(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RateLimit("429 shared pool")
        return original(**kwargs)

    client.chat.completions.create = flaky
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    goal = families.goal_text("WriterMemoSave", 1,
                              families.instance_params("WriterMemoSave", 1))
    result = extract_binding(client, "mock", "WriterMemoSave", goal)
    assert result["type_check"]["passed"] is True
    assert [a["status"] for a in result["provider_attempts"]] == [
        "rate_limited", "rate_limited", "success"]


def test_resume_reuses_cached_extractions(tmp_path, monkeypatch):
    from guiexp_osworld import compiler, verify_runner

    client = FakeClient("WriterMemoSave")
    prior = verify_initial(
        make_build(tmp_path), client,
        replay=lambda *_a, **_k: {"passed": False, "status": "fail",
                                  "wall_s": 0.1, "error": "program fail",
                                  "trace": [], "screen": ""})
    prior["terminal_status"] = "provider_error"
    usage = {"prompt_tokens": 1, "cached_tokens": 0,
             "completion_tokens": 1, "cost_usd": 0.0}
    monkeypatch.setattr(verify_runner, "analyze", lambda *_a, **_k: {
        "usage": usage, "text": "", "cause": "x", "done": "",
        "plan": "fix", "decision": "restart"})
    monkeypatch.setattr(verify_runner, "react_resume", lambda *_a, **_k: {
        "calls_detail": [], "actions": [], "success": False,
        "restarted": True, "steps": 0})
    monkeypatch.setattr(compiler, "refine_artifact", lambda *_a, **_k: {
        "artifact_text": PROGRAM + "# fixed\n", "usage": usage,
        "attempts": 1, "calls_detail": []})
    result, _ = verify_and_repair(
        tmp_path / "build.json", client,
        replay=lambda source, *_a, **_k: {
            "passed": source != PROGRAM, "status": "pass" if source != PROGRAM else "fail",
            "wall_s": 0.1, "error": None, "trace": [], "screen": ""},
        prior_result=prior, prior_source=PROGRAM)
    assert client.chat.completions.index == 3
    assert len(result["extractions"]) == 3


def test_builder_checkpoints_three_paid_empty_replies(monkeypatch):
    from guiexp_osworld.compiler import GenerationFailure, call_builder

    class EmptyCompletions:
        count = 0

        def create(self, **_kwargs):
            self.count += 1
            return SimpleNamespace(
                id=f"resp-{self.count}",
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=""), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=1,
                                      prompt_tokens_details=None),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions()))
    saved = []
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    try:
        call_builder("mock", [{"role": "user", "content": "build"}],
                     client=client, max_attempts=3,
                     call_checkpoint=lambda record: saved.append(dict(record)))
    except GenerationFailure as exc:
        assert "failed after 3 attempts" in str(exc)
    else:
        raise AssertionError("three empty replies must exhaust builder attempts")
    assert len(saved) == 3
    assert [record["response_id"] for record in saved] == [
        "resp-1", "resp-2", "resp-3"]
    assert all(record["finish_reason"] == "stop" for record in saved)
    assert sum(record["total_tokens"] for record in saved) == 36


def test_builder_classifies_length_responses_as_generation_failure(monkeypatch):
    from guiexp_osworld.compiler import GenerationFailure, call_builder

    class LengthCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                id="capped", choices=[SimpleNamespace(
                    message=SimpleNamespace(content="partial"), finish_reason="length")],
                usage=SimpleNamespace(prompt_tokens=7, completion_tokens=13,
                                      prompt_tokens_details=None))

    saved = []
    client = SimpleNamespace(chat=SimpleNamespace(completions=LengthCompletions()))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    try:
        call_builder("mock", [{"role": "user", "content": "build"}], client=client,
                     max_attempts=3, call_checkpoint=lambda record: saved.append(dict(record)))
    except GenerationFailure:
        pass
    else:
        raise AssertionError("length completion must be a generation failure")
    assert len(saved) == 3
    assert all(record["finish_reason"] == "length" for record in saved)


def test_canonical_launch_skips_complete_and_requires_explicit_resume(tmp_path):
    path = tmp_path / "result.json"
    identity = {"protocol_id": PROTOCOL_ID, "platform": "desktop", "model": "mock",
                "family": "WriterMemoSave", "attempt_id": "initial",
                "attempt_role": "initial"}
    atomic_json_write(path, {**identity, "terminal_status": "complete"})
    state, _ = canonical_launch_state(path, resume=False, model="mock",
                                      family="WriterMemoSave", attempt_id="initial",
                                      attempt_role="initial")
    assert state == "skip"
    atomic_json_write(path, {**identity, "terminal_status": "provider_error", "tasks": []})
    try:
        canonical_launch_state(path, resume=False, model="mock",
                               family="WriterMemoSave", attempt_id="initial",
                               attempt_role="initial")
    except FileExistsError:
        pass
    else:
        raise AssertionError("partial output must require --resume")
    state, _ = canonical_launch_state(path, resume=True, model="mock",
                                      family="WriterMemoSave", attempt_id="initial",
                                      attempt_role="initial")
    assert state == "resume"


def test_canonical_resume_rejects_identity_and_partial_extraction_set(tmp_path):
    path = tmp_path / "result.json"
    base = {"protocol_id": PROTOCOL_ID, "platform": "desktop", "model": "mock",
            "family": "WriterMemoSave", "attempt_id": "initial",
            "attempt_role": "initial", "terminal_status": "provider_error"}
    atomic_json_write(path, {**base, "tasks": []})
    try:
        canonical_launch_state(path, resume=True, model="wrong",
                               family="WriterMemoSave", attempt_id="initial",
                               attempt_role="initial")
    except ValueError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("identity mismatch must reject resume")
    atomic_json_write(path, {**base, "tasks": [{"extraction": {"binding": {}}}]})
    try:
        canonical_launch_state(path, resume=True, model="mock",
                               family="WriterMemoSave", attempt_id="initial",
                               attempt_role="initial")
    except ValueError as exc:
        assert "1/3" in str(exc)
    else:
        raise AssertionError("partial extraction cache must reject resume")


def test_official_route_maps_model_and_receipts_text_image_usage():
    from guiexp_osworld.agent import OSWorldAgent
    from guiexp_osworld.routing import RoutedOpenAIClient, attach_official_accounting

    captured = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                id="official-response", model="qwen3.8-flash",
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"),
                                         finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20,
                                      prompt_tokens_details=SimpleNamespace(cached_tokens=40)))

    route = {
        "route_id": "qwen-official-payg-v1", "provider": "official",
        "base_url": "https://example.invalid/v1",
        "canonical_model": "qwen/qwen3.8-flash", "served_model": "qwen3.8-flash",
        "rates_cny_per_million": {"input": 0.8, "output": 2.7, "cache_read": 0.1},
        "pricing_source": "test", "pricing_note": "test",
    }
    underlying = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    client = RoutedOpenAIClient(underlying, route)
    response = client.chat.completions.create(
        model="qwen/qwen3.8-flash", temperature=0.0,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]}])
    assert captured["model"] == "qwen3.8-flash"
    assert captured["messages"][0]["content"][1]["type"] == "image_url"
    usage = OSWorldAgent._usage(response)
    assert usage["route_receipt"]["canonical_model"] == "qwen/qwen3.8-flash"
    assert usage["route_receipt"]["requested_temperature"] == 0.0
    record = {"call": {"usage": usage}}
    attach_official_accounting(record)
    assert record["official_route_accounting"]["calls"] == 1
    assert record["official_route_accounting"]["cost_cny"] > 0
