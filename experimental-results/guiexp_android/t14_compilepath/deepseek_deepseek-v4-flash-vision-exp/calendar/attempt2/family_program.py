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

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


def get_element(device, index):
    for e in device.elements():
        if e['index'] == index:
            return e
    return {}


def find_editable_with_keyword(device, keyword):
    for e in device.elements():
        if e.get('editable'):
            haystack = ((e.get('hint') or '') + ' ' +
                        (e.get('text') or '') + ' ' +
                        (e.get('description') or '')).lower()
            if keyword.lower() in haystack:
                return e['index']
    return None


def is_date_text(text):
    if not text:
        return False
    if re.search(r'\b(19|20)\d{2}\b', text):
        if '/' in text or '-' in text or '.' in text:
            return True
        if any(m.lower() in text.lower() for m in MONTHS):
            return True
    return False


def is_time_text(text):
    return bool(re.match(r'^\d{1,2}:\d{2}$', text))


def find_time_rows(device):
    rows = []
    for e in device.elements():
        if e.get('clickable') and is_time_text(e.get('text')):
            rows.append(e['index'])
    return rows


def open_new_event(device):
    idx = device.find(description="New event") or device.find(text="New event")
    if idx is not None:
        device.click(idx)
        return

    more = device.find(description="More options") or device.find(contains="More options")
    if more is not None:
        device.click(more)
        device.settle(1)
        new = device.find(text="New event") or device.find(description="New event")
        if new is not None:
            device.click(new)
            return

    idx = (device.find(description="Add event") or
           device.find(description="Create event") or
           device.find(text="+"))
    if idx is not None:
        device.click(idx)
        return

    raise RuntimeError("Cannot find new event button")


def find_mode_switch(device):
    for e in device.elements():
        desc = (e.get('description') or '').lower()
        if ('input mode' in desc or 'text input' in desc or
            'switch to input' in desc or 'switch to text' in desc or
            desc == 'edit'):
            return e['index']
    return None


def clear_field(device, index):
    device.click(index)
    el = get_element(device, index)
    cur_text = el.get('text') or ''
    if cur_text:
        device.adb_shell('input', 'keyevent', '123')  # MOVE_END
        for _ in range(len(cur_text)):
            device.adb_shell('input', 'keyevent', '67')  # DEL
    device.wait()


def select_popup_item(device, value):
    idx = device.find(text=value, clickable=True)
    if idx is None:
        idx = device.find(text=value)
    if idx is None:
        if len(value) > 1 and value.startswith('0'):
            idx = device.find(text=value[1], clickable=True)
        else:
            idx = device.find(text=value.zfill(2), clickable=True)
    if idx is None:
        for e in device.elements():
            if e.get('clickable') and (e.get('text') or '') == value:
                idx = e['index']
                break
    if idx is None:
        raise RuntimeError(f"Popup item {value} not found")
    device.click(idx)


# ---------- Date picker ----------

def find_date_header(device):
    for e in device.elements():
        text = (e.get('text') or '')
        if re.match(r'^[A-Za-z]+[\.,]? \d{4}$', text):
            if any(m.lower() in text.lower() or m[:3].lower() in text.lower() for m in MONTHS):
                return e
    return None


def parse_header(text):
    for i, m in enumerate(MONTHS, 1):
        if m.lower() in text.lower() or m[:3].lower() in text.lower():
            month = i
            break
    else:
        return None, None
    m = re.search(r'\b(19|20)\d{2}\b', text)
    if not m:
        return None, None
    return month, int(m.group())


def find_prev_month_button(device):
    for e in device.elements():
        desc = (e.get('description') or '').lower()
        if 'previous' in desc and 'month' in desc:
            return e['index']
        if (e.get('text') or '') in ('<', '‹', '«'):
            return e['index']
    return None


def find_next_month_button(device):
    for e in device.elements():
        desc = (e.get('description') or '').lower()
        if 'next' in desc and 'month' in desc:
            return e['index']
        if (e.get('text') or '') in ('>', '›', '»'):
            return e['index']
    return None


def select_day(device, month, year, day):
    month_name = MONTHS[month - 1]
    for e in device.elements():
        if e.get('clickable') and (e.get('text') or '') == str(day):
            desc = (e.get('description') or '')
            if month_name.lower() in desc.lower() or str(year) in desc:
                return e['index']
    for e in device.elements():
        if e.get('clickable') and (e.get('text') or '') == str(day):
            return e['index']
    raise RuntimeError(f"Day {day} not found in calendar")


