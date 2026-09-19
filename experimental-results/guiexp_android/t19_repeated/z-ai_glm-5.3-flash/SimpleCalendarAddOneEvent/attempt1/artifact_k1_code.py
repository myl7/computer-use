import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "Event year"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Event month, 1-12"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Event day of month"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Event start hour on a 24-hour clock"},
    "duration_mins": {"type": "int", "required": True, "min": 1, "max": 43200,
                      "description": "Event length in minutes"},
    "event_title": {"type": "str", "required": True,
                    "description": "Event title"},
    "event_description": {"type": "str", "required": True,
                          "description": "Event description"},
}

_APP_NAME = "Simple Calendar Pro"

_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]
_MONTH_ABBRS = [m[:3] for m in _MONTHS]

_TIME_RE = re.compile(r"^\s*\d{1,2}:\d{2}(\s*[ap]\.?m\.?)?\s*$", re.I)
_OK_WORDS = ("ok", "okay", "done", "set")
_MINUTE_DIAL_MARKERS = ("05", "25", "30", "35", "40", "45", "50", "55")


def _txt(e):
    return (e.get("text") or "").strip()


def _desc(e):
    return (e.get("description") or "").strip()


def _hint(e):
    return (e.get("hint") or "").strip()


def _is_field(e):
    if e.get("editable"):
        return True
    h = _hint(e).lower()
    return "title" in h or "description" in h


def _find(device, pred):
    for e in device.elements():
        try:
            if pred(e):
                return e["index"]
        except Exception:
            continue
    return None


def _find_all(device, pred):
    out = []
    for e in device.elements():
        try:
            if pred(e):
                out.append(e)
        except Exception:
            continue
    return out


def _month_from_text(s):
    sl = (s or "").lower()
    for i, name in enumerate(_MONTHS):
        if name in sl:
            return i + 1
    for i, ab in enumerate(_MONTH_ABBRS):
        if re.search(r"\b%s" % ab, sl):
            return i + 1
    return None


def _parse_date_text(s):
    sl = (s or "").lower()
    m = re.search(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", sl)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", sl)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:
            return (y, b, a)
        return (y, a, b)
    mo = _month_from_text(sl)
    ym = re.search(r"\b(\d{4})\b", sl)
    dm = re.search(r"\b(\d{1,2})\b", sl)
    if mo is None or ym is None or dm is None:
        return None
    return (int(ym.group(1)), mo, int(dm.group(1)))


def _parse_time_text(s):
    sl = (s or "").strip().lower()
    m = re.match(r"^(\d{1,2}):(\d{2})\s*([ap])\.?m?\.?$", sl)
    if m:
        h = int(m.group(1)) % 12
        if m.group(3) == "p":
            h += 12
        return h * 60 + int(m.group(2))
    m = re.match(r"^(\d{1,2}):(\d{2})$", sl)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _is_time_row(e):
    if _is_field(e):
        return False
    return bool(_TIME_RE.match(_txt(e)))


def _is_date_row(e):
    if _is_field(e):
        return False
    t = _txt(e)
    if not t or _TIME_RE.match(t):
        return False
    if not re.search(r"\d", t):
        return False
    if re.search(r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}", t):
        return True
    return _month_from_text(t) is not None


def _get_rows(device):
    els = device.elements()
    dates = [e for e in els if _is_date_row(e)]
    times = [e for e in els if _is_time_row(e)]
    dates_c = [e for e in dates if e.get("clickable")]
    times_c = [e for e in times if e.get("clickable")]
    return (dates_c or dates, times_c or times)


def _on_event_screen(device):
    editables = _find_all(device, lambda e: e.get("editable"))
    if len(editables) >= 2:
        return True
    return any("title" in _hint(e).lower() for e in editables)


def _dismiss_overlay(device):
    for word in ("while using the app", "only this time", "got it",
                 "no thanks", "no, thanks", "dismiss", "cancel", "ok",
                 "allow", "don't allow", "skip"):
        idx = _find(device, lambda e, w=word:
                    w in _txt(e).lower() or w in _desc(e).lower())
        if idx is not None:
            device.click(idx)
            return True
    device.navigate_back()
    return True


