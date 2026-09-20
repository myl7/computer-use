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

Per-request image budget: map-canvas screenshots run 1.8-1.95MB apiece, and
an accumulated history once crossed the model channel's 30MB image limit
(OpenRouter 413), killing whole episodes. Each request therefore carries at
most ``GUIEXP_IMAGE_BUDGET_MB`` (default 25, safety margin) MB of image
payload. Images accumulate while they fit; at the FIRST observation whose
screenshot would cross the budget, that image is omitted (its interface-tree
text is kept) and the image set is FROZEN -- every later observation is
text-only. Already-sent images are never dropped, so the request prefix up
to the freeze point stays byte-identical for the rest of the episode and
provider prefix caching is not invalidated. The freeze is recorded on the
agent (``image_budget_event``) and marked in that call's usage dict
(``image_budget_freeze``) so episode JSON can footnote affected runs.
"""

from __future__ import annotations

import os

from .actions import ACTION_SPACE_DESCRIPTION

OBS_MODES = ("screenshot", "screenshot+ax")

SYSTEM_PROMPT = "You are a GUI agent operating a web application.\n\n" + ACTION_SPACE_DESCRIPTION

# Per-request image budget in effect (bytes). Measured on the base64 image
# payload as transmitted -- an upper bound on the decoded bytes -- so the
# margin under the channel's decoded-bytes limit is if anything larger.
DEFAULT_IMAGE_BUDGET_MB = 25.0


def image_budget_bytes() -> int:
    """Current per-request image budget; env override exists for tests."""
    mb = float(os.environ.get("GUIEXP_IMAGE_BUDGET_MB", DEFAULT_IMAGE_BUDGET_MB))
    return int(mb * 1024 * 1024)


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
        # Image-budget guard state (freeze policy; see module docstring):
        # at the first screenshot that would cross the budget the image set
        # freezes and later observations go text-only. History is never
        # rewritten, so request prefixes stay byte-identical after the freeze.
        self._images_frozen = False
        self.image_budget_event: dict | None = None

    def act(self, goal_prompt: str | None, obs: dict, note: str | None = None) -> tuple[str, dict]:
        """One model call. Returns (raw_reply, usage_dict).

        ``note`` is a generic system-level nudge appended to this turn's
        user message (e.g. the anti-flail repetition warning). It must never
        carry task- or procedure-specific content. When this call freezes
        the image set, the freeze event is also stored under
        ``usage["image_budget_freeze"]`` (absent otherwise).
        """
        content = self._observation_parts(goal_prompt, obs, note)
        freeze = self._guard_image_budget(content)
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
        if freeze is not None:
            # Same dict object as self.image_budget_event, so the runner can
            # stamp the step index into it when writing the trajectory.
            usage["image_budget_freeze"] = freeze
        reply = response.choices[0].message.content or ""

        self.history.append({"role": "user", "content": content})
        self.history.append({"role": "assistant", "content": reply})
        return reply, usage

    # -- helpers -----------------------------------------------------------

    def _guard_image_budget(self, content: list[dict]) -> dict | None:
        """Freeze-style per-request image budget (module docstring).

        Under budget -- the common case -- this is a read-only no-op: the
        outgoing request is byte-identical to the unguarded build. At the
        first observation whose screenshot would push the accumulated image
        payload over the budget, that image part is removed from the NEW
        turn only (its observation text is kept), the image set freezes for
        the rest of the episode, and the freeze event dict is returned (and
        stored as ``self.image_budget_event``). Already-sent images are
        never dropped and ``self.history`` is never mutated, so the request
        prefix up to the freeze point stays byte-identical -- dropping the
        oldest image each step would invalidate provider prefix caching and
        re-bill the whole history every step. Returns None when this call
        changed nothing.
        """
        if self._images_frozen:
            # Frozen: the screenshot part of every new observation is omitted
            # entirely (text only); no new image ever enters the request.
            idx = next(
                (i for i, p in enumerate(content) if isinstance(p, dict) and p.get("type") == "image_url"),
                None,
            )
            if idx is not None:
                content.pop(idx)
            self.image_budget_event["text_only_turns_after_freeze"] += 1
            return None
        image_idx = next(
            (i for i, p in enumerate(content) if isinstance(p, dict) and p.get("type") == "image_url"),
            None,
        )
        if image_idx is None:
            return None  # no screenshot in this observation: nothing to add
        used = 0
        images = 0
        for msg in self.history:
            if msg.get("role") != "user":
                continue
            for part in msg.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    used += len(part["image_url"]["url"])
                    images += 1
        new_bytes = len(content[image_idx]["image_url"]["url"])
        budget = image_budget_bytes()
        if used + new_bytes <= budget:
            return None  # still fits: byte-identical to the unguarded build
        # Budget crossed: omit this screenshot, freeze from here on. The
        # observation text (URL line, AX tree, error note) is untouched.
        content.pop(image_idx)
        self._images_frozen = True
        self.image_budget_event = {
            "images_kept": images,
            "bytes_kept": used,
            "bytes_omitted": new_bytes,
            "budget_bytes": budget,
            "text_only_turns_after_freeze": 0,
        }
        return self.image_budget_event

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
