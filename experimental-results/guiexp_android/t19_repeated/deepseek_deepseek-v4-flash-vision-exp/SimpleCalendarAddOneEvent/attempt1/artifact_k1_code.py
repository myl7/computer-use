import datetime
import calendar
import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event length in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"},
}

def program(device, binding: dict) -> bool:
    def clear_and_input(text, index):
        device.click(index=index)
        device.adb_shell("input", "keyevent", "123")
        device.adb_shell("input", "keyevent", *(["67"] * 30))
        device.input_text(text, index=index)

    def find_row(label, scroll_if_missing=False):
        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains=label, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description=label, clickable=True)
        if idx is not None:
            return idx
        for el in device.elements():
            text = el.get('text') or ''
            desc = el.get('description') or ''
            if el.get('clickable') and (label.lower() in text.lower() or label.lower() in desc.lower()):
                return el['index']
        if scroll_if_missing:
            device.scroll(direction='down')
            return find_row(label, scroll_if_missing=False)
        return None

    def get_date_picker_month_year():
        for el in device.elements():
            text = (el.get('text') or '').strip()
            m = re.match(r'^([A-Za-z]+)\s+(\d{4})$', text)
            if m:
                month_name = m.group(1)
                year = int(m.group(2))
                try:
                    month = datetime.datetime.strptime(month_name, "%B").month
                except:
                    try:
                        month = datetime.datetime.strptime(month_name, "%b").month
                    except:
                        month = None
                if month:
                    return month, year
        return None

    def click_next_month():
        btn = device.find(description="Next month")
        if btn is None:
            btn = device.find(description="Next")
        if btn is None:
            btn = device.find(description="Next Month")
        if btn is None:
            btn = device.find(text=">")
        if btn is None:
            raise Exception("Could not find Next month button")
        device.click(index=btn)

    def click_prev_month():
        btn = device.find(description="Previous month")
        if btn is None:
            btn = device.find(description="Previous")
        if btn is None:
            btn = device.find(description="Previous Month")
        if btn is None:
            btn = device.find(text="<")
        if btn is None:
            raise Exception("Could not find Previous month button")
        device.click(index=btn)

    def set_date_in_picker(year, month, day):
        target = (year, month)
        for _ in range(120):
            cur = get_date_picker_month_year()
            if cur is None:
                break
            if cur == target:
                break
            if cur < target:
                click_next_month()
            else:
                click_prev_month()
        cur = get_date_picker_month_year()
        if cur != target:
            raise Exception("Could not navigate to target month in date picker")
        day_el = device.find(text=str(day), clickable=True)
        if day_el is None:
            for el in device.elements():
                if el.get('text') == str(day) and el.get('clickable'):
                    day_el = el['index']
                    break
        if day_el is None:
            raise Exception(f"Could not find day {day} in date picker")
        device.click(index=day_el)
        ok = device.find(text="OK")
        if ok is None:
            ok = device.find(text="Done")
        if ok is None:
            ok = device.find(text="Confirm")
        if ok is None:
            raise Exception("Could not find OK button in date picker")
        device.click(index=ok)

    def set_time_in_picker(hour, minute):
        editable_els = [el for el in device.elements() if el.get('editable')]
        if len(editable_els) < 2:
            toggle = device.find(description="Switch to input mode")
            if toggle is None:
                toggle = device.find(description="Switch to keyboard input")
            if toggle is None:
                toggle = device.find(description="Edit")
            if toggle is None:
                toggle = device.find(text="Edit")
            if toggle is not None:
                device.click(index=toggle)
                editable_els = [el for el in device.elements() if el.get('editable')]
        if len(editable_els) < 2:
            # Fallback: clock face
            hour_el = device.find(text=str(hour), clickable=True)
            if hour_el is None:
                raise Exception("Could not find hour on clock")
            device.click(index=hour_el)
            minute_str = str(minute).zfill(2)
            minute_el = device.find(text=minute_str, clickable=True)
            if minute_el is None:
                minute_el = device.find(text=str(minute), clickable=True)
            if minute_el is None:
                raise Exception("Could not find minute on clock")
            device.click(index=minute_el)
            ok = device.find(text="OK")
            if ok is None:
                ok = device.find(text="Done")
            if ok is None:
                ok = device.find(text="Confirm")
            if ok is None:
                raise Exception("Could not find OK button in time picker")
            device.click(index=ok)
            return

        hour_field = None
        minute_field = None
        for el in editable_els:
            hint = el.get('hint') or ''
            if 'hour' in hint.lower():
                hour_field = el['index']
            elif 'minute' in hint.lower():
                minute_field = el['index']
        if hour_field is None or minute_field is None:
            hour_field = editable_els[0]['index']
            minute_field = editable_els[1]['index']
        clear_and_input(str(hour), hour_field)
        clear_and_input(str(minute).zfill(2), minute_field)
        ok = device.find(text="OK")
        if ok is None:
            ok = device.find(text="Done")
        if ok is None:
            ok = device.find(text="Confirm")
        if ok is None:
            raise Exception("Could not find OK button in time picker")
        device.click(index=ok)

    year = binding['year']
    month = binding['month']
    day = binding['day']
    hour = binding['hour']
    duration_mins = binding['duration_mins']
    title = binding['event_title']
    description = binding['event_description']

    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration_mins)
    end_year = end_dt.year
    end_month = end_dt.month
    end_day = end_dt.day
    end_hour = end_dt.hour
    end_minute = end_dt.minute

    device.open_app("Simple Calendar Pro")

    new_event = device.find(description="New event")
    if new_event is None:
        new_event = device.find(description="Create event")
    if new_event is None:
        new_event = device.find(text="New event")
    if new_event is None:
        new_event = device.find(description="Add event")
    if new_event is None:
        raise Exception("Could not find New event button")
    device.click(index=new_event)

    title_field = device.find(hint="Title", editable=True)
    if title_field is None:
        title_field = device.find(hint="Event title", editable=True)
    if title_field is None:
        title_field = device.find(description="Title", editable=True)
    if title_field is None:
        menu_event = device.find(text="New event")
        if menu_event is not None:
            device.click(index=menu_event)
            title_field = device.find(hint="Title", editable=True)
    if title_field is None:
        raise Exception("Could not find Title field")
    device.click(index=title_field)
    device.input_text(title, index=title_field)

    desc_field = device.find(hint="Description", editable=True)
    if desc_field is None:
        desc_field = device.find(hint="Event description", editable=True)
    if desc_field is None:
        desc_field = device.find(description="Description", editable=True)
    if desc_field is None:
        raise Exception("Could not find Description field")
    device.click(index=desc_field)
    device.input_text(description, index=desc_field)

    start_date_row = find_row("Start date")
    if start_date_row is None:
        raise Exception("Could not find Start date row")
    device.click(index=start_date_row)
    set_date_in_picker(year, month, day)

    start_time_row = find_row("Start time")
    if start_time_row is None:
        raise Exception("Could not find Start time row")
    device.click(index=start_time_row)
    set_time_in_picker(hour, 0)

    device.scroll(direction='down')

    end_date_row = find_row("End date", scroll_if_missing=True)
    if end_date_row is not None:
        device.click(index=end_date_row)
        set_date_in_picker(end_year, end_month, end_day)
    elif (end_year, end_month, end_day) != (year, month, day):
        raise Exception("Could not find End date row for multi-day event")

    end_time_row = find_row("End time", scroll_if_missing=True)
    if end_time_row is None:
        raise Exception("Could not find End time row")
    device.click(index=end_time_row)
    set_time_in_picker(end_hour, end_minute)

    save_btn = device.find(text="Save")
    if save_btn is None:
        save_btn = device.find(description="Save")
    if save_btn is None:
        save_btn = device.find(description="Save event")
    if save_btn is None:
        save_btn = device.find(text="Done")
    if save_btn is None:
        raise Exception("Could not find Save button")
    device.click(index=save_btn)

    return True
