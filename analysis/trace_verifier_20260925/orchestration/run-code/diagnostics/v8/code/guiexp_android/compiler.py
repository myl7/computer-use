"""Compile a reactive Android trajectory into a parameterized program.

The Android arm of the web side's ``guiexp/compiler.py``. The compile step
whose price C is measured: a recorded trajectory (JSON-action steps against
the a11y element list) is turned into ONE python function

    def program(device, binding: dict) -> bool

that completes the family's task for ANY binding, driving the phone through
the ``device`` wrapper (``guiexp_android.program_runtime.ProgramDevice``:
fresh a11y dumps, find-by-text/hint, and the android_world JSONAction space;
raw ``adb shell`` is available for uiautomator-dump style programs).

Ways to produce the source:

  * ``build_compile_prompt`` -- the LLM prompt (OpenRouter chat format): the
    family goal template with named placeholders, a compact view of each
    recorded step (action JSON, the target element's text/hint when
    annotated, the typed value mapped to its placeholder expression, and the
    foreground activity after the action settles). No screenshots.
  * ``compile_trajectory`` -- call the model through the openai client
    (OPENROUTER_BASE_URL / OPENROUTER_API_KEY from the environment; the key
    is never printed) and extract the python source from the reply.
  * ``MockCompiler`` -- a deterministic template compiler for tests: it
    replays the recorded actions with each typed value replaced by its
    binding placeholder, the element index re-resolved against a FRESH
    a11y dump at execution time (exactly how the reactive harness resolved
    it), or, when ``annotations`` are supplied, re-found by the recorded
    text hints. Zero network calls, and the emitted program genuinely passes
    the gate for arbitrary bindings on the replay-stable families.

Binding contract per family (the "six-field equivalent"; android_world's
own ``generate_random_params`` keys):

    ContactsAddContact        name (two words), number
    SimpleCalendarAddOneEvent year, month, day, hour, duration_mins,
                              event_title, event_description
    MarkorCreateNote          file_name (with extension), text
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from . import android_env
from .accounting_check import call_record, sum_usage

COMPILE_SYSTEM_PROMPT = (
    "You compile a recorded GUI automation into a reusable, parameterized "
    "python program. You reply with python source code only."
)

DOC_SYSTEM_PROMPT = (
    "You compile recorded GUI automations into a reusable, parameterized "
    "operation document that another agent will read while driving the app. "
    "You reply with the document text only, never with code."
)

# The two artifacts the builder can emit from the SAME input: an executable
# parameterized program, and a plain-text operation document. Both are
# charged as C; only the doc arm pays a per-use prompt cost at deploy time.
ARTIFACTS = ("code", "doc")


def _system_prompt(artifact: str) -> str:
    if artifact == "code":
        return COMPILE_SYSTEM_PROMPT
    if artifact == "doc":
        return DOC_SYSTEM_PROMPT
    raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")

# ------------------------------------------------------------------ families


FAMILY_BINDINGS: dict[str, dict] = {
    "ContactsAddContact": {
        "app": "Google Contacts",
        "fields": ("name", "number"),
        "int_fields": (),
        "descriptions": {
            "name": "full contact name, exactly two words (first and last)",
            "number": "phone number, one string copied verbatim",
        },
        "note": (
            "The create-contact form takes the name in TWO separate fields:\n"
            "the first word of `name` goes into the 'First name' field, the\n"
            "second word into the 'Last name' field. Type `number` into the\n"
            "Phone field and leave the phone label at its default."
        ),
    },
    "SimpleCalendarAddOneEvent": {
        "app": "Simple Calendar Pro",
        "fields": (
            "year", "month", "day", "hour", "duration_mins",
            "event_title", "event_description",
        ),
        "int_fields": ("year", "month", "day", "hour", "duration_mins"),
        "descriptions": {
            "year": "event year, int",
            "month": "event month, int 1-12",
            "day": "event day of month, int",
            "hour": "event start hour, int 0-23 (24-hour clock)",
            "duration_mins": "event length in minutes, int",
            "event_title": "event title string",
            "event_description": "event description string",
        },
        "note": (
            "There is no duration field: set the event's END time to the\n"
            "start time plus `duration_mins` minutes. The new-event screen's\n"
            "text fields come first (Title, then Description); the start\n"
            "date row and the start/end time rows follow. Date and time are\n"
            "edited in picker dialogs that must each be confirmed with OK.\n"
            "The device clock is frozen at October 2023 and is 24-hour."
        ),
    },
    "MarkorCreateNote": {
        "app": "Markor",
        "fields": ("file_name", "text"),
        "int_fields": (),
        "descriptions": {
            "file_name": "note file name including its extension, e.g. note.txt",
            "text": "the note's text content",
        },
        "note": (
            "The create dialog takes the file name in TWO separate fields:\n"
            "`file_name` without its extension goes into the Name field\n"
            "(replace the pre-filled placeholder), the extension (the part\n"
            "after the last dot, WITH the dot) into the small extension\n"
            "field; confirm with OK. The note is saved by leaving the editor\n"
            "(navigate_back)."
        ),
    },
    "MarkorDeleteNote": {
        "app": "Markor",
        "fields": ("file_name",),
        "int_fields": (),
        "descriptions": {
            "file_name": "note file name including its extension, e.g. note.txt",
        },
        "note": (
            "The file list holds several OTHER notes besides the target;\n"
            "match the row by its exact title (`file_name`) and never the\n"
            "row position, and leave every other note alone. Deletion works\n"
            "on a selection: long-press the target row, then use the delete\n"
            "icon the selection top bar shows, then confirm the dialog.\n"
            "The target row may need a scroll to become visible."
        ),
    },
    "OsmAndFavorite": {
        "app": "OsmAnd",
        "fields": ("location",),
        "int_fields": (),
        "descriptions": {
            "location": (
                "place to save, one string: either a place name such as "
                "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair"
            ),
        },
        "note": (
            "`location` is typed VERBATIM into the app's search field: the\n"
            "generator draws either a place name or a 'lat, lon' pair, and\n"
            "the same field takes both, so never reformat or round it. Pick\n"
            "the first search result, then use the FAVORITES control on the\n"
            "place panel (the star), not the marker control next to it --\n"
            "the two write to different stores and only favorites.gpx is\n"
            "read by this family's oracle. Accept the name the app proposes\n"
            "in the dialog that follows."
        ),
    },
    "OsmAndMarker": {
        "app": "OsmAnd",
        "fields": ("location",),
        "int_fields": (),
        "descriptions": {
            "location": (
                "place to mark, one string: either a place name such as "
                "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair"
            ),
        },
        "note": (
            "`location` is typed VERBATIM into the app's search field: the\n"
            "generator draws either a place name or a 'lat, lon' pair, and\n"
            "the same field takes both, so never reformat or round it. Pick\n"
            "the first search result, then use the MARKER control on the\n"
            "place panel, not the favorites star next to it -- the two write\n"
            "to different stores and only the map_markers table is read by\n"
            "this family's oracle. The marker is stored on the tap; no\n"
            "confirmation dialog follows."
        ),
    },
    "FilesMoveFile": {
        "app": "Files",
        "fields": ("file_name", "source_folder", "destination_folder"),
        "int_fields": (),
        "descriptions": {
            "file_name": "file to move, name with extension, e.g. note.mp3",
            "source_folder": "folder the file starts in, e.g. Download",
            "destination_folder": "folder the file must end up in, e.g. DCIM",
        },
        "note": (
            "Both folders are top-level folders of the sdk_gphone_x86_64\n"
            "storage area, which is NOT the app's first screen: it is\n"
            "reached from the drawer behind the hamburger button. The move\n"
            "is cut-and-paste, in two halves: long-press the target row in\n"
            "the source folder, Cut from the selection bar's overflow menu,\n"
            "then open the destination folder and Paste. Nothing on screen\n"
            "marks the clipboard between the two halves. The oracle checks\n"
            "both ends, so a copy that leaves the source file in place\n"
            "fails. The source folder holds other files; match the row by\n"
            "its exact name."
        ),
    },
}


def binding_fields(family: str) -> tuple[str, ...]:
    return FAMILY_BINDINGS[family]["fields"]


def params_to_binding(family: str, params: dict) -> dict:
    """The program-facing binding: only the family's binding fields."""
    return {field: params[field] for field in binding_fields(family)}


