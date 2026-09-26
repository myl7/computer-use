"""Offline tests for the explicitly authorized exploratory budget tranche."""
from __future__ import annotations

import fcntl
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from guiexp_android import selective_explore_budget as ex
from guiexp_android import selective_budget as base
from guiexp_android.budget_client import BudgetStop, OFFICIAL_BASE
from guiexp_android.budget_client_v9 import BudgetLedgerV9


MODEL = ex.MODEL


def response(cost="0.001", content="action"):
    return SimpleNamespace(
        model_dump=lambda mode="json": {
            "id": "gen-explore-test",
            "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"cost": cost},
        },
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


def metadata(provider="wafer", prompt="0.00000010", completion="0.00000035", max_completion=943718):
    return {
        "data": {
            "id": MODEL,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [{
                "tag": provider,
                "status": 0,
                "context_length": 1048576,
                "max_completion_tokens": max_completion,
                "supported_parameters": ["max_tokens", "temperature", "reasoning", "reasoning_effort"],
                "pricing": {"prompt": prompt, "completion": completion, "input_cache_read": "0.00000002"},
            }],
        }
    }


class SDK:
    max_retries = 0
    base_url = OFFICIAL_BASE

    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.outcomes.pop(0) if self.outcomes else response()
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        return None


class Clock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += float(seconds)


class APIConnectionError(Exception):
    pass


class Provider429:
    status_code = 429

    def __init__(self):
        self.raw = {"error": {"code": 429, "message": "capacity"}, "id": "gen-error"}

    def model_dump(self, mode="json"):
        return self.raw


def _request_body(path, **overrides):
    value = {
        "record_type": "explicit_user_budget_extension_request",
        "additional_usd": "10",
        "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10",
        "shared_ledger": str(path),
        "preserve_all_unknown_fees": True,
    }
    value.update(overrides)
    return value


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    ledger_path = tmp_path / "revision_20260913" / "budget.sqlite3"
    lock_path = tmp_path / "revision_20260913" / "run.lock"
    auth_request = tmp_path / "selective_20260915" / "budget_authorization_request_20260915.json"
    auth_path = tmp_path / "selective_20260915" / "budget_authorization_20260915.json"
    explore_out = tmp_path / "selective_20260915" / "provider_v3"
    ledger_path.parent.mkdir(parents=True)
    auth_request.parent.mkdir(parents=True)
    lock_path.touch()
    base_ledger = BudgetLedgerV9(ledger_path)
    monkeypatch.setattr(ex, "SHARED_LEDGER_PATH", ledger_path.resolve())
    monkeypatch.setattr(ex, "SHARED_RUN_LOCK_PATH", lock_path.resolve())
    monkeypatch.setattr(ex, "AUTHORIZATION_REQUEST_PATH", auth_request.resolve())
    monkeypatch.setattr(ex, "AUTHORIZATION_PATH", auth_path.resolve())
    monkeypatch.setattr(ex, "EXPLORE_OUT", explore_out.resolve())
    monkeypatch.setattr(ex, "AUTHORIZATION_REQUEST_PATH", auth_request.resolve())
    return SimpleNamespace(
        ledger_path=ledger_path.resolve(),
        lock_path=lock_path.resolve(),
        request_path=auth_request.resolve(),
        auth_path=auth_path.resolve(),
        ledger=base_ledger,
    )


def authorize(sandbox):
    with sandbox.request_path.open("w") as handle:
        json.dump(_request_body(sandbox.ledger_path), handle)
    # The request watermarks must match the actual baseline.
    snapshot = ex._read_baseline(sandbox.ledger_path)
    request = json.loads(sandbox.request_path.read_text())
    request.update(snapshot)
    sandbox.request_path.write_text(json.dumps(request) + "\n")
    return ex.authorize_tranche(sandbox.request_path, sandbox.auth_path, ledger_path=sandbox.ledger_path)


def client_for(sandbox, *, profile=ex.DEFAULT_SERVING_PROFILE, provider="wafer", outcomes=(), metadata_fn=None, guard=lambda: True, attempts=None):
    auth = authorize(sandbox)
    clock = Clock()
    ledger = ex.SelectiveExploreLedger(
        sandbox.ledger_path,
        authorization_path=sandbox.auth_path,
        now=clock.now,
        host_guard=guard,
    )
    request_profile = profile
    if attempts is not None:
        request_profile = {**ex.REQUEST_PROFILES[profile], "max_physical_attempts": attempts}
    sdk = SDK(outcomes)
    client = ex.make_client(
        {MODEL: {"provider": provider, "max_tokens": 4096 if profile == "serving_4096" else 16384}},
        ledger=ledger,
        sdk=sdk,
        episode=f"{ex.CALL_PREFIX}provider_v3/test",
        profile=request_profile,
        provider_profile=provider,
        metadata_fetcher=metadata_fn or (lambda _model: metadata(provider=provider)),
        sleep=clock.sleep,
        host_guard=guard,
        authorization_path=sandbox.auth_path,
    )
    return client, ledger, sdk, auth


def ask(client):
    return client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "goal"}],
        temperature=0,
    )


