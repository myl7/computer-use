import datetime
import re

PARAMS_SCHEMA = {
    "year": {
        "type": "integer",
        "description": "Event year, e.g. 2023",
        "required": True,
    },
    "month": {
        "type": "integer",
        "description": "Event month, 1-12",
        "required": True,
    },
    "day": {
        "type": "integer",
        "description": "Event day of month",
        "required": True,
    },
    "hour": {
        "type": "integer",
        "description": "Event start hour on a 24-hour clock, 0-23",
        "required": True,
    },
    "duration_mins": {
        "type": "integer",
        "description": "Event duration in minutes",
        "required": True,
    },
    "event_title": {
        "type": "string",
        "description": "Calendar event title",
        "required": True,
    },
    "event_description": {
        "type": "string",
        "description": "Calendar event description",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    MONTHS_FULL = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    MONTHS_ABBR = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    MONTH_MAP = {}
    for i, name in enumerate(MONTHS_FULL, 1):
        MONTH_MAP[name.lower()] = i
        MONTH_MAP[name[:3].lower()] = i

    def _as_int(value, name):
        if value is None:
            raise ValueError(f"binding['{name}'] is missing")
        s = str(value).strip()
        m = re.search(r"-?\d+", s)
        if not m:
            raise ValueError(f"binding['{name}'] must contain an integer, got {value!r}")
        return int(m.group())

    year = _as_int(binding["year"], "year")
    month = _as_int(binding["month"], "month")
    day = _as_int(binding["day"], "day")
    hour = _as_int(binding["hour"], "hour") % 24
    duration_mins = _as_int(binding["duration_mins"], "duration_mins")
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

    def _norm(value):
        return str(value or "").strip()

    def _all_text(e):
        return " ".join(
            [
                _norm(e.get("text")),
                _norm(e.get("hint")),
                _norm(e.get("description")),
            ]
        ).strip()

    def _find_by(**kwargs):
        try:
            return device.find(**kwargs)
        except TypeError:
            pass
        except Exception:
            return None

        for e in device.elements():
            ok = True
            for k, v in kwargs.items():
                if k == "clickable":
                    if bool(e.get("clickable")) != bool(v):
                        ok = False
                        break
                elif k == "editable":
                    if bool(e.get("editable")) != bool(v):
                        ok = False
                        break
                elif k == "text":
                    if _norm(e.get("text")) != _norm(v):
                        ok = False
                        break
                elif k == "hint":
                    if _norm(e.get("hint")) != _norm(v):
                        ok = False
                        break
                elif k == "description":
                    if _norm(e.get("description")) != _norm(v):
                        ok = False
                        break
                elif k == "contains":
                    if _norm(v).lower() not in _all_text(e).lower():
                        ok = False
                        break
            if ok:
                return e.get("index")
        return None

    def _click_index(idx):
        if idx is None:
            return False
        device.click(index=idx)
        device.settle(1.0)
        return True

    def _click_find(**kwargs):
        idx = _find_by(**kwargs)
        return _click_index(idx)

    def _parse_month_year(text):
        text = _norm(text)
        if not text:
            return None

        m = re.search(r"([A-Za-z]+)\s+(\d{4})", text)
        if m:
            name = m.group(1).lower()
            if name in MONTH_MAP:
                return int(m.group(2)), MONTH_MAP[name]

        m = re.search(r"(\d{4})[-/](\d{1,2})", text)
        if m:
            try:
                return int(m.group(1)), int(m.group(2))
            except Exception:
                pass

        m = re.search(r"(\d{1,2})[-/](\d{4})", text)
        if m:
            try:
                return int(m.group(2)), int(m.group(1))
            except Exception:
                pass

        m = re.search(r"^([A-Za-z]+)$", text)
        if m:
            name = m.group(1).lower()
            if name in MONTH_MAP:
                return None, MONTH_MAP[name]

        return None

    def _current_month_year():
        for e in device.elements():
            ym = _parse_month_year(_all_text(e))
            if ym is not None:
                return ym
        return None

    def _click_month_arrow(direction):
        if direction == "next":
            descs = ("Next month", "Go to next month", "Next", "Forward")
            texts = (">", "›")
            contains = ("next",)
        else:
            descs = ("Previous month", "Go to previous month", "Previous", "Back")
            texts = ("<", "‹")
            contains = ("prev",)

        for desc in descs:
            idx = _find_by(description=desc, clickable=True)
            if idx is not None:
                return _click_index(idx)

        for text in texts:
            idx = _find_by(text=text, clickable=True)
            if idx is not None:
                return _click_index(idx)

        for c in contains:
            idx = _find_by(contains=c, clickable=True)
            if idx is not None:
                return _click_index(idx)

        return False

    def _navigate_main_to_month(target_year, target_month):
        target = target_year * 12 + target_month
        for _ in range(24):
            cur = _current_month_year()
            if cur is None:
                return False

            cy, cm = cur
            if cy is None:
                cy = target_year

            cur_idx = cy * 12 + cm
            if cur_idx == target:
                return True

            if cur_idx < target:
                if not _click_month_arrow("next"):
                    device.scroll("left")
            else:
                if not _click_month_arrow("prev"):
                    device.scroll("right")

            device.settle(1.0)

        return False

    def _click_day_on_main(d, m, y):
        month_full = MONTHS_FULL[m - 1]
        month_abbr = MONTHS_ABBR[m - 1]

        for desc in (
            f"{month_full} {d}",
            f"{d} {month_full}",
            f"{month_abbr} {d}",
            f"{d} {month_abbr}",
            f"{y}-{m:02d}-{d:02d}",
            str(d),
            f"{d:02d}",
        ):
            idx = _find_by(description=desc, clickable=True)
            if idx is not None:
                return _click_index(idx)

        candidates = []
        for e in device.elements():
            if not e.get("clickable"):
                continue
            text = _norm(e.get("text"))
            if text == str(d) or text == f"{d:02d}":
                candidates.append((e.get("index"), _norm(e.get("description"))))

        if not candidates:
            return False

        month_words = [month_full.lower(), month_abbr.lower()]
        matching = [
            idx for idx, desc in candidates
            if any(w in desc.lower() for w in month_words)
        ]

        if matching:
            idx = matching[-1] if d >= 25 else matching[0]
        else:
            idx = candidates[-1][0] if d >= 25 else candidates[0][0]

        return _click_index(idx)

    def _is_event_screen():
        return (
            _find_by(hint="Title", editable=True) is not None
            or _find_by(text="Title", editable=True) is not None
        )

    def _click_new_event():
        for desc in ("New Event", "New event", "Add event", "Create event", "Add"):
            idx = _find_by(description=desc, clickable=True)
            if idx is not None:
                return _click_index(idx)

        for text in ("New Event", "Add event", "Create event"):
            idx = _find_by(text=text, clickable=True)
            if idx is not None:
                return _click_index(idx)

        idx = _find_by(contains="New Event", clickable=True)
        if idx is not None:
            return _click_index(idx)

        return False

    def _click_event_option():
        for text in ("Event", "Add event", "New event"):
            idx = _find_by(text=text, clickable=True)
            if idx is not None:
                return _click_index(idx)

        for e in device.elements():
            if e.get("clickable") and _norm(e.get("text")).lower() == "event":
                return _click_index(e.get("index"))

        idx = _find_by(contains="Event", clickable=True)
        if idx is not None:
            return _click_index(idx)

        return False

    def _input_field(hint, value):
        idx = _find_by(hint=hint, editable=True)
        if idx is None:
            idx = _find_by(text=hint, editable=True)
        if idx is None:
            idx = _find_by(contains=hint, editable=True)

        if idx is None:
            editables = [e.get("index") for e in device.elements() if e.get("editable")]
            if hint == "Title" and editables:
                idx = editables[0]
            elif hint == "Description" and len(editables) > 1:
                idx = editables[1]

        if idx is None:
            raise RuntimeError(f"Could not find editable field for {hint!r}")

        device.input_text(value, index=idx)
        device.settle(1.0)
        return True

    def _scroll_to_top():
        for _ in range(3):
            device.scroll("up")
            device.settle(0.5)

    def _looks_date(text):
        text = _norm(text)
        if not text or ":" in text:
            return False

        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return True
        if re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", text):
            return True

        for m in MONTHS_FULL + MONTHS_ABBR:
            if re.search(rf"\b{m}\b", text, re.IGNORECASE):
                if re.search(r"\b\d{1,2}\b", text):
                    return True

        return False

    def _looks_time(text):
        text = _norm(text)
        return bool(re.search(r"\b\d{1,2}:\d{2}\b", text))

    def _find_date_field_indices(min_count=2, scroll=True):
        found = []
        attempts = 6 if scroll else 1

        for _ in range(attempts):
            found = []
            for e in device.elements():
                if not e.get("clickable"):
                    continue
                text = _norm(e.get("text")) or _norm(e.get("description"))
                if _looks_date(text):
                    idx = e.get("index")
                    if idx is not None and idx not in found:
                        found.append(idx)

            if len(found) >= min_count:
                break

            if not scroll:
                break

            device.scroll("down")
            device.settle(0.5)

        return found

    def _find_time_field_indices(min_count=2, scroll=True):
        found = []
        attempts = 6 if scroll else 1

        for _ in range(attempts):
            found = []
            for e in device.elements():
                if not e.get("clickable"):
                    continue
                text = _norm(e.get("text")) or _norm(e.get("description"))
                if _looks_time(text):
                    idx = e.get("index")
                    if idx is not None and idx not in found:
                        found.append(idx)

            if len(found) >= min_count:
                break

            if not scroll:
                break

            device.scroll("down")
            device.settle(0.5)

        return found

    def _element_by_index(idx):
        if idx is None:
            return None
        for e in device.elements():
            if e.get("index") == idx:
                return e
        return None

    def _date_text_matches(text, y, m, d):
        text = _norm(text)
        if not text:
            return False

        if re.search(rf"\b{y}-{m:02d}-{d:02d}\b", text):
            return True
        if re.search(rf"\b{m}/{d}/{y}\b", text):
            return True
        if re.search(rf"\b{m}/{d}/{y % 100:02d}\b", text):
            return True
        if re.search(rf"\b{d}/{m}/{y}\b", text):
            return True

        if re.search(rf"\b{d}\b", text):
            month_full = MONTHS_FULL[m - 1]
            month_abbr = MONTHS_ABBR[m - 1]
            low = text.lower()
            if month_full.lower() in low or month_abbr.lower() in low:
                return True

        if re.search(rf"\b{m:02d}-{d:02d}\b", text):
            return True

        return False

    def _click_ok():
        for kwargs in (
            {"text": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"text": "Okay", "clickable": True},
            {"text": "Done", "clickable": True},
            {"description": "OK", "clickable": True},
            {"description": "Ok", "clickable": True},
            {"description": "Done", "clickable": True},
        ):
            if _click_find(**kwargs):
                return True

        for e in device.elements():
            if e.get("clickable"):
                text = _norm(e.get("text")).upper()
                desc = _norm(e.get("description")).upper()
                if text in ("OK", "DONE") or desc in ("OK", "DONE"):
                    return _click_index(e.get("index"))

        return False

    def _click_cancel():
        for kwargs in (
            {"text": "Cancel", "clickable": True},
            {"text": "Close", "clickable": True},
            {"description": "Cancel", "clickable": True},
            {"description": "Close", "clickable": True},
        ):
            if _click_find(**kwargs):
                return True
        return False

    def _dismiss_dialog():
        if _click_cancel():
            return
        if (
            _find_by(text="OK", clickable=True) is not None
            or _find_by(description="OK", clickable=True) is not None
        ):
            device.navigate_back()
            device.settle(1.0)

    def _time_picker_open():
        if _find_by(text="OK", clickable=True) is not None:
            return True
        if _find_by(description="OK", clickable=True) is not None:
            return True

        for e in device.elements():
            desc = _norm(e.get("description")).lower()
            if e.get("clickable") and (desc.endswith(" hours") or desc.endswith(" minutes")):
                return True

        return False

    def _click_time_mode_header(suffix):
        best = None
        for e in device.elements():
            if not e.get("clickable"):
                continue
            desc = _norm(e.get("description")).lower()
            text = _norm(e.get("text"))
            if desc.endswith(suffix) and text:
                idx = e.get("index")
                if idx is not None and (best is None or idx < best):
                    best = idx

        if best is not None:
            _click_index(best)
            return best

        return None

    def _click_clock_value(value, suffix, after_index=None):
        value = int(value)
        descs = {f"{value}{suffix}", f"{value:02d}{suffix}"}
        texts = {str(value), f"{value:02d}"}
        candidates = []

        for e in device.elements():
            if not e.get("clickable"):
                continue

            idx = e.get("index")
            if idx is None:
                continue

            if after_index is not None and idx <= after_index:
                continue

            desc = _norm(e.get("description")).lower()
            text = _norm(e.get("text"))
            cls = _norm(e.get("class_name"))

            match = False
            if desc in descs:
                match = True
            elif desc.endswith(suffix):
                if re.search(rf"\b{value}\b", desc) or re.search(rf"\b{value:02d}\b", desc):
                    match = True

            if not match and text in texts and desc.endswith(suffix):
                match = True

            if not match:
                continue

            score = 0
            if not text or ("TextView" not in cls and "View" in cls):
                score += 100
            if text in texts:
                score += 10
            if desc in descs:
                score += 5

            candidates.append((score, idx))

        if not candidates:
            return False

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return _click_index(candidates[0][1])

    def _click_text_value(value, suffixes, after_index=None):
        value = int(value)
        texts = {str(value), f"{value:02d}"}
        candidates = []

        for e in device.elements():
            if not e.get("clickable"):
                continue

            idx = e.get("index")
            if idx is None:
                continue

            if after_index is not None and idx <= after_index:
                continue

            text = _norm(e.get("text"))
            desc = _norm(e.get("description")).lower()
            cls = _norm(e.get("class_name"))

            if text not in texts:
                continue

            score = 0
            if any(s in desc for s in suffixes):
                score += 100
            if not text or ("TextView" not in cls and "View" in cls):
                score += 10

            candidates.append((score, idx))

        if not candidates:
            return False

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return _click_index(candidates[0][1])

    def _select_time_via_text_input(h, m):
        h = int(h) % 24
        m = int(m) % 60

        toggle_idx = None
        for desc in (
            "Toggle to text input mode",
            "Enter time using text",
            "Switch to text input mode",
            "Text input mode",
        ):
            idx = _find_by(description=desc, clickable=True)
            if idx is not None:
                toggle_idx = idx
                break

        if toggle_idx is None:
            for e in device.elements():
                if not e.get("clickable"):
                    continue
                desc = _norm(e.get("description")).lower()
                text = _norm(e.get("text")).lower()
                if "text input" in desc or "input mode" in desc or "text input" in text:
                    toggle_idx = e.get("index")
                    break

        if toggle_idx is not None:
            _click_index(toggle_idx)

        editables = [e.get("index") for e in device.elements() if e.get("editable")]
        if len(editables) < 2:
            return False

        try:
            device.input_text(f"{h:02d}", index=editables[0])
            device.settle(0.5)
            device.input_text(f"{m:02d}", index=editables[1])
            device.settle(0.5)
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(0.5)
            return True
        except Exception:
            return False

    def _set_time_picker(h, m, require_minute=True):
        if not _time_picker_open():
            return False

        h = int(h) % 24
        m = int(m) % 60

        hour_header = _click_time_mode_header(" hours")
        hour_ok = _click_clock_value(h, " hours", hour_header)
        if not hour_ok:
            hour_ok = _click_text_value(h, ("hours",), hour_header)

        minute_header = _click_time_mode_header(" minutes")
        minute_ok = _click_clock_value(m, " minutes", minute_header)
        if not minute_ok:
            minute_ok = _click_text_value(m, ("minutes",), minute_header)

        if not hour_ok or not minute_ok:
            if _select_time_via_text_input(h, m):
                return _click_ok()
            if not hour_ok or require_minute:
                return False

        return _click_ok()

    def _open_time_field(which):
        if which == 0:
            _scroll_to_top()
            time_idxs = _find_time_field_indices(1, scroll=False)
            if not time_idxs:
                time_idxs = _find_time_field_indices(1, scroll=True)
            if not time_idxs:
                return False
            idx = time_idxs[0]
        else:
            time_idxs = _find_time_field_indices(2, scroll=True)
            if len(time_idxs) >= 2:
                idx = time_idxs[1]
            elif len(time_idxs) == 1:
                idx = time_idxs[0]
            else:
                return False

        return _click_index(idx)

    def _current_date_picker_month():
        for e in device.elements():
            ym = _parse_month_year(_all_text(e))
            if ym is not None:
                return ym
        return None

    def _date_picker_open():
        if (
            _find_by(text="OK", clickable=True) is None
            and _find_by(description="OK", clickable=True) is None
        ):
            return False

        if _current_date_picker_month() is not None:
            return True

        count = 0
        for e in device.elements():
            if e.get("clickable") and re.fullmatch(r"\d{1,2}", _norm(e.get("text"))):
                count += 1
                if count >= 3:
                    return True

        return False

    def _find_month_year_header():
        for e in device.elements():
            if e.get("clickable"):
                ym = _parse_month_year(_all_text(e))
                if ym is not None:
                    return e.get("index")
        return None

    def _click_date_arrow(direction):
        return _click_month_arrow(direction)

    def _navigate_date_picker_month(target_year, target_month):
        target = target_year * 12 + target_month

        for _ in range(36):
            cur = _current_date_picker_month()
            if cur is None:
                return False

            cy, cm = cur
            if cy is None:
                cy = target_year

            cur_idx = cy * 12 + cm
            if cur_idx == target:
                return True

            if cur_idx < target:
                if not _click_date_arrow("next"):
                    device.scroll("left")
            else:
                if not _click_date_arrow("prev"):
                    device.scroll("right")

            device.settle(1.0)

        return False

    def _set_year_in_date_picker(target_year):
        header = _find_month_year_header()
        if header is None:
            return False

        _click_index(header)

        for _ in range(20):
            idx = _find_by(text=str(target_year), clickable=True)
            if idx == header:
                idx = None

            if idx is None:
                idx = _find_by(contains=str(target_year), clickable=True)
                if idx == header:
                    idx = None

            if idx is not None:
                return _click_index(idx)

            device.scroll("down")
            device.settle(0.5)

        return False

    def _click_day_in_date_picker(d, m):
        month_full = MONTHS_FULL[m - 1]
        month_abbr = MONTHS_ABBR[m - 1]

        for desc in (
            f"{month_full} {d}",
            f"{d} {month_full}",
            f"{month_abbr} {d}",
            f"{d} {month_abbr}",
        ):
            idx = _find_by(description=desc, clickable=True)
            if idx is not None:
                return _click_index(idx)

        candidates = []
        for e in device.elements():
            if not e.get("clickable"):
                continue
            text = _norm(e.get("text"))
            if text == str(d) or text == f"{d:02d}":
                candidates.append((e.get("index"), _norm(e.get("description"))))

        if not candidates:
            return False

        month_words = [month_full.lower(), month_abbr.lower()]
        matching = [
            idx for idx, desc in candidates
            if any(w in desc.lower() for w in month_words)
        ]

        if matching:
            idx = matching[-1] if d >= 25 else matching[0]
        else:
            idx = candidates[-1][0] if d >= 25 else candidates[0][0]

        return _click_index(idx)

    def _set_date_picker(y, m, d):
        if not _date_picker_open():
            return False

        cur = _current_date_picker_month()
        if cur:
            cy, _ = cur
            if cy is None:
                cy = y
            if cy != y:
                _set_year_in_date_picker(y)

        if not _navigate_date_picker_month(y, m):
            return False

        if not _click_day_in_date_picker(d, m):
            return False

        return _click_ok()

    def _click_date_field_and_set(which, y, m, d):
        date_idxs = _find_date_field_indices(which + 1, scroll=True)
        if len(date_idxs) <= which:
            return False

        idx = date_idxs[which]
        _click_index(idx)

        if not _date_picker_open():
            _dismiss_dialog()
            return False

        ok = _set_date_picker(y, m, d)
        if not ok:
            _dismiss_dialog()

        return ok

    def _ensure_start_date(y, m, d, day_clicked):
        date_idxs = _find_date_field_indices(2, scroll=False)
        if len(date_idxs) >= 2:
            e = _element_by_index(date_idxs[0])
            if e is not None and _date_text_matches(_all_text(e), y, m, d):
                return True
            return _click_date_field_and_set(0, y, m, d)

        if not day_clicked:
            date_idxs = _find_date_field_indices(2, scroll=True)
            if date_idxs:
                return _click_date_field_and_set(0, y, m, d)

        return True

    def _ensure_end_date(y, m, d):
        date_idxs = _find_date_field_indices(2, scroll=True)
        if len(date_idxs) >= 2:
            e = _element_by_index(date_idxs[1])
            if e is not None and _date_text_matches(_all_text(e), y, m, d):
                return True
            return _click_date_field_and_set(1, y, m, d)

        return False

    def _save():
        for kwargs in (
            {"description": "Save", "clickable": True},
            {"description": "save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"text": "save", "clickable": True},
            {"contains": "Save", "clickable": True},
            {"description": "Done", "clickable": True},
            {"text": "Done", "clickable": True},
        ):
            if _click_find(**kwargs):
                return True

        _scroll_to_top()

        for kwargs in (
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ):
            if _click_find(**kwargs):
                return True

        raise RuntimeError("Could not find the Save button on the event screen")

    device.open_app("Simple Calendar Pro")
    device.settle(2.0)

    _navigate_main_to_month(year, month)
    day_clicked = _click_day_on_main(day, month, year)

    if not _click_new_event():
        raise RuntimeError("Could not open the New Event control")

    if not _is_event_screen():
        _click_event_option()
        device.settle(1.0)

    if not _is_event_screen():
        raise RuntimeError("Could not reach the event editor")

    _input_field("Title", event_title)
    _input_field("Description", event_description)

    _ensure_start_date(year, month, day, day_clicked)

    if not _open_time_field(0):
        raise RuntimeError("Could not find/open the start time field")

    if not _set_time_picker(hour, 0, require_minute=False):
        raise RuntimeError("Could not set the start time")

    if end_dt.date() != start_dt.date():
        _ensure_end_date(end_dt.year, end_dt.month, end_dt.day)

    if not _open_time_field(1):
        raise RuntimeError("Could not find/open the end time field")

    if not _set_time_picker(end_dt.hour, end_dt.minute, require_minute=True):
        raise RuntimeError("Could not set the end time")

    if end_dt.date() != start_dt.date():
        _ensure_end_date(end_dt.year, end_dt.month, end_dt.day)

    _save()
    return True
