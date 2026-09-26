"""OpenRouter client with durable reservations and a shared USD 10 ceiling.

Only the official OpenRouter origin is accepted. A request reserves the full
endpoint context window, including image tokens, plus capped output, at the
provider price ceilings. No character-based token estimates are used.
Unknown bills retain their reservation and stop every subsequent paid call.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sqlite3
import time
import urllib.request
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from types import SimpleNamespace

OFFICIAL_BASE = "https://openrouter.ai/api/v1"
MAX_USD = Decimal("10")
NANO = Decimal("1000000000")
MODEL_LOCKS = {
    "z-ai/glm-5.3-flash": {"provider": "deepinfra/fp4", "prompt_per_m": "0.15", "completion_per_m": "0.5"},
    "deepseek/deepseek-v4-flash-vision-exp": {"provider": "deepinfra/fp8", "prompt_per_m": "0.44", "completion_per_m": "1.32"},
}
MAX_COMPLETION = 4096


class BudgetStop(RuntimeError):
    """Safe to print: messages never contain SDK exceptions or credentials."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as f:
        f.write(json.dumps(value, indent=2, ensure_ascii=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def nano_usd(value):
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < 0:
            raise ValueError
        return int((d * NANO).to_integral_value(rounding=ROUND_CEILING))
    except Exception:
        raise BudgetStop("Missing or invalid provider bill; no further paid calls.") from None


def credentials(path):
    """Read only the two required keys without shell execution or output."""
    values = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?(OPENROUTER_API_KEY|OPENROUTER_BASE_URL)\s*=\s*(.*)$", line)
        if not m:
            continue
        try:
            pieces = shlex.split(m[2], comments=True)
        except ValueError:
            raise BudgetStop("Malformed credential file.") from None
        if len(pieces) != 1:
            raise BudgetStop("Malformed credential file.")
        values[m[1]] = pieces[0]
    if not values.get("OPENROUTER_API_KEY"):
        raise BudgetStop("OPENROUTER_API_KEY is absent.")
    if values.get("OPENROUTER_BASE_URL", "").rstrip("/") != OFFICIAL_BASE:
        raise BudgetStop("Budget proof requires the official OpenRouter API origin.")
    return values["OPENROUTER_API_KEY"]


def fetch_metadata(model):
    """Public GET only. No credential is sent and redirects are not accepted."""
    if model not in MODEL_LOCKS:
        raise BudgetStop("Unapproved model.")
    url = f"{OFFICIAL_BASE}/models/{model}/endpoints"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            if response.geturl() != url:
                raise BudgetStop("Unexpected metadata redirect.")
            return json.load(response)
    except Exception:
        raise BudgetStop("Public provider metadata unavailable; no paid call.") from None


def validate_metadata(model, metadata, expected=None):
    """Reject unsupported/variable fee types instead of inventing a bound."""
    policy = MODEL_LOCKS[model]
    data = metadata.get("data", {})
    if data.get("id") != model or data.get("architecture", {}).get("output_modalities") != ["text"]:
        raise BudgetStop("Unexpected model identity or output modalities.")
    endpoints = [e for e in data.get("endpoints", []) if e.get("tag") == policy["provider"]]
    if len(endpoints) != 1:
        raise BudgetStop("Pinned provider endpoint is unavailable or ambiguous.")
    endpoint = endpoints[0]
    if endpoint.get("status") != 0:
        raise BudgetStop("Pinned provider endpoint is not active.")
    context = endpoint.get("context_length")
    completion = endpoint.get("max_completion_tokens")
    if not isinstance(context, int) or context <= 0 or not isinstance(completion, int) or completion < MAX_COMPLETION:
        raise BudgetStop("Provider token upper bounds are unavailable.")
    if "max_tokens" not in endpoint.get("supported_parameters", []):
        raise BudgetStop("Pinned endpoint cannot enforce max_tokens.")
    pricing = endpoint.get("pricing", {})
    for key in ("prompt", "completion"):
        if key not in pricing:
            raise BudgetStop("Missing provider pricing.")
        rate = Decimal(str(pricing[key]))
        cap = Decimal(policy[key + "_per_m"]) / 1000000
        if not rate.is_finite() or rate < 0 or rate > cap:
            raise BudgetStop("Provider price exceeds the frozen ceiling.")
    for key, value in pricing.items():
        if key in ("prompt", "completion", "discount"):
            continue
        try:
            rate = Decimal(str(value))
        except Exception:
            raise BudgetStop("Non-scalar pricing cannot be bounded.") from None
        # Cache reads may replace prompt tokens at no higher unit price.
        if key == "input_cache_read" and rate.is_finite() and 0 <= rate <= Decimal(policy["prompt_per_m"]) / 1000000:
            continue
        if not rate.is_finite() or rate != 0:
            raise BudgetStop("Additional provider fees are not bounded by this client.")
    locked = dict(policy, context_length=context, max_tokens=MAX_COMPLETION)
    locked["reservation_usd"] = str(
        (Decimal(context) * Decimal(policy["prompt_per_m"]) +
         Decimal(MAX_COMPLETION) * Decimal(policy["completion_per_m"])) / 1000000
    )
    if expected and locked != expected:
        raise BudgetStop("Provider limits changed after preparation; no paid call.")
    return locked


class BudgetLedger:
    """SQLite FULL sync + BEGIN IMMEDIATE serialize all reservation writers."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('limit_nano', ?)", (str(nano_usd(MAX_USD)),))
            if db.execute("SELECT value FROM settings WHERE key='limit_nano'").fetchone()[0] != str(nano_usd(MAX_USD)):
                raise BudgetStop("The shared ledger limit does not match USD 10.")
            db.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, episode TEXT NOT NULL, model TEXT NOT NULL, request_sha TEXT NOT NULL, reserved_nano INTEGER NOT NULL, actual_nano INTEGER, state TEXT NOT NULL, response_json TEXT, error_type TEXT, created REAL NOT NULL)")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def summary(self):
        with self.connect() as db:
            rows = db.execute("SELECT state, reserved_nano, actual_nano FROM calls").fetchall()
        actual = sum(r[2] or 0 for r in rows)
        reserved = sum(r[1] for r in rows if r[0] != "settled")
        return {"limit_usd": str(MAX_USD), "actual_usd": str(Decimal(actual) / NANO),
                "unresolved_reserved_usd": str(Decimal(reserved) / NANO),
                "calls": len(rows), "settled_calls": sum(r[0] == "settled" for r in rows),
                "blocked": any(r[0] != "settled" for r in rows)}

    def reserve(self, call_id, episode, model, request_sha, reservation):
        amount = nano_usd(reservation)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (call_id,)).fetchone():
                raise BudgetStop("Request id already exists; it will not be sent again.")
            if db.execute("SELECT 1 FROM calls WHERE state != 'settled'").fetchone():
                raise BudgetStop("Unresolved or in-flight billing reservation; no further paid calls.")
            spent = db.execute("SELECT COALESCE(SUM(actual_nano),0) FROM calls").fetchone()[0]
            if spent + amount > nano_usd(MAX_USD):
                raise BudgetStop("USD 10 ceiling: insufficient room for the full request reservation.")
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,NULL,'reserved',NULL,NULL,?)", (call_id, episode, model, request_sha, amount, time.time()))

    def settle(self, call_id, response):
        raw = response.model_dump(mode="json")
        # Persist even a response without a bill for later reconciliation.
        with self.connect() as db:
            db.execute("UPDATE calls SET response_json=? WHERE id=? AND state='reserved'", (canonical(raw), call_id))
        amount = nano_usd((raw.get("usage") or {}).get("cost"))
        with self.connect() as db:
            row = db.execute("SELECT reserved_nano, state FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row[1] != "reserved":
                raise BudgetStop("Invalid reservation settlement.")
            state = "settled" if amount <= row[0] else "overrun"
            db.execute("UPDATE calls SET actual_nano=?, state=?, response_json=? WHERE id=?", (amount, state, canonical(raw), call_id))
        if state != "settled":
            raise BudgetStop("Provider bill exceeded its contractual reservation; all paid work stopped.")

    def uncertain(self, call_id, error_type):
        with self.connect() as db:
            db.execute("UPDATE calls SET state='uncertain',error_type=? WHERE id=? AND state='reserved'", (error_type, call_id))


class BudgetClient:
    """Drop-in client.chat.completions.create used by Android and builder arms."""

    def __init__(self, ledger, model_locks, sdk, metadata_fetcher=fetch_metadata):
        if getattr(sdk, "max_retries", None) != 0:
            raise BudgetStop("SDK retries must be disabled.")
        if str(getattr(sdk, "base_url", "")).rstrip("/") != OFFICIAL_BASE:
            raise BudgetStop("Only the official OpenRouter origin is permitted.")
        self.ledger, self.model_locks, self.sdk = ledger, model_locks, sdk
        self.metadata_fetcher = metadata_fetcher
        self.episode = None
        self.sequence = 0
        self.checked_at = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def begin_episode(self, episode):
        self.episode, self.sequence = episode, 0

    def create(self, **kwargs):
        if not self.episode:
            raise BudgetStop("A durable episode id is required.")
        if set(kwargs) - {"model", "messages", "temperature"}:
            raise BudgetStop("Unapproved request options could invalidate the budget bound.")
        model = kwargs.get("model")
        if model not in self.model_locks:
            raise BudgetStop("Model was not prepared.")
        lock = self.model_locks[model]
        if time.time() - self.checked_at.get(model, 0) > 60:
            validate_metadata(model, self.metadata_fetcher(model), expected=lock)
            self.checked_at[model] = time.time()
        self.sequence += 1
        call_id = f"{self.episode}/call-{self.sequence:04d}"
        payload = dict(kwargs, max_tokens=lock["max_tokens"], stream=False, extra_body={
            "provider": {"only": [lock["provider"]], "allow_fallbacks": False,
                         "require_parameters": True,
                         "max_price": {"prompt": float(lock["prompt_per_m"]), "completion": float(lock["completion_per_m"]), "request": 0}},
            "usage": {"include": True},
        })
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        self.ledger.reserve(call_id, self.episode, model, request_sha, lock["reservation_usd"])
        try:
            response = self.sdk.chat.completions.create(**payload)
            self.ledger.settle(call_id, response)
            return response
        except BaseException as exc:
            self.ledger.uncertain(call_id, type(exc).__name__)
            # Do not expose SDK exception text, headers, or response bodies.
            raise BudgetStop("Request failed or bill unavailable; reservation retained and paid work stopped.") from None


def real_client(ledger, locks, env_path):
    import httpx
    from openai import OpenAI
    sdk = OpenAI(api_key=credentials(env_path), base_url=OFFICIAL_BASE,
                 max_retries=0, timeout=180,
                 http_client=httpx.Client(follow_redirects=False, trust_env=False, timeout=180))
    return BudgetClient(ledger, locks, sdk)
