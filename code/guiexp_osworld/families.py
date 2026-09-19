"""The two OSWorld task families: templates, parameter generators, checkers.

Selection criteria (docs/measurement-experiments-plan-2026-09.md P1.1): one
LibreOffice Calc family and one LibreOffice Writer family, each with

  * a WRITE operation (create + type + save a document),
  * a parameter generator whose draws bind the goal template, seed-
    deterministic exactly like android_env.instance_params,
  * a programmatic checker that reads the saved file's CONTENT host-side
    (odfpy over the file pulled out of the guest) -- no gold-file download,
    because the generator already knows the ground truth.

Families:

  CalcTableSave   (Calc)  6 typed cells (2 header strings + 4 integers laid
                          out in a 2x2 block under them) + Save As to the
                          Desktop as .ods. Traps: cell navigation (Tab/Enter
                          semantics), the GTK save dialog (name field, Places
                          -> Desktop, confirm), format picker untouched.
  WriterMemoSave  (Writer) a 3-paragraph document (title / empty line / body
                          sentence) + Save As .odt to the Desktop. Traps: the
                          empty middle line, the same save dialog.

Checker details: values are compared after odfpy parsing -- cell text vs cell
number is normalised (a number typed into Calc is a float in the file), so a
checker pass means the document's content, not its pixel rendering, is right.
"""

from __future__ import annotations

import io
import random

# --------------------------------------------------------------------------- #
# Calc: headers + 2x2 integer block in A1:B3, saved as .ods on the Desktop
# --------------------------------------------------------------------------- #

_CALC_HEADERS = (
    "Region", "Product", "Quarter", "Channel", "Segment", "Division",
    "Category", "Branch", "District", "Unit", "Payment", "Shipment",
    "Supplier", "Customer", "Workgroup", "Cost Center", "Account", "Ledger",
    "Facility", "Location", "Currency", "Budget", "Payroll", "Inventory",
    "Forecast", "Backlog", "Pipeline", "Attendance", "Payable", "Receivable",
)
_CALC_BASES = (
    "region_totals", "product_summary", "quarter_review", "channel_breakdown",
    "segment_report", "division_output", "category_counts", "branch_ledger",
    "district_data", "unit_figures", "payment_records", "shipment_log",
    "supplier_table", "customer_list", "workgroup_grid", "cost_center_sheet",
    "account_export", "budget_draft", "payroll_extract", "inventory_check",
    "forecast_sheet", "backlog_view", "pipeline_state", "attendance_roll",
    "payable_status", "receivable_age", "ledger_slice", "facility_load",
    "location_counts", "currency_rates",
)

