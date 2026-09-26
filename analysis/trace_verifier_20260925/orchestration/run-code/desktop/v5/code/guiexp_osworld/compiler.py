"""Compile reactive OSWorld trajectories into parameterized programs.

Ported from guiexp_android/compiler.py; the artifact contract is the same
(``def program(device, binding: dict) -> bool``), the ``device`` vocabulary is
this arm's ProgramDevice (a11y name/text/role lookups, click/input_text by
index, press/hotkey for the keyboard, type_at_caret for caret typing). The
family header, trajectory view, code/doc contract tails and the repair
prompt mirror the Android arm's so the builder's task is directly comparable.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import families, guest_env
from .accounting import call_record, sum_usage

COMPILE_SYSTEM_PROMPT = (
    "You compile a recorded GUI automation into a reusable, parameterized "
    "python program. You reply with python source code only."
)

DOC_SYSTEM_PROMPT = (
    "You compile recorded GUI automations into a reusable, parameterized "
    "operation document that another agent will read while driving the apps. "
    "You reply with the document text only, never with code."
)

ARTIFACTS = ("code", "doc")


class GenerationFailure(RuntimeError):
    """A completed model response could not produce a valid artifact."""


def _system_prompt(artifact: str) -> str:
    if artifact == "code":
        return COMPILE_SYSTEM_PROMPT
    if artifact == "doc":
        return DOC_SYSTEM_PROMPT
    raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")


def binding_fields(family: str) -> tuple[str, ...]:
    return families.binding_fields(family)


def params_to_binding(family: str, params: dict) -> dict:
    return families.params_to_binding(family, params)


def binding_to_params(family: str, binding: dict) -> dict:
    return families.binding_to_params(family, binding)


# ------------------------------------------------------------------ trajectory


def load_trajectory(trajectory_jsonl: Path | str,
                    family: str | None = None) -> tuple[list[dict], dict]:
    """Parse a runner trajectory into (steps, final record).

    Each step dict carries the parsed action dict, the typed value mapped to
    its binding placeholder (``field`` + ``fragment``) and the active window
    observed after the action settled.
    """
    path = Path(trajectory_jsonl)
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{path}: no final record; not a runner trajectory")
    final = finals[-1]
    family = family or final["family"]
    params = families.instance_params(family, final["seed"])
    steps: list[dict] = []
    for rec in records:
        if rec.get("record_type") == "final" or not rec.get("action"):
            continue
        try:
            action = json.loads(rec["action"])
        except (TypeError, ValueError):
            continue
        if not isinstance(action, dict):
            continue
        entry = {
            "step": rec["step"],
            "action": action,
            "value": action.get("text") if action.get("action_type") == "input_text" else None,
            "field": None,
            "fragment": None,
            "after_activity": (rec.get("obs_meta") or {}).get("url") or "",
        }
        if entry["value"] is not None:
            entry["field"], entry["fragment"] = map_value(entry["value"], params, family)
        steps.append(entry)
    if not steps:
        raise ValueError(f"{path}: no parsable step actions")
    return steps, final


def trajectory_instance(family: str, final: dict) -> dict:
    return families.instance_params(family, final["seed"])


def map_value(text: str, params: dict, family: str) -> tuple[str | None, tuple | None]:
    """Map a recorded typed value onto its binding placeholder (Android arm's
    rules: whole / digits-normalized / whitespace token / dotted base-ext)."""
    fields = [f for f in binding_fields(family) if isinstance(params.get(f), str)]
    for field in fields:
        if text == params[field]:
            return field, ("whole",)
    for field in fields:
        if _digits(text) and _digits(text) == _digits(params[field]):
            return field, ("whole",)
    for field in fields:
        tokens = params[field].split()
        if len(tokens) > 1 and text in tokens:
            return field, ("token", tokens.index(text))
    for field in fields:
        value = params[field]
        if "." in value:
            base, ext = value.rsplit(".", 1)
            if ext and text == base:
                return field, ("base",)
            if ext and text == "." + ext:
                return field, ("ext",)
    return None, None


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def placeholder_expr(field: str | None, fragment: tuple | None) -> str:
    if field is None:
        return ""
    kind = fragment[0]
    if kind == "whole":
        return "binding[" + repr(field) + "]"
    if kind == "token":
        return f"binding[{field!r}].split()[{fragment[1]}]"
    if kind == "base":
        return f"binding[{field!r}].rsplit('.', 1)[0]"
    if kind == "ext":
        return f"'.' + binding[{field!r}].rsplit('.', 1)[1]"
    raise ValueError(f"unknown fragment {fragment!r}")


def goal_template(goal_text_str: str, family: str, params: dict) -> str:
    return families.goal_template_text(family, goal_text_str, params)


# -------------------------------------------------------------------- prompt


def _compact_action(action: dict, field: str | None, fragment: tuple | None) -> str:
    parts = [f'"{key}": {json.dumps(value)}' for key, value in action.items()]
    if action.get("action_type") == "input_text" and field:
        parts = [
            f'"text": {placeholder_expr(field, fragment)}' if p.startswith('"text"') else p
            for p in parts
        ]
    return "{" + ", ".join(parts) + "}"


def _compact_step_line(step: dict, annotations: dict | None) -> str:
    action = step["action"]
    line = f"step {step['step']:>2}: {_compact_action(action, step['field'], step['fragment'])}"
    anno = (annotations or {}).get(step["step"]) or {}
    described = [f"{k}={anno[k]!r}" for k in ("name", "role", "description") if anno.get(k)]
    if described:
        line += "  [" + ", ".join(described) + "]"
    if step["after_activity"]:
        line += f"  -> {step['after_activity']}"
    return line


_CODE_CONTRACT_LINES = [
    "Write ONE python function",
    "",
    "    def program(device, binding: dict) -> bool",
    "",
    "that completes the task for ANY binding by driving the desktop GUI",
    "through the given ``device`` object, plus a module-level PARAMS_SCHEMA",
    "dict describing each binding key. The device API (every action settles",
    "~2 s before returning):",
    "  device.elements()  -> fresh list of dicts {index, role, name, text,",
    "                        description, clickable, editable, x, y, w, h}",
    "  device.find(name=..., contains=..., role=..., description=...,",
    "               editable=..., clickable=...) -> element index or None",
    "  device.click(index=None, **find)   # index OR find-criteria",
    "  device.input_text(text, index=None, **find)  # click field, select,",
    "                                               # type; commits cells",
    "  device.type_at_caret(text)         # type at the current caret",
    "  device.press(key) ; device.hotkey(*keys)   # keyboard, e.g. 'enter'",
    "  device.scroll(direction='down'|'up'|'left'|'right')",
    "  device.open_app(app_name) ; device.wait() ; device.settle(seconds)",
    "  device.active_window()  # the where-am-I title string",
    "  device.execute({...})  # raw action-space dict passthrough",
    "Rules:",
    "- Use only ``device``, ``time`` and the standard library.",
    "- Never hard-code any binding value; read them from ``binding``.",
    "- Re-locate elements per screen (device.find / device.elements);",
    "  indexes from another screen are stale and fail.",
    "- Locate elements ONLY by a11y name/text/role -- never by screen",
    "  coordinates.",
    "- Drive the apps' GUI only; never write files, run shell commands,",
    "  or call LibreOffice internals by other means.",
    "- Raise on failure; return True once the flow has completed.",
    "Reply with ONLY the python source (one ```python block or raw code),",
    "no explanation.",
]

_DOC_CONTRACT_LINES = [
    "Write ONE plain-text OPERATION DOCUMENT that tells an agent operating",
    "this desktop how to carry out any instance of this task type. The agent",
    "reading it sees the desktop's screens and the numbered element list; it",
    "does NOT see the trajectories above.",
    "Requirements:",
    "- Refer to the task's values only through the {placeholders} of the",
    "  family goal template; never write a concrete value from a recorded",
    "  instance.",
    "- Name the screens in the order they are met, the control that leads",
    "  from each screen to the next, and the field each value goes into.",
    "- State the app-specific traps the recorded trajectories reveal (the",
    "  save dialog's Name field and Desktop place, a cell edit that must be",
    "  committed, a middle line that must stay empty).",
    "- Write NO code: no python, no pseudo-code, no device.* calls, no",
    "  JSON actions, and no element indexes (indexes differ per screen).",
    "- Keep it to at most 25 short lines, imperative, one step per line.",
    "Reply with ONLY the document text, no preamble and no code fences.",
]


def _contract_lines(artifact: str) -> list[str]:
    if artifact == "code":
        return list(_CODE_CONTRACT_LINES)
    if artifact == "doc":
        return list(_DOC_CONTRACT_LINES)
    raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")


def _family_header_lines(family: str, template: str) -> list[str]:
    spec = families.FAMILY_BINDINGS[family]
    keys = ", ".join(spec["fields"])
    lines = [
        "FAMILY GOAL TEMPLATE (the {placeholders} are the binding's values):",
        template.strip(),
        "",
        f"APP: {spec['app']} on a Linux desktop (a11y element indexes address the UI).",
        "The program must work for ANY binding dict with exactly the keys",
        f"{keys}:",
    ]
    lines += [f"  {field}: {desc}" for field, desc in spec["descriptions"].items()]
    lines += ["", f"App-specific knowledge: {spec['note']}", ""]
    return lines


_TRAJECTORY_LEGEND = [
    "in the numbered element list of the screen the agent saw BEFORE acting;",
    "[name=... role=...] describes that element; the window title after '->'",
    "is the active window once the action has settled; typed values are",
    "shown as the binding expression that rebuilds them):",
]


def _trajectory_goal(family: str, final: dict) -> str:
    return families.goal_text(family, final["seed"],
                              families.instance_params(family, final["seed"]))


# ------------------------------------------------------------------ annotation


def annotate_trajectory(trajectory_jsonl: Path | str, family: str | None = None,
                        env=None, settle_s: float = 1.5) -> dict:
    """Shadow-replay the trajectory to recover each index's element identity.

    Zero LLM calls; needs the live guest. Returns {step: {name, role,
    description}} plus a "_replay_stopped_at" note when replay drifted.
    """
    import time as _time

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    own_env = None
    if env is None:
        own_env = env = guest_env.OSWorldEnv()
    notes: dict = {}
    try:
        task = guest_env.get_task(family, final.get("condition", "discover"), final["seed"])
        env.reset(task)
        for step in steps:
            action = step["action"]
            index = action.get("index")
            if index is not None:
                element = env.element_by_index(int(index))
                if element is not None:
                    notes[step["step"]] = {
                        "name": element["name"],
                        "role": element["role"],
                        "description": element["description"],
                    }
            try:
                env.execute_action(action)
            except Exception as exc:  # noqa: BLE001 - replay drifted
                notes["_replay_stopped_at"] = f"step {step['step']}: {type(exc).__name__}"
                break
            _time.sleep(settle_s)
    finally:
        if own_env is not None:
            own_env.close()
    return notes


# ------------------------------------------------------- k-trajectory builder


def normalize_entry(entry) -> dict:
    if isinstance(entry, (str, Path)):
        return {"trajectory": Path(entry), "annotations": None,
                "translation": None, "label": None}
    out = dict(entry)
    out["trajectory"] = Path(out["trajectory"])
    out.setdefault("annotations", None)
    out.setdefault("translation", None)
    out.setdefault("label", None)
    return out


def _translation_lines(translation: dict | None) -> list[str]:
    steps = (translation or {}).get("steps") or []
    if not steps:
        return []
    lines = [
        "  TRANSLATED ACTIONS (one translator call per effective action: the",
        "  recorded element index rewritten as a semantic lookup, plus what",
        "  the action depends on):",
    ]
    for item in steps:
        snippet = (item.get("snippet") or "").strip()
        if not snippet:
            continue
        head, *rest = snippet.splitlines()
        lines.append(f"    step {item.get('step')}: {head}")
        lines += [f"      {line}" for line in rest]
        analysis = (item.get("analysis") or "").strip().splitlines()
        if analysis:
            lines.append(f"      # {analysis[0][:180]}")
    return lines


def build_builder_prompt(
    entries: list,
    family: str | None = None,
    artifact: str = "code",
) -> list[dict]:
    """Messages for ONE builder call over k = 1, 2 or 3 building trajectories."""
    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    entries = [normalize_entry(e) for e in entries]
    if not entries:
        raise ValueError("build_builder_prompt needs at least one building trajectory")

    blocks: list[list[str]] = []
    template = None
    for entry in entries:
        steps, final = load_trajectory(entry["trajectory"], family)
        entry_family = family or final["family"]
        params = trajectory_instance(entry_family, final)
        goal_text_str = _trajectory_goal(entry_family, final)
        if template is None:
            family = entry_family
            template = goal_template(goal_text_str, entry_family, params)
        label = entry["label"] or f"seed {final['seed']}"
        block = [
            f"GOAL of this instance: {goal_text_str.strip()}",
            f"OUTCOME: {'succeeded' if final.get('success') else 'did not reach the goal'}"
            f" in {final.get('steps', len(steps))} steps.",
            "  RECORDED ACTIONS:",
        ]
        block += ["    " + _compact_step_line(s, entry["annotations"]) for s in steps]
        block += _translation_lines(entry["translation"])
        blocks.append([f"=== building instance {len(blocks) + 1} of {len(entries)} ({label}) ==="] + block)

    lines = _family_header_lines(family, template)
    lines += [
        f"BUILDING TRAJECTORIES ({len(entries)} recorded instance"
        f"{'s' if len(entries) != 1 else ''} of this task type). In each",
        "recorded action, index = the element's position",
        *_TRAJECTORY_LEGEND,
        "The artifact must work for EVERY instance below and for unseen",
        "bindings of the same family, so prefer what the instances share.",
    ]
    for block in blocks:
        lines += [""] + block
    lines += [""]
    lines += _contract_lines(artifact)

    return [
        {"role": "system", "content": _system_prompt(artifact)},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _hybrid_lines(hybrid: dict) -> list[str]:
    lines = [
        "IT FAILED on a building instance that it had to handle.",
        f"FAILING INSTANCE: {json.dumps(hybrid.get('binding') or {})}",
        f"GOAL: {(hybrid.get('goal') or '').strip()}",
        f"FAILURE: {hybrid.get('failure') or 'the checker rejected the end state'}",
        "",
        "EXECUTED PROGRAM TRACE up to the breakpoint (what the artifact",
        "actually did on the desktop):",
    ]
    trace = hybrid.get("program_trace") or []
    lines += [f"  {line}" for line in trace] or ["  (no action reached the guest)"]
    lines += ["", "BREAKPOINT SCREEN (the element list at the moment it stopped):"]
    lines += [f"  {line}" for line in (hybrid.get("breakpoint_screen") or "").splitlines()[:60]]
    analysis = (hybrid.get("analysis") or "").strip()
    if analysis:
        lines += ["", "BREAKPOINT ANALYSIS (cause, what was already done, how to carry on):", analysis]
    resume = hybrid.get("resume_actions") or []
    lines += [
        "",
        "REACTIVE REPAIR from the breakpoint (a live agent finished the same",
        f"instance; outcome: {'goal reached' if hybrid.get('resume_success') else 'goal NOT reached'}):",
    ]
    lines += [f"  {item}" for item in resume] or ["  (the agent took no action)"]
    return lines


def build_refine_prompt(
    family: str,
    artifact: str,
    current_artifact: str,
    hybrid: dict,
    conclusions: list[str] | None = None,
    template: str | None = None,
) -> list[dict]:
    """Messages for ONE refinement call in the verify-and-repair loop."""
    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    lines = _family_header_lines(family, template or "(see the failing instance below)")
    lines += ["CURRENT ARTIFACT, the one to repair:"]
    if artifact == "code":
        lines += ["```python", current_artifact.rstrip(), "```"]
    else:
        lines += ["---", current_artifact.rstrip(), "---"]
    lines += ["", *_hybrid_lines(hybrid)]
    for i, conclusion in enumerate(conclusions or [], start=1):
        if i == 1:
            lines += ["", "CONCLUSIONS from earlier rounds of this repair loop:"]
        lines.append(f"  {i}. {conclusion.strip()}")
    lines += [
        "",
        "Repair the artifact so it handles this instance AND keeps handling",
        "the ones it already passed. Change what the failure shows to be",
        "wrong; do not rewrite what already worked.",
        "",
    ]
    lines += _contract_lines(artifact)
    return [
        {"role": "system", "content": _system_prompt(artifact)},
        {"role": "user", "content": "\n".join(lines)},
    ]


# -------------------------------------------------------------------- LLM path


def extract_python_source(reply: str) -> str:
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty model reply")
    match = re.search(r"```(?:python|py)\s*\n(.*?)```", text, re.S)
    if match is None:
        match = re.search(r"```\s*\n(.*?)```", text, re.S)
    if match is not None:
        return match.group(1).strip() + "\n"
    return text + "\n"


def _openai_client():
    import os

    if os.environ.get("GUIEXP_MODEL_ROUTE") == "qwen-official-payg-v1":
        from .routing import qwen_official_client
        return qwen_official_client()

    from openai import OpenAI

    return OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=180.0,
        max_retries=2,
    )


def extract_document(reply: str) -> str:
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty model reply")
    match = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.S)
    if match is not None:
        text = match.group(1).strip()
    return text + "\n"


def call_builder(
    model: str,
    messages: list[dict],
    artifact: str = "code",
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
    call_checkpoint=None,
) -> dict:
    """One builder-family model call (the Android arm's shared body):
    transport failures retried with linear backoff; a code artifact is
    syntax-checked so a broken reply is retried like a transport failure;
    every attempt that REACHED the model is kept in calls_detail (a reply
    that was charged and then rejected is still a real build cost)."""
    from .agent import OSWorldAgent

    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    if client is None:
        client = _openai_client()
    text = ""
    failures: list[str] = []
    failure_kinds: list[str] = []
    attempt = 0
    calls_detail: list[dict] = []
    for attempt in range(1, max_attempts + 1):
        response_received = False
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature
            )
            response_received = True
            reply = response.choices[0].message.content or ""
            choice = response.choices[0] if response.choices else None
            calls_detail.append(call_record(
                attempt, attempt, OSWorldAgent._usage(response),
                stage="builder", accepted=False,
                response_id=getattr(response, "id", None),
                finish_reason=getattr(choice, "finish_reason", None),
            ))
            if call_checkpoint is not None:
                call_checkpoint(calls_detail[-1])
            if getattr(choice, "finish_reason", None) == "length":
                raise GenerationFailure("response ended at its output limit")
            if artifact == "code":
                text = extract_python_source(reply)
                compile(text, "<builder_artifact>", "exec")
            else:
                text = extract_document(reply)
            calls_detail[-1]["accepted"] = True
            break
        except Exception as exc:  # noqa: BLE001 - transport failure shapes are data
            failure_kind = "generation_failure" if response_received else "transport_failure"
            failure_kinds.append(failure_kind)
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None)
            message = str(exc).lower()
            if status == 402 or ("insufficient" in message and "credit" in message):
                raise
            if attempt == max_attempts:
                if all(kind == "generation_failure" for kind in failure_kinds):
                    raise GenerationFailure(
                        f"builder generation failed after {max_attempts} attempts: "
                        + " | ".join(failures)
                    ) from exc
                raise RuntimeError(
                    f"builder call failed after {max_attempts} attempts: " + " | ".join(failures)
                ) from exc
            time.sleep(retry_backoff_s * attempt)
    usage = sum_usage([c["usage"] for c in calls_detail])
    result = {
        "artifact": artifact,
        "artifact_text": text,
        "usage": usage,
        "calls_detail": calls_detail,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "model": model,
        "attempts": attempt,
        "api_failures": failures,
        "failure_kinds": failure_kinds,
    }
    if artifact == "code":
        result["program_source"] = text
    return result


def compile_trajectories(
    model: str,
    entries: list,
    family: str | None = None,
    artifact: str = "code",
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
) -> dict:
    """One builder call over k building trajectories; charged as C."""
    messages = build_builder_prompt(entries, family, artifact=artifact)
    result = call_builder(
        model, messages, artifact=artifact, client=client, temperature=temperature,
        max_attempts=max_attempts, retry_backoff_s=retry_backoff_s,
    )
    result["k"] = len(entries)
    result["stage"] = "builder_initial"
    return result


def refine_artifact(
    model: str,
    family: str,
    current_artifact: str,
    hybrid: dict,
    conclusions: list[str] | None = None,
    artifact: str = "code",
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
    call_checkpoint=None,
) -> dict:
    """One refinement call in the verify-and-repair loop; charged as C."""
    messages = build_refine_prompt(
        family, artifact, current_artifact, hybrid, conclusions
    )
    result = call_builder(
        model, messages, artifact=artifact, client=client, temperature=temperature,
        max_attempts=max_attempts, retry_backoff_s=retry_backoff_s,
        call_checkpoint=call_checkpoint,
    )
    result["stage"] = "builder_refine"
    return result
