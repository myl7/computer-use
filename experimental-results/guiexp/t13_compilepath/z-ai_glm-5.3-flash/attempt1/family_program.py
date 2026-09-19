import re
from datetime import datetime

PARAMS_SCHEMA = {
    "title": {"type": "string", "required": True, "description": "Title of the calendar event."},
    "date": {"type": "string", "required": True, "description": "Date of the event (e.g. 2026-06-15)."},
    "description": {"type": "string", "required": True, "description": "Description of the event."},
    "location": {"type": "string", "required": True, "description": "Location of the event."},
    "url": {"type": "string", "required": True, "description": "URL associated with the event."},
    "invitees": {"type": "string", "required": True, "description": "Invitees for the event."},
}

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y",
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
)


def _parse_date(date_str):
    s = str(date_str).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{4})\D+(\d{1,2})", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return datetime(year, month, 1)
    return None


def _visible_text_fields(page):
    return page.locator(
        "input:not([type='hidden']):not([type='checkbox']):not([type='radio'])"
        ":not([type='submit']):not([type='button']):visible, textarea:visible"
    )


def _fill_field(page, keywords, index, value):
    for kw in keywords:
        for getter in (page.get_by_label, page.get_by_placeholder):
            try:
                loc = getter(re.compile(kw, re.I))
                if loc.count() > 0:
                    loc.first.fill(value)
                    return
            except Exception:
                continue
        try:
            loc = page.get_by_role("textbox", name=re.compile(kw, re.I))
            if loc.count() > 0:
                loc.first.fill(value)
                return
        except Exception:
            continue
    fields = _visible_text_fields(page)
    if fields.count() <= index:
        raise RuntimeError("Could not locate text field for keywords %r" % (keywords,))
    fields.nth(index).wait_for(state="visible", timeout=10000)
    fields.nth(index).fill(value)


def _click_button(page, pattern):
    btn = page.get_by_role("button", name=re.compile(pattern, re.I))
    if btn.count() == 0:
        btn = page.locator("button, [role='button'], a").filter(
            has_text=re.compile(pattern, re.I)
        )
    if btn.count() == 0:
        raise RuntimeError("No button matching %r found" % pattern)
    btn.first.click()
    return btn.first


def _wait_url(page, pattern):
    page.wait_for_url(lambda u: re.search(pattern, u) is not None, timeout=15000)


def program(page, binding: dict, base_url: str) -> bool:
    keys = ("title", "date", "description", "location", "url", "invitees")
    for key in keys:
        if key not in binding:
            raise ValueError("Binding is missing key %r" % key)
        if not str(binding[key]).strip():
            raise ValueError("Binding value for %r is empty" % key)

    title = binding["title"]
    date = binding["date"]
    description = binding["description"]
    location = binding["location"]
    url = binding["url"]
    invitees = binding["invitees"]

    # Open the calendar app.
    page.goto(base_url + "/calendar")
    page.wait_for_load_state("load")

    # Bring the calendar view to the month of the event date
    # (generalises the repeated previous-month clicks of the recording).
    parsed = _parse_date(date)
    if parsed is not None:
        page.goto(base_url + "/calendar/calendar_content/%d/%d" % (parsed.year, parsed.month))
        page.wait_for_load_state("load")

    # Open the create-event form.
    _click_button(page, r"create\s*(an?\s*)?(event|meeting)|new\s*event|add\s*event|create|new|add")
    _wait_url(page, r"/create_event$")

    # Wizard step 1: title + date.
    _fill_field(page, ["title"], 0, title)
    _fill_field(page, ["date"], 1, date)
    _click_button(page, r"^next$|next")
    _wait_url(page, r"/create_event/step2$")

    # Wizard step 2: description + location.
    _fill_field(page, ["description", "desc"], 0, description)
    _fill_field(page, ["location"], 1, location)
    _click_button(page, r"^next$|next")
    _wait_url(page, r"/create_event/step3$")

    # Wizard step 3: url + invitees, then submit.
    _fill_field(page, ["url", "link"], 0, url)
    _fill_field(page, ["invitee", "guest", "attendee", "participant", "people"], 1, invitees)
    _click_button(page, r"create|save|submit|finish|done")

    # Flow completes back on the calendar page.
    _wait_url(page, r"/calendar/?(\?|#|$)")
    page.wait_for_load_state("load")
    return True