CALC_GOALS = (
    "In LibreOffice Calc, create a new spreadsheet. Put the header "
    "'{header_a}' in cell A1 and the header '{header_b}' in B1. Under the "
    "headers enter the numbers {val_a2} in A2, {val_b2} in B2, {val_a3} in A3 "
    "and {val_b3} in B3. Save the spreadsheet as {file_name} on the Desktop.",
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

# --------------------------------------------------------------------------- #
# Writer: title / empty line / body sentence, saved as .odt on the Desktop
# --------------------------------------------------------------------------- #

_WRITER_TITLES = (
    "Team Standup Notes", "Weekly Status Digest", "Project Kickoff Brief",
    "Client Visit Recap", "Quarterly Goals Draft", "Hiring Update Memo",
    "Budget Revision Notes", "Incident Response Log", "Release Checklist",
    "Office Move Plan", "Training Session Summary", "Vendor Review Notes",
    "Sprint Retro Outline", "Policy Change Notice", "Facilities Request",
    "Onboarding Checklist", "Audit Preparation List", "Meeting Agenda Draft",
    "Travel Itinerary Sketch", "Research Reading List", "Maintenance Window",
    "Customer Feedback Summary", "Partnership Talking Points",
    "Inventory Recount Plan", "Recruiting Pipeline Notes",
    "Compliance Review Draft", "Board Meeting Prep", "Team Offsite Ideas",
    "Product Demo Script", "Support Rotation Schedule",
)
_WRITER_BODIES = (
    "The team agreed to move the deadline to the end of the month.",
    "All action items from the last session were closed on schedule.",
    "The vendor confirmed delivery of the remaining parts this week.",
    "We will revisit the budget figures once the audit is complete.",
    "The new hire starts on Monday and joins the platform group.",
    "Customer feedback on the pilot was positive across every region.",
    "The office will be closed for maintenance on the first Friday.",
    "Testing finished ahead of plan and no blockers were reported.",
    "The design review is scheduled for Thursday afternoon in room four.",
    "Please send your slides to the shared drive before the deadline.",
    "The migration ran overnight and every record carried over cleanly.",
    "We are holding two more interviews before making the final decision.",
    "The supplier lowered the unit price by five percent for next quarter.",
    "Attendance was high and the recording is available on the portal.",
    "Training materials were updated to match the revised process.",
    "The outage was traced to a misconfigured router in the branch office.",
    "Sales exceeded the forecast in every region except the northwest.",
    "The workshop will run twice to cover both product lines.",
    "Facilities confirmed the new desks arrive within two weeks.",
    "Legal signed off on the contract with two minor wording changes.",
    "The prototype demo convinced the steering committee to proceed.",
    "Queue times dropped by half after the staffing change last week.",
    "All findings from the review were assigned an owner and a date.",
    "The archive migration preserved folder permissions without changes.",
    "We will pilot the new checklist with one team before rolling it out.",
    "The feedback survey closes on Friday and results go out next week.",
    "Budget holders approved the additional headcount for the support team.",
    "The documentation refresh covers all endpoints released this year.",
    "Security scanning found no critical issues in the current build.",
    "The team voted to keep the current cadence for the next quarter.",
)
_WRITER_BASES = (
    "standup_notes", "status_digest", "kickoff_brief", "visit_recap",
    "goals_draft", "hiring_memo", "budget_notes", "incident_log",
    "release_checklist", "move_plan", "training_summary", "vendor_notes",
    "retro_outline", "policy_notice", "facilities_request",
    "onboarding_list", "audit_prep", "agenda_draft", "travel_sketch",
    "reading_list", "maintenance_window", "feedback_summary",
    "partner_points", "recount_plan", "pipeline_notes", "compliance_draft",
    "board_prep", "offsite_ideas", "demo_script", "rotation_schedule",
)

WRITER_GOALS = (
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


FAMILY_BINDINGS: dict[str, dict] = {
    "CalcTableSave": {
        "app": "LibreOffice Calc",
        "fields": ("file_name", "header_a", "header_b",
                   "val_a2", "val_b2", "val_a3", "val_b3"),
        "int_fields": ("val_a2", "val_b2", "val_a3", "val_b3"),
        "descriptions": {
            "file_name": "spreadsheet file name ending in .ods, one string",
            "header_a": "column A header text, one string",
            "header_b": "column B header text, one string",
            "val_a2": "integer for cell A2",
            "val_b2": "integer for cell B2",
            "val_a3": "integer for cell A3",
            "val_b3": "integer for cell B3",
        },
        "note": (
            "The numbers go UNDER the headers: A2 and B2 are the second\n"
            "row, A3 and B3 the third. Calc commits a typed cell and moves\n"
            "the cursor with Enter (down) or Tab (right). Save through the\n"
            "save dialog: the Name field takes {file_name} INCLUDING the\n"
            ".ods extension; the folder must be changed to Desktop (the\n"
            "dialog opens somewhere else) and the save confirmed. A file\n"
            "with this name never exists yet; if an overwrite prompt\n"
            "appears something already went wrong."
        ),
    },
    "WriterMemoSave": {
        "app": "LibreOffice Writer",
        "fields": ("file_name", "title", "body"),
        "int_fields": (),
        "descriptions": {
            "file_name": "document file name ending in .odt, one string",
            "title": "the title line, one string",
            "body": "the body sentence for the third line, one string",
        },
        "note": (
            "The document has exactly three lines: the title, one EMPTY\n"
            "line (a pressed Enter, not a space), then the body sentence.\n"
            "Save through the save dialog: the Name field takes "
            "{file_name} INCLUDING the .odt extension; the folder must be\n"
            "changed to Desktop and the save confirmed."
        ),
    },
}

FAMILIES = tuple(FAMILY_BINDINGS)


def binding_fields(family: str) -> tuple[str, ...]:
    return FAMILY_BINDINGS[family]["fields"]


def params_to_binding(family: str, params: dict) -> dict:
    return {field: params[field] for field in binding_fields(family)}


def binding_to_params(family: str, binding: dict) -> dict:
    """Identity on this arm: every family's checker reads only the binding."""
    return {field: binding[field] for field in binding_fields(family)}


# ------------------------------------------------------------------ generators


def _calc_params(rng: random.Random) -> dict:
    headers = rng.sample(_CALC_HEADERS, 2)
    base = rng.choice(_CALC_BASES)
    return {
        "file_name": f"{base}.ods",
        "header_a": headers[0],
        "header_b": headers[1],
        "val_a2": rng.randrange(105, 9_875),
        "val_b2": rng.randrange(105, 9_875),
        "val_a3": rng.randrange(105, 9_875),
        "val_b3": rng.randrange(105, 9_875),
    }


def _writer_params(rng: random.Random) -> dict:
    base = rng.choice(_WRITER_BASES)
    return {
        "file_name": f"{base}.odt",
        "title": rng.choice(_WRITER_TITLES),
        "body": rng.choice(_WRITER_BODIES),
    }


def instance_params(family: str, seed: int) -> dict:
    """Seed-deterministic draw, the android_env.instance_params convention:
    a seed maps to one instance on every machine and across conditions."""
    rng = random.Random(f"guiexp_osworld:instance:{family}:{seed}")
    return _calc_params(rng) if family == "CalcTableSave" else _writer_params(rng)


GOAL_TEMPLATES = {
    "CalcTableSave": CALC_GOALS,
    "WriterMemoSave": WRITER_GOALS,
}


def goal_text(family: str, seed: int, params: dict | None = None,
              variant: int | None = None) -> str:
    """The goal prompt for one instance.

    ``variant`` picks the phrasing; None draws it seed-deterministically so
    the SAME instance always gets the SAME phrasing (what a reactive run, its
    translator shadow replay and any repair all see)."""
    params = params or instance_params(family, seed)
    goals = GOAL_TEMPLATES[family]
    if variant is None:
        variant = random.Random(f"guiexp_osworld:phrasing:{family}:{seed}").randrange(len(goals))
    return goals[variant % len(goals)].format(**params)


def goal_template_text(family: str, goal: str, params: dict) -> str:
    """Concrete values -> {placeholders} (the compiler's family header)."""
    text = goal
    for field in binding_fields(family):
        value = str(params.get(field))
        if value and value in text:
            text = text.replace(value, "{" + field + "}")
    return text


# -------------------------------------------------------------------- checkers


def _cell_map(sheet) -> dict[str, str]:
    """A1-style -> text, walking rows/cells and honouring odf's repeat
    attributes (empty cells are stored as repeats, not as elements)."""
    from odf.table import TableCell, TableRow
    from odf import teletype
    from odf.text import P

    def repeats(element, attr) -> int:
        value = element.getAttribute(attr)
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    cells: dict[str, str] = {}
    row_index = 0
    for row in sheet.getElementsByType(TableRow):
        for _ in range(repeats(row, "numberrowsrepeated")):
            column_index = 0
            for cell in row.getElementsByType(TableCell):
                text = "".join(
                    teletype.extractText(p) or ""
                    for p in cell.getElementsByType(P)
                ).strip()
                for _ in range(repeats(cell, "numbercolumnsrepeated")):
                    cells[_a1(row_index, column_index)] = text
                    column_index += 1
            row_index += 1
    return cells


def _a1(row_index: int, column_index: int) -> str:
    letters = ""
    n = column_index
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            return f"{letters}{row_index + 1}"


def _cell_text(sheet, cell: str) -> str:
    value = sheet.getCell(cell).value if hasattr(sheet, "getCell") else None
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def check_calc(binding: dict, file_bytes: bytes) -> tuple[bool, str | None]:
    from odf.opendocument import load
    from odf.table import Table

    try:
        doc = load(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        return False, f"not a readable .ods: {type(exc).__name__}"
    tables = doc.spreadsheet.getElementsByType(Table)
    if not tables:
        return False, "no sheet in the file"
    cells = _cell_map(tables[0])
    expected = {
        "A1": str(binding["header_a"]).strip(),
        "B1": str(binding["header_b"]).strip(),
        "A2": str(int(binding["val_a2"])),
        "B2": str(int(binding["val_b2"])),
        "A3": str(int(binding["val_a3"])),
        "B3": str(int(binding["val_b3"])),
    }
    for cell, want in expected.items():
        got = cells.get(cell, "")
        if got != want:
            return False, f"{cell}: expected {want!r}, found {got!r}"
    return True, None


def _paragraph_texts(file_bytes: bytes) -> list[str]:
    from odf.opendocument import load
    from odf.text import P
    from odf import teletype

    doc = load(io.BytesIO(file_bytes))
    out = []
    for para in doc.text.getElementsByType(P):
        out.append(teletype.extractText(para))
    return out


def check_writer(binding: dict, file_bytes: bytes) -> tuple[bool, str | None]:
    try:
        paragraphs = _paragraph_texts(file_bytes)
    except Exception as exc:  # noqa: BLE001
        return False, f"not a readable .odt: {type(exc).__name__}"
    while paragraphs and paragraphs[-1] == "":  # trailing empties are inert
        paragraphs.pop()
    if len(paragraphs) < 3:
        return False, f"document has {len(paragraphs)} paragraph(s), need 3"
    if paragraphs[0] != binding["title"]:
        return False, f"line 1: expected {binding['title']!r}, found {paragraphs[0]!r}"
    if paragraphs[1] != "":
        return False, f"line 2: expected empty, found {paragraphs[1]!r}"
    if paragraphs[2] != binding["body"]:
        return False, f"line 3: expected {binding['body']!r}, found {paragraphs[2]!r}"
    return True, None


CHECKERS = {
    "CalcTableSave": check_calc,
    "WriterMemoSave": check_writer,
}

# Desktop paths inside the guest (single-user container, OSWorld layout).
GUEST_DESKTOP = "/home/user/Desktop"
APP_LAUNCH = {
    "CalcTableSave": ["libreoffice", "--calc"],
    "WriterMemoSave": ["libreoffice", "--writer"],
}

# AutoRPA's step cap = ceil(10 x complexity) capped at 50. OSWorld tasks carry
# no complexity attribute, so these two values are OUR assignment, by the same
# standard android_world uses (count of distinct UI manipulations on the way):
# Calc = 6 typed cells + save-dialog navigation; Writer = 3 lines + the same
# dialog. Recorded in the port log.
COMPLEXITY = {"CalcTableSave": 2.2, "WriterMemoSave": 1.8}
