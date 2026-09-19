import calendar
from datetime import datetime

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event length in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"},
}


def find_first(device, criteria_list):
    for criteria in criteria_list:
        idx = device.find(**criteria)
        if idx is not None:
            return idx
    return None


def set_field_text(device, text, index):
    """Focus an editable field, clear it if needed, then type text."""
    elems = device.elements()
    elem = next((e for e in elems if e["index"] == index), None)
    if elem and elem.get("text"):
        device.click(index=index)
        device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
        for _ in range(len(elem["text"]) + 5):
            device.adb_shell("input", "keyevent", "KEYCODE_DEL")
    device.input_text(text, index=index)
    device.settle(1)


def click_ok(device):
    ok_idx = find_first(device, [
        {"text": "OK"},
        {"description": "OK"},
        {"text": "Done"},
        {"text": "Set"},
    ])
    if ok_idx is None:
        raise RuntimeError("OK button not found")
    device.click(index=ok_idx)
    device.settle(1)


def set_date_in_picker(device, year, month, day):
    # Try to switch to text input mode (Material DatePicker style)
    switch_btn = find_first(device, [
        {"description": "Switch to input"},
        {"description": "Switch to keyboard"},
        {"description": "Switch to text"},
        {"description": "Input mode"},
        {"description": "Edit"},
    ])
    if switch_btn is not None:
        device.click(index=switch_btn)
        device.settle(1)

        elems = device.elements()
        date_field = None
        date_fmt = "mm/dd/yyyy"
        for e in elems:
            if e.get("editable"):
                hint = (e.get("hint") or "").lower()
                if "date" in hint or "mm" in hint or "yyyy" in hint:
                    date_field = e["index"]
                    if "dd/mm/yyyy" in hint:
                        date_fmt = "dd/mm/yyyy"
                    elif "yyyy-mm-dd" in hint:
                        date_fmt = "yyyy-mm-dd"
                    break
        if date_field is None:
            editables = [e for e in elems if e.get("editable")]
            if editables:
                date_field = editables[0]["index"]

        if date_field is not None:
            if date_fmt == "dd/mm/yyyy":
                date_str = f"{day:02d}/{month:02d}/{year:04d}"
            elif date_fmt == "yyyy-mm-dd":
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                date_str = f"{month:02d}/{day:02d}/{year:04d}"
            set_field_text(device, date_str, date_field)
            click_ok(device)
            return

    # Fallback: calendar navigation
    # Year
    year_btn = find_first(device, [
        {"text": str(year)},
        {"contains": str(year)},
    ])
    if year_btn is not None:
        device.click(index=year_btn)
        device.settle(1)
        year_item = find_first(device, [
            {"text": str(year)},
            {"contains": str(year)},
        ])
        if year_item is not None:
            device.click(index=year_item)
            device.settle(1)
        else:
            for _ in range(30):
                device.scroll(direction="down")
                year_item = find_first(device, [
                    {"text": str(year)},
                    {"contains": str(year)},
                ])
                if year_item is not None:
                    device.click(index=year_item)
                    device.settle(1)
                    break

    # Month
    month_name = calendar.month_name[month]
    month_abbr = calendar.month_abbr[month]
    month_btn = find_first(device, [
        {"text": month_name},
        {"contains": month_name},
        {"text": month_abbr},
        {"contains": month_abbr},
    ])
    if month_btn is not None:
        device.click(index=month_btn)
        device.settle(1)
        month_item = find_first(device, [
            {"text": month_name},
            {"contains": month_name},
            {"text": month_abbr},
            {"contains": month_abbr},
        ])
        if month_item is not None:
            device.click(index=month_item)
            device.settle(1)

    # Day
    day_btn = find_first(device, [
        {"text": str(day)},
        {"contains": str(day)},
    ])
    if day_btn is None:
        raise RuntimeError("Day cell not found in date picker")
    device.click(index=day_btn)
    device.settle(1)

    click_ok(device)