def find_date_edit(device):
    editables = [e['index'] for e in device.elements() if e.get('editable')]
    if len(editables) == 1:
        return editables[0]
    for i in editables:
        el = get_element(device, i)
        hint = (el.get('hint') or '').lower()
        text = (el.get('text') or '').lower()
        if '/' in hint or '/' in text or 'date' in hint or 'yyyy' in hint or 'mm' in hint:
            return i
    if editables:
        return editables[0]
    return None


def format_date_for_input(device, edit_index, year, month, day):
    el = get_element(device, edit_index)
    hint = (el.get('hint') or '').upper()
    if 'MM' in hint and 'DD' in hint and 'YYYY' in hint:
        if hint.index('MM') < hint.index('DD') < hint.index('YYYY'):
            return f"{month:02d}/{day:02d}/{year}"
        if hint.index('DD') < hint.index('MM') < hint.index('YYYY'):
            return f"{day:02d}/{month:02d}/{year}"
        if hint.index('YYYY') < hint.index('MM') < hint.index('DD'):
            return f"{year}/{month:02d}/{day:02d}"
    return f"{month:02d}/{day:02d}/{year}"


def try_date_text_input(device, year, month, day):
    switch = find_mode_switch(device)
    if switch is None:
        return False
    device.click(switch)
    device.settle(1)
    date_edit = find_date_edit(device)
    if date_edit is None:
        return False
    date_str = format_date_for_input(device, date_edit, year, month, day)
    clear_field(device, date_edit)
    device.input_text(date_str, index=date_edit)
    ok = device.find(text="OK") or device.find(description="OK")
    if ok is not None:
        device.click(ok)
        device.settle(1)
        return True
    return False


def set_date_calendar(device, year, month, day):
    last_header = None
    for _ in range(1000):
        header = find_date_header(device)
        if header is None:
            raise RuntimeError("Date picker header not found")
        text = (header.get('text') or '')
        cur_month, cur_year = parse_header(text)
        if cur_month == month and cur_year == year:
            day_idx = select_day(device, month, year, day)
            device.click(day_idx)
            device.settle(1)
            ok = device.find(text="OK") or device.find(description="OK")
            if ok is None:
                raise RuntimeError("Date picker OK not found")
            device.click(ok)
            device.settle(1)
            return
        if last_header == text:
            raise RuntimeError("Date picker navigation stuck")
        last_header = text
        prev = find_prev_month_button(device)
        nxt = find_next_month_button(device)
        if prev is None or nxt is None:
            raise RuntimeError("Date picker navigation buttons not found")
        if year < cur_year or (year == cur_year and month < cur_month):
            device.click(prev)
        else:
            device.click(nxt)
        device.settle(1)
    raise RuntimeError("Date picker navigation limit exceeded")


def set_date_spinner(device, year, month, day):
    month_spinner = None
    day_spinner = None
    year_spinner = None
    for e in device.elements():
        if not e.get('clickable'):
            continue
        desc = (e.get('description') or '').lower()
        if 'month' in desc:
            month_spinner = e['index']
        elif 'day' in desc:
            day_spinner = e['index']
        elif 'year' in desc:
            year_spinner = e['index']
    if month_spinner is None or day_spinner is None or year_spinner is None:
        raise RuntimeError("Date spinners not found")

    device.click(month_spinner)
    device.settle(1)
    month_name = MONTHS[month - 1]
    try:
        select_popup_item(device, month_name)
    except RuntimeError:
        select_popup_item(device, month_name[:3])
    device.settle(1)

    device.click(day_spinner)
    device.settle(1)
    select_popup_item(device, str(day))
    device.settle(1)

    device.click(year_spinner)
    device.settle(1)
    select_popup_item(device, str(year))
    device.settle(1)

    ok = device.find(text="OK") or device.find(description="OK")
    if ok is not None:
        device.click(ok)
        device.settle(1)
    else:
        raise RuntimeError("Date picker OK not found")


def set_date_picker(device, year, month, day):
    device.settle(1)
    if try_date_text_input(device, year, month, day):
        return
    try:
        set_date_calendar(device, year, month, day)
    except Exception:
        set_date_spinner(device, year, month, day)


# ---------- Time picker ----------

