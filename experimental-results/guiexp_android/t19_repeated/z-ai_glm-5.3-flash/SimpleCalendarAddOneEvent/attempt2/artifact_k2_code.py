import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "min": 1970, "max": 2100,
             "description": "Year of the event start date"},
    "month": {"type": "int", "min": 1, "max": 12,
              "description": "Month of the event start date (1-12)"},
    "day": {"type": "int", "min": 1, "max": 31,
            "description": "Day of month of the event start date"},
    "hour": {"type": "int", "min": 0, "max": 23,
             "description": "Event start hour on a 24h clock"},
    "duration_mins": {"type": "int", "min": 1, "max": 43200,
                      "description": "Event duration in minutes"},
    "event_title": {"type": "str", "description": "Title of the calendar event"},
    "event_description": {"type": "str", "description": "Description of the calendar event"},
}

_MONTH_KEYS = ("jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec")


def _txt(e):
    return (e.get("text") or "").strip()


def _desc(e):
    return (e.get("description") or "").strip()


def _parse_month_label(t):
    m = re.fullmatch(r"([A-Za-z]+)\.?\s*,?\s*(\d{4})", t)
    if m and m.group(1)[:3].lower() in _MONTH_KEYS:
        return int(m.group(2)), _MONTH_KEYS.index(m.group(1)[:3].lower()) + 1
    m = re.fullmatch(r"(\d{4})\s+([A-Za-z]+)", t)
    if m and m.group(2)[:3].lower() in _MONTH_KEYS:
        return int(m.group(1)), _MONTH_KEYS.index(m.group(2)[:3].lower()) + 1
    return None


def _parse_time_text(t):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?:\s*([AaPp])\.?\s*[Mm]?\.?)?", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    ap = (m.group(3) or "").upper()
    if ap == "PM" and h != 12:
        h += 12
    if ap == "AM" and h == 12:
        h = 0
    return (h, mi)


def _visible_month(device):
    for e in device.elements():
        ym = _parse_month_label(_txt(e))
        if ym:
            return ym
    return None


def _find_confirm(device):
    for e in device.elements():
        t = _txt(e).upper()
        d = _desc(e).upper()
        if t in ("OK", "SET") or d in ("OK", "SET"):
            return e["index"]
    return None


def _on_event_editor(device):
    if device.find(hint="Title") is not None:
        return True
    for e in device.elements():
        if e.get("editable"):
            return True
        if (_txt(e) or _desc(e)).lower().startswith("start:"):
            return True
    return False


def _click_new_event(device, tries=5):
    for _ in range(tries):
        for e in device.elements():
            if "new event" in (_txt(e) + " " + _desc(e)).lower():
                device.click(e["index"])
                device.settle(2)
                return
        ok = _find_confirm(device)
        if ok is not None:
            device.click(ok)
            device.settle(2)
            continue
        device.scroll("down")
        device.settle(1)
    raise RuntimeError("'New Event' button not found on the main screen")


def _enter_event_editor(device, tries=6):
    for _ in range(tries):
        if _on_event_editor(device):
            return
        ok = _find_confirm(device)
        if ok is not None:
            device.click(ok)
            device.settle(2)
            continue
        clicked = False
        for e in device.elements():
            if e.get("clickable") and (_txt(e) or _desc(e)) and \
                    "new event" in (_txt(e) + " " + _desc(e)).lower():
                device.click(e["index"])
                device.settle(2)
                clicked = True
                break
        if not clicked:
            for e in device.elements():
                if e.get("clickable") and _txt(e):
                    device.click(e["index"])
                    device.settle(2)
                    clicked = True
                    break
        if not clicked:
            device.settle(1)
    if not _on_event_editor(device):
        raise RuntimeError("The new-event editor did not open")


def _editable_index(device, nth):
    edits = [e for e in device.elements() if e.get("editable")]
    if len(edits) > nth:
        return edits[nth]["index"]
    return None


def _fill_field(device, hint, value, nth):
    idx = device.find(hint=hint)
    if idx is None:
        idx = device.find(text=hint)
    if idx is None:
        idx = _editable_index(device, nth)
    if idx is None:
        device.scroll("down")
        device.settle(1)
        idx = device.find(hint=hint)
        if idx is None:
            idx = _editable_index(device, nth)
    if idx is None:
        raise RuntimeError("Input field '%s' not found" % hint)
    device.click(idx)
    device.settle(1)
    device.input_text(value, idx)
    device.settle(1)


def _looks_like_date(t):
    tl = t.lower()
    return any(k in tl for k in _MONTH_KEYS)


