import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "Year of the event start date"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Month of the event start date (1-12)"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Day of month of the event start date"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Event start hour on a 24-hour clock"},
    "duration_mins": {"type": "int", "required": True, "min": 1,
                      "description": "Event duration in minutes"},
    "event_title": {"type": "str", "required": True,
                    "description": "Title of the calendar event"},
    "event_description": {"type": "str", "required": True,
                          "description": "Description of the calendar event"},
}

MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
               "july", "august", "september", "october", "november", "december"]
MONTH_ABBRS = [name[:3] for name in MONTH_NAMES]
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
MINUTE_LABELS = {"%02d" % m for m in range(0, 60, 5)}
OK_NAMES = ("ok", "done", "set")


def _text(e):
    return (e.get("text") or "").strip()


def _desc(e):
    return (e.get("description") or "").strip()


def _hint(e):
    return (e.get("hint") or "").strip()


def _find_elem(device, pred):
    for e in device.elements():
        if pred(e):
            return e
    return None


def _find_by_text(device, value):
    v = value.strip().lower()
    return _find_elem(device, lambda e: _text(e).lower() == v)


def _find_by_desc(device, value):
    v = value.strip().lower()
    return _find_elem(device, lambda e: _desc(e).lower() == v)


def _find_by_desc_part(device, part):
    p = part.lower()
    return _find_elem(device, lambda e: p in _desc(e).lower())


def _ok_button(device):
    for e in device.elements():
        if _text(e).lower() in OK_NAMES:
            return e
    return None


def _main_ready(device):
    if device.find(description="New Event") is not None:
        return True
    return _find_by_desc_part(device, "new event") is not None


def _wait_main(device, attempts=4):
    for _ in range(attempts):
        if _main_ready(device):
            return True
        device.settle(1)
    return False


def _looks_like_date(t):
    tl = t.lower()
    for name in MONTH_NAMES:
        if name in tl:
            return True
    for ab in MONTH_ABBRS:
        if re.search(r"\b%s\b" % ab, tl):
            return True
    if re.search(r"\d{1,2}\s*[/.-]\s*\d{1,2}(\s*[/.-]\s*\d{2,4})?", tl):
        return True
    if re.search(r"\d{4}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{1,2}", tl):
        return True
    return False


def _locate_rows(device):
    dates, times = [], []
    for e in device.elements():
        t = _text(e)
        if not t or e.get("editable"):
            continue
        if TIME_RE.match(t):
            times.append(e)
        elif _looks_like_date(t):
            dates.append(e)
    order = lambda x: (0 if x.get("clickable") else 1, x.get("index", 0))
    dates.sort(key=order)
    times.sort(key=order)
    return dates, times


def _row_getter(device, kind, n):
    def get():
        dates, times = _locate_rows(device)
        lst = dates if kind == "date" else times
        return lst[n] if len(lst) > n else None
    return get


def _locate_fields(device):
    editables = [e for e in device.elements() if e.get("editable")]
    title_idx = None
    desc_idx = None
    for e in editables:
        h = _hint(e).lower()
        t = _text(e).lower()
        if title_idx is None and ("title" in h or t == "title"):
            title_idx = e.get("index")
        elif desc_idx is None and ("description" in h or t == "description"):
            desc_idx = e.get("index")
    if title_idx is None and editables:
        title_idx = editables[0].get("index")
    if desc_idx is None:
        for e in editables:
            if e.get("index") != title_idx:
                desc_idx = e.get("index")
                break
    if title_idx is None or desc_idx is None:
        raise RuntimeError("Could not locate the Title/Description fields")
    return title_idx, desc_idx


def _field_with_text(device, value):
    v = value.strip().lower()
    for e in device.elements():
        if e.get("editable") and _text(e).lower() == v:
            return e
    return None


def _row_date_ok(e, y, m, d):
    t = _text(e)
    if not t:
        return True
    tl = t.lower()
    if not re.search(r"(?<!\d)%d(?!\d)" % d, tl):
        return False
    if MONTH_NAMES[m - 1] in tl or re.search(r"\b%s\b" % MONTH_ABBRS[m - 1], tl):
        return True
    if str(y) in tl:
        return True
    if re.search(r"(?<!\d)%d\s*[/.-]\s*%d(\s*[/.-]\s*%d)?(?!\d)" % (d, m, y), tl):
        return True
    if re.search(r"(?<!\d)%d\s*[/.-]\s*%d\s*[/.-]\s*%d(?!\d)" % (y, m, d), tl):
        return True
    return False


