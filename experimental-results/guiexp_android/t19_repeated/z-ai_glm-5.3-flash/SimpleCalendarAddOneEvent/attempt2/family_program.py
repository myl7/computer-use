import re

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "Year of the event's start date"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Month of the event's start date (1-12)"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Day of month of the event's start date"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Event start hour on a 24-hour clock"},
    "duration_mins": {"type": "int", "required": True, "min": 1, "max": 1440,
                      "description": "Event length in minutes; applied by setting the "
                                     "event's END time to start time + duration"},
    "event_title": {"type": "str", "required": True,
                    "description": "Title of the calendar event"},
    "event_description": {"type": "str", "required": True,
                          "description": "Description of the calendar event"},
}

_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")
_MINUTES_RE = re.compile(r"^(\d{1,2}) minutes$")
_MONTH_HEADER_RE = re.compile(
    r"^(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})$", re.IGNORECASE)
_DATE_ROW_RE = re.compile(
    r"\b(" + "|".join(MONTH_NAMES) + r")\s+\d{1,2}\b", re.IGNORECASE)


def _elems(device):
    try:
        return device.elements()
    except Exception:
        return []


def _txt(e):
    return str(e.get("text") or "").strip()


def _desc(e):
    return str(e.get("description") or "").strip()


def _cls(e):
    return str(e.get("class_name") or "").lower()


def _main_screen_ready(device):
    if device.find(description="New Event") is not None:
        return True
    if device.find(hint="Title", editable=True) is not None:
        return True
    return False


def _open_event_editor(device):
    for _ in range(3):
        if device.find(hint="Title", editable=True) is not None:
            return
        idx = device.find(description="New Event", clickable=True)
        if idx is None:
            device.settle(1)
            idx = device.find(description="New Event", clickable=True)
        if idx is None:
            raise LookupError("Floating 'New Event' button not found on the calendar main screen")
        device.click(idx)
        device.settle(1)
        for _ in range(3):
            if device.find(hint="Title", editable=True) is not None:
                return
            device.settle(1)
    raise RuntimeError("Tapping 'New Event' did not open the event editor")


def _find_text_field(device, hint):
    idx = device.find(hint=hint, editable=True)
    if idx is not None:
        return idx
    editables = [e for e in _elems(device) if e.get("editable")]
    pos = 0 if hint == "Title" else 1
    if pos < len(editables):
        return editables[pos]["index"]
    raise LookupError(f"'{hint}' input field not found on the event form")


def _time_rows(device):
    rows = []
    for e in _elems(device):
        if e.get("clickable") and _HHMM_RE.match(_txt(e)):
            rows.append((int(e.get("index")), _txt(e)))
    rows.sort(key=lambda r: r[0])
    return rows


def _exact_clickable(device, description=None, text=None):
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        if description is not None and _desc(e) != description:
            continue
        if text is not None and _txt(e) != text:
            continue
        return e.get("index")
    return None


def _displayed_ym(device):
    for e in _elems(device):
        m = _MONTH_HEADER_RE.match(_txt(e))
        if m:
            name = m.group(1).lower()
            for i, mn in enumerate(MONTH_NAMES):
                if mn.lower() == name:
                    return (int(m.group(2)), i + 1)
    return None


def _fuzzy_date_cell(device, year, month, day):
    want_month = MONTH_NAMES[month - 1].lower()
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        d = _desc(e)
        if re.search(rf"\b{re.escape(str(day))}\b", d) and str(year) in d and want_month in d.lower():
            return e.get("index")
    return None


