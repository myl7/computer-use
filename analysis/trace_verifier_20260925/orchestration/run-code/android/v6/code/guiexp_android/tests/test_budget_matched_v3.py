"""No-paid tests for bounded physical retries and interrupted-run recovery."""
import json
from types import SimpleNamespace

import pytest

from guiexp_android.budget_client import BudgetStop, validate_metadata, atomic_json
from guiexp_android.budget_client_v3 import BudgetClientV3, BudgetLedgerV3
from guiexp_android.tests.test_budget_matched import MODEL, Response, SDK, metadata, ask
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android import matched_run_v3
from guiexp_android import matched_analyze
from guiexp_android.tests.test_budget_matched import spec_fixture


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    status_code = 429
    response = SimpleNamespace(headers={"retry-after": "120"})


class SequenceSDK(SDK):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.outcomes.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def guarded(tmp_path, outcomes):
    clock = Clock()
    ledger = BudgetLedgerV3(tmp_path / "budget.sqlite3", now=clock.now)
    sdk = SequenceSDK(outcomes)
    client = BudgetClientV3(ledger, {MODEL: validate_metadata(MODEL, metadata())},
                            sdk, metadata_fetcher=metadata, sleep=clock.sleep)
    client.begin_episode("fresh")
    return client, ledger, clock


@pytest.mark.parametrize("error", [APIConnectionError, APITimeoutError, RateLimitError])
def test_one_retry_has_two_receipts_and_keeps_first_unknown(tmp_path, error):
    client, ledger, clock = guarded(tmp_path, [error("SECRET"), Response("0.01")])
    ask(client)
    assert len(client.sdk.calls) == 2
    assert client.sdk.calls[0] == client.sdk.calls[1]
    assert clock.now() >= (1120 if error is RateLimitError else 1060)
    with ledger.connect() as db:
        rows = db.execute("SELECT id,state,actual_nano,reserved_nano,request_sha FROM calls ORDER BY created").fetchall()
    assert rows[0][0] == "v3/fresh/logical-0001/attempt-1"
    assert rows[1][0] == "v3/fresh/logical-0001/attempt-2"
    assert rows[0][1:3] == ("uncertain", None)
    assert rows[1][1:3] == ("settled", 10000000)
    assert rows[0][4] == rows[1][4]
    assert ledger.summary()["budget_occupied_usd"] == "0.1693344"


def test_two_failures_stop_without_third_attempt(tmp_path):
    client, ledger, clock = guarded(tmp_path, [APIConnectionError(), APITimeoutError(), Response()])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(client.sdk.calls) == 2
    assert ledger.summary()["unresolved_reserved_usd"] == "0.3186688"
    assert ledger.summary()["actual_usd"] == "0"


@pytest.mark.parametrize("outcome", [ValueError("bad parse"), Response(None)])
def test_other_error_or_missing_bill_never_retries(tmp_path, outcome):
    client, ledger, clock = guarded(tmp_path, [outcome, Response()])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(client.sdk.calls) == 1
    assert ledger.summary()["unresolved_reserved_usd"] == "0.1593344"


def test_insufficient_budget_for_second_attempt_never_sends_it(tmp_path):
    client, ledger, clock = guarded(tmp_path, [APIConnectionError(), Response()])
    ledger.reserve("old", "old", MODEL, "sha", "9.75")
    ledger.settle("old", Response("9.75"))
    with pytest.raises(BudgetStop, match="ceiling"):
        ask(client)
    assert len(client.sdk.calls) == 1
    assert ledger.summary()["budget_occupied_usd"] == "9.9093344"


def test_old_retryable_error_does_not_retry_a_current_metadata_failure(tmp_path):
    client, ledger, clock = guarded(tmp_path, [Response()])
    ledger.reserve("old", "old", MODEL, "sha", "0.1")
    ledger.record_error("old", APIConnectionError())
    def fail_metadata(model):
        raise BudgetStop("metadata unavailable")
    client.metadata_fetcher = fail_metadata
    with pytest.raises(BudgetStop, match="metadata"):
        ask(client)
    assert client.sdk.calls == [] and ledger.summary()["calls"] == 1


