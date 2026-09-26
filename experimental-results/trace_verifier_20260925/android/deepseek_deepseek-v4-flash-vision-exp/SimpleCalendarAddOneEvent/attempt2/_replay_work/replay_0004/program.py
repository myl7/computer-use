import calendar
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    def settle(sec=2):
        device.settle(sec)

    def month_name_to_num(name):
        if not name:
            return None
        name = name.strip().lower()
        for i in range(1, 13):
            if calendar.month_name[i].lower() == name:
                return i
            if calendar.month_abbr[i].lower() == name:
                return i
        if len(name) >= 3:
            for i in range(1, 13):
                if calendar.month_name[i].lower().startswith(name[:3]):
                    return i
        return None

    def dismiss_disclaimer_if_present():
        if device.find(text="Disclaimer") is not None:
            ok_idx = device.find(text="OK", clickable=True)
            if ok_idx is None:
                for e in device.elements():
                    if e.get("text") == "OK":
                        ok_idx = e["index"]
                        break
            if ok_idx is not None:
                device.click(index=ok_idx)
                settle(1)
                return True
        return False

    def find_clickable_by_text_or_desc(target):
        for e in device.elements():
            if e.get("clickable") and (e.get("text") == target or e.get("description") == target):
                return e["index"]
        return None

    def find_clickable_contains(substr):
        for e in device.elements():
            if e.get("clickable"):
                t = (e.get("text") or "").lower()
                d = (e.get("description") or "").lower()
                if substr.lower() in t or substr.lower() in d:
                    return e["index"]
        return None

    def open_new_event():
        dismiss_disclaimer_if_present()
        settle(1)
        tried = set()
        for _ in range(10):
            dismiss_disclaimer_if_present()
            settle(0.5)
            candidates = []
            for p in ["New Event", "Add Event", "New event", "+"]:
                idx = device.find(description=p, clickable=True)
                if idx is None:
                    idx = device.find(text=p, clickable=True)
                if idx is None:
                    idx = find_clickable_by_text_or_desc(p)
                if idx is not None and idx not in tried:
                    candidates.append(idx)
            idx = find_clickable_contains("New Event")
            if idx is not None and idx not in tried:
                candidates.append(idx)
            idx = find_clickable_contains("Add Event")
            if idx is not None and idx not in tried:
                candidates.append(idx)
            if not candidates:
                for e in device.elements():
                    if e.get("clickable"):
                        t = (e.get("text") or "").lower()
                        d = (e.get("description") or "").lower()
                        if "+" in t or "add" in t or "new" in t:
                            if e["index"] not in tried:
                                candidates.append(e["index"])
            if not candidates:
                raise RuntimeError("Could not find New Event button")
            idx = candidates[0]
            tried.add(idx)
            device.click(index=idx)
            settle(1)
            dismiss_disclaimer_if_present()
            if device.find(hint="Title", editable=True) is not None:
                return True
        raise RuntimeError("Could not open new event screen")

    def set_text(hint, text):
        idx = device.find(hint=hint, editable=True)
        if idx is None:
            for e in device.elements():
                if e.get("editable") and e.get("hint") == hint:
                    idx = e["index"]
                    break
        if idx is None:
            raise RuntimeError(f"Field with hint '{hint}' not found")
        device.click(index=idx)
        settle(0.5)
        device.input_text(text=text, index=idx)
        settle(0.5)

    def get_nth_clickable_after(hint, n=1):
        elements = device.elements()
        anchor = None
        for e in elements:
            if e.get("hint") == hint:
                anchor = e
                break
        if anchor is None:
            for e in elements:
                if e.get("text") == hint or e.get("description") == hint:
                    anchor = e
                    break
        if anchor is None:
            raise RuntimeError(f"Anchor with hint '{hint}' not found")
        anchor_idx = anchor["index"]
        clickables = [e for e in elements if e.get("index") > anchor_idx and e.get("clickable")]
        clickables.sort(key=lambda x: x["index"])
        if len(clickables) < n:
            raise RuntimeError(f"Only found {len(clickables)} clickable elements after {hint}, needed {n}")
        return clickables[n - 1]["index"]

    def click_ok():
        ok_idx = device.find(text="OK", clickable=True)
        if ok_idx is None:
            ok_idx = device.find(text="OK")
        if ok_idx is None:
            for e in device.elements():
                if e.get("text") == "OK":
                    ok_idx = e["index"]
                    break
        if ok_idx is None:
            raise RuntimeError("OK button not found")
        device.click(index=ok_idx)
        settle(1)

    # --- date picker -------------------------------------------------------
    def get_displayed_month_year(elements):
        # The picker's day cells carry descriptions like "19 October 2023",
        # which is the most reliable source for the displayed month/year.
        counts = {}
        for e in elements:
            d = e.get("description") or ""
            m = re.search(r'(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})', d)
            if m:
                mn = month_name_to_num(m.group(2))
                if mn is not None:
                    key = (int(m.group(3)), mn)
                    counts[key] = counts.get(key, 0) + 1
        if counts:
            return max(counts.items(), key=lambda kv: kv[1])[0]
        # Fallback: header text like "October 2023"
        for e in elements:
            t = e.get("text") or ""
            m = re.search(r'([A-Za-z]{3,9})\s+(\d{4})', t)
            if m:
                mn = month_name_to_num(m.group(1))
                if mn is not None:
                    return (int(m.group(2)), mn)
        for e in elements:
            t = e.get("text") or ""
            m = re.search(r'(\d{1,2})[\/\-](\d{4})', t)
            if m:
                return (int(m.group(2)), int(m.group(1)))
            m = re.search(r'(\d{4})[\/\-](\d{1,2})', t)
            if m:
                return (int(m.group(1)), int(m.group(2)))
        return None

    def find_nav_button(direction):
        btn = device.find(description=direction, clickable=True)
        if btn is None:
            btn = device.find(text=direction, clickable=True)
        if btn is None:
            for e in device.elements():
                if e.get("clickable"):
                    t = e.get("text") or ""
                    d = e.get("description") or ""
                    if direction.lower() in t.lower() or direction.lower() in d.lower():
                        btn = e["index"]
                        break
        if btn is None:
            core = "Next" if "Next" in direction else "Previous"
            for e in device.elements():
                if e.get("clickable"):
                    t = e.get("text") or ""
                    d = e.get("description") or ""
                    if core in t or core in d:
                        btn = e["index"]
                        break
        if btn is None:
            raise RuntimeError(f"Could not find navigation button for {direction}")
        return btn

    def find_day_cell(elements, year, month, day):
        mname = calendar.month_name[month]
        target1 = f"{day:02d} {mname} {year}"
        target2 = f"{day} {mname} {year}"
        for e in elements:
            d = (e.get("description") or "")
            if d.startswith(target1) or d.startswith(target2):
                return e["index"]
        for e in elements:
            d = (e.get("description") or "")
            if mname.lower() in d.lower() and str(year) in d:
                m = re.search(r'(\d{1,2})', d)
                if m and int(m.group(1)) == day:
                    return e["index"]
        for e in elements:
            if (e.get("text") or "").strip() == str(day):
                return e["index"]
        return None

    def set_date(year, month, day):
        dismiss_disclaimer_if_present()
        start_date_idx = get_nth_clickable_after("Description", 1)
        device.click(index=start_date_idx)
        settle(1)

        for _ in range(72):
            elements = device.elements()
            cur = get_displayed_month_year(elements)
            if cur == (year, month):
                day_idx = find_day_cell(elements, year, month, day)
                if day_idx is None:
                    raise RuntimeError(f"Day {day} not found in date picker for {year}-{month}")
                device.click(index=day_idx)
                settle(1)
                click_ok()
                return
            if cur is None:
                raise RuntimeError("Could not determine the displayed month in date picker")
            if (year, month) < cur:
                direction = "Previous month"
            else:
                direction = "Next month"
            btn = find_nav_button(direction)
            device.click(index=btn)
            settle(1)

        raise RuntimeError(f"Could not set date to {year}-{month}-{day}")

    # --- time picker -------------------------------------------------------
    def clear_text_field(idx):
        device.click(index=idx)
        settle(0.5)
        device.adb_shell("input", "keyevent", "123")  # MOVE_END
        for _ in range(20):
            device.adb_shell("input", "keyevent", "67")  # DEL
        settle(0.5)

    def set_input_time(hour, minute):
        hour_field = device.find(hint="Hour", editable=True)
        if hour_field is None:
            for e in device.elements():
                if e.get("editable"):
                    h = (e.get("hint") or "").lower()
                    d = (e.get("description") or "").lower()
                    if "hour" in h or "hour" in d:
                        hour_field = e["index"]
                        break
        if hour_field is None:
            raise RuntimeError("Hour input field not found")
        clear_text_field(hour_field)
        device.input_text(text=f"{hour:02d}", index=hour_field)
        settle(0.5)

        minute_field = device.find(hint="Minute", editable=True)
        if minute_field is None:
            for e in device.elements():
                if e.get("editable"):
                    h = (e.get("hint") or "").lower()
                    d = (e.get("description") or "").lower()
                    if "minute" in h or "minute" in d:
                        minute_field = e["index"]
                        break
        if minute_field is None:
            raise RuntimeError("Minute input field not found")
        clear_text_field(minute_field)
        device.input_text(text=f"{minute:02d}", index=minute_field)
        settle(0.5)

        click_ok()

    def _leading_num(s):
        m = re.match(r'\s*(\d{1,2})(?:\D|$)', s or "")
        if m:
            return int(m.group(1))
        return None

    def find_time_number(num, unit):
        elements = device.elements()
        cands = {str(num), f"{num:02d}"}
        # exact text on a clickable node
        for e in elements:
            if e.get("clickable") and (e.get("text") or "").strip() in cands:
                return e["index"]
        # clickable description like "18 hours" / "15 minutes"
        for e in elements:
            d = e.get("description") or ""
            if e.get("clickable") and unit in d.lower() and _leading_num(d) == num:
                return e["index"]
        # exact text anywhere
        for e in elements:
            if (e.get("text") or "").strip() in cands:
                return e["index"]
        # exact description anywhere
        for e in elements:
            if (e.get("description") or "").strip() in cands:
                return e["index"]
        # description carrying the unit
        for e in elements:
            d = e.get("description") or ""
            if unit in d.lower() and _leading_num(d) == num:
                return e["index"]
        # loose text / description starting with the number
        for e in elements:
            t = e.get("text") or ""
            if _leading_num(t) == num and not re.search(r'\d{4}', t):
                return e["index"]
        for e in elements:
            d = e.get("description") or ""
            if _leading_num(d) == num and not re.search(r'\d{4}', d):
                return e["index"]
        return None

    def find_edit_icon():
        for e in device.elements():
            d = (e.get("description") or "").lower()
            if d in ("edit", "switch to text input mode", "input mode", "keyboard"):
                return e["index"]
            t = (e.get("text") or "").lower()
            if t in ("edit", "switch to text input mode", "input mode", "keyboard"):
                return e["index"]
        for e in device.elements():
            d = (e.get("description") or "").lower()
            t = (e.get("text") or "").lower()
            if "text input" in d or "input mode" in d or "keyboard" in d or \
               "text input" in t or "input mode" in t or "keyboard" in t:
                return e["index"]
        return None

    def set_time_picker(hour, minute):
        dismiss_disclaimer_if_present()
        hour_idx = find_time_number(hour, "hour")
        if hour_idx is not None:
            device.click(index=hour_idx)
            settle(1)
            minute_idx = find_time_number(minute, "minute")
            if minute_idx is not None:
                device.click(index=minute_idx)
                settle(0.5)
                click_ok()
                return
            edit_idx = find_edit_icon()
            if edit_idx is not None:
                device.click(index=edit_idx)
                settle(1)
                set_input_time(hour, minute)
                return
            # minute might not be on the clock face; retry after a short wait
            settle(1)
            minute_idx = find_time_number(minute, "minute")
            if minute_idx is not None:
                device.click(index=minute_idx)
                settle(0.5)
                click_ok()
                return
            raise RuntimeError(f"Minute {minute} not found in time picker after selecting hour {hour}")
        else:
            edit_idx = find_edit_icon()
            if edit_idx is not None:
                device.click(index=edit_idx)
                settle(1)
                set_input_time(hour, minute)
                return
            hour_field = device.find(hint="Hour", editable=True)
            if hour_field is not None:
                set_input_time(hour, minute)
                return
            raise RuntimeError(f"Hour {hour} not found in time picker")

    # --- main flow ---------------------------------------------------------
    device.open_app("Simple Calendar Pro")
    settle(2)

    dismiss_disclaimer_if_present()
    settle(1)
    dismiss_disclaimer_if_present()

    open_new_event()

    dismiss_disclaimer_if_present()

    set_text("Title", binding["event_title"])
    set_text("Description", binding["event_description"])

    # Close the keyboard so the clickable rows after Description are visible
    device.navigate_back()
    settle(1)

    dismiss_disclaimer_if_present()

    set_date(binding["year"], binding["month"], binding["day"])

    # Start time
    dismiss_disclaimer_if_present()
    start_time_idx = get_nth_clickable_after("Description", 2)
    device.click(index=start_time_idx)
    settle(1)
    set_time_picker(binding["hour"], 0)

    # End time = start time + duration
    total_minutes = binding["hour"] * 60 + binding["duration_mins"]
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60

    dismiss_disclaimer_if_present()
    end_time_idx = get_nth_clickable_after("Description", 3)
    device.click(index=end_time_idx)
    settle(1)
    set_time_picker(end_hour, end_minute)

    # Save
    dismiss_disclaimer_if_present()
    save_idx = device.find(description="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(text="Save", clickable=True)
    if save_idx is None:
        for e in device.elements():
            if e.get("clickable") and (e.get("text") == "Save" or e.get("description") == "Save"):
                save_idx = e["index"]
                break
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    settle(2)

    return True