def test_authorize_tranche_snapshots_baseline_and_is_idempotent(sandbox):
    first = authorize(sandbox)
    assert first["namespace"] == ex.EXPLORE_NAMESPACE
    assert first["baseline_calls"] == 0
    assert first["baseline_max_rowid"] == 0
    assert first["total_occupied_ceiling_usd"] == "20"
    # Reusing the same request after a ledger change returns the immutable
    # first baseline rather than refreshing it.
    sandbox.ledger.reserve("later", "old", MODEL, "sha", "0.1")
    sandbox.ledger.settle("later", response("0.001"))
    second = ex.authorize_tranche(sandbox.request_path, sandbox.auth_path, ledger_path=sandbox.ledger_path)
    assert second["authorization_sha256"] == first["authorization_sha256"]
    assert second["baseline_calls"] == 0


def test_legacy_ten_setting_stays_unchanged_and_summary_reports_twenty(sandbox):
    authorize(sandbox)
    ledger = ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, host_guard=lambda: True)
    with ledger.connect() as db:
        assert db.execute("SELECT value FROM settings WHERE key='limit_nano'").fetchone()[0] == "10000000000"
    summary = ledger.summary()
    assert summary["limit_usd"] == "20"
    assert summary["total_limit_usd"] == "20"
    assert summary["tranche_limit_usd"] == "10"
    assert summary["legacy_limit_usd"] == "10"


def test_constructor_requires_authorization_artifact(sandbox):
    with pytest.raises(BudgetStop, match="authorization"):
        ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, host_guard=lambda: True)


def test_old_unknown_reservation_is_global_only_and_does_not_consume_new_tranche(sandbox):
    sandbox.ledger.reserve("old-unknown", "old", MODEL, "sha", "9.99")
    sandbox.ledger.uncertain("old-unknown", "Timeout")
    authorize(sandbox)
    ledger = ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, host_guard=lambda: True)
    with ledger.connect() as db:
        db.execute("UPDATE pacing_v2 SET next_send=0 WHERE id=1")
    amount = ex._lock_set({MODEL: {}}, "wafer", "serving_4096")[MODEL]["reservation_usd"]
    ledger.reserve("new", "selective_20260915/provider_v3/new", MODEL, "sha", amount)
    assert ledger.summary()["tranche_occupied_usd"] == amount


def test_total_twenty_ceiling_is_checked_separately_from_new_tranche(sandbox):
    # Simulate a pre-existing ledger occupied above USD 10.  The legacy
    # setting remains 10, but the exploratory total ceiling is 20.
    with sandbox.ledger.connect() as db:
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("selective_20260915/provider_v3/baseline", "selective_20260915/provider_v3/baseline", MODEL, "sha", 1, 12_000_000_000, "settled", None, None, 1000),
        )
    authorize(sandbox)
    ledger = ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, host_guard=lambda: True)
    with ledger.connect() as db:
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("selective_20260915/provider_v3/new-old", "selective_20260915/provider_v3/new-old", MODEL, "sha", 1, 7_900_000_000, "settled", None, None, 1001),
        )
        db.execute("UPDATE pacing_v2 SET next_send=0 WHERE id=1")
    lock = ex._lock_set({MODEL: {"provider": "wafer"}}, "wafer", "serving_4096")[MODEL]
    with pytest.raises(BudgetStop, match="USD 20"):
        ledger.reserve("new", "selective_20260915/provider_v3/new", MODEL, "sha", lock["reservation_usd"])
    assert ledger.summary()["tranche_occupied_usd"] == "7.9"


