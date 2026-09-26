"""Builder + translator stage: compile building trajectories into artifacts.

The WebArena mirror of guiexp_android/compiler.py. Two artifacts from the
same input, both charged as C:

  code: a python ``def program(device, binding: dict) -> bool`` whose only
        localization channel is DOM text/attributes through
        ``device.find(...)`` (never coordinates);
  doc:  a plain-text operation document another agent reads at deploy.

The builder prompt carries, per building instance: the concrete goal, the
recorded actions (compact), and the translator snippets when present.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .family import FAMILY, goal_template, params_to_binding

COMPILE_SYSTEM_PROMPT = (
    "You compile a recorded GUI automation into a reusable, parameterized "
    "python program. You reply with python source code only."
)

DOC_SYSTEM_PROMPT = (
    "You compile recorded GUI automations into a reusable, parameterized "
    "operation document that another agent will read while driving the "
    "site. You reply with the document text only, never with code."
)

ARTIFACTS = ("code", "doc")

FAMILY_HEADER = {
    "app": "a Reddit-style forum (postmill) website",
    "fields": ("forum", "title", "text"),
    "descriptions": {
        "forum": "forum short name as it appears in the site's /f/<forum> URLs and forum lists",
        "title": "the target post's full title, one string copied verbatim",
        "text": "the comment body to leave, one string copied verbatim",
    },
    "note": (
        "The site is a server-rendered forum. The forum page is at\n"
        "/f/<forum> (with hot/new/top sort tabs; /f/<forum>/new lists the\n"
        "25 newest posts). IMPORTANT site quirk: a listing row's title\n"
        "link opens the post's IMAGE or EXTERNAL url, never the post page.\n"
        "The post's own page (with the comment box) opens from the row's\n"
        "comments link ('No comments' / 'N comments', href starting with\n"
        "/f/). On the post page the comment box is the big textarea, and\n"
        "the comment submit button is labelled 'Post'. Programs must find\n"
        "elements by DOM text/attributes via device.find(...), never by\n"
        "coordinates or index."
    ),
}

_TRAJECTORY_LEGEND = (
    "the position of the element in the numbered element list of the",
    "screen the action acted on.",
)


def _system_prompt(artifact: str) -> str:
    if artifact == "code":
        return COMPILE_SYSTEM_PROMPT
    if artifact == "doc":
        return DOC_SYSTEM_PROMPT
    raise ValueError(f"unknown artifact {artifact!r}")


# ------------------------------------------------------------ trajectory io

def load_trajectory(trajectory_jsonl: Path | str, family: str | None = None):
    """Steps (parsable action records) + final record of one episode."""
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{trajectory_jsonl}: no final record")
    final = finals[-1]
    steps = [r for r in records if r.get("action") is not None]
    if family and final.get("family") != family:
        raise ValueError(f"{trajectory_jsonl}: family {final.get('family')} != {family}")
    return steps, final


def trajectory_instance(family: str, final: dict, env=None) -> dict:
    """Rebuild the episode's instance params from the goal text (the seed
    is recorded, but params must match the DB corpus state the episode saw)."""
    from .family import instance_params

    return instance_params(family, final["seed"], env=env)


# ------------------------------------------------------------ prompt pieces

def _compact_action(action_str: str) -> str:
    try:
        obj = json.loads(action_str)
    except Exception:  # noqa: BLE001
        return (action_str or "")[:120]
    at = obj.get("action_type")
    if at == "click":
        return f"click(element {obj.get('index')})"
    if at == "input_text":
        text = (obj.get("text") or "")[:80]
        return f"input_text(element {obj.get('index')}, {text!r})"
    if at == "goto":
        return f"goto({(obj.get('url') or '')[:90]!r})"
    if at == "scroll":
        return f"scroll({obj.get('direction')})"
    return f"{at}()"


def _compact_step_line(step: dict) -> str:
    meta = step.get("obs_meta") or {}
    err = f" ERROR: {meta['last_action_error'][:120]}" if meta.get("last_action_error") else ""
    return f"{_compact_action(step['action'])}{err}"


def _family_header_lines() -> list[str]:
    header = FAMILY_HEADER
    lines = [
        f"FAMILY: {FAMILY} on {header['app']}.",
        "The task's goal template (values are the binding's):",
        f"  {goal_template({})}",
        "",
        f"The binding has {len(header['fields'])} fields:",
    ]
    lines += [f"  {field}: {header['descriptions'][field]}" for field in header["fields"]]
    lines += ["", header["note"]]
    return lines


def _contract_lines(artifact: str) -> list[str]:
    if artifact == "code":
        return [
            "OUTPUT CONTRACT (code):",
            "- One fenced ```python block, nothing else outside it.",
            "- Define exactly:  def program(device, binding: dict) -> bool",
            "  returning True when the comment was successfully posted.",
            "- `binding` holds the fields above. NEVER hard-code their",
            "  values; read them from binding.",
            "- Localize elements ONLY through device.find(text=...,",
            "  contains=..., tag=..., placeholder=..., aria=..., name=...,",
            "  href=..., href_contains=..., editable=...) -> element id,",
            "  then device.click(index=...) / device.input_text(text,",
            "  index=...). Never use coordinates.",
            "- device.elements() returns the page's numbered element list",
            "  (index/tag/text/href/... in document order) when a lookup",
            "  needs to walk a row (e.g. the comments link after a row's",
            "  title link).",
            "- Other device calls: device.goto(url), device.scroll(),",
            "  device.keyboard_enter(), device.wait(), device.settle(s),",
            "  device.page_text(), device.current_url().",
            "- Raise a clear error when a lookup finds nothing.",
            "- The program must work for ANY binding of this family, so",
            "  key on what every instance shares, not on one recording.",
        ]
    return [
        "OUTPUT CONTRACT (doc):",
        "- Plain text only, at most 25 short lines, no code.",
        "- Steps at control granularity (pages, fields in order, the",
        "  buttons between them), parameterized: refer to binding values",
        "  as <forum>, <title>, <text> -- never concrete recorded values.",
        "- Another agent with NO knowledge of this recording will follow",
        "  the document on a fresh binding; it must stand alone.",
    ]


def build_builder_prompt(
    entries: list,
    artifact: str = "code",
    goal_texts: dict | None = None,
) -> list[dict]:
    """Messages for ONE builder call over k building trajectories."""
    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}")
    if not entries:
        raise ValueError("build_builder_prompt needs at least one trajectory")

    blocks = []
    for entry in entries:
        steps, final = load_trajectory(entry["trajectory"])
        goal_text_str = (goal_texts or {}).get(final["seed"]) or entry.get("goal") or "?"
        block = [
            f"=== building instance {len(blocks) + 1} of {len(entries)} (seed {final['seed']}) ===",
            f"GOAL of this instance: {goal_text_str.strip()}",
            f"OUTCOME: {'succeeded' if final.get('success') else 'did not reach the goal'}"
            f" in {final.get('steps', len(steps))} steps.",
            "  RECORDED ACTIONS:",
        ]
        block += ["    " + _compact_step_line(s) for s in steps]
        if entry.get("translation"):
            snippets = entry["translation"].get("steps") or []
            if snippets:
                block += ["  TRANSLATOR SNIPPETS (robust element lookups):"]
                for snippet in snippets:
                    block += ["    step %s: %s" % (snippet["step"], snippet["snippet"].replace("\n", "\n    "))]
        blocks.append(block)

    lines = _family_header_lines()
    lines += [
        "",
        f"BUILDING TRAJECTORIES ({len(entries)} recorded instance"
        f"{'s' if len(entries) != 1 else ''} of this task type). In each",
        "recorded action, 'element <n>' is",
        *_TRAJECTORY_LEGEND,
        "The artifact must work for EVERY instance below and for unseen",
        "bindings of the same family, so prefer what the instances share.",
    ]
    for block in blocks:
        lines += [""] + block
    lines += ["", *_contract_lines(artifact)]
    return [
        {"role": "system", "content": _system_prompt(artifact)},
        {"role": "user", "content": "\n".join(lines)},
    ]


# ------------------------------------------------------------- reply parsing

def extract_python_source(reply: str) -> str:
    """The first fenced python block, or the whole reply if bare code."""
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty model reply")
    match = re.search(r"```(?:python|py)\s*\n(.*?)```", text, re.S)
    if match is None:
        match = re.search(r"```\s*\n(.*?)```", text, re.S)
    if match is not None:
        return match.group(1).strip() + "\n"
    if "def program" in text or "import " in text:
        return text + "\n"
    raise ValueError("no python source found in builder reply")


def extract_document(reply: str) -> str:
    text = (reply or "").strip()
    if not text:
        raise ValueError("empty model reply")
    match = re.search(r"```(?:\w+)?\s*\n(.*?)```", text, re.S)
    if match is not None:
        text = match.group(1).strip()
    return text + "\n"


# ----------------------------------------------------------------- the call

def _openai_client():
    import os

    from openai import OpenAI

    return OpenAI(
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=300.0,
        max_retries=2,
    )


def call_builder(
    model: str,
    messages: list[dict],
    artifact: str = "code",
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
    on_response=None,
    provider_preferences: dict | None = None,
    generation_settings: dict | None = None,
) -> dict:
    """One builder-family model call (both arms' shared body): transport
    failures retried with linear backoff; a code artifact is syntax-checked
    so a broken reply is retried like a transport failure; every attempt
    that REACHED the model is kept in calls_detail (a reply that was
    charged and then rejected is still a real build cost)."""
    import time as _time

    from .agent import WebAgent, _with_backoff
    from .cost_ledger import call_record, sum_usage

    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    if client is None:
        client = _openai_client()
    text = ""
    failures: list[str] = []
    attempt = 0
    calls_detail: list[dict] = []
    for attempt in range(1, max_attempts + 1):
        try:
            request_kwargs = {"model": model, "messages": messages,
                              "temperature": temperature}
            extra_body = {}
            if provider_preferences is not None:
                extra_body["provider"] = provider_preferences
            if generation_settings:
                if generation_settings.get("max_tokens") is not None:
                    request_kwargs["max_tokens"] = generation_settings["max_tokens"]
                if generation_settings.get("reasoning") is not None:
                    extra_body["reasoning"] = generation_settings["reasoning"]
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            response = _with_backoff(client.chat.completions.create, **request_kwargs)
            reply = response.choices[0].message.content or ""
            choice = response.choices[0]
            message = choice.message
            calls_detail.append(call_record(
                attempt, attempt, WebAgent._usage(response),
                stage="builder", accepted=False,
                response_id=getattr(response, "id", None),
                finish_reason=getattr(response.choices[0], "finish_reason", None),
                provider=getattr(response, "provider", None),
                empty_content=not bool(reply.strip()),
                received_at_unix=_time.time(),
                retry_index=attempt - 1,
                requested_model=model,
                served_model=getattr(response, "model", None),
                native_finish_reason=(getattr(response, "model_extra", {}) or {}).get(
                    "native_finish_reason"),
                message=(message.model_dump() if hasattr(message, "model_dump") else {
                    "content": getattr(message, "content", None),
                    "reasoning": getattr(message, "reasoning", None),
                    "tool_calls": getattr(message, "tool_calls", None),
                }),
                provider_preferences=provider_preferences,
                generation_settings=generation_settings,
            ))
            if on_response is not None:
                on_response(dict(calls_detail[-1]))
            if artifact == "code":
                text = extract_python_source(reply)
                compile(text, "<builder_artifact>", "exec")
            else:
                text = extract_document(reply)
            calls_detail[-1]["accepted"] = True
            break
        except Exception as exc:  # noqa: BLE001 - failure shapes are data
            failures.append(
                f"attempt {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            if attempt == max_attempts:
                error = RuntimeError(
                    f"builder call failed after {max_attempts} attempts: "
                    + " | ".join(failures)
                )
                error.calls_detail = list(calls_detail)
                error.api_failures = list(failures)
                raise error from exc
            _time.sleep(retry_backoff_s * attempt)
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
    }
    if artifact == "code":
        result["program_source"] = text
    return result


def compile_trajectories(
    model: str,
    entries: list,
    family: str,
    artifact: str = "code",
    client=None,
    goal_texts: dict | None = None,
    temperature: float = 0.0,
) -> dict:
    if family != FAMILY:
        raise ValueError(f"unknown family {family!r}")
    messages = build_builder_prompt(entries, artifact=artifact, goal_texts=goal_texts)
    result = call_builder(
        model, messages, artifact=artifact, client=client, temperature=temperature,
    )
    result["k"] = len(entries)
    result["stage"] = "builder_initial"
    return result


# -------------------------------------------------------------- refine (repair)

def _hybrid_lines(hybrid: dict) -> list[str]:
    lines = [
        "IT FAILED on a building instance that it had to handle.",
        f"FAILING INSTANCE: {json.dumps(hybrid.get('binding') or {})}",
        f"GOAL: {(hybrid.get('goal') or '').strip()}",
        f"FAILURE: {hybrid.get('failure') or 'the checker rejected the end state'}",
        "",
        "EXECUTED PROGRAM TRACE up to the breakpoint (what the artifact",
        "actually did on the site; 'NOT FOUND' lines are element lookups",
        "that matched nothing):",
    ]
    trace = hybrid.get("program_trace") or []
    lines += [f"  {line}" for line in trace] or ["  (no action reached the site)"]
    lines += ["", "BREAKPOINT PAGE (the numbered element list where it stopped):"]
    lines += [f"  {line}" for line in (hybrid.get("breakpoint_screen") or "").splitlines()[:60]]
    analysis = (hybrid.get("analysis") or "").strip()
    if analysis:
        lines += ["", "BREAKPOINT ANALYSIS (cause, what was already done, how to carry on):",
                  analysis]
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
) -> list[dict]:
    """Messages for ONE refinement call in the verify-and-repair loop."""
    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    lines = _family_header_lines()
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
    on_response=None,
    provider_preferences: dict | None = None,
    generation_settings: dict | None = None,
) -> dict:
    """One refinement call over the hybrid failure context; charged as C."""
    messages = build_refine_prompt(
        family, artifact, current_artifact, hybrid, conclusions)
    result = call_builder(
        model, messages, artifact=artifact, client=client,
        temperature=temperature, max_attempts=max_attempts,
        on_response=on_response,
        provider_preferences=provider_preferences,
        generation_settings=generation_settings,
    )
    result["stage"] = "builder_refine"
    return result
