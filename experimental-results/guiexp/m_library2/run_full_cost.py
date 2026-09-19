"""Deploy-full-cost measurement: route + extract in ONE call (d_full), with
binding fidelity, split-call comparison, and a bounded-retry share.

Motivation (docs/deploy-full-cost-measurement.md): our per-use accounting
charged d0 = 347 tok (extraction only -- the experiment TOLD the agent which
program to use). Real deployment must first determine the FAMILY against the
program manifest. This measures the combined call

    prompt = manifest(n) + goal + instruction
    reply  = `USE <name>` + JSON binding of that program's parameters, or NONE

Reuses the m_library assets verbatim (manifests, subset policy, distractor
goals) and adds 3 fresh instances per match goal (18 match-instances, deploy30
goal style: prose carrying exact parameter values) each with a ground-truth
binding. Fidelity is EXACT field-value match (fuzzy variants like trailing
punctuation count as FAIL, mirroring the deploy30 oracle_fail taxonomy).

Phases (all records appended to JSONL, resumable, cost-guarded):
  A combined  : GLM n in {1,5,20,100} x 32 goals x seeds {0,1};
                DS spot n=100 x 32 x seed {0} (max_tokens 3072).
  B split     : GLM n=100 seed 0: route call (run_routing.py's exact router
                instruction), then an extraction call (deploy_runner-style
                prompt parameterized by the routed program's schema) for the
                cells the router matched.
  C retry     : one bounded retry for every GLM combined binding fail, with
                deploy_runner's contract reminder ("Your previous reply was
                rejected by the type check: ...; Fix exactly these problems").

Ground truth per manifest, as in run_routing.py: a match instance's correct
answer is USE <target> iff the target is listed in manifest(n), else NONE.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MLIB = HERE.parent / "m_library"

GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"

COST_GUARD_USD = 0.45

# --------------------------------------------------------------- programs

WIZARD = "openapps_calendar_create_event_wizard"
SINGLE = "openapps_calendar_create_event_single_page"
SECTIONED = "openapps_calendar_create_event_sectioned"
TODO = "openapps_todo_add_item"
MESSAGES = "openapps_messages_send"
FLIGHTS = "flights_book_roundtrip"
EMAIL = "email_send_with_attachment"
SHEET = "spreadsheet_append_row"

# field -> type ('str' | 'int' | 'str_list'); ground truth bindings below
# must match these types.
FIELDS_META: dict[str, dict[str, str]] = {
    WIZARD: {"title": "str", "date": "str", "description": "str",
             "location": "str", "url": "str", "invitees": "str"},
    SINGLE: {"title": "str", "date": "str", "description": "str",
             "location": "str", "url": "str", "invitees": "str"},
    SECTIONED: {"title": "str", "date": "str", "description": "str",
                "location": "str", "url": "str", "invitees": "str"},
    TODO: {"item_title": "str", "due_date": "str", "priority": "str",
           "notes": "str"},
    MESSAGES: {"recipient_name": "str", "message_body": "str"},
    FLIGHTS: {"origin": "str", "destination": "str", "depart_date": "str",
              "return_date": "str", "cabin": "str", "passengers": "int"},
    EMAIL: {"to": "str", "subject": "str", "body": "str",
            "attachment_path": "str"},
    SHEET: {"workbook": "str", "sheet": "str", "values": "str_list"},
}
OPTIONAL_FIELDS = {(TODO, "notes")}
INVITEES_NOTE = " The invitees field is a single string of names, never a list."

# ------------------------------------------------------ goals (32 = 18 + 14)
# Match instances: deploy30 / conditions.py discover-goal style prose carrying
# the exact parameter values; 3 fresh instances per match goal.

GOAL_INSTANCES: list[dict] = [
    # G1 -- calendar wizard (must disambiguate vs single_page/sectioned)
    dict(goal_id="G1_calendar_wizard", inst="a", target=WIZARD,
         text="Please create Orion Kickoff on 2027-06-03. It is a Project "
              "launch workshop, located in Room 12, url is "
              "https://example.com/orion, invite Nadia. Our calendar site "
              "walks you through the new-event form one screen at a time "
              "with a Next button.",
         binding={"title": "Orion Kickoff", "date": "2027-06-03",
                  "description": "Project launch workshop",
                  "location": "Room 12", "url": "https://example.com/orion",
                  "invitees": "Nadia"}),
    dict(goal_id="G1_calendar_wizard", inst="b", target=WIZARD,
         text="Add Vega Review on 2027-06-10 with description Sprint planning "
              "sync at Hall C, see https://example.com/vega, invite Omar. The "
              "event form on this deployment is a multi-step wizard with a "
              "Next button between screens.",
         binding={"title": "Vega Review", "date": "2027-06-10",
                  "description": "Sprint planning sync", "location": "Hall C",
                  "url": "https://example.com/vega", "invitees": "Omar"}),
    dict(goal_id="G1_calendar_wizard", inst="c", target=WIZARD,
         text="New calendar item: Lyra Sync, 2027-06-17, Data pipeline check, "
              "Suite 5, https://example.com/lyra, guest Ines. The calendar's "
              "create form goes one screen at a time (Next button wizard).",
         binding={"title": "Lyra Sync", "date": "2027-06-17",
                  "description": "Data pipeline check", "location": "Suite 5",
                  "url": "https://example.com/lyra", "invitees": "Ines"}),
    # G2 -- todo add
    dict(goal_id="G2_todo_add", inst="a", target=TODO,
         text="Please put 'renew passport' on my to-do list for 2027-10-01 "
              "-- mark it important.",
         binding={"item_title": "renew passport", "due_date": "2027-10-01",
                  "priority": "high"}),
    dict(goal_id="G2_todo_add", inst="b", target=TODO,
         text="Add 'book dentist appointment' to my to-do list, due "
              "2027-11-05, high priority.",
         binding={"item_title": "book dentist appointment",
                  "due_date": "2027-11-05", "priority": "high"}),
    dict(goal_id="G2_todo_add", inst="c", target=TODO,
         text="To-do list: 'submit expense report' by 2027-09-30, priority "
              "high.",
         binding={"item_title": "submit expense report",
                  "due_date": "2027-09-30", "priority": "high"}),
    # G3 -- flights roundtrip
    dict(goal_id="G3_flights_roundtrip", inst="a", target=FLIGHTS,
         text="Lock in flights for me: out from JFK to London 2027-10-12, "
              "back 2027-10-19, economy, two travelers.",
         binding={"origin": "JFK", "destination": "London",
                  "depart_date": "2027-10-12", "return_date": "2027-10-19",
                  "cabin": "economy", "passengers": 2}),
    dict(goal_id="G3_flights_roundtrip", inst="b", target=FLIGHTS,
         text="Book the round trip Boston to Amsterdam: leave 2027-03-04, "
              "return 2027-03-18, business class, just me.",
         binding={"origin": "Boston", "destination": "Amsterdam",
                  "depart_date": "2027-03-04", "return_date": "2027-03-18",
                  "cabin": "business", "passengers": 1}),
    dict(goal_id="G3_flights_roundtrip", inst="c", target=FLIGHTS,
         text="Round-trip flights ORD to Tokyo, out 2027-05-07, back "
              "2027-05-21, economy, 3 passengers.",
         binding={"origin": "ORD", "destination": "Tokyo",
                  "depart_date": "2027-05-07", "return_date": "2027-05-21",
                  "cabin": "economy", "passengers": 3}),
    # G4 -- email with attachment
    dict(goal_id="G4_email_attachment", inst="a", target=EMAIL,
         text="Send an email to dana@corp.io with q3_report.pdf attached -- "
              "subject 'Q3 documents', body just 'attached, please review by "
              "Friday'.",
         binding={"to": "dana@corp.io", "subject": "Q3 documents",
                  "body": "attached, please review by Friday",
                  "attachment_path": "q3_report.pdf"}),
    dict(goal_id="G4_email_attachment", inst="b", target=EMAIL,
         text="Email prof.lee@uni.edu the file contract_v2.pdf -- subject "
              "'Contract for signature', body 'Please sign and return by "
              "Monday'.",
         binding={"to": "prof.lee@uni.edu",
                  "subject": "Contract for signature",
                  "body": "Please sign and return by Monday",
                  "attachment_path": "contract_v2.pdf"}),
    dict(goal_id="G4_email_attachment", inst="c", target=EMAIL,
         text="Compose a mail to sam.k@inbox.org attaching photos.zip, "
              "subject 'Trip photos', body 'Here are the photos from the "
              "trip'.",
         binding={"to": "sam.k@inbox.org", "subject": "Trip photos",
                  "body": "Here are the photos from the trip",
                  "attachment_path": "photos.zip"}),
    # G5 -- spreadsheet append row
    dict(goal_id="G5_spreadsheet_row", inst="a", target=SHEET,
         text="Append a row to the October sheet in sales_2027.xlsx with: "
              "42, 5039, downtown.",
         binding={"workbook": "sales_2027.xlsx", "sheet": "October",
                  "values": ["42", "5039", "downtown"]}),
    dict(goal_id="G5_spreadsheet_row", inst="b", target=SHEET,
         text="Log a new row in the Leads sheet of tracker.xlsx: Marie, "
              "webinar, 88.",
         binding={"workbook": "tracker.xlsx", "sheet": "Leads",
                  "values": ["Marie", "webinar", "88"]}),
    dict(goal_id="G5_spreadsheet_row", inst="c", target=SHEET,
         text="Append to expenses.xlsx, sheet Q3, the row: 2027-07-14, taxi, "
              "34.5.",
         binding={"workbook": "expenses.xlsx", "sheet": "Q3",
                  "values": ["2027-07-14", "taxi", "34.5"]}),
    # G6 -- messages send
    dict(goal_id="G6_messages_send", inst="a", target=MESSAGES,
         text="Text Mom from the messages app: 'running 15 minutes late'.",
         binding={"recipient_name": "Mom",
                  "message_body": "running 15 minutes late"}),
    dict(goal_id="G6_messages_send", inst="b", target=MESSAGES,
         text="Send Dad a message in the messages app saying 'picked up the "
              "dry cleaning'.",
         binding={"recipient_name": "Dad",
                  "message_body": "picked up the dry cleaning"}),
    dict(goal_id="G6_messages_send", inst="c", target=MESSAGES,
         text="In the messages app, text my sister Ana: 'dinner at 8, see "
              "you then'.",
         binding={"recipient_name": "Ana",
                  "message_body": "dinner at 8, see you then"}),
]

# 14 distractors, verbatim from run_routing.py (correct NONE at every n).
DISTRACTORS: list[dict] = [
    dict(goal_id="D1_delete_event",
         text="Remove the 'Security audit' event from my calendar -- another "
              "team owns it now."),
    dict(goal_id="D2_move_flight",
         text="My plans changed: move my existing London flight from October "
              "12 to October 15, same airline."),
    dict(goal_id="D3_unsubscribe",
         text="Please take me off the daily-deals newsletter mailing list, I "
              "get too many of them."),
    dict(goal_id="D4_share_edit",
         text="Share the budget sheet with Sam -- he needs to be able to "
              "edit it."),
    dict(goal_id="D5_bank_transfer",
         text="Move 500 dollars from my checking account to my savings "
              "account."),
    dict(goal_id="D6_order_pizza",
         text="Order a large pepperoni pizza for delivery tonight around 7."),
    dict(goal_id="D7_post_photo",
         text="Post the beach photo to my feed with the caption 'sunset'."),
    dict(goal_id="D8_track_run",
         text="Start tracking my 5k run this evening."),
    dict(goal_id="D9_leave_review",
         text="Leave a five-star review for the standing desk I bought last "
              "month."),
    dict(goal_id="D10_boarding_pass",
         text="Print my boarding pass for tomorrow morning's flight."),
    dict(goal_id="D11_thermostat",
         text="Set the thermostat to 68 and turn off the downstairs lights "
              "before we leave."),
    dict(goal_id="D12_find_doctor",
         text="Find me a new primary care doctor nearby and get my records "
              "transferred."),
    dict(goal_id="D13_cancel_gym",
         text="Cancel my gym membership before it renews on the first."),
    dict(goal_id="D14_backup_email",
         text="Back up my entire email account to a local archive file on "
              "the laptop."),
]

ALL_GOALS = GOAL_INSTANCES + DISTRACTORS

# ------------------------------------------------------------- prompts

COMBINED_INSTRUCTION = (
    "You are the entry point of a GUI-automation library. For each user goal "
    "you decide whether one of the library's compiled programs can accomplish "
    "the goal end-to-end. The library manifest below lists every available "
    "program with its trigger scenario and parameters. A program matches only "
    "when its trigger scenario fits the goal and its parameters can carry the "
    "specifics the user gave; closely related but different tasks do not "
    "match.\n\n"
    "If one listed program covers the task, reply with 'USE: <program_name>' "
    "(the single best-matching program name copied verbatim from the "
    "manifest) on the first line, then a JSON object that binds that "
    "program's parameters to the values given in the goal:\n"
    "- copy values verbatim from the goal; never invent or paraphrase;\n"
    "- dates as YYYY-MM-DD strings; counts (e.g. passengers) as JSON "
    "integers; list-valued parameters as JSON arrays with one entry per "
    "listed item;\n"
    "- the invitees field is a single string of names, never a list;\n"
    "- omit optional parameters the goal does not supply.\n"
    "If no listed program fits, reply 'NONE' and nothing else. Never output "
    "a program name that is not in the manifest.\n\n"
)

# run_routing.py's router instruction, reused verbatim for the split route arm.
ROUTER_INSTRUCTION = (
    "You are the router of a GUI-automation library. For each user goal you "
    "decide whether one of the library's compiled programs can accomplish the "
    "goal end-to-end. The library manifest below lists every available program "
    "with its trigger scenario and parameters. A program matches only when its "
    "trigger scenario fits the goal and its parameters can carry the specifics "
    "the user gave; closely related but different tasks do not match. Reply "
    "with exactly one line and nothing else: 'USE: <program_name>' with the "
    "single best-matching program name copied verbatim from the manifest, or "
    "'NONE' when no listed program fits. Never output a program name that is "
    "not in the manifest.\n\n"
)

EXTRACTION_SYSTEM = (
    "You extract structured parameters from a natural-language request. "
    "You reply with a JSON object only."
)


def extraction_body(program: str, goal_text: str) -> str:
    meta = FIELDS_META[program]
    keys = list(meta)
    note = INVITEES_NOTE if "invitees" in meta else ""
    return (
        f"Extract the {len(keys)} parameters of the compiled program "
        f"'{program}' from the request below as a JSON object with exactly "
        f"the keys {', '.join(keys)}.\n\n"
        "- Every value is copied verbatim from the request; never invent or "
        "paraphrase.\n"
        "- Dates as YYYY-MM-DD strings; counts (e.g. passengers) as JSON "
        "integers; list-valued parameters as JSON arrays with one entry per "
        "listed item.\n"
        f"-{note.strip() if note else ''} Omit optional parameters the "
        "request does not supply.\n"
        f"Request: {goal_text}\n\n"
        "Reply with ONLY the JSON object. No markdown fences, no explanation."
    )


def retry_user_text(goal_text: str, errors: list[str]) -> str:
    # deploy_runner.build_retry_prompt's contract reminder, adapted to the
    # combined reply format.
    return (
        f"{goal_text}\n\n"
        "Your previous reply was rejected by the type check:\n"
        + "; ".join(errors)
        + "\nFix exactly these problems. Reply again in the required format: "
        "'USE: <program_name>' on the first line, then the corrected JSON "
        "binding (or 'NONE' if no listed program fits)."
    )


# ------------------------------------------------------------- parsing

def manifest_members(n: int) -> set[str]:
    text = (MLIB / f"manifest_n{n}.txt").read_text()
    return set(re.findall(r"^PROGRAM (\S+) —", text, flags=re.M))


def parse_binding_json(text: str):
    """JSON object from a reply tail (fence-tolerant); None on failure."""
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", t, re.S)
    if m is not None:
        t = m.group(1).strip()
    try:
        got = json.loads(t)
        return got if isinstance(got, dict) else None
    except Exception:
        pass
    # last resort: first balanced {...} block anywhere
    m = re.search(r"\{.*\}", t, re.S)
    if m is not None:
        try:
            got = json.loads(m.group(0))
            return got if isinstance(got, dict) else None
        except Exception:
            return None
    return None


def parse_combined(reply: str):
    """-> (pred_kind, pred_name, binding); pred_kind in {use,none,malformed}."""
    text = (reply or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if re.fullmatch(r"NONE[.!]?", first, flags=re.I) or text.upper().startswith("NONE"):
        return "none", None, None
    m = re.search(r"USE:?\s*`?([A-Za-z0-9_.\-]+)`?", text)
    if not m:
        return "malformed", None, None
    name = m.group(1)
    binding = parse_binding_json(text[m.end():])
    return "use", name, binding


def parse_route(reply: str):
    """run_routing.py's parse_reply."""
    text = (reply or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    if re.fullmatch(r"NONE[.!]?", first, flags=re.I) or text.upper().startswith("NONE"):
        return "none", None
    m = re.search(r"USE:\s*`?([A-Za-z0-9_.\-]+)`?", text)
    if m:
        return "use", m.group(1)
    return "malformed", None


# ------------------------------------------------------------- scoring

def _elem_eq(got, exp: str) -> bool:
    if isinstance(got, bool):
        return False
    if isinstance(got, (int, float)):
        got = str(got)
    return isinstance(got, str) and got == exp


def canon_eq(ftype: str, got, exp) -> bool:
    if ftype == "int":
        if isinstance(got, bool):
            return False
        try:
            return int(got) == int(exp)
        except Exception:
            return False
    if ftype == "str_list":
        if not isinstance(got, list) or len(got) != len(exp):
            return False
        return all(_elem_eq(g, e) for g, e in zip(got, exp))
    return isinstance(got, str) and got == exp


def classify_mismatch(ftype: str, got, exp):
    """deploy30 oracle_fail-style taxonomy for one field."""
    if got is None:
        return "missing_field"
    if ftype == "int":
        return "format_error"
    if ftype == "str_list":
        if not isinstance(got, list):
            return "type_error"
        if len(got) != len(exp):
            return "wrong_arity"
        return "element_mismatch"
    if not isinstance(got, str):
        return "type_error"
    if got.strip() == "":
        return "empty_value"
    if got != exp and got.strip().rstrip(".,;:!?").strip() == exp.strip():
        return "fuzzy_punctuation"
    if exp.lower() in got.lower():
        return "swallowed_context"
    if got.lower() in exp.lower():
        return "truncated_value"
    return "value_mismatch"


def eval_against_gt(program: str, gt: dict, binding) -> dict:
    """Exact-match scoring of a binding against the goal instance's gt.

    Returns dict(exact_ok, field_fails, error_phrases, extras_empty) where
    error_phrases are deploy_runner-style messages for the retry prompt.
    """
    meta = FIELDS_META[program]
    field_fails = []
    error_phrases = []
    if binding is None:
        return dict(exact_ok=False,
                    field_fails=[("?", "unparseable_binding", None, None)],
                    error_phrases=["reply did not contain a usable JSON binding"],
                    extras_empty=[])
    for f, exp in gt.items():
        got = binding.get(f, None)
        ftype = meta[f]
        if not canon_eq(ftype, got, exp):
            kind = classify_mismatch(ftype, got, exp)
            field_fails.append((f, kind, got, exp))
            if kind == "missing_field":
                error_phrases.append(f"missing key {f}")
            elif kind == "type_error":
                tn = type(got).__name__
                extra = (" (invitees must be one string of names, never a "
                         "list)" if f == "invitees" and isinstance(got, list) else "")
                error_phrases.append(
                    f"{f}: expected a single value of the declared type, "
                    f"got {tn}{extra}")
            elif kind == "empty_value":
                error_phrases.append(f"{f}: empty string")
            else:
                error_phrases.append(
                    f"{f}: value is not copied verbatim from the request")
    extras_bad = []
    extras_empty = []
    for k, v in binding.items():
        if k in gt:
            continue
        if (program, k) in OPTIONAL_FIELDS:
            if v in (None, ""):
                extras_empty.append(k)
            else:
                extras_bad.append((k, "invented_optional", v, None))
        else:
            extras_bad.append((k, "invented_field", v, None))
    for k, kind, v, _ in extras_bad:
        error_phrases.append(f"{k}: not a parameter of this request")
    exact_ok = not field_fails and not extras_bad
    return dict(exact_ok=exact_ok, field_fails=field_fails + extras_bad,
                error_phrases=error_phrases, extras_empty=extras_empty)


def routing_error_kind(rec: dict) -> str:
    """run_routing.py's taxonomy."""
    if rec["routing_correct"]:
        return "correct"
    if rec["pred_kind"] == "malformed":
        return "malformed"
    if rec["gt_kind"] == "none":
        return "false_positive" if rec.get("pred_listed") else "unlisted_name"
    if rec["pred_kind"] == "none":
        return "false_negative"
    if not rec.get("pred_listed"):
        return "unlisted_name"
    return "wrong_program"


# ------------------------------------------------------------- client

def make_client():
    from openai import OpenAI
    return OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=240.0,
        max_retries=3,
    )


