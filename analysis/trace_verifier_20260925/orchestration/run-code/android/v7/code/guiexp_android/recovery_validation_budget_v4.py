"""V4 single-request OpenRouter client with a fail-closed USD ledger.

All monetary ledger values are integer nano-USD. Endpoint ``pricing`` values
are USD per token, matching OpenRouter endpoint metadata. The caller freezes
the endpoint pool before constructing the client and supplies the ledger path.
No old experiment ledger, environment base URL, retry, or reconciliation path
is used by the client. Reopening a ledger with an unfinished request makes it
unknown. An inconsistent reasoning subcount is recorded as unavailable when
all primary usage, identity, price and charge checks pass. The explicit narrow
reconciliation helper defaults to dry-run and never makes a network request.

``transport`` is an optional offline test function with signature
``transport(url, payload, api_key, timeout) -> response_dict``. Production
transport sends exactly one HTTP request and rejects redirects.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from typing import Any, Callable
import urllib.request
import urllib.error
import urllib.parse
import uuid
from types import SimpleNamespace


API_URL = "https://openrouter.ai/api/v1/chat/completions"
NANO_USD = Decimal(1_000_000_000)
MAX_BUDGET_NUSD = 30_000_000_000
ROUNDING_TOLERANCE_NUSD = 1_000  # One micro-USD, never a percentage of spend.
TARGET_PRICES = {
    "z-ai/glm-5.3-flash": {
        "prompt": Decimal("0.00000015"),
        "completion": Decimal("0.00000050"),
        "input_cache_read": Decimal("0.00000003"),
    },
    "deepseek/deepseek-v4.1-flash": {
        "prompt": Decimal("0.00000030"),
        "completion": Decimal("0.00000120"),
        "input_cache_read": Decimal("0.000000006"),
    },
}


class BudgetError(RuntimeError):
    """Base error. Error messages never contain a credential or response body."""


class BudgetBlocked(BudgetError):
    pass


class BudgetExceeded(BudgetError):
    pass


class BudgetOverrun(BudgetError):
    pass


class QuoteError(BudgetError):
    pass


class BillingError(BudgetError):
    pass


class TransportUncertain(BudgetError):
    pass


class ReconciliationError(BudgetError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BillingError(f"{label} is not a nonnegative finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BillingError(f"{label} is not a nonnegative finite decimal") from None
    if not result.is_finite() or result < 0:
        raise BillingError(f"{label} is not a nonnegative finite decimal")
    return result


def _nanos(value: Any, label: str) -> int:
    return int((_decimal(value, label) * NANO_USD).to_integral_value(
        rounding=ROUND_CEILING))


def _usd(value: int) -> str:
    return format(Decimal(value) / NANO_USD, ".9f")


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BillingError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise BillingError(f"{label} is out of range")
    return value


def _check_no_overrides(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _check_no_overrides(item)
        return
    if not isinstance(value, Mapping):
        return
    for key, entry in value.items():
        lower = str(key).lower()
        if (any(word in lower for word in ("tier", "override", "time_of_day",
                                           "surge", "additional_fee", "discount"))
                or ("time" in lower and ("price" in lower or "pricing" in lower))):
            if entry not in (None, [], {}, False, "0", 0):
                raise QuoteError("Endpoint metadata contains a price override or fee")
        if isinstance(entry, (Mapping, list)):
            _check_no_overrides(entry)


def validate_locks(locks: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Validate frozen routing tags, provider names, vision, bounds, and quotes.

    Each model lock has ``model_id``, ``context_length``, and ``endpoints``.
    Each endpoint has ``tag``, ``provider_name``, ``status`` (integer zero),
    ``context_length``, ``supports_image`` (true), and ``pricing``. Pricing
    includes prompt, completion, input_cache_read, request, and image. Extra
    pricing fields are accepted only when their numeric value is zero.
    """
    if isinstance(locks, (str, Path)):
        locks = json.loads(Path(locks).read_text(encoding="utf-8"))
    if not isinstance(locks, Mapping) or not locks:
        raise QuoteError("At least one frozen model lock is required")
    try:
        frozen = json.loads(_json(locks))
        for model, lock in frozen.items():
            if model not in TARGET_PRICES or not isinstance(lock, dict):
                raise QuoteError("Only the two authorized model IDs are allowed")
            if lock.get("model_id") != model:
                raise QuoteError("Model lock identity does not match its key")
            _check_no_overrides(lock)
            context = _integer(lock.get("context_length"), "context_length", positive=True)
            endpoints = lock.get("endpoints")
            if not isinstance(endpoints, list) or not endpoints:
                raise QuoteError("Each model needs a nonempty endpoint pool")
            tags: set[str] = set()
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    raise QuoteError("Endpoint metadata must be an object")
                for identity in ("model_id", "model"):
                    if identity in endpoint and endpoint[identity] != model:
                        raise QuoteError("Endpoint model identity differs from the lock")
                tag, provider = endpoint.get("tag"), endpoint.get("provider_name")
                if not isinstance(tag, str) or not tag.strip() or tag != tag.strip():
                    raise QuoteError("Endpoint routing tag is missing or malformed")
                if tag in tags:
                    raise QuoteError("Duplicate endpoint routing tag")
                tags.add(tag)
                if not isinstance(provider, str) or not provider.strip():
                    raise QuoteError("Endpoint response provider name is missing")
                if type(endpoint.get("status")) is not int or endpoint["status"] != 0:
                    raise QuoteError("Every locked endpoint must be active (status 0)")
                if endpoint.get("supports_image") is not True:
                    raise QuoteError("Every endpoint must support image input")
                endpoint_context = _integer(endpoint.get("context_length"),
                                            "endpoint context_length", positive=True)
                if endpoint_context < context:
                    raise QuoteError("Declared context exceeds an endpoint context")
                if endpoint.get("max_completion_tokens") is not None:
                    _integer(endpoint["max_completion_tokens"],
                             "max_completion_tokens", positive=True)
                pricing = endpoint.get("pricing")
                required = {*TARGET_PRICES[model], "request", "image"}
                if not isinstance(pricing, dict) or not required <= pricing.keys():
                    raise QuoteError("Quote needs token, cache, request, and image rates")
                for key, value in pricing.items():
                    rate = _decimal(value, "endpoint pricing")
                    if key in TARGET_PRICES[model]:
                        if rate != TARGET_PRICES[model][key]:
                            raise QuoteError("An endpoint differs from the authorized quote")
                    elif rate != 0:
                        raise QuoteError("Unknown or additional endpoint fee is nonzero")
    except BillingError as exc:
        raise QuoteError(str(exc)) from None
    except (ValueError, TypeError):
        raise QuoteError("Frozen quote metadata is not valid JSON") from None
    return frozen


