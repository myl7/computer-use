"""The agent loop for WebArena: same message contract as guiexp_android.

Layout per turn: system (action space + one-action-per-reply) + full
history + user (goal on the first turn, then the observation). Observation
= unmarked screenshot (image_url) + numbered DOM tree (text), plus the
"where am I" URL line, same as the web guiexp agent.

Every call's usage is captured the same way: prompt/completion tokens,
cached_tokens (OpenRouter usage.prompt_tokens_details.cached_tokens) and
OpenRouter's usage.cost (USD). 429/5xx are retried with exponential
backoff INSIDE the client (the OpenAI SDK retries connection and 429 up
to max_retries with its own backoff; we add a retry-and-backoff wrapper
for 5xx because OpenRouter 5xxs are common under load).
"""

from __future__ import annotations

import os
import random
import time

from .actions import system_prompt

OBS_MODES = ("screenshot+ax",)

SYSTEM_PROMPT = system_prompt()


def _with_backoff(func, *args, max_attempts: int = 6, **kwargs):
    """Exponential backoff around one chat.completions.create call.

    Retries on 429/5xx/network per the protocol; sleeps 2^k seconds with
    jitter, capped at 60s per sleep, ~6 attempts (~3.5 min worst case).
    """
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            status = getattr(exc, "status_code", None)
            retryable = (
                status in (429, 500, 502, 503, 504)
                or status is None  # network/timeout shapes carry no code
            )
            if not retryable or attempt == max_attempts - 1:
                raise
            sleep = min(60.0, 2 ** attempt + random.random())
            print(f"[agent] {type(exc).__name__} (status={status}); "
                  f"backoff {sleep:.1f}s (attempt {attempt + 2}/{max_attempts})", flush=True)
            time.sleep(sleep)
    raise last_exc  # unreachable


class WebAgent:
    def __init__(self, model: str, obs_mode: str = "screenshot+ax", client=None, temperature: float = 0.0):
        if obs_mode not in OBS_MODES:
            raise ValueError(f"unknown obs_mode {obs_mode!r}")
        if client is None:
            from openai import OpenAI

            base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            api_key = os.environ["OPENROUTER_API_KEY"]
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0, max_retries=2)
        self.client = client
        self.model = model
        self.obs_mode = obs_mode
        self.temperature = temperature
        self.history: list[dict] = []

    def act(self, goal_prompt: str | None, obs: dict) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict)."""
        content = self._observation_parts(goal_prompt, obs)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.history:
            messages.extend(self.history)
        messages.append({"role": "user", "content": content})

        response = _with_backoff(
            self.client.chat.completions.create,
            model=self.model, messages=messages, temperature=self.temperature,
        )
        usage = self._usage(response)
        reply = response.choices[0].message.content or ""

        self.history.append({"role": "user", "content": content})
        self.history.append({"role": "assistant", "content": reply})
        return reply, usage

    # -- helpers -----------------------------------------------------------

    def _observation_parts(self, goal_prompt: str | None, obs: dict) -> list[dict]:
        parts: list[dict] = []
        text = ""
        if goal_prompt:
            text += goal_prompt.rstrip("\n") + "\n\n"
        text += f"Current URL: {obs.get('url', '')}\n"
        if obs.get("last_action_error"):
            text += f"Your previous action failed: {obs['last_action_error']}\n"
        parts.append({"type": "text", "text": text})
        image_b64 = obs.get("screenshot_b64")
        if image_b64:
            parts.append({
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + image_b64},
            })
        if obs.get("ax_tree_text"):
            parts.append({
                "type": "text",
                "text": (
                    "Numbered list of UI elements on the current page"
                    " (the numbers are the element ids the actions use):\n"
                    + obs["ax_tree_text"]
                ),
            })
        return parts

    @staticmethod
    def _usage(response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": None, "cost_usd": None}
        cost = getattr(usage, "cost", None)
        if cost is None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost")
        cached = None
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", None)
        if cached is None and getattr(usage, "model_extra", None):
            extra_details = (usage.model_extra or {}).get("prompt_tokens_details")
            if isinstance(extra_details, dict):
                cached = extra_details.get("cached_tokens")
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "cached_tokens": cached,
            "cost_usd": cost,
        }
