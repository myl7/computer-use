import re
import time

PARAMS_SCHEMA = {
    "title": {
        "type": "string",
        "required": True,
        "description": "Title of the calendar event.",
    },
    "date": {
        "type": "string",
        "required": True,
        "description": "Date (and optional time) of the event, e.g. '2026-06-15'.",
    },
    "description": {
        "type": "string",
        "required": True,
        "description": "Free-text description of the event.",
    },
    "location": {
        "type": "string",
        "required": True,
        "description": "Location / venue of the event.",
    },
    "url": {
        "type": "string",
        "required": True,
        "description": "URL associated with the event (e.g. a meeting link).",
    },
    "invitees": {
        "type": "string",
        "required": True,
        "description": "Invitees of the event (e.g. comma-separated emails).",
    },
}

_TEXTBOX_SELECTOR = (
    "input[type='text'], input[type='search'], input[type='email'], "
    "input[type='url'], input[type='tel'], input[type='password'], "
    "input:not([type]), textarea"
)

_NEXT_BUTTON_HINTS = [r"\bnext\b", r"\bcontinue\b", r"save\s*&?\s*continue", r"\bproceed\b"]
_CREATE_BUTTON_HINTS = [r"\bcreate\b", r"\bsave\b", r"\badd\b", r"\bdone\b",
                        r"\bfinish\b", r"\bsubmit\b", r"\bconfirm\b", r"\bok\b"]


def _scroll_page(page, dy):
    try:
        page.evaluate("window.scrollBy(0, %d);" % int(dy))
    except Exception:
        try:
            page.mouse.wheel(0, dy)
        except Exception:
            pass


def _field_candidates(page, pattern):
    return [
        page.get_by_role("textbox", name=pattern),
        page.get_by_label(pattern),
        page.get_by_placeholder(pattern),
    ]


def _field_present(page, labels):
    for lbl in labels:
        pat = re.compile(re.escape(lbl), re.I)
        for loc in _field_candidates(page, pat):
            try:
                for i in range(loc.count()):
                    try:
                        if loc.nth(i).is_visible():
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
    return False


def _visible_textboxes(page):
    boxes = []
    try:
        loc = page.locator(_TEXTBOX_SELECTOR)
        for i in range(loc.count()):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.is_enabled():
                    boxes.append(el)
            except Exception:
                continue
    except Exception:
        pass
    return boxes


def _type_value(page, el, value):
    try:
        el.fill(value, timeout=2500)
        return
    except Exception:
        pass
    try:
        el.click(timeout=1500)
    except Exception:
        pass
    for type_fn in ("press_sequentially", "type"):
        try:
            getattr(el, type_fn)(value, delay=15, timeout=8000)
            return
        except Exception:
            continue
    try:
        page.keyboard.insert_text(value)
    except Exception:
        pass


def _fill_field(page, labels, value, timeout_s=12.0):
    """Fill one logical field: try label/name/placeholder lookup first,
    then fall back to the first visible empty textbox in DOM order."""
    patterns = [re.compile(re.escape(l), re.I) for l in labels]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for pat in patterns:
            for loc in _field_candidates(page, pat):
                try:
                    count = loc.count()
                except Exception:
                    count = 0
                for i in range(count):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible() or not el.is_enabled():
                            continue
                        el.scroll_into_view_if_needed(timeout=1000)
                        _type_value(page, el, value)
                        return
                    except Exception:
                        continue
        for el in _visible_textboxes(page):
            try:
                if el.input_value().strip() == "":
                    el.scroll_into_view_if_needed(timeout=1000)
                    _type_value(page, el, value)
                    return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError("Could not fill a field matching any of %r" % (labels,))


def _click_button(page, patterns, timeout_s=8.0):
    pats = [re.compile(p, re.I) for p in patterns]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        for pat in pats:
            for role in ("button", "link"):
                loc = page.get_by_role(role, name=pat)
                try:
                    count = loc.count()
                except Exception:
                    count = 0
                for i in range(count):
                    el = loc.nth(i)
                    try:
                        if not el.is_visible() or not el.is_enabled():
                            continue
                        el.scroll_into_view_if_needed(timeout=1000)
                        el.click(timeout=2500)
                        return
                    except Exception:
                        continue
        page.wait_for_timeout(200)
    raise RuntimeError("No clickable button matched any of %r" % (patterns,))