def binding_to_params(family: str, binding: dict) -> dict:
    """The task-evaluator params for a binding.

    Identity for the families whose oracle reads only the binding fields.
    Three exceptions: the calendar, whose oracle judges a full CalendarEvent
    row built from the fields; MarkorDeleteNote, whose task setup needs the
    ``noise_candidates`` pool it fills the notebook with (the binding names
    only the file to delete); and FilesMoveFile, whose setup fills the SOURCE
    folder with the noise files android_world lists for that folder.
    """
    if family == "MarkorDeleteNote":
        from android_world.task_evals.single import markor as _markor

        return {
            "file_name": str(binding["file_name"]),
            "noise_candidates": list(_markor._NOTE_TITLES),
        }
    if family == "FilesMoveFile":
        from android_world.task_evals.utils import user_data_generation

        source = str(binding["source_folder"])
        return {
            "file_name": str(binding["file_name"]),
            "source_folder": source,
            "destination_folder": str(binding["destination_folder"]),
            "noise_candidates": list(
                user_data_generation.EMULATOR_DIRECTORIES.get(source, [])
            ),
        }
    if family == "SimpleCalendarAddOneEvent":
        import datetime

        from android_world.task_evals.utils import sqlite_schema_utils

        year, month, day = int(binding["year"]), int(binding["month"]), int(binding["day"])
        hour, duration_mins = int(binding["hour"]), int(binding["duration_mins"])
        start_ts = int(
            datetime.datetime(year, month, day, hour, tzinfo=datetime.timezone.utc).timestamp()
        )
        row = sqlite_schema_utils.CalendarEvent(
            start_ts=start_ts,
            end_ts=start_ts + duration_mins * 60,
            title=str(binding["event_title"]),
            description=str(binding["event_description"]),
        )
        return {
            "year": year, "month": month, "day": day, "hour": hour,
            "duration_mins": duration_mins,
            "event_title": str(binding["event_title"]),
            "event_description": str(binding["event_description"]),
            "row_objects": [row],
        }
    return {field: binding[field] for field in binding_fields(family)}


