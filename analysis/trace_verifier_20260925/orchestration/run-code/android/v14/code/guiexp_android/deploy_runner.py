"""Stage-2 deployment: N natural-language uses through the full chain.

The Android mirror of ``guiexp/deploy_runner.py``. Per use, the chain is
exactly ONE LLM extraction call (natural-language goal -> the family's
binding JSON), a type check with ONE bounded retry, the compiled program on
the real device, and the family's own oracle. The tokens of the extraction
call(s) are the per-use cost d; everything after extraction is code and
costs zero tokens.

    use = (goal text, ground-truth binding)   # judge is the oracle with the
                                              # ground-truth params, never
                                              # the extracted binding itself

Error types per use: "extraction_json" (reply not JSON), "type_check"
(still malformed after the one retry -- the boundary reject; a real episode
would fall back reactive), "program_error" (the program raised), or
"oracle_fail" (it ran but produced wrong state).

    ../.venv-android/bin/python -m guiexp_android.deploy_runner --mock \
        --program .../family_program.py --family ContactsAddContact --uses 10
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path
from types import SimpleNamespace

from . import android_env
from .accounting_check import call_record
from .compiler import FAMILY_BINDINGS, binding_fields, binding_to_params
from .mock_model import _approx_prompt_tokens
from .program_runtime import ProgramRunner, program_from_path

# ----------------------------------------------------------------- use pools
#
# The deployment uses: a natural-language request plus the binding it must
# yield. Deterministic (random.Random over a fixed vocab, no global state);
# values are drawn OUTSIDE android_world's generator pools so they stay
# disjoint from the instance seeds and the gate bindings.

_CONTACT_FIRSTS = (
    "Aaron", "Bethany", "Chloe", "Dorian", "Elise", "Farrah", "Gideon", "Hana",
    "Imogen", "Jasper", "Katya", "Lennon", "Marisol", "Nadia", "Orion", "Priya",
    "Quentin", "Rosalind", "Stefan", "Tamsin", "Ulric", "Vera", "Wendell",
    "Ximena", "Yusuf", "Zelda", "Anouk", "Bram", "Cordelia", "Dmitri",
)
_CONTACT_LASTS = (
    "Ashford", "Bellamy", "Carrington", "Delacroix", "Everhart", "Fairbanks",
    "Grayson", "Holloway", "Ingram", "Jessup", "Kingsley", "Larkspur",
    "Maverick", "Norwood", "Oakhurst", "Pemberton", "Quimby", "Rothbury",
    "Sinclair", "Thornbury", "Underhill", "Vandermeer", "Wexford", "Yardley",
    "Zabowski", "Aldergate", "Birchall", "Crestwood", "Dunmore", "Ellsworth",
)
_CONTACT_GOALS = (
    "Please add {name} to my contacts. Their number is {number}.",
    "Save a new contact: {name}, phone {number}.",
    "I need a contact entry for {name}; the number is {number}.",
    "Create a contact for {name}. Number: {number}.",
    "Add {name} to the Contacts app. You can reach them at {number}.",
    "New contact {name}, phone number {number}.",
    "Can you save {name} ({number}) as a contact?",
    "Contacts: add {name}, reachable at {number}.",
)

_CAL_DAYS = tuple(range(16, 29))          # device clock is frozen at 2023-10-15
_CAL_HOURS = tuple(range(8, 20))
_CAL_DURATIONS = (15, 30, 45, 60)
_CAL_TITLES = (
    "Sprint Planning", "Budget Review", "Design Critique", "Client Call",
    "Team Retro", "Roadmap Sync", "Hiring Loop", "Incident Review",
    "Product Demo", "Architecture Walkthrough", "Onboarding Session",
    "Quarterly Offsite", "Compliance Audit", "Vendor Meeting",
    "Research Sync", "Release Readiness", "Security Review", "Training Lab",
    "Workshop: Writing", "1:1 with Manager", "All-Hands Rehearsal",
    "Customer Workshop", "Data Review", "Escalation Call", "Pilot Kickoff",
    "Postmortem Meeting", "Recruiting Debrief", "Sales Pipeline", "Support Rotation", "UX Study",
)
_CAL_DESCRIPTIONS = (
    "We will go through the agenda together.", "Prepare notes beforehand.",
    "Bring the latest numbers to the table.", "Decide on next steps.",
    "Review the action items from last time.", "Keep it short and focused.",
    "Follow up with stakeholders after.", "Take notes and share them.",
    "Confirm the room booking beforehand.", "Everyone should attend.",
    "Please arrive five minutes early.", "Agenda will be shared in advance.",
    "We will cover the whole backlog.", "Draft the summary as we go.",
    "Timeboxed to the minute.", "Optional for remote folks.",
    "Mandatory for the core team.", "Recording will be posted.",
    "Catering is ordered.", "Divide the work at the end.",
    "Check the dashboard first.", "Escalate blockers immediately.",
    "Pre-read the design doc.", "Vote on the shortlist.",
    "Two presenters this time.", "Focus on the top three items.",
    "Verify the fixes landed.", "Interviewer debrief afterwards.",
    "Pipeline health check.", "Five participants scheduled.",
)
_CAL_GOALS = (
    "In Simple Calendar Pro, create an event on {year}-{month:02d}-{day:02d} at {hour}h "
    "with the title '{title}' and the description '{desc}'. The event should last for {dur} mins.",
    "Please add a calendar event: '{title}' on October {day}, {year}, starting at {hour}:00 "
    "and lasting {dur} minutes. Description: '{desc}'.",
    "I need an event in the calendar app: '{title}', {year}-10-{day} at {hour}h, "
    "{dur} minutes long. Add the description '{desc}'.",
    "Schedule '{title}' in Simple Calendar Pro for October {day}, {year} at {hour}h sharp, "
    "duration {dur} minutes, description '{desc}'.",
    "New calendar entry: title '{title}', date {year}-{month:02d}-{day:02d}, start {hour}h, "
    "lasts {dur} mins, described as '{desc}'.",
)

_MARKOR_BASES = (
    "call_notes", "standup_summary", "reading_list", "recipe_ideas",
    "workout_log", "trip_plan", "expense_draft", "meeting_minutes",
    "book_quotes", "interview_prep", "plant_care", "gift_ideas",
    "sleep_log", "water_intake", "podcast_notes", "language_drills",
    "code_snippets", "paint_shopping", "car_maintenance", "home_tasks",
    "study_plan", "movie_watchlist", "vendor_contacts", "thesis_outline",
    "garden_layout", "chores_rota", "budget_sketch", "training_notes",
    "repair_steps", "event_checklist",
)
_MARKOR_TEXTS = (
    "Check the pressure gauge before starting.",
    "Order the replacement filter online.",
    "Ask the dentist about the night guard.",
    "Water the fern twice a week only.",
    "Backup the laptop before the trip.",
    "Call the plumber about the kitchen tap.",
    "Renew the library membership by Friday.",
    "Compare prices across three shops.",
    "Print the boarding pass the night before.",
    "Charge the camera batteries beforehand.",
    "Return the borrowed ladder to David.",
    "Label the boxes by room and floor.",
    "Test the smoke alarm on the first.",
    "Reserve the table for six people.",
    "Bring the folding chairs from the garage.",
    "Copy the keys for the new tenant.",
    "Measure the shelf before buying bins.",
    "Sort the photos by year and month.",
    "Cancel the unused subscriptions.",
    "Refill the bird feeder on Sunday.",
    "Vacuum the car before the sale.",
    "Practice the toast twice out loud.",
    "Freeze the leftover soup tonight.",
    "Sync the playlists for offline use.",
    "Sharpen the kitchen knives properly.",
    "Register for the autumn workshop.",
    "Leave a review for the handyman.",
    "Pack the adapters in the carry-on.",
    "Write the thank-you notes by hand.",
    "Reset the router after the move.",
)
_MARKOR_GOALS = (
    "Create a new note in Markor named {file} with the following text: {text}",
    "Please add a Markor note called {file} containing: {text}",
    "New Markor note {file}; its content should be: {text}",
    "In Markor, write a note named {file} with this text: {text}",
    "Add a note {file} to Markor. The note text is: {text}",
)


def _contacts_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    firsts = list(_CONTACT_FIRSTS)
    lasts = list(_CONTACT_LASTS)
    rng.shuffle(firsts)
    rng.shuffle(lasts)
    uses = []
    for i in range(n):
        name = f"{firsts[i % len(firsts)]} {lasts[(i * 7 + 3) % len(lasts)]}"
        number = f"+1 {200 + (i * 13) % 700:03d} 555 {1000 + i * 37 % 9000:04d}"
        goal = _CONTACT_GOALS[i % len(_CONTACT_GOALS)].format(name=name, number=number)
        uses.append((goal, {"name": name, "number": number}))
    return uses


def _calendar_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    titles = list(_CAL_TITLES)
    descriptions = list(_CAL_DESCRIPTIONS)
    rng.shuffle(titles)
    rng.shuffle(descriptions)
    uses = []
    for i in range(n):
        binding = {
            "year": 2023,
            "month": 10,
            "day": _CAL_DAYS[(i * 5 + 2) % len(_CAL_DAYS)],
            "hour": _CAL_HOURS[(i * 3 + 1) % len(_CAL_HOURS)],
            "duration_mins": _CAL_DURATIONS[i % len(_CAL_DURATIONS)],
            "event_title": titles[i % len(titles)],
            "event_description": descriptions[(i * 11 + 5) % len(descriptions)],
        }
        goal = _CAL_GOALS[i % len(_CAL_GOALS)].format(
            year=binding["year"], month=binding["month"], day=binding["day"],
            hour=binding["hour"], dur=binding["duration_mins"],
            title=binding["event_title"], desc=binding["event_description"],
        )
        uses.append((goal, binding))
    return uses


def _markor_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    bases = list(_MARKOR_BASES)
    texts = list(_MARKOR_TEXTS)
    rng.shuffle(bases)
    rng.shuffle(texts)
    uses = []
    for i in range(n):
        file_name = f"{bases[i % len(bases)]}{i if i >= len(bases) else ''}.{'md' if i % 2 else 'txt'}"
        text = texts[(i * 3 + 1) % len(texts)]
        goal = _MARKOR_GOALS[i % len(_MARKOR_GOALS)].format(file=file_name, text=text)
        uses.append((goal, {"file_name": file_name, "text": text}))
    return uses


_MARKOR_DELETE_GOALS = (
    "Delete the note in Markor named {file}.",
    "Please remove the Markor note called {file}.",
    "In Markor, delete the note {file}.",
    "Get rid of the note named {file} in Markor.",
    "Remove {file} from Markor's notes.",
)


def _markor_delete_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    """Deletion uses reuse the create pool's name space; only the file name
    is a parameter, so the note text plays no role here."""
    bases = list(_MARKOR_BASES)
    rng.shuffle(bases)
    uses = []
    for i in range(n):
        file_name = f"{bases[i % len(bases)]}{i if i >= len(bases) else ''}.{'md' if i % 2 else 'txt'}"
        goal = _MARKOR_DELETE_GOALS[i % len(_MARKOR_DELETE_GOALS)].format(file=file_name)
        uses.append((goal, {"file_name": file_name}))
    return uses


_OSMAND_PLACES = (
    "Balzers, Liechtenstein", "Bendern, Liechtenstein", "Malbun, Liechtenstein",
    "Nendeln, Liechtenstein", "Oberplanken, Liechtenstein",
    "Planken, Liechtenstein", "Rotenboden, Liechtenstein",
    "Ruggell, Liechtenstein", "Schaan, Liechtenstein",
    "Schaanwald, Liechtenstein", "Triesen, Liechtenstein",
)
# The generator draws either a place name or a "lat, lon" pair, so the deploy
# pool must exercise both spellings; these are android_world's own coordinates.
_OSMAND_COORDS = (
    "47.0688832, 9.5061564", "47.2122151, 9.5062101", "47.1026191, 9.6083057",
    "47.1973857, 9.5430636", "47.1784977, 9.5450163", "47.1858882, 9.5452201",
    "47.1275785, 9.5387131", "47.23976, 9.5262837", "47.1663432, 9.5103085",
    "47.2165476, 9.5699984", "47.106997, 9.5274854",
)
_OSMAND_FAVORITE_GOALS = (
    "Add a favorite location marker for {place} in the OsmAnd maps app.",
    "In OsmAnd, save {place} as a favorite location.",
    "Please star {place} as a favorite in the OsmAnd maps app.",
    "Mark {place} as one of my favorites in OsmAnd.",
    "Add {place} to the favorites in the OsmAnd maps app.",
)
_OSMAND_MARKER_GOALS = (
    "Add a location marker for {place} in the OsmAnd maps app.",
    "In OsmAnd, drop a location marker on {place}.",
    "Please place a map marker at {place} in the OsmAnd maps app.",
    "Set a marker for {place} in OsmAnd.",
    "Put a location marker on {place} in the OsmAnd maps app.",
)


def _osmand_uses(rng: random.Random, n: int, goals: tuple) -> list[tuple[str, dict]]:
    """Places alternate between the name spelling and the coordinate
    spelling, because the family's own generator draws both."""
    order = list(range(len(_OSMAND_PLACES)))
    rng.shuffle(order)
    uses = []
    for i in range(n):
        j = order[i % len(order)]
        place = _OSMAND_PLACES[j] if i % 2 == 0 else _OSMAND_COORDS[j]
        uses.append((goals[i % len(goals)].format(place=place), {"location": place}))
    return uses