def _wait_url_contains(page, fragment, timeout_s=10.0, alt=None):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if fragment in page.url:
                return
        except Exception:
            pass
        if alt is not None:
            try:
                if alt():
                    return
            except Exception:
                pass
        page.wait_for_timeout(150)
    raise RuntimeError("URL never contained %r (last url=%r)" % (fragment, page.url))


def _open_create_form_via_ui(page, base_url):
    """Fallback path mirroring the recording: calendar -> (select a day) -> create button."""
    hints = [r"add event", r"create event", r"new event", r"\bnew\b", r"\badd\b", r"\bcreate\b", r"\+"]
    for _ in range(3):
        if "/calendar" not in page.url:
            page.goto(base_url + "/calendar", wait_until="load")
        for pat in hints:
            loc = page.get_by_role("button", name=re.compile(pat, re.I))
            try:
                count = loc.count()
            except Exception:
                count = 0
            for i in range(count):
                el = loc.nth(i)
                try:
                    if not el.is_visible():
                        continue
                    el.scroll_into_view_if_needed(timeout=1000)
                    el.click(timeout=2000)
                    page.wait_for_timeout(400)
                    if "create_event" in page.url:
                        return
                except Exception:
                    continue
        # In the recording a day had to be selected before the create button appeared.
        try:
            days = page.get_by_role("button", name=re.compile(r"^\d{1,2}$"))
            for i in range(days.count()):
                el = days.nth(i)
                try:
                    if el.is_visible():
                        el.click(timeout=1500)
                        break
                except Exception:
                    continue
        except Exception:
            pass
        page.wait_for_timeout(300)
        _scroll_page(page, 600)


def program(page, binding: dict, base_url: str) -> bool:
    missing = [k for k in PARAMS_SCHEMA
               if k not in binding or not isinstance(binding[k], str) or not binding[k].strip()]
    if missing:
        raise ValueError("binding is missing or has empty values for: %s" % ", ".join(missing))
    v = {k: binding[k] for k in PARAMS_SCHEMA}
    base = base_url.rstrip("/")

    def _on_dialog(dialog):
        try:
            dialog.accept()
        except Exception:
            pass

    try:
        page.on("dialog", _on_dialog)
    except Exception:
        pass

    # --- reach the create-event wizard ---------------------------------
    page.goto(base + "/calendar/create_event", wait_until="load")
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass

    if not _field_present(page, ["title"]) and not _visible_textboxes(page):
        _open_create_form_via_ui(page, base)
        if "create_event" not in page.url:
            page.goto(base + "/calendar/create_event", wait_until="load")

    _wait_url_contains(page, "/calendar/create_event", timeout_s=10.0)

    deadline = time.time() + 10.0
    rendered = False
    while time.time() < deadline:
        if _field_present(page, ["title", "summary", "name"]) or _visible_textboxes(page):
            rendered = True
            break
        page.wait_for_timeout(200)
    if not rendered:
        raise RuntimeError("Create-event form did not render (url=%r)" % page.url)

    # --- step 1: basics -------------------------------------------------
    _fill_field(page, ["title", "event title", "summary", "event name"], v["title"])
    _fill_field(page, ["date", "event date", "when", "start", "day"], v["date"])
    _click_button(page, _NEXT_BUTTON_HINTS)
    _wait_url_contains(page, "/create_event/step2", timeout_s=10.0,
                       alt=lambda: _field_present(page, ["description", "notes", "details"]))

    # --- step 2: details -------------------------------------------------
    _fill_field(page, ["description", "notes", "details", "about"], v["description"])
    _fill_field(page, ["location", "place", "where", "venue", "address"], v["location"])
    _click_button(page, _NEXT_BUTTON_HINTS)
    _wait_url_contains(page, "/create_event/step3", timeout_s=10.0,
                       alt=lambda: _field_present(page, ["url", "link", "invitee", "guest", "attendee"]))

    # --- step 3: extras + submit -----------------------------------------
    _fill_field(page, ["url", "event url", "link", "website", "meeting", "video"], v["url"])
    _fill_field(page, ["invitees", "invitee", "invite", "guests", "guest",
                       "attendees", "attendee", "participants", "people",
                       "email", "emails", "share"], v["invitees"])
    _click_button(page, _CREATE_BUTTON_HINTS)

    # --- confirm the wizard closed and we are back on the calendar -------
    deadline = time.time() + 12.0
    while time.time() < deadline:
        try:
            url = page.url
        except Exception:
            url = ""
        if "/create_event" not in url and "/calendar" in url:
            return True
        page.wait_for_timeout(200)
    raise RuntimeError("Event creation did not complete (url=%r)" % page.url)