# ------------------------------------------------------------------ trajectory


def load_trajectory(trajectory_jsonl: Path | str, family: str | None = None) -> tuple[list[dict], dict]:
    """Parse a runner.py trajectory into (steps, final_record).

    Each step dict carries the parsed action dict, the typed value mapped to
    its binding placeholder (``field`` + ``fragment``; e.g. the first token
    of ``name``), and the foreground activity observed after the action.
    """
    path = Path(trajectory_jsonl)
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{path}: no final record; not a runner trajectory")
    final = finals[-1]
    family = family or final["family"]
    params = android_env.instance_params(family, final["seed"])
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
    """The task instance the trajectory ran (seed-deterministic, as in env)."""
    return android_env.instance_params(family, final["seed"])


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def map_value(text: str, params: dict, family: str) -> tuple[str | None, tuple | None]:
    """Map a recorded typed value onto its binding placeholder.

    Returns (field, fragment) where fragment says how to rebuild the text
    from the binding value: ("whole",), ("token", i) for the i-th whitespace
    token, ("base",) / ("ext",) for the parts around the last dot. A value
    that matches nothing is a constant (field None).
    """
    fields = [f for f in binding_fields(family) if isinstance(params.get(f), str)]
    for field in fields:  # 1. exact copy of one binding value
        if text == params[field]:
            return field, ("whole",)
    for field in fields:  # 2. same phone number in a different format
        if _digits(text) and _digits(text) == _digits(params[field]):
            return field, ("whole",)
    for field in fields:  # 3. one whitespace token (first / last name)
        tokens = params[field].split()
        if len(tokens) > 1 and text in tokens:
            return field, ("token", tokens.index(text))
    for field in fields:  # 4. dotted base / extension (file names)
        value = params[field]
        if "." in value:
            base, ext = value.rsplit(".", 1)
            if ext and text == base:
                return field, ("base",)
            if ext and text == "." + ext:
                return field, ("ext",)
    return None, None


def placeholder_expr(field: str | None, fragment: tuple | None) -> str:
    """The python expression over ``binding`` that rebuilds the typed text."""
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


