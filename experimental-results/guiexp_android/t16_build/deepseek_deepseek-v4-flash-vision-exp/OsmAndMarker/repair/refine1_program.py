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

    def find_show_on_map():
        """Return index of the 'SHOW ON MAP' button if present."""
        idx = device.find(contains="SHOW ON MAP", clickable=True)
        if idx is None:
            idx = device.find(description="SHOW ON MAP", clickable=True)
        return idx

    def find_search_button():
        """Try multiple criteria to locate the search button."""
        for kwargs in [
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"contains": "Search", "clickable": True},
            {"hint": "Search", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        return None

    def find_search_input():
        """Locate the editable search input after the search UI is open."""
        for hint in ["Type to search all", "Search", "Search OsmAnd", "Type to search"]:
            idx = device.find(hint=hint, editable=True)
            if idx is not None:
                return idx
        # Fallback: any editable element (usually the search field)
        return device.find(editable=True)

    def find_first_result(search_input_idx):
        """Return index of the first search result."""
        loc = location.strip()
        # First, try to find an element containing the location text
        idx = device.find(contains=loc, clickable=True, editable=False)
        if idx is not None:
            return idx
        # Fallback: pick the first clickable, non-editable element that is
        # not the search input, not a clear button, and has non-empty text/desc.
        elements = device.elements()
        for el in elements:
            if el["index"] == search_input_idx:
                continue
            if not el.get("clickable"):
                continue
            if el.get("editable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            combined = (text + " " + desc).lower()
            # Exclude common controls / non-result elements
            if "clear" in combined or "search" in combined:
                continue
            if "map" in combined and not text:
                continue
            if "zoom in" in combined or "zoom out" in combined:
                continue
            if "my location" in combined or "compass" in combined:
                continue
            if "settings" in combined or "menu" in combined:
                continue
            if text or desc:
                return el["index"]
        return None

    # --- Launch OsmAnd ---
    device.open_app("OsmAnd")
    device.settle(2)

    # --- Get to search input ---
    search_input = device.find(hint="Type to search all", editable=True)
    if search_input is None:
        search_btn = find_search_button()
        if search_btn is None:
            # Maybe a stray panel/overlay is open; try back once
            device.navigate_back()
            device.settle(1)
            search_btn = find_search_button()
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        device.settle(1)
        search_input = find_search_input()
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # --- Clear existing text if any ---
    el = device.elements()[search_input]
    if el.get("text") and el["text"] != "Type to search all":
        clear_btn = device.find(description="Clear", clickable=True)
        if clear_btn is None:
            clear_btn = device.find(contains="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)
            device.settle(1)

    # --- Type the location verbatim ---
    device.click(index=search_input)
    device.settle(0.5)
    device.input_text(location, index=search_input)
    device.settle(1)

    # --- Wait for search results to appear ---
    result = None
    for _ in range(3):
        result = find_first_result(search_input)
        if result is not None:
            break
        device.settle(1)

    # If no results yet, try pressing enter to submit the search
    if result is None:
        device.keyboard_enter()
        device.settle(1)
        for _ in range(3):
            result = find_first_result(search_input)
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