def chat(client, model, system, user, seed, max_tokens):
    last = None
    for attempt in range(4):
        try:
            return client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                max_tokens=max_tokens,
                seed=seed,
            )
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"retry after error: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            time.sleep(4 * (attempt + 1))
    raise last


def usage_of(resp) -> tuple[int, int, float | None]:
    u = resp.usage
    cost = getattr(u, "cost", None)
    if cost is None and getattr(u, "model_extra", None):
        cost = u.model_extra.get("cost")
    return (getattr(u, "prompt_tokens", 0) or 0,
            getattr(u, "completion_tokens", 0) or 0,
            cost)


def load_done(path: Path) -> set[tuple]:
    done = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            done.add((r["phase"], r["model"], r["n"], r["seed"],
                      r["goal_id"], r.get("inst")))
    return done


def append(path: Path, rec: dict):
    with path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


# ------------------------------------------------------------- phases

def gt_of(goal: dict, members: set[str]):
    """(gt_target, gt_kind) per manifest membership."""
    t = goal.get("target")
    gt = t if t in members else None
    return gt, ("use" if gt else "none")


def phase_combined(client, records_path, state):
    grid = [(GLM, n, s) for n in (1, 5, 20, 100) for s in (0, 1)]
    grid.append((DS, 100, 0))
    done = load_done(records_path)
    t0 = time.time()
    for model, n, seed in grid:
        manifest = (MLIB / f"manifest_n{n}.txt").read_text()
        members = manifest_members(n)
        system = COMBINED_INSTRUCTION + manifest
        max_tokens = 3072 if model == DS else 1024
        cell = 0
        for goal in ALL_GOALS:
            key = ("combined", model, n, seed, goal["goal_id"], goal.get("inst"))
            if key in done:
                continue
            if state["cost"] > COST_GUARD_USD:
                print("COST GUARD hit in phase_combined", file=sys.stderr)
                return
            gt, gt_kind = gt_of(goal, members)
            resp = chat(client, model, system, goal["text"], seed, max_tokens)
            pt, ct, cost = usage_of(resp)
            reply = resp.choices[0].message.content or ""
            pred_kind, pred_name, binding = parse_combined(reply)
            binding_eval = None
            if gt is not None and pred_kind == "use" and pred_name == gt:
                binding_eval = eval_against_gt(gt, goal["binding"], binding)
            routing_correct = (
                (pred_kind == "use" and pred_name == gt) or
                (pred_kind == "none" and gt is None))
            rec = dict(
                phase="combined", model=model, n=n, seed=seed,
                goal_id=goal["goal_id"], inst=goal.get("inst"),
                gt=gt, gt_kind=gt_kind,
                reply=reply, pred_kind=pred_kind, pred_name=pred_name,
                pred_listed=pred_name in members if pred_name else None,
                routing_correct=routing_correct,
                binding=binding,
                binding_exact=binding_eval["exact_ok"] if binding_eval else None,
                field_fails=binding_eval["field_fails"] if binding_eval else None,
                error_phrases=binding_eval["error_phrases"] if binding_eval else None,
                prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
            )
            rec["error_type"] = routing_error_kind(rec)
            append(records_path, rec)
            state["cost"] += cost or 0.0
            cell += 1
        print(f"[combined] {model} n={n} seed={seed}: {cell} new calls, "
              f"running cost ${state['cost']:.4f} ({time.time()-t0:.0f}s)",
              flush=True)