def goal_template_text(goal_text_str: str, family: str, params: dict) -> str:
    """The family goal template: each concrete value -> its {placeholder}."""
    text = goal_text_str
    for field in binding_fields(family):
        value = str(params.get(field))
        if value and value in text:
            text = text.replace(value, "{" + field + "}")
    return text


# -------------------------------------------------------------------- prompt


def _compact_action(action: dict, field: str | None, fragment: tuple | None) -> str:
    parts = [f'"{action_type}": {json.dumps(value)}' for action_type, value in action.items()]
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
    described = [f"{k}={anno[k]!r}" for k in ("text", "hint", "description") if anno.get(k)]
    if described:
        line += "  [" + ", ".join(described) + "]"
    if step["after_activity"]:
        line += f"  -> {step['after_activity']}"
    return line


# The code-artifact contract tail. Shared by the single-trajectory compile
# prompt, the k>1 builder prompt and the refinement prompt, so the three
# differ ONLY in the evidence they show, never in what they ask for.
_CODE_CONTRACT_LINES = [
    "Write ONE python function",
    "",
    "    def program(device, binding: dict) -> bool",
    "",
    "that completes the task for ANY binding by driving the phone's UI",
    "through the given ``device`` object, plus a module-level PARAMS_SCHEMA",
    "dict describing each binding key. The device API (every action",
    "settles ~2 s before returning):",
    "  device.elements()  -> fresh list of dicts {index, text, hint,",
    "                        description, clickable, editable, scrollable, ...}",
    "  device.find(text=..., contains=..., hint=..., description=...,",
    "               clickable=..., editable=...) -> element index or None",
    "  device.click(index=None, **find)   # index OR find-criteria",
    "  device.input_text(text, index=None, **find)",
    "  device.scroll(direction='down'|'up'|'left'|'right')",
    "  device.open_app(app_name) ; device.navigate_back() ; device.navigate_home()",
    "  device.keyboard_enter() ; device.wait() ; device.settle(seconds)",
    "  device.execute({...})  # raw android_world JSONAction dict",
    "  device.adb_shell(*args) -> stdout  # raw adb, e.g. uiautomator dump",
    "Rules:",
    "- Use only ``device`` and the standard library.",
    "- Never hard-code any binding value; read them from ``binding``.",
    "- Re-locate elements per screen (device.find / device.elements);",
    "  indexes from another screen are stale and fail.",
    "- Drive the app's touchscreen UI only, never content providers,",
    "  sqlite, or the app's files by other means.",
    "- Raise on failure; return True once the flow has completed.",
    "Reply with ONLY the python source (one ```python block or raw code),",
    "no explanation.",
]