def _osmand_favorite_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    return _osmand_uses(rng, n, _OSMAND_FAVORITE_GOALS)


def _osmand_marker_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    return _osmand_uses(rng, n, _OSMAND_MARKER_GOALS)


# android_world's own top-level folders of the sdk_gphone_x86_64 storage area.
_FILES_FOLDERS = (
    "Alarms", "Audiobooks", "DCIM", "Documents", "Download", "Movies",
    "Music", "Notifications", "Pictures", "Podcasts", "Recordings",
    "Ringtones",
)
_FILES_NAMES = (
    "holiday_photos.jpg", "morning_alarm.mp3", "sci_fi_book.mp3",
    "trip_report.pdf", "lecture_capture.mp3", "family_video.mp4",
    "new_message.mp3", "beach_sunset.jpg", "podcast_ep12.mp3",
    "old_ringtone.mp3", "invoice_march.pdf", "memoir_audio.mp3",
    "wedding_clip.mp4", "garden_plan.jpg", "meeting_record.mp3",
    "tax_return.pdf", "birthday_party.jpg", "night_alarm.mp3",
    "history_lecture.mp3", "voice_memo.mp3", "roadmap_draft.pdf",
    "hiking_trail.jpg", "chapter_one.mp3", "screen_capture.mp4",
    "budget_sheet.pdf", "wake_up.mp3", "novel_chapter.mp3",
    "city_skyline.jpg", "daily_standup.mp3", "contract_v2.pdf",
)
_FILES_GOALS = (
    "Move the file {file} from {src} within the sdk_gphone_x86_64 storage area "
    "to the {dst} within the same sdk_gphone_x86_64 storage area in the Android "
    "filesystem.",
    "In the Files app, move {file} out of {src} and into {dst} on the "
    "sdk_gphone_x86_64 storage area.",
    "Please relocate the file {file} from the {src} folder to the {dst} folder "
    "in the Android filesystem.",
    "Take {file} from {src} and put it in {dst} using the Files app.",
    "Transfer the file {file} from the {src} folder into the {dst} folder of "
    "the sdk_gphone_x86_64 storage area.",
)


