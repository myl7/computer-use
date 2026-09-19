import calendar

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "event year"},
    "month": {"type": "int", "description": "event month 1-12"},
    "day": {"type": "int", "description": "event day of month"},
    "hour": {"type": "int", "description": "event start hour 0-23"},
    "duration_mins": {"type": "int", "description": "event length in minutes"},
    "event_title": {"type": "str", "description": "event title"},
    "event_description": {"type": "str", "description": "event description"},
}


def program(device, binding: dict) -> bool:
    def _month_from_name(name: str):
        for i, n in enumerate(calendar.month_name):
            if n.lower() == name.lower():
                return i
        for i, n in enumerate(calendar.month_abbr):
            if n.lower() == name.lower():
                return i
        return None

    def _get_calendar_header():
        import re
        for e in device.elements():
            t = e.get('text', '')
            if t and re.search(r'[A-Za-z]+\s+\d{4}', t):
                return t.strip()
        return None

    def _parse_month_year(header):
        import re
        m = re.search(r'([A-Za-z]+)\s+(\d{4})', header)
        if m:
            month = _month_from_name(m.group(1))
            if month:
                return int(m.group(2)), month
        return None

    def _navigate_to_month(target_year, target_month):
        for _ in range(24):
            header = _get_calendar_header()
            parsed = _parse_month_year(header) if header else None
            if parsed and parsed[0] == target_year and parsed[1] == target_month:
                return
            prev_idx = device.find(description="Previous month") or device.find(text="<") or device.find(description="Previous")
            next_idx = device.find(description="Next month") or device.find(text=">") or device.find(description="Next")
            if next_idx is None and prev_idx is None:
                raise Exception("Could not find calendar navigation arrows")
            if parsed:
                cur_year, cur_month = parsed
                if (target_year, target_month) > (cur_year, cur_month):
                    if next_idx is None:
                        raise Exception("Next arrow not found")
                    device.click(index=next_idx)
                elif (target_year, target_month) < (cur_year, cur_month):
                    if prev_idx is None:
                        raise Exception("Prev arrow not found")
                    device.click(index=prev_idx)
                else:
                    return
            else:
                if next_idx is None:
                    raise Exception("Next arrow not found")
                device.click(index=next_idx)
            device.wait()
        raise Exception("Could not navigate to target month")

    def _open_new_event():
        fab_idx = device.find(text="+") or device.find(description="New event") or device.find(description="Add event") or device.find(description="Add")
        if fab_idx is None:
            fab_idx = device.find(text="New event")
        if fab_idx is None:
            raise Exception("Could not find FAB")
        device.click(index=fab_idx)
        device.wait()
        item_idx = device.find(text="New event") or device.find(description="New event")
        if item_idx is None:
            raise Exception("Could not find 'New event' menu item")
        device.click(index=item_idx)
        device.wait()

    def _set_text_at_index(index, text):
        device.click(index=index)
        device.wait()
        current = None
        for e in device.elements():
            if e['index'] == index:
                current = e.get('text', '')
                break
        if current:
            device.adb_shell('input keyevent 123')
            for _ in range(len(current)):
                device.adb_shell('input keyevent 67')
        device.input_text(text, index=index)
        device.wait()

    def _set_text(text, **find):
        idx = device.find(**find)
        if idx is None:
            if 'hint' in find:
                idx = device.find(text=find['hint'])
        if idx is None:
            raise Exception(f"Could not find field with {find}")
        _set_text_at_index(idx, text)

    def _click_ok():
        idx = device.find(text="OK") or device.find(description="OK") or device.find(text="Ok") or device.find(text="ok")
        if idx is None:
            raise Exception("Could not find OK button")
        device.click(index=idx)
        device.wait()

    def _switch_to_input_mode():
        for desc in ["Switch to input mode", "Switch to keyboard", "Keyboard", "Input mode", "Edit time"]:
            idx = device.find(description=desc)
            if idx is not None:
                device.click(index=idx)
                device.wait()
                return True
        for text in ["Keypad", "Keyboard", "Input"]:
            idx = device.find(text=text)
            if idx is not None:
                device.click(index=idx)
                device.wait()
                return True
        if device.find(hint="Hour") is not None:
            return False
        raise Exception("Could not switch time picker to input mode")

    def _set_time_in_picker(hour, minute):
        _switch_to_input_mode()
        hour_idx = device.find(hint="Hour") or device.find(text="Hour")
        minute_idx = device.find(hint="Minute") or device.find(text="Minute")
        if hour_idx is not None and minute_idx is not None:
            _set_text_at_index(hour_idx, str(hour))
            _set_text_at_index(minute_idx, str(minute).zfill(2))
        else:
            editable = [e for e in device.elements() if e.get('editable')]
            if len(editable) >= 2:
                _set_text_at_index(editable[0]['index'], str(hour))
                _set_text_at_index(editable[1]['index'], str(minute).zfill(2))
            elif len(editable) == 1:
                _set_text_at_index(editable[0]['index'], f"{hour:02d}:{minute:02d}")
            else:
                raise Exception("Could not find time input fields")
        _click_ok()

    # --- Main flow ---
    device.open_app("Simple Calendar Pro")
    device.wait()

    # Select the target date on the calendar
    _navigate_to_month(binding["year"], binding["month"])
    day_idx = device.find(text=str(binding["day"]))
    if day_idx is None:
        raise Exception("Day not found on calendar")
    device.click(index=day_idx)
    device.wait()

    # Open new event screen
    _open_new_event()

    # Fill title and description
    _set_text(binding["event_title"], hint="Title")
    _set_text(binding["event_description"], hint="Description")

    # Confirm start date (it should already be the selected day)
    start_date_idx = device.find(text="Start date") or device.find(contains="Start date")
    if start_date_idx is not None:
        device.click(index=start_date_idx)
        device.wait()
        _click_ok()

    # Set start time
    start_time_idx = device.find(text="Start time") or device.find(contains="Start time")
    if start_time_idx is None:
        raise Exception("Start time row not found")
    device.click(index=start_time_idx)
    device.wait()
    _set_time_in_picker(binding["hour"], 0)

    # Set end time = start + duration_mins
    end_time_idx = device.find(text="End time") or device.find(contains="End time")
    if end_time_idx is None:
        raise Exception("End time row not found")
    device.click(index=end_time_idx)
    device.wait()
    total_minutes = binding["hour"] * 60 + binding["duration_mins"]
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60
    _set_time_in_picker(end_hour, end_minute)

    # Save event
    save_idx = device.find(text="Save") or device.find(description="Save") or device.find(text="OK")
    if save_idx is None:
        raise Exception("Save button not found")
    device.click(index=save_idx)
    device.wait()

    return True
