"""Translator stage: one model call per effective action of a building
trajectory (the Android arm's stage, same text-shaped input decision:
action JSON + target element record + before/after element diff, no
screenshots). The before/after element lists come from a zero-LLM shadow
replay on the live guest; snippets use this arm's ProgramDevice vocabulary.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import families, guest_env
from .compiler import (
    _compact_action,
    load_trajectory,
    placeholder_expr,
    trajectory_instance,
)

TRANSLATOR_SYSTEM = (
    "You make one recorded GUI action robust. You reply with a short "
    "dependency analysis and one python snippet, nothing else."
)

_ELEMENT_KEYS = ("name", "text", "description", "role", "clickable", "editable")


def _element_records_xml(at_xml: str) -> list[dict]:
    return [
        {key: record.get(key) for key in _ELEMENT_KEYS}
        for record in guest_env.element_records(at_xml)
    ]


def _element_records(env) -> list[dict]:
    return _element_records_xml(guest_env.get_accessibility_xml())


def _element_label(record: dict) -> str:
    parts = [f"{key}={record[key]!r}" for key in ("name", "text", "description") if record.get(key)]
    flags = [key for key in ("clickable", "editable") if record.get(key)]
    body = ", ".join(parts) or "(no text)"
    tail = f" [{'/'.join(flags)}]" if flags else ""
    return f"{record.get('role', '?')} {body}{tail}"


def element_diff(before: list[dict], after: list[dict], max_lines: int = 24) -> str:
    """Compact before/after element diff; identity is semantic content."""
    def key(record: dict) -> tuple:
        return tuple(str(record.get(field, "")) for field in _ELEMENT_KEYS)

    before_keys = {key(r) for r in before}
    after_keys = {key(r) for r in after}
    gone = [r for r in before if key(r) not in after_keys]
    new = [r for r in after if key(r) not in before_keys]
    lines = []
    for record in gone[:max_lines]:
        lines.append(f"  - {_element_label(record)}")
    if len(gone) > max_lines:
        lines.append(f"  - ... {len(gone) - max_lines} more elements left the screen")
    for record in new[:max_lines]:
        lines.append(f"  + {_element_label(record)}")
    if len(new) > max_lines:
        lines.append(f"  + ... {len(new) - max_lines} more elements appeared")
    return "\n".join(lines)


def capture_observations(trajectory_jsonl: Path | str, family: str | None = None,
                         env=None, settle_s: float = 1.5) -> dict:
    """Shadow-replay the trajectory, recording the element list before and
    after every action. Zero model calls; needs the live guest."""
    import time as _time

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    own_env = None
    if env is None:
        own_env = env = guest_env.OSWorldEnv()
    captured: list[dict] = []
    stopped = None
    try:
        task = guest_env.get_task(family, final.get("condition", "discover"), final["seed"])
        env.reset(task)
        for step in steps:
            action = step["action"]
            at_xml = guest_env.get_accessibility_xml()
            before = _element_records_xml(at_xml)
            activity_before = guest_env.active_window_title(at_xml)
            index = action.get("index")
            target = dict(before[index]) if index is not None and 0 <= index < len(before) else None
            try:
                env.execute_action(action)
            except Exception as exc:  # noqa: BLE001 - replay drifted
                stopped = f"step {step['step']}: {type(exc).__name__}"
                break
            _time.sleep(settle_s)
            at_xml = guest_env.get_accessibility_xml()
            after = _element_records_xml(at_xml)
            activity_after = guest_env.active_window_title(at_xml)
            captured.append({
                "step": step["step"],
                "activity_before": activity_before,
                "activity_after": activity_after,
                "target": target,
                "before": before,
                "after": after,
            })
    finally:
        if own_env is not None:
            own_env.close()
    return {"steps": captured, "replay_stopped_at": stopped}


def effective_steps(observations: dict) -> list[dict]:
    """Steps whose action changed the screen (element list or active window)."""
    out = []
    for item in observations.get("steps") or []:
        diff = element_diff(item["before"], item["after"])
        if diff.strip() or item["activity_before"] != item["activity_after"]:
            out.append({**item, "diff": diff})
    return out


def build_translator_prompt(family: str, step: dict, observation: dict,
                            goal_template_str: str) -> list[dict]:
    action = step["action"]
    target = observation.get("target")
    lines = [
        f"APP: {family} on a Linux desktop. The recorded agent was carrying",
        f"out this family of tasks: {goal_template_str.strip()}",
        "",
        "ONE RECORDED ACTION, addressed by the element's index in the numbered",
        "element list of the screen it acted on:",
        f"  {_compact_action(action, step.get('field'), step.get('fragment'))}",
        "",
        "THE ELEMENT THAT INDEX ADDRESSED:",
    ]
    if target is None:
        lines.append("  (the action addressed no element: it is a global action)")
    else:
        lines += [f"  {key}: {target[key]!r}" for key in _ELEMENT_KEYS]
    if step.get("field"):
        lines += [
            "",
            "The typed text is a task parameter: it must be rebuilt from the",
            f"binding as {placeholder_expr(step['field'], step['fragment'])}.",
        ]
    lines += [
        "",
        f"SCREEN BEFORE: {observation['activity_before']}",
        f"SCREEN AFTER:  {observation['activity_after']}",
        "WHAT THE ACTION CHANGED ('-' left the screen, '+' appeared):",
        observation.get("diff") or "  (no element changed)",
        "",
        "Rewrite this action so it works on ANY instance of the family, on a",
        "screen whose element indexes may differ. The runtime object is",
        "``device``:",
        "  device.find(name=..., contains=..., role=..., description=...,",
        "               editable=..., clickable=...) -> element index or None",
        "  device.click(index=None, **find)",
        "  device.input_text(text, index=None, **find) ; device.type_at_caret(text)",
        "  device.press(key) ; device.hotkey(*keys) ; device.scroll(direction)",
        "  device.open_app(name) ; device.wait() ; device.settle(s)",
        "  device.elements() -> the fresh element list as dicts",
        "Rules:",
        "- Never use the recorded index; look the element up by the semantic",
        "  attributes that identify it (name, text, role).",
        "- Never hard-code a task value; read it from ``binding``.",
        "- Never use screen coordinates.",
        "- Raise a clear error when the lookup finds nothing.",
        "",
        "Reply in exactly this shape:",
        "ANALYSIS: <one or two lines: what this action depends on and how it",
        "can fail on another instance>",
        "```python",
        "<the snippet, a few lines, using device and binding only>",
        "```",
    ]
    return [
        {"role": "system", "content": TRANSLATOR_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_translation(reply: str) -> dict:
    from .compiler import extract_python_source

    text = (reply or "").strip()
    analysis = ""
    head = text.split("```", 1)[0].strip()
    if head.upper().startswith("ANALYSIS:"):
        analysis = head[len("ANALYSIS:"):].strip()
    elif head:
        analysis = head
    try:
        snippet = extract_python_source(text).strip()
    except ValueError:
        snippet = ""
    if snippet == text:
        snippet = ""
    return {"analysis": analysis, "snippet": snippet}


def translate_trajectory(
    model: str,
    trajectory_jsonl: Path | str,
    family: str | None = None,
    observations: dict | None = None,
    client=None,
    env=None,
    temperature: float = 0.0,
) -> dict:
    """One translator call per effective action; returns the translation."""
    from .agent import OSWorldAgent
    from .compiler import _openai_client

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    params = trajectory_instance(family, final)
    goal_text_str = families.goal_text(family, final["seed"], params)
    template = families.goal_template_text(family, goal_text_str, params)

    if observations is None:
        observations = capture_observations(trajectory_jsonl, family, env=env)
    by_step = {item["step"]: item for item in effective_steps(observations)}
    if client is None:
        client = _openai_client()

    out_steps: list[dict] = []
    prompt_tokens = cached_tokens = completion_tokens = 0
    cost = 0.0
    for step in steps:
        observation = by_step.get(step["step"])
        if observation is None:
            continue
        messages = build_translator_prompt(family, step, observation, template)
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature
        )
        usage = OSWorldAgent._usage(response)
        parsed = parse_translation(response.choices[0].message.content or "")
        prompt_tokens += usage.get("prompt_tokens") or 0
        cached_tokens += usage.get("cached_tokens") or 0
        completion_tokens += usage.get("completion_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        out_steps.append({
            "step": step["step"],
            "action": step["action"],
            "field": step.get("field"),
            "analysis": parsed["analysis"],
            "snippet": parsed["snippet"],
            "usage": usage,
        })

    return {
        "family": family,
        "model": model,
        "trajectory": str(trajectory_jsonl),
        "seed": final["seed"],
        "steps": out_steps,
        "effective_actions": len(out_steps),
        "recorded_actions": len(steps),
        "replay_stopped_at": observations.get("replay_stopped_at"),
        "totals": {
            "calls": len(out_steps),
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": round(cost, 8),
        },
        "record_type": "translation",
    }
