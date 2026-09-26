import calendar
import datetime
import re

PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "description": "Event month (1-12)"},
    "day": {"type": "integer", "description": "Event day of month"},
    "hour": {"type": "integer", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def program(device, binding):
    # Extract bindings
    year = int(binding['year'])
    month = int(binding['month'])
    day = int(binding['day'])
    hour = int(binding['hour'])
    duration = int(binding['duration_mins'])
    title = binding['event_title']
    description = binding['event_description']

    # Compute start and end datetimes
    start_dt = datetime.datetime(year, month, day, hour, 0)
    end_dt = start_dt + datetime.timedelta(minutes=duration)

    # Open app
    device.open_app("Simple Calendar Pro")
    device.settle(2)

    # Ensure we are on MainActivity
    for _ in range(5):
        if device.find(description="New Event") or device.find(description="New Event", clickable=True):
            break
        device.navigate_back()
        device.settle(1)

    # Click New Event FAB
    new_event = device.find(description="New Event", clickable=True)
    if new_event is None:
        new_event = device.find(description="New Event")
    if new_event is None:
        new_event = device.find(description="New event", clickable=True)
    if new_event is None:
        new_event = device.find(description="New event")
    if new_event is None:
        new_event = device.find(description="Add event", clickable=True)
    if new_event is None:
        new_event = device.find(description="Add event")
    if new_event is None:
        new_event = device.find(text="New Event")
    if new_event is None:
        new_event = device.find(text="New event")
    if new_event is None:
        # Search all elements for a clickable element with description containing "new event" or "add event"
        for el in device.elements():
            desc = (el.get('description') or '').lower()
            if el.get('clickable') and ('new event' in desc or 'add event' in desc):
                new_event = el['index']
                break
    if new_event is None:
        raise RuntimeError("Could not find 'New Event' button")
    device.click(index=new_event)
    device.settle(2)

    # Wait for EventActivity
    for _ in range(10):
        if device.find(hint="Title") is not None:
            break
        device.settle(1)
    else:
        raise RuntimeError("EventActivity did not open")

    # Fill Title
    title_field = device.find(hint="Title", editable=True)
    if title_field is None:
        raise RuntimeError("Title field not found")
    device.click(index=title_field)
    device.settle(0.5)
    device.input_text(title, index=title_field)
    device.settle(0.5)

    # Fill Description
    desc_field = device.find(hint="Description", editable=True)
    if desc_field is None:
        desc_field = device.find(hint="Description")
    if desc_field is None:
        raise RuntimeError("Description field not found")
    device.click(index=desc_field)
    device.settle(0.5)
    device.input_text(description, index=desc_field)
    device.settle(0.5)

    # Helper to find date and time rows
    def get_date_time_rows():
        elements = device.elements()
        date_rows = []
        time_rows = []
        for el in elements:
            if not el.get('clickable'):
                continue
            txt = (el.get('text') or '').strip()
            if any(m in txt for m in calendar.month_name[1:]) or any(m in txt for m in calendar.month_abbr[1:]):
                date_rows.append(el['index'])
            if re.match(r'^\d{1,2}:\d{2}$', txt):
                time_rows.append(el['index'])
        date_rows.sort()
        time_rows.sort()
        return date_rows, time_rows

    # Set start date
    date_rows, time_rows = get_date_time_rows()
    if not date_rows:
        raise RuntimeError("No date row found")
    start_date_row = date_rows[0]
    set_date_via_picker(device, start_date_row, start_dt.date())

    # Set start time
    device.settle(1)
    date_rows, time_rows = get_date_time_rows()
    if not time_rows:
        raise RuntimeError("No time row found after setting date")
    start_time_row = time_rows[0]
    set_time_via_picker(device, start_time_row, start_dt.hour, start_dt.minute)

    # Set end date if different
    if end_dt.date() != start_dt.date():
        device.settle(1)
        date_rows, time_rows = get_date_time_rows()
        if len(date_rows) >= 2:
            end_date_row = date_rows[1]
            set_date_via_picker(device, end_date_row, end_dt.date())
        else:
            end_date_row = None
            for el in device.elements():
                if el.get('clickable') and 'end' in (el.get('description') or '').lower():
                    end_date_row = el['index']
                    break
            if end_date_row is not None:
                set_date_via_picker(device, end_date_row, end_dt.date())

    # Set end time
    device.settle(1)
    date_rows, time_rows = get_date_time_rows()
    if len(time_rows) >= 2:
        end_time_row = time_rows[1]
    else:
        end_time_row = None
        for el in device.elements():
            if el.get('clickable') and 'end' in (el.get('description') or '').lower():
                end_time_row = el['index']
                break
        if end_time_row is None:
            raise RuntimeError("Could not find end time row")
    set_time_via_picker(device, end_time_row, end_dt.hour, end_dt.minute)

    # Save
    device.settle(1)
    save_btn = device.find(description="Save") or device.find(text="Save")
    if save_btn is None:
        for el in device.elements():
            if el.get('clickable') and el.get('class_name') == 'ImageButton':
                desc = (el.get('description') or '').lower()
                if 'save' in desc or 'done' in desc:
                    save_btn = el['index']
                    break
    if save_btn is None:
        save_btn = device.find(text="Save", clickable=True)
    if save_btn is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_btn)
    device.settle(2)

    return True


