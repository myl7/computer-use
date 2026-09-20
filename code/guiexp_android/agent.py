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

Reactive image-413 guard (ported from web guiexp/agent.py, v3 semantics):
map-style screenshots pushed one lane's accumulated history past the model
channel's 30MB image limit (OpenRouter 413, "Downloaded image content cannot
exceed 30MB"), killing episodes deterministically. Observations ALWAYS
attach at full quality (HD PNG) -- no proactive truncation, requests
byte-identical to the unguarded client, no permanent tier switch. When a
request IS rejected with HTTP 413 (or, safety net, "cannot exceed" wording),
it is reworked and retried immediately (the rejection is pre-routing and
unbilled: the probe contributes NO usage record; the successful retry's
usage is recorded verbatim from its response), climbing a tier ladder:

    tier 1  compress_history  every HISTORY image -> WebP q75, original
                              pixel dimensions (tap coordinates depend on
                              them); already-compressed ones stay as-is; the
                              current image stays HD
    tier 2  compress_current  the current image -> WebP q75 as well
    tier 3  strip_current     the current image goes (text-only step)
    tier 4  strip_history     most recent history image goes, repeating

Tier 1 mutates history in place: one accepted prefix break per pass, at the
first newly-compressed image. Between passes the history is append-only and
prefix-stable, and later observations attach HD again -- a later 413 (HD
images re-accumulated on long episodes) simply runs another pass, never
double-compressing. Tiers 3-4 are the final resort and are logged loudly.
Every pass is recorded on the agent (``image_413_events``) and in that
call's usage dict so episode JSON can footnote affected runs. Requires
Pillow (venv-expa must provide it) only when a compress tier fires.
"""

from __future__ import annotations

import base64
import io
import os
import sys

from .actions import system_prompt

OBS_MODES = ("screenshot", "screenshot+ax")

SYSTEM_PROMPT = system_prompt()

# Compression tier: WebP quality for the reactive 413 pass. Original pixel
# dimensions are always preserved (tap coordinates depend on resolution).
_WEBP_QUALITY = 75
_PNG_PREFIX = "data:image/png;base64,"
_WEBP_PREFIX = "data:image/webp;base64,"


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


def _compress_image_part(part: dict) -> tuple[int, int] | None:
    """Re-encode one image part to WebP at ``_WEBP_QUALITY`` in place, with
    original pixel dimensions preserved (never resize: the agent's tap
    coordinates depend on the resolution). Only ``data:image/png`` parts are
    touched, so already-compressed ones stay as-is (no double compression).
    Returns (url_bytes_before, url_bytes_after), or None when the part is
    not a PNG data URL or does not decode (left unchanged; a decode failure
    is logged loudly). Needs Pillow -- only ever called on the 413 path."""
    url = part["image_url"]["url"]
    if not url.startswith(_PNG_PREFIX):
        return None
    try:
        from PIL import Image
    except ImportError as exc:  # provisioning guard: venv-expa must ship pillow
        raise ImportError(
            "the image-413 guard needs Pillow to re-encode screenshots to WebP; "
            "install pillow into the run venv (venv-expa on the servers)"
        ) from exc
    try:
        raw = base64.b64decode(url[len(_PNG_PREFIX):])
        with Image.open(io.BytesIO(raw)) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=_WEBP_QUALITY)
    except Exception as exc:
        print(
            f"guiexp_android: WARNING image-413 pass could not re-encode a "
            f"screenshot ({type(exc).__name__}: {exc}); leaving it as PNG",
            file=sys.stderr,
        )
        return None
    webp_url = _WEBP_PREFIX + base64.b64encode(buf.getvalue()).decode()
    before = len(url)
    part["image_url"]["url"] = webp_url
    return before, len(webp_url)


def _compress_msg_images(msgs: list[dict]) -> tuple[int, int, int]:
    """Re-encode every PNG image part of the given user messages in place.
    Returns (images, bytes_before, bytes_after) over the parts actually
    re-encoded (skipped/undecodable parts are not counted)."""
    images = before = after = 0
    for msg in msgs:
        if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
            continue
        for part in msg["content"]:
            if isinstance(part, dict) and part.get("type") == "image_url":
                result = _compress_image_part(part)
                if result is not None:
                    images += 1
                    before += result[0]
                    after += result[1]
    return images, before, after


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
        # Reactive image-413 guard log (see module docstring): one event per
        # applied tier per 413 pass; requests otherwise go out untouched.
        self.image_413_events: list[dict] = []

    def act(self, goal_prompt: str | None, obs: dict) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict).

        If the channel rejects the request with 413, this call reworks it
        through the tier ladder in ``_create_with_image_413_guard`` (history
        compressed first, the current image kept HD as long as possible) and
        retries immediately; the pass events are returned under
        ``usage["image_413_events"]`` (absent otherwise). The rejected probe
        itself contributes no usage record; the returned usage is the
        successful response's, verbatim.
        """
        content = self._observation_parts(goal_prompt, obs)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if self.history:
            messages.extend(self.history)
        messages.append({"role": "user", "content": content})

        response, events = self._create_with_image_413_guard(messages)
        usage = self._usage(response)
        if events:
            # The same dicts as self.image_413_events, so a runner can stamp
            # the step index into them when writing the trajectory.
            usage["image_413_events"] = events
        reply = response.choices[0].message.content or ""

        self.history.append({"role": "user", "content": content})
        self.history.append({"role": "assistant", "content": reply})
        return reply, usage

    # -- helpers -----------------------------------------------------------

    def _create_with_image_413_guard(self, messages: list[dict]):
        """chat.completions.create with the reactive 413 tier ladder (v3).

        Normally a single straight-through call (no overhead, byte-identical
        request; observations always attach at full-quality PNG). On the
        channel's image-budget rejection the request is reworked IN PLACE and
        retried immediately, one tier per rejection:

            tier 1  compress_history  history images -> WebP q75 (original
                                      pixel dimensions; already-compressed
                                      ones stay as-is); current stays HD
            tier 2  compress_current  the current image -> WebP q75 as well
            tier 3  strip_current     the current image goes (text-only step)
            tier 4  strip_history     most recent history image goes,
                                      repeating as needed

        Each application is one event; a pass may climb several tiers when a
        compressed request is still rejected. The rejected probe contributes
        no usage record; whatever response finally succeeds has its usage
        taken verbatim by the caller. Non-image errors propagate exactly as
        before. Returns (response, events), events empty in the normal case.
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
                # Tier 1: history only; the current image stays HD.
                images, before, after = _compress_msg_images(messages[:-1])
                if images:
                    event = {
                        "step": None,  # stamped by the runner's _record_step
                        "trigger": trigger,
                        "tier": "compress_history",
                        "images": images,
                        "bytes_before": before,
                        "bytes_after": after,
                        "ratio": round(before / after, 2),
                    }
                else:
                    # Tier 2: the current image too.
                    images, before, after = _compress_msg_images(messages[-1:])
                    if images:
                        event = {
                            "step": None,
                            "trigger": trigger,
                            "tier": "compress_current",
                            "images": images,
                            "bytes_before": before,
                            "bytes_after": after,
                            "ratio": round(before / after, 2),
                        }
                    else:
                        # Tiers 3-4: the final resort, logged loudly.
                        stripped = _strip_image_part(messages[-1]["content"])
                        if stripped is not None:
                            tier = "strip_current"
                        else:
                            stripped = self._strip_latest_history_image()
                            tier = "strip_history"
                        if stripped is None:
                            raise  # nothing image-shaped left: a genuine error
                        event = {
                            "step": None,
                            "trigger": trigger,
                            "tier": tier,
                            "images_stripped": 1,
                            "bytes_stripped": stripped,
                            "degenerate": True,  # final resort: prefix broken
                        }
                        print(
                            f"guiexp_android: WARNING image-413 final resort "
                            f"({tier}): stripped a screenshot ({stripped} "
                            "bytes); its text stays but request prefix "
                            "stability is broken",
                            file=sys.stderr,
                        )
                self.image_413_events.append(event)
                events.append(event)

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
