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
        """Return index of the first clickable search result.

        Tries the full location first, then the part before/after a comma,
        then the first word. This handles OsmAnd suggestions that may show
        only the place name (e.g. 'Malbun') instead of the full query.
        """
        loc = location.strip()
        keys = [loc]

        if ',' in loc:
            before = loc.split(',')[0].strip()
            if before and before not in keys:
                keys.append(before)
            after = loc.split(',')[1].strip()
            if after and after not in keys:
                keys.append(after)

        words = loc.replace(',', ' ').split()
        if words:
            first_word = words[0].strip()
            if first_word and first_word not in keys:
                keys.append(first_word)

        # Direct find first
        for key in keys:
            idx = device.find(contains=key, clickable=True, editable=False)
            if idx is not None:
                return idx

        # Fallback: iterate elements for case-insensitive match
        for el in device.elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            text = (el.get("text") or "") + " " + (el.get("description") or "")
            text_lower = text.lower()
            for key in keys:
                if key.lower() in text_lower:
                    return el["index"]
        return None

    def find_show_on_map():
        """Return index of the 'SHOW ON MAP' button if present."""
        idx = device.find(contains="SHOW ON MAP", clickable=True)
        if idx is None:
            idx = device.find(description="SHOW ON MAP", clickable=True)
        return idx

    def try_increase_radius():
        """Tap 'INCREASE SEARCH RADIUS' if present, then retry for a result."""
        radius_btn = device.find(contains="INCREASE SEARCH RADIUS")
        if radius_btn is None:
            return None
        device.click(index=radius_btn)
        device.settle(2)
        for _ in range(5):
            result = find_first_result()
            if result is not None:
                return result
            device.settle(1)
        return None

    def is_coordinate(loc: str) -> bool:
        """Return True if loc looks like a 'lat, lon' coordinate pair."""
        if ',' not in loc:
            return False
        parts = loc.split(',')
        if len(parts) != 2:
            return False
        try:
            float(parts[0].strip())
            float(parts[1].strip())
            return True
        except ValueError:
            return False

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

    # --- Do NOT press Enter: it dismisses the search overlay.
    # Wait for suggestions and pick the first result. ---
    result = None
    for _ in range(5):
        result = find_first_result()
        if result is not None:
            break
        device.settle(1)

    if result is None:
        # The full query may fail (e.g. "Malbun, Liechtenstein" not found within
        # the default radius). Try increasing the search radius first.
        result = try_increase_radius()

    if result is None:
        # Still no result: try a simplified query (the part before the comma),
        # unless the location is a coordinate pair.
        simplified = None
        if ',' in location and not is_coordinate(location):
            simplified = location.split(',')[0].strip()
            if simplified == location:
                simplified = None
        elif ',' not in location:
            words = location.split()
            if len(words) > 1:
                simplified = words[0]

        if simplified is not None:
            # Clear the current query and type the simplified one
            search_input = device.find(hint="Type to search all", editable=True)
            if search_input is None:
                search_input = device.find(editable=True)
            if search_input is not None:
                device.click(index=search_input)
                device.settle(0.5)
                clear_btn = device.find(description="Clear", clickable=True)
                if clear_btn is not None:
                    device.click(index=clear_btn)
                    device.settle(0.5)
                # Re-find search input after clearing
                search_input = device.find(hint="Type to search all", editable=True)
                if search_input is None:
                    search_input = device.find(editable=True)
                if search_input is not None:
                    device.click(index=search_input)
                    device.settle(0.5)
                    device.input_text(simplified, index=search_input)
                    device.settle(1)
                    for _ in range(5):
                        result = find_first_result()
                        if result is not None:
                            break
                        device.settle(1)

    if result is None:
        # After the simplified query, try increasing the radius again.
        result = try_increase_radius()

    if result is None:
        # Last resort: tap "SHOW ON MAP" to go to the map view and look for a marker.
        show_on_map = find_show_on_map()
        if show_on_map is not None:
            device.click(index=show_on_map)
            device.settle(1)
            marker = find_marker()
            if marker is not None:
                device.click(index=marker)
                device.settle(1)
                return True
        raise RuntimeError("No search result found for %r" % location)

    # --- Click the first search result ---
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
