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


def _find_date_rows(device):
    rows = []
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable") or e.get("editable"):
            continue
        t = str(e.get("text") or "").strip()
        if not t or ":" in t:
            continue
        low = t.lower()
        if any(m.lower() in low for m in MONTHS) or _NUMERIC_DATE_RE.search(t):
            rows.append(e.get("index", i))
    return rows


def _find_time_rows(device):
    rows = []
    for i, e in enumerate(_elems(device)):
        if not e.get("clickable") or e.get("editable"):
            continue
        t = str(e.get("text") or "").strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            rows.append(e.get("index", i))
    return rows


def _confirm_dialog(device):
    for _ in range(4):
        idx = _first(device, [
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ])
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
        device.settle(1)
    raise RuntimeError("Picker dialog 'OK' button not found")


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


def _click_picker_value(device, value, unit, scrolls=2):
    label = f"{value} {unit}"
    for s in range(scrolls + 1):
        idx = _find(device, description=label, clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return True
        for i, e in enumerate(_elems(device)):
            d = str(e.get("description") or "").lower()
            t = str(e.get("text") or "").strip()
            if e.get("clickable") and t == str(value) and unit in d:
                device.click(e.get("index", i))
                device.settle(1)
                return True
        if s < scrolls:
            device.scroll("down")
            device.settle(1)
    return False


def _picker_switch(device, unit):
    other = "minutes" if unit == "hours" else "hours"
    for i, e in enumerate(_elems(device)):
        d = str(e.get("description") or "").lower()
        if e.get("clickable") and unit in d and other not in d:
            return e.get("index", i)
    return None


def _set_picker_time(device, hour, minute, minute_required=True):
    if not _click_picker_value(device, hour, "hours", scrolls=1):
        sel = _picker_switch(device, "hours")
        if sel is None:
            raise LookupError(f"Hour cell '{hour} hours' not found in the time picker")
        device.click(sel)
        device.settle(1)
        if not _click_picker_value(device, hour, "hours", scrolls=1):
            raise LookupError(f"Hour cell '{hour} hours' not found in the time picker")
    if _click_picker_value(device, minute, "minutes", scrolls=1):
        return
    sel = _picker_switch(device, "minutes")
    if sel is not None:
        device.click(sel)
        device.settle(1)
        if _click_picker_value(device, minute, "minutes", scrolls=2):
            return
    if minute_required:
        raise LookupError(f"Minute cell '{minute} minutes' not found in the time picker")


def _dismiss_post_save_dialogs(device):
    """Saving an event can pop Simple Calendar Pro's reminders 'Disclaimer'
    dialog (Settings / OK). The event is only committed once that dialog's
    OK button is tapped, so keep dismissing OK dialogs until none is left."""
    for _ in range(6):
        ok_idx = _first(device, [
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ])
        if ok_idx is None:
            return
        if _editor_open(device) and not _disclaimer_dialog_up(device):
            return
        device.click(ok_idx)
        device.settle(2)


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

    # 2. Title.
    idx = _find(device, hint="Title", editable=True)
    if idx is None:
        raise LookupError("Event 'Title' field not found")
    device.input_text(title, index=idx)
    device.settle(1)

    # 3. Description.
    idx = _find(device, hint="Description", editable=True)
    if idx is None:
        raise LookupError("Event 'Description' field not found")
    device.input_text(description, index=idx)
    device.settle(1)

    # 4. Start date via the date picker dialog.
    date_rows = _find_date_rows(device)
    if not date_rows:
        raise LookupError("Start-date row not found on the event form")
    device.click(date_rows[0])
    device.settle(1)
    _select_date_in_picker(device, year, month, day)
    _confirm_dialog(device)

    # 5. Start time (hour from binding, minutes 00).
    time_rows = _find_time_rows(device)
    if len(time_rows) < 2:
        raise LookupError("Start/end time rows not found on the event form")
    device.click(time_rows[0])
    device.settle(1)
    _set_picker_time(device, hour, 0, minute_required=False)
    _confirm_dialog(device)

    # 6. If the duration crosses midnight, the end date has to move too.
    if end_dt.date() != start_dt.date():
        date_rows = _find_date_rows(device)
        if len(date_rows) >= 2:
            device.click(date_rows[1])
            device.settle(1)
            _select_date_in_picker(device, end_dt.year, end_dt.month, end_dt.day)
            _confirm_dialog(device)

    # 7. End time = start time + duration_mins (no duration field exists).
    time_rows = _find_time_rows(device)
    if len(time_rows) < 2:
        raise LookupError("End-time row not found on the event form")
    device.click(time_rows[1])
    device.settle(1)
    _set_picker_time(device, end_dt.hour, end_dt.minute, minute_required=True)
    _confirm_dialog(device)

    # 8. Save the event.
    idx = _first(device, [
        {"description": "Save", "clickable": True},
        {"text": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
    ])
    if idx is None:
        raise LookupError("Save button not found on the event form")
    device.click(idx)
    device.settle(2)

    # 9. Saving can pop the reminders 'Disclaimer' dialog (Settings / OK);
    #    the event is only committed once that dialog's OK is tapped.
    _dismiss_post_save_dialogs(device)

    # 10. If the editor is still up, the save did not go through: retry once.
    if _editor_open(device):
        idx = _first(device, [
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ])
        if idx is not None:
            device.click(idx)
            device.settle(2)
            _dismiss_post_save_dialogs(device)

    if _editor_open(device):
        raise RuntimeError("Event editor still open after Save — event may not have been saved")
    return True
