"""The agent loop for Android: same message contract as web guiexp/agent.py.

Message layout per turn: system (action-space description + one-action-per-
reply format) + full history + user (goal on the first turn, then the
observation). Observation modes:

    "screenshot"     SoM-marked screenshot only. SoM IS practical on Android:
                     android_world ships the mark renderer
                     (m3a_utils.add_ui_element_mark) and draws a box + the
                     numeric element index on every valid element, so the
                     bid the action space addresses is visible in the image
                     exactly like a BrowserGym bid badge on the web side.
    "screenshot+ax"  unmarked screenshot + the M3A-text-variant numbered
                     element list (ax_tree_text), whose indexes are the same
                     element indexes.

Every call's usage is captured -- prompt_tokens, completion_tokens,
``cached_tokens`` (OpenRouter ``usage.prompt_tokens_details.cached_tokens``,
when the provider reports it), and the ``usage.cost`` field OpenRouter
returns when present (USD) -- and returned to the caller for the trajectory.
The API key is read from the environment
(OPENROUTER_API_KEY / OPENROUTER_BASE_URL) and never logged or printed; no
client is constructed at import time, so tests with a canned client make no
network calls and need no credentials.
"""

from __future__ import annotations

import os

from .actions import system_prompt

OBS_MODES = ("screenshot", "screenshot+ax")

SYSTEM_PROMPT = system_prompt()


class AndroidAgent:
    def __init__(self, model: str, obs_mode: str = "screenshot+ax", client=None, temperature: float = 0.0):
        if obs_mode not in OBS_MODES:
            raise ValueError(f"unknown obs_mode {obs_mode!r}; expected one of {OBS_MODES}")
        if client is None:
            from openai import OpenAI

            base_url = os.environ["OPENROUTER_BASE_URL"]
            api_key = os.environ["OPENROUTER_API_KEY"]
            # Bounded timeout + few retries so one wedged request cannot hang
            # a whole batch (same belt-and-braces as the web agent).
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0, max_retries=2)
        self.client = client
        self.model = model
        self.obs_mode = obs_mode
        self.temperature = temperature
        self.history: list[dict] = []  # user/assistant turns after the first

    def act(self, goal_prompt: str | None, obs: dict) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict)."""
        content = self._observation_parts(goal_prompt, obs)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.history:
            messages.extend(self.history)
        messages.append({"role": "user", "content": content})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
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
        # The "where am I" line is always present, in both observation modes,
        # so the model never has to guess which screen it is on. On Android
        # this is the foreground activity (web guiexp: the page URL).
        text += f"Current app: {obs.get('url', '')}\n"
        if obs.get("last_action_error"):
            text += f"Your previous action failed: {obs['last_action_error']}\n"
        parts.append({"type": "text", "text": text})
        if "screenshot" in self.obs_mode:
            # screenshot-only mode: use the SoM-marked image (element indexes
            # are drawn on it); screenshot+ax stays unmarked because the
            # element list already carries the indexes.
            image_b64 = obs.get("som_screenshot_b64") or obs.get("screenshot_b64")
            if image_b64:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + image_b64},
                    }
                )
        if self.obs_mode.endswith("+ax") and obs.get("ax_tree_text"):
            parts.append(
                {
                    "type": "text",
                    "text": (
                        "Numbered list of UI elements on the current screen"
                        " (the numbers are the element indexes the actions"
                        " use):\n" + obs["ax_tree_text"]
                    ),
                }
            )
        return parts

    @staticmethod
    def _usage(response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": None, "cost_usd": None}
        # OpenRouter adds a `cost` field (USD) to usage; keep it when present.
        cost = getattr(usage, "cost", None)
        if cost is None and getattr(usage, "model_extra", None):
            cost = usage.model_extra.get("cost")
        # Cache-read tokens, when the provider reports them (OpenRouter:
        # usage.prompt_tokens_details.cached_tokens). None when absent, so
        # downstream accounting can tell "not reported" apart from "zero"
        # (same convention as web guiexp/agent.py).
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
