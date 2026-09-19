import re
import calendar

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event duration in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"}
}


def program(device, binding: dict) -> bool:
    device.open_app("Simple Calendar Pro")
    device.settle(2)

    # Open the new-event screen if we are not already there.
    if not device.find(hint="Title"):
        fab = (device.find(description="New event") or
               device.find(contains="New event") or
               device.find(description="Add"))
        if fab is not None:
            device.click(fab)
            device.settle(1)
        if not device.find(hint="Title"):
            new_event = device.find(text="New event") or device.find(contains="New event")
            if new_event is not None:
                device.click(new_event)
                device.settle(1)

    # Title
    title_elem = device.find(hint="Title")
    if title_elem is None:
        editables = [e for e in device.elements() if e.get("editable")]
        if editables:
            title_elem = editables[0]["index"]
    if title_elem is not None:
        device.click(title_elem)
        device.input_text(binding["event_title"], index=title_elem)
    device.settle(1)

    # Description
    desc_elem = device.find(hint="Description")
    if desc_elem is None:
        editables = [e for e in device.elements() if e.get("editable")]
        if len(editables) >= 2:
            desc_elem = editables[1]["index"]
    if desc_elem is not None:
        device.click(desc_elem)
        device.input_text(binding["event_description"], index=desc_elem)
    device.settle(1)

    # Start date
    start_date_elem = (device.find(description="Start date") or
                       device.find(contains="Start date"))
    if start_date_elem is None:
        elements = device.elements()
        desc_idx = None
        for e in elements:
            if e.get("editable") and (e.get("hint") == "Description" or e.get("text") == "Description"):
                desc_idx = e["index"]
                break
        if desc_idx is None:
            editables = [e for e in elements if e.get("editable")]
            if len(editables) >= 2:
                desc_idx = editables[1]["index"]
        if desc_idx is not None:
            for e in elements:
                if e["index"] > desc_idx and e.get("clickable") and not e.get("editable"):
                    start_date_elem = e["index"]
                    break
    if start_date_elem is not None:
        device.click(start_date_elem)
        device.settle(1)
        set_date_in_picker(device, binding["year"], binding["month"], binding["day"])
        device.settle(1)

    # Start time
    start_time_elem = (device.find(description="Start time") or
                       device.find(contains="Start time"))
    if start_time_elem is None:
        elements = device.elements()
        time_elems = [e for e in elements
                      if e.get("clickable") and re.match(r"^\d{1,2}:\d{2}$", e.get("text", ""))]
        if time_elems:
            start_time_elem = time_elems[0]["index"]
    if start_time_elem is not None:
        device.click(start_time_elem)
        device.settle(1)
        set_time(device, binding["hour"], 0)
        device.settle(1)

    # End time
    total_mins = binding["hour"] * 60 + binding["duration_mins"]
    end_hour = (total_mins // 60) % 24
    end_min = total_mins % 60
    end_time_elem = (device.find(description="End time") or
                     device.find(contains="End time"))
    if end_time_elem is None:
        elements = device.elements()
        time_elems = [e for e in elements
                      if e.get("clickable") and re.match(r"^\d{1,2}:\d{2}$", e.get("text", ""))]
        if len(time_elems) >= 2:
            end_time_elem = time_elems[1]["index"]
    if end_time_elem is not None:
        device.click(end_time_elem)
        device.settle(1)
        set_time(device, end_hour, end_min)
        device.settle(1)

    # Save
    save = (device.find(description="Save") or
            device.find(text="Save") or
            device.find(description="Done") or
            device.find(text="Done"))
    if save is not None:
        device.click(save)
    else:
        save = device.find(contains="Save") or device.find(contains="Done")
        if save is not None:
            device.click(save)
    device.settle(2)
    return True


def parse_month_year(text):
    months_full = {name: i for i, name in enumerate(calendar.month_name) if name}
    months_abbr = {name: i for i, name in enumerate(calendar.month_abbr) if name}
    month = None
    year = None
    words = re.findall(r"[A-Za-z]+|\d+", text)
    for w in words:
        if w in months_full:
            month = months_full[w]
        elif w in months_abbr:
            month = months_abbr[w]
        elif w.isdigit() and len(w) == 4:
            year = int(w)
    if month is None or year is None:
        for w in words:
            if w.isdigit() and len(w) == 4:
                year = int(w)
        for w in words:
            for m, name in enumerate(calendar.month_name):
                if name and name.lower().startswith(w.lower()[:3]):
                    month = m
                    break
            if month:
                break
    return month, year


def set_date_in_picker(device, year, month, day):
    ok = (device.find(text="OK") or
          device.find(text="Done") or
          device.find(description="OK"))
    max_iter = 120
    for _ in range(max_iter):
        elements = device.elements()
        header = None
        for e in elements:
            t = e.get("text", "") or ""
            if (any(m in t for m in calendar.month_name[1:]) and
                    any(str(y) in t for y in range(2000, 2100))):
                header = t
                break
        if not header:
            break
        cur_month, cur_year = parse_month_year(header)
        if cur_month == month and cur_year == year:
            break
        if (cur_year, cur_month) < (year, month):
            btn = (device.find(description="Next month") or
                   device.find(description="Next") or
                   device.find(text=">"))
        else:
            btn = (device.find(description="Previous month") or
                   device.find(description="Previous") or
                   device.find(text="<"))
        if btn is not None:
            device.click(btn)
            device.settle(1)
        else:
            hdr_elem = device.find(text=header)
            if hdr_elem is not None:
                device.click(hdr_elem)
                device.settle(1)
                year_elem = device.find(text=str(year))
                if year_elem is not None:
                    device.click(year_elem)
                    device.settle(1)
                    month_name = calendar.month_name[month]
                    month_elem = (device.find(text=month_name) or
                                  device.find(text=calendar.month_abbr[month]))
                    if month_elem is not None:
                        device.click(month_elem)
                        device.settle(1)
                else:
                    device.navigate_back()
                    device.settle(1)
            else:
                break

    day_elem = device.find(text=str(day))
    if day_elem is not None:
        device.click(day_elem)
        device.settle(1)
    if ok is not None:
        device.click(ok)
    else:
        ok2 = device.find(text="OK") or device.find(text="Done")
        if ok2:
            device.click(ok2)
    device.settle(1)


def set_time(device, hour, minute):
    # Try keyboard input first
    kb = (device.find(description="Keyboard") or
          device.find(contains="Keyboard") or
          device.find(description="Switch to keyboard input") or
          device.find(contains="keyboard"))
    if kb is not None:
        device.click(kb)
        device.settle(1)
        editables = [e for e in device.elements() if e.get("editable")]
        if len(editables) >= 2:
            device.click(editables[0]["index"])
            device.input_text(str(hour), index=editables[0]["index"])
            device.click(editables[1]["index"])
            device.input_text(f"{minute:02d}", index=editables[1]["index"])
            device.settle(1)
            ok = device.find(text="OK") or device.find(text="Done")
            if ok:
                device.click(ok)
                device.settle(1)
            return

    # Clock-face method
    hour_elem = device.find(text=str(hour))
    if hour_elem is not None:
        device.click(hour_elem)
        device.settle(1)
    minute_str1 = str(minute)
    minute_str2 = f"{minute:02d}"
    minute_elem = device.find(text=minute_str1) or device.find(text=minute_str2)
    if minute_elem is not None:
        device.click(minute_elem)
        device.settle(1)
    ok = device.find(text="OK") or device.find(text="Done")
    if ok is not None:
        device.click(ok)
        device.settle(1)