# The doc-artifact contract tail: the same evidence, but the artifact asked
# for is prose an agent reads, not code a runtime executes.
_DOC_CONTRACT_LINES = [
    "Write ONE plain-text OPERATION DOCUMENT that tells an agent operating",
    "this phone how to carry out any instance of this task type. The agent",
    "reading it sees the phone's screens and the numbered element list; it",
    "does NOT see the trajectories above.",
    "Requirements:",
    "- Refer to the task's values only through the {placeholders} of the",
    "  family goal template; never write a concrete value from a recorded",
    "  instance.",
    "- Name the screens in the order they are met, the control that leads",
    "  from each screen to the next, and the field each value goes into.",
    "- State the app-specific traps the recorded trajectories reveal (a",
    "  value split across two fields, a dialog that must be confirmed, a",
    "  pre-filled field that must be cleared, a list row that must be",
    "  matched by its title).",
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
    """The family block every builder prompt opens with: goal template,
    app, the binding contract, and the family's app-specific knowledge."""
    spec = FAMILY_BINDINGS[family]
    keys = ", ".join(spec["fields"])
    lines = [
        "FAMILY GOAL TEMPLATE (the {placeholders} are the binding's values):",
        template.strip(),
        "",
        f"APP: {spec['app']} on Android (a11y element indexes address the UI).",
        "The program must work for ANY binding dict with exactly the keys",
        f"{keys}:",
    ]
    lines += [f"  {field}: {desc}" for field, desc in spec["descriptions"].items()]
    lines += [
        "",
        f"App-specific knowledge: {spec['note']}",
        "",
    ]
    return lines


_TRAJECTORY_LEGEND = [
    "in the a11y element list of the screen the agent saw BEFORE acting;",
    "[text=... hint=...] describes that element; the activity after '->'",
    "is the foreground app once the action has settled; typed values are",
    "shown as the binding expression that rebuilds them):",
]


def build_compile_prompt(
    trajectory_jsonl: Path | str,
    family: str | None = None,
    annotations: dict | None = None,
    goal_text_str: str | None = None,
) -> list[dict]:
    """Messages (OpenRouter chat format) for one compile call.

    ``annotations`` (optional) maps step number -> {"text", "hint",
    "description"} of the element the recorded index addressed, recovered by
    :func:`annotate_trajectory`'s live shadow replay.
    """
    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    params = trajectory_instance(family, final)
    if goal_text_str is None:
        goal_text_str = android_env.goal_text(android_env.get_task(family, final.get("condition", "discover"), final["seed"]))
    template = goal_template_text(goal_text_str, family, params)

    lines = _family_header_lines(family, template)
    lines += [
        "RECORDED TRAJECTORY of one instance (index = the element's position",
        *_TRAJECTORY_LEGEND,
    ]
    lines += ["  " + _compact_step_line(s, annotations) for s in steps]
    lines += [""]
    lines += _CODE_CONTRACT_LINES

    user = "\n".join(lines)
    return [
        {"role": "system", "content": COMPILE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ------------------------------------------------------- k-trajectory builder


def normalize_entry(entry) -> dict:
    """One building entry as {trajectory, annotations, translation, label}.

    Accepts a bare path (the common case) or a dict carrying the optional
    annotations (live shadow replay) and translation (translator.py output).
    """
    if isinstance(entry, (str, Path)):
        return {"trajectory": Path(entry), "annotations": None, "translation": None, "label": None}
    out = dict(entry)
    out["trajectory"] = Path(out["trajectory"])
    out.setdefault("annotations", None)
    out.setdefault("translation", None)
    out.setdefault("label", None)
    return out


def _translation_lines(translation: dict | None) -> list[str]:
    """The translator's per-action snippets, as prompt lines."""
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
    goal_texts: dict | None = None,
) -> list[dict]:
    """Messages for ONE builder call over k = 1, 2 or 3 building trajectories.

    Each entry contributes its concrete goal, its compact step view (the same
    rendering the single-trajectory path uses) and, when present, the
    translator's per-action snippets. The family header and the artifact
    contract are shared with the single-trajectory path, so k and the
    artifact are the only things that vary.
    """
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
        goal_text_str = (goal_texts or {}).get(final["seed"]) or android_env.goal_text(
            android_env.get_task(entry_family, final.get("condition", "discover"), final["seed"])
        )
        if template is None:
            family = entry_family
            template = goal_template_text(goal_text_str, entry_family, params)
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
    """The repair evidence: the executed program trace up to the breakpoint,
    then the reactive agent's actions that carried on from there."""
    lines = [
        "IT FAILED on a building instance that it had to handle.",
        f"FAILING INSTANCE: {json.dumps(hybrid.get('binding') or {})}",
        f"GOAL: {(hybrid.get('goal') or '').strip()}",
        f"FAILURE: {hybrid.get('failure') or 'the oracle rejected the end state'}",
        "",
        "EXECUTED PROGRAM TRACE up to the breakpoint (what the artifact",
        "actually did on the device):",
    ]
    trace = hybrid.get("program_trace") or []
    lines += [f"  {line}" for line in trace] or ["  (no action reached the device)"]
    lines += [
        "",
        "BREAKPOINT SCREEN (the element list at the moment it stopped):",
    ]
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
    lines += [""]
    lines += _hybrid_lines(hybrid)
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


# ------------------------------------------------------------------ annotation


def annotate_trajectory(
    trajectory_jsonl: Path | str,
    family: str | None = None,
    env=None,
    settle_s: float = 2.0,
) -> dict:
    """Shadow-replay the trajectory to recover each index's element hints.

    The trajectory records indexes but not the a11y lines behind them;
    replaying the recorded actions against the same instance (reset to the
    trajectory's own seed) lets us read every target element's text, hint
    and content description from the fresh dump -- the same resolution the
    replayed action itself uses. Returns {step number: {text, hint,
    description}}. Needs the live emulator; makes zero LLM calls.
    """
    import time as _time

    from android_world.env import json_action

    steps, final = load_trajectory(trajectory_jsonl, family)
    family = family or final["family"]
    own_env = None
    if env is None:
        own_env = env = android_env.AndroidWorldEnv()
    notes: dict[int, dict] = {}
    try:
        task = android_env.get_task(family, final.get("condition", "discover"), final["seed"])
        env.reset(task)
        for step in steps:
            action = step["action"]
            index = action.get("index")
            if index is not None:
                state = env.aw_env.get_state(wait_to_stabilize=False)
                if 0 <= index < len(state.ui_elements):
                    element = state.ui_elements[index]
                    notes[step["step"]] = {
                        "text": element.text or "",
                        "hint": element.hint_text or "",
                        "description": element.content_description or "",
                    }
            try:
                env.aw_env.execute_action(json_action.JSONAction(**action))
            except Exception as exc:  # noqa: BLE001 - replay drifted off the recorded screens
                # The recorded index no longer resolves (a dialog or a
                # different first-run state): later steps' screens are
                # meaningless, so keep the notes so far and stop replaying.
                notes["_replay_stopped_at"] = f"step {step['step']}: {type(exc).__name__}"
                break
            _time.sleep(settle_s)
    finally:
        if own_env is not None:
            own_env.close()
    return notes


# -------------------------------------------------------------------- LLM path


def extract_python_source(reply: str) -> str:
    """The python source from a model reply: ```python fences, ``` fences,
    or raw code."""
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

    from openai import OpenAI

    # The API key is read from the environment and never logged or printed.
    return OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=180.0,
        max_retries=2,
    )


def extract_document(reply: str) -> str:
    """The document text from a doc-artifact reply: fences stripped when the
    model wrapped it anyway, otherwise the reply as written."""
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
) -> dict:
    """One builder-family model call: prompt -> {artifact_text, usage, ...}.

    The shared body of the compile, k-trajectory build and refine calls.
    API-level failures (timeouts, 5xx, rate limits) are retried with linear
    backoff; a reply that parses is final. A code artifact is additionally
    syntax-checked here, so a syntactically broken reply is treated as a
    transport-shaped failure and retried.
    """
    from .agent import AndroidAgent

    if artifact not in ARTIFACTS:
        raise ValueError(f"unknown artifact {artifact!r}; expected one of {ARTIFACTS}")
    if client is None:
        client = _openai_client()
    response = None
    text = ""
    failures: list[str] = []
    attempt = 0
    # One record per attempt that actually reached the model. A reply that
    # came back and was then rejected (unparseable code) was still charged,
    # so it belongs on disk even though ``usage`` below reports the accepted
    # call only; a transport failure never reached the model and has none.
    calls_detail: list[dict] = []
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature
            )
            reply = response.choices[0].message.content or ""
            calls_detail.append(call_record(
                attempt, attempt, AndroidAgent._usage(response),
                stage="builder", accepted=False,
            ))
            if artifact == "code":
                text = extract_python_source(reply)
                compile(text, "<builder_artifact>", "exec")
            else:
                text = extract_document(reply)
            calls_detail[-1]["accepted"] = True
            break
        except Exception as exc:  # noqa: BLE001 - transport failure shapes are data
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            response = None
            if attempt == max_attempts:
                raise RuntimeError(
                    f"builder call failed after {max_attempts} attempts: " + " | ".join(failures)
                ) from exc
            time.sleep(retry_backoff_s * attempt)
    # Every attempt that reached the model is a real build cost, including a
    # reply that came back and was then rejected (docs/methods-v3.md 1.3).
    # ``calls_detail`` keeps the accepted-only figure recoverable.
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
) -> dict:
    """One refinement call in the verify-and-repair loop; charged as C."""
    messages = build_refine_prompt(
        family, artifact, current_artifact, hybrid, conclusions
    )
    result = call_builder(
        model, messages, artifact=artifact, client=client, temperature=temperature,
        max_attempts=max_attempts, retry_backoff_s=retry_backoff_s,
    )
    result["stage"] = "builder_refine"
    return result


