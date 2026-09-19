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


def _is_time_text(t):
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", (t or "").strip()))


def _confirm_picker_ok(device, what):
    if device.find(text="OK", clickable=True) is not None:
        device.click(text="OK", clickable=True)
        device.settle(1)
        return
    idx = None
    cancel_seen = False
    for e in device.elements():
        if not e.get("clickable"):
            continue
        t = str(e.get("text", "")).strip().lower()
        if t == "ok" and idx is None:
            idx = e["index"]
        elif t == "cancel":
            cancel_seen = True
    if idx is not None:
        device.click(idx)
        device.settle(1)
        return
    if cancel_seen:
        raise RuntimeError(f"{what} 'OK' button not found")
    # Neither OK nor Cancel visible: the dialog already closed by itself.


def _event_editor_open(device):
    if device.find(hint="Title", editable=True) is not None:
        return True
    for e in device.elements():
        h = str(e.get("hint", "")).strip().lower()
        if e.get("editable") and ("title" in h or "description" in h):
            return True
        if e.get("clickable") and str(e.get("description", "")).strip().lower() == "save":
            return True
    return False


def _open_new_event(device):
    for attempt in range(4):
        if _event_editor_open(device):
            return
        # Click by criteria so the tap is resolved against a fresh element
        # list: a stale index once tapped the calendar area next to the
        # 'Task' chip instead of the floating 'New Event' button.
        if device.find(description="New Event", clickable=True) is not None:
            device.click(description="New Event", clickable=True)
            device.settle(2)
            continue
        idx = None
        for e in device.elements():
            if e.get("clickable") and str(e.get("description", "")).strip() == "New Event":
                idx = e["index"]
                break
        if idx is not None:
            device.click(idx)
            device.settle(2)
            continue
        # 'New Event' not visible: something may cover the app; recover.
        device.navigate_back()
        device.settle(1)
        if attempt >= 1:
            device.open_app("Simple Calendar Pro")
            device.settle(2)
    if not _event_editor_open(device):
        raise LookupError("Floating 'New Event' button not found")


def _fill_text_field(device, hint, value, what):
    if device.find(hint=hint, editable=True) is not None:
        device.input_text(value, hint=hint, editable=True)
        return
    idx = None
    low = hint.lower()
    for require_editable in (True, False):
        for e in device.elements():
            h = str(e.get("hint", "")).strip().lower()
            if h.startswith(low) and (e.get("editable") or not require_editable):
                idx = e["index"]
                break
        if idx is not None:
            break
    if idx is None:
        raise LookupError(f"{what} field not found")
    device.input_text(value, index=idx)


def _find_date_row(device):
    cands = []
    for e in device.elements():
        t = str(e.get("text", "")).strip()
        if not t or not e.get("clickable") or e.get("editable"):
            continue
        if _is_time_text(t) or "before" in t.lower():
            continue
        cands.append(e)
    for e in cands:
        low = e["text"].lower()
        if any(m.lower() in low for m in MONTHS):
            return e
    for e in cands:
        if any(ch.isdigit() for ch in e["text"]):
            return e
    if cands:
        return cands[0]
    for e in device.elements():
        t = str(e.get("text", "")).strip()
        if t and not e.get("editable") and not _is_time_text(t):
            if any(m.lower() in t.lower() for m in MONTHS):
                return e
    return None


def _shown_month_year(els):
    counts = {}
    for e in els:
        s = (str(e.get("description", "")) + " " + str(e.get("text", ""))).lower()
        ym = re.search(r"\b(\d{4})\b", s)
        if not ym:
            continue
        for i, m in enumerate(MONTHS):
            low = m.lower()
            if low in s or low[:3] in s:
                key = (i + 1, int(ym.group(1)))
                counts[key] = counts.get(key, 0) + 1
                break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _step_month(device, els, year, month, attempt):
    nxt = prv = None
    for e in els:
        if not e.get("clickable"):
            continue
        d = str(e.get("description", "")).lower()
        if nxt is None and "next" in d:
            nxt = e["index"]
        elif prv is None and ("previous" in d or "prev" in d):
            prv = e["index"]
    shown = _shown_month_year(els)
    if shown is None:
        forward = attempt % 2 == 0
    else:
        forward = (year - shown[1]) * 12 + (month - shown[0]) > 0
    if forward and nxt is not None:
        device.click(nxt)
    elif not forward and prv is not None:
        device.click(prv)
    else:
        device.scroll("left" if forward else "right")
    device.settle(1)


