"""OpenRouter transport with exponential backoff on 429 / 5xx / timeouts.

A thin wrapper around the OpenAI SDK client the agent already uses: the
SDK's own `max_retries=2` stays on, and this wrapper adds longer,
rate-limit-aware exponential backoff so one wedged minute cannot lose a
paid episode. The API key is read from the environment exactly as
`guiexp_android.agent` reads it and is never logged or printed.
"""

from __future__ import annotations

import os
import random
import time

MAX_ATTEMPTS = 8
BACKOFF_BASE_S = 5.0
BACKOFF_CAP_S = 120.0


def _retryable(exc: Exception) -> bool:
    """429 / 5xx / connection-and-timeout class errors, by SDK name."""
    name = type(exc).__name__
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError",
                "InternalServerError"):
        return True
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
        return True
    return False


class _Completions:
    def __init__(self, inner, sleep=time.sleep, max_attempts=MAX_ATTEMPTS,
                 clock=time.monotonic):
        self._inner = inner
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._clock = clock

    def create(self, **kwargs):
        delay = BACKOFF_BASE_S
        last = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._inner.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not _retryable(exc) or attempt == self._max_attempts:
                    raise
                last = exc
                # Full jitter around the exponential delay, capped.
                self._sleep(min(BACKOFF_CAP_S, delay) * (0.5 + random.random()))
                delay *= 2.0
        raise last  # pragma: no cover - unreachable


class RetryingClient:
    """Drop-in `client` for AndroidAgent: same surface, retried transport."""

    def __init__(self, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=os.environ["OPENROUTER_BASE_URL"],
                api_key=os.environ["OPENROUTER_API_KEY"],
                timeout=180.0,
                max_retries=2,
            )
        self.chat = type("Chat", (), {
            "completions": _Completions(client.chat.completions),
        })()
