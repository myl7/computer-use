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

Reactive image-budget guard: map-canvas screenshots run 1.8-1.95MB apiece,
and an accumulated history once crossed the model channel's 30MB image limit
(OpenRouter 413, "Downloaded image content cannot exceed 30MB"), killing
whole verification episodes deterministically. Requests are therefore sent
normally -- no proactive truncation, byte-identical to the unguarded client.
When a request IS rejected with HTTP 413 (or, safety net, "cannot exceed"
wording), the guard strips the CURRENT step's screenshot part (its
interface-tree text stays), retries that request immediately once -- the 413
is rejected pre-routing and unbilled -- and freezes the episode's image set
after the first successful post-strip retry: every later observation is
text-only, already-sent history images are never removed, and the request
prefix up to the strip point stays byte-identical (provider prefix caching
is not invalidated). Degenerate fallback (not expected to fire): a request
still 413ing with no current image left strips the most recent HISTORY
image, repeating as needed -- this breaks prefix stability, so each such
strip is recorded as a distinct degenerate event and logged loudly. Events
land on the agent (``image_413_events``) and in that call's usage dict so
episode JSON can footnote affected runs.
"""

from __future__ import annotations

import os
import sys

from .actions import ACTION_SPACE_DESCRIPTION

OBS_MODES = ("screenshot", "screenshot+ax")

SYSTEM_PROMPT = "You are a GUI agent operating a web application.\n\n" + ACTION_SPACE_DESCRIPTION


def _strip_image_part(content: list[dict]) -> int | None:
    """Remove the first image part of a content list; its data-URL length
    (an upper bound on the decoded bytes), or None if there was no image."""
    for i, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "image_url":
            size = len(part["image_url"]["url"])
            content.pop(i)
            return size
    return None


def _is_image_channel_error(exc: BaseException) -> bool:
    """The channel's image-budget rejection: HTTP 413, or (safety net) the
    "cannot exceed" wording wherever the client surfaces it."""
    if getattr(exc, "status_code", None) == 413:
        return True
    parts = [str(v) for v in (getattr(exc, "message", None), getattr(exc, "body", None)) if v]
    text = " ".join(parts) if parts else str(exc)
    return "cannot exceed" in text.lower()


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
        # Reactive image-413 guard state (see module docstring): requests go
        # out untouched until the channel rejects one with 413; the first
        # post-strip success freezes the episode's image set (all later
        # observations text-only, history images never removed).
        self._images_frozen = False
        self.image_413_events: list[dict] = []

    def act(self, goal_prompt: str | None, obs: dict, note: str | None = None) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict).

        ``note`` is a generic system-level nudge appended to this turn's
        user message (e.g. the anti-flail repetition warning). It must never
        carry task- or procedure-specific content. If the channel rejects
        the request with 413, this call strips the current screenshot,
        retries immediately once, and freezes images off for the rest of the
        episode; the events are returned under ``usage["image_413_events"]``
        (absent otherwise).
        """
        content = self._observation_parts(goal_prompt, obs, note)
        if self._images_frozen:
            # Frozen episode: every observation goes out text-only; the
            # screenshot part is omitted before the request ever leaves.
            _strip_image_part(content)
            if self.image_413_events:
                self.image_413_events[0]["text_only_turns_after_freeze"] += 1
        messages = [{"role": "system", "content": self.system_prompt}]
        if self.history:
            messages.extend(self.history)
        messages.append({"role": "user", "content": content})

        response, events = self._create_with_image_413_guard(messages)
        usage = self._usage(response)
        if events:
            # The same dicts as self.image_413_events, so the runner can
            # stamp the step index into them when writing the trajectory.
            usage["image_413_events"] = events
        reply = response.choices[0].message.content or ""

        self.history.append({"role": "user", "content": content})
        self.history.append({"role": "assistant", "content": reply})
        return reply, usage

    # -- helpers -----------------------------------------------------------

    def _create_with_image_413_guard(self, messages: list[dict]):
        """chat.completions.create with the reactive 413 guard.

        Normally a single straight-through call (no overhead, byte-identical
        request). On the channel's image-budget rejection: strip the CURRENT
        step's image part from the pending user turn and retry immediately
        once (413s are rejected pre-routing and unbilled). If the retry is
        rejected too -- the degenerate path, not expected to fire -- strip
        the most recent REMAINING history image and retry again, repeating
        as needed; this breaks request-prefix stability and is logged
        loudly. Non-image errors propagate exactly as before. Returns
        (response, events) where events lists the 413 strips this call made
        (empty in the normal case).
        """
        events: list[dict] = []
        while True:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                )
                return response, events
            except Exception as exc:
                if not _is_image_channel_error(exc):
                    raise
                trigger = "http_413" if getattr(exc, "status_code", None) == 413 else "cannot_exceed_message"
                stripped = _strip_image_part(messages[-1]["content"])  # current step
                source = "current"
                if stripped is None:
                    source = "history"
                    stripped = self._strip_latest_history_image()
                if stripped is None:
                    raise  # nothing image-shaped left: a genuine channel error
                event = {
                    "step": None,  # stamped by the runner's _record_step
                    "trigger": trigger,
                    "source": source,
                    "images_stripped": 1,
                    "bytes_stripped": stripped,
                    "degenerate": source == "history",
                    "frozen": True,  # the image set is frozen from this event on
                }
                if not self._images_frozen:
                    self._images_frozen = True
                    event["text_only_turns_after_freeze"] = 0
                self.image_413_events.append(event)
                events.append(event)
                if event["degenerate"]:
                    print(
                        "guiexp: WARNING degenerate image-413 strip: request still "
                        f"rejected after the current-step strip; removed a HISTORY "
                        f"image ({stripped} bytes) -- request prefix stability is "
                        "broken for the rest of this episode",
                        file=sys.stderr,
                    )

    def _strip_latest_history_image(self) -> int | None:
        """Degenerate path only: remove the most recent image still in
        history (walking backwards). MUTATES history -- breaks the request
        prefix, so callers must record the strip as a degenerate event."""
        for msg in reversed(self.history):
            if msg.get("role") != "user":
                continue
            size = _strip_image_part(msg["content"])
            if size is not None:
                return size
        return None

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
