"""Budget and host-safety infrastructure for the 2026-09-15 pilot.

The pilot has its own namespace and manifest, but its accounting remains in
the existing revision ledger.  ``BudgetClientV9`` remains the request state
machine.  This module adds the shared-ledger path, receipt namespace, bounded
host checks, and a durable stop after repeated unresolved physical failures.

No function in this module prints credentials, response content, or raw SDK
errors.  Provider metadata validation is a public, unauthenticated GET and is
kept separate from paid request entry.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import time
import uuid
from decimal import Decimal
from pathlib import Path

from .budget_client import BudgetStop, MAX_COMPLETION, MAX_USD, OFFICIAL_BASE, atomic_json, canonical, credentials, fetch_metadata
from .budget_client_v2 import PacingWait
from .budget_client_v9 import (
    BudgetClientV9,
    BudgetLedgerV9,
    FREE_METADATA_ATTEMPTS,
    FREE_METADATA_RETRY_SECONDS,
    MAX_PHYSICAL_ATTEMPTS,
    MODEL_LOCKS_V4 as _V9_MODEL_LOCKS,
    RETRY_MIN_SECONDS,
    RETRYABLE_TRANSPORT_ERRORS,
    TRANSIENT_PROVIDER_STATUS,
    MissingBillEvidence,
    response_problem,
    real_client,
    validate_metadata,
    validated_metadata,
)
from .lossless_transport_v6 import LosslessTransport, MAX_WIRE_BYTES, TransportPayloadStop


# ``matched_run.ROOT`` resolves to the outer ``computer-use`` project root.
ROOT = Path(__file__).resolve().parents[2]
SHARED_REVISION = (ROOT / "experimental-results/guiexp_android/revision_20260913").resolve()
SHARED_LEDGER_PATH = (SHARED_REVISION / "budget.sqlite3").resolve()
SHARED_RUN_LOCK_PATH = (SHARED_REVISION / "run.lock").resolve()
SELECTIVE_OUT = (ROOT / "experimental-results/guiexp_android/selective_20260915").resolve()
PID_RECORD_PATH = SELECTIVE_OUT / "run_identity.json"
DEFAULT_ENV_PATH = (ROOT.parent / ".env").resolve()
# Short aliases used by the harness and by offline checks.
LEDGER_PATH = SHARED_LEDGER_PATH
RUN_LOCK_PATH = SHARED_RUN_LOCK_PATH
DEFAULT_OUT = SELECTIVE_OUT
ENV_PATH = DEFAULT_ENV_PATH

NAMESPACE = "selective_20260915"
CALL_PREFIX = NAMESPACE + "/"
PREFIX = CALL_PREFIX
MAX_CONSECUTIVE_UNKNOWN_FAILURES = 3
HOST_COMMAND_TIMEOUT = 3.0
RUNTIME_CWD = (ROOT / "computer-use").resolve()
RUNTIME_PYTHON = "../.venv-android/bin/python"
RUNTIME_PYTHON_PATH = (RUNTIME_CWD / RUNTIME_PYTHON).resolve()


# Keep this policy local to the pilot.  These are the v9 frozen bounds, with
# the already prepared 1,048,576-token context made explicit so callers can
# use ``MODEL_LOCKS`` directly without first making a paid request.
_FROZEN_CONTEXT_LENGTH = 1048576
MODEL_LOCKS = {}
for _model, _policy in _V9_MODEL_LOCKS.items():
    _lock = dict(_policy, context_length=_FROZEN_CONTEXT_LENGTH, max_tokens=MAX_COMPLETION)
    _lock["reservation_usd"] = str(
        (Decimal(_FROZEN_CONTEXT_LENGTH) * Decimal(_policy["prompt_per_m"]) +
         Decimal(MAX_COMPLETION) * Decimal(_policy["completion_per_m"])) / 1000000
    )
    MODEL_LOCKS[_model] = _lock
PILOT_MODEL_LOCKS = copy.deepcopy(MODEL_LOCKS)


class HostNotReady(BudgetStop):
    """A safe, non-retryable stop raised when the host is not fully awake."""


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def _run_readonly(command, *, runner=subprocess.run, timeout=HOST_COMMAND_TIMEOUT):
    """Run one bounded, read-only power query without exposing its output."""
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    return _text(getattr(result, "stdout", ""))


def _ioreg_power_flags(text: str):
    """Return ``(lid_open, full_wake, reason)`` from bounded ioreg text."""
    lid = re.search(r'"AppleClamshellState"\s*=\s*(Yes|No)\b', text, re.I)
    lid_open = lid is not None and lid.group(1).lower() == "no"

    hibernate = re.search(r'"IOHibernateState"\s*=\s*<\s*([^>]+?)\s*>', text, re.I)
    hibernate_value = re.sub(r"\s+", "", hibernate.group(1)) if hibernate else ""
    hibernate_awake = bool(hibernate_value) and set(hibernate_value.lower()) <= {"0"}

    wake_type_match = re.search(r'"Wake Type"\s*=\s*"([^"]*)"', text, re.I)
    wake_type = wake_type_match.group(1).strip().lower() if wake_type_match else ""
    darkwake = "darkwake" in wake_type or bool(re.search(r"\bdarkwake\b", text, re.I))

    reasons = []
    if lid is None:
        reasons.append("lid state unavailable")
    elif not lid_open:
        reasons.append("lid closed")
    if not hibernate:
        reasons.append("hibernate state unavailable")
    elif not hibernate_awake:
        reasons.append("host sleeping")
    if darkwake:
        reasons.append("DarkWake")
    # ``IOPMUserTriggeredFullWake`` is not used as current run-mode evidence.
    # The live snapshot had it at ``No`` while independent current-state and
    # latest-Wake evidence established a usable full-wake state.
    full_wake = hibernate_awake and not darkwake
    return lid_open, full_wake, ", ".join(reasons)


_POWER_EVENT_RE = re.compile(
    r"^\s*(?P<timestamp>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+[+-]\d{4})\s+"
    # The event column is separated from its detail by multiple spaces.
    # This excludes ``Wake Requests`` from the standalone event stream.
    r"(?P<event>DarkWake|Wake|Sleep)(?=\s{2,})\s+(?P<detail>.*)$",
    re.I,
)


def _latest_power_event(text: str):
    """Return the newest standalone pmset event, excluding Wake Requests."""
    events = []
    for line in text.splitlines():
        match = _POWER_EVENT_RE.match(line)
        if match:
            events.append({
                "timestamp": match.group("timestamp"),
                "event": match.group("event"),
            })
    return events[-1] if events else None


_SYSTEM_CAPABILITIES_RE = re.compile(
    r"^\s*Current System Capabilities are:\s*(?P<capabilities>.*?)\s*$",
    re.I | re.M,
)
_CURRENT_POWER_STATE_RE = re.compile(
    r"^\s*Current Power State:\s*(?P<state>\d+)\s*$",
    re.I | re.M,
)
# Graphics is the generic capability needed for this UI workload.  CPU,
# Audio, and Network remain recorded as corroborating current-state data.
_REQUIRED_SYSTEM_CAPABILITIES = frozenset({"graphics"})
# This pilot accepts the host's observed pmset value.  It is not the
# unrelated kFullWakeState bitfield used inside Apple's pmconfigd source.
_FULL_WAKE_POWER_STATE = 4


def _systemstate_power_flags(text: str):
    """Return ``(full_wake, reason, details)`` from current ``pmset`` state."""
    capability_matches = list(_SYSTEM_CAPABILITIES_RE.finditer(text))
    power_matches = list(_CURRENT_POWER_STATE_RE.finditer(text))
    reasons = []
    details = {"current_system_capabilities": None, "current_power_state": None}

    if not capability_matches:
        reasons.append("current system capabilities unavailable")
    else:
        capability_sets = [
            frozenset(token.lower() for token in match.group("capabilities").split())
            for match in capability_matches
        ]
        if any(value != capability_sets[0] for value in capability_sets[1:]):
            reasons.append("conflicting current system capabilities")
        else:
            details["current_system_capabilities"] = sorted(capability_sets[0])
            missing = _REQUIRED_SYSTEM_CAPABILITIES - capability_sets[0]
            if missing:
                reasons.append("current system capabilities incomplete")

    if not power_matches:
        reasons.append("current power state unavailable")
    else:
        power_states = {int(match.group("state")) for match in power_matches}
        if len(power_states) != 1:
            reasons.append("conflicting current power state")
        else:
            state = power_states.pop()
            details["current_power_state"] = state
            if state != _FULL_WAKE_POWER_STATE:
                reasons.append(f"current power state {state}")

    return not reasons, ", ".join(reasons), details


def read_host_state(*, runner=subprocess.run, now=time.time):
    """Read a small, bounded host-power snapshot.

    The existing readiness waiter only checked the clamshell property.  The
    pilot also requires an awake hibernate state, the current full-wake power
    state and capabilities, and a newest standalone ``Wake`` event.  The
    output is discarded after parsing.  This function never alters power
    settings or creates a caffeinate assertion.
    """
    ioreg = _run_readonly(
        ["ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"],
        runner=runner,
    )
    pmset = _run_readonly(["pmset", "-g"], runner=runner)
    systemstate = _run_readonly(["pmset", "-g", "systemstate"], runner=runner)
    power_log = _run_readonly(["pmset", "-g", "log"], runner=runner)
    checked = now()
    if ioreg is None or pmset is None or systemstate is None or power_log is None:
        reason = "bounded power-state query failed"
        return {
            "ready": False,
            "lid_open": False,
            "normal_full_wake": False,
            "reason": reason,
            "checked_unix": checked,
        }

    lid_open, full_wake, reason = _ioreg_power_flags(ioreg)
    current_full_wake, state_reason, state_details = _systemstate_power_flags(systemstate)
    full_wake = full_wake and current_full_wake
    reason = ", ".join(filter(None, (reason, state_reason)))
    event = _latest_power_event(power_log)
    if event is None:
        full_wake = False
        reason = ", ".join(filter(None, (reason, "power event unavailable")))
    elif event["event"].lower() != "wake":
        full_wake = False
        reason = ", ".join(filter(None, (reason, event["event"])))
    if re.search(r"\bdarkwake\b", pmset, re.I):
        full_wake = False
        reason = ", ".join(filter(None, (reason, "DarkWake")))
    if not reason and not pmset.strip():
        reason = "pmset power state unavailable"
        full_wake = False
    ready = lid_open and full_wake and not reason
    if not ready and not reason:
        reason = "host is not in normal full wake"
    return {
        "ready": ready,
        "lid_open": lid_open,
        "normal_full_wake": full_wake,
        "latest_power_event": event,
        **state_details,
        "reason": reason,
        "checked_unix": checked,
    }


def host_ready(*, runner=subprocess.run):
    """Return whether the host passes the current full-wake guard."""
    return bool(read_host_state(runner=runner).get("ready"))


# Compatibility names make the guard easy to call from a harness that already
# uses the provider waiter's terminology.
host_awake = host_ready


def _invoke_host_guard(guard):
    """Run an injected guard and fail closed for false or missing evidence."""
    if guard is None:
        evidence = read_host_state()
    else:
        evidence = guard() if callable(guard) else guard
    if evidence is False or evidence is None:
        raise HostNotReady("Host is not in normal full wake; paid requests and UI actions are stopped.")
    if isinstance(evidence, dict) and not evidence.get("ready", False):
        raise HostNotReady("Host is not in normal full wake; paid requests and UI actions are stopped.")
    return evidence


def require_host_ready(guard=None):
    """Raise a safe stop unless the current host state is ready."""
    return _invoke_host_guard(guard)


def before_ui_action(guard=None):
    """Guard an emulator/UI action.  Call immediately before the action."""
    return require_host_ready(guard)


@contextlib.contextmanager
def guard_ui_action(
    episode_id=None,
    action=None,
    episode=None,
    guard=None,
    host_guard=None,
    **_ignored,
):
    """Context-manager form used by the selective serving harness."""
    del episode_id, action, episode
    before_ui_action(host_guard if host_guard is not None else guard)
    yield


@contextlib.contextmanager
def guard_request(
    episode_id=None,
    action=None,
    episode=None,
    guard=None,
    host_guard=None,
    **_ignored,
):
    """Context-manager alias for code that labels a physical call as a request."""
    del episode_id, action, episode
    require_host_ready(host_guard if host_guard is not None else guard)
    yield


class _GuardedCompletions:
    def __init__(self, completions, guard):
        self._completions = completions
        self._guard = guard

    def create(self, **kwargs):
        # This is the SDK transport entry.  A host transition after the
        # reservation but before this call therefore fails closed before the
        # underlying SDK sees the request.
        _invoke_host_guard(self._guard)
        return self._completions.create(**kwargs)

    def __getattr__(self, name):
        return getattr(self._completions, name)


class _GuardedChat:
    def __init__(self, chat, guard):
        self._chat = chat
        completions = getattr(chat, "completions")
        self.completions = _GuardedCompletions(completions, guard)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _GuardedSDK:
    """Delegate an OpenAI SDK while guarding only its physical create call."""

    def __init__(self, sdk, guard):
        self._sdk = sdk
        self.chat = _GuardedChat(sdk.chat, guard)

    def __getattr__(self, name):
        return getattr(self._sdk, name)


class SelectiveLedger(BudgetLedgerV9):
    """A v9 ledger adapter with selective receipt IDs and failure stop state."""

    def __init__(self, path=SHARED_LEDGER_PATH, *, now=time.time, host_guard=None):
        super().__init__(Path(path).resolve(), now=now)
        self.host_guard = host_guard
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS selective_failure_state "
                "(id INTEGER PRIMARY KEY CHECK(id=1), consecutive_unknown INTEGER NOT NULL, "
                "updated REAL NOT NULL, last_call_id TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS selective_unknown_failures "
                "(call_id TEXT PRIMARY KEY, recorded REAL NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO selective_failure_state VALUES (1,0,?,NULL)",
                (self.now(),),
            )

    @staticmethod
    def _call_id(call_id):
        value = str(call_id)
        # v9 and the builder include the episode in their logical ID.  Collapse
        # that nested namespace so receipts have one stable selective prefix.
        for kind in ("v9/", "builder/"):
            nested = kind + CALL_PREFIX
            if value.startswith(nested):
                value = kind + value[len(nested):]
                break
        return value if value.startswith(CALL_PREFIX) else CALL_PREFIX + value

    @staticmethod
    def _episode_id(episode):
        value = str(episode)
        if not value.startswith(CALL_PREFIX):
            raise BudgetStop("Selective episode IDs must start with selective_20260915/.")
        return value

    def _guard(self):
        _invoke_host_guard(self.host_guard)

    def consecutive_unknown_failures(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT consecutive_unknown FROM selective_failure_state WHERE id=1"
            ).fetchone()
        return int(row[0]) if row else 0

    def paid_failures_blocked(self):
        return self.consecutive_unknown_failures() >= MAX_CONSECUTIVE_UNKNOWN_FAILURES

    def _set_unknown_count(self, count, call_id=None):
        with self.connect() as db:
            db.execute(
                "UPDATE selective_failure_state SET consecutive_unknown=?,updated=?,last_call_id=? WHERE id=1",
                (int(count), self.now(), call_id),
            )

    def _reset_unknown_count(self):
        self._set_unknown_count(0, None)

    def reserve(self, call_id, episode, model, request_sha, reservation):
        # The guard is deliberately inside reserve.  This covers every
        # pacing wake-up and every physical retry, not only the first create.
        self._guard()
        episode = self._episode_id(episode)
        if self.paid_failures_blocked():
            raise BudgetStop("Three consecutive unresolved physical failures stopped paid work; ledger retained.")
        return super().reserve(
            self._call_id(call_id), episode, model, request_sha, reservation
        )

    def preserve_response(self, call_id, raw):
        return super().preserve_response(self._call_id(call_id), raw)

    def record_transport(self, logical_id, episode, report):
        return super().record_transport(self._call_id(logical_id), episode, report)

    def settle(self, call_id, response):
        result = super().settle(self._call_id(call_id), response)
        self._reset_unknown_count()
        return result

    def uncertain(self, call_id, error_type):
        return super().uncertain(self._call_id(call_id), error_type)

    def record_error(self, call_id, exc):
        result = super().record_error(self._call_id(call_id), exc)
        # A host transition is known locally and must not consume the
        # unresolved-physical-failure allowance.  Its reservation remains in
        # the v9 ledger because the physical outcome is still conservative.
        if isinstance(exc, HostNotReady):
            return result
        with self.connect() as db:
            row = db.execute(
                "SELECT state,actual_nano FROM calls WHERE id=?", (self._call_id(call_id),)
            ).fetchone()
        if row and row[0] == "uncertain" and row[1] is None:
            safe_call_id = self._call_id(call_id)
            with self.connect() as db:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO selective_unknown_failures VALUES (?,?)",
                    (safe_call_id, self.now()),
                ).rowcount
                if inserted:
                    current = db.execute(
                        "SELECT consecutive_unknown FROM selective_failure_state WHERE id=1"
                    ).fetchone()[0]
                    db.execute(
                        "UPDATE selective_failure_state SET consecutive_unknown=?,updated=?,last_call_id=? WHERE id=1",
                        (int(current) + 1, self.now(), safe_call_id),
                    )
        return result

    def summary(self):
        result = super().summary()
        failures = self.consecutive_unknown_failures()
        result.update(
            selective_namespace=NAMESPACE,
            consecutive_unknown_failures=failures,
            paid_failures_blocked=failures >= MAX_CONSECUTIVE_UNKNOWN_FAILURES,
        )
        result["blocked"] = bool(result.get("blocked") or failures >= MAX_CONSECUTIVE_UNKNOWN_FAILURES)
        return result


def _validate_builder_metadata(model, metadata, expected):
    """Validate provider bounds while applying the larger builder cap."""
    # v9 validates model identity, provider pin, pricing ceilings, context,
    # and max_tokens support.  Its fixed 4096 check is intentionally followed
    # by the builder-specific upper-bound check below.
    checked = validate_metadata(model, metadata)
    data = metadata.get("data", {}) if isinstance(metadata, dict) else {}
    endpoints = [e for e in data.get("endpoints", []) if e.get("tag") == expected["provider"]]
    if len(endpoints) != 1:
        raise BudgetStop("Pinned builder provider endpoint is unavailable or ambiguous.")
    endpoint = endpoints[0]
    max_completion = endpoint.get("max_completion_tokens")
    if not isinstance(max_completion, int) or max_completion < expected["max_tokens"]:
        raise BudgetStop("Pinned builder endpoint cannot enforce its frozen output cap.")
    if "reasoning" not in endpoint.get("supported_parameters", []):
        raise BudgetStop("Pinned builder endpoint does not support the frozen reasoning option.")
    for key in ("provider", "prompt_per_m", "completion_per_m", "context_length"):
        if checked.get(key) != expected.get(key):
            raise BudgetStop("Pinned builder provider bounds changed after preparation.")
    return copy.deepcopy(expected)


def validated_builder_metadata(model, expected=None, fetcher=fetch_metadata, sleep=time.sleep):
    """Retry only free metadata GET failures for a frozen builder profile."""
    if expected is None:
        expected = builder_model_locks(8192)[model]
    for attempt in range(1, FREE_METADATA_ATTEMPTS + 1):
        try:
            response = fetcher(model)
        except BudgetStop:
            if attempt == FREE_METADATA_ATTEMPTS:
                raise BudgetStop("Public metadata unavailable after 3 free GET attempts; no physical builder request sent.") from None
            sleep(FREE_METADATA_RETRY_SECONDS)
            continue
        return _validate_builder_metadata(model, response, expected)
    raise BudgetStop("No free builder metadata attempts remain.")


class BuilderBudgetClient(BudgetClientV9):
    """v9 request state machine with a frozen larger build output profile."""

    def create(self, **kwargs):
        if not self.episode:
            raise BudgetStop("A durable episode id is required.")
        if set(kwargs) - {"model", "messages", "temperature"}:
            raise BudgetStop("Unapproved request options.")
        model = kwargs.get("model")
        if model not in self.model_locks:
            raise BudgetStop("Model was not prepared.")
        lock = self.model_locks[model]
        self.sequence += 1
        logical_id = f"builder/{self.episode}/execution-{self.execution_id}/logical-{self.sequence:04d}"
        # The reasoning option and larger max_tokens are part of the payload
        # before transport measurement, request hashing, and reservation.
        payload = dict(
            kwargs,
            max_tokens=lock["max_tokens"],
            stream=False,
            extra_body={
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
                # OpenAI's Python SDK merges extra_body into the top-level
                # wire JSON.  Keeping reasoning here avoids passing an
                # unsupported SDK keyword while still hashing/reserving the
                # exact request option before any physical attempt.
                "reasoning": copy.deepcopy(lock["reasoning"]),
            },
        )
        if not hasattr(self, "_lossless_transport"):
            self._lossless_transport = LosslessTransport()
        payload, transport = self._lossless_transport.prepare(payload)
        self.ledger.record_transport(logical_id, self.episode, transport)
        if not transport["within_local_limit"]:
            raise TransportPayloadStop(
                f"Local serialized request body {transport['serialized_body_bytes']} bytes exceeds the "
                f"{MAX_WIRE_BYTES}-byte local limit; full history retained and no paid request sent."
            )
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        validated_builder_metadata(model, lock, self.metadata_fetcher, self.sleep)
        for attempt in range(1, MAX_PHYSICAL_ATTEMPTS + 1):
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
                retryable = type(exc).__name__ in RETRYABLE_TRANSPORT_ERRORS or getattr(exc, "status_code", None) in TRANSIENT_PROVIDER_STATUS
                if retryable and attempt < MAX_PHYSICAL_ATTEMPTS:
                    self.ledger.finish_pacing(self.ledger.now() + RETRY_MIN_SECONDS)
                    continue
                raise BudgetStop("Physical builder request failed; bounded attempts exhausted or error is not retryable. All unresolved reserves retained.") from None
            try:
                raw = response.model_dump(mode="json")
                self.ledger.preserve_response(call_id, raw)
                problem = response_problem(raw)
                from .budget_client import nano_usd
                try:
                    nano_usd((raw.get("usage") or {}).get("cost"))
                    bill_known = True
                except BudgetStop:
                    bill_known = False
                if bill_known:
                    self.ledger.settle(call_id, response)
            except BaseException as exc:
                self.ledger.record_error(call_id, exc)
                raise BudgetStop("Builder response structure could not be validated; no answer delivered and reserve retained.") from None
            if problem is not None:
                self.ledger.record_error(call_id, problem)
                if problem.retryable and attempt < MAX_PHYSICAL_ATTEMPTS:
                    self.ledger.finish_pacing(self.ledger.now() + RETRY_MIN_SECONDS)
                    continue
                raise BudgetStop("Provider builder error envelope or empty answer; bounded retry ended and unresolved reserves retained.")
            try:
                if not bill_known:
                    raise BudgetStop("No finite nonnegative provider bill.")
            except BaseException:
                self.ledger.record_error(call_id, MissingBillEvidence(raw))
                raise BudgetStop("Usable builder answer lacks settled billing evidence; response saved and full reserve retained for reconciliation.") from None
            self.ledger.finish_pacing()
            return response
        raise BudgetStop("No physical builder attempts remain.")


def make_ledger(path=None, *, now=time.time, host_guard=None):
    """Open the one absolute ledger shared by old and new experiment code."""
    target = SHARED_LEDGER_PATH if path is None else Path(path).resolve()
    if target != SHARED_LEDGER_PATH:
        raise BudgetStop("Selective pilot must use the shared revision ledger.")
    return SelectiveLedger(target, now=now, host_guard=host_guard)


# ``selective_pilot`` discovers this callable by its historical ``ledger``
# name.  It still resolves only to the one canonical absolute path.
ledger = make_ledger


def ledger_factory(path=None, *, now=time.time, host_guard=None):
    return make_ledger(path, now=now, host_guard=host_guard)


def shared_ledger(path=None, *, now=time.time, host_guard=None):
    return make_ledger(path, now=now, host_guard=host_guard)


def _validate_model_locks(model_locks):
    if not isinstance(model_locks, dict) or not model_locks:
        raise BudgetStop("A non-empty frozen model-lock mapping is required.")
    result = {}
    for model, supplied in model_locks.items():
        lock = supplied
        expected = MODEL_LOCKS.get(model)
        if expected is None or not isinstance(lock, dict):
            raise BudgetStop("Model is outside the selective pilot provider lock.")
        if lock.get("provider") != expected["provider"]:
            raise BudgetStop("Selective pilot provider pin changed.")
        # The pilot spec records the public cap as ``max_completion_tokens``.
        # Expand that compact form to the complete v9 frozen lock before
        # metadata validation and client construction.
        if "max_tokens" not in lock:
            if lock.get("max_completion_tokens") != MAX_COMPLETION:
                raise BudgetStop("Selective pilot output cap must remain 4096 tokens.")
            lock = expected
        if lock.get("max_tokens") != MAX_COMPLETION:
            raise BudgetStop("Selective pilot output cap must remain 4096 tokens.")
        result[model] = copy.deepcopy(expected)
        for key in expected:
            if key in lock and lock[key] != expected[key]:
                raise BudgetStop("Selective pilot frozen model bounds changed.")
    return result


BUILDER_MAX_TOKENS = (8192, 16384)
BUILDER_REASONING_EFFORT = "low"


def _builder_base_locks(model_locks=None):
    """Normalize compact serving locks before deriving a builder profile."""
    if model_locks is None:
        return copy.deepcopy(MODEL_LOCKS)
    if not isinstance(model_locks, dict) or not model_locks:
        raise BudgetStop("A non-empty frozen model-lock mapping is required.")
    result = {}
    for model, supplied in model_locks.items():
        expected = MODEL_LOCKS.get(model)
        if expected is None or not isinstance(supplied, dict):
            raise BudgetStop("Model is outside the selective pilot provider lock.")
        if supplied.get("provider") != expected["provider"]:
            raise BudgetStop("Selective pilot provider pin changed.")
        for key in ("prompt_per_m", "completion_per_m", "context_length"):
            if key in supplied and supplied[key] != expected[key]:
                raise BudgetStop("Selective pilot frozen model bounds changed.")
        result[model] = copy.deepcopy(expected)
    return result


def builder_model_locks(
    max_tokens=8192,
    model_locks=None,
    *,
    reasoning_effort=BUILDER_REASONING_EFFORT,
):
    """Derive a frozen builder lock set from the unchanged serving bounds."""
    if isinstance(max_tokens, str) and max_tokens.startswith("builder_"):
        max_tokens = max_tokens.removeprefix("builder_")
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        raise BudgetStop("Builder max_tokens must be 8192 or 16384.") from None
    if max_tokens not in BUILDER_MAX_TOKENS:
        raise BudgetStop("Builder max_tokens must be 8192 or 16384.")
    if reasoning_effort != BUILDER_REASONING_EFFORT:
        raise BudgetStop("Builder reasoning effort is frozen to low.")
    result = {}
    for model, base in _builder_base_locks(model_locks).items():
        lock = copy.deepcopy(base)
        lock["max_tokens"] = max_tokens
        lock["reasoning"] = {"effort": BUILDER_REASONING_EFFORT}
        lock["reservation_usd"] = str(
            (Decimal(lock["context_length"]) * Decimal(lock["prompt_per_m"]) +
             Decimal(max_tokens) * Decimal(lock["completion_per_m"])) / 1000000
        )
        result[model] = lock
    return result


BUILDER_LOCKS_8192 = builder_model_locks(8192)
BUILDER_LOCKS_16384 = builder_model_locks(16384)
BUILDER_PROFILES = {
    "builder_8192": {
        "name": "builder_8192",
        "max_tokens": 8192,
        "reasoning": {"effort": BUILDER_REASONING_EFFORT},
        "model_locks": copy.deepcopy(BUILDER_LOCKS_8192),
    },
    "builder_16384": {
        "name": "builder_16384",
        "max_tokens": 16384,
        "reasoning": {"effort": BUILDER_REASONING_EFFORT},
        "model_locks": copy.deepcopy(BUILDER_LOCKS_16384),
    },
}


def builder_profile(profile="builder_8192", model_locks=None):
    """Return a copy of one of the two frozen builder profiles."""
    if isinstance(profile, int) or (isinstance(profile, str) and profile.isdigit()):
        profile = f"builder_{profile}"
    if isinstance(profile, dict):
        max_tokens = profile.get("max_tokens")
        reasoning = profile.get("reasoning") or {}
        reasoning_effort = reasoning.get("effort", profile.get("reasoning_effort"))
        if reasoning_effort is None:
            reasoning_effort = BUILDER_REASONING_EFFORT
        locks = builder_model_locks(max_tokens, model_locks, reasoning_effort=reasoning_effort)
        return {
            "name": str(profile.get("name") or f"builder_{int(max_tokens)}"),
            "max_tokens": int(max_tokens),
            "reasoning": {"effort": reasoning_effort},
            "model_locks": locks,
        }
    if profile not in BUILDER_PROFILES:
        raise BudgetStop("Unknown frozen builder profile.")
    frozen = BUILDER_PROFILES[profile]
    locks = builder_model_locks(frozen["max_tokens"], model_locks)
    return {
        "name": frozen["name"],
        "max_tokens": frozen["max_tokens"],
        "reasoning": copy.deepcopy(frozen["reasoning"]),
        "model_locks": locks,
    }


BUILD_PROFILES = BUILDER_PROFILES


def real_builder_client(ledger, locks, env_path):
    """Construct the builder client with the same no-retry transport as v9."""
    import httpx
    from openai import OpenAI

    sdk = OpenAI(
        api_key=credentials(env_path),
        base_url=OFFICIAL_BASE,
        max_retries=0,
        timeout=180,
        http_client=httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=180,
        ),
    )
    return BuilderBudgetClient(ledger, locks, sdk)


def make_client(
    model_locks,
    env_path=None,
    *,
    ledger=None,
    sdk=None,
    host_guard=None,
    metadata_fetcher=None,
    sleep=time.sleep,
    episode=None,
    episode_id=None,
    env_file=None,
    spec=None,
    profile=None,
    max_tokens=None,
    reasoning_effort=None,
):
    """Create a host-guarded v9-compatible client.

    The returned object retains ``client.chat.completions.create`` and
    ``client.begin_episode``.  ``ledger`` and ``sdk`` are optional injection
    points for offline tests.  Production calls use the shared absolute
    ledger and v9's ``real_client`` with SDK retries disabled.
    """
    # ``selective_pilot`` also supports older budget-facade call shapes such
    # as ``make_client(ledger, spec, episode=..., env_file=...)``.  Resolve
    # that shape here while retaining the documented public mapping-first API.
    if isinstance(model_locks, BudgetLedgerV9):
        supplied_ledger = model_locks
        spec_value = env_path if isinstance(env_path, dict) else spec
        ledger = supplied_ledger
        env_path = env_file
        spec_locks = spec_value.get("model_locks") if isinstance(spec_value, dict) else None
        model_name = spec_value.get("model") if isinstance(spec_value, dict) else None
        if not model_name and isinstance(spec_locks, dict) and len(spec_locks) == 1:
            model_name = next(iter(spec_locks))
        if model_name not in MODEL_LOCKS:
            raise BudgetStop("Selective pilot specification has no approved model lock.")
        if isinstance(spec_locks, dict):
            spec_lock = spec_locks.get(model_name) or {}
            if spec_lock.get("provider") not in (None, MODEL_LOCKS[model_name]["provider"]):
                raise BudgetStop("Selective pilot specification provider pin changed.")
        model_locks = {model_name: MODEL_LOCKS[model_name]}
    if episode_id is not None:
        episode = episode_id
    builder_requested = profile is not None or max_tokens is not None or reasoning_effort is not None
    if builder_requested:
        if profile is None:
            profile = max_tokens if max_tokens is not None else "builder_8192"
        if max_tokens is not None and isinstance(profile, str) and profile.removeprefix("builder_").isdigit() and int(profile.removeprefix("builder_")) != int(max_tokens):
            raise BudgetStop("Builder profile and max_tokens disagree.")
        selected_profile = builder_profile(profile, model_locks,)
        if reasoning_effort is not None and reasoning_effort != selected_profile["reasoning"]["effort"]:
            raise BudgetStop("Builder reasoning effort is frozen to low.")
        locks = selected_profile["model_locks"]
        client_type = BuilderBudgetClient
    else:
        locks = _validate_model_locks(model_locks)
        selected_profile = None
        client_type = BudgetClientV9
    guard = host_guard if host_guard is not None else (lambda: read_host_state())
    target_ledger = ledger if ledger is not None else make_ledger(host_guard=guard)
    if isinstance(target_ledger, BudgetLedgerV9) and not isinstance(target_ledger, SelectiveLedger):
        target_ledger = SelectiveLedger(
            target_ledger.path,
            now=getattr(target_ledger, "now", time.time),
            host_guard=guard,
        )
    if not isinstance(target_ledger, SelectiveLedger):
        raise BudgetStop("Selective client requires a BudgetLedgerV9-compatible ledger.")
    target_ledger.host_guard = guard

    if sdk is None:
        if Path(target_ledger.path).resolve() != SHARED_LEDGER_PATH:
            raise BudgetStop("Production selective calls must use the shared revision ledger.")
        if selected_profile is None:
            client = real_client(target_ledger, locks, Path(env_path).resolve() if env_path else DEFAULT_ENV_PATH)
        else:
            client = real_builder_client(target_ledger, locks, Path(env_path).resolve() if env_path else DEFAULT_ENV_PATH)
    else:
        client = client_type(target_ledger, locks, sdk)

    # Keep v9's exact request state machine and disablement checks.  The
    # guarded SDK is installed after construction because v9's client itself
    # owns the public ``chat.completions.create`` adapter.
    client.sdk = _GuardedSDK(client.sdk, guard)
    if metadata_fetcher is not None:
        client.metadata_fetcher = metadata_fetcher
    client.sleep = sleep
    client.host_guard = guard
    client.call_prefix = CALL_PREFIX
    if selected_profile is not None:
        client.builder_profile = selected_profile["name"]
        client.builder_max_tokens = selected_profile["max_tokens"]

    # Keep the v9 implementation and its no-prior-receipt check, while
    # requiring the new namespace at the public episode boundary.
    original_begin_episode = client.begin_episode

    def begin_episode(episode):
        target_ledger._episode_id(episode)
        return original_begin_episode(episode)

    client.begin_episode = begin_episode
    if episode is not None:
        begin_episode(episode)
    return client


def make_builder_client(
    model_locks,
    env_path=None,
    *,
    profile="builder_16384",
    **kwargs,
):
    """Convenience wrapper for an explicitly selected builder profile."""
    return make_client(
        model_locks,
        env_path,
        profile=profile,
        **kwargs,
    )


def validate_locks(model_locks, *, metadata_fetcher=None, sleep=time.sleep):
    """Validate frozen bounds with at most v9's three free metadata GETs."""
    locks = _validate_model_locks(model_locks)
    fetcher = metadata_fetcher
    result = {}
    for model, lock in locks.items():
        kwargs = {"expected": lock, "sleep": sleep}
        if fetcher is not None:
            kwargs["fetcher"] = fetcher
        result[model] = validated_metadata(model, **kwargs)
    return result