def _open_date_dialog(device, start):
    key = "start:" if start else "end:"
    for _attempt in range(3):
        els = device.elements()
        anchor = None
        prefix = []
        for e in els:
            if e.get("editable"):
                continue
            if (_txt(e) or _desc(e)).lower().startswith(key):
                if anchor is None:
                    anchor = e["index"]
                prefix.append(e)
        near = []
        if anchor is not None:
            for e in els:
                if e.get("editable"):
                    continue
                if anchor < e["index"] <= anchor + 4 and (
                        _looks_like_date(_txt(e)) or _looks_like_date(_desc(e))):
                    near.append(e)
        cands = ([e for e in prefix if e.get("clickable")] +
                 [e for e in prefix if not e.get("clickable")] +
                 [e for e in near if e.get("clickable")] +
                 [e for e in near if not e.get("clickable")])
        seen = set()
        for e in cands:
            if e["index"] in seen:
                continue
            seen.add(e["index"])
            device.click(e["index"])
            device.settle(2)
            if _find_confirm(device) is not None or _visible_month(device) is not None:
                return
        device.scroll("down")
        device.settle(1)
    raise RuntimeError("The date picker did not open")


def _pick_year(device, target_year):
    pill = None
    for e in device.elements():
        if re.fullmatch(r"\d{4}", _txt(e)) and e.get("clickable", True):
            pill = e["index"]
            break
    if pill is None:
        return False
    device.click(pill)
    device.settle(1)
    for _ in range(25):
        years = []
        for e in device.elements():
            t = _txt(e)
            if re.fullmatch(r"\d{4}", t):
                years.append((int(t), e["index"]))
        for y, idx in years:
            if y == target_year:
                device.click(idx)
                device.settle(1)
                return True
        if not years:
            device.scroll("down")
            device.settle(1)
            continue
        device.scroll("down" if target_year > max(y for y, _ in years) else "up")
        device.settle(1)
    return False


def _month_arrows(device):
    els = device.elements()
    prv = nxt = None
    for e in els:
        if not e.get("clickable"):
            continue
        dl = _desc(e).lower()
        if "next month" in dl:
            nxt = e["index"]
        elif "previous month" in dl or "prev month" in dl:
            prv = e["index"]
    if prv is not None and nxt is not None:
        return prv, nxt
    label_pos = None
    for i, e in enumerate(els):
        if _parse_month_label(_txt(e)):
            label_pos = i
            break
    if label_pos is None:
        return prv, nxt
    for e in els[:label_pos]:
        if e.get("clickable") and not _txt(e) and not _desc(e):
            prv = e["index"]
    for e in els[label_pos + 1:]:
        if e.get("clickable") and not _txt(e) and not _desc(e):
            nxt = e["index"]
            break
    return prv, nxt


def _navigate_to_month(device, ty, tm):
    for _ in range(30):
        vis = _visible_month(device)
        if vis is None:
            device.settle(1)
            continue
        if vis == (ty, tm):
            return True
        delta = (ty * 12 + tm) - (vis[0] * 12 + vis[1])
        prv, nxt = _month_arrows(device)
        if delta > 0 and nxt is not None:
            device.click(nxt)
        elif delta < 0 and prv is not None:
            device.click(prv)
        else:
            device.scroll("up" if delta > 0 else "down")
        device.settle(1)
    return _visible_month(device) == (ty, tm)


def _click_day_in_dialog(device, d):
    strs = {str(d), "%02d" % d}
    cands = [e for e in device.elements() if _txt(e) in strs]
    if not cands:
        raise RuntimeError("Day %d not visible in the date picker" % d)
    clickable = [e for e in cands if e.get("clickable")]
    pool = clickable or cands
    el = pool[-1] if d >= 24 else pool[0]
    device.click(el["index"])
    device.settle(1)


def _confirm_dialog(device):
    ok = _find_confirm(device)
    if ok is None:
        raise RuntimeError("No OK button found in the dialog")
    device.click(ok)
    device.settle(2)


def _set_date_in_dialog(device, y, m, d):
    vis = _visible_month(device)
    if vis is not None and vis[0] != y:
        _pick_year(device, y)
    if _visible_month(device) != (y, m):
        if not _navigate_to_month(device, y, m):
            raise RuntimeError("Date picker could not show %04d-%02d" % (y, m))
    _click_day_in_dialog(device, d)
    _confirm_dialog(device)


def _open_time_dialog(device, start):
    key = "start:" if start else "end:"
    for _attempt in range(3):
        els = device.elements()
        anchor = None
        for e in els:
            if e.get("editable"):
                continue
            if (_txt(e) or _desc(e)).lower().startswith(key):
                anchor = e["index"]
                break
        times = []
        for e in els:
            if e.get("editable"):
                continue
            if _parse_time_text(_txt(e)) or _parse_time_text(_desc(e)):
                times.append(e)
        cands = []
        if anchor is not None:
            cands += [e for e in times if e["index"] > anchor]
        cands += [e for e in times if e.get("clickable")]
        cands += times
        seen = set()
        for e in cands:
            if e["index"] in seen:
                continue
            seen.add(e["index"])
            device.click(e["index"])
            device.settle(2)
            if _find_confirm(device) is not None:
                return
    raise RuntimeError("The time picker did not open")


