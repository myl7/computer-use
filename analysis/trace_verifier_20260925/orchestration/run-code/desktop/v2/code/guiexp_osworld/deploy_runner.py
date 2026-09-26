"""Deployment: N natural-language uses through the full chain.

Ported from guiexp_android/deploy_runner.py. Per use: ONE LLM extraction call
(natural-language goal -> the family's binding JSON), a type check with ONE
bounded retry, the compiled program on the guest, and the family checker
(judged with the GROUND-TRUTH params, never the extracted binding). The
extraction call(s)' priced tokens are the per-use cost d; everything after
extraction is code and costs zero tokens.
"""

from __future__ import annotations

import json
import random
import re
import time

from . import families
from .accounting import call_record
from .program_runtime import ProgramRunner

# ----------------------------------------------------------------- use pools

_CALC_DEPLOY_HEADERS = (
    "Workflow", "Milestone", "Objective", "Priority", "Backlog Item",
    "Roadmap", "Milestone Task", "Objective Key", "Priority Queue",
    "Backlog Row", "Roadmap Entry", "Objective Area", "Priority Tier",
    "Workflow Stage", "Milestone Log", "Objective Set", "Priority Field",
    "Backlog Line", "Roadmap Phase", "Objective List", "Priority Class",
    "Workflow Batch", "Milestone Note", "Objective Cell", "Priority Group",
    "Backlog Sheet", "Roadmap Track", "Objective Block", "Priority Label",
    "Workflow Entry",
)
_CALC_DEPLOY_BASES = (
    "workflow_state", "milestone_track", "objective_rows", "priority_board",
    "backlog_grid", "roadmap_sheet", "milestone_log", "objective_table",
    "priority_matrix", "backlog_view", "roadmap_grid", "objective_sheet",
    "priority_list", "workflow_log", "milestone_grid", "objective_export",
    "backlog_draft", "roadmap_draft", "priority_summary", "workflow_batch",
    "milestone_rows", "objective_cells", "backlog_state", "roadmap_rows",
    "priority_cells", "workflow_cells", "milestone_cells", "objective_grid",
    "backlog_matrix", "roadmap_matrix",
)

_WRITER_DEPLOY_TITLES = (
    "Safety Briefing Notes", "Orientation Packet Draft", "Field Report Notes",
    "Shift Handover Log", "Purchase Order Draft", "Change Request Notes",
    "Service Level Draft", "Access Review Notes", "Migration Plan Memo",
    "Escalation Path Draft", "Contingency Plan Notes", "Asset Register Memo",
    "Timesheet Policy Draft", "Meeting Room Rules", "Mailroom Notice Draft",
    "Parking Permit Notes", "Badge Renewal Memo", "Desk Booking Rules",
    "Visitor Log Draft", "Printer Setup Notes", "Coffee Fund Memo",
    "Plant Care Rota Notes", "Stationery Order Draft", "Archive Box List",
    "Shredding Day Notice", "Fire Drill Notes", "First Aid Kit Draft",
    "Lost Property Memo", "Recycling Guide Notes", "Keys Register Draft",
)
_WRITER_DEPLOY_BODIES = (
    "The checklist has been shortened to fit on a single page.",
    "Every item now carries the name of its current owner.",
    "The deadlines move forward by two working days from next month.",
    "Please confirm receipt of the updated instructions by email.",
    "The cover page now lists all three supporting departments.",
    "New requests must arrive before noon to be processed the same day.",
    "The summary table replaces the older bullet list entirely.",
    "Sign-off moves to the team lead once the review is complete.",
    "Copies go to the floor manager and the compliance inbox.",
    "The revised wording removes any mention of the old vendor.",
    "Two signatures are now required for amounts above the threshold.",
    "The schedule in the appendix reflects the winter opening hours.",
    "Labels on the shelves must match the codes in this document.",
    "The contact block at the top has been refreshed this week.",
    "Any exception needs a short note in the remarks column.",
    "The storage room is inventoried on the last Friday each month.",
    "Forms printed from the portal supersede the attached templates.",
    "The afternoon slot is reserved for walk-in questions only.",
    "Updates to this memo are announced in the weekly digest.",
    "Please keep the second page blank for reviewer comments.",
    "The new template drops the legacy header and footer lines.",
    "Handovers require a printed copy signed by both parties.",
    "The appendix lists the rooms in building order, not alphabetical.",
    "Requests older than thirty days are closed automatically.",
    "The tracker referenced below is maintained by the platform team.",
    "Sample entries at the end show the expected level of detail.",
    "The final paragraph summarises the changes made in this revision.",
    "Retention for these records is five years from creation.",
    "Colour printing is available only for the customer-facing copy.",
    "The instruction set replaces all earlier memos on this topic.",
)
_WRITER_DEPLOY_BASES = (
    "safety_notes", "orientation_draft", "field_report", "handover_log",
    "purchase_draft", "change_request", "service_draft", "access_review",
    "migration_memo", "escalation_draft", "contingency_notes", "asset_memo",
    "timesheet_draft", "room_rules", "mailroom_draft", "parking_notes",
    "badge_memo", "desk_rules", "visitor_draft", "printer_notes",
    "coffee_memo", "plant_rota", "stationery_draft", "archive_list",
    "shredding_notice", "fire_notes", "firstaid_draft", "lost_memo",
    "recycling_notes", "keys_draft",
)

