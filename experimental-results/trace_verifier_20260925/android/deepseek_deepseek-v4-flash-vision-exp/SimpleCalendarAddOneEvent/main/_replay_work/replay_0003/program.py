import re
import calendar

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event duration in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"},
}

_MONTH_NUM = {name.lower(): (i - 1) for i, name in enumerate(calendar.month_name) if name}
_DATE_ROW_RE = re.compile(r"[A-Za-z]{3,}\s+\d{1,2}\s*\([A-Za-z]+\)")
_TIME_ROW_RE = re.compile(r"^\d{1,2}:\d{2}$")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _dismiss_disclaimer(device):
    """If the reminder disclaimer dialog is showing, tap OK to dismiss it."""
    els = device.elements()
    markers = ("disclaimer", "please make sure", "reminders work properly")
    for e in els:
        t = (e.get("text") or "").lower()
        if any(m in t for m in markers):
            for label in ("OK", "Got it", "Dismiss", "Close", "Allow"):
                btn = device.find(text=label, clickable=True)
                if btn is not None:
                    device.click(index=btn)
                    device.settle(1.0)
                    return True
            return False
    return False


def _find_field(device, hint):
    idx = device.find(hint=hint, editable=True)
    if idx is not None:
        return idx
    for e in device.elements():
        if e.get("editable") and (e.get("hint") == hint or e.get("text") == hint or e.get("description") == hint):
            return e["index"]
    return None


def _open_new_event(device):
    _dismiss_disclaimer(device)
    for _ in range(3):
        el = device.find(description="New Event", clickable=True)
        if el is not None:
            device.click(index=el)
            device.settle(1.0)
            _dismiss_disclaimer(device)
            if _find_field(device, "Title") is not None:
                return
            el2 = device.find(text="New Event", clickable=True)
            if el2 is None:
                el2 = device.find(description="New Event", clickable=True)
            if el2 is not None and el2 != el:
                device.click(index=el2)
                device.settle(1.0)
                _dismiss_disclaimer(device)
                if _find_field(device, "Title") is not None:
                    return
        dismissed = False
        for text in ["OK", "Close", "Got it", "Allow", "Next", "Continue", "Dismiss"]:
            d = device.find(text=text, clickable=True)
            if d is not None:
                device.click(index=d)
                device.settle(0.5)
                dismissed = True
                break
        if not dismissed:
            break
    raise RuntimeError("Could not open new event editor")


def _click_dialog_ok(device):
    _dismiss_disclaimer(device)
    for label in ("OK", "Done", "Set"):
        ok = device.find(text=label, clickable=True)
        if ok is not None:
            device.click(index=ok)
            device.settle(1.0)
            return True
    return False


# --------------------------------------------------------------------------
# date handling
# --------------------------------------------------------------------------

def _date_rows(device):
    """All clickable rows on the event form that look like 'October 15 (Sun)'."""
    rows = []
    for e in device.elements():
        t = (e.get("text") or "").strip()
        if e.get("clickable") and _DATE_ROW_RE.search(t):
            rows.append(e["index"])
    return sorted(rows)


def _picker_month_index(device):
    """Absolute month index (year*12 + month-1) of the month shown in the
    currently open date picker, or None if it cannot be determined."""
    els = device.elements()
    strict = re.compile(r"^\s*([A-Za-z]+)\s+(\d{4})\s*$")
    for e in els:
        for field in ("text", "description"):
            s = e.get(field) or ""
            m = strict.match(s)
            if m and m.group(1).lower() in _MONTH_NUM:
                return int(m.group(2)) * 12 + _MONTH_NUM[m.group(1).lower()]
    for e in els:
        t = (e.get("text") or "").strip()
        if not re.fullmatch(r"\d{1,2}", t):
            continue
        d = e.get("description") or ""
        ym = re.search(r"(\d{4})", d)
        if not ym:
            continue
        low = d.lower()
        for name, off in _MONTH_NUM.items():
            if name in low:
                return int(ym.group(1)) * 12 + off
    return None


def _click_month_nav(device, desc):
    btn = device.find(description=desc, clickable=True)
    if btn is None:
        btn = device.find(description=desc)
    if btn is None:
        for e in device.elements():
            d = (e.get("description") or "").lower()
            if desc.lower() in d and e.get("clickable"):
                btn = e["index"]
                break
    if btn is None:
        return False
    device.click(index=btn)
    device.settle(0.5)
    return True


def _find_day_cell(device, year, month, day):
    month_name = calendar.month_name[month]
    variants = [
        f"{day} {month_name} {year}",
        f"{month_name} {day}, {year}",
        f"{month_name} {day} {year}",
    ]
    els = device.elements()
    for e in els:
        if (e.get("text") or "").strip() != str(day):
            continue
        d = (e.get("description") or "").lower()
        if any(v.lower() in d for v in variants):
            return e["index"]
    for e in els:
        if (e.get("text") or "").strip() != str(day):
            continue
        d = (e.get("description") or "").lower()
        if month_name.lower() in d and str(year) in d:
            return e["index"]
    # If the grid carries no per-day descriptions at all, fall back to text.
    has_desc = any(
        re.fullmatch(r"\d{1,2}", (e.get("text") or "").strip()) and e.get("description")
        for e in els
    )
    if not has_desc:
        for e in els:
            if (e.get("text") or "").strip() == str(day) and e.get("clickable"):
                return e["index"]
    return None


