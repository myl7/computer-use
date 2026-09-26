"""Translator stage: one model call per effective action of a building
trajectory, rewriting a recorded index-addressed action into a semantic
find-plus-action snippet.

AutoRPA (Section 3.2, page 4) runs a translator agent over "each effective
action (i.e., actions causing screen changes)". Its output is a robustness
analysis of what the action depends on plus a snippet that replaces
``click(index=2)`` with an element lookup on semantic attributes.

Deliberate deviation from the paper, recorded in
docs/autorpa-replication-protocol.md section 5.3(a): the input here is TEXT
SHAPED, not multimodal. The paper sends the before and after screenshots; on
this harness one observation is 4.2k to 7.2k tokens, so a before-and-after
multimodal call would cost 10k to 14k and put the translator at about one
whole reactive episode per trajectory, four times their ratio. Instead each
call gets the action JSON, the target element's a11y record, and a compact
diff of the before and after element lists, which lands near their measured
12k per trajectory.

The before/after element lists come from a zero-token shadow replay of the
trajectory on the live emulator (:func:`capture_observations`), the same
mechanism ``compiler.annotate_trajectory`` already uses to recover element
hints. Snippets are written in the vocabulary ``program_runtime.ProgramDevice``
exposes, so the builder can paste them into a program unchanged.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.translator \
        --trajectory .../trajectory.jsonl --family ContactsAddContact \
        --model z-ai/glm-5.3-flash --out .../translation.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import android_env
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

# Element fields the diff and the target record carry. Coordinates are left
# out on purpose: a snippet that keys on them is exactly what the stage is
# supposed to remove.
_ELEMENT_KEYS = ("text", "hint", "description", "class_name", "clickable", "editable")


# ------------------------------------------------------------- observations


def _element_records(ui_elements) -> list[dict]:
    """The compact a11y record of every element on the current screen."""
    out = []
    for index, element in enumerate(ui_elements):
        out.append({
            "index": index,
            "text": (getattr(element, "text", "") or "").strip(),
            "hint": (getattr(element, "hint_text", "") or "").strip(),
            "description": (getattr(element, "content_description", "") or "").strip(),
            "class_name": (getattr(element, "class_name", "") or "").split(".")[-1],
            "clickable": bool(getattr(element, "is_clickable", False)),
            "editable": bool(getattr(element, "is_editable", False)),
        })
    return out


def _element_label(record: dict) -> str:
    parts = [f"{key}={record[key]!r}" for key in ("text", "hint", "description") if record.get(key)]
    flags = [key for key in ("clickable", "editable") if record.get(key)]
    body = ", ".join(parts) or "(no text)"
    tail = f" [{'/'.join(flags)}]" if flags else ""
    return f"{record['class_name']} {body}{tail}"


def element_diff(before: list[dict], after: list[dict], max_lines: int = 24) -> str:
    """A compact before/after element-list diff: what left and what arrived.

    Identity is the element's semantic content, not its index, so a screen
    that only renumbered its elements produces an empty diff and the action
    counts as ineffective.
    """
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


def capture_observations(
    trajectory_jsonl: Path | str,
    family: str | None = None,
    env=None,
    settle_s: float = 2.0,
) -> dict:
    """Shadow-replay the trajectory, recording the element list before and
    after every action. Zero model calls; needs the live emulator.

    Returns {"steps": [{step, activity_before, activity_after, target,
    before, after}], "replay_stopped_at": ...}.
    """
    import time as _time

    from android_world.env import json_action

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    own_env = None
    if env is None:
        own_env = env = android_env.AndroidWorldEnv()
    captured: list[dict] = []
    stopped = None
    try:
        task = android_env.get_task(family, final.get("condition", "discover"), final["seed"])
        env.reset(task)
        for step in steps:
            action = step["action"]
            state = env.aw_env.get_state(wait_to_stabilize=False)
            before = _element_records(state.ui_elements)
            activity_before = env.aw_env.foreground_activity_name
            index = action.get("index")
            target = before[index] if index is not None and 0 <= index < len(before) else None
            try:
                env.aw_env.execute_action(json_action.JSONAction(**action))
            except Exception as exc:  # noqa: BLE001 - replay drifted off the recorded screens
                stopped = f"step {step['step']}: {type(exc).__name__}"
                break
            _time.sleep(settle_s)
            state = env.aw_env.get_state(wait_to_stabilize=False)
            captured.append({
                "step": step["step"],
                "activity_before": activity_before,
                "activity_after": env.aw_env.foreground_activity_name,
                "target": target,
                "before": before,
                "after": _element_records(state.ui_elements),
            })
    finally:
        if own_env is not None:
            own_env.close()
    return {"steps": captured, "replay_stopped_at": stopped}


def effective_steps(observations: dict) -> list[dict]:
    """The steps whose action changed the screen (AutoRPA's 'effective
    actions'): the element list or the foreground activity moved."""
    out = []
    for item in observations.get("steps") or []:
        diff = element_diff(item["before"], item["after"])
        if diff.strip() or item["activity_before"] != item["activity_after"]:
            out.append({**item, "diff": diff})
    return out


# ------------------------------------------------------------------ prompt


def build_translator_prompt(
    family: str,
    step: dict,
    observation: dict,
    goal_template: str,
) -> list[dict]:
    """Messages for ONE translator call: one action, one target element, one
    before/after element-list diff. No screenshots (see the module docstring)."""
    action = step["action"]
    target = observation.get("target")
    lines = [
        f"APP: {family} on Android. The recorded agent was carrying out this",
        f"family of tasks: {goal_template.strip()}",
        "",
        "ONE RECORDED ACTION, addressed by the element's index in the a11y",
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
        "  device.find(text=..., contains=..., hint=..., description=...,",
        "               clickable=..., editable=...) -> element index or None",
        "  device.click(index=None, **find) ; device.long_press(index=None, **find)",
        "  device.input_text(text, index=None, **find) ; device.keyboard_enter()",
        "  device.scroll(direction) ; device.open_app(name)",
        "  device.navigate_back() ; device.navigate_home() ; device.settle(s)",
        "  device.elements() -> the fresh element list as dicts",
        "Rules:",
        "- Never use the recorded index; look the element up by the semantic",
        "  attributes that identify it (text, hint, description, class).",
        "- Never hard-code a task value; read it from ``binding``.",
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
    """Split a translator reply into its analysis and its python snippet."""
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
    if snippet == text:  # no fence at all: the whole reply was prose
        snippet = ""
    return {"analysis": analysis, "snippet": snippet}


# ------------------------------------------------------------------- stage


def translate_trajectory(
    model: str,
    trajectory_jsonl: Path | str,
    family: str | None = None,
    observations: dict | None = None,
    client=None,
    env=None,
    temperature: float = 0.0,
) -> dict:
    """One translator call per effective action; returns the translation.

    ``observations`` (a previous :func:`capture_observations` result) skips
    the shadow replay, which is how tests run this stage without an emulator.
    """
    from .agent import AndroidAgent
    from .compiler import _openai_client, goal_template_text

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    params = trajectory_instance(family, final)
    goal_text_str = android_env.goal_text(
        android_env.get_task(family, final.get("condition", "discover"), final["seed"])
    )
    template = goal_template_text(goal_text_str, family, params)

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
        if observation is None:  # ineffective action: nothing to translate
            continue
        messages = build_translator_prompt(family, step, observation, template)
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature
        )
        usage = AndroidAgent._usage(response)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trajectory", required=True)
    parser.add_argument("--family", default=None)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--observations", default=None,
                        help="reuse a previous capture_observations JSON instead of shadow-replaying")
    parser.add_argument("--out", required=True, help="translation.json path")
    parser.add_argument("--keep-emulator", action="store_true")
    args = parser.parse_args()

    observations = json.loads(Path(args.observations).read_text()) if args.observations else None
    env = None
    try:
        if observations is None:
            env = android_env.AndroidWorldEnv()
        result = translate_trajectory(
            args.model, args.trajectory, args.family, observations=observations, env=env
        )
    finally:
        if env is not None:
            env.close()
            if not args.keep_emulator:
                env.stop_emulator()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    print(json.dumps(result["totals"], indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
