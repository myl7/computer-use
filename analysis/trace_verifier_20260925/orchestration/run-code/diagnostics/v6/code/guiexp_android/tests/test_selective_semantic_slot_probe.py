"""Focused offline tests for the three-request semantic-slot probe."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from guiexp_android import selective_budget as base
from guiexp_android import selective_diagnostic_budget as diagnostic
from guiexp_android import selective_explore_budget as explore
from guiexp_android import selective_semantic_slot_probe as probe
from guiexp_android.budget_client import BudgetStop


def _sandbox(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "inputs.json"
    source.write_text((probe.INPUT_PATH).read_text(encoding="utf-8"), encoding="utf-8")
    ledger = tmp_path / "revision" / "budget.sqlite3"
    lock = tmp_path / "revision" / "run.lock"
    request = tmp_path / "authorization_request.json"
    auth = tmp_path / "authorization.json"
    config = tmp_path / "diagnostic_budget.json"
    lock.parent.mkdir(parents=True)
    for module in (explore, diagnostic):
        monkeypatch.setattr(module, "SHARED_LEDGER_PATH", ledger.resolve())
        monkeypatch.setattr(module, "SHARED_RUN_LOCK_PATH", lock.resolve())
    monkeypatch.setattr(explore, "AUTHORIZATION_PATH", auth.resolve())
    monkeypatch.setattr(explore, "AUTHORIZATION_REQUEST_PATH", request.resolve())
    monkeypatch.setattr(diagnostic, "AUTHORIZATION_PATH", auth.resolve())
    monkeypatch.setattr(diagnostic, "DEFAULT_CONFIG_PATH", config.resolve())
    monkeypatch.setattr(probe, "INPUT_PATH", source.resolve())
    monkeypatch.setattr(probe, "INPUT_SHA256", hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(probe, "DIAGNOSTIC_CONFIG_PATH", config.resolve())
    monkeypatch.setattr(probe, "SHARED_LEDGER_PATH", ledger.resolve())
    monkeypatch.setattr(probe, "SHARED_RUN_LOCK_PATH", lock.resolve())
    monkeypatch.setattr(probe, "AUTHORIZATION_PATH", auth.resolve())
    base.SelectiveLedger(ledger, host_guard=lambda: True)
    request.write_text(json.dumps({
        "additional_usd": "10", "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10", "shared_ledger": str(ledger),
        "preserve_all_unknown_fees": True,
    }))
    request_body = json.loads(request.read_text())
    request_body.update(explore._read_baseline(ledger))
    request.write_text(json.dumps(request_body))
    explore.authorize_tranche(request, auth, ledger_path=ledger)
    diagnostic.prepare_diagnostic_config(
        probe.DIAGNOSTIC_PREFIX, path=config, ledger_path=ledger, authorization_path=auth
    )
    ledger_obj = diagnostic.SelectiveDiagnosticLedger(
        ledger, config_path=config, host_guard=lambda: True
    )
    return SimpleNamespace(source=source, ledger=ledger_obj)


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.episode = None

    def begin_episode(self, episode):
        self.episode = episode

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(content, *, cost=0.001):
    usage = {"prompt_tokens": 3, "completion_tokens": 2}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "fake", "choices": [{"message": {"content": content}}], "usage": usage}


def _prepare(tmp_path, monkeypatch):
    fixture = _sandbox(tmp_path, monkeypatch)
    out = tmp_path / "semantic_slots_v1"
    spec = probe.prepare(out)
    assert spec["request_count"] == 3
    return fixture, out


def test_run_sends_exactly_three_source_only_requests_and_keeps_slot_list(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch)
    source = json.loads(fixture.source.read_text())
    values = [["backup_cool_bear.txt"], ["garden_blooms.jpg"], ["jolly_fox_xXbo"]]
    client = _Client([_response(json.dumps({"source_values": value})) for value in values])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert result["status"] == "complete"
    assert result["attempted_rows"] == 3
    assert result["physical_receipts"] == 0
    assert [row["source_values"] for row in result["rows"]] == values
    assert len(client.calls) == 3
    heldout = "2023_07_16_fierce_house"
    for call, row in zip(client.calls, source["rows"]):
        prompt = json.dumps(call["messages"])
        assert row["source_goal"] in prompt
        assert heldout not in prompt
        assert "complete program" in prompt or "JSON patch" in prompt


def test_malformed_json_is_saved_once_and_does_not_trigger_repair(tmp_path, monkeypatch):
    _fixture, out = _prepare(tmp_path, monkeypatch)
    client = _Client([_response("{malformed"), _response('{"source_values":["garden_blooms.jpg"]}'), _response('{"source_values":["jolly_fox_xXbo"]}')])
    result = probe.run(out, ledger=_fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert result["attempted_rows"] == 3
    assert result["rows"][0]["status"] == "malformed_json"
    assert len(client.calls) == 3
    first_call = json.loads((out / "rows" / "MarkorCreateNote" / "call_01.json").read_text())
    assert first_call["raw_response"]["choices"][0]["message"]["content"] == "{malformed"


@pytest.mark.parametrize("content,cost,status", [("", 0.001, "empty_response"), ('{"source_values":[]}', None, "budget_stopped")])
def test_empty_or_missing_billing_stops_remaining_rows(tmp_path, monkeypatch, content, cost, status):
    fixture, out = _prepare(tmp_path, monkeypatch)
    client = _Client([_response(content, cost=cost), _response('{"source_values":["garden_blooms.jpg"]}'), _response('{"source_values":["jolly_fox_xXbo"]}')])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert result["status"] == status
    assert result["attempted_rows"] == 1
    assert result["physical_receipts"] == 0
    assert len(client.calls) == 1
    assert (out / "rows" / "MarkorCreateNote" / "call_01.json").is_file()


def test_source_drift_and_completed_rerun_are_refused(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch)
    source = json.loads(fixture.source.read_text())
    source["rows"][0]["source_goal"] += " "
    fixture.source.write_text(json.dumps(source))
    client = _Client([])
    with pytest.raises(probe.ProbeStop, match="drift|hash"):
        probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert client.calls == []

    fixture, out = _prepare(tmp_path / "second", monkeypatch)
    client = _Client([_response('{"source_values":["backup_cool_bear.txt"]}'), _response('{"source_values":["garden_blooms.jpg"]}'), _response('{"source_values":["jolly_fox_xXbo"]}')])
    probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    with pytest.raises(BudgetStop, match="rerun|state|claim"):
        probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert len(client.calls) == 3


def test_prepare_cli_prints_prepared_summary(monkeypatch, tmp_path, capsys):
    spec = {"schema": probe.SCHEMA, "version": probe.VERSION, "request_count": 3}
    monkeypatch.setattr(probe, "prepare", lambda _out: spec)
    assert probe.main(["--prepare", "--out", str(tmp_path / "semantic_slots_v1")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "prepared"
    assert output["attempted_rows"] == 0


def test_run_cli_is_nonzero_for_stopped_batch(monkeypatch, tmp_path, capsys):
    result = {"status": "budget_stopped", "attempted_rows": 1, "physical_receipts": 0}
    monkeypatch.setattr(probe, "run", lambda _out, env_file=None: result)
    assert probe.main(["--run", "--out", str(tmp_path / "semantic_slots_v1")]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "budget_stopped"
    assert output["attempted_rows"] == 1


def test_existing_run_claim_refuses_run_without_receipts(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch)
    spec = probe.load_spec(out)
    probe._claim_run(out, spec)
    client = _Client([_response('{"source_values":["backup_cool_bear.txt"]}')])
    with pytest.raises(BudgetStop, match="claim|rerun"):
        probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert client.calls == []
