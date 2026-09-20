import re
from datetime import date, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "description": "Event month, 1-12"},
    "day": {"type": "integer", "description": "Event day of month"},
    "hour": {"type": "integer", "description": "Event start hour, 0-23, 24-hour clock"},
    "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}

_MONTH_FULL = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTH_NAMES = {}
for _i, _name in enumerate(_MONTH_FULL, 1):
    _MONTH_NAMES[_name.lower()] = _i
    _MONTH_NAMES[_name[:3].lower()] = _i
_MONTH_KEYS = sorted(_MONTH_NAMES.keys(), key=len, reverse=True)


def _parse_month_year(text):
    s = str(text or "")
    if not s:
        return None, None
    low = s.lower()

    iso = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", s)
    if iso:
        try:
            y = int(iso.group(1))
            m = int(iso.group(2))
            d = int(iso.group(3))
            if 1 <= m <= 12 and 1 <= d <= 31:
                return y, m
        except Exception:
            pass

    ym = re.search(r"\b(\d{4})[-/](\d{1,2})\b", s)
    if ym:
        try:
            y = int(ym.group(1))
            m = int(ym.group(2))
            if 1 <= m <= 12:
                return y, m
        except Exception:
            pass

    my = re.search(r"\b(\d{1,2})[-/](\d{4})\b", s)
    if my:
        try:
            m = int(my.group(1))
            y = int(my.group(2))
            if 1 <= m <= 12:
                return y, m
        except Exception:
            pass

    num_year = re.search(r"\b(\d{1,2})\s+(\d{4})\b", s)
    if num_year:
        try:
            m = int(num_year.group(1))
            y = int(num_year.group(2))
            if 1 <= m <= 12:
                return y, m
        except Exception:
            pass

    year = None
    y4 = re.search(r"\b(19\d{2}|20\d{2}|21\d{2}|22\d{2})\b", s)
    if y4:
        year = int(y4.group(1))

    month = None
    for name in _MONTH_KEYS:
        if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
            month = _MONTH_NAMES[name]
            break

    return year, month


def _get_current_year_month(device):
    best_year = None
    best_month = None
    for el in device.elements():
        for key in ("text", "description", "hint"):
            y, m = _parse_month_year(el.get(key) or "")
            if y and m:
                return y, m
            if y and best_year is None:
                best_year = y
            if m and best_month is None:
                best_month = m
    return best_year, best_month


def _find_clickable(device, text=None, contains=None, hint=None, description=None,
                    desc_contains=None, clickable=None, editable=None, class_name=None):
    for el in device.elements():
        if clickable is not None and bool(el.get("clickable")) != clickable:
            continue
        if editable is not None and bool(el.get("editable")) != editable:
            continue
        if class_name is not None and el.get("class_name") != class_name:
            continue

        t = str(el.get("text") or "").strip()
        d = str(el.get("description") or "").strip()
        h = str(el.get("hint") or "").strip()

        matched = False
        if text is not None and t.lower() == text.lower():
            matched = True
        if contains is not None and contains.lower() in t.lower():
            matched = True
        if hint is not None and hint.lower() in h.lower():
            matched = True
        if description is not None and d.lower() == description.lower():
            matched = True
        if desc_contains is not None and desc_contains.lower() in d.lower():
            matched = True

        if matched:
            return el.get("index")
    return None


def _is_event_screen(device):
    for lab in ("Title", "Event title", "title"):
        if _find_clickable(device, hint=lab, editable=True) is not None:
            return True
        if _find_clickable(device, text=lab, editable=True) is not None:
            return True
    return False


def _find_editable(device, labels, max_scrolls=3):
    for attempt in range(max_scrolls + 1):
        for el in device.elements():
            if not bool(el.get("editable")):
                continue
            hint = str(el.get("hint") or "").lower()
            text = str(el.get("text") or "").lower()
            for lab in labels:
                l = lab.lower()
                if l in hint or l == text.strip():
                    return el.get("index")
        if attempt < max_scrolls:
            device.scroll(direction="down")
    return None


