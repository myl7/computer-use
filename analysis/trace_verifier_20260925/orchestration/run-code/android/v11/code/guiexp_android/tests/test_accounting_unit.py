"""Per-call usage accounting: the records ``react_resume`` writes, and the
guard that a finished cell has one record per charged model call.

Zero LLM calls (MockOpenAI), zero emulator use (a scripted fake env).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guiexp_android import accounting_check, android_env, verify_runner
from guiexp_android.mock_model import MockOpenAI

FAMILY = "ContactsAddContact"


class FakeEnv:
    """A scripted env: ``steps_until_done`` model calls, then the task ends."""

    def __init__(self, steps_until_done: int = 3):
        self.steps_until_done = steps_until_done
        self.steps = 0
        self.resets = 0
        self.observes = 0

    def _obs(self):
        return {"url": "com.example/.Screen", "ax_tree_text": "UI element 0: {}"}

    def reset(self, task):
        self.resets += 1
        return self._obs()

    def observe(self, goal=None):
        self.observes += 1
        return self._obs()

    def step(self, action_text):
        self.steps += 1
        done = self.steps >= self.steps_until_done
        return self._obs(), done, (1.0 if done else 0.0)


def make_failure(seed: int = 1) -> dict:
    params = android_env.instance_params(FAMILY, seed)
    from guiexp_android.compiler import params_to_binding

    return {
        "instance": {
            "seed": seed,
            "params": params,
            "binding": params_to_binding(FAMILY, params),
            "goal": android_env.goal_text(android_env.get_task(FAMILY, "discover", seed)),
        },
        "error": "oracle mismatch",
        "trace": ['{"action_type": "click", "index": 2} -> com.example/.A'],
        "screen": "  0: text='Save' [clickable]",
    }


def run_resume(decision: str = "continue", steps: int = 3):
    env = FakeEnv(steps_until_done=steps)
    analysis = {"cause": "the Save control moved", "done": "", "plan": "",
                "decision": decision, "text": "CAUSE: the Save control moved"}
    result = verify_runner.react_resume(
        "mock", env, FAMILY, make_failure(), analysis,
        client=MockOpenAI(), max_steps=10,
    )
    return env, result


# ------------------------------------- A. react_resume's per-call records


def test_react_resume_records_one_call_per_model_call():
    _env, result = run_resume(steps=3)
    detail = result["calls_detail"]
    assert len(detail) == result["usage"]["calls"] == 3
    assert [r["step"] for r in detail] == [1, 2, 3]
    assert [r["call"] for r in detail] == [1, 2, 3]
    assert [r["first_call"] for r in detail] == [True, False, False]
    assert all(r["stage"] == "react_resume" for r in detail)


def test_react_resume_records_sum_to_the_totals():
    _env, result = run_resume(steps=4)
    folded = accounting_check.fold_calls(result["calls_detail"])
    totals = result["usage"]
    for field in ("calls", "prompt_tokens", "cached_tokens",
                  "completion_tokens", "total_tokens"):
        assert folded[field] == totals[field], field
    assert folded["cost_usd"] == pytest.approx(totals["cost_usd"])


def test_react_resume_records_carry_the_four_raw_numbers_twice_over():
    # flat, like build.json's rows; nested under "usage", like trajectory.jsonl
    _env, result = run_resume(steps=2)
    for record in result["calls_detail"]:
        for field in accounting_check.PER_CALL_FIELDS:
            assert field in record
            assert field in record["usage"]
        assert record["prompt_tokens"] == record["usage"]["prompt_tokens"]
        assert record["prompt_tokens"] > 0


def test_the_prompt_grows_call_on_call_so_the_cache_unit_is_recoverable():
    # Section 8 needs call i's prompt and call i-1's prompt + completion. The
    # records make that a local read, which stage totals never could.
    _env, result = run_resume(steps=3)
    prompts = [r["prompt_tokens"] for r in result["calls_detail"]]
    assert prompts == sorted(prompts) and prompts[0] < prompts[-1]


def test_a_restart_decision_resets_and_still_records_every_call():
    env, result = run_resume(decision="restart", steps=2)
    assert env.resets == 1 and env.observes == 0
    assert result["restarted"] is True
    assert len(result["calls_detail"]) == 2


def test_a_continue_decision_observes_instead_of_resetting():
    env, result = run_resume(decision="continue", steps=2)
    assert env.observes == 1 and env.resets == 0
    assert result["restarted"] is False
    assert len(result["calls_detail"]) == 2


# ------------------------------- B. verify_and_repair persists the records


def install_resume_fakes(monkeypatch, tmp_path):
    """Drive verify_and_repair with one real resume episode, everything else
    scripted, so the on-disk records come from the real code path."""
    from test_build_protocol_unit import install_fakes  # same-directory helper

    real_resume = verify_runner.react_resume
    runner, calls = install_fakes(monkeypatch, [False] + [True] * 20)

    def resume_on_a_fake_phone(model, env, family, failure, analysis,
                               obs_mode="screenshot+ax", client=None, max_steps=None):
        return real_resume(
            model, FakeEnv(steps_until_done=2), family, failure, analysis,
            client=MockOpenAI(), max_steps=10,
        )

    monkeypatch.setattr(verify_runner, "react_resume", resume_on_a_fake_phone)
    return runner, calls


def test_verify_and_repair_writes_resume_calls_to_disk(monkeypatch, tmp_path):
    install_resume_fakes(monkeypatch, tmp_path)
    record = verify_runner.verify_and_repair(
        "mock", FAMILY, "def program(device, binding):\n    return True\n",
        env=None, out_dir=tmp_path,
    )
    assert record["refinements"] == 1
    path = tmp_path / "resume1_calls.jsonl"
    assert path.exists()
    on_disk = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(on_disk) == 2
    assert [r["first_call"] for r in on_disk] == [True, False]
    folded = accounting_check.fold_calls(on_disk)
    assert folded["calls"] == record["totals"]["resume_episodes"]["calls"]
    assert folded["prompt_tokens"] == record["totals"]["resume_episodes"]["prompt_tokens"]


def test_the_round_record_carries_every_stage_of_that_round(monkeypatch, tmp_path):
    install_resume_fakes(monkeypatch, tmp_path)
    record = verify_runner.verify_and_repair(
        "mock", FAMILY, "def program(device, binding):\n    return True\n",
        env=None, out_dir=tmp_path,
    )
    round_ = record["rounds"][0]
    assert round_["resume"]["calls"] == len(round_["resume"]["calls_detail"])
    for field in accounting_check.PER_CALL_FIELDS:
        assert field in round_["analysis"]  # the analyzer call
        assert field in round_["refinement_usage"]  # the builder refinement call
    # the diagnosis fields the repair loop reads are still there
    assert round_["analysis"]["decision"] in ("continue", "restart")
    assert round_["analysis"]["first_call"] is True


def test_the_stage_totals_are_unchanged_by_the_new_fields(monkeypatch, tmp_path):
    install_resume_fakes(monkeypatch, tmp_path)
    record = verify_runner.verify_and_repair(
        "mock", FAMILY, "def program(device, binding):\n    return True\n",
        env=None, out_dir=tmp_path,
    )
    totals = record["totals"]
    assert set(totals) == {"analyzer", "resume_episodes", "builder_refinements",
                           "program_replays"}
    for key in ("analyzer", "resume_episodes", "builder_refinements"):
        assert set(totals[key]) == {"calls", "prompt_tokens", "cached_tokens",
                                    "completion_tokens", "total_tokens", "cost_usd"}
        assert totals[key]["total_tokens"] > 0


# --------------------------------------------- C. the finished-cell guard


def write_cell(tmp_path: Path, **overrides) -> Path:
    """A minimal finished cell that passes the guard, before overrides."""
    cell = tmp_path / "cell"
    cell.mkdir(parents=True, exist_ok=True)

    def usage(prompt=100, completion=10):
        return {"prompt_tokens": prompt, "cached_tokens": 0,
                "completion_tokens": completion, "cost_usd": 1e-5}

    def trajectory(path: Path, calls: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({"step": i, "usage": usage(), "obs_meta": {}})
                 for i in range(1, calls + 1)]
        lines.append(json.dumps({"record_type": "final", "model_calls": calls}))
        path.write_text("\n".join(lines) + "\n")

    trajectory(cell / "explore" / "s1_a0" / "trajectory.jsonl", 2)
    trajectory(cell / "doc_arm" / "s4" / "trajectory.jsonl", 2)

    (cell / "translation.json").write_text(json.dumps({
        "1": {"steps": [{"step": 1, **usage(), "usage": usage()}],
              "totals": {"calls": 1}},
    }))
    (cell / "verify.json").write_text(json.dumps({
        "rounds": [{
            "analysis": {"decision": "continue",
                         **accounting_check.call_record(1, 1, usage(), stage="analyzer")},
            "resume": {"calls": 2, "calls_detail": [
                accounting_check.call_record(1, 1, usage(), stage="react_resume"),
                accounting_check.call_record(2, 2, usage(), stage="react_resume"),
            ]},
            "refinement_usage": accounting_check.call_record(
                1, 1, usage(), stage="builder_refine"),
        }],
    }))
    (cell / "deploy.json").write_text(json.dumps({
        "uses": [{"tokens": 110, "calls_detail": [
            accounting_check.call_record(1, 1, usage(), stage="deploy_extract")]}],
    }))

    build = {
        "exploration": {
            "totals": {"calls": 2},
            "reflection_calls": 0,
            "reflections": [],
            "per_episode": [{"seed": 1, "attempt": 0, "reused": False, "model_calls": 2}],
        },
        "translator": {"totals": {"calls": 1}},
        "builder": {"totals_all_calls": {"calls": 1},
                    "initial": {"k1_code": {"k": 1, **usage()}}},
        "verification": {"analyzer": {"calls": 1}, "resume_episodes": {"calls": 2},
                         "builder_refinements": {"calls": 1}},
        "deploy": {"total_tokens": 110},
        "doc_arm": {"totals": {"calls": 2},
                    "episodes": [{"seed": 4, "model_calls": 2}]},
    }
    for key, value in overrides.items():
        build[key] = value
    (cell / "build.json").write_text(json.dumps(build))
    return cell


def test_a_fully_instrumented_cell_passes(tmp_path):
    assert accounting_check.check_cell(write_cell(tmp_path)) == []


def test_a_resume_with_only_totals_is_caught(tmp_path):
    cell = write_cell(tmp_path)
    verify = json.loads((cell / "verify.json").read_text())
    del verify["rounds"][0]["resume"]["calls_detail"]
    (cell / "verify.json").write_text(json.dumps(verify))
    problems = accounting_check.check_cell(cell)
    assert any("resume episode has no per-call records" in p for p in problems)


def test_a_resume_whose_records_do_not_match_the_charge_is_caught(tmp_path):
    cell = write_cell(tmp_path)
    verify = json.loads((cell / "verify.json").read_text())
    verify["rounds"][0]["resume"]["calls_detail"].pop()
    (cell / "verify.json").write_text(json.dumps(verify))
    problems = accounting_check.check_cell(cell)
    assert any("charged 2 calls but recorded 1" in p for p in problems)


def test_a_deploy_use_with_only_totals_is_caught(tmp_path):
    cell = write_cell(tmp_path)
    deploy = json.loads((cell / "deploy.json").read_text())
    del deploy["uses"][0]["calls_detail"]
    (cell / "deploy.json").write_text(json.dumps(deploy))
    assert any("deploy: use 1" in p for p in accounting_check.check_cell(cell))


def test_a_missing_episode_trajectory_is_caught(tmp_path):
    cell = write_cell(tmp_path)
    (cell / "explore" / "s1_a0" / "trajectory.jsonl").unlink()
    assert any("exploration: seed 1" in p for p in accounting_check.check_cell(cell))
    (cell / "doc_arm" / "s4" / "trajectory.jsonl").unlink()
    assert any("doc_arm: seed 4" in p for p in accounting_check.check_cell(cell))


def test_a_reused_episode_is_not_asked_for_records_in_this_cell(tmp_path):
    cell = write_cell(tmp_path)
    build = json.loads((cell / "build.json").read_text())
    build["exploration"]["per_episode"][0]["reused"] = True
    (cell / "build.json").write_text(json.dumps(build))
    (cell / "explore" / "s1_a0" / "trajectory.jsonl").unlink()
    assert accounting_check.check_cell(cell) == []


def test_a_stage_with_no_model_calls_is_not_asked_for_records(tmp_path):
    cell = write_cell(tmp_path, translator={"totals": {"calls": 0}})
    (cell / "translation.json").unlink()
    assert accounting_check.check_cell(cell) == []


def test_a_missing_build_json_is_reported_not_raised(tmp_path):
    problems = accounting_check.check_cell(tmp_path / "nothing-here")
    assert len(problems) == 1 and "build.json" in problems[0]


def test_the_warning_never_raises_and_returns_the_problems(tmp_path, capsys):
    cell = write_cell(tmp_path)
    deploy = json.loads((cell / "deploy.json").read_text())
    del deploy["uses"][0]["calls_detail"]
    (cell / "deploy.json").write_text(json.dumps(deploy))
    problems = accounting_check.warn_if_unaccountable(cell)
    assert problems
    assert "WARNING" in capsys.readouterr().out


# ------------------------- D. the other stages that call the model directly


class SequencedClient:
    """Returns the given replies in order, one per call, with real usage."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = 0
        self.chat = type("_Chat", (), {})()
        self.chat.completions = self

    def create(self, *, model=None, messages=None, **_kw):
        from types import SimpleNamespace

        reply = self.replies[min(self.seen, len(self.replies) - 1)]
        self.seen += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=SimpleNamespace(prompt_tokens=100 * self.seen, completion_tokens=10,
                                  cost=1e-5),
            model=model or "mock",
        )


