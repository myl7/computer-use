"""Stage-2 deployment: N natural-language uses through the full chain.

Per use, the chain is exactly one LLM extraction call (natural-language goal
-> binding JSON), a type check with ONE bounded retry, the compiled program
on a real page, and the environment oracle. The tokens of the extraction
call(s) are the per-use cost d; everything after extraction is code and
costs zero tokens.

    use = (goal text, ground-truth binding)   # judge is the oracle, never
                                              # the extracted binding itself

Error types per use: "extraction_json" (reply not JSON), "type_check"
(still malformed after the one retry -- the boundary reject; a real episode
would fall back reactive), "program_error" (the program raised), or
"oracle_fail" (it ran but produced wrong state).

    ../.venv-gui/bin/python -m guiexp.deploy_runner --mock \
        --program experimental-results/guiexp/gate_mock_wizard/family_program.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

from .agent import GuiAgent
from .compiler import MockCompiler, compile_trajectory, load_trajectory
from .env import FIELDS
from .mock_model import _approx_prompt_tokens
from .program_runtime import BindingRunner, program_from_path
from .runner import DEFAULT_OUT_ROOT

# ----------------------------------------------------------------- use pool

# The deployment uses: a natural-language goal plus the values it must yield.
# Ported from openapps-exp/compile_gate.py so the two harnesses' d numbers
# stay comparable; disjoint from the gate pool and from env.INSTANCE_POOL.
DEPLOY_USES: list[tuple[str, dict]] = [
    ("Add a Zeta Standup on 2026-10-01 to the calendar. Description: Daily check-in. "
     "Location: Standup Room. Link: https://example.com/zeta. Invite: Heidi.",
     dict(title="Zeta Standup", date="2026-10-01", description="Daily check-in",
          location="Standup Room", url="https://example.com/zeta", invitees="Heidi")),
    ("Put this on my calendar please: Eta Retro, 2026-10-08, the notes say Sprint "
     "postmortem, it happens in Room 12, more info at https://example.com/eta, and "
     "Ivan should be invited.",
     dict(title="Eta Retro", date="2026-10-08", description="Sprint postmortem",
          location="Room 12", url="https://example.com/eta", invitees="Ivan")),
    ("Calendar entry needed: Theta Planning on 2026-10-15; description Quarterly "
     "roadmap; venue Room 3; url https://example.com/theta; guest Judy.",
     dict(title="Theta Planning", date="2026-10-15", description="Quarterly roadmap",
          location="Room 3", url="https://example.com/theta", invitees="Judy")),
    ("Create Iota Review, happening 2026-10-22, described as Design review, in "
     "Room 9, link https://example.com/iota, with Karl invited.",
     dict(title="Iota Review", date="2026-10-22", description="Design review",
          location="Room 9", url="https://example.com/iota", invitees="Karl")),
    ("I need an event: Lambda Sync 2026-10-29, summary Weekly sync, place Atrium, "
     "details https://example.com/lambda, attendee Lily.",
     dict(title="Lambda Sync", date="2026-10-29", description="Weekly sync",
          location="Atrium", url="https://example.com/lambda", invitees="Lily")),
    ("Schedule Mu Demo for 2026-11-05. It is a Feature demo, located in Hall C, "
     "url is https://example.com/mu, invite Mallory.",
     dict(title="Mu Demo", date="2026-11-05", description="Feature demo",
          location="Hall C", url="https://example.com/mu", invitees="Mallory")),
    ("New calendar item: Nu Audit, 2026-11-12, Compliance check, Suite 4, "
     "https://example.com/nu, guest Ned.",
     dict(title="Nu Audit", date="2026-11-12", description="Compliance check",
          location="Suite 4", url="https://example.com/nu", invitees="Ned")),
    ("Add Xi Workshop on 2026-11-19 with description Training day at Lab 2, see "
     "https://example.com/xi, invite Olivia.",
     dict(title="Xi Workshop", date="2026-11-19", description="Training day",
          location="Lab 2", url="https://example.com/xi", invitees="Olivia")),
    ("Please create Omicron Sync, date 2026-11-26, notes say Budget review, room "
     "Finance 1, link https://example.com/omicron, invite Pat.",
     dict(title="Omicron Sync", date="2026-11-26", description="Budget review",
          location="Finance 1", url="https://example.com/omicron", invitees="Pat")),
    ("Calendar: Pi Retro on 2026-12-03, Team retrospective, Room 5, "
     "https://example.com/pi, invite Quinn.",
     dict(title="Pi Retro", date="2026-12-03", description="Team retrospective",
          location="Room 5", url="https://example.com/pi", invitees="Quinn")),
    ("Add Rho Planning on 2026-12-10 for me. It is a Roadmap session, held in "
     "Room 21, details at https://example.com/rho, with Rosa invited.",
     dict(title="Rho Planning", date="2026-12-10", description="Roadmap session",
          location="Room 21", url="https://example.com/rho", invitees="Rosa")),
    ("New calendar item: Sigma Review, 2026-12-17, described as Architecture "
     "review, it happens in Hall D, link https://example.com/sigma, guest Sam.",
     dict(title="Sigma Review", date="2026-12-17", description="Architecture review",
          location="Hall D", url="https://example.com/sigma", invitees="Sam")),
    ("Schedule Tau Sync on 2027-01-07. Description: Weekly alignment. "
     "Location: Atrium North. Url: https://example.com/tau. Invite: Tara.",
     dict(title="Tau Sync", date="2027-01-07", description="Weekly alignment",
          location="Atrium North", url="https://example.com/tau", invitees="Tara")),
    ("I need an event: Upsilon Workshop 2027-01-14, it is a Hands-on lab, "
     "located at Lab 7, more info https://example.com/upsilon, invite Ugo.",
     dict(title="Upsilon Workshop", date="2027-01-14", description="Hands-on lab",
          location="Lab 7", url="https://example.com/upsilon", invitees="Ugo")),
    ("Create Phi Demo, happening 2027-01-21, described as Feature preview, in "
     "Room 14, link https://example.com/phi, with Pia invited.",
     dict(title="Phi Demo", date="2027-01-21", description="Feature preview",
          location="Room 14", url="https://example.com/phi", invitees="Pia")),
    ("Put this on my calendar please: Chi Audit, 2027-01-28, the notes say "
     "Compliance drill, it happens in Suite 12, details https://example.com/chi, "
     "and Chad should be invited.",
     dict(title="Chi Audit", date="2027-01-28", description="Compliance drill",
          location="Suite 12", url="https://example.com/chi", invitees="Chad")),
    ("Calendar entry needed: Psi Retro on 2027-02-04; description Incident "
     "review; venue Room 8; url https://example.com/psi; guest Petra.",
     dict(title="Psi Retro", date="2027-02-04", description="Incident review",
          location="Room 8", url="https://example.com/psi", invitees="Petra")),
    ("Schedule Omega Planning for 2027-02-11. It is a Quarterly sync, located "
     "in Hall A, url is https://example.com/omega, invite Otto.",
     dict(title="Omega Planning", date="2027-02-11", description="Quarterly sync",
          location="Hall A", url="https://example.com/omega", invitees="Otto")),
    ("Add Atlas Standup on 2027-02-18. Description: Daily check-in. "
     "Location: Standup Room 2. Link: https://example.com/atlas. Invite: Amy.",
     dict(title="Atlas Standup", date="2027-02-18", description="Daily check-in",
          location="Standup Room 2", url="https://example.com/atlas", invitees="Amy")),
    ("New calendar item: Boreas Review, 2027-02-25, Design critique, Room 11, "
     "https://example.com/boreas, guest Ben.",
     dict(title="Boreas Review", date="2027-02-25", description="Design critique",
          location="Room 11", url="https://example.com/boreas", invitees="Ben")),
    ("Please create Chronos Sync on 2027-03-04. Description: Timezone "
     "alignment. Location: Atrium South. Link: https://example.com/chronos. "
     "Invite: Cleo.",
     dict(title="Chronos Sync", date="2027-03-04", description="Timezone alignment",
          location="Atrium South", url="https://example.com/chronos", invitees="Cleo")),
    ("I need an event: Dione Workshop 2027-03-11, summary Onboarding lab, "
     "place Lab 4, details https://example.com/dione, attendee Dora.",
     dict(title="Dione Workshop", date="2027-03-11", description="Onboarding lab",
          location="Lab 4", url="https://example.com/dione", invitees="Dora")),
    ("Create Echo Demo, happening 2027-03-18, described as Live walkthrough, "
     "in Hall F, link https://example.com/echo, with Evan invited.",
     dict(title="Echo Demo", date="2027-03-18", description="Live walkthrough",
          location="Hall F", url="https://example.com/echo", invitees="Evan")),
    ("Calendar: Focalor Audit on 2027-03-25, Security patch review, Suite 3, "
     "https://example.com/focalor, invite Fay.",
     dict(title="Focalor Audit", date="2027-03-25", description="Security patch review",
          location="Suite 3", url="https://example.com/focalor", invitees="Fay")),
    ("Put this on my calendar please: Golem Sync, 2027-04-01, the notes say "
     "Infrastructure sync, it happens in Room 6, more info at "
     "https://example.com/golem, and Gus should be invited.",
     dict(title="Golem Sync", date="2027-04-01", description="Infrastructure sync",
          location="Room 6", url="https://example.com/golem", invitees="Gus")),
    ("Calendar entry needed: Helios Review on 2027-04-08; description Lighting "
     "design review; venue Room 17; url https://example.com/helios; guest Hana.",
     dict(title="Helios Review", date="2027-04-08", description="Lighting design review",
          location="Room 17", url="https://example.com/helios", invitees="Hana")),
    ("Schedule Icarus Demo for 2027-04-15. It is a Flight test demo, located "
     "in Hall B, url is https://example.com/icarus, invite Iris.",
     dict(title="Icarus Demo", date="2027-04-15", description="Flight test demo",
          location="Hall B", url="https://example.com/icarus", invitees="Iris")),
    ("Add Janus Workshop on 2027-04-22 with description Dual-track training at "
     "Lab 9, see https://example.com/janus, invite Jade.",
     dict(title="Janus Workshop", date="2027-04-22", description="Dual-track training",
          location="Lab 9", url="https://example.com/janus", invitees="Jade")),
    ("New calendar item: Kestrel Sync, 2027-04-29, Sprint coordination, Room "
     "23, https://example.com/kestrel, guest Kai.",
     dict(title="Kestrel Sync", date="2027-04-29", description="Sprint coordination",
          location="Room 23", url="https://example.com/kestrel", invitees="Kai")),
    ("Please create Lyra Retro on 2027-05-06, Team harmony retrospective, "
     "Room 19, https://example.com/lyra, invite Lena.",
     dict(title="Lyra Retro", date="2027-05-06", description="Team harmony retrospective",
          location="Room 19", url="https://example.com/lyra", invitees="Lena")),
]

# --------------------------------------------------------------- prompts

EXTRACTION_SYSTEM = (
    "You extract structured parameters from a natural-language request. "
    "You reply with a JSON object only."
)

_EXTRACTION_BODY = """Extract the six calendar-event fields from the request below as a JSON
object with exactly the keys title, date, description, location, url, invitees.

