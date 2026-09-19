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
        # Exclude the search field and any editable element
        def is_search_field(el):
            if el.get('editable'):
                return True
            if el.get('index') == search_field_idx:
                return True
            hint = (el.get('hint') or '').lower()
            if 'search' in hint or 'type to search' in hint:
                return True
            return False

        # Exact text match, excluding the search field
        for el in device.elements():
            if is_search_field(el):
                continue
            if el.get('text') == location and el.get('clickable'):
                return el['index']
        # Contains match, excluding the search field
        for el in device.elements():
            if is_search_field(el):
                continue
            if location in (el.get('text') or '') and el.get('clickable'):
                return el['index']
        # Contains match without requiring clickable, excluding the search field
        for el in device.elements():
            if is_search_field(el):
                continue
            if location in (el.get('text') or ''):
                return el['index']
        return None

    def find_show_on_map():
        for criteria in (
            {"text": "SHOW ON MAP", "clickable": True},
            {"text": "SHOW ON MAP"},
            {"contains": "SHOW ON MAP", "clickable": True},
            {"contains": "SHOW ON MAP"},
        ):
            idx = device.find(**criteria)
            if idx is not None:
                return idx
        for el in device.elements():
            text = (el.get('text') or '').upper()
            desc = (el.get('description') or '').upper()
            if 'SHOW ON MAP' in text or 'SHOW ON MAP' in desc:
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
        # Fallback: any element with marker in text/desc (even if not clickable)
        for el in device.elements():
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

    if result is not None:
        device.click(index=result)
        device.settle(2)
    else:
        # No direct search result; show the query on the map
        show_on_map = find_show_on_map()
        if show_on_map is None:
            raise RuntimeError("Search result not found")
        device.click(index=show_on_map)
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