def _navigate_date_picker(device, year, month, day):
    target = year * 12 + (month - 1)
    cur = _picker_month_index(device)
    if cur is None:
        # Fresh events default to "today"; the device clock is frozen in 2023-10.
        cur = 2023 * 12 + 9
    delta = target - cur
    if delta != 0:
        desc = "Next month" if delta > 0 else "Previous month"
        for _ in range(abs(delta)):
            if not _click_month_nav(device, desc):
                raise RuntimeError("Month navigation button not found in date picker")

    cell = _find_day_cell(device, year, month, day)
    if cell is None:
        # Last resort: wander a few months in each direction looking for the day.
        for direction in ("Next month", "Previous month"):
            for _ in range(12):
                if not _click_month_nav(device, direction):
                    break
                cell = _find_day_cell(device, year, month, day)
                if cell is not None:
                    break
            if cell is not None:
                break
    if cell is None:
        raise RuntimeError(f"Day cell {day} not found in date picker")
    device.click(index=cell)
    device.settle(0.8)


def _set_start_date(device, year, month, day):
    _dismiss_disclaimer(device)
    rows = _date_rows(device)
    if not rows:
        raise RuntimeError("Start date row not found")
    device.click(index=rows[0])          # start date row, whatever it currently shows
    device.settle(1.0)
    _dismiss_disclaimer(device)
    _navigate_date_picker(device, year, month, day)
    if not _click_dialog_ok(device):
        raise RuntimeError("OK button not found in date picker")


# --------------------------------------------------------------------------
# time handling
# --------------------------------------------------------------------------

def _get_time_rows(device):
    rows = []
    for e in device.elements():
        t = (e.get("text") or "").strip()
        if e.get("clickable") and _TIME_ROW_RE.match(t):
            rows.append(e["index"])
    return sorted(rows)


def _desc_has_number(desc, value):
    return re.search(rf"(?<!\d){value}(?!\d)", desc) is not None


def _click_picker_number(device, value, unit_words, pad=True):
    texts = {str(value)}
    if pad:
        texts.add(f"{value:02d}")

    # 1) exact description, e.g. "18 hours" / "15 minutes"
    for w in unit_words:
        for tv in texts:
            idx = device.find(description=f"{tv} {w}", clickable=True)
            if idx is not None:
                device.click(index=idx)
                device.settle(0.5)
                return

    # 2) any clickable whose description mentions the unit and the number
    for e in device.elements():
        if not e.get("clickable"):
            continue
        d = (e.get("description") or "").lower()
        if not any(w in d for w in unit_words):
            continue
        if _desc_has_number(d, value):
            device.click(index=e["index"])
            device.settle(0.5)
            return

    # 3) plain text match
    for tv in texts:
        idx = device.find(text=tv, clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(0.5)
            return
        idx = device.find(text=tv)
        if idx is not None:
            device.click(index=idx)
            device.settle(0.5)
            return

    raise RuntimeError(f"Could not select value {value} in picker")


def _select_time_in_picker(device, hour, minute):
    _click_picker_number(device, hour, ["hours", "hour"])
    _click_picker_number(device, minute, ["minutes", "minute"])


def _set_time(device, which, hour, minute):
    _dismiss_disclaimer(device)
    rows = _get_time_rows(device)
    if len(rows) < 2:
        raise RuntimeError("Not enough time rows found")
    row = rows[0] if which == "start" else rows[1]
    device.click(index=row)
    device.settle(1.0)
    _dismiss_disclaimer(device)
    _select_time_in_picker(device, hour, minute)
    if not _click_dialog_ok(device):
        # Some builds close the picker automatically once the value is chosen.
        if not _get_time_rows(device):
            raise RuntimeError("Time picker OK button not found")


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------

def program(device, binding: dict) -> bool:
    device.open_app("Simple Calendar Pro")
    device.settle(1.0)
    _dismiss_disclaimer(device)

    _open_new_event(device)
    _dismiss_disclaimer(device)

    title_field = _find_field(device, "Title")
    if title_field is None:
        raise RuntimeError("Title field not found")
    device.click(index=title_field)
    device.settle(0.5)
    device.input_text(binding["event_title"], index=title_field)
    device.settle(0.5)

    desc_field = _find_field(device, "Description")
    if desc_field is None:
        raise RuntimeError("Description field not found")
    device.click(index=desc_field)
    device.settle(0.5)
    device.input_text(binding["event_description"], index=desc_field)
    device.settle(0.5)

    _dismiss_disclaimer(device)
    _set_start_date(device, binding["year"], binding["month"], binding["day"])

    _dismiss_disclaimer(device)
    _set_time(device, "start", binding["hour"], 0)

    start_total = binding["hour"] * 60
    end_total = start_total + binding["duration_mins"]
    end_hour = (end_total // 60) % 24
    end_minute = end_total % 60

    _dismiss_disclaimer(device)
    _set_time(device, "end", end_hour, end_minute)

    _dismiss_disclaimer(device)
    save_btn = device.find(description="Save", clickable=True)
    if save_btn is None:
        save_btn = device.find(text="Save", clickable=True)
    if save_btn is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_btn)
    device.settle(2.0)

    # The save flow may show the reminder disclaimer; tapping OK confirms and saves.
    _dismiss_disclaimer(device)

    return True
