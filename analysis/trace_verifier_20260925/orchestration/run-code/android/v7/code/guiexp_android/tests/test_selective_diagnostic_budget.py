"""Offline tests for the atomic per-diagnostic USD 1 budget facade."""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from guiexp_android import selective_budget as base
from guiexp_android import selective_diagnostic_budget as diag
from guiexp_android import selective_explore_budget as explore
from guiexp_android.budget_client import BudgetStop
from guiexp_android.budget_client_v9 import BudgetLedgerV9


MODEL = explore.MODEL


class Clock:
    def __init__(self):
        self.value = 1000.0

    def now(self):
        return self.value


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    ledger_path = (tmp_path / "revision_20260913" / "budget.sqlite3").resolve()
    lock_path = (tmp_path / "revision_20260913" / "run.lock").resolve()
    auth_request = (tmp_path / "selective_20260915" / "budget_authorization_request.json").resolve()
    auth_path = (tmp_path / "selective_20260915" / "budget_authorization.json").resolve()
    config_path = (tmp_path / "selective_20260915" / "diagnostic_budget.json").resolve()
    ledger_path.parent.mkdir(parents=True)
    auth_request.parent.mkdir(parents=True)
    lock_path.touch()
    base_ledger = BudgetLedgerV9(ledger_path)

    # The production modules keep absolute shared paths.  These patches are
    # confined to this fixture and mirror the existing selective budget tests.
    for module in (explore, diag):
        monkeypatch.setattr(module, "SHARED_LEDGER_PATH", ledger_path)
        monkeypatch.setattr(module, "SHARED_RUN_LOCK_PATH", lock_path)
    monkeypatch.setattr(explore, "AUTHORIZATION_PATH", auth_path)
    monkeypatch.setattr(diag, "AUTHORIZATION_PATH", auth_path)
    monkeypatch.setattr(diag, "DEFAULT_CONFIG_PATH", config_path)

    return SimpleNamespace(
        ledger_path=ledger_path,
        lock_path=lock_path,
        request_path=auth_request,
        auth_path=auth_path,
        config_path=config_path,
        ledger=base_ledger,
        prefix=f"{diag.CALL_PREFIX}diagnostic_v5",
    )


def _request_body(path):
    return {
        "record_type": "explicit_user_budget_extension_request",
        "additional_usd": "10",
        "total_occupied_ceiling_usd": "20",
        "new_tranche_occupied_ceiling_usd": "10",
        "shared_ledger": str(path),
        "preserve_all_unknown_fees": True,
    }


def prepare(sandbox):
    sandbox.request_path.write_text(json.dumps(_request_body(sandbox.ledger_path)))
    request = json.loads(sandbox.request_path.read_text())
    request.update(explore._read_baseline(sandbox.ledger_path))
    sandbox.request_path.write_text(json.dumps(request) + "\n")
    auth = explore.authorize_tranche(
        sandbox.request_path,
        sandbox.auth_path,
        ledger_path=sandbox.ledger_path,
    )
    config = diag.prepare_diagnostic_config(
        sandbox.prefix,
        path=sandbox.config_path,
        ledger_path=sandbox.ledger_path,
        authorization_path=sandbox.auth_path,
    )
    return auth, config


def diagnostic_ledger(sandbox, *, clock=None):
    return diag.SelectiveDiagnosticLedger(
        sandbox.ledger_path,
        config_path=sandbox.config_path,
        now=(clock or Clock()).now,
        host_guard=lambda: True,
    )


def permit_next(ledger):
    with ledger.connect() as db:
        db.execute("UPDATE pacing_v2 SET next_send=0 WHERE id=1")


def insert_call(ledger, call_id, episode, state, reserved, actual):
    with ledger.connect() as db:
        db.execute(
            "INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?)",
            (call_id, episode, MODEL, "sha", 1, reserved, state, None, None, 1000),
        )
        db.execute(
            "UPDATE calls SET actual_nano=? WHERE id=?",
            (actual, call_id),
        )


