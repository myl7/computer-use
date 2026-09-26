PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark in OsmAnd",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    location = binding['location']
    if not isinstance(location, str):
        raise ValueError("binding['location'] must be a string")

    def is_control_or_navigation(el):
        text = (el.get('text') or '').lower()
        desc = (el.get('description') or '').lower()
        if 'navigate up' in desc or 'navigate up' in text:
            return True
        if desc in ('back', 'search', 'clear', 'voice', 'cancel', 'close', 'done', 'switch input method'):
            return True
        if text in ('back', 'search', 'clear', 'voice', 'cancel', 'close', 'done'):
            return True
        if any(k in desc for k in ('clear', 'voice', 'back', 'collapse', 'search')):
            return True
        return False

    def clear_field(index):
        els = device.elements()
        field = next((e for e in els if e.get('index') == index), None)
        if not field:
            return
        field_text = (field.get('text') or '').strip()
        field_hint = (field.get('hint') or '').strip()
        if not field_text or field_text == field_hint:
            return
        clear_btn = device.find(description="Clear", clickable=True) or device.find(text="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)
            device.wait()
        else:
            device.adb_shell('input', 'keyevent', 'KEYCODE_MOVE_END')
            for _ in range(len(field_text)):
                device.adb_shell('input', 'keyevent', 'KEYCODE_DEL')

    def type_query(index, text):
        device.click(index=index)
        device.wait()
        device.input_text(text, index=index)
        device.wait()

        els = device.elements()
        field = next((e for e in els if e.get('index') == index), None)
        if field:
            current = (field.get('text') or '').strip()
            hint = (field.get('hint') or '').strip()
            if current and current != hint and text.lower() in current.lower():
                return

        # Retry with adb shell input text (spaces as %s)
        device.click(index=index)
        device.wait()
        clear_field(index)
        escaped = text.replace(' ', '%s')
        device.adb_shell('input', 'text', escaped)
        device.wait()

    def find_search_result(location):
        els = device.elements()
        parts = [p.strip().lower() for p in location.split(',') if p.strip()]
        if not parts:
            parts = [location.lower()]

        # Pass 1: element containing any part of the typed location
        for el in els:
            if not el.get('clickable') or el.get('editable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            if is_control_or_navigation(el):
                continue
            low_text = text.lower()
            low_desc = desc.lower()
            if low_text in ('history', 'categories', 'address') or low_desc in ('history', 'categories', 'address'):
                continue
            combined = (text + ' ' + desc).lower()
            if any(part in combined for part in parts):
                return el['index']

        # Pass 2: fallback to first likely result element
        for el in els:
            if not el.get('clickable') or el.get('editable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            if is_control_or_navigation(el):
                continue
            low_text = text.lower()
            low_desc = desc.lower()
            if low_text in ('history', 'categories', 'address') or low_desc in ('history', 'categories', 'address'):
                continue
            if (len(text) <= 2 and not desc) or (len(desc) <= 2 and not text):
                continue
            return el['index']
        return None

    def find_marker_button():
        els = device.elements()
        for el in els:
            if not el.get('clickable'):
                continue
            text = (el.get('text') or '').lower()
            desc = (el.get('description') or '').lower()
            if 'marker' in text or 'marker' in desc:
                return el['index']
        for kwargs in (
            {'description': 'Map marker', 'clickable': True},
            {'description': 'Add marker', 'clickable': True},
            {'text': 'Marker', 'clickable': True},
            {'text': 'Map marker', 'clickable': True},
            {'description': 'Marker', 'clickable': True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        return None

    # Open OsmAnd
    device.open_app("OsmAnd")

    # Dismiss any expanded notification shade if present
    collapse = device.find(description="Collapse", clickable=True)
    if collapse is not None:
        device.click(index=collapse)
        device.open_app("OsmAnd")

    # Locate the search input
    search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(description="Search")
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        device.settle(1)
        search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_input = device.find(editable=True)
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Focus and clear the field
    device.click(index=search_input)
    device.wait()
    clear_field(search_input)

    # Type the target location verbatim
    type_query(search_input, location)

    # Trigger search and wait for results
    device.keyboard_enter()
    device.settle(2)

    # Pick the first search result
    result = find_search_result(location)
    if result is None:
        device.scroll(direction='down')
        device.settle(1)
        result = find_search_result(location)
    if result is None:
        raise RuntimeError("No search result found for %r" % location)

    device.click(index=result)
    device.settle(2)

    # Now the place panel is shown; find and tap the MARKER control
    marker_btn = find_marker_button()
    if marker_btn is None:
        device.scroll(direction='left')
        device.settle(1)
        marker_btn = find_marker_button()
    if marker_btn is None:
        device.scroll(direction='right')
        device.settle(1)
        marker_btn = find_marker_button()
    if marker_btn is None:
        raise RuntimeError("Marker button not found on place panel")

    device.click(index=marker_btn)
    device.settle(1)

    # If a confirmation dialog appears (normally none), confirm it
    ok = device.find(text="OK", clickable=True) or device.find(text="Add", clickable=True) or device.find(description="OK", clickable=True)
    if ok is not None:
        device.click(index=ok)

    return True