def phase_split(client, records_path, state):
    n, seed = 100, 0
    done = load_done(records_path)
    manifest = (MLIB / f"manifest_n{n}.txt").read_text()
    members = manifest_members(n)
    router_sys = ROUTER_INSTRUCTION + manifest
    t0 = time.time()
    for goal in ALL_GOALS:
        key = ("split_route", GLM, n, seed, goal["goal_id"], goal.get("inst"))
        if key not in done:
            if state["cost"] > COST_GUARD_USD:
                print("COST GUARD hit in phase_split", file=sys.stderr)
                return
            gt, gt_kind = gt_of(goal, members)
            resp = chat(client, GLM, router_sys, goal["text"], seed, 512)
            pt, ct, cost = usage_of(resp)
            reply = resp.choices[0].message.content or ""
            pred_kind, pred_name = parse_route(reply)
            routing_correct = (
                (pred_kind == "use" and pred_name == gt) or
                (pred_kind == "none" and gt is None))
            rec = dict(
                phase="split_route", model=GLM, n=n, seed=seed,
                goal_id=goal["goal_id"], inst=goal.get("inst"),
                gt=gt, gt_kind=gt_kind, reply=reply,
                pred_kind=pred_kind, pred_name=pred_name,
                pred_listed=pred_name in members if pred_name else None,
                routing_correct=routing_correct,
                prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
            )
            rec["error_type"] = routing_error_kind(rec)
            append(records_path, rec)
            state["cost"] += cost or 0.0
    print(f"[split_route] done, running cost ${state['cost']:.4f} "
          f"({time.time()-t0:.0f}s)", flush=True)
    # extraction for cells the router matched
    route_recs = {}
    for line in records_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["phase"] == "split_route":
            route_recs[(r["goal_id"], r.get("inst"))] = r
    for goal in ALL_GOALS:
        k = (goal["goal_id"], goal.get("inst"))
        rr = route_recs.get(k)
        if rr is None or rr["pred_kind"] != "use":
            continue
        key = ("split_extract", GLM, n, seed, goal["goal_id"], goal.get("inst"))
        if key in load_done(records_path):
            continue
        if state["cost"] > COST_GUARD_USD:
            print("COST GUARD hit in phase_split extract", file=sys.stderr)
            return
        program = rr["pred_name"]
        resp = chat(client, GLM, EXTRACTION_SYSTEM,
                    extraction_body(program, goal["text"]), seed, 1024)
        pt, ct, cost = usage_of(resp)
        reply = resp.choices[0].message.content or ""
        binding = parse_binding_json(reply)
        binding_eval = None
        if rr["gt"] is not None and program == rr["gt"]:
            binding_eval = eval_against_gt(program, goal["binding"], binding)
        rec = dict(
            phase="split_extract", model=GLM, n=n, seed=seed,
            goal_id=goal["goal_id"], inst=goal.get("inst"),
            program=program, gt=rr["gt"], reply=reply, binding=binding,
            binding_exact=binding_eval["exact_ok"] if binding_eval else None,
            field_fails=binding_eval["field_fails"] if binding_eval else None,
            error_phrases=binding_eval["error_phrases"] if binding_eval else None,
            prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
        )
        append(records_path, rec)
        state["cost"] += cost or 0.0
    print(f"[split_extract] done, running cost ${state['cost']:.4f} "
          f"({time.time()-t0:.0f}s)", flush=True)


