import calendar
import re
import time

APP_NAME = "Simple Calendar Pro"

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "Year of the event date."},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Month of the event date (1-12)."},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Day of month of the event date."},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Start hour on a 24h clock; the event starts at HH:00."},
    "duration_mins": {"type": "int", "required": True, "min": 1,
                      "description": "Event length in minutes; the end time is start + duration."},
    "event_title": {"type": "str", "required": True,
                    "description": "Title text of the event."},
    "event_description": {"type": "str", "required": True,
                          "description": "Description text of the event."},
}

MONTHS_FULL = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]
MONTHS_ABBR = ["jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]

_TEXT_KEYS = ("text", "hint", "description")
_ID_KEYS = ("view_id_resource_name", "resource_id", "view_id", "resource_name",
            "class_name", "class")
_TIME_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*(am|pm)?\s*$", re.I)
_YEAR_RE = re.compile(r"\b\d{4}\b")
_NOISE_TOKENS = {"increment", "decrement", "increase", "decrease", "ok", "cancel", "ok!"}


def _txt(e, key="text"):
    v = e.get(key)
    return "" if v is None else str(v)


def _blob(e):
    parts = [_txt(e, k) for k in _TEXT_KEYS]
    for k in _ID_KEYS:
        v = e.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts).lower()


def _find_any(device, criteria):
    for crit in criteria:
        try:
            idx = device.find(**crit)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    return None


def _scan_for(device, predicate):
    for e in device.elements():
        try:
            if predicate(e):
                return e["index"]
        except Exception:
            continue
    return None


