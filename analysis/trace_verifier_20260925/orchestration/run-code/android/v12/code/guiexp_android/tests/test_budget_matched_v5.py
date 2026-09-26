"""Regression tests for metadata failures before a bounded physical retry."""
import json

import pytest

from guiexp_android.budget_client import BudgetStop
from guiexp_android.budget_client_v5 import BudgetClientV5, BudgetLedgerV5, validate_metadata
from guiexp_android.tests.test_budget_matched import MODEL, Response, spec_fixture
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android.tests.test_budget_matched_v3 import SequenceSDK, APITimeoutError
from guiexp_android.tests.test_budget_matched_v4 import metadata
from guiexp_android import matched_run_v5


def guarded(tmp_path, outcomes, fetcher=metadata):
    clock = Clock()
    ledger = BudgetLedgerV5(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SequenceSDK(outcomes)
    client = BudgetClientV5(ledger, {MODEL: validate_metadata(MODEL, metadata(MODEL))},
                            sdk, metadata_fetcher=fetcher, sleep=clock.sleep)
    client.begin_episode("fresh")
    return client, ledger, clock


def ask(client):
    return client.create(model=MODEL, messages=[{"role": "user", "content": "goal"}], temperature=0)


def test_sdk_timeout_retry_does_not_depend_on_a_second_metadata_get(tmp_path):
    gets = []
    def fetch(model):
        gets.append(model)
        if len(gets) > 1:
            raise BudgetStop("Public provider metadata unavailable; no paid call.")
        return metadata(model)
    client, ledger, clock = guarded(tmp_path, [APITimeoutError(), Response("0.01")], fetch)
    ask(client)
    assert len(gets) == 1
    assert len(client.sdk.calls) == 2
    assert clock.now() >= 1060
    with ledger.connect() as db:
        rows = db.execute("SELECT id,state,actual_nano FROM calls ORDER BY created").fetchall()
    assert rows[0] == ("v5/fresh/logical-0001/attempt-1", "uncertain", None)
    assert rows[1] == ("v5/fresh/logical-0001/attempt-2", "settled", 10000000)
    assert ledger.summary()["unresolved_reserved_usd"] == "0.09560064"


def test_transient_free_metadata_failures_retry_without_paid_reservations(tmp_path):
    gets = []
    def fetch(model):
        gets.append(model)
        if len(gets) < 3:
            raise BudgetStop("Public provider metadata unavailable; no paid call.")
        return metadata(model)
    client, ledger, clock = guarded(tmp_path, [Response("0.01")], fetch)
    ask(client)
    assert len(gets) == 3 and len(client.sdk.calls) == 1
    assert ledger.summary()["calls"] == 1
    assert ledger.summary()["unresolved_reserved_usd"] == "0"
    assert clock.now() >= 1010


def test_three_failed_free_gets_have_safe_reason_and_no_paid_send(tmp_path):
    gets = []
    def fetch(model):
        gets.append(model)
        raise BudgetStop("An upstream private error must not escape")
    client, ledger, clock = guarded(tmp_path, [Response()], fetch)
    with pytest.raises(BudgetStop, match="after 3 free GET attempts") as exc:
        ask(client)
    assert "private" not in str(exc.value)
    assert len(gets) == 3 and client.sdk.calls == []
    assert ledger.summary()["calls"] == 0


def test_changed_returned_bounds_fail_closed_without_quote_shopping(tmp_path):
    gets = []
    def fetch(model):
        gets.append(model)
        result = metadata(model)
        result["data"]["endpoints"][0]["pricing"]["prompt"] = "0.00001"
        return result
    client, ledger, clock = guarded(tmp_path, [Response()], fetch)
    with pytest.raises(BudgetStop, match="ceiling"):
        ask(client)
    assert len(gets) == 1 and client.sdk.calls == []
    assert ledger.summary()["calls"] == 0


def test_next_logical_call_validates_metadata_again(tmp_path):
    gets = []
    def fetch(model):
        gets.append(model)
        return metadata(model)
    client, ledger, clock = guarded(tmp_path, [Response(), Response()], fetch)
    ask(client)
    ask(client)
    assert len(gets) == 2 and len(client.sdk.calls) == 2


def test_scheduler_saves_safe_stop_reason(tmp_path):
    spec = spec_fixture(tmp_path)
    spec["episodes"] = spec["episodes"][:2]
    spec["censored_pairs"] = {}
    spec["planned_bindings"] = {}
    for row in spec["episodes"]:
        key = f"{row['model'].replace('/', '_')}/{row['family']}"
        spec["planned_bindings"].setdefault(key, {"bindings": {}})["bindings"][str(row["seed"])] = {"binding_sha256": "test"}
    client, ledger, clock = guarded(tmp_path, [])
    reason = "Public metadata unavailable after 3 free GET attempts; no physical request sent for this logical call."
    def episode(**kwargs):
        raise BudgetStop(reason)
    result = matched_run_v5.execute(tmp_path, spec, ledger, client, None, episode, 1)
    row = spec["episodes"][0]
    state = json.loads((tmp_path / "episodes" / row["id"] / "state.json").read_text())
    assert state["stop_reason"] == reason
    assert state["status"] == "budget_stopped" and result["batch_status"] == "budget_stopped"
    assert ledger.summary()["calls"] == 0