def _files_move_uses(rng: random.Random, n: int) -> list[tuple[str, dict]]:
    names = list(_FILES_NAMES)
    rng.shuffle(names)
    folders = list(_FILES_FOLDERS)
    rng.shuffle(folders)
    uses = []
    for i in range(n):
        source = folders[i % len(folders)]
        destination = folders[(i * 5 + 3) % len(folders)]
        if destination == source:  # a move needs two different folders
            destination = folders[(i * 5 + 4) % len(folders)]
        file_name = names[i % len(names)]
        goal = _FILES_GOALS[i % len(_FILES_GOALS)].format(
            file=file_name, src=source, dst=destination
        )
        uses.append((goal, {"file_name": file_name, "source_folder": source,
                            "destination_folder": destination}))
    return uses


def deploy_uses(family: str, n: int, offset: int = 0) -> list[tuple[str, dict]]:
    """N deterministic (goal, ground-truth binding) uses for the family."""
    builders = {
        "ContactsAddContact": _contacts_uses,
        "SimpleCalendarAddOneEvent": _calendar_uses,
        "MarkorCreateNote": _markor_uses,
        "MarkorDeleteNote": _markor_delete_uses,
        "OsmAndFavorite": _osmand_favorite_uses,
        "OsmAndMarker": _osmand_marker_uses,
        "FilesMoveFile": _files_move_uses,
    }
    if family not in builders:
        raise ValueError(f"unknown family {family!r}")
    rng = random.Random(f"guiexp_android:deploy:{family}")  # deterministic per family
    return builders[family](rng, n + offset)[offset:]


