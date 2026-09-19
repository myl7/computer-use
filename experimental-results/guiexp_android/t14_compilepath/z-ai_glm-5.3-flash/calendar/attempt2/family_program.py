import calendar
import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "min": 1970, "max": 2100, "required": True,
             "description": "Year of the event start date"},
    "month": {"type": "int", "min": 1, "max": 12, "required": True,
              "description": "Month of the event start date (1-12)"},
    "day": {"type": "int", "min": 1, "max": 31, "required": True,
            "description": "Day of month of the event start date"},
    "hour": {"type": "int", "min": 0, "max": 23, "required": True,
             "description": "Event start hour (24h clock)"},
    "duration_mins": {"type": "int", "min": 1, "max": 525600, "required": True,
                      "description": "Event duration in minutes"},
    "event_title": {"type": "str", "required": True,
                    "description": "Title of the event"},
    "event_description": {"type": "str", "required": True,
                          "description": "Description of the event"},
}

_DATE_RE = re.compile(
    r"\b(?:19|20)\d{2}\b"
    r"|\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
    r"|\b\d{1,2}\s+[A-Za-z]{3,9}\b"
    r"|\b[A-Za-z]{3,9}\.?,?\s+\d{1,2}\b"
)
_TIME_RE = re.compile(r"^\d{1,2}[:.]\d{2}(\s?(?:am|pm))?$", re.IGNORECASE)


def _text_of(element):
    try:
        return (element.get("text") or "").strip()
    except Exception:
        return ""


def _element_by_index(device, index):
    for e in device.elements():
        if e.get("index") == index:
            return e
    return None


def _find(device, **criteria):
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _adb_escape(text):
    return "".join("%s" if ch == " " else ch for ch in text)


def _looks_like_picker_text(t):
    if not t:
        return True
    return len(t) <= 16


def _month_from_text(t):
    t = (t or "").strip().lower()
    if not t:
        return None
    for i in range(1, 13):
        try:
            if t == calendar.month_name[i].lower() or t == calendar.month_abbr[i].lower():
                return i
        except Exception:
            continue
    return None


def _int_check(target):
    def check(t):
        t = (t or "").strip()
        return t.isdigit() and int(t) == int(target)
    return check


def _month_check(target):
    def check(t):
        t = (t or "").strip()
        if t.isdigit():
            return int(t) == int(target)
        return _month_from_text(t) == int(target)
    return check


def _is_event_screen(device):
    if _find(device, hint="Title") is not None:
        return True
    try:
        return len([e for e in device.elements() if e.get("editable")]) >= 2
    except Exception:
        return False


def _click_last_clickable(device):
    els = [e for e in device.elements() if e.get("clickable")]
    if not els:
        raise RuntimeError("no clickable element found on screen")
    device.click(max(e["index"] for e in els))


def _ensure_event_screen(device):
    for _ in range(8):
        if _is_event_screen(device):
            return
        idx = (_find(device, description="New event")
               or _find(device, text="New event")
               or _find(device, text="Event")
               or _find(device, text="event")
               or _find(device, description="Event"))
        if idx is not None:
            device.click(idx)
        else:
            _click_last_clickable(device)
    if not _is_event_screen(device):
        raise RuntimeError("could not reach the new-event screen")


def _field_index(device, hints, ordinal):
    for h in hints:
        i = _find(device, hint=h)
        if i is not None:
            return i
    eds = [e for e in device.elements() if e.get("editable")]
    if len(eds) > ordinal:
        return eds[ordinal]["index"]
    return None


def _fill_field(device, value, hints, ordinal):
    text = str(value)
    for mode in ("plain", "clicked", "adb"):
        idx = _field_index(device, hints, ordinal)
        if idx is None:
            raise RuntimeError("text field %r not found" % (hints,))
        try:
            if mode in ("clicked", "adb"):
                device.click(idx)
            if mode == "adb":
                device.adb_shell("input", "text", _adb_escape(text))
            else:
                device.input_text(text, idx)
            device.keyboard_enter()
        except Exception:
            continue
        idx = _field_index(device, hints, ordinal)
        if idx is not None and _text_of(_element_by_index(device, idx)) == text:
            return
    raise RuntimeError("could not fill text field %r" % (hints,))


