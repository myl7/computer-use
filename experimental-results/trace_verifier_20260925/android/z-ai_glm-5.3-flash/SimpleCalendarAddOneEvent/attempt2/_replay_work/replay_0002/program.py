import re
import datetime

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

_MONTH_ALTS = "|".join(f"{name}|{name[:3]}" for name in MONTH_NAMES)
_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_MINUTES_RE = re.compile(r"^(\d{1,2}) minutes$")
_MONTH_HEADER_RE = re.compile(
    r"^(" + "|".join(MONTH_NAMES) + r")\s+(\d{4})$", re.IGNORECASE)
_DATE_ROW_RE = re.compile(r"\b(" + _MONTH_ALTS + r")\s+\d{1,2}\b", re.IGNORECASE)
_MONTH_ANY_RE = re.compile(r"(" + _MONTH_ALTS + r")", re.IGNORECASE)


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


def _dismiss_ok_dialogs(device, rounds=3):
    for _ in range(rounds):
        idx = device.find(text="OK", clickable=True)
        if idx is None:
            idx = device.find(description="OK", clickable=True)
        if idx is None:
            return
        device.click(idx)
        device.settle(1)


def _prepare_main(device, app_name):
    device.open_app(app_name)
    device.settle(2)
    for _ in range(4):
        if _main_screen_ready(device):
            return
        idx = device.find(text="OK", clickable=True)
        if idx is not None:
            # e.g. the reminder 'Disclaimer' dialog left over from an earlier run
            device.click(idx)
            device.settle(1)
            continue
        device.navigate_back()
        device.settle(1)
        if _main_screen_ready(device):
            return
        device.open_app(app_name)
        device.settle(2)
    if not _main_screen_ready(device):
        raise RuntimeError(f"App '{app_name}' did not open: no 'New Event' control on screen")


def _open_event_editor(device):
    for _ in range(4):
        if device.find(hint="Title", editable=True) is not None:
            return
        idx = device.find(description="New Event", clickable=True)
        if idx is None:
            device.settle(1)
            idx = device.find(description="New Event", clickable=True)
        if idx is None:
            idx = device.find(text="New Event", clickable=True)
        if idx is None:
            ok = device.find(text="OK", clickable=True)
            if ok is not None:
                device.click(ok)
                device.settle(1)
            else:
                device.settle(1)
            continue
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


def _row_time_matches(text, hour, minute):
    m = _HHMM_RE.match(str(text or "").strip())
    if not m:
        return False
    return int(m.group(1)) == int(hour) and int(m.group(2)) == int(minute)


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


def _date_rows(device):
    rows = []
    for e in _elems(device):
        t = _txt(e)
        if not e.get("clickable") or e.get("editable"):
            continue
        if _HHMM_RE.match(t):
            continue
        if _DATE_ROW_RE.search(t) or (_MONTH_ANY_RE.search(t) and re.search(r"\b\d{4}\b", t)):
            rows.append((int(e.get("index")), t))
    rows.sort(key=lambda r: r[0])
    return rows


def _date_row_matches(text, year, month, day):
    t = str(text or "").lower()
    name = MONTH_NAMES[int(month) - 1].lower()
    if not re.search(rf"\b{int(day)}\b", t):
        return False
    if name not in t and name[:3] not in t:
        return False
    m = re.search(r"\b(\d{4})\b", t)
    if m and int(m.group(1)) != int(year):
        return False
    return True


def _date_row_verified(rows, pos, year, month, day):
    if pos >= len(rows):
        return True
    t = rows[pos][1]
    if not _MONTH_ANY_RE.search(t):
        return True  # unrecognized date format; the picker already picked exactly
    return _date_row_matches(t, year, month, day)


def _set_start_date(device, year, month, day):
    wanted = f"{day} {MONTH_NAMES[month - 1]} {year}"
    for _ in range(2):
        rows = _date_rows(device)
        if not rows:
            raise LookupError("Start-date row not found on the event form")
        if _date_row_matches(rows[0][1], year, month, day):
            return
        device.click(rows[0][0])
        device.settle(1)
        _pick_date_in_dialog(device, year, month, day)
        rows = _date_rows(device)
        if _date_row_verified(rows, 0, year, month, day):
            return
        device.settle(1)
    raise RuntimeError(f"Failed to set the start date to {wanted}")


def _set_end_date(device, year, month, day):
    wanted = f"{day} {MONTH_NAMES[month - 1]} {year}"
    for _ in range(2):
        rows = _date_rows(device)
        if len(rows) < 2:
            return
        if _date_row_matches(rows[1][1], year, month, day):
            return
        device.click(rows[1][0])
        device.settle(1)
        _pick_date_in_dialog(device, year, month, day)
        rows = _date_rows(device)
        if len(rows) < 2 or _date_row_verified(rows, 1, year, month, day):
            return
        device.settle(1)
    raise RuntimeError(f"Failed to set the end date to {wanted}")