def _input_text(device, labels, text):
    idx = _find_editable(device, labels)
    if idx is None:
        raise RuntimeError(f"Could not find editable field for {labels}")
    device.input_text(text, index=idx)
    device.settle(0.5)


def _find_month_arrow(device, direction):
    prev_words = ("previous month", "prev month", "go to previous", "back month", "previous")
    next_words = ("next month", "forward month", "go to next", "next")
    for el in device.elements():
        if not bool(el.get("clickable")):
            continue
        d = str(el.get("description") or "").lower()
        t = str(el.get("text") or "").lower()
        if direction == "prev":
            if any(w in d for w in prev_words) or any(w in t for w in ("<", "‹", "←", "previous")):
                return el.get("index")
        else:
            if any(w in d for w in next_words) or any(w in t for w in (">", "›", "→", "next")):
                return el.get("index")
    return None


def _navigate_to_month(device, target_year, target_month, max_steps=36):
    for _ in range(max_steps):
        y, m = _get_current_year_month(device)
        if y is None or m is None:
            return False
        if y == target_year and m == target_month:
            return True

        diff = (target_year - y) * 12 + (target_month - m)
        direction = "next" if diff > 0 else "prev"
        idx = _find_month_arrow(device, direction)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
        else:
            device.scroll(direction="left" if direction == "next" else "right")
            device.settle(1)

        ny, nm = _get_current_year_month(device)
        if ny == y and nm == m:
            return False
    return False


def _click_year_button(device):
    for el in device.elements():
        if bool(el.get("clickable")):
            t = str(el.get("text") or "").strip()
            if re.fullmatch(r"\d{4}", t):
                device.click(index=el.get("index"))
                device.settle(1)
                return True
    idx = _find_clickable(device, desc_contains="change year", clickable=True)
    if idx is None:
        idx = _find_clickable(device, desc_contains="year", clickable=True)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        return True
    return False


def _is_year_list_open(device):
    count = 0
    for el in device.elements():
        if bool(el.get("clickable")) and re.fullmatch(r"\d{4}", str(el.get("text") or "").strip()):
            count += 1
            if count >= 3:
                return True
    return False