def compile_trajectory(
    model: str,
    trajectory_jsonl: Path | str,
    family: str | None = None,
    annotations: dict | None = None,
    client=None,
    temperature: float = 0.0,
    max_attempts: int = 3,
    retry_backoff_s: float = 5.0,
) -> dict:
    """One compile call: trajectory -> {program_source, usage, cost_usd}.

    API-level failures (timeouts, 5xx, rate limits) are retried up to
    ``max_attempts`` times with linear backoff; each attempt's failure is
    recorded. Retries are for transport errors only: a reply that parses is
    final. The program source is additionally syntax-checked here so a
    syntactically broken reply is a transport-shaped failure, retried.
    """
    from .agent import AndroidAgent

    messages = build_compile_prompt(trajectory_jsonl, family, annotations)
    if client is None:
        client = _openai_client()
    response = None
    failures: list[str] = []
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=temperature
            )
            reply = response.choices[0].message.content or ""
            source = extract_python_source(reply)
            compile(source, f"<compile_{family or 'program'}>", "exec")
            break
        except Exception as exc:  # noqa: BLE001 - transport failure shapes are data
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            response = None
            if attempt == max_attempts:
                raise RuntimeError(
                    f"compile call failed after {max_attempts} attempts: " + " | ".join(failures)
                ) from exc
            time.sleep(retry_backoff_s * attempt)
    usage = AndroidAgent._usage(response)  # same accounting as the reactive agent
    return {
        "program_source": source,
        "usage": usage,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "model": model,
        "attempts": attempt,
        "api_failures": failures,
    }


