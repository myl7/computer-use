from pathlib import Path

from guiexp_webarena.trace_verifier import evaluate, extract_binding, load_original_tasks


class Usage:
    prompt_tokens = 10
    completion_tokens = 2
    prompt_tokens_details = None
    cost = 0.01


class Response:
    def __init__(self, text):
        self.choices = [type("Choice", (), {"message": type("M", (), {"content": text})()})()]
        self.usage = Usage()


class Completions:
    def __init__(self, replies):
        self.replies = iter(replies)

    def create(self, **_kwargs):
        return Response(next(self.replies))


def client(*replies):
    return type("Client", (), {"chat": type("Chat", (), {"completions": Completions(replies)})()})()


def test_extraction_records_input_output_and_one_retry(monkeypatch):
    monkeypatch.setattr("guiexp_webarena.agent._with_backoff", lambda fn, **kw: fn(**kw))
    task = {"seed": 1, "prompt": "Leave a comment."}
    record = extract_binding(task, "test/model", client("bad", '{"forum":"f","title":"t","text":"x"}'))
    assert record["type_check"] == "pass"
    assert record["retries"] == 1
    assert record["calls"][0]["output"] == "bad"
    assert record["calls"][1]["input"][1]["content"].find("previous reply was rejected") >= 0


class FakeRunner:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = []

    def run(self, _program, binding, _family, judge_params=None, device_factory=None):
        self.calls.append((binding, judge_params))
        return next(self.outcomes)


def test_three_of_three_and_input_is_distinct_from_judge():
    tasks = [{"seed": i, "params": {"truth": i}} for i in (1, 2, 3)]
    bindings = {i: {"model_input": i} for i in (1, 2, 3)}
    runner = FakeRunner([{"passed": True, "error": None}] * 3)
    result = evaluate(lambda *_: None, tasks, bindings, runner)
    assert result["passed"] == 3
    assert all(row["status"] == "pass" for row in result["detail"])
    assert runner.calls[0] == ({"model_input": 1}, {"truth": 1})


def test_first_failure_marks_remaining_untested():
    tasks = [{"seed": i, "params": {"truth": i}, "prompt": str(i)} for i in (1, 2, 3)]
    bindings = {i: {"model_input": i} for i in (1, 2, 3)}
    runner = FakeRunner([{"passed": False, "error": "oracle"}])
    result = evaluate(lambda *_: None, tasks, bindings, runner)
    assert [row["status"] for row in result["detail"]] == ["fail", "untested", "untested"]
    assert len(runner.calls) == 1


def test_prompt_provenance_uses_recorded_exploration(tmp_path, monkeypatch):
    explore = tmp_path / "explore"
    explore.mkdir()
    path = explore / "exploration.json"
    goals = [
        f'Leave a comment on the post titled "t" in the f forum, saying "x{i}".'
        for i in (1, 2, 3)
    ]
    path.write_text(__import__("json").dumps({"instances": [
        {"seed": i, "goal": goals[i - 1]} for i in (1, 2, 3)
    ]}))
    monkeypatch.setattr("guiexp_webarena.trace_verifier.instance_params",
                        lambda _family, seed, env=None:
                        {"forum": "f", "title": "t", "text": f"x{seed}"})
    tasks = load_original_tasks(tmp_path, object())
    assert [task["prompt"] for task in tasks] == goals
    assert tasks[0]["prompt_provenance"]["path"] == str(path)


def test_deadline_bypasses_generated_except_exception():
    from guiexp_webarena import program_runtime

    source = "def program(device, binding):\n    while True:\n        try:\n            pass\n        except Exception:\n            pass\n"
    _, program = program_runtime.program_from_source(source)
    env = type("Env", (), {})()
    env.reddit_url = "http://invalid"
    env.page = type("Page", (), {"goto": lambda *a, **k: None})()
    env.db_rows_typed = lambda _sql, columns: (
        [{"name": "f"}] if columns == ["name"] else [{"id": "1", "title": "t"}]
    )
    env.reset = lambda task: None
    env.reward = lambda task: 0
    runner = program_runtime.ProgramRunner(env)
    result = runner.run(program, {}, "CommentPost", judge_params={}, timeout_s=0.02)
    assert result["error"].startswith("replay timeout")


def _send_large(connection):
    connection.send({"trace": "x" * 1_000_000})
    connection.close()


def test_worker_payload_larger_than_pipe_buffer_is_received_before_join():
    import multiprocessing
    from guiexp_webarena.trace_verifier import _receive_worker

    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_send_large, args=(child,))
    process.start()
    child.close()
    payload = _receive_worker(process, parent, 5)
    parent.close()
    assert len(payload["trace"]) == 1_000_000
    assert not process.is_alive()
