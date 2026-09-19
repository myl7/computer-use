import datetime
import re

APP_NAME = "Simple Calendar Pro"

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "year of the event date"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "month of the event date (1-12)"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "day of month of the event date"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "start hour of the event (24-hour clock)"},
    "duration_mins": {"type": "int", "required": True, "min": 1,
                      "description": "length of the event in minutes"},
    "event_title": {"type": "str", "required": True,
                    "description": "title of the event"},
    "event_description": {"type": "str", "required": True,
                          "description": "description of the event"},
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b", re.IGNORECASE)
_YEAR_TOKEN_RE = re.compile(r"\b(\d{4})\b")
_TIME_RE = re.compile(r"^(\d{1,2})\s*[:.]\s*(\d{2})(?:\s*([ap])\.?m\.?)?$")
_YEAR_ITEM_RE = re.compile(r"^\d{4}$")


def _norm(value):
    return (value or "").strip().lower()


def _text(element):
    return element.get("text") or ""


def _clickable(element):
    return bool(element.get("clickable"))


def _editable(element):
    return bool(element.get("editable"))


def _parse_time(text):
    m = _TIME_RE.match((text or "").strip().lower())
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ap = m.group(3)
    if ap:
        if ap == "p" and hour != 12:
            hour += 12
        if ap == "a" and hour == 12:
            hour = 0
    return hour, minute


def _parse_month_year(text):
    t = (text or "").strip()
    m = _MONTH_RE.search(t)
    if m:
        ym = _YEAR_TOKEN_RE.search(t)
        if ym:
            return _MONTHS[m.group(0).lower()], int(ym.group(1))
        return None
    m = re.match(r"^\s*(\d{1,2})\s*[./-]\s*(\d{4})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^\s*(\d{4})\s*[./-]\s*(\d{1,2})\b", t)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None


def _looks_like_date(text):
    if not text:
        return False
    if _MONTH_RE.search(text):
        return True
    if _YEAR_TOKEN_RE.search(text):
        return True
    if re.search(r"\b\d{1,2}\s*[./-]\s*\d{1,2}(?:\s*[./-]\s*\d{2,4})?\b", text):
        return True
    return False


def _ok_index(els):
    for e in els:
        if _clickable(e) and _norm(_text(e)) == "ok":
            return e["index"]
    return None


