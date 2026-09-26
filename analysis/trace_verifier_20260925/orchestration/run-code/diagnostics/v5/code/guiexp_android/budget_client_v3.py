"""Two bounded physical transport attempts per logical model request.

Every attempt has a unique receipt and a full budget reservation. Unknown
bills stay occupied. Retrying occurs before any response reaches agent.act,
so no action or conversation-history update is replayed.
"""
from __future__ import annotations

import hashlib
import time

from .budget_client import BudgetStop, OFFICIAL_BASE, canonical, credentials, fetch_metadata, validate_metadata
from .budget_client_v2 import BudgetClientV2, BudgetLedgerV2, PacingWait

RETRYABLE_TRANSPORT_ERRORS = frozenset({"APIConnectionError", "APITimeoutError", "RateLimitError"})
MAX_PHYSICAL_ATTEMPTS = 2
RETRY_MIN_SECONDS = 60.0


class BudgetLedgerV3(BudgetLedgerV2):
    """Uses the original receipt table and v2 pacing/error metadata tables."""


class BudgetClientV3(BudgetClientV2):
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
        logical_id = f"v3/{self.episode}/logical-{self.sequence:04d}"
        payload = dict(kwargs, max_tokens=lock["max_tokens"], stream=False, extra_body={
            "provider": {"only": [lock["provider"]], "allow_fallbacks": False,
                         "require_parameters": True,
                         "max_price": {"prompt": float(lock["prompt_per_m"]), "completion": float(lock["completion_per_m"]), "request": 0}},
            "usage": {"include": True},
        })
        request_sha = hashlib.sha256(canonical(payload).encode()).hexdigest()
        for attempt in range(1, MAX_PHYSICAL_ATTEMPTS + 1):
            call_id = f"{logical_id}/attempt-{attempt}"
            # These failures occur before sending and are never transport
            # retries. They cannot be confused with an older error receipt.
            validate_metadata(model, self.metadata_fetcher(model), expected=lock)
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
    return BudgetClientV3(ledger, locks, sdk)
