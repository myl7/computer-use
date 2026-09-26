"""Real-client integration tests for the provider-v3 exploratory budget."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI

from guiexp_android import selective_explore_budget as budget
from guiexp_android.budget_client import BudgetStop, OFFICIAL_BASE


MODEL = "z-ai/glm-5.3-flash"


class Clock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def provider_metadata(model=MODEL, *, max_completion=943718):
    profile = budget._PROVIDER_PROFILES["wafer"]
    return {
        "data": {
            "id": model,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [{
                "tag": profile["provider"],
                "status": 0,
                "context_length": profile["context_length"],
                "max_completion_tokens": max_completion,
                "supported_parameters": ["max_tokens", "temperature", "reasoning"],
                "pricing": {
                    "prompt": str(Decimal(profile["prompt_per_m"]) / Decimal(1_000_000)),
                    "completion": str(Decimal(profile["completion_per_m"]) / Decimal(1_000_000)),
                },
            }],
        }
    }


def _make_fixture(monkeypatch, tmp_path, *, baseline_unknown=False):
    """Create a real exploratory ledger and real signed authorization in tmp."""
    ledger_path = (tmp_path / "shared.sqlite3").resolve()
    request_path = (tmp_path / "authorization-request.json").resolve()
    authorization_path = (tmp_path / "authorization.json").resolve()
    lock_path = (tmp_path / "run.lock").resolve()
    monkeypatch.setattr(budget, "SHARED_LEDGER_PATH", ledger_path)
    monkeypatch.setattr(budget, "SHARED_RUN_LOCK_PATH", lock_path)
    monkeypatch.setattr(budget, "AUTHORIZATION_REQUEST_PATH", request_path)
    monkeypatch.setattr(budget, "AUTHORIZATION_PATH", authorization_path)

    clock = Clock()
    base_ledger = budget.base.SelectiveLedger(
        ledger_path, now=clock.now, host_guard=lambda: True
    )
    if baseline_unknown:
        with sqlite3.connect(ledger_path) as database:
            database.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "baseline/unknown",
                    "historical/episode",
                    MODEL,
                    "baseline-sha",
                    10_000_000_000,
                    None,
                    "uncertain",
                    None,
                    "BudgetStop",
                    1.0,
                ),
            )
    request = {
        "additional_usd": "10",
        "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10",
        "shared_ledger": str(ledger_path),
        "preserve_all_unknown_fees": True,
    }
    request_path.write_text(json.dumps(request), encoding="utf-8")
    authorization = budget.authorize_tranche(
        request_path, authorization_path, ledger_path=ledger_path
    )
    ledger = budget.SelectiveExploreLedger(
        ledger_path,
        authorization_path=authorization_path,
        now=clock.now,
        host_guard=lambda: True,
    )
    assert authorization["baseline_calls"] == (1 if baseline_unknown else 0)
    return SimpleNamespace(
        ledger=ledger,
        ledger_path=ledger_path,
        authorization_path=authorization_path,
        clock=clock,
    )


def _sdk(handler):
    return OpenAI(
        api_key="offline-test-key",
        base_url=OFFICIAL_BASE,
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False,
        ),
    )


def _response(content='{"ok":true}'):
    return {
        "id": "gen-explore-integration",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 1,
            "total_tokens": 3,
            "cost": 0.001,
        },
    }


def _client(fixture, sdk, *, profile="builder_16384", episode="selective_20260915/provider_v3/test", env_path=None):
    return budget.make_builder_client(
        None,
        env_path,
        ledger=fixture.ledger,
        sdk=sdk,
        episode=episode,
        profile=profile,
        provider_profile="wafer",
        metadata_fetcher=lambda model: provider_metadata(model),
        sleep=fixture.clock.sleep,
        host_guard=lambda: True,
        authorization_path=fixture.authorization_path,
    )


def test_builder_wafer_16384_low_reasoning_is_in_real_sdk_wire_and_request_sha(
    tmp_path, monkeypatch
):
    fixture = _make_fixture(monkeypatch, tmp_path)
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(200, json=_response())

    sdk = _sdk(handler)
    try:
        client = _client(fixture, sdk)
        client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "build"}],
            temperature=0.0,
        )
    finally:
        sdk.close()
    assert len(captured) == 1
    body = captured[0]
    assert body["max_tokens"] == 16384
    assert body["reasoning"] == {"effort": "low"}
    assert body["provider"]["only"] == ["wafer"]
    assert body["provider"]["allow_fallbacks"] is False
    assert body["provider"]["require_parameters"] is True

    extra = {key: body.pop(key) for key in ("provider", "usage", "reasoning")}
    expected_payload = dict(body, extra_body=extra)
    expected_sha = hashlib.sha256(
        budget.canonical(expected_payload).encode("utf-8")
    ).hexdigest()
    without_reasoning = copy.deepcopy(expected_payload)
    without_reasoning["extra_body"].pop("reasoning")
    without_reasoning_sha = hashlib.sha256(
        budget.canonical(without_reasoning).encode("utf-8")
    ).hexdigest()
    with fixture.ledger.connect() as database:
        request_sha, reserved, state = database.execute(
            "SELECT request_sha,reserved_nano,state FROM calls"
        ).fetchone()
    assert request_sha == expected_sha
    assert request_sha != without_reasoning_sha
    assert state == "settled"
    assert reserved == 110_592_000


def test_real_ledger_preserves_unknown_reservation_and_enforces_atomic_20_10_caps(
    tmp_path, monkeypatch
):
    fixture = _make_fixture(monkeypatch, tmp_path, baseline_unknown=True)
    fixture.ledger.reserve(
        "new-call-1",
        "selective_20260915/provider_v3/ledger-test",
        MODEL,
        "sha-1",
        "10",
    )
    before = fixture.ledger.summary()
    assert before["budget_occupied_usd"] == "20"
    assert before["tranche_occupied_usd"] == "10"
    assert before["unresolved_reserved_usd"] == "20"
    with pytest.raises(BudgetStop, match="USD 20|USD 10"):
        fixture.ledger.reserve(
            "new-call-2",
            "selective_20260915/provider_v3/ledger-test",
            MODEL,
            "sha-2",
            "0.001",
        )
    after = fixture.ledger.summary()
    assert after == before
    with fixture.ledger.connect() as database:
        rows = database.execute(
            "SELECT id,state,actual_nano,reserved_nano FROM calls ORDER BY id"
        ).fetchall()
    assert rows == [
        ("baseline/unknown", "uncertain", None, 10_000_000_000),
        ("selective_20260915/new-call-1", "reserved", None, 10_000_000_000),
    ]


def test_second_begin_same_episode_rejects_replay_after_real_receipt(tmp_path, monkeypatch):
    fixture = _make_fixture(monkeypatch, tmp_path)

    def handler(request):
        return httpx.Response(200, json=_response())

    sdk = _sdk(handler)
    try:
        client = _client(fixture, sdk, episode="selective_20260915/provider_v3/replay")
        client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "one"}],
            temperature=0.0,
        )
        with pytest.raises(BudgetStop, match="earlier request|receipt"):
            client.begin_episode("selective_20260915/provider_v3/replay")
    finally:
        sdk.close()


def test_sdk_read_timeout_is_one_physical_attempt_with_unknown_receipt(tmp_path, monkeypatch):
    fixture = _make_fixture(monkeypatch, tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("read timeout", request=request)

    sdk = _sdk(handler)
    try:
        client = _client(
            fixture,
            sdk,
            profile="serving_4096",
            episode="selective_20260915/provider_v3/timeout",
        )
        with pytest.raises(BudgetStop, match="physical request failed|reserve retained"):
            client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": "timeout"}],
                temperature=0.0,
            )
    finally:
        sdk.close()
    assert len(calls) == 1
    with fixture.ledger.connect() as database:
        row = database.execute(
            "SELECT state,actual_nano,reserved_nano FROM calls"
        ).fetchone()
    assert row[0] == "uncertain"
    assert row[1] is None
    assert row[2] == 106_291_200


def test_new_client_without_auth_fails_before_constructing_http_client(tmp_path, monkeypatch):
    fixture = _make_fixture(monkeypatch, tmp_path)
    env_file = tmp_path / "missing-key.env"
    env_file.write_text(
        'OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"\n',
        encoding="utf-8",
    )
    with pytest.raises(BudgetStop, match="OPENROUTER_API_KEY"):
        budget.make_builder_client(
            None,
            env_file,
            ledger=fixture.ledger,
            sdk=None,
            episode="selective_20260915/provider_v3/no-auth",
            profile="builder_16384",
            provider_profile="wafer",
            authorization_path=fixture.authorization_path,
        )
    with fixture.ledger.connect() as database:
        assert database.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
