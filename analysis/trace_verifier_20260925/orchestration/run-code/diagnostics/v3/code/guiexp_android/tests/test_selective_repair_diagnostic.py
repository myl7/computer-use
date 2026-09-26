"""Offline contract and bounded-run checks for selector_repair_v7."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_explore_budget as budget
from guiexp_android import selective_pilot as pilot
from guiexp_android import selective_repair_diagnostic as diagnostic


MODEL = diagnostic.MODEL


def _write_authorization(path: Path, ledger: Path, run_lock: Path) -> None:
    unsigned = {
        "schema": "selective-explore-budget-authorization/1",
        "namespace": budget.EXPLORE_NAMESPACE,
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
    body = dict(unsigned, authorization_sha256=budget.hashlib.sha256(budget.canonical(unsigned).encode()).hexdigest())
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")


def _budget_fixture(monkeypatch, tmp_path: Path):
    ledger_path = (tmp_path / "budget.sqlite3").resolve()
    run_lock = (tmp_path / "run.lock").resolve()
    auth_path = (tmp_path / "authorization.json").resolve()
    monkeypatch.setattr(budget, "SHARED_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(budget, "SHARED_RUN_LOCK_PATH", run_lock)
    monkeypatch.setattr(budget, "AUTHORIZATION_PATH", auth_path)
    monkeypatch.setattr(pilot, "SHARED_LEDGER", ledger_path)
    monkeypatch.setattr(pilot, "SHARED_RUN_LOCK", run_lock)
    monkeypatch.setattr(diagnostic, "AUTHORIZATION_PATH", auth_path)
    budget.base.SelectiveLedger(ledger_path, host_guard=lambda: True)
    _write_authorization(auth_path, ledger_path, run_lock)
    monkeypatch.setattr(budget, "before_ui_action", lambda guard=None: True)
    return ledger_path, run_lock, auth_path


def _reset_pacing(ledger_path: Path):
    """Advance the real ledger's cooldown without sleeping in the test."""
    import sqlite3

    with sqlite3.connect(ledger_path) as database:
        database.execute("UPDATE pacing_v2 SET next_send=0 WHERE id=1")


def _metadata(model: str = MODEL) -> dict:
    profile = budget._PROVIDER_PROFILES["z_ai_fp8"]
    return {
        "data": {
            "id": model,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [{
                "tag": profile["provider"],
                "status": 0,
                "context_length": profile["context_length"],
                "max_completion_tokens": 4096,
                "supported_parameters": ["max_tokens"],
                "pricing": {
                    "prompt": "0.00000015",
                    "completion": "0.00000050",
                },
            }],
        }
    }


class _Response:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
            cost=0.000001,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1),
        )
        self._raw = {
            "id": "offline-selector-repair",
            "object": "chat.completion",
            "model": MODEL,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
                "cost": 0.000001,
                "prompt_tokens_details": {"cached_tokens": 1},
            },
        }

    def model_dump(self, mode="json"):
        del mode
        return self._raw


class _SDK:
    max_retries = 0
    base_url = budget.OFFICIAL_BASE

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake SDK received an unexpected model call")
        return _Response(self.responses.pop(0))


class _Element:
    def __init__(self, *, text="", description="", clickable=True):
        self.text = text
        self.hint_text = ""
        self.content_description = description
        self.is_clickable = clickable
        self.is_editable = False
        self.is_long_clickable = clickable


class _AW:
    def __init__(self, env):
        self.env = env

    def get_state(self, wait_to_stabilize=False):
        del wait_to_stabilize
        return SimpleNamespace(ui_elements=self.env.elements_now)

    def execute_action(self, action):
        action_type = getattr(action, "action_type", None)
        if action_type == "open_app":
            self.env.elements_now = [
                _Element(text="Markor", clickable=False),
                _Element(description="File fresh"),
            ]
        elif action_type == "long_press":
            self.env.elements_now = [_Element(description="Delete")]
        elif action_type == "click" and any(
            item.content_description == "Delete" for item in self.env.elements_now
        ):
            self.env.elements_now = [
                _Element(text="Confirm Delete", clickable=False),
                _Element(text="OK"),
            ]
        elif action_type == "click" and any(item.text == "OK" for item in self.env.elements_now):
            self.env.elements_now = [_Element(description="Files", clickable=False)]


class _Env:
    wait_after_action_seconds = 0.0

    def __init__(self):
        self.elements_now = []
        self.aw_env = _AW(self)
        self.closed = 0

    def reset(self, task):
        del task
        self.elements_now = [_Element(text="Home", clickable=False)]

    def observe(self, goal):
        rows = []
        for index, element in enumerate(self.elements_now):
            data = {"index": index, "is_clickable": element.is_clickable}
            if element.text:
                data["text"] = element.text
            if element.content_description:
                data["content_description"] = element.content_description
            rows.append(f"UI element {index}: {json.dumps(data)}")
        return {"url": "fake", "goal_text": goal, "ax_tree_text": "\n".join(rows)}

    def reward(self):
        return 1.0

    def close(self):
        self.closed += 1


