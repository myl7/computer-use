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
    # 1. Open the app
    device.open_app("Simple Calendar Pro")
    device.settle(2)

    # 2. Open the new-event screen (clicking New Event if necessary)
    for _ in range(5):
        idx = device.find(description="New Event", clickable=True)
        if idx is None:
            idx = device.find(text="New Event", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(2)
        if device.find(hint="Title", editable=True) is not None:
            break
    else:
        raise RuntimeError("Could not open new event screen")

    # 3. Title
    title_idx = device.find(hint="Title", editable=True)
    if title_idx is None:
        raise RuntimeError("Title field not found")
    device.click(index=title_idx)
    device.input_text(text=binding["event_title"], index=title_idx)
    device.settle(0.5)

    # 4. Description
    desc_idx = device.find(hint="Description", editable=True)
    if desc_idx is None:
        raise RuntimeError("Description field not found")
    device.click(index=desc_idx)
    device.input_text(text=binding["event_description"], index=desc_idx)
    device.settle(0.5)

    def get_nth_clickable_after(hint, n=1):
        """Return the index of the n-th clickable element after the element with the given hint."""
        elements = device.elements()
        anchor = None
        for e in elements:
            if e.get("hint") == hint:
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

    # 5. Set start date
    start_date_idx = get_nth_clickable_after("Description", 1)
    device.click(index=start_date_idx)
    device.settle(1)

    target_year = binding["year"]
    target_month = binding["month"]
    target_day = binding["day"]
    day_desc = f"{target_day} {calendar.month_name[target_month]} {target_year}"

    found = False
    for _ in range(200):
        idx = device.find(description=day_desc, clickable=True)
        if idx is None:
            idx = device.find(text=str(target_day), description=day_desc)
        if idx is None:
            idx = device.find(text=str(target_day), clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            found = True
            break

        if (target_year, target_month) < (2023, 10):
            direction = "Previous month"
        else:
            direction = "Next month"

        btn = device.find(description=direction, clickable=True)
        if btn is None:
            if "Next" in direction:
                btn = device.find(description_contains="Next", clickable=True)
            else:
                btn = device.find(description_contains="Previous", clickable=True)
        if btn is None:
            btn = device.find(text=direction, clickable=True)
        if btn is None:
            raise RuntimeError(f"Could not find navigation button for {direction}")
        device.click(index=btn)
        device.settle(1)

    if not found:
        raise RuntimeError(f"Could not find day cell {day_desc} after navigation")

    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise RuntimeError("OK button not found in date picker")
    device.click(index=ok_idx)
    device.settle(1)

    # 6. Set start time (hour:00)
    start_time_idx = get_nth_clickable_after("Description", 2)
    device.click(index=start_time_idx)
    device.settle(1)

    start_hour = binding["hour"]
    start_minute = 0

    hour_desc = f"{start_hour} hours"
    idx = device.find(description=hour_desc, clickable=True)
    if idx is None:
        idx = device.find(description=f"{start_hour:02d} hours", clickable=True)
    if idx is None:
        idx = device.find(text=str(start_hour), clickable=True)
    if idx is None:
        raise RuntimeError(f"Start hour {start_hour} not found in time picker")
    device.click(index=idx)
    device.settle(0.5)

    minute_desc = f"{start_minute:02d} minutes"
    idx = device.find(description=minute_desc, clickable=True)
    if idx is None:
        idx = device.find(text=f"{start_minute:02d}", clickable=True)
    if idx is None:
        idx = device.find(description=f"{start_minute} minutes", clickable=True)
    if idx is None:
        raise RuntimeError(f"Start minute {start_minute} not found in time picker")
    device.click(index=idx)
    device.settle(0.5)

    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise RuntimeError("OK button not found in start time picker")
    device.click(index=ok_idx)
    device.settle(1)

    # 7. Set end time (start + duration_mins)
    total_minutes = start_hour * 60 + start_minute + binding["duration_mins"]
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60

    end_time_idx = get_nth_clickable_after("Description", 3)
    device.click(index=end_time_idx)
    device.settle(1)

    hour_desc = f"{end_hour} hours"
    idx = device.find(description=hour_desc, clickable=True)
    if idx is None:
        idx = device.find(description=f"{end_hour:02d} hours", clickable=True)
    if idx is None:
        idx = device.find(text=str(end_hour), clickable=True)
    if idx is None:
        raise RuntimeError(f"End hour {end_hour} not found in time picker")
    device.click(index=idx)
    device.settle(0.5)

    minute_desc = f"{end_minute:02d} minutes"
    idx = device.find(description=minute_desc, clickable=True)
    if idx is None:
        idx = device.find(text=f"{end_minute:02d}", clickable=True)
    if idx is None:
        idx = device.find(description=f"{end_minute} minutes", clickable=True)
    if idx is None:
        raise RuntimeError(f"End minute {end_minute} not found in time picker")
    device.click(index=idx)
    device.settle(0.5)

    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise RuntimeError("OK button not found in end time picker")
    device.click(index=ok_idx)
    device.settle(1)

    # 8. Save the event
    save_idx = device.find(description="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(text="Save", clickable=True)
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    device.settle(2)

    return True
