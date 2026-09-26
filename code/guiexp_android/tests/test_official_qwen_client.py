from types import SimpleNamespace

import pytest

from guiexp_android.official_qwen_client import (OfficialQwenClient,
                                                 load_official_qwen_config,
                                                 official_usage_record,
                                                 recover_shared_budget_ledger)


class Capture:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7,
                                model_extra={"cost_cny": 0.03})
        return SimpleNamespace(model="qwen3.8-flash", usage=usage)


def test_maps_model_and_preserves_text_image_payload_and_defaults():
    capture = Capture()
    client = OfficialQwenClient(underlying=capture)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "inspect"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
    ]}]
    response = client.chat.completions.create(
        model="qwen/qwen3.8-flash", messages=messages, temperature=0.0)
    assert capture.kwargs == {
        "model": "qwen3.8-flash", "messages": messages, "temperature": 0.0}
    assert "max_tokens" not in capture.kwargs
    assert official_usage_record(response)["cost_cny"] == "0.03"


def test_rejects_model_substitution():
    client = OfficialQwenClient(underlying=Capture())
    with pytest.raises(ValueError):
        client.chat.completions.create(model="other", messages=[], temperature=0.0)


def test_loads_exact_private_contract_without_key_provenance(tmp_path):
    path = tmp_path / "official.env"
    path.write_text("\n".join([
        "QWEN_OFFICIAL_API_KEY=secret-test-value",
        "QWEN_OFFICIAL_BASE_URL=https://maas.qianwenaiapi.com/compatible-mode/v1",
        "QWEN_OFFICIAL_WIRE_MODEL=qwen3.8-flash",
        "QWEN_OFFICIAL_ACCOUNT_BUDGET_CNY=100",
    ]))
    config = load_official_qwen_config(path)
    assert config["account_budget_cny"] == "100"
    client = OfficialQwenClient(path, underlying=Capture())
    assert "api_key" not in client.route_provenance


def test_run_budget_persists_cny_and_blocks_when_exhausted(tmp_path):
    capture = Capture()
    ledger = tmp_path / "budget.json"
    client = OfficialQwenClient(
        underlying=capture, run_budget_cny="5.02", budget_ledger_path=ledger)
    client.chat.completions.create(
        model="qwen/qwen3.8-flash", messages=[], temperature=0.0)
    saved = __import__("json").loads(ledger.read_text())
    assert saved["spent_cny"] == "0.03" and saved["blocked"] is True
    assert saved["reserved_cny"] == "0" and saved["calls_completed"] == 1
    with pytest.raises(RuntimeError, match="budget"):
        client.chat.completions.create(
            model="qwen/qwen3.8-flash", messages=[], temperature=0.0)


def test_missing_provider_cny_uses_frozen_receipt_rates(tmp_path):
    class NoCost(Capture):
        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(model="qwen3.8-flash",
                                   usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1,
                                                         model_extra={}))
    ledger = tmp_path / "budget.json"
    client = OfficialQwenClient(
        underlying=NoCost(), run_budget_cny="45", budget_ledger_path=ledger)
    client.chat.completions.create(
        model="qwen/qwen3.8-flash", messages=[], temperature=0.0)
    saved = __import__("json").loads(ledger.read_text())
    assert saved["spent_cny"] == "0.0000035"
    assert saved["blocked"] is False


def test_two_clients_share_one_atomic_android_allocation(tmp_path):
    ledger = tmp_path / "shared-android-budget.json"
    first = OfficialQwenClient(
        underlying=Capture(), run_budget_cny="45", budget_ledger_path=ledger)
    second = OfficialQwenClient(
        underlying=Capture(), run_budget_cny="45", budget_ledger_path=ledger)
    for client in (first, second):
        client.chat.completions.create(
            model="qwen/qwen3.8-flash", messages=[], temperature=0.0)
    saved = __import__("json").loads(ledger.read_text())
    assert saved["calls_started"] == saved["calls_completed"] == 2
    assert saved["reserved_cny"] == "0"
    assert saved["spent_cny"] == "0.06"


def test_operator_recovery_retains_unresolved_reservation(tmp_path):
    ledger = tmp_path / "shared.json"
    ledger.write_text(__import__("json").dumps({
        "cap_cny": "75", "spent_cny": "0.001", "reserved_cny": "5",
        "calls_started": 4, "calls_completed": 3, "unresolved_calls": 1,
        "blocked": True, "block_reason": "call_failed_with_billing_unresolved",
    }))
    recovered = recover_shared_budget_ledger(ledger, "75", True)
    assert recovered["blocked"] is False
    assert recovered["reserved_cny"] == "5"
    assert recovered["unresolved_calls"] == 1


def test_operator_recovery_refuses_second_unresolved_call(tmp_path):
    ledger = tmp_path / "shared.json"
    ledger.write_text(__import__("json").dumps({
        "cap_cny": "75", "spent_cny": "0.001", "reserved_cny": "10",
        "unresolved_calls": 2, "blocked": True,
        "block_reason": "call_failed_with_billing_unresolved",
    }))
    with pytest.raises(ValueError, match="exhausted"):
        recover_shared_budget_ledger(ledger, "75", True)
