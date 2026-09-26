"""Translator stage: one call per effective action of a building trajectory.

The WebArena mirror of guiexp_android/translator.py. TEXT-SHAPED input
(same recorded deviation): per action, the action JSON, the target
element's DOM record, and a compact before/after element-list diff (from a
zero-token shadow replay on the live browser). Output: a robustness
analysis + a python snippet in the WebDevice vocabulary the builder pastes
into programs unchanged.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .compiler import _compact_action, load_trajectory

TRANSLATOR_SYSTEM = (
    "You make one recorded GUI action robust. You reply with a short "
    "dependency analysis and one python snippet, nothing else."
)

_ELEMENT_KEYS = ("tag", "text", "placeholder", "aria", "name", "href", "value", "editable")


def element_records(env) -> list[dict]:
    return env._elements()


def element_diff(before: list[dict], after: list[dict], max_lines: int = 20) -> str:
    """Compact before/after DOM diff: identity is semantic content."""
    def key(record):
        return tuple(str(record.get(field, "")) for field in _ELEMENT_KEYS)

    before_keys = {key(r) for r in before}
    after_keys = {key(r) for r in after}
    gone = [r for r in before if key(r) not in after_keys]
    new = [r for r in after if key(r) not in before_keys]
    lines = []

    def label(record):
        bits = [f"<{record.get('tag')}>"]
        for field in ("text", "placeholder", "aria", "value"):
            if record.get(field):
                bits.append(f"{field}={record[field]!r}")
        if record.get("href"):
            bits.append(f"href={record['href']!r}")
        return " ".join(bits)

    for record in gone[:max_lines]:
        lines.append(f"  - {label(record)}")
    if len(gone) > max_lines:
        lines.append(f"  - ... {len(gone) - max_lines} more elements left the page")
    for record in new[:max_lines]:
        lines.append(f"  + {label(record)}")
    if len(new) > max_lines:
        lines.append(f"  + ... {len(new) - max_lines} more elements appeared")
    return "\n".join(lines)


def capture_observations(trajectory_jsonl, family, env=None, settle_s: float = 1.2) -> dict:
    """Shadow-replay the trajectory on the live browser, recording the
    element list before and after every action. Zero model calls."""
    from .actions import parse_action
    from .family import task_for

    steps, final = load_trajectory(trajectory_jsonl, family)
    own_env = None
    if env is None:
        from .env import WebArenaEnv

        own_env = env = WebArenaEnv()
    captured = []
    stopped = None
    try:
        task = task_for(family, final.get("condition", "discover"), final["seed"], env=env)
        env.reset(task)
        for step in steps:
            action = parse_action(step["action"])
            before = element_records(env)
            url_before = env.page.url
            try:
                obs, _done, _reward = env.step(action)
            except Exception as exc:  # noqa: BLE001 - replay drifted
                stopped = f"step {step['step']}: {type(exc).__name__}"
                break
            time.sleep(settle_s)
            after = element_records(env)
            captured.append({
                "step": step["step"],
                "action": action,
                "url_before": url_before,
                "url_after": env.page.url,
                "before": before,
                "after": after,
            })
    finally:
        if own_env is not None:
            own_env.close()
    return {"steps": captured, "replay_stopped_at": stopped}


def effective_steps(observations: dict) -> list[dict]:
    out = []
    for item in observations.get("steps") or []:
        diff = element_diff(item["before"], item["after"])
        if diff.strip() or item["url_before"] != item["url_after"]:
            out.append({**item, "diff": diff})
    return out


def build_translator_prompt(goal_template_str: str, action: dict, observation: dict) -> list[dict]:
    idx = action.get("index")
    target = None
    if idx is not None:
        for record in observation["before"]:
            if record["index"] == idx:
                target = record
                break
    lines = [
        f"SITE: a Reddit-style forum (postmill). The recorded agent was",
        f"carrying out this family of tasks: {goal_template_str}",
        "",
        "ONE RECORDED ACTION, addressed by the element's id in the numbered",
        "element list of the page it acted on:",
        f"  {_compact_action(json.dumps(action))}",
        "",
        "THE ELEMENT THAT ID ADDRESSED:",
    ]
    if target is None:
        lines.append("  (the action addressed no element: it is a global action)")
    else:
        lines += [f"  {key}: {target.get(key)!r}" for key in _ELEMENT_KEYS if target.get(key) not in (None, "")]
    if action.get("action_type") == "input_text":
        lines += [
            "",
            "The typed text is a task parameter: it must be rebuilt from the",
            "binding as binding['text'] (the comment body).",
        ]
    lines += [
        "",
        f"PAGE BEFORE: {observation['url_before']}",
        f"PAGE AFTER:  {observation['url_after']}",
        "WHAT THE ACTION CHANGED ('-' left the page, '+' appeared):",
        observation.get("diff") or "  (no element changed)",
        "",
        "Rewrite this action so it works on ANY instance of the family, on a",
        "page whose element ids may differ. The runtime object is ``device``:",
        "  device.find(text=..., contains=..., tag=..., placeholder=...,",
        "               aria=..., name=..., href=..., href_contains=...,",
        "               editable=...) -> element id",
        "  device.click(index=..., **find) ; device.input_text(text, index=..., **find)",
        "  device.goto(url) ; device.scroll(direction) ; device.wait()",
        "  device.settle(s) ; device.page_text() ; device.current_url()",
        "  device.elements() -> the numbered element list (index/tag/text/",
        "               href in document order), for row-structured lookups",
        "Rules:",
        "- Never use the recorded element id; look the element up by the",
        "  semantic attributes that identify it (text, tag, placeholder, href).",
        "- Never hard-code a task value; read it from ``binding``.",
        "- Raise a clear error when the lookup finds nothing.",
        "",
        "Reply in exactly this shape:",
        "ANALYSIS: <one or two lines>",
        "```python",
        "<the snippet, a few lines, using device and binding only>",
        "```",
    ]
    return [
        {"role": "system", "content": TRANSLATOR_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_translation(reply: str) -> dict:
    text = (reply or "").strip()
    analysis = ""
    head = text.split("```", 1)[0].strip()
    if head.upper().startswith("ANALYSIS:"):
        analysis = head[len("ANALYSIS:"):].strip()
    elif head:
        analysis = head
    fence = None
    import re

    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    if match:
        fence = match.group(1).strip()
    return {"analysis": analysis, "snippet": fence or ""}


def translate_trajectory(
    model: str,
    trajectory_jsonl: Path | str,
    family: str,
    observations: dict | None = None,
    client=None,
    env=None,
) -> dict:
    from .agent import WebAgent, _with_backoff
    from .compiler import _openai_client
    from .family import goal_template

    steps, final = load_trajectory(trajectory_jsonl, family)
    if observations is None:
        observations = capture_observations(trajectory_jsonl, family, env=env)
    by_step = {item["step"]: item for item in effective_steps(observations)}
    if client is None:
        client = _openai_client()

    out_steps = []
    prompt_tokens = cached_tokens = completion_tokens = 0
    cost = 0.0
    for step in steps:
        observation = by_step.get(step["step"])
        if observation is None:
            continue
        messages = build_translator_prompt(goal_template({}), observation["action"], observation)
        response = _with_backoff(
            client.chat.completions.create, model=model, messages=messages, temperature=0.0
        )
        usage = WebAgent._usage(response)
        parsed = parse_translation(response.choices[0].message.content or "")
        prompt_tokens += usage.get("prompt_tokens") or 0
        cached_tokens += usage.get("cached_tokens") or 0
        completion_tokens += usage.get("completion_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        out_steps.append({
            "step": step["step"],
            "action": step["action"],
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
