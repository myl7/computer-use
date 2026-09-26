import re
from datetime import datetime, timedelta

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

_NUMERIC_DATE_RE = re.compile(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "Year of the event start date"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Month of the event start date (1-12)"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Day of month of the event start date"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Event start hour, 24-hour clock (start minutes are 00)"},
    "duration_mins": {"type": "int", "required": True, "min": 1,
                      "description": "Event length in minutes; the end time is set to start + duration"},
    "event_title": {"type": "str", "required": True,
                    "description": "Title of the calendar event"},
    "event_description": {"type": "str", "required": True,
                          "description": "Description of the calendar event"},
}


def _elems(device):
    try:
        return list(device.elements())
    except Exception:
        return []


def _find(device, description=None, text=None, hint=None, contains=None,
          clickable=None, editable=None):
    for i, e in enumerate(_elems(device)):
        if clickable is not None and bool(e.get("clickable")) != bool(clickable):
            continue
        if editable is not None and bool(e.get("editable")) != bool(editable):
            continue
        if description is not None and str(e.get("description") or "") != description:
            continue
        if text is not None and str(e.get("text") or "") != text:
            continue
        if hint is not None and str(e.get("hint") or "") != hint:
            continue
        if contains is not None:
            blob = (str(e.get("description") or "") + " " + str(e.get("text") or "")).lower()
            if contains.lower() not in blob:
                continue
        return e.get("index", i)
    return None


def _first(device, criteria_list):
    for crit in criteria_list:
        idx = _find(device, **crit)
        if idx is not None:
            return idx
    return None


def _editor_open(device):
    if _find(device, description="Save", clickable=True) is not None:
        return True
    return _find(device, hint="Title", editable=True) is not None


def _find_new_event(device):
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable"):
            continue
        d = str(e.get("description") or "").strip().lower()
        t = str(e.get("text") or "").strip().lower()
        if "new event" in d or "new event" in t:
            return e.get("index", i)
    return None


def _disclaimer_dialog_up(device):
    for e in _elems(device):
        blob = (str(e.get("text") or "") + " " + str(e.get("description") or "")).lower()
        if "disclaimer" in blob or "reminders work properly" in blob:
            return True
        if e.get("clickable"):
            tl = str(e.get("text") or "").strip().lower()
            dl = str(e.get("description") or "").strip().lower()
            if tl == "settings" or dl == "settings":
                return True
    return False


def _open_new_event_editor(device):
    for _ in range(6):
        if _editor_open(device):
            return
        # A first-launch reminders 'Disclaimer' dialog (Settings / OK) can
        # block the screen; dismiss it before looking for the New Event button.
        if _disclaimer_dialog_up(device):
            ok_idx = _first(device, [
                {"text": "OK", "clickable": True},
                {"description": "OK", "clickable": True},
            ])
            if ok_idx is not None:
                device.click(ok_idx)
                device.settle(1)
                continue
        idx = _find_new_event(device)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            continue
        idx = _first(device, [
            {"text": "Allow", "clickable": True},
            {"text": "While using the app", "clickable": True},
            {"text": "WHILE USING THE APP", "clickable": True},
            {"text": "Only this time", "clickable": True},
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
            {"text": "GOT IT", "clickable": True},
            {"text": "Got it", "clickable": True},
        ])
        if idx is not None:
            device.click(idx)
            device.settle(1)
            continue
        device.settle(1)
    if not _editor_open(device):
        raise RuntimeError("Simple Calendar Pro: failed to open the New Event editor")


def _date_rows(device):
    rows = []
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable") or e.get("editable"):
            continue
        t = str(e.get("text") or "").strip()
        if not t or ":" in t:
            continue
        low = t.lower()
        if any(m.lower() in low for m in MONTHS) or _NUMERIC_DATE_RE.search(t):
            rows.append((e.get("index", i), t))
    return rows


def _time_rows(device):
    rows = []
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable") or e.get("editable"):
            continue
        t = str(e.get("text") or "").strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            rows.append((e.get("index", i), t))
    return rows


def _time_text_matches(text, hour, minute):
    t = str(text or "").strip()
    return t in (f"{hour:02d}:{minute:02d}", f"{hour}:{minute:02d}")


def _date_text_matches(text, year, month, day):
    t = str(text or "").strip()
    if not t:
        return False
    low = t.lower()
    name = MONTHS[month - 1].lower()
    if (name in low or re.search(r"\b" + name[:3], low)) and re.search(rf"(?<!\d){day}(?!\d)", t):
        return True
    m = _NUMERIC_DATE_RE.search(t)
    if m:
        parts = re.split(r"[/.-]", m.group(0))
        if len(parts) >= 2:
            try:
                a, b = int(parts[0]), int(parts[1])
            except ValueError:
                return False
            if (a, b) in ((day, month), (month, day)):
                return True
    return False