# --------------------------------------------------------------- prompts

EXTRACTION_SYSTEM = (
    "You extract structured parameters from a natural-language request. "
    "You reply with a JSON object only."
)

_FIELD_HINTS = {
    "ContactsAddContact": (
        "- name is the person's full name, exactly two words (first and last),\n"
        "  one string -- never split into separate keys.\n"
        "- number is the phone number, one string copied verbatim (keep any\n"
        "  dashes/spaces/plus exactly as written).\n"
    ),
    "SimpleCalendarAddOneEvent": (
        "- year, month, day, hour and duration_mins are INTEGERS.\n"
        "- event_title and event_description are strings copied verbatim,\n"
        "  without their surrounding quotes.\n"
        "- month is the month number (October = 10), not the name.\n"
    ),
    "MarkorCreateNote": (
        "- file_name is the note's file name INCLUDING its extension\n"
        "  (e.g. notes.txt), one string.\n"
        "- text is the note's text content, copied verbatim.\n"
    ),
    "MarkorDeleteNote": (
        "- file_name is the name of the note to delete, INCLUDING its\n"
        "  extension (e.g. notes.txt), one string and nothing else.\n"
    ),
    "OsmAndFavorite": (
        "- location is the place to save, one string copied VERBATIM: either\n"
        "  a place name such as 'Schaan, Liechtenstein' or a coordinate pair\n"
        "  such as '47.16634, 9.51030'. Keep the comma, the spacing and every\n"
        "  decimal digit exactly as written; never convert one form to the\n"
        "  other and never round.\n"
    ),
    "OsmAndMarker": (
        "- location is the place to mark, one string copied VERBATIM: either\n"
        "  a place name such as 'Schaan, Liechtenstein' or a coordinate pair\n"
        "  such as '47.16634, 9.51030'. Keep the comma, the spacing and every\n"
        "  decimal digit exactly as written; never convert one form to the\n"
        "  other and never round.\n"
    ),
    "FilesMoveFile": (
        "- file_name is the file to move, name WITH its extension\n"
        "  (e.g. holiday_photos.jpg), one string.\n"
        "- source_folder is the folder the file is in now and\n"
        "  destination_folder is the folder it must end up in; both are bare\n"
        "  folder names (e.g. Download, DCIM), without any path and without\n"
        "  the storage-area name.\n"
    ),
}