def _header_month_year(els):
    found = []
    for e in els:
        p = _parse_month_year(_text(e))
        if p and p[1] is not None:
            found.append(p)
    if not found:
        return None
    return found[len(found) // 2]


def _on_event_screen(els):
    for e in els:
        if _editable(e) and ("title" in _norm(e.get("hint"))
                             or "title" in _norm(_text(e))):
            return True
    return False


def _goto_new_event(device):
    for _ in range(10):
        els = device.elements()
        if _on_event_screen(els):
            return
        target = None
        for e in els:
            if _clickable(e) and "new event" in _norm(_text(e)):
                target = e["index"]
                break
        if target is None:
            for e in els:
                if not _clickable(e):
                    continue
                d = _norm(e.get("description"))
                if "new event" in d or d in ("add", "new", "plus"):
                    target = e["index"]
                    break
        if target is not None:
            device.click(index=target)
        else:
            device.wait()
    raise RuntimeError("could not open the new-event screen")


def _find_editable(device, keyword):
    hint_match = None
    text_match = None
    for e in device.elements():
        if not _editable(e):
            continue
        if hint_match is None and keyword in _norm(e.get("hint")):
            hint_match = e["index"]
        if text_match is None and keyword in _norm(_text(e)):
            text_match = e["index"]
    return hint_match if hint_match is not None else text_match


def _editable_indexes(device):
    return [e["index"] for e in device.elements() if _editable(e)]


def _scan_rows(device):
    dates, times = [], []
    for e in device.elements():
        if not _clickable(e) or _editable(e):
            continue
        t = _text(e).strip()
        if _parse_time(t):
            times.append(e)
        elif _looks_like_date(t):
            dates.append(e)
    return dates, times


def _date_time_rows(device):
    dates, times = _scan_rows(device)
    if not dates or len(times) < 2:
        device.scroll("down")
        dates, times = _scan_rows(device)
    return dates, times


def _wait_for_ok(device, polls=10):
    for _ in range(polls):
        if _ok_index(device.elements()) is not None:
            return True
        device.wait()
    return False


def _date_row_shows(device, month, year, day, position=0):
    rows = []
    for e in device.elements():
        if _clickable(e) and not _editable(e) and _looks_like_date(_text(e).strip()):
            rows.append(e)
    if position >= len(rows):
        return False
    t = _text(rows[position])
    if str(year) not in t and "%02d" % (year % 100) not in t:
        return False
    if not re.search(r"\b%d\b" % day, t):
        return False
    m = _MONTH_RE.search(t)
    if m:
        return _MONTHS[m.group(0).lower()] == month
    nums = [int(x) for x in re.findall(r"\d+", t)]
    return month in nums


def _step_month(device, els, forward):
    keys = ("next month", "next") if forward else (
        "previous month", "prev month", "previous", "prev")
    for e in els:
        d = _norm(e.get("description"))
        if _clickable(e) and any(k in d for k in keys):
            device.click(index=e["index"])
            return
    device.scroll("left" if forward else "right")


def _drive_date_dialog(device, year, month, day):
    for _ in range(120):
        els = device.elements()
        if _ok_index(els) is None and _header_month_year(els) is None:
            device.wait()
            continue

        year_items = [e for e in els if _YEAR_ITEM_RE.match(_text(e).strip())]
        if len(year_items) >= 3:
            target = [e for e in year_items if _text(e).strip() == str(year)]
            if target:
                device.click(index=target[0]["index"])
                device.wait()
                continue
            years = sorted(int(_text(e).strip()) for e in year_items)
            device.scroll("down" if year > years[-1] else "up")
            device.wait()
            continue

        header = _header_month_year(els)
        if header is None:
            device.wait()
            continue
        cur_m, cur_y = header

        if cur_y != year:
            btn = None
            for e in els:
                if _clickable(e) and _text(e).strip() == str(cur_y):
                    btn = e["index"]
                    break
            if btn is None:
                device.wait()
                continue
            device.click(index=btn)
            device.wait()
            continue

        if cur_m != month:
            before = (cur_m, cur_y)
            forward = month > cur_m
            _step_month(device, els, forward)
            device.wait()
            after = _header_month_year(device.elements())
            if after == before:
                _step_month(device, device.elements(), not forward)
                device.wait()
            continue

        cells = [e for e in els if _clickable(e) and _text(e).strip() == str(day)]
        if not cells:
            cells = [e for e in els if _text(e).strip() == str(day)]
        if not cells:
            device.wait()
            continue
        if len(cells) == 3:
            pick = cells[1]
        elif day <= 24:
            pick = cells[0]
        else:
            pick = cells[-1]
        device.click(index=pick["index"])
        device.wait()
        ok = _ok_index(device.elements())
        if ok is None:
            device.wait()
            ok = _ok_index(device.elements())
        if ok is not None:
            device.click(index=ok)
            device.wait()
        return
    raise RuntimeError("could not set the date in the picker")


def _open_date_dialog_and_set(device, year, month, day, row_index, probe):
    for _ in range(2):
        device.click(index=row_index)
        if _wait_for_ok(device):
            _drive_date_dialog(device, year, month, day)
            device.wait()
            if probe():
                return
    raise RuntimeError("failed to set the event date in the picker")


def _pick_by_exact_text(device, els, texts):
    cands = [e for e in els if _text(e).strip() in texts]
    if not cands:
        return False
    pref = [e for e in cands if _clickable(e)] or cands
    device.click(index=pref[-1]["index"])
    return True


def _type_over(device, index, value):
    device.click(index=index)
    device.settle(1)
    try:
        for _ in range(6):
            device.adb_shell("input", "keyevent", "KEYCODE_DEL")
    except Exception:
        pass
    device.input_text(value, index=index)


def _fill_text_time(device, fields, hour, minute):
    _type_over(device, fields[0]["index"], str(hour))
    _type_over(device, fields[1]["index"], str(minute))
    ok = _ok_index(device.elements())
    if ok is None:
        device.wait()
        ok = _ok_index(device.elements())
    if ok is None:
        raise RuntimeError("time picker OK button not found")
    device.click(index=ok)
    device.wait()


def _toggle_text_mode_and_fill(device, hour, minute):
    els = device.elements()
    toggle = None
    keys = ("keyboard", "text", "input", "type", "edit")
    for e in els:
        d = _norm(e.get("description"))
        if _clickable(e) and any(k in d for k in keys):
            toggle = e["index"]
            break
    if toggle is not None:
        device.click(index=toggle)
        device.wait()
        els = device.elements()
    fields = [e for e in els if _editable(e)]
    if len(fields) < 2:
        raise RuntimeError("could not set the time in the picker")
    _fill_text_time(device, fields, hour, minute)


def _drive_time_dialog(device, hour, minute):
    if not _wait_for_ok(device):
        raise RuntimeError("time picker did not open")
    hour_texts = {str(hour), "%02d" % hour}
    minute_texts = {str(minute), "%02d" % minute}

    for _ in range(2):
        els = device.elements()
        fields = [e for e in els if _editable(e)]
        if len(fields) >= 2:
            _fill_text_time(device, fields, hour, minute)
            return
        if not _pick_by_exact_text(device, els, hour_texts):
            break
        device.wait()
        els = device.elements()
        if _pick_by_exact_text(device, els, minute_texts):
            device.wait()
            ok = _ok_index(device.elements())
            if ok is None:
                device.wait()
                ok = _ok_index(device.elements())
            if ok is not None:
                device.click(index=ok)
                device.wait()
            return
    _toggle_text_mode_and_fill(device, hour, minute)


def _time_row_shows(device, hour, minute):
    for e in device.elements():
        if _clickable(e) and not _editable(e):
            if _parse_time(_text(e)) == (hour % 24, minute):
                return True
    return False


def _set_time_on_row(device, row_index, hour, minute):
    for _ in range(2):
        device.click(index=row_index)
        _drive_time_dialog(device, hour, minute)
        device.wait()
        if _time_row_shows(device, hour, minute):
            return
    raise RuntimeError("failed to set the time %02d:%02d" % (hour, minute))


def _confirm_saved(device):
    for _ in range(6):
        els = device.elements()
        if not _on_event_screen(els):
            return True
        device.wait()
    raise RuntimeError("the event screen is still open after saving")


def _save_event(device):
    els = device.elements()
    for e in els:
        if _clickable(e) and "save" in _norm(e.get("description")):
            device.click(index=e["index"])
            return _confirm_saved(device)
    for e in els:
        if _clickable(e) and "save" in _norm(_text(e)):
            device.click(index=e["index"])
            return _confirm_saved(device)
    for e in els:
        d = _norm(e.get("description"))
        if _clickable(e) and ("more options" in d or d == "more" or d == "menu"):
            device.click(index=e["index"])
            device.wait()
            for ee in device.elements():
                if "save" in _norm(_text(ee)):
                    device.click(index=ee["index"])
                    return _confirm_saved(device)
            break
    raise RuntimeError("save button not found")


def program(device, binding):
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

    device.open_app(APP_NAME)
    _goto_new_event(device)

    title_idx = _find_editable(device, "title")
    if title_idx is None:
        editables = _editable_indexes(device)
        if not editables:
            raise RuntimeError("title field not found")
        title_idx = editables[0]
    device.input_text(event_title, index=title_idx)

    desc_idx = _find_editable(device, "description")
    if desc_idx is None:
        editables = _editable_indexes(device)
        desc_idx = editables[1] if len(editables) > 1 else None
    if desc_idx is None:
        raise RuntimeError("description field not found")
    device.input_text(event_description, index=desc_idx)

    def start_date_ok():
        return _date_row_shows(device, month, year, day, 0)

    dates, times = _date_time_rows(device)
    if not dates:
        raise RuntimeError("start date row not found")
    _open_date_dialog_and_set(device, year, month, day,
                              dates[0]["index"], start_date_ok)

    dates, times = _date_time_rows(device)
    if len(times) < 2:
        raise RuntimeError("start/end time rows not found")
    _set_time_on_row(device, times[0]["index"], start_dt.hour, start_dt.minute)

    dates, times = _date_time_rows(device)
    if len(times) < 2:
        raise RuntimeError("end time row not found")
    _set_time_on_row(device, times[1]["index"], end_dt.hour, end_dt.minute)

    if (end_dt.year, end_dt.month, end_dt.day) != (year, month, day):
        def end_date_ok():
            return _date_row_shows(device, end_dt.month, end_dt.year,
                                   end_dt.day, 1)

        dates, times = _date_time_rows(device)
        if len(dates) < 2:
            raise RuntimeError("end date row not found")
        _open_date_dialog_and_set(device, end_dt.year, end_dt.month,
                                  end_dt.day, dates[1]["index"], end_date_ok)

    _save_event(device)
    return True
