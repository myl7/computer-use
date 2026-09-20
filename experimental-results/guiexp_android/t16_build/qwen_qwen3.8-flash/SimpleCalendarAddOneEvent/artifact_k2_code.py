import re
import calendar

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

PARAMS_SCHEMA = {
    "year": {
        "type": "integer",
        "description": "Event year."
    },
    "month": {
        "type": "integer",
        "minimum": 1,
        "maximum": 12,
        "description": "Event month, 1-12."
    },
    "day": {
        "type": "integer",
        "description": "Event day of month."
    },
    "hour": {
        "type": "integer",
        "minimum": 0,
        "maximum": 23,
        "description": "Event start hour in 24-hour clock."
    },
    "duration_mins": {
        "type": "integer",
        "description": "Event duration in minutes."
    },
    "event_title": {
        "type": "string",
        "description": "Event title."
    },
    "event_description": {
        "type": "string",
        "description": "Event description."
    },
}


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"]) % 24
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    days_in_month = calendar.monthrange(year, month)[1]
    month_name = MONTH_NAMES[month - 1]

    def settle(seconds=0.5):
        device.settle(seconds)

    def elements():
        return device.elements()

    def find_first(*queries):
        for q in queries:
            if not q:
                continue
            idx = device.find(**q)
            if idx is not None:
                return idx
        return None

    def click_first(*queries):
        idx = find_first(*queries)
        if idx is None:
            return False
        device.click(index=idx)
        return True

    def month_key(ym):
        return ym[0] * 12 + ym[1]

    def get_current_month_year():
        for el in elements():
            for field in ("text", "description", "hint"):
                s = str(el.get(field) or "")
                if not s:
                    continue

                m = re.search(r"([A-Za-z]+)\s+(\d{4})", s)
                if m:
                    name = m.group(1).title()
                    if name in MONTH_NAMES:
                        return int(m.group(2)), MONTH_NAMES.index(name) + 1

                m = re.search(r"(\d{4})\s+([A-Za-z]+)", s)
                if m:
                    name = m.group(2).title()
                    if name in MONTH_NAMES:
                        return int(m.group(1)), MONTH_NAMES.index(name) + 1
        return None

    def find_nav_button(which):
        if which == "prev":
            queries = [
                {"description": "Previous month", "clickable": True},
                {"text": "Previous month", "clickable": True},
                {"hint": "Previous month", "clickable": True},
                {"description": "Prev month", "clickable": True},
                {"contains": "Previous", "clickable": True},
                {"contains": "Prev", "clickable": True},
            ]
        else:
            queries = [
                {"description": "Next month", "clickable": True},
                {"text": "Next month", "clickable": True},
                {"hint": "Next month", "clickable": True},
                {"contains": "Next", "clickable": True},
            ]
        return find_first(*queries)

    def find_day_cell():
        # Prefer the contiguous current-month sequence if day cells expose digits.
        cells = []
        for el in elements():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip()
            if text.isdigit():
                value = int(text)
                if 1 <= value <= 31:
                    idx = el.get("index")
                    if idx is not None:
                        cells.append((idx, value))

        if len(cells) >= days_in_month:
            for start in range(len(cells) - days_in_month + 1):
                if cells[start][1] != 1:
                    continue
                ok = True
                for d in range(days_in_month):
                    if cells[start + d][1] != d + 1:
                        ok = False
                        break
                if ok and 1 <= day <= days_in_month:
                    return cells[start + day - 1][0]

        # Full-date accessibility labels, if present.
        full_labels = [
            f"{month_name} {day}",
            f"{day} {month_name}",
            f"{month_name} {day}, {year}",
            f"{year}-{month:02d}-{day:02d}",
            f"{month_name} {day:02d}",
            f"{day:02d} {month_name}",
        ]
        for el in elements():
            if not el.get("clickable"):
                continue
            vals = [
                str(el.get("description") or ""),
                str(el.get("hint") or ""),
                str(el.get("text") or ""),
            ]
            for s in vals:
                if not s:
                    continue
                for label in full_labels:
                    if label in s:
                        idx = el.get("index")
                        if idx is not None:
                            return idx

        # Exact day-number candidates.
        target_exact = {str(day), f"{day:02d}"}
        best_idx = None
        best_score = -1
        for el in elements():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            cls = str(el.get("class_name") or "")

            score = -1
            if text in target_exact:
                score = 4
            elif desc in target_exact or hint in target_exact:
                score = 3

            if score < 0:
                continue

            if "Button" in cls:
                score -= 3
            if "View" in cls:
                score += 1
            if "TextView" in cls:
                score += 1

            idx = el.get("index")
            if idx is not None and score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None:
            return best_idx

        return find_first(
            {"text": str(day), "clickable": True},
            {"description": str(day), "clickable": True},
            {"hint": str(day), "clickable": True},
            {"contains": str(day), "clickable": True},
        )

    def visible_correct():
        idx = find_day_cell()
        if idx is None:
            return None
        cur = get_current_month_year()
        if cur is None or (cur[0] == year and cur[1] == month):
            return idx
        return None

    def navigate_to_date():
        idx = visible_correct()
        if idx is not None:
            return idx

        target_key = year * 12 + month
        cur = get_current_month_year()

        # Use month navigation buttons when available.
        if cur is not None:
            diff = target_key - month_key(cur)
            for _ in range(abs(diff)):
                if diff > 0:
                    btn = find_nav_button("next")
                    if btn is None:
                        break
                    device.click(index=btn)
                else:
                    btn = find_nav_button("prev")
                    if btn is None:
                        break
                    device.click(index=btn)

                settle(0.3)
                cur = get_current_month_year()
                if cur is None:
                    break
                diff = target_key - month_key(cur)
                if diff == 0:
                    break

            idx = visible_correct()
            if idx is not None:
                return idx

        # Trial horizontal scrolling to learn forward/backward direction.
        cur = get_current_month_year()
        if cur is not None:
            device.scroll("left")
            settle(0.3)
            trial = get_current_month_year()
            if trial is not None and trial != cur:
                delta = month_key(trial) - month_key(cur)
                forward_dir = "left" if delta > 0 else "right"
                backward_dir = "right" if delta > 0 else "left"
                diff = target_key - month_key(trial)
                for _ in range(abs(diff)):
                    device.scroll(forward_dir if diff > 0 else backward_dir)
                    settle(0.3)

                idx = visible_correct()
                if idx is not None:
                    return idx

            cur = get_current_month_year()
            if cur is not None:
                device.scroll("right")
                settle(0.3)
                trial = get_current_month_year()
                if trial is not None and trial != cur:
                    delta = month_key(trial) - month_key(cur)
                    forward_dir = "right" if delta > 0 else "left"
                    backward_dir = "left" if delta > 0 else "right"
                    diff = target_key - month_key(trial)
                    for _ in range(abs(diff)):
                        device.scroll(forward_dir if diff > 0 else backward_dir)
                        settle(0.3)

                    idx = visible_correct()
                    if idx is not None:
                        return idx

        # Last-resort bounded scan.
        for _ in range(48):
            idx = visible_correct()
            if idx is not None:
                return idx

            cur = get_current_month_year()
            if cur is not None:
                diff = target_key - month_key(cur)
                if diff > 0:
                    btn = find_nav_button("next")
                    if btn is not None:
                        device.click(index=btn)
                        settle(0.3)
                        continue
                    device.scroll("left")
                elif diff < 0:
                    btn = find_nav_button("prev")
                    if btn is not None:
                        device.click(index=btn)
                        settle(0.3)
                        continue
                    device.scroll("right")
                else:
                    break
            else:
                device.scroll("left")

            settle(0.3)

        return None

    def find_title_index():
        return find_first(
            {"hint": "Title", "editable": True},
            {"text": "Title", "editable": True},
            {"contains": "Title", "editable": True},
        )

    def find_description_index():
        idx = find_first(
            {"hint": "Description", "editable": True},
            {"text": "Description", "editable": True},
            {"contains": "Description", "editable": True},
        )
        if idx is not None:
            return idx

        device.scroll("down")
        settle(0.3)
        idx = find_first(
            {"hint": "Description", "editable": True},
            {"text": "Description", "editable": True},
            {"contains": "Description", "editable": True},
        )
        if idx is not None:
            return idx

        editables = [el.get("index") for el in elements() if el.get("editable")]
        if len(editables) >= 2:
            return editables[1]
        if len(editables) == 1:
            return editables[0]
        return None

    def is_time_text(s):
        s = str(s or "").strip()
        if ":" not in s:
            return False
        parts = s.split(":")
        return len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()

    def get_time_field_indices():
        times = []
        for _ in range(4):
            times = []
            for el in elements():
                text = str(el.get("text") or "")
                cls = str(el.get("class_name") or "")
                if el.get("clickable") and "TextView" in cls and is_time_text(text):
                    idx = el.get("index")
                    if idx is not None:
                        times.append(idx)
            if len(times) >= 2:
                return times
            device.scroll("down")
            settle(0.3)

        # Fallback: any clickable time-like TextView/View.
        for _ in range(4):
            times = []
            for el in elements():
                text = str(el.get("text") or "")
                if el.get("clickable") and is_time_text(text):
                    idx = el.get("index")
                    if idx is not None:
                        times.append(idx)
            if len(times) >= 2:
                return times
            device.scroll("down")
            settle(0.3)

        return times

    def click_ok():
        idx = find_first(
            {"text": "OK", "clickable": True},
            {"text": "Ok", "clickable": True},
            {"text": "Okay", "clickable": True},
            {"description": "OK", "clickable": True},
        )
        if idx is not None:
            device.click(index=idx)
            return True

        for el in elements():
            if not el.get("clickable"):
                continue
            cls = str(el.get("class_name") or "")
            text = str(el.get("text") or "").strip().upper()
            idx = el.get("index")
            if idx is not None and "Button" in cls and text in ("OK", "OKAY"):
                device.click(index=idx)
                return True
        return False

    def click_picker_value(kind, value):
        value = int(value) % (24 if kind == "hour" else 60)

        if kind == "hour":
            descs = [f"{value} hours", f"{value:02d} hours"]
            texts = [str(value), f"{value:02d}"]
        else:
            descs = [f"{value} minutes", f"{value:02d} minutes"]
            texts = [str(value), f"{value:02d}"]

        def try_click():
            for d in descs:
                idx = find_first({"description": d, "clickable": True})
                if idx is not None:
                    device.click(index=idx)
                    return True

            for el in elements():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").strip()
                text = str(el.get("text") or "").strip()
                cls = str(el.get("class_name") or "")
                idx = el.get("index")
                if idx is None:
                    continue

                if desc in descs:
                    device.click(index=idx)
                    return True

                if text in texts and ":" not in text:
                    if desc.endswith(" hours" if kind == "hour" else " minutes") or "View" in cls:
                        device.click(index=idx)
                        return True

            return False

        if try_click():
            return True

        for _ in range(2):
            device.scroll("down")
            settle(0.2)
            if try_click():
                return True

        for _ in range(2):
            device.scroll("up")
            settle(0.2)
            if try_click():
                return True

        return False

    def click_picker_selector(kind):
        suffix = " hours" if kind == "hour" else " minutes"
        other_suffix = " minutes" if kind == "hour" else " hours"

        for el in elements():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").strip()
            idx = el.get("index")
            if idx is not None and desc.endswith(suffix):
                device.click(index=idx)
                return True

        # Fallback: a small digit selector, avoiding the other unit if possible.
        for el in elements():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").strip()
            text = str(el.get("text") or "").strip()
            cls = str(el.get("class_name") or "")
            idx = el.get("index")
            if idx is None:
                continue
            if desc.endswith(other_suffix):
                continue
            if text.isdigit() and len(text) <= 2 and ("View" in cls or "TextView" in cls):
                device.click(index=idx)
                return True

        return False

    def switch_to_text_input():
        return click_first(
            {"description": "Toggle text input", "clickable": True},
            {"text": "Toggle text input", "clickable": True},
            {"description": "Switch to text input", "clickable": True},
            {"contains": "text input", "clickable": True},
            {"contains": "keyboard", "clickable": True},
            {"contains": "enter time", "clickable": True},
        )

    def set_time_via_text(hour_value, minute_value):
        hour_value = int(hour_value) % 24
        minute_value = int(minute_value) % 60

        switch_to_text_input()
        settle(0.3)

        hour_idx = find_first(
            {"hint": "Hours", "editable": True},
            {"hint": "Hour", "editable": True},
            {"text": "Hours", "editable": True},
            {"text": "Hour", "editable": True},
            {"contains": "hour", "editable": True},
            {"contains": "HH", "editable": True},
        )
        minute_idx = find_first(
            {"hint": "Minutes", "editable": True},
            {"hint": "Minute", "editable": True},
            {"text": "Minutes", "editable": True},
            {"text": "Minute", "editable": True},
            {"contains": "minute", "editable": True},
            {"contains": "MM", "editable": True},
        )

        editables = [el.get("index") for el in elements() if el.get("editable")]
        editables = [x for x in editables if x is not None]

        if hour_idx is None and minute_idx is None:
            if len(editables) == 1:
                device.input_text(f"{hour_value:02d}:{minute_value:02d}", index=editables[0])
                return click_ok()
            if len(editables) >= 2:
                hour_idx, minute_idx = editables[0], editables[1]
        else:
            if hour_idx is None and len(editables) >= 2:
                hour_idx = editables[0]
            if minute_idx is None and len(editables) >= 2:
                minute_idx = editables[1]

        if hour_idx is None or minute_idx is None:
            return False

        device.input_text(f"{hour_value:02d}", index=hour_idx)
        settle(0.2)
        device.input_text(f"{minute_value:02d}", index=minute_idx)
        settle(0.2)
        return click_ok()

    def set_time(hour_value, minute_value):
        hour_value = int(hour_value) % 24
        minute_value = None if minute_value is None else int(minute_value) % 60

        hour_set = False
        if hour_value is not None:
            hour_set = click_picker_value("hour", hour_value)
            if not hour_set:
                click_picker_selector("hour")
                hour_set = click_picker_value("hour", hour_value)

        minute_set = True if minute_value is None else False
        if minute_value is not None:
            minute_set = click_picker_value("minute", minute_value)
            if not minute_set:
                click_picker_selector("minute")
                minute_set = click_picker_value("minute", minute_value)

        if (minute_value is None and hour_set) or (minute_value is not None and hour_set and minute_set):
            if click_ok():
                return True

        if set_time_via_text(hour_value, minute_value if minute_value is not None else 0):
            return True

        if hour_set and click_ok():
            return True

        return False

    device.open_app("Simple Calendar Pro")
    settle(1.0)

    date_idx = navigate_to_date()
    if date_idx is None:
        raise RuntimeError("Could not find target date in Simple Calendar Pro month view")

    device.click(index=date_idx)
    settle(1.0)

    # If the day tap did not already open the event editor, use the New Event action.
    if find_title_index() is None:
        click_first(
            {"description": "New Event", "clickable": True},
            {"text": "New Event", "clickable": True},
            {"hint": "New Event", "clickable": True},
            {"contains": "New Event", "clickable": True},
        )
        settle(1.0)

    # Some versions show an event-type chooser after New Event.
    if find_title_index() is None:
        click_first(
            {"text": "Event", "clickable": True},
            {"description": "Event", "clickable": True},
            {"hint": "Event", "clickable": True},
        )
        settle(1.0)

    title_idx = find_title_index()
    if title_idx is None:
        for el in elements():
            if el.get("editable"):
                title_idx = el.get("index")
                break

    if title_idx is None:
        raise RuntimeError("Could not find the event Title field")

    device.input_text(event_title, index=title_idx)
    settle(0.3)

    desc_idx = find_description_index()
    if desc_idx is None:
        if event_description:
            raise RuntimeError("Could not find the event Description field")
    else:
        device.input_text(event_description, index=desc_idx)
        settle(0.3)

    time_fields = get_time_field_indices()
    if len(time_fields) < 1:
        raise RuntimeError("Could not find the start time field")

    device.click(index=time_fields[0])
    settle(0.5)

    if not set_time(hour, None):
        raise RuntimeError("Could not set and confirm the start time")

    settle(0.5)

    total_end_minutes = hour * 60 + duration_mins
    end_hour = (total_end_minutes // 60) % 24
    end_minute = total_end_minutes % 60

    time_fields = get_time_field_indices()
    if len(time_fields) < 2:
        device.scroll("up")
        settle(0.3)
        time_fields = get_time_field_indices()

    if len(time_fields) < 2:
        raise RuntimeError("Could not find the end time field")

    device.click(index=time_fields[1])
    settle(0.5)

    if not set_time(end_hour, end_minute):
        raise RuntimeError("Could not set and confirm the end time")

    settle(0.5)

    if not click_first(
        {"description": "Save", "clickable": True},
        {"text": "Save", "clickable": True},
        {"hint": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
    ):
        raise RuntimeError("Could not find the Save button")

    settle(1.0)
    return True
