import re

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

    end_total_minutes = hour * 60 + duration_mins
    end_hour = (end_total_minutes // 60) % 24
    end_minute = end_total_minutes % 60

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    month_abbrs = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    month_name = month_names[month - 1]
    month_abbr = month_abbrs[month - 1]

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
        s = str(text or "").strip().upper()
        if ":" not in s:
            return None
        s = s.replace("AM", "").replace("PM", "").strip()
        parts = s.split(":")
        if len(parts) < 2:
            return None
        try:
            h = int(parts[0].strip())
            m = int(parts[1].strip()[:2])
        except Exception:
            return None
        return h, m

    def is_time_text(text):
        return parse_time(text) is not None

    def time_matches(text, h, m):
        parsed = parse_time(text)
        return parsed is not None and parsed[0] == h and parsed[1] == m

    def click_ok():
        idx = find_first(
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"description": "Ok", "clickable": True},
            {"text": "Okay", "clickable": True},
            {"description": "Okay", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            settle(1.0)
            return

        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().upper()
            if text in ("OK", "OKAY"):
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(1.0)
                    return

        raise RuntimeError("Could not find clickable OK button")

    def parse_current_month_year():
        for el in els():
            for val in (el.get("text"), el.get("hint"), el.get("description")):
                s = str(val or "").strip()
                if not s:
                    continue
                low = s.lower()

                for i, name in enumerate(month_names):
                    if name.lower() in low:
                        m = re.search(r"\b(\d{4})\b", s)
                        if m:
                            return int(m.group(1)), i + 1

                for i, abbr in enumerate(month_abbrs):
                    if re.search(rf"\b{abbr.lower()}\b", low):
                        m = re.search(r"\b(\d{4})\b", s)
                        if m:
                            return int(m.group(1)), i + 1

                m = re.search(r"\b(\d{4})[-/](\d{1,2})\b", s)
                if m:
                    y = int(m.group(1))
                    mo = int(m.group(2))
                    if 1 <= mo <= 12:
                        return y, mo

                m = re.search(r"\b(\d{1,2})[-/](\d{4})\b", s)
                if m:
                    mo = int(m.group(1))
                    y = int(m.group(2))
                    if 1 <= mo <= 12:
                        return y, mo

        return None

    def step_month(forward):
        if forward:
            idx = find_first(
                {"description": "Next month", "clickable": True},
                {"description": "next month", "clickable": True},
                {"description": "Forward", "clickable": True},
                {"text": "›", "clickable": True},
                {"text": ">", "clickable": True},
            )
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return

            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                if "next" in desc:
                    idx = el.get("index")
                    if idx is not None:
                        device.click(index=int(idx))
                        settle(0.5)
                        return

            device.scroll("left")
            settle(0.5)
        else:
            idx = find_first(
                {"description": "Previous month", "clickable": True},
                {"description": "previous month", "clickable": True},
                {"description": "Back", "clickable": True},
                {"text": "‹", "clickable": True},
                {"text": "<", "clickable": True},
            )
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return

            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                if "previous" in desc:
                    idx = el.get("index")
                    if idx is not None:
                        device.click(index=int(idx))
                        settle(0.5)
                        return

            device.scroll("right")
            settle(0.5)

    def navigate_to_month(y, m):
        target = y * 12 + m
        last_cur = None
        same_count = 0

        for _ in range(48):
            cur = parse_current_month_year()
            if cur is None:
                return

            cur_idx = cur[0] * 12 + cur[1]
            if cur_idx == target:
                return

            if last_cur == cur_idx:
                same_count += 1
                if same_count >= 3:
                    return
            else:
                same_count = 0
            last_cur = cur_idx

            step_month(cur_idx < target)

    def click_day_cell(y, m, d, allow_full_text=False):
        m_name = month_names[m - 1]
        m_abbr = month_abbrs[m - 1]
        labels = [
            f"{m_name} {d}",
            f"{d} {m_name}",
            f"{m_name} {d}, {y}",
            f"{y}-{m:02d}-{d:02d}",
            f"{d:02d}-{m:02d}-{y}",
            f"{m_abbr} {d}",
            f"{d} {m_abbr}",
        ]

        for label in labels:
            idx = find_first(
                {"clickable": True, "description": label},
                {"clickable": True, "hint": label},
            )
            if idx is None and allow_full_text:
                idx = find_first(
                    {"clickable": True, "contains": label},
                    {"clickable": True, "text": label},
                )
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True

        day_strs = {str(d), f"{d:02d}"}
        candidates = []

        for el in els():
            if not el.get("clickable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()

            if text in day_strs or desc in day_strs or hint in day_strs:
                score = 0
                if text in day_strs:
                    score += 1
                if m_name.lower() in desc.lower() or m_abbr.lower() in desc.lower():
                    score += 3
                if str(y) in desc:
                    score += 3
                candidates.append((score, int(idx)))

        if candidates:
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            device.click(index=candidates[0][1])
            settle(0.5)
            return True

        return False

    def is_date_like(text):
        s = str(text or "").strip()
        if not s or ":" in s:
            return False

        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", s):
            return True
        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", s):
            return True

        low = s.lower()
        for name in month_names + month_abbrs:
            if name.lower() in low:
                return True

        if re.search(r"\b\d{1,2}\b", s) and re.search(r"\b\d{4}\b", s):
            return True

        return False

    def matches_target_date(text):
        s = str(text or "")
        if not s:
            return False

        low = s.lower()

        if f"{year}-{month:02d}-{day:02d}" in s:
            return True
        if f"{day:02d}-{month:02d}-{year}" in s:
            return True

        has_month = any(name.lower() in low for name in month_names + month_abbrs)
        has_year = bool(re.search(rf"\b{year}\b", s))
        has_day = bool(re.search(rf"(?<!\d){day}(?!\d)", s)) or bool(
            re.search(rf"(?<!\d){day:02d}(?!\d)", s)
        )

        if has_month and has_day:
            if has_year or not re.search(r"\b\d{4}\b", s):
                return True

        if has_year and has_day and re.search(rf"\b{month:02d}\b", s):
            return True

        return False

    def find_date_row():
        idx = find_first(
            {"text": "Date", "clickable": True},
            {"description": "Date", "clickable": True},
            {"hint": "Date", "clickable": True},
            {"text": "Start date", "clickable": True},
            {"description": "Start date", "clickable": True},
        )
        if idx is not None:
            return idx

        candidates = []
        for el in els():
            if not el.get("clickable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            combined = f"{text} {desc} {hint}".lower()

            if ":" in text:
                continue

            if "date" in combined:
                candidates.append(el)
            elif "TextView" in str(el.get("class_name") or "") and is_date_like(text):
                candidates.append(el)

        if candidates:
            candidates.sort(key=lambda e: int(e.get("index", -1)))
            return int(candidates[0].get("index"))

        return None

    def find_date_row_with_scroll():
        idx = find_date_row()
        if idx is not None:
            return idx

        device.scroll("down")
        settle(0.5)
        idx = find_date_row()
        if idx is not None:
            return idx

        return None

    def set_date_via_keyboard():
        if find_first({"text": "OK", "clickable": True}, {"description": "OK", "clickable": True}) is None:
            return False

        editables = [e for e in els() if e.get("editable")]
        if not editables:
            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").lower()
                if "keyboard" in desc or "keyboard" in text or "input" in desc:
                    idx = el.get("index")
                    if idx is not None:
                        device.click(index=int(idx))
                        settle(0.5)
                        break
            editables = [e for e in els() if e.get("editable")]

        if not editables:
            return False

        filtered = []
        for e in editables:
            combined = f"{e.get('hint') or ''} {e.get('description') or ''} {e.get('text') or ''}".lower()
            if "title" in combined or "description" in combined:
                continue
            if e.get("index") is not None:
                filtered.append(e)

        if not filtered:
            return False

        filtered.sort(key=lambda e: int(e.get("index", -1)))
        idx = int(filtered[0].get("index"))

        formats = [
            f"{month:02d}/{day:02d}/{year}",
            f"{year}-{month:02d}-{day:02d}",
            f"{day:02d}/{month:02d}/{year}",
            f"{month}/{day}/{year}",
        ]

        for fmt in formats:
            try:
                device.input_text(fmt, index=idx)
                device.keyboard_enter()
                settle(0.5)
                if find_first({"text": "OK", "clickable": True}) is not None:
                    return True
            except Exception:
                pass

        return False

    def set_date_via_picker(start_idx=None):
        idx = start_idx if start_idx is not None else find_date_row_with_scroll()
        if idx is None:
            return False

        device.click(index=idx)
        settle(1.0)

        navigate_to_month(year, month)

        clicked = click_day_cell(year, month, day, allow_full_text=False)
        if not clicked:
            clicked = click_day_cell(year, month, day, allow_full_text=True)
        if not clicked:
            clicked = set_date_via_keyboard()

        if not clicked:
            raise RuntimeError("Could not select the requested date in the date picker")

        click_ok()
        settle(1.0)
        return True

    def ensure_date():
        for _ in range(2):
            idx = find_date_row_with_scroll()
            if idx is None:
                return

            el = get_el_by_index(idx)
            text = str((el or {}).get("text") or "")
            if matches_target_date(text):
                return

            set_date_via_picker(idx)

        idx = find_date_row_with_scroll()
        if idx is not None:
            el = get_el_by_index(idx)
            if not matches_target_date(str((el or {}).get("text") or "")):
                raise RuntimeError("Event date did not match the requested date")

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
        )
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

        device.input_text(value, index=idx)
        settle(0.5)

    def delete_default_reminder():
        has_reminder = False
        for el in els():
            combined = f"{el.get('text') or ''} {el.get('description') or ''}".lower()
            if "reminder" in combined or "minutes before" in combined or "minutes after" in combined:
                has_reminder = True
                break

        if not has_reminder:
            return False

        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").strip()
            cls = str(el.get("class_name") or "")
            if desc == "Delete" and "FrameLayout" in cls:
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(0.5)
                    return True

        return False

    def get_time_fields(min_count=2):
        last_fields = []

        for _ in range(5):
            fields = []
            for el in els():
                if not el.get("clickable"):
                    continue
                if "TextView" not in str(el.get("class_name") or ""):
                    continue
                if is_time_text(el.get("text")):
                    idx = el.get("index")
                    if idx is not None:
                        fields.append(el)

            last_fields = fields
            if len(fields) >= min_count:
                date_idx = find_date_row()
                if date_idx is not None:
                    try:
                        after = [f for f in fields if int(f.get("index", -1)) > int(date_idx)]
                    except Exception:
                        after = fields
                    if len(after) >= min_count:
                        return after[:min_count]
                return fields[:min_count]

            device.scroll("down")
            settle(0.5)

        return last_fields

    def click_hour(h):
        hour_texts = {str(h), f"{h:02d}"}
        hour_descs = {f"{h} hours".lower(), f"{h:02d} hours".lower()}
        candidates = []

        for el in els():
            if not el.get("clickable"):
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
                idx = device.find(description=desc, clickable=True)
                if idx is not None:
                    candidates.append({"index": idx})

        if not candidates:
            raise RuntimeError(f"Could not find hour {h} in the time picker")

        valid = [e for e in candidates if e.get("index") is not None]
        if not valid:
            raise RuntimeError(f"Could not click hour {h} in the time picker")

        idx = max(int(e.get("index")) for e in valid)
        device.click(index=idx)
        settle(0.5)

    def click_minute_selector():
        candidates = []
        for el in els():
            if not el.get("clickable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue

            desc = str(el.get("description") or "").strip().lower()
            cls = str(el.get("class_name") or "")

            if desc.endswith("minutes") or desc.endswith("minute"):
                candidates.append((int(idx), cls))

        if not candidates:
            raise RuntimeError("Could not find the minute selector in the time picker")

        non_text_view = [c for c in candidates if "TextView" not in c[1]]
        pool = non_text_view if non_text_view else candidates
        idx = min(pool, key=lambda x: x[0])[0]

        device.click(index=idx)
        settle(0.5)

    def click_minute(m):
        minute_texts = {str(m), f"{m:02d}"}
        minute_descs = {f"{m} minutes".lower(), f"{m:02d} minutes".lower()}
        candidates = []

        for el in els():
            if not el.get("clickable"):
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
                idx = device.find(description=desc, clickable=True)
                if idx is not None:
                    candidates.append({"index": idx})

        if not candidates:
            raise RuntimeError(f"Could not find minute {m} in the time picker")

        text_view_candidates = [
            e for e in candidates
            if "TextView" in str(e.get("class_name") or "") and e.get("index") is not None
        ]
        pool = text_view_candidates if text_view_candidates else [e for e in candidates if e.get("index") is not None]

        if not pool:
            raise RuntimeError(f"Could not click minute {m} in the time picker")

        idx = max(int(e.get("index")) for e in pool)
        device.click(index=idx)
        settle(0.5)

    def toggle_time_keyboard():
        idx = find_first(
            {"description": "Toggle keyboard", "clickable": True},
            {"description": "Switch to keyboard", "clickable": True},
            {"description": "Keyboard", "clickable": True},
            {"text": "Keyboard", "clickable": True},
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
            if "keyboard" in desc or "keyboard" in text or "toggle" in desc:
                idx = el.get("index")
                if idx is not None:
                    device.click(index=int(idx))
                    settle(0.5)
                    return True

        return False

    def input_time_digits(h, m):
        idx = find_first(
            {"hint": "Time", "editable": True},
            {"description": "Time", "editable": True},
            {"text": "Time", "editable": True},
        )
        if idx is not None:
            try:
                device.input_text(f"{h:02d}:{m:02d}", index=idx)
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        ok_present = find_first(
            {"text": "OK", "clickable": True},
            {"description": "OK", "clickable": True},
        ) is not None
        if not ok_present:
            return False

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
                device.input_text(f"{h:02d}", index=hour_idx)
                device.input_text(f"{m:02d}", index=minute_idx)
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        editables = [e for e in els() if e.get("editable") and e.get("index") is not None]
        filtered = []
        for e in editables:
            combined = f"{e.get('hint') or ''} {e.get('description') or ''} {e.get('text') or ''}".lower()
            if "title" in combined or "description" in combined:
                continue
            filtered.append(e)

        if len(filtered) >= 2:
            filtered.sort(key=lambda e: int(e.get("index", -1)))
            try:
                device.input_text(f"{h:02d}", index=int(filtered[0].get("index")))
                device.input_text(f"{m:02d}", index=int(filtered[1].get("index")))
                device.keyboard_enter()
                settle(0.5)
                return True
            except Exception:
                pass

        return False

    def set_time(h, m, force_keyboard=False):
        if not force_keyboard:
            try:
                click_hour(h)
                try:
                    click_minute_selector()
                except Exception:
                    pass
                click_minute(m)
                click_ok()
                return
            except Exception:
                pass

        toggle_time_keyboard()
        if input_time_digits(h, m):
            click_ok()
            return

        raise RuntimeError(f"Could not set time to {h:02d}:{m:02d}")

    def open_and_set_time(which, h, m):
        for attempt in range(2):
            fields = get_time_fields(2)
            if len(fields) <= which:
                raise RuntimeError("Could not find start/end time fields")

            device.click(index=int(fields[which].get("index")))
            settle(1.0)

            set_time(h, m, force_keyboard=(attempt == 1))

            fields = get_time_fields(2)
            if len(fields) > which and time_matches(fields[which].get("text"), h, m):
                return

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
            raise RuntimeError("Could not find the Save button")

        device.click(index=idx)
        settle(1.0)

    device.open_app("Simple Calendar Pro")
    settle(2.0)

    navigate_to_month(year, month)
    day_clicked = click_day_cell(year, month, day, allow_full_text=True)

    open_new_event()

    input_text_field("Title", event_title)
    input_text_field("Description", event_description)

    if not day_clicked and find_date_row_with_scroll() is None:
        raise RuntimeError("Could not locate the event date control")

    delete_default_reminder()
    ensure_date()

    open_and_set_time(0, hour, 0)
    open_and_set_time(1, end_hour, end_minute)

    click_save()
    return True
