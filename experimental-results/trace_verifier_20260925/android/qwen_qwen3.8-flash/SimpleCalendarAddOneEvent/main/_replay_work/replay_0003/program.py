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

    EVENT_ROW_BAD = (
        "reminder", "remind", "minutes", "hours", "all day", "all-day",
        "repeat", "event type", "location", "title", "description",
        "settings", "disclaimer", "before", "after", "notification",
        "battery", "signal", "internet", "serial console", "check access",
        "save", "cancel", "back", "previous", "next", "keyboard",
        "voice input", "switch input method", "enter", "submit", "ok",
        "done", "set",
    )

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

    def safe_click(idx, sec=0.5):
        if idx is None:
            return False
        try:
            device.click(index=int(idx))
            settle(sec)
            return True
        except Exception:
            return False

    def combined(el):
        return " ".join(
            str(el.get(k) or "")
            for k in ("text", "hint", "description", "class_name")
        ).lower()

    def parse_time(text):
        s = str(text or "").strip()
        if ":" not in s:
            return None
        up = s.upper()
        pm = "PM" in up
        am = "AM" in up
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
        elif am and h == 12:
            h = 0
        return h % 24, m

    def extract_time(text):
        m = re.search(r"\b(\d{1,2}:\d{2})\b", str(text or ""))
        if not m:
            return None
        return parse_time(m.group(1))

    def is_time_text(text):
        return extract_time(text) is not None

    def time_matches(text, h, m):
        parsed = extract_time(text)
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
            if (
                "voice input" in desc
                or "switch input method" in desc
                or "keyboard" in desc
                or "keyboard" in text
            ):
                return True
        return False

    def hide_keyboard():
        if keyboard_visible():
            device.navigate_back()
            settle(0.5)
            dismiss_disclaimer()

    def is_disclaimer_present():
        for el in els():
            comb = combined(el)
            if (
                "disclaimer" in comb
                or "reminders work properly" in comb
                or "blocking the reminders" in comb
                or "battery and notification settings" in comb
            ):
                return True
        return False

    def dismiss_disclaimer():
        for _ in range(5):
            if not is_disclaimer_present():
                return False

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

            if idx is None:
                for el in els():
                    if not el.get("clickable"):
                        continue
                    text = str(el.get("text") or "").strip().upper()
                    desc = str(el.get("description") or "").strip().upper()
                    if text in ("OK", "OKAY", "DONE", "SET") or desc in ("OK", "OKAY", "DONE", "SET"):
                        idx = el.get("index")
                        break

            if idx is not None:
                safe_click(idx, 0.8)
                continue

            device.navigate_back()
            settle(0.5)
        return True

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
            safe_click(idx, 1.0)
            return

        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().upper()
            desc = str(el.get("description") or "").strip().upper()
            if text in ("OK", "OKAY", "DONE", "SET") or desc in ("OK", "OKAY", "DONE", "SET"):
                idx = el.get("index")
                if idx is not None:
                    safe_click(idx, 1.0)
                    return

        raise RuntimeError("Could not find clickable OK button")

    def is_event_screen():
        return find_first(
            {"hint": "Title", "editable": True},
            {"text": "Title", "editable": True},
            {"hint": "Description", "editable": True},
            {"text": "Description", "editable": True},
        ) is not None

    def find_editable(label):
        idx = find_first(
            {"hint": label, "editable": True},
            {"text": label, "editable": True},
            {"description": label, "editable": True},
        )
        if idx is not None:
            return idx

        for el in els():
            if not el.get("editable"):
                continue
            if label.lower() in combined(el):
                idx = el.get("index")
                if idx is not None:
                    return int(idx)
        return None

    def find_editable_by_keywords(keywords):
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        for el in els():
            if not el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if title_idx is not None and int(idx) == int(title_idx):
                continue
            if desc_idx is not None and int(idx) == int(desc_idx):
                continue
            comb = combined(el)
            if any(k in comb for k in ("title", "description", "location", "event", "reminder")):
                continue
            if any(k in comb for k in keywords):
                return int(idx)
        return None

    def open_new_event():
        dismiss_disclaimer()
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
                comb = combined(el)
                if "new event" in comb or "add event" in comb or str(el.get("text") or "").strip() == "+":
                    idx = el.get("index")
                    break

        if idx is not None:
            safe_click(idx, 1.0)
            dismiss_disclaimer()
            if is_event_screen():
                return

        idx = find_first(
            {"text": "Event", "clickable": True},
            {"description": "Event", "clickable": True},
            {"hint": "Event", "clickable": True},
        )
        if idx is not None:
            safe_click(idx, 1.0)
            dismiss_disclaimer()

        if not is_event_screen():
            device.scroll(direction="down")
            settle(0.5)
            idx = find_first(
                {"description": "New Event", "clickable": True},
                {"text": "New Event", "clickable": True},
                {"text": "Event", "clickable": True},
                {"description": "Event", "clickable": True},
            )
            if idx is not None:
                safe_click(idx, 1.0)
                dismiss_disclaimer()

        if not is_event_screen():
            raise RuntimeError("Could not open the Event editor")

    def input_text_field(label, value):
        for _ in range(3):
            idx = find_editable(label)
            if idx is None:
                device.scroll(direction="down")
                settle(0.5)
                idx = find_editable(label)
            if idx is None:
                device.scroll(direction="up")
                settle(0.5)
                idx = find_editable(label)
            if idx is not None:
                device.input_text(str(value), index=int(idx))
                settle(0.5)
                return
        raise RuntimeError(f"Could not find editable field for {label}")

    def find_date_rows():
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        after = desc_idx if desc_idx is not None else title_idx

        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if after is not None and int(idx) <= int(after):
                continue

            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if "time" in comb:
                continue

            text = str(el.get("text") or "")
            desc = str(el.get("description") or "")
            if is_time_text(text) or is_time_text(desc):
                continue

            if (
                "date" in comb
                or is_date_text(text)
                or is_date_text(desc)
                or re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", comb)
            ):
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
            device.scroll(direction="down")
            settle(0.5)

        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if "date" in comb:
                rows.append(int(idx))

        rows = sorted(set(rows))
        if len(rows) > len(best):
            best = rows
        if len(best) >= min_count:
            return best

        for _ in range(2):
            device.scroll(direction="up")
            settle(0.5)
            rows = find_date_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

    def find_end_date_row():
        idx = find_first(
            {"clickable": True, "contains": "End date"},
            {"clickable": True, "contains": "End Date"},
        )
        if idx is not None:
            return idx

        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if "end date" in combined(el):
                return int(idx)

        rows = find_date_rows()
        if len(rows) >= 2:
            return rows[1]
        return None

    def find_time_rows():
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        after = desc_idx if desc_idx is not None else title_idx

        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if after is not None and int(idx) <= int(after):
                continue

            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if "date" in comb:
                continue

            text = str(el.get("text") or "")
            desc = str(el.get("description") or "")

            if (
                "time" in comb
                or is_time_text(text)
                or is_time_text(desc)
                or re.search(r"\b\d{1,2}:\d{2}\b", comb)
            ):
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
            device.scroll(direction="down")
            settle(0.5)

        rows = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if "time" in comb:
                rows.append(int(idx))

        rows = sorted(set(rows))
        if len(rows) > len(best):
            best = rows
        if len(best) >= min_count:
            return best

        for _ in range(2):
            device.scroll(direction="up")
            settle(0.5)
            rows = find_time_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

    def parse_month_year():
        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()
                if any(b in low for b in ("reminder", "disclaimer", "notification")):
                    continue

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

        best_mo = None
        best_mo_score = -1
        best_yy = None
        best_yy_score = -1

        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()
                if any(b in low for b in ("reminder", "disclaimer", "notification")):
                    continue

                mo = None
                score = 0
                for i, name in enumerate(month_names):
                    if low == name.lower():
                        mo = i + 1
                        score = 100
                        break
                    if re.search(rf"\b{name.lower()}\b", low):
                        mo = i + 1
                        score = 50
                if mo is None:
                    for i, abbr in enumerate(month_abbrs):
                        if low == abbr.lower():
                            mo = i + 1
                            score = 90
                            break
                        if re.search(rf"\b{abbr.lower()}\b", low):
                            mo = i + 1
                            score = 40

                if mo is not None and score > best_mo_score:
                    best_mo = mo
                    best_mo_score = score

                m = re.search(r"\b(\d{4})\b", s)
                if m:
                    yy = int(m.group(1))
                    if 1900 <= yy <= 2100:
                        ys = 100 if re.fullmatch(r"\d{4}", s) else 50
                        if best_yy is None or ys > best_yy_score:
                            best_yy = yy
                            best_yy_score = ys

        return best_yy, best_mo, best_yy is not None

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
            if idx is None:
                for el in els():
                    if not el.get("clickable"):
                        continue
                    comb = combined(el)
                    if "next" in comb or "forward" in comb:
                        idx = el.get("index")
                        break

            if idx is not None:
                safe_click(idx, 0.5)
                return True

            device.scroll(direction="left")
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
        if idx is None:
            for el in els():
                if not el.get("clickable"):
                    continue
                comb = combined(el)
                if "previous" in comb or "back" in comb:
                    idx = el.get("index")
                    break

        if idx is not None:
            safe_click(idx, 0.5)
            return True

        device.scroll(direction="right")
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

    def click_day_cell(y, m, d):
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

            comb = combined(el)
            if any(b in comb for b in ("reminder", "disclaimer", "notification", "battery", "signal")):
                continue
            if ":" in comb:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            score = 0

            if text in day_texts:
                score += 10
            elif desc in day_texts:
                score += 8
            else:
                labels = [
                    f"{m_name} {d}",
                    f"{d} {m_name}",
                    f"{m_abbr} {d}",
                    f"{d} {m_abbr}",
                ]
                if any(label.lower() in comb for label in labels):
                    score += 5
                else:
                    continue

            if m_name.lower() in comb:
                score += 20
            if m_abbr.lower() in comb:
                score += 20
            if str(y) in comb:
                score += 20

            if score > 0:
                candidates.append((score, int(idx)))

        if not candidates:
            return False

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        safe_click(candidates[0][1], 0.5)
        return True

    def find_date_editables():
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        res = []

        for el in els():
            if not el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if title_idx is not None and int(idx) == int(title_idx):
                continue
            if desc_idx is not None and int(idx) == int(desc_idx):
                continue

            comb = combined(el)
            if any(k in comb for k in ("title", "description", "location", "event", "reminder")):
                continue

            text = str(el.get("text") or "").strip()
            if text == event_title or text == event_description:
                continue
            if event_description and len(text) > 10 and event_description in text:
                continue
            if event_title and len(text) > 5 and event_title in text:
                continue

            score = 0
            if any(k in comb for k in ("month", "day", "year", "date", "mm", "dd", "yyyy")):
                score += 100
            if len(text) > 20:
                score -= 50
            if re.search(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", text):
                score += 50
            if re.fullmatch(r"\d{1,4}", text):
                score += 20

            if score > 0:
                res.append((score, int(idx), el))

        res.sort(key=lambda x: (-x[0], x[1]))
        return res

    def toggle_date_keyboard():
        idx = find_first(
            {"description": "Switch to text input mode", "clickable": True},
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Switch input mode", "clickable": True},
            {"description": "Text input", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
        )
        if idx is None:
            for el in els():
                if not el.get("clickable"):
                    continue
                comb = combined(el)
                if "switch" in comb and ("keyboard" in comb or "text" in comb or "input" in comb):
                    idx = el.get("index")
                    break

        if idx is not None:
            safe_click(idx, 0.5)
            return True
        return False

    def try_date_text_input(y, m, d):
        editables = find_date_editables()
        if not editables:
            if not toggle_date_keyboard():
                return False
            editables = find_date_editables()

        if not editables:
            return False

        month_idx = find_editable_by_keywords(("month", "mm"))
        day_idx = find_editable_by_keywords(("day", "dd"))
        year_idx = find_editable_by_keywords(("year", "yyyy"))

        if (
            month_idx is not None
            and day_idx is not None
            and year_idx is not None
            and len({int(month_idx), int(day_idx), int(year_idx)}) == 3
        ):
            try:
                device.input_text(f"{m:02d}", index=int(month_idx))
                device.input_text(f"{d:02d}", index=int(day_idx))
                device.input_text(str(y), index=int(year_idx))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        formats = (
            f"{m:02d}/{d:02d}/{y}",
            f"{d:02d}/{m:02d}/{y}",
            f"{y}-{m:02d}-{d:02d}",
            f"{m:02d}-{d:02d}-{y}",
            f"{d:02d}-{m:02d}-{y}",
            f"{m}/{d}/{y}",
            f"{d}/{m}/{y}",
        )

        for _, idx, _ in editables[:3]:
            for fmt in formats:
                try:
                    device.input_text(fmt, index=int(idx))
                    device.keyboard_enter()
                    settle(0.5)
                    if has_ok_button():
                        return True
                except Exception:
                    pass

        return False

    def set_date_picker(y, m, d):
        if navigate_to_month(y, m):
            if click_day_cell(y, m, d):
                return True

        if click_day_cell(y, m, d):
            return True

        if try_date_text_input(y, m, d):
            return True

        return False

    def set_date_for_row(row_number, y, m, d):
        for attempt in range(4):
            dismiss_disclaimer()
            rows = find_date_rows_with_scroll(row_number + 1)
            if len(rows) <= row_number:
                device.scroll(direction="down")
                settle(0.5)
                rows = find_date_rows_with_scroll(row_number + 1)

            if len(rows) <= row_number:
                raise RuntimeError("Could not locate the requested date row")

            idx = rows[row_number]
            safe_click(idx, 1.0)
            dismiss_disclaimer()

            if not has_ok_button():
                safe_click(idx, 1.0)
                dismiss_disclaimer()

            if not has_ok_button():
                continue

            done = False
            if attempt == 0:
                done = set_date_picker(y, m, d)
            elif attempt == 1:
                done = try_date_text_input(y, m, d)
            else:
                done = set_date_picker(y, m, d)

            if done:
                try:
                    click_ok()
                except Exception:
                    pass
                settle(1.0)
                dismiss_disclaimer()

                rows = find_date_rows_with_scroll(row_number + 1)
                if len(rows) > row_number:
                    el = get_el_by_index(rows[row_number])
                    txt = str((el or {}).get("text") or "") + " " + str((el or {}).get("description") or "")
                    if is_date_text(txt) and not matches_date_text(txt, y, m, d):
                        if attempt < 2:
                            continue
                return

            if has_ok_button():
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

        raise RuntimeError("Could not set the requested date")

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
            comb = combined(el)

            if ":" in text:
                continue
            if "minute" in comb:
                continue

            score = 0
            if text in hour_texts:
                score += 10
            if desc in hour_descs:
                score += 20
            if "hour" in comb:
                score += 5

            if score > 0:
                candidates.append((score, int(idx)))

        if not candidates:
            raise RuntimeError(f"Could not find hour {h} in the time picker")

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        safe_click(candidates[0][1], 0.5)

    def click_minute_selector():
        candidates = []
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if "minute" in combined(el):
                candidates.append(int(idx))

        if not candidates:
            raise RuntimeError("Could not find the minute selector in the time picker")

        candidates.sort()
        safe_click(candidates[0], 0.5)

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
            comb = combined(el)

            if ":" in text:
                continue
            if "hour" in comb:
                continue

            score = 0
            if text in minute_texts:
                score += 10
            if desc in minute_descs:
                score += 20
            if "minute" in comb:
                score += 5

            if score > 0:
                candidates.append((score, int(idx)))

        if not candidates:
            raise RuntimeError(f"Could not find minute {m} in the time picker")

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        safe_click(candidates[0][1], 0.5)

    def set_time_clock(h, m):
        try:
            click_hour(h)
            try:
                click_minute(m)
            except Exception:
                click_minute_selector()
                click_minute(m)
            return True
        except Exception:
            return False

    def toggle_time_keyboard():
        idx = find_first(
            {"description": "Switch to text input mode", "clickable": True},
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
            {"description": "Switch input mode", "clickable": True},
        )
        if idx is None:
            for el in els():
                if not el.get("clickable"):
                    continue
                comb = combined(el)
                if (
                    "keyboard" in comb
                    or "text input" in comb
                    or ("switch" in comb and ("keyboard" in comb or "text" in comb or "input" in comb))
                ):
                    idx = el.get("index")
                    break

        if idx is not None:
            safe_click(idx, 0.5)
            return True
        return False

    def toggle_time_clock():
        idx = find_first(
            {"description": "Switch to clock input mode", "clickable": True},
            {"description": "Switch to clock", "clickable": True},
            {"description": "Clock", "clickable": True},
            {"text": "Clock", "clickable": True},
        )
        if idx is None:
            for el in els():
                if not el.get("clickable"):
                    continue
                comb = combined(el)
                if "clock" in comb or ("switch" in comb and "clock" in comb):
                    idx = el.get("index")
                    break

        if idx is not None:
            safe_click(idx, 0.5)
            return True
        return False

    def find_time_editables():
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        res = []

        for el in els():
            if not el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if title_idx is not None and int(idx) == int(title_idx):
                continue
            if desc_idx is not None and int(idx) == int(desc_idx):
                continue

            comb = combined(el)
            if any(k in comb for k in ("title", "description", "location", "event", "reminder", "date")):
                continue

            text = str(el.get("text") or "").strip()
            if text == event_title or text == event_description:
                continue
            if event_description and len(text) > 10 and event_description in text:
                continue
            if event_title and len(text) > 5 and event_title in text:
                continue

            score = 0
            if any(k in comb for k in ("time", "hour", "minute", "hh", "mm")):
                score += 100
            if len(text) > 20:
                score -= 50
            if re.search(r"\d{1,2}:\d{2}", text):
                score += 50
            if re.fullmatch(r"\d{1,2}", text):
                score += 20

            if score > 0:
                res.append((score, int(idx), el))

        res.sort(key=lambda x: (-x[0], x[1]))
        return res

    def try_time_text_input(h, m):
        editables = find_time_editables()
        if not editables:
            if not toggle_time_keyboard():
                return False
            editables = find_time_editables()

        if not editables:
            return False

        hour_idx = find_editable_by_keywords(("hour", "hh"))
        minute_idx = find_editable_by_keywords(("minute", "mm"))

        if (
            hour_idx is not None
            and minute_idx is not None
            and int(hour_idx) != int(minute_idx)
        ):
            try:
                device.input_text(f"{h:02d}", index=int(hour_idx))
                device.input_text(f"{m:02d}", index=int(minute_idx))
                device.keyboard_enter()
                settle(0.5)
                return has_ok_button()
            except Exception:
                pass

        if len(editables) >= 2:
            try:
                device.input_text(f"{h:02d}", index=int(editables[0][1]))
                device.input_text(f"{m:02d}", index=int(editables[1][1]))
                device.keyboard_enter()
                settle(0.5)
                if has_ok_button():
                    return True
            except Exception:
                pass

        for _, idx, _ in editables[:3]:
            for txt in (f"{h:02d}:{m:02d}", f"{h:02d}{m:02d}", f"{h}:{m:02d}", f"{h}{m:02d}"):
                try:
                    device.input_text(txt, index=int(idx))
                    device.keyboard_enter()
                    settle(0.5)
                    if has_ok_button():
                        return True
                except Exception:
                    pass

        return False

    def set_time_picker(h, m):
        if try_time_text_input(h, m):
            return True
        toggle_time_clock()
        if set_time_clock(h, m):
            return True
        return False

    def set_time_for_row(which, h, m):
        for attempt in range(4):
            dismiss_disclaimer()
            rows = find_time_rows_with_scroll(2)
            if len(rows) <= which:
                device.scroll(direction="up")
                settle(0.5)
                rows = find_time_rows_with_scroll(2)

            if len(rows) <= which:
                raise RuntimeError("Could not find start/end time fields")

            idx = rows[which]
            safe_click(idx, 1.0)
            dismiss_disclaimer()

            if not has_ok_button():
                safe_click(idx, 1.0)
                dismiss_disclaimer()

            if not has_ok_button():
                continue

            done = False
            if attempt == 0:
                done = set_time_picker(h, m)
            elif attempt == 1:
                done = try_time_text_input(h, m) or set_time_clock(h, m)
            else:
                done = set_time_clock(h, m) or try_time_text_input(h, m)

            if done:
                try:
                    click_ok()
                except Exception:
                    pass
                settle(1.0)
                dismiss_disclaimer()

                rows = find_time_rows_with_scroll(2)
                if len(rows) > which:
                    el = get_el_by_index(rows[which])
                    txt = str((el or {}).get("text") or "") + " " + str((el or {}).get("description") or "")
                    if is_time_text(txt) and not time_matches(txt, h, m):
                        if attempt < 2:
                            continue
                return

            if has_ok_button():
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

        raise RuntimeError("Failed to set the requested time")

    def find_save():
        idx = find_first(
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"hint": "Save", "clickable": True},
            {"description": "Save event", "clickable": True},
            {"text": "Save event", "clickable": True},
            {"description": "Done", "clickable": True},
            {"text": "Done", "clickable": True},
            {"description": "Check", "clickable": True},
            {"text": "✓", "clickable": True},
            {"description": "Submit", "clickable": True},
        )
        if idx is not None:
            return idx

        for el in els():
            if not el.get("clickable"):
                continue
            comb = combined(el)
            if "save" in comb or "submit" in comb or comb.strip() in ("done", "ok"):
                idx = el.get("index")
                if idx is not None:
                    return int(idx)
        return None

    def click_save():
        for _ in range(5):
            dismiss_disclaimer()
            idx = find_save()
            if idx is None:
                device.scroll(direction="up")
                settle(0.5)
                idx = find_save()
            if idx is None:
                device.scroll(direction="down")
                settle(0.5)
                idx = find_save()

            if idx is None:
                raise RuntimeError("Could not find the Save button")

            safe_click(idx, 1.0)
            dismiss_disclaimer()

            if not is_event_screen():
                return

            hide_keyboard()

        raise RuntimeError("Could not save the event")

    device.open_app("Simple Calendar Pro")
    settle(2.0)
    dismiss_disclaimer()

    open_new_event()

    input_text_field("Title", event_title)
    input_text_field("Description", event_description)
    hide_keyboard()

    if not is_event_screen():
        open_new_event()
        input_text_field("Title", event_title)
        input_text_field("Description", event_description)
        hide_keyboard()

    set_date_for_row(0, year, month, day)

    end_date_set = False
    if end_date != start_date:
        rows = find_date_rows_with_scroll(2)
        if len(rows) >= 2:
            set_date_for_row(1, end_date.year, end_date.month, end_date.day)
            end_date_set = True

    set_time_for_row(0, hour, 0)
    set_time_for_row(1, end_hour, end_minute)

    if end_date != start_date and not end_date_set:
        rows = find_date_rows_with_scroll(2)
        if len(rows) >= 2:
            set_date_for_row(1, end_date.year, end_date.month, end_date.day)
            set_time_for_row(1, end_hour, end_minute)

    click_save()
    return True
