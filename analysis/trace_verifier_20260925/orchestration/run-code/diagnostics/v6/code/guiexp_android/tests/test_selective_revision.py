"""Offline tests for the compiler_v2 evidence migration."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_pilot as pilot
from guiexp_android import selective_revision as revision
from guiexp_android import selective_budget as sb
from guiexp_android.budget_client import BudgetStop


def _origin_fixture(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    private = origin / "private" / "evaluator_bindings.json"
    private.parent.mkdir()
    private.write_text(json.dumps({"families": {family: [{"seed": 1, "params": {"value": "private"}, "params_sha256": "p", "binding_sha256": "b"}] for family in pilot.FAMILIES}}) + "\n")
    private.chmod(0o600)
    (origin / "manifest.json").write_text("{}\n")
    for family in pilot.FAMILIES:
        trace = origin / "training" / family / "s1" / "trajectory.jsonl"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(json.dumps({"record_type": "step", "step": 1, "pre_obs": {"ax_tree_text": "Save"}, "post_obs": {"ax_tree_text": "Saved"}, "action": {"action_type": "click"}}) + "\n")
    training = {
        "record_type": "selective-training",
        "families": {
            family: [{"family": family, "seed": 1, "attempt": 0, "success": family == "MarkorDeleteNote", "trajectory": f"training/{family}/s1/trajectory.jsonl"}]
            for family in pilot.FAMILIES
        },
    }
    (origin / "training_manifest.json").write_text(json.dumps(training) + "\n")
    rows = []
    statuses = ["done", "budget_stopped", "skipped_unbuildable", "pending"]
    for index, status in enumerate(statuses):
        row = {"id": f"{pilot.NAMESPACE}/MarkorDeleteNote/b{index + 1:02d}/reactive", "family": "MarkorDeleteNote", "binding_id": "b01", "arm": "reactive", "seed": 915201, "condition": "discover", "obs_mode": pilot.OBS_MODE, "max_actions": pilot.MAX_ACTIONS}
        rows.append(row)
        if status != "pending":
            state_dir = origin / "episodes" / row["id"]
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "state.json").write_text(json.dumps({"status": status}) + "\n")
    body = {
        "schema": pilot.SPEC_SCHEMA,
        "version": 1,
        "namespace": pilot.NAMESPACE,
        "model": pilot.MODEL,
        "provider": pilot.PROVIDER,
        "model_locks": {pilot.MODEL: {"provider": pilot.PROVIDER, "prompt_per_m": "0.09", "completion_per_m": "0.30", "max_tokens": 4096}},
        "families": list(pilot.FAMILIES),
        "arms": list(pilot.ARMS),
        "episodes": rows,
        "action_budget": pilot.MAX_ACTIONS,
        "local_assistance_max": pilot.MAX_LOCAL_ASSISTANCE,
        "obs_mode": pilot.OBS_MODE,
        "temperature": 0.0,
        "max_completion_tokens": 4096,
        "ledger": "missing.sqlite3",
        "run_lock": "missing.lock",
        "ledger_absolute": str(tmp_path / "missing.sqlite3"),
        "run_lock_absolute": str(tmp_path / "missing.lock"),
        "source_manifest": {},
        "source_manifest_sha256": "source",
        "private_bindings": "private/evaluator_bindings.json",
        "private_binding_visibility": "evaluator_only",
    }
    body["spec_sha256"] = pilot.hash_json(body)
    (origin / "spec.json").write_text(json.dumps(body) + "\n")
    return origin, origin / "training_manifest.json"


def test_prepare_versions_only_unexecuted_rows_and_preserves_origin_evidence(tmp_path, monkeypatch):
    origin, _ = _origin_fixture(tmp_path)
    out = tmp_path / "compiler_v2"
    monkeypatch.setattr(pilot, "source_manifest", lambda root=pilot.REPO_ROOT: {})
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: SimpleNamespace())
    spec = revision.prepare(origin, out)
    assert revision._load_version(out)["spec_sha256"] == spec["spec_sha256"]
    assert len(spec["episodes"]) == 2
    assert all(row["id"].startswith(f"{pilot.NAMESPACE}/{revision.VERSION}/") for row in spec["episodes"])
    assert all(row["receipt_episode_id"] == row["id"] for row in spec["episodes"])
    origin_record = json.loads((out / "origin.json").read_text())
    assert len(origin_record["preserved_terminal_rows"]) == 3
    assert origin_record["training"]["readonly"] is True
    assert origin_record["training"]["families"]["MarkorCreateNote"][0]["success"] is False


def test_builder_profile_is_fail_closed_until_budget_facade_exposes_it(tmp_path, monkeypatch):
    origin, _ = _origin_fixture(tmp_path)
    out = tmp_path / "compiler_v2"
    monkeypatch.setattr(pilot, "source_manifest", lambda root=pilot.REPO_ROOT: {})
    class Budget:
        def make_ledger(self):
            return SimpleNamespace(path=pilot.SHARED_LEDGER)

    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: Budget())
    revision.prepare(origin, out)
    monkeypatch.setattr(revision, "_load_version", lambda value: json.loads((out / "spec.json").read_text()))
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: Budget())
    monkeypatch.setattr(pilot, "_make_ledger", lambda budget, spec: SimpleNamespace(path=spec["ledger_absolute"]))
    with pytest.raises(BudgetStop, match="validate_builder_locks"):
        revision.build(out, max_families=1)


def test_compiler_v2_marks_unbuilt_family_rows_before_delegated_run(tmp_path, monkeypatch):
    origin, _ = _origin_fixture(tmp_path)
    out = tmp_path / "compiler_v2"
    monkeypatch.setattr(pilot, "source_manifest", lambda root=pilot.REPO_ROOT: {})
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: SimpleNamespace())
    spec = revision.prepare(origin, out)
    # Put one plan in place.  Other families must be terminally skipped and
    # must never enter pilot.run as reactive work.
    pilot.atomic_json(out / spec["plans"]["MarkorDeleteNote"], {"schema": pilot.PLAN_SCHEMA, "slots": {}, "steps": [{"id": "x", "intent": "x", "action": {"action_type": "open_app", "app_name": "Markor"}, "target": None, "before": [], "after": [{"text": "Markor"}]}]})
    spec = dict(spec)
    spec["episodes"] = list(spec["episodes"]) + [{
        "id": f"{pilot.NAMESPACE}/{revision.VERSION}/FilesMoveFile/b01/reactive",
        "family": "FilesMoveFile",
        "arm": "reactive",
        "binding_id": "b01",
        "seed": 1,
    }]
    pilot.atomic_json(out / "build" / "FilesMoveFile" / "build.json", {"status": "unbuildable"})
    captured = {}
    monkeypatch.setattr(revision, "_load_version", lambda value: spec)
    monkeypatch.setattr(pilot, "run", lambda out, max_episodes=None, families=None, env_file=None: captured.update({"out": out, "families": families}) or {"batch_status": "complete"})
    revision.run(out)
    assert captured["out"] == out.resolve()
    assert captured["families"] == ("MarkorDeleteNote",)
    states = list((out / "episodes").glob("**/state.json"))
    assert states
    assert all(json.loads(path.read_text())["status"] == "skipped_unbuildable" for path in states)


def test_real_budget_builder_profile_smoke_uses_16384_low_with_fake_sdk(tmp_path, monkeypatch):
    origin, training_path = _origin_fixture(tmp_path)
    training = json.loads(training_path.read_text())
    for rows in training["families"].values():
        rows[0]["success"] = True
    training_path.write_text(json.dumps(training) + "\n")
    out = tmp_path / "compiler_v2"
    monkeypatch.setattr(pilot, "source_manifest", lambda root=pilot.REPO_ROOT: {})
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: SimpleNamespace())
    spec = revision.prepare(origin, out)

    class Clock:
        def __init__(self):
            self.value = 1000.0

        def now(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    class Response:
        def __init__(self):
            self.content = json.dumps({
                "schema": pilot.PLAN_SCHEMA,
                "slots": {"dummy": "unused test slot"},
                "steps": [{
                    "id": "save",
                    "intent": "save the form",
                    "action": {"action_type": "click"},
                    "target": {"text": "Save"},
                    "before": [{"text": "Save"}],
                    "after": [{"text": "Saved"}],
                }],
            })
            self.raw = {
                "id": "gen-compiler-v2-test",
                "choices": [{"message": {"content": self.content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": "0.001"},
            }
            self.choices = [SimpleNamespace(message=SimpleNamespace(content=self.content))]
            self.usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, cost=0.001)

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

    def metadata(model):
        policy = sb.MODEL_LOCKS[model]
        return {
            "data": {
                "id": model,
                "architecture": {"output_modalities": ["text"]},
                "endpoints": [{
                    "tag": policy["provider"],
                    "status": 0,
                    "context_length": 1048576,
                    "max_completion_tokens": 131072,
                    "supported_parameters": ["max_tokens", "temperature", "reasoning"],
                    "pricing": {
                        "prompt": str(float(policy["prompt_per_m"]) / 1000000),
                        "completion": str(float(policy["completion_per_m"]) / 1000000),
                    },
                }],
            }
        }

    clock = Clock()
    sdk = SDK()
    ledger = sb.SelectiveLedger(tmp_path / "ledger.sqlite3", now=clock.now, host_guard=lambda: True)
    monkeypatch.setattr(revision, "_load_version", lambda value: json.loads((out / "spec.json").read_text()))
    monkeypatch.setattr(pilot, "_budget_module", lambda required=True: sb)
    monkeypatch.setattr(pilot, "_make_ledger", lambda budget, spec: ledger)
    monkeypatch.setattr(sb, "exclusive_run", lambda: contextlib.nullcontext())
    result = revision.build(
        out,
        max_families=1,
        sdk=sdk,
        metadata_fetcher=metadata,
        sleep=clock.sleep,
        host_guard=lambda: True,
    )
    assert result["status"] == "pilot_limit"
    assert result["families"]["MarkorCreateNote"]["status"] == "built"
    assert len(sdk.calls) == 1
    assert sdk.calls[0]["max_tokens"] == 16384
    assert sdk.calls[0]["extra_body"]["reasoning"] == {"effort": "low"}
    assert sdk.calls[0]["extra_body"]["provider"]["only"] == ["relace"]