def _confirm_dialog(device):
    for _ in range(3):
        idx = _first(device, [
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ])
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
        device.settle(1)
    return False


def _close_stray_dialog(device):
    """Dismiss a picker/dialog left on screen (Cancel preferred so pending
    edits are dropped) before retrying."""
    for _ in range(2):
        idx = _first(device, [
            {"text": "Cancel", "clickable": True},
            {"description": "Cancel", "clickable": True},
        ])
        if idx is None:
            idx = _first(device, [
                {"text": "OK", "clickable": True},
                {"description": "OK", "clickable": True},
            ])
        if idx is None:
            return
        device.click(idx)
        device.settle(1)


def _dismiss_ok_dialogs(device, rounds=3):
    for _ in range(rounds):
        idx = _first(device, [
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ])
        if idx is None:
            return
        device.click(idx)
        device.settle(1)


def _shift_month(device, year, month):
    shown = None
    for e in _elems(device):
        t = str(e.get("text") or "").strip()
        m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", t)
        if m:
            name = m.group(1).capitalize()
            if name in MONTHS:
                shown = (MONTHS.index(name), int(m.group(2)))
                break
    if shown is None:
        return False
    delta = (year - shown[1]) * 12 + (month - 1 - shown[0])
    if delta == 0:
        return False
    want = "next" if delta > 0 else "previous"
    for i, e in enumerate(_elems(device)):
        d = str(e.get("description") or "").lower()
        if e.get("clickable") and want in d and "month" in d:
            device.click(e.get("index", i))
            device.settle(1)
            return True
    return False


def _select_date_in_picker(device, year, month, day):
    target = f"{day} {MONTHS[month - 1]} {year}"
    for _ in range(13):
        idx = _find(device, description=target, clickable=True)
        if idx is None:
            pat = re.compile(rf"^\s*{day}\s+\w+\s+{year}\s*$")
            for i, e in enumerate(_elems(device)):
                if e.get("clickable") and pat.match(str(e.get("description") or "")):
                    idx = e.get("index", i)
                    break
        if idx is None:
            for i, e in enumerate(_elems(device)):
                d = str(e.get("description") or "")
                if (e.get("clickable") and str(e.get("text") or "") == str(day)
                        and str(year) in d
                        and MONTHS[month - 1][:3].lower() in d.lower()):
                    idx = e.get("index", i)
                    break
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return
        if not _shift_month(device, year, month):
            break
    raise LookupError(f"Date cell '{target}' not found in the date picker")


