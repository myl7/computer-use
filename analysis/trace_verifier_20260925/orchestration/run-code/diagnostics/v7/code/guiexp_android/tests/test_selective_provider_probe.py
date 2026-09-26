"""Offline tests for the one-shot provider probe."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_explore_budget as budget
from guiexp_android import selective_provider_probe as probe
from guiexp_android.budget_client import BudgetStop, OFFICIAL_BASE


def _fixture(monkeypatch, tmp_path):
    ledger = (tmp_path / "ledger.sqlite3").resolve()
    request = (tmp_path / "request.json").resolve()
    auth = (tmp_path / "authorization.json").resolve()
    lock = (tmp_path / "run.lock").resolve()
    for name, value in {
        "SHARED_LEDGER_PATH": ledger,
        "SHARED_RUN_LOCK_PATH": lock,
        "AUTHORIZATION_PATH": auth,
        "AUTHORIZATION_REQUEST_PATH": request,
    }.items():
        monkeypatch.setattr(budget, name, value)
    budget.base.SelectiveLedger(ledger, host_guard=lambda: True)
    request.write_text(json.dumps({
        "additional_usd": "10",
        "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10",
        "shared_ledger": str(ledger),
        "preserve_all_unknown_fees": True,
    }))
    budget.authorize_tranche(request, auth, ledger_path=ledger)
    ledger_obj = budget.SelectiveExploreLedger(
        ledger, authorization_path=auth, host_guard=lambda: True
    )
    return SimpleNamespace(ledger=ledger_obj, ledger_path=ledger, auth=auth)


def _metadata(model=probe.MODEL):
    profile = budget._PROVIDER_PROFILES["deepinfra_fp4"]
    return {"data": {
        "id": model,
        "architecture": {"output_modalities": ["text"]},
        "endpoints": [{
            "tag": profile["provider"], "status": 0,
            "context_length": profile["context_length"],
            "max_completion_tokens": profile["provider_max_completion_tokens"],
            "supported_parameters": ["max_tokens", "temperature"],
            "pricing": {
                "prompt": "0.00000015", "completion": "0.00000050",
            },
        }],
    }}


class _Response:
    def __init__(self):
        self.raw = {
            "id": "gen-provider-probe",
            "choices": [{"message": {"role": "assistant", "content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "cost": 0.001},
        }

    def model_dump(self, mode="json"):
        return self.raw


class _SDK:
    max_retries = 0
    base_url = OFFICIAL_BASE

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()


def test_prepare_is_free_and_freezes_selectable_profile_without_client(tmp_path, monkeypatch):
    fixture = _fixture(monkeypatch, tmp_path)
    called = []
    monkeypatch.setattr(budget, "make_client", lambda *args, **kwargs: called.append(1))
    out = tmp_path / "probe"
    manifest = probe.prepare(out, "deepinfra_fp4")
    loaded = probe.load(out)
    assert called == []
    assert manifest == loaded
    assert manifest["prompt"] == "Return exactly one JSON object with ok:true."
    assert manifest["request_profile"] == "serving_4096"
    assert manifest["provider_profile"] == "deepinfra_fp4"
    assert manifest["provider"] == "deepinfra/fp4"
    assert manifest["phase_manifest"]["phase_manifest_sha256"]
    assert "z_ai_fp8" in probe.PROVIDER_PROFILE_CHOICES
    assert probe.PROVIDER_REGISTRY["z_ai_fp8"]["provider"] == "z-ai/fp8"
    with fixture.ledger.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_run_uses_real_explore_budget_client_once_and_saves_raw_reference(tmp_path, monkeypatch):
    fixture = _fixture(monkeypatch, tmp_path)
    out = tmp_path / "probe"
    probe.prepare(out, "deepinfra_fp4")
    sdk = _SDK()
    result = probe.run(
        out,
        ledger=fixture.ledger,
        sdk=sdk,
        metadata_fetcher=_metadata,
        host_guard=lambda: True,
    )
    assert result["status"] == "returned"
    assert len(sdk.calls) == 1
    assert result["raw_result_path"] == "raw_result.json"
    assert json.loads((out / result["raw_result_path"]).read_text())["id"] == "gen-provider-probe"
    state = json.loads((out / "state.json").read_text())
    assert state["status"] == "returned"
    assert state["result_path"] == "result.json"
    identity = json.loads((out / "run_identity.json").read_text())
    assert identity["status"] == "ended"
    with fixture.ledger.connect() as database:
        rows = database.execute("SELECT episode,state FROM calls").fetchall()
    assert rows == [("selective_20260915/provider_probe/deepinfra_fp4", "settled")]


def test_run_rejects_prior_state_or_receipt_without_http_or_overwrite(tmp_path, monkeypatch):
    fixture = _fixture(monkeypatch, tmp_path)
    out = tmp_path / "probe"
    probe.prepare(out, "deepinfra_fp4")
    state_path = out / "state.json"
    state_path.write_text("prior", encoding="utf-8")
    with pytest.raises(BudgetStop, match="prior state"):
        probe.run(out, ledger=fixture.ledger, sdk=_SDK(), metadata_fetcher=_metadata, host_guard=lambda: True)
    assert state_path.read_text(encoding="utf-8") == "prior"

    state_path.unlink()
    episode = "selective_20260915/provider_probe/deepinfra_fp4"
    fixture.ledger.reserve("probe-prior", episode, probe.MODEL, "sha", "0.001")
    sdk = _SDK()
    with pytest.raises(BudgetStop, match="receipt"):
        probe.run(out, ledger=fixture.ledger, sdk=sdk, metadata_fetcher=_metadata, host_guard=lambda: True)
    assert sdk.calls == []

