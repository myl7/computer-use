"""Offline tests for the selective pilot budget and host guards."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import fcntl
import pytest

from guiexp_android import selective_budget as sb
from guiexp_android.budget_client import BudgetStop, OFFICIAL_BASE
from guiexp_android.budget_client_v9 import BudgetLedgerV9, validate_metadata


MODEL = "z-ai/glm-5.3-flash"


def metadata(model=MODEL):
    policy = sb.MODEL_LOCKS[model]
    return {
        "data": {
            "id": model,
            "architecture": {"output_modalities": ["text"]},
            "endpoints": [{
                "tag": policy["provider"],
                "status": 0,
                "context_length": 1048576,
                "max_completion_tokens": 131072,
                "supported_parameters": ["max_tokens", "temperature", "reasoning", "reasoning_effort"],
                "pricing": {
                    "prompt": str(float(policy["prompt_per_m"]) / 1000000),
                    "completion": str(float(policy["completion_per_m"]) / 1000000),
                },
            }],
        }
    }


class Response:
    def __init__(self, cost="0.001"):
        self.raw = {
            "id": "gen-selective-test",
            "choices": [{"message": {"role": "assistant", "content": "action"}, "finish_reason": "stop"}],
            "usage": {"cost": cost},
        }

    def model_dump(self, mode="json"):
        return self.raw


class SDK:
    max_retries = 0
    base_url = OFFICIAL_BASE

    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if self.outcomes else Response()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        return None


class Clock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def lock():
    return validate_metadata(MODEL, metadata())


def client_for(tmp_path, outcomes=(), host_guard=lambda: True):
    clock = Clock()
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", now=clock.now, host_guard=host_guard)
    sdk = SDK(outcomes)
    client = sb.make_client(
        {MODEL: lock()},
        ledger=ledger,
        sdk=sdk,
        host_guard=host_guard,
        metadata_fetcher=metadata,
        sleep=clock.sleep,
    )
    client.begin_episode("selective_20260915/episode")
    return client, ledger, sdk


class APIConnectionError(Exception):
    pass


class APITimeoutError(Exception):
    pass


def ask(client):
    return client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "goal"}],
        temperature=0,
    )


def _runner(outputs):
    def run(command, **kwargs):
        key = tuple(command)
        value = outputs.get(key, "")
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    return run


def good_ioreg():
    return """+-o IOPMrootDomain {
  \"IOHibernateState\" = <00000000>
  \"IOPMUserTriggeredFullWake\" = Yes
  \"Wake Type\" = \"UserActivity Assertion\"
  \"AppleClamshellState\" = No
}"""


def good_power_log(event="Wake"):
    return f"2026-09-15 00:17:08 +0800 {event:<18} Wake from Deep Idle\n"


def good_systemstate():
    return "Current System Capabilities are: CPU Graphics Audio Network\nCurrent Power State: 4\n"


def test_host_guard_requires_lid_open_and_full_wake_without_log_scan():
    runner = _runner({
        ("ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"): good_ioreg(),
        ("pmset", "-g"): "System-wide power settings:\n sleep 1\n",
        ("pmset", "-g", "systemstate"): good_systemstate(),
        ("pmset", "-g", "log"): good_power_log(),
    })
    state = sb.read_host_state(runner=runner)
    assert state["ready"] and state["lid_open"] and state["normal_full_wake"]

    darkwake = good_ioreg().replace("UserActivity Assertion", "DarkWake")
    state = sb.read_host_state(runner=_runner({
        ("ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"): darkwake,
        ("pmset", "-g"): "System-wide power settings:\n",
        ("pmset", "-g", "systemstate"): good_systemstate(),
        ("pmset", "-g", "log"): good_power_log("DarkWake"),
    }))
    assert not state["ready"] and "DarkWake" in state["reason"]

    no_marker = good_ioreg().replace('  "IOPMUserTriggeredFullWake" = Yes\n', "")
    state = sb.read_host_state(runner=_runner({
        ("ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"): no_marker,
        ("pmset", "-g"): "System-wide power settings:\n",
        ("pmset", "-g", "systemstate"): good_systemstate(),
        ("pmset", "-g", "log"): "",
    }))
    assert not state["ready"] and "power event unavailable" in state["reason"]


def test_sleeping_host_stops_before_reservation_and_sdk(tmp_path):
    client, ledger, sdk = client_for(tmp_path, host_guard=lambda: {"ready": False})
    with pytest.raises(BudgetStop):
        ask(client)
    assert sdk.calls == []
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_host_transition_after_reservation_stops_sdk_and_retains_reserve(tmp_path):
    checks = iter((True, False))
    client, ledger, sdk = client_for(tmp_path, host_guard=lambda: next(checks))
    with pytest.raises(BudgetStop):
        ask(client)
    assert sdk.calls == []
    with ledger.connect() as db:
        state, reserved = db.execute("SELECT state,reserved_nano FROM calls").fetchone()
    assert state == "uncertain" and reserved > 0


def test_receipts_are_prefixed_but_episode_identity_stays_canonical(tmp_path):
    client, ledger, sdk = client_for(tmp_path)
    ask(client)
    with ledger.connect() as db:
        call_id, episode = db.execute("SELECT id,episode FROM calls").fetchone()
    assert call_id.startswith("selective_20260915/") and episode == "selective_20260915/episode"
    with pytest.raises(BudgetStop):
        client.begin_episode("selective_20260915/episode")


def test_v9_two_attempt_retry_keeps_prefixed_receipts(tmp_path):
    client, ledger, sdk = client_for(tmp_path, (APIConnectionError("opaque"), Response()))
    ask(client)
    assert len(sdk.calls) == 2
    with ledger.connect() as db:
        rows = db.execute("SELECT id,state,request_sha FROM calls ORDER BY created").fetchall()
    assert len(rows) == 2
    assert all(row[0].startswith("selective_20260915/v9/episode/") for row in rows)
    assert rows[0][1] == "uncertain" and rows[1][1] == "settled"
    assert rows[0][2] == rows[1][2]
    assert ledger.consecutive_unknown_failures() == 0


@pytest.mark.parametrize("profile,expected_max,expected_reservation", [
    ("builder_8192", 8192, "0.09682944"),
    ("builder_16384", 16384, "0.09928704"),
])
def test_builder_profile_adds_reasoning_before_hash_and_reserve(
    tmp_path, profile, expected_max, expected_reservation
):
    _serving_client, ledger, sdk = client_for(tmp_path, host_guard=lambda: True)
    client = sb.make_client(
        {MODEL: lock()},
        ledger=ledger,
        sdk=sdk,
        host_guard=lambda: True,
        metadata_fetcher=metadata,
        sleep=_serving_client.sleep,
        profile=profile,
    )
    episode = "selective_20260915/builder/" + profile
    client.begin_episode(episode)
    ask(client)
    request = sdk.calls[0]
    assert request["max_tokens"] == expected_max
    assert request["extra_body"]["reasoning"] == {"effort": "low"}
    provider = request["extra_body"]["provider"]
    assert provider["only"] == ["relace"] and provider["allow_fallbacks"] is False
    assert provider["require_parameters"] is True
    with ledger.connect() as db:
        call_id, saved_episode, request_sha, reserved = db.execute(
            "SELECT id,episode,request_sha,reserved_nano FROM calls"
        ).fetchone()
    assert call_id.startswith("selective_20260915/builder/")
    assert saved_episode == episode
    assert request_sha == sb.hashlib.sha256(sb.canonical(request).encode()).hexdigest()
    assert str(reserved / 1_000_000_000) == expected_reservation


def test_builder_real_openai_sdk_merges_reasoning_into_wire_json(tmp_path):
    import httpx
    from openai import OpenAI

    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "gen-wire-test",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "action"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "cost": 0.001,
                },
            },
        )

    sdk = OpenAI(
        api_key="offline-test-key",
        base_url=OFFICIAL_BASE,
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )
    clock = Clock()
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", now=clock.now, host_guard=lambda: True)
    client = sb.make_client(
        {MODEL: lock()},
        ledger=ledger,
        sdk=sdk,
        host_guard=lambda: True,
        metadata_fetcher=metadata,
        sleep=clock.sleep,
        profile="builder_16384",
    )
    client.begin_episode("selective_20260915/builder/real-sdk")
    ask(client)
    assert len(captured) == 1
    body = captured[0]
    assert body["max_tokens"] == 16384
    assert body["reasoning"] == {"effort": "low"}
    assert body["provider"]["require_parameters"] is True
    sdk.close()


def test_serving_profile_remains_4096(tmp_path):
    assert sb.MODEL_LOCKS[MODEL]["max_tokens"] == 4096
    assert "reasoning" not in sb.MODEL_LOCKS[MODEL]
    assert sb.builder_model_locks(8192)[MODEL]["max_tokens"] == 8192


def test_builder_metadata_rejects_endpoint_below_selected_cap(tmp_path):
    serving_client, ledger, sdk = client_for(tmp_path, host_guard=lambda: True)
    bad = metadata()
    bad["data"]["endpoints"][0]["max_completion_tokens"] = 8192
    client = sb.make_client(
        {MODEL: lock()},
        ledger=ledger,
        sdk=sdk,
        host_guard=lambda: True,
        metadata_fetcher=lambda _model: bad,
        sleep=serving_client.sleep,
        profile="builder_16384",
    )
    client.begin_episode("selective_20260915/builder/bad-cap")
    with pytest.raises(BudgetStop):
        ask(client)
    assert sdk.calls == []
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_validate_builder_profile_checks_harness_schema_and_metadata():
    profile = {
        "name": "selective_compiler_v2_builder",
        "model": MODEL,
        "provider": "relace",
        "reasoning_effort": "low",
        "max_tokens": 16384,
    }
    validated = sb.validate_builder_profile(
        profile,
        {MODEL: lock()},
        metadata_fetcher=metadata,
    )
    assert validated["name"] == profile["name"]
    assert validated["max_tokens"] == 16384
    assert validated["reasoning"] == {"effort": "low"}
    assert validated["model_locks"][MODEL]["reservation_usd"] == "0.09928704"


def test_three_unresolved_physical_failures_stop_later_paid_work(tmp_path):
    outcomes = [APIConnectionError(), APITimeoutError(), APIConnectionError()]
    client, ledger, sdk = client_for(tmp_path, outcomes)
    with pytest.raises(BudgetStop):
        ask(client)
    with pytest.raises(BudgetStop):
        ask(client)
    assert ledger.consecutive_unknown_failures() == 3
    before = len(sdk.calls)
    with pytest.raises(BudgetStop):
        ask(client)
    assert len(sdk.calls) == before
    assert ledger.summary()["paid_failures_blocked"] is True


def test_repeated_error_handler_for_one_call_is_deduplicated(tmp_path):
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", host_guard=lambda: True)
    ledger.reserve("call", "selective_20260915/episode", MODEL, "sha", lock()["reservation_usd"])
    error = APIConnectionError()
    ledger.record_error("call", error)
    ledger.record_error("call", error)
    assert ledger.consecutive_unknown_failures() == 1


def test_manifest_locks_budget_and_relative_runtime(tmp_path):
    ledger = sb.SelectiveLedger(tmp_path / "budget.sqlite3", host_guard=lambda: True)
    assert ledger.summary()["limit_usd"] == "10"
    path = tmp_path / "manifest.json"
    frozen = sb.freeze_manifest({MODEL: lock()}, path)
    loaded = sb.load_manifest(path)
    assert loaded == frozen
    assert frozen["budget"] == {"shared_ledger": str(sb.SHARED_LEDGER_PATH), "limit_usd": "10"}
    assert frozen["runtime"]["python"] == "../.venv-android/bin/python"


def test_builder_manifest_freezes_both_profiles_separately(tmp_path):
    path = tmp_path / "builder_manifest.json"
    frozen = sb.freeze_builder_manifest({MODEL: lock()}, path)
    loaded = sb.load_builder_manifest(path)
    assert loaded == frozen
    assert set(frozen["profiles"]) == {"builder_8192", "builder_16384"}
    assert frozen["profiles"]["builder_8192"]["model_locks"][MODEL]["max_tokens"] == 8192
    assert frozen["profiles"]["builder_16384"]["model_locks"][MODEL]["max_tokens"] == 16384


def test_exclusive_run_uses_old_lock_and_new_identity_record(tmp_path, monkeypatch):
    old_lock = tmp_path / "revision_20260913" / "run.lock"
    identity_path = tmp_path / "selective_20260915" / "run_identity.json"
    monkeypatch.setattr(sb, "SHARED_RUN_LOCK_PATH", old_lock)
    monkeypatch.setattr(sb, "PID_RECORD_PATH", identity_path)
    with sb.exclusive_run() as identity:
        assert identity["namespace"] == "selective_20260915"
        assert json.loads(identity_path.read_text())["status"] == "active"
    assert json.loads(identity_path.read_text())["status"] == "ended"

    held = old_lock.open("a+")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(BudgetStop):
            with sb.exclusive_run():
                pass
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