_CALC_GOALS = (
    "In LibreOffice Calc, create a new spreadsheet. Put the header "
    "'{header_a}' in cell A1 and the header '{header_b}' in B1. Under the "
    "headers enter the numbers {val_a2} in A2, {val_b2} in B2, {val_a3} in "
    "A3 and {val_b3} in B3. Save the spreadsheet as {file_name} on the "
    "Desktop.",
    "Open a new spreadsheet in LibreOffice Calc. Cell A1 must read "
    "'{header_a}' and B1 '{header_b}'. Fill A2 with {val_a2}, B2 with "
    "{val_b2}, A3 with {val_a3} and B3 with {val_b3}. Then save it to the "
    "Desktop under the name {file_name}.",
    "Please set up a small table in a new LibreOffice Calc spreadsheet: "
    "headers '{header_a}' (A1) and '{header_b}' (B1), then {val_a2} and "
    "{val_b2} on the second row, {val_a3} and {val_b3} on the third. Save "
    "the file on the Desktop as {file_name}.",
    "Create a fresh spreadsheet in LibreOffice Calc with two labelled "
    "columns, '{header_a}' and '{header_b}' in A1 and B1, and the values "
    "{val_a2}, {val_b2} in row 2 and {val_a3}, {val_b3} in row 3. The "
    "spreadsheet must be saved on the Desktop as {file_name}.",
    "New Calc spreadsheet: A1='{header_a}', B1='{header_b}', A2={val_a2}, "
    "B2={val_b2}, A3={val_a3}, B3={val_b3}. Save it as {file_name} on the "
    "Desktop.",
)

_WRITER_GOALS = (
    "In LibreOffice Writer, create a new document. Its first line must be "
    "the title '{title}'. Leave the second line empty and put the sentence "
    "'{body}' on the third line. Save the document as {file_name} on the "
    "Desktop.",
    "Open a new document in LibreOffice Writer. Start with the title "
    "'{title}' on the first line, keep the second line blank, and write "
    "'{body}' on the third line. Then save it to the Desktop under the name "
    "{file_name}.",
    "Please draft a short memo in a new LibreOffice Writer document: the "
    "title '{title}' on line one, an empty line, then the sentence '{body}'. "
    "Save the finished document on the Desktop as {file_name}.",
    "Create a fresh document in LibreOffice Writer with three lines: "
    "'{title}', an empty line, and '{body}'. The document must be saved on "
    "the Desktop as {file_name}.",
    "New Writer document: first line '{title}', second line left empty, "
    "third line '{body}'. Save it as {file_name} on the Desktop.",
)


def _calc_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    headers = list(_CALC_DEPLOY_HEADERS)
    bases = list(_CALC_DEPLOY_BASES)
    rng.shuffle(headers)
    rng.shuffle(bases)
    uses = []
    for i in range(n):
        binding = {
            "file_name": f"{bases[i % len(bases)]}{i if i >= len(bases) else ''}.ods",
            "header_a": headers[i % len(headers)],
            "header_b": headers[(i * 7 + 3) % len(headers)],
            "val_a2": 120 + (i * 173) % 9_500,
            "val_b2": 120 + (i * 311) % 9_500,
            "val_a3": 120 + (i * 457) % 9_500,
            "val_b3": 120 + (i * 601) % 9_500,
        }
        goal = _CALC_GOALS[i % len(_CALC_GOALS)].format(**binding)
        uses.append((goal, binding))
    return uses


