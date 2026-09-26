"""Compact, replay-free context packets for recovery decisions.

The optional ``current_plus_actions`` policy gives a fresh reactive model the
full goal, current node intent, current app, all already-issued action JSON,
and the remaining UI-action budget.  Screenshots and accessibility trees stay
in the current observation supplied to that model and are never copied into
the semantic packet or its digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

POLICIES = (None, "current_plus_actions")
CURRENT_PLUS_ACTIONS = "current_plus_actions"
MAX_ACTIONS = 40


class RecoveryContextError(ValueError):
    """The requested recovery-context policy is not part of the protocol."""


def validate_policy(policy: str | None) -> None:
    if policy not in POLICIES:
        raise RecoveryContextError(
            f"unknown recovery_context_policy {policy!r}; expected None or {CURRENT_PLUS_ACTIONS!r}"
        )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _action_history(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        action = record.get("action")
        if isinstance(action, Mapping):
            actions.append(dict(action))
    return actions


@dataclass(frozen=True)
class RecoveryContext:
    policy: str
    packet: dict[str, Any]
    prompt: str
    digest: str
    byte_length: int


def build_context(
    policy: str | None,
    *,
    goal_text: str,
    goal_prompt: str,
    observation: Mapping[str, Any],
    issued_records: Sequence[Mapping[str, Any]],
    node_intent: str | None,
    remaining_actions: int,
) -> RecoveryContext | None:
    """Build one deterministic semantic packet, or ``None`` for default mode."""

    validate_policy(policy)
    if policy is None:
        return None
    if type(remaining_actions) is not int or remaining_actions < 0:
        raise RecoveryContextError("remaining_actions must be a nonnegative integer")
    actions = _action_history(issued_records)
    if len(actions) > MAX_ACTIONS:
        raise RecoveryContextError(f"issued action history exceeds the shared {MAX_ACTIONS}-action cap")
    packet = {
        "goal_text": str(goal_text),
        "goal_constraints": str(goal_prompt),
        "current_node_intent": str(node_intent or ""),
        "current_app": str(observation.get("url") or ""),
        "already_issued_actions": actions,
        "remaining_ui_actions": remaining_actions,
    }
    encoded = _canonical(packet).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    action_text = json.dumps(actions, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    prompt = (
        "Continue toward the full original task goal using the current screenshot and accessibility tree.\n"
        "Full task request and all constraints:\n"
        + str(goal_prompt)
        + "\n\nCurrent app:\n"
        + str(observation.get("url") or "")
        + "\n\nCurrent guarded node intent:\n"
        + str(node_intent or "")
        + "\n\nAll already-issued action JSON, in order:\n"
        + action_text
        + "\n\nRemaining shared UI action budget: "
        + str(remaining_actions)
        + ".\nDo not replay an already-issued action. Preserve every original task constraint."
    )
    return RecoveryContext(
        policy=CURRENT_PLUS_ACTIONS,
        packet=packet,
        prompt=prompt,
        digest=digest,
        byte_length=len(encoded),
    )


__all__ = [
    "CURRENT_PLUS_ACTIONS",
    "MAX_ACTIONS",
    "POLICIES",
    "RecoveryContext",
    "RecoveryContextError",
    "build_context",
    "validate_policy",
]