def _row_time_ok(e, hour, minute):
    mobj = re.search(r"(\d{1,2}):(\d{2})", _text(e))
    if not mobj:
        return True
    return (int(mobj.group(1)), int(mobj.group(2))) == (hour, minute)


def _picker_month_year(device):
    for e in device.elements():
        mobj = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", _text(e))
        if mobj:
            name = mobj.group(1).lower()
            if name in MONTH_NAMES:
                return int(mobj.group(2)), MONTH_NAMES.index(name) + 1
    return None


def _jump_to_year(device, cur_y, tgt_y):
    if cur_y == tgt_y:
        return True
    header = None
    for e in device.elements():
        if _text(e) == str(cur_y) and e.get("clickable"):
            header = e
            break
    if header is None:
        return False
    device.click(index=header["index"])
    years = [e for e in device.elements() if re.fullmatch(r"\d{4}", _text(e))]
    if len(years) < 3:
        return False
    for e in years:
        if _text(e) == str(tgt_y):
            device.click(index=e["index"])
            return True
    for e in years:
        if _text(e) == str(cur_y):
            device.click(index=e["index"])
            return True
    return False


def _navigate_to_month(device, y, m):
    for _ in range(48):
        cur = _picker_month_year(device)
        if cur is None:
            return
        cy, cm = cur
        if (cy, cm) == (y, m):
            return
        if cy != y and _jump_to_year(device, cy, y):
            continue
        step = (y * 12 + m) - (cy * 12 + cm)
        want = "next month" if step > 0 else "previous month"
        btn = None
        for e in device.elements():
            if want in _desc(e).lower():
                btn = e
                break
        if btn is None:
            return
        device.click(index=btn["index"])
    raise RuntimeError("Could not navigate the date picker to %04d-%02d" % (y, m))


def _tap_calendar_day(device, d):
    target = str(d)
    cands = [e for e in device.elements() if _text(e) == target]
    if not cands:
        raise RuntimeError("Day %d is not visible in the date picker" % d)
    cands.sort(key=lambda e: (0 if e.get("clickable") else 1, e.get("index", 0)))
    device.click(index=cands[0]["index"])


def _set_date(device, get_row, y, m, d, label):
    for _ in range(2):
        row = get_row()
        if row is None:
            device.scroll(direction="down")
            row = get_row()
            if row is None:
                break
        device.click(index=row["index"])
        if _ok_button(device) is None:
            device.navigate_back()
            device.settle(1)
            continue
        _navigate_to_month(device, y, m)
        _tap_day_with_retry(device, d)
        btn = _ok_button(device)
        if btn is not None:
            device.click(index=btn["index"])
        check = get_row()
        if check is None or _row_date_ok(check, y, m, d):
            return True
    raise RuntimeError("Failed to set the %s via the date picker" % label)


def _clock_mode(device):
    texts = [_text(e) for e in device.elements()]
    minute_count = sum(1 for t in texts if t in MINUTE_LABELS)
    hour_count = 0
    for t in texts:
        if re.fullmatch(r"\d{1,2}", t) and 0 <= int(t) <= 23:
            hour_count += 1
    if minute_count >= 8:
        return "minute"
    if hour_count >= 12:
        return "hour"
    return None


def _time_dialog_open(device):
    if _clock_mode(device) is not None:
        return True
    return _ok_button(device) is not None


def _pick_clock_hour(device, hour):
    label = "00" if hour == 0 else str(hour)
    for _ in range(2):
        cands = [e for e in device.elements() if _text(e) == label]
        cands.sort(key=lambda e: (0 if not e.get("clickable") else 1, -e.get("index", 0)))
        for e in cands:
            device.click(index=e["index"])
            if _clock_mode(device) == "minute":
                return True
    return False


def _pick_clock_minute(device, minute):
    label = "%02d" % minute
    cands = [e for e in device.elements() if _text(e) == label]
    if not cands:
        return False
    cands.sort(key=lambda e: (0 if not e.get("clickable") else 1, -e.get("index", 0)))
    device.click(index=cands[0]["index"])
    return True