def set_time_in_picker(device, hour, minute):
    elems = device.elements()
    hour_field = None
    minute_field = None
    for e in elems:
        if e.get("editable"):
            hint = (e.get("hint") or "").lower()
            if "hour" in hint or "hh" in hint:
                hour_field = e["index"]
            elif "minute" in hint or "mm" in hint:
                minute_field = e["index"]

    if hour_field is None or minute_field is None:
        switch_btn = find_first(device, [
            {"description": "Switch to keyboard"},
            {"description": "Switch to input"},
            {"description": "Keyboard"},
            {"description": "Input mode"},
        ])
        if switch_btn is not None:
            device.click(index=switch_btn)
            device.settle(1)
            elems = device.elements()
            for e in elems:
                if e.get("editable"):
                    hint = (e.get("hint") or "").lower()
                    if "hour" in hint or "hh" in hint:
                        hour_field = e["index"]
                    elif "minute" in hint or "mm" in hint:
                        minute_field = e["index"]
            if hour_field is None or minute_field is None:
                editables = [e for e in elems if e.get("editable")]
                if len(editables) >= 2:
                    hour_field = editables[0]["index"]
                    minute_field = editables[1]["index"]
                elif len(editables) == 1:
                    hour_field = editables[0]["index"]
                    minute_field = None

    if hour_field is not None:
        set_field_text(device, str(hour), hour_field)
    if minute_field is not None:
        set_field_text(device, f"{minute:02d}", minute_field)

    # If we typed, click OK
    if hour_field is not None or minute_field is not None:
        click_ok(device)
        return

    # Fallback: clock dial
    hour_btn = find_first(device, [
        {"text": str(hour)},
        {"contains": str(hour)},
    ])
    if hour_btn is None:
        raise RuntimeError("Hour not found in time picker")
    device.click(index=hour_btn)
    device.settle(1)

    minute_btn = None
    if minute == 0:
        minute_btn = find_first(device, [
            {"text": "0"},
            {"text": "00"},
            {"contains": "0"},
        ])
    else:
        minute_btn = find_first(device, [
            {"text": str(minute)},
            {"contains": str(minute)},
        ])
    if minute_btn is None:
        raise RuntimeError("Minute not found in time picker")
    device.click(index=minute_btn)
    device.settle(1)

    click_ok(device)


def program(device, binding: dict) -> bool:
    year = binding["year"]
    month = binding["month"]
    day = binding["day"]
    hour = binding["hour"]
    duration_mins = binding["duration_mins"]
    event_title = binding["event_title"]
    event_description = binding["event_description"]

    # Open the app
    device.open_app("Simple Calendar Pro")
    device.settle(2)

    # Find and tap the "New Event" button
    new_event_idx = find_first(device, [
        {"description": "New event"},
        {"hint": "New event"},
        {"text": "New event"},
        {"description": "Create event"},
        {"description": "Add event"},
        {"hint": "Create event"},
        {"hint": "Add event"},
    ])
    if new_event_idx is None:
        # Last attempt: the FAB is usually the only clickable element with a + icon
        elems = device.elements()
        for e in elems:
            if e.get("clickable") and (e.get("description") or "").lower() in ("add", "new", "create"):
                new_event_idx = e["index"]
                break
    if new_event_idx is None:
        raise RuntimeError("New Event button not found")
    device.click(index=new_event_idx)
    device.settle(2)

    # Title
    title_idx = find_first(device, [
        {"hint": "Title"},
        {"description": "Title"},
        {"hint": "Event title"},
    ])
    if title_idx is None:
        elems = device.elements()
        editables = [e for e in elems if e.get("editable")]
        if editables:
            title_idx = editables[0]["index"]
        else:
            raise RuntimeError("Title field not found")
    set_field_text(device, event_title, title_idx)

    # Description
    desc_idx = find_first(device, [
        {"hint": "Description"},
        {"description": "Description"},
        {"hint": "Event description"},
    ])
    if desc_idx is None:
        elems = device.elements()
        editables = [e for e in elems if e.get("editable")]
        if len(editables) >= 2:
            desc_idx = editables[1]["index"]
        else:
            raise RuntimeError("Description field not found")
    set_field_text(device, event_description, desc_idx)

    # Start date
    start_date_idx = find_first(device, [
        {"text": "Start date"},
        {"contains": "Start date"},
        {"description": "Start date"},
    ])
    if start_date_idx is None:
        raise RuntimeError("Start date row not found")
    device.click(index=start_date_idx)
    device.settle(1)
    set_date_in_picker(device, year, month, day)

    # Start time
    start_time_idx = find_first(device, [
        {"text": "Start time"},
        {"contains": "Start time"},
        {"description": "Start time"},
    ])
    if start_time_idx is None:
        raise RuntimeError("Start time row not found")
    device.click(index=start_time_idx)
    device.settle(1)
    set_time_in_picker(device, hour, 0)

    # End time
    end_time_idx = find_first(device, [
        {"text": "End time"},
        {"contains": "End time"},
        {"description": "End time"},
    ])
    if end_time_idx is None:
        # Maybe the row is just "End"
        end_time_idx = find_first(device, [
            {"text": "End"},
            {"contains": "End"},
            {"description": "End"},
        ])
    if end_time_idx is None:
        raise RuntimeError("End time row not found")
    device.click(index=end_time_idx)
    device.settle(1)

    total_minutes = hour * 60 + duration_mins
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60
    set_time_in_picker(device, end_hour, end_minute)

    # Save
    save_idx = find_first(device, [
        {"text": "Save"},
        {"description": "Save"},
        {"hint": "Save"},
        {"description": "Save event"},
    ])
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    device.settle(2)

    return True