def _prepare(monkeypatch, tmp_path: Path):
    _budget_fixture(monkeypatch, tmp_path)
    out = tmp_path / diagnostic.VERSION
    spec = diagnostic.prepare(
        out,
        origin=diagnostic.ORIGIN_OUT,
        authorization_path=diagnostic.AUTHORIZATION_PATH,
    )
    return out, spec


def test_real_prepare_load_freezes_two_fresh_bindings_and_descriptor_rule(tmp_path, monkeypatch):
    out, spec = _prepare(monkeypatch, tmp_path)
    loaded = diagnostic.load_spec(out)
    assert loaded["spec_sha256"] == spec["spec_sha256"]
    assert len(loaded["episodes"]) == 8
    assert sorted({row["seed"] for row in loaded["episodes"]}) == [915303, 915304]
    assert set(row["treatment"] for row in loaded["episodes"]) == set(diagnostic.TREATMENTS)
    assert loaded["inferred_binding_render_rules"] == {
        "note_name": {"prefix": "File ", "suffix": ""}
    }
    assert loaded["inferred_static_prefix"] == "File "
    assert loaded["original_plan"]["sha256"] == diagnostic.ORIGINAL_PLAN_SHA256
    assert loaded["authorization"]["authorization_sha256"] != ""
    assert all(value not in (out / "spec.json").read_text() for value in ("jolly_fox_xXbo", "zEaX_curious_penguin"))
    private = out / loaded["private_bindings"]
    assert private.stat().st_mode & 0o077 == 0
    proof = json.loads((out / loaded["patch_proof"]).read_text())
    assert proof["oracle_used"] is False
    assert proof["evaluator_params_used"] is False
    assert proof["render_rules"] == loaded["inferred_binding_render_rules"]


def test_run_uses_real_exploratory_budget_fake_sdk_and_fake_env(tmp_path, monkeypatch):
    out, _spec = _prepare(monkeypatch, tmp_path)
    sdk = _SDK([
        '{"action_type":"status","goal_status":"complete"}',
        '{"note_name":"fresh"}',
        '{"action_type":"status","goal_status":"complete"}',
        '{"note_name":"fresh"}',
        '{"action_type":"status","goal_status":"complete"}',
        '{"action_type":"status","goal_status":"complete"}',
        '{"note_name":"fresh"}',
    ])
    result = diagnostic.run(
        out,
        max_episodes=4,
        sdk=sdk,
        env_factory=_Env,
        metadata_fetcher=_metadata,
        host_guard=lambda: True,
        sleep=lambda seconds: _reset_pacing(tmp_path / "budget.sqlite3"),
    )
    assert result["batch_status"] == "pilot_limit"
    assert result["counts"]["done"] == 4
    assert len(sdk.calls) == 7
    states = {
        row["treatment"]: json.loads(
            (out / "episodes" / row["id"] / "state.json").read_text()
        )
        for row in diagnostic.load_spec(out)["episodes"]
        if row["treatment"] in diagnostic.TREATMENTS
        and (out / "episodes" / row["id"] / "state.json").is_file()
    }
    assert states["patched_full_fallback"]["result"]["binding_rendering"] == {
        "raw": {"note_name": "fresh"},
        "rendered": {"note_name": "File fresh"},
        "rules": {"note_name": {"prefix": "File ", "suffix": ""}},
    }
    assert "exact description accessibility label" not in json.dumps(sdk.calls[-1]["messages"])
    assert states["reactive"]["source_plan_hash"] is None
    analysis = diagnostic.analyze(out)
    assert set(analysis["treatments"]) == set(diagnostic.TREATMENTS)
    for treatment in diagnostic.TREATMENTS:
        item = analysis["treatments"][treatment]
        assert item["planned"] == 2
        assert item["successes"] == 1
        assert item["complete"] is False
        assert item["cost_bounds"]["receipt_count"] >= 1
        assert "lower_bound_usd" in item["cost_bounds"]
        assert "upper_bound_usd" in item["cost_bounds"]
    assert "learning" in analysis["prior_costs"]
    assert "build" in analysis["prior_costs"]


def test_run_stops_before_ui_when_patched_hook_is_missing(tmp_path, monkeypatch):
    out, _spec = _prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(diagnostic, "_supports_binding_render_rules", lambda: False)
    with pytest.raises(diagnostic.DiagnosticStop, match="binding_render_rules"):
        diagnostic.run(out, max_episodes=1)
