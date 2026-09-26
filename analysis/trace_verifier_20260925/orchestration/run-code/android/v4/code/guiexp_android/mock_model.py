"""A deterministic canned model for tests: a fake `openai` client.

Zero network, zero LLM. ``MockOpenAI`` quacks like the OpenAI client the
agent uses (``client.chat.completions.create``) and returns scripted JSON
actions in the Android action space. The default policy replies with the
terminating status action; passing ``script=["...", ...]`` hands out those
replies in order (one per model call) before falling back to termination.
The point of the canned stub is to exercise the harness -- prompts, action
parsing, obs plumbing, usage capture, trajectory writing -- without any LLM
and without a scripted emulator round-trip (the integration test drives the
real env directly).
"""

from __future__ import annotations

from types import SimpleNamespace

DONE_REPLY = '{"action_type": "status", "goal_status": "complete"}'


def policy_reply(messages, script: list[str] | None = None) -> str:
    """One deterministic reply: the script's next line, or termination."""
    if script:
        replies_so_far = sum(1 for m in messages if m.get("role") == "assistant")
        if replies_so_far < len(script):
            return script[replies_so_far]
    return DONE_REPLY


def _approx_prompt_tokens(messages) -> int:
    total = 50  # system/action-space overhead
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += len(part.get("text", "")) // 4
                    else:  # image parts: flat placeholder, keeps usage deterministic
                        total += 1000
    return total


class _Completions:
    def __init__(self, script: list[str] | None = None):
        self.script = list(script or [])

    def create(self, *, model=None, messages=None, **_kwargs):
        reply = policy_reply(messages or [], self.script)
        prompt_tokens = _approx_prompt_tokens(messages or [])
        completion_tokens = max(1, len(reply) // 4)
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(prompt_tokens * 1e-6 + completion_tokens * 2e-6, 8),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
            model=model or "mock",
        )


class MockOpenAI:
    """Drop-in stand-in for the OpenAI client; deterministic, offline."""

    def __init__(self, script: list[str] | None = None) -> None:
        self.chat = SimpleNamespace(completions=_Completions(script))
