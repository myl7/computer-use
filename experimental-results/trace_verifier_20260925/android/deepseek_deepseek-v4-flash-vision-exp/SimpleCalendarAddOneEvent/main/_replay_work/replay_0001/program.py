import re
import calendar
import datetime

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event duration in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"}
}

def _find_field(device, hint):
    idx = device.find(hint=hint, editable=True)
    if idx is not None:
        return idx
    for e in device.elements():
        if e.get("editable") and (e.get("hint") == hint or e.get("text") == hint or e.get("description") == hint):
            return e["index"]
    return None

def _open_new_event(device):
    for _ in range(3):
        el = device.find(description="New Event", clickable=True)
        if el is not None:
            device.click(index=el)
            device.settle(1.0)
            if _find_field(device, "Title") is not None:
                return
            el2 = device.find(text="New Event", clickable=True)
            if el2 is None:
                el2 = device.find(description="New Event", clickable=True)
            if el2 is not None and el2 != el:
                device.click(index=el2)
                device.settle(1.0)
                if _find_field(device, "Title") is not None:
                    return
        dismissed = False
        for text in ["OK", "Close", "Got it", "Allow", "Next", "Continue", "Dismiss"]:
            d = device.find(text=text, clickable=True)
            if d is not None:
                device.click(index=d)
                device.settle(0.5)
                dismissed = True
                break
        if not dismissed:
            break
    raise RuntimeError("Could not open new event editor")

def _get_time_rows(device):
    rows = []
    for e in device.elements():
        if e.get("clickable") and e.get("text") and re.fullmatch(r"\d{2}:\d{2}", e["text"]):
            rows.append(e["index"])
    return rows

def _select_time_in_picker(device, hour, minute):
    hour_el = device.find(description=f"{hour} hours", clickable=True)
    if hour_el is None:
        hour_el = device.find(text=str(hour), description=f"{hour} hours")
    if hour_el is None:
        hour_el = device.find(text=str(hour), clickable=True)
    if hour_el is None:
        for e in device.elements():
            if e.get("clickable") and e.get("text") == str(hour):
                hour_el = e["index"]
                break
    if hour_el is None:
        raise RuntimeError(f"Hour element {hour} not found in time picker")
    device.click(index=hour_el)
    device.settle(0.5)

    minute_text = f"{minute:02d}"
    minute_el = device.find(description=f"{minute} minutes", clickable=True)
    if minute_el is None:
        minute_el = device.find(text=minute_text, description=f"{minute} minutes")
    if minute_el is None:
        minute_el = device.find(text=minute_text, clickable=True)
    if minute_el is None:
        for e in device.elements():
            if e.get("clickable") and (e.get("text") == minute_text or (e.get("text") == str(minute) and e.get("description") == f"{minute} minutes")):
                minute_el = e["index"]
                break
    if minute_el is None:
        raise RuntimeError(f"Minute element {minute} not found in time picker")
    device.click(index=minute_el)
    device.settle(0.5)

def _set_time_row(device, which, hour, minute):
    rows = _get_time_rows(device)
    if len(rows) < 2:
        raise RuntimeError("Not enough time rows found")
    row = rows[0] if which == "start" else rows[1]
    device.click(index=row)
    device.settle(1.0)
    _select_time_in_picker(device, hour, minute)
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("OK button not found in time picker")
    device.click(index=ok)
    device.settle(1.0)

def _set_start_date(device, year, month, day):
    dt = datetime.date(year, month, day)
    expected_text = f"{calendar.month_name[month]} {day} ({calendar.day_abbr[dt.weekday()]})"
    date_row = device.find(text=expected_text, clickable=True)
    if date_row is None:
        for e in device.elements():
            if e.get("clickable") and e.get("text") and str(day) in e["text"] and calendar.month_name[month] in e["text"]:
                date_row = e["index"]
                break
    if date_row is None:
        raise RuntimeError(f"Start date row not found: {expected_text}")
    device.click(index=date_row)
    device.settle(1.0)

    label = f"{day} {calendar.month_name[month]} {year}"
    day_cell = device.find(text=str(day), description=label)
    if day_cell is None:
        for e in device.elements():
            if e.get("text") == str(day) and label.lower() in (e.get("description") or "").lower():
                day_cell = e["index"]
                break
    if day_cell is None:
        month_year_text = f"{calendar.month_name[month]} {year}"
        header_found = any(e.get("text") and month_year_text.lower() in e["text"].lower() for e in device.elements())
        if not header_found:
            current_year, current_month = 2023, 10
            target_total = year * 12 + (month - 1)
            current_total = current_year * 12 + (current_month - 1)
            delta = target_total - current_total
            if delta > 0:
                for _ in range(delta):
                    next_btn = device.find(description="Next month")
                    if next_btn is None:
                        next_btn = device.find(text=">", clickable=True)
                    if next_btn is None:
                        raise RuntimeError("Next month button not found in date picker")
                    device.click(index=next_btn)
                    device.settle(0.5)
            elif delta < 0:
                for _ in range(-delta):
                    prev_btn = device.find(description="Previous month")
                    if prev_btn is None:
                        prev_btn = device.find(text="<", clickable=True)
                    if prev_btn is None:
                        raise RuntimeError("Previous month button not found in date picker")
                    device.click(index=prev_btn)
                    device.settle(0.5)
            day_cell = device.find(text=str(day), description=label)
            if day_cell is None:
                for e in device.elements():
                    if e.get("text") == str(day) and label.lower() in (e.get("description") or "").lower():
                        day_cell = e["index"]
                        break
    if day_cell is None:
        day_cell = device.find(text=str(day), clickable=True)
    if day_cell is None:
        raise RuntimeError(f"Day cell {day} not found in date picker")
    device.click(index=day_cell)
    device.settle(1.0)

    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("OK button not found in date picker")
    device.click(index=ok)
    device.settle(1.0)

def program(device, binding: dict) -> bool:
    device.open_app("Simple Calendar Pro")
    device.settle(1.0)

    _open_new_event(device)

    title_field = _find_field(device, "Title")
    if title_field is None:
        raise RuntimeError("Title field not found")
    device.click(index=title_field)
    device.settle(0.5)
    device.input_text(binding['event_title'], index=title_field)
    device.settle(0.5)

    desc_field = _find_field(device, "Description")
    if desc_field is None:
        raise RuntimeError("Description field not found")
    device.click(index=desc_field)
    device.settle(0.5)
    device.input_text(binding['event_description'], index=desc_field)
    device.settle(0.5)

    _set_start_date(device, binding['year'], binding['month'], binding['day'])

    _set_time_row(device, "start", binding['hour'], 0)

    start_total = binding['hour'] * 60
    end_total = start_total + binding['duration_mins']
    end_hour = (end_total // 60) % 24
    end_minute = end_total % 60

    _set_time_row(device, "end", end_hour, end_minute)

    save_btn = device.find(description="Save", clickable=True)
    if save_btn is None:
        save_btn = device.find(text="Save", clickable=True)
    if save_btn is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_btn)
    device.settle(2.0)

    return True