def phase_retry(client, combined_path, retry_path, state):
    """One bounded retry per GLM combined binding fail (correctly routed)."""
    done = load_done(retry_path)
    fails = []
    for line in combined_path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if (r["phase"] == "combined" and r["model"] == GLM
                and r["binding_exact"] is False):
            fails.append(r)
    t0 = time.time()
    for r in fails:
        key = ("retry", GLM, r["n"], r["seed"], r["goal_id"], r.get("inst"))
        if key in done:
            continue
        if state["cost"] > COST_GUARD_USD:
            print("COST GUARD hit in phase_retry", file=sys.stderr)
            return
        goal = next(g for g in ALL_GOALS
                    if g["goal_id"] == r["goal_id"]
                    and g.get("inst") == r.get("inst"))
        manifest = (MLIB / f"manifest_n{r['n']}.txt").read_text()
        system = COMBINED_INSTRUCTION + manifest
        user = retry_user_text(goal["text"], r["error_phrases"] or
                               ["binding was not exactly correct"])
        resp = chat(client, GLM, system, user, r["seed"], 1024)
        pt, ct, cost = usage_of(resp)
        reply = resp.choices[0].message.content or ""
        pred_kind, pred_name, binding = parse_combined(reply)
        ev = eval_against_gt(r["gt"], goal["binding"], binding)
        rec = dict(
            phase="retry", model=GLM, n=r["n"], seed=r["seed"],
            goal_id=r["goal_id"], inst=r.get("inst"), gt=r["gt"],
            reply=reply, pred_kind=pred_kind, pred_name=pred_name,
            binding=binding, binding_exact=ev["exact_ok"],
            field_fails=ev["field_fails"],
            prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
            fixed=(pred_kind == "use" and pred_name == r["gt"]
                   and ev["exact_ok"]),
        )
        append(retry_path, rec)
        state["cost"] += cost or 0.0
    print(f"[retry] {len(fails)} fails -> retried, cost ${state['cost']:.4f} "
          f"({time.time()-t0:.0f}s)", flush=True)


def main() -> int:
    records = HERE / "full_cost_calls.jsonl"
    retry_records = HERE / "retry_calls.jsonl"
    state = {"cost": 0.0}
    client = make_client()
    phase_combined(client, records, state)
    phase_split(client, records, state)
    phase_retry(client, records, retry_records, state)
    print(f"TOTAL new spend this invocation: ${state['cost']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