def validate_builder_locks(
    model_locks=None,
    profile="builder_8192",
    *,
    metadata_fetcher=None,
    sleep=time.sleep,
):
    """Refresh free metadata for one frozen builder profile."""
    selected = builder_profile(profile, model_locks)
    fetcher = metadata_fetcher or fetch_metadata
    result = {}
    for model, lock in selected["model_locks"].items():
        result[model] = validated_builder_metadata(model, lock, fetcher, sleep)
    return result


def validate_builder_profile(
    profile,
    model_locks=None,
    *,
    metadata_fetcher=None,
    sleep=time.sleep,
):
    """Validate a harness profile and its provider bounds before paid work."""
    if isinstance(profile, str):
        selected = builder_profile(profile, model_locks)
        profile_name = selected["name"]
        model_name = None
    elif isinstance(profile, dict):
        profile_name = profile.get("name") or "builder_profile"
        model_name = profile.get("model")
        requested_provider = profile.get("provider")
        selected = builder_profile(profile, model_locks)
        if requested_provider is not None:
            providers = {lock["provider"] for lock in selected["model_locks"].values()}
            if requested_provider not in providers:
                raise BudgetStop("Builder profile provider pin changed.")
    else:
        raise BudgetStop("Builder profile must be a frozen object or profile name.")
    if model_name is not None:
        if model_name not in selected["model_locks"]:
            raise BudgetStop("Builder profile model is not in its frozen lock set.")
        selected["model_locks"] = {model_name: selected["model_locks"][model_name]}
    validated = validate_builder_locks(
        selected["model_locks"],
        profile=selected["max_tokens"],
        metadata_fetcher=metadata_fetcher,
        sleep=sleep,
    )
    return {
        "name": profile_name,
        "max_tokens": selected["max_tokens"],
        "reasoning": copy.deepcopy(selected["reasoning"]),
        "model_locks": validated,
    }


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    """Return source hashes for this adapter and the immutable v9 stack."""
    names = (
        "guiexp_android/selective_budget.py",
        "guiexp_android/budget_client.py",
        "guiexp_android/budget_client_v2.py",
        "guiexp_android/budget_client_v9.py",
        "guiexp_android/lossless_transport_v6.py",
    )
    return {name: _sha(RUNTIME_CWD / name.removeprefix("computer-use/")) for name in names}