- Every value is a plain string, copied verbatim from the request.
- date is the YYYY-MM-DD date; url is the https:// link.
- invitees is a single string of names, never a list.
{extra}
Request: {goal}

Reply with ONLY the JSON object. No markdown fences, no explanation."""


def build_extraction_prompt(goal_text: str) -> list[dict]:
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _EXTRACTION_BODY.format(extra="", goal=goal_text)},
    ]


def build_retry_prompt(goal_text: str, errors: list[str]) -> list[dict]:
    extra = (
        "\nYour previous reply was rejected by the type check:\n"
        + "; ".join(errors)
        + "\nFix exactly these problems.\n"
    )
    return [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": _EXTRACTION_BODY.format(extra=extra, goal=goal_text)},
    ]


def binding_type_errors(got) -> list[str]:
    """The deploy boundary: six keys, each a non-empty string; invitees is a
    single string (a list is the classic model boundary failure)."""
    if not isinstance(got, dict):
        return ["reply is not a JSON object"]
    errs = []
    for field in FIELDS:
        if field not in got:
            errs.append(f"missing key {field}")
        elif not isinstance(got[field], str):
            errs.append(
                f"{field}: expected a single string, got {type(got[field]).__name__}"
                + (" (invitees must be one string of names, never a list)" if field == "invitees" and isinstance(got[field], list) else "")
            )
        elif not got[field].strip():
            errs.append(f"{field}: empty string")
    return errs


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

_LEADS = (
    "Please create ",
    "Calendar entry needed: ",
    "New calendar item: ",
    "Put this on my calendar please: ",
    "I need an event: ",
    "Schedule ",
    "Create ",
    "Add a ",
    "Add ",
    "Calendar: ",
)
_TITLE_CONNECTORS = {"on", "for", "happening", "with", "date", "please", "to", "in", "at", "and"}
_VENUE_TOKENS = ("Room", "Hall", "Suite", "Lab", "Atrium", "Stage", "Floor")


def _clean(segment: str) -> str:
    return segment.strip().strip("\"'").rstrip(".,;:").strip()


def mock_extract_fields(goal: str) -> dict:
    """Rule-based extraction of the six fields from a deploy-style goal.

    A deterministic stand-in for the extraction LLM: cascades of literal
    patterns over the goal prose, no access to the ground truth.
    """
    text = goal.strip()

    date = None
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        date = m.group(1)

    url = None
    m = re.search(r"(https://\S+)", text)
    if m:
        url = m.group(1).rstrip(".,")
        m2 = re.match(r"(https://[^\s,;]+)", url)
        url = m2.group(1) if m2 else url

    invitees = None
    for pattern in (
        r"(?:[Ii]nvite(?:es)?|[Gg]uest|[Aa]ttendee)\s*(?::|is)?\s*([A-Z][a-z]+)\b",
        r"with ([A-Z][a-z]+) invited",
        r"\b([A-Z][a-z]+) should be invited",
    ):
        m = re.search(pattern, text)
        if m:
            invitees = m.group(1)
            break

    # title: the head of the sentence with lead-in and connectors stripped
    head = text.split(date)[0] if date else text
    for lead in _LEADS:
        if head.startswith(lead):
            head = head[len(lead):]
            break
    changed = True
    while changed:
        changed = False
        head = head.strip()
        while head and head[-1] in ".,;:":
            head = head[:-1].rstrip()
            changed = True
        tokens = head.split(" ")
        if len(tokens) > 1 and tokens[-1] in _TITLE_CONNECTORS:
            head = " ".join(tokens[:-1])
            changed = True
    title = head.strip() or None

    def first(patterns):
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return _clean(m.group(1))
        return None

    description = first(
        [
            r"[Dd]escribed as ([^,;.]+?)(?=\s+at\s|[,;.]|$)",
            r"[Dd]escription\s*(?::|is)?\s*([^,;.]+?)(?=\s+at\s|[,;.]|$)",
            r"notes say ([^,;.]+?)(?=\s+at\s|[,;.]|$)",
            r"summary ([^,;.]+?)(?=\s+at\s|[,;.]|$)",
            r"[Ii]t is an? ([^,;.]+?)(?=\s+at\s|[,;.]|$)",
        ]
    )
    location = first(
        [
            r"[Ll]ocation\s*(?::|is)?\s*(?:in\s+)?([^,;.]+)",
            r"\bvenue\s+([^,;.]+)",
            r"\bplace\s+([^,;.]+)",
            r"located in ([^,;.]+)",
            r"happens in ([^,;.]+)",
            r"\bin\s+([^,;.]+)",
            r"\bat\s+([^,;.]+)",
            r"\broom\s+([^,;.]+)",
        ]
    )
    if location is None:  # a bare venue segment, e.g. "Suite 4"
        for segment in re.split(r"[,;.]", text):
            seg = segment.strip()
            if seg and any(re.search(rf"\b{token}\b", seg) for token in _VENUE_TOKENS) and "http" not in seg:
                location = _clean(seg)
                break
    if description is None:  # the first unclaimed segment after the date
        for segment in re.split(r"[,;.]", text):
            seg = segment.strip()
            if (
                seg
                and "http" not in seg
                and seg != location
                and seg != title
                and "calendar" not in seg.lower()
                and not re.match(r"(?:guest|invite|attendee)\b", seg.lower())
                and (date is None or date not in seg)
            ):
                description = _clean(seg)
                break

    fields = {
        "title": title,
        "date": date,
        "description": description,
        "location": location,
        "url": url,
        "invitees": invitees,
    }
    return {k: v for k, v in fields.items() if v}


class _MockExtractionCompletions:
    def create(self, *, model=None, messages=None, **_kwargs):
        last_user = ""
        for message in reversed(messages or []):
            if message.get("role") == "user":
                content = message.get("content")
                last_user = content if isinstance(content, str) else json.dumps(content)
                break
        m = re.search(r"Request: (.+)", last_user)
        fields = mock_extract_fields(m.group(1)) if m else {}
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

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=_MockExtractionCompletions())


# ------------------------------------------------------------------- the chain


def run_single_use(program, goal: str, expected: dict, client, model: str, runner: BindingRunner) -> dict:
    """One deployment use: extract -> type check (one retry) -> program -> oracle."""
    t0 = time.time()
    tokens = 0
    cost = 0.0
    retries = 0
    error_type = None
    extracted = None

    def call(messages: list[dict]) -> str:
        nonlocal tokens, cost
        response = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
        usage = GuiAgent._usage(response)
        tokens += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        cost += usage.get("cost_usd") or 0.0
        return response.choices[0].message.content or ""

    got = parse_binding_json(call(build_extraction_prompt(goal)))
    errors = binding_type_errors(got)
    if errors:  # one bounded retry with the errors in the prompt
        retries = 1
        got = parse_binding_json(call(build_retry_prompt(goal, errors)))
        errors = binding_type_errors(got)

    success = False
    if got is None:
        error_type = "extraction_json"
    elif errors:
        error_type = "type_check"  # boundary reject: a real episode falls back reactive
    else:
        extracted = got
        outcome = runner.run(program, got, judge_event=expected)
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
        "wall_s": round(time.time() - t0, 2),
    }


def run_deployment(
    program,
    uses: list[tuple[str, dict]],
    client,
    model: str,
    base_url: str,
    layout: str,
    headless: bool = True,
) -> dict:
    records: list[dict] = []
    with BindingRunner(base_url, layout, headless=headless) as runner:
        for goal, expected in uses:
            record = run_single_use(program, goal, expected, client, model, runner)
            records.append(record)
            tag = record["error_type"] or ("OK" if record["success"] else "FAIL")
            print(f"  use {len(records):>2}: {record['tokens']:>5} tok (r{record['retries']}) {tag}")
    tokens = [r["tokens"] for r in records]
    return {
        "model": model,
        "n": len(records),
        "uses": records,
        "success_count": sum(1 for r in records if r["success"]),
        "success_rate": (sum(1 for r in records if r["success"]) / len(records)) if records else None,
        "d_tokens_mean": (sum(tokens) / len(tokens)) if tokens else None,
        "total_tokens": sum(tokens),
        "total_cost_usd": round(sum(r["cost_usd"] for r in records), 8),
        "record_type": "deploy",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", default=None, help="compiled family program .py")
    parser.add_argument("--trajectory", default=None, help="compile from this trajectory first")
    parser.add_argument("--mock", action="store_true", help="MockCompiler + MockExtractor: fully offline")
    parser.add_argument("--model", default=None, help="OpenRouter model id for extraction")
    parser.add_argument("--layout", default="wizard")
    parser.add_argument("--uses", type=int, default=10, help="number of deployment uses")
    parser.add_argument("--out", default=None, help="output JSON path (default under experimental-results/guiexp/)")
    parser.add_argument("--show", action="store_true", help="run Chromium headed")
    args = parser.parse_args()

    if not args.program and not args.trajectory:
        parser.error("need --program or --trajectory (to compile first)")

    from .app_server import AppServer

    compile_usage = {}
    compile_cost = 0.0
    model = args.model or ("mock" if args.mock else "openai/gpt-4o-mini")
    if args.program:
        _module, program = program_from_path(args.program)
        program_path = str(Path(args.program).resolve())
    else:
        trajectory = Path(args.trajectory)
        _steps, final = load_trajectory(trajectory)
        server = AppServer(args.layout)
        server.start()
        try:
            if args.mock:
                result = MockCompiler().compile(trajectory, layout=args.layout)
            else:
                result = compile_trajectory(model, trajectory, layout=args.layout)
        finally:
            server.stop()
        compile_usage = result["usage"]
        compile_cost = result["cost_usd"] or 0.0
        out_dir = Path(args.out).parent if args.out else (
            DEFAULT_OUT_ROOT / f"deploy_{model.replace('/', '-')}_{args.layout}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        program_path = str(out_dir / "family_program.py")
        Path(program_path).write_text(result["program_source"])
        from .program_runtime import program_from_source

        _module, program = program_from_source(result["program_source"])

    uses = DEPLOY_USES[: max(1, min(args.uses, len(DEPLOY_USES)))]
    client = MockExtractor() if (args.mock and not args.model) else None
    if client is None:
        from .compiler import _openai_client

        client = _openai_client()

    server = AppServer(args.layout)
    base_url = server.start()
    try:
        summary = run_deployment(
            program, uses, client, model, base_url, args.layout, headless=not args.show
        )
    finally:
        server.stop()

    summary.update(
        {
            "program_path": program_path,
            "layout": args.layout,
            "compile_usage": compile_usage or None,
            "compile_cost_usd": compile_cost,
        }
    )
    out = Path(args.out) if args.out else (
        DEFAULT_OUT_ROOT / f"deploy_{model.replace('/', '-')}_{args.layout}" / "deploy.json"
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