def test_prepare_creates_immutable_config_and_does_not_refresh_auth_or_ledger(sandbox):
    auth, first = prepare(sandbox)
    assert first["schema"] == diag.CONFIG_SCHEMA
    assert first["diagnostic_prefix"] == sandbox.prefix
    assert first["limit_nano"] == 1_000_000_000
    assert first["authorization_sha256"] == auth["authorization_sha256"]
    assert first["diagnostic_config_sha256"] == diag.hashlib.sha256(
        diag.canonical(diag._unsigned_config(first)).encode()
    ).hexdigest()
    before = sandbox.config_path.read_text()
    sandbox.ledger.reserve("later", "outside", MODEL, "sha", "0.1")
    second = diag.prepare_diagnostic_config(
        sandbox.prefix,
        path=sandbox.config_path,
        ledger_path=sandbox.ledger_path,
        authorization_path=sandbox.auth_path,
    )
    assert second == first
    assert sandbox.config_path.read_text() == before
    with sandbox.ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1


def test_different_episodes_under_prefix_share_one_diagnostic_cap(sandbox):
    prepare(sandbox)
    ledger = diagnostic_ledger(sandbox)
    ledger.reserve("build-call", f"{sandbox.prefix}/build", MODEL, "a", "0.4")
    permit_next(ledger)
    ledger.reserve("acquisition-call", f"{sandbox.prefix}/acquisition", MODEL, "b", "0.4")
    permit_next(ledger)
    with pytest.raises(BudgetStop, match="Diagnostic USD 1"):
        ledger.reserve("third-call", f"{sandbox.prefix}/build", MODEL, "c", "0.3")
    summary = ledger.summary()
    assert summary["diagnostic_occupied_usd"] == "0.8"
    assert summary["diagnostic_calls"] == 2


def test_uncertain_and_inflight_reservations_are_not_zero(sandbox):
    prepare(sandbox)
    ledger = diagnostic_ledger(sandbox)
    ledger.reserve("uncertain-call", f"{sandbox.prefix}/build", MODEL, "a", "0.6")
    ledger.uncertain("uncertain-call", "MissingBillEvidence")
    permit_next(ledger)
    with pytest.raises(BudgetStop, match="Diagnostic USD 1"):
        ledger.reserve("new-call", f"{sandbox.prefix}/acquisition", MODEL, "b", "0.5")
    summary = ledger.summary()
    assert summary["diagnostic_actual_usd"] == "0"
    assert summary["diagnostic_unresolved_reserved_usd"] == "0.6"
    assert summary["diagnostic_occupied_usd"] == "0.6"


def test_episode_outside_prefix_or_with_path_escape_is_refused(sandbox):
    prepare(sandbox)
    ledger = diagnostic_ledger(sandbox)
    with pytest.raises(BudgetStop, match="outside|unsafe"):
        ledger.reserve("outside-call", f"{diag.CALL_PREFIX}other/build", MODEL, "a", "0.1")
    with pytest.raises(BudgetStop, match="outside|unsafe"):
        ledger.reserve("escape-call", f"{sandbox.prefix}/../escape", MODEL, "b", "0.1")
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0


def test_outside_episode_cannot_leave_transport_metadata(sandbox):
    prepare(sandbox)
    ledger = diagnostic_ledger(sandbox)
    with pytest.raises(BudgetStop, match="outside|unsafe"):
        ledger.record_transport(
            "logical-outside",
            f"{diag.CALL_PREFIX}other/build",
            {"within_local_limit": True},
        )
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM transport_metrics_v6").fetchone()[0] == 0


def test_duplicate_receipt_is_rejected_without_resend(sandbox):
    prepare(sandbox)
    ledger = diagnostic_ledger(sandbox)
    episode = f"{sandbox.prefix}/build"
    ledger.reserve("same-call", episode, MODEL, "a", "0.2")
    permit_next(ledger)
    with pytest.raises(BudgetStop, match="already exists|resend"):
        ledger.reserve("same-call", episode, MODEL, "a", "0.2")
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1


