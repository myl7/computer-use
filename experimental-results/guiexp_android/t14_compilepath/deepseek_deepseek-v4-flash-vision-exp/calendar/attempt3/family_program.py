import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "min": 1, "max": 12, "description": "Event month"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "min": 0, "max": 23, "description": "Event start hour (24h)"},
    "duration_mins": {"type": "int", "min": 1, "description": "Event duration in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    year = binding["year"]
    month = binding["month"]
    day = binding["day"]
    hour = binding["hour"]
    duration_mins = binding["duration_mins"]
    event_title = binding["event_title"]
    event_description = binding["event_description"]

    end_total = hour * 60 + duration_mins
    end_hour = (end_total // 60) % 24
    end_minute = end_total % 60

    device.open_app("Simple Calendar Pro")
    device.wait()

    _go_to_new_event(device)
    device.wait()

    # Title and Description are the first two editable fields on EventActivity
    _enter_text_in_editable(device, event_title, 0)
    _enter_text_in_editable(device, event_description, 1)

    # Set start date
    _click_row(device, "Start date")
    device.wait()
    _set_date(device, year, month, day)

    # Set start time (minute is always 0 because event starts at {hour}h)
    _click_row(device, "Start time")
    device.wait()
    _set_time(device, hour, 0)

    # Set end time = start + duration
    _click_row(device, "End time")
    device.wait()
    _set_time(device, end_hour, end_minute)

    # Save event
    _click_button(device, "Save")
    device.wait()

    return True


# ----- Helper functions -----


def _go_to_new_event(device):
    candidates = [
        {"description": "New event"},
        {"text": "New event"},
        {"description": "Add event"},
        {"text": "Add event"},
        {"description": "Create event"},
        {"text": "+"},
    ]
    for cand in candidates:
        idx = device.find(**cand)
        if idx is not None:
            device.click(index=idx)
            device.wait()
            if _count_editable(device) >= 2 or device.find(hint="Title") is not None:
                return
            # A popup/drawer may have opened: tap the actual "Event" item.
            for cand2 in [{"text": "Event"}, {"text": "New event"}, {"description": "New event"}]:
                idx2 = device.find(**cand2)
                if idx2 is not None:
                    device.click(index=idx2)
                    device.wait()
                    if _count_editable(device) >= 2 or device.find(hint="Title") is not None:
                        return
    # Fallback: search by generic description / text
    for el in device.elements():
        if el.get("clickable"):
            desc = (el.get("description") or "").lower()
            text = (el.get("text") or "").lower()
            if "new" in desc or "add" in desc or "new" in text or "add" in text:
                device.click(index=el["index"])
                device.wait()
                if _count_editable(device) >= 2 or device.find(hint="Title") is not None:
                    return
                for el2 in device.elements():
                    if el2.get("clickable") and (el2.get("text") or "").strip() in ("Event", "New event", "Add event"):
                        device.click(index=el2["index"])
                        device.wait()
                        if _count_editable(device) >= 2 or device.find(hint="Title") is not None:
                            return
    raise Exception("Could not open new event screen")


def _count_editable(device):
    return sum(1 for el in device.elements() if el.get("editable"))


def _enter_text_in_editable(device, text, position):
    ed = [el for el in device.elements() if el.get("editable")]
    if len(ed) <= position:
        raise Exception(f"Expected at least {position + 1} editable fields, got {len(ed)}")
    idx = ed[position]["index"]
    device.click(index=idx)
    device.input_text(text, index=idx)


def _click_row(device, label):
    possible = [label, label.replace(" ", ""), label.upper()]
    for text in possible:
        idx = device.find(text=text)
        if idx is not None:
            device.click(index=idx)
            return
    idx = device.find(contains=label)
    if idx is not None:
        device.click(index=idx)
        return
    idx = device.find(description=label)
    if idx is not None:
        device.click(index=idx)
        return
    raise Exception(f"Row '{label}' not found")


def _click_button(device, label):
    for text in [label, label + " event", "Save", "Done", "Confirm"]:
        idx = device.find(text=text)
        if idx is not None:
            device.click(index=idx)
            return
    idx = device.find(description=label)
    if idx is not None:
        device.click(index=idx)
        return
    for el in device.elements():
        if el.get("clickable") and label.lower() in (el.get("text") or "").lower():
            device.click(index=el["index"])
            return
    raise Exception(f"Button '{label}' not found")


def _find_switch_to_text_input(device):
    for desc in ["Switch to text input mode", "Text input mode", "Input mode", "Keyboard", "Edit"]:
        idx = device.find(description=desc)
        if idx is not None:
            return idx
    for text in ["Switch to text input mode", "Keyboard"]:
        idx = device.find(text=text)
        if idx is not None:
            return idx
    return None


# ----- Date picker helpers -----

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"]


def _set_date(device, year, month, day):
    if device.find(text="OK") is None:
        raise Exception("DatePicker OK not found")
    if _set_date_text(device, year, month, day):
        return
    _set_date_calendar(device, year, month, day)


def _set_date_text(device, year, month, day):
    switch = _find_switch_to_text_input(device)
    if switch is None:
        return False
    device.click(index=switch)
    device.wait()
    ed = [el for el in device.elements() if el.get("editable")]
    if not ed:
        return False
    field_idx = ed[0]["index"]
    existing = ed[0].get("text") or ""
    fmt = "M/D/Y"
    if "/" in existing:
        parts = existing.split("/")
        if len(parts) == 3:
            try:
                a, b = int(parts[0]), int(parts[1])
                if a > 12 and b <= 12:
                    fmt = "D/M/Y"
                elif b > 12 and a <= 12:
                    fmt = "M/D/Y"
            except ValueError:
                pass
    device.click(index=field_idx)
    if fmt == "D/M/Y":
        date_str = f"{day:02d}/{month:02d}/{year}"
    else:
        date_str = f"{month:02d}/{day:02d}/{year}"
    device.input_text(date_str, index=field_idx)
    ok_idx = device.find(text="OK")
    if ok_idx is not None:
        device.click(index=ok_idx)
        device.wait()
        return True
    return False


def _set_date_calendar(device, year, month, day):
    ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise Exception("DatePicker OK not found")

    displayed = _get_displayed_month_year(device)
    if displayed is not None:
        cur_month, cur_year = displayed
        for _ in range(120):
            if (cur_year, cur_month) == (year, month):
                break
            if (year, month) > (cur_year, cur_month):
                next_idx = _find_next_month(device)
                if next_idx is None:
                    raise Exception("Next month button not found in date picker")
                device.click(index=next_idx)
                device.wait()
                cur_month += 1
                if cur_month > 12:
                    cur_month = 1
                    cur_year += 1
            else:
                prev_idx = _find_prev_month(device)
                if prev_idx is None:
                    raise Exception("Previous month button not found in date picker")
                device.click(index=prev_idx)
                device.wait()
                cur_month -= 1
                if cur_month < 1:
                    cur_month = 12
                    cur_year -= 1
        else:
            raise Exception("Date picker navigation failed after 120 months")

    day_idx = device.find(text=str(day))
    if day_idx is None:
        day_idx = device.find(text=str(day).zfill(2))
    if day_idx is None:
        for el in device.elements():
            if el.get("text") and el["text"].strip() == str(day) and (el.get("clickable") or el.get("description")):
                day_idx = el["index"]
                break
    if day_idx is None:
        raise Exception("Day not found in date picker")
    device.click(index=day_idx)
    device.wait()

    ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise Exception("OK not found after clicking day")
    device.click(index=ok_idx)
    device.wait()


def _get_displayed_month_year(device):
    pattern = r"\b(" + "|".join(_MONTH_NAMES) + r")\s+(\d{4})\b"
    for el in device.elements():
        text = el.get("text") or ""
        m = re.search(pattern, text)
        if m:
            month = _MONTH_NAMES.index(m.group(1)) + 1
            year = int(m.group(2))
            return month, year
    return None


def _find_next_month(device):
    for desc in ["Next month", "Next"]:
        idx = device.find(description=desc)
        if idx is not None:
            return idx
    for text in [">", "›", "→", "Right"]:
        idx = device.find(text=text)
        if idx is not None:
            return idx
    return None


def _find_prev_month(device):
    for desc in ["Previous month", "Previous"]:
        idx = device.find(description=desc)
        if idx is not None:
            return idx
    for text in ["<", "‹", "←", "Left"]:
        idx = device.find(text=text)
        if idx is not None:
            return idx
    return None


# ----- Time picker helpers -----


def _set_time(device, hour, minute):
    if device.find(text="OK") is None:
        raise Exception("TimePicker OK not found")
    if _set_time_text(device, hour, minute):
        return
    _set_time_clock(device, hour, minute)


def _set_time_text(device, hour, minute):
    switch = _find_switch_to_text_input(device)
    if switch is None:
        return False
    device.click(index=switch)
    device.wait()

    eds = [el for el in device.elements() if el.get("editable")]
    if not eds:
        return False

    hour_field = None
    minute_field = None
    single_field = None
    for el in eds:
        hint = (el.get("hint") or "").lower()
        desc = (el.get("description") or "").lower()
        text = el.get("text") or ""
        if "hour" in hint or "hour" in desc:
            hour_field = el["index"]
        elif "minute" in hint or "minute" in desc:
            minute_field = el["index"]
        if ":" in text:
            single_field = el["index"]

    if single_field is not None:
        device.click(index=single_field)
        device.input_text(f"{hour:02d}:{minute:02d}", index=single_field)
    elif hour_field is not None:
        device.click(index=hour_field)
        device.input_text(str(hour).zfill(2), index=hour_field)
        if minute_field is not None:
            device.click(index=minute_field)
            device.input_text(str(minute).zfill(2), index=minute_field)
    else:
        if len(eds) == 1:
            device.click(index=eds[0]["index"])
            device.input_text(f"{hour:02d}:{minute:02d}", index=eds[0]["index"])
        else:
            return False

    ok_idx = device.find(text="OK")
    if ok_idx is not None:
        device.click(index=ok_idx)
        device.wait()
        return True
    return False


def _set_time_clock(device, hour, minute):
    hour_el = None
    for text in [str(hour), f"{hour:02d}"]:
        idx = device.find(text=text, clickable=True)
        if idx is not None:
            hour_el = idx
            break
    if hour_el is None:
        for desc in [f"{hour} o'clock", f"{hour}:00", f"{hour}"]:
            idx = device.find(description=desc)
            if idx is not None:
                hour_el = idx
                break
    if hour_el is None:
        raise Exception("Cannot find hour in time picker")
    device.click(index=hour_el)
    device.wait()

    if minute % 5 != 0:
        raise Exception("Cannot set non-multiple-of-5 minute without text input mode")

    min_el = None
    for text in [str(minute), f"{minute:02d}"]:
        idx = device.find(text=text, clickable=True)
        if idx is not None:
            min_el = idx
            break
    if min_el is None and minute == 0:
        min_el = device.find(text="0", clickable=True)
    if min_el is None:
        raise Exception("Cannot find minute in time picker")
    device.click(index=min_el)
    device.wait()

    ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise Exception("OK not found in time picker")
    device.click(index=ok_idx)
    device.wait()
