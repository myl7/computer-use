"""No network, model calls, or emulator access; fake transports only."""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from guiexp_android.budget_client import (
    BudgetClient, BudgetLedger, BudgetStop, MODEL_LOCKS, OFFICIAL_BASE,
    atomic_json, credentials, validate_metadata,
)
from guiexp_android import matched_run

MODEL = "z-ai/glm-5.3-flash"


def metadata(model=MODEL):
    lock = MODEL_LOCKS[model]
    return {"data": {"id": model, "architecture": {"output_modalities": ["text"]},
                     "endpoints": [{"tag": lock["provider"], "status": 0,
                                    "context_length": 1048576, "max_completion_tokens": 131072,
                                    "supported_parameters": ["max_tokens", "temperature"],
                                    "pricing": {"prompt": str(Decimal(lock["prompt_per_m"]) / 1000000),
                                                "completion": str(Decimal(lock["completion_per_m"]) / 1000000)}}]}}


class Response:
    def __init__(self, cost="0.01"):
        self.cost = cost

    def model_dump(self, mode):
        return {"id": "gen-test", "usage": {"cost": self.cost},
                "choices": [{"message": {"content": "action"}}]}


class SDK:
    max_retries = 0
    base_url = OFFICIAL_BASE

    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or Response()
        self.error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def client_for(tmp_path, sdk=None):
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    lock = validate_metadata(MODEL, metadata())
    client = BudgetClient(ledger, {MODEL: lock}, sdk or SDK(), metadata_fetcher=metadata)
    client.begin_episode("test/episode1")
    return client, ledger


def ask(client, **extra):
    return client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "hello"}], temperature=0, **extra)


def test_price_bound_includes_full_image_context_and_output(tmp_path):
    client, ledger = client_for(tmp_path)
    ask(client)
    p = client.sdk.calls[0]
    assert p["max_tokens"] == 4096 and p["stream"] is False
    policy = p["extra_body"]["provider"]
    assert policy["only"] == ["deepinfra/fp4"]
    assert policy["allow_fallbacks"] is False and policy["require_parameters"] is True
    assert policy["max_price"] == {"prompt": 0.15, "completion": 0.5, "request": 0}
    with ledger.connect() as db:
        reserved, actual = db.execute("SELECT reserved_nano,actual_nano FROM calls").fetchone()
    assert reserved == 159334400  # 1,048,576 prompt tokens + 4,096 output tokens
    assert actual == 10000000


def test_shared_ledger_rejects_before_request_even_with_new_client(tmp_path):
    first, ledger = client_for(tmp_path)
    ledger.reserve("earlier", "earlier", MODEL, "hash", "9.9")
    ledger.settle("earlier", Response("9.9"))
    second, _ = client_for(tmp_path)
    with pytest.raises(BudgetStop, match="ceiling"):
        ask(second)
    assert second.sdk.calls == []
    assert ledger.summary()["actual_usd"] == "9.9"


def test_unknown_transport_bill_keeps_reserve_and_no_retries(tmp_path):
    client, ledger = client_for(tmp_path, SDK(error=TimeoutError("secret must not print")))
    with pytest.raises(BudgetStop) as exc:
        ask(client)
    assert "secret" not in str(exc.value)
    assert len(client.sdk.calls) == 1
    assert ledger.summary()["blocked"] is True
    new, _ = client_for(tmp_path)
    with pytest.raises(BudgetStop):
        ask(new)
    assert len(new.sdk.calls) == 0
    assert Decimal(ledger.summary()["unresolved_reserved_usd"]) > 0


@pytest.mark.parametrize("cost", [None, -0.1, "NaN", "Infinity"])
def test_missing_or_invalid_bill_stops_and_preserves_response(tmp_path, cost):
    client, ledger = client_for(tmp_path, SDK(response=Response(cost)))
    with pytest.raises(BudgetStop):
        ask(client)
    assert ledger.summary()["blocked"]
    with ledger.connect() as db:
        assert db.execute("SELECT response_json FROM calls").fetchone()[0]


def test_paid_invalid_reply_still_counts_before_caller_parse(tmp_path):
    client, ledger = client_for(tmp_path)
    response = ask(client)
    # A downstream parser rejecting this response cannot erase its bill.
    assert response.model_dump(mode="json")["choices"][0]["message"]["content"] == "action"
    assert ledger.summary()["actual_usd"] == "0.01"


def test_same_call_id_never_sent_twice(tmp_path):
    client, ledger = client_for(tmp_path)
    ask(client)
    client.begin_episode("test/episode1")
    with pytest.raises(BudgetStop, match="already exists"):
        ask(client)
    assert len(client.sdk.calls) == 1


def test_unsettled_crash_blocks_new_process(tmp_path):
    client, ledger = client_for(tmp_path)
    ledger.reserve("crash", "ep", MODEL, "sha", "0.2")
    second, _ = client_for(tmp_path)
    with pytest.raises(BudgetStop):
        ask(second)
    assert second.sdk.calls == []


