"""The five prompt conditions, ported from openapps-exp's discovery_cost.py.

The ladder prices progressively more interface knowledge, holding the task
constant:

    discover  goal + starting URL only
    told      + the exact interface procedure (steps, fields, screen order,
              at form-field/button granularity) -- but NO instance values
    mid       + structure only (screen count, field grouping/order), no
              controls, no labels
    skill     + a compressed family-level skill doc (~240 tokens): task and
              trigger, the six fields, honest layout-variety note, cautions;
              no selectors, URLs, field order or screen count
    floor     no task at all; the harness shows one trivial observation and
              the episode exits (pure harness-overhead baseline)

All conditions except floor share the same goal block, so a difference in
tokens between two conditions is a difference in knowledge, not in task.
"""

from __future__ import annotations

CONDITIONS = ("discover", "told", "mid", "skill", "floor")

GOAL_TEMPLATE = (
    "Add an event to the calendar with exactly these values:\n"
    "  title: {title}\n"
    "  date: {date}\n"
    "  description: {description}\n"
    "  location: {location}\n"
    "  url: {url}\n"
    "  invitees: {invitees}\n"
)

COMMON_TEMPLATE = (
    "You are operating the web application at {base}.\n"
    "\n"
    "{goal}"
    "\n"
    "Rules:\n"
    "- Drive the application through its browser UI using the actions\n"
    "  provided (click / fill / select / scroll / goto / press / done).\n"
    "- Do not call the application's endpoints directly and do not write to\n"
    "  its database; going around the UI does not count as completing the\n"
    "  task.\n"
    "- When the event has been created, answer done().\n"
)

# The told arm: the exact procedure at form-field/button granularity. It names
# screens, fields in the order they are met, and the buttons between them, but
# never the instance's parameter values (those live only in the goal block).
TOLD_PROCEDURE = {
    "single_page": (
        "The interaction procedure for this form is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Go to the create-event form at {base}/calendar/create_event.\n"
        "   All six fields are on one screen.\n"
        "2. Fill the fields in the order they appear on the form: Title,\n"
        "   Date, Description, URL, Invitees, Location.\n"
        "3. Click the Submit button.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "sectioned": (
        "The interaction procedure for this form is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Go to the create-event form at {base}/calendar/create_event.\n"
        "   Title and Date are visible first; the remaining fields are\n"
        "   hidden behind a disclosure.\n"
        "2. Fill the Title field, then the Date field.\n"
        "3. Click the More details button; the Description, Location, URL\n"
        "   and Invitees fields appear.\n"
        "4. Fill Description, Location, URL, Invitees, in that order.\n"
        "5. Click the Submit button.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "wizard": (
        "The interaction procedure for this form is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Go to the create-event form at {base}/calendar/create_event.\n"
        "   The form spans three screens.\n"
        "2. On screen 1, fill the Title field and the Date field, then\n"
        "   click the Next button.\n"
        "3. On screen 2, fill the Description field and the Location field,\n"
        "   then click the Next button.\n"
        "4. On screen 3, fill the URL field and the Invitees field, then\n"
        "   click the Create button.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
}

# The mid arm: structure only. Ported from discovery_cost.MID_STEPS: how many
# screens and which fields in which grouping, but no controls, no labels.
MID_STEPS = {
    "single_page": (
        "- open the calendar app\n"
        "- start creating a new event\n"
        "- all six fields (title, date, description, location, url, invitees)\n"
        "  are on one screen; fill them\n"
        "- submit the form"
    ),
    "sectioned": (
        "- open the calendar app\n"
        "- start creating a new event\n"
        "- the form reveals its fields in sections; the six fields (title,\n"
        "  date, description, location, url, invitees) are grouped, and each\n"
        "  section must be completed before the next appears\n"
        "- submit the form"
    ),
    "wizard": (
        "- open the calendar app\n"
        "- start creating a new event\n"
        "- the form spans three screens: title and date first, then\n"
        "  description, location and url, then invitees; move through them\n"
        "  with each screen's continue control\n"
        "- on the last screen, submit the form"
    ),
}

MID_SUFFIX = (
    "The structure of this form is known, but its controls are not. The\n"
    "steps are:\n"
    "{steps}\n"
    "The exact controls and labels are not specified; locate each named\n"
    "field or control yourself. No other exploration is needed."
)

# The skill arm: a family-level skill entry, the kind a workflow memory or an
# induced skill actually stores. Ported verbatim from discovery_cost.SKILL_DOC:
# it names the task, the six fields, and the cautions a site skill would
# carry -- no selectors, no URL paths, no field order, and no screen count
# for the form at hand, only the honest note that layouts vary.
SKILL_DOC = (
    "Skill: add an event to the OpenApps calendar\n"
    "\n"
    "Use when a request asks to add, create, or schedule an event on this\n"
    "app's calendar.\n"
    "\n"
    "1. From the starting URL, find the calendar's create-event control and\n"
    "   open the form.\n"
    "2. Record the six details of the event: title, date, description,\n"
    "   location, url, and invitees. Copy the requested values exactly;\n"
    "   invitees is one string of names.\n"
    "3. The form's layout differs between installations: some show all\n"
    "   fields at once, some group them in sections, and some split them\n"
    "   over several screens with a continue control. Work with whichever\n"
    "   layout the form presents.\n"
    "4. Submit and make sure the event is on the calendar before reporting\n"
    "   success.\n"
    "\n"
    "Cautions: drive the browser UI rather than the app's endpoints; the\n"
    "date field expects the format shown in the request; every field is\n"
    "required."
)

SKILL_SUFFIX = (
    "A skill entry from memory may help. It was written for this family of\n"
    "tasks, not for this specific form, so parts may not match what you see:\n"
    "\n"
    "{doc}\n"
    "\n"
    "Follow it where it applies and work out the rest from the interface."
)

# The floor probe: no task, no URL. The harness shows one trivial observation
# and the episode exits; whatever tokens this costs is harness overhead, not
# task work, and must be subtracted from every condition before shares are
# computed.
FLOOR_PROMPT = "Reply with exactly done() and nothing else. Do not take any other action."


def goal_text(event: dict) -> str:
    """The shared goal block: the task and its six parameter values."""
    return GOAL_TEMPLATE.format(**event)


def told_procedure(layout: str, base_url: str) -> str:
    return TOLD_PROCEDURE[layout].format(base=base_url.rstrip("/"))


def mid_steps(layout: str) -> str:
    return MID_STEPS[layout]


def approx_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token heuristic)."""
    return len(text) // 4


def build_prompt(condition: str, layout: str, event: dict, base_url: str) -> str:
    """The full user-side prompt for one condition. floor has no task."""
    if condition == "floor":
        return FLOOR_PROMPT
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    prompt = COMMON_TEMPLATE.format(base=base_url, goal=goal_text(event))
    if condition == "told":
        prompt += "\n" + told_procedure(layout, base_url) + "\n"
    elif condition == "mid":
        prompt += "\n" + MID_SUFFIX.format(steps=mid_steps(layout)) + "\n"
    elif condition == "skill":
        prompt += "\n" + SKILL_SUFFIX.format(doc=SKILL_DOC) + "\n"
    return prompt