# ----------------------------------------------------------------- mock path


# NOTE: filled with str.replace (NOT str.format), so the single braces in
# the body below are literal python; only the @-marked slots are templated.
_MOCK_TEMPLATE = '''"""Family program for @FAMILY@ (@APP@).

Compiled deterministically from the recorded reactive trajectory @TASK_ID@
by guiexp_android.compiler.MockCompiler: the recorded action sequence is
replayed with every typed value taken from the binding (whole value, a
whitespace token, or the base/extension around the last dot, exactly as
recorded). Element indexes are re-resolved at run time against a FRESH a11y
dump -- android_world's execute_action maps the index over the current
screen's element list, the same resolution the reactive harness used@HINT_NOTE@.
"""

from __future__ import annotations

# The binding contract: this family's parameter fields.
PARAMS_SCHEMA = {
@PARAMS_SCHEMA@
}

# One row per recorded step: (action_type, kwargs, field, fragment,
# literal). For input_text rows the typed text is _value(binding, field,
# fragment, literal); every other row replays verbatim.
_STEPS = [
@STEPS_LITERAL@
]


def _value(binding: dict, field, fragment, literal):
    """Rebuild the recorded text from the binding (or pass a constant)."""
    if field is None:
        return literal
    value = binding[field]
    kind = fragment[0]
    if kind == "whole":
        return value
    if kind == "token":
        return value.split()[fragment[1]]
    if kind == "base":
        return value.rsplit(".", 1)[0]
    if kind == "ext":
        return "." + value.rsplit(".", 1)[1]
    raise ValueError(f"unknown fragment {fragment!r}")


def program(device, binding: dict) -> bool:
    """Replay the recorded flow for ``binding``; the caller's oracle decides
    whether the resulting app state is correct."""
    missing = [
        key
        for key in PARAMS_SCHEMA
        if not str(binding.get(key, "")).strip()
    ]
    if missing:
        raise ValueError("binding is missing non-empty values for: " + ", ".join(missing))
    for action_type, kwargs, field, fragment, literal in _STEPS:
        kwargs = dict(kwargs)
        if action_type == "input_text":
            kwargs.pop("text", None)
            kwargs["text"] = _value(binding, field, fragment, literal)
        device.execute({"action_type": action_type, **kwargs})
    return True
'''


