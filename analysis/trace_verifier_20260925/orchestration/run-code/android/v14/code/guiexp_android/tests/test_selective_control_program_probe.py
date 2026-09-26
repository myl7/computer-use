"""Focused offline checks for the three-request complete-program baseline."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from guiexp_android import selective_budget as base
from guiexp_android import selective_control_program_probe as probe
from guiexp_android import selective_diagnostic_budget as diagnostic
from guiexp_android import selective_explore_budget as explore
from guiexp_android.budget_client import BudgetStop


def _sandbox(tmp_path, monkeypatch):
    source = tmp_path / "training_inputs.json"
    source.write_text(probe.INPUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    ledger = tmp_path / "revision" / "budget.sqlite3"
    lock = tmp_path / "revision" / "run.lock"
    request, auth, config = (tmp_path / name for name in ("authorization_request.json", "authorization.json", "diagnostic_budget.json"))
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
    request.write_text(json.dumps({"additional_usd": "10", "total_occupied_ceiling_usd": "20", "new_tranche_occupied_ceiling_usd": "10", "shared_ledger": str(ledger), "preserve_all_unknown_fees": True}))
    request_body = json.loads(request.read_text()); request_body.update(explore._read_baseline(ledger)); request.write_text(json.dumps(request_body))
    explore.authorize_tranche(request, auth, ledger_path=ledger)
    diagnostic.prepare_diagnostic_config(probe.DIAGNOSTIC_PREFIX, path=config, ledger_path=ledger, authorization_path=auth)
    return SimpleNamespace(source=source, ledger=diagnostic.SelectiveDiagnosticLedger(ledger, config_path=config, host_guard=lambda: True))


class _Client:
    def __init__(self, responses):
        self.responses, self.calls, self.episode = list(responses), [], None
    def begin_episode(self, episode): self.episode = episode
    def create(self, **kwargs): self.calls.append(kwargs); return self.responses.pop(0)


def _response(content, *, cost=0.001):
    usage = {"prompt_tokens": 3, "completion_tokens": 2}
    if cost is not None: usage["cost"] = cost
    return {"id": "fake", "choices": [{"message": {"content": content}}], "usage": usage}


def _prepare(tmp_path, monkeypatch):
    fixture = _sandbox(tmp_path, monkeypatch); out = tmp_path / probe.VERSION
    assert probe.prepare(out)["request_count"] == 3
    return fixture, out


def _candidate(row, source=None):
    spans = probe._slot_spans(row)["spans"]
    slots = {item["slot"]: {"source_span": [item["start"], item["end"]], "source_value": item["value"], "description": "exact source occurrence"} for item in spans}
    source = source or "def program(bindings):\n    observation = yield {\"op\": \"observe\"}\n    if observation.get(\"done\"):\n        return\n    for step in range(1):\n        yield {\"op\": \"action\", \"action\": {\"action_type\": \"wait\"}}"
    return json.dumps({"source": source, "bindings": {"grammar": "mapping[str, scalar]", "slots": slots}, "unsupported_cases": ["A postcondition absent from an observation is unverified."]})


def test_default_paths_and_profile_smoke_without_prepare_or_network():
    assert probe.INPUT_PATH.is_file()
    assert probe.SLOT_PATH.is_file()
    assert probe.DIAGNOSTIC_CONFIG_PATH.is_file()
    assert probe.PROGRAM_SHADOW_PATH.is_file()
    assert probe.exact_command().startswith("../.venv-android/bin/python -m guiexp_android.selective_control_program_probe")
    assert probe._config(probe.DIAGNOSTIC_CONFIG_PATH, probe.SHARED_LEDGER_PATH, probe.AUTHORIZATION_PATH)["diagnostic_prefix"] == probe.DIAGNOSTIC_PREFIX
    assert probe._input(probe.INPUT_PATH)["count"] == 3
    assert all(probe._slot_spans(row)["compatible"] for row in probe._input(probe.INPUT_PATH)["rows"])
    profile = probe._profile()
    assert (profile["model"], profile["provider"], profile["max_tokens"], profile["reasoning"]) == (probe.MODEL, probe.PROVIDER, 8192, {"effort": "low"})
    assert profile["base_profile"] == "serving_4096"


def test_run_uses_full_source_only_trace_once_per_family_and_shared_prefix(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch); rows = json.loads(fixture.source.read_text())["rows"]
    client = _Client([_response(_candidate(row)) for row in rows])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert (result["status"], result["attempted_rows"], len(client.calls)) == ("complete", 3, 3)
    assert all(item["episode"].startswith(probe.DIAGNOSTIC_PREFIX + "/") for item in probe.load_spec(out)["rows"])
    for call, row in zip(client.calls, rows):
        prompt = call["messages"][1]["content"]
        assert row["goal_text"] in prompt and '"elements"' in prompt and "UI element 0:" not in prompt and "ax_tree_text" not in prompt
        assert "2023_07_16_fierce_house" not in prompt and "development_goal" not in prompt and "oracle" not in prompt
        payload = json.loads(prompt.split("\n\nSOURCE INPUT:\n", 1)[1])
        assert [step["action"] for step in payload["source_trace"]] == [step["action"] for step in row["steps"]]
        assert set(payload["source_trace"][0]["pre_observation"]) == {"url", "elements"}
        assert payload["source_trace"][0]["pre_recorded_metadata"]["screenshot_files"] == row["steps"][0]["pre_obs"]["screenshot_files"]
    assert result["generated_code_executed"] is False


def test_default_prompt_has_one_lossless_elements_array_and_keeps_raw_input_unchanged():
    source_bytes = probe.INPUT_PATH.read_bytes()
    row = probe._input(probe.INPUT_PATH)["rows"][0]
    prompt = probe.build_prompt(row)[1]["content"]
    payload = json.loads(prompt.split("\n\nSOURCE INPUT:\n", 1)[1])
    for step, recorded in zip(payload["source_trace"], row["steps"]):
        for side, raw_key in (("pre_observation", "pre_obs"), ("post_observation", "post_obs")):
            parsed = step[side]
            expected_nodes = probe.parse_ax_tree(recorded[raw_key]["ax_tree_text"])["nodes"]
            assert parsed["elements"] == expected_nodes
            assert len(parsed["elements"]) == len({node["index"] for node in parsed["elements"]})
    assert probe.INPUT_PATH.read_bytes() == source_bytes
    assert "ax_tree_text" not in prompt and "UI element 0:" not in prompt
    assert "2023_07_16_fierce_house" not in prompt and "development_goal" not in prompt and "oracle" not in prompt


def test_returned_python_is_only_parsed_and_never_executed(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch); marker = tmp_path / "executed.txt"
    malicious = f'def program(bindings):\n    open({str(marker)!r}, "w").write("ran")\n    yield {{"op": "observe"}}'
    rows = json.loads(fixture.source.read_text())["rows"]
    client = _Client([_response(_candidate(rows[0], malicious)), _response(_candidate(rows[1])), _response(_candidate(rows[2]))])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert len(client.calls) == 3 and not marker.exists()
    assert result["rows"][0]["status"] == "invalid_source"
    saved = json.loads((out / "rows" / rows[0]["family"] / "call_01.json").read_text())
    assert str(marker) in saved["raw_response"]["choices"][0]["message"]["content"] and saved["saved_before_parse"] is True


def test_slot_hints_are_optional_but_occurrence_indices_are_required(tmp_path, monkeypatch):
    _fixture, _out = _prepare(tmp_path, monkeypatch)
    row = json.loads(probe.INPUT_PATH.read_text())["rows"][0]
    candidate = json.loads(_candidate(row))
    candidate["bindings"]["slots"] = {"full_body": {"source_span": [80, 121], "source_value": row["goal_text"][80:121], "description": "explicit body occurrence"}}
    status, parsed, error = probe._parse(json.dumps(candidate), row)
    assert (status, error) == ("returned", None)
    assert parsed["bindings"]["slots"]["full_body"]["source_value"] == "Parents' evening at school this Wednesday"


def test_malformed_raw_result_is_saved_once_without_repair_or_retry(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch); rows = json.loads(fixture.source.read_text())["rows"]
    client = _Client([_response('{"source":'), _response(_candidate(rows[1])), _response(_candidate(rows[2]))])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert len(client.calls) == 3 and result["rows"][0]["status"] == "malformed_json"
    saved = json.loads((out / "rows" / rows[0]["family"] / "call_01.json").read_text())
    assert saved["raw_response"]["choices"][0]["message"]["content"] == '{"source":'
    assert not any("repair" in json.dumps(call).lower() for call in client.calls)


def test_missing_billing_preserves_unknown_reservation_and_stops_remaining_rows(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch); rows = json.loads(fixture.source.read_text())["rows"]
    client = _Client([_response(_candidate(rows[0]), cost=None), _response(_candidate(rows[1])), _response(_candidate(rows[2]))])
    result = probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert result["status"] == "budget_stopped" and result["attempted_rows"] == 1 and len(client.calls) == 1


def test_completed_run_and_existing_claim_refuse_rerun(tmp_path, monkeypatch):
    fixture, out = _prepare(tmp_path, monkeypatch); rows = json.loads(fixture.source.read_text())["rows"]
    client = _Client([_response(_candidate(row)) for row in rows]); probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    with pytest.raises(BudgetStop, match="rerun|state|claim"): probe.run(out, ledger=fixture.ledger, client_factory=lambda *_: client, host_guard=lambda: True)
    assert len(client.calls) == 3
