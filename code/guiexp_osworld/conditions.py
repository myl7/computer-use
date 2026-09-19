"""Prompt conditions on the OSWorld arm.

Only the conditions the build protocol needs: ``discover`` (the reactive
arm), ``doc`` (the compiled operation document injected at the goal block,
same slot the Android arm's told condition uses) and ``floor`` (the no-task
probe). told/mid/skill are Android-arm measurement conditions and are not
ported.
"""

from __future__ import annotations

CONDITIONS = ("discover", "doc", "floor")

FAMILIES = ("CalcTableSave", "WriterMemoSave")

COMMON_TEMPLATE = (
    "You are operating a Linux desktop with LibreOffice applications.\n"
    "\n"
    "{goal}"
    "\n"
    "Rules:\n"
    "- Complete the task by driving the desktop GUI with the JSON actions\n"
    "  provided (click / input_text / press / hotkey / scroll / open_app /\n"
    "  status).\n"
    "- Only interaction through the apps' own GUI counts; do not try to\n"
    "  reach the apps' files or settings by other means.\n"
    "- When the task is finished, reply with the status action with\n"
    "  goal_status complete.\n"
)

FLOOR_PROMPT = (
    'Reply with exactly {"action_type": "status", "goal_status": "complete"}'
    " and nothing else. Do not take any other action."
)


def build_prompt(condition: str, family: str, goal_text: str,
                 doc_text: str | None = None) -> str:
    """The full user-side prompt for one condition. floor has no task."""
    if condition == "floor":
        return FLOOR_PROMPT
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
    if condition == "doc" and not (doc_text or "").strip():
        raise ValueError("condition 'doc' needs a compiled document (doc_text)")
    prompt = COMMON_TEMPLATE.format(goal=goal_text.rstrip("\n"))
    if condition == "doc":
        prompt += "\n" + doc_text.strip("\n") + "\n"
    return prompt
