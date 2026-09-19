import re
import time
from datetime import datetime

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
        "description": "Date of the event (parsed to locate the right month).",
    },
    "description": {
        "type": "string",
        "required": True,
        "description": "Description of the event.",
    },
    "location": {
        "type": "string",
        "required": True,
        "description": "Location of the event.",
    },
    "url": {
        "type": "string",
        "required": True,
        "format": "uri",
        "description": "URL associated with the event.",
    },
    "invitees": {
        "type": "string",
        "required": True,
        "description": "Invitee name(s) for the event.",
    },
}

_REQUIRED_KEYS = ("title", "date", "description", "location", "url", "invitees")

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
_MONTH_YEAR_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$"
)


def _parse_date(value):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError("Cannot parse date value: %r" % (value,))


def _read_calendar_month(page):
    """Return (month_number, year) shown in the calendar heading, or None."""
    try:
        headings = page.get_by_role("heading").all()
    except Exception:
        return None
    for h in headings:
        try:
            text = h.inner_text().strip()
        except Exception:
            continue
        m = _MONTH_YEAR_RE.match(text)
        if m:
            return _MONTH_NAMES[m.group(1).lower()], int(m.group(2))
    return None


def _wait_for_calendar(page, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _read_calendar_month(page) is not None:
            return
        page.wait_for_timeout(150)
    raise RuntimeError("Calendar view did not load")


def _navigate_to_month(page, month, year, timeout=60.0):
    """Step through months with the < Prev / Next > buttons until target month."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = _read_calendar_month(page)
        if current is None:
            page.wait_for_timeout(150)
            continue
        diff = (year - current[1]) * 12 + (month - current[0])
        if diff == 0:
            return
        if diff < 0:
            page.get_by_role("button", name="< Prev").click()
        else:
            page.get_by_role("button", name="Next >").click()
        change_deadline = time.time() + 5.0
        while time.time() < change_deadline:
            page.wait_for_timeout(150)
            now = _read_calendar_month(page)
            if now is not None and now != current:
                break
    raise RuntimeError("Unable to navigate calendar to %04d-%02d" % (year, month))


def _wait_for_step(page, step_label, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            page.get_by_role("heading", name=step_label).wait_for(
                state="visible", timeout=1000
            )
            return
        except Exception:
            continue
    raise RuntimeError("Wizard step not reached: %s" % step_label)


def program(page, binding: dict, base_url: str) -> bool:
    missing = [
        k for k in _REQUIRED_KEYS
        if k not in binding or not str(binding[k]).strip()
    ]
    if missing:
        raise ValueError("Missing/empty binding keys: %s" % ", ".join(missing))

    title = str(binding["title"])
    description = str(binding["description"])
    location = str(binding["location"])
    url_value = str(binding["url"])
    invitees = str(binding["invitees"])
    parsed = _parse_date(binding["date"])
    date_iso = parsed.strftime("%Y-%m-%d")

    base = base_url if base_url.endswith("/") else base_url + "/"
    page.goto(base, wait_until="domcontentloaded")

    # Open the OpenCalendar app from the home screen.
    page.get_by_role("link", name="OpenCalendar").first.click()
    _wait_for_calendar(page)

    # Bring the calendar to the month containing the event date.
    _navigate_to_month(page, parsed.month, parsed.year)

    # Open the create-event wizard (button sits below the fold).
    add_event = page.get_by_role("button", name="Add Event")
    add_event.scroll_into_view_if_needed()
    add_event.click()

    # Step 1 of 3: title + date.
    _wait_for_step(page, "Create New Event (1 of 3)")
    page.get_by_role("textbox", name="Event title").fill(title)
    page.get_by_role("textbox", name="Event date, YYYY-MM-DD").fill(date_iso)
    page.get_by_role("button", name="Next").click()

    # Step 2 of 3: description + location.
    _wait_for_step(page, "Create New Event (2 of 3)")
    page.get_by_role("textbox", name="Event description").fill(description)
    page.get_by_role("textbox", name="Event location").fill(location)
    page.get_by_role("button", name="Next").click()

    # Step 3 of 3: url + invitees, then submit.
    _wait_for_step(page, "Create New Event (3 of 3)")
    page.get_by_role("textbox", name="Event URL").fill(url_value)
    page.get_by_role("textbox", name="Event invitees").fill(invitees)
    page.get_by_role("button", name="Create").click()

    # Confirm the wizard closed and we are back on the calendar view.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if _read_calendar_month(page) is not None:
            return True
        page.wait_for_timeout(200)
    raise RuntimeError("Event creation did not complete (calendar view not shown)")
