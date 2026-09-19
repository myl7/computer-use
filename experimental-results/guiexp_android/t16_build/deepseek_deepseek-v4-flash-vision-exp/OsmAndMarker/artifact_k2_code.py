import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    location = binding["location"]

    # Open OsmAnd
    device.open_app("OsmAnd")
    device.settle(3)

    # Dismiss any notification shade that might be open
    if device.find(description="Collapse", clickable=True) is not None:
        device.navigate_back()
        device.settle(1)
        device.open_app("OsmAnd")
        device.settle(3)

    # Find and click the search button
    search_btn = None
    for _ in range(3):
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(text="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(hint="Search", clickable=True)
        if search_btn is not None:
            break
        device.settle(2)
    if search_btn is None:
        # Fallback: any clickable element with "search" in description
        for el in device.elements():
            if el.get("clickable") and "search" in (el.get("description") or "").lower():
                search_btn = el["index"]
                break
    if search_btn is None:
        raise RuntimeError("Search button not found in OsmAnd")

    device.click(index=search_btn)
    device.settle(2)

    # Find the search input field
    search_input = None
    for _ in range(3):
        search_input = device.find(editable=True)
        if search_input is not None:
            break
        device.settle(1)
    if search_input is None:
        for el in device.elements():
            if el.get("editable"):
                search_input = el["index"]
                break
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Type the location verbatim
    device.input_text(location, index=search_input)
    device.settle(2)

    # Find and click the first search result
    loc_lower = location.lower()

    def find_result():
        # Try to find an element whose text/description contains the location
        for el in device.elements():
            if el.get("editable"):
                continue
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "").lower()
            desc = (el.get("description") or "").lower()
            if loc_lower in text or loc_lower in desc:
                return el["index"]
        # Fallback: first clickable non-control element
        for el in device.elements():
            if el.get("editable"):
                continue
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            if not text and not desc:
                continue
            combined = (text + " " + desc).lower()
            if combined in ("search", "clear", "back", "close", "cancel"):
                continue
            if "search" in combined and "result" not in combined:
                continue
            if "clear" in combined:
                continue
            if "back" in combined:
                continue
            return el["index"]
        return None

    first_result = find_result()
    if first_result is None:
        # Try pressing enter to trigger search
        device.keyboard_enter()
        device.settle(2)
        first_result = find_result()
    if first_result is None:
        raise RuntimeError("No search result found for location: %s" % location)

    device.click(index=first_result)
    device.settle(2)

    # Find and click the MARKER button on the place panel
    marker_btn = None
    for attempt in range(3):
        # Try known labels/descriptions
        candidates = [
            {"description": "Marker", "clickable": True},
            {"text": "Marker", "clickable": True},
            {"description": "Add marker", "clickable": True},
            {"text": "Add marker", "clickable": True},
            {"description": "Mark as marker", "clickable": True},
            {"text": "Mark as marker", "clickable": True},
            {"description": "Add to map markers", "clickable": True},
            {"text": "Add to map markers", "clickable": True},
        ]
        for cand in candidates:
            marker_btn = device.find(**cand)
            if marker_btn is not None:
                break

        if marker_btn is None:
            # Search for any clickable element with "marker" in text/description
            for el in device.elements():
                if not el.get("clickable"):
                    continue
                text = (el.get("text") or "").lower()
                desc = (el.get("description") or "").lower()
                if "marker" in text or "marker" in desc:
                    if "favorite" in text or "favorite" in desc:
                        continue
                    marker_btn = el["index"]
                    break

        if marker_btn is not None:
            break

        # If not found, try to expand the bottom sheet by scrolling up
        device.scroll("up")
        device.settle(1)

    if marker_btn is None:
        raise RuntimeError("Marker button not found on place panel")

    device.click(index=marker_btn)
    device.settle(1)

    return True
