"""Provider-pin and bound tests for v4. Fake transports only."""
from decimal import Decimal

import pytest

from guiexp_android.budget_client import BudgetStop, atomic_json
from guiexp_android.budget_client_v4 import MODEL_LOCKS_V4, BudgetClientV4, BudgetLedgerV4, validate_metadata
from guiexp_android.tests.test_budget_matched import MODEL, SDK, spec_fixture
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android import matched_analyze


def metadata(model):
    lock = MODEL_LOCKS_V4[model]
    return {"data": {"id": model, "architecture": {"output_modalities": ["text"]},
                     "endpoints": [{"tag": lock["provider"], "status": 0,
                                    "context_length": 1048576, "max_completion_tokens": 131072,
                                    "supported_parameters": ["max_tokens", "temperature"],
                                    "pricing": {"prompt": str(Decimal(lock["prompt_per_m"]) / 1000000),
                                                "completion": str(Decimal(lock["completion_per_m"]) / 1000000),
                                                "input_cache_read": "0.000000001"}}]}}


@pytest.mark.parametrize("model,tag,bound", [
    ("z-ai/glm-5.3-flash", "relace", "0.09560064"),
    ("deepseek/deepseek-v4-flash-vision-exp", "fireworks", "0.23339008"),
])
def test_fixed_provider_bound_and_no_fallback(tmp_path, model, tag, bound):
    clock = Clock()
    lock = validate_metadata(model, metadata(model))
    assert Decimal(lock["reservation_usd"]) == Decimal(bound)
    ledger = BudgetLedgerV4(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SDK()
    client = BudgetClientV4(ledger, {model: lock}, sdk, metadata_fetcher=metadata, sleep=clock.sleep)
    client.begin_episode("fresh-pair/discover")
    client.create(model=model, messages=[{"role": "user", "content": "test"}], temperature=0)
    provider = sdk.calls[0]["extra_body"]["provider"]
    assert provider["only"] == [tag] and provider["allow_fallbacks"] is False
    assert provider["max_price"]["prompt"] == float(MODEL_LOCKS_V4[model]["prompt_per_m"])
    assert provider["max_price"]["completion"] == float(MODEL_LOCKS_V4[model]["completion_per_m"])
    with ledger.connect() as db:
        call_id = db.execute("SELECT id FROM calls").fetchone()[0]
    assert call_id.startswith("v4/")


def test_unpinned_deepinfra_is_rejected():
    data = metadata(MODEL)
    data["data"]["endpoints"][0]["tag"] = "deepinfra/fp4"
    with pytest.raises(BudgetStop):
        validate_metadata(MODEL, data)


def test_provider_price_increase_is_rejected_before_send():
    data = metadata(MODEL)
    data["data"]["endpoints"][0]["pricing"]["prompt"] = "0.0000001"
    with pytest.raises(BudgetStop, match="ceiling"):
        validate_metadata(MODEL, data)


@pytest.mark.parametrize("key,value", [("image", "0.001"), ("request", "0.001"), ("input_cache_write", "0.001"), ("overrides", {"tier": "unknown"})])
def test_extra_or_unbounded_fee_types_still_rejected(key, value):
    data = metadata(MODEL)
    data["data"]["endpoints"][0]["pricing"][key] = value
    with pytest.raises(BudgetStop):
        validate_metadata(MODEL, data)


def test_both_arms_use_same_frozen_provider(tmp_path):
    clock = Clock()
    lock = validate_metadata(MODEL, metadata(MODEL))
    ledger = BudgetLedgerV4(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SDK()
    client = BudgetClientV4(ledger, {MODEL: lock}, sdk, metadata_fetcher=metadata, sleep=clock.sleep)
    for arm in ("discover", "doc"):
        client.begin_episode(f"pair/{arm}")
        client.create(model=MODEL, messages=[{"role": "user", "content": arm}], temperature=0)
    assert sdk.calls[0]["extra_body"]["provider"] == sdk.calls[1]["extra_body"]["provider"]


def test_analysis_prefers_v4_spec_over_older_versions(tmp_path):
    spec = spec_fixture(tmp_path)
    atomic_json(tmp_path / "spec.v3.json", dict(spec, spec_sha256="older-spec"))
    atomic_json(tmp_path / "spec.v4.json", dict(spec, spec_sha256="v4-spec"))
    BudgetLedgerV4(tmp_path / "budget.sqlite3")
    result = matched_analyze.analyze(tmp_path)
    assert result["spec_sha256"] == "v4-spec"
