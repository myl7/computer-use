"""HTTP-success provider error envelopes must never reach the UI as answers."""
import json
from types import SimpleNamespace

import pytest

from guiexp_android.budget_client import BudgetStop
from guiexp_android.budget_client_v7 import BudgetClientV7, BudgetLedgerV7, validate_metadata
from guiexp_android.tests.test_budget_matched import MODEL, Response
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android.tests.test_budget_matched_v3 import SequenceSDK
from guiexp_android.tests.test_budget_matched_v4 import metadata


class Envelope:
    def __init__(self, code=502, generation="gen-envelope", message="Retry after 2s.", choices=None):
        self.raw = {"id": generation, "usage": None, "choices": choices or [],
                    "error": {"code": code, "message": message,
                              "metadata": {"error_type": "provider_unavailable"}}}
    def model_dump(self, mode):
        return self.raw


def guarded(tmp_path, outcomes):
    clock = Clock()
    ledger = BudgetLedgerV7(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SequenceSDK(outcomes)
    gets = []
    def fetch(model):
        gets.append(model)
        return metadata(model)
    client = BudgetClientV7(ledger, {MODEL: validate_metadata(MODEL, metadata(MODEL))},
                            sdk, metadata_fetcher=fetch, sleep=clock.sleep)
    client.begin_episode("fresh")
    return client, ledger, clock, gets


def ask(client):
    return client.create(model=MODEL, messages=[{"role": "user", "content": "goal"}], temperature=0)


def test_observed_502_envelope_retries_before_billing_and_keeps_raw_response(tmp_path):
    bad = Envelope(message="Upstream error: Predicted prefill wait 22.9s exceeds 8s. Retry after 2s.")
    good = Response("0.01")
    client, ledger, clock, gets = guarded(tmp_path, [bad, good])
    assert ask(client) is good
    assert len(client.sdk.calls) == 2 and len(gets) == 1 and clock.now() >= 1060
    with ledger.connect() as db:
        rows = db.execute("SELECT id,state,actual_nano,response_json FROM calls ORDER BY created").fetchall()
        safe = json.loads(db.execute("SELECT safe_json FROM error_metadata_v2").fetchone()[0])
    assert rows[0][0] == "v7/fresh/logical-0001/attempt-1"
    assert rows[0][1:3] == ("uncertain", None)
    assert json.loads(rows[0][3]) == bad.raw
    assert rows[1][1:3] == ("settled", 10000000)
    assert safe["status_code"] == 502 and safe["generation_id"] == "gen-envelope"
    assert safe["retry_after"] == "2.0"
    assert ledger.summary()["unresolved_reserved_usd"] == "0.09560064"


def test_longer_provider_retry_hint_is_honored(tmp_path):
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(message="Retry after 120 seconds."), Response()])
    ask(client)
    assert clock.now() >= 1120


def test_two_error_envelopes_exhaust_attempts_no_ui_answer(tmp_path):
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(generation="gen-one"), Envelope(generation="gen-two"), Response()])
    with pytest.raises(BudgetStop, match="no UI answer delivered"):
        ask(client)
    assert len(client.sdk.calls) == 2
    assert ledger.summary()["unresolved_reserved_usd"] == "0.19120128"


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504, 524, 529])
def test_explicit_transient_provider_status_can_use_one_retry(tmp_path, code):
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(code=code), Response()])
    ask(client)
    assert len(client.sdk.calls) == 2


@pytest.mark.parametrize("code", [400, 401, 402, 403, 404, 413, 415, 422])
def test_explicit_permanent_status_wins_over_generic_metadata(tmp_path, code):
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(code=code), Response()])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(client.sdk.calls) == 1


def test_error_envelope_with_partial_text_is_not_a_model_answer(tmp_path):
    bad = Envelope(choices=[{"message": {"content": "Partial action that must not execute"}, "finish_reason": "error"}])
    good = Response()
    client, ledger, clock, gets = guarded(tmp_path, [bad, good])
    assert ask(client) is good


def test_empty_answer_without_explicit_transient_error_stops(tmp_path):
    bad = Envelope()
    bad.raw = {"id": "gen-empty", "choices": [], "usage": {"cost": 0}}
    client, ledger, clock, gets = guarded(tmp_path, [bad, Response()])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(client.sdk.calls) == 1


def test_usable_answer_missing_bill_keeps_id_and_never_model_retries(tmp_path):
    good = Envelope()
    good.raw = {"id": "gen-valid-missing-bill", "usage": None,
                "choices": [{"message": {"content": "action: complete"}, "finish_reason": "stop"}]}
    client, ledger, clock, gets = guarded(tmp_path, [good, Response()])
    with pytest.raises(BudgetStop, match="no model retry"):
        ask(client)
    assert len(client.sdk.calls) == 1 and len(gets) == 1
    with ledger.connect() as db:
        actual, raw = db.execute("SELECT actual_nano,response_json FROM calls").fetchone()
        safe = json.loads(db.execute("SELECT safe_json FROM error_metadata_v2").fetchone()[0])
    assert actual is None and json.loads(raw)["usage"] is None
    assert safe["generation_id"] == "gen-valid-missing-bill"
    assert ledger.summary()["unresolved_reserved_usd"] == "0.09560064"


def test_error_retry_still_obeys_cumulative_budget(tmp_path):
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(), Response()])
    ledger.reserve("old", "old", MODEL, "hash", "9.85")
    ledger.settle("old", Response("9.85"))
    with pytest.raises(BudgetStop, match="ceiling"):
        ask(client)
    assert len(client.sdk.calls) == 1
    assert ledger.summary()["budget_occupied_usd"] == "9.94560064"


def test_error_envelope_does_not_advance_agent_history(tmp_path):
    from guiexp_android.agent import AndroidAgent
    good = Response("0.01")
    good.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=.01,
                                 prompt_tokens_details=None, model_extra={})
    good.choices = [SimpleNamespace(message=SimpleNamespace(content='action: {"action_type":"status","goal_status":"complete"}'))]
    client, ledger, clock, gets = guarded(tmp_path, [Envelope(), good])
    agent = AndroidAgent(MODEL, client=client)
    reply, usage = agent.act("Goal", {"screenshot_b64": None, "ax_tree_text": "Screen", "url": "test"})
    assert len(agent.history) == 2
    assert agent.history[-1]["content"] == reply
    assert len(client.sdk.calls) == 2
