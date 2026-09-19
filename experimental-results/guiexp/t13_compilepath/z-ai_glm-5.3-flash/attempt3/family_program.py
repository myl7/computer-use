import re
import time

PARAMS_SCHEMA = {
    "title": {
        "type": "string",
        "required": True,
        "description": "Event title, entered into the 'title' textbox on wizard step 1.",
    },
    "date": {
        "type": "string",
        "required": True,
        "description": "Event date, entered into the 'date' textbox on wizard step 1.",
    },
    "description": {
        "type": "string",
        "required": True,
        "description": "Event description, entered into the 'description' textbox on wizard step 2.",
    },
    "location": {
        "type": "string",
        "required": True,
        "description": "Event location, entered into the 'location' textbox on wizard step 2.",
    },
    "url": {
        "type": "string",
        "required": True,
        "description": "Event URL, entered into the 'url' textbox on wizard step 3.",
    },
    "invitees": {
        "type": "string",
        "required": True,
        "description": "Event invitees, entered into the 'invitees' textbox on wizard step 3.",
    },
}

_FIELDS = ("title", "date", "description", "location", "url", "invitees")

_NEXT_PATTERNS = [r"^\s*next\s*$", r"\bnext\b", r"\bcontinue\b", r"^\s*>\s*$"]
_SUBMIT_PATTERNS = [
    r"^\s*(create|save|submit|finish|done|add)\s*(event)?\s*!?\s*$",
    r"\b(create|save|submit|finish|done|add)\b",
]
_CREATE_EVENT_PATTERNS = [
    r"^\s*\+?\s*create\s*(an?\s+)?event\s*$",
    r"create\s*event",
    r"new\s*event",
    r"add\s*event",
    r"^\s*\+\s*(create|event|new)?\s*$",
    r"\bcreate\b",
    r"\bnew\b",
    r"\badd\b",
]


def _visible(loc):
    try:
        count = loc.count()
    except Exception:
        return None
    for i in range(count):
        el = loc.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def _find(page, locators, timeout_ms, desc):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        for loc in locators:
            el = _visible(loc)
            if el is not None:
                return el
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(200)
        except Exception:
            break
    raise RuntimeError("Could not locate " + desc)


def _field_locators(page, field):
    return [
        page.get_by_role("textbox", name=re.compile(r"^{}$".format(re.escape(field)), re.I)),
        page.get_by_role("textbox", name=re.compile(re.escape(field), re.I)),
        page.get_by_label(re.compile(re.escape(field), re.I)),
        page.locator('[name="{}"]'.format(field)),
        page.locator("#" + field),
        page.locator('[data-field="{}"]'.format(field)),
        page.get_by_placeholder(re.compile(re.escape(field), re.I)),
    ]


def _fill_field(page, field, value):
    value = str(value)
    locators = _field_locators(page, field)
    deadline = time.monotonic() + 10.0
    last_error = None
    while True:
        for loc in locators:
            try:
                count = loc.count()
            except Exception:
                continue
            for i in range(count):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                except Exception:
                    continue
                try:
                    el.click(timeout=2000)
                except Exception:
                    pass
                try:
                    el.fill(value)
                    return
                except Exception as exc:
                    last_error = exc
                    try:
                        el.click(timeout=2000)
                        page.keyboard.insert_text(value)
                        return
                    except Exception as exc2:
                        last_error = exc2
        if time.monotonic() >= deadline:
            break
        try:
            page.wait_for_timeout(250)
        except Exception:
            break
    msg = "Failed to fill field '{}'".format(field)
    if last_error is not None:
        msg += ": {}".format(last_error)
    raise RuntimeError(msg)


def _click_button(page, patterns, desc):
    locators = [page.get_by_role("button", name=re.compile(p, re.I)) for p in patterns]
    css = "button, input[type='submit'], input[type='button'], [role='button']"
    locators += [page.locator(css).filter(has_text=re.compile(p, re.I)) for p in patterns]
    el = _find(page, locators, 10000, desc)
    try:
        el.scroll_into_view_if_needed(timeout=3000)
    except Exception:
        pass
    el.click(timeout=5000)


def _settle(page):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass


def _wait_for_step(page, step):
    try:
        page.wait_for_url(re.compile(r"/step{}(?:[/?#].*)?$".format(step)), timeout=10000)
    except Exception:
        pass
    _settle(page)


def _open_create_form(page):
    for attempt in range(2):
        for pat in _CREATE_EVENT_PATTERNS:
            if "/create_event" not in page.url and "/calendar" not in page.url:
                break
            el = _visible(page.get_by_role("button", name=re.compile(pat, re.I)))
            if el is None:
                continue
            try:
                el.scroll_into_view_if_needed(timeout=2000)
                el.click(timeout=3000)
                page.wait_for_url(re.compile(r"/create_event"), timeout=8000)
                return True
            except Exception:
                continue
        if attempt == 0:
            # Some layouts require a selected day before the create button appears.
            day_locators = [
                page.locator(".calendar-day"),
                page.locator("[data-day]"),
                page.locator("td button"),
                page.get_by_role("button", name=re.compile(r"^\d{1,2}$")),
            ]
            for dl in day_locators:
                el = _visible(dl)
                if el is not None:
                    try:
                        el.scroll_into_view_if_needed(timeout=2000)
                        el.click(timeout=2000)
                        break
                    except Exception:
                        continue
            _settle(page)
    return False


def program(page, binding: dict, base_url: str) -> bool:
    # --- validate the binding ---------------------------------------------
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")
    expected = set(_FIELDS)
    actual = set(binding.keys())
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            "binding keys mismatch (missing={}, unexpected={})".format(missing, extra)
        )
    for key in _FIELDS:
        if not isinstance(binding[key], str) or not binding[key].strip():
            raise ValueError("binding[{!r}] must be a non-empty string".format(key))

    base_url = base_url.rstrip("/")

    # --- 1) open the calendar screen ---------------------------------------
    page.goto(base_url + "/calendar")
    _settle(page)

    # --- 2) open the create-event form through the UI ----------------------
    if "/create_event" not in page.url:
        if not _open_create_form(page):
            page.goto(base_url + "/calendar/create_event")
        _settle(page)
    if "/create_event" not in page.url:
        raise RuntimeError("create-event form did not open (url={})".format(page.url))

    # --- 3) wizard step 1: title + date, then Next -------------------------
    _fill_field(page, "title", binding["title"])
    _fill_field(page, "date", binding["date"])
    _click_button(page, _NEXT_PATTERNS, "the 'Next' button on wizard step 1")
    _wait_for_step(page, 2)

    # --- 4) wizard step 2: description + location, then Next ---------------
    _fill_field(page, "description", binding["description"])
    _fill_field(page, "location", binding["location"])
    _click_button(page, _NEXT_PATTERNS, "the 'Next' button on wizard step 2")
    _wait_for_step(page, 3)

    # --- 5) wizard step 3: url + invitees, then submit ---------------------
    _fill_field(page, "url", binding["url"])
    _fill_field(page, "invitees", binding["invitees"])
    _click_button(page, _SUBMIT_PATTERNS, "the submit button on wizard step 3")

    # --- 6) wait for the wizard to close and return to the calendar --------
    try:
        page.wait_for_url(re.compile(r"/calendar/?(?:[?#].*)?$"), timeout=15000)
    except Exception:
        pass
    _settle(page)
    if "/create_event" in page.url:
        raise RuntimeError("event creation did not complete (url={})".format(page.url))
    return True
