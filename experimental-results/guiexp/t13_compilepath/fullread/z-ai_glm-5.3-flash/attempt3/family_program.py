import re
import time

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_LOOKUP = {name.lower(): idx + 1 for idx, name in enumerate(MONTH_NAMES)}

_MONTH_HEADING_RE = re.compile(
    r"^(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}$"
)

PARAMS_SCHEMA = {
    "title": {
        "type": "string", "required": True,
        "description": "Title of the calendar event (wizard step 1).",
    },
    "date": {
        "type": "string", "required": True, "format": "YYYY-MM-DD",
        "description": "Event date in YYYY-MM-DD form (wizard step 1); also drives month navigation on the calendar.",
    },
    "description": {
        "type": "string", "required": True,
        "description": "Description of the event (wizard step 2).",
    },
    "location": {
        "type": "string", "required": True,
        "description": "Location of the event (wizard step 2).",
    },
    "url": {
        "type": "string", "required": True,
        "description": "URL attached to the event (wizard step 3).",
    },
    "invitees": {
        "type": "string", "required": True,
        "description": "Invitee name(s) for the event (wizard step 3).",
    },
}


def _validate_binding(binding):
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")
    for key in PARAMS_SCHEMA:
        if key not in binding:
            raise ValueError(f"binding is missing required key: {key!r}")
        value = binding[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"binding value for {key!r} must be a non-empty string")


def _parse_date(value):
    match = re.match(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$", str(value))
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return year, month, day


def _month_heading(page):
    return page.get_by_role("heading", name=_MONTH_HEADING_RE).first


def _navigate_to_month(page, year, month, max_clicks=240):
    """Click '< Prev' / 'Next >' until the visible month matches the target."""
    target = f"{MONTH_NAMES[month - 1]} {year}"
    heading = _month_heading(page)
    heading.wait_for(state="visible", timeout=15000)
    deadline = time.time() + 120
    clicks = 0
    while True:
        current = heading.inner_text().strip()
        if current == target:
            return
        match = re.match(r"^([A-Za-z]+)\s+(\d{4})$", current)
        if not match:
            raise RuntimeError(f"Unparseable calendar month heading: {current!r}")
        cur_month = _MONTH_LOOKUP.get(match.group(1).lower())
        if cur_month is None:
            raise RuntimeError(f"Unknown month name in heading: {current!r}")
        cur_year = int(match.group(2))
        if clicks >= max_clicks or time.time() > deadline:
            raise RuntimeError(f"Could not navigate to {target!r}; stuck at {current!r}")
        if (cur_year, cur_month) > (year, month):
            page.get_by_role("button", name="< Prev").click()
        else:
            page.get_by_role("button", name="Next >").click()
        clicks += 1
        # Wait until the visible month actually changes.
        changed = False
        sub_deadline = time.time() + 5
        while time.time() < sub_deadline:
            try:
                if heading.inner_text().strip() != current:
                    changed = True
                    break
            except Exception:
                pass
            page.wait_for_timeout(100)
        if not changed:
            raise RuntimeError(
                f"Calendar month did not change after prev/next click (still {current!r})"
            )


def program(page, binding: dict, base_url: str) -> bool:
    """Create a calendar event in OpenApps with the values from `binding`.

    Flow (mirrors the recorded episode):
      home -> OpenCalendar -> (navigate to the event's month) -> Add Event
      -> wizard step 1 (title, date) -> step 2 (description, location)
      -> step 3 (url, invitees) -> Create -> back on /calendar.
    """
    _validate_binding(binding)
    title = binding["title"]
    date_value = binding["date"]
    description = binding["description"]
    location = binding["location"]
    url_value = binding["url"]
    invitees = binding["invitees"]

    parsed_date = _parse_date(date_value)

    # 1. Home screen -> open the OpenCalendar app.
    page.goto(base_url.rstrip("/") + "/")
    page.get_by_role("link", name="OpenCalendar").first.click()
    _month_heading(page).wait_for(state="visible", timeout=15000)

    # 2. Bring the calendar to the month of the requested event.
    if parsed_date is not None:
        year, month, _day = parsed_date
        _navigate_to_month(page, year, month)

    # 3. Open the create-event wizard.
    add_event = page.get_by_role("button", name="Add Event")
    try:
        add_event.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    add_event.click()
    page.get_by_role("heading", name="Create New Event (1 of 3)").wait_for(
        state="visible", timeout=15000
    )

    # 4. Wizard step 1: title + date.
    page.get_by_role("textbox", name="Event title").fill(title)
    page.get_by_role("textbox", name=re.compile(r"^Event date")).fill(date_value)
    page.get_by_role("button", name="Next").click()
    page.get_by_role("heading", name="Create New Event (2 of 3)").wait_for(
        state="visible", timeout=15000
    )

    # 5. Wizard step 2: description + location.
    page.get_by_role("textbox", name="Event description").fill(description)
    page.get_by_role("textbox", name="Event location").fill(location)
    page.get_by_role("button", name="Next").click()
    page.get_by_role("heading", name="Create New Event (3 of 3)").wait_for(
        state="visible", timeout=15000
    )

    # 6. Wizard step 3: URL + invitees ('Recurring' left untouched, as recorded).
    page.get_by_role("textbox", name="Event URL").fill(url_value)
    page.get_by_role("textbox", name="Event invitees").fill(invitees)
    page.get_by_role("button", name="Create").click()

    # 7. Flow complete: the app returns to the calendar view.
    page.wait_for_url(re.compile(r"/calendar/?$"), timeout=15000)
    page.get_by_role("heading", name="Create New Event (1 of 3)").wait_for(
        state="hidden", timeout=15000
    )
    _month_heading(page).wait_for(state="visible", timeout=15000)
    return True