def _extraction_body(family: str) -> str:
    fields = binding_fields(family)
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


_INT_RANGES = {  # family-independent sanity bounds for the calendar ints
    "month": (1, 12),
    "day": (1, 31),
    "hour": (0, 23),
    "duration_mins": (1, 24 * 60),
    "year": (2000, 2100),
}


def binding_type_errors(got, family: str) -> list[str]:
    """The deploy boundary: every required key present with the right type
    (non-empty string, or integer-valued for the calendar's numeric keys),
    plus the family's own shape rules."""
    if not isinstance(got, dict):
        return ["reply is not a JSON object"]
    spec = FAMILY_BINDINGS[family]
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
            hint = ""
            if family == "ContactsAddContact" and field == "name" and isinstance(got[field], dict):
                hint = " (name must be ONE string 'First Last', never separate first/last keys)"
            if family == "ContactsAddContact" and field == "number" and isinstance(got[field], list):
                hint = " (number must be one string, never a list)"
            errs.append(f"{field}: expected a single string, got {type(got[field]).__name__}{hint}")
        elif not got[field].strip():
            errs.append(f"{field}: empty string")
    # family shape rules (the boundary failure each family is prone to)
    if family == "ContactsAddContact" and isinstance(got.get("name"), str):
        words = got["name"].split()
        if len(words) != 2:
            errs.append(f"name: must be exactly two words (first and last), got {len(words)}")
    if family in ("MarkorCreateNote", "FilesMoveFile") and isinstance(got.get("file_name"), str):
        name = got["file_name"].strip()
        if "." not in name or name.startswith(".") or name.endswith("."):
            errs.append("file_name: must include its extension (base.ext)")
    if family == "FilesMoveFile":
        source = got.get("source_folder")
        destination = got.get("destination_folder")
        if isinstance(source, str) and isinstance(destination, str):
            if source.strip() == destination.strip():
                errs.append("source_folder and destination_folder must differ")
            for field, value in (("source_folder", source), ("destination_folder", destination)):
                if "/" in value:
                    errs.append(f"{field}: must be a bare folder name, not a path")
    if family in ("OsmAndFavorite", "OsmAndMarker") and isinstance(got.get("location"), str):
        # a coordinate pair must keep both numbers; a name must keep its text
        text = got["location"].strip()
        numbers = re.findall(r"-?\d+\.\d+", text)
        if numbers and len(numbers) != 2:
            errs.append("location: a coordinate location needs exactly two numbers (lat, lon)")
    return errs