def _select_year_in_list(device, year, current_year=None):
    target = str(year)

    def try_click():
        idx = _find_clickable(device, text=target, clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return True
        idx = _find_clickable(device, description=target, clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return True
        return False

    if try_click():
        return True

    if current_year is None:
        current_year, _ = _get_current_year_month(device)

    directions = ("down", "up")
    if current_year is not None:
        if year > current_year:
            directions = ("down", "up")
        elif year < current_year:
            directions = ("up", "down")

    for direction in directions:
        for _ in range(60):
            device.scroll(direction=direction)
            device.settle(0.5)
            if try_click():
                return True
    return False


def _select_day(device, day, year=None, month=None):
    target = str(day)
    for _ in range(3):
        candidates = []
        for el in device.elements():
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "").strip()
            d = str(el.get("description") or "").strip()
            h = str(el.get("hint") or "").strip()
            if t == target or d == target or h == target:
                candidates.append(el)
            elif d and re.search(r"(?<!\d)" + re.escape(target) + r"(?!\d)", d):
                candidates.append(el)

        if candidates:
            best = None
            if year is not None and month is not None:
                for el in candidates:
                    y, m = _parse_month_year(el.get("description") or "")
                    if y == year and m == month:
                        best = el
                        break
            if best is None:
                best = sorted(
                    candidates,
                    key=lambda el: (
                        0 if str(el.get("text") or "").strip() == target else 1,
                        0 if el.get("class_name") == "View" else 1,
                        el.get("index") if el.get("index") is not None else 999999,
                    ),
                )[0]
            device.click(index=best.get("index"))
            device.settle(1)
            return True

        device.scroll(direction="down")

    idx = _find_clickable(device, text=target, clickable=True)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        return True
    return False


def _click_ok_if_present(device):
    for _ in range(2):
        for label in ("OK", "Ok", "Okay", "Confirm", "Done"):
            idx = _find_clickable(device, text=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                return True
            idx = _find_clickable(device, description=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                return True
        device.scroll(direction="down")
    return False


def _date_picker_open(device):
    if _find_clickable(device, text="OK", clickable=True) is not None:
        return True
    if _find_month_arrow(device, "next") is not None:
        return True
    if _find_month_arrow(device, "prev") is not None:
        return True
    if _is_year_list_open(device):
        return True
    return False


def _set_date_via_editable_fields(device, year, month, day):
    y_idx = _find_editable(device, ["Year", "year"], max_scrolls=1)
    m_idx = _find_editable(device, ["Month", "month"], max_scrolls=1)
    d_idx = _find_editable(device, ["Day", "day"], max_scrolls=1)

    if y_idx is not None and m_idx is not None and d_idx is not None:
        device.input_text(str(year), index=y_idx)
        month_name = _MONTH_FULL[month - 1]
        for val in (str(month), month_name, month_name[:3]):
            device.input_text(val, index=m_idx)
        device.input_text(str(day), index=d_idx)
        device.keyboard_enter()
        return True

    date_idx = _find_editable(device, ["Date", "date", "MM/DD/YYYY", "YYYY-MM-DD"], max_scrolls=1)
    if date_idx is not None:
        for val in (
            f"{year}-{month:02d}-{day:02d}",
            f"{month:02d}/{day:02d}/{year}",
            f"{month}/{day}/{year}",
            f"{day:02d}/{month:02d}/{year}",
        ):
            device.input_text(val, index=date_idx)
            device.keyboard_enter()
            return True

    return False


def _set_date_in_picker(device, year, month, day):
    if _set_date_via_editable_fields(device, year, month, day):
        return _click_ok_if_present(device) or not _date_picker_open(device)

    y, m = _get_current_year_month(device)
    if y is not None and y != year:
        if _click_year_button(device):
            _select_year_in_list(device, year, y)
            if _is_year_list_open(device):
                device.navigate_back()
                device.settle(1)
            y, m = _get_current_year_month(device)

    _navigate_to_month(device, year, month)
    day_ok = _select_day(device, day, year, month)
    y, m = _get_current_year_month(device)

    if not day_ok and (y != year or m != month):
        return False

    if _click_ok_if_present(device):
        return True
    return not _date_picker_open(device)


def _find_date_row(device, which):
    labels = ("start date",) if which == "start" else ("end date",)
    for _ in range(4):
        elems = device.elements()

        for el in elems:
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "").lower()
            d = str(el.get("description") or "").lower()
            if any(lab in t or lab in d for lab in labels):
                return el.get("index")

        candidates = []
        for el in elems:
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "")
            if not any(ch.isdigit() for ch in t):
                continue
            if ":" in t:
                continue
            low = t.lower()
            if any(k in low for k in ("time", "before", "minutes", "hours", "days", "reminder", "repeat", "all-day", "event", "task")):
                continue
            candidates.append(el.get("index"))

        if which == "start" and candidates:
            return candidates[0]
        if which == "end" and len(candidates) >= 2:
            return candidates[1]

        device.scroll(direction="down")
    return None


def _open_picker_for_row(device, row_idx, picker, which):
    for _ in range(3):
        if row_idx is None:
            return False
        device.click(index=row_idx)
        device.settle(1)

        if picker == "date":
            if _date_picker_open(device):
                return True
            row_idx = _find_date_row(device, which)
        else:
            if _has_time_picker(device):
                return True
            row_idx = _find_time_row(device, which)

    return False


def _set_date_for_row(device, which, year, month, day):
    row_idx = _find_date_row(device, which)
    if row_idx is None:
        return False
    if not _open_picker_for_row(device, row_idx, "date", which):
        return False
    return _set_date_in_picker(device, year, month, day)


def _has_time_picker(device):
    for el in device.elements():
        if bool(el.get("clickable")):
            d = str(el.get("description") or "").lower()
            if d.endswith(" hours") or d.endswith(" minutes"):
                return True
    return False


def _find_time_row(device, which):
    labels = ("start time",) if which == "start" else ("end time",)
    for _ in range(4):
        elems = device.elements()

        for el in elems:
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "").lower()
            d = str(el.get("description") or "").lower()
            if any(lab in t or lab in d for lab in labels):
                return el.get("index")

        candidates = []
        for el in elems:
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "")
            if ":" in t and any(ch.isdigit() for ch in t):
                candidates.append(el.get("index"))

        if which == "start" and candidates:
            return candidates[0]
        if which == "end" and len(candidates) >= 2:
            return candidates[1]

        device.scroll(direction="down")
    return None