def _event_rows(device):
    els = device.elements()
    best = ([], [])
    for require_clickable in (True, False):
        dates, times = [], []
        for e in els:
            if require_clickable:
                if not e.get("clickable"):
                    continue
            elif e.get("editable"):
                continue
            t = _text_of(e)
            if not t or len(t) > 40:
                continue
            if _DATE_RE.search(t):
                dates.append(e)
            elif _TIME_RE.match(t):
                times.append(e)
        if len(dates) >= 2 and len(times) >= 2:
            return dates, times
        if len(dates) + len(times) > len(best[0]) + len(best[1]):
            best = (dates, times)
    return best


def _wait_for_dialog(device):
    for _ in range(5):
        if _find(device, text="OK") is not None or _find(device, text="Ok") is not None:
            return True
        device.wait()
    return False


def _click_ok(device):
    for _ in range(3):
        i = _find(device, text="OK") or _find(device, text="Ok")
        if i is not None:
            device.click(i)
            return True
        device.wait()
    return False


def _cancel_dialog(device):
    i = _find(device, text="Cancel")
    if i is not None:
        device.click(i)
        return True
    return False


def _picker_indexes(device, count):
    for _ in range(4):
        eds = [e for e in device.elements()
               if e.get("editable")
               and not (e.get("hint") or "").strip()
               and _looks_like_picker_text(_text_of(e))]
        if len(eds) >= count:
            return [e["index"] for e in eds[:count]]
        device.wait()
    return None


def _ordinal_index(device, count, ordinal):
    idxs = _picker_indexes(device, count)
    if not idxs or len(idxs) <= ordinal:
        return None
    return idxs[ordinal]


def _ordinal_text(device, count, ordinal):
    idx = _ordinal_index(device, count, ordinal)
    if idx is None:
        return None
    return _text_of(_element_by_index(device, idx))


def _neutral_click(device, count, avoid_ordinal):
    for o in range(count):
        if o == avoid_ordinal:
            continue
        idx = _ordinal_index(device, count, o)
        if idx is not None:
            try:
                device.click(idx)
                return True
            except Exception:
                pass
    for e in device.elements()[:8]:
        if e.get("editable"):
            continue
        t = _text_of(e)
        if not t or len(t) > 30:
            continue
        blob = (t + " " + str(e.get("description") or "")).lower()
        if any(w in blob for w in ("ok", "cancel", "save", "delete", "discard")):
            continue
        try:
            device.click(e["index"])
            return True
        except Exception:
            continue
    return False


def _parse_picker_number(t, wrap):
    t = (t or "").strip()
    if t.isdigit():
        v = int(t)
        if wrap and v == 0:
            v = wrap
        return v
    return _month_from_text(t)


def _dpad_to(device, count, ordinal, target, wrap=None, max_presses=220):
    target = int(target)

    def read():
        idx = _ordinal_index(device, count, ordinal)
        if idx is None:
            return None
        return _parse_picker_number(_text_of(_element_by_index(device, idx)), wrap)

    cur = read()
    if cur is None:
        return False
    down_inc = None
    presses = 0
    while presses < max_presses:
        if cur == target:
            return True
        if down_inc is None:
            try:
                device.adb_shell("input", "keyevent", "20")
            except Exception:
                return False
            presses += 1
            device.settle(0.4)
            nv = read()
            if nv is None:
                return False
            if nv == cur:
                try:
                    device.adb_shell("input", "keyevent", "19")
                except Exception:
                    return False
                presses += 1
                device.settle(0.4)
                nv = read()
                if nv is None:
                    return False
                if nv == cur:
                    return False
                down_inc = False
            else:
                down_inc = (nv == cur + 1) or (wrap is not None and nv == (cur + 1) % wrap)
            cur = nv
            continue
        go_down = (target > cur) if down_inc else (target < cur)
        try:
            device.adb_shell("input", "keyevent", "20" if go_down else "19")
        except Exception:
            return False
        presses += 1
        device.settle(0.4)
        nv = read()
        if nv is None:
            return False
        cur = nv
    return cur == target


