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

    def dismiss_disclaimer():
        ok_idx = device.find(text="OK", clickable=True)
        settings_idx = device.find(text="Settings", clickable=True)
        if ok_idx is not None and settings_idx is not None:
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
        dismiss_disclaimer()
        settle(1)
        tried = set()
        for _ in range(10):
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
            if not candidates:
                raise RuntimeError("Could not find New Event button")
            idx = candidates[0]
            tried.add(idx)
            device.click(index=idx)
            settle(1)
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
        start_date_idx = get_nth_clickable_after("Description", 1)
        device.click(index=start_date_idx)
        settle(1)

        target_month_name = calendar.month_name[month]
        target_month_abbr = calendar.month_abbr[month]
        day_str = str(day)

        for _ in range(60):
            elements = device.elements()
            day_idx = None
            for e in elements:
                t = e.get("text") or ""
                d = e.get("description") or ""
                if t == day_str or d.startswith(day_str + " ") or d.startswith(day_str + ","):
                    if target_month_name in d or target_month_abbr in d:
                        day_idx = e["index"]
                        break
            if day_idx is None:
                header = get_header_month_year(elements)
                if header is not None and header[0] == month and header[1] == year:
                    for e in elements:
                        t = e.get("text") or ""
                        d = e.get("description") or ""
                        if t == day_str or d.startswith(day_str + " ") or d.startswith(day_str + ","):
                            day_idx = e["index"]
                            break
            if day_idx is not None:
                device.click(index=day_idx)
                settle(1)
                click_ok()
                return

            header = get_header_month_year(elements)
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

    def set_time_picker(hour, minute):
        hour_str = str(hour)
        hour_padded = f"{hour:02d}"
        idx = device.find(text=hour_str)
        if idx is None:
            idx = device.find(text=hour_padded)
        if idx is None:
            idx = device.find(description=hour_str)
        if idx is None:
            idx = device.find(description=hour_padded)
        if idx is None:
            for e in device.elements():
                if e.get("text") in (hour_str, hour_padded) or e.get("description") in (hour_str, hour_padded):
                    idx = e["index"]
                    break
        if idx is None:
            raise RuntimeError(f"Hour {hour} not found in time picker")
        device.click(index=idx)
        settle(0.5)

        minute_str = str(minute)
        minute_padded = f"{minute:02d}"
        idx = device.find(text=minute_str)
        if idx is None:
            idx = device.find(text=minute_padded)
        if idx is None:
            idx = device.find(description=minute_str)
        if idx is None:
            idx = device.find(description=minute_padded)
        if idx is None:
            for e in device.elements():
                if e.get("text") in (minute_str, minute_padded) or e.get("description") in (minute_str, minute_padded):
                    idx = e["index"]
                    break
        if idx is None:
            raise RuntimeError(f"Minute {minute} not found in time picker")
        device.click(index=idx)
        settle(0.5)

        click_ok()

    device.open_app("Simple Calendar Pro")
    settle(2)

    dismiss_disclaimer()
    settle(1)

    open_new_event()

    set_text("Title", binding["event_title"])
    set_text("Description", binding["event_description"])

    set_date(binding["year"], binding["month"], binding["day"])

    start_time_idx = get_nth_clickable_after("Description", 2)
    device.click(index=start_time_idx)
    settle(1)
    set_time_picker(binding["hour"], 0)

    total_minutes = binding["hour"] * 60 + binding["duration_mins"]
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60
    end_time_idx = get_nth_clickable_after("Description", 3)
    device.click(index=end_time_idx)
    settle(1)
    set_time_picker(end_hour, end_minute)

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
