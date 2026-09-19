"""Compile a reactive step trajectory into a parameterized family program.

This is the "compile" step whose price C the paper measures: the harness
takes a recorded trajectory (goal + bid-based step primitives) and turns it
into ONE python function,

    def program(page, binding: dict, base_url: str) -> bool

that completes the task for ANY binding of the six calendar fields. Three
ways to produce the source:

  * ``build_compile_prompt`` -- the LLM prompt (OpenRouter chat format):
    the family goal template with named placeholders, a compact view of
    each recorded step (action, bid, role, name, and the filled value
    mapped to its placeholder), and the code contract above. Two variants:
    ``stepview`` (default; the deterministic ~814-token distillation above)
    and ``fullread`` (the general recipe: the model reads the COMPLETE
    episode -- the concrete goal, every step's action, and the full AX-tree
    text of every screen the agent saw -- and does the distillation
    itself; screenshots are NOT embedded in v1).
  * ``compile_trajectory`` -- call the model through the openai client
    (OPENROUTER_BASE_URL / OPENROUTER_API_KEY from the environment; the
    key is never printed) and extract the python source from the reply.
  * ``MockCompiler`` -- a deterministic template compiler for tests: it
    parses the trajectory's fill/click sequence and emits a program that
    replays the recorded bid-based actions with ``get_elem_by_bid``
    locators, re-marking the page before each action the same way the
    reactive harness did. Zero network calls, and the emitted program
    genuinely passes the gate for arbitrary bindings.

CLI (see README): compile from a trajectory, optionally annotating each
step's role/name by shadow-replaying the trajectory against the live app.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from .actions import Action, ActionError, parse_action
from .agent import GuiAgent
from .conditions import GOAL_TEMPLATE, approx_tokens, goal_text
from .env import FIELDS, instance_for_seed

COMPILE_SYSTEM_PROMPT = (
    "You compile a recorded GUI automation into a reusable, parameterized "
    "python program. You reply with python source code only."
)

# Compile-prompt variants. "stepview" (aka "default") is the deterministic
# programmatic distillation; "fullread" reads the whole episode into the model.
VARIANTS = ("stepview", "default", "fullread")


# ------------------------------------------------------------------ trajectory


def load_trajectory(trajectory_jsonl: Path | str) -> tuple[list[dict], dict]:
    """Parse a runner.py trajectory into (steps, final_record).

    Each step dict carries the parsed ``Action`` (name, bid, raw value),
    the field name the value belongs to (when it is one of the instance's
    six values), and the URL path observed after the action.
    """
    path = Path(trajectory_jsonl)
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{path}: no final record; not a runner trajectory")
    final = finals[-1]
    instance = instance_for_seed(final["seed"])
    value_to_field = {instance[f]: f for f in FIELDS}
    steps: list[dict] = []
    for rec in records:
        if rec.get("record_type") == "final" or not rec.get("action"):
            continue
        try:
            action = parse_action(rec["action"])
        except ActionError:
            continue
        url_after = (rec.get("obs_meta") or {}).get("url") or ""
        entry = {
            "step": rec["step"],
            "action": action,
            "value": action.args[1] if action.name in ("fill", "select") else None,
            "field": None,
            "after_path": urlsplit(url_after).path,
        }
        if entry["value"] is not None:
            entry["field"] = value_to_field.get(entry["value"])
        steps.append(entry)
    if not steps:
        raise ValueError(f"{path}: no parsable step actions")
    return steps, final


def trajectory_instance(final: dict) -> dict:
    """The task instance the trajectory ran (seed-deterministic, as in env)."""
    return instance_for_seed(final["seed"])


def goal_template_text(goal_text_str: str, instance: dict) -> str:
    """The family goal template: each concrete value -> its {placeholder}."""
    text = goal_text_str
    for field in FIELDS:
        value = instance.get(field)
        if value and value in text:
            text = text.replace(value, "{" + field + "}")
    if "{" not in text:  # goal text did not carry the values; use the canonical template
        return GOAL_TEMPLATE
    return text


# ---------------------------------------------------------------------- prompt

_ROLE_BY_ACTION = {
    "click": "button",
    "fill": "textbox",
    "select": "combobox",
    "press": "key",
    "scroll": "page",
    "goto": "page",
    "done": "-",
}


# The shared code-contract tail every compile variant ends with. Kept as one
# constant so the stepview and fullread prompts differ ONLY in how the
# recorded episode is represented, never in what program they ask for.
_CODE_CONTRACT_LINES = [
    "",
    "Write ONE python function",
    "",
    "    def program(page, binding: dict, base_url: str) -> bool",
    "",
    "that uses the given Playwright ``page`` (already open; navigate with",
    "page.goto(base_url + path)) to complete the task for ANY binding, plus",
    "a module-level PARAMS_SCHEMA dict describing each of the six keys.",
    "Rules:",
    "- Use only the Playwright ``page`` object and the standard library.",
    "- Never hard-code any of the six values; read them from ``binding``.",
    "- Drive the form through the browser UI, not the app's HTTP API.",
    "- Re-locate elements per screen as the flow moves on; raise on failure",
    "  and return True once the flow has completed.",
    "Reply with ONLY the python source (one ```python block or raw code),",
    "no explanation.",
]


def _compact_step_line(step: dict, annotations: dict | None) -> str:
    action: Action = step["action"]
    anno = (annotations or {}).get(step["step"]) or {}
    role = anno.get("role") or _ROLE_BY_ACTION.get(action.name, "?")
    name = anno.get("name")
    line = f"step {step['step']:>2}: {action.name}"
    if action.name in ("click", "fill", "select"):
        line += f"(bid={action.args[0]} role={role}"
        line += f" name={name!r}" if name else (f" field={step['field']!r}" if step["field"] else "")
        line += ")"
        if action.name in ("fill", "select"):
            line += f" value={{{step['field']}}}" if step["field"] else f" value={step['value']!r} (constant)"
    elif action.name in ("press", "scroll"):
        line += f"({action.args[0]!r})"
    elif action.name == "goto":
        line += f"('{urlsplit(action.args[0]).path}')"
    else:
        line += "()"
    if step["after_path"]:
        line += f"  -> {step['after_path']}"
    return line


def build_compile_prompt(
    trajectory_jsonl: Path | str,
    goal_text_str: str | None = None,
    layout: str | None = None,
    annotations: dict | None = None,
    variant: str = "stepview",
    ax_trees: dict | None = None,
) -> list[dict]:
    """Messages (OpenRouter chat format) for one compile call.

    ``annotations`` (optional) maps step number -> {"role", "name"}, e.g.
    from :func:`annotate_trajectory`; without it roles are inferred from
    the action kind and names fall back to the placeholder field name.

    ``variant``: "stepview"/"default" builds the compact programmatic
    distillation (the original behavior, byte-identical); "fullread"
    instead puts the COMPLETE episode in the prompt -- the concrete goal
    text, and for every recorded step the full AX-tree text of the screen
    the agent saw (from ``ax_trees``, see :func:`capture_ax_trees`)
    followed by the action it took. Screenshots are NOT embedded (v1).
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown compile variant {variant!r}; expected one of {VARIANTS}")
    if variant == "default":
        variant = "stepview"
    steps, final = load_trajectory(trajectory_jsonl)
    instance = trajectory_instance(final)
    if goal_text_str is None:
        goal_text_str = goal_text(instance)

    if variant == "fullread":
        return _build_fullread_prompt(steps, final, goal_text_str, layout, ax_trees)

    template = goal_template_text(goal_text_str, instance)

    lines = [
        "FAMILY GOAL TEMPLATE (the {placeholders} are the binding's values):",
        template.strip(),
        "",
        f"APP: OpenApps calendar, create-event form, layout: {layout or final.get('layout', 'unknown')}.",
        "The program must work for ANY binding dict with exactly the six keys",
        "title, date, description, location, url, invitees (all non-empty strings).",
        "",
        "RECORDED TRAJECTORY of one instance (bid = element id from the AX-tree",
        "observation the agent saw; value shows the instance's value mapped to",
        "its placeholder; the path after '->' is the page URL once the action",
        "has settled):",
    ]
    lines += ["  " + _compact_step_line(s, annotations) for s in steps]
    lines += _CODE_CONTRACT_LINES

    user = "\n".join(lines)
    return [
        {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _build_fullread_prompt(
    steps: list[dict],
    final: dict,
    goal_text_str: str,
    layout: str | None,
    ax_trees: dict | None,
) -> list[dict]:
    """The fullread variant: the model reads the whole episode, no
    programmatic distillation of the observations (only the replayed AX
    capture, which is a byte-faithful recovery of what the agent saw, not
    a summary). The goal appears with its CONCRETE instance values; the
    model itself must infer the six-parameter family behind them."""
    if not ax_trees:
        raise ValueError(
            "fullread needs the per-step AX-tree capture (capture_ax_trees "
            "against the live app, or --ax-trees from a previous fullread run)"
        )
    captured = {entry["step"]: entry for entry in ax_trees.get("steps", [])}

    lines = [
        "GOAL GIVEN TO THE AGENT (concrete values of one instance):",
        goal_text_str.strip(),
        "",
        f"APP: OpenApps calendar, create-event form, layout: {layout or final.get('layout', 'unknown')}.",
        "The program must work for ANY binding dict with exactly the six keys",
        "title, date, description, location, url, invitees (all non-empty strings).",
        "",
        "COMPLETE RECORDED EPISODE -- everything the agent saw and did.",
        "Each observation is the flattened accessibility tree of the current",
        "page, one line per interactive element: [bid] role 'name'; it is",
        "shown exactly as the agent saw it BEFORE taking the action that",
        "follows it. Screenshots are omitted.",
    ]
    for step in steps:
        entry = captured.get(step["step"])
        if entry is None:
            raise ValueError(f"fullread AX capture is missing step {step['step']}")
        action: Action = step["action"]
        lines += [
            "",
            f"=== observation before step {step['step']} (url path: {entry.get('url_path', '?')}) ===",
            entry["ax"].rstrip(),
            f"--- action {step['step']}: {action.render()}",
        ]
    if ax_trees.get("final"):
        lines += [
            "",
            f"=== final observation after the last action (url path: "
            f"{ax_trees['final'].get('url_path', '?')}) ===",
            ax_trees["final"]["ax"].rstrip(),
        ]
    lines += ["", "The episode above ended with the task completed."]
    lines += _CODE_CONTRACT_LINES

    return [
        {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


# -------------------------------------------------------------------- LLM path

def extract_python_source(reply: str) -> str:
    """The python source from a model reply: ```python fences, ``` fences,
    or raw code (the old compile_gate convention asked for raw code)."""
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
    from openai import OpenAI

    # The API key is read from the environment and never logged or printed.
    return OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
    )


def compile_trajectory(
    model: str,
    trajectory_jsonl: Path | str,
    goal_text_str: str | None = None,
    layout: str | None = None,
    annotations: dict | None = None,
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
    variant: str = "stepview",
    ax_trees: dict | None = None,
) -> dict:
    """One compile call: trajectory -> {program_source, usage, cost_usd}.

    API-level failures (timeouts, 5xx, rate limits -- the DS compile calls
    died on latency) are retried up to ``max_attempts`` times with
    exponential backoff; each attempt's failure is recorded. Retries are for
    transport errors only: a reply that parses is final.

    ``variant``/``ax_trees`` select the fullread prompt (see
    :func:`build_compile_prompt`); usage recording is identical either way
    (prompt/completion/cached_tokens/cost via ``GuiAgent._usage``).
    """
    messages = build_compile_prompt(
        trajectory_jsonl, goal_text_str, layout, annotations,
        variant=variant, ax_trees=ax_trees,
    )
    if client is None:
        client = _openai_client()
    response = None
    failures: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature
            )
            break
        except Exception as exc:  # noqa: BLE001 - transport failure shapes are data
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            if attempt == max_attempts:
                raise RuntimeError(
                    f"compile call failed after {max_attempts} attempts: " + " | ".join(failures)
                ) from exc
            time.sleep(retry_backoff_s * attempt)
    reply = response.choices[0].message.content or ""
    source = extract_python_source(reply)
    usage = GuiAgent._usage(response)  # same accounting as the reactive agent
    return {
        "program_source": source,
        "usage": usage,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "model": model,
        "attempts": attempt,
        "api_failures": failures,
        "variant": variant,
    }


# ------------------------------------------------------------------ annotation

_JS_ROLE_NAME = """el => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let role = 'generic';
    if (tag === 'button' || (tag === 'input' && ['button', 'submit'].includes(type))) role = 'button';
    else if (tag === 'a') role = 'link';
    else if (tag === 'select') role = 'combobox';
    else if (tag === 'input' || tag === 'textarea') role = 'textbox';
    const name = el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
        (el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '') ||
        el.textContent.trim().slice(0, 40) || el.getAttribute('value') || '';
    return {role: role, name: name};
}"""


def annotate_trajectory(
    base_url: str,
    trajectory_jsonl: Path | str,
    headless: bool = True,
) -> dict:
    """Shadow-replay the trajectory to recover each bid's role and name.

    The trajectory records bids but not the AX lines behind them; replaying
    the recorded actions against the live app (marking the page with the
    same browsergym helper the harness used) lets us read every target
    element's role and accessible name. Returns {step number: {role, name}}.

    Replay runs through the env's own popup-aware action execution, so a
    recorded click on a ``target="_blank"`` control (e.g. the calendar's Add
    Event link) follows the popup exactly as the live run now does, instead
    of silently doing nothing.
    """
    from browsergym.core.observation import _pre_extract

    from .env import GuiEnv

    steps, _final = load_trajectory(trajectory_jsonl)
    base = base_url.rstrip("/")
    notes: dict[int, dict] = {}
    env = GuiEnv(base, layout=None, headless=headless)
    try:
        page = env.page
        page.goto(base)
        for step in steps:
            action: Action = step["action"]
            page = env.page  # may have changed after a popup adoption
            if action.name == "goto":
                env._execute_action(action)
            else:
                _pre_extract(page)
                element = page.locator(f'[bid="{action.args[0]}"]')
                try:
                    notes[step["step"]] = element.evaluate(_JS_ROLE_NAME)
                except Exception:
                    notes[step["step"]] = {"role": _ROLE_BY_ACTION.get(action.name, "?"), "name": ""}
                env._execute_action(action)
            page = env.page
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(300)
    finally:
        env.close()
    return notes


def capture_ax_trees(
    base_url: str,
    trajectory_jsonl: Path | str,
    headless: bool = True,
) -> dict:
    """Recover the per-step AX-tree text of a trajectory by shadow-replay.

    trajectory.jsonl records only ``ax_chars`` per step, not the AX text
    itself; the app is deterministic, so replaying the recorded actions
    against the live app reproduces byte-identical screens. The capture
    mirrors the runner's observation timing: the AX tree is taken with the
    env's own ``_observe`` (same browsergym marking + flattening) BEFORE
    each recorded action -- i.e. exactly what the agent saw when choosing
    it -- plus one final observation after the last action.

    Returns {"steps": [{step, url, url_path, ax_chars, ax}], "final":
    {url, url_path, ax_chars, ax} | None} (json-serializable; URLs are
    recorded in full but prompts render the path only, so a replay on a
    different local port yields an identical prompt).
    """
    from .env import GuiEnv

    steps, _final = load_trajectory(trajectory_jsonl)
    base = base_url.rstrip("/")

    def snapshot(env: GuiEnv) -> dict:
        obs = env._observe()
        url = obs.get("url") or ""
        return {
            "url": url,
            "url_path": urlsplit(url).path,
            "ax_chars": len(obs.get("ax_tree_text") or ""),
            "ax": obs.get("ax_tree_text") or "",
        }

    entries: list[dict] = []
    final_snap = None
    env = GuiEnv(base, layout=None, headless=headless)
    try:
        page = env.page
        page.goto(base)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(300)
        for step in steps:
            entries.append({"step": step["step"], **snapshot(env)})
            action: Action = step["action"]
            page = env.page  # may have changed after a popup adoption
            env._execute_action(action)  # popup-aware, as in annotate_trajectory
            page = env.page
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(300)
        final_snap = snapshot(env)
    finally:
        env.close()
    return {"steps": entries, "final": final_snap}


# ----------------------------------------------------------------- mock path

_PROGRAM_TEMPLATE = '''"""Family program for the OpenApps calendar create-event family
(layout: @LAYOUT@).

Compiled deterministically from the recorded reactive trajectory
@TASK_ID@ by guiexp.compiler.MockCompiler: the recorded bid-based action
sequence is replayed with the six calendar values taken from the binding.
Bids are re-derived at run time the same way the reactive harness derived
them -- the page is marked (browsergym's _pre_extract) before each element
action, so every fresh navigation assigns the same bids for the same DOM.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

# The binding contract: the six calendar fields, plain non-empty strings.
PARAMS_SCHEMA = {
@PARAMS_SCHEMA@
}

# One row per recorded step: (action, bid_or_arg, field_or_None,
# literal_value_or_None, expected URL path after the action settles).
_STEPS = [
@STEPS_LITERAL@
]

_SETTLE_TIMEOUT_S = 8.0


def program(page, binding: dict, base_url: str) -> bool:
    """Create the event described by ``binding`` through the UI.

    Returns True when the recorded flow has run to its end; the caller's
    oracle decides whether the app state is correct.
    """
    from browsergym.core.action.utils import get_elem_by_bid
    from browsergym.core.observation import _post_extract, _pre_extract

    base = (base_url or "").rstrip("/")

    def settle(expected_path=None):
        page.wait_for_load_state("domcontentloaded")
        if expected_path:
            deadline = time.monotonic() + _SETTLE_TIMEOUT_S
            while time.monotonic() < deadline and urlparse(page.url).path != expected_path:
                page.wait_for_timeout(100)
        page.wait_for_timeout(300)  # let form posts / redirects settle

    missing = [
        key
        for key in PARAMS_SCHEMA
        if not isinstance(binding.get(key), str) or not binding[key].strip()
    ]
    if missing:
        raise ValueError("binding is missing non-empty string values for: " + ", ".join(missing))

    try:
        for action, arg, field, literal, after in _STEPS:
            if action == "goto":
                page.goto(base + arg)
            else:
                _pre_extract(page)  # (re)derive the bid attributes on this screen
                element = get_elem_by_bid(page, arg)
                if action == "click":
                    element.click()
                elif action == "fill":
                    element.fill(binding[field] if field else literal)
                elif action == "select":
                    element.select_option(binding[field] if field else literal)
                elif action == "press":
                    page.keyboard.press(literal or arg)
                elif action == "scroll":
                    page.mouse.wheel(0, 600 if (literal or arg) == "down" else -600)
                else:
                    raise ValueError(f"unsupported action {action!r}")
            settle(after)
        return True
    finally:
        try:
            _post_extract(page)  # clean the marking's aria attributes
        except Exception:
            pass
'''

_SCHEMA_DESCRIPTIONS = {
    "title": "event title, non-empty string",
    "date": "event date, YYYY-MM-DD string",
    "description": "event description, string",
    "location": "event location, string",
    "url": "event URL, string",
    "invitees": "invitee names, one string (never a list)",
}


class MockCompiler:
    """Deterministic, offline stand-in for the compile LLM call.

    Parses the trajectory's fill/click sequence and emits a program that
    replays the recorded bid-based actions (get_elem_by_bid locators,
    browsergym page marking), with each recorded field value replaced by
    its ``binding`` key. Same return shape as :func:`compile_trajectory`.
    """

    model = "mock"

    def compile(
        self,
        trajectory_jsonl: Path | str,
        goal_text_str: str | None = None,
        layout: str | None = None,
    ) -> dict:
        steps, final = load_trajectory(trajectory_jsonl)
        layout = layout or final.get("layout")
        instance = trajectory_instance(final)

        rows: list[tuple] = []
        covered: set[str] = set()
        for step in steps:
            action: Action = step["action"]
            if action.name == "goto":
                path = urlsplit(action.args[0]).path or "/"
                rows.append(("goto", path, None, None, step["after_path"]))
                continue
            if action.name in ("click", "fill", "select"):
                field, literal = step["field"], None if step["field"] else step["value"]
                if field:
                    covered.add(field)
                rows.append((action.name, action.args[0], field, literal, step["after_path"]))
            elif action.name in ("press", "scroll"):
                rows.append((action.name, action.args[0], None, action.args[0], step["after_path"]))
            # "done" carries no effect; drop it
        missing = [f for f in FIELDS if f not in covered]
        if missing:
            raise ValueError(
                f"trajectory fills no value for: {', '.join(missing)}; "
                "cannot generalize it into a family program"
            )

        params_schema = "\n".join(
            f'    "{field}": "{_SCHEMA_DESCRIPTIONS[field]}",' for field in FIELDS
        )
        steps_literal = "\n".join(f"    {repr(row)}," for row in rows)
        source = (
            _PROGRAM_TEMPLATE
            .replace("@LAYOUT@", str(layout))
            .replace("@TASK_ID@", str(final.get("task_id", "?")))
            .replace("@PARAMS_SCHEMA@", params_schema)
            .replace("@STEPS_LITERAL@", steps_literal)
        )
        compile(source, f"<mock_program_{final.get('task_id', 'x')}>", "exec")  # syntax gate

        # Deterministic pseudo-usage, priced like mock_model's (no network).
        prompt = build_compile_prompt(trajectory_jsonl, goal_text_str, layout)
        prompt_chars = sum(len(m["content"]) for m in prompt)
        prompt_tokens = approx_tokens("x" * prompt_chars)
        completion_tokens = max(1, len(source) // 4)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(prompt_tokens * 1e-6 + completion_tokens * 2e-6, 8),
        }
        return {
            "program_source": source,
            "usage": usage,
            "cost_usd": usage["cost_usd"],
            "model": self.model,
        }


# ----------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trajectory", required=True, help="trajectory.jsonl of the source reactive run")
    parser.add_argument("--model", default=None, help="OpenRouter model id (omit with --mock)")
    parser.add_argument("--mock", action="store_true", help="deterministic offline MockCompiler")
    parser.add_argument("--layout", default=None, help="form layout (default: the trajectory's)")
    parser.add_argument("--annotate", action="store_true",
                        help="shadow-replay the trajectory against the live app to attach each bid's role/name")
    parser.add_argument("--variant", default="stepview", choices=VARIANTS,
                        help="compile prompt variant: stepview/default = programmatic distillation "
                             "(original behavior); fullread = the model reads the COMPLETE episode "
                             "(goal + every action + full per-step AX trees; no screenshots in v1)")
    parser.add_argument("--ax-trees", default=None,
                        help="fullread only: reuse an ax_trees.json captured by a previous fullread "
                             "run (byte-identical prompts across attempts); default: capture by "
                             "shadow-replay against the live app and save ax_trees.json in --out")
    parser.add_argument("--out", default=None, help="output dir (default: experimental-results/guiexp/compile_<model>_<layout>)")
    args = parser.parse_args()

    from .app_server import AppServer
    from .runner import DEFAULT_OUT_ROOT

    variant = "stepview" if args.variant == "default" else args.variant
    model = args.model or ("mock" if args.mock else "openai/gpt-4o-mini")
    layout = args.layout
    annotations = None
    ax_trees = None
    server = None
    try:
        if args.annotate:  # role/name recovery needs the live app
            server = AppServer(layout or "wizard")
            annotations = annotate_trajectory(server.start(), args.trajectory)

        if variant == "fullread":
            if args.ax_trees:
                ax_trees = json.loads(Path(args.ax_trees).read_text())
            else:  # the AX text is not in trajectory.jsonl; replay to recover it
                server = AppServer(layout or "wizard")
                ax_trees = capture_ax_trees(server.start(), args.trajectory, headless=True)

        if args.mock:
            result = MockCompiler().compile(args.trajectory, layout=layout)
        else:
            result = compile_trajectory(
                model, args.trajectory, layout=layout, annotations=annotations,
                variant=variant, ax_trees=ax_trees,
            )
    finally:
        if server is not None:
            server.stop()

    layout_name = layout or json.loads(
        Path(args.trajectory).read_text().splitlines()[-1]
    ).get("layout", "unknown")
    out_dir = Path(args.out) if args.out else (
        DEFAULT_OUT_ROOT / f"compile_{model.replace('/', '-')}_{layout_name}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    program_path = out_dir / "family_program.py"
    program_path.write_text(result["program_source"])
    if variant == "fullread":
        # Keep the exact capture and the exact prompt bytes: a rerun with
        # --ax-trees is then byte-identical, and cache/cost claims stay
        # auditable.
        if ax_trees is not None:
            (out_dir / "ax_trees.json").write_text(json.dumps(ax_trees, indent=1))
        (out_dir / "compile_prompt.txt").write_text(
            build_compile_prompt(
                args.trajectory, layout=layout, variant=variant, ax_trees=ax_trees
            )[1]["content"]
        )
    record = {
        "model": result["model"],
        "trajectory": str(args.trajectory),
        "layout": layout_name,
        "annotated": annotations is not None,
        "variant": variant,
        "ax_trees": str(out_dir / "ax_trees.json") if variant == "fullread" else None,
        "usage": result["usage"],
        "cost_usd": result["cost_usd"],
        "program_path": str(program_path),
        "record_type": "compile",
    }
    (out_dir / "compile.json").write_text(json.dumps(record, indent=1))
    usage = result["usage"]
    print(
        f"compiled {result['program_source'].count(chr(10))} lines, "
        f"{usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0)} tokens, "
        f"cost {result['cost_usd']}"
    )
    print(f"program: {program_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