class MockCompiler:
    """Deterministic, offline stand-in for the compile LLM call.

    Parses the trajectory's action sequence and emits a replay program:
    every recorded typed value replaced by its binding placeholder, element
    indexes re-resolved per screen. With ``annotations`` (from a live
    :func:`annotate_trajectory`) input steps additionally re-find their
    field by the recorded hint. Same return shape as :func:`compile_trajectory`.
    """

    model = "mock"

    def compile(
        self,
        trajectory_jsonl: Path | str,
        family: str | None = None,
        annotations: dict | None = None,
    ) -> dict:
        steps, final = load_trajectory(trajectory_jsonl, family)
        family = family or final["family"]
        spec = FAMILY_BINDINGS[family]

        rows: list[tuple] = []
        covered: set[str] = set()
        for step in steps:
            action = step["action"]
            action_type = action.get("action_type")
            kwargs = {k: v for k, v in action.items() if k not in ("action_type", "text")}
            field, fragment = step["field"], step["fragment"]
            literal = None if field else (action.get("text") if action_type == "input_text" else None)
            if field:
                covered.add(field)
            rows.append((action_type, kwargs, field, fragment, literal))

        params_schema = "\n".join(
            f'    "{field}": "{desc}",' for field, desc in spec["descriptions"].items()
        )
        steps_literal = "\n".join(f"    {repr(row)}," for row in rows)
        hint_note = (
            "; input fields are re-found by their recorded text hints first"
            if annotations
            else ""
        )
        source = (
            _MOCK_TEMPLATE
            .replace("@FAMILY@", family)
            .replace("@APP@", spec["app"])
            .replace("@TASK_ID@", str(final.get("task_id", "?")))
            .replace("@HINT_NOTE@", hint_note)
            .replace("@PARAMS_SCHEMA@", params_schema)
            .replace("@STEPS_LITERAL@", steps_literal)
        )
        compile(source, f"<mock_program_{final.get('task_id', 'x')}>", "exec")  # syntax gate

        from .conditions import approx_tokens

        prompt = build_compile_prompt(trajectory_jsonl, family, annotations)
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
            "covered_fields": sorted(covered),
        }


# ----------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trajectory", required=True, help="trajectory.jsonl of the source reactive run")
    parser.add_argument("--family", default=None, help="task family (default: the trajectory's)")
    parser.add_argument("--model", default=None, help="OpenRouter model id (omit with --mock)")
    parser.add_argument("--mock", action="store_true", help="deterministic offline MockCompiler")
    parser.add_argument("--annotate", action="store_true",
                        help="shadow-replay the trajectory on the emulator to attach each index's text/hint")
    parser.add_argument("--out", default=None, help="output dir (default: experimental-results/guiexp_android/compile_<model>_<family>)")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    args = parser.parse_args()

    trajectory = Path(args.trajectory)
    _steps, final = load_trajectory(trajectory, args.family)
    family = args.family or final["family"]
    model = args.model or ("mock" if args.mock else "openai/gpt-4o-mini")

    annotations = None
    env = None
    try:
        if args.annotate:  # element text/hint recovery needs the live device
            env = android_env.AndroidWorldEnv()
            annotations = annotate_trajectory(trajectory, family, env=env)
        if args.mock:
            result = MockCompiler().compile(trajectory, family, annotations)
        else:
            result = compile_trajectory(model, trajectory, family, annotations)
    finally:
        if env is not None:
            env.close()
            if not args.keep_emulator:
                env.stop_emulator()

    out_dir = Path(args.out) if args.out else (
        android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
        / f"compile_{model.replace('/', '-')}_{family}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    program_path = out_dir / "family_program.py"
    program_path.write_text(result["program_source"])
    record = {
        "model": result["model"],
        "trajectory": str(trajectory),
        "family": family,
        "annotated": annotations is not None,
        "annotations": annotations,
        "usage": result["usage"],
        "cost_usd": result["cost_usd"],
        "program_path": str(program_path),
        "record_type": "compile",
    }
    (out_dir / "compile.json").write_text(json.dumps(record, indent=1))
    usage = result["usage"]
    print(
        f"compiled {result['program_source'].count(chr(10))} lines, "
        f"{(usage.get('prompt_tokens') or 0) + (usage.get('completion_tokens') or 0)} tokens, "
        f"cost {result['cost_usd']}"
    )
    print(f"program: {program_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