def test_global_twenty_ceiling_still_applies(sandbox):
    # This row is part of the authorization baseline, so it consumes global
    # room but not the post-baseline tranche.
    insert_call(sandbox.ledger, "baseline", f"{diag.CALL_PREFIX}baseline", "settled", 1, 12_000_000_000)
    prepare(sandbox)
    # A post-baseline row outside this diagnostic fills global but also keeps
    # the diagnostic and its own per-tranche room available.
    insert_call(sandbox.ledger, "other", f"{diag.CALL_PREFIX}other", "settled", 1, 7_700_000_000)
    ledger = diagnostic_ledger(sandbox)
    permit_next(ledger)
    with pytest.raises(BudgetStop, match="USD 20"):
        ledger.reserve("new", f"{sandbox.prefix}/build", MODEL, "a", "0.4")
    assert ledger.summary()["diagnostic_occupied_usd"] == "0"


def test_new_tranche_ten_ceiling_still_applies(sandbox):
    prepare(sandbox)
    insert_call(sandbox.ledger, "other", f"{diag.CALL_PREFIX}other", "settled", 1, 9_800_000_000)
    ledger = diagnostic_ledger(sandbox)
    permit_next(ledger)
    with pytest.raises(BudgetStop, match="tranche"):
        ledger.reserve("new", f"{sandbox.prefix}/build", MODEL, "a", "0.4")
    assert ledger.summary()["diagnostic_occupied_usd"] == "0"


def test_concurrent_reservations_serialize_diagnostic_cap(sandbox, monkeypatch):
    prepare(sandbox)
    monkeypatch.setattr(diag, "PACE_SECONDS", -1)
    ledgers = [diagnostic_ledger(sandbox, clock=Clock()) for _ in range(2)]
    episodes = [f"{sandbox.prefix}/build", f"{sandbox.prefix}/acquisition"]

    def reserve(index):
        try:
            ledgers[index].reserve(
                f"parallel-{index}", episodes[index], MODEL, str(index), "0.6"
            )
            return "reserved"
        except BudgetStop as exc:
            return str(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(result == "reserved" for result in results) == 1
    with ledgers[0].connect() as db:
        rows = db.execute("SELECT COUNT(*),SUM(reserved_nano) FROM calls").fetchone()
    assert rows == (1, 600_000_000)


def test_config_tamper_is_rejected_without_replacement(sandbox):
    prepare(sandbox)
    original = sandbox.config_path.read_text()
    tampered = json.loads(original)
    tampered["limit_nano"] = 2_000_000_000
    sandbox.config_path.write_text(json.dumps(tampered) + "\n")
    with pytest.raises(BudgetStop, match="hash|limit"):
        diag.load_diagnostic_config(
            sandbox.config_path,
            ledger_path=sandbox.ledger_path,
            authorization_path=sandbox.auth_path,
        )
    with pytest.raises(BudgetStop):
        diag.prepare_diagnostic_config(
            sandbox.prefix,
            path=sandbox.config_path,
            ledger_path=sandbox.ledger_path,
            authorization_path=sandbox.auth_path,
        )
    assert sandbox.config_path.read_text() != original


def test_existing_valid_different_config_is_refused_and_not_reset(sandbox):
    prepare(sandbox)
    original = sandbox.config_path.read_text()
    different = diag._config_body(f"{diag.CALL_PREFIX}different", sandbox.auth_path)
    sandbox.config_path.write_text(json.dumps(different) + "\n")
    with pytest.raises(BudgetStop, match="different"):
        diag.prepare_diagnostic_config(
            sandbox.prefix,
            path=sandbox.config_path,
            ledger_path=sandbox.ledger_path,
            authorization_path=sandbox.auth_path,
        )
    assert json.loads(sandbox.config_path.read_text())["diagnostic_prefix"] == f"{diag.CALL_PREFIX}different"
    assert json.loads(original)["diagnostic_prefix"] == sandbox.prefix
