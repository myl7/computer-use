"""Version 5 validates metadata once per logical request with bounded free retries.

Every attempt has a unique receipt and a full budget reservation. Unknown
bills stay occupied. Retrying occurs before any response reaches agent.act,
so no action or conversation-history update is replayed.
"""
from __future__ import annotations

import hashlib
import time

from .budget_client import BudgetStop, OFFICIAL_BASE, MAX_COMPLETION, canonical, credentials, fetch_metadata
from decimal import Decimal
from .budget_client_v2 import BudgetClientV2, BudgetLedgerV2, PacingWait

RETRYABLE_TRANSPORT_ERRORS = frozenset({"APIConnectionError", "APITimeoutError", "RateLimitError"})
MAX_PHYSICAL_ATTEMPTS = 2
RETRY_MIN_SECONDS = 60.0


MODEL_LOCKS_V4 = {
    "z-ai/glm-5.3-flash": {"provider": "relace", "prompt_per_m": "0.09", "completion_per_m": "0.30"},
    "deepseek/deepseek-v4-flash-vision-exp": {"provider": "fireworks", "prompt_per_m": "0.22", "completion_per_m": "0.66"},
}


def validate_metadata(model, metadata, expected=None):
    """Reject unsupported/variable fee types instead of inventing a bound."""
    policy = MODEL_LOCKS_V4[model]
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



FREE_METADATA_ATTEMPTS = 3
FREE_METADATA_RETRY_SECONDS = 5.0


def validated_metadata(model, expected=None, fetcher=fetch_metadata, sleep=time.sleep):
    """Retry the free metadata GET only; invalid returned bounds fail closed."""
    for attempt in range(1, FREE_METADATA_ATTEMPTS + 1):
        try:
            response = fetcher(model)
        except BudgetStop:
            if attempt == FREE_METADATA_ATTEMPTS:
                raise BudgetStop("Public metadata unavailable after 3 free GET attempts; no physical request sent for this logical call.") from None
            sleep(FREE_METADATA_RETRY_SECONDS)
            continue
        # A changed/unsupported price or context is not a network failure.
        # Never retry validation to wait for a more favourable quotation.
        return validate_metadata(model, response, expected=expected)
    raise BudgetStop("No free metadata attempts remain.")


class BudgetLedgerV5(BudgetLedgerV2):
    """Uses the original receipt table and v2 pacing/error metadata tables."""


class BudgetClientV5(BudgetClientV2):
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
        logical_id = f"v5/{self.episode}/logical-{self.sequence:04d}"
        payload = dict(kwargs, max_tokens=lock["max_tokens"], stream=False, extra_body={
            "provider": {"only": [lock["provider"]], "allow_fallbacks": False,
                         "require_parameters": True,
                         "max_price": {"prompt": float(lock["prompt_per_m"]), "completion": float(lock["completion_per_m"]), "request": 0}},
            "usage": {"include": True},
        })
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        # Validate exactly once for this logical request. A retry of the
        # identical payload reuses these fixed provider/token/price bounds.
        # This prevents a second free GET from aborting an eligible physical
        # retry after the original SDK timeout.
        validated_metadata(model, lock, self.metadata_fetcher, self.sleep)
        for attempt in range(1, MAX_PHYSICAL_ATTEMPTS + 1):
            call_id = f"{logical_id}/attempt-{attempt}"
            # These failures occur before sending and are never transport
            # retries. They cannot be confused with an older error receipt.
            while True:
                try:
                    self.ledger.reserve(call_id, self.episode, model, request_sha, lock["reservation_usd"])
                    break
                except PacingWait as wait:
                    self.sleep(min(wait.seconds, 10.0))
            try:
                response = self.sdk.chat.completions.create(**payload)
            except BaseException as exc:
                # This exception belongs to this exact sent physical request.
                # Only these three SDK transport/status errors can retry.
                self.ledger.record_error(call_id, exc)
                retryable = type(exc).__name__ in RETRYABLE_TRANSPORT_ERRORS
                if retryable and attempt < MAX_PHYSICAL_ATTEMPTS:
                    # record_error already preserved a possibly longer
                    # Retry-After deadline in the shared pacing table.
                    self.ledger.finish_pacing(self.ledger.now() + RETRY_MIN_SECONDS)
                    continue
                raise BudgetStop("Physical request failed; bounded attempts exhausted or error is not retryable. All unresolved reserves retained.") from None
            try:
                self.ledger.settle(call_id, response)
            except BaseException as exc:
                # A response with missing/invalid billing is not a transport
                # failure and must never be resent just to obtain a bill.
                self.ledger.record_error(call_id, exc)
                raise BudgetStop("Response billing could not be settled; no retry and full reserve retained.") from None
            self.ledger.finish_pacing()
            return response
        raise BudgetStop("No physical attempts remain.")


def real_client(ledger, locks, env_path):
    import httpx
    from openai import OpenAI
    sdk = OpenAI(api_key=credentials(env_path), base_url=OFFICIAL_BASE,
                 max_retries=0, timeout=180,
                 http_client=httpx.Client(follow_redirects=False, trust_env=False, timeout=180))
    return BudgetClientV5(ledger, locks, sdk)