def _open_new_event_screen(device):
    for attempt in range(6):
        if _on_event_screen(device):
            return
        idx = _find(device, lambda e: _desc(e).lower() == "new event"
                    or _txt(e).lower() == "new event")
        if idx is not None:
            device.click(idx)
        elif attempt == 0:
            device.wait()
        elif attempt == 1:
            _dismiss_overlay(device)
        else:
            device.open_app(_APP_NAME)
    if _on_event_screen(device):
        return
    raise RuntimeError("New Event screen did not open in %s" % _APP_NAME)


def _input_field(device, value, hint_word, position):
    idx = _find(device, lambda e: e.get("editable")
                and hint_word in _hint(e).lower())
    if idx is None:
        editables = _find_all(device, lambda e: e.get("editable"))
        if len(editables) > position:
            idx = editables[position]["index"]
    if idx is None:
        raise RuntimeError("Text field for %r not found" % hint_word)
    device.input_text(value, idx)


def _click_ok(device):
    idx = _find(device, lambda e: not e.get("editable")
                and (_txt(e).lower() in _OK_WORDS
                     or _desc(e).lower() in _OK_WORDS))
    if idx is None:
        raise RuntimeError("OK button not found in dialog")
    device.click(idx)


def _dismiss_stuck_ok(device):
    idx = _find(device, lambda e: not e.get("editable")
                and (_txt(e).lower() in _OK_WORDS
                     or _desc(e).lower() in _OK_WORDS))
    if idx is not None:
        device.click(idx)
        return True
    return False


def _day_cell(device, d, m, y):
    d_s = str(d)
    day_re = re.compile(r"(?<!\d)%s(?!\d)" % re.escape(d_s))
    text_matches = []
    desc_matches = []
    for e in device.elements():
        if e.get("editable"):
            continue
        t = _txt(e)
        dl = _desc(e).lower()
        if t == d_s:
            text_matches.append(e)
        elif dl and day_re.search(dl) and _month_from_text(dl) is not None:
            desc_matches.append(e)
    for e in text_matches:
        dl = _desc(e).lower()
        mo = _month_from_text(dl)
        ym = re.search(r"\b(\d{4})\b", dl)
        yr = int(ym.group(1)) if ym else None
        if (mo is None or mo == m) and (yr is None or yr == y):
            return e["index"]
    if text_matches:
        clickable = [e for e in text_matches if e.get("clickable")]
        return (clickable or text_matches)[0]["index"]
    for e in desc_matches:
        dl = _desc(e).lower()
        mo = _month_from_text(dl)
        ym = re.search(r"\b(\d{4})\b", dl)
        yr = int(ym.group(1)) if ym else None
        if (mo is None or mo == m) and (yr is None or yr == y):
            return e["index"]
    return None


def _navigate_month(device, forward):
    if forward:
        pred = lambda e: "next month" in _desc(e).lower()
    else:
        pred = lambda e: ("previous month" in _desc(e).lower()
                          or "prev month" in _desc(e).lower())
    idx = _find(device, pred)
    if idx is not None:
        device.click(idx)
        return True
    device.scroll("left" if forward else "right")
    return True


def _pick_date_in_dialog(device, y, m, d, open_ym):
    if open_ym is not None:
        diff = (y - open_ym[0]) * 12 + (m - open_ym[1])
        for _ in range(min(abs(diff), 60)):
            _navigate_month(device, diff > 0)
    for _ in range(12):
        cell = _day_cell(device, d, m, y)
        if cell is not None:
            device.click(cell)
            _click_ok(device)
            return
        device.wait()
    raise RuntimeError("Could not select day %d in the date picker" % d)


def _set_date_row(device, row_pos, y, m, d, what):
    target = (y, m, d)
    last_err = None
    for attempt in range(3):
        dates, _times = _get_rows(device)
        if len(dates) <= row_pos:
            if attempt >= 1 and _dismiss_stuck_ok(device):
                continue
            raise RuntimeError("%s date row not found" % what)
        row = dates[row_pos]
        cur = _parse_date_text(_txt(row))
        if cur == target:
            return
        device.click(row["index"])
        try:
            _pick_date_in_dialog(device, y, m, d,
                                 (cur[0], cur[1]) if cur else None)
        except Exception as exc:
            last_err = exc
            continue
        dates2, _times2 = _get_rows(device)
        if len(dates2) > row_pos:
            new = _parse_date_text(_txt(dates2[row_pos]))
            if new == target or new is None:
                return
    raise RuntimeError("Failed to set the %s date to %04d-%02d-%02d (%s)"
                       % (what, y, m, d, last_err))