def test_budget_denial_before_any_send_does_not_use_old_retry_error(tmp_path):
    client, ledger, clock = guarded(tmp_path, [Response()])
    ledger.reserve("old", "old", MODEL, "sha", "9.9")
    ledger.record_error("old", APIConnectionError())
    with pytest.raises(BudgetStop):
        ask(client)
    assert client.sdk.calls == [] and ledger.summary()["calls"] == 1


def test_transport_retry_does_not_advance_agent_history_twice(tmp_path):
    from guiexp_android.agent import AndroidAgent
    response = Response("0.01")
    response.usage = SimpleNamespace(prompt_tokens=10, completion_tokens=10, cost=.01,
                                     prompt_tokens_details=None, model_extra={})
    response.choices = [SimpleNamespace(message=SimpleNamespace(content='action: {"action_type":"status","goal_status":"complete"}'))]
    client, ledger, clock = guarded(tmp_path, [APIConnectionError(), response])
    agent = AndroidAgent(MODEL, client=client)
    reply, usage = agent.act("Goal", {"screenshot_b64": "AAAA", "ax_tree_text": "Screen", "url": "test"})
    assert len(client.sdk.calls) == 2
    assert len(agent.history) == 2  # one user observation and one final reply
    assert agent.history[-1]["content"] == reply
    assert usage["cost_usd"] == .01


def test_running_recovery_is_explicit_and_does_not_touch_receipts(tmp_path):
    row = {"id": "model/family/s1/doc"}
    spec = {"episodes": [row]}
    path = tmp_path / "episodes" / row["id"] / "state.json"
    atomic_json(path, {"status": "running", "started_unix": 1})
    assert matched_run_v3.recover_running(tmp_path, spec) == 1
    assert json.loads(path.read_text())["status"] == "interrupted"
    assert matched_run_v3.recover_running(tmp_path, spec) == 0


def test_completed_pair_with_unknown_attempt_gets_bounds_not_exact_mean(tmp_path):
    spec = spec_fixture(tmp_path)
    spec["episodes"] = spec["episodes"][:2]
    atomic_json(tmp_path / "spec.v3.json", spec)
    clock = Clock()
    ledger = BudgetLedgerV3(tmp_path / "budget.sqlite3", now=clock.now)
    discover, doc = spec["episodes"]
    assert discover["condition"] == "discover"
    ledger.reserve("unknown", discover["id"], MODEL, "hash", "0.1")
    ledger.uncertain("unknown", "APIConnectionError")
    clock.sleep(20)
    ledger.reserve("known-react", discover["id"], MODEL, "hash", "0.1")
    ledger.settle("known-react", Response("0.02"))
    clock.sleep(20)
    ledger.reserve("known-doc", doc["id"], MODEL, "hash", "0.1")
    ledger.settle("known-doc", Response("0.03"))
    for row in (discover, doc):
        atomic_json(tmp_path / "episodes" / row["id"] / "state.json", {
            "status": "done", "binding_sha256": "same-binding",
            "result": {"success": row["condition"] == "discover", "total_cost_usd": .02 if row["condition"] == "discover" else .03}})
    with ledger.connect() as db:
        before = db.execute("SELECT * FROM calls ORDER BY id").fetchall()
    result = matched_analyze.analyze(tmp_path)
    with ledger.connect() as db:
        assert before == db.execute("SELECT * FROM calls ORDER BY id").fetchall()
    assert result["complete_serving_pairs"] == 1 and result["exact_cost_pairs"] == 0
    cell = next(iter(result["cells"].values()))
    assert cell["confidence_interval"] is None and "discover_mean_usd" not in cell
    service = cell["complete_serving"]
    assert service["mean_cost_usd_intervals"]["discover"] == pytest.approx([.02, .12])
    assert service["mean_cost_usd_intervals"]["doc"] == pytest.approx([.03, .03])
    assert service["mean_doc_minus_discover_usd_interval"] == pytest.approx([-.09, .01])
    assert service["success_counts"] == {"discover": 1, "doc": 0}
    assert result["all_requests_actual_usd"] == pytest.approx(.05)
    assert result["all_requests_unresolved_reserved_usd"] == pytest.approx(.1)