def _wait_for(device, predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        device.settle(1)
    return False


def _text_of(device, index):
    for e in device.elements():
        if e.get("index") == index:
            return _txt(e)
    return ""


def _month_from_text(t):
    s = (t or "").strip().lower()
    if not s:
        return None
    for i in range(12):
        if s.startswith(MONTHS_ABBR[i]) or s.startswith(MONTHS_FULL[i]):
            return i + 1
    return None


def _find_ok(device):
    idx = _find_any(device, [{"text": "OK"}, {"description": "OK"}, {"hint": "OK"}])
    if idx is not None:
        return idx
    return _scan_for(device, lambda e: _txt(e).strip().lower()
                     in ("ok", "okay", "ok!", "done", "set", "accept"))


def _wait_ok(device, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        idx = _find_ok(device)
        if idx is not None:
            return idx
        device.settle(1)
    return None


def _confirm_ok(device):
    idx = _wait_ok(device, 6)
    if idx is None:
        raise RuntimeError("The picker dialog's OK button was not found")
    device.click(index=idx)
    device.settle(1)
    for _ in range(2):
        idx = _find_ok(device)
        if idx is None:
            return
        try:
            device.click(index=idx)
        except Exception:
            pass
        device.settle(1)
    idx = _find_ok(device)
    if idx is None:
        return
    try:
        device.navigate_back()  # hide a possibly covering soft keyboard
        device.settle(1)
    except Exception:
        pass
    idx = _find_ok(device)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)


def _close_dialog(device):
    idx = _find_ok(device)
    if idx is not None:
        try:
            device.click(index=idx)
            device.settle(1)
            return
        except Exception:
            pass
    try:
        device.navigate_back()
        device.settle(1)
    except Exception:
        pass


def _wheels(device):
    """Spinner-style NumberPicker wheels: (increment button, value, decrement button)."""
    els = device.elements()
    wheels = []
    n = len(els)
    for i in range(n):
        b = _blob(els[i])
        if "increment" not in b and "increase" not in b:
            continue
        w = {"inc": els[i]["index"], "dec": None, "value_idx": None, "value_text": ""}
        for j in range(i + 1, min(n, i + 7)):
            bj = _blob(els[j])
            t = _txt(els[j]).strip()
            if "decrement" in bj or "decrease" in bj:
                if w["dec"] is None:
                    w["dec"] = els[j]["index"]
            elif t and w["value_idx"] is None and t.lower() not in _NOISE_TOKENS:
                w["value_idx"] = els[j]["index"]
                w["value_text"] = t
        wheels.append(w)
    return wheels


def _input_wheels(device):
    out = []
    for e in device.elements():
        if e.get("editable"):
            out.append({"inc": None, "dec": None, "value_idx": e["index"],
                        "value_text": _txt(e).strip()})
    return out


def _read_wheel(device, w):
    if w.get("value_idx") is None:
        return w.get("value_text") or ""
    for e in device.elements():
        if e.get("index") == w["value_idx"]:
            return _txt(e).strip()
    return w.get("value_text") or ""


def _wheel_value(device, w):
    t = _read_wheel(device, w)
    if not t:
        return None
    m = re.match(r"^(\d{1,4})", t)
    if m:
        return int(m.group(1))
    return _month_from_text(t)


def _type_into(device, w, payload):
    idx = w.get("value_idx")
    if idx is None:
        return False
    try:
        device.click(index=idx)
        device.input_text(str(payload), index=idx)
        device.keyboard_enter()
        device.settle(1)
        return True
    except Exception:
        return False


def _spin(device, w, target, modulus, label):
    for _ in range(2):
        cur = _wheel_value(device, w)
        if cur == target:
            return
        if cur is None:
            break
        click_idx = None
        count = 0
        if w.get("inc") is not None and w.get("dec") is not None and modulus:
            up = (target - cur) % modulus
            down = (cur - target) % modulus
            if up <= down:
                click_idx, count = w["inc"], up
            else:
                click_idx, count = w["dec"], down
        elif w.get("inc") is not None and target > cur:
            click_idx, count = w["inc"], target - cur
        elif w.get("dec") is not None and target < cur:
            click_idx, count = w["dec"], cur - target
        if click_idx is not None and count > 0:
            for _k in range(count):
                device.click(index=click_idx)
            if _wheel_value(device, w) == target:
                return
        elif click_idx is not None:
            return
    payload = target
    t = _read_wheel(device, w)
    if t and not t[0].isdigit() and 1 <= target <= 12:
        payload = MONTHS_ABBR[target - 1].capitalize()
    if _type_into(device, w, payload) and _wheel_value(device, w) == target:
        return
    raise RuntimeError("Could not set the %s wheel to %s (it shows %r)"
                       % (label, target, _read_wheel(device, w)))


def _classify_date_wheels(device, wheels):
    if len(wheels) < 3:
        return None
    year_w = month_w = None
    numerics = []
    for w in wheels:
        t = _read_wheel(device, w)
        if re.fullmatch(r"\d{4}", t):
            year_w = w
        elif t and not t[0].isdigit() and _month_from_text(t) is not None:
            month_w = w
        else:
            numerics.append(w)
    if year_w is None:
        return None
    if month_w is not None:
        if len(numerics) != 1:
            return None
        return year_w, month_w, numerics[0]
    if len(numerics) != 2:
        return None
    a, b = numerics
    va, vb = _wheel_value(device, a), _wheel_value(device, b)
    if va is not None and vb is not None and va != vb:
        if va == 10 or (va <= 12 < vb):
            return year_w, a, b
        if vb == 10 or (vb <= 12 < va):
            return year_w, b, a
    return year_w, a, b  # ambiguous: assume visual order month, day, year


def _apply_date_via_wheels(device, year, month, day):
    classified = _classify_date_wheels(device, _wheels(device))
    if classified is None:
        return False
    year_w, month_w, day_w = classified
    _spin(device, year_w, year, None, "year")
    _spin(device, month_w, month, 12, "month")
    _spin(device, day_w, day, calendar.monthrange(year, month)[1], "day")
    return True


def _apply_date_via_typing(device, year, month, day):
    classified = _classify_date_wheels(device, _input_wheels(device))
    if classified is None:
        return False
    year_w, month_w, day_w = classified
    if not _type_into(device, year_w, year):
        return False
    mt = _read_wheel(device, month_w)
    payload = (MONTHS_ABBR[month - 1].capitalize()
               if mt and not mt[0].isdigit() else month)
    if not _type_into(device, month_w, payload):
        return False
    if not _type_into(device, day_w, day):
        return False
    return (_wheel_value(device, year_w) == year
            and _wheel_value(device, month_w) == month
            and _wheel_value(device, day_w) == day)


def _apply_time_via_wheels(device, hh, mm):
    nums = []
    for w in _wheels(device):
        if re.fullmatch(r"\d{1,2}", _read_wheel(device, w)):
            nums.append(w)
    if len(nums) < 2:
        return False
    hour_w, minute_w = nums[0], nums[1]
    v0, v1 = _wheel_value(device, hour_w), _wheel_value(device, minute_w)
    if v0 is not None and v1 is not None and v0 > 23 >= v1:
        hour_w, minute_w = minute_w, hour_w
    _spin(device, hour_w, hh, 24, "hour")
    _spin(device, minute_w, mm, 60, "minute")
    return True


def _apply_time_via_typing(device, hh, mm):
    nums = []
    for w in _input_wheels(device):
        t = _read_wheel(device, w)
        if t == "" or re.fullmatch(r"\d{1,2}", t):
            nums.append(w)
    if len(nums) < 2:
        return False
    hour_w, minute_w = nums[0], nums[1]
    if not _type_into(device, hour_w, hh):
        return False
    if not _type_into(device, minute_w, mm):
        return False
    return (_wheel_value(device, hour_w) == hh
            and _wheel_value(device, minute_w) == mm)


def _apply_time_via_clock(device, hh, mm):
    hour_tokens = {str(hh), "%02d" % hh}
    minute_tokens = {"%02d" % mm, str(mm)}
    picked_hour = False
    for _ in range(4):
        hits = [e for e in device.elements() if _txt(e).strip() in hour_tokens]
        if hits:
            device.click(index=hits[-1]["index"])  # prefer a clock-face number
            picked_hour = True
            break
        device.scroll(direction="up")
    if not picked_hour:
        return False
    for _ in range(4):
        hits = [e for e in device.elements() if _txt(e).strip() in minute_tokens]
        if hits:
            device.click(index=hits[-1]["index"])
            return True
        device.scroll(direction="up")
    return False


def _calendar_header(device):
    for e in device.elements():
        t = _txt(e).strip()
        if not t or len(t) > 32:
            continue
        m = (re.search(r"([A-Za-z]{3,9})\D{0,3}(\d{4})", t)
             or re.search(r"(\d{4})\D{1,3}([A-Za-z]{3,9})", t))
        if not m:
            continue
        month = _month_from_text(m.group(1)) or _month_from_text(m.group(2))
        ym = re.search(r"\d{4}", m.group(1)) or re.search(r"\d{4}", m.group(2))
        if month and ym:
            return int(ym.group(0)), month
    return None


def _calendar_year_jump(device, ty):
    yidx = _scan_for(device, lambda e: re.fullmatch(r"(19|20)\d{2}", _txt(e).strip())
                     and e.get("clickable", False))
    if yidx is None:
        return False
    device.click(index=yidx)
    device.settle(1)
    tidx = _scan_for(device, lambda e: _txt(e).strip() == str(ty))
    if tidx is None:
        return False
    device.click(index=tidx)
    return True


def _calendar_step(device, cur, target):
    cy, cm = cur
    ty, tm = target
    direction = "next" if (ty * 12 + tm) > (cy * 12 + cm) else "previous"
    idx = _scan_for(device, lambda e: direction in _blob(e))
    if idx is None:
        return _calendar_year_jump(device, ty)
    device.click(index=idx)
    return True


def _apply_date_via_calendar(device, year, month, day):
    target = (year, month)
    for _ in range(64):
        cur = _calendar_header(device)
        if cur == target:
            day_idx = _scan_for(device, lambda e: _txt(e).strip() == str(day))
            if day_idx is None:
                return False
            device.click(index=day_idx)
            return True
        if cur is None or not _calendar_step(device, cur, target):
            return False
    return False


def _locate_rows(device):
    date_idx = None
    date_fallback = None
    times = []
    for e in device.elements():
        if e.get("editable"):
            continue
        t = _txt(e).strip()
        if not t or len(t) > 40:
            continue
        if _TIME_RE.match(t):
            times.append(e["index"])
            continue
        if _YEAR_RE.search(t):
            if date_idx is None and re.search(r"[A-Za-z]", t):
                date_idx = e["index"]
            elif date_fallback is None:
                date_fallback = e["index"]
    if date_idx is None:
        date_idx = date_fallback
    start_idx = times[0] if len(times) >= 1 else None
    end_idx = times[1] if len(times) >= 2 else None
    return date_idx, start_idx, end_idx


def _new_event_fab(device):
    idx = _find_any(device, [{"description": "New Event"},
                             {"text": "New Event"},
                             {"hint": "New Event"}])
    if idx is not None:
        return idx
    return _scan_for(device, lambda e: "new event" in _blob(e))


def _on_event_screen(device):
    if _find_any(device, [{"hint": "Title"}, {"text": "Title"},
                          {"hint": "Description"}, {"text": "Description"}]) is not None:
        return True
    editables = [e for e in device.elements() if e.get("editable")]
    return len(editables) >= 2


def _dismiss_popup(device):
    idx = _scan_for(device, lambda e: e.get("clickable", False)
                    and _txt(e).strip().upper() in ("OK", "OKAY", "GOT IT", "GOT IT!",
                                                    "ACCEPT", "ALLOW", "DONE", "CONTINUE"))
    if idx is None:
        return False
    device.click(index=idx)
    device.settle(1)
    return True


def _open_new_event(device):
    if _on_event_screen(device):
        return
    for _attempt in range(5):
        if _on_event_screen(device):
            return
        idx = _new_event_fab(device)
        if idx is not None:
            device.click(index=idx)
            if _wait_for(device, _on_event_screen, timeout=6):
                return
        elif not _dismiss_popup(device):
            device.settle(2)
    raise RuntimeError("Could not open the New Event screen of " + APP_NAME)


def _fill_field(device, value, hint, order):
    idx = _find_any(device, [{"hint": hint, "editable": True},
                             {"text": hint, "editable": True},
                             {"hint": hint}, {"text": hint}])
    if idx is None:
        editables = sorted((e for e in device.elements() if e.get("editable")),
                           key=lambda e: e["index"])
        if len(editables) > order:
            idx = editables[order]["index"]
    if idx is None:
        raise RuntimeError("Could not find the %s field" % hint)
    device.input_text(str(value), index=idx)
    device.settle(1)


def _open_row_dialog(device, finder, label):
    for _attempt in range(3):
        idx = finder()
        if idx is None:
            raise RuntimeError("The %s row was not found on the new-event screen" % label)
        try:
            device.click(index=idx)
        except Exception:
            pass
        if _wait_ok(device, 5) is not None:
            return
        try:
            device.navigate_back()  # most likely hides the soft keyboard
            device.settle(1)
        except Exception:
            pass
    raise RuntimeError("The %s picker dialog did not open" % label)


def _set_date(device, finder, year, month, day):
    month_tokens = (MONTHS_FULL[month - 1], MONTHS_ABBR[month - 1])
    for _attempt in range(2):
        _open_row_dialog(device, finder, "start date")
        applied = False
        for strategy in (_apply_date_via_wheels, _apply_date_via_typing,
                         _apply_date_via_calendar):
            try:
                if strategy(device, year, month, day):
                    applied = True
                    break
            except RuntimeError:
                continue
        if not applied:
            _close_dialog(device)
            raise RuntimeError("Could not set the start date %04d-%02d-%02d"
                               % (year, month, day))
        _confirm_ok(device)
        idx = finder()
        row = (_text_of(device, idx) if idx is not None else "").strip().lower()
        if (any(tok in row for tok in month_tokens) and str(year) in row
                and re.search(r"\b0?%d\b" % day, row)):
            return
    raise RuntimeError("The start date row does not show %04d-%02d-%02d"
                       % (year, month, day))


def _set_time(device, finder, hh, mm, label):
    want = re.compile(r"^0?%d:%02d" % (hh, mm))
    for _attempt in range(2):
        _open_row_dialog(device, finder, label)
        applied = False
        for strategy in (_apply_time_via_wheels, _apply_time_via_typing,
                         _apply_time_via_clock):
            try:
                if strategy(device, hh, mm):
                    applied = True
                    break
            except RuntimeError:
                continue
        if not applied:
            _close_dialog(device)
            raise RuntimeError("Could not set the %s to %02d:%02d" % (label, hh, mm))
        _confirm_ok(device)
        idx = finder()
        row = (_text_of(device, idx) if idx is not None else "").strip()
        if row and want.match(row):
            return
    raise RuntimeError("The %s row does not show %02d:%02d" % (label, hh, mm))


def _save_event(device):
    idx = _find_any(device, [{"description": "Save"}, {"text": "Save"}, {"hint": "Save"}])
    if idx is None:
        idx = _scan_for(device, lambda e: "save" in _blob(e) and e.get("clickable", True))
    if idx is None:
        raise RuntimeError("The Save button was not found on the new-event screen")
    device.click(index=idx)
    device.settle(2)

    def _done():
        return _new_event_fab(device) is not None and not _on_event_screen(device)

    if not _wait_for(device, _done, timeout=6):
        extra = _find_ok(device)
        if extra is not None:
            device.click(index=extra)
            device.settle(2)
    if not _done():
        raise RuntimeError("The event was not saved (the event editor is still open)")


def program(device, binding):
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    end_hour = (hour + duration_mins // 60) % 24
    end_minute = duration_mins % 60

    device.open_app(APP_NAME)
    device.settle(2)
    _open_new_event(device)

    _fill_field(device, title, "Title", 0)
    _fill_field(device, description, "Description", 1)

    def _date_row():
        d, _s, _e = _locate_rows(device)
        return d

    def _start_row():
        _d, s, _e = _locate_rows(device)
        return s

    def _end_row():
        _d, _s, e = _locate_rows(device)
        return e

    _set_date(device, _date_row, year, month, day)
    _set_time(device, _start_row, hour, 0, "start time")
    _set_time(device, _end_row, end_hour, end_minute, "end time")
    _save_event(device)
    return True