def runtime_manifest():
    """Describe the frozen relative runtime used by the harness."""
    packages = {}
    for name in ("openai", "httpx", "httpcore"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "working_directory": "computer-use",
        "working_directory_absolute": str(RUNTIME_CWD),
        "python": RUNTIME_PYTHON,
        "interpreter": RUNTIME_PYTHON,
        "executable": str(RUNTIME_PYTHON_PATH),
        "packages": packages,
    }


def _frozen_builder_profiles(model_locks=None):
    base = _builder_base_locks(model_locks)
    return {
        name: {
            "name": name,
            "max_tokens": config["max_tokens"],
            "reasoning": copy.deepcopy(config["reasoning"]),
            "model_locks": builder_model_locks(config["max_tokens"], base),
        }
        for name, config in BUILDER_PROFILES.items()
    }


def freeze_manifest(model_locks, path=None, *, extra=None):
    """Write an integrity-checked manifest without making network calls."""
    locks = _validate_model_locks(model_locks)
    manifest_path = Path(path) if path is not None else SELECTIVE_OUT / "manifest.json"
    body = {
        "schema": "guiexp_android/selective-compilation-pilot/20260915",
        "namespace": NAMESPACE,
        "model_locks": locks,
        "budget": {"shared_ledger": str(SHARED_LEDGER_PATH), "limit_usd": str(MAX_USD)},
        "source_sha256": source_hashes(),
        "runtime": runtime_manifest(),
        # Serving remains a separate 4096-token profile.  Builder profiles are
        # frozen alongside it so a later build cannot silently change caps.
        "builder_profiles": _frozen_builder_profiles(locks),
        "prepared_unix": time.time(),
    }
    if extra:
        body["extra"] = copy.deepcopy(extra)
    body["manifest_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    atomic_json(manifest_path, body)
    return body


def load_manifest(path=None):
    """Load and verify a selective pilot manifest before execution."""
    manifest_path = Path(path) if path is not None else SELECTIVE_OUT / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        raise BudgetStop("Selective pilot manifest is unavailable or invalid.") from None
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    expected_hash = hashlib.sha256(canonical(body).encode()).hexdigest()
    if manifest.get("manifest_sha256") != expected_hash:
        raise BudgetStop("Selective pilot manifest hash changed.")
    if manifest.get("schema") != "guiexp_android/selective-compilation-pilot/20260915":
        raise BudgetStop("Unexpected selective pilot manifest schema.")
    if manifest.get("namespace") != NAMESPACE:
        raise BudgetStop("Selective pilot namespace changed.")
    budget = manifest.get("budget") or {}
    if budget.get("shared_ledger") != str(SHARED_LEDGER_PATH) or budget.get("limit_usd") != str(MAX_USD):
        raise BudgetStop("Selective pilot must use the shared USD 10 ledger.")
    if manifest.get("source_sha256") != source_hashes():
        raise BudgetStop("Selective pilot source changed after freezing.")
    if manifest.get("runtime") != runtime_manifest():
        raise BudgetStop("Selective pilot runtime changed after freezing.")
    serving_locks = _validate_model_locks(manifest.get("model_locks"))
    if "builder_profiles" in manifest and manifest["builder_profiles"] != _frozen_builder_profiles(serving_locks):
        raise BudgetStop("Selective builder profiles changed after freezing.")
    return manifest


def freeze_builder_manifest(model_locks=None, path=None, *, extra=None):
    """Freeze the two larger builder profiles independently of serving."""
    base = _builder_base_locks(model_locks)
    profiles = _frozen_builder_profiles(base)
    manifest_path = Path(path) if path is not None else SELECTIVE_OUT / "builder_manifest.json"
    body = {
        "schema": "guiexp_android/selective-builder/20260915",
        "namespace": NAMESPACE,
        "serving_max_tokens": MAX_COMPLETION,
        "models": sorted(base),
        "profiles": profiles,
        "budget": {"shared_ledger": str(SHARED_LEDGER_PATH), "limit_usd": str(MAX_USD)},
        "source_sha256": source_hashes(),
        "runtime": runtime_manifest(),
        "prepared_unix": time.time(),
    }
    if extra:
        body["extra"] = copy.deepcopy(extra)
    body["manifest_sha256"] = hashlib.sha256(canonical(body).encode()).hexdigest()
    atomic_json(manifest_path, body)
    return body


def load_builder_manifest(path=None):
    """Load and verify an independently frozen builder-profile manifest."""
    manifest_path = Path(path) if path is not None else SELECTIVE_OUT / "builder_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        raise BudgetStop("Selective builder manifest is unavailable or invalid.") from None
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != hashlib.sha256(canonical(body).encode()).hexdigest():
        raise BudgetStop("Selective builder manifest hash changed.")
    if manifest.get("schema") != "guiexp_android/selective-builder/20260915":
        raise BudgetStop("Unexpected selective builder manifest schema.")
    if manifest.get("namespace") != NAMESPACE:
        raise BudgetStop("Selective builder namespace changed.")
    budget = manifest.get("budget") or {}
    if budget.get("shared_ledger") != str(SHARED_LEDGER_PATH) or budget.get("limit_usd") != str(MAX_USD):
        raise BudgetStop("Selective builder must use the shared USD 10 ledger.")
    if manifest.get("serving_max_tokens") != MAX_COMPLETION:
        raise BudgetStop("Serving max_tokens changed while loading builder manifest.")
    if manifest.get("source_sha256") != source_hashes():
        raise BudgetStop("Selective builder source changed after freezing.")
    if manifest.get("runtime") != runtime_manifest():
        raise BudgetStop("Selective builder runtime changed after freezing.")
    models = manifest.get("models")
    if not isinstance(models, list) or not models or any(model not in MODEL_LOCKS for model in models):
        raise BudgetStop("Selective builder model registry changed after freezing.")
    expected_locks = {model: MODEL_LOCKS[model] for model in models}
    if manifest.get("profiles") != _frozen_builder_profiles(expected_locks):
        raise BudgetStop("Selective builder profiles changed after freezing.")
    return manifest


freeze_build_manifest = freeze_builder_manifest
load_build_manifest = load_builder_manifest


freeze = freeze_manifest
load = load_manifest


@contextlib.contextmanager
def exclusive_run(
    *,
    identity_path=None,
    now=time.time,
    run_lock=None,
    ledger=None,
    namespace=None,
    mode=None,
    spec=None,
):
    """Own the existing revision lock and record selective process identity."""
    shared_run_lock = Path(SHARED_RUN_LOCK_PATH).resolve()
    shared_ledger = Path(SHARED_LEDGER_PATH).resolve()
    if run_lock is not None and Path(run_lock).resolve() != shared_run_lock:
        raise BudgetStop("Selective pilot must use the existing revision run lock.")
    if ledger is not None and Path(ledger).resolve() != shared_ledger:
        raise BudgetStop("Selective pilot must use the shared revision ledger.")
    if namespace is not None and namespace != NAMESPACE:
        raise BudgetStop("Selective pilot namespace changed.")
    if isinstance(spec, dict):
        if spec.get("run_lock_absolute") and Path(spec["run_lock_absolute"]).resolve() != shared_run_lock:
            raise BudgetStop("Selective pilot must use the existing revision run lock.")
        if spec.get("ledger_absolute") and Path(spec["ledger_absolute"]).resolve() != shared_ledger:
            raise BudgetStop("Selective pilot must use the shared revision ledger.")
    lock_path = shared_run_lock
    record_path = Path(identity_path) if identity_path is not None else PID_RECORD_PATH
    record_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another Android runner owns the shared revision run lock.") from None
        identity = {
            "namespace": NAMESPACE,
            "pid": os.getpid(),
            "lock_path": str(lock_path),
            "started_unix": now(),
            "status": "active",
        }
        if mode is not None:
            identity["mode"] = str(mode)
        atomic_json(record_path, identity)
        try:
            yield identity
        except BaseException as exc:
            identity.update(status="failed", error_type=type(exc).__name__, ended_unix=now())
            atomic_json(record_path, identity)
            raise
        else:
            identity.update(status="ended", ended_unix=now())
            atomic_json(record_path, identity)
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            lock_file.close()


__all__ = [
    "BUILD_PROFILES",
    "BUILDER_LOCKS_16384",
    "BUILDER_LOCKS_8192",
    "BUILDER_MAX_TOKENS",
    "BUILDER_PROFILES",
    "BUILDER_REASONING_EFFORT",
    "BuilderBudgetClient",
    "builder_model_locks",
    "builder_profile",
    "CALL_PREFIX",
    "DEFAULT_OUT",
    "DEFAULT_ENV_PATH",
    "ENV_PATH",
    "HostNotReady",
    "LEDGER_PATH",
    "MODEL_LOCKS",
    "MAX_CONSECUTIVE_UNKNOWN_FAILURES",
    "NAMESPACE",
    "PID_RECORD_PATH",
    "PILOT_MODEL_LOCKS",
    "PREFIX",
    "ROOT",
    "RUN_LOCK_PATH",
    "SELECTIVE_OUT",
    "SHARED_LEDGER_PATH",
    "SHARED_REVISION",
    "SHARED_RUN_LOCK_PATH",
    "SelectiveLedger",
    "before_ui_action",
    "exclusive_run",
    "freeze_builder_manifest",
    "freeze_build_manifest",
    "freeze_manifest",
    "guard_request",
    "guard_ui_action",
    "host_awake",
    "host_ready",
    "ledger_factory",
    "ledger",
    "load_builder_manifest",
    "load_build_manifest",
    "load_manifest",
    "make_builder_client",
    "make_client",
    "make_ledger",
    "read_host_state",
    "real_builder_client",
    "require_host_ready",
    "runtime_manifest",
    "shared_ledger",
    "source_hashes",
    "validate_builder_locks",
    "validate_builder_profile",
    "validate_locks",
    "validated_builder_metadata",
]