def _pick_day_in_date_picker(device, year, month, day):
    month_name = MONTHS[month - 1]
    want = f"{day} {month_name} {year}".lower()
    low_month = month_name.lower()
    abbr = low_month[:3]
    for attempt in range(40):
        els = device.elements()
        for e in els:
            if str(e.get("description", "")).strip().lower() == want:
                device.click(e["index"])
                return
        shown = _shown_month_year(els)
        if shown is None or shown == (month, year):
            for e in els:
                t = str(e.get("text", "")).strip()
                if t == str(day) or t == f"{day:02d}":
                    device.click(e["index"])
                    return
            for e in els:
                d = str(e.get("description", "")).lower()
                t = str(e.get("text", "")).strip()
                if (low_month in d or abbr in d) and re.search(rf"\b{day}\b", d) \
                        and (not t or t == str(day) or t == f"{day:02d}"):
                    device.click(e["index"])
                    return
            if shown == (month, year):
                raise LookupError(f"Day {day} cell not found in the date picker")
        _step_month(device, els, year, month, attempt)
    raise LookupError(f"Date cell for '{day} {month_name} {year}' not found on screen")


def _set_start_date(device, year, month, day):
    month_name = MONTHS[month - 1]
    low_month = month_name.lower()
    for _ in range(2):
        row = _find_date_row(device)
        if row is None:
            raise LookupError("Start-date field not found on the event form")
        t = str(row.get("text", "")).strip()
        ym = re.search(r"\b(\d{4})\b", t)
        if ym is not None and int(ym.group(1)) == year and low_month in t.lower() \
                and re.search(rf"\b{day}\b", t):
            return
        if device.find(text=t, clickable=True) is not None:
            device.click(text=t, clickable=True)
        else:
            device.click(row["index"])
        device.settle(1)
        _pick_day_in_date_picker(device, year, month, day)
        _confirm_picker_ok(device, "Date-picker")
        row2 = _find_date_row(device)
        if row2 is not None:
            t2 = str(row2.get("text", "")).strip()
            ym2 = re.search(r"\b(\d{4})\b", t2)
            if (ym2 is None or int(ym2.group(1)) == year) and low_month in t2.lower() \
                    and re.search(rf"\b{day}\b", t2):
                return
    raise RuntimeError(f"Could not set the start date to {day} {month_name} {year}")


def _time_rows(device):
    rows = []
    for e in device.elements():
        t = str(e.get("text", "")).strip()
        if not t or not e.get("clickable") or not _is_time_text(t):
            continue
        d = str(e.get("description", "")).strip()
        if d and _is_time_text(d) and d != t:
            continue
        rows.append(e)
    return rows


def _click_row(device, row, rows):
    t = str(row.get("text", "")).strip()
    others = [str(r.get("text", "")).strip() for r in rows if r is not row]
    if t not in others and device.find(text=t, clickable=True) is not None:
        device.click(text=t, clickable=True)
    else:
        device.click(row["index"])


def _pick_hour(device, hour):
    hours = [hour] + ([24] if hour == 0 else [])
    descs = []
    texts = []
    for h in hours:
        descs.extend([f"{h} hours", f"{h:02d} hours"])
        texts.extend([str(h), f"{h:02d}"])
    for d in descs:
        if device.find(description=d, clickable=True) is not None:
            device.click(description=d, clickable=True)
            device.settle(1)
            return
    for e in device.elements():
        if not e.get("clickable"):
            continue
        d = str(e.get("description", "")).lower()
        if "hour" in d and any(re.search(rf"\b{h}\b", d) for h in hours):
            device.click(e["index"])
            device.settle(1)
            return
    for t in texts:
        if device.find(text=t, clickable=True) is not None:
            device.click(text=t, clickable=True)
            device.settle(1)
            return
    raise LookupError(f"Hour {hour} cell not found in time picker")


