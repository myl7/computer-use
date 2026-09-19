PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "location": {
            "type": "string",
            "description": "Place name or 'lat, lon' coordinate pair to mark in OsmAnd"
        }
    },
    "required": ["location"]
}


def program(device, binding: dict) -> bool:
    location = binding["location"]

    # 1. Open OsmAnd and ensure the map is in the foreground.
    device.open_app("OsmAnd")
    device.settle(2)

    # If the notification shade / quick settings is overlaying, dismiss it.
    if device.find(description="Collapse", clickable=True) is not None:
        device.navigate_back()
        device.settle(1)
        device.open_app("OsmAnd")
        device.settle(2)

    # 2. Locate the search field. If it's not already visible, tap the Search button.
    search_input = device.find(editable=True)
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(text="Search", clickable=True)
        if search_btn is None:
            raise RuntimeError("OsmAnd search button not found")
        device.click(index=search_btn)
        device.settle(1)
        search_input = device.find(editable=True)
        if search_input is None:
            raise RuntimeError("Search input not found after tapping Search")

    # 3. Type the location verbatim into the search field.
    device.input_text(location, index=search_input)
    device.settle(1)

    # 4. Pick the first search result.
    # Helper to locate the first result in the current screen state.
    def _find_search_result():
        elements = device.elements()

        # Exact full match (preferred).
        for el in elements:
            if el.get("editable"):
                continue
            text = el.get("text") or ""
            if text and location.lower() in text.lower():
                return el["index"]

        # Fallback: match any comma-separated term (e.g., "Malbun").
        terms = [t.strip() for t in location.split(",") if t.strip()]
        for el in elements:
            if el.get("editable"):
                continue
            text = el.get("text") or ""
            if not text:
                continue
            for term in terms:
                if len(term) > 2 and term.lower() in text.lower():
                    return el["index"]

        # Last resort: first element after the search input with meaningful text.
        elements_sorted = sorted(elements, key=lambda e: e["index"])
        after = False
        for el in elements_sorted:
            if el["index"] == search_input:
                after = True
                continue
            if after:
                if el.get("editable"):
                    continue
                text = el.get("text") or ""
                desc = (el.get("description") or "").lower()
                if text.strip() and "clear" not in desc and "search" not in desc and "voice" not in desc:
                    return el["index"]
        return None

    result_index = _find_search_result()
    if result_index is None:
        # If no suggestions appeared, press Enter and try again.
        device.keyboard_enter()
        device.settle(2)
        result_index = _find_search_result()
        if result_index is None:
            raise RuntimeError("No search result found for %r" % location)

    device.click(index=result_index)
    device.settle(2)

    # 5. Click the Marker control on the place panel (not the favorites star).
    def _find_marker_index():
        # Exact text / description matches.
        idx = device.find(text="Marker", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="Marker", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Add marker", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="Add marker", clickable=True)
        if idx is not None:
            return idx

        # Fallback: scan for text containing "marker", then description.
        for el in device.elements():
            if el.get("editable") or not el.get("clickable"):
                continue
            text = el.get("text") or ""
            if "marker" in text.lower():
                return el["index"]

        for el in device.elements():
            if el.get("editable") or not el.get("clickable"):
                continue
            desc = el.get("description") or ""
            if "marker" in desc.lower():
                return el["index"]

        return None

    marker_index = _find_marker_index()
    if marker_index is None:
        raise RuntimeError("Marker control not found on place panel")

    device.click(index=marker_index)
    device.settle(1)

    return True
