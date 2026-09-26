"""Offline checks for the independent recovery-validation budget client."""

from __future__ import annotations

import copy
from decimal import Decimal
import io
import json
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from guiexp_android import recovery_validation_budget_v4 as budget


GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4.1-flash"
TEST_KEY = "offline-fixture-key-no-real-credential"
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRfkAAAAASUVORK5CYII="
MESSAGES = [{"role": "user", "content": [
    {"type": "text", "text": "Current AX tree: button Save. Verify the final state."},
    {"type": "image_url", "image_url": {"url": IMAGE}},
]}]


def make_locks(models=(GLM, DS)):
    return {model: {
        "model_id": model, "context_length": 4096,
        "endpoints": [{
            "tag": tag, "provider_name": provider, "status": 0,
            "context_length": 8192, "max_completion_tokens": 2048,
            "supports_image": True, "pricing_tiers": [],
            "pricing": {**{key: str(value) for key, value in budget.TARGET_PRICES[model].items()},
                        "request": "0", "image": "0", "discount": "0"},
        } for tag, provider in (("example-a", "Example A"), ("example-b", "Example B"))],
    } for model in models}


def receipt(model=GLM, generation="gen-offline-1", *, discount=False):
    prices = budget.TARGET_PRICES[model]
    expected = Decimal(600) * prices["prompt"] + Decimal(400) * prices["input_cache_read"] + Decimal(200) * prices["completion"]
    cost = expected * (Decimal("0.99") if discount else 1)
    return {
        "id": generation, "model": model, "provider": "Example A",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "Verified."}}],
        "usage": {
            "prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200,
            "prompt_tokens_details": {"cached_tokens": 400},
            "completion_tokens_details": {"reasoning_tokens": 50},
            "cost": str(cost), "is_byok": False,
            "cost_details": {"upstream_inference_cost": str(expected)},
        },
    }


class OfflineTransport:
    def __init__(self, response=None, exception=None):
        self.response = response or receipt()
        self.exception = exception
        self.calls = []

    def __call__(self, url, payload, api_key, timeout):
        self.calls.append((url, copy.deepcopy(payload), api_key, timeout))
        if self.exception:
            raise self.exception
        return copy.deepcopy(self.response)


class BudgetClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / "fixture.env"
        self.env.write_text(
            "OPENROUTER_BASE_URL=https://untrusted.invalid\n"
            "OTHER_KEY=$(this_must_never_execute)\n"
            f"export OPENROUTER_API_KEY='{TEST_KEY}' # offline fixture\n",
            encoding="utf-8",
        )
        self.sequence = 0

    def client(self, *, response=None, exception=None, budget_usd=30, locks=None):
        self.sequence += 1
        ledger = budget.BudgetLedger(self.root / f"ledger-{self.sequence}.sqlite", budget_usd)
        transport = OfflineTransport(response, exception)
        client = budget.BudgetClient(ledger, locks or make_locks(), self.env, transport)
        return client, ledger, transport

    def call(self, client, model=GLM):
        return client.complete(model, MESSAGES, max_tokens=512, episode="offline-episode")

    def test_both_models_raw_cached_accounting_and_platform_discount(self):
        for model, expected in ((GLM, 202_000), (DS, 422_400)):
            with self.subTest(model=model):
                client, ledger, transport = self.client(response=receipt(model, discount=True))
                result = self.call(client, model)
                row = ledger.records()[0]
                self.assertEqual(result["model"], model)
                self.assertEqual(row["expected_cost_nusd"], expected)
                self.assertEqual(row["actual_cost_nusd"], expected * 99 // 100)
                self.assertEqual(row["platform_discount_nusd"], expected // 100)
                self.assertEqual(row["prompt_tokens"], 1000)
                self.assertEqual(row["cached_prompt_tokens"], 400)
                self.assertEqual(row["completion_tokens"], 200)
                self.assertEqual(row["reasoning_tokens"], 50)
                self.assertEqual(row["state"], "settled")
                self.assertEqual(ledger.summary()["remaining_nusd"], 30_000_000_000 - row["actual_cost_nusd"])
                self.assertEqual(len(transport.calls), 1)

    def test_reservation_exists_before_one_physical_request(self):
        client, ledger, transport = self.client()
        def checked_transport(*args):
            summary = ledger.summary()
            self.assertEqual(summary["states"], {"pending": 1})
            self.assertTrue(summary["blocked"])
            self.assertEqual(summary["exposure_nusd"], 4096 * 150 + 512 * 500)
            return transport(*args)
        client.transport = checked_transport
        self.call(client)
        self.assertFalse(ledger.summary()["blocked"])

    def test_fixed_official_url_pool_and_safe_request_evidence(self):
        client, ledger, transport = self.client()
        self.call(client)
        url, payload, key, timeout = transport.calls[0]
        self.assertEqual(url, budget.API_URL)
        self.assertEqual(key, TEST_KEY)
        self.assertEqual(timeout, 120)
        self.assertEqual(payload["provider"], {
            "only": ["example-a", "example-b"], "allow_fallbacks": True,
            "require_parameters": True,
            "max_price": {"prompt": 0.15, "completion": 0.5, "image": 0},
        })
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        row = ledger.records()[0]
        for field in ("request_hash", "response_hash", "output_hash"):
            self.assertEqual(len(row[field]), 64)
        self.assertNotIn(IMAGE, row["request_json"])
        self.assertNotIn(TEST_KEY, json.dumps(row))
        self.assertIn("Current AX tree", row["request_json"])
        self.assertEqual(json.loads(row["response_json"]), receipt())

    def test_budget_rejects_before_transport(self):
        client, ledger, transport = self.client(budget_usd="0.0008")
        with self.assertRaises(budget.BudgetExceeded):
            self.call(client)
        self.assertEqual(transport.calls, [])
        self.assertEqual(ledger.records(), [])

    def test_budget_cap_and_existing_budget_are_immutable(self):
        with self.assertRaises(budget.BudgetExceeded):
            budget.BudgetLedger(self.root / "too-high.sqlite", 30.000000001)
        _, ledger, _ = self.client()
        with self.assertRaises(budget.BudgetExceeded):
            budget.BudgetLedger(ledger.path, 29)

    def test_repeated_settled_spend_consumes_cap(self):
        client, ledger, transport = self.client(budget_usd="0.0011")
        self.call(client)
        transport.response["id"] = "gen-offline-2"
        self.call(client)
        with self.assertRaises(budget.BudgetExceeded):
            self.call(client)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(ledger.summary()["settled_cost_nusd"], 404_000)

    def test_timeout_retains_reservation_and_blocks_next_call(self):
        client, ledger, transport = self.client(exception=TimeoutError("do not log arbitrary exception text"))
        with self.assertRaises(budget.TransportUncertain):
            self.call(client)
        summary = ledger.summary()
        self.assertEqual(summary["states"], {"unknown": 1})
        self.assertEqual(summary["exposure_nusd"], 870_400)
        self.assertEqual(summary["known_cost_nusd"], 0)
        with self.assertRaises(budget.BudgetBlocked):
            self.call(client)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn("do not log", ledger.records()[0]["error"])

    def test_reopened_pending_request_becomes_unknown(self):
        _, ledger, _ = self.client()
        ledger.reserve(model=GLM, episode="interrupted", reservation_nusd=1234,
                       request_hash="hash", request_json="{}")
        reopened = budget.BudgetLedger(ledger.path)
        self.assertEqual(reopened.summary()["states"], {"unknown": 1})
        self.assertEqual(reopened.summary()["exposure_nusd"], 1234)

    def test_malformed_usage_identity_or_success_body_stops_paid_work(self):
        cases = []
        for key in ("cost", "total_tokens", "prompt_tokens_details"):
            bad = receipt()
            del bad["usage"][key]
            cases.append(bad)
        for path, value in (
            (("model",), "unapproved/model"),
            (("provider",), "Unapproved Provider"),
            (("id",), None),
            (("choices",), []),
            (("usage", "prompt_tokens"), 1000.0),
            (("usage", "prompt_tokens_details", "cached_tokens"), 1001),
            (("usage", "total_tokens"), 1201),
            (("usage", "is_byok"), True),
            (("usage", "cost"), "NaN"),
        ):
            bad = receipt()
            item = bad
            for key in path[:-1]:
                item = item[key]
            item[path[-1]] = value
            cases.append(bad)
        for index, response in enumerate(cases):
            with self.subTest(case=index):
                client, ledger, transport = self.client(response=response)
                with self.assertRaises(budget.BillingError):
                    self.call(client)
                self.assertEqual(ledger.records()[0]["state"], "unknown")
                self.assertEqual(ledger.summary()["exposure_nusd"], 870_400)
                with self.assertRaises(budget.BudgetBlocked):
                    self.call(client)
                self.assertEqual(len(transport.calls), 1)

    def test_repeated_generation_id_is_not_an_independent_generation(self):
        client, ledger, transport = self.client()
        self.call(client)
        with self.assertRaisesRegex(budget.BillingError, "Repeated generation ID"):
            self.call(client)
        rows = ledger.records()
        self.assertEqual([row["state"] for row in rows], ["settled", "unknown"])
        self.assertEqual(rows[0]["generation_id"], rows[1]["generation_id"])
        self.assertEqual(ledger.summary()["exposure_nusd"], 202_000 + 870_400)
        with self.assertRaises(budget.BudgetBlocked):
            self.call(client)
        self.assertEqual(len(transport.calls), 2)

    def test_quote_mismatch_context_fees_and_overrides_are_rejected(self):
        cases = [
            ("status", 1), ("status", False), ("context_length", 2048),
            ("supports_image", False), ("model_id", "other/model"),
            ("pricing_tiers", [{"from": 1, "rate": "0.1"}]),
            ("time_price_override", "1"), ("discount", 0.1),
        ]
        for key, value in cases:
            with self.subTest(field=key):
                locks = make_locks()
                locks[GLM]["endpoints"][1][key] = value
                with self.assertRaises(budget.QuoteError):
                    budget.validate_locks(locks)
        for key in ("prompt", "completion", "input_cache_read", "image", "request", "unknown_fee"):
            with self.subTest(price=key):
                locks = make_locks()
                locks[GLM]["endpoints"][1]["pricing"][key] = "0.1"
                with self.assertRaises(budget.QuoteError):
                    budget.validate_locks(locks)
        locks = make_locks()
        del locks[GLM]["endpoints"][0]["pricing"]["image"]
        with self.assertRaises(budget.QuoteError):
            budget.validate_locks(locks)

    def test_frozen_pool_cannot_change_in_existing_ledger(self):
        _, ledger, _ = self.client()
        changed = make_locks()
        changed[GLM]["endpoints"][0]["tag"] = "new-provider"
        with self.assertRaises(budget.QuoteError):
            budget.BudgetClient(ledger, changed, self.env, OfflineTransport())

    def test_in_memory_quote_mutation_cannot_change_the_request_pool(self):
        client, ledger, transport = self.client()
        client.locks[GLM]["endpoints"][0]["tag"] = "unfrozen-provider"
        with self.assertRaises(budget.QuoteError):
            self.call(client)
        self.assertEqual(transport.calls, [])
        self.assertEqual(ledger.records(), [])

    def test_reported_cost_over_reservation_records_overrun(self):
        response = receipt()
        response["usage"]["cost"] = "0.01"
        client, ledger, transport = self.client(response=response, budget_usd="0.001")
        with self.assertRaises(budget.BudgetOverrun):
            self.call(client)
        self.assertEqual(ledger.summary()["states"], {"overrun": 1})
        self.assertEqual(ledger.summary()["exposure_nusd"], 10_000_000)
        self.assertEqual(ledger.summary()["remaining_nusd"], 0)
        with self.assertRaises(budget.BudgetBlocked):
            self.call(client)
        self.assertEqual(len(transport.calls), 1)

    def test_no_upstream_requires_actual_to_match_quote(self):
        response = receipt()
        del response["usage"]["cost_details"]
        client, ledger, _ = self.client(response=response)
        self.call(client)
        self.assertIsNone(ledger.records()[0]["upstream_cost_nusd"])
        self.assertEqual(ledger.records()[0]["platform_discount_nusd"], 0)
        response["usage"]["cost"] = "0.0001"
        client, _, _ = self.client(response=response)
        with self.assertRaises(budget.BillingError):
            self.call(client)

    def test_upstream_cost_must_match_cache_adjusted_quote(self):
        response = receipt()
        response["usage"]["cost_details"]["upstream_inference_cost"] = "0.0005"
        client, _, _ = self.client(response=response)
        with self.assertRaises(budget.BillingError):
            self.call(client)

    def test_upstream_components_are_checked_separately(self):
        response = receipt()
        response["usage"]["cost_details"].update({
            "upstream_inference_prompt_cost": "0.000102",
            "upstream_inference_completions_cost": "0.000100",
        })
        client, _, _ = self.client(response=response)
        self.call(client)
        response["usage"]["cost_details"]["upstream_inference_prompt_cost"] = "0.000200"
        client, _, _ = self.client(response=response)
        with self.assertRaises(budget.BillingError):
            self.call(client)

    def test_missing_reasoning_is_unknown_not_invented_zero(self):
        response = receipt()
        del response["usage"]["completion_tokens_details"]
        client, ledger, _ = self.client(response=response)
        self.call(client)
        self.assertIsNone(ledger.records()[0]["reasoning_tokens"])

    def test_isolated_reasoning_anomaly_keeps_primary_billing_and_raw_value(self):
        for raw in (201, -1, "not-an-integer"):
            with self.subTest(raw=raw):
                response = receipt()
                response["usage"]["completion_tokens_details"]["reasoning_tokens"] = raw
                client, ledger, _ = self.client(response=response)
                self.call(client)
                row = ledger.records()[0]
                self.assertEqual(row["state"], "settled")
                self.assertIsNone(row["reasoning_tokens"])
                self.assertEqual(row["completion_tokens"], 200)
                self.assertEqual(row["actual_cost_nusd"], 202_000)
                recorded = json.loads(row["billing_json"])
                self.assertEqual(recorded["reasoning_tokens_raw"], raw)
                self.assertIsNotNone(recorded["reasoning_tokens_anomaly"])
                self.assertFalse(ledger.summary()["blocked"])

    def reconciliation_fixture(self):
        locks = make_locks(models=(DS,))
        locks[DS]["context_length"] = 32768
        for endpoint in locks[DS]["endpoints"]:
            endpoint.update(context_length=65536, max_completion_tokens=8192)
        self.sequence += 1
        ledger = budget.BudgetLedger(self.root / f"reconcile-{self.sequence}.sqlite")
        ledger.bind_locks(locks)
        payload = {"model": DS, "messages": MESSAGES, "max_tokens": 4096,
                   "provider": {"only": [e["tag"] for e in locks[DS]["endpoints"]],
                                "allow_fallbacks": True, "require_parameters": True,
                                "max_price": {"prompt": 0.3, "completion": 1.2, "image": 0}}}
        reservation = 32768 * 300 + 4096 * 1200
        request_id = ledger.reserve(model=DS, episode="v3/fixture/binding", reservation_nusd=reservation,
                                    request_hash=budget._hash(budget._json(payload)),
                                    request_json=budget._safe_request(payload))
        response = receipt(DS, generation="gen-reconcile-fixture")
        response["usage"] = {
            "prompt_tokens": 8731, "completion_tokens": 1557, "total_tokens": 10288,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 1646},
            "cost": "0.004442823", "is_byok": False,
            "cost_details": {"upstream_inference_prompt_cost": "0.0026193",
                             "upstream_inference_completions_cost": "0.0018684",
                             "upstream_inference_cost": "0.0044877"},
        }
        ledger.record_response(request_id, response)
        ledger.fail(request_id, "Reasoning tokens exceed completion tokens", response)
        native = "deepseek/deepseek-v4.1-flash-20260910"
        generation = {"url": "https://openrouter.ai/api/v1/generation?id=gen-reconcile-fixture",
                      "response": {"data": {
                          "id": response["id"], "model": native, "provider_name": "Example A",
                          "native_tokens_prompt": 8731, "native_tokens_completion": 1557,
                          "native_tokens_cached": 0, "native_tokens_reasoning": 1646,
                          "tokens_prompt": 7818, "tokens_completion": 1655,
                          "usage": "0.004442823", "total_cost": "0.004442823",
                          "upstream_inference_cost": 0, "cancelled": False,
                          "is_byok": False, "finish_reason": "stop",
                          "provider_responses": [{"provider_name": "Example A", "status": 200,
                                                  "model_permaslug": native, "is_byok": False}],
                      }}}
        path = self.root / f"generation-{self.sequence}.json"
        path.write_text(json.dumps(generation))
        return ledger, request_id, path, generation

    def test_reconciliation_dry_run_never_mutates_or_reads_key(self):
        ledger, request_id, path, _ = self.reconciliation_fixture()
        before = ledger.records()[0]
        with patch.object(budget, "_load_api_key", side_effect=AssertionError("No key access")):
            result = budget.reconcile_reasoning_subcount(ledger.path, request_id, path)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["actual_cost_nusd"], 4_442_823)
        self.assertEqual(result["expected_quote_nusd"], 4_487_700)
        self.assertEqual(result["platform_discount_nusd"], 44_877)
        self.assertEqual(result["reasoning_tokens_raw"], 1646)
        self.assertEqual(result["generation_metadata_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(ledger.records()[0], before)
        with sqlite3.connect(ledger.path) as con:
            self.assertIsNone(con.execute("SELECT name FROM sqlite_master WHERE name=?", (budget._AUDIT_TABLE,)).fetchone())

    def test_reconciliation_updates_only_target_preserves_raw_and_audit_is_append_only(self):
        ledger, request_id, path, _ = self.reconciliation_fixture()
        old = ledger.records()[0]
        other = {**old, "request_id": "other-row", "generation_id": "other-generation", "error": "Another unresolved error"}
        with sqlite3.connect(ledger.path) as con:
            con.execute(f"INSERT INTO requests ({', '.join(other)}) VALUES ({', '.join('?' for _ in other)})", tuple(other.values()))
        result = budget.reconcile_reasoning_subcount(ledger.path, request_id, path, apply=True)
        self.assertTrue(result["applied"])
        rows = {r["request_id"]: r for r in ledger.records()}
        self.assertEqual(rows["other-row"], other)
        settled = rows[request_id]
        self.assertEqual(settled["state"], "settled")
        self.assertEqual((settled["prompt_tokens"], settled["completion_tokens"], settled["cached_prompt_tokens"]), (8731, 1557, 0))
        self.assertIsNone(settled["reasoning_tokens"])
        self.assertEqual(settled["actual_cost_nusd"], 4_442_823)
        for field in ("response_json", "response_hash", "output_hash", "request_json", "request_hash"):
            self.assertEqual(settled[field], old[field])
        with sqlite3.connect(ledger.path) as con:
            con.row_factory = sqlite3.Row
            audit = dict(con.execute(f"SELECT * FROM {budget._AUDIT_TABLE}").fetchone())
            self.assertEqual(json.loads(audit["old_row_json"]), old)
            self.assertEqual(audit["generation_metadata_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            for sql in (f"DELETE FROM {budget._AUDIT_TABLE}", f"UPDATE {budget._AUDIT_TABLE} SET validator='changed'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(sql)
                con.rollback()
        with self.assertRaises(budget.ReconciliationError):
            budget.reconcile_reasoning_subcount(ledger.path, request_id, path, apply=True)
        self.assertEqual(ledger.summary()["states"], {"settled": 1, "unknown": 1})

    def test_reconciliation_rejects_each_primary_mismatch_without_writes(self):
        ledger, request_id, path, original = self.reconciliation_fixture()
        before = ledger.records()[0]
        changes = [("native_tokens_prompt", 8732), ("native_tokens_completion", 1558),
                   ("native_tokens_cached", 1), ("native_tokens_reasoning", 1647),
                   ("total_cost", "0.004442824"), ("usage", "0.004442824"),
                   ("provider_name", "Other Provider"), ("model", DS), ("id", "another-id"),
                   ("upstream_inference_cost", "0.001"), ("cancelled", True)]
        for field, value in changes:
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed["response"]["data"][field] = value
                path.write_text(json.dumps(changed))
                with self.assertRaises(budget.BudgetError):
                    budget.reconcile_reasoning_subcount(ledger.path, request_id, path, apply=True)
                self.assertEqual(ledger.records()[0], before)
        path.write_text(json.dumps(original))
        with sqlite3.connect(ledger.path) as con:
            con.execute("UPDATE requests SET error='Uncertain transport' WHERE request_id=?", (request_id,))
        with self.assertRaises(budget.ReconciliationError):
            budget.reconcile_reasoning_subcount(ledger.path, request_id, path, apply=True)

    def test_screenshot_and_tree_observation_required_before_reservation(self):
        client, ledger, transport = self.client()
        for messages in (
            [{"role": "user", "content": "Tree only"}],
            [{"role": "user", "content": [MESSAGES[0]["content"][0]]}],
            [{"role": "user", "content": [MESSAGES[0]["content"][1]]}],
        ):
            with self.assertRaises(budget.BudgetError):
                client.complete(GLM, messages, 512, "no-image")
        self.assertEqual(transport.calls, [])
        self.assertEqual(ledger.records(), [])

    def test_context_and_completion_bounds_reject_before_transport(self):
        client, ledger, transport = self.client()
        for output_tokens in (0, -1, True, 2049, 4097):
            with self.assertRaises(budget.BudgetError):
                client.complete(GLM, MESSAGES, output_tokens, "invalid-bound")
        self.assertEqual(transport.calls, [])
        self.assertEqual(ledger.records(), [])

    def test_http_error_preserves_sanitized_body_and_never_retries(self):
        client, ledger, _ = self.client()
        client.transport = budget._http_transport
        error = urllib.error.HTTPError(
            budget.API_URL, 503, "unavailable", {},
            io.BytesIO(f'{{"error":"unavailable {TEST_KEY}"}}'.encode()),
        )
        with patch.object(budget.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(budget.BillingError):
                self.call(client)
            self.assertEqual(build.return_value.open.call_count, 1)
            self.assertIsInstance(build.call_args.args[0], budget._NoRedirect)
        row = ledger.records()[0]
        saved = json.loads(row["response_json"])
        self.assertEqual(saved["_transport"]["http_status"], 503)
        self.assertIn("[REDACTED_API_KEY]", saved["_transport"]["body"])
        self.assertNotIn(TEST_KEY, json.dumps(row))
        self.assertEqual(row["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
