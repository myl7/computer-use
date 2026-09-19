PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": (
            "Place to save as an OsmAnd favorite: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. It is "
            "typed verbatim into the app's search field."
        ),
    }
}


def _lower(s):
    return (s or "").lower()


def _is_search_field(el):
    if el.get("editable"):
        return True
    h = _lower(el.get("hint"))
    return ("search" in h) or ("type to" in h)


def _open_search(device):
    for kw in (
        {"description": "Search", "clickable": True},
        {"description": "Search"},
        {"text": "Search", "clickable": True},
        {"text": "Search"},
    ):
        idx = device.find(**kw)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            if device.find(editable=True) is not None:
                return True
    return device.find(editable=True) is not None


def _type_location(device, location):
    field = device.find(hint="Type to search all", editable=True)
    if field is None:
        field = device.find(editable=True)
    if field is None:
        return False
    device.click(index=field)
    device.settle(0.8)
    device.input_text(location, index=field)
    device.settle(1.8)
    return True


def _match_candidates(elements, location):
    loc = location.strip()
    low = loc.lower()
    # exact text match on the location string
    for e in elements:
        txt = (e.get("text") or "").strip()
        if txt and txt.lower() == low:
            return e["index"]
    # substring match on the whole location string
    if low:
        for e in elements:
            if low in _lower(e.get("text")):
                return e["index"]
    # fall back to the leading token (place name part)
    token = loc.split(",")[0].strip().lower()
    if token and token != low:
        for e in elements:
            if token and token in _lower(e.get("text")):
                return e["index"]
    return None


_SKIP_TEXTS = {
    "search", "back", "clear", "cancel", "done", "close",
    "delete", "menu", "more", "show on map",
}


def _first_result_after_field(elements):
    field_pos = None
    for i, e in enumerate(elements):
        if e.get("editable"):
            field_pos = i
            break
    if field_pos is None:
        return None
    tail = elements[field_pos + 1:]
    for e in tail:
        txt = (e.get("text") or "").strip()
        if e.get("clickable") and txt and txt.lower() not in _SKIP_TEXTS:
            return e["index"]
    for e in tail:
        txt = (e.get("text") or "").strip()
        if txt and txt.lower() not in _SKIP_TEXTS:
            return e["index"]
    return None


def _pick_result(device, location):
    for attempt in range(3):
        els = device.elements()
        candidates = [e for e in els if not _is_search_field(e)]
        idx = _match_candidates(candidates, location)
        if idx is not None:
            device.click(index=idx)
            device.settle(2.0)
            return True
        if attempt == 1:
            device.keyboard_enter()
            device.settle(2.0)
        else:
            device.settle(1.0)

    # last resort: first plausible row in the results list
    els = device.elements()
    idx = _first_result_after_field(els)
    if idx is not None:
        device.click(index=idx)
        device.settle(2.0)
        return True
    return False


def _click_favorite_control(device):
    for kw in (
        {"description": "Add to favorites"},
        {"description": "Add to favourites"},
        {"description": "Favorites"},
        {"description": "Favourites"},
        {"description": "Favorite"},
        {"text": "Add to favorites"},
        {"text": "Add to favourites"},
        {"text": "Favorites"},
        {"text": "Favourites"},
        {"text": "Favorite"},
    ):
        idx = device.find(**kw)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            return True
    for needle in ("Favorite", "Favourite", "favorite", "favourite"):
        idx = device.find(contains=needle)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            return True
    return False


def _find_name_field(device):
    for kw in (
        {"hint": "Name", "editable": True},
        {"hint": "Name"},
        {"text": "Name", "editable": True},
    ):
        idx = device.find(**kw)
        if idx is not None:
            return idx
    return None


def _confirm_favorite_dialog(device):
    name_field = None
    for _ in range(5):
        name_field = _find_name_field(device)
        if name_field is not None:
            break
        device.settle(0.7)

    # If no naming dialog showed up, the favorite was toggled directly.
    if name_field is None:
        return True

    for label in ("Save", "OK", "Add", "Done", "Yes", "Confirm"):
        idx = device.find(text=label)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            return True
    for label in ("Save", "OK", "Add", "Done", "Yes", "Confirm"):
        idx = device.find(description=label)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            return True
    return False


def _add_to_favorites(device):
    device.settle(1.0)
    if _click_favorite_control(device):
        _confirm_favorite_dialog(device)
        return True

    # Some layouts hide the star behind an overflow / "More" menu.
    for kw in (
        {"text": "More"},
        {"description": "More"},
        {"description": "More options"},
        {"text": "..."},
    ):
        idx = device.find(**kw)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.5)
            if _click_favorite_control(device):
                _confirm_favorite_dialog(device)
                return True
    return False


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if not isinstance(location, str) or not location.strip():
        raise ValueError("binding['location'] must be a non-empty string")

    device.open_app("OsmAnd")
    device.settle(2.0)

    if not _open_search(device):
        raise RuntimeError("Could not open the OsmAnd search screen")

    if not _type_location(device, location):
        raise RuntimeError("Could not type the location into the search field")

    if not _pick_result(device, location):
        raise RuntimeError("Could not select the first search result")

    if not _add_to_favorites(device):
        raise RuntimeError("Could not add the location to favorites")

    return True