def _tap_clock_number(device, value, expect):
    variants = {str(value), "%02d" % value}
    tried = set()
    for _ in range(8):
        matches = [e for e in device.elements()
                   if not e.get("editable") and _txt(e) in variants
                   and e["index"] not in tried]
        if not matches:
            break
        clickable = [e for e in matches if e.get("clickable")]
        pool = clickable or matches
        target = pool[-1]
        tried.add(target["index"])
        device.click(target["index"])
        if expect is None:
            return
        texts = set(_txt(e) for e in device.elements())
        if expect == "minute" and any(t in _MINUTE_DIAL_MARKERS
                                      for t in texts):
            return
    raise RuntimeError("Could not select clock value %r" % value)


def _pick_time_in_dialog(device, hour, minute):
    _tap_clock_number(device, hour, "minute")
    _tap_clock_number(device, minute, None)
    _click_ok(device)


def _set_time_row(device, row_pos, hour, minute, what):
    target = hour * 60 + minute
    last_err = None
    for attempt in range(3):
        _dates, times = _get_rows(device)
        if len(times) <= row_pos:
            if attempt >= 1 and _dismiss_stuck_ok(device):
                continue
            raise RuntimeError("%s time row not found" % what)
        row = times[row_pos]
        if _parse_time_text(_txt(row)) == target:
            return
        device.click(row["index"])
        try:
            _pick_time_in_dialog(device, hour, minute)
        except Exception as exc:
            last_err = exc
            continue
        _dates2, times2 = _get_rows(device)
        if len(times2) > row_pos:
            new = _parse_time_text(_txt(times2[row_pos]))
            if new == target or new is None:
                return
    raise RuntimeError("Failed to set the %s time to %02d:%02d (%s)"
                       % (what, hour, minute, last_err))


def _save_event(device):
    idx = _find(device, lambda e: not e.get("editable")
                and (_desc(e).lower() == "save" or _txt(e).lower() == "save"
                     or _hint(e).lower() == "save"))
    if idx is None:
        idx = _find(device, lambda e: e.get("clickable")
                    and not e.get("editable")
                    and ("save" in _desc(e).lower()
                         or "save" in _txt(e).lower()))
    if idx is None:
        raise RuntimeError("Save control not found in the event editor")
    device.click(idx)
    for _ in range(4):
        ok_idx = _find(device, lambda e: not e.get("editable")
                       and (_txt(e).lower() in _OK_WORDS
                            or _desc(e).lower() in _OK_WORDS))
        if ok_idx is not None:
            device.click(ok_idx)
            continue
        if _find(device, lambda e: e.get("editable")) is None:
            return
        idx2 = _find(device, lambda e: not e.get("editable")
                     and (_desc(e).lower() == "save"
                          or _txt(e).lower() == "save"))
        if idx2 is not None:
            device.click(idx2)
        else:
            device.navigate_back()
    raise RuntimeError("Event editor did not close after saving")


def program(device, binding):
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    if not 1 <= month <= 12:
        raise ValueError("month must be 1-12, got %r" % (month,))
    start = datetime.datetime(year, month, day, hour, 0)
    end = start + datetime.timedelta(minutes=duration)

    device.open_app(_APP_NAME)
    device.settle(2)

    _open_new_event_screen(device)

    _input_field(device, title, "title", 0)
    _input_field(device, description, "description", 1)

    _set_date_row(device, 0, start.year, start.month, start.day, "start")
    _set_time_row(device, 0, start.hour, 0, "start")
    _set_date_row(device, 1, end.year, end.month, end.day, "end")
    _set_time_row(device, 1, end.hour, end.minute, "end")

    _save_event(device)
    return True
