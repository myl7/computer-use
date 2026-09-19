"""Create a calendar event in Simple Calendar Pro (parameterized by binding)."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "description": "Event year"},
    "month": {"type": "int", "required": True, "description": "Event month, 1-12"},
    "day": {"type": "int", "required": True, "description": "Event day of month"},
    "hour": {"type": "int", "required": True,
             "description": "Event start hour, 0-23 (24-hour clock)"},
    "duration_mins": {"type": "int", "required": True,
                      "description": "Event duration in minutes"},
    "event_title": {"type": "str", "required": True, "description": "Event title"},
    "event_description": {"type": "str", "required": True,
                          "description": "Event description"},
}

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_NUM2_RE = re.compile(r"^\d{1,2}$")
_NUM4_RE = re.compile(r"^\d{4}$")
_WORD_RE = re.compile(r"^[A-Za-z]{3,9}\.?$")
_YEAR_IN_TEXT_RE = re.compile(r"\b\d{4}\b")
_NUMERIC_DATE_RE = re.compile(r"^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$")
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_DUMP_PATH = "/sdcard/awcal_dump.xml"


def _txt(e):
    return (e.get("text") or "").strip()


def _sig(e):
    return (_txt(e), (e.get("hint") or "").strip(),
            (e.get("description") or "").strip())


def _vals_equal(a, b):
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return int(a) == int(b)
    except ValueError:
        pass
    la, lb = a.lower(), b.lower()
    return la.startswith(lb) or lb.startswith(la)


def _text_matches(e, c):
    for s in (_txt(e), (e.get("description") or "").strip()):
        if not s:
            continue
        if s == c or re.match(r"^%s\b" % re.escape(c), s):
            return True
    return False


def _is_time_row(e):
    if e.get("editable") or not e.get("clickable"):
        return False
    return _TIME_RE.fullmatch(_txt(e)) is not None


def _is_date_row(e):
    if e.get("editable") or not e.get("clickable"):
        return False
    t = _txt(e)
    if _TIME_RE.fullmatch(t):
        return False
    if _YEAR_IN_TEXT_RE.search(t) or _NUMERIC_DATE_RE.fullmatch(t):
        return True
    tl = t.lower()
    return any(m.lower() in tl for m in SHORT_MONTHS)


def _get_rows(device):
    elems = device.elements()
    dates = [e for e in elems if _is_date_row(e)]
    times = [e for e in elems if _is_time_row(e)]
    if len(times) < 2:
        raise RuntimeError("Start/end time rows not found on the event screen")
    if len(dates) < 2:
        raise RuntimeError("Start/end date rows not found on the event screen")
    return dates, times


def _new_elems(device, before):
    return [e for e in device.elements() if _sig(e) not in before]


def _open_dialog(device, row_index, what):
    before = {_sig(e) for e in device.elements()}
    for _ in range(3):
        device.click(index=row_index)
        if any(_txt(e).upper() == "OK" for e in _new_elems(device, before)):
            return before
        if any(_txt(e).upper() == "OK" for e in device.elements()):
            return before
    raise RuntimeError("The %s picker dialog did not open" % what)


def _spinner_inputs(elems):
    return [e for e in elems if e.get("editable")]


def _assign_inputs(inputs, kind):
    res = {}
    if kind == "date":
        year_el = next((e for e in inputs if _NUM4_RE.fullmatch(_txt(e))), None)
        if year_el is not None:
            res["year"] = year_el
        words = [e for e in inputs if _WORD_RE.fullmatch(_txt(e))]
        if words:
            res.setdefault("month", words[0])
        nums = [e for e in inputs if _NUM2_RE.fullmatch(_txt(e))]
        if len(nums) >= 2:
            res.setdefault("month", nums[0])
            res.setdefault("day", nums[1])
        elif len(nums) == 1:
            res.setdefault("day", nums[0])
        if len(inputs) >= 3:
            res.setdefault("month", inputs[0])
            res.setdefault("day", inputs[1])
            res.setdefault("year", inputs[2])
    else:
        nums = [e for e in inputs if _NUM2_RE.fullmatch(_txt(e))]
        use = nums if len(nums) >= 2 else inputs
        if len(use) >= 2:
            res["hour"] = use[0]
            res["minute"] = use[1]
    return res


def _try_typing_strategy(device, get_new, kind, targets):
    for _ in range(6):
        inputs = _spinner_inputs(get_new())
        if not inputs:
            return False
        assign = _assign_inputs(inputs, kind)
        pending = []
        for key, cands, pos in targets:
            el = assign.get(key)
            if el is None:
                return False
            if not any(_vals_equal(_txt(el), c) for c in cands):
                pending.append((key, el, cands))
        if not pending:
            return True
        key, el, cands = pending[0]
        for c in cands:
            device.click(index=el["index"])
            assign2 = _assign_inputs(_spinner_inputs(get_new()), kind)
            el2 = assign2.get(key)
            if el2 is None:
                break
            if any(_vals_equal(_txt(el2), cc) for cc in cands):
                break
            device.input_text(c, index=el2["index"])
            device.keyboard_enter()
    inputs = _spinner_inputs(get_new())
    assign = _assign_inputs(inputs, kind)
    for key, cands, pos in targets:
        el = assign.get(key)
        if el is None or not any(_vals_equal(_txt(el), c) for c in cands):
            return False
    return True


def _grid_click_values(device, get_new, kind, targets):
    if kind == "date":
        elems = get_new()
        year_c = next((c for k, c, p in targets if k == "year"), None)
        month_pos = next((p for k, c, p in targets if k == "month"), None)
        if year_c is None or month_pos is None:
            return False
        mname = SHORT_MONTHS[month_pos].lower()
        if not any(mname in _txt(e).lower() and year_c in _txt(e) for e in elems):
            return False
    for key, cands, pos in targets:
        target_el = None
        for c in cands:
            target_el = next((e for e in get_new()
                              if e.get("clickable") and _text_matches(e, c)), None)
            if target_el is None:
                pool = [e for e in device.elements()
                        if e.get("clickable") and not e.get("editable")]
                target_el = next((e for e in pool if _text_matches(e, c)), None)
            if target_el is not None:
                break
        if target_el is None:
            return False
        device.click(index=target_el["index"])
    return True


def _parse_bounds(b):
    m = _BOUNDS_RE.match(b or "")
    if not m:
        return None
    x1, y1, x2, y2 = (int(g) for g in m.groups())
    return x1, y1, x2, y2


def _adb_dump_pickers(device):
    raw = ""
    for _ in range(2):
        out = None
        try:
            device.adb_shell("rm", "-f", _DUMP_PATH)
            device.adb_shell("uiautomator", "dump", _DUMP_PATH)
            out = device.adb_shell("cat", _DUMP_PATH)
        except Exception:
            out = None
        if out:
            raw = out.decode("utf-8", "ignore") if isinstance(out, bytes) else str(out)
            break
    m = re.search(r"<\?xml.*", raw, re.S)
    if m:
        raw = m.group(0)
    if not raw.strip():
        return []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    pickers = []
    for node in root.iter("node"):
        if (node.get("class") or "") == "android.widget.NumberPicker":
            text = ""
            for child in node.iter("node"):
                if (child.get("class") or "") == "android.widget.EditText":
                    text = (child.get("text") or "").strip()
                    break
            pickers.append({"text": text, "bounds": node.get("bounds") or ""})
    return pickers


def _adb_assign(pickers, kind):
    res = {}
    if kind == "date":
        for p in pickers:
            t = p["text"]
            if _NUM4_RE.fullmatch(t):
                res.setdefault("year", p)
            elif _WORD_RE.fullmatch(t):
                res.setdefault("month", p)
            elif _NUM2_RE.fullmatch(t):
                res.setdefault("day", p)
    else:
        nums = [p for p in pickers if _NUM2_RE.fullmatch(p["text"])]
        if len(nums) >= 2:
            res["hour"] = nums[0]
            res["minute"] = nums[1]
    return res


def _adb_pos(kind, key, text):
    t = (text or "").strip()
    if kind == "date" and key == "month":
        tl = t.lower()[:3]
        for i, m in enumerate(SHORT_MONTHS):
            if m.lower() == tl:
                return i
        try:
            v = int(t)
        except ValueError:
            return None
        return v - 1 if 1 <= v <= 12 else None
    try:
        return int(t)
    except ValueError:
        return None


def _adb_nudge(device, bounds, delta):
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    unit = max(40, (y2 - y1) // 4)
    dist = unit * min(max(abs(delta), 1), 4)
    if delta > 0:
        device.adb_shell("input", "swipe", str(cx), str(cy + dist),
                         str(cx), str(cy - dist), "250")
    elif delta < 0:
        device.adb_shell("input", "swipe", str(cx), str(cy - dist),
                         str(cx), str(cy + dist), "250")


def _adb_set_picker_value(device, kind, key, cands, target_pos):
    for _ in range(70):
        assign = _adb_assign(_adb_dump_pickers(device), kind)
        el = assign.get(key)
        if el is None:
            return False
        if any(_vals_equal(el["text"], c) for c in cands):
            return True
        cur = _adb_pos(kind, key, el["text"])
        bounds = _parse_bounds(el["bounds"])
        if cur is None or target_pos is None or bounds is None:
            return False
        _adb_nudge(device, bounds, max(-4, min(4, target_pos - cur)))
    return False


def _adb_check(device, kind, targets):
    assign = _adb_assign(_adb_dump_pickers(device), kind)
    for key, cands, pos in targets:
        el = assign.get(key)
        if el is None or not any(_vals_equal(el["text"], c) for c in cands):
            return False
    return True


def _set_spinner_values(device, get_new, kind, targets):
    if _try_typing_strategy(device, get_new, kind, targets):
        return
    if _grid_click_values(device, get_new, kind, targets):
        return
    tried = set()
    for _ in range(2):
        for key, cands, pos in targets:
            if key in tried:
                continue
            try:
                if _adb_set_picker_value(device, kind, key, cands, pos):
                    tried.add(key)
            except Exception:
                pass
        if len(tried) >= len(targets):
            break
    try:
        if _adb_check(device, kind, targets):
            return
    except Exception:
        pass
    raise RuntimeError("Could not set the picker values for the %s dialog" % kind)


def _confirm_dialog(device, get_new):
    for _ in range(3):
        ok_btn = next((e for e in get_new() if _txt(e).upper() == "OK"), None)
        if ok_btn is None:
            return
        device.click(index=ok_btn["index"])
    raise RuntimeError("The picker dialog did not close after tapping OK")


def _edit_date(device, before, y, m, d):
    get_new = lambda: _new_elems(device, before)
    targets = [
        ("year", [str(y)], y),
        ("month", [SHORT_MONTHS[m - 1], str(m)], m - 1),
        ("day", [str(d)], d),
    ]
    _set_spinner_values(device, get_new, "date", targets)
    _confirm_dialog(device, get_new)


def _edit_time(device, before, h, mi):
    get_new = lambda: _new_elems(device, before)
    targets = [
        ("hour", [str(h)], h),
        ("minute", [str(mi), "%02d" % mi], mi),
    ]
    _set_spinner_values(device, get_new, "time", targets)
    _confirm_dialog(device, get_new)


def _date_text_ok(t, y, m, d):
    t = (t or "").lower()
    if str(d) not in t or str(y) not in t:
        return False
    return (SHORT_MONTHS[m - 1].lower() in t or ("%02d" % m) in t
            or str(m) in t)


def _time_text_ok(t, h, mi):
    t = (t or "").strip()
    return t in ("%d:%02d" % (h, mi), "%02d:%02d" % (h, mi))


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    desc = str(binding["event_description"])
    app_name = binding.get("app_name", "Simple Calendar Pro")

    # 1) Launch the app and wait for the main screen ('New Event' FAB).
    device.open_app(app_name)
    fab = None
    for _ in range(4):
        fab = device.find(description="New Event")
        if fab is not None:
            break
        device.wait()
    if fab is None:
        fab = device.find(text="New Event")
    if fab is None:
        raise RuntimeError("Simple Calendar Pro did not open: no 'New Event' button")

    # 2) New event via the FAB (+ 'Event' entry of the popup menu when present).
    device.click(index=fab)
    title_idx = None
    for _ in range(6):
        title_idx = device.find(hint="Title", editable=True)
        if title_idx is not None:
            break
        ev = device.find(text="Event")
        if ev is not None:
            device.click(index=ev)
        else:
            device.wait()
    if title_idx is None:
        editable = [e for e in device.elements() if e.get("editable")]
        if not editable:
            raise RuntimeError("Event editor did not open (no Title field)")
        title_idx = editable[0]["index"]
    device.input_text(title, index=title_idx)

    desc_idx = device.find(hint="Description", editable=True)
    if desc_idx is None:
        editable = [e for e in device.elements() if e.get("editable")]
        cand = [e for e in editable
                if e.get("index") != title_idx and _txt(e) != title]
        if cand:
            desc_idx = cand[0]["index"]
        elif len(editable) > 1:
            desc_idx = editable[1]["index"]
        elif editable:
            desc_idx = editable[0]["index"]
        else:
            raise RuntimeError("Description field not found")
    device.input_text(desc, index=desc_idx)

    # 3) Start date (text fields come first, then the date/time rows).
    for _ in range(2):
        dates, times = _get_rows(device)
        before = _open_dialog(device, dates[0]["index"], "start date")
        _edit_date(device, before, year, month, day)
        dates, _ = _get_rows(device)
        if _date_text_ok(_txt(dates[0]), year, month, day):
            break

    # 4) Start time at HH:00.
    for _ in range(2):
        dates, times = _get_rows(device)
        before = _open_dialog(device, times[0]["index"], "start time")
        _edit_time(device, before, hour, 0)
        _, times = _get_rows(device)
        if _time_text_ok(_txt(times[0]), hour, 0):
            break

    # 5) No duration field exists: set the END date/time to start + duration.
    start_dt = datetime(year, month, day, hour, 0)
    end_dt = start_dt + timedelta(minutes=duration)
    for _ in range(2):
        dates, times = _get_rows(device)
        before = _open_dialog(device, dates[1]["index"], "end date")
        _edit_date(device, before, end_dt.year, end_dt.month, end_dt.day)
        dates, _ = _get_rows(device)
        if _date_text_ok(_txt(dates[1]), end_dt.year, end_dt.month, end_dt.day):
            break
    for _ in range(2):
        dates, times = _get_rows(device)
        before = _open_dialog(device, times[1]["index"], "end time")
        _edit_time(device, before, end_dt.hour, end_dt.minute)
        _, times = _get_rows(device)
        if _time_text_ok(_txt(times[1]), end_dt.hour, end_dt.minute):
            break

    # 6) Save the event.
    save_idx = None
    for _ in range(3):
        save_idx = device.find(description="Save")
        if save_idx is None:
            save_idx = device.find(text="Save")
        if save_idx is not None:
            break
        device.wait()
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)

    for _ in range(3):
        if device.find(hint="Title", editable=True) is None:
            return True
        save_idx = device.find(description="Save")
        if save_idx is None:
            save_idx = device.find(text="Save")
        if save_idx is None:
            break
        device.click(index=save_idx)
    raise RuntimeError("The event editor did not close after saving")