def test_a_builder_reply_that_is_rejected_is_still_recorded():
    # the first reply is charged and then thrown away for not compiling; a
    # stage total would lose it entirely
    from guiexp_android import compiler

    client = SequencedClient(["```python\ndef program(\n```",
                              "```python\ndef program(device, binding):\n    return True\n```"])
    result = compiler.call_builder(
        "mock", [{"role": "user", "content": "build it"}], artifact="code",
        client=client, retry_backoff_s=0.0,
    )
    detail = result["calls_detail"]
    assert len(detail) == 2 == client.seen
    assert [r["accepted"] for r in detail] == [False, True]
    assert detail[0]["prompt_tokens"] == 100 and detail[1]["prompt_tokens"] == 200
    # the build is charged for both attempts; calls_detail keeps the
    # accepted-only figure recoverable
    assert result["usage"]["prompt_tokens"] == 300
    assert result["usage"]["completion_tokens"] == 20
    assert result["cost_usd"] == pytest.approx(2e-5)
    accepted = [c for c in detail if c["accepted"]]
    assert accounting_check.fold_calls(accepted)["prompt_tokens"] == 200


def test_a_builder_call_that_lands_first_time_charges_exactly_that_call():
    from guiexp_android import compiler

    client = SequencedClient(
        ["```python\ndef program(device, binding):\n    return True\n```"])
    result = compiler.call_builder(
        "mock", [{"role": "user", "content": "build it"}], artifact="code",
        client=client, retry_backoff_s=0.0,
    )
    assert client.seen == 1
    assert result["usage"]["prompt_tokens"] == 100
    assert accounting_check.fold_calls(result["calls_detail"])["prompt_tokens"] == 100