def _set_picker_field(device, count, ordinal, value, check, tries=2):
    value = int(value)
    for _ in range(tries):
        for mode in ("plain", "clicked", "adb"):
            idx = _ordinal_index(device, count, ordinal)
            if idx is None:
                return None
            try:
                if mode in ("clicked", "adb"):
                    device.click(idx)
                if mode == "adb":
                    device.adb_shell("input", "text", str(value))
                else:
                    device.input_text(str(value), idx)
                device.keyboard_enter()
            except Exception:
                pass
            _neutral_click(device, count, ordinal)
            device.settle(1)
            t = _ordinal_text(device, count, ordinal)
            if t is None:
                return None
            try:
                if check(t):
                    return True
            except Exception:
                pass
        idx = _ordinal_index(device, count, ordinal)
        if idx is None:
            return None
        try:
            device.click(idx)
        except Exception:
            pass
        if _dpad_to(device, count, ordinal, value):
            return True
    return False


def _probe_picker(device, count, ordinal, value):
    idx = _ordinal_index(device, count, ordinal)
    if idx is None:
        return None
    try:
        device.click(idx)
        device.input_text(str(value), idx)
        device.keyboard_enter()
    except Exception:
        pass
    _neutral_click(device, count, ordinal)
    device.settle(1)
    return _ordinal_text(device, count, ordinal)


def _classify_date_fields(device, count=3):
    entries = []
    for o in range(count):
        t = _ordinal_text(device, count, o)
        if t is None:
            return None
        entries.append((o, t))
    year_ord = None
    for o, t in entries:
        if re.fullmatch(r"\d{4}", t):
            year_ord = o
            break
    rest = [(o, t) for o, t in entries if o != year_ord]
    month_ord = day_ord = None
    alpha = [(o, t) for o, t in rest if re.search(r"[A-Za-z]", t)]
    nums = [(o, t) for o, t in rest if t.isdigit()]
    if len(alpha) == 1 and len(nums) >= 1:
        month_ord = alpha[0][0]
        day_ord = nums[0][0]
    elif len(nums) == 2:
        (o1, t1), (o2, t2) = nums
        v1, v2 = int(t1), int(t2)
        if v1 > 12 >= v2:
            day_ord, month_ord = o1, o2
        elif v2 > 12 >= v1:
            day_ord, month_ord = o2, o1
    if month_ord is None or day_ord is None and rest:
        probe_ord = rest[0][0]
        before = dict(rest)[probe_ord]
        after = _probe_picker(device, count, probe_ord, 31)
        if after is None:
            return None
        if after == before:
            day_ord, month_ord = rest[0][0], rest[-1][0]
        elif re.search(r"[A-Za-z]", after) or (after.isdigit() and int(after) <= 12):
            month_ord = probe_ord
            day_ord = [o for o, _ in rest if o != probe_ord][0]
        else:
            day_ord = probe_ord
            month_ord = [o for o, _ in rest if o != probe_ord][0]
    if year_ord is None:
        for o, t in entries:
            if o not in (month_ord, day_ord):
                year_ord = o
                break
    if year_ord is None or month_ord is None or day_ord is None:
        return None
    return day_ord, month_ord, year_ord


def _set_month_field(device, count, ordinal, m):
    for cand in (m, m - 1):
        if cand >= 1:
            r = _set_picker_field(device, count, ordinal, cand, _month_check(m))
            if r is True:
                return True
            if r is None:
                return None
    idx = _ordinal_index(device, count, ordinal)
    if idx is None:
        return None
    try:
        device.click(idx)
    except Exception:
        pass
    return _dpad_to(device, count, ordinal, m, wrap=12)


def _fill_date_dialog(device, y, m, d):
    count = 3
    cls = _classify_date_fields(device, count)
    if cls is None:
        return False
    day_ord, month_ord, year_ord = cls
    if _set_picker_field(device, count, year_ord, y, _int_check(y)) is not True:
        return False
    if _set_month_field(device, count, month_ord, m) is not True:
        return False
    if _set_picker_field(device, count, day_ord, d, _int_check(d)) is not True:
        return False
    return _click_ok(device)