def try_time_text_input(device, hour, minute):
    switch = find_mode_switch(device)
    if switch is None:
        return False
    device.click(switch)
    device.settle(1)
    editables = [e['index'] for e in device.elements() if e.get('editable')]
    if len(editables) < 2:
        return False

    hour_edit = None
    minute_edit = None
    for i in editables:
        el = get_element(device, i)
        hint = (el.get('hint') or '').lower()
        desc = (el.get('description') or '').lower()
        if 'hour' in hint or 'hour' in desc:
            hour_edit = i
        elif 'minute' in hint or 'minute' in desc:
            minute_edit = i
    if hour_edit is None:
        hour_edit = editables[0]
    if minute_edit is None:
        minute_edit = editables[1]

    clear_field(device, hour_edit)
    device.input_text(f"{hour:02d}", index=hour_edit)
    clear_field(device, minute_edit)
    device.input_text(f"{minute:02d}", index=minute_edit)

    ok = device.find(text="OK") or device.find(description="OK")
    if ok is not None:
        device.click(ok)
        device.settle(1)
        return True
    return False


def set_time_spinner(device, hour, minute):
    hour_spinner = None
    minute_spinner = None
    for e in device.elements():
        if not e.get('clickable'):
            continue
        desc = (e.get('description') or '').lower()
        if 'hour' in desc:
            if hour_spinner is None:
                hour_spinner = e['index']
        elif 'minute' in desc:
            if minute_spinner is None:
                minute_spinner = e['index']

    if hour_spinner is None or minute_spinner is None:
        numerics = []
        for e in device.elements():
            if e.get('clickable') and re.match(r'^\d{1,2}$', (e.get('text') or '')):
                numerics.append(e['index'])
        if len(numerics) >= 2:
            hour_spinner = numerics[0]
            minute_spinner = numerics[1]
        else:
            raise RuntimeError("Time spinners not found")

    device.click(hour_spinner)
    device.settle(1)
    select_popup_item(device, str(hour))
    device.settle(1)

    device.click(minute_spinner)
    device.settle(1)
    select_popup_item(device, f"{minute:02d}")
    device.settle(1)

    ok = device.find(text="OK") or device.find(description="OK")
    if ok is not None:
        device.click(ok)
        device.settle(1)
    else:
        raise RuntimeError("Time picker OK not found")


def set_time_picker(device, hour, minute):
    device.settle(1)
    if try_time_text_input(device, hour, minute):
        return
    set_time_spinner(device, hour, minute)


# ---------- Main program ----------

def program(device, binding: dict) -> bool:
    device.open_app("Simple Calendar Pro")
    device.settle(1)
    open_new_event(device)
    device.settle(1)

    # Title
    title_idx = (device.find(hint="Title") or
                 device.find(description="Title") or
                 device.find(editable=True, contains="Title") or
                 find_editable_with_keyword(device, "Title"))
    if title_idx is None:
        raise RuntimeError("Title field not found")
    device.click(title_idx)
    device.input_text(binding['event_title'], index=title_idx)

    # Description
    desc_idx = (device.find(hint="Description") or
                device.find(description="Description") or
                device.find(editable=True, contains="Description") or
                find_editable_with_keyword(device, "Description"))
    if desc_idx is None:
        raise RuntimeError("Description field not found")
    device.click(desc_idx)
    device.input_text(binding['event_description'], index=desc_idx)

    # Start date
    date_rows = [e['index'] for e in device.elements()
                 if e.get('clickable') and is_date_text(e.get('text'))]
    if not date_rows:
        raise RuntimeError("Start date row not found")
    device.click(date_rows[0])
    set_date_picker(device, binding['year'], binding['month'], binding['day'])

    # Start time (minutes are always 0; binding only gives hour)
    time_rows = find_time_rows(device)
    if len(time_rows) < 1:
        raise RuntimeError("Start time row not found")
    device.click(time_rows[0])
    set_time_picker(device, binding['hour'], 0)

    # End time = start time + duration
    total_minutes = binding['hour'] * 60 + binding['duration_mins']
    end_hour = (total_minutes // 60) % 24
    end_minute = total_minutes % 60
    time_rows = find_time_rows(device)
    if len(time_rows) < 2:
        raise RuntimeError("End time row not found")
    device.click(time_rows[1])
    set_time_picker(device, end_hour, end_minute)

    # Save
    save_idx = (device.find(text="Save") or
                device.find(description="Save") or
                device.find(text="Add") or
                device.find(description="Add"))
    if save_idx is None:
        save_idx = device.find(text="OK") or device.find(description="OK")
    if save_idx is not None:
        device.click(save_idx)
        device.settle(1)
        return True

    raise RuntimeError("Save button not found")
