PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to save as a favorite: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "It is typed verbatim into OsmAnd's search field."
        ),
    }
}

_RADIUS_LABELS = ("INCREASE SEARCH RADIUS", "SEARCH RADIUS")


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _label_of(el):
    return el.get("text") or ""


def _is_radius_label(text):
    low = (text or "").lower()
    return any(r.lower() in low for r in _RADIUS_LABELS)


def _coord_token_in(location, text):
    if "," not in location:
        return False
    for token in location.split(","):
        token = token.strip()
        if len(token) >= 3 and any(c.isdigit() for c in token) and token in text:
            return True
    return False


def _find_search_field(device):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is None:
        idx = device.find(hint="search", contains=True, editable=True, clickable=True)
    if idx is None:
        for el in _elements(device):
            hint = (el.get("hint") or "").lower()
            if el.get("editable") and ("search" in hint or "type" in hint):
                return el["index"]
    return idx


def _find_result_index(device, location):
    elements = _elements(device)
    # 1) verbatim match on a non-editable row's text (skips the echoing search box
    #    and the 'INCREASE SEARCH RADIUS' retry row)
    for el in elements:
        if el.get("editable"):
            continue
        label = _label_of(el)
        if not label or _is_radius_label(label):
            continue
        if location in label or _coord_token_in(location, label):
            return el["index"]
    # 2) token match (first token of a place name / parts of a coordinate pair),
    #    covers rows like 'Country <place>' or reformatted coordinates
    tokens = [t.strip() for t in location.split(",") if t.strip()]
    for el in elements:
        if el.get("editable"):
            continue
        label = _label_of(el)
        if not label or _is_radius_label(label):
            continue
        for tok in tokens:
            if len(tok) >= 3 and tok in label:
                return el["index"]
    # 3) verbatim match anywhere on a clickable non-editable row
    for el in elements:
        if el.get("editable") or not el.get("clickable"):
            continue
        hay = " ".join(str(el.get(k) or "") for k in ("text", "hint", "description"))
        if location in hay:
            return el["index"]
    return None


def _find_favorite_button(device):
    # The FAVORITES (star) control on the place panel; must NOT pick the
    # marker/flag control next to it.
    for desc in ("Add favorite", "Favorite", "Add to favorites", "Favorites",
                 "Add to favorite", "star", "Star"):
        idx = device.find(description=desc)
        if idx is not None:
            return idx
    for el in _elements(device):
        d = (el.get("description") or "").lower()
        t = _label_of(el).lower()
        if ("favorite" in d or "favorite" in t or "star" in d) and \
                "marker" not in d and "marker" not in t:
            return el["index"]
    return None


def _find_dialog_positive_button(device):
    for label in ("Save", "OK", "Add", "Apply", "Done", "Yes"):
        idx = device.find(text=label, clickable=True)
        if idx is None:
            idx = device.find(text=label)
        if idx is not None:
            return idx
    for el in _elements(device):
        label = _label_of(el).strip().lower()
        if label in ("save", "ok", "add", "apply", "done", "yes"):
            return el["index"]
    return None


def program(device, binding: dict) -> bool:
    if isinstance(binding, dict):
        location = binding["location"]
    else:
        location = binding.location
    if not isinstance(location, str) or not location.strip():
        raise ValueError("binding['location'] must be a non-empty string")

    # 1) Launch OsmAnd and make sure the map screen (with its controls) is up.
    if not device.open_app("OsmAnd"):
        raise RuntimeError("Failed to launch OsmAnd; is it installed?")
    device.settle(3)
    if device.find(description="Configure map") is None and \
            device.find(description="Search") is None:
        raise RuntimeError("OsmAnd launched but MapActivity (map controls) not visible")

    # 2) Open the search UI from the map screen.
    idx = device.find(description="Search", clickable=True)
    if idx is None:
        idx = device.find(description="Search")
    if idx is None:
        for el in _elements(device):
            if "search" in (el.get("description") or "").lower() and el.get("clickable"):
                idx = el["index"]
                break
    if idx is None:
        raise RuntimeError(
            "OsmAnd 'Search' button not found on map screen; cannot search for %r" % location)
    device.click(idx)
    device.settle(1.5)

    # 3) Type the location VERBATIM into the search field and submit it.
    field = _find_search_field(device)
    if field is None:
        raise LookupError("Search input (hint 'Type to search all') not found on screen")
    device.input_text(location, index=field)
    device.keyboard_enter()
    device.settle(2)

    # 4) Wait for results; if OsmAnd shows the 'no results' state, widen the
    #    search radius and retry, exactly like the recorded flow.
    result_idx = None
    for attempt in range(6):
        result_idx = _find_result_index(device, location)
        if result_idx is not None:
            break
        radius = None
        for lbl in _RADIUS_LABELS:
            radius = device.find(text=lbl, contains=True)
            if radius is not None:
                break
        if radius is not None:
            device.click(radius)
            device.settle(1.5)
            device.keyboard_enter()
            device.settle(2)
        else:
            device.wait()
            device.settle(2)
    if result_idx is None:
        raise RuntimeError("OsmAnd returned no search result for location %r" % location)

    # 5) Open the first search result -> place panel on the map.
    device.click(result_idx)
    device.settle(2)

    # 6) Tap the FAVORITES (star) control on the place panel, not the marker control.
    fav = _find_favorite_button(device)
    if fav is None:
        raise RuntimeError(
            "Favorites (star) control not found on place panel for %r" % location)
    device.click(fav)
    device.settle(2)

    # 7) Accept the name the app proposes in the add-favorite dialog.
    btn = _find_dialog_positive_button(device)
    if btn is None:
        raise RuntimeError("Confirmation button in add-favorite dialog not found")
    device.click(btn)
    device.settle(2)

    return True