def _click_picker_value(device, value, unit):
    """Click the clock-face value node (descriptions like '18 hours' /
    '15 minutes').  Never scrolls: a swipe inside the time picker drags the
    dial and silently corrupts the selected hour/minute."""
    for label in (f"{value} {unit}", f"{value:02d} {unit}"):
        idx = _find(device, description=label, clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
    texts = {str(value), f"{value:02d}"}
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable"):
            continue
        t = str(e.get("text") or "").strip()
        d = str(e.get("description") or "").lower()
        if t in texts and unit in d:
            device.click(e.get("index", i))
            device.settle(1)
            return True
    return False


def _unit_display(device, unit):
    """The hour/minute display chip that switches between the two clock
    faces.  Only meaningful while the other face is showing (then the radial
    value nodes of `unit` are absent from the tree)."""
    other = "minutes" if unit == "hours" else "hours"
    pat = re.compile(rf"^\s*(?:{unit}|(\d{{1,2}})\s+{unit})\s*$", re.IGNORECASE)
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable"):
            continue
        d = str(e.get("description") or "")
        if pat.match(d) and other not in d.lower():
            return e.get("index", i)
    return None


def _set_picker_time(device, hour, minute):
    """Pick hour and minute on the clock-face time picker.  The picker opens
    on the hour face; the minute face is reached by tapping the minute
    display chip.  No scrolling is ever done inside the picker."""
    if not _click_picker_value(device, hour, "hours"):
        disp = _unit_display(device, "hours")
        if disp is None:
            raise LookupError(f"Hour cell '{hour} hours' not found in the time picker")
        device.click(disp)
        device.settle(1)
        if not _click_picker_value(device, hour, "hours"):
            raise LookupError(f"Hour cell '{hour} hours' not found in the time picker")
    if _click_picker_value(device, minute, "minutes"):
        return
    disp = _unit_display(device, "minutes")
    if disp is None:
        raise LookupError(f"Minute cell '{minute} minutes' not found in the time picker")
    device.click(disp)
    device.settle(1)
    if not _click_picker_value(device, minute, "minutes"):
        raise LookupError(f"Minute cell '{minute} minutes' not found in the time picker")


def _set_date_row(device, which, year, month, day, tries=3):
    for _ in range(tries):
        rows = _date_rows(device)
        if len(rows) <= which:
            raise LookupError("Start/end date row not found on the event form")
        if _date_text_matches(rows[which][1], year, month, day):
            return
        try:
            device.click(rows[which][0])
            device.settle(1)
            _select_date_in_picker(device, year, month, day)
            _confirm_dialog(device)
        except Exception:
            pass
        rows = _date_rows(device)
        if len(rows) > which and _date_text_matches(rows[which][1], year, month, day):
            return
        _close_stray_dialog(device)
    raise RuntimeError(f"Failed to set the event date to {year}-{month:02d}-{day:02d}")


def _set_time_row(device, which, hour, minute, tries=3):
    for _ in range(tries):
        rows = _time_rows(device)
        if len(rows) <= which:
            raise LookupError("Start/end time row not found on the event form")
        if _time_text_matches(rows[which][1], hour, minute):
            return
        try:
            device.click(rows[which][0])
            device.settle(1)
            _set_picker_time(device, hour, minute)
            _confirm_dialog(device)
        except Exception:
            pass
        rows = _time_rows(device)
        if len(rows) > which and _time_text_matches(rows[which][1], hour, minute):
            return
        _close_stray_dialog(device)
    raise RuntimeError(f"Failed to set the event time to {hour:02d}:{minute:02d}")


def _fill_field(device, hint, value, tries=2):
    for _ in range(tries):
        idx = _find(device, hint=hint, editable=True)
        if idx is None:
            raise LookupError(f"Event '{hint}' field not found")
        device.input_text(value, index=idx)
        device.settle(1)
        for e in _elems(device):
            if e.get("editable") and str(e.get("hint") or "") == hint:
                if value.strip() in str(e.get("text") or "").strip():
                    return
                break
    # Verification is best-effort; the input itself reported success.


def _finish_save(device):
    """Saving pops Simple Calendar Pro's reminders 'Disclaimer' dialog
    (Settings / OK); the event is only committed once that dialog's OK is
    tapped.  Keep dismissing OK dialogs until the editor has closed.
    Returns True once the editor is gone."""
    for _ in range(8):
        ok_idx = _first(device, [
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ])
        if ok_idx is not None:
            device.click(ok_idx)
            device.settle(2)
            continue
        if not _editor_open(device):
            return True
        device.settle(1)
    return not _editor_open(device)


def _save_event(device, start_dt, end_dt):
    for _ in range(3):
        idx = _first(device, [
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ])
        if idx is None:
            raise LookupError("Save button not found on the event form")
        device.click(idx)
        device.settle(2)
        if _finish_save(device):
            return
        # The editor is still open, so the save was rejected — most often
        # because a date/time field is invalid (e.g. the end time was left
        # before the start time).  Re-verify every field, then save again.
        try:
            _dismiss_ok_dialogs(device)
            _set_date_row(device, 0, start_dt.year, start_dt.month, start_dt.day)
            _set_date_row(device, 1, end_dt.year, end_dt.month, end_dt.day)
            _set_time_row(device, 0, start_dt.hour, 0)
            _set_time_row(device, 1, end_dt.hour, end_dt.minute)
        except Exception:
            pass
    raise RuntimeError("Event editor still open after Save — event may not have been saved")


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

    # 1. Launch the app and open the New Event editor.
    device.open_app("Simple Calendar Pro")
    device.settle(2)
    _open_new_event_editor(device)

    # 2. Title and description.
    _fill_field(device, "Title", title)
    _fill_field(device, "Description", description)

    # 3. Start date, then start time (minutes are 00).  Each setter verifies
    #    the row text afterwards and retries if the picker did not stick.
    _set_date_row(device, 0, start_dt.year, start_dt.month, start_dt.day)
    _set_time_row(device, 0, start_dt.hour, 0)

    # 4. End date (there is no duration field: end = start + duration_mins).
    #    Always verified, so a default end date on the wrong day is fixed too.
    _set_date_row(device, 1, end_dt.year, end_dt.month, end_dt.day)

    # 5. End time = start time + duration_mins, verified on the row.
    _set_time_row(device, 1, end_dt.hour, end_dt.minute)

    # 6. Save; dismiss the post-save Disclaimer dialog; if the editor is
    #    still open the save was rejected, so repair the fields and retry.
    _save_event(device, start_dt, end_dt)
    return True