def _step_month_towards(device, year, month):
    disp = _displayed_ym(device)
    forward = True if disp is None else (year, month) > disp
    labels = ("Next month", "Next") if forward else ("Previous month", "Previous")
    for lab in labels:
        idx = device.find(description=lab, clickable=True)
        if idx is None:
            idx = device.find(text=lab, clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
    syms = ("\u203a", ">", "\u2192") if forward else ("\u2039", "<", "\u2190")
    for s in syms:
        idx = device.find(text=s, clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
    return False


def _pick_date_in_dialog(device, year, month, day):
    exact = f"{day} {MONTH_NAMES[month - 1]} {year}"
    for _ in range(24):
        idx = _exact_clickable(device, description=exact)
        if idx is None:
            idx = _fuzzy_date_cell(device, year, month, day)
        if idx is None and _displayed_ym(device) == (year, month):
            idx = _exact_clickable(device, text=str(day))
        if idx is not None:
            device.click(idx)
            device.settle(1)
            _click_ok(device, "date picker")
            return
        if not _step_month_towards(device, year, month):
            break
    raise LookupError(f"Could not select the date {exact} in the date picker")


def _set_start_date(device, year, month, day):
    row = None
    for e in _elems(device):
        t = _txt(e)
        if e.get("clickable") and not e.get("editable") and _DATE_ROW_RE.search(t) \
                and not _HHMM_RE.match(t):
            row = e.get("index")
            break
    if row is None:
        raise LookupError("Start-date row not found on the event form")
    device.click(row)
    device.settle(1)
    _pick_date_in_dialog(device, year, month, day)


def _click_ok(device, what):
    for _ in range(4):
        idx = device.find(text="OK", clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return
        device.settle(1)
    raise RuntimeError(f"'OK' button not found in the {what}")


def _pick_hour_in_dialog(device, hour, label):
    wanted = [f"{hour} hours", f"{hour:02d} hours"]
    for attempt in range(8):
        elems = _elems(device)
        for want in wanted:
            for e in elems:
                if e.get("clickable") and _desc(e) == want and "textview" in _cls(e):
                    device.click(e["index"])
                    device.settle(1)
                    return
        for want in wanted:
            matches = [e for e in elems if e.get("clickable") and _desc(e) == want]
            if matches:
                device.click(matches[-1]["index"])
                device.settle(1)
                return
        device.scroll("down" if attempt < 4 else "up")
        device.settle(1)
    raise LookupError(f"Hour cell '{hour} hours' not found in the {label} time picker")


def _current_minutes(device):
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        d = _desc(e)
        m = _MINUTES_RE.match(d)
        if m and "hours" not in d.lower() and "textview" not in _cls(e):
            return int(m.group(1))
    return None


def _open_minutes_list(device, label):
    for _ in range(2):
        elems = _elems(device)
        for e in elems:
            if e.get("clickable") and _MINUTES_RE.match(_desc(e)) and "textview" in _cls(e):
                return
        for e in elems:
            d = _desc(e).lower()
            if e.get("clickable") and _MINUTES_RE.match(_desc(e)) \
                    and "hours" not in d and "textview" not in _cls(e):
                device.click(e["index"])
                device.settle(1)
                return
        device.settle(1)
    raise RuntimeError(f"Minutes selector not found in the {label} time picker")


def _pick_minute_in_dialog(device, minute, label):
    want = f"{minute} minutes"
    for attempt in range(8):
        elems = _elems(device)
        for e in elems:
            if e.get("clickable") and _desc(e) == want and "textview" in _cls(e):
                device.click(e["index"])
                device.settle(1)
                return
        matches = [e for e in elems if e.get("clickable") and _desc(e) == want]
        if matches:
            device.click(matches[-1]["index"])
            device.settle(1)
            return
        device.scroll("down" if attempt < 4 else "up")
        device.settle(1)
    raise LookupError(f"Minute cell '{want}' not found in the {label} time picker")


def _set_dialog_time(device, row_index, hour, minute, label):
    device.click(row_index)
    device.settle(1)
    _pick_hour_in_dialog(device, hour, label)
    current = _current_minutes(device)
    if current != minute:
        _open_minutes_list(device, label)
        _pick_minute_in_dialog(device, minute, label)
    _click_ok(device, f"{label} time picker")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    # 1. Launch Simple Calendar Pro.
    app_name = binding.get("app_name", "Simple Calendar Pro")
    device.open_app(app_name)
    device.settle(2)
    if not _main_screen_ready(device):
        device.navigate_back()
        device.settle(1)
        if not _main_screen_ready(device):
            device.open_app(app_name)
            device.settle(2)
        if not _main_screen_ready(device):
            raise RuntimeError(f"App '{app_name}' did not open: no 'New Event' control on screen")

    # 2. Open the new-event editor (tap the floating 'New Event' button;
    #    some recordings needed the tap repeated before the editor appeared).
    _open_event_editor(device)

    # 3. Title.
    idx = _find_text_field(device, "Title")
    device.input_text(title, index=idx)

    # 4. Description.
    idx = _find_text_field(device, "Description")
    device.input_text(description, index=idx)

    # 5. Start date via the date picker dialog.
    _set_start_date(device, year, month, day)

    # 6. Start time at {hour}:00 via the time picker dialog.
    rows = _time_rows(device)
    if not rows:
        raise LookupError("No start-time row (HH:MM) found on the event form")
    _set_dialog_time(device, rows[0][0], hour, 0, "start")

    # 7. End time = start time + duration_mins (there is no duration field).
    total = hour * 60 + duration
    end_hour = (total // 60) % 24
    end_min = total % 60
    rows = _time_rows(device)
    if len(rows) < 2:
        raise LookupError("No end-time row (HH:MM) found on the event form")
    _set_dialog_time(device, rows[-1][0], end_hour, end_min, "end")

    # 8. Save the event.
    idx = device.find(description="Save", clickable=True)
    if idx is None:
        idx = device.find(text="Save", clickable=True)
    if idx is None:
        raise LookupError("Save button not found on the event form")
    device.click(idx)
    device.settle(2)
    if device.find(hint="Title", editable=True) is not None:
        raise RuntimeError("Save did not complete: the event editor is still open")
    return True
