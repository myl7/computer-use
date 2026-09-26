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

    def is_keyboard_key(el):
        desc = (el.get('description') or '').strip()
        text = (el.get('text') or '').strip()
        if desc and len(desc) <= 2 and not text:
            return True
        keyboard_keys = {
            'back', 'switch input method', 'exclamation', 'at', 'hash', 'dollar',
            'percent', 'circumflex accent', 'ampersand', 'asterisk', 'left parenthesis',
            'right parenthesis', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p',
            'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'z', 'x', 'c', 'v', 'b',
            'n', 'm', 'comma', 'period', 'slash', 'space', 'enter', 'delete', 'shift',
            'alt', 'ctrl', 'tab', 'caps lock', 'arrow left', 'arrow right', 'arrow up',
            'arrow down', 'escape', 'menu', 'home', 'end', 'page up', 'page down'
        }
        if desc.lower() in keyboard_keys:
            return True
        if desc and len(desc) == 1 and not text:
            return True
        return False

    def find_search_field():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(editable=True)
        if idx is not None:
            return idx
        els = device.elements()
        for el in els:
            if el.get('editable') and 'search' in (el.get('hint') or '').lower():
                return el['index']
        return None

    def search_field_contains(text):
        field = find_search_field()
        if field is None:
            return False
        els = device.elements()
        f = next((e for e in els if e.get('index') == field), None)
        if not f:
            return False
        current = (f.get('text') or '').strip()
        hint = (f.get('hint') or '').strip()
        if current and current != hint and text.lower() in current.lower():
            return True
        return False

    def clear_search_field():
        field = find_search_field()
        if field is None:
            return
        els = device.elements()
        f = next((e for e in els if e.get('index') == field), None)
        if not f:
            return
        field_text = (f.get('text') or '').strip()
        field_hint = (f.get('hint') or '').strip()
        if not field_text or field_text == field_hint:
            return
        clear_btn = device.find(description="Clear", clickable=True)
        if clear_btn is None:
            clear_btn = device.find(text="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)
            device.wait()
        else:
            device.adb_shell('input', 'keyevent', 'KEYCODE_MOVE_END')
            for _ in range(len(field_text)):
                device.adb_shell('input', 'keyevent', 'KEYCODE_DEL')

    def type_query(text):
        field = find_search_field()
        if field is None:
            raise RuntimeError("Search input field not found")
        # Focus the field
        device.click(index=field)
        device.wait()
        # Try device.input_text first
        device.input_text(text, index=field)
        device.wait()
        if search_field_contains(text):
            return
        # Retry with adb shell input text
        field = find_search_field()
        if field is None:
            raise RuntimeError("Search input field not found")
        device.click(index=field)
        device.wait()
        clear_search_field()
        device.click(index=field)
        device.wait()
        escaped = text.replace(' ', '%s')
        device.adb_shell('input', 'text', escaped)
        device.wait()
        if search_field_contains(text):
            return
        # Final attempt with device.input_text after clearing
        field = find_search_field()
        if field is None:
            raise RuntimeError("Search input field not found")
        device.click(index=field)
        device.wait()
        clear_search_field()
        device.input_text(text, index=field)
        device.wait()
        if search_field_contains(text):
            return
        raise RuntimeError("Failed to type query into search field")

    def find_search_result(location):
        els = device.elements()
        parts = [p.strip().lower() for p in location.split(',') if p.strip()]
        if not parts:
            parts = [location.lower()]
        first_part = parts[0]
        full_lower = location.lower()
        candidates = []
        for el in els:
            if not el.get('clickable') or el.get('editable'):
                continue
            if is_control_or_navigation(el) or is_keyboard_key(el):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            low_text = text.lower()
            low_desc = desc.lower()
            if low_text in ('history', 'categories', 'address') or low_desc in ('history', 'categories', 'address'):
                continue
            combined = (text + ' ' + desc).lower()
            if full_lower in combined:
                return el['index']
            if first_part in combined:
                candidates.append(el['index'])
        if candidates:
            return candidates[0]
        return None

    def expand_search_radius():
        idx = device.find(text="INCREASE SEARCH RADIUS", clickable=True)
        if idx is None:
            idx = device.find(description="INCREASE SEARCH RADIUS", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return True
        return False

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
    search_input = find_search_field()
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(description="Search")
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        device.settle(1)
        search_input = find_search_field()
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Focus and clear the field
    device.click(index=search_input)
    device.wait()
    clear_search_field()

    # Type the target location verbatim
    type_query(location)

    # Trigger search and wait for results
    device.keyboard_enter()
    device.settle(2)

    # Pick the first search result
    result = find_search_result(location)
    if result is None:
        if expand_search_radius():
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
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        ok = device.find(text="Add", clickable=True)
    if ok is None:
        ok = device.find(description="OK", clickable=True)
    if ok is not None:
        device.click(index=ok)

    return True
