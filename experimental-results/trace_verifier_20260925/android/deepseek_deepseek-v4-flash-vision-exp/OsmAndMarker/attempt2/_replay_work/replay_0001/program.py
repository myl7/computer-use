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

    # Open OsmAnd
    device.open_app("OsmAnd")

    # Dismiss any expanded notification shade if present
    collapse = device.find(description="Collapse", clickable=True)
    if collapse is not None:
        device.click(index=collapse)
        device.open_app("OsmAnd")

    # Try to locate the search input directly; if not, tap the Search button.
    search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(description="Search")
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_input = device.find(editable=True)
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Focus the search field
    device.click(index=search_input)

    # Clear any pre-existing text in the field
    els = device.elements()
    field_el = next((e for e in els if e.get('index') == search_input), None)
    if field_el and (field_el.get('text') or '').strip():
        clear_btn = device.find(description="Clear", clickable=True) or device.find(text="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)

    # Type the target location verbatim
    device.input_text(location, index=search_input)

    # Pick the first search result
    elements = device.elements()
    result = None

    # Prefer an element that contains the typed location text
    for el in elements:
        if not el.get('clickable') or el.get('editable'):
            continue
        text = (el.get('text') or '').strip()
        desc = (el.get('description') or '').strip()
        if location in text or location in desc:
            result = el['index']
            break

    # Fallback: first clickable non-editable text/description element that isn't a known control
    if result is None:
        for el in elements:
            if not el.get('clickable') or el.get('editable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            low_text = text.lower()
            low_desc = desc.lower()
            if low_text in ('clear', 'voice', 'search', 'back', 'cancel', 'close', 'done'):
                continue
            if any(k in low_desc for k in ('clear', 'voice', 'back', 'collapse', 'search')):
                continue
            result = el['index']
            break

    if result is None:
        raise RuntimeError("No search result found for %r" % location)

    device.click(index=result)

    # Now the place panel is shown. Find and tap the MARKER control.
    elements = device.elements()
    marker_btn = None
    for el in elements:
        if not el.get('clickable'):
            continue
        text = (el.get('text') or '').lower()
        desc = (el.get('description') or '').lower()
        if 'marker' in text or 'marker' in desc:
            marker_btn = el['index']
            break

    if marker_btn is None:
        marker_btn = device.find(description="Map marker", clickable=True)
    if marker_btn is None:
        marker_btn = device.find(description="Add marker", clickable=True)
    if marker_btn is None:
        marker_btn = device.find(text="Marker", clickable=True)
    if marker_btn is None:
        raise RuntimeError("Marker button not found on place panel")

    device.click(index=marker_btn)

    return True
