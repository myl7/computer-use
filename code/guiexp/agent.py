"""The agent loop: an OpenAI-compatible chat client pointed at OpenRouter.

Message layout per turn: system (action-space description) + full history +
user (goal on the first turn, then the observation). Observation modes:

    "screenshot"     image content part only
    "screenshot+ax"  image + AX-tree text part

Every call's usage is captured -- prompt_tokens, completion_tokens,
``cached_tokens`` (OpenRouter ``usage.prompt_tokens_details.cached_tokens``,
when the provider reports it), and the ``usage.cost`` field OpenRouter
returns when present (USD) -- and returned to the caller for the trajectory.
The API key is read from the environment and never logged or printed.
"""

from __future__ import annotations

import os

from .actions import ACTION_SPACE_DESCRIPTION

OBS_MODES = ("screenshot", "screenshot+ax")

SYSTEM_PROMPT = "You are a GUI agent operating a web application.\n\n" + ACTION_SPACE_DESCRIPTION


class GuiAgent:
    def __init__(self, model: str, obs_mode: str = "screenshot+ax", client=None, temperature: float = 0.0,
                 manifest: str | None = None):
        if obs_mode not in OBS_MODES:
            raise ValueError(f"unknown obs_mode {obs_mode!r}; expected one of {OBS_MODES}")
        if client is None:
            from openai import OpenAI

            base_url = os.environ["OPENROUTER_BASE_URL"]
            api_key = os.environ["OPENROUTER_API_KEY"]
            # Bounded timeout + few retries so one wedged request cannot hang
            # a whole batch (a live run stalled >10 min on a dead call before).
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=180.0, max_retries=2)
        self.client = client
        self.model = model
        self.obs_mode = obs_mode
        self.temperature = temperature
        # Optional library-manifest block appended verbatim to the system
        # prompt (every call then carries it -> per-step cost floor(n)+m*n).
        # Unset by default: identical behavior to before.
        self.manifest = manifest
        self.system_prompt = SYSTEM_PROMPT
        if manifest:
            self.system_prompt = SYSTEM_PROMPT + "\n\n" + manifest
        self.history: list[dict] = []  # user/assistant turns after the first

    def act(self, goal_prompt: str | None, obs: dict, note: str | None = None) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict).

        ``note`` is a generic system-level nudge appended to this turn's
        user message (e.g. the anti-flail repetition warning). It must never
        carry task- or procedure-specific content.
        """
        content = self._observation_parts(goal_prompt, obs, note)
        messages = [{"role": "system", "content": self.system_prompt}]
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

    def _observation_parts(
        self, goal_prompt: str | None, obs: dict, note: str | None = None
    ) -> list[dict]:
        parts: list[dict] = []
        text = ""
        if goal_prompt:
            text += goal_prompt.rstrip("\n") + "\n\n"
        # The URL line is always present, in both observation modes, so the
        # model never has to guess routes (v1 runs did, endlessly).
        text += f"Current URL: {obs.get('url', '')}\n"
        if note:
            text += f"{note}\n"
        if obs.get("last_action_error"):
            text += f"Your previous action failed: {obs['last_action_error']}\n"
        parts.append({"type": "text", "text": text})
        if "screenshot" in self.obs_mode:
            # screenshot-only mode: prefer the SoM-annotated image (bids are
            # only visible there); screenshot+ax stays unannotated because
            # the AX text already carries the bids.
            if self.obs_mode == "screenshot":
                image_b64 = obs.get("som_screenshot_b64") or obs.get("screenshot_b64")
            else:
                image_b64 = obs.get("screenshot_b64")
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
                        "Accessibility tree of the current page (one line per "
                        "interactive element: [bid] role 'name'):\n" + obs["ax_tree_text"]
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
        # downstream accounting can tell "not reported" apart from "zero".
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