def _writer_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    titles = list(_WRITER_DEPLOY_TITLES)
    bodies = list(_WRITER_DEPLOY_BODIES)
    bases = list(_WRITER_DEPLOY_BASES)
    rng.shuffle(titles)
    rng.shuffle(bodies)
    rng.shuffle(bases)
    uses = []
    for i in range(n):
        binding = {
            "file_name": f"{bases[i % len(bases)]}{i if i >= len(bases) else ''}.odt",
            "title": titles[i % len(titles)],
            "body": bodies[(i * 11 + 5) % len(bodies)],
        }
        goal = _WRITER_GOALS[i % len(_WRITER_GOALS)].format(**binding)
        uses.append((goal, binding))
    return uses


def deploy_uses(family: str, n: int, offset: int = 0) -> list[tuple[str, dict]]:
    builders = {"CalcTableSave": _calc_uses, "WriterMemoSave": _writer_uses}
    rng = random.Random(f"guiexp_osworld:deploy:{family}")
    return builders[family](rng, n + offset)[offset:]


# ------------------------------------------------------------------- prompts

EXTRACTION_SYSTEM = (
    "You extract structured parameters from a natural-language request. "
    "You reply with a JSON object only."
)

_FIELD_HINTS = {
    "CalcTableSave": (
        "- file_name is the spreadsheet's file name INCLUDING its .ods\n"
        "  extension (e.g. report.ods), one string.\n"
        "- header_a and header_b are the two column headers, strings copied\n"
        "  verbatim, without their surrounding quotes.\n"
        "- val_a2, val_b2, val_a3, val_b3 are INTEGERS (the numbers of the\n"
        "  second and third rows; a2/b2 = row 2, a3/b3 = row 3).\n"
    ),
    "WriterMemoSave": (
        "- file_name is the document's file name INCLUDING its .odt\n"
        "  extension (e.g. memo.odt), one string.\n"
        "- title is the title line, one string copied verbatim, without its\n"
        "  surrounding quotes.\n"
        "- body is the body sentence for the third line, copied verbatim.\n"
    ),
}


def _extraction_body(family: str) -> str:
    fields = families.binding_fields(family)
    keys = ", ".join(fields)
    return f"""Extract the {len(fields)} task fields from the request below as a JSON
object with exactly the keys {keys}.

{(_FIELD_HINTS[family]).rstrip()}
{{extra}}
Request: {{goal}}

Reply with ONLY the JSON object. No markdown fences, no explanation."""


def build_extraction_prompt(goal_text: str, family: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _extraction_body(family).format(extra="", goal=goal_text)},
    ]


def build_retry_prompt(goal_text: str, family: str, errors: list[str]) -> list[dict]:
    extra = (
        "\nYour previous reply was rejected by the type check:\n"
        + "; ".join(errors)
        + "\nFix exactly these problems.\n"
    )
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _extraction_body(family).format(extra=extra, goal=goal_text)},
    ]


_INT_RANGES = {
    "val_a2": (0, 1_000_000),
    "val_b2": (0, 1_000_000),
    "val_a3": (0, 1_000_000),
    "val_b3": (0, 1_000_000),
}