def _click_time_selector(device, kind):
    plural = kind + "s"
    matches = []
    for el in device.elements():
        if not bool(el.get("clickable")):
            continue
        d = str(el.get("description") or "").lower()
        t = str(el.get("text") or "").strip()
        if d.endswith(plural) and t.isdigit():
            matches.append(el)

    if not matches:
        return False

    if kind == "minute":
        matches.sort(key=lambda el: (
            0 if len(str(el.get("text") or "").strip()) == 2 else 1,
            el.get("index") if el.get("index") is not None else 999999,
        ))
    else:
        matches.sort(key=lambda el: el.get("index") if el.get("index") is not None else 999999)

    device.click(index=matches[0].get("index"))
    device.settle(1)
    return True


def _select_time_value(device, kind, value):
    plural = kind + "s"
    descs = [f"{value} {plural}", f"{value:02d} {plural}"]
    texts = {str(value), f"{value:02d}"}

    for _ in range(4):
        elems = device.elements()

        for el in elems:
            if not bool(el.get("clickable")):
                continue
            d = str(el.get("description") or "").strip()
            if d in descs:
                device.click(index=el.get("index"))
                device.settle(1)
                return True

        for el in elems:
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "").strip()
            d = str(el.get("description") or "").strip().lower()
            if t in texts and (d.endswith(plural) or plural in d):
                device.click(index=el.get("index"))
                device.settle(1)
                return True

        if kind == "minute":
            for el in elems:
                if not bool(el.get("clickable")):
                    continue
                t = str(el.get("text") or "").strip()
                d = str(el.get("description") or "").lower()
                if t == f"{value:02d}" and "hour" not in d:
                    device.click(index=el.get("index"))
                    device.settle(1)
                    return True

        device.scroll(direction="down")

    for desc in descs:
        idx = _find_clickable(device, description=desc, clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return True

    return False


def _set_time_via_editable_fields(device, hour, minute):
    h_idx = _find_editable(device, ["Hours", "Hour", "hours", "hour"], max_scrolls=1)
    m_idx = _find_editable(device, ["Minutes", "Minute", "minutes", "minute"], max_scrolls=1)
    if h_idx is not None and m_idx is not None:
        device.input_text(f"{hour:02d}", index=h_idx)
        device.input_text(f"{minute:02d}", index=m_idx)
        device.keyboard_enter()
        return True
    return False


def _set_time_in_picker(device, hour, minute):
    if _set_time_via_editable_fields(device, hour, minute):
        return _click_ok_if_present(device) or not _has_time_picker(device)

    if not _select_time_value(device, "hour", hour):
        _click_time_selector(device, "hour")
        if not _select_time_value(device, "hour", hour):
            return False

    _click_time_selector(device, "minute")
    if not _select_time_value(device, "minute", minute):
        if not _select_time_value(device, "minute", minute):
            desc = f"{minute} minutes"
            if _find_clickable(device, description=desc, clickable=True) is None:
                desc = f"{minute:02d} minutes"
                if _find_clickable(device, description=desc, clickable=True) is None:
                    return False

    if _click_ok_if_present(device):
        return True
    return not _has_time_picker(device)


def _set_time_for_row(device, which, hour, minute):
    row_idx = _find_time_row(device, which)
    if row_idx is None:
        return False
    if not _open_picker_for_row(device, row_idx, "time", which):
        return False
    return _set_time_in_picker(device, hour, minute)


def _click_day_cell(device, day):
    target = str(day)
    for _ in range(3):
        candidates = []
        for el in device.elements():
            if not bool(el.get("clickable")):
                continue
            t = str(el.get("text") or "").strip()
            d = str(el.get("description") or "").strip()
            h = str(el.get("hint") or "").strip()
            if t == target or d == target or h == target:
                candidates.append(el)
            elif d and re.search(r"(?<!\d)" + re.escape(target) + r"(?!\d)", d):
                candidates.append(el)

        if candidates:
            best = sorted(
                candidates,
                key=lambda el: (
                    0 if str(el.get("text") or "").strip() == target else 1,
                    0 if el.get("class_name") == "View" else 1,
                    el.get("index") if el.get("index") is not None else 999999,
                ),
            )[0]
            device.click(index=best.get("index"))
            device.settle(1)
            return True

        device.scroll(direction="down")

    idx = _find_clickable(device, text=target, clickable=True)
    if idx is None:
        idx = _find_clickable(device, contains=target, clickable=True)
    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        return True
    return False


def _prepare_date_on_main(device, year, month, day):
    y, m = _get_current_year_month(device)
    if y is not None and m is not None:
        if y != year or m != month:
            if not _navigate_to_month(device, year, month):
                return False
        return _click_day_cell(device, day)
    return False


def _create_event(device):
    if _is_event_screen(device):
        return

    idx = _find_clickable(device, desc_contains="new event", clickable=True)
    if idx is None:
        idx = _find_clickable(device, desc_contains="add event", clickable=True)
    if idx is None:
        idx = _find_clickable(device, text="New event", clickable=True)

    if idx is not None:
        device.click(index=idx)
        device.settle(1)
        if _is_event_screen(device):
            return

    for _ in range(3):
        idx = _find_clickable(device, text="Event", clickable=True)
        if idx is None:
            idx = _find_clickable(device, description="Event", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            if _is_event_screen(device):
                return

        device.scroll(direction="up")
        idx = _find_clickable(device, text="Event", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            if _is_event_screen(device):
                return

        device.scroll(direction="down")

    raise RuntimeError("Could not open event editor")


def _save_event(device):
    for _ in range(3):
        idx = _find_clickable(device, description="Save", clickable=True)
        if idx is None:
            idx = _find_clickable(device, text="Save", clickable=True)
        if idx is None:
            idx = _find_clickable(device, desc_contains="save", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return True
        device.scroll(direction="up")
    raise RuntimeError("Could not save event")


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"]) % 24
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    device.open_app("Simple Calendar Pro")
    device.settle(1.0)

    main_date_set = _prepare_date_on_main(device, year, month, day)

    _create_event(device)

    _input_text(device, ["Title", "Event title", "title"], event_title)
    _input_text(device, ["Description", "Event description", "description"], event_description)

    total_minutes = hour * 60 + duration_mins
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60
    end_days = total_minutes // (24 * 60)

    start_date = date(year, month, day)
    end_date = start_date + timedelta(days=end_days)

    if not main_date_set:
        if not _set_date_for_row(device, "start", year, month, day):
            raise RuntimeError("Could not set start date")
        if not _set_date_for_row(device, "end", end_date.year, end_date.month, end_date.day):
            if end_date != start_date:
                raise RuntimeError("Could not set end date")
    else:
        if end_date != start_date:
            _set_date_for_row(device, "end", end_date.year, end_date.month, end_date.day)

    if not _set_time_for_row(device, "start", hour, 0):
        raise RuntimeError("Could not set start time")

    if not _set_time_for_row(device, "end", end_hour, end_minute):
        raise RuntimeError("Could not set end time")

    _save_event(device)
    return True
