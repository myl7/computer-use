import calendar
import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1970, "max": 2100,
             "description": "event year"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "event month, 1-12"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "event day of month"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "event start hour on a 24h clock"},
    "duration_mins": {"type": "int", "required": True, "min": 1, "max": 20160,
                      "description": "event length in minutes"},
    "event_title": {"type": "str", "required": True, "description": "event title"},
    "event_description": {"type": "str", "required": True,
                          "description": "event description"},
}

_MONTH_FULL = {i: calendar.month_name[i] for i in range(1, 13)}
_MONTH_ABBR = {i: calendar.month_abbr[i] for i in range(1, 13)}
_YEAR_RE = re.compile(r"\b\d{4}\b")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+)\.?\s+(\d{4})$")


def _text(el):
    return (el.get("text") or "").strip()


def _desc(el):
    return el.get("description") or ""


def _find_by_pred(device, pred):
    for el in device.elements():
        if pred(el):
            return el["index"]
    return None


def _click_ok(device):
    for _ in range(3):
        idx = device.find(text="OK")
        if idx is None:
            idx = device.find(description="OK")
        if idx is not None:
            device.click(index=idx)
            return
        device.wait()
    raise RuntimeError("OK button not found in dialog")


def _shown_month_year(device):
    for el in device.elements():
        m = _MONTH_YEAR_RE.match(_text(el))
        if m:
            name = m.group(1).lower().rstrip(".")
            for i in range(1, 13):
                if name == _MONTH_FULL[i].lower() or name == _MONTH_ABBR[i].lower():
                    return i, int(m.group(2))
    return None


def _arrow(device, forward):
    want = "next" if forward else "previous"
    for el in device.elements():
        d = _desc(el).lower().strip()
        if want in d and ("month" in d or d == want):
            device.click(index=el["index"])
            return True
    return False


def _day_matches_text(text, day_s):
    return re.search(r"(^|[^\d])%s([^\d]|$)" % re.escape(day_s), text) is not None


def _select_day(device, day_s, names, year, month_confirmed):
    els = device.elements()
    for el in els:
        if _text(el) == day_s:
            d = _desc(el)
            if any(n in d for n in names) and str(year) in d:
                device.click(index=el["index"])
                return True
    for el in els:
        if _text(el) == day_s and any(n in _desc(el) for n in names):
            device.click(index=el["index"])
            return True

    def header_confirms():
        for el in device.elements():
            t = el.get("text") or ""
            if any(n in t for n in names) and _day_matches_text(t, day_s):
                return True
        return False

    cands = [el["index"] for el in els if _text(el) == day_s]
    if month_confirmed and len(cands) == 1:
        device.click(index=cands[0])
        return True
    for i in cands:
        try:
            device.click(index=i)
        except Exception:
            continue
        if header_confirms():
            return True
    return False


def _pick_date(device, year, month, day):
    names = (_MONTH_FULL[month], _MONTH_ABBR[month])
    day_s = str(day)

    label = _shown_month_year(device)
    first_forward = True
    if label:
        delta = (year - label[1]) * 12 + (month - label[0])
        first_forward = delta >= 0
        if delta:
            forward = delta > 0
            for _ in range(min(abs(delta), 64)):
                if not _arrow(device, forward):
                    break
            label = _shown_month_year(device)

    if _select_day(device, day_s, names, year, label == (month, year)):
        _click_ok(device)
        return

    for forward in (first_forward, not first_forward):
        moved = 0
        for _ in range(36):
            if not _arrow(device, forward):
                break
            moved += 1
            lab = _shown_month_year(device)
            if _select_day(device, day_s, names, year, lab == (month, year)):
                _click_ok(device)
                return
        for _ in range(moved):
            if not _arrow(device, not forward):
                break
        lab = _shown_month_year(device)
        if _select_day(device, day_s, names, year, lab == (month, year)):
            _click_ok(device)
            return
    raise RuntimeError("could not select %04d-%02d-%02d in the date picker"
                       % (year, month, day))


def _date_rows(device):
    rows = []
    for el in device.elements():
        if el.get("editable"):
            continue
        t = el.get("text") or ""
        if ":" in t:
            continue
        if _YEAR_RE.search(t) or ("/" in t and re.search(r"\d", t)):
            rows.append(el)
    return rows


def _time_rows(device):
    return [el for el in device.elements()
            if not el.get("editable") and _TIME_RE.match(_text(el))]


def _pick_hour(device, hour):
    want = "%d hours" % hour
    for _ in range(4):
        idx = device.find(description=want)
        if idx is None:
            idx = _find_by_pred(device, lambda el: _text(el) == str(hour)
                                and "hour" in _desc(el).lower())
        if idx is not None:
            try:
                device.click(index=idx)
                return True
            except Exception:
                pass
        for el in device.elements():
            if _desc(el).lower().strip() == "select hours":
                try:
                    device.click(index=el["index"])
                except Exception:
                    pass
                break
        device.wait()
    return False


