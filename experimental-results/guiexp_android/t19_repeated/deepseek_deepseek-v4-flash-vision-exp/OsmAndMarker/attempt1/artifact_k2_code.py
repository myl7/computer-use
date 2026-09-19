PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "Place name or 'lat, lon' coordinate pair to mark"
        }
    },
    "required": ["location"]
}

def program(device, binding: dict) -> bool:
    location = binding['location']

    def ensure_osmand():
        device.open_app("OsmAnd")
        device.settle(2)
        # Dismiss notification shade if present
        collapse = device.find(description="Collapse", clickable=True)
        if collapse is not None:
            device.click(index=collapse)
            device.settle(1)
        # If search button not present, try back to dismiss dialog
        if device.find(description="Search", clickable=True) is None and device.find(editable=True) is None:
            device.navigate_back()
            device.settle(1)
            device.open_app("OsmAnd")
            device.settle(2)
        # Verify OsmAnd is in foreground
        if device.find(description="Search", clickable=True) is None and device.find(editable=True) is None:
            raise RuntimeError("OsmAnd did not open")

    def find_search_input():
        search_input = device.find(editable=True)
        if search_input is None:
            search_btn = device.find(description="Search", clickable=True)
            if search_btn is None:
                raise RuntimeError("Search button not found")
            device.click(index=search_btn)
            device.settle(1)
            search_input = device.find(editable=True)
        if search_input is None:
            raise RuntimeError("Could not find search input")
        return search_input

    def find_first_result():
        # Try exact/contains match first, excluding the editable search field
        idx = device.find(text=location, contains=True, clickable=True, editable=False)
        if idx is not None:
            return idx
        # Fallback: first clickable element with non-empty text that is not the search field
        elements = device.elements()
        for el in elements:
            if el.get('clickable') and not el.get('editable') and el.get('text'):
                if el.get('description') == 'Search':
                    continue
                if el.get('text') == 'Search':
                    continue
                return el['index']
        return None

    def find_marker_button():
        idx = device.find(text='Marker', clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description='Marker', clickable=True)
        if idx is not None:
            return idx
        elements = device.elements()
        for el in elements:
            if el.get('clickable') and el.get('text'):
                text = el['text']
                if 'Marker' in text and 'Favorites' not in text and 'Favorite' not in text:
                    return el['index']
        return None

    ensure_osmand()
    search_input = find_search_input()
    device.input_text(location, index=search_input)
    device.settle(1)  # wait for search results

    result_idx = find_first_result()
    if result_idx is None:
        raise RuntimeError("No search result found for %r" % location)
    device.click(index=result_idx)
    device.settle(2)  # wait for place panel

    marker_idx = find_marker_button()
    if marker_idx is None:
        raise RuntimeError("Marker button not found")
    device.click(index=marker_idx)
    device.settle(1)

    return True
