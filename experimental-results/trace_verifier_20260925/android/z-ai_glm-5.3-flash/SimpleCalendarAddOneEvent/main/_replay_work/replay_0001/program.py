import re
from datetime import datetime, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True},
    "month": {"type": "int", "required": True, "min": 1, "max": 12},
    "day": {"type": "int", "required": True},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23},
    "duration_mins": {"type": "int", "required": True},
    "event_title": {"type": "str", "required": True},
    "event_description": {"type": "str", "required": True},
}

MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = binding["event_title"]
    description = binding["event_description"]

    # ---- Open app ----
    device.open_app("Simple Calendar Pro")
    device.settle(2)
    if device.find(description="New Event") is None:
        raise RuntimeError("Simple Calendar Pro did not open: no 'New Event' control found")

    # ---- Open New Event editor ----
    idx = device.find(description="New Event", clickable=True)
    if idx is None:
        raise LookupError("Floating 'New Event' button not found")
    device.click(idx)
    device.settle(1)

    # ---- Title ----
    idx = device.find(hint="Title", editable=True)
    if idx is None:
        raise LookupError("Title field not found")
    device.input_text(title, index=idx)

    # ---- Description ----
    idx = device.find(hint="Description", editable=True)
    if idx is None:
        raise RuntimeError("Description field not found")
    device.input_text(description, index=idx)

    # ---- Start date: open date picker ----
    def is_time(t):
        return bool(re.fullmatch(r"\d{1,2}:\d{2}", (t or "").strip()))

    cands = [e for e in device.elements()
             if e.get("class_name") == "TextView" and e.get("clickable")
             and (e.get("text") or "").strip()
             and not is_time(e.get("text"))
             and "before" not in e["text"].lower()]
    target = next((e for e in cands if re.search(rf"\b{day}\b", e["text"])), None)
    if target is None:
        if not cands:
            raise RuntimeError("Start-date field not found on the event form")
        target = cands[0]
    device.click(target["index"])

    # ---- Select date in picker ----
    desc = f"{day} {MONTHS[month - 1]} {year}"
    idx = device.find(description=desc, clickable=True)
    if idx is None:
        idx = device.find(text=str(day), clickable=True)
    if idx is None:
        raise LookupError(f"Date cell for '{desc}' not found on screen")
    device.click(idx)

    # ---- Confirm date ----
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("Date-picker 'OK' button not found")
    device.click(ok)
    device.settle(1)

    # ---- Open start-time picker ----
    candidates = [f"{hour:02d}:00", f"{hour}:00", f"{hour % 12 or 12}:00"]
    idx = None
    for t in candidates:
        idx = device.find(text=t, class_name="TextView", clickable=True)
        if idx is not None:
            break
    if idx is None:
        raise LookupError(f"Start-time TextView for hour {hour} not found")
    device.click(idx)

    # ---- Select hour ----
    idx = None
    for d in (f"{hour} hours", f"{hour:02d} hours"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            break
    if idx is None:
        raise LookupError(f"Hour {hour} cell not found in time picker")
    device.click(index=idx)

    # ---- Confirm start time ----
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise LookupError("Time picker 'OK' button not found")
    device.click(ok)
    device.settle(1)

    # ---- Open end-time picker (start time + duration) ----
    elems = device.elements()
    time_re = re.compile(r"^\d{1,2}:\d{2}$")
    matches = [e for e in elems if e.get("clickable")
               and time_re.match((e.get("text") or "").strip())]
    if not matches:
        raise LookupError("No clickable start-time TextView (HH:MM) found")
    device.click(matches[0]["index"])

    # ---- Switch to minutes list ----
    idx = None
    for e in device.elements():
        d = str(e.get("description", ""))
        if e.get("clickable") and e.get("class_name") == "View" \
                and "minutes" in d and "hours" not in d:
            idx = e["index"]
            break
    if idx is None:
        raise RuntimeError("Time-picker minutes selector not found")
    device.click(idx)

    # ---- Compute end time and select minutes ----
    start = datetime(year, month, day, hour, 0)
    end = start + timedelta(minutes=duration)
    end_h, end_m = end.hour, end.minute
    # Ensure the picker's hour is the end hour: if the minutes list already
    # contains the target minute, select it; otherwise adjust.
    label = f"{end_m} minutes"
    idx = device.find(description=label, text=str(end_m), clickable=True)
    if idx is None:
        device.scroll("down")
        device.settle(1)
        idx = device.find(description=label, text=str(end_m), clickable=True)
    if idx is None:
        raise LookupError(f"Duration option '{label}' not found on duration picker")
    device.click(idx)

    # ---- Confirm end time ----
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("Time picker 'OK' button not found; dialog may not be open")
    device.click(ok)
    device.settle(1)

    # ---- Save ----
    idx = device.find(description="Save", clickable=True)
    if idx is None:
        raise LookupError("Save button not found on the event form")
    device.click(idx)
    device.settle(2)

    return True
