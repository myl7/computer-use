"""Atomic USD 1 budget for one explicitly prepared diagnostic prefix.

The exploratory tranche already has a shared USD 20 total ceiling and a USD
10 post-authorization ceiling.  This facade adds one narrower, immutable
diagnostic ceiling without changing either existing budget module or its
authorization baseline.  A diagnostic may contain several serving episodes;
the ledger groups them by an exact episode-prefix match in Python while the
SQLite write transaction is held, so SQL wildcard characters cannot broaden
the scope.

``prepare_diagnostic_config`` is the only function that creates the config
artifact.  It never authorizes a tranche and never writes the shared ledger.
Once the artifact exists, a different prefix or path is rejected.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import time
from decimal import Decimal
from pathlib import Path

from . import selective_explore_budget as explore
from .budget_client import BudgetStop, canonical, nano_usd
from .budget_client_v2 import PACE_SECONDS, PacingWait


ROOT = Path(__file__).resolve().parents[2]
SELECTIVE_OUT = (ROOT / "experimental-results/guiexp_android/selective_20260915").resolve()
SHARED_REVISION = (ROOT / "experimental-results/guiexp_android/revision_20260913").resolve()
SHARED_LEDGER_PATH = (SHARED_REVISION / "budget.sqlite3").resolve()
SHARED_RUN_LOCK_PATH = (SHARED_REVISION / "run.lock").resolve()
AUTHORIZATION_PATH = (SELECTIVE_OUT / "budget_authorization_20260915.json").resolve()
DEFAULT_CONFIG_PATH = (SELECTIVE_OUT / "diagnostic_budget_20260915.json").resolve()

NAMESPACE = "selective_20260915"
CALL_PREFIX = NAMESPACE + "/"
CONFIG_SCHEMA = "selective-diagnostic-budget/1"
DIAGNOSTIC_LIMIT_NANO = nano_usd("1")
DIAGNOSTIC_LIMIT_USD = Decimal("1")

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _canonical_prefix(value):
    """Validate a stable receipt prefix below the selective namespace."""
    if not isinstance(value, str) or not value.startswith(CALL_PREFIX):
        raise BudgetStop("Diagnostic prefix must be below selective_20260915/.")
    if value == CALL_PREFIX or value.endswith("/") or "\\" in value or "\x00" in value:
        raise BudgetStop("Diagnostic prefix is not canonical.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or _SEGMENT_RE.fullmatch(part) is None for part in parts):
        raise BudgetStop("Diagnostic prefix contains an unsafe path segment.")
    return value


def _canonical_episode(value, prefix):
    """Require an episode to equal or descend from the frozen prefix."""
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise BudgetStop("Diagnostic episode is not canonical.")
    if value != prefix and not value.startswith(prefix + "/"):
        raise BudgetStop("Episode is outside the frozen diagnostic prefix.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or _SEGMENT_RE.fullmatch(part) is None for part in parts):
        raise BudgetStop("Diagnostic episode contains an unsafe path segment.")
    return value


def _matches_episode(value, prefix):
    return value == prefix or value.startswith(prefix + "/")


def _resolved(value):
    return Path(value).resolve()


def _read_authorization(path):
    """Read the existing authorization through the frozen explorer verifier."""
    body = explore._authorization_body(path)
    if body.get("shared_ledger") != str(SHARED_LEDGER_PATH):
        raise BudgetStop("Diagnostic authorization does not name the shared ledger.")
    if body.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH):
        raise BudgetStop("Diagnostic authorization does not name the shared run lock.")
    return body


def _unsigned_config(body):
    return {key: value for key, value in body.items() if key != "diagnostic_config_sha256"}


def _verify_config_body(body, *, expected_prefix=None, expected_ledger=None, expected_authorization=None):
    if not isinstance(body, dict):
        raise BudgetStop("Diagnostic budget config is not an object.")
    digest = body.get("diagnostic_config_sha256")
    if not isinstance(digest, str) or hashlib.sha256(canonical(_unsigned_config(body)).encode()).hexdigest() != digest:
        raise BudgetStop("Diagnostic budget config hash changed.")
    if body.get("schema") != CONFIG_SCHEMA:
        raise BudgetStop("Unexpected diagnostic budget config schema.")
    prefix = _canonical_prefix(body.get("diagnostic_prefix"))
    if expected_prefix is not None and prefix != _canonical_prefix(expected_prefix):
        raise BudgetStop("A different diagnostic config already exists.")
    limit = body.get("limit_nano")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit != DIAGNOSTIC_LIMIT_NANO:
        raise BudgetStop("Diagnostic USD 1 limit changed.")
    ledger_path = _resolved(body.get("shared_ledger", ""))
    run_lock_path = _resolved(body.get("shared_run_lock", ""))
    authorization_path = _resolved(body.get("authorization_path", ""))
    expected_ledger = _resolved(expected_ledger or SHARED_LEDGER_PATH)
    expected_authorization = _resolved(expected_authorization or AUTHORIZATION_PATH)
    if ledger_path != expected_ledger or ledger_path != SHARED_LEDGER_PATH:
        raise BudgetStop("Diagnostic config must use the shared ledger.")
    if run_lock_path != SHARED_RUN_LOCK_PATH:
        raise BudgetStop("Diagnostic config must use the shared run lock.")
    if authorization_path != expected_authorization:
        raise BudgetStop("Diagnostic config authorization path changed.")
    authorization = _read_authorization(authorization_path)
    if body.get("authorization_sha256") != authorization.get("authorization_sha256"):
        raise BudgetStop("Diagnostic authorization baseline changed.")
    return body


def load_diagnostic_config(
    path=None,
    *,
    expected_prefix=None,
    ledger_path=None,
    authorization_path=None,
):
    """Load and verify one immutable diagnostic config."""
    path = DEFAULT_CONFIG_PATH if path is None else path
    ledger_path = SHARED_LEDGER_PATH if ledger_path is None else ledger_path
    authorization_path = AUTHORIZATION_PATH if authorization_path is None else authorization_path
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise BudgetStop("Diagnostic budget config is missing or invalid.") from None
    return _verify_config_body(
        body,
        expected_prefix=expected_prefix,
        expected_ledger=ledger_path,
        expected_authorization=authorization_path,
    )


def _config_body(prefix, authorization_path):
    prefix = _canonical_prefix(prefix)
    authorization_path = _resolved(authorization_path)
    authorization = _read_authorization(authorization_path)
    body = {
        "schema": CONFIG_SCHEMA,
        "diagnostic_prefix": prefix,
        "limit_nano": DIAGNOSTIC_LIMIT_NANO,
        "shared_ledger": str(SHARED_LEDGER_PATH),
        "shared_run_lock": str(SHARED_RUN_LOCK_PATH),
        "authorization_path": str(authorization_path),
        "authorization_sha256": authorization["authorization_sha256"],
    }
    body["diagnostic_config_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    return body


def prepare_diagnostic_config(
    diagnostic_prefix,
    *,
    path=None,
    ledger_path=None,
    authorization_path=None,
):
    """Create a diagnostic config once, or return its unchanged existing copy."""
    path = DEFAULT_CONFIG_PATH if path is None else path
    ledger_path = SHARED_LEDGER_PATH if ledger_path is None else ledger_path
    authorization_path = AUTHORIZATION_PATH if authorization_path is None else authorization_path
    destination = _resolved(path)
    prefix = _canonical_prefix(diagnostic_prefix)
    ledger_path = _resolved(ledger_path)
    authorization_path = _resolved(authorization_path)
    if ledger_path != SHARED_LEDGER_PATH:
        raise BudgetStop("Diagnostic config must use the shared ledger.")
    # The shared run lock serializes first creation with every paid worker.
    SHARED_RUN_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SHARED_RUN_LOCK_PATH.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another runner owns the shared revision run lock.") from None
        try:
            if destination.exists():
                return load_diagnostic_config(
                    destination,
                    expected_prefix=prefix,
                    ledger_path=ledger_path,
                    authorization_path=authorization_path,
                )
            body = _config_body(prefix, authorization_path)
            # The verifier also checks the exact paths and authorization hash;
            # retain that check immediately before the first write.
            _verify_config_body(
                body,
                expected_prefix=prefix,
                expected_ledger=ledger_path,
                expected_authorization=authorization_path,
            )
            explore.base.atomic_json(destination, body)
            return body
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


prepare = prepare_diagnostic_config
load_config = load_diagnostic_config


def _occupied_row(state, reserved_nano, actual_nano):
    """Count an uncertain/in-flight reservation at its full bound."""
    return (actual_nano or 0) + (reserved_nano if state != "settled" else 0)


class SelectiveDiagnosticLedger(explore.SelectiveExploreLedger):
    """Shared exploratory ledger with one atomic USD 1 diagnostic ceiling."""

    def __init__(
        self,
        path=None,
        *,
        config_path=None,
        now=time.time,
        host_guard=None,
    ):
        path = SHARED_LEDGER_PATH if path is None else path
        config_path = DEFAULT_CONFIG_PATH if config_path is None else config_path
        resolved = _resolved(path)
        if resolved != SHARED_LEDGER_PATH:
            raise BudgetStop("Diagnostic ledger must use the shared revision ledger.")
        self.config_path = _resolved(config_path)
        self.config = load_diagnostic_config(self.config_path, ledger_path=resolved)
        if self.config["shared_ledger"] != str(resolved):
            raise BudgetStop("Diagnostic config ledger path changed.")
        self.diagnostic_prefix = _canonical_prefix(self.config["diagnostic_prefix"])
        super().__init__(
            resolved,
            authorization_path=self.config["authorization_path"],
            now=now,
            host_guard=host_guard,
        )

    def _diagnostic_occupied(self, db):
        rows = db.execute(
            "SELECT episode,state,reserved_nano,actual_nano FROM calls"
        ).fetchall()
        return sum(
            _occupied_row(state, reserved, actual)
            for episode, state, reserved, actual in rows
            if _matches_episode(str(episode), self.diagnostic_prefix)
        )

    def _diagnostic_rows(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT episode,state,reserved_nano,actual_nano FROM calls"
            ).fetchall()
        return [
            row for row in rows if _matches_episode(str(row[0]), self.diagnostic_prefix)
        ]

    def has_episode(self, episode):
        """Reject an out-of-scope episode before the client records transport."""
        episode = _canonical_episode(episode, self.diagnostic_prefix)
        return super().has_episode(episode)

    def record_transport(self, logical_id, episode, report):
        """Apply the prefix guard before any transport metadata is persisted."""
        episode = _canonical_episode(episode, self.diagnostic_prefix)
        return super().record_transport(logical_id, episode, report)

    def reserve(self, call_id, episode, model, request_sha, reservation):
        """Reserve atomically under global, tranche, and diagnostic bounds."""
        self._guard()
        episode = _canonical_episode(episode, self.diagnostic_prefix)
        if self.paid_failures_blocked():
            raise BudgetStop("Three consecutive unresolved physical failures stopped diagnostic paid work.")
        amount = nano_usd(reservation)
        safe_call_id = explore._canonical_call_id(call_id)
        if "\\" in safe_call_id or "\x00" in safe_call_id or any(
            part in {"", ".", ".."} for part in safe_call_id.split("/")
        ):
            raise BudgetStop("Diagnostic receipt ID is not canonical.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (safe_call_id,)).fetchone():
                raise BudgetStop("Diagnostic request ID already exists; no resend.")
            if db.execute("SELECT 1 FROM calls WHERE state='overrun'").fetchone():
                raise BudgetStop("A provider exceeded its diagnostic bound; paid work remains stopped.")
            total, tranche = self._authorization_check(db)
            if total + amount > explore.TOTAL_LIMIT_NANO:
                raise BudgetStop("Exploratory total USD 20 occupied ceiling would be exceeded.")
            if tranche + amount > explore.TRANCHE_LIMIT_NANO:
                raise BudgetStop("Exploratory new-tranche USD 10 occupied ceiling would be exceeded.")
            diagnostic = self._diagnostic_occupied(db)
            if diagnostic + amount > DIAGNOSTIC_LIMIT_NANO:
                raise BudgetStop("Diagnostic USD 1 occupied ceiling would be exceeded.")
            now = self.now()
            next_send = db.execute("SELECT next_send FROM pacing_v2 WHERE id=1").fetchone()[0]
            if now < next_send:
                raise PacingWait(next_send - now)
            db.execute(
                "INSERT INTO calls VALUES (?,?,?,?,?,NULL,'reserved',NULL,NULL,?)",
                (safe_call_id, episode, model, request_sha, amount, now),
            )
            db.execute("UPDATE pacing_v2 SET next_send=? WHERE id=1", (now + PACE_SECONDS + 1,))

    def summary(self):
        result = super().summary()
        rows = self._diagnostic_rows()
        diagnostic = sum(_occupied_row(row[1], row[2], row[3]) for row in rows)
        diagnostic_actual = sum(row[3] or 0 for row in rows)
        diagnostic_unresolved = sum(row[2] for row in rows if row[1] != "settled")
        result.update(
            diagnostic_prefix=self.diagnostic_prefix,
            diagnostic_limit_nano=DIAGNOSTIC_LIMIT_NANO,
            diagnostic_limit_usd=str(DIAGNOSTIC_LIMIT_USD),
            diagnostic_occupied_usd=str(Decimal(diagnostic) / Decimal(1000000000)),
            diagnostic_actual_usd=str(Decimal(diagnostic_actual) / Decimal(1000000000)),
            diagnostic_unresolved_reserved_usd=str(Decimal(diagnostic_unresolved) / Decimal(1000000000)),
            diagnostic_available_usd=str(
                max(Decimal("0"), DIAGNOSTIC_LIMIT_USD - Decimal(diagnostic) / Decimal(1000000000))
            ),
            diagnostic_calls=len(rows),
        )
        result["blocked"] = bool(result.get("blocked") or diagnostic >= DIAGNOSTIC_LIMIT_NANO)
        return result


def make_diagnostic_ledger(
    config_path=None,
    *,
    now=time.time,
    host_guard=None,
):
    """Build the shared ledger facade from a verified diagnostic config."""
    config_path = DEFAULT_CONFIG_PATH if config_path is None else config_path
    config = load_diagnostic_config(config_path)
    return SelectiveDiagnosticLedger(
        config["shared_ledger"],
        config_path=config_path,
        now=now,
        host_guard=host_guard,
    )


make_ledger = make_diagnostic_ledger
ledger = make_diagnostic_ledger


__all__ = [
    "AUTHORIZATION_PATH",
    "CALL_PREFIX",
    "CONFIG_SCHEMA",
    "DEFAULT_CONFIG_PATH",
    "DIAGNOSTIC_LIMIT_NANO",
    "DIAGNOSTIC_LIMIT_USD",
    "NAMESPACE",
    "SELECTIVE_OUT",
    "SHARED_LEDGER_PATH",
    "SHARED_RUN_LOCK_PATH",
    "SelectiveDiagnosticLedger",
    "ledger",
    "load_config",
    "load_diagnostic_config",
    "make_diagnostic_ledger",
    "make_ledger",
    "prepare",
    "prepare_diagnostic_config",
]
