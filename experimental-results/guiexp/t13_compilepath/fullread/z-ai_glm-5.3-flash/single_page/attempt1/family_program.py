import re
from datetime import date

PARAMS_SCHEMA = {
    "title": {
        "type": "string",
        "required": True,
        "description": "Title of the calendar event.",
    },
    "date": {
        "type": "string",
        "required": True,
        "format": "YYYY-MM-DD",
        "description": "Date of the calendar event.",
    },
    "description": {
        "type": "string",
        "required": True,
        "description": "Description of the calendar event.",
    },
    "location": {
        "type": "string",
        "required": True,
        "description": "Location of the calendar event.",
    },
    "url": {
        "type": "string",
        "required": True,
        "description": "URL associated with the calendar event.",
    },
    "invitees": {
        "type": "string",
        "required": True,
        "description": "Invitees for the calendar event.",
    },
}

_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_HEADING_RE = re.compile(r"^([A-Z][a-z]+)\s+(\d{4})$")


def _month_index(label: str) -> int:
    """Convert a 'Month YYYY' heading into an absolute month index."""
    m = _HEADING_RE.match(label.strip())
    if not m:
        raise RuntimeError(f"Unexpected calendar heading: {label!r}")
    month_name, year = m.group(1), int(m.group(2))
    if month_name not in _MONTHS:
        raise RuntimeError(f"Unknown month in heading: {label!r}")
    return year * 12 + (_MONTHS.index(month_name) + 1)


def program(page, binding: dict, base_url: str) -> bool:
    """Create a calendar event in OpenApps from the values in ``binding``."""
    # ---- validate binding -------------------------------------------------
    missing = [
        k for k in PARAMS_SCHEMA
        if not isinstance(binding.get(k), str) or not binding[k].strip()
    ]
    if missing:
        raise ValueError(f"Missing or invalid binding keys: {missing}")

    target_date = date.fromisoformat(binding["date"].strip())
    target_idx = target_date.year * 12 + target_date.month

    # ---- open the calendar app -------------------------------------------
    page.goto(base_url + "/calendar", wait_until="domcontentloaded")

    heading = page.get_by_role("heading", name=_HEADING_RE)
    heading.first.wait_for(state="visible")

    prev_btn = page.get_by_role("button", name="< Prev")
    next_btn = page.get_by_role("button", name="Next >")

    # ---- navigate the month view to the target month ---------------------
    for _ in range(240):
        current_idx = _month_index(heading.first.inner_text())
        if current_idx == target_idx:
            break
        if current_idx > target_idx:
            prev_btn.click()
        else:
            next_btn.click()
        page.wait_for_timeout(200)
    else:
        raise RuntimeError(f"Could not navigate calendar to {binding['date']}")

    # ---- open the create-event form ---------------------------------------
    add_event_btn = page.get_by_role("button", name="Add Event")
    add_event_btn.scroll_into_view_if_needed()
    add_event_btn.click()

    page.get_by_role("heading", name="Create New Event").wait_for(state="visible")

    # ---- fill the upper half of the form ----------------------------------
    page.get_by_role("textbox", name="Event title").fill(binding["title"])
    page.get_by_role("textbox", name="Event date, YYYY-MM-DD").fill(binding["date"])
    page.get_by_role("textbox", name="Event description").fill(binding["description"])
    page.get_by_role("textbox", name="Event URL").fill(binding["url"])

    # ---- reveal the lower half of the form --------------------------------
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(200)

    page.get_by_role("textbox", name="Event invitees").fill(binding["invitees"])
    page.get_by_role("textbox", name="Event location").fill(binding["location"])

    # ---- submit ------------------------------------------------------------
    page.get_by_role("button", name="Submit").click()

    page.wait_for_url(lambda url: url.rstrip("/").endswith("/calendar"))
    return True