def _type_time_in_dialog(device, hour, minute):
    toggle = None
    for e in device.elements():
        d = _desc(e).lower()
        if "keyboard" in d or "text input" in d or ("toggle" in d and "input" in d):
            toggle = e
            break
    if toggle is None:
        return False
    device.click(index=toggle["index"])
    editables = [e for e in device.elements() if e.get("editable")]
    if hour is not None and len(editables) >= 1:
        device.input_text(str(hour), index=editables[0]["index"])
    if minute is not None and len(editables) >= 2:
        device.input_text("%02d" % minute, index=editables[1]["index"])
    return True


def _set_time(device, get_row, hour, minute, row_text):
    for attempt in range(2):
        row = get_row()
        if row is None:
            raise RuntimeError("Time row not found")
        device.click(index=row["index"])
        if not _time_dialog_open(device):
            device.navigate_back()
            device.settle(1)
            continue
        hour_done = _pick_clock_hour(device, hour)
        minute_done = _pick_clock_minute(device, minute)
        if not minute_mode_ok and not _minute_mode(device):
            _type_time_in_dialog(device, hour, minute)
        btn = _ok_button(device)
        if btn is not None:
            device.click(index=btn["index"])
        check = _row_time_text(device, row)
        if check is None or _row_time_ok(check, hour, minute):
            return True
    raise RuntimeError("Failed to set the %s to %02d:%02d" % (label, hour, minute))


def _set_time(device, get_row, hour, minute, label):
    for attempt in range(2):
        row = get_row()
        if row is None:
            device.scroll(direction="down")
            row = _row_get(device, row_idx)
            if row is None:
                break
        device.click(index=row["index"])
        if not _time_dialog_open(device):
            device.navigate_back()
            device.settle(1)
            continue
        if attempt == 0:
            if _pick_clock_hour(device, hour) and _pick_clock_minute(device, minute):
                device.click(ok_idx())
                if _verify_time(row_idx, hour, minute):
                    return
            else:
                device.click(ok_btn())
        else:
            if not _set_time_by_typing(device, hour, minute):
                raise RuntimeError("Failed to set the time via the time picker")
            device.click(ok_btn())
            if _verify_time(row_idx, hour, minute):
                return
    raise RuntimeError("Failed to set the %s to %02d:%02d" % (row_name, hour, minute))


def _save(device):
    for _ in range(2):
        btn = _find_by_desc("save") or _find_by_text("save")
        if btn is not None:
            device.click(index=btn["index"])
            if _wait_home(3):
                return True
    for ele in self._tree():
        if (ele.get("clickable") and not ele.get("editable")
                and not ele.get("scrollable")
                and not self._text(ele) and not self._desc(ele) and not self._hint(ele)):
            device.click(index=ele["index"])
            if self._wait_main(2):
                return True
            save = self._find_by_text("save")
            if save is not None:
                device.click(index=save["index"])
                if self._wait_main(3):
                    return True
            cancel = self._find_by_text("cancel")
            if cancel is not None:
                device.click(index=cancel["index"])
                self._sleep(1)
    raise RuntimeError("Could not save the event")


def main(device, binding):
    app = binding.get("app_name", "Simple Calendar Pro")
    device.open_app(app)
    device.wait(2)
    if not self._on_main(device):
        device.back()
        device.sleep(1)
        if not self._on_main(device):
            raise RuntimeError("App did not open")
    device.click(name="New Event")
    device.wait(2)
    fields = [e for e in device.elements() if e.get("editable")]
    if len(fields) < 2:
        raise RuntimeError("Title/Description fields not found")
    device.input_text(binding["event_title"], index=fields[0]["index"])
    device.input_text(binding["event_description"], index=fields[1]["index"])
    # date
    self._open_date_picker(device)
    self._navigate_to_month(device, binding["year"], binding["month"])
    self._tap_day(device, binding["day"])
    self._click_ok(device)
    if not self._date_ok(date_row, ...):
        raise RuntimeError("Date not set")
    # start time
    self._set_time(start_time_row, binding["hour"], 0)
    # end time = start + duration
    total = binding["hour"] * 60 + binding["duration_mins"]
    self._set_time(end_time_row, (total // 60) % 24, total % 60)
    self._save(device)
    return True