def test_sum_usage_keeps_an_unreported_field_unreported():
    assert accounting_check.sum_usage([
        {"prompt_tokens": 10, "completion_tokens": 1, "cached_tokens": None,
         "cost_usd": None},
        {"prompt_tokens": 20, "completion_tokens": 2, "cached_tokens": None,
         "cost_usd": None},
    ]) == {"prompt_tokens": 30, "completion_tokens": 3, "cached_tokens": None,
           "cost_usd": None}
    summed = accounting_check.sum_usage([
        {"prompt_tokens": 10, "cached_tokens": None, "cost_usd": None},
        {"prompt_tokens": 20, "cached_tokens": 5, "cost_usd": 1e-5},
    ])
    assert summed["cached_tokens"] == 5 and summed["cost_usd"] == pytest.approx(1e-5)


def test_a_deploy_use_records_each_extraction_call():
    from guiexp_android import deploy_runner

    class PassingRunner:
        def run(self, program, binding, family, judge_params=None, **_kw):
            return {"passed": True, "error": None, "reward": 1.0, "device": None}

    goal, expected = deploy_runner.deploy_uses(FAMILY, 1)[0]
    record = deploy_runner.run_single_use(
        program=lambda device, binding: True, family=FAMILY, goal=goal,
        expected=expected, expected_params=None,
        client=deploy_runner.MockExtractor(FAMILY), model="mock",
        runner=PassingRunner(),
    )
    detail = record["calls_detail"]
    assert len(detail) == record["retries"] + 1
    assert detail[0]["first_call"] is True
    assert detail[0]["kind"] == "extract"
    folded = accounting_check.fold_calls(detail)
    assert folded["total_tokens"] == record["tokens"]
    assert folded["cost_usd"] == pytest.approx(record["cost_usd"])


def test_run_build_runs_the_guard_and_records_what_it_found(tmp_path, monkeypatch, capsys):
    from test_build_protocol_unit import install_build_fakes, run_mock_build

    install_build_fakes(monkeypatch)
    out = tmp_path / "cell"
    record = run_mock_build(out)
    assert "accounting_problems" in record
    assert json.loads((out / "build.json").read_text())["accounting_problems"] \
        == record["accounting_problems"]
    if record["accounting_problems"]:  # a warning, never an exception
        assert "WARNING" in capsys.readouterr().out
