PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place to mark: either a place name such as 'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair",
    }
}


def program(device, binding: dict) -> bool:
    location = binding["location"]

    # Bring OsmAnd to the foreground
    device.open_app("OsmAnd")
    device.settle(2.0)

    # 1. Find and tap the search field
    search_idx = None
    for kwargs in [
        {"description": "Search"},
        {"hint": "Search"},
        {"text": "Search"},
        {"description": "Search OsmAnd"},
        {"hint": "Search OsmAnd"},
        {"description": "Search the map"},
        {"hint": "Search the map"},
    ]:
        search_idx = device.find(**kwargs)
        if search_idx is not None:
            break

    if search_idx is None:
        els = device.elements()
        for e in els:
            if e.get("clickable"):
                text = (e.get("text") or "").strip().lower()
                hint = (e.get("hint") or "").lower()
                desc = (e.get("description") or "").lower()
                if text == "search" or "search" in hint or "search" in desc:
                    search_idx = e["index"]
                    break

    if search_idx is None:
        raise RuntimeError("OsmAnd search field not found")

    device.click(index=search_idx)
    device.settle(1.0)

    # 2. Find the editable search input and type the location verbatim
    input_idx = None
    els = device.elements()
    for e in els:
        if e.get("editable"):
            text = (e.get("text") or "").strip().lower()
            hint = (e.get("hint") or "").lower()
            desc = (e.get("description") or "").lower()
            if "search" in hint or "search" in desc or text == "search":
                input_idx = e["index"]
                break

    if input_idx is None:
        for e in els:
            if e.get("editable"):
                input_idx = e["index"]
                break

    if input_idx is None:
        raise RuntimeError("OsmAnd search input not found")

    device.input_text(location, index=input_idx)
    device.settle(1.0)
    device.keyboard_enter()
    device.settle(2.0)

    # 3. Pick the first search result
    result_idx = None

    # Prefer a result whose text contains the exact location
    try:
        idx = device.find(contains=location, clickable=True)
        if idx is not None:
            for e in device.elements():
                if e["index"] == idx and not e.get("editable"):
                    result_idx = idx
                    break
    except Exception:
        pass

    if result_idx is None:
        els = device.elements()
        candidates = []
        for e in els:
            if not e.get("clickable"):
                continue
            text = (e.get("text") or "").strip()
            if not text:
                continue
            if e.get("editable"):
                continue
            low = text.lower()
            if low in ("search", "close", "back"):
                continue
            candidates.append(e)

        if not candidates:
            raise RuntimeError("No search results found")

        loc_lower = location.lower()
        for e in candidates:
            if loc_lower in (e.get("text") or "").lower():
                result_idx = e["index"]
                break

        if result_idx is None:
            for e in candidates:
                if len((e.get("text") or "").strip()) > 1:
                    result_idx = e["index"]
                    break

        if result_idx is None:
            result_idx = candidates[0]["index"]

    device.click(index=result_idx)
    device.settle(2.0)

    # 4. Find and tap the MARKER control (not the favorites star)
    marker_idx = None
    for kwargs in [
        {"text": "Marker"},
        {"description": "Marker"},
        {"hint": "Marker"},
        {"text": "Add marker"},
        {"description": "Add marker"},
        {"hint": "Add marker"},
        {"description": "Add a map marker"},
        {"hint": "Add a map marker"},
        {"description": "Mark as a map marker"},
        {"hint": "Mark as a map marker"},
    ]:
        marker_idx = device.find(**kwargs)
        if marker_idx is not None:
            break

    if marker_idx is None:
        els = device.elements()
        for e in els:
            if not e.get("clickable"):
                continue
            text = (e.get("text") or "").lower()
            desc = (e.get("description") or "").lower()
            hint = (e.get("hint") or "").lower()
            if "marker" in text or "marker" in desc or "marker" in hint:
                # Skip the favorites star if it is adjacent / mentioned
                if "favorite" in text or "favorite" in desc or "favorite" in hint:
                    continue
                marker_idx = e["index"]
                break

    if marker_idx is None:
        raise RuntimeError("Marker control not found")

    device.click(index=marker_idx)
    device.settle(1.0)

    return True