def normalize_binding(got: dict, family: str) -> dict:
    """Coerce the checked reply into the binding the program expects
    (integer fields as ints, strings stripped)."""
    int_fields = set(FAMILY_BINDINGS[family]["int_fields"])
    out = {}
    for field in binding_fields(family):
        value = got[field]
        if field in int_fields and (not isinstance(value, int) or isinstance(value, bool)):
            value = int(str(value).strip())
        elif isinstance(value, str):
            value = value.strip()
        out[field] = value
    return out


def parse_binding_json(reply: str):
    """JSON object from a model reply (tolerates ```json fences); {} on failure."""
    text = (reply or "").strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.S)
    if match is not None:
        text = match.group(1).strip()
    try:
        got = json.loads(text)
    except Exception:
        return None
    return got if isinstance(got, dict) else None


# ------------------------------------------------------- mock extraction model

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_NAME_STOPLIST = {"Simple Calendar", "The Event", "Markor"}


def _mock_contacts(goal: str) -> dict:
    number = None
    m = re.search(r"(\+?\d[\d\-(). ]{5,}\d)", goal)
    if m:
        number = m.group(1).strip()
    name = None
    for pattern in (
        r"[Aa]dd ([A-Z][a-z]+ [A-Z][a-z]+) to",
        r"contact (?:entry )?for ([A-Z][a-z]+ [A-Z][a-z]+)",
        r"[Cc]reate a contact for ([A-Z][a-z]+ [A-Z][a-z]+)",
        r"[Ss]ave a new contact: ([A-Z][a-z]+ [A-Z][a-z]+)",
        r"save ([A-Z][a-z]+ [A-Z][a-z]+) \(",
        r"[Nn]ew contact ([A-Z][a-z]+ [A-Z][a-z]+)",
        r"([A-Z][a-z]+ [A-Z][a-z]+), phone",
        r"([A-Z][a-z]+ [A-Z][a-z]+); the number",
        r"([A-Z][a-z]+ [A-Z][a-z]+), reachable",
        r"reach them at",
    ):
        m = re.search(pattern, goal)
        if m and m.lastindex and m.group(1):
            name = m.group(1)
            break
    if name is None:  # first two-capital-word run that is not stoplisted
        for m in re.finditer(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b", goal):
            if m.group(1) not in _NAME_STOPLIST:
                name = m.group(1)
                break
    return {k: v for k, v in {"name": name, "number": number}.items() if v}


def _mock_calendar(goal: str) -> dict:
    quoted = re.findall(r"'([^']+)'", goal)
    title = quoted[0] if len(quoted) >= 1 else None
    description = quoted[1] if len(quoted) >= 2 else None

    year = month = day = None
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", goal)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b([A-Z][a-z]+) (\d{1,2}), (\d{4})\b", goal)
        if m and m.group(1).lower() in _MONTHS:
            month, day, year = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))

    hour = None
    for pattern in (
        r"at (\d{1,2})h\b",
        r"starting at (\d{1,2}):00\b",
        r"at (\d{1,2}):00\b",
        r"\bstart (\d{1,2})h\b",
        r"at (\d{1,2}) hours\b",
    ):
        m = re.search(pattern, goal)
        if m:
            hour = int(m.group(1))
            break

    duration = None
    for pattern in (
        r"(?:last for|lasting|lasts|duration) (\d{1,4}) min",
        r", (\d{1,4}) minutes",
        r" (\d{1,4}) minutes long",
    ):
        m = re.search(pattern, goal)
        if m:
            duration = int(m.group(1))
            break

    fields = {
        "year": year, "month": month, "day": day, "hour": hour,
        "duration_mins": duration, "event_title": title,
        "event_description": description,
    }
    return {k: v for k, v in fields.items() if v is not None}


