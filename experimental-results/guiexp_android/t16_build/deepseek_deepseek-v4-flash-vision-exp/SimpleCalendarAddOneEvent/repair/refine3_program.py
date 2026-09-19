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


def _find_date_rows(device):
    rows = []
    for e in device.elements():
        text = e.get("text") or ""
        if e.get("clickable") and re.fullmatch(r"[A-Z][a-z]+ \d{1,2} \([A-Z][a-z]{2}\)", text):
            rows.append(e["index"])
    return rows


def _find_day_cell(device, day, label):
    day_cell = device.find(text=str(day), description=label)
    if day_cell is not None:
        return day_cell
    for e in device.elements():
        if e.get("text") == str(day) and label.lower() in (e.get("description") or "").lower():
            return e["index"]
    return None


def _get_current_month_year(device):
    for e in device.elements():
        text = e.get("text") or ""
        m = re.search(r"([A-Z][a-z]+)\s+(\d{4})", text)
        if m:
            month_name = m.group(1)
            year = int(m.group(2))
            if month_name in calendar.month_name:
                month = list(calendar.month_name).index(month_name)
                return year, month
    return 2023, 10


def _set_date_row(device, which, year, month, day):
    rows = _find_date_rows(device)
    if len(rows) < 2:
        raise RuntimeError("Not enough date rows found")
    row = rows[0] if which == "start" else rows[1]
    device.click(index=row)
    device.settle(1.0)

    label = f"{day} {calendar.month_name[month]} {year}"
    day_cell = _find_day_cell(device, day, label)
    if day_cell is None:
        month_year_text = f"{calendar.month_name[month]} {year}"
        header_found = any((e.get("text") or "") and month_year_text.lower() in (e.get("text") or "").lower() for e in device.elements())
        if not header_found:
            current_year, current_month = _get_current_month_year(device)
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
            day_cell = _find_day_cell(device, day, label)
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


def _click_text(device, text):
    """Click the first clickable element whose text exactly matches ``text``."""
    for e in device.elements():
        if e.get("clickable") and (e.get("text") or "") == text:
            device.click(index=e["index"])
            device.settle(0.5)
            return True
    return False


def _select_time_in_picker(device, hour, minute, current_time_text=None):
    """Set the hour and minute in the time picker.

    The picker may open in hour mode or minute mode.  To be robust we
    explicitly switch to hour mode by clicking the current hour in the
    header, then select the desired hour.  Afterwards we switch to minute
    mode by clicking the current minute in the header, then select the
    desired minute.  Finally we confirm with OK.
    """
    current_hour = None
    current_minute = None
    if current_time_text:
        parts = current_time_text.split(':')
        if len(parts) == 2:
            current_hour = parts[0]
            current_minute = parts[1]

    # Switch to hour mode (if we know the current hour)
    if current_hour:
        _click_text(device, current_hour)
        device.settle(0.5)

    # Select the desired hour
    hour_str = str(hour)
    hour_str_2 = f"{hour:02d}"
    if not _click_text(device, hour_str):
        if hour_str_2 != hour_str:
            _click_text(device, hour_str_2)

    # Switch to minute mode (if we know the current minute)
    if current_minute:
        _click_text(device, current_minute)
        device.settle(0.5)

    # Select the desired minute
    minute_str = f"{minute:02d}"
    minute_str_2 = str(minute)
    if not _click_text(device, minute_str):
        if minute_str_2 != minute_str:
            _click_text(device, minute_str_2)

    # Confirm with OK
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("OK button not found in time picker")
    device.click(index=ok)
    device.settle(1.0)


def _set_time_row(device, which, hour, minute):
    rows = _get_time_rows(device)
    if len(rows) < 2:
        raise RuntimeError("Not enough time rows found")
    row = rows[0] if which == "start" else rows[1]

    # Capture the current time text before clicking, so we can switch picker modes.
    current_time_text = None
    for e in device.elements():
        if e.get("index") == row:
            current_time_text = e.get("text")
            break

    device.click(index=row)
    device.settle(1.0)

    _select_time_in_picker(device, hour, minute, current_time_text)

    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("OK button not found in time picker")
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

    _set_date_row(device, "start", binding['year'], binding['month'], binding['day'])
    _set_date_row(device, "end", binding['year'], binding['month'], binding['day'])

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