def binding_type_errors(got, family: str) -> list[str]:
    """The deploy boundary: required keys, right types, family shape rules."""
    if not isinstance(got, dict):
        return ["reply is not a JSON object"]
    spec = families.FAMILY_BINDINGS[family]
    int_fields = set(spec["int_fields"])
    errs = []
    for field in spec["fields"]:
        if field not in got:
            errs.append(f"missing key {field}")
        elif field in int_fields:
            value = got[field]
            if not isinstance(value, int) or isinstance(value, bool):
                try:
                    value = int(str(value).strip())
                except (TypeError, ValueError):
                    errs.append(f"{field}: expected an integer, got {type(got[field]).__name__}")
                    continue
            low, high = _INT_RANGES.get(field, (None, None))
            if (low is not None and not low <= value <= high) or value != int(value):
                errs.append(f"{field}: {value!r} is out of range {low}-{high}")
        elif not isinstance(got[field], str):
            errs.append(f"{field}: expected a single string, got {type(got[field]).__name__}")
        elif not got[field].strip():
            errs.append(f"{field}: empty string")
    # family shape rules
    extension = ".ods" if family == "CalcTableSave" else ".odt"
    name = got.get("file_name") if isinstance(got, dict) else None
    if isinstance(name, str):
        name = name.strip()
        if not name.endswith(extension):
            errs.append(f"file_name: must end in {extension}")
        if "/" in name or name.startswith("."):
            errs.append("file_name: a bare file name, not a path")
    return errs


def normalize_binding(got: dict, family: str) -> dict:
    int_fields = set(families.FAMILY_BINDINGS[family]["int_fields"])
    out = {}
    for field in families.binding_fields(family):
        value = got[field]
        if field in int_fields and (not isinstance(value, int) or isinstance(value, bool)):
            value = int(str(value).strip())
        elif isinstance(value, str):
            value = value.strip()
        out[field] = value
    return out


def parse_binding_json(reply: str):
    text = (reply or "").strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.S)
    if match is not None:
        text = match.group(1).strip()
    try:
        got = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return got if isinstance(got, dict) else None


# ------------------------------------------------------------------- the chain


def run_single_use(program, family, goal, expected, expected_params, client,
                   model, runner: ProgramRunner) -> dict:
    """One deployment use: extract -> type check (one retry) -> program -> checker."""
    from .agent import OSWorldAgent

    t0 = time.time()
    tokens = 0
    cost = 0.0
    retries = 0
    error_type = None
    extracted = None
    calls_detail: list[dict] = []

    def call(messages: list[dict], kind: str) -> str:
        nonlocal tokens, cost
        response = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
        usage = OSWorldAgent._usage(response)
        tokens += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        cost += usage.get("cost_usd") or 0.0
        calls_detail.append(call_record(
            len(calls_detail) + 1, len(calls_detail) + 1, usage,
            stage="deploy_extract", kind=kind))
        return response.choices[0].message.content or ""

    got = parse_binding_json(call(build_extraction_prompt(goal, family), "extract"))
    errors = binding_type_errors(got, family)
    if errors:
        retries = 1
        got = parse_binding_json(call(build_retry_prompt(goal, family, errors), "retry"))
        errors = binding_type_errors(got, family)

    success = False
    if got is None:
        error_type = "extraction_json"
    elif errors:
        error_type = "type_check"
    else:
        extracted = normalize_binding(got, family)
        outcome = runner.run(program, extracted, family, judge_params=expected_params)
        success = outcome["passed"]
        if not success:
            error_type = "program_error" if outcome["error"] else "oracle_fail"

    return {
        "goal": goal,
        "expected": expected,
        "extracted": extracted,
        "success": success,
        "error_type": error_type,
        "retries": retries,
        "tokens": tokens,
        "cost_usd": round(cost, 8),
        "calls_detail": calls_detail,
        "wall_s": round(time.time() - t0, 2),
    }


def run_deployment(program, family, uses, client, model, runner: ProgramRunner,
                   role: str | None = None) -> dict:
    records = []
    for goal, expected in uses:
        expected_params = families.binding_to_params(family, expected)
        record = run_single_use(
            program, family, goal, expected, expected_params, client, model, runner)
        records.append(record)
        tag = record["error_type"] or ("OK" if record["success"] else "FAIL")
        print(f"  use {len(records):>2}: {record['tokens']:>5} tok (r{record['retries']}) {tag}",
              flush=True)
    tokens = [r["tokens"] for r in records]
    summary = {
        "model": model,
        "family": family,
        "n": len(records),
        "uses": records,
        "success_count": sum(1 for r in records if r["success"]),
        "success_rate": (sum(1 for r in records if r["success"]) / len(records)) if records else None,
        "d_tokens_mean": (sum(tokens) / len(tokens)) if tokens else None,
        "total_tokens": sum(tokens),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 8),
        "record_type": "deploy",
    }
    if role:
        summary["role"] = role
    return summary
