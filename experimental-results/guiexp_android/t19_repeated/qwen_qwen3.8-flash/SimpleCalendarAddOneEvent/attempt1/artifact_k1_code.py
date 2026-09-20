PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "description": "Event month, 1-12"},
    "day": {"type": "integer", "description": "Event day of month"},
    "hour": {"type": "integer", "description": "Start hour in 24-hour clock, 0-23"},
    "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    import datetime
    import re

    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)

    MONTH_NAMES = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    MONTH_ABBREVS = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    DEFAULT_YEAR = 2023

    def settle(seconds=1.0):
        device.settle(seconds)

    def els():
        return device.elements()

    def get_el(index):
        for el in els():
            if el.get("index") == index:
                return el
        return None

    def get_text(index):
        el = get_el(index)
        return str(el.get("text") or "") if el else ""

    def get_desc(index):
        el = get_el(index)
        return str(el.get("description") or "") if el else ""

    def parse_month_year(text):
        if text is None:
            return None
        s = str(text)
        if not s:
            return None

        m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", s)
        if m:
            mm = int(m.group(1))
            yy = int(m.group(3))
            if 1 <= mm <= 12:
                return (yy, mm)

        m = re.search(r"\b(\d{4})[/-](\d{1,2})\b", s)
        if m:
            yy = int(m.group(1))
            mm = int(m.group(2))
            if 1 <= mm <= 12:
                return (yy, mm)

        m = re.search(r"\b(\d{1,2})[/-](\d{4})\b", s)
        if m:
            mm = int(m.group(1))
            yy = int(m.group(2))
            if 1 <= mm <= 12:
                return (yy, mm)

        low = s.lower()
        found_month = None
        for i, name in enumerate(MONTH_NAMES, 1):
            if re.search(r"\b" + re.escape(name.lower()) + r"\b", low):
                found_month = i
                break
        if found_month is None:
            for i, name in enumerate(MONTH_ABBREVS, 1):
                if re.search(r"\b" + re.escape(name.lower()) + r"\b", low):
                    found_month = i
                    break

        if found_month is None:
            return None

        ym = re.search(r"\b(19|20)\d{2}\b", s)
        if ym:
            return (int(ym.group(0)), found_month)
        return (None, found_month)

    def get_current_month_year():
        elements = els()
        for el in elements:
            for key in ("text", "description", "hint"):
                parsed = parse_month_year(el.get(key))
                if parsed and parsed[0] is not None:
                    return parsed
        for el in elements:
            for key in ("text", "description", "hint"):
                parsed = parse_month_year(el.get(key))
                if parsed:
                    return (DEFAULT_YEAR, parsed[1])
        return None

    def find_arrow(direction):
        if direction == "prev":
            exact_desc = ["Previous month", "Previous year", "Previous", "Back"]
            keys = ["previous", "prev", "back", "left", "<", "◀", "←"]
        else:
            exact_desc = ["Next month", "Next year", "Next", "Forward"]
            keys = ["next", "forward", "right", ">", "▶", "→"]

        for d in exact_desc:
            idx = device.find(description=d, clickable=True)
            if idx is not None:
                return idx

        for el in els():
            if not el.get("clickable"):
                continue
            combined = " ".join([
                str(el.get("description") or ""),
                str(el.get("text") or ""),
                str(el.get("hint") or ""),
            ]).lower()
            if direction == "prev" and any(k in combined for k in keys):
                if "next" in combined:
                    continue
                return el.get("index")
            if direction == "next" and any(k in combined for k in keys):
                if "previous" in combined:
                    continue
                return el.get("index")
        return None

    def try_select_year(target_year):
        header_idx = None
        for el in els():
            if el.get("clickable") and parse_month_year(el.get("text") or ""):
                header_idx = el.get("index")
                break
        if header_idx is None:
            for el in els():
                if parse_month_year(el.get("text") or ""):
                    header_idx = el.get("index")
                    break
        if header_idx is None:
            return False

        device.click(index=header_idx)
        settle(0.5)

        y_idx = device.find(text=str(target_year), clickable=True)
        if y_idx is None:
            y_idx = device.find(description=str(target_year), clickable=True)
        if y_idx is None:
            for el in els():
                if el.get("clickable") and str(el.get("text") or "").strip() == str(target_year):
                    y_idx = el.get("index")
                    break
        if y_idx is not None:
            device.click(index=y_idx)
            settle(0.5)
            return True

        cur_year = None
        for el in els():
            m = re.search(r"\b(19|20)\d{2}\b", str(el.get("text") or ""))
            if m:
                cur_year = int(m.group(0))
                break
        if cur_year is None:
            return False

        for _ in range(120):
            if cur_year == target_year:
                break
            direction = "prev" if cur_year > target_year else "next"
            idx = find_arrow(direction)
            if idx is None:
                return False
            device.click(index=idx)
            settle(0.5)
            cur_year += -1 if direction == "prev" else 1

        y_idx = device.find(text=str(target_year), clickable=True)
        if y_idx is not None:
            device.click(index=y_idx)
            settle(0.5)
            return True
        return True

    def navigate_to_month(target_year, target_month, max_steps=400):
        target = (target_year, target_month)
        tried_year = False
        for step in range(max_steps):
            cur = get_current_month_year()
            if cur is None:
                return False
            if cur == target:
                return True

            if not tried_year and step >= 12:
                if try_select_year(target_year):
                    tried_year = True
                    continue

            direction = "prev" if cur > target else "next"
            idx = find_arrow(direction)
            if idx is None:
                if not tried_year and try_select_year(target_year):
                    tried_year = True
                    continue
                return False

            device.click(index=idx)
            settle(0.5)
        return False

    def get_bounds(el):
        b = el.get("bounds")
        if isinstance(b, str):
            nums = re.findall(r"\d+", b)
            if len(nums) >= 4:
                return tuple(int(x) for x in nums[:4])
        elif isinstance(b, dict):
            x1 = b.get("left", b.get("x1"))
            y1 = b.get("top", b.get("y1"))
            x2 = b.get("right", b.get("x2"))
            y2 = b.get("bottom", b.get("y2"))
            if all(v is not None for v in (x1, y1, x2, y2)):
                return (int(x1), int(y1), int(x2), int(y2))
        elif isinstance(b, (list, tuple)) and len(b) >= 4:
            try:
                return tuple(int(x) for x in b[:4])
            except Exception:
                return None
        return None

    def click_day(target_day, target_month=None, require_unique=False):
        day_str = str(target_day)
        cands = []
        for el in els():
            if not el.get("clickable"):
                continue
            txt = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            if txt == day_str or desc == day_str or hint == day_str:
                bounds = get_bounds(el)
                y = bounds[1] if bounds else el.get("index", 0)
                x = bounds[0] if bounds else el.get("index", 0)
                cands.append({"index": el.get("index"), "y": y, "x": x, "desc": desc})

        if cands:
            if target_month:
                terms = [MONTH_NAMES[target_month - 1].lower(), MONTH_ABBREVS[target_month - 1].lower()]
                filtered = [c for c in cands if any(t in str(c.get("desc") or "").lower() for t in terms)]
                if filtered:
                    cands = filtered

            if require_unique and len(cands) > 1:
                return False

            cands.sort(key=lambda c: (c["y"], c["x"]))
            if len(cands) == 1:
                idx = cands[0]["index"]
            elif len(cands) == 2:
                idx = cands[1]["index"] if (target_day is not None and target_day <= 7) else cands[0]["index"]
            else:
                idx = cands[len(cands) // 2]["index"]

            if idx is None:
                return False
            device.click(index=idx)
            settle(0.5)
            return True

        idx = device.find(text=day_str, clickable=True)
        if idx is None:
            idx = device.find(description=day_str, clickable=True)
        if idx is None:
            idx = device.find(hint=day_str, clickable=True)
        if idx is not None:
            device.click(index=idx)
            settle(0.5)
            return True

        if not require_unique:
            idx = device.find(contains=day_str, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
            idx = device.find(text=day_str)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        return False

    def click_ok():
        for label in ("OK", "Ok", "Okay", "Done"):
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        for label in ("OK", "Ok", "Okay", "Done"):
            idx = device.find(description=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        for el in els():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip().lower()
            desc = str(el.get("description") or "").strip().lower()
            if text in ("ok", "okay", "done") or desc in ("ok", "okay", "done"):
                device.click(index=el.get("index"))
                settle(0.5)
                return True
        return False

    def click_cancel():
        for label in ("Cancel", "Close"):
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        for label in ("Cancel", "Close"):
            idx = device.find(description=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        device.navigate_back()
        settle(0.5)
        return True

    def close_keyboard_if_open():
        for el in els():
            cls = str(el.get("class_name") or "").lower()
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "keyboard" in cls or "keyboard" in desc or "hide keyboard" in desc or text == "hide keyboard":
                device.navigate_back()
                settle(0.5)
                return True
        return False

    def find_title_field():
        idx = device.find(hint="Title", editable=True)
        if idx is None:
            idx = device.find(text="Title", editable=True)
        if idx is None:
            idx = device.find(description="Title", editable=True)
        if idx is None:
            for el in els():
                if not el.get("editable"):
                    continue
                hint = str(el.get("hint") or "").lower()
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").lower()
                if "title" in hint or "title" in desc or "title" in text:
                    return el.get("index")
            for el in els():
                if el.get("editable"):
                    return el.get("index")
        return idx

    def find_description_field():
        idx = device.find(hint="Description", editable=True)
        if idx is None:
            idx = device.find(text="Description", editable=True)
        if idx is None:
            idx = device.find(description="Description", editable=True)
        if idx is None:
            for el in els():
                if not el.get("editable"):
                    continue
                hint = str(el.get("hint") or "").lower()
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").lower()
                if "description" in hint or "description" in desc or "description" in text:
                    return el.get("index")
            editables = [el.get("index") for el in els() if el.get("editable")]
            if len(editables) >= 2:
                return editables[1]
        return idx

    def create_event():
        idx = None
        for desc in ("New Event", "Add event", "New event", "Create event", "Add Event"):
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                break
        if idx is None:
            for el in els():
                if el.get("clickable") and "event" in str(el.get("description") or "").lower():
                    idx = el.get("index")
                    break
        if idx is None:
            idx = device.find(text="+", clickable=True)
        if idx is None:
            idx = device.find(text="Add", clickable=True)
        if idx is None:
            raise RuntimeError("Could not find New Event button")

        device.click(index=idx)
        settle(1.0)

        event_idx = None
        for text in ("Event", "New event", "Add event"):
            event_idx = device.find(text=text, clickable=True)
            if event_idx is not None:
                break
        if event_idx is None:
            for desc in ("Event", "New event", "Add event"):
                event_idx = device.find(description=desc, clickable=True)
                if event_idx is not None:
                    break

        title_idx = find_title_field()
        if event_idx is not None and title_idx is None:
            device.click(index=event_idx)
            settle(1.0)
            title_idx = find_title_field()

        if title_idx is None:
            device.scroll("up")
            settle(0.5)
            title_idx = find_title_field()

        if title_idx is None and event_idx is not None:
            device.click(index=event_idx)
            settle(1.0)
            title_idx = find_title_field()

        if title_idx is None:
            raise RuntimeError("Could not open event editor")

    def is_date_text(text):
        s = str(text or "")
        if not s or ":" in s:
            return False
        low = s.lower()
        for name in MONTH_NAMES + MONTH_ABBREVS:
            if re.search(r"\b" + re.escape(name.lower()) + r"\b", low):
                return True
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", s):
            return True
        if re.search(r"\b\d{1,2}\b.*\b(19|20)\d{2}\b", s):
            return True
        return False

    def find_date_rows():
        rows = []
        seen = set()
        for el in els():
            if not el.get("clickable"):
                continue
            txt = el.get("text")
            desc = el.get("description")
            hint = el.get("hint")
            if is_date_text(txt) or is_date_text(desc) or is_date_text(hint):
                idx = el.get("index")
                if idx not in seen:
                    rows.append(idx)
                    seen.add(idx)

        if not rows:
            for el in els():
                if el.get("clickable") and "date" in str(el.get("description") or "").lower():
                    idx = el.get("index")
                    if idx not in seen:
                        rows.append(idx)
                        seen.add(idx)
        return rows

    def date_row_contains_target(index, target_year, target_month, target_day):
        combined = (get_text(index) + " " + get_desc(index)).lower()
        if str(target_day) not in combined:
            return False
        terms = [MONTH_NAMES[target_month - 1].lower(), MONTH_ABBREVS[target_month - 1].lower()]
        return any(t in combined for t in terms)

    def has_clickable_day_cell():
        for el in els():
            if not el.get("clickable"):
                continue
            txt = str(el.get("text") or "").strip()
            if txt.isdigit():
                try:
                    v = int(txt)
                    if 1 <= v <= 31:
                        return True
                except Exception:
                    pass
        return False

    def set_date_row(position, target_year, target_month, target_day):
        rows = find_date_rows()
        idx = None
        if position < len(rows):
            idx = rows[position]
        else:
            labels = []
            if position == 0:
                labels = ["Start date", "Start Date", "Starts at", "Start"]
            elif position == 1:
                labels = ["End date", "End Date", "Ends at", "End"]
            for label in labels:
                idx = device.find(description=label, clickable=True)
                if idx is None:
                    idx = device.find(text=label, clickable=True)
                if idx is not None:
                    break

        if idx is None:
            return False

        device.click(index=idx)
        settle(1.0)

        has_ok = device.find(text="OK", clickable=True) is not None or device.find(description="OK", clickable=True) is not None
        if not has_ok and not has_clickable_day_cell():
            return False

        navigated = navigate_to_month(target_year, target_month, 400)
        if not navigated:
            if try_select_year(target_year):
                navigated = navigate_to_month(target_year, target_month, 400)

        if not navigated:
            day_str = str(target_day)
            visible = any(
                el.get("clickable") and str(el.get("text") or "").strip() == day_str
                for el in els()
            )
            if not visible:
                click_cancel()
                return False

        if not click_day(target_day, target_month, require_unique=False):
            click_cancel()
            return False

        if not click_ok():
            click_cancel()
            return False
        return True

    def click_time_field_by_suffix(suffix):
        for el in els():
            if el.get("clickable") and str(el.get("description") or "").lower().endswith(suffix):
                device.click(index=el.get("index"))
                settle(0.5)
                return True
        return False

    def click_time_option(value, kind):
        if kind == "hour":
            descs = [f"{value} hours", f"{value:02d} hours"]
            texts = {str(value), f"{value:02d}"}
            suffix = "hours"
        else:
            descs = [f"{value} minutes", f"{value:02d} minutes"]
            texts = {str(value), f"{value:02d}"}
            suffix = "minutes"

        for d in descs:
            idx = device.find(description=d, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True

        for el in els():
            if not el.get("clickable"):
                continue
            txt = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").lower()
            if desc in [d.lower() for d in descs]:
                device.click(index=el.get("index"))
                settle(0.5)
                return True
            if txt in texts:
                if kind == "hour" and desc.endswith("minutes"):
                    continue
                if kind == "minute" and desc.endswith("hours"):
                    continue
                if desc.endswith(suffix) or desc == "" or "select" in desc:
                    device.click(index=el.get("index"))
                    settle(0.5)
                    return True

        for _ in range(3):
            device.scroll("down")
            settle(0.3)
            for d in descs:
                idx = device.find(description=d, clickable=True)
                if idx is not None:
                    device.click(index=idx)
                    settle(0.5)
                    return True

        for _ in range(6):
            device.scroll("up")
            settle(0.3)
            for d in descs:
                idx = device.find(description=d, clickable=True)
                if idx is not None:
                    device.click(index=idx)
                    settle(0.5)
                    return True

        return False

    def set_time_via_keyboard(h, m):
        def find_editable_time_fields():
            hour_idx = None
            minute_idx = None
            for el in els():
                if not el.get("editable"):
                    continue
                hint = str(el.get("hint") or "").lower()
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").lower()
                if "hour" in hint or "hour" in desc or hint == "hh" or text == "hh":
                    hour_idx = el.get("index")
                elif "minute" in hint or "minute" in desc or hint == "mm" or text == "mm":
                    minute_idx = el.get("index")

            if hour_idx is None or minute_idx is None:
                editables = [el.get("index") for el in els() if el.get("editable")]
                if len(editables) >= 2:
                    if hour_idx is None:
                        hour_idx = editables[0]
                    if minute_idx is None:
                        minute_idx = editables[1]
            return hour_idx, minute_idx

        hour_idx, minute_idx = find_editable_time_fields()

        if hour_idx is None or minute_idx is None:
            toggle = None
            for el in els():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "")
                if "keyboard" in desc or "input" in desc or "edit" in desc or text in ("✎", "⌨", "Keyboard", "Input"):
                    toggle = el.get("index")
                    break
            if toggle is not None:
                device.click(index=toggle)
                settle(0.5)
                hour_idx, minute_idx = find_editable_time_fields()

        if hour_idx is None or minute_idx is None:
            return False

        device.input_text(f"{h:02d}", index=hour_idx)
        settle(0.3)
        device.input_text(f"{m:02d}", index=minute_idx)
        settle(0.3)
        return True

    def set_time(h, m):
        click_time_field_by_suffix("hours")
        hour_ok = click_time_option(h, "hour")
        if not hour_ok:
            if not set_time_via_keyboard(h, m):
                raise RuntimeError("Could not set time")
            if not click_ok():
                raise RuntimeError("OK button not found in time picker")
            return

        click_time_field_by_suffix("minutes")
        minute_ok = click_time_option(m, "minute")
        if not minute_ok:
            if not set_time_via_keyboard(h, m):
                raise RuntimeError("Could not set time")

        if not click_ok():
            raise RuntimeError("OK button not found in time picker")

    def find_time_fields():
        fields = []
        for el in els():
            if not el.get("clickable"):
                continue
            txt = str(el.get("text") or "")
            desc = str(el.get("description") or "").lower()
            if ":" in txt and any(ch.isdigit() for ch in txt) and "date" not in desc:
                fields.append(el.get("index"))
        return fields

    def find_time_fields_with_scroll():
        for _ in range(4):
            fields = find_time_fields()
            if len(fields) >= 2:
                return fields
            device.scroll("down")
            settle(0.5)
        return find_time_fields()

    def click_save():
        for desc in ("Save", "Save event", "Save Event"):
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        for text in ("Save",):
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(0.5)
                return True
        for el in els():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "save" in desc or text == "save":
                device.click(index=el.get("index"))
                settle(0.5)
                return True
        return False

    device.open_app("Simple Calendar Pro")
    settle(1.5)

    if get_current_month_year() is None:
        for label in ("Month", "Month view"):
            idx = device.find(text=label, clickable=True)
            if idx is None:
                idx = device.find(description=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                settle(1.0)
                break

    date_set_via_main = False
    if navigate_to_month(year, month, 400):
        date_set_via_main = click_day(day, month, require_unique=True)

    create_event()

    title_idx = find_title_field()
    if title_idx is None:
        raise RuntimeError("Could not find event Title field")
    device.input_text(event_title, index=title_idx)
    settle(0.5)

    desc_idx = find_description_field()
    if desc_idx is None:
        raise RuntimeError("Could not find event Description field")
    device.input_text(event_description, index=desc_idx)
    settle(0.5)
    close_keyboard_if_open()

    start_date_set = False
    if find_date_rows():
        start_date_set = set_date_row(0, year, month, day)

    if not start_date_set and not date_set_via_main:
        raise RuntimeError("Could not set event start date")

    rows = find_date_rows()
    if len(rows) >= 2:
        if not date_row_contains_target(rows[1], end_dt.year, end_dt.month, end_dt.day):
            set_date_row(1, end_dt.year, end_dt.month, end_dt.day)
    elif end_dt.date() != start_dt.date():
        set_date_row(1, end_dt.year, end_dt.month, end_dt.day)

    time_fields = find_time_fields_with_scroll()
    if len(time_fields) < 2:
        raise RuntimeError("Could not find start and end time fields")

    device.click(index=time_fields[0])
    settle(1.0)
    set_time(hour, 0)

    time_fields = find_time_fields_with_scroll()
    if len(time_fields) < 2:
        raise RuntimeError("Could not find end time field")

    device.click(index=time_fields[1])
    settle(1.0)
    set_time(end_dt.hour, end_dt.minute)

    if not click_save():
        raise RuntimeError("Save button not found on event screen")

    settle(1.0)
    return True
