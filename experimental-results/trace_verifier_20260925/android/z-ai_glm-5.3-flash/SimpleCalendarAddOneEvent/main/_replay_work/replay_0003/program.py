import re
from datetime import datetime, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True},
    "month": {"type": "int", "required": True, "min": 1, "max": 12},
    "day": {"type": "int", "required": True},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23},
    "duration_mins": {"type": "int", "required": True},
    "event_title": {"type": "str", "required": True},
    "event_description": {"type": "str", "required": True},
}

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_MONTH_PREFIXES = [m.lower()[:3] for m in MONTHS]
_WEEKDAY_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)", re.IGNORECASE)


def _elems(device):
    return device.elements()


def _dismiss_dialogs(device):
    """Dismiss modal dialogs (Disclaimer / reminders etc.) with OK if present."""
    for _ in range(3):
        els = _elems(device)
        blob = " ".join([(e.get("text") or "") for e in els]).lower()
        idx = device.find(text="OK", clickable=True)
        if idx is None:
            idx = device.find(description="OK", clickable=True)
        if idx is not None and ("disclaimer" in blob or "reminder" in blob
                                or "settings" in blob.lower()):
            device.click(index=idx)
            device.settle(1)
        else:
            return


def _in_editor(device):
    if device.find(hint="Title", editable=True) is not None:
        return True
    if device.find(hint="Description", editable=True) is not None:
        return True
    return device.find(description="Save", clickable=True) is not None


def _open_new_event(device):
    if _in_editor(device):
        return
    for _ in range(4):
        idx = device.find(description="New Event", clickable=True)
        if idx is None:
            idx = device.find(text="New Event", clickable=True)
        if idx is None:
            idx = device.find(description="New Event")
        if idx is not None:
            device.click(index=idx)
            if _in_editor(device):
                return
            device.settle(2)
            if _in_editor(device):
                return
        idx = device.find(text="Event", clickable=True)
        if idx is None:
            idx = device.find(text="Event")
        if idx is None:
            idx = device.find(description="Event", clickable=True)
        if idx is not None:
            device.click(index=idx)
            if _in_editor(device):
                return
            device.settle(2)
            if _in_editor(device):
                return
    raise RuntimeError("Could not open the New Event editor from the calendar main screen")


def _input_into_field(device, hint, previous_index=None):
    idx = device.find(hint=hint, editable=True)
    if idx is None:
        idx = device.find(hint=hint)
    if idx is not None:
        return idx
    editables = [e for e in _elems(device) if e.get("editable")]
    if previous_index is not None:
        editables = [e for e in editables if e["index"] > previous_index]
    if not editables:
        return None
    return editables[0]["index"]


def _looks_like_date(txt):
    t = (txt or "").strip()
    if not t:
        return False
    low = t.lower()
    for p in _MONTH_PREFIXES:
        if p in low:
            return True
    if re.search(r"\b\d{4}\b", t):
        return True
    if re.match(r"^\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?$", t):
        return True
    return False


def _candidate_rows(device):
    rows = []
    for e in _elems(device):
        txt = (e.get("text") or "").strip()
        if not txt or not e.get("clickable") or e.get("editable"):
            continue
        if _TIME_RE.match(txt):
            continue
        low = txt.lower()
        if "before" in low or "after" in low:
            continue
        rows.append(e)
    return rows


def _dateish_rows(device):
    return [e for e in _candidate_rows(device) if _looks_like_date(e["text"])]


def _find_date_row(device, day):
    dateish = _dateish_rows(device)
    pool = dateish if dateish else _candidate_rows(device)
    for e in pool:
        if re.search(rf"\b{day}\b", e["text"]):
            return e["index"]
    return pool[0]["index"] if pool else None


def _date_row_matches(txt, year, month, day):
    low = (txt or "").strip().lower()
    if not re.search(rf"\b{day}\b", low):
        return False
    want = _MONTH_PREFIXES[month - 1]
    has_month = any(p in low for p in _MONTH_PREFIXES)
    if has_month and want not in low:
        return False
    if re.search(r"\b\d{4}\b", low) and str(year) not in low:
        return False
    return True


