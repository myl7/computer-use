import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "min": 1, "max": 12, "description": "Event month (1-12)"},
    "day": {"type": "int", "min": 1, "max": 31, "description": "Event day of month"},
    "hour": {"type": "int", "min": 0, "max": 23, "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "min": 0, "description": "Event length in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    month_name = month_names[binding["month"] - 1]

    # Open app
    device.open_app("Simple Calendar Pro")
    device.wait()

    # Tap New Event
    idx = device.find(description="New Event")
    if idx is None:
        idx = device.find(text="New Event")
    if idx is None:
        raise Exception("New Event button not found")
    device.click(index=idx)
    device.wait()

    # If a type selector appears, tap "Event"
    idx = device.find(text="Event", clickable=True)
    if idx is not None:
        device.click(index=idx)
        device.wait()

    # Enter title
    idx = device.find(hint="Title")
    if idx is None:
        idx = device.find(text="Title", editable=True)
    if idx is None:
        raise Exception("Title field not found")
    device.click(index=idx)
    device.wait()
    device.input_text(binding["event_title"], index=idx)
    device.wait()

    # Enter description
    idx = device.find(hint="Description")
    if idx is None:
        idx = device.find(text="Description", editable=True)
    if idx is None:
        raise Exception("Description field not found")
    device.click(index=idx)
    device.wait()
    device.input_text(binding["event_description"], index=idx)
    device.wait()

    # Locate and click the start date row
    def find_date_row():
        elements = device.elements()
        pattern = re.compile(r"^[A-Za-z]+ \d{1,2} \([A-Za-z]{3}\)$")
        for e in elements:
            txt = e.get("text") or ""
            if pattern.match(txt) and e.get("clickable"):
                return e["index"]
        # Fallback: clickable non-editable element containing a month name
        for e in elements:
            txt = e.get("text") or ""
            if any(m in txt for m in month_names) and e.get("clickable") and not e.get("editable"):
                return e["index"]
        return None

    date_row_index = find_date_row()
    if date_row_index is None:
        raise Exception("Start date row not found")
    device.click(index=date_row_index)
    device.wait()

    # Date picker: navigate to the correct month/year
    # Device clock is frozen at October 2023, so the picker starts at that month.
    initial_year = 2023
    initial_month = 10
    month_diff = (binding["year"] - initial_year) * 12 + (binding["month"] - initial_month)

    if month_diff > 0:
        for _ in range(month_diff):
            nav_idx = device.find(description="Next month")
            if nav_idx is None:
                nav_idx = device.find(text="Next month")
            if nav_idx is None:
                raise Exception("Next month button not found")
            device.click(index=nav_idx)
            device.wait()
    elif month_diff < 0:
        for _ in range(-month_diff):
            nav_idx = device.find(description="Previous month")
            if nav_idx is None:
                nav_idx = device.find(text="Previous month")
            if nav_idx is None:
                raise Exception("Previous month button not found")
            device.click(index=nav_idx)
            device.wait()

    # Select the day
    day_description = f"{binding['day']} {month_name} {binding['year']}"
    idx = device.find(description=day_description)
    if idx is None:
        idx = device.find(text=str(binding["day"]), clickable=True)
    if idx is None:
        raise Exception("Day not found in date picker")
    device.click(index=idx)
    device.wait()

    # Confirm date picker with OK
    idx = device.find(text="OK", clickable=True)
    if idx is None:
        idx = device.find(text="OK")
    if idx is None:
        raise Exception("OK button not found in date picker")
    device.click(index=idx)
    device.wait()

    # Helper to set a time via the dialog time picker
    def set_time(row_index: int, hour: int, minute: int) -> None:
        device.click(index=row_index)
        device.wait()

        # Hour
        idx = device.find(description=f"{hour} hours")
        if idx is None:
            idx = device.find(text=str(hour))
        if idx is None:
            idx = device.find(text=f"{hour:02d}")
        if idx is None:
            raise Exception(f"Hour {hour} not found in time picker")
        device.click(index=idx)
        device.wait()

        # Minute
        idx = device.find(description=f"{minute} minutes")
        if idx is None:
            idx = device.find(text=f"{minute:02d}")
        if idx is None:
            idx = device.find(text=str(minute))
        if idx is None:
            raise Exception(f"Minute {minute} not found in time picker")
        device.click(index=idx)
        device.wait()

        # Confirm
        idx = device.find(text="OK", clickable=True)
        if idx is None:
            idx = device.find(text="OK")
        if idx is None:
            raise Exception("OK button not found in time picker")
        device.click(index=idx)
        device.wait()

    # Locate start and end time rows
    def find_time_rows():
        elements = device.elements()
        rows = []
        for e in elements:
            txt = e.get("text") or ""
            if re.match(r"^\d{1,2}:\d{2}$", txt) and e.get("clickable"):
                rows.append(e)
        return rows

    time_rows = find_time_rows()
    if len(time_rows) < 2:
        raise Exception("Could not find start/end time rows")

    start_hour = binding["hour"]
    start_minute = 0
    set_time(time_rows[0]["index"], start_hour, start_minute)

    # Re-fetch rows after setting start time
    time_rows = find_time_rows()
    if len(time_rows) < 2:
        raise Exception("Could not find time rows after setting start time")

    start_minutes = binding["hour"] * 60
    end_minutes = start_minutes + binding["duration_mins"]
    end_hour = (end_minutes // 60) % 24
    end_minute = end_minutes % 60

    set_time(time_rows[1]["index"], end_hour, end_minute)

    # Save
    idx = device.find(description="Save")
    if idx is None:
        idx = device.find(text="Save")
    if idx is None:
        raise Exception("Save button not found")
    device.click(index=idx)
    device.wait()

    return True
