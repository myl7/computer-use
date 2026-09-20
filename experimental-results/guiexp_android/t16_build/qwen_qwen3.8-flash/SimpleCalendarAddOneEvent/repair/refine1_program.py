import re
from datetime import datetime, timedelta

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
            digits = "".join(ch for ch in value if ch.isdigit())
            if not digits:
                raise ValueError(f"{name} must be numeric")
            return int(digits)
        return int(value)

    year = parse_int(binding["year"], "year")
    month = parse_int(binding["month"], "month")
    day = parse_int(binding["day"], "day")
    hour = parse_int(binding["hour"], "hour")
    duration_mins = parse_int(binding["duration_mins"], "duration_mins")
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    DEFAULT_YEAR = 2023
    DEFAULT_MONTH = 10

    start_date = datetime(year, month, day)
    end_date = start_date + timedelta(minutes=duration_mins)

    start_y, start_m, start_d = start_date.year, start_date.month, start_date.day
    end_y, end_m, end_d = end_date.year, end_date.month, end_date.day
    start_h, start_min = hour, 0
    end_h, end_min = end_date.hour, end_date.minute

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    month_abbrs = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

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

    def click_index(idx):
        if idx is None:
            raise RuntimeError("Attempted to click a missing element")
        device.click(index=int(idx))
        settle(0.5)

    def ok_index():
        idx = find_first(
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"description": "Ok", "clickable": True},
            {"text": "Okay", "clickable": True},
            {"description": "Okay", "clickable": True},
            {"text": "Confirm", "clickable": True},
            {"description": "Confirm", "clickable": True},
            {"text": "Select", "clickable": True},
            {"description": "Select", "clickable": True},
        )
        if idx is not None:
            return idx

        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().upper()
            desc = str(el.get("description") or "").strip().upper()
            if text in ("OK", "OKAY", "CONFIRM", "SELECT", "CONTINUE") or desc in ("OK", "OKAY", "CONFIRM", "SELECT", "CONTINUE"):
                idx = el.get("index")
                if idx is not None:
                    return int(idx)
        return None

    def click_ok():
        idx = ok_index()
        if idx is None:
            return False
        click_index(idx)
        settle(0.5)
        return True

    def dialog_has_ok():
        return ok_index() is not None

    def is_keyboard_open():
        letters = set("abcdefghijklmnopqrstuvwxyz")
        for el in els():
            desc = str(el.get("description") or "").strip().lower()
            if len(desc) == 1 and desc in letters:
                return True
            if desc in ("voice input", "switch input method"):
                return True
        return False

    def dismiss_keyboard():
        if not is_keyboard_open():
            return
        device.navigate_back()
        settle(0.5)
        if is_keyboard_open():
            device.navigate_back()
            settle(0.5)

    def is_event_screen():
        if find_first(
            {"hint": "Title", "editable": True},
            {"text": "Title", "editable": True},
        ) is not None:
            return True

        save_idx = find_first(
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"hint": "Save", "clickable": True},
        )
        if save_idx is not None:
            for el in els():
                if el.get("editable") and el.get("index") is not None:
                    return True
        return False

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
        )
        if idx is not None:
            click_index(idx)
            settle(1.0)

        if is_event_screen():
            return

        idx = find_first(
            {"text": "Event", "clickable": True},
            {"description": "Event", "clickable": True},
            {"hint": "Event", "clickable": True},
        )
        if idx is not None:
            click_index(idx)
            settle(1.0)

        if not is_event_screen():
            raise RuntimeError("Could not open the Event editor")

    def get_editable_fields():
        fields = []
        for el in els():
            if el.get("editable") and el.get("index") is not None:
                fields.append(el)
        fields.sort(key=lambda e: int(e.get("index", -1)))
        return fields

    def input_text_field(hint, value):
        idx = find_first(
            {"hint": hint, "editable": True},
            {"text": hint, "editable": True},
        )
        if idx is None:
            device.scroll(direction="down")
            settle(0.5)
            idx = find_first(
                {"hint": hint, "editable": True},
                {"text": hint, "editable": True},
            )

        if idx is None:
            fields = get_editable_fields()
            if hint.lower() == "title":
                if fields:
                    idx = int(fields[0].get("index"))
            else:
                filtered = []
                for e in fields:
                    combined = (
                        f"{e.get('hint') or ''} "
                        f"{e.get('description') or ''} "
                        f"{e.get('text') or ''}"
                    ).lower()
                    if "title" in combined or "location" in combined:
                        continue
                    filtered.append(e)
                if filtered:
                    idx = int(filtered[0].get("index"))
                elif len(fields) >= 2:
                    idx = int(fields[-1].get("index"))

        if idx is None:
            raise RuntimeError(f"Could not find editable field for {hint}")

        device.input_text(value, index=idx)
        settle(0.5)

    def date_like(text):
        s = str(text or "").strip()
        if not s or ":" in s:
            return False

        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", s):
            return True
        if re.search(r"\b\d{4}-\d{1,2}-\d{1,2}\b", s):
            return True

        low = s.lower()
        has_month = any(name.lower() in low for name in month_names + month_abbrs)
        has_number = bool(re.search(r"\b\d{1,2}\b", s)) or bool(re.search(r"\b\d{4}\b", s))
        return has_month and has_number

    def get_date_fields():
        fields = []
        for el in els():
            if not el.get("clickable") or el.get("editable") or el.get("index") is None:
                continue

            text = str(el.get("text") or "")
            desc = str(el.get("description") or "")
            hint = str(el.get("hint") or "")
            combined = f"{text} {desc} {hint}".lower()

            if "reminder" in combined or "minutes before" in combined or "minutes after" in combined:
                continue
            if "all-day" in combined:
                continue

            if date_like(text) or date_like(desc) or "date" in combined:
                fields.append(el)

        fields.sort(key=lambda e: int(e.get("index", -1)))
        return fields

    def matches_date_text(el, y, m, d, allow_unknown_year=False):
        if isinstance(el, dict):
            s = f"{el.get('text') or ''} {el.get('description') or ''} {el.get('hint') or ''}"
        else:
            s = str(el or "")

        if not s.strip():
            return False

        if f"{y}-{m:02d}-{d:02d}" in s:
            return True
        if f"{d:02d}-{m:02d}-{y}" in s:
            return True

        low = s.lower()
        month_ok = month_names[m - 1].lower() in low or month_abbrs[m - 1].lower() in low
        day_ok = bool(re.search(rf"(?<!\d){d}(?!\d)", s)) or bool(re.search(rf"(?<!\d){d:02d}(?!\d)", s))
        year_ok = bool(re.search(rf"\b{y}\b", s))

        if month_ok and day_ok:
            if year_ok:
                return True
            if not re.search(r"\b\d{4}\b", s):
                return allow_unknown_year or y == DEFAULT_YEAR

        for sep in ("/", "-"):
            e = re.escape(sep)
            if re.search(rf"\b{m}{e}{d}{e}{y}\b", s):
                return True
            if re.search(rf"\b{m:02d}{e}{d:02d}{e}{y}\b", s):
                return True
            if re.search(rf"\b{d}{e}{m}{e}{y}\b", s):
                return True
            if re.search(rf"\b{d:02d}{e}{m:02d}{e}{y}\b", s):
                return True

        return False

    def parse_current_month_year():
        best_month = None
        best_year = None

        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()

                for i, name in enumerate(month_names):
                    if re.search(rf"\b{re.escape(name.lower())}\b", low):
                        mm = re.search(r"\b(\d{4})\b", s)
                        if mm:
                            return int(mm.group(1)), i + 1

                for i, abbr in enumerate(month_abbrs):
                    if re.search(rf"\b{re.escape(abbr.lower())}\b", low):
                        mm = re.search(r"\b(\d{4})\b", s)
                        if mm:
                            return int(mm.group(1)), i + 1

                mm = re.search(r"\b(\d{4})\b", s)
                if mm:
                    y = int(mm.group(1))
                    for i, name in enumerate(month_names):
                        if re.search(rf"\b{re.escape(name.lower())}\b", low):
                            return y, i + 1
                    for i, abbr in enumerate(month_abbrs):
                        if re.search(rf"\b{re.escape(abbr.lower())}\b", low):
                            return y, i + 1

                mm = re.search(r"\b(\d{4})[-/](\d{1,2})\b", s)
                if mm:
                    y = int(mm.group(1))
                    mo = int(mm.group(2))
                    if 1 <= mo <= 12:
                        return y, mo

                mm = re.search(r"\b(\d{1,2})[-/](\d{4})\b", s)
                if mm:
                    mo = int(mm.group(1))
                    y = int(mm.group(2))
                    if 1 <= mo <= 12:
                        return y, mo

                if best_month is None:
                    for i, name in enumerate(month_names):
                        if re.search(rf"\b{re.escape(name.lower())}\b", low):
                            best_month = i + 1
                            break
                    if best_month is None:
                        for i, abbr in enumerate(month_abbrs):
                            if re.search(rf"\b{re.escape(abbr.lower())}\b", low):
                                best_month = i + 1
                                break

                if best_year is None:
                    mm = re.search(r"\b(\d{4})\b", s)
                    if mm:
                        best_year = int(mm.group(1))

        if best_month is not None and best_year is not None:
            return best_year, best_month
        if best_month is not None:
            return DEFAULT_YEAR, best_month
        if best_year is not None:
            return best_year, DEFAULT_MONTH
        return None

    def step_month(forward):
        if forward:
            idx = find_first(
                {"description": "Next month", "clickable": True},
                {"description": "next month", "clickable": True},
                {"text": "›", "clickable": True},
                {"text": ">", "clickable": True},
                {"text": "»", "clickable": True},
            )
            if idx is not None:
                click_index(idx)
                settle(0.5)
                return True

            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                if "next" in desc:
                    idx = el.get("index")
                    if idx is not None:
                        click_index(int(idx))
                        settle(0.5)
                        return True

            device.scroll(direction="left")
            settle(0.5)
            return True

        idx = find_first(
            {"description": "Previous month", "clickable": True},
            {"description": "previous month", "clickable": True},
            {"text": "‹", "clickable": True},
            {"text": "<", "clickable": True},
            {"text": "«", "clickable": True},
        )
        if idx is not None:
            click_index(idx)
            settle(0.5)
            return True

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            if "previous" in desc:
                idx = el.get("index")
                if idx is not None:
                    click_index(int(idx))
                    settle(0.5)
                    return True

        device.scroll(direction="right")
        settle(0.5)
        return True

    def navigate_to_month(y, m):
        target = y * 12 + m
        last_cur = None
        same_count = 0

        for _ in range(1000):
            cur = parse_current_month_year()
            if cur is None:
                if y == DEFAULT_YEAR and m == DEFAULT_MONTH:
                    return True
                raise RuntimeError("Could not determine the date picker month")

            cur_idx = cur[0] * 12 + cur[1]
            if cur_idx == target:
                return True

            if last_cur == cur_idx:
                same_count += 1
                if same_count >= 3:
                    raise RuntimeError("Could not navigate to the requested month")
            else:
                same_count = 0
            last_cur = cur_idx

            if not step_month(cur_idx < target):
                raise RuntimeError("Could not navigate to the requested month")
            settle(0.5)

        raise RuntimeError("Could not navigate to the requested month")

    def day_candidates(y, m, d):
        texts = {str(d), f"{d:02d}"}
        month_name = month_names[m - 1].lower()
        month_abbr = month_abbrs[m - 1].lower()
        candidates = []

        for el in els():
            if not el.get("clickable") or el.get("index") is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()

            if ":" in text:
                continue

            score = 0

            if text in texts:
                score += 2
            elif re.match(rf"^{re.escape(str(d))}\b", text) and not any(
                name.lower() in text.lower() for name in month_names + month_abbrs
            ) and not re.search(r"\b\d{4}\b", text):
                score += 1

            if desc in texts or hint in texts:
                score += 1

            if desc:
                dl = desc.lower()
                if re.search(rf"(?<!\d){d}(?!\d)", desc) or re.search(rf"(?<!\d){d:02d}(?!\d)", desc):
                    if (month_name in dl or month_abbr in dl) and re.search(r"\b\d{4}\b", desc):
                        score += 4

            if hint:
                hl = hint.lower()
                if re.search(rf"(?<!\d){d}(?!\d)", hint) or re.search(rf"(?<!\d){d:02d}(?!\d)", hint):
                    if (month_name in hl or month_abbr in hl) and re.search(r"\b\d{4}\b", hint):
                        score += 2

            if score > 0:
                candidates.append((score, int(el.get("index"))))

        return candidates

    def click_day(y, m, d):
        candidates = day_candidates(y, m, d)
        if not candidates:
            raise RuntimeError("Could not find the requested day in the date picker")
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        click_index(candidates[0][1])
        settle(0.5)

    def set_date_field(field, y, m, d):
        idx = int(field.get("index"))
        click_index(idx)
        settle(1.0)

        if not dialog_has_ok():
            dismiss_keyboard()
            click_index(idx)
            settle(1.0)

        if not dialog_has_ok():
            raise RuntimeError("Date picker did not open")

        try:
            navigate_to_month(y, m)
            click_day(y, m, d)
            if not click_ok():
                if dialog_has_ok():
                    raise RuntimeError("Could not confirm date")
        except Exception:
            if dialog_has_ok():
                device.navigate_back()
                settle(0.5)
            raise

    def find_date_field(which):
        for _ in range(4):
            fields = get_date_fields()
            if len(fields) > which:
                return fields[which]
            device.scroll(direction="down")
            settle(0.5)

        device.scroll(direction="up")
        settle(0.5)

        for _ in range(4):
            fields = get_date_fields()
            if len(fields) > which:
                return fields[which]
            device.scroll(direction="down")
            settle(0.5)

        fields = get_date_fields()
        if fields and which == 0:
            return fields[0]
        return None

    def ensure_date(which, y, m, d, force=False):
        last_error = None

        for attempt in range(3):
            dismiss_keyboard()
            field = find_date_field(which)
            if field is None:
                last_error = RuntimeError("Could not locate date field")
                continue

            if not force and matches_date_text(field, y, m, d, allow_unknown_year=(attempt > 0)):
                return

            try:
                set_date_field(field, y, m, d)
                force = False
            except Exception as e:
                last_error = e
                continue

        dismiss_keyboard()
        field = find_date_field(which)
        if field is not None and matches_date_text(field, y, m, d, allow_unknown_year=True):
            return

        if last_error:
            raise last_error
        raise RuntimeError("Event date did not match the requested date")

    def parse_time(text):
        s = str(text or "").strip().upper()
        if ":" not in s:
            return None

        ampm = None
        if "AM" in s:
            ampm = "AM"
            s = s.replace("AM", "")
        if "PM" in s:
            ampm = "PM"
            s = s.replace("PM", "")

        s = s.strip()
        parts = s.split(":")
        if len(parts) < 2:
            return None

        try:
            h = int(parts[0].strip())
            m = int(parts[1].strip()[:2])
        except Exception:
            return None

        if ampm == "PM" and h < 12:
            h += 12
        if ampm == "AM" and h == 12:
            h = 0

        return h, m

    def time_matches(text, h, m):
        parsed = parse_time(text)
        return parsed is not None and parsed[0] == h and parsed[1] == m

    def get_time_fields():
        fields = []
        for el in els():
            if not el.get("clickable") or el.get("editable") or el.get("index") is None:
                continue
            if parse_time(el.get("text")) is not None:
                fields.append(el)
        fields.sort(key=lambda e: int(e.get("index", -1)))
        return fields

    def toggle_time_keyboard():
        idx = find_first(
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Toggle keyboard", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
            {"description": "Switch to text input", "clickable": True},
            {"description": "Enter time using keyboard", "clickable": True},
        )
        if idx is not None:
            click_index(idx)
            settle(0.5)
            return True

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "keyboard" in desc or "keyboard" in text:
                idx = el.get("index")
                if idx is not None:
                    click_index(int(idx))
                    settle(0.5)
                    return True

        return False

    def input_time_digits(h, m):
        idx = find_first(
            {"hint": "Time", "editable": True},
            {"description": "Time", "editable": True},
            {"text": "Time", "editable": True},
            {"hint": "Enter time", "editable": True},
            {"description": "Enter time", "editable": True},
        )
        if idx is not None:
            try:
                device.input_text(f"{h:02d}:{m:02d}", index=idx)
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        hour_idx = find_first(
            {"hint": "Hour", "editable": True},
            {"description": "Hour", "editable": True},
            {"text": "Hour", "editable": True},
            {"hint": "Hours", "editable": True},
            {"description": "Hours", "editable": True},
            {"text": "Hours", "editable": True},
        )
        minute_idx = find_first(
            {"hint": "Minute", "editable": True},
            {"description": "Minute", "editable": True},
            {"text": "Minute", "editable": True},
            {"hint": "Minutes", "editable": True},
            {"description": "Minutes", "editable": True},
            {"text": "Minutes", "editable": True},
        )

        if hour_idx is not None and minute_idx is not None:
            try:
                device.input_text(f"{h:02d}", index=hour_idx)
                device.input_text(f"{m:02d}", index=minute_idx)
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        editables = []
        for el in els():
            if not el.get("editable") or el.get("index") is None:
                continue
            combined = (
                f"{el.get('hint') or ''} "
                f"{el.get('description') or ''} "
                f"{el.get('text') or ''}"
            ).lower()
            if "title" in combined or "description" in combined or "location" in combined:
                continue
            editables.append(el)

        if len(editables) >= 2:
            editables.sort(key=lambda e: int(e.get("index", -1)))
            try:
                device.input_text(f"{h:02d}", index=int(editables[0].get("index")))
                device.input_text(f"{m:02d}", index=int(editables[1].get("index")))
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        if len(editables) == 1:
            try:
                device.input_text(f"{h:02d}:{m:02d}", index=int(editables[0].get("index")))
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        return False

    def click_hour(h):
        for el in els():
            if el.get("clickable") and parse_time(el.get("text")) is not None:
                idx = el.get("index")
                if idx is not None:
                    click_index(int(idx))
                    settle(0.5)
                    break

        texts = {str(h), f"{h:02d}"}
        candidates = []

        for el in els():
            if not el.get("clickable") or el.get("index") is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()

            if ":" in text:
                continue
            if desc.endswith("minutes") or desc.endswith("minute"):
                continue

            if text in texts or "hours" in desc or "hour" in desc:
                score = 0
                if text in texts:
                    score += 1
                if "hour" in desc:
                    score += 3
                candidates.append((score, int(el.get("index"))))

        if not candidates:
            raise RuntimeError(f"Could not find hour {h} in the time picker")

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        click_index(candidates[0][1])
        settle(0.5)

    def click_minute_selector():
        candidates = []
        for el in els():
            if not el.get("clickable") or el.get("index") is None:
                continue
            desc = str(el.get("description") or "").strip().lower()
            if desc.endswith("minutes") or desc.endswith("minute"):
                candidates.append(int(el.get("index")))

        if not candidates:
            raise RuntimeError("Could not find the minute selector in the time picker")

        click_index(min(candidates))
        settle(0.5)

    def click_minute(m):
        texts = {str(m), f"{m:02d}"}
        candidates = []

        for el in els():
            if not el.get("clickable") or el.get("index") is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip().lower()

            if ":" in text:
                continue
            if desc.endswith("hours") or desc.endswith("hour"):
                continue

            if text in texts or "minutes" in desc or "minute" in desc:
                score = 0
                if text in texts:
                    score += 1
                if "minute" in desc:
                    score += 3
                candidates.append((score, int(el.get("index"))))

        if not candidates:
            raise RuntimeError(f"Could not find minute {m} in the time picker")

        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        click_index(candidates[0][1])
        settle(0.5)

    def set_time_picker(h, m):
        if input_time_digits(h, m):
            click_ok()
            settle(1.0)
            return

        if toggle_time_keyboard():
            if input_time_digits(h, m):
                click_ok()
                settle(1.0)
                return

        try:
            click_hour(h)
            try:
                click_minute(m)
            except Exception:
                try:
                    click_minute_selector()
                    click_minute(m)
                except Exception:
                    if toggle_time_keyboard() and input_time_digits(h, m):
                        click_ok()
                        settle(1.0)
                        return
                    raise
            click_ok()
            settle(1.0)
            return
        except Exception:
            pass

        if toggle_time_keyboard() and input_time_digits(h, m):
            click_ok()
            settle(1.0)
            return

        raise RuntimeError(f"Could not set time to {h:02d}:{m:02d}")

    def ensure_time(which, h, m):
        last_error = None

        for attempt in range(3):
            dismiss_keyboard()
            fields = get_time_fields()

            if len(fields) <= which:
                device.scroll(direction="down")
                settle(0.5)
                fields = get_time_fields()

            if len(fields) <= which:
                device.scroll(direction="up")
                settle(0.5)
                fields = get_time_fields()

            if len(fields) <= which:
                device.scroll(direction="down")
                settle(0.5)
                fields = get_time_fields()

            if len(fields) <= which:
                last_error = RuntimeError("Could not find time fields")
                continue

            if time_matches(fields[which].get("text"), h, m):
                return

            try:
                idx = int(fields[which].get("index"))
                click_index(idx)
                settle(1.0)

                if not dialog_has_ok():
                    dismiss_keyboard()
                    click_index(idx)
                    settle(1.0)

                if not dialog_has_ok():
                    raise RuntimeError("Time picker did not open")

                set_time_picker(h, m)
            except Exception as e:
                last_error = e
                if dialog_has_ok():
                    device.navigate_back()
                    settle(0.5)
                continue

        dismiss_keyboard()
        fields = get_time_fields()
        if len(fields) > which and time_matches(fields[which].get("text"), h, m):
            return

        if last_error:
            raise last_error
        raise RuntimeError("Failed to set the requested time")

    def click_save():
        idx = find_first(
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"hint": "Save", "clickable": True},
            {"description": "Save event", "clickable": True},
            {"text": "Save event", "clickable": True},
        )
        if idx is None:
            device.scroll(direction="up")
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

        click_index(idx)
        settle(1.0)

    device.open_app("Simple Calendar Pro")
    settle(2.0)

    open_new_event()
    input_text_field("Title", event_title)
    input_text_field("Description", event_description)

    dismiss_keyboard()

    ensure_date(0, start_y, start_m, start_d, force=(start_y != DEFAULT_YEAR))
    ensure_date(1, end_y, end_m, end_d, force=(end_y != DEFAULT_YEAR or end_date != start_date))

    ensure_time(0, start_h, start_min)
    ensure_time(1, end_h, end_min)

    dismiss_keyboard()
    click_save()

    return True
