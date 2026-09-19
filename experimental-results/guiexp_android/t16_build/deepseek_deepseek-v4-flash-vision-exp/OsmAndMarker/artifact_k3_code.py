PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    location = binding["location"]

    # --- Helpers (defined inside to close over device) ---
    def find_marker():
        """Return index of a clickable element whose text/description contains 'marker'."""
        idx = device.find(contains="MARKER", clickable=True)
        if idx is not None:
            return idx
        for el in device.elements():
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "") + " " + (el.get("description") or "")
            if "marker" in text.lower():
                return el["index"]
        return None

    def find_first_result():
        """Return index of the first clickable search result containing the location."""
        loc = location.strip()
        # Direct find first
        idx = device.find(contains=loc, clickable=True, editable=False)
        if idx is not None:
            return idx
        # Fallback: iterate elements for case-insensitive match
        for el in device.elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            text = (el.get("text") or "") + " " + (el.get("description") or "")
            if loc.lower() in text.lower():
                return el["index"]
        return None

    def find_show_on_map():
        """Return index of the 'SHOW ON MAP' button if present."""
        idx = device.find(contains="SHOW ON MAP", clickable=True)
        if idx is None:
            idx = device.find(description="SHOW ON MAP", clickable=True)
        return idx

    # --- Launch OsmAnd ---
    device.open_app("OsmAnd")
    device.settle(2)

    # --- Get to search input ---
    search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            # Maybe a stray panel/overlay is open; try back once
            device.navigate_back()
            device.settle(1)
            search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        device.settle(1)
        search_input = device.find(hint="Type to search all", editable=True)
        if search_input is None:
            search_input = device.find(editable=True)
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # --- Clear existing text if any ---
    el = device.elements()[search_input]
    if el.get("text") and el["text"] != "Type to search all":
        clear_btn = device.find(description="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)
            device.settle(1)

    # --- Type the location verbatim ---
    device.click(index=search_input)
    device.settle(0.5)
    device.input_text(location, index=search_input)
    device.settle(1)

    # --- Submit search and pick first result ---
    device.keyboard_enter()
    device.settle(1)

    result = None
    for _ in range(3):
        result = find_first_result()
        if result is not None:
            break
        device.settle(1)
    if result is None:
        raise RuntimeError("No search result found for %r" % location)

    device.click(index=result)
    device.settle(1)

    # --- Get to the place panel and find MARKER ---
    marker = find_marker()
    if marker is None:
        show_on_map = find_show_on_map()
        if show_on_map is not None:
            device.click(index=show_on_map)
            device.settle(1)
            marker = find_marker()

    if marker is None:
        # Try expanding the bottom sheet / scrolling the panel
        device.scroll("up")
        device.settle(1)
        marker = find_marker()

    if marker is None:
        raise RuntimeError("MARKER control not found on place panel")

    # --- Tap MARKER (marker is saved immediately, no confirmation) ---
    device.click(index=marker)
    device.settle(1)

    return True
