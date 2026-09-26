"""The agent loop for OSWorld: same message contract as the other two arms.

Message layout per turn: system (action-space description + one-action-per-
reply) + full history + user (goal on the first turn, then the observation).
One observation mode on this arm, ``screenshot+ax``: the UNMARKED screenshot
plus the numbered a11y element list (the brief's observation contract; no SoM
variant, no extra browser, nothing else).

Every call's usage is captured -- prompt_tokens, completion_tokens,
``cached_tokens`` (OpenRouter ``usage.prompt_tokens_details.cached_tokens``
when reported) and OpenRouter's ``usage.cost`` (USD) -- and returned for the
trajectory. The key lives in the environment
(OPENROUTER_API_KEY / OPENROUTER_BASE_URL) and is never logged or printed.
"""

from __future__ import annotations

import os

from .actions import system_prompt

OBS_MODES = ("screenshot+ax",)

SYSTEM_PROMPT = system_prompt()


class OSWorldAgent:
    def __init__(self, model: str, obs_mode: str = "screenshot+ax", client=None,
                 temperature: float = 0.0):
        if obs_mode not in OBS_MODES:
            raise ValueError(f"unknown obs_mode {obs_mode!r}; expected one of {OBS_MODES}")
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=os.environ["OPENROUTER_BASE_URL"],
                api_key=os.environ["OPENROUTER_API_KEY"],
                timeout=180.0,
                max_retries=2,
            )
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

    def _observation_parts(self, goal_prompt: str | None, obs: dict) -> list[dict]:
        parts: list[dict] = []
        text = ""
        if goal_prompt:
            text += goal_prompt.rstrip("\n") + "\n\n"
        # The "where am I" line: the active window title on this arm.
        text += f"Active window: {obs.get('url', '')}\n"
        if obs.get("last_action_error"):
            text += f"Your previous action failed: {obs['last_action_error']}\n"
        parts.append({"type": "text", "text": text})
        if obs.get("screenshot_b64"):
            parts.append({
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + obs["screenshot_b64"]},
            })
        if obs.get("ax_tree_text"):
            parts.append({
                "type": "text",
                "text": (
                    "Numbered list of UI elements on the current screen"
                    " (the numbers are the element indexes the actions use):\n"
                    + obs["ax_tree_text"]
                ),
            })
        return parts

    @staticmethod
    def _usage(response) -> dict:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0,
                    "cached_tokens": None, "cost_usd": None}
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
        result = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "cached_tokens": cached,
            "cost_usd": cost,
        }
        receipt = getattr(response, "_guiexp_route_receipt", None)
        if receipt is not None:
            result["route_receipt"] = receipt
            result["cost_cny"] = receipt.get("cost_cny")
        return result