def _classify_time_fields(device, count=2):
    entries = []
    for o in range(count):
        t = _ordinal_text(device, count, o)
        if t is None:
            return None
        entries.append((o, t))
    (o1, t1), (o2, t2) = entries
    v1 = int(t1) if t1.isdigit() else None
    v2 = int(t2) if t2.isdigit() else None
    if v1 is not None and v2 is not None:
        if v1 > 23 >= v2:
            return o2, o1
        if v2 > 23 >= v1:
            return o1, o2
    return o1, o2


def _fill_time_dialog(device, hour, minute):
    count = 2
    cls = _classify_time_fields(device, count)
    if cls is None:
        return False
    hour_ord, minute_ord = cls
    if _set_picker_field(device, count, hour_ord, hour, _int_check(hour)) is not True:
        return False
    if _set_picker_field(device, count, minute_ord, minute, _int_check(minute)) is not True:
        return False
    return _click_ok(device)


def _date_row_ok(t, y, m, d):
    t = (t or "").lower()
    tokens = re.findall(r"\d+", t)
    day_ok = str(d) in tokens or ("%02d" % d) in tokens
    month_name = (calendar.month_name[m] or "").lower()
    month_abbr = (calendar.month_abbr[m] or "").lower()
    month_ok = ((month_name and month_name in t)
                or (month_abbr and month_abbr in t)
                or str(m) in tokens or ("%02d" % m) in tokens)
    year_ok = str(y) in tokens or str(y % 100) in tokens or ("%02d" % (y % 100)) in tokens
    return day_ok and month_ok and year_ok


def _set_event_datetime(device, kind, ordinal, y=None, m=None, d=None, hh=None, mm=None):
    err = "unknown"
    for attempt in range(3):
        dates, times = _event_rows(device)
        pool = dates if kind == "date" else times
        if len(pool) <= ordinal:
            err = "%s row #%d not found" % (kind, ordinal + 1)
            device.wait()
            continue
        device.click(pool[ordinal]["index"])
        if not _wait_for_dialog(device):
            err = "picker dialog did not open"
            continue
        try:
            if kind == "date":
                ok = _fill_date_dialog(device, y, m, d)
            else:
                ok = _fill_time_dialog(device, hh, mm)
        except Exception as exc:
            ok = False
            err = "error: %s" % exc
        if ok:
            if kind == "date":
                new_dates, _ = _event_rows(device)
                if len(new_dates) > ordinal and not _date_row_ok(_text_of(new_dates[ordinal]), y, m, d):
                    err = "date row shows unexpected value"
                    _cancel_dialog(device)
                    continue
            return
        _cancel_dialog(device)
    raise RuntimeError("failed to set %s #%d (%s)" % (kind, ordinal + 1, err))


def _click_save(device):
    for kw in ({"description": "Save"}, {"text": "Save"}, {"hint": "Save"},
               {"description": "save"}, {"text": "save"}):
        i = _find(device, **kw)
        if i is not None:
            device.click(i)
            return True
    for e in device.elements():
        blob = " ".join([_text_of(e), str(e.get("description") or ""),
                         str(e.get("hint") or "")]).lower()
        if "save" in blob:
            device.click(e["index"])
            return True
    raise RuntimeError("save control not found")


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
    _ensure_event_screen(device)

    _fill_field(device, title, ("Title",), 0)
    _fill_field(device, description, ("Description",), 1)

    _set_event_datetime(device, "date", 0,
                        y=start_dt.year, m=start_dt.month, d=start_dt.day)
    _set_event_datetime(device, "time", 0, hh=start_dt.hour, mm=0)

    dates, _times = _event_rows(device)
    if len(dates) >= 2:
        _set_event_datetime(device, "date", 1,
                            y=end_dt.year, m=end_dt.month, d=end_dt.day)
    _set_event_datetime(device, "time", 1, hh=end_dt.hour, mm=end_dt.minute)

    _click_save(device)

    for _ in range(3):
        if not _is_event_screen(device):
            return True
        device.wait()
    _click_save(device)
    device.wait()
    if not _is_event_screen(device):
        return True
    raise RuntimeError("event screen did not close after saving")
