import re
from datetime import date, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "description": "Event month, 1-12"},
    "day": {"type": "integer", "description": "Event day of month"},
    "hour": {"type": "integer", "description": "Event start hour, 0-23"},
    "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    def parse_int(value, name):
        if isinstance(value, str):
            m = re.search(r"-?\d+", value)
            if not m:
                raise ValueError(f"{name} must be numeric")
            return int(m.group(0))
        return int(value)

    year = parse_int(binding["year"], "year")
    month = parse_int(binding["month"], "month")
    day = parse_int(binding["day"], "day")
    hour = parse_int(binding["hour"], "hour")
    duration_mins = parse_int(binding["duration_mins"], "duration_mins")
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    start_date = date(year, month, day)
    total_minutes = hour * 60 + duration_mins
    end_date = start_date + timedelta(minutes=total_minutes)
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    month_abbrs = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    weekday_abbrs = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def settle(sec=0.5):
        device.settle(sec)

    def els():
        return device.elements()

    def find_first(*criteria):
        for crit in criteria:
            kwargs = {k: v for k, v in crit.items() if v is not None}
            if not kwargs:
                continue
            try:
                idx = device.find(**kwargs)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def get_el_by_index(idx):
        if idx is None:
            return None
        try:
            target = int(idx)
        except Exception:
            return None
        for el in els():
            try:
                if int(el.get("index", -1)) == target:
                    return el
            except Exception:
                pass
        return None

    def parse_time(text):
        s = str(text or "").strip()
        if ":" not in s:
            return None
        up = s.upper()
        pm = "PM" in up
        s = re.sub(r"\s*[AP]M", "", s).strip()
        parts = s.split(":")
        if len(parts) < 2:
            return None
        try:
            h = int(parts[0].strip())
            m = int(parts[1].strip()[:2])
        except Exception:
            return None
        if pm and h != 12:
            h += 12
        if not pm and h == 12:
            h = 0
        return h % 24, m

    def is_time_text(text):
        return parse_time(text) is not None and ":" in str(text or "")

    def time_matches(text, h, m):
        parsed = parse_time(text)
        return parsed is not None and parsed[0] == h and parsed[1] == m

    def is_date_text(text):
        s = str(text or "").strip()
        if not s or ":" in s:
            return False
        low = s.lower()

        for name in month_names:
            if name.lower() in low:
                return True
        for abbr in month_abbrs:
            if re.search(rf"\b{abbr.lower()}\b", low):
                return True
        for wd in weekday_abbrs:
            if re.search(rf"\b{wd.lower()}\b", low) and re.search(r"\d", s):
                return True

        if re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", s):
            return True
        if re.search(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b", s):
            return True
        return False

    def matches_date_text(text, y, m, d):
        s = str(text or "")
        if not s:
            return False
        low = s.lower()
        m_name = month_names[m - 1]
        m_abbr = month_abbrs[m - 1]

        labels = [
            f"{m_name} {d}",
            f"{d} {m_name}",
            f"{m_abbr} {d}",
            f"{d} {m_abbr}",
            f"{m_name} {d}, {y}",
            f"{y}-{m:02d}-{d:02d}",
            f"{d:02d}-{m:02d}-{y}",
            f"{m:02d}/{d:02d}/{y}",
            f"{d:02d}/{m:02d}/{y}",
        ]
        for label in labels:
            if label.lower() in low:
                return True

        has_month = any(name.lower() in low for name in month_names)
        if not has_month:
            has_month = any(re.search(rf"\b{abbr.lower()}\b", low) for abbr in month_abbrs)

        has_day = bool(re.search(rf"(?<!\d){d}(?!\d)", s)) or bool(
            re.search(rf"(?<!\d){d:02d}(?!\d)", s)
        )
        has_year = bool(re.search(rf"\b{y}\b", s))

        if has_month and has_day:
            if has_year or not re.search(r"\b\d{4}\b", s):
                return True

        return False

    def keyboard_visible():
        letters = set("abcdefghijklmnopqrstuvwxyz")
        for el in els():
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if desc in letters or text in letters:
                return True
            if "voice input" in desc or "switch input method" in desc or "keyboard" in desc or "keyboard" in text:
                return True
        return False

    def hide_keyboard():
        if keyboard_visible():
            device.navigate_back()
            settle(0.5)

    def has_ok_button():
        return find_first(
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"description": "Ok", "clickable": True},
            {"text": "Done", "clickable": True},
            {"description": "Done", "clickable": True},
            {"text": "Set", "clickable": True},
            {"description": "Set", "clickable": True},
        ) is not None

    def click_ok():
        idx = find_first(
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"description": "Ok", "clickable": True},
            {"text": "Done", "clickable": True},
            {"description": "Done", "clickable": True},
            {"text": "Set", "clickable": True},
            {"description": "Set", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            settle(1.0)
            return

        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().upper()
            desc = str(el.get("description") or "").strip().upper()
            if text in ("OK", "OKAY", "DONE", "SET") or desc in ("OK", "OKAY", "DONE", "SET"):
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(1.0)
                    return

        raise RuntimeError("Could not find clickable OK button")

    def is_event_screen():
        return find_first(
            {"hint": "Title", "editable": True},
            {"text": "Title", "editable": True},
        ) is not None

    def open_new_event():
        if is_event_screen():
            return

        idx = find_first(
            {"description": "New Event", "clickable": True},
            {"description": "New event", "clickable": True},
            {"text": "New Event", "clickable": True},
            {"hint": "New Event", "clickable": True},
            {"description": "Add event", "clickable": True},
            {"text": "Add", "clickable": True},
            {"text": "+", "clickable": True},
        )

        if idx is None:
            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").lower()
                if "new event" in desc or "add" in desc or text == "+":
                    idx = el.get("index")
                    if idx is not None:
                        break

        if idx is None:
            raise RuntimeError("Could not find the New Event button")

        device.click(index=idx)
        settle(1.0)

        if is_event_screen():
            return

        idx = find_first(
            {"text": "Event", "clickable": True},
            {"description": "Event", "clickable": True},
            {"hint": "Event", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            settle(1.0)

        if not is_event_screen():
            raise RuntimeError("Could not open the Event editor")

    def input_text_field(hint, value):
        idx = find_first(
            {"hint": hint, "editable": True},
            {"text": hint, "editable": True},
        )
        if idx is None:
            device.scroll("down")
            settle(0.5)
            idx = find_first(
                {"hint": hint, "editable": True},
                {"text": hint, "editable": True},
            )
        if idx is None:
            raise RuntimeError(f"Could not find editable field for {hint}")

        device.input_text(str(value), index=idx)
        settle(0.5)

    def find_date_rows():
        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            text = str(el.get("text") or "")
            if is_time_text(text):
                continue
            if is_date_text(text):
                idx = el.get("index")
                if idx is not None:
                    rows.append(int(idx))
        return sorted(set(rows))

    def find_date_rows_fallback():
        rows = []
        desc_idx = find_first(
            {"hint": "Description", "editable": True},
            {"text": "Description", "editable": True},
        )
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            text = str(el.get("text") or "")
            if is_time_text(text):
                continue
            if not text.strip():
                continue
            low = text.lower()
            if "minutes" in low or "reminder" in low or "all-day" in low:
                continue
            if desc_idx is not None and int(idx) <= int(desc_idx):
                continue
            if re.search(r"\d", text):
                rows.append(int(idx))
        return sorted(set(rows))

    def find_date_rows_with_scroll(min_count=1):
        best = []
        for _ in range(3):
            rows = find_date_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best
            device.scroll("down")
            settle(0.5)

        rows = find_date_rows_fallback()
        if len(rows) > len(best):
            best = rows
        if len(best) >= min_count:
            return best

        for _ in range(2):
            device.scroll("up")
            settle(0.5)
            rows = find_date_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

    def parse_month_year():
        # Pass 1: month and year in the same string.
        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()

                m = re.search(r"\b(\d{1,2})[/-](\d{4})\b", s)
                if m:
                    mo = int(m.group(1))
                    yy = int(m.group(2))
                    if 1 <= mo <= 12:
                        return yy, mo, True

                m = re.search(r"\b(\d{4})[/-](\d{1,2})\b", s)
                if m:
                    yy = int(m.group(1))
                    mo = int(m.group(2))
                    if 1 <= mo <= 12:
                        return yy, mo, True

                mo = None
                for i, name in enumerate(month_names):
                    if name.lower() in low:
                        mo = i + 1
                        break
                if mo is None:
                    for i, abbr in enumerate(month_abbrs):
                        if re.search(rf"\b{abbr.lower()}\b", low):
                            mo = i + 1
                            break

                if mo is not None:
                    m2 = re.search(r"\b(\d{4})\b", s)
                    if m2:
                        return int(m2.group(1)), mo, True

        # Pass 2: month and year may be separate header elements.
        mo = None
        yy = None
        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()

                if mo is None:
                    for i, name in enumerate(month_names):
                        if name.lower() in low:
                            mo = i + 1
                            break
                if mo is None:
                    for i, abbr in enumerate(month_abbrs):
                        if re.search(rf"\b{abbr.lower()}\b", low):
                            mo = i + 1
                            break

                if yy is None:
                    m = re.search(r"\b(\d{4})\b", s)
                    if m:
                        candidate = int(m.group(1))
                        if 1900 <= candidate <= 2100:
                            yy = candidate

        return yy, mo, yy is not None

    def step_month(forward):
        if forward:
            idx = find_first(
                {"description": "Next month", "clickable": True},
                {"description": "next month", "clickable": True},
                {"description": "Forward", "clickable": True},
                {"text": "›", "clickable": True},
                {"text": ">", "clickable": True},
                {"text": "Next", "clickable": True},
            )
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True

            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                if "next" in desc:
                    idx = el.get("index")
                    if idx is not None:
                        device.click(index=int(idx))
                        settle(0.5)
                        return True

            device.scroll("left")
            settle(0.5)
            return True

        idx = find_first(
            {"description": "Previous month", "clickable": True},
            {"description": "previous month", "clickable": True},
            {"description": "Back", "clickable": True},
            {"text": "‹", "clickable": True},
            {"text": "<", "clickable": True},
            {"text": "Previous", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            settle(0.5)
            return True

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            if "previous" in desc or "back" in desc:
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(0.5)
                    return True

        device.scroll("right")
        settle(0.5)
        return True

    def navigate_to_month(y, m):
        target = y * 12 + m
        state_y, state_m = 2023, 10
        last = None
        same = 0

        for _ in range(240):
            yy, mo, has_year = parse_month_year()
            if mo is not None:
                state_m = mo
                if has_year and yy is not None:
                    state_y = yy

            cur = state_y * 12 + state_m
            if cur == target:
                return True

            if last == cur:
                same += 1
                if same >= 3:
                    return False
            else:
                same = 0
            last = cur

            forward = cur < target
            if not step_month(forward):
                return False

            if forward:
                state_m += 1
                if state_m > 12:
                    state_m = 1
                    state_y += 1
            else:
                state_m -= 1
                if state_m < 1:
                    state_m = 12
                    state_y -= 1

            settle(0.2)

        return False

    def click_day_cell_picker(y, m, d):
        day_texts = {str(d), f"{d:02d}"}
        m_name = month_names[m - 1]
        m_abbr = month_abbrs[m - 1]
        candidates = []

        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()

            if text in day_texts:
                candidates.append(el)
            elif desc in day_texts or hint in day_texts:
                candidates.append(el)
            else:
                labels = [
                    f"{m_name} {d}",
                    f"{d} {m_name}",
                    f"{m_abbr} {d}",
                    f"{d} {m_abbr}",
                ]
                combined = f"{desc} {hint}".lower()
                if any(label.lower() in combined for label in labels):
                    candidates.append(el)

        if not candidates:
            idx = find_first(
                {"clickable": True, "contains": f"{m_name} {d}"},
                {"clickable": True, "contains": f"{d} {m_name}"},
                {"clickable": True, "contains": f"{m_abbr} {d}"},
                {"clickable": True, "contains": f"{d} {m_abbr}"},
            )
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True

        valid = [e for e in candidates if e.get("index") is not None]
        if not valid:
            return False

        def score(el):
            s = 0
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()
            if text in day_texts:
                s += 10
            if m_name.lower() in desc or m_abbr.lower() in desc:
                s += 20
            if str(y) in desc:
                s += 20
            if el.get("editable"):
                s -= 100
            return s

        valid.sort(key=lambda e: (score(e), int(e.get("index", -1))), reverse=True)
        device.click(index=int(valid[0].get("index")))
        settle(0.5)
        return True

    def find_date_keyboard_toggle():
        idx = find_first(
            {"description": "Switch to text input mode", "clickable": True},
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Switch input mode", "clickable": True},
            {"description": "Text input", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
        )
        if idx is not None:
            return idx

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            if "switch" in desc and ("keyboard" in desc or "text" in desc or "input mode" in desc):
                idx = el.get("index")
                if idx is not None:
                    return idx
        return None

    def input_date_keyboard(y, m, d):
        if not has_ok_button():
            return False

        month_idx = find_first(
            {"hint": "Month", "editable": True},
            {"description": "Month", "editable": True},
            {"text": "Month", "editable": True},
        )
        day_idx = find_first(
            {"hint": "Day", "editable": True},
            {"description": "Day", "editable": True},
            {"text": "Day", "editable": True},
        )
        year_idx = find_first(
            {"hint": "Year", "editable": True},
            {"description": "Year", "editable": True},
            {"text": "Year", "editable": True},
        )

        if month_idx is not None and day_idx is not None and year_idx is not None:
            try:
                device.input_text(f"{m:02d}", index=int(month_idx))
                device.input_text(f"{d:02d}", index=int(day_idx))
                device.input_text(str(y), index=int(year_idx))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        editables = [e for e in els() if e.get("editable") and e.get("index") is not None]

        def score(e):
            combined = f"{e.get('hint') or ''} {e.get('description') or ''} {e.get('text') or ''}".lower()
            if any(k in combined for k in ("title", "description", "location")):
                return -100
            if any(k in combined for k in ("date", "month", "day", "year")):
                return 100
            text = str(e.get("text") or "").strip()
            if re.fullmatch(r"\d{1,4}", text):
                return 50
            return 0

        candidates = [e for e in editables if score(e) > 0]
        if not candidates:
            candidates = [e for e in editables if score(e) == 0]

        candidates.sort(key=lambda e: score(e), reverse=True)

        for e in candidates:
            idx = int(e.get("index"))
            for fmt in (
                f"{m:02d}/{d:02d}/{y}",
                f"{y}-{m:02d}-{d:02d}",
                f"{d:02d}/{m:02d}/{y}",
                f"{m}/{d}/{y}",
            ):
                try:
                    device.input_text(fmt, index=idx)
                    device.keyboard_enter()
                    settle(0.5)
                    if has_ok_button():
                        return True
                except Exception:
                    pass
            break

        return False

    def try_date_keyboard_input(y, m, d):
        toggle = find_date_keyboard_toggle()
        if toggle is None:
            return False

        device.click(index=toggle)
        settle(0.5)

        ok = input_date_keyboard(y, m, d)
        if not ok:
            toggle2 = find_date_keyboard_toggle()
            if toggle2 is not None:
                device.click(index=toggle2)
                settle(0.5)
        return ok

    def set_date_for_row(row_number, y, m, d):
        for attempt in range(3):
            hide_keyboard()
            rows = find_date_rows_with_scroll(row_number + 1)
            if len(rows) <= row_number:
                device.scroll("down")
                settle(0.5)
                rows = find_date_rows_with_scroll(row_number + 1)

            if len(rows) <= row_number:
                raise RuntimeError("Could not locate the requested date row")

            device.click(index=rows[row_number])
            settle(1.0)

            if not has_ok_button():
                hide_keyboard()
                device.click(index=rows[row_number])
                settle(1.0)

            if not has_ok_button():
                continue

            done = False
            if attempt == 1:
                done = try_date_keyboard_input(y, m, d)
            else:
                navigate_to_month(y, m)
                done = click_day_cell_picker(y, m, d)
                if not done:
                    done = try_date_keyboard_input(y, m, d)

            if done:
                try:
                    click_ok()
                except Exception:
                    pass
                settle(1.0)
                hide_keyboard()

                rows = find_date_rows_with_scroll(row_number + 1)
                if len(rows) > row_number:
                    el = get_el_by_index(rows[row_number])
                    if matches_date_text(str((el or {}).get("text") or ""), y, m, d):
                        return
                else:
                    return

            if has_ok_button():
                device.navigate_back()
                settle(0.5)

        raise RuntimeError("Could not set the requested date")

    def find_time_rows():
        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            if is_time_text(el.get("text")):
                idx = el.get("index")
                if idx is not None:
                    rows.append(int(idx))
        return sorted(set(rows))

    def find_time_rows_with_scroll(min_count=2):
        best = []
        for _ in range(4):
            rows = find_time_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best
            device.scroll("down")
            settle(0.5)

        for _ in range(2):
            device.scroll("up")
            settle(0.5)
            rows = find_time_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

    def click_hour(h):
        hour_texts = {str(h), f"{h:02d}"}
        if h == 0:
            hour_texts.add("24")
        hour_descs = {f"{h} hours".lower(), f"{h:02d} hours".lower()}
        if h == 0:
            hour_descs.add("24 hours")

        candidates = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()

            if ":" in text:
                continue
            if desc.endswith("minutes") or desc.endswith("minute"):
                continue

            if text in hour_texts or desc in hour_descs:
                candidates.append(el)

        if not candidates:
            for desc in hour_descs:
                try:
                    idx = device.find(description=desc, clickable=True)
                except Exception:
                    idx = None
                if idx is not None:
                    candidates.append({"index": idx})

        if not candidates:
            raise RuntimeError(f"Could not find hour {h} in the time picker")

        valid = [e for e in candidates if e.get("index") is not None]
        if not valid:
            raise RuntimeError(f"Could not click hour {h} in the time picker")

        def score(el):
            s = 0
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()
            if text in hour_texts:
                s += 10
            if desc in hour_descs:
                s += 20
            if el.get("editable"):
                s -= 100
            return s

        valid.sort(key=lambda e: (score(e), int(e.get("index", -1))), reverse=True)
        device.click(index=int(valid[0].get("index")))
        settle(0.5)

    def click_minute_selector():
        candidates = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            desc = str(el.get("description") or "").strip().lower()
            if desc.endswith("minutes") or desc.endswith("minute"):
                candidates.append(el)

        if not candidates:
            raise RuntimeError("Could not find the minute selector in the time picker")

        valid = [e for e in candidates if e.get("index") is not None]
        valid.sort(key=lambda e: int(e.get("index", -1)))
        device.click(index=int(valid[0].get("index")))
        settle(0.5)

    def click_minute(m):
        minute_texts = {str(m), f"{m:02d}"}
        minute_descs = {f"{m} minutes".lower(), f"{m:02d} minutes".lower()}

        candidates = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()

            if ":" in text:
                continue
            if desc.endswith("hours") or desc.endswith("hour"):
                continue

            if text in minute_texts or desc in minute_descs:
                candidates.append(el)

        if not candidates:
            for desc in minute_descs:
                try:
                    idx = device.find(description=desc, clickable=True)
                except Exception:
                    idx = None
                if idx is not None:
                    candidates.append({"index": idx})

        if not candidates:
            raise RuntimeError(f"Could not find minute {m} in the time picker")

        valid = [e for e in candidates if e.get("index") is not None]
        if not valid:
            raise RuntimeError(f"Could not click minute {m} in the time picker")

        def score(el):
            s = 0
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()
            if text in minute_texts:
                s += 10
            if desc in minute_descs:
                s += 20
            if "TextView" in str(el.get("class_name") or ""):
                s += 5
            if el.get("editable"):
                s -= 100
            return s

        valid.sort(key=lambda e: (score(e), int(e.get("index", -1))), reverse=True)
        device.click(index=int(valid[0].get("index")))
        settle(0.5)

    def set_time_clock(h, m):
        try:
            click_hour(h)
            try:
                click_minute_selector()
            except Exception:
                pass
            click_minute(m)
            click_ok()
            return True
        except Exception:
            return False

    def toggle_time_keyboard():
        idx = find_first(
            {"description": "Toggle keyboard", "clickable": True},
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
            {"description": "Switch input mode", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            settle(0.5)
            return True

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "keyboard" in desc or "keyboard" in text or "toggle" in desc or "text input" in desc:
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(0.5)
                    return True

        return False

    def input_time_digits(h, m):
        if not has_ok_button():
            return False

        idx = find_first(
            {"hint": "Time", "editable": True},
            {"description": "Time", "editable": True},
            {"text": "Time", "editable": True},
        )
        if idx is not None:
            for txt in (f"{h:02d}{m:02d}", f"{h:02d}:{m:02d}", f"{h}:{m:02d}"):
                try:
                    device.input_text(txt, index=int(idx))
                    device.keyboard_enter()
                    settle(0.5)
                    if has_ok_button():
                        return True
                except Exception:
                    pass

        hour_idx = find_first(
            {"hint": "Hour", "editable": True},
            {"description": "Hour", "editable": True},
            {"text": "Hour", "editable": True},
        )
        minute_idx = find_first(
            {"hint": "Minute", "editable": True},
            {"description": "Minute", "editable": True},
            {"text": "Minute", "editable": True},
        )
        if hour_idx is not None and minute_idx is not None:
            try:
                device.input_text(f"{h:02d}", index=int(hour_idx))
                device.input_text(f"{m:02d}", index=int(minute_idx))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        editables = [e for e in els() if e.get("editable") and e.get("index") is not None]

        def score(e):
            combined = f"{e.get('hint') or ''} {e.get('description') or ''} {e.get('text') or ''}".lower()
            if any(k in combined for k in ("title", "description", "location")):
                return -100
            if any(k in combined for k in ("hour", "minute", "time")):
                return 100
            text = str(e.get("text") or "").strip()
            if re.fullmatch(r"\d{1,2}", text):
                return 50
            return 0

        filtered = [e for e in editables if score(e) >= 0]
        filtered.sort(key=lambda e: score(e), reverse=True)

        if len(filtered) >= 2:
            try:
                device.input_text(f"{h:02d}", index=int(filtered[0].get("index")))
                device.input_text(f"{m:02d}", index=int(filtered[1].get("index")))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        if len(filtered) >= 1:
            try:
                device.input_text(f"{h:02d}{m:02d}", index=int(filtered[0].get("index")))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        return False

    def set_time_keyboard(h, m):
        if not has_ok_button():
            return False
        toggle_time_keyboard()
        if input_time_digits(h, m):
            try:
                click_ok()
            except Exception:
                pass
            return True
        return False

    def set_time_for_row(which, h, m):
        for attempt in range(3):
            hide_keyboard()
            rows = find_time_rows_with_scroll(2)
            if len(rows) <= which:
                device.scroll("up")
                settle(0.5)
                rows = find_time_rows_with_scroll(2)

            if len(rows) <= which:
                raise RuntimeError("Could not find start/end time fields")

            device.click(index=rows[which])
            settle(1.0)

            if not has_ok_button():
                hide_keyboard()
                device.click(index=rows[which])
                settle(1.0)

            if not has_ok_button():
                continue

            done = False
            if attempt == 1:
                done = set_time_keyboard(h, m)
            else:
                done = set_time_clock(h, m)
                if not done:
                    done = set_time_keyboard(h, m)

            if done:
                hide_keyboard()
                rows = find_time_rows_with_scroll(2)
                if len(rows) > which:
                    el = get_el_by_index(rows[which])
                    if time_matches(str((el or {}).get("text") or ""), h, m):
                        return
                return

            if has_ok_button():
                device.navigate_back()
                settle(0.5)

        raise RuntimeError("Failed to set the requested time")

    def click_save():
        hide_keyboard()
        idx = find_first(
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"hint": "Save", "clickable": True},
            {"description": "Save event", "clickable": True},
            {"text": "Save event", "clickable": True},
        )
        if idx is None:
            device.scroll("up")
            settle(0.5)
            idx = find_first(
                {"description": "Save", "clickable": True},
                {"text": "Save", "clickable": True},
                {"hint": "Save", "clickable": True},
                {"description": "Save event", "clickable": True},
                {"text": "Save event", "clickable": True},
            )
        if idx is None:
            raise RuntimeError("Could not find the Save button")

        device.click(index=idx)
        settle(1.0)

    device.open_app("Simple Calendar Pro")
    settle(2.0)

    open_new_event()

    input_text_field("Title", event_title)
    input_text_field("Description", event_description)
    hide_keyboard()

    set_date_for_row(0, year, month, day)

    rows = find_date_rows_with_scroll(2)
    if len(rows) >= 2:
        set_date_for_row(1, end_date.year, end_date.month, end_date.day)
    elif end_date != start_date:
        raise RuntimeError("Could not locate the end date row")

    set_time_for_row(0, hour, 0)
    set_time_for_row(1, end_hour, end_minute)

    click_save()
    return True