def _mock_markor(goal: str) -> dict:
    file_name = None
    for pattern in (
        r"named ([\w.-]+\.\w+)",
        r"called ([\w.-]+\.\w+)",
        r"note ([\w.-]+\.\w+)[;.]",
        r"[Aa]dd a note ([\w.-]+\.\w+)",
    ):
        m = re.search(pattern, goal)
        if m:
            file_name = m.group(1)
            break
    text = None
    for pattern in (
        r"with the following text: (.+)$",
        r"containing: (.+)$",
        r"content should be: (.+)$",
        r"with this text: (.+)$",
        r"note text is: (.+)$",
    ):
        m = re.search(pattern, goal)
        if m:
            text = m.group(1).strip()
            break
    return {k: v for k, v in {"file_name": file_name, "text": text}.items() if v}


def _mock_markor_delete(goal: str) -> dict:
    for pattern in (
        r"named ([\w.-]+\.\w+)",
        r"called ([\w.-]+\.\w+)",
        r"note ([\w.-]+\.\w+)[;.]",
        r"[Rr]emove ([\w.-]+\.\w+) from",
        r"delete the note ([\w.-]+\.\w+)",
    ):
        m = re.search(pattern, goal)
        if m:
            return {"file_name": m.group(1)}
    return {}


def _mock_osmand(goal: str) -> dict:
    """The place is whatever sits between the app-independent lead-in and the
    trailing app phrase; coordinates are recognised before names so a
    'lat, lon' pair is never split at its comma."""
    m = re.search(r"\b(-?\d{1,3}\.\d+,\s*-?\d{1,3}\.\d+)", goal)
    if m:
        return {"location": m.group(1).strip()}
    for pattern in (
        r"marker for (.+?) in the OsmAnd",
        r"In OsmAnd, save (.+?) as a favorite",
        r"star (.+?) as a favorite",
        r"[Mm]ark (.+?) as one of my favorites",
        r"Add (.+?) to the favorites",
        r"drop a location marker on (.+?)\.",
        r"map marker at (.+?) in the OsmAnd",
        r"[Ss]et a marker for (.+?) in OsmAnd",
        r"location marker on (.+?) in the OsmAnd",
    ):
        m = re.search(pattern, goal)
        if m:
            return {"location": m.group(1).strip()}
    return {}


def _mock_files_move(goal: str) -> dict:
    for pattern in (
        r"[Mm]ove the file (?P<file>\S+) from (?P<src>\w+) within .* to the "
        r"(?P<dst>\w+) within",
        r"move (?P<file>\S+) out of (?P<src>\w+) and into (?P<dst>\w+)",
        r"relocate the file (?P<file>\S+) from the (?P<src>\w+) folder to the "
        r"(?P<dst>\w+) folder",
        r"Take (?P<file>\S+) from (?P<src>\w+) and put it in (?P<dst>\w+)",
        r"[Tt]ransfer the file (?P<file>\S+) from the (?P<src>\w+) folder into "
        r"the (?P<dst>\w+) folder",
    ):
        m = re.search(pattern, goal)
        if m:
            return {
                "file_name": m.group("file"),
                "source_folder": m.group("src"),
                "destination_folder": m.group("dst"),
            }
    return {}


def mock_extract_fields(goal: str, family: str) -> dict:
    """Rule-based extraction of the family's fields from a deploy-style goal.

    A deterministic stand-in for the extraction LLM: cascades of literal
    patterns over the goal prose, no access to the ground truth.
    """
    extractors = {
        "ContactsAddContact": _mock_contacts,
        "SimpleCalendarAddOneEvent": _mock_calendar,
        "MarkorCreateNote": _mock_markor,
        "MarkorDeleteNote": _mock_markor_delete,
        "OsmAndFavorite": _mock_osmand,
        "OsmAndMarker": _mock_osmand,
        "FilesMoveFile": _mock_files_move,
    }
    return extractors[family](goal.strip())


class _MockExtractionCompletions:
    def __init__(self, family: str):
        self.family = family

    def create(self, *, model=None, messages=None, **_kwargs):
        last_user = ""
        for message in reversed(messages or []):
            if message.get("role") == "user":
                content = message.get("content")
                last_user = content if isinstance(content, str) else json.dumps(content)
                break
        m = re.search(r"Request: (.+)", last_user, re.S)
        fields = mock_extract_fields(m.group(1), self.family) if m else {}
        # fenced, as many models reply; the pipeline must strip fences
        reply = "```json\n" + json.dumps(fields, indent=None) + "\n```"
        prompt_tokens = _approx_prompt_tokens(messages or [])
        completion_tokens = max(1, len(reply) // 4)
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=round(prompt_tokens * 1e-6 + completion_tokens * 2e-6, 8),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
            usage=usage,
            model=model or "mock-extract",
        )


