def program(device, binding: dict) -> bool:
    location = binding['location']

    def find_search_button():
        for desc in ("Search", "Search button", "Open search"):
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx
        for text in ("Search", "Search icon"):
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        for el in device.elements():
            if el.get('clickable') and not el.get('editable'):
                desc = (el.get('description') or '').lower()
                text = (el.get('text') or '').lower()
                if 'search' in desc or 'search' in text:
                    return el['index']
        return None

    def find_search_field():
        idx = device.find(editable=True)
        if idx is not None:
            return idx
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        for el in device.elements():
            if el.get('editable'):
                return el['index']
            hint = (el.get('hint') or '').lower()
            if 'search' in hint:
                return el['index']
        return None

    def is_keyboard_key(el):
        desc = (el.get('description') or '').strip()
        if len(desc) == 1:
            return True
        if desc.lower() in {'shift', 'space', 'enter', 'backspace', 'delete', 'symbols', 'emojis', 'switch input method'}:
            return True
        return False

    def is_control(el):
        desc = (el.get('description') or '').strip().lower()
        text = (el.get('text') or '').strip().lower()
        controls = {
            'navigate up', 'back', 'history', 'categories', 'address',
            'clear', 'search', 'marker', 'favorites', 'directions', 'share',
            'more', 'close', 'switch input method'
        }
        if desc in controls or text in controls:
            return True
        return False

    def is_non_result(el):
        desc = (el.get('description') or '').strip().lower()
        text = (el.get('text') or '').strip().lower()
        non_results = {
            'no search results?', 'provide feedback', 'show on map',
            'could not find anything', 'change the search or increase its radius.',
            'increase search radius', 'send', 'clear', 'navigate up', 'back',
            'switch input method', 'voice input', 'search', 'marker', 'favorites',
            'directions', 'share', 'more', 'close', 'history', 'categories', 'address'
        }
        if desc in non_results or text in non_results:
            return True
        if 'no search results' in text or 'could not find' in text or 'increase search radius' in text or 'change the search' in text or 'increase its radius' in text:
            return True
        return False

    def find_first_result(search_field_idx, location):
        elements = device.elements()
        # First pass: pick the first valid clickable non-control element with text
        for el in elements:
            if el.get('index') == search_field_idx:
                continue
            if not el.get('clickable'):
                continue
            if el.get('editable'):
                continue
            if is_keyboard_key(el):
                continue
            if is_control(el) or is_non_result(el):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            if text:
                return el['index']
            if desc and len(desc) > 1:
                return el['index']
        # Second pass: try to find an element containing the location text
        for el in elements:
            if el.get('index') == search_field_idx:
                continue
            if el.get('editable'):
                continue
            if not el.get('clickable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if location in text or location in desc:
                return el['index']
        return None

    def find_increase_search_radius():
        for _ in range(3):
            idx = device.find(text="INCREASE SEARCH RADIUS", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(contains="INCREASE SEARCH RADIUS", clickable=True)
            if idx is not None:
                return idx
            for el in device.elements():
                if el.get('clickable'):
                    text = (el.get('text') or '').upper()
                    if 'INCREASE SEARCH RADIUS' in text:
                        return el['index']
            device.settle(1)
        return None

    def find_marker():
        for _ in range(10):
            idx = device.find(text="MARKER", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(description="MARKER", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(contains="MARKER", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(contains="marker", clickable=True)
            if idx is not None:
                return idx
            for el in device.elements():
                if el.get('clickable'):
                    desc = (el.get('description') or '').lower()
                    text = (el.get('text') or '').lower()
                    if 'marker' in desc or 'marker' in text:
                        return el['index']
            device.settle(1)
        return None

    # Open OsmAnd
    device.open_app("OsmAnd")
    device.settle(2)

    # Ensure search screen is open
    search_field = find_search_field()
    if search_field is None:
        search_btn = find_search_button()
        if search_btn is None:
            raise RuntimeError("Search button not found")
        device.click(index=search_btn)
        device.settle(2)
        search_field = find_search_field()
    if search_field is None:
        raise RuntimeError("Search field not found")

    # Focus search field and type location verbatim
    device.click(index=search_field)
    device.settle(1)
    device.input_text(location, index=search_field)
    device.settle(2)

    # Wait for results, increasing search radius if needed
    result = None
    for attempt in range(5):
        result = find_first_result(search_field, location)
        if result is not None:
            break
        # If no results, try to increase search radius
        increase_btn = find_increase_search_radius()
        if increase_btn is not None:
            device.click(index=increase_btn)
            device.settle(2)
            continue
        # Fallback: press enter to trigger search
        device.keyboard_enter()
        device.settle(2)
        result = find_first_result(search_field, location)
        if result is not None:
            break
        device.settle(1)

    if result is None:
        raise RuntimeError("Search result not found")

    device.click(index=result)
    device.settle(2)

    # Click the MARKER control on the place panel
    marker = find_marker()
    if marker is None:
        raise RuntimeError("MARKER button not found")
    device.click(index=marker)
    device.settle(1)

    return True


PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark in OsmAnd"
    }
}
