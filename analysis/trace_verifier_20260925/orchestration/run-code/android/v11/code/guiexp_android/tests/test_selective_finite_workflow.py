"""Fake subprocess checks for the finite selective workflow."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from guiexp_android import selective_finite_workflow as workflow


def _fixture(tmp_path: Path, count: int = 2):
    jobs_path = tmp_path / "jobs.json"
    outputs = []
    jobs = []
    for number in range(count):
        output = tmp_path / "versions" / f"v{number + 1}"
        output.mkdir(parents=True)
        (output / "spec.json").write_text(json.dumps({"version": number + 1}), encoding="utf-8")
        outputs.append(output)
        jobs.append({"out": str(output)})
    jobs_path.write_text(json.dumps(jobs), encoding="utf-8")
    return jobs_path, outputs


def _runner_factory(codes=None):
    calls = []
    codes = iter(codes or [])

    def runner(command, **kwargs):
        command = list(command)
        calls.append((command, kwargs))
        if command[2] == workflow.SUPERVISOR_MODULE:
            code = next(codes, 0)
            output = Path(command[command.index("--out") + 1])
            (output / "batch_status.run.json").write_text(
                json.dumps({"status": "completed" if code == 0 else "failed", "exit_code": code}),
                encoding="utf-8",
            )
        else:
            code = 0
        return SimpleNamespace(returncode=code, pid=60000 + len(calls), stdout="", stderr="")

    return runner, calls


def test_success_runs_two_jobs_and_uses_canonical_commands(tmp_path):
    jobs_path, outputs = _fixture(tmp_path)
    workflow_out = tmp_path / "workflow"
    spec = workflow.prepare(jobs_path, workflow_out)
    runner, calls = _runner_factory([0, 0])

    result = workflow.run(workflow_out, runner=runner)

    assert result["status"] == "completed"
    assert result["completed_jobs"] == 2
    assert [call[0][2] for call in calls] == [workflow.SUPERVISOR_MODULE, workflow.DEFAULT_HARNESS_MODULE] * 2
    assert calls[0][0] == [workflow.PYTHON, "-m", workflow.SUPERVISOR_MODULE, "--phase", "run",
                           "--harness-module", workflow.DEFAULT_HARNESS_MODULE, "--out",
                           str(outputs[0].resolve()), "--notify-events"]
    assert calls[1][0] == [workflow.PYTHON, "-m", workflow.DEFAULT_HARNESS_MODULE,
                           "--analyze", "--out", str(outputs[0].resolve())]
    saved = json.loads((workflow_out / "workflow_status.json").read_text())
    assert saved["jobs"][0]["supervisor_pid"] == 60001
    assert saved["jobs"][1]["analysis_exit_code"] == 0
    assert spec["canonical"]["cwd"] == str(workflow.WORKDIR.resolve())


def test_first_failure_halts_without_analysis_or_next_job(tmp_path):
    jobs_path, _outputs = _fixture(tmp_path)
    workflow_out = tmp_path / "workflow"
    workflow.prepare(jobs_path, workflow_out)
    runner, calls = _runner_factory([17, 0])

    result = workflow.run(workflow_out, runner=runner)

    assert result["status"] == "halted"
    assert result["halt_reason"] == "supervisor_failure"
    assert len(calls) == 1
    assert result["jobs"][0]["supervisor_exit_code"] == 17
    assert result["jobs"][1]["status"] == "pending"


def test_drift_is_before_pay_and_started_workflow_cannot_restart(tmp_path):
    jobs_path, outputs = _fixture(tmp_path, 1)
    workflow_out = tmp_path / "workflow"
    workflow.prepare(jobs_path, workflow_out)
    (outputs[0] / "spec.json").write_text(json.dumps({"version": "changed"}), encoding="utf-8")
    calls = []
    try:
        workflow.run(workflow_out, runner=lambda *args, **kwargs: calls.append(args))
    except workflow.WorkflowError:
        pass
    else:
        raise AssertionError("changed frozen spec was accepted")
    assert calls == []
    assert json.loads((workflow_out / "workflow_status.json").read_text())["status"] == "halted"

    jobs_path, outputs = _fixture(tmp_path / "again", 1)
    workflow_out = (tmp_path / "again") / "workflow"
    workflow.prepare(jobs_path, workflow_out)
    runner, calls = _runner_factory([0])
    workflow.run(workflow_out, runner=runner)
    try:
        workflow.run(workflow_out, runner=runner)
    except workflow.WorkflowError:
        pass
    else:
        raise AssertionError("started workflow was resumed")
    assert len(calls) == 2