def _read_shown_month_year(device):
    pat = re.compile(r"([A-Za-z]{3,9})[^0-9]{0,3}(\d{4})")
    for e in _elems(device):
        blob = " ".join([(e.get("text") or ""), (e.get("description") or "")])
        if _WEEKDAY_RE.search(blob):
            continue
        m = pat.search(blob)
        if not m:
            continue
        name = m.group(1).lower()
        for i, p in enumerate(_MONTH_PREFIXES):
            if name.startswith(p):
                try:
                    return (i + 1, int(m.group(2)))
                except ValueError:
                    break
    return None


def _click_month_nav(device, direction):
    primary = "Next month" if direction == "next" else "Previous month"
    idx = device.find(description=primary, clickable=True)
    if idx is not None:
        device.click(index=idx)
        return True
    word = "next" if direction == "next" else "previous"
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        d = (e.get("description") or "").lower()
        if word in d and "month" in d:
            device.click(index=e["index"])
            return True
    return False


def _select_date_in_picker(device, year, month, day):
    mn = MONTHS[month - 1]
    day_s = str(day)
    desc_formats = [f"{day} {mn} {year}", f"{mn} {day}, {year}",
                    f"{day} {mn}", f"{mn} {day} {year}"]
    for _ in range(24):
        for d in desc_formats:
            idx = device.find(description=d, clickable=True)
            if idx is not None:
                device.click(index=idx)
                return
        shown = _read_shown_month_year(device)
        if shown is not None and shown != (month, year):
            delta = (year - shown[1]) * 12 + (month - shown[0])
            if not _click_month_nav(device, "next" if delta > 0 else "previous"):
                raise LookupError("Date picker month navigation control not found")
            continue
        idx = device.find(text=day_s, clickable=True)
        if idx is not None:
            device.click(index=idx)
            return
        break
    raise LookupError(f"Date cell for {day} {mn} {year} not found in the date picker")


def _confirm_ok(device, what):
    idx = device.find(text="OK", clickable=True)
    if idx is None:
        idx = device.find(description="OK", clickable=True)
    if idx is None:
        for e in _elems(device):
            if not e.get("clickable") or e.get("editable"):
                continue
            t = (e.get("text") or "").strip().upper()
            d = (e.get("description") or "").strip().upper()
            if t == "OK" or d == "OK":
                idx = e["index"]
                break
    if idx is None:
        raise RuntimeError(f"'{what}' picker OK button not found")
    device.click(index=idx)
    device.settle(1)


def _cell_by_text(device, value):
    """Find a clickable picker cell whose text (or description) is exactly the value."""
    want = str(value)
    idx = device.find(text=want, clickable=True)
    if idx is not None:
        return idx
    for e in _elems(device):
        if not e.get("clickable"):
            continue
        t = (e.get("text") or "").strip()
        d = (e.get("description") or "").strip()
        if t == want or d == want or d.startswith(want + " "):
            return e["index"]
    return None


