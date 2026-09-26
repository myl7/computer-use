"""Version 2: carry uncertain bills at their full bound and pace new calls.

Original receipt rows remain intact. Uncertainty is never recorded as a zero
bill. SDK retries and resubmission of any prior episode remain prohibited.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

from .budget_client import (
    BudgetClient, BudgetLedger, BudgetStop, MAX_USD, NANO, OFFICIAL_BASE,
    canonical, credentials, fetch_metadata, nano_usd, validate_metadata,
)

PACE_SECONDS = 10.0


class PacingWait(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


def safe_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) else None


def error_metadata(exc, now):
    """Explicit allowlist; never serialize raw error bodies or HTTP headers."""
    status = getattr(exc, "status_code", None)
    status = status if isinstance(status, int) and 100 <= status <= 599 else None
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw_retry = headers.get("retry-after") or headers.get("Retry-After")
    retry_until = None
    retry_text = None
    if raw_retry is not None:
        try:
            delay = float(raw_retry)
            if 0 <= delay < float("inf"):
                retry_until = now + delay
                retry_text = str(delay)
        except (TypeError, ValueError, OverflowError):
            try:
                date = parsedate_to_datetime(str(raw_retry))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                retry_until = max(now, date.timestamp())
                retry_text = date.isoformat()
            except (TypeError, ValueError, OverflowError):
                pass
    if status == 429 and retry_until is None:
        retry_until = now + 60  # fallback cooldown, not an inferred server reset
    body = getattr(exc, "body", None)
    body = body if isinstance(body, dict) else {}
    nested = body.get("error") or {}
    nested = nested if isinstance(nested, dict) else {}
    meta = nested.get("metadata") or {}
    meta = meta if isinstance(meta, dict) else {}
    return {
        "status_code": status, "retry_after": retry_text,
        "retry_not_before": retry_until,
        "request_id": safe_id(getattr(exc, "request_id", None)) or safe_id(headers.get("x-request-id")),
        "provider_request_id": safe_id(meta.get("provider_request_id")),
        "generation_id": safe_id(body.get("id")) or safe_id(meta.get("generation_id")),
        "error_type": type(exc).__name__,
    }


class BudgetLedgerV2(BudgetLedger):
    def __init__(self, path, now=time.time):
        super().__init__(path)
        self.now = now
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pacing_v2 (id INTEGER PRIMARY KEY CHECK(id=1), next_send REAL NOT NULL)")
            db.execute("INSERT OR IGNORE INTO pacing_v2 VALUES (1,0)")
            db.execute("CREATE TABLE IF NOT EXISTS error_metadata_v2 (call_id TEXT PRIMARY KEY, safe_json TEXT NOT NULL)")

    def summary(self):
        base = super().summary()
        occupied = Decimal(base["actual_usd"]) + Decimal(base["unresolved_reserved_usd"])
        with self.connect() as db:
            overrun = bool(db.execute("SELECT 1 FROM calls WHERE state='overrun'").fetchone())
            next_send = db.execute("SELECT next_send FROM pacing_v2 WHERE id=1").fetchone()[0]
        base.update(policy="v2_unknowns_retained_at_full_reservation", budget_occupied_usd=str(occupied),
                    available_usd=str(max(Decimal(0), MAX_USD - occupied)),
                    blocked=overrun or occupied >= MAX_USD, next_send_not_before=next_send)
        return base

    def has_episode(self, episode):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM calls WHERE episode=?", (episode,)).fetchone())

    def reserve(self, call_id, episode, model, request_sha, reservation):
        amount = nano_usd(reservation)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
                raise BudgetStop("Request id already exists; no resend.")
            if db.execute("SELECT 1 FROM calls WHERE state='overrun'").fetchone():
                raise BudgetStop("A provider exceeded its bound; paid work remains stopped.")
            occupied = db.execute("SELECT COALESCE(SUM(COALESCE(actual_nano,0) + CASE WHEN state!='settled' THEN reserved_nano ELSE 0 END),0) FROM calls").fetchone()[0]
            if occupied + amount > nano_usd(MAX_USD):
                raise BudgetStop("USD 10 ceiling includes every unresolved request reservation.")
            now = self.now()
            next_send = db.execute("SELECT next_send FROM pacing_v2 WHERE id=1").fetchone()[0]
            if now < next_send:
                raise PacingWait(next_send - now)
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,NULL,'reserved',NULL,NULL,?)", (call_id, episode, model, request_sha, amount, now))
            # A crash before a response still leaves a global cooldown. In the
            # normal path finish_pacing starts the 10 seconds after completion.
            db.execute("UPDATE pacing_v2 SET next_send=? WHERE id=1", (now + PACE_SECONDS + 1,))

    def finish_pacing(self, retry_not_before=None):
        not_before = max(self.now() + PACE_SECONDS, retry_not_before or 0)
        with self.connect() as db:
            db.execute("UPDATE pacing_v2 SET next_send=MAX(next_send,?) WHERE id=1", (not_before,))

    def record_error(self, call_id, exc):
        metadata = error_metadata(exc, self.now())
        self.uncertain(call_id, type(exc).__name__)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO error_metadata_v2 VALUES (?,?)", (call_id, canonical(metadata)))
        self.finish_pacing(metadata["retry_not_before"])


class BudgetClientV2(BudgetClient):
    def __init__(self, ledger, model_locks, sdk, metadata_fetcher=fetch_metadata,
                 sleep=time.sleep):
        super().__init__(ledger, model_locks, sdk, metadata_fetcher)
        self.sleep = sleep

    def begin_episode(self, episode):
        if self.ledger.has_episode(episode):
            raise BudgetStop("An earlier request belongs to this episode; it will not be rerun.")
        super().begin_episode(episode)

    def create(self, **kwargs):
        if not self.episode:
            raise BudgetStop("A durable episode id is required.")
        if set(kwargs) - {"model", "messages", "temperature"}:
            raise BudgetStop("Unapproved request options.")
        model = kwargs.get("model")
        if model not in self.model_locks:
            raise BudgetStop("Model was not prepared.")
        lock = self.model_locks[model]
        # Metadata is refreshed per request in v2. This GET has no API key.
        validate_metadata(model, self.metadata_fetcher(model), expected=lock)
        self.sequence += 1
        call_id = f"v2/{self.episode}/call-{self.sequence:04d}"
        payload = dict(kwargs, max_tokens=lock["max_tokens"], stream=False, extra_body={
            "provider": {"only": [lock["provider"]], "allow_fallbacks": False,
                         "require_parameters": True,
                         "max_price": {"prompt": float(lock["prompt_per_m"]), "completion": float(lock["completion_per_m"]), "request": 0}},
            "usage": {"include": True},
        })
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        while True:
            try:
                self.ledger.reserve(call_id, self.episode, model, request_sha, lock["reservation_usd"])
                break
            except PacingWait as wait:
                self.sleep(min(wait.seconds, 10.0))
        try:
            response = self.sdk.chat.completions.create(**payload)
            self.ledger.settle(call_id, response)
        except BaseException as exc:
            self.ledger.record_error(call_id, exc)
            raise BudgetStop("Request stopped; safe error metadata saved and full unresolved reserve retained.") from None
        self.ledger.finish_pacing()
        return response


def real_client(ledger, locks, env_path):
    import httpx
    from openai import OpenAI
    sdk = OpenAI(api_key=credentials(env_path), base_url=OFFICIAL_BASE,
                 max_retries=0, timeout=180,
                 http_client=httpx.Client(follow_redirects=False, trust_env=False, timeout=180))
    return BudgetClientV2(ledger, locks, sdk)