def test_tranche_and_total_limits_are_atomic_and_fail_closed(sandbox):
    authorize(sandbox)
    ledger = ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, host_guard=lambda: True)
    # Add a post-baseline settled row close to the tranche ceiling.  The
    # remaining total headroom is larger, so the tranche guard is decisive.
    with ledger.connect() as db:
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("selective_20260915/provider_v3/old", "selective_20260915/provider_v3/old", MODEL, "sha", 1, 9_950_000_000, "settled", None, None, 1000),
        )
        db.execute("UPDATE pacing_v2 SET next_send=0 WHERE id=1")
    lock = ex._lock_set({MODEL: {}}, "wafer", "serving_4096")[MODEL]
    with pytest.raises(BudgetStop, match="tranche"):
        ledger.reserve("new", "selective_20260915/provider_v3/new", MODEL, "sha", lock["reservation_usd"])
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls WHERE id=?", ("selective_20260915/new",)).fetchone()[0] == 0


def test_host_guard_blocks_reservation_and_sdk(sandbox):
    client, ledger, sdk, _ = client_for(sandbox, guard=lambda: {"ready": False})
    with pytest.raises(BudgetStop):
        ask(client)
    assert sdk.calls == []
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


@pytest.mark.parametrize("provider,price,max_completion", [
    ("wafer", ("0.00000010", "0.00000035"), 943718),
    ("deepinfra_fp4", ("0.00000015", "0.00000050"), 131072),
    ("z_ai_fp8", ("0.00000015", "0.00000050"), 131072),
])
def test_provider_profiles_require_exact_endpoint_and_frozen_prices(sandbox, provider, price, max_completion):
    expected_provider = "deepinfra/fp4" if provider == "deepinfra_fp4" else ("z-ai/fp8" if provider == "z_ai_fp8" else "wafer")
    assert ex._provider_profile(provider)["provider"] == expected_provider
    assert ex._provider_profile(provider)["provider_max_completion_tokens"] == max_completion
    lock = ex._lock_set({MODEL: {"provider": expected_provider}}, provider, "serving_4096")[MODEL]
    good = metadata(provider=lock["provider"], prompt=price[0], completion=price[1], max_completion=max_completion)
    assert ex.validate_provider_metadata(MODEL, good, lock)["reservation_usd"] == lock["reservation_usd"]
    bad = metadata(provider=lock["provider"], prompt=price[0], completion="0.000001")
    with pytest.raises(BudgetStop):
        ex.validate_provider_metadata(MODEL, bad, lock)


def test_builder_profile_real_openai_sdk_wire_and_default_no_retry(sandbox):
    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "gen-wire",
            "object": "chat.completion",
            "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "action"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.001},
        })

    authorize(sandbox)
    clock = Clock()
    ledger = ex.SelectiveExploreLedger(sandbox.ledger_path, authorization_path=sandbox.auth_path, now=clock.now, host_guard=lambda: True)
    sdk = OpenAI(
        api_key="offline",
        base_url=OFFICIAL_BASE,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )
    client = ex.make_builder_client(
        {MODEL: {"provider": "wafer"}},
        ledger=ledger,
        sdk=sdk,
        episode="selective_20260915/provider_v3/builder",
        provider_profile="wafer",
        metadata_fetcher=lambda _model: metadata(),
        sleep=clock.sleep,
        host_guard=lambda: True,
        authorization_path=sandbox.auth_path,
    )
    ask(client)
    assert captured[0]["max_tokens"] == 16384
    assert captured[0]["reasoning"] == {"effort": "low"}
    assert captured[0]["provider"]["only"] == ["wafer"]
    assert client.sdk.max_retries == 0
    sdk.close()


