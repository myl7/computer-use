import re

PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month (1-12)"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour (0-23)"},
    "duration_mins": {"type": "int", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"}
}


def program(device, binding: dict) -> bool:
    def get_month_year(device):
        elements = device.elements()
        month_names = {
            "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
            "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
        }
        found_month = None
        found_year = None
        for el in elements:
            text = el.get('text') or ''
            for m_name, m_num in month_names.items():
                if m_name in text:
                    found_month = m_num
                    break
            match = re.search(r'\b(20\d{2})\b', text)
            if match:
                found_year = int(match.group(1))
        if found_month is not None and found_year is not None:
            return found_month, found_year
        return None, None

    def click_next_month(device):
        for desc in ["Next month", "Next", "Forward"]:
            idx = device.find(description=desc)
            if idx is not None:
                device.click(index=idx)
                return True
        for txt in [">", ">>"]:
            idx = device.find(text=txt)
            if idx is not None:
                device.click(index=idx)
                return True
        elements = device.elements()
        month_idx = None
        for i, el in enumerate(elements):
            text = el.get('text') or ''
            if any(m in text for m in ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]):
                month_idx = i
                break
        if month_idx is not None:
            for offset in [1, 2, -1, -2]:
                idx = month_idx + offset
                if 0 <= idx < len(elements) and elements[idx].get('clickable'):
                    device.click(index=idx)
                    return True
        device.scroll(direction='left')
        return True

    def click_prev_month(device):
        for desc in ["Previous month", "Previous", "Back"]:
            idx = device.find(description=desc)
            if idx is not None:
                device.click(index=idx)
                return True
        for txt in ["<", "<<"]:
            idx = device.find(text=txt)
            if idx is not None:
                device.click(index=idx)
                return True
        elements = device.elements()
        month_idx = None
        for i, el in enumerate(elements):
            text = el.get('text') or ''
            if any(m in text for m in ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]):
                month_idx = i
                break
        if month_idx is not None:
            for offset in [-1, -2, 1, 2]:
                idx = month_idx + offset
                if 0 <= idx < len(elements) and elements[idx].get('clickable'):
                    device.click(index=idx)
                    return True
        device.scroll(direction='right')
        return True

    def navigate_to_date(device, year, month, day):
        max_iter = 60
        for _ in range(max_iter):
            cur_month, cur_year = get_month_year(device)
            if cur_month is None or cur_year is None:
                break
            if cur_year == year and cur_month == month:
                break
            if (cur_year, cur_month) < (year, month):
                click_next_month(device)
            else:
                click_prev_month(device)
            device.settle(1)
        day_idx = device.find(text=str(day), clickable=True)
        if day_idx is None:
            day_idx = device.find(text=str(day).zfill(2), clickable=True)
        if day_idx is None:
            day_idx = device.find(text=str(day))
        if day_idx is not None:
            device.click(index=day_idx)
        else:
            raise Exception(f"Could not find day {day}")
        device.settle(1)

    def open_new_event(device):
        fab_idx = None
        for desc in ["New event", "Add", "Plus", "Create", "New"]:
            fab_idx = device.find(description=desc)
            if fab_idx is not None:
                break
        if fab_idx is None:
            candidates = []
            for el in device.elements():
                if el.get('clickable') and not el.get('text') and not el.get('editable'):
                    candidates.append(el['index'])
            if candidates:
                fab_idx = candidates[-1]
        if fab_idx is not None:
            device.click(index=fab_idx)
            device.settle(1)
        else:
            raise Exception("Could not find FAB")
        new_event_idx = device.find(text="New event")
        if new_event_idx is None:
            new_event_idx = device.find(contains="event")
        if new_event_idx is not None:
            device.click(index=new_event_idx)
        else:
            raise Exception("Could not find New event option")
        device.settle(2)

    def set_time_picker(device, hour, minute):
        for desc in ["Switch to text input mode", "Keyboard", "Text input", "Edit"]:
            idx = device.find(description=desc)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                break
        elements = device.elements()
        editables = [el for el in elements if el.get('editable')]
        if len(editables) >= 2:
            hour_idx = editables[0]['index']
            min_idx = editables[1]['index']
            device.click(index=hour_idx)
            device.input_text(str(hour))
            device.click(index=min_idx)
            device.input_text(str(minute).zfill(2))
            ok_idx = device.find(text="OK")
            if ok_idx is None:
                ok_idx = device.find(description="OK")
            if ok_idx is not None:
                device.click(index=ok_idx)
            device.settle(1)
            return True
        elif len(editables) == 1:
            idx = editables[0]['index']
            device.click(index=idx)
            device.input_text(f"{hour:02d}:{minute:02d}")
            ok_idx = device.find(text="OK")
            if ok_idx is None:
                ok_idx = device.find(description="OK")
            if ok_idx is not None:
                device.click(index=ok_idx)
            device.settle(1)
            return True

        hour_str = str(hour)
        hour_idx = device.find(text=hour_str, clickable=True)
        if hour_idx is None:
            hour_idx = device.find(text=hour_str.zfill(2), clickable=True)
        if hour_idx is not None:
            device.click(index=hour_idx)
            device.settle(1)
        min_str = str(minute)
        min_idx = device.find(text=min_str, clickable=True)
        if min_idx is None:
            min_idx = device.find(text=min_str.zfill(2), clickable=True)
        if min_idx is not None:
            device.click(index=min_idx)
            device.settle(1)
        ok_idx = device.find(text="OK")
        if ok_idx is None:
            ok_idx = device.find(description="OK")
        if ok_idx is not None:
            device.click(index=ok_idx)
        device.settle(1)
        return True

    def fill_event(device, binding):
        title_idx = device.find(hint="Title")
        if title_idx is None:
            editables = [el for el in device.elements() if el.get('editable')]
            if editables:
                title_idx = editables[0]['index']
        if title_idx is not None:
            device.click(index=title_idx)
            device.input_text(binding['event_title'])
        else:
            raise Exception("Could not find title field")

        desc_idx = device.find(hint="Description")
        if desc_idx is None:
            editables = [el for el in device.elements() if el.get('editable')]
            if len(editables) >= 2:
                desc_idx = editables[1]['index']
        if desc_idx is not None:
            device.click(index=desc_idx)
            device.input_text(binding['event_description'])
        else:
            raise Exception("Could not find description field")

        device.settle(1)

        year = binding['year']
        date_row = device.find(contains=str(year), clickable=True)
        if date_row is not None:
            device.click(index=date_row)
            device.settle(1)
            ok_idx = device.find(text="OK")
            if ok_idx is None:
                ok_idx = device.find(description="OK")
            if ok_idx is not None:
                device.click(index=ok_idx)
            device.settle(1)

        start_time_row = device.find(hint="Start time")
        if start_time_row is None:
            time_rows = []
            for el in device.elements():
                if el.get('clickable') and ':' in (el.get('text') or ''):
                    time_rows.append(el['index'])
            if time_rows:
                start_time_row = time_rows[0]
        if start_time_row is not None:
            device.click(index=start_time_row)
            device.settle(1)
            set_time_picker(device, binding['hour'], 0)
        else:
            raise Exception("Could not find start time row")

        start_hour = binding['hour']
        duration = binding['duration_mins']
        end_hour = (start_hour + duration // 60) % 24
        end_minute = duration % 60

        end_time_row = device.find(hint="End time")
        if end_time_row is None:
            time_rows = []
            for el in device.elements():
                if el.get('clickable') and ':' in (el.get('text') or ''):
                    time_rows.append(el['index'])
            if len(time_rows) >= 2:
                end_time_row = time_rows[1]
        if end_time_row is not None:
            device.click(index=end_time_row)
            device.settle(1)
            set_time_picker(device, end_hour, end_minute)
        else:
            raise Exception("Could not find end time row")

        device.settle(1)

        save_idx = None
        for desc in ["Save", "Done"]:
            save_idx = device.find(description=desc)
            if save_idx is not None:
                break
        if save_idx is None:
            for txt in ["Save", "Done"]:
                save_idx = device.find(text=txt)
                if save_idx is not None:
                    break
        if save_idx is None:
            elements = device.elements()
            if len(elements) > 2:
                save_idx = elements[2]['index']
        if save_idx is not None:
            device.click(index=save_idx)
        else:
            raise Exception("Could not find save button")
        device.settle(2)
        return True

    device.open_app("Simple Calendar Pro")
    device.settle(2)

    navigate_to_date(device, binding['year'], binding['month'], binding['day'])
    open_new_event(device)
    fill_event(device, binding)

    return True
