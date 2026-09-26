"""Version 2 continuation tests use only fake HTTP responses and clocks."""
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from guiexp_android.budget_client import BudgetLedger, BudgetStop, atomic_json, validate_metadata
from guiexp_android.budget_client_v2 import BudgetClientV2, BudgetLedgerV2, error_metadata
from guiexp_android.tests.test_budget_matched import MODEL, Response, SDK, metadata, ask, spec_fixture
from guiexp_android import matched_run_v2, matched_analyze


class Clock:
    def __init__(self):
        self.t = 1000.0
    def now(self):
        return self.t
    def sleep(self, seconds):
        self.t += seconds


def guarded(tmp_path, sdk=None):
    clock = Clock()
    ledger = BudgetLedgerV2(tmp_path / "budget.sqlite3", now=clock.now)
    client = BudgetClientV2(ledger, {MODEL: validate_metadata(MODEL, metadata())},
                            sdk or SDK(), metadata_fetcher=metadata, sleep=clock.sleep)
    client.begin_episode("new-episode")
    return client, ledger, clock


def test_original_unknown_receipt_unchanged_and_new_spend_allowed(tmp_path):
    old = BudgetLedger(tmp_path / "budget.sqlite3")
    old.reserve("original-id", "original-episode", MODEL, "hash", "0.1593344")
    old.uncertain("original-id", "RateLimitError")
    with old.connect() as db:
        before = db.execute("SELECT * FROM calls WHERE id='original-id'").fetchone()
    client, ledger, clock = guarded(tmp_path)
    ask(client)
    with old.connect() as db:
        after = db.execute("SELECT * FROM calls WHERE id='original-id'").fetchone()
    assert before == after
    assert ledger.summary()["actual_usd"] == "0.01"
    assert ledger.summary()["unresolved_reserved_usd"] == "0.1593344"
    assert ledger.summary()["budget_occupied_usd"] == "0.1693344"


def test_unknowns_count_against_hard_ceiling(tmp_path):
    old = BudgetLedger(tmp_path / "budget.sqlite3")
    old.reserve("unknown", "old", MODEL, "hash", "9.9")
    old.uncertain("unknown", "TimeoutError")
    client, ledger, clock = guarded(tmp_path)
    with pytest.raises(BudgetStop, match="ceiling"):
        ask(client)
    assert client.sdk.calls == []
    assert ledger.summary()["actual_usd"] == "0"
    assert ledger.summary()["budget_occupied_usd"] == "9.9"


def test_no_old_episode_resubmission_even_with_new_call_namespace(tmp_path):
    old = BudgetLedger(tmp_path / "budget.sqlite3")
    old.reserve("v1-first", "old-episode", MODEL, "hash", "0.1")
    old.settle("v1-first", Response("0.01"))
    client, ledger, clock = guarded(tmp_path)
    with pytest.raises(BudgetStop, match="will not be rerun"):
        client.begin_episode("old-episode")
    assert client.sdk.calls == []


def test_pacing_uses_same_ledger_across_client_instances(tmp_path):
    client, ledger, clock = guarded(tmp_path)
    start = clock.now()
    ask(client)
    second = BudgetClientV2(ledger, client.model_locks, SDK(), metadata_fetcher=metadata, sleep=clock.sleep)
    second.begin_episode("another-episode")
    ask(second)
    assert clock.now() - start >= 10


def test_429_safe_metadata_and_retry_after_are_persistent(tmp_path):
    class RateLimitError(Exception):
        status_code = 429
        request_id = "req-safe"
        response = SimpleNamespace(headers={"retry-after": "75", "authorization": "SECRET"})
        body = {"id": "gen-safe", "user_id": "SECRET", "error": {"metadata": {"raw": "SECRET"}}}
    client, ledger, clock = guarded(tmp_path, SDK(error=RateLimitError("SECRET")))
    with pytest.raises(BudgetStop) as exc:
        ask(client)
    assert "SECRET" not in str(exc.value)
    with ledger.connect() as db:
        safe = db.execute("SELECT safe_json FROM error_metadata_v2").fetchone()[0]
    assert "SECRET" not in safe
    info = json.loads(safe)
    assert info["status_code"] == 429 and info["retry_after"] == "75.0"
    assert info["generation_id"] == "gen-safe" and info["request_id"] == "req-safe"
    assert ledger.summary()["next_send_not_before"] >= 1075
    resumed = BudgetClientV2(ledger, client.model_locks, SDK(), metadata_fetcher=metadata, sleep=clock.sleep)
    resumed.begin_episode("next-pair")
    ask(resumed)
    assert clock.now() >= 1075
    assert Decimal(ledger.summary()["unresolved_reserved_usd"]) > 0