def set_date_via_picker(device, row_index, target_date):
    device.click(index=row_index)
    device.settle(1.5)

    target_year = target_date.year
    target_month = target_date.month
    target_day = target_date.day

    for attempt in range(30):
        cur_month = None
        cur_year = None
        header_el = None
        for el in device.elements():
            txt = (el.get('text') or '')
            m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', txt)
            if m:
                month_name = m.group(1)
                cur_year = int(m.group(2))
                cur_month = list(calendar.month_name).index(month_name)
                header_el = el
                break
        if cur_month is None:
            for el in device.elements():
                txt = (el.get('text') or '')
                m = re.search(r'([A-Za-z]{3,9})\s+(\d{4})', txt)
                if m:
                    month_name = m.group(1)
                    for i, name in enumerate(calendar.month_name):
                        if name.startswith(month_name) or calendar.month_abbr[i] == month_name:
                            cur_month = i
                            cur_year = int(m.group(2))
                            header_el = el
                            break
                    if cur_month is not None:
                        break
        if cur_month is None:
            break

        if (cur_year, cur_month) == (target_year, target_month):
            break

        if (cur_year, cur_month) < (target_year, target_month):
            next_btn = device.find(description="Next month") or device.find(description="next") or device.find(text=">") or device.find(description="Next")
            if next_btn is None:
                for el in device.elements():
                    if el.get('clickable') and el.get('class_name') == 'ImageButton':
                        if (el.get('text') or '') in ('>', '❯', '→'):
                            next_btn = el['index']
                            break
            if next_btn is None:
                if header_el:
                    device.click(index=header_el['index'])
                    device.settle(1)
                    year_el = device.find(text=str(target_year))
                    if year_el:
                        device.click(index=year_el)
                        device.settle(1)
                        month_name = calendar.month_name[target_month]
                        month_el = device.find(text=month_name)
                        if month_el:
                            device.click(index=month_el)
                            device.settle(1)
                            break
                break
            else:
                device.click(index=next_btn)
                device.settle(0.5)
        else:
            prev_btn = device.find(description="Previous month") or device.find(description="previous") or device.find(text="<") or device.find(description="Previous")
            if prev_btn is None:
                for el in device.elements():
                    if el.get('clickable') and el.get('class_name') == 'ImageButton':
                        if (el.get('text') or '') in ('<', '❮', '←'):
                            prev_btn = el['index']
                            break
            if prev_btn is None:
                if header_el:
                    device.click(index=header_el['index'])
                    device.settle(1)
                    year_el = device.find(text=str(target_year))
                    if year_el:
                        device.click(index=year_el)
                        device.settle(1)
                        month_name = calendar.month_name[target_month]
                        month_el = device.find(text=month_name)
                        if month_el:
                            device.click(index=month_el)
                            device.settle(1)
                            break
                break
            else:
                device.click(index=prev_btn)
                device.settle(0.5)

    # Click day cell
    day_el = None
    for el in device.elements():
        if el.get('clickable'):
            txt = (el.get('text') or '').strip()
            desc = (el.get('description') or '')
            if txt == str(target_day):
                if str(target_year) in desc and (calendar.month_name[target_month] in desc or calendar.month_abbr[target_month] in desc):
                    day_el = el['index']
                    break
    if day_el is None:
        day_el = device.find(text=str(target_day), clickable=True)
    if day_el is None:
        for el in device.elements():
            if el.get('clickable') and el.get('text') == str(target_day):
                day_el = el['index']
                break
    if day_el is None:
        raise RuntimeError(f"Could not find day cell {target_day} in date picker")
    device.click(index=day_el)
    device.settle(0.5)

    # Click OK
    ok_btn = device.find(text="OK") or device.find(description="OK")
    if ok_btn is None:
        ok_btn = device.find(text="Ok") or device.find(text="ok")
    if ok_btn is None:
        raise RuntimeError("OK button not found in date picker")
    device.click(index=ok_btn)
    device.settle(1)


def set_time_via_picker(device, row_index, hour, minute):
    device.click(index=row_index)
    device.settle(1.5)

    # Set hour
    hour_el = None
    for el in device.elements():
        if el.get('clickable'):
            desc = (el.get('description') or '').lower()
            txt = (el.get('text') or '').strip()
            if desc == f"{hour} hours" or desc == f"{hour} hour" or txt == str(hour):
                hour_el = el['index']
                break
    if hour_el is None:
        hour_el = device.find(text=str(hour), clickable=True)
    if hour_el is not None:
        device.click(index=hour_el)
        device.settle(0.5)

    # Set minute
    minute_str = f"{minute:02d}"
    minute_el = None
    for el in device.elements():
        if el.get('clickable'):
            desc = (el.get('description') or '').lower()
            txt = (el.get('text') or '').strip()
            if desc == f"{minute_str} minutes" or desc == f"{minute} minutes" or txt == minute_str:
                minute_el = el['index']
                break
    if minute_el is None:
        # Try to switch to minute view by clicking the header
        for el in device.elements():
            if el.get('clickable'):
                txt = (el.get('text') or '').strip()
                if re.match(r'^\d{1,2}:\d{2}', txt):
                    device.click(index=el['index'])
                    device.settle(0.5)
                    break
        # Try again
        for el in device.elements():
            if el.get('clickable'):
                desc = (el.get('description') or '').lower()
                txt = (el.get('text') or '').strip()
                if desc == f"{minute_str} minutes" or desc == f"{minute} minutes" or txt == minute_str:
                    minute_el = el['index']
                    break
    if minute_el is not None:
        device.click(index=minute_el)
        device.settle(0.5)

    # Click OK
    ok_btn = device.find(text="OK") or device.find(description="OK")
    if ok_btn is None:
        ok_btn = device.find(text="Ok") or device.find(text="ok")
    if ok_btn is None:
        raise RuntimeError("OK button not found in time picker")
    device.click(index=ok_btn)
    device.settle(1)