def _find_hour_cell(device, hour):
    for h in (hour, f"{hour:02d}"):
        idx = _cell_by_text(device, h)
        if idx is not None:
            return idx
    for d in (f"{hour} hours", f"{hour:02d} hours"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            return idx
    for _ in range(3):
        device.scroll("down")
        for h in (hour, f"{hour:02d}"):
            idx = _cell_by_text(device, h)
            if idx is not None:
                return idx
    for _ in range(3):
        device.scroll("up")
        for h in (hour, f"{hour:02d}"):
            idx = _cell_by_text(device, h)
            if idx is not None:
                return idx
    return None


def _minute_nonzero(device, minute):
    for m in (minute, f"{minute:02d}"):
        idx = _cell_by_text(device, m)
        if idx is not None:
            return idx
    for d in (f"{minute} minutes", f"{minute:02d} minutes"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            return idx
    for _ in range(4):
        device.scroll("down")
        for m in (minute, f"{minute:02d}"):
            idx = _cell_by_text(device, m)
            if idx is not None:
                return idx
    for _ in range(4):
        device.scroll("up")
        for m in (minute, f"{minute:02d}"):
            idx = _cell_by_text(device, m)
            if idx is not None:
                return idx
    return None


def _minute_zero(device):
    # For :00, the hour tap already selects it; look for a 0/00 cell but
    # tolerate its absence (many pickers select 00 by default).
    for z in ("00", "0"):
        idx = _cell_by_text(device, z)
        if idx is not None:
            return idx
    return None


def _pick_time(device, hour, minute):
    hcell = _find_hour_cell(device, hour)
    if hcell is None:
        raise LookupError(f"Hour {hour} cell not found in the time picker")
    device.click(index=hcell)
    if minute == 0:
        mcell = _minute_zero(device)
    else:
        mcell = _minute_nonzero(device, minute)
    if mcell is not None:
        device.click(index=mcell)
    _confirm_ok(device, "time")


def _time_rows(device):
    return [e for e in _elems(device)
            if not e.get("editable") and _TIME_RE.match((e.get("text") or "").strip())]


def _parse_hhmm(txt):
    t = (txt or "").strip()
    if not _TIME_RE.match(t):
        return None
    h, m = t.split(":")
    return (int(h), int(m))


def _set_time_row(device, row_pos, hour, minute):
    label = "start" if row_pos == 0 else "end"
    for _ in range(2):
        _dismiss_dialogs(device)
        rows = _time_rows(device)
        if len(rows) <= row_pos:
            raise LookupError(f"The {label} time row was not found on the event form")
        device.click(index=rows[row_pos]["index"])
        _pick_time(device, hour, minute)
        rows = _time_rows(device)
        if len(rows) > row_pos and _parse_hhmm(rows[row_pos]["text"]) == (hour, minute):
            return
    raise RuntimeError(f"Failed to set the {label} time to {hour:02d}:{minute:02d}")


def _set_start_date(device, year, month, day):
    def _do():
        row = _find_date_row(device, day)
        if row is None:
            raise RuntimeError("Start-date field not found on the event form")
        device.click(index=row)
        _select_date_in_picker(device, year, month, day)
        _confirm_ok(device, "date")

    _do()
    rows = _dateish_rows(device)
    if rows and not _date_row_matches(rows[0]["text"], year, month, day):
        _do()
        rows = _dateish_rows(device)
        if rows and not _date_row_matches(rows[0]["text"], year, month, day):
            raise RuntimeError("Start date did not update to the requested date")


def _save_event(device):
    def _find_save():
        idx = device.find(description="Save", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Save", clickable=True)
        if idx is not None:
            return idx
        for e in _elems(device):
            if not e.get("clickable") or e.get("editable"):
                continue
            d = (e.get("description") or "").strip().lower()
            t = (e.get("text") or "").strip().lower()
            if d == "save" or t == "save":
                return e["index"]
        return None

    idx = _find_save()
    if idx is None:
        raise LookupError("Save button not found on the event form")
    device.click(index=idx)
    if _in_editor(device):
        _dismiss_dialogs(device)
        idx = _find_save()
        if idx is None:
            raise RuntimeError("Save button disappeared before the event could be saved")
        device.click(index=idx)
        if _in_editor(device):
            raise RuntimeError("Event could not be saved: the editor did not close")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    start_dt = datetime(year, month, day, hour, 0)
    end_dt = start_dt + timedelta(minutes=duration)

    # ---- Open the app ----
    device.open_app("Simple Calendar Pro")
    device.settle(2)
    _dismiss_dialogs(device)

    # ---- Open the New Event editor (verify it really opened) ----
    _open_new_event(device)
    _dismiss_dialogs(device)

    # ---- Title ----
    t_idx = _input_into_field(device, "Title")
    if t_idx is None:
        raise LookupError("Title field not found")
    device.input_text(title, index=t_idx)

    # ---- Description ----
    d_idx = _input_into_field(device, "Description", previous_index=t_idx)
    if d_idx is None:
        raise RuntimeError("Description field not found")
    device.input_text(description, index=d_idx)

    # ---- Start date ----
    _set_start_date(device, year, month, day)

    # ---- Start time (HH:00) ----
    _set_time_row(device, 0, hour, 0)

    # ---- End time = start time + duration ----
    _set_time_row(device, 1, end_dt.hour, end_dt.minute)

    # ---- Save ----
    _dismiss_dialogs(device)
    _save_event(device)

    return True
