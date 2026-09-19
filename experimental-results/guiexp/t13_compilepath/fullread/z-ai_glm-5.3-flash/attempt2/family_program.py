import re
from datetime import datetime

PARAMS_SCHEMA = {
    "title": {
        "type": "string",
        "required": True,
        "description": "Event title displayed on the calendar.",
    },
    "date": {
        "type": "string",
        "required": True,
        "format": "date",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
        "description": "Event date in YYYY-MM-DD format.",
    },
    "description": {
        "type": "string",
        "required": True,
        "description": "Free-text description of the event.",
    },
    "location": {
        "type": "string",
        "required": True,
        "description": "Room or place where the event takes place.",
    },
    "url": {
        "type": "string",
        "required": True,
        "format": "uri",
        "description": "Related URL for the event.",
    },
    "invitees": {
        "type": "string",
        "required": True,
        "description": "Invitee name(s) for the event.",
    },
}


def program(page, binding: dict, base_url: str) -> bool:
    """Create a calendar event in OpenApps via the create-event wizard UI.

    Fills all three wizard steps (title/date -> description/location ->
    url/invitees) using only the browser UI, then verifies the app returned
    to the calendar view.
    """
    keys = ("title", "date", "description", "location", "url", "invitees")
    for key in keys:
        if key not in binding:
            raise ValueError(f"binding is missing required key: {key}")
        value = binding[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"binding[{key!r}] must be a non-empty string")

    title = binding["title"]
    date = binding["date"]
    description = binding["description"]
    location = binding["location"]
    url = binding["url"]
    invitees = binding["invitees"]

    # Fail fast on a malformed date instead of inside the form.
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"binding['date'] must be YYYY-MM-DD, got {date!r}"
        ) from exc

    root = base_url.rstrip("/")

    # Enter the calendar app from the home screen.
    page.goto(root + "/")
    page.get_by_role("link", name="OpenCalendar").click()
    page.wait_for_url(re.compile(r"/calendar/?$"))

    # Open the create-event form (button sits below the month grid;
    # scroll it into view first, as in the recorded episode).
    add_event = page.get_by_role("button", name="Add Event")
    add_event.scroll_into_view_if_needed()
    add_event.click()
    page.wait_for_url(re.compile(r"/create_event/?$"))

    # ---- Step 1 of 3: Title + Date ----
    page.get_by_role("textbox", name="Event title").fill(title)
    page.get_by_role("textbox", name="Event date, YYYY-MM-DD").fill(date)
    page.get_by_role("button", name="Next").click()
    page.wait_for_url(re.compile(r"/create_event/step2/?$"))

    # ---- Step 2 of 3: Description + Location ----
    page.get_by_role("textbox", name="Event description").fill(description)
    page.get_by_role("textbox", name="Event location").fill(location)
    page.get_by_role("button", name="Next").click()
    page.wait_for_url(re.compile(r"/create_event/step3/?$"))

    # ---- Step 3 of 3: URL + Invitees (Recurring left at 'Not Recurring') ----
    page.get_by_role("textbox", name="Event URL").fill(url)
    page.get_by_role("textbox", name="Event invitees").fill(invitees)
    page.get_by_role("button", name="Create").click()

    # The wizard returns to the calendar view once the event is created.
    page.wait_for_url(re.compile(r"/calendar/?$"))
    page.get_by_role("heading", name="OpenCalendar").wait_for(state="visible")

    return True
