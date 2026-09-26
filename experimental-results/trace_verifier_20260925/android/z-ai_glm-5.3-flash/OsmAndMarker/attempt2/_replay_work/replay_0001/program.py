import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd. One string: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. It is "
            "typed verbatim into OsmAnd's search field (never reformatted or "
            "rounded); the first search result is opened and the 'Marker' "
            "control on the place panel is tapped (not the favorites star)."
        ),
    },
}

# Screen chrome / non-result labels that must never be mistaken for a result.
_CHROME_TEXTS = {
    "search", "search all", "type to search all", "clear", "clear all",
    "categories", "history", "address", "addresses", "places", "poi",
    "increase search radius", "could not find anything", "nothing found",
    "no results found", "navigate up", "my location", "configure map",
    "map", "menu", "settings", "favorites", "tracks", "map markers",
    "markers", "back", "directions", "share", "options", "apply", "ok",
}

_MARKER_LABELS = {
    "marker", "add marker", "add to markers", "map marker", "add map marker",
}


def _coord_patterns(location):
    """Ordered (a, b) substring pairs OsmAnd may display for a 'lat, lon' query."""
    pats = []
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(location))
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
        except ValueError:
            return pats
        for dec in (5, 4, 3, 2):
            la = f"{abs(lat):.{dec}f}"
            lo = f"{abs(lon):.{dec}f}"
            pats.append((f"{la}\u00b0 {'N' if lat >= 0 else 'S'}",
                         f"{lo}\u00b0 {'E' if lon >= 0 else 'W'}"))
            pats.append((la, lo))
        pats.append((nums[0], nums[1]))  # verbatim pair
    return pats


def _dedupe(idxs):
    out, seen = [], set()
    for i in idxs:
        if i is None or i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _on_map_screen(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    return device.find(description="Map") is not None


def _find_search_field(device):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is not None:
        return idx
    idx = device.find(text="Type to search all", editable=True)
    if idx is not None:
        return idx
    idx = device.find(editable=True, clickable=True)
    if idx is not None:
        return idx
    return device.find(editable=True)


def _ensure_search_screen(device):
    field = _find_search_field(device)
    if field is not None:
        return field
    btn = device.find(description="Search", clickable=True)
    if btn is None:
        btn = device.find(description="Search")
    if btn is None:
        return None
    device.click(btn)
    device.settle(2)
    return _find_search_field(device)


def _clear_query(device):
    btn = device.find(description="Clear", clickable=True)
    if btn is not None:
        device.click(btn)
        device.settle(1)


def _type_location(device, location):
    field = _find_search_field(device)
    if field is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(field)
    device.settle(1)
    refield = _find_search_field(device)
    if refield is not None:
        field = refield
    # Verbatim: never reformat or round the location string.
    device.input_text(location, index=field)
    device.settle(2)


def _find_result_candidates(device, location):
    loc = str(location).strip()
    loc_l = loc.lower()
    tokens = [t for t in re.split(r"[\s,]+", loc_l) if t]
    els = device.elements()
    hits = []

    def scan(pred):
        for el in els:
            if el.get("editable"):
                continue
            txt = el.get("text") or ""
            if txt and pred(txt.lower()):
                hits.append(el["index"])

    for a, b in _coord_patterns(loc):
        scan(lambda t, a=a.lower(), b=b.lower(): a in t and b in t)
        if hits:
            return _dedupe(hits)

    scan(lambda t: loc_l in t)
    if hits:
        return _dedupe(hits)

    if tokens:
        scan(lambda t: all(tok in t for tok in tokens))
        if hits:
            return _dedupe(hits)

    # Last resort: first plausible result-looking rows below the search field.
    field_idx = None
    for el in els:
        if el.get("editable"):
            field_idx = el["index"]
            break
    for el in els:
        if el.get("editable"):
            continue
        txt = (el.get("text") or "").strip()
        if len(txt) < 3:
            continue
        if field_idx is not None and el["index"] <= field_idx:
            continue
        tl = txt.lower()
        if tl in _CHROME_TEXTS or "search radius" in tl:
            continue
        hits.append(el["index"])
        if len(hits) >= 3:
            break
    return _dedupe(hits)


def _await_results(device, location):
    entered = False
    widened = 0
    for _ in range(8):
        candidates = _find_result_candidates(device, location)
        if candidates:
            return candidates
        radius = device.find(contains="SEARCH RADIUS")
        if radius is not None and widened < 3:
            device.click(radius)
            device.settle(2)
            widened += 1
            continue
        if not entered:
            device.keyboard_enter()
            device.settle(2)
            entered = True
            continue
        device.wait()
        device.settle(1)
    return []


def _find_marker(device):
    def scan():
        for el in device.elements():
            if el.get("editable"):
                continue
            for key in ("text", "description"):
                val = (el.get(key) or "").strip().lower()
                if val in _MARKER_LABELS:
                    return el["index"]
        return None

    idx = scan()
    if idx is not None:
        return idx
    # If the place panel is open but its action row is scrolled, nudge it.
    menu_open = (device.find(text="Directions") is not None
                 or device.find(description="Directions") is not None
                 or device.find(contains="Directions") is not None
                 or device.find(contains="Share") is not None)
    if menu_open:
        device.scroll(direction="left")
        device.settle(1)
        return scan()
    return None


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] (place name or 'lat, lon') is required")
    location = str(location).strip()

    app = binding.get("app_name") or binding.get("app") or "OsmAnd"

    # 1) Launch OsmAnd and confirm its map screen is in the foreground.
    opened = False
    for _ in range(2):
        device.open_app(app)
        device.settle(3)
        if _on_map_screen(device):
            opened = True
            break
    if not opened:
        raise RuntimeError(f"OsmAnd map screen not detected after opening {app!r}")

    # 2) Open the search overlay (tap the map screen's Search button).
    if _ensure_search_screen(device) is None:
        raise RuntimeError("OsmAnd search screen could not be opened")

    # 3) Clear any stale query, then type the location verbatim.
    _clear_query(device)
    _type_location(device, location)

    # 4) Wait for the results list (widen radius / press enter if needed).
    candidates = _await_results(device, location)
    if not candidates:
        raise RuntimeError(f"OsmAnd: no search results found for {location!r}")

    # 5) Open the first result, then tap 'Marker' on the place panel
    #    (the MARKER control, never the favorites star next to it).
    marker_idx = None
    cand = list(candidates)
    for attempt in range(3):
        if not cand:
            break
        idx = cand.pop(0)
        device.click(idx)
        device.settle(2)
        marker_idx = _find_marker(device)
        if marker_idx is not None:
            break
        if _find_search_field(device) is None and attempt < 2:
            # We left the results list without a usable place panel; go back.
            device.navigate_back()
            device.settle(2)
            fresh = _find_result_candidates(device, location)
            cand = fresh if fresh else []
    if marker_idx is None:
        raise RuntimeError("OsmAnd: 'Marker' control not found on the place panel")

    # The marker is stored on this tap; no confirmation dialog follows.
    device.click(marker_idx)
    device.settle(1)
    return True