class BudgetLedger:
    """Durable, exclusively owned experiment ledger with atomic reservations."""

    def __init__(self, path: str | Path, budget_usd: Any = 30):
        self.path = Path(path).expanduser().resolve()
        self.budget_nusd = _nanos(budget_usd, "budget_usd")
        if not 0 < self.budget_nusd <= MAX_BUDGET_NUSD:
            raise BudgetExceeded("Budget must be positive and at most USD 30")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, episode TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','settled','unknown','overrun')),
                created_at TEXT NOT NULL, finalized_at TEXT,
                model_requested TEXT NOT NULL, model_returned TEXT, provider TEXT,
                generation_id TEXT, reservation_nusd INTEGER NOT NULL,
                actual_cost_nusd INTEGER, expected_cost_nusd INTEGER,
                upstream_cost_nusd INTEGER, platform_discount_nusd INTEGER,
                prompt_tokens INTEGER, cached_prompt_tokens INTEGER,
                completion_tokens INTEGER, reasoning_tokens INTEGER, total_tokens INTEGER,
                request_hash TEXT NOT NULL, request_json TEXT NOT NULL,
                response_hash TEXT, output_hash TEXT, response_json TEXT, usage_json TEXT,
                billing_json TEXT, error TEXT
            )""")
            con.execute("CREATE INDEX IF NOT EXISTS generation_ids ON requests(generation_id)")
            saved = con.execute("SELECT value FROM metadata WHERE key='budget_nusd'").fetchone()
            if saved is not None and int(saved[0]) != self.budget_nusd:
                raise BudgetExceeded("Existing ledger budget is immutable")
            con.execute("INSERT OR IGNORE INTO metadata VALUES ('budget_nusd', ?)",
                        (str(self.budget_nusd),))
            con.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version', '1')")
            con.execute("""UPDATE requests SET state='unknown', finalized_at=?,
                           error='ledger reopened with an unfinished request'
                           WHERE state='pending'""", (_now(),))

    @contextmanager
    def _transaction(self):
        con = sqlite3.connect(str(self.path), timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def bind_locks(self, locks: Mapping[str, Any]) -> None:
        serialized = _json(locks)
        with self._transaction() as con:
            saved = con.execute("SELECT value FROM metadata WHERE key='locks_sha256'").fetchone()
            if saved is not None and saved[0] != _hash(serialized):
                raise QuoteError("This ledger is already bound to another frozen quote pool")
            for key, value in (("locks_sha256", _hash(serialized)), ("locks_json", serialized)):
                con.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))

    @staticmethod
    def _exposure(con: sqlite3.Connection) -> int:
        return con.execute("""SELECT COALESCE(SUM(CASE WHEN state='settled'
            THEN actual_cost_nusd ELSE MAX(reservation_nusd, COALESCE(actual_cost_nusd, 0))
            END), 0) FROM requests""").fetchone()[0]

    def reserve(self, *, model: str, episode: str, reservation_nusd: int,
                request_hash: str, request_json: str) -> str:
        _integer(reservation_nusd, "reservation_nusd", positive=True)
        with self._transaction() as con:
            blocked = con.execute("SELECT 1 FROM requests WHERE state!='settled' LIMIT 1").fetchone()
            if blocked:
                raise BudgetBlocked("Unresolved or overrun request blocks further paid work")
            if self._exposure(con) + reservation_nusd > self.budget_nusd:
                raise BudgetExceeded("Insufficient unreserved budget for the declared context bound")
            request_id = str(uuid.uuid4())
            con.execute("""INSERT INTO requests
                (request_id, episode, state, created_at, model_requested,
                 reservation_nusd, request_hash, request_json)
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)""",
                (request_id, str(episode), _now(), model, reservation_nusd,
                 request_hash, request_json))
            return request_id

    def record_response(self, request_id: str, response: Any) -> None:
        serialized = _json(response)
        response_dict = response if isinstance(response, dict) else {}
        identities = [response_dict.get(key) if isinstance(response_dict.get(key), str)
                      else None for key in ("id", "model", "provider")]
        with self._transaction() as con:
            con.execute("""UPDATE requests SET response_json=?, response_hash=?,
                           output_hash=?, generation_id=?, model_returned=?,
                           provider=?, usage_json=? WHERE request_id=?""",
                        (serialized, _hash(serialized),
                         _hash(_json(response_dict.get("choices"))), *identities,
                         _json(response_dict.get("usage")), request_id))

    def fail(self, request_id: str, error: str, response: Any = None) -> str:
        known_cost = None
        if isinstance(response, dict) and isinstance(response.get("usage"), dict):
            try:
                known_cost = _nanos(response["usage"].get("cost"), "usage.cost")
            except BillingError:
                pass
        with self._transaction() as con:
            row = con.execute("SELECT reservation_nusd FROM requests WHERE request_id=?",
                              (request_id,)).fetchone()
            state = "overrun" if known_cost is not None and known_cost > row[0] else "unknown"
            con.execute("""UPDATE requests SET state=?, finalized_at=?, error=?,
                           actual_cost_nusd=? WHERE request_id=? AND state!='settled'""",
                        (state, _now(), error, known_cost, request_id))
            return state

    def settle(self, request_id: str, fields: dict[str, Any]) -> None:
        error = None
        with self._transaction() as con:
            row = con.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row["state"] != "pending":
                raise BudgetBlocked("Reservation is not pending")
            duplicate = con.execute("""SELECT 1 FROM requests WHERE generation_id=?
                                       AND request_id!=? LIMIT 1""",
                                    (fields["generation_id"], request_id)).fetchone()
            if duplicate:
                state, error = "unknown", "Repeated generation ID across physical requests"
            elif fields["actual_cost_nusd"] > row["reservation_nusd"]:
                state, error = "overrun", "Actual charge exceeds its reserved upper bound"
            else:
                state = "settled"
            values = {**fields, "state": state, "finalized_at": _now(), "error": error}
            assignments = ", ".join(f"{key}=?" for key in values)
            con.execute(f"UPDATE requests SET {assignments} WHERE request_id=?",
                        (*values.values(), request_id))
        if error:
            if state == "overrun":
                raise BudgetOverrun(error)
            raise BillingError(error)

    def records(self) -> list[dict[str, Any]]:
        with self._transaction() as con:
            return [dict(row) for row in con.execute("SELECT * FROM requests ORDER BY created_at, request_id")]

    def summary(self) -> dict[str, Any]:
        with self._transaction() as con:
            states = dict(con.execute("SELECT state, COUNT(*) FROM requests GROUP BY state"))
            exposure = self._exposure(con)
            known_cost = con.execute("SELECT COALESCE(SUM(actual_cost_nusd), 0) FROM requests").fetchone()[0]
            settled_cost = con.execute("""SELECT COALESCE(SUM(actual_cost_nusd), 0)
                                          FROM requests WHERE state='settled'""").fetchone()[0]
            reserved = con.execute("""SELECT COALESCE(SUM(reservation_nusd), 0)
                                      FROM requests WHERE state!='settled'""").fetchone()[0]
            by_model = [dict(row) for row in con.execute("""SELECT model_requested AS model,
                COUNT(*) AS requests, SUM(prompt_tokens) AS prompt_tokens,
                SUM(cached_prompt_tokens) AS cached_prompt_tokens,
                SUM(completion_tokens) AS completion_tokens,
                SUM(reasoning_tokens) AS reasoning_tokens,
                SUM(actual_cost_nusd) AS actual_cost_nusd,
                SUM(expected_cost_nusd) AS expected_cost_nusd,
                SUM(platform_discount_nusd) AS platform_discount_nusd
                FROM requests GROUP BY model_requested""")]
        return {
            "budget_nusd": self.budget_nusd, "budget_usd": _usd(self.budget_nusd),
            "known_cost_nusd": known_cost, "known_cost_usd": _usd(known_cost),
            "settled_cost_nusd": settled_cost, "settled_cost_usd": _usd(settled_cost),
            "unsettled_reserved_nusd": reserved, "exposure_nusd": exposure,
            "exposure_usd": _usd(exposure),
            "remaining_nusd": max(0, self.budget_nusd - exposure),
            "remaining_usd": _usd(max(0, self.budget_nusd - exposure)),
            "blocked": any(state != "settled" for state in states),
            "states": states, "by_model": by_model,
        }


def _load_api_key(env_path: str | Path) -> str:
    """Parse only OPENROUTER_API_KEY. Never evaluate or export .env content."""
    values = []
    with Path(env_path).expanduser().open(encoding="utf-8") as stream:
        for line in stream:
            match = re.match(r"^\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*(.*?)\s*$", line)
            if not match:
                continue
            value = match.group(1)
            if value.startswith(("'", '"')):
                quote = value[0]
                end = value.find(quote, 1)
                suffix = value[end + 1:].strip()
                if end < 0 or (suffix and not suffix.startswith("#")):
                    raise BudgetError("OPENROUTER_API_KEY assignment is malformed")
                value = value[1:end]
            else:
                value = value.split(" #", 1)[0].strip()
            if not value or any(char.isspace() for char in value):
                raise BudgetError("OPENROUTER_API_KEY is empty or malformed")
            values.append(value)
    if len(values) != 1:
        raise BudgetError("The supplied env file must contain exactly one OPENROUTER_API_KEY")
    return values[0]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(url: str, payload: dict[str, Any], api_key: str,
                    timeout: float) -> dict[str, Any]:
    if url != API_URL:
        raise BudgetError("Only the fixed official OpenRouter URL is allowed")
    request = urllib.request.Request(
        API_URL, data=_json(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    # urllib has no automatic retry. Redirects would be another physical request.
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as result:
            body = result.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except ValueError:
                return {"error": {"message": "OpenRouter returned malformed JSON"},
                        "_transport": {"http_status": result.status,
                                       "body": body.replace(api_key, "[REDACTED_API_KEY]")}}
    except urllib.error.HTTPError as exc:
        # HTTP errors can still have an uncertain charge. Keep the body, without
        # headers or a credential, so the caller can perform later reconciliation.
        body = exc.read().decode("utf-8", errors="replace").replace(api_key, "[REDACTED_API_KEY]")
        return {"error": {"message": "OpenRouter returned an HTTP error"},
                "_transport": {"http_status": exc.code, "body": body}}


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or not messages:
        raise BudgetError("messages must be a nonempty list")
    user_messages = [message for message in messages
                     if isinstance(message, dict) and message.get("role") == "user"]
    if not user_messages or not isinstance(user_messages[-1].get("content"), list):
        raise BudgetError("The final user observation must include image and tree text blocks")
    has_text = has_image = False
    for part in user_messages[-1]["content"]:
        if not isinstance(part, dict):
            raise BudgetError("Observation content blocks must be objects")
        if part.get("type") == "text" and isinstance(part.get("text"), str) and part["text"].strip():
            has_text = True
        if part.get("type") == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else None
            if not isinstance(url, str) or not re.match(r"^data:image/(png|jpeg|webp);base64,", url):
                raise BudgetError("Screenshots must use embedded PNG, JPEG, or WebP data URLs")
            try:
                data = base64.b64decode(url.split(",", 1)[1], validate=True)
            except ValueError:
                raise BudgetError("Screenshot base64 data is malformed") from None
            valid_image = (
                (url.startswith("data:image/png;") and data.startswith(b"\x89PNG\r\n\x1a\n"))
                or (url.startswith("data:image/jpeg;") and data.startswith(b"\xff\xd8\xff"))
                or (url.startswith("data:image/webp;") and data.startswith(b"RIFF")
                    and data[8:12] == b"WEBP")
            )
            if not valid_image:
                raise BudgetError("Screenshot header does not match its declared image format")
            has_image = True
    if not (has_text and has_image):
        raise BudgetError("Each final observation requires screenshot and AX/HTML tree text")


def _safe_request(payload: dict[str, Any]) -> str:
    safe = json.loads(_json(payload))
    for message in safe["messages"]:
        if not isinstance(message, dict) or not isinstance(message.get("content"), list):
            continue
        for part in message["content"]:
            if isinstance(part, dict) and part.get("type") == "image_url":
                raw = _json(part["image_url"])
                part["image_url"] = {"omitted": True, "sha256": _hash(raw),
                                     "serialized_chars": len(raw)}
    return _json(safe)


class BudgetClient:
    def __init__(self, ledger: BudgetLedger, locks: Mapping[str, Any] | str | Path,
                 env_path: str | Path, transport: Callable | None = None,
                 timeout: float = 120):
        self.ledger = ledger
        self.locks = validate_locks(locks)
        self._locks_hash = _hash(_json(self.locks))
        self.ledger.bind_locks(self.locks)
        self.env_path = Path(env_path).expanduser()
        self.transport = transport or _http_transport
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise BudgetError("timeout must be finite and between 0 and 300 seconds")
        self.timeout = timeout

    def complete(self, model: str, messages: list[dict[str, Any]], max_tokens: int,
                 episode: str, temperature: float = 0,
                 reasoning_effort: str = "low") -> dict[str, Any]:
        if _hash(_json(self.locks)) != self._locks_hash:
            raise QuoteError("The client's frozen endpoint metadata was modified")
        if model not in self.locks:
            raise QuoteError("Model is not in the frozen quote pool")
        _validate_messages(messages)
        max_tokens = _integer(max_tokens, "max_tokens", positive=True)
        if isinstance(temperature, bool) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise BudgetError("temperature must be finite and between 0 and 2")
        if reasoning_effort not in ("none", "minimal", "low", "medium", "high"):
            raise BudgetError("Unsupported reasoning_effort")
        lock = self.locks[model]
        if max_tokens > lock["context_length"]:
            raise BudgetError("max_tokens exceeds the declared context bound")
        if any(endpoint.get("max_completion_tokens") is not None and
               max_tokens > endpoint["max_completion_tokens"] for endpoint in lock["endpoints"]):
            raise BudgetError("max_tokens exceeds an endpoint completion bound")
        prices = TARGET_PRICES[model]
        payload = {
            "model": model, "messages": messages, "max_tokens": max_tokens,
            "temperature": temperature, "stream": False,
            "reasoning": {"effort": reasoning_effort},
            "usage": {"include": True},
            "provider": {
                "only": [endpoint["tag"] for endpoint in lock["endpoints"]],
                "allow_fallbacks": True, "require_parameters": True,
                "max_price": {**{key: float(prices[key] * 1_000_000)
                                 for key in ("prompt", "completion")}, "image": 0},
            },
        }
        serialized = _json(payload)
        reservation = _nanos(lock["context_length"] * prices["prompt"] +
                             max_tokens * prices["completion"], "reservation")
        api_key = _load_api_key(self.env_path)
        request_id = self.ledger.reserve(
            model=model, episode=episode, reservation_nusd=reservation,
            request_hash=_hash(serialized), request_json=_safe_request(payload))
        response = None
        try:
            response = self.transport(API_URL, payload, api_key, self.timeout)
            self.ledger.record_response(request_id, response)
        except BaseException as exc:
            self.ledger.fail(request_id, f"Uncertain transport or response: {type(exc).__name__}")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise TransportUncertain("Request outcome is uncertain. Reservation retained and paid work stopped.") from None
        finally:
            api_key = ""
        try:
            fields = self._billing_fields(model, response, max_tokens)
        except (BudgetError, ValueError, TypeError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, BudgetError) else "Malformed response or billing"
            state = self.ledger.fail(request_id, reason, response)
            if state == "overrun":
                raise BudgetOverrun("Reported cost exceeds the reservation. Paid work stopped.") from None
            raise BillingError(f"{reason}. Reservation retained and paid work stopped.") from None
        self.ledger.settle(request_id, fields)
        return response

    def _billing_fields(self, model: str, response: Any, max_tokens: int) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("error") is not None:
            raise BillingError("Response is not a successful completion object")
        generation_id = response.get("id")
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise BillingError("Generation ID is missing")
        if response.get("model") != model:
            raise BillingError("Returned model differs from the requested model")
        providers = {endpoint["provider_name"] for endpoint in self.locks[model]["endpoints"]}
        if response.get("provider") not in providers:
            raise BillingError("Returned provider is outside the frozen pool")
        if not isinstance(response.get("choices"), list) or not response["choices"]:
            raise BillingError("Completion choices are missing")
        usage = response.get("usage")
        if not isinstance(usage, dict):
            raise BillingError("Response usage is missing")
        if usage.get("is_byok") is True:
            raise BillingError("BYOK billing is outside this experiment")
        prompt = _integer(usage.get("prompt_tokens"), "prompt_tokens", positive=True)
        completion = _integer(usage.get("completion_tokens"), "completion_tokens")
        total = _integer(usage.get("total_tokens"), "total_tokens", positive=True)
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict):
            raise BillingError("Prompt token details are missing")
        cached = _integer(details.get("cached_tokens"), "cached_tokens")
        completion_details = usage.get("completion_tokens_details")
        reasoning = None
        reasoning_raw = None
        reasoning_anomaly = None
        if isinstance(completion_details, dict) and completion_details.get("reasoning_tokens") is not None:
            reasoning_raw = completion_details["reasoning_tokens"]
            try:
                reasoning = _integer(reasoning_raw, "reasoning_tokens")
            except BillingError:
                reasoning_anomaly = {"kind": "invalid_reasoning_subcount", "raw_value": reasoning_raw,
                                     "completion_tokens": completion,
                                     "reason": "Not a nonnegative integer; not comparable to completion tokens"}
            if reasoning is not None and reasoning > completion:
                reasoning_anomaly = {"kind": "reasoning_exceeds_completion", "raw_value": reasoning_raw,
                                     "completion_tokens": completion,
                                     "reason": "Reported reasoning exceeds billed completion tokens"}
                reasoning = None
        if cached > prompt or total != prompt + completion:
            raise BillingError("Token totals or cache counts are inconsistent")
        if completion > max_tokens or total > self.locks[model]["context_length"]:
            raise BillingError("Returned tokens exceed the declared request bounds")
        prices = TARGET_PRICES[model]
        expected_prompt = _nanos((prompt - cached) * prices["prompt"] +
                                cached * prices["input_cache_read"], "expected prompt cost")
        expected_completion = _nanos(completion * prices["completion"], "expected completion cost")
        expected = expected_prompt + expected_completion
        actual = _nanos(usage.get("cost"), "usage.cost")
        cost_details = usage.get("cost_details")
        if cost_details is not None and not isinstance(cost_details, dict):
            raise BillingError("cost_details is malformed")
        upstream = None
        if isinstance(cost_details, dict):
            for field, expected_part in (
                ("upstream_inference_prompt_cost", expected_prompt),
                ("upstream_inference_completions_cost", expected_completion),
            ):
                if cost_details.get(field) is not None:
                    if abs(_nanos(cost_details[field], field) - expected_part) > ROUNDING_TOLERANCE_NUSD:
                        raise BillingError("Upstream cost component differs from locked token pricing")
        if isinstance(cost_details, dict) and cost_details.get("upstream_inference_cost") is not None:
            upstream = _nanos(cost_details["upstream_inference_cost"], "upstream_inference_cost")
            if abs(upstream - expected) > ROUNDING_TOLERANCE_NUSD:
                raise BillingError("Upstream cost differs from locked token pricing")
        reference = upstream if upstream is not None else actual
        if upstream is None and abs(actual - expected) > ROUNDING_TOLERANCE_NUSD:
            raise BillingError("Actual cost differs from locked pricing without a verified upstream cost")
        if actual > reference + ROUNDING_TOLERANCE_NUSD:
            raise BillingError("Actual cost includes an unexplained additional charge")
        discount = max(0, reference - actual) if upstream is not None else 0
        return {
            "generation_id": generation_id, "model_returned": response["model"],
            "provider": response["provider"], "prompt_tokens": prompt,
            "cached_prompt_tokens": cached, "completion_tokens": completion,
            "reasoning_tokens": reasoning, "total_tokens": total,
            "actual_cost_nusd": actual, "expected_cost_nusd": expected,
            "upstream_cost_nusd": upstream, "platform_discount_nusd": discount,
            "usage_json": _json(usage),
            "billing_json": _json({
                "pricing_basis": "uncached_input_plus_cached_input_plus_completion",
                "discount_basis": "verified_upstream_minus_actual" if upstream is not None else "none",
                "rounding_tolerance_nusd": ROUNDING_TOLERANCE_NUSD,
                "actual_charge_basis": "usage.cost",
                "reasoning_tokens_raw": reasoning_raw,
                "reasoning_tokens_anomaly": reasoning_anomaly,
                "reasoning_subcount_policy": "Invalid subcounts are unavailable, never capped or added to billed completion tokens",
                "pricing_usd_per_token": {key: str(value) for key, value in prices.items()},
            }),
        }


_REASONING_ERROR = "Reasoning tokens exceed completion tokens"
_RECONCILIATION_NATIVE_MODELS = {
    "deepseek/deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash-20260910",
}
_AUDIT_TABLE = "reasoning_subcount_reconciliation_audit"


def _require_reconciliation(condition: bool, message: str) -> None:
    if not condition:
        raise ReconciliationError(message)


def _reasoning_reconciliation_plan(con: sqlite3.Connection, request_id: str,
                                   generation_record: dict, metadata_sha256: str) -> tuple[dict, dict, dict]:
    """Validate the isolated historical failure without constructing a client."""
    found = con.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
    _require_reconciliation(found is not None, "The requested ledger row does not exist")
    old = dict(found)
    _require_reconciliation(old.get("state") == "unknown", "Only an unknown row can be reconciled")
    _require_reconciliation(old.get("error") == _REASONING_ERROR,
                            "The original failure is not the exact isolated reasoning-subcount error")
    metadata = dict(con.execute("SELECT key, value FROM metadata"))
    try:
        locks_raw = json.loads(metadata["locks_json"])
        budget_nusd = int(metadata["budget_nusd"])
        response = json.loads(old["response_json"])
        request = json.loads(old["request_json"])
    except (KeyError, TypeError, ValueError):
        raise ReconciliationError("Required ledger metadata or preserved JSON is malformed") from None
    _require_reconciliation(0 < budget_nusd <= MAX_BUDGET_NUSD, "Ledger budget exceeds the authorized cap")
    locks = validate_locks(locks_raw)
    _require_reconciliation(metadata.get("locks_sha256") == _hash(_json(locks)),
                            "Bound quote metadata hash does not match")
    model = old.get("model_requested")
    _require_reconciliation(model in _RECONCILIATION_NATIVE_MODELS,
                            "No audited canonical/native alias is defined for this model")
    _require_reconciliation(isinstance(response, dict) and isinstance(request, dict),
                            "Preserved request and response must be JSON objects")
    _require_reconciliation(old.get("response_hash") == _hash(_json(response)),
                            "Preserved response hash does not match")
    _require_reconciliation(old.get("output_hash") == _hash(_json(response.get("choices"))),
                            "Preserved output hash does not match")
    _require_reconciliation(request.get("model") == model, "Saved request model differs from ledger identity")
    max_tokens = _integer(request.get("max_tokens"), "saved request max_tokens", positive=True)
    _require_reconciliation(model in locks, "Requested model is absent from bound locks")
    lock = locks[model]
    _require_reconciliation(max_tokens <= lock["context_length"], "Saved request exceeds its context bound")
    _require_reconciliation(all(endpoint.get("max_completion_tokens") is None
                                or max_tokens <= endpoint["max_completion_tokens"]
                                for endpoint in lock["endpoints"]),
                            "Saved request exceeds an endpoint completion bound")
    provider_options = request.get("provider")
    _require_reconciliation(isinstance(provider_options, dict), "Saved provider restrictions are missing")
    _require_reconciliation(provider_options.get("only") == [endpoint["tag"] for endpoint in lock["endpoints"]]
                            and provider_options.get("allow_fallbacks") is True
                            and provider_options.get("require_parameters") is True,
                            "Saved request routing differs from its bound pool")
    rates = {key: _decimal(lock["endpoints"][0]["pricing"][key], "bound token price")
             for key in ("prompt", "completion", "input_cache_read")}
    caps = provider_options.get("max_price")
    _require_reconciliation(isinstance(caps, dict), "Saved price ceilings are missing")
    for key in ("prompt", "completion"):
        _require_reconciliation(_decimal(caps.get(key), "saved price ceiling") == rates[key] * 1_000_000,
                                "Saved price ceiling differs from the bound quote")
    _require_reconciliation(_decimal(caps.get("image"), "saved image price ceiling") == 0,
                            "Saved request allowed an extra image fee")
    expected_reservation = _nanos(lock["context_length"] * rates["prompt"]
                                  + max_tokens * rates["completion"], "expected reservation")
    _require_reconciliation(old.get("reservation_nusd") == expected_reservation,
                            "Original reservation differs from the declared worst-case bound")
    # _billing_fields is pure receipt validation. It only reads self.locks and
    # never accesses a key, transport, or ledger. No constructor is invoked.
    fields = BudgetClient._billing_fields(SimpleNamespace(locks=locks), model, response, max_tokens)
    billing = json.loads(fields["billing_json"])
    anomaly = billing.get("reasoning_tokens_anomaly")
    _require_reconciliation(isinstance(anomaly, dict) and anomaly.get("kind") == "reasoning_exceeds_completion",
                            "The preserved receipt lacks the isolated reasoning-subcount anomaly")
    _require_reconciliation(fields["reasoning_tokens"] is None, "Anomalous reasoning must remain unavailable")
    for key in ("generation_id", "model_returned", "provider"):
        _require_reconciliation(old.get(key) == fields[key], "Preserved identity differs from the receipt")
    for key in ("prompt_tokens", "cached_prompt_tokens", "completion_tokens", "total_tokens", "actual_cost_nusd"):
        _require_reconciliation(old.get(key) is None or old[key] == fields[key],
                                "A previously recorded primary billing value differs from the receipt")
    duplicate = con.execute("SELECT 1 FROM requests WHERE generation_id=? AND request_id!=? LIMIT 1",
                            (fields["generation_id"], request_id)).fetchone()
    _require_reconciliation(duplicate is None, "The generation ID is repeated in another physical request")
    _require_reconciliation(fields["actual_cost_nusd"] <= old["reservation_nusd"],
                            "Actual charge exceeds the retained reservation")
    _require_reconciliation(isinstance(generation_record, dict), "Generation file must contain an object")
    wrapper = generation_record.get("response")
    data = wrapper.get("data") if isinstance(wrapper, dict) else None
    _require_reconciliation(isinstance(data, dict), "Saved official generation data is missing")
    try:
        url = urllib.parse.urlsplit(generation_record.get("url", ""))
        query = urllib.parse.parse_qs(url.query, strict_parsing=True)
    except (ValueError, TypeError):
        raise ReconciliationError("Saved generation URL is malformed") from None
    _require_reconciliation(url.scheme == "https" and url.netloc == "openrouter.ai"
                            and url.path == "/api/v1/generation" and not url.fragment
                            and query == {"id": [fields["generation_id"]]},
                            "Saved metadata URL is not the exact official generation lookup")
    native_model = _RECONCILIATION_NATIVE_MODELS[model]
    _require_reconciliation(data.get("id") == fields["generation_id"], "Generation IDs do not match")
    _require_reconciliation(data.get("model") == native_model, "Generation native model differs from the audited alias")
    _require_reconciliation(data.get("provider_name") == fields["provider"], "Generation provider differs from the receipt")
    _require_reconciliation(data.get("is_byok") is False and data.get("cancelled") is False,
                            "BYOK or cancelled generations are outside this reconciliation")
    attempts = data.get("provider_responses")
    _require_reconciliation(isinstance(attempts, list) and len(attempts) == 1 and isinstance(attempts[0], dict),
                            "The generation does not identify exactly one provider response")
    attempt = attempts[0]
    _require_reconciliation(attempt.get("provider_name") == fields["provider"]
                            and attempt.get("model_permaslug") == native_model
                            and attempt.get("status") == 200 and attempt.get("is_byok") is False,
                            "Provider response identity or success status differs")
    for native_key, ledger_key in (("native_tokens_prompt", "prompt_tokens"),
                                   ("native_tokens_completion", "completion_tokens"),
                                   ("native_tokens_cached", "cached_prompt_tokens")):
        _require_reconciliation(_integer(data.get(native_key), native_key) == fields[ledger_key],
                                "Native generation token counts differ from the receipt")
    _require_reconciliation(_integer(data.get("native_tokens_reasoning"), "native_tokens_reasoning")
                            == billing["reasoning_tokens_raw"], "Reasoning anomaly is not corroborated")
    actual_decimal = _decimal(response["usage"]["cost"], "receipt charge")
    for key in ("usage", "total_cost"):
        _require_reconciliation(_decimal(data.get(key), "generation charge") == actual_decimal,
                                "Official generation charge differs from the receipt")
    _require_reconciliation(data.get("finish_reason") == response["choices"][0].get("finish_reason"),
                            "Generation finish reason differs from the receipt")
    prompt_cost = ((fields["prompt_tokens"] - fields["cached_prompt_tokens"]) * rates["prompt"]
                   + fields["cached_prompt_tokens"] * rates["input_cache_read"])
    completion_cost = fields["completion_tokens"] * rates["completion"]
    cost_details = response["usage"].get("cost_details")
    _require_reconciliation(isinstance(cost_details, dict), "Receipt upstream components are missing")
    for key, expected in (("upstream_inference_prompt_cost", prompt_cost),
                          ("upstream_inference_completions_cost", completion_cost),
                          ("upstream_inference_cost", prompt_cost + completion_cost)):
        _require_reconciliation(_decimal(cost_details.get(key), "receipt upstream component") == expected,
                                "Receipt upstream components differ from bound token pricing")
    native_upstream = data.get("upstream_inference_cost")
    if native_upstream is not None and _decimal(native_upstream, "generation upstream cost") != 0:
        _require_reconciliation(_decimal(native_upstream, "generation upstream cost") == prompt_cost + completion_cost,
                                "Nonzero generation upstream cost differs from bound pricing")
    billing["reasoning_subcount_reconciliation"] = {
        "validator": "recovery_validation_budget_v4",
        "original_error": _REASONING_ERROR,
        "generation_metadata_sha256": metadata_sha256,
        "canonical_model": model, "native_model": native_model,
        "generation_upstream_zero_means_unavailable": native_upstream is not None and _decimal(native_upstream, "generation upstream cost") == 0,
        "token_basis": "Corroborated native prompt, cached input and completion. Normalized generation token counts are not used.",
    }
    fields["billing_json"] = _json(billing)
    preview = {
        "request_id": request_id, "generation_id": fields["generation_id"],
        "canonical_model": model, "native_model": native_model, "provider": fields["provider"],
        "prior_state": "unknown", "new_state": "settled",
        "prompt_tokens": fields["prompt_tokens"], "cached_prompt_tokens": fields["cached_prompt_tokens"],
        "completion_tokens": fields["completion_tokens"], "reasoning_tokens": None,
        "reasoning_tokens_raw": billing["reasoning_tokens_raw"],
        "actual_cost_nusd": fields["actual_cost_nusd"], "actual_cost_usd": _usd(fields["actual_cost_nusd"]),
        "expected_quote_nusd": fields["expected_cost_nusd"],
        "platform_discount_nusd": fields["platform_discount_nusd"],
        "released_reservation_nusd": old["reservation_nusd"] - fields["actual_cost_nusd"],
        "generation_metadata_sha256": metadata_sha256,
    }
    return old, fields, preview


def reconcile_reasoning_subcount(ledger_path: str | Path, request_id: str,
                                 generation_file: str | Path, *, apply: bool = False) -> dict[str, Any]:
    """Dry-run or atomically reconcile exactly one corroborated reasoning error.

    The only accepted original failure is ``Reasoning tokens exceed completion
    tokens`` on the audited DeepSeek alias. A saved official generation lookup
    must corroborate every primary billing field. The generation endpoint's
    zero upstream cost is unavailable, so receipt upstream components are used.
    Applying appends an audit row protected against UPDATE/DELETE and settles
    only the specified request. It never rewrites the raw request or response.
    No credentials, network calls, or BudgetLedger constructors are used.
    """
    if type(apply) is not bool:
        raise ReconciliationError("apply must explicitly be True or False")
    metadata_path = Path(generation_file).expanduser().resolve()
    metadata_bytes = metadata_path.read_bytes()
    metadata_sha256 = hashlib.sha256(metadata_bytes).hexdigest()
    try:
        generation_record = json.loads(metadata_bytes)
    except ValueError:
        raise ReconciliationError("Saved generation metadata is not valid JSON") from None
    path = Path(ledger_path).expanduser().resolve()
    mode = "rw" if apply else "ro"
    con = sqlite3.connect(path.as_uri() + f"?mode={mode}", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if not apply:
            con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        old, fields, preview = _reasoning_reconciliation_plan(con, request_id, generation_record, metadata_sha256)
        audit_id = None
        if apply:
            con.execute(f"""CREATE TABLE IF NOT EXISTS {_AUDIT_TABLE} (
                audit_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL, validator TEXT NOT NULL,
                old_row_json TEXT NOT NULL, old_row_sha256 TEXT NOT NULL,
                generation_metadata_path TEXT NOT NULL, generation_metadata_sha256 TEXT NOT NULL,
                generation_metadata_json TEXT NOT NULL, settled_fields_json TEXT NOT NULL
            )""")
            for action in ("UPDATE", "DELETE"):
                con.execute(f"""CREATE TRIGGER IF NOT EXISTS {_AUDIT_TABLE}_no_{action.lower()}
                    BEFORE {action} ON {_AUDIT_TABLE} BEGIN
                    SELECT RAISE(ABORT, 'Reconciliation audit is append-only'); END""")
            audit_id = str(uuid.uuid4())
            now = _now()
            billing = json.loads(fields["billing_json"])
            billing["reasoning_subcount_reconciliation"].update(audit_id=audit_id, applied_at=now)
            fields["billing_json"] = _json(billing)
            updated = {**fields, "state": "settled", "finalized_at": now, "error": None}
            old_json = _json(old)
            con.execute(f"INSERT INTO {_AUDIT_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (audit_id, request_id, now, "recovery_validation_budget_v4", old_json, _hash(old_json),
                         str(metadata_path), metadata_sha256, metadata_bytes.decode("utf-8"), _json(updated)))
            assignments = ", ".join(f"{key}=?" for key in updated)
            changed = con.execute(f"UPDATE requests SET {assignments} WHERE request_id=? AND state='unknown' AND error=?",
                                  (*updated.values(), request_id, _REASONING_ERROR))
            _require_reconciliation(changed.rowcount == 1, "The target row changed during reconciliation")
            saved = con.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            for key in ("response_json", "response_hash", "output_hash", "request_json", "request_hash",
                        "reservation_nusd", "created_at", "episode", "model_requested"):
                _require_reconciliation(saved[key] == old[key], "Reconciliation changed preserved evidence")
            con.commit()
        else:
            con.rollback()
        return {**preview, "applied": apply, "dry_run": not apply, "audit_id": audit_id}
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