def _pick_minute(device, minute):
    want_desc = "%d minutes" % minute
    want_text = "%02d" % minute
    for _ in range(3):
        idx = device.find(description=want_desc)
        if idx is None:
            idx = _find_by_pred(device, lambda el: _text(el) == want_text
                                and "minute" in _desc(el).lower())
        if idx is not None:
            try:
                device.click(index=idx)
                return True
            except Exception:
                pass
        device.wait()
    return False


def _time_via_input_mode(device, hour, minute):
    toggle = None
    for el in device.elements():
        d = _desc(el).lower()
        if "text input" in d or "keyboard" in d or ("input" in d and "mode" in d):
            toggle = el["index"]
            break
    if toggle is None:
        return False
    try:
        device.click(index=toggle)
    except Exception:
        return False
    fields = [el for el in device.elements() if el.get("editable")]
    if len(fields) < 2:
        return False
    try:
        device.input_text("%02d" % hour, index=fields[0]["index"])
        device.input_text("%02d" % minute, index=fields[1]["index"])
    except Exception:
        return False
    return True


def _set_time(device, hour, minute):
    if not _pick_hour(device, hour):
        raise RuntimeError("could not pick hour %d in the time picker" % hour)
    if minute % 5 == 0:
        if not _pick_minute(device, minute) and minute != 0:
            if not _time_via_input_mode(device, hour, minute):
                raise RuntimeError("could not pick minute %d" % minute)
    else:
        if not _time_via_input_mode(device, hour, minute):
            raise RuntimeError("could not pick minute %d" % minute)
    _click_ok(device)


def _set_row_time(device, which, hour, minute):
    want = {"%02d:%02d" % (hour, minute), "%d:%02d" % (hour, minute)}
    for _ in range(2):
        rows = _time_rows(device)
        if len(rows) <= which:
            raise RuntimeError("time row %d not found" % which)
        device.click(index=rows[which]["index"])
        try:
            _set_time(device, hour, minute)
        except Exception:
            continue
        rows = _time_rows(device)
        if len(rows) > which and _text(rows[which]) in want:
            return
    raise RuntimeError("failed to set time to %02d:%02d" % (hour, minute))


def program(device, binding):
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    device.open_app("Simple Calendar Pro")
    device.settle(2)

    fab = None
    for _ in range(4):
        fab = device.find(description="New Event")
        if fab is None:
            fab = device.find(text="New Event")
        if fab is not None:
            break
        device.wait()
    if fab is None:
        raise RuntimeError("'New Event' button not found")
    device.click(index=fab)

    ev = None
    for _ in range(4):
        if device.find(hint="Title") is not None:
            break
        ev = device.find(text="Event")
        if ev is not None:
            break
        device.wait()
    if ev is not None and device.find(hint="Title") is None:
        device.click(index=ev)

    title_idx = None
    for _ in range(5):
        title_idx = device.find(hint="Title")
        if title_idx is None:
            title_idx = device.find(text="Title")
        if title_idx is not None:
            break
        device.wait()
    if title_idx is None:
        raise RuntimeError("Title field not found")
    device.input_text(title, index=title_idx)

    desc_idx = device.find(hint="Description")
    if desc_idx is None:
        desc_idx = device.find(text="Description")
    if desc_idx is None:
        raise RuntimeError("Description field not found")
    device.input_text(description, index=desc_idx)

    rows = _date_rows(device)
    if not rows:
        raise RuntimeError("start date row not found")
    device.click(index=rows[0]["index"])
    _pick_date(device, year, month, day)
    rows = _date_rows(device)
    start_txt = _text(rows[0]) if rows else ""
    if str(year) not in start_txt or str(day) not in start_txt:
        raise RuntimeError("start date was not set correctly (got %r)" % start_txt)

    _set_row_time(device, 0, hour, 0)

    end_total = hour * 60 + duration
    end_hour = (end_total // 60) % 24
    end_min = end_total % 60
    _set_row_time(device, 1, end_hour, end_min)

    if end_total >= 24 * 60:
        d0 = datetime.date(year, month, day) + datetime.timedelta(
            days=end_total // (24 * 60))
        rows = _date_rows(device)
        if len(rows) > 1:
            device.click(index=rows[1]["index"])
            _pick_date(device, d0.year, d0.month, d0.day)

    save = device.find(description="Save")
    if save is None:
        save = device.find(text="Save")
    if save is None:
        raise RuntimeError("Save button not found")
    device.click(index=save)
    device.settle(1)
    return True
