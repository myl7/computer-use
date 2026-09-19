PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark in OsmAnd",
        "required": True
    }
}


_CONTROLS = {
    "navigate up", "back", "switch input method", "clear", "search",
    "voice", "history", "categories", "address", "collapse", "cancel",
    "close", "done", "favorites", "favourites", "more options",
    "directions", "share", "menu",
}


def _is_osmand_map(device):
    """Return True if the current screen looks like the OsmAnd map view."""
    try:
        els = device.elements()
    except Exception:
        return False
    return any((e.get("description") == "Search" or e.get("text") == "Search") for e in els)


def _find_result(device, location):
    """Return the index of the first plausible search-result row."""
    els = device.elements()
    q = location.split(",")[0].strip().lower()
    fallback = None
    partial = None

    for el in els:
        if el.get("editable"):
            continue
        text = (el.get("text") or "").strip()
        desc = (el.get("description") or "").strip()
        if not text and not desc:
            continue
        low_text = text.lower()
        low_desc = desc.lower()

        # Skip known controls / irrelevant rows.
        if low_text in _CONTROLS or low_desc in _CONTROLS:
            continue
        if low_text.replace(" ", "") in ("history", "categories", "address") or \
           low_desc.replace(" ", "") in ("history", "categories", "address"):
            continue
        if len(text) == 1 and text.isalnum():
            continue

        blob = low_text + " " + low_desc

        # First tier: exact / prefix match on the typed place name.
        if q and (low_text == q or low_text.startswith(q) or
                  low_desc == q or low_desc.startswith(q)):
            return el["index"]

        # Second tier: substring match.
        if partial is None and q and (q in low_text or q in low_desc):
            partial = el["index"]

        # Fallback: first result-like row.
        if fallback is None:
            fallback = el["index"]

    if partial is not None:
        return partial
    return fallback


def _submit_search(device):
    """Press the keyboard action key to fire the search."""
    try:
        device.keyboard_enter()
        return True
    except Exception:
        pass
    for desc in ("Search", "Enter", "Return", "Go", "Done"):
        idx = device.find(description=desc, clickable=True)
        if idx is not None:
            device.click(index=idx)
            return True
    return False


def _find_marker(device):
    """Return the index of the place panel's MARKER control (not the star)."""
    els = device.elements()
    fallback = None
    for el in els:
        if not el.get("clickable"):
            continue
        text = (el.get("text") or "").lower()
        desc = (el.get("description") or "").lower()
        blob = text + " " + desc
        # Skip favorite / remove / delete actions.
        if "favorite" in blob or "favourite" in blob:
            continue
        if "remove" in blob or "delete" in blob:
            continue
        if "marker" in blob:
            if "add" in blob:
                return el["index"]
            if fallback is None:
                fallback = el["index"]
    if fallback is not None:
        return fallback
    for d in ("Add marker", "Map marker", "Add marker to map",
              "Marker", "Add to markers"):
        idx = device.find(description=d, clickable=True)
        if idx is not None:
            return idx
    idx = device.find(text="Marker", clickable=True)
    if idx is not None:
        return idx
    return None


def program(device, binding: dict) -> bool:
    location = binding["location"]
    if not isinstance(location, str):
        raise ValueError("binding['location'] must be a string")

    # Open OsmAnd. If the adb/controller connection hiccups, try to recover
    # by checking whether the app is already in the foreground.
    try:
        device.open_app("OsmAnd")
    except Exception:
        if not _is_osmand_map(device):
            raise

    # Dismiss any expanded notification shade if present
    collapse = device.find(description="Collapse", clickable=True)
    if collapse is not None:
        device.click(index=collapse)
        try:
            device.open_app("OsmAnd")
        except Exception:
            if not _is_osmand_map(device):
                raise

    # Locate the search input directly; if not, tap the Search button to open
    # the search screen.
    search_input = device.find(editable=True, hint="Type to search all")
    if search_input is None:
        search_input = device.find(editable=True)
    if search_input is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is None:
            search_btn = device.find(description="Search")
        if search_btn is None:
            raise RuntimeError("Search button not found on OsmAnd map screen")
        device.click(index=search_btn)
        search_input = device.find(editable=True, hint="Type to search all")
        if search_input is None:
            search_input = device.find(editable=True)
    if search_input is None:
        raise RuntimeError("Search input field not found")

    # Focus the search field
    device.click(index=search_input)
    search_input = device.find(editable=True)
    if search_input is None:
        search_input = device.find(hint="Type to search all")
    if search_input is None:
        raise RuntimeError("Search input field lost focus")

    # Clear any pre-existing text in the field (best effort)
    els = device.elements()
    field_el = next((e for e in els if e.get("index") == search_input), None)
    if field_el and (field_el.get("text") or "").strip():
        clear_btn = device.find(description="Clear", clickable=True)
        if clear_btn is None:
            clear_btn = device.find(text="Clear", clickable=True)
        if clear_btn is not None:
            device.click(index=clear_btn)
            search_input = device.find(editable=True)
            if search_input is None:
                search_input = device.find(hint="Type to search all")

    # Type the target location verbatim
    device.input_text(location, index=search_input)

    # Pick the first search result (fire the search if needed)
    result = _find_result(device, location)
    if result is None:
        _submit_search(device)
        device.settle(1)
        result = _find_result(device, location)
    if result is None:
        device.wait()
        result = _find_result(device, location)
    if result is None:
        raise RuntimeError("No search result found for %r" % location)

    device.click(index=result)

    # Now the place panel is shown. Find and tap the MARKER control.
    device.settle(1)
    marker_btn = _find_marker(device)
    if marker_btn is None:
        device.wait()
        marker_btn = _find_marker(device)
    if marker_btn is None:
        raise RuntimeError("Marker button not found on place panel")

    device.click(index=marker_btn)

    return True