def _click_ok(device, what):
    for _ in range(4):
        idx = device.find(text="OK", clickable=True)
        if idx is None:
            idx = device.find(description="OK", clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return
        device.settle(1)
    raise RuntimeError(f"'OK' button not found in the {what}")


def _pick_hour_in_dialog(device, hour, label):
    wanted = [f"{hour} hours", f"{hour:02d} hours"]
    texts = [str(hour), f"{hour:02d}"]
    if hour == 0:
        wanted.append("24 hours")
        texts.append("24")
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
        for e in elems:
            if e.get("clickable") and "hour" in _desc(e).lower() and _txt(e) in texts:
                device.click(e["index"])
                device.settle(1)
                return
        device.scroll("down" if attempt < 4 else "up")
        device.settle(1)
    raise LookupError(f"Hour cell for hour {hour} not found in the {label} time picker")


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
            d = _desc(e)
            if e.get("clickable") and _MINUTES_RE.match(d) \
                    and "hours" not in d.lower() and "textview" not in _cls(e):
                device.click(e["index"])
                device.settle(1)
                return
        device.settle(1)
    # no minutes selector found; the caller falls back to text entry


def _pick_minute_in_dialog(device, minute, label):
    wanted = [f"{minute} minutes", f"{minute:02d} minutes"]
    texts = [str(minute), f"{minute:02d}"]
    for attempt in range(8):
        elems = _elems(device)
        for want in wanted:
            for e in elems:
                if e.get("clickable") and _desc(e) == want and "textview" in _cls(e):
                    device.click(e["index"])
                    device.settle(1)
                    return True
        for want in wanted:
            matches = [e for e in elems if e.get("clickable") and _desc(e) == want]
            if matches:
                device.click(matches[-1]["index"])
                device.settle(1)
                return True
        for e in elems:
            if e.get("clickable") and "minute" in _desc(e).lower() and _txt(e) in texts:
                device.click(e["index"])
                device.settle(1)
                return True
        device.scroll("down" if attempt < 4 else "up")
        device.settle(1)
    return False


def _set_time_via_text(device, hour, minute):
    toggle = None
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        d = _desc(e).lower()
        t = _txt(e).lower()
        if "text input" in d or "text input" in t or "keyboard" in d or "keyboard" in t:
            toggle = e.get("index")
            break
    if toggle is None:
        return False
    device.click(toggle)
    device.settle(1)
    field = device.find(editable=True)
    if field is None:
        return False
    device.input_text(f"{int(hour):02d}{int(minute):02d}", index=field)
    device.settle(1)
    device.keyboard_enter()
    device.settle(1)
    return True


def _set_dialog_time(device, row_index, hour, minute, label):
    device.click(row_index)
    device.settle(1)
    _pick_hour_in_dialog(device, hour, label)
    if _current_minutes(device) != minute:
        _open_minutes_list(device, label)
        if not _pick_minute_in_dialog(device, minute, label):
            _set_time_via_text(device, hour, minute)
    _click_ok(device, f"{label} time picker")


def _set_time_row(device, which, hour, minute, label):
    for _ in range(2):
        rows = _time_rows(device)
        if not rows:
            raise LookupError(f"No {label}-time row (HH:MM) found on the event form")
        pos = 0 if which == "start" else -1
        row_index, row_text = rows[pos]
        if _row_time_matches(row_text, hour, minute):
            return
        _set_dialog_time(device, row_index, hour, minute, label)
        rows = _time_rows(device)
        if rows and _row_time_matches(rows[pos][1], hour, minute):
            return
        device.settle(1)
    raise RuntimeError(f"Failed to set the {label} time to {hour:02d}:{minute:02d}")


def _save_event(device):
    for _ in range(3):
        idx = device.find(description="Save", clickable=True)
        if idx is None:
            idx = device.find(text="Save", clickable=True)
        if idx is None:
            if device.find(hint="Title", editable=True) is None:
                return
            raise LookupError("Save button not found on the event form")
        device.click(idx)
        device.settle(2)
        # Saving can pop Simple Calendar's reminder 'Disclaimer' dialog; the event
        # is only stored once that dialog is confirmed with 'OK'.
        _dismiss_ok_dialogs(device, 3)
        device.settle(1)
        _dismiss_ok_dialogs(device, 1)
        if device.find(hint="Title", editable=True) is None:
            return
        device.settle(1)
    raise RuntimeError("Save did not complete: the event editor is still open")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    # 1. Launch Simple Calendar Pro and make sure the main screen is reachable
    #    (clearing e.g. a leftover 'Disclaimer' dialog with OK).
    app_name = binding.get("app_name", "Simple Calendar Pro")
    _prepare_main(device, app_name)

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
    _set_time_row(device, "start", hour, 0, "start")

    # 7. End time = start time + duration_mins (there is no duration field).
    try:
        end_dt = datetime.datetime(year, month, day, hour, 0) + datetime.timedelta(minutes=duration)
        end_hour, end_min = end_dt.hour, end_dt.minute
        end_date = (end_dt.year, end_dt.month, end_dt.day)
    except ValueError:
        total = hour * 60 + duration
        end_hour, end_min = (total // 60) % 24, total % 60
        end_date = (year, month, day)
    _set_time_row(device, "end", end_hour, end_min, "end")

    # 8. Move the end date too when the duration crosses midnight.
    if end_date != (year, month, day):
        _set_end_date(device, end_date[0], end_date[1], end_date[2])

    # 9. Save the event, confirming the reminder 'Disclaimer' dialog with OK
    #    if it appears (otherwise the event is never actually created).
    _save_event(device)
    return True
