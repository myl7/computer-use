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


def _confirm_picker_ok(device, what):
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError(f"{what} 'OK' button not found")
    device.click(ok)
    device.settle(1)


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

    # ---- Open New Event editor (exact description match to avoid
    # matching some other clickable area) ----
    idx = None
    for e in device.elements():
        if e.get("clickable") and str(e.get("description", "")).strip() == "New Event":
            idx = e["index"]
            break
    if idx is None:
        idx = device.find(description="New Event", clickable=True)
    if idx is None:
        raise LookupError("Floating 'New Event' button not found")
    device.click(idx)
    device.settle(2)

    # ---- Title ----
    idx = device.find(hint="Title", editable=True)
    if idx is None:
        for e in device.elements():
            if e.get("editable") and (e.get("hint") or "").lower().startswith("title"):
                idx = e["index"]
                break
    if idx is None:
        raise LookupError("Title field not found")
    device.input_text(title, index=idx)

    # ---- Description ----
    idx = device.find(hint="Description", editable=True)
    if idx is None:
        for e in device.elements():
            if e.get("editable") and (e.get("hint") or "").lower().startswith("description"):
                idx = e["index"]
                break
    if idx is None:
        raise RuntimeError("Description field not found")
    device.input_text(description, index=idx)

    # ---- Start date: open date picker ----
    month_name = MONTHS[month - 1]

    def is_time(t):
        return bool(re.fullmatch(r"\d{1,2}:\d{2}", (t or "").strip()))

    cands = [e for e in device.elements()
             if e.get("class_name") == "TextView" and e.get("clickable")
             and (e.get("text") or "").strip()
             and not is_time(e.get("text"))
             and "before" not in e["text"].lower()]
    target = None
    # Prefer the start-date row showing the target month name
    for e in cands:
        t = e["text"]
        if month_name.lower() in t.lower() and re.search(rf"\b{day}\b", t):
            target = e
            break
    if target is None:
        target = next((e for e in cands if re.search(rf"\b{day}\b", e["text"])), None)
    if target is None:
        if not cands:
            raise RuntimeError("Start-date field not found on the event form")
        target = cands[0]
    device.click(target["index"])
    device.settle(1)

    # ---- Select date in picker: navigate to the right month if needed ----
    desc = f"{day} {month_name} {year}"

    def find_day_cell():
        i = device.find(description=desc, clickable=True)
        if i is None:
            i = device.find(text=str(day), clickable=True)
        return i

    idx = find_day_cell()
    tries = 0
    while idx is None and tries < 24:
        # Scroll through months of the picker
        device.scroll("left" if tries % 2 == 0 else "right")
        device.settle(1)
        idx = find_day_cell()
        tries += 1
    if idx is None:
        raise LookupError(f"Date cell for '{desc}' not found on screen")
    device.click(idx)

    # ---- Confirm date ----
    _confirm_picker_ok(device, "Date-picker")

    # ---- Open start-time picker ----
    def time_row_candidates(h):
        return [f"{h:02d}:00", f"{h}:00"]

    idx = None
    for t in time_row_candidates(hour):
        idx = device.find(text=t, class_name="TextView", clickable=True)
        if idx is not None:
            break
    if idx is None:
        for e in device.elements():
            if e.get("clickable") and re.fullmatch(r"\d{1,2}:\d{2}", (e.get("text") or "").strip()):
                idx = e["index"]
                break
    if idx is None:
        raise LookupError(f"Start-time TextView for hour {hour} not found")
    device.click(idx)
    device.settle(1)

    # ---- Select hour in the time picker ----
    idx = None
    for d in (f"{hour} hours", f"{hour:02d} hours"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            break
    if idx is None:
        idx = device.find(text=str(hour), clickable=True)
    if idx is None:
        raise LookupError(f"Hour {hour} cell not found in time picker")
    device.click(idx)
    # minutes default to 00

    # ---- Confirm start time ----
    _confirm_picker_ok(device, "Time picker")

    # ---- Open end-time picker ----
    end_dt = datetime(year, month, day, hour, 0) + timedelta(minutes=duration)
    end_h, end_m = end_dt.hour, end_dt.minute

    time_re = re.compile(r"^\d{1,2}:\d{2}$")
    matches = []
    for e in device.elements():
        if e.get("clickable") and time_re.match((e.get("text") or "").strip()):
            matches.append(e)
    # Prefer the row that already shows the (just-set) start time
    start_str = f"{hour:02d}:00"
    chosen = None
    for e in matches:
        if e["text"].strip() == start_str:
            chosen = e
            break
    if chosen is None and matches:
        chosen = matches[0]
    if chosen is None:
        raise LookupError("No clickable start-time TextView (HH:MM) found")
    device.click(chosen["index"])
    device.settle(1)

    # ---- Select end hour ----
    idx = None
    for d in (f"{end_h} hours", f"{end_h:02d} hours"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            break
    if idx is None:
        idx = device.find(text=str(end_h), clickable=True)
    if idx is not None:
        device.click(idx)
    else:
        # If hour wheel not directly clickable, cycle wheels via the selector
        for e in device.elements():
            d = str(e.get("description", ""))
            if e.get("clickable") and "hours" in d and "minutes" not in d:
                device.click(e["index"])
                break

    # ---- Switch to minutes list ----
    idx = None
    for e in device.elements():
        d = str(e.get("description", ""))
        if e.get("clickable") and "minutes" in d and "hours" not in d:
            idx = e["index"]
            break
    if idx is None:
        raise RuntimeError("Time-picker minutes selector not found")
    device.click(idx)
    device.settle(1)

    # ---- Select end minutes ----
    label = f"{end_m} minutes"
    idx = device.find(description=label, clickable=True)
    if idx is None:
        idx = device.find(text=str(end_m), clickable=True)
    if idx is None:
        device.scroll("down")
        device.settle(1)
        idx = device.find(description=label, clickable=True)
    if idx is None:
        raise LookupError(f"Duration option '{label}' not found on duration picker")
    device.click(idx)

    # ---- Confirm end time ----
    _confirm_picker_ok(device, "Time picker")

    # ---- Save ----
    idx = device.find(description="Save", clickable=True)
    if idx is None:
        raise LookupError("Save button not found on the event form")
    device.click(idx)
    device.settle(2)

    return True
