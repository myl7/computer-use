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
            if el.get('editable') or (el.get('hint') and 'search' in el.get('hint').lower()):
                return el['index']
        return None

    def find_first_result(search_field_idx):
        # Exact text match, excluding the search field
        for el in device.elements():
            if el.get('index') == search_field_idx:
                continue
            if el.get('text') == location and el.get('clickable'):
                return el['index']
        # Contains match, excluding the search field
        for el in device.elements():
            if el.get('index') == search_field_idx:
                continue
            if location in (el.get('text') or '') and el.get('clickable'):
                return el['index']
        # Contains match without requiring clickable, excluding the search field
        for el in device.elements():
            if el.get('index') == search_field_idx:
                continue
            if location in (el.get('text') or ''):
                return el['index']
        # Fallback: first clickable element that looks like a suggestion
        for el in device.elements():
            if el.get('index') == search_field_idx:
                continue
            if not el.get('clickable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            low_text = text.lower()
            low_desc = desc.lower()
            if 'clear' in low_text or 'clear' in low_desc:
                continue
            if 'back' in low_desc and 'button' in low_desc:
                continue
            if 'search' in low_desc and 'button' in low_desc:
                continue
            if 'show on map' in low_text or 'show on map' in low_desc:
                continue
            return el['index']
        return None

    def find_marker():
        idx = device.find(text="MARKER", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="MARKER", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains="MARKER", clickable=True)
        if idx is not None:
            return idx
        for el in device.elements():
            if el.get('clickable'):
                desc = (el.get('description') or '').lower()
                text = (el.get('text') or '').lower()
                if 'marker' in desc or 'marker' in text:
                    return el['index']
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
        device.settle(1)
        search_field = find_search_field()
    if search_field is None:
        raise RuntimeError("Search field not found")

    # Type location verbatim
    device.input_text(location, index=search_field)
    device.settle(2)

    # Pick first search result
    result = find_first_result(search_field)
    if result is None:
        # Try pressing enter to trigger search
        device.keyboard_enter()
        device.settle(2)
        result = find_first_result(search_field)
    if result is None:
        raise RuntimeError("Search result not found")

    device.click(index=result)
    device.settle(2)

    # Click MARKER control on the place panel
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