def _pick_minute(device, minute, required):
    descs = [f"{minute} minutes", f"{minute:02d} minutes"]
    texts = [str(minute), f"{minute:02d}"]
    for _ in range(3):
        for d in descs:
            if device.find(description=d, clickable=True) is not None:
                device.click(description=d, clickable=True)
                device.settle(1)
                return
        for e in device.elements():
            t = str(e.get("text", "")).strip()
            d = str(e.get("description", "")).lower()
            if e.get("clickable") and t in texts and ("minute" in d or not d):
                device.click(e["index"])
                device.settle(1)
                return
        switched = False
        for e in device.elements():
            d = str(e.get("description", "")).lower()
            if e.get("clickable") and "minute" in d and "hour" not in d:
                device.click(e["index"])
                switched = True
                break
        if switched:
            device.settle(1)
            continue
        device.scroll("down")
        device.settle(1)
    if required:
        raise LookupError(f"Minute option '{minute} minutes' not found in time picker")


def _set_time_row(device, which, target_str, hour, minute):
    need = 2 if which == "end" else 1
    for _ in range(3):
        rows = _time_rows(device)
        if len(rows) < need:
            device.scroll("down")
            device.settle(1)
            rows = _time_rows(device)
            if len(rows) < need:
                continue
        row = rows[0] if which == "start" else rows[-1]
        if str(row.get("text", "")).strip() == target_str:
            return
        _click_row(device, row, rows)
        device.settle(1)
        _pick_hour(device, hour)
        try:
            initial_minute = int(str(row.get("text", "")).strip().split(":")[1])
        except Exception:
            initial_minute = -1
        if initial_minute != minute:
            _pick_minute(device, minute, required=(which == "end"))
        _confirm_picker_ok(device, "Time picker")
        rows2 = _time_rows(device)
        if len(rows2) >= need:
            row2 = rows2[0] if which == "start" else rows2[-1]
            if str(row2.get("text", "")).strip() == target_str:
                return
    raise RuntimeError(f"Could not set the {which} time to {target_str}")


def _set_end_date_if_needed(device, end_dt):
    rows = []
    for e in device.elements():
        t = str(e.get("text", "")).strip()
        if t and e.get("clickable") and not e.get("editable") \
                and not _is_time_text(t) and "before" not in t.lower() \
                and any(m.lower() in t.lower() for m in MONTHS):
            rows.append(e)
    if len(rows) < 2:
        return
    try:
        device.click(rows[1]["index"])
        device.settle(1)
        _pick_day_in_date_picker(device, end_dt.year, end_dt.month, end_dt.day)
        _confirm_picker_ok(device, "Date-picker")
    except Exception:
        pass


def _save_event(device):
    for _ in range(2):
        if device.find(description="Save", clickable=True) is not None:
            device.click(description="Save", clickable=True)
        else:
            idx = None
            for e in device.elements():
                if not e.get("clickable"):
                    continue
                if "save" in str(e.get("description", "")).lower() \
                        or str(e.get("text", "")).strip().lower() == "save":
                    idx = e["index"]
                    break
            if idx is None:
                raise LookupError("Save button not found on the event form")
            device.click(idx)
        device.settle(2)
        if not _event_editor_open(device):
            return
    raise RuntimeError("Event editor did not close after saving")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = binding["event_title"]
    description = binding["event_description"]

    # No duration field: the END time is the start time plus duration_mins.
    end_dt = datetime(year, month, day, hour, 0) + timedelta(minutes=duration)
    end_h, end_m = end_dt.hour, end_dt.minute
    start_str = f"{hour:02d}:00"
    end_str = f"{end_h:02d}:{end_m:02d}"

    # ---- Open app ----
    device.open_app("Simple Calendar Pro")
    device.settle(2)

    # ---- Open the New Event editor (criteria click resolves a fresh index;
    # a stale index once hit the calendar area instead of the FAB) ----
    _open_new_event(device)

    # ---- Title, then Description (text fields come first on the form) ----
    _fill_text_field(device, "Title", title, "Title")
    _fill_text_field(device, "Description", description, "Description")

    # ---- Start date (picker dialog, confirmed with OK) ----
    _set_start_date(device, year, month, day)

    # ---- Start time (minutes default to 00) ----
    _set_time_row(device, "start", start_str, hour, 0)

    # ---- End time = start + duration ----
    if (end_dt.year, end_dt.month, end_dt.day) != (year, month, day):
        _set_end_date_if_needed(device, end_dt)
    _set_time_row(device, "end", end_str, end_h, end_m)

    # ---- Save ----
    _save_event(device)
    return True
