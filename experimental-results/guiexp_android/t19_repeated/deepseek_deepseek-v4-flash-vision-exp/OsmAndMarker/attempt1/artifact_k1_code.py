PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark"
    }
}

def program(device, binding: dict) -> bool:
    location = binding["location"]

    # Bring OsmAnd to the foreground
    device.open_app("OsmAnd")
    device.settle(2.0)

    # Find and tap the search button
    search_btn = None
    for desc in ["Search", "Search OsmAnd", "Search map", "Search address"]:
        search_btn = device.find(description=desc)
        if search_btn is not None:
            break
    if search_btn is None:
        search_btn = device.find(text="Search")
    if search_btn is None:
        els = device.elements()
        for e in els:
            if e.get("clickable") and (e.get("text") == "Search" or e.get("description") == "Search"):
                search_btn = e["index"]
                break
    if search_btn is None:
        # If already on the search screen, there will be an editable field
        search_input = device.find(editable=True)
        if search_input is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
    else:
        device.click(index=search_btn)
        device.settle(2.0)

    # Find the search input field
    search_input = device.find(editable=True)
    if search_input is None:
        search_input = device.find(hint="Search") or device.find(text="Search")
    if search_input is None:
        els = device.elements()
        for e in els:
            if e.get("editable"):
                search_input = e["index"]
                break
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Focus the field and type the location verbatim
    device.click(index=search_input)
    device.settle(0.5)
    device.input_text(location, index=search_input)
    device.settle(1.0)
    device.keyboard_enter()
    device.settle(2.0)

    # Pick the first search result
    first_result = None
    els = device.elements()
    excluded_texts = {"Search", "Back", "Cancel", "Clear", "Close", "Done", "Search results"}
    excluded_descs = {"Search", "Back", "Cancel", "Clear", "Close", "Done"}
    for e in els:
        idx = e.get("index")
        if idx == search_input:
            continue
        if not e.get("clickable"):
            continue
        text = e.get("text") or ""
        desc = e.get("description") or ""
        if text in excluded_texts or desc in excluded_descs:
            continue
        first_result = idx
        break

    if first_result is None:
        # Fallback: find an element whose text contains the location, excluding the search field
        for e in els:
            if e.get("index") == search_input:
                continue
            text = e.get("text") or ""
            if location.lower() in text.lower():
                first_result = e["index"]
                break

    if first_result is None:
        raise RuntimeError("First search result not found")

    device.click(index=first_result)
    device.settle(2.0)

    # Find and tap the MARKER control on the place panel
    marker_btn = None
    marker_btn = device.find(text="MARKER") or device.find(text="Marker")
    if marker_btn is None:
        marker_btn = device.find(description="MARKER") or device.find(description="Marker") or device.find(description="Add marker")
    if marker_btn is None:
        marker_btn = device.find(contains="MARKER") or device.find(contains="Marker")
    if marker_btn is None:
        els = device.elements()
        for e in els:
            text = e.get("text") or ""
            desc = e.get("description") or ""
            if "marker" in text.lower() or "marker" in desc.lower():
                marker_btn = e["index"]
                break
    if marker_btn is None:
        # The panel may need a scroll to reveal the marker button
        device.scroll("down")
        device.settle(1.0)
        marker_btn = device.find(text="MARKER") or device.find(text="Marker") or device.find(description="MARKER") or device.find(description="Marker") or device.find(description="Add marker")
        if marker_btn is None:
            els = device.elements()
            for e in els:
                text = e.get("text") or ""
                desc = e.get("description") or ""
                if "marker" in text.lower() or "marker" in desc.lower():
                    marker_btn = e["index"]
                    break

    if marker_btn is None:
        raise RuntimeError("MARKER button not found on place panel")

    device.click(index=marker_btn)
    device.settle(1.0)

    return True
