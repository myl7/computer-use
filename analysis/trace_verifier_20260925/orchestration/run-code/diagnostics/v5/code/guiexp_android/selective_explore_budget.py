"""Exploratory provider budget for the explicitly authorized 2026-09-15 tranche.

This module is deliberately separate from the frozen selective pilot.  It
keeps the legacy ``settings.limit_nano`` value at USD 10 while enforcing a
new, explicit total ceiling of USD 20 and a USD 10 ceiling for receipts added
after the authorization baseline.  Unknown and in-flight reservations are
counted at their full reservation in both checks.

The ``authorize-tranche`` command only reads the shared ledger and writes an
immutable authorization record under the existing selective output directory.
No model client is constructed by that command.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from . import selective_budget as base
from .budget_client import (
    BudgetStop,
    MAX_COMPLETION,
    MAX_USD as LEGACY_MAX_USD,
    OFFICIAL_BASE,
    canonical,
    credentials,
    fetch_metadata,
    nano_usd,
)
from .budget_client_v2 import PACE_SECONDS, PacingWait
from .budget_client_v9 import (
    FREE_METADATA_ATTEMPTS,
    FREE_METADATA_RETRY_SECONDS,
    MAX_PHYSICAL_ATTEMPTS,
    RETRY_MIN_SECONDS,
    TRANSIENT_PROVIDER_STATUS,
    BudgetClientV9,
    MissingBillEvidence,
    RETRYABLE_TRANSPORT_ERRORS,
    response_problem,
)
from .lossless_transport_v6 import LosslessTransport, MAX_WIRE_BYTES, TransportPayloadStop


ROOT = Path(__file__).resolve().parents[2]
SHARED_REVISION = (ROOT / "experimental-results/guiexp_android/revision_20260913").resolve()
SHARED_LEDGER_PATH = (SHARED_REVISION / "budget.sqlite3").resolve()
SHARED_RUN_LOCK_PATH = (SHARED_REVISION / "run.lock").resolve()
EXPLORE_NAMESPACE = "selective_20260915"
CALL_PREFIX = EXPLORE_NAMESPACE + "/"
EXPLORE_VERSION = "provider_v3"
EXPLORE_OUT = (ROOT / "experimental-results/guiexp_android" / EXPLORE_NAMESPACE / EXPLORE_VERSION).resolve()
AUTHORIZATION_REQUEST_PATH = (
    ROOT / "experimental-results/guiexp_android/selective_20260915/budget_authorization_request_20260915.json"
).resolve()
AUTHORIZATION_PATH = (
    ROOT / "experimental-results/guiexp_android/selective_20260915/budget_authorization_20260915.json"
).resolve()
DEFAULT_ENV_PATH = (ROOT.parent / ".env").resolve()

TOTAL_LIMIT_USD = Decimal("20")
TRANCHE_LIMIT_USD = Decimal("10")
TOTAL_LIMIT_NANO = nano_usd(TOTAL_LIMIT_USD)
TRANCHE_LIMIT_NANO = nano_usd(TRANCHE_LIMIT_USD)
LEGACY_LIMIT_NANO = nano_usd(LEGACY_MAX_USD)

MODEL = "z-ai/glm-5.3-flash"
DEFAULT_PROVIDER_PROFILE = "wafer"
DEFAULT_BUILDER_PROFILE = "builder_16384"
DEFAULT_SERVING_PROFILE = "serving_4096"

_PROVIDER_PROFILES = {
    "wafer": {
        "name": "wafer",
        "provider": "wafer",
        "prompt_per_m": "0.10",
        "completion_per_m": "0.35",
        "context_length": 1048576,
        "provider_max_completion_tokens": 943718,
    },
    "deepinfra_fp4": {
        "name": "deepinfra_fp4",
        "provider": "deepinfra/fp4",
        "prompt_per_m": "0.15",
        "completion_per_m": "0.50",
        "context_length": 1048576,
        "provider_max_completion_tokens": 131072,
    },
    "z_ai_fp8": {
        "name": "z_ai_fp8",
        "provider": "z-ai/fp8",
        "prompt_per_m": "0.15",
        "completion_per_m": "0.50",
        "context_length": 1048576,
        "provider_max_completion_tokens": 131072,
    },
}
_PROVIDER_ALIASES = {
    "deepinfra/fp4": "deepinfra_fp4",
    "z-ai/fp8": "z_ai_fp8",
}

REQUEST_PROFILES = {
    "serving_4096": {
        "name": DEFAULT_SERVING_PROFILE,
        "max_tokens": 4096,
        "reasoning": None,
        "max_physical_attempts": 1,
    },
    "builder_16384": {
        "name": DEFAULT_BUILDER_PROFILE,
        "max_tokens": 16384,
        "reasoning": {"effort": "low"},
        "max_physical_attempts": 1,
    },
}
BUILDER_PROFILE = copy.deepcopy(REQUEST_PROFILES[DEFAULT_BUILDER_PROFILE])


# SDK status exceptions expose a structured ``body``/``response`` object, but
# their string form can contain the complete response or request headers.  The
# ledger records only these two allowlisted error fields, after bounded
# redaction.  Keep the patterns local to this exploratory version so the old
# budget clients remain byte-for-byte frozen.
_SECRET_TOKEN_RE = re.compile(r"(?i)\b(?:sk|rk)-[A-Za-z0-9][A-Za-z0-9_-]{2,}")
_HEADER_SECRET_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|x-api-key|api-key|openai-api-key|"
    r"openrouter-api-key)\s*[:=]\s*[^\s,;\]}\)]+"
)
_BEARER_SECRET_RE = re.compile(r"(?i)\bbearer\s+[^\s,;\]}\)]+")
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")


def _redact_error_text(value):
    """Return one bounded, secret-scrubbed line from an HTTP error message."""
    if not isinstance(value, str):
        return None
    text = value.splitlines()[0] if value.splitlines() else ""
    text = _HEADER_SECRET_RE.sub("[REDACTED]", text)
    text = _BEARER_SECRET_RE.sub("Bearer [REDACTED]", text)
    text = _SECRET_TOKEN_RE.sub("[REDACTED]", text)
    text = text.strip()
    return text[:512] if text else None


def _safe_error_code(value):
    """Allow numeric HTTP codes and short symbolic provider error codes only."""
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    if isinstance(value, str) and _SAFE_ERROR_CODE_RE.fullmatch(value) and not _SECRET_TOKEN_RE.search(value):
        return value[:80]
    return None


def _safe_http_error_fields(exc):
    """Extract only ``error.code`` and ``error.message`` from an SDK error."""
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        response = getattr(exc, "response", None)
        parser = getattr(response, "json", None)
        if callable(parser):
            try:
                candidate = parser()
            except Exception:
                candidate = None
            body = candidate if isinstance(candidate, Mapping) else None
    if not isinstance(body, Mapping):
        return {}
    error = body.get("error")
    if not isinstance(error, Mapping):
        error = body if "code" in body or "message" in body else {}
    result = {}
    code = _safe_error_code(error.get("code"))
    if code is not None:
        result["http_error_code"] = code
    message = _redact_error_text(error.get("message"))
    if message is not None:
        result["http_error_message"] = message
    return result


def _scrub_error_metadata(value):
    """Remove secret-like strings from inherited allowlisted metadata."""
    if not isinstance(value, dict):
        return {}
    scrubbed = {}
    for key, item in value.items():
        if isinstance(item, str) and (_SECRET_TOKEN_RE.search(item) or _HEADER_SECRET_RE.search(item)):
            scrubbed[key] = "[REDACTED]"
        else:
            scrubbed[key] = item
    return scrubbed


def _provider_profile(value=DEFAULT_PROVIDER_PROFILE):
    if isinstance(value, dict):
        profile = copy.deepcopy(value)
        name = profile.get("name") or profile.get("provider")
        if name in _PROVIDER_ALIASES:
            name = _PROVIDER_ALIASES[name]
        expected = _PROVIDER_PROFILES.get(name)
        if expected is None:
            raise BudgetStop("Unknown exploratory provider profile.")
        for key in ("provider", "prompt_per_m", "completion_per_m", "context_length"):
            if key in profile and profile[key] != expected[key]:
                raise BudgetStop("Exploratory provider profile changed.")
        return copy.deepcopy(expected)
    name = _PROVIDER_ALIASES.get(value, value)
    if name not in _PROVIDER_PROFILES:
        raise BudgetStop("Unknown exploratory provider profile.")
    return copy.deepcopy(_PROVIDER_PROFILES[name])


def _request_profile(value=DEFAULT_SERVING_PROFILE):
    if isinstance(value, dict):
        profile = copy.deepcopy(value)
        max_tokens = profile.get("max_tokens")
        reasoning = profile.get("reasoning")
        if reasoning is None and profile.get("reasoning_effort") is not None:
            reasoning = {"effort": profile["reasoning_effort"]}
        if reasoning is not None and reasoning != {"effort": "low"}:
            raise BudgetStop("Exploratory reasoning configuration is frozen to low or absent.")
        try:
            max_tokens = int(max_tokens)
        except (TypeError, ValueError):
            raise BudgetStop("Exploratory max_tokens must be 4096 or 16384.") from None
        try:
            attempts = int(profile.get("max_physical_attempts", 1))
        except (TypeError, ValueError):
            raise BudgetStop("Exploratory physical-attempt bound is invalid.") from None
        if attempts not in (1, 2):
            raise BudgetStop("Exploratory physical-attempt bound must be one or two.")
        if max_tokens == 4096 and reasoning is None:
            selected = copy.deepcopy(REQUEST_PROFILES[DEFAULT_SERVING_PROFILE])
            selected["max_physical_attempts"] = attempts
            return selected
        if max_tokens == 16384 and reasoning == {"effort": "low"}:
            selected = copy.deepcopy(REQUEST_PROFILES[DEFAULT_BUILDER_PROFILE])
            selected["max_physical_attempts"] = attempts
            return selected
        raise BudgetStop("Exploratory request profile is not frozen.")
    aliases = {"builder_16384_low": DEFAULT_BUILDER_PROFILE, "serving": DEFAULT_SERVING_PROFILE}
    name = aliases.get(value, value)
    if name not in REQUEST_PROFILES:
        raise BudgetStop("Unknown exploratory request profile.")
    return copy.deepcopy(REQUEST_PROFILES[name])


def _model_names(model_locks):
    if model_locks is None:
        return [MODEL]
    if not isinstance(model_locks, dict) or not model_locks:
        raise BudgetStop("A non-empty exploratory model-lock mapping is required.")
    names = list(model_locks)
    if any(name != MODEL for name in names):
        raise BudgetStop("Only the frozen exploratory GLM model is supported.")
    return names


def _lock_set(model_locks=None, provider_profile=DEFAULT_PROVIDER_PROFILE, request_profile=DEFAULT_SERVING_PROFILE):
    provider = _provider_profile(provider_profile)
    request = _request_profile(request_profile)
    result = {}
    for model in _model_names(model_locks):
        supplied = (model_locks or {}).get(model) or {}
        if not isinstance(supplied, dict):
            raise BudgetStop("Exploratory model lock is invalid.")
        for key, expected in (
            ("provider", provider["provider"]),
            ("prompt_per_m", provider["prompt_per_m"]),
            ("completion_per_m", provider["completion_per_m"]),
            ("context_length", provider["context_length"]),
        ):
            if key in supplied and supplied[key] != expected:
                raise BudgetStop("Exploratory provider lock changed before the explicit phase freeze.")
        if "max_tokens" in supplied:
            try:
                supplied_max_tokens = int(supplied["max_tokens"])
            except (TypeError, ValueError):
                raise BudgetStop("Exploratory request lock has an invalid max_tokens value.") from None
            if supplied_max_tokens not in {request["max_tokens"], MAX_COMPLETION}:
                raise BudgetStop("Exploratory request lock has an unexpected max_tokens value.")
        if "max_completion_tokens" in supplied:
            try:
                if int(supplied["max_completion_tokens"]) != MAX_COMPLETION:
                    raise BudgetStop("Exploratory request lock has an unexpected max_completion_tokens value.")
            except (TypeError, ValueError):
                raise BudgetStop("Exploratory request lock has an invalid max_completion_tokens value.") from None
        if "reasoning" in supplied and supplied["reasoning"] != request["reasoning"]:
            raise BudgetStop("Exploratory request reasoning lock changed.")
        if "reasoning_effort" in supplied:
            expected_effort = (request["reasoning"] or {}).get("effort")
            if supplied["reasoning_effort"] != expected_effort:
                raise BudgetStop("Exploratory request reasoning lock changed.")
        lock = {
            "provider": provider["provider"],
            "prompt_per_m": provider["prompt_per_m"],
            "completion_per_m": provider["completion_per_m"],
            "context_length": provider["context_length"],
            "max_tokens": request["max_tokens"],
            "reasoning": copy.deepcopy(request["reasoning"]),
            "max_physical_attempts": request["max_physical_attempts"],
        }
        lock["reservation_usd"] = str(
            (Decimal(lock["context_length"]) * Decimal(lock["prompt_per_m"]) +
             Decimal(lock["max_tokens"]) * Decimal(lock["completion_per_m"])) / Decimal(1000000)
        )
        if "reservation_usd" in supplied:
            try:
                reservation = Decimal(str(supplied["reservation_usd"]))
            except (ArithmeticError, ValueError):
                raise BudgetStop("Exploratory request reservation bound is invalid.") from None
            if reservation != Decimal(lock["reservation_usd"]):
                raise BudgetStop("Exploratory request reservation bound changed.")
        result[model] = lock
    return result


def _endpoint(metadata, model, provider):
    if not isinstance(metadata, dict):
        raise BudgetStop("Exploratory provider metadata is invalid.")
    data = metadata.get("data") or {}
    if data.get("id") != model or (data.get("architecture") or {}).get("output_modalities") != ["text"]:
        raise BudgetStop("Unexpected exploratory model identity or output modalities.")
    endpoints = [item for item in data.get("endpoints", []) if item.get("tag") == provider["provider"]]
    if len(endpoints) != 1:
        raise BudgetStop("Exploratory provider endpoint is unavailable or ambiguous.")
    return endpoints[0]


def validate_provider_metadata(model, metadata, lock):
    """Validate one exact provider endpoint against a frozen exploratory lock."""
    provider = _provider_profile({"name": lock["provider"], **lock})
    endpoint = _endpoint(metadata, model, provider)
    if endpoint.get("status") != 0:
        raise BudgetStop("Exploratory provider endpoint is not active.")
    if endpoint.get("context_length") != lock["context_length"]:
        raise BudgetStop("Exploratory provider context length changed.")
    max_completion = endpoint.get("max_completion_tokens")
    if not isinstance(max_completion, int) or max_completion < lock["max_tokens"]:
        raise BudgetStop("Exploratory provider cannot enforce the selected output cap.")
    supported = endpoint.get("supported_parameters") or []
    if "max_tokens" not in supported:
        raise BudgetStop("Exploratory provider does not support max_tokens.")
    if lock.get("reasoning") is not None and "reasoning" not in supported:
        raise BudgetStop("Exploratory provider does not support reasoning configuration.")
    pricing = endpoint.get("pricing") or {}
    for key in ("prompt", "completion"):
        try:
            rate = Decimal(str(pricing[key]))
        except (KeyError, ArithmeticError, ValueError):
            raise BudgetStop("Exploratory provider pricing is unavailable.") from None
        cap = Decimal(lock[key + "_per_m"]) / Decimal(1000000)
        if not rate.is_finite() or rate < 0 or rate > cap:
            raise BudgetStop("Exploratory provider price exceeds its frozen ceiling.")
    for key, value in pricing.items():
        if key in {"prompt", "completion", "discount"}:
            continue
        try:
            rate = Decimal(str(value))
        except (ArithmeticError, ValueError):
            raise BudgetStop("Exploratory provider fee type cannot be bounded.") from None
        if key == "input_cache_read" and rate.is_finite() and 0 <= rate <= Decimal(lock["prompt_per_m"]) / Decimal(1000000):
            continue
        if not rate.is_finite() or rate != 0:
            raise BudgetStop("Exploratory provider has an unbounded additional fee.")
    return copy.deepcopy(lock)


def validate_locks(
    model_locks=None,
    *,
    provider_profile=DEFAULT_PROVIDER_PROFILE,
    request_profile=DEFAULT_SERVING_PROFILE,
    metadata_fetcher=fetch_metadata,
    sleep=time.sleep,
):
    """Validate exact endpoint metadata with bounded free GET retries."""
    locks = _lock_set(model_locks, provider_profile, request_profile)
    result = {}
    for model, lock in locks.items():
        for attempt in range(1, FREE_METADATA_ATTEMPTS + 1):
            try:
                metadata = metadata_fetcher(model)
            except BudgetStop:
                if attempt == FREE_METADATA_ATTEMPTS:
                    raise BudgetStop("Exploratory provider metadata unavailable after 3 free GET attempts.") from None
                sleep(FREE_METADATA_RETRY_SECONDS)
                continue
            result[model] = validate_provider_metadata(model, metadata, lock)
            break
    return result


def _canonical_call_id(call_id):
    value = str(call_id)
    for kind in ("v9/", "explore/", "builder/"):
        nested = kind + CALL_PREFIX
        if value.startswith(nested):
            value = kind + value[len(nested):]
            break
    return value if value.startswith(CALL_PREFIX) else CALL_PREFIX + value


def _canonical_episode(episode):
    value = str(episode)
    if not value.startswith(CALL_PREFIX):
        raise BudgetStop("Exploratory episode IDs must start with selective_20260915/.")
    return value


def _authorization_body(path=AUTHORIZATION_PATH):
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise BudgetStop("Explicit exploratory budget authorization is missing or invalid.") from None
    digest = body.get("authorization_sha256")
    unsigned = {key: value for key, value in body.items() if key != "authorization_sha256"}
    if not isinstance(digest, str) or hashlib.sha256(canonical(unsigned).encode()).hexdigest() != digest:
        raise BudgetStop("Exploratory budget authorization hash changed.")
    if body.get("schema") != "selective-explore-budget-authorization/1":
        raise BudgetStop("Unexpected exploratory budget authorization schema.")
    if body.get("namespace") != EXPLORE_NAMESPACE:
        raise BudgetStop("Exploratory budget authorization namespace changed.")
    if body.get("shared_ledger") != str(SHARED_LEDGER_PATH) or body.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH):
        raise BudgetStop("Exploratory authorization does not name the shared ledger.")
    if body.get("total_occupied_ceiling_usd") != str(TOTAL_LIMIT_USD) or body.get("new_tranche_occupied_ceiling_usd") != str(TRANCHE_LIMIT_USD):
        raise BudgetStop("Exploratory authorization ceilings changed.")
    ids = body.get("baseline_call_ids")
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or any(not isinstance(value, str) for value in ids):
        raise BudgetStop("Exploratory authorization baseline call IDs are invalid.")
    return body


def _read_baseline(ledger_path=SHARED_LEDGER_PATH):
    path = Path(ledger_path).resolve()
    if path != SHARED_LEDGER_PATH:
        raise BudgetStop("Exploratory authorization must use the shared ledger.")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as db:
            rows = db.execute("SELECT rowid,id,created FROM calls ORDER BY rowid").fetchall()
            limit = db.execute("SELECT value FROM settings WHERE key='limit_nano'").fetchone()
    except sqlite3.Error:
        raise BudgetStop("Cannot read the shared ledger for authorization.") from None
    if not limit or int(limit[0]) != LEGACY_LIMIT_NANO:
        raise BudgetStop("Legacy USD 10 ledger setting changed; no exploratory authorization.")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as db:
        amounts = db.execute("SELECT state,reserved_nano,actual_nano FROM calls ORDER BY rowid").fetchall()
    actual = sum(row[2] or 0 for row in amounts)
    unresolved = sum(row[1] for row in amounts if row[0] != "settled")
    return {
        "baseline_call_ids": [str(row[1]) for row in rows],
        "baseline_max_rowid": int(rows[-1][0]) if rows else 0,
        "baseline_max_created": rows[-1][2] if rows else None,
        "baseline_calls": len(rows),
        "baseline_actual_nano": int(actual),
        "baseline_unresolved_reserved_nano": int(unresolved),
    }


def authorize_tranche(
    request_path=AUTHORIZATION_REQUEST_PATH,
    output_path=AUTHORIZATION_PATH,
    *,
    ledger_path=SHARED_LEDGER_PATH,
):
    """Snapshot the current ledger baseline after checking explicit user auth."""
    try:
        request = json.loads(Path(request_path).read_text())
    except (OSError, ValueError):
        raise BudgetStop("Explicit user budget extension request is missing or invalid.") from None
    if request.get("additional_usd") != "10" or request.get("total_occupied_ceiling_usd") != "20" or request.get("new_tranche_occupied_ceiling_usd") != "10":
        raise BudgetStop("Explicit authorization is not the USD 10 exploratory tranche.")
    if request.get("shared_ledger") != str(SHARED_LEDGER_PATH) or request.get("preserve_all_unknown_fees") is not True:
        raise BudgetStop("Explicit authorization does not preserve the shared unknown fees.")
    lock_path = SHARED_RUN_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    destination = Path(output_path)
    request_digest = hashlib.sha256(Path(request_path).read_bytes()).hexdigest()
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another runner owns the shared revision run lock.") from None
        try:
            if destination.exists():
                existing = _authorization_body(destination)
                if (
                    existing.get("source_request") == str(Path(request_path).resolve())
                    and existing.get("source_request_sha256") == request_digest
                ):
                    return existing
                raise BudgetStop("Exploratory authorization already exists for a different request.")
            baseline = _read_baseline(ledger_path)
            for key in ("baseline_max_rowid", "baseline_calls", "baseline_actual_nano", "baseline_unresolved_reserved_nano"):
                if request.get(key) is not None and int(request[key]) != int(baseline[key]):
                    raise BudgetStop("Shared ledger changed since the authorization request baseline.")
            body = {
                "schema": "selective-explore-budget-authorization/1",
                "namespace": EXPLORE_NAMESPACE,
                "shared_ledger": str(SHARED_LEDGER_PATH),
                "shared_run_lock": str(SHARED_RUN_LOCK_PATH),
                "total_occupied_ceiling_usd": str(TOTAL_LIMIT_USD),
                "new_tranche_occupied_ceiling_usd": str(TRANCHE_LIMIT_USD),
                "legacy_limit_usd": str(LEGACY_MAX_USD),
                "source_request": str(Path(request_path).resolve()),
                "source_request_sha256": hashlib.sha256(Path(request_path).read_bytes()).hexdigest(),
                **baseline,
                "created_unix": time.time(),
            }
            body["authorization_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
            base.atomic_json(destination, body)
            return body
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class SelectiveExploreLedger(base.SelectiveLedger):
    """Selective ledger guard with explicit total and tranche reservations."""

    def __init__(self, path=SHARED_LEDGER_PATH, *, authorization_path=AUTHORIZATION_PATH, now=time.time, host_guard=None):
        resolved = Path(path).resolve()
        if resolved != SHARED_LEDGER_PATH:
            raise BudgetStop("Exploratory ledger must be the shared revision ledger.")
        self.authorization_path = Path(authorization_path).resolve()
        self.authorization = _authorization_body(self.authorization_path)
        super().__init__(resolved, now=now, host_guard=host_guard)

    def record_error(self, call_id, exc):
        """Persist inherited safe metadata plus a redacted HTTP error envelope."""
        result = super().record_error(call_id, exc)
        safe_call_id = self._call_id(call_id)
        fields = _safe_http_error_fields(exc)
        with self.connect() as db:
            row = db.execute(
                "SELECT safe_json FROM error_metadata_v2 WHERE call_id=?", (safe_call_id,)
            ).fetchone()
            if row is None:
                return result
            try:
                metadata = json.loads(row[0])
            except (TypeError, ValueError):
                metadata = {}
            metadata = _scrub_error_metadata(metadata)
            metadata.update(fields)
            db.execute(
                "UPDATE error_metadata_v2 SET safe_json=? WHERE call_id=?",
                (canonical(metadata), safe_call_id),
            )
        return result

    def _authorization_check(self, db):
        baseline_ids = set(self.authorization["baseline_call_ids"])
        rows = db.execute("SELECT id,state,reserved_nano,actual_nano FROM calls").fetchall()
        current_ids = {str(row[0]) for row in rows}
        if not baseline_ids <= current_ids:
            raise BudgetStop("Exploratory baseline receipts changed; no paid call.")
        total = sum((row[3] or 0) + (row[2] if row[1] != "settled" else 0) for row in rows)
        new_rows = [row for row in rows if str(row[0]) not in baseline_ids]
        tranche = sum((row[3] or 0) + (row[2] if row[1] != "settled" else 0) for row in new_rows)
        return total, tranche

    def reserve(self, call_id, episode, model, request_sha, reservation):
        self._guard()
        episode = _canonical_episode(episode)
        if self.paid_failures_blocked():
            raise BudgetStop("Three consecutive unresolved physical failures stopped exploratory paid work.")
        amount = nano_usd(reservation)
        safe_call_id = _canonical_call_id(call_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM calls WHERE id=?", (safe_call_id,)).fetchone():
                raise BudgetStop("Exploratory request ID already exists; no resend.")
            if db.execute("SELECT 1 FROM calls WHERE state='overrun'").fetchone():
                raise BudgetStop("A provider exceeded its exploratory bound; paid work remains stopped.")
            total, tranche = self._authorization_check(db)
            if total + amount > TOTAL_LIMIT_NANO:
                raise BudgetStop("Exploratory total USD 20 occupied ceiling would be exceeded.")
            if tranche + amount > TRANCHE_LIMIT_NANO:
                raise BudgetStop("Exploratory new-tranche USD 10 occupied ceiling would be exceeded.")
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
        with self.connect() as db:
            rows = db.execute("SELECT id,state,reserved_nano,actual_nano FROM calls").fetchall()
        overrun = any(row[1] == "overrun" for row in rows)
        total = sum((row[3] or 0) + (row[2] if row[1] != "settled" else 0) for row in rows)
        baseline_ids = set(self.authorization["baseline_call_ids"])
        new_rows = [row for row in rows if str(row[0]) not in baseline_ids]
        tranche = sum((row[3] or 0) + (row[2] if row[1] != "settled" else 0) for row in new_rows)
        result.update(
            selective_namespace=EXPLORE_NAMESPACE,
            limit_usd=str(TOTAL_LIMIT_USD),
            budget_occupied_usd=str(Decimal(total) / Decimal(1000000000)),
            available_usd=str(max(Decimal("0"), TOTAL_LIMIT_USD - Decimal(total) / Decimal(1000000000))),
            total_limit_usd=str(TOTAL_LIMIT_USD),
            legacy_limit_usd=str(LEGACY_MAX_USD),
            tranche_occupied_usd=str(Decimal(tranche) / Decimal(1000000000)),
            tranche_available_usd=str(max(Decimal("0"), TRANCHE_LIMIT_USD - Decimal(tranche) / Decimal(1000000000))),
            tranche_limit_usd=str(TRANCHE_LIMIT_USD),
        )
        result["blocked"] = bool(
            overrun or result.get("paid_failures_blocked") or total >= TOTAL_LIMIT_NANO or tranche >= TRANCHE_LIMIT_NANO
        )
        return result

    @staticmethod
    def _call_id(call_id):
        return _canonical_call_id(call_id)

    @staticmethod
    def _episode_id(episode):
        return _canonical_episode(episode)


class SelectiveExploreClient(BudgetClientV9):
    """Generic provider/profile client with conservative one-attempt default."""

    def __init__(self, ledger, model_locks, sdk, metadata_fetcher=fetch_metadata, sleep=time.sleep):
        super().__init__(ledger, model_locks, sdk, metadata_fetcher=metadata_fetcher, sleep=sleep)
        self.max_physical_attempts = max(
            int(lock.get("max_physical_attempts", 1)) for lock in model_locks.values()
        )
        if self.max_physical_attempts < 1 or self.max_physical_attempts > MAX_PHYSICAL_ATTEMPTS:
            raise BudgetStop("Exploratory physical-attempt bound is invalid.")

    def create(self, **kwargs):
        if not self.episode:
            raise BudgetStop("A durable exploratory episode id is required.")
        if set(kwargs) - {"model", "messages", "temperature"}:
            raise BudgetStop("Unapproved exploratory request options.")
        model = kwargs.get("model")
        if model not in self.model_locks:
            raise BudgetStop("Exploratory model was not prepared.")
        lock = self.model_locks[model]
        self.sequence += 1
        logical_id = f"explore/{self.episode}/execution-{self.execution_id}/logical-{self.sequence:04d}"
        extra_body = {
            "provider": {
                "only": [lock["provider"]],
                "allow_fallbacks": False,
                "require_parameters": True,
                "max_price": {
                    "prompt": float(lock["prompt_per_m"]),
                    "completion": float(lock["completion_per_m"]),
                    "request": 0,
                },
            },
            "usage": {"include": True},
        }
        if lock.get("reasoning") is not None:
            extra_body["reasoning"] = copy.deepcopy(lock["reasoning"])
        payload = dict(
            kwargs,
            max_tokens=lock["max_tokens"],
            stream=False,
            extra_body=extra_body,
        )
        if not hasattr(self, "_lossless_transport"):
            self._lossless_transport = LosslessTransport()
        payload, transport = self._lossless_transport.prepare(payload)
        self.ledger.record_transport(logical_id, self.episode, transport)
        if not transport["within_local_limit"]:
            raise TransportPayloadStop("Exploratory serialized request exceeds the local limit; no paid request sent.")
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        validated_explore_metadata(model, lock, self.metadata_fetcher, self.sleep)
        for attempt in range(1, self.max_physical_attempts + 1):
            call_id = f"{logical_id}/attempt-{attempt}"
            while True:
                try:
                    self.ledger.reserve(call_id, self.episode, model, request_sha, lock["reservation_usd"])
                    break
                except PacingWait as wait:
                    self.sleep(min(wait.seconds, 10.0))
            try:
                response = self.sdk.chat.completions.create(**payload)
            except BaseException as exc:
                self.ledger.record_error(call_id, exc)
                status = getattr(exc, "status_code", None)
                if status in {429, 503} and attempt < self.max_physical_attempts:
                    self.ledger.finish_pacing(self.ledger.now() + RETRY_MIN_SECONDS)
                    continue
                raise BudgetStop("Exploratory physical request failed; reserve retained and no blind resend.") from None
            try:
                raw = response.model_dump(mode="json")
                self.ledger.preserve_response(call_id, raw)
                problem = response_problem(raw)
                try:
                    nano_usd((raw.get("usage") or {}).get("cost"))
                    bill_known = True
                except BudgetStop:
                    bill_known = False
                if bill_known:
                    self.ledger.settle(call_id, response)
            except BaseException:
                self.ledger.record_error(call_id, MissingBillEvidence(raw) if "raw" in locals() else BudgetStop("Invalid exploratory response."))
                raise BudgetStop("Exploratory response could not be validated; reserve retained.") from None
            if problem is not None:
                self.ledger.record_error(call_id, problem)
                if getattr(problem, "status_code", None) in {429, 503} and attempt < self.max_physical_attempts:
                    self.ledger.finish_pacing(self.ledger.now() + RETRY_MIN_SECONDS)
                    continue
                raise BudgetStop("Exploratory provider error; reserve retained and no further blind retry.")
            if not bill_known:
                self.ledger.record_error(call_id, MissingBillEvidence(raw))
                raise BudgetStop("Exploratory answer lacks settled billing evidence; reserve retained.") from None
            self.ledger.finish_pacing()
            return response
        raise BudgetStop("No exploratory physical attempts remain.")


def validated_explore_metadata(model, lock, fetcher=fetch_metadata, sleep=time.sleep):
    for attempt in range(1, FREE_METADATA_ATTEMPTS + 1):
        try:
            metadata = fetcher(model)
        except BudgetStop:
            if attempt == FREE_METADATA_ATTEMPTS:
                raise BudgetStop("Exploratory provider metadata unavailable after 3 free GET attempts.") from None
            sleep(FREE_METADATA_RETRY_SECONDS)
            continue
        return validate_provider_metadata(model, metadata, lock)
    raise BudgetStop("No exploratory metadata attempts remain.")


def real_client(ledger, locks, env_path):
    import httpx
    from openai import OpenAI

    sdk = OpenAI(
        api_key=credentials(env_path),
        base_url=OFFICIAL_BASE,
        max_retries=0,
        timeout=180,
        http_client=httpx.Client(follow_redirects=False, trust_env=False, timeout=180),
    )
    return SelectiveExploreClient(ledger, locks, sdk)


def make_ledger(*, authorization_path=AUTHORIZATION_PATH, now=time.time, host_guard=None):
    return SelectiveExploreLedger(
        SHARED_LEDGER_PATH,
        authorization_path=authorization_path,
        now=now,
        host_guard=host_guard,
    )


ledger = make_ledger


def make_client(
    model_locks=None,
    env_path=None,
    *,
    ledger=None,
    sdk=None,
    episode=None,
    profile=DEFAULT_SERVING_PROFILE,
    provider_profile=DEFAULT_PROVIDER_PROFILE,
    metadata_fetcher=None,
    sleep=time.sleep,
    host_guard=None,
    authorization_path=AUTHORIZATION_PATH,
):
    locks = _lock_set(model_locks, provider_profile, profile)
    guard = host_guard if host_guard is not None else (lambda: base.read_host_state())
    target_ledger = ledger if ledger is not None else make_ledger(authorization_path=authorization_path, host_guard=guard)
    if not isinstance(target_ledger, SelectiveExploreLedger):
        raise BudgetStop("Exploratory client requires its tranche ledger.")
    target_ledger.host_guard = guard
    target_env = Path(env_path).resolve() if env_path else DEFAULT_ENV_PATH
    if sdk is None:
        if target_ledger.path.resolve() != SHARED_LEDGER_PATH:
            raise BudgetStop("Exploratory production calls must use the shared ledger.")
        client = real_client(target_ledger, locks, target_env)
    else:
        client = SelectiveExploreClient(target_ledger, locks, sdk, metadata_fetcher=metadata_fetcher or fetch_metadata, sleep=sleep)
    client.sdk = base._GuardedSDK(client.sdk, guard)
    if metadata_fetcher is not None:
        client.metadata_fetcher = metadata_fetcher
    client.sleep = sleep
    client.host_guard = guard
    client.call_prefix = CALL_PREFIX
    original_begin = client.begin_episode

    def begin_episode(value):
        _canonical_episode(value)
        return original_begin(value)

    client.begin_episode = begin_episode
    if episode is not None:
        begin_episode(episode)
    return client


def make_builder_client(
    model_locks=None,
    env_path=None,
    *,
    profile=DEFAULT_BUILDER_PROFILE,
    provider_profile=DEFAULT_PROVIDER_PROFILE,
    **kwargs,
):
    return make_client(
        model_locks,
        env_path,
        profile=profile,
        provider_profile=provider_profile,
        **kwargs,
    )


def freeze_phase_manifest(
    phase,
    *,
    model_locks=None,
    provider_profile=DEFAULT_PROVIDER_PROFILE,
    request_profile=DEFAULT_SERVING_PROFILE,
    path=None,
    authorization_path=AUTHORIZATION_PATH,
    extra=None,
):
    """Freeze one explicit provider/request phase before paid execution."""
    locks = _lock_set(model_locks, provider_profile, request_profile)
    authorization = _authorization_body(authorization_path)
    body = {
        "schema": "selective-explore-phase/1",
        "namespace": EXPLORE_NAMESPACE,
        "phase": str(phase),
        "provider_profile": _provider_profile(provider_profile),
        "request_profile": _request_profile(request_profile),
        "model_locks": locks,
        "shared_ledger": str(SHARED_LEDGER_PATH),
        "shared_run_lock": str(SHARED_RUN_LOCK_PATH),
        "total_occupied_ceiling_usd": str(TOTAL_LIMIT_USD),
        "new_tranche_occupied_ceiling_usd": str(TRANCHE_LIMIT_USD),
        "authorization_path": str(Path(authorization_path).resolve()),
        "authorization_sha256": authorization["authorization_sha256"],
        "source_sha256": source_hashes(),
        "runtime": runtime_manifest(),
        "created_unix": time.time(),
    }
    if extra:
        body["extra"] = copy.deepcopy(extra)
    body["phase_manifest_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    destination = Path(path) if path is not None else EXPLORE_OUT / f"phase_{phase}.json"
    base.atomic_json(destination, body)
    return body


def load_phase_manifest(path):
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise BudgetStop("Exploratory phase manifest is unavailable or invalid.") from None
    body = {key: value for key, value in manifest.items() if key != "phase_manifest_sha256"}
    if manifest.get("phase_manifest_sha256") != hashlib.sha256(canonical(body).encode()).hexdigest():
        raise BudgetStop("Exploratory phase manifest hash changed.")
    if manifest.get("schema") != "selective-explore-phase/1" or manifest.get("namespace") != EXPLORE_NAMESPACE:
        raise BudgetStop("Unexpected exploratory phase manifest schema.")
    if manifest.get("shared_ledger") != str(SHARED_LEDGER_PATH) or manifest.get("shared_run_lock") != str(SHARED_RUN_LOCK_PATH):
        raise BudgetStop("Exploratory phase paths changed.")
    if manifest.get("total_occupied_ceiling_usd") != str(TOTAL_LIMIT_USD) or manifest.get("new_tranche_occupied_ceiling_usd") != str(TRANCHE_LIMIT_USD):
        raise BudgetStop("Exploratory phase budget ceilings changed.")
    authorization_path = Path(manifest.get("authorization_path", AUTHORIZATION_PATH))
    authorization = _authorization_body(authorization_path)
    if manifest.get("authorization_sha256") != authorization["authorization_sha256"]:
        raise BudgetStop("Exploratory authorization baseline changed.")
    if manifest.get("source_sha256") != source_hashes() or manifest.get("runtime") != runtime_manifest():
        raise BudgetStop("Exploratory phase source or runtime changed.")
    return manifest


def source_hashes():
    names = (
        "guiexp_android/selective_explore_budget.py",
        "guiexp_android/selective_budget.py",
        "guiexp_android/budget_client_v9.py",
        "guiexp_android/budget_client_v2.py",
        "guiexp_android/lossless_transport_v6.py",
    )
    return {name: hashlib.sha256((ROOT / "computer-use" / name).read_bytes()).hexdigest() for name in names}


def runtime_manifest():
    return base.runtime_manifest()


# The serving harness consumes this exact adapter name before every physical
# UI action.  Reuse the established bounded host reader without duplicating
# its power-state parser here.
before_ui_action = base.before_ui_action
read_host_state = base.read_host_state


@contextlib.contextmanager
def exclusive_run(*, identity_path=None, now=time.time, mode=None):
    lock_path = SHARED_RUN_LOCK_PATH
    record_path = Path(identity_path) if identity_path is not None else EXPLORE_OUT / "run_identity.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another runner owns the shared revision run lock.") from None
        identity = {
            "namespace": EXPLORE_NAMESPACE,
            "pid": os.getpid(),
            "lock_path": str(lock_path),
            "started_unix": now(),
            "status": "active",
        }
        if mode is not None:
            identity["mode"] = str(mode)
        base.atomic_json(record_path, identity)
        try:
            yield identity
        except BaseException as exc:
            identity.update(status="failed", error_type=type(exc).__name__, ended_unix=now())
            base.atomic_json(record_path, identity)
            raise
        else:
            identity.update(status="ended", ended_unix=now())
            base.atomic_json(record_path, identity)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", nargs="?", choices=("authorize-tranche",))
    parser.add_argument("--authorize-tranche", action="store_true")
    parser.add_argument("--request", type=Path, default=AUTHORIZATION_REQUEST_PATH)
    parser.add_argument("--out", type=Path, default=AUTHORIZATION_PATH)
    args = parser.parse_args(argv)
    if args.command == "authorize-tranche" or args.authorize_tranche:
        result = authorize_tranche(args.request, args.out)
        print(json.dumps({"status": "authorized", "baseline_calls": result["baseline_calls"], "baseline_max_rowid": result["baseline_max_rowid"]}, indent=2))
        return 0
    parser.error("use authorize-tranche or --authorize-tranche")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetStop as exc:
        raise SystemExit(str(exc)) from None


__all__ = [
    "AUTHORIZATION_PATH",
    "AUTHORIZATION_REQUEST_PATH",
    "BUILDER_PROFILE",
    "CALL_PREFIX",
    "DEFAULT_BUILDER_PROFILE",
    "DEFAULT_ENV_PATH",
    "DEFAULT_PROVIDER_PROFILE",
    "DEFAULT_SERVING_PROFILE",
    "EXPLORE_NAMESPACE",
    "EXPLORE_OUT",
    "MODEL",
    "REQUEST_PROFILES",
    "SHARED_LEDGER_PATH",
    "SHARED_RUN_LOCK_PATH",
    "SelectiveExploreClient",
    "SelectiveExploreLedger",
    "TOTAL_LIMIT_USD",
    "TRANCHE_LIMIT_USD",
    "authorize_tranche",
    "before_ui_action",
    "exclusive_run",
    "freeze_phase_manifest",
    "ledger",
    "load_phase_manifest",
    "make_client",
    "make_ledger",
    "real_client",
    "read_host_state",
    "runtime_manifest",
    "source_hashes",
    "validate_locks",
    "validate_provider_metadata",
]