@pytest.mark.parametrize("key,value", [("image", "0.01"), ("request", "0.01"), ("overrides", []), ("input_cache_write", "0.0001")])
def test_unknown_surcharges_rejected(key, value):
    data = metadata()
    data["data"]["endpoints"][0]["pricing"][key] = value
    with pytest.raises(BudgetStop):
        validate_metadata(MODEL, data)


def test_context_drift_rejected_before_paid_call(tmp_path):
    client, ledger = client_for(tmp_path)
    data = metadata()
    data["data"]["endpoints"][0]["context_length"] *= 2
    client.metadata_fetcher = lambda model: data
    with pytest.raises(BudgetStop, match="changed"):
        ask(client)
    assert client.sdk.calls == [] and ledger.summary()["calls"] == 0


def test_unbounded_options_and_sdk_retry_rejected(tmp_path):
    client, ledger = client_for(tmp_path)
    with pytest.raises(BudgetStop):
        ask(client, extra_body={"plugins": [{"id": "web"}]})
    bad = SDK()
    bad.max_retries = 2
    with pytest.raises(BudgetStop, match="retries"):
        BudgetClient(ledger, {}, bad)
    assert client.sdk.calls == []


def test_credentials_no_shell_evaluation_and_wrong_origin(tmp_path):
    path = tmp_path / "test.env"
    path.write_text("OPENROUTER_API_KEY='$(touch never)'\nOPENROUTER_BASE_URL='https://openrouter.ai/api/v1'\n")
    assert credentials(path) == "$(touch never)"
    assert not (tmp_path / "never").exists()
    path.write_text("OPENROUTER_API_KEY=test\nOPENROUTER_BASE_URL=https://elsewhere.example\n")
    with pytest.raises(BudgetStop, match="official"):
        credentials(path)


def spec_fixture(tmp_path):
    doc = tmp_path / "doc.txt"
    doc.write_text("Frozen document")
    docs = {f"{m.replace('/', '_')}/{f}": {"frozen": "doc.txt"} for m in matched_run.MODELS for f in matched_run.FAMILIES}
    rows = matched_run.rows_for(docs)
    return {"episodes": rows, "spec_sha256": "test", "obs_mode": "screenshot+ax",
            "pair_allowances_usd": {k: "0.1" for k in docs},
            "model_locks": {m: validate_metadata(m, metadata(m)) for m in matched_run.MODELS}}


def test_140_episode_plan_is_matched_and_counterbalanced(tmp_path):
    spec = spec_fixture(tmp_path)
    rows = spec["episodes"]
    assert len(rows) == 140 and len({r["id"] for r in rows}) == 140
    for a, b in zip(rows[::2], rows[1::2]):
        assert (a["model"], a["family"], a["seed"]) == (b["model"], b["family"], b["seed"])
        assert {a["condition"], b["condition"]} == {"discover", "doc"}
        assert a["max_steps"] == b["max_steps"]
    assert {r["seed"] for r in rows} == set(range(913101, 913106))
    assert {r["condition"] for r in rows[::2]} == {"discover", "doc"}


def test_completed_episodes_not_repeated_on_resume(tmp_path):
    spec = spec_fixture(tmp_path)
    client, ledger = client_for(tmp_path)
    called = []
    def episode(**kwargs):
        called.append((kwargs["family"], kwargs["seed"], kwargs["condition"]))
        return {"success": False, "total_cost_usd": 0}
    first = matched_run.execute(tmp_path, spec, ledger, client, None, episode, 2)
    assert first["complete_pairs"] == 1 and len(called) == 2
    second = matched_run.execute(tmp_path, spec, ledger, client, None, episode, 2)
    assert second["complete_pairs"] == 2 and len(called) == 4
    assert len(set(called)) == 2  # same family/seed under the second model


def test_interrupted_paid_episode_is_not_retried(tmp_path):
    spec = spec_fixture(tmp_path)
    row = spec["episodes"][0]
    atomic_json(tmp_path / "episodes" / row["id"] / "state.json", {"status": "running"})
    client, ledger = client_for(tmp_path)
    called = []
    def episode(**kwargs):
        called.append(kwargs["model"])
        return {"success": True}
    result = matched_run.execute(tmp_path, spec, ledger, client, None, episode, 2)
    assert result["counts"]["interrupted"] == 1
    assert result["counts"]["skipped_incomplete_pair"] == 1
    assert len(called) == 2 and result["complete_pairs"] == 1


def test_budget_low_starts_neither_half_of_pair(tmp_path):
    spec = spec_fixture(tmp_path)
    client, ledger = client_for(tmp_path)
    ledger.reserve("earlier", "earlier", MODEL, "sha", "9.9")
    ledger.settle("earlier", Response("9.9"))
    called = []
    result = matched_run.execute(tmp_path, spec, ledger, client, None, lambda **k: called.append(k), 2)
    assert called == [] and result["counts"] == {"pending": 140}