class MockExtractor:
    """Drop-in openai-style client for tests: deterministic, offline."""

    def __init__(self, family: str) -> None:
        self.chat = SimpleNamespace(completions=_MockExtractionCompletions(family))


# ------------------------------------------------------------------- the chain


def run_single_use(
    program,
    family: str,
    goal: str,
    expected: dict,
    expected_params: dict | None,
    client,
    model: str,
    runner: ProgramRunner,
) -> dict:
    """One deployment use: extract -> type check (one retry) -> program -> oracle."""
    from .agent import AndroidAgent

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
        usage = AndroidAgent._usage(response)
        tokens += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        cost += usage.get("cost_usd") or 0.0
        # One record per call: the extraction chain is one or two calls, and
        # the cache-adjusted unit cannot be recovered from their sum.
        calls_detail.append(
            call_record(len(calls_detail) + 1, len(calls_detail) + 1, usage,
                        stage="deploy_extract", kind=kind)
        )
        return response.choices[0].message.content or ""

    got = parse_binding_json(call(build_extraction_prompt(goal, family), "extract"))
    errors = binding_type_errors(got, family)
    if errors:  # one bounded retry with the errors in the prompt
        retries = 1
        got = parse_binding_json(call(build_retry_prompt(goal, family, errors), "retry"))
        errors = binding_type_errors(got, family)

    success = False
    if got is None:
        error_type = "extraction_json"
    elif errors:
        error_type = "type_check"  # boundary reject: a real episode falls back reactive
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


def run_deployment(
    program,
    family: str,
    uses: list[tuple[str, dict]],
    client,
    model: str,
    runner: ProgramRunner,
    role: str | None = None,
) -> dict:
    records: list[dict] = []
    for goal, expected in uses:
        expected_params = binding_to_params(family, expected)
        record = run_single_use(
            program, family, goal, expected, expected_params, client, model, runner
        )
        records.append(record)
        tag = record["error_type"] or ("OK" if record["success"] else "FAIL")
        print(f"  use {len(records):>2}: {record['tokens']:>5} tok (r{record['retries']}) {tag}", flush=True)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", required=True, help="compiled family program .py")
    parser.add_argument("--family", required=True, help="task family")
    parser.add_argument("--mock", action="store_true", help="MockExtractor: fully offline extraction")
    parser.add_argument("--model", default=None, help="OpenRouter model id for extraction")
    parser.add_argument("--uses", type=int, default=10, help="number of deployment uses")
    parser.add_argument("--use-offset", type=int, default=0, help="start index into the use pool")
    parser.add_argument("--role", default=None, help="label recorded into deploy.json (e.g. deploy30-best)")
    parser.add_argument("--out", default=None, help="output JSON path (default under experimental-results/guiexp_android/)")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")
    if args.mock and args.model:
        parser.error("--mock and --model are mutually exclusive")

    model = args.model or ("mock" if args.mock else "openai/gpt-4o-mini")
    _module, program = program_from_path(args.program)
    uses = deploy_uses(args.family, args.uses, offset=args.use_offset)
    client = MockExtractor(args.family) if args.mock else None
    if client is None:
        from .compiler import _openai_client

        client = _openai_client()

    env = android_env.AndroidWorldEnv()
    try:
        summary = run_deployment(
            program, args.family, uses, client, model, ProgramRunner(env), role=args.role
        )
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()  # adb emu kill, only if we booted it

    summary.update(
        {
            "program_path": str(Path(args.program).resolve()),
            "use_offset": args.use_offset,
        }
    )
    out = Path(args.out) if args.out else (
        android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
        / f"deploy_{model.replace('/', '-')}_{args.family}" / "deploy.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1))
    print(
        f"deploy: {summary['success_count']}/{summary['n']} uses ok, "
        f"d(mean) = {summary['d_tokens_mean'] or 0:.0f} tok"
    )
    print(f"wrote {out}")
    return 0 if summary["success_count"] == summary["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
