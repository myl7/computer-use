import calendar
import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Event month"},
    "day": {"type": "integer", "minimum": 1, "maximum": 31, "description": "Event day of month"},
    "hour": {"type": "integer", "minimum": 0, "maximum": 23, "description": "Start hour in 24-hour clock"},
    "duration_mins": {"type": "integer", "minimum": 0, "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def _text(el):
    return str(el.get("text") or "")


def _hint(el):
    return str(el.get("hint") or "")


def _desc(el):
    return str(el.get("description") or "")


def _contains_any(s, words):
    s = s.lower()
    return any(w.lower() in s for w in words if w)


def _find_clickable_any(device, text_exact=None, text_contains=None, desc_exact=None,
                        desc_contains=None, hint_contains=None, occurrence="first"):
    matches = []
    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        idx = el.get("index", pos)
        t = _text(el).strip()
        d = _desc(el).strip()
        h = _hint(el).strip()
        hit = False

        if text_exact and any(t.lower() == x.lower() for x in text_exact):
            hit = True
        if desc_exact and any(d.lower() == x.lower() for x in desc_exact):
            hit = True
        if text_contains and _contains_any(t, text_contains):
            hit = True
        if desc_contains and _contains_any(d, desc_contains):
            hit = True
        if hint_contains and _contains_any(h, hint_contains):
            hit = True

        if hit:
            matches.append(idx)

    if not matches:
        return None
    return matches[0] if occurrence == "first" else matches[-1]


def _find_editable_any(device, labels, occurrence="first"):
    matches = []
    for pos, el in enumerate(device.elements()):
        if not el.get("editable"):
            continue
        idx = el.get("index", pos)
        s = (_text(el) + " " + _hint(el) + " " + _desc(el)).lower()
        if any(l.lower() in s for l in labels):
            matches.append(idx)

    if not matches:
        return None
    return matches[0] if occurrence == "first" else matches[-1]


def _editable_indexes(device):
    return [el.get("index", pos) for pos, el in enumerate(device.elements()) if el.get("editable")]


def _element_by_index(device, idx):
    for el in device.elements():
        if el.get("index") == idx:
            return el
    return None


def _month_header_present(device, year, month):
    names = [calendar.month_name[month], calendar.month_abbr[month]]
    for el in device.elements():
        t = _text(el).lower()
        if not t:
            continue
        if str(year) in t and any(n.lower() in t for n in names if n):
            return True
    return False


def _get_current_header(device):
    for el in device.elements():
        t = _text(el)
        if not t:
            continue
        m = re.search(r"([A-Za-z]{3,})\s+(\d{4})", t)
        if m:
            name = m.group(1).lower()
            year = int(m.group(2))
            for i in range(1, 13):
                full = calendar.month_name[i].lower()
                abbr = calendar.month_abbr[i].lower()
                if name == full or name == abbr or full.startswith(name) or abbr.startswith(name):
                    return (year, i)
    return None


def _navigate_to_month(device, target_year, target_month):
    if _month_header_present(device, target_year, target_month):
        return

    cur = _get_current_header(device)
    if cur is None:
        return

    target_idx = target_year * 12 + target_month
    cur_idx = cur[0] * 12 + cur[1]
    delta = target_idx - cur_idx
    if delta == 0:
        return

    primary = "left" if delta > 0 else "right"
    secondary = "right" if primary == "left" else "left"
    max_steps = min(12, abs(delta) + 2)

    for direction in (primary, secondary):
        steps = max_steps if direction == primary else max_steps + abs(delta) + 5
        for _ in range(steps):
            device.scroll(direction)
            device.settle(0.5)
            if _month_header_present(device, target_year, target_month):
                return
            new = _get_current_header(device)
            if new is not None and new[0] * 12 + new[1] == target_idx:
                return


def _find_day_cell(device, day):
    day_str = str(day)
    clickable = []
    nonclickable = []

    for pos, el in enumerate(device.elements()):
        idx = el.get("index", pos)
        t = _text(el).strip()
        d = _desc(el).strip()
        h = _hint(el).strip()
        if t == day_str or d == day_str or h == day_str:
            if el.get("clickable"):
                clickable.append(idx)
            else:
                nonclickable.append(idx)

    if clickable:
        return clickable[0]
    if nonclickable:
        return nonclickable[0]

    for pos, el in enumerate(device.elements()):
        idx = el.get("index", pos)
        if re.fullmatch(day_str, _text(el).strip()):
            if el.get("clickable"):
                return idx

    idx = device.find(text=day_str)
    if idx is not None:
        return idx

    idx = device.find(contains=day_str, clickable=True)
    if idx is not None:
        return idx

    return None


def _select_day(device, year, month, day):
    _navigate_to_month(device, year, month)

    idx = _find_day_cell(device, day)
    if idx is None:
        for direction in ("left", "right"):
            for _ in range(12):
                device.scroll(direction)
                device.settle(0.5)
                if _month_header_present(device, year, month):
                    idx = _find_day_cell(device, day)
                    if idx is not None:
                        break
                idx = _find_day_cell(device, day)
                if idx is not None:
                    break
            if idx is not None:
                break

    if idx is None:
        raise RuntimeError(f"Could not find calendar day {day} for {year}-{month}")

    device.click(index=idx)
    device.settle(1)


def _find_title_field(device):
    idx = _find_editable_any(device, ["title"])
    if idx is not None:
        return idx

    editables = _editable_indexes(device)
    if len(editables) >= 2:
        return editables[0]

    if len(editables) == 1:
        el = _element_by_index(device, editables[0])
        if el is not None:
            s = (_text(el) + " " + _hint(el) + " " + _desc(el)).lower()
            if any(k in s for k in ("title", "name", "subject")):
                return editables[0]

    return None


def _find_desc_field(device):
    idx = _find_editable_any(device, ["description"])
    if idx is not None:
        return idx

    editables = _editable_indexes(device)
    if len(editables) >= 2:
        return editables[1]

    if len(editables) == 1:
        el = _element_by_index(device, editables[0])
        if el is not None:
            s = (_text(el) + " " + _hint(el) + " " + _desc(el)).lower()
            if any(k in s for k in ("description", "notes", "details")) and not any(k in s for k in ("title", "name", "subject")):
                return editables[0]

    return None


def _find_new_event_button(device):
    idx = _find_clickable_any(
        device,
        desc_contains=["new event", "add event", "create event"],
        text_contains=["new event", "add event"],
        hint_contains=["new event", "add event"],
    )
    if idx is not None:
        return idx

    idx = _find_clickable_any(device, desc_contains=["add", "new", "create"], text_contains=["+"])
    if idx is not None:
        return idx

    idx = _find_clickable_any(device, text_contains=["new event"], desc_contains=["event"])
    return idx


def _find_event_option(device):
    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        t = _text(el).strip().lower()
        d = _desc(el).strip().lower()
        if t == "event" or d == "event":
            return el.get("index", pos)

    for pos, el in enumerate(device.elements()):
        if _text(el).strip().lower() == "event":
            return el.get("index", pos)

    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        t = _text(el).lower()
        d = _desc(el).lower()
        if ("event" in t or "event" in d) and "new" not in t and "new" not in d:
            return el.get("index", pos)

    return None


def _create_event(device):
    if _find_title_field(device) is not None:
        return

    new_idx = _find_new_event_button(device)
    if new_idx is not None:
        device.click(index=new_idx)
        device.settle(1)
        if _find_title_field(device) is not None:
            return

    event_idx = _find_event_option(device)
    if event_idx is not None:
        device.click(index=event_idx)
        device.settle(1)
        if _find_title_field(device) is not None:
            return

    event_idx = _find_clickable_any(device, text_contains=["event"], desc_contains=["event"], hint_contains=["event"])
    if event_idx is not None:
        device.click(index=event_idx)
        device.settle(1)

    if _find_title_field(device) is None:
        raise RuntimeError("Could not open event editor")


def _input_fields(device, title, description):
    idx = _find_title_field(device)
    if idx is None:
        raise RuntimeError("Could not find event Title field")
    device.input_text(title, index=idx)
    device.settle(0.5)

    idx = _find_desc_field(device)
    if idx is None:
        device.scroll("down")
        device.settle(0.5)
        idx = _find_desc_field(device)

    if idx is None:
        raise RuntimeError("Could not find event Description field")

    device.input_text(description, index=idx)
    device.settle(0.5)


def _looks_like_date(t):
    if not t or ":" in t:
        return False
    if re.search(r"\b\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}\b", t):
        return True
    if re.search(r"\b\d{1,2}\s+[A-Za-z]{3,}\s+\d{4}\b", t):
        return True
    if re.search(r"\b[A-Za-z]{3,}\s+\d{1,2},?\s+\d{4}\b", t):
        return True
    return False


def _date_text_matches(t, dt):
    if not _looks_like_date(t):
        return False

    if str(dt.day) not in t and f"{dt.day:02d}" not in t:
        return False

    if str(dt.year) not in t and str(dt.year)[-2:] not in t:
        return False

    names = [calendar.month_name[dt.month], calendar.month_abbr[dt.month]]
    tl = t.lower()
    if any(n.lower() in tl for n in names if n):
        return True

    if re.search(rf"(^|[/\-]){dt.month:02d}([/\-]|$)", t):
        return True
    if re.search(rf"(^|[/\-]){dt.month}([/\-]|$)", t):
        return True

    return False


def _find_date_fields(device):
    matches = []
    for pos, el in enumerate(device.elements()):
        if el.get("editable"):
            continue
        t = _text(el)
        if _looks_like_date(t):
            matches.append((bool(el.get("clickable")), el.get("index", pos)))

    if any(c for c, i in matches):
        return [i for c, i in matches if c]
    return [i for c, i in matches]


def _find_ok(device):
    for label in ("OK", "Ok", "Okay", "Done", "Confirm"):
        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx

    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        t = _text(el).strip().lower()
        d = _desc(el).strip().lower()
        if t in ("ok", "done", "confirm") or d in ("ok", "done", "confirm"):
            return el.get("index", pos)

    return None


def _click_ok(device):
    idx = _find_ok(device)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        return

    device.navigate_back()
    device.settle(0.5)

    idx = _find_ok(device)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        return

    raise RuntimeError("Could not find OK button")


def _set_date_field(device, field_number, dt):
    fields = _find_date_fields(device)
    if field_number >= len(fields):
        for _ in range(3):
            device.scroll("down")
            device.settle(0.5)
            fields = _find_date_fields(device)
            if field_number < len(fields):
                break

    if field_number >= len(fields):
        raise RuntimeError("Date field not found")

    device.click(index=fields[field_number])
    device.settle(1)

    _navigate_to_month(device, dt.year, dt.month)

    idx = _find_day_cell(device, dt.day)
    if idx is None:
        for direction in ("left", "right"):
            for _ in range(12):
                device.scroll(direction)
                device.settle(0.5)
                idx = _find_day_cell(device, dt.day)
                if idx is not None:
                    break
            if idx is not None:
                break

    if idx is None:
        raise RuntimeError("Could not find day in date picker")

    device.click(index=idx)
    device.settle(0.5)
    _click_ok(device)


def _ensure_start_date(device, dt):
    try:
        fields = _find_date_fields(device)
        if not fields:
            return

        for idx in fields:
            el = _element_by_index(device, idx)
            if el is not None and _date_text_matches(_text(el), dt):
                return

        _set_date_field(device, 0, dt)
    except Exception:
        pass


def _ensure_end_date(device, dt):
    try:
        fields = _find_date_fields(device)
        if len(fields) < 2:
            device.scroll("down")
            device.settle(0.5)
            fields = _find_date_fields(device)
        if len(fields) < 2:
            device.scroll("up")
            device.settle(0.5)
            fields = _find_date_fields(device)

        if len(fields) < 2:
            return

        el = _element_by_index(device, fields[1])
        if el is not None and _date_text_matches(_text(el), dt):
            return

        _set_date_field(device, 1, dt)
    except Exception:
        pass


def _time_field_indexes(device):
    matches = []
    for pos, el in enumerate(device.elements()):
        if el.get("editable"):
            continue

        t = _text(el).strip()
        score = 0
        if re.fullmatch(r"\d{1,2}:\d{2}", t):
            score = 2
        elif re.search(r"\d{1,2}:\d{2}", t):
            score = 1

        if score:
            matches.append((score, bool(el.get("clickable")), el.get("index", pos)))

    if sum(1 for s, c, i in matches if s == 2) >= 2:
        return [i for s, c, i in matches if s == 2]

    return [i for s, c, i in matches]


def _time_text_matches(t, hour, minute):
    if not t:
        return False

    patterns = [
        f"{hour:02d}:{minute:02d}",
        f"{hour}:{minute:02d}",
        f"{hour:02d}:{minute}",
        f"{hour}:{minute}",
    ]

    for p in patterns:
        if re.search(rf"(?<!\d){re.escape(p)}(?!\d)", t):
            return True
    return False


def _open_time_field(device, which, avoid_hour=None, avoid_minute=None):
    for _ in range(8):
        idxs = _time_field_indexes(device)

        if len(idxs) > which:
            device.click(index=idxs[which])
            device.settle(1)
            return

        if len(idxs) == 1 and which == 1:
            idx = idxs[0]
            el = _element_by_index(device, idx)
            if (
                avoid_hour is not None
                and avoid_minute is not None
                and el is not None
                and _time_text_matches(_text(el), avoid_hour, avoid_minute)
            ):
                device.scroll("down")
                device.settle(0.5)
                continue

            device.click(index=idx)
            device.settle(1)
            return

        device.scroll("down")
        device.settle(0.5)

    raise RuntimeError(f"Could not find time field {which}")


def _clock_value_matches(el, value, unit):
    t = _text(el).strip()
    d = _desc(el).strip()
    vals = {str(value), f"{value:02d}"}

    for v in vals:
        if t == v:
            return True
        if d == f"{v} {unit}":
            return True
        if d.endswith(f"{v} {unit}"):
            return True
        if re.search(rf"(^|\s){re.escape(v)}\s+{re.escape(unit)}(\s|$)", d, re.I):
            return True

    return False


def _select_clock_value(device, value, unit):
    matches = []
    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        if _clock_value_matches(el, value, unit):
            matches.append(el.get("index", pos))

    if not matches:
        raise RuntimeError(f"Could not find {unit} {value} in time picker")

    device.click(index=matches[-1])
    device.settle(0.5)


def _click_mode_display(device, unit):
    matches = []
    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        d = _desc(el).strip()
        if d.lower().endswith(unit) or re.search(rf"\b{re.escape(unit)}$", d, re.I):
            matches.append(el.get("index", pos))

    if not matches:
        return False

    device.click(index=matches[0])
    device.settle(0.5)
    return True


def _try_set_time_editable(device, hour, minute, allow_generic=False):
    hour_idx = _find_editable_any(device, ["hour"])
    minute_idx = _find_editable_any(device, ["minute"])

    if hour_idx is not None and minute_idx is not None:
        device.input_text(f"{hour:02d}", index=hour_idx)
        device.settle(0.3)
        device.input_text(f"{minute:02d}", index=minute_idx)
        device.settle(0.3)
        return True

    time_idx = _find_editable_any(device, ["time"])
    if time_idx is not None:
        device.input_text(f"{hour:02d}:{minute:02d}", index=time_idx)
        device.settle(0.3)
        return True

    if allow_generic:
        editables = _editable_indexes(device)
        if len(editables) >= 2:
            device.input_text(f"{hour:02d}", index=editables[0])
            device.settle(0.3)
            device.input_text(f"{minute:02d}", index=editables[1])
            device.settle(0.3)
            return True

        if len(editables) == 1:
            device.input_text(f"{hour:02d}:{minute:02d}", index=editables[0])
            device.settle(0.3)
            return True

    return False


def _try_switch_to_keyboard_input(device):
    for pos, el in enumerate(device.elements()):
        if not el.get("clickable"):
            continue
        s = (_text(el) + " " + _hint(el) + " " + _desc(el)).lower()
        if any(k in s for k in ("keyboard", "text input", "input mode", "enter time")):
            device.click(index=el.get("index", pos))
            device.settle(0.5)
            return True
    return False


def _set_time_in_picker(device, hour, minute):
    if _try_set_time_editable(device, hour, minute, False):
        return

    hour_ok = False
    try:
        _select_clock_value(device, hour, "hours")
        hour_ok = True
    except RuntimeError:
        if _click_mode_display(device, "hours"):
            try:
                _select_clock_value(device, hour, "hours")
                hour_ok = True
            except RuntimeError:
                pass

    if not hour_ok:
        if _try_switch_to_keyboard_input(device) and _try_set_time_editable(device, hour, minute, True):
            return
        raise RuntimeError(f"Could not set hour {hour}")

    minute_ok = False
    try:
        _select_clock_value(device, minute, "minutes")
        minute_ok = True
    except RuntimeError:
        if _click_mode_display(device, "minutes"):
            try:
                _select_clock_value(device, minute, "minutes")
                minute_ok = True
            except RuntimeError:
                pass

    if not minute_ok:
        if _try_switch_to_keyboard_input(device) and _try_set_time_editable(device, hour, minute, True):
            return
        raise RuntimeError(f"Could not set minute {minute}")


def _set_start_time(device, hour, minute):
    _open_time_field(device, 0)
    _set_time_in_picker(device, hour, minute)
    _click_ok(device)


def _set_end_time(device, hour, minute, avoid_hour=None, avoid_minute=None):
    _open_time_field(device, 1, avoid_hour, avoid_minute)
    _set_time_in_picker(device, hour, minute)
    _click_ok(device)


def _find_save(device):
    idx = _find_clickable_any(device, desc_contains=["save"], text_contains=["save"], hint_contains=["save"])
    if idx is not None:
        return idx

    for pos, el in enumerate(device.elements()):
        if el.get("clickable") and _desc(el).strip().lower() == "save":
            return el.get("index", pos)

    return None


def _save_event(device):
    for _ in range(4):
        idx = _find_save(device)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            return
        device.scroll("up")
        device.settle(0.5)

    device.navigate_back()
    device.settle(0.5)

    idx = _find_save(device)
    if idx is not None:
        device.click(index=idx)
        device.settle(1.5)
        return

    raise RuntimeError("Could not find Save button")


def program(device, binding):
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if duration_mins < 0:
        raise ValueError("duration_mins must be non-negative")

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

    device.open_app("Simple Calendar Pro")
    device.settle(1.5)

    try:
        _select_day(device, year, month, day)
    except Exception:
        pass

    _create_event(device)
    _input_fields(device, event_title, event_description)

    _ensure_start_date(device, start_dt)

    _set_start_time(device, hour, 0)
    _set_end_time(device, end_dt.hour, end_dt.minute, hour, 0)

    if end_dt.date() != start_dt.date():
        _ensure_end_date(device, end_dt)

    _save_event(device)
    return True
