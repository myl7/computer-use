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
        if parsed is not None:
            return parsed[0] == h and parsed[1] == m
        s = str(text or "").strip()
        if m == 0 and re.fullmatch(rf"{h}|{h:02d}", s):
            return True
        if h == 0 and m == 0 and re.fullmatch(r"0|00", s):
            return True
        return False

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

    def looks_like_date_value(text):
        s = str(text or "").strip()
        if not s or ":" in s:
            return False
        if is_date_text(s):
            return True
        if re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", s):
            return True
        if re.fullmatch(r"\d{1,2}", s):
            try:
                v = int(s)
                return 1 <= v <= 31
            except Exception:
                return False
        return False

    def matches_date_text(text, y, m, d):
        s = str(text or "")
        if not s:
            return False
        low = s.lower()
        m_name = month_names[m - 1]
        m_abbr = month_abbrs[m - 1]

        clean = s.strip()
        if re.fullmatch(rf"{d}|{d:02d}", clean):
            return True

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

    def is_keyboard_key(el):
        comb = combined(el)
        if "keyboard" in comb or "voice input" in comb or "switch input method" in comb:
            return True
        if "candidate" in comb or "ime" in comb:
            return True
        for val in (el.get("text"), el.get("description")):
            s = str(val or "").strip()
            if len(s) == 1 and s.isalpha():
                return True
        return False

    def is_disclaimer_present():
        for el in els():
            if el.get("editable"):
                continue
            comb = combined(el)
            if not comb:
                continue
            if any(
                phrase in comb
                for phrase in (
                    "reminders work properly",
                    "blocking the reminders",
                    "battery and notification settings",
                    "ignore battery optimization",
                    "notification access",
                    "serial console",
                    "check access",
                    "reminder settings",
                    "battery optimization",
                )
            ):
                return True
            if (
                len(comb) > 80
                and any(w in comb for w in ("reminder", "battery", "notification", "optimization", "access"))
                and any(w in comb for w in ("work", "block", "settings", "access", "optimization", "ignore", "disable"))
            ):
                return True
        return False

    def find_editable_direct(label):
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

    def is_event_screen():
        return (
            find_editable_direct("Title") is not None
            or find_editable_direct("Description") is not None
        )

    def find_ok_idx():
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
            return idx

        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().upper()
            desc = str(el.get("description") or "").strip().upper()
            if text in ("OK", "OKAY", "DONE", "SET") or desc in ("OK", "OKAY", "DONE", "SET"):
                idx = el.get("index")
                if idx is not None:
                    return int(idx)

        idx = find_first(
            {"text": "OK"},
            {"description": "OK"},
            {"text": "Ok"},
            {"description": "Ok"},
            {"text": "Done"},
            {"description": "Done"},
            {"text": "Set"},
            {"description": "Set"},
        )
        return idx

    def has_ok_button():
        if is_event_screen():
            return False
        return find_ok_idx() is not None

    def click_ok():
        if is_event_screen():
            raise RuntimeError("Not in a dialog")
        idx = find_ok_idx()
        if idx is not None:
            safe_click(idx, 1.0)
            return
        raise RuntimeError("Could not find clickable OK button")

    def dismiss_disclaimer():
        for _ in range(5):
            if not is_disclaimer_present():
                return False

            idx = find_ok_idx()
            if idx is not None:
                safe_click(idx, 0.8)
                continue

            device.navigate_back()
            settle(0.5)
        return True

    def find_editable(label):
        idx = find_editable_direct(label)
        if idx is not None:
            return idx

        title_idx = find_editable_direct("Title")
        desc_idx = find_editable_direct("Description")
        if title_idx is None and desc_idx is None:
            return None

        editables = []
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
            if any(k in comb for k in ("date", "time", "hour", "minute", "month", "day", "year")):
                continue
            editables.append(int(idx))

        editables = sorted(set(editables))
        if label == "Title" and editables:
            return editables[0]
        if label == "Description":
            if len(editables) > 1:
                return editables[1]
            if editables:
                return editables[0]
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

    def click_main_day_cell():
        if is_event_screen():
            return False

        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if is_keyboard_key(el):
                continue
            for val in (el.get("text"), el.get("description")):
                s = str(val or "").strip()
                if re.fullmatch(r"\d{1,2}", s):
                    try:
                        v = int(s)
                    except Exception:
                        continue
                    if 1 <= v <= 31:
                        return safe_click(idx, 1.0)

        header_idx = None
        for el in els():
            comb = combined(el)
            if any(name.lower() in comb for name in month_names):
                header_idx = el.get("index")
                break
            if any(re.search(rf"\b{abbr.lower()}\b", comb) for abbr in month_abbrs):
                header_idx = el.get("index")
                break

        if header_idx is not None:
            cands = []
            for el in els():
                if not el.get("clickable") or el.get("editable"):
                    continue
                idx = el.get("index")
                if idx is None:
                    continue
                if int(idx) <= int(header_idx):
                    continue
                comb = combined(el)
                if any(b in comb for b in EVENT_ROW_BAD):
                    continue
                if is_keyboard_key(el):
                    continue
                cands.append(int(idx))
            if cands:
                return safe_click(sorted(cands)[0], 1.0)

        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            comb = combined(el)
            if any(b in comb for b in EVENT_ROW_BAD):
                continue
            if is_keyboard_key(el):
                continue
            return safe_click(idx, 1.0)

        return False

    def open_new_event():
        dismiss_disclaimer()
        if is_event_screen():
            return

        for _ in range(5):
            idx = find_first(
                {"description": "New Event", "clickable": True},
                {"description": "New event", "clickable": True},
                {"text": "New Event", "clickable": True},
                {"hint": "New Event", "clickable": True},
                {"description": "Add event", "clickable": True},
                {"text": "Add", "clickable": True},
                {"text": "+", "clickable": True},
            )
            if idx is not None:
                safe_click(idx, 1.0)
                dismiss_disclaimer()
                if is_event_screen():
                    return

            if click_main_day_cell():
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
                if is_event_screen():
                    return

            device.scroll(direction="down")
            settle(0.5)
            if is_event_screen():
                return
            device.scroll(direction="up")
            settle(0.5)

        raise RuntimeError("Could not open the Event editor")

    def input_text_field(label, value):
        value = str(value)
        for _ in range(4):
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
                if value == "":
                    return
                try:
                    device.input_text(value, index=int(idx))
                    settle(0.5)
                    el = get_el_by_index(idx)
                    if el is None or value in str(el.get("text") or ""):
                        return
                except Exception:
                    pass

        raise RuntimeError(f"Could not find editable field for {label}")

    def event_anchor():
        title_idx = find_editable("Title")
        desc_idx = find_editable("Description")
        return title_idx, desc_idx

    def event_candidates_after_anchor():
        title_idx, desc_idx = event_anchor()
        after = desc_idx if desc_idx is not None else title_idx
        out = []

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
            if is_keyboard_key(el):
                continue

            out.append((int(idx), el, comb))

        out.sort(key=lambda x: x[0])
        return out

    def find_date_rows():
        primary = []
        numeric = []

        for idx, el, comb in event_candidates_after_anchor():
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()

            if ":" in comb or is_time_text(text) or is_time_text(desc):
                continue
            if "all day" in comb:
                continue

            if (
                "date" in comb
                or is_date_text(text)
                or is_date_text(desc)
                or re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", comb)
                or re.search(r"\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b", comb)
            ):
                primary.append(idx)
            else:
                for val in (text, desc):
                    if re.fullmatch(r"\d{1,2}", val):
                        try:
                            v = int(val)
                        except Exception:
                            continue
                        if 1 <= v <= 31:
                            numeric.append(idx)
                            break

        rows = primary if primary else numeric
        return sorted(set(rows))

    def find_time_rows():
        primary = []
        numeric = []

        for idx, el, comb in event_candidates_after_anchor():
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()

            if "date" in comb or looks_like_date_value(text) or looks_like_date_value(desc):
                continue
            if re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", comb):
                continue

            if (
                "time" in comb
                or is_time_text(text)
                or is_time_text(desc)
                or re.search(r"\b\d{1,2}:\d{2}\b", comb)
            ):
                primary.append(idx)
            else:
                for val in (text, desc):
                    if re.fullmatch(r"\d{1,2}", val):
                        try:
                            v = int(val)
                        except Exception:
                            continue
                        if 0 <= v <= 23:
                            numeric.append(idx)
                            break

        rows = primary if primary else numeric
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

        for _ in range(2):
            device.scroll(direction="up")
            settle(0.5)
            rows = find_date_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

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

        for _ in range(2):
            device.scroll(direction="up")
            settle(0.5)
            rows = find_time_rows()
            if len(rows) > len(best):
                best = rows
            if len(best) >= min_count:
                return best

        return best

    def find_clickable_containing(*phrases):
        phrases = [p.lower() for p in phrases if p]

        for p in phrases:
            idx = find_first({"clickable": True, "contains": p})
            if idx is not None:
                return idx

        for idx, el, comb in event_candidates_after_anchor():
            if any(p in comb for p in phrases):
                return idx

        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            comb = combined(el)
            if any(p in comb for p in phrases):
                idx = el.get("index")
                if idx is not None:
                    return int(idx)

        return None

    def find_start_date_row():
        idx = find_clickable_containing("start date")
        if idx is not None:
            return idx

        idx = find_clickable_containing("date")
        if idx is not None:
            comb = combined(get_el_by_index(idx) or {})
            if "end date" not in comb and "all day" not in comb:
                return idx

        rows = find_date_rows_with_scroll(1)
        if rows:
            return rows[0]

        for idx, el, comb in event_candidates_after_anchor():
            if "time" in comb or is_time_text(comb):
                continue
            if looks_like_date_value(comb) or "date" in comb:
                return idx

        return None

    def find_end_date_row():
        idx = find_clickable_containing("end date")
        if idx is not None:
            return idx

        rows = find_date_rows_with_scroll(2)
        if len(rows) >= 2:
            return rows[1]

        start_time_idx = find_start_time_row()
        for idx, el, comb in event_candidates_after_anchor():
            if start_time_idx is not None and int(idx) <= int(start_time_idx):
                continue
            if "end date" in comb or looks_like_date_value(comb) or "date" in comb:
                return idx

        if rows:
            return rows[0]

        return None

    def find_start_time_row():
        idx = find_clickable_containing("start time")
        if idx is not None:
            return idx

        idx = find_clickable_containing("time")
        if idx is not None:
            comb = combined(get_el_by_index(idx) or {})
            if "end time" not in comb:
                return idx

        rows = find_time_rows_with_scroll(1)
        if rows:
            return rows[0]

        start_date_idx = find_start_date_row()
        for idx, el, comb in event_candidates_after_anchor():
            if start_date_idx is not None and int(idx) <= int(start_date_idx):
                continue
            if "time" in comb or is_time_text(comb) or re.search(r"\b\d{1,2}:\d{2}\b", comb):
                return idx

        if start_date_idx is not None:
            for idx, el, comb in event_candidates_after_anchor():
                if int(idx) > int(start_date_idx) and not looks_like_date_value(comb) and "date" not in comb:
                    return idx

        return None

    def find_end_time_row():
        idx = find_clickable_containing("end time")
        if idx is not None:
            return idx

        rows = find_time_rows_with_scroll(2)
        if len(rows) >= 2:
            return rows[1]

        end_date_idx = find_end_date_row()
        if end_date_idx is not None:
            for idx, el, comb in event_candidates_after_anchor():
                if int(idx) > int(end_date_idx):
                    if "time" in comb or is_time_text(comb) or re.search(r"\b\d{1,2}:\d{2}\b", comb):
                        return idx
            for idx, el, comb in event_candidates_after_anchor():
                if int(idx) > int(end_date_idx):
                    return idx

        start_time_idx = find_start_time_row()
        if start_time_idx is not None:
            for idx, el, comb in event_candidates_after_anchor():
                if int(idx) > int(start_time_idx):
                    if "time" in comb or is_time_text(comb) or re.search(r"\b\d{1,2}:\d{2}\b", comb):
                        return idx
            for idx, el, comb in event_candidates_after_anchor():
                if int(idx) > int(start_time_idx):
                    return idx

        if rows:
            return rows[0]

        return None

    def looks_like_date_dialog():
        if not has_ok_button():
            return False

        idx = find_first(
            {"description": "Next month", "clickable": True},
            {"description": "next month", "clickable": True},
            {"description": "Previous month", "clickable": True},
            {"description": "previous month", "clickable": True},
            {"description": "Forward", "clickable": True},
            {"description": "Back", "clickable": True},
            {"text": "›", "clickable": True},
            {"text": "<", "clickable": True},
            {"text": "‹", "clickable": True},
            {"text": ">", "clickable": True},
        )
        if idx is not None:
            return True

        count = 0
        for el in els():
            if not el.get("clickable") or el.get("editable"):
                continue
            comb = combined(el)
            if "hour" in comb or "minute" in comb or ":" in comb or "date" in comb:
                continue
            for val in (el.get("text"), el.get("description")):
                s = str(val or "").strip()
                if re.fullmatch(r"\d{1,2}", s):
                    try:
                        v = int(s)
                    except Exception:
                        continue
                    if 1 <= v <= 31:
                        count += 1
                        break

        return count >= 8

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

        for _ in range(360):
            yy, mo, has_year = parse_month_year()
            if mo is None:
                return False

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
            if ":" in comb or "time" in comb or "hour" in comb or "minute" in comb or "date" in comb:
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
        return safe_click(candidates[0][1], 0.5)

    def find_date_editables():
        title_idx = find_editable_direct("Title")
        desc_idx = find_editable_direct("Description")
        scored = []
        fallback = []

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
            if any(k in comb for k in ("title", "description", "location", "event", "reminder", "time", "hour", "minute")):
                continue

            text = str(el.get("text") or "").strip()
            if event_title and text == event_title:
                continue
            if event_description and text == event_description:
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
                scored.append((score, int(idx), el))
            else:
                fallback.append((0, int(idx), el))

        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored:
            return scored

        fallback.sort(key=lambda x: x[1])
        return fallback

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

        month_idx = None
        day_idx = None
        year_idx = None

        for _, idx, el in editables:
            comb = combined(el)
            text = str(el.get("text") or "").strip()

            if month_idx is None and (re.search(r"\bmonth\b|\bmm\b", comb) or text == f"{m:02d}"):
                month_idx = idx
            if day_idx is None and (re.search(r"\bday\b|\bdd\b", comb) or text == f"{d:02d}"):
                day_idx = idx
            if year_idx is None and (re.search(r"\byear\b|\byyyy\b", comb) or re.fullmatch(r"\d{4}", text)):
                year_idx = idx

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
                return True
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
                    return True
                except Exception:
                    pass

        return False

    def set_date_picker(y, m, d):
        if not looks_like_date_dialog():
            return False

        if navigate_to_month(y, m):
            if click_day_cell(y, m, d):
                return True

        if click_day_cell(y, m, d):
            return True

        if try_date_text_input(y, m, d):
            return True

        return False

    def date_row_matches(row_number, y, m, d):
        idx = find_start_date_row() if row_number == 0 else find_end_date_row()
        if idx is None:
            return False
        el = get_el_by_index(idx)
        if not el:
            return False
        txt = combined(el)
        if not looks_like_date_value(txt):
            return False
        return matches_date_text(txt, y, m, d)

    def get_date_row_candidates(row_number, exclude):
        out = []

        idx = find_start_date_row() if row_number == 0 else find_end_date_row()
        if idx is not None and int(idx) not in exclude:
            out.append(int(idx))

        rows = find_date_rows_with_scroll(2)
        if len(rows) > row_number and int(rows[row_number]) not in exclude:
            out.append(int(rows[row_number]))

        cands = event_candidates_after_anchor()
        date_like = []
        other = []

        for idx, el, comb in cands:
            i = int(idx)
            if i in exclude:
                continue
            if "time" in comb or is_time_text(comb):
                continue
            if looks_like_date_value(comb) or "date" in comb:
                date_like.append(i)
            else:
                other.append(i)

        if row_number == 1:
            start_time_idx = find_start_time_row()
            if start_time_idx is not None:
                filtered = [i for i in date_like if i > int(start_time_idx)]
                date_like = filtered + date_like

        for i in date_like + other:
            if i not in out:
                out.append(i)

        return out

    def open_date_dialog(row_number, exclude):
        for idx in get_date_row_candidates(row_number, exclude):
            if not safe_click(idx, 1.0):
                continue
            dismiss_disclaimer()

            if not has_ok_button():
                safe_click(idx, 1.0)
                dismiss_disclaimer()

            if has_ok_button():
                if looks_like_date_dialog():
                    return idx
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

            exclude.add(int(idx))

        return None

    def set_date_for_row(row_number, y, m, d):
        exclude = set()

        for attempt in range(6):
            if has_ok_button():
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

            dismiss_disclaimer()

            idx = open_date_dialog(row_number, exclude)
            if idx is None:
                device.scroll(direction="down")
                settle(0.5)
                continue

            done = False
            if attempt % 2 == 0:
                done = set_date_picker(y, m, d)
            else:
                done = try_date_text_input(y, m, d)

            if done:
                try:
                    click_ok()
                except Exception:
                    pass

                settle(1.0)
                dismiss_disclaimer()

                if date_row_matches(row_number, y, m, d):
                    return

                if has_ok_button():
                    device.navigate_back()
                    settle(0.5)
                    dismiss_disclaimer()
            else:
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
            if "date" in comb or looks_like_date_value(text):
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
            if "date" in comb or looks_like_date_value(text):
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
        if looks_like_date_dialog():
            return False

        try:
            click_hour(h)
        except Exception:
            return False

        try:
            click_minute(m)
        except Exception:
            try:
                click_minute_selector()
                click_minute(m)
            except Exception:
                return m == 0

        return True

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
        title_idx = find_editable_direct("Title")
        desc_idx = find_editable_direct("Description")
        scored = []
        fallback = []

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
            if any(k in comb for k in ("title", "description", "location", "event", "reminder", "date", "month", "day", "year")):
                continue

            text = str(el.get("text") or "").strip()
            if event_title and text == event_title:
                continue
            if event_description and text == event_description:
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
                scored.append((score, int(idx), el))
            else:
                fallback.append((0, int(idx), el))

        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored:
            return scored

        fallback.sort(key=lambda x: x[1])
        return fallback

    def try_time_text_input(h, m):
        editables = find_time_editables()
        if not editables:
            if not toggle_time_keyboard():
                return False
            editables = find_time_editables()

        if not editables:
            return False

        hour_idx = None
        minute_idx = None

        for _, idx, el in editables:
            comb = combined(el)
            text = str(el.get("text") or "").strip()

            if hour_idx is None and (re.search(r"\bhour\b|\bhh\b", comb) or text in (str(h), f"{h:02d}")):
                hour_idx = idx
            if minute_idx is None and (re.search(r"\bminute\b|\bmm\b", comb) or text in (str(m), f"{m:02d}")):
                minute_idx = idx

        if hour_idx is not None and minute_idx is not None and int(hour_idx) != int(minute_idx):
            try:
                device.input_text(f"{h:02d}", index=int(hour_idx))
                device.input_text(f"{m:02d}", index=int(minute_idx))
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        if len(editables) >= 2:
            try:
                device.input_text(f"{h:02d}", index=int(editables[0][1]))
                device.input_text(f"{m:02d}", index=int(editables[1][1]))
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        for _, idx, _ in editables[:3]:
            for txt in (f"{h:02d}:{m:02d}", f"{h}:{m:02d}", f"{h:02d}{m:02d}", f"{h}{m:02d}"):
                try:
                    device.input_text(txt, index=int(idx))
                    device.keyboard_enter()
                    settle(0.5)
                    return True
                except Exception:
                    pass

        return False

    def set_time_picker(h, m, text_first=True):
        if text_first:
            if try_time_text_input(h, m):
                return True
            toggle_time_clock()
            return set_time_clock(h, m)

        if set_time_clock(h, m):
            return True
        toggle_time_keyboard()
        return try_time_text_input(h, m)

    def time_row_matches(which, h, m):
        idx = find_start_time_row() if which == 0 else find_end_time_row()
        if idx is None:
            return False
        el = get_el_by_index(idx)
        if not el:
            return False
        return time_matches(combined(el), h, m)

    def get_time_row_candidates(which, exclude):
        out = []

        idx = find_start_time_row() if which == 0 else find_end_time_row()
        if idx is not None and int(idx) not in exclude:
            out.append(int(idx))

        rows = find_time_rows_with_scroll(2)
        if len(rows) > which and int(rows[which]) not in exclude:
            out.append(int(rows[which]))

        cands = event_candidates_after_anchor()
        time_like = []
        other = []

        for idx, el, comb in cands:
            i = int(idx)
            if i in exclude:
                continue
            if "date" in comb or looks_like_date_value(comb):
                continue
            if "time" in comb or is_time_text(comb) or re.search(r"\b\d{1,2}:\d{2}\b", comb):
                time_like.append(i)
            else:
                other.append(i)

        if which == 1:
            start_time_idx = find_start_time_row()
            if start_time_idx is not None:
                filtered = [i for i in time_like if i > int(start_time_idx)]
                time_like = filtered + time_like

        for i in time_like + other:
            if i not in out:
                out.append(i)

        return out

    def open_time_dialog(which, exclude):
        for idx in get_time_row_candidates(which, exclude):
            if not safe_click(idx, 1.0):
                continue
            dismiss_disclaimer()

            if not has_ok_button():
                safe_click(idx, 1.0)
                dismiss_disclaimer()

            if has_ok_button():
                if not looks_like_date_dialog():
                    return idx
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

            exclude.add(int(idx))

        return None

    def set_time_for_row(which, h, m):
        exclude = set()

        for attempt in range(6):
            if has_ok_button():
                device.navigate_back()
                settle(0.5)
                dismiss_disclaimer()

            dismiss_disclaimer()

            idx = open_time_dialog(which, exclude)
            if idx is None:
                device.scroll(direction="down")
                settle(0.5)
                continue

            done = set_time_picker(h, m, text_first=(attempt % 2 == 0))

            if done:
                try:
                    click_ok()
                except Exception:
                    pass

                settle(1.0)
                dismiss_disclaimer()

                if time_row_matches(which, h, m):
                    return

                if has_ok_button():
                    device.navigate_back()
                    settle(0.5)
                    dismiss_disclaimer()
            else:
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
        for _ in range(6):
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

        raise RuntimeError("Could not save the event")

    device.open_app("Simple Calendar Pro")
    settle(2.0)
    dismiss_disclaimer()

    open_new_event()

    input_text_field("Title", event_title)
    input_text_field("Description", event_description)

    if not is_event_screen():
        open_new_event()
        input_text_field("Title", event_title)
        input_text_field("Description", event_description)

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