def _click_last_number(device, strs):
    cands = [e for e in device.elements() if _txt(e) in strs]
    if not cands:
        return False
    clickable = [e for e in cands if e.get("clickable")]
    pool = clickable or cands
    device.click(pool[-1]["index"])
    device.settle(1)
    return True


def _time_via_input_mode(device, hour, minute):
    toggle = None
    for e in device.elements():
        if not e.get("clickable"):
            continue
        blob = (_txt(e) + " " + _desc(e)).lower()
        if "input" in blob or "keyboard" in blob or "type" in blob:
            toggle = e["index"]
            break
    edits = [e for e in device.elements() if e.get("editable")]
    if toggle is None and not edits:
        raise RuntimeError("Time picker has neither dial numbers nor input fields")
    if toggle is not None:
        device.click(toggle)
        device.settle(1)
        edits = [e for e in device.elements() if e.get("editable")]
    if not edits:
        raise RuntimeError("Time picker has no editable time fields")
    if len(edits) >= 2:
        device.input_text(str(hour) if hour else "0", edits[0]["index"])
        device.settle(1)
        edits = [e for e in device.elements() if e.get("editable")]
        device.input_text("%02d" % minute, edits[-1]["index"])
        device.settle(1)
    else:
        device.input_text("%02d:%02d" % (hour, minute), edits[0]["index"])
        device.settle(1)


def _dial_time(device, hour, minute):
    ampm = [e for e in device.elements() if _txt(e).upper() in ("AM", "PM")]
    if ampm:
        want = "PM" if hour >= 12 else "AM"
        for e in ampm:
            if _txt(e).upper() == want:
                device.click(e["index"])
                device.settle(1)
                break
        h12 = hour % 12 or 12
        hour_strs = {str(h12), "%02d" % h12}
    else:
        hour_strs = {str(hour), "%02d" % hour}
    if not _click_last_number(device, hour_strs):
        _time_via_input_mode(device, hour, minute)
        return
    if minute % 5 == 0:
        if not _click_last_number(device, {str(minute), "%02d" % minute}):
            _time_via_input_mode(device, hour, minute)
    else:
        _time_via_input_mode(device, hour, minute)


def _row_time(device, start):
    key = "start:" if start else "end:"
    els = device.elements()
    anchor = None
    for e in els:
        if e.get("editable"):
            continue
        if (_txt(e) or _desc(e)).lower().startswith(key):
            anchor = e["index"]
            break
    times = []
    for e in els:
        if e.get("editable"):
            continue
        p = _parse_time_text(_txt(e)) or _parse_time_text(_desc(e))
        if p:
            times.append((e["index"], p))
    if anchor is not None:
        after = [(i, p) for i, p in times if i > anchor]
        if after:
            return after[0][1]
    if times:
        return times[0][1] if start else times[-1][1]
    return None


def _set_event_time(device, hour, minute, start):
    expected = (hour, minute)
    for attempt in range(3):
        if _row_time(device, start) == expected:
            return
        _open_time_dialog(device, start)
        if attempt < 2:
            _dial_time(device, hour, minute)
        else:
            _time_via_input_mode(device, hour, minute)
        _confirm_dialog(device)
    if _row_time(device, start) != expected:
        raise RuntimeError("Failed to set the %s time to %02d:%02d" %
                           ("start" if start else "end", hour, minute))


def _save_event(device):
    for _attempt in range(3):
        idx = None
        for e in device.elements():
            d = _desc(e).lower()
            t = _txt(e).lower()
            if (t == "save" or d in ("save", "save event")) and e.get("clickable", True):
                idx = e["index"]
                break
        if idx is None:
            more = None
            for e in device.elements():
                if e.get("clickable") and "more options" in _desc(e).lower():
                    more = e["index"]
                    break
            if more is not None:
                device.click(more)
                device.settle(2)
            for e in device.elements():
                if _txt(e).lower() == "save":
                    idx = e["index"]
                    break
        if idx is None:
            raise RuntimeError("Save control not found")
        device.click(idx)
        device.settle(2)
        if not _on_event_editor(device):
            return
    raise RuntimeError("The event was not saved (editor still open)")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

    device.open_app("Simple Calendar Pro")
    device.settle(2)

    _click_new_event(device)
    _enter_event_editor(device)

    _fill_field(device, "Title", title, 0)
    _fill_field(device, "Description", description, 1)

    _open_date_dialog(device, True)
    _set_date_in_dialog(device, start_dt.year, start_dt.month, start_dt.day)

    _open_date_dialog(device, False)
    _set_date_in_dialog(device, end_dt.year, end_dt.month, end_dt.day)

    _set_event_time(device, start_dt.hour, start_dt.minute, True)
    _set_event_time(device, end_dt.hour, end_dt.minute, False)

    _save_event(device)
    return True