def test_real_openai_http_429_body_is_recorded_redacted_without_retry(sandbox):
    captured = []

    def handler(request):
        captured.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": "1", "X-API-Key": "header-secret"},
            json={
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": (
                        "Upstream capacity; Authorization: Bearer sk-or-v1-supersecret "
                        "X-API-Key: header-secret"
                    ),
                }
            },
        )

    authorize(sandbox)
    clock = Clock()
    ledger = ex.SelectiveExploreLedger(
        sandbox.ledger_path,
        authorization_path=sandbox.auth_path,
        now=clock.now,
        host_guard=lambda: True,
    )
    sdk = OpenAI(
        api_key="offline",
        base_url=OFFICIAL_BASE,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )
    client = ex.make_client(
        {MODEL: {"provider": "wafer"}},
        ledger=ledger,
        sdk=sdk,
        episode="selective_20260915/provider_v3/http-429",
        metadata_fetcher=lambda _model: metadata(),
        sleep=clock.sleep,
        host_guard=lambda: True,
        authorization_path=sandbox.auth_path,
    )
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(captured) == 1
    with ledger.connect() as db:
        state, safe_json = db.execute(
            "SELECT state,(SELECT safe_json FROM error_metadata_v2 WHERE call_id=calls.id) FROM calls"
        ).fetchone()
    safe = json.loads(safe_json)
    assert state == "uncertain"
    assert safe["status_code"] == 429
    assert safe["http_error_code"] == "rate_limit_exceeded"
    assert "capacity" in safe["http_error_message"]
    assert "sk-" not in safe["http_error_message"]
    assert "Bearer" not in safe["http_error_message"]
    assert "header-secret" not in json.dumps(safe)
    assert "headers" not in safe and "body" not in safe
    sdk.close()


def test_connection_failure_is_unknown_without_automatic_resend(sandbox):
    client, ledger, sdk, _ = client_for(sandbox, outcomes=[APIConnectionError("opaque")])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(sdk.calls) == 1
    with ledger.connect() as db:
        state = db.execute("SELECT state FROM calls").fetchone()[0]
    assert state == "uncertain"


def test_explicit_provider_429_retry_uses_distinct_receipts(sandbox):
    client, ledger, sdk, _ = client_for(
        sandbox,
        profile="builder_16384",
        attempts=2,
        outcomes=[Provider429(), response("0.001")],
    )
    ask(client)
    assert len(sdk.calls) == 2
    with ledger.connect() as db:
        rows = db.execute("SELECT id,state,request_sha FROM calls ORDER BY created").fetchall()
    assert len(rows) == 2 and rows[0][1] == "uncertain" and rows[1][1] == "settled"
    assert rows[0][0] != rows[1][0] and rows[0][2] == rows[1][2]


def test_valid_answer_without_bill_is_saved_and_not_retried(sandbox):
    client, ledger, sdk, _ = client_for(sandbox, outcomes=[response(None)])
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(sdk.calls) == 1
    with ledger.connect() as db:
        state, saved = db.execute("SELECT state,response_json FROM calls").fetchone()
    assert state == "uncertain" and saved


def test_phase_manifest_freezes_provider_request_and_authorization(sandbox, tmp_path):
    authorize(sandbox)
    path = tmp_path / "phase.json"
    frozen = ex.freeze_phase_manifest(
        "build",
        model_locks={MODEL: {"provider": "wafer"}},
        provider_profile="wafer",
        request_profile="builder_16384",
        path=path,
        authorization_path=sandbox.auth_path,
    )
    loaded = ex.load_phase_manifest(path)
    assert loaded == frozen
    assert loaded["model_locks"][MODEL]["max_tokens"] == 16384
    assert loaded["model_locks"][MODEL]["reasoning"] == {"effort": "low"}
