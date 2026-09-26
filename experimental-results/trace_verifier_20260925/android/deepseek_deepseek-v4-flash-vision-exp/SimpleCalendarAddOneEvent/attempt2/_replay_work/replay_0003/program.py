import calendar
import datetime
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
        elements = device.elements()
        for e in elements:
            if e.get("clickable"):
                if e.get("text") == target or e.get("description") == target:
                    return e["index"]
        return None

    def find_clickable_contains(substr):
        elements = device.elements()
        for e in elements:
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
            patterns = ["New Event", "Add Event", "New event", "+"]
            for p in patterns:
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
            idx = device.find(description="New event", clickable=True)
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

    def get_header_month_year(elements=None):
        if elements is None:
            elements = device.elements()
        for e in elements:
            t = e.get("text") or ""
            m = re.search(r'([A-Za-z]+)\s+(\d{4})', t)
            if m:
                month_str = m.group(1)
                year = int(m.group(2))
                for i in range(1, 13):
                    if calendar.month_name[i].lower().startswith(month_str.lower()[:3]):
                        return (i, year)
                for i in range(1, 13):
                    if calendar.month_abbr[i].lower() == month_str.lower():
                        return (i, year)
            m = re.search(r'(\d{1,2})[\/\-](\d{4})', t)
            if m:
                return (int(m.group(1)), int(m.group(2)))
            m = re.search(r'(\d{4})[\/\-](\d{1,2})', t)
            if m:
                return (int(m.group(2)), int(m.group(1)))
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
            if "Next" in direction:
                for e in device.elements():
                    if e.get("clickable") and ("Next" in (e.get("text") or "") or "Next" in (e.get("description") or "")):
                        btn = e["index"]
                        break
            else:
                for e in device.elements():
                    if e.get("clickable") and ("Previous" in (e.get("text") or "") or "Previous" in (e.get("description") or "")):
                        btn = e["index"]
                        break
        if btn is None:
            raise RuntimeError(f"Could not find navigation button for {direction}")
        return btn

    def set_date(year, month, day):
        dismiss_disclaimer_if_present()
        start_date_idx = get_nth_clickable_after("Description", 1)
        device.click(index=start_date_idx)
        settle(1)

        target_month_name = calendar.month_name[month]
        target_month_abbr = calendar.month_abbr[month]
        day_str = str(day)

        for _ in range(60):
            elements = device.elements()
            header = get_header_month_year(elements)
            if header is not None and header[0] == month and header[1] == year:
                day_idx = None
                for e in elements:
                    t = e.get("text") or ""
                    if t == day_str:
                        day_idx = e["index"]
                        break
                if day_idx is None:
                    for e in elements:
                        d = e.get("description") or ""
                        if d == day_str or d.startswith(day_str + " ") or d.startswith(day_str + ","):
                            day_idx = e["index"]
                            break
                if day_idx is not None:
                    device.click(index=day_idx)
                    settle(1)
                    click_ok()
                    return
                raise RuntimeError(f"Day {day} not found in date picker for {year}-{month}")
            else:
                if header is None:
                    if (year, month) < (2023, 10):
                        direction = "Previous month"
                    else:
                        direction = "Next month"
                else:
                    curr_month, curr_year = header
                    if (year, month) < (curr_year, curr_month):
                        direction = "Previous month"
                    elif (year, month) > (curr_year, curr_month):
                        direction = "Next month"
                    else:
                        direction = "Next month"
                btn = find_nav_button(direction)
                device.click(index=btn)
                settle(1)

        raise RuntimeError(f"Could not set date to {year}-{month}-{day}")

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

    def find_exact_number(num):
        candidates = [str(num), f"{num:02d}"]
        for c in candidates:
            idx = device.find(text=c, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(description=c, clickable=True)
            if idx is not None:
                return idx
        for c in candidates:
            idx = device.find(text=c)
            if idx is not None:
                return idx
            idx = device.find(description=c)
            if idx is not None:
                return idx
        for e in device.elements():
            t = e.get("text") or ""
            d = e.get("description") or ""
            for c in candidates:
                if t == c or d == c:
                    return e["index"]
                if d.startswith(c + " ") or d.endswith(" " + c):
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
        return None

    def set_time_picker(hour, minute):
        dismiss_disclaimer_if_present()
        hour_idx = find_exact_number(hour)
        if hour_idx is not None:
            device.click(index=hour_idx)
            settle(1)
            minute_idx = find_exact_number(minute)
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
            settle(1)
            minute_idx = find_exact_number(minute)
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

    device.open_app("Simple Calendar Pro")
    settle(2)

    dismiss_disclaimer_if_present()
    settle(1)
    dismiss_disclaimer_if_present()

    open_new_event()

    dismiss_disclaimer_if_present()

    set_text("Title", binding["event_title"])
    set_text("Description", binding["event_description"])

    # Close the keyboard so that the clickable rows after Description are visible
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