def test_no_bill_remains_unknown_not_zero(tmp_path):
    client, ledger, clock = guarded(tmp_path, SDK(response=Response(None)))
    with pytest.raises(BudgetStop):
        ask(client)
    with ledger.connect() as db:
        actual, state = db.execute("SELECT actual_nano,state FROM calls").fetchone()
    assert actual is None and state == "uncertain"


def test_scheduler_skips_censored_pair_and_runs_next_complete_pair(tmp_path):
    spec = spec_fixture(tmp_path)
    first_key = spec["episodes"][0]["id"].rsplit("/", 1)[0]
    spec["censored_pairs"] = {first_key: {"reason": "censored pilot"}}
    spec["planned_bindings"] = {}
    for row in spec["episodes"]:
        cell = f"{row['model'].replace('/', '_')}/{row['family']}"
        spec["planned_bindings"].setdefault(cell, {"bindings": {}})["bindings"][str(row["seed"])] = {"binding_sha256": "hash"}
    client, ledger, clock = guarded(tmp_path)
    called = []
    def episode(**kwargs):
        called.append(kwargs)
        return {"success": False, "total_cost_usd": 0}
    result = matched_run_v2.execute(tmp_path, spec, ledger, client, None, episode, 1)
    assert len(called) == 2
    assert result["counts"]["censored_v1"] == 2
    assert result["complete_pairs"] == 1
    assert called[0]["model"] == called[1]["model"]
    assert called[0]["seed"] == called[1]["seed"]
    assert {k["condition"] for k in called} == {"discover", "doc"}


def test_analysis_one_binding_has_no_ci_and_reports_paired_success():
    pairs = [{"discover": {"total_cost_usd": .02, "success": True},
              "doc": {"total_cost_usd": .03, "success": False}}]
    result = matched_analyze.summarize(pairs)
    assert result["confidence_interval"] is None
    assert result["success_rates"] == {"discover": 1, "doc": 0}
    assert result["paired_doc_minus_discover_success_rate"] == -1
    assert result["paired_doc_minus_discover_mean_usd"] == pytest.approx(.01)


def test_analysis_bootstraps_pairs_and_does_not_impute_missing_cache():
    pairs = [{"discover": {"total_cost_usd": i + 2, "success": True},
              "doc": {"total_cost_usd": i + 1, "success": True}} for i in range(5)]
    result = matched_analyze.summarize(pairs, 100)
    assert result["confidence_interval"]["doc_minus_discover_usd"] == [-1, -1]
    split = matched_analyze.token_totals([{"prompt_tokens": 100, "completion_tokens": 20},
                                         {"prompt_tokens": 50, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 30}}])
    assert split["prompt_tokens_reported_sum"] == 150
    assert split["cached_input_tokens_reported_sum"] == 30
    assert split["uncached_input_tokens"] is None
    assert split["calls_missing_or_invalid_cache_split"] == 1


def test_analysis_does_not_double_count_ledger_and_excludes_censored(tmp_path):
    spec = spec_fixture(tmp_path)
    spec["episodes"] = spec["episodes"][:4]
    censored = spec["episodes"][0]["id"].rsplit("/", 1)[0]
    spec["censored_pairs"] = {censored: {"reason": "old partial"}}
    atomic_json(tmp_path / "spec.v2.json", spec)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3")
    for index, row in enumerate(spec["episodes"]):
        ledger.reserve(str(index), row["id"], MODEL, "hash", "0.2")
        ledger.settle(str(index), Response("0.01"))
        atomic_json(tmp_path / "episodes" / row["id"] / "state.json", {
            "status": "done", "binding_sha256": "same-pair-binding",
            "result": {"success": True, "total_cost_usd": .01}})
    result = matched_analyze.analyze(tmp_path)
    assert result["complete_pairs"] == 1
    assert result["all_requests_actual_usd"] == pytest.approx(.04)
    assert len(result["excluded_pairs"]) == 1
    cell = next(iter(result["cells"].values()))
    assert cell["actual_usd_total"] == {"discover": .01, "doc": .01}
    assert cell["distinct_binding_n"] == 1 and cell["confidence_interval"] is None
