"""Reusable, parameterized OsmAnd automation: add a map marker for a location.

Baked-in app knowledge:
  * binding['location'] is typed VERBATIM into OsmAnd's search field (the
    same field accepts place names and 'lat, lon' pairs; never reformat).
  * Pick the FIRST search result, then use the MARKER control on the place
    panel -- NOT the favorites star next to it (they write to different
    stores; only the map-marker store is read by this family's oracle).
  * The marker is stored on the tap; no confirmation dialog follows.
"""

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark, one string: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "Typed verbatim into OsmAnd's search field."
        ),
    }
}


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def _norm(value):
    return " ".join(str(value if value is not None else "").split()).lower()


def _label(el):
    for key in ("text", "hint", "description"):
        val = el.get(key)
        if val:
            return str(val)
    return ""


def _elements(device):
    try:
        return list(device.elements() or [])
    except Exception:
        return []


def _find(device, **criteria):
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _find_fuzzy(device, needle, clickable=None, editable=None, exclude=()):
    """First element whose text/hint/description contains needle (case-insensitive)."""
    needle = _norm(needle)
    if not needle:
        return None
    for el in _elements(device):
        if clickable is not None and bool(el.get("clickable")) != bool(clickable):
            continue
        if editable is not None and bool(el.get("editable")) != bool(editable):
            continue
        hay = _norm(_label(el))
        if not hay or needle not in hay:
            continue
        if any(_norm(bad) and _norm(bad) in hay for bad in exclude):
            continue
        return el.get("index")
    return None


def _element_by_index(device, index):
    if index is None:
        return None
    for el in _elements(device):
        if el.get("index") == index:
            return el
    return None


# ---------------------------------------------------------------------------
# OsmAnd lookups
# ---------------------------------------------------------------------------

def _find_search_button(device):
    idx = _find(device, description="Search", clickable=True)
    if idx is None:
        idx = _find_fuzzy(
            device, "search", clickable=True,
            exclude=("history", "categories", "everything", "favorites", "all"),
        )
    return idx


def _find_search_field(device):
    idx = _find(device, hint="Type to search all", editable=True)
    if idx is None:
        idx = _find(device, text="Type to search all", editable=True)
    if idx is None:
        idx = _find_fuzzy(device, "type to search", editable=True)
    if idx is None:
        idx = _find_fuzzy(device, "search", editable=True)
    if idx is None:
        for el in _elements(device):
            if el.get("editable"):
                return el.get("index")
    return idx


_EXACT_SKIP = {
    "search", "clear", "menu", "settings", "categories", "category",
    "history", "favorites", "favourites", "my places", "map markers",
    "markers", "navigation", "navigate up", "back", "compass", "layers",
    "drawer", "searching", "no results", "ok", "cancel", "dismiss",
    "space", "enter", "shift", "delete", "backspace", "done", "next",
    "comma", "cities", "city", "postcodes", "postcode", "addresses",
    "address", "transport", "coordinates", "tracks", "recordings",
    "poi",
}

_SUBSTR_SKIP = (
    "increase search radius",
    "could not find",
    "nothing found",
    "no result",
    "type to search",
    "search everything",
    "search all",
    "search online",
    "search the internet",
    "recent searches",
    "search history",
    "download",
    "my location",
    "back to",
)


def _skipped(hay):
    if hay in _EXACT_SKIP:
        return True
    return any(s in hay for s in _SUBSTR_SKIP)


def _pick_first_result(device, location, skip=()):
    """Element best matching 'the first search result' for location."""
    loc = _norm(location)
    tokens = [t for t in loc.replace(",", " ").split() if len(t) >= 3]
    candidates = []
    for el in _elements(device):
        if el.get("editable"):
            continue
        hay = _norm(_label(el))
        if not hay or len(hay) < 3 or _skipped(hay):
            continue
        if el.get("index") in skip:
            continue
        candidates.append((el, hay))

    for el, hay in candidates:              # row text contains full location
        if loc and loc in hay:
            return el.get("index")
    for tok in tokens:                      # most significant token first
        for el, hay in candidates:
            if tok in hay:
                return el.get("index")
    for el, hay in candidates:              # first tappable row
        if el.get("clickable"):
            return el.get("index")
    for el, hay in candidates:              # first non ALL-CAPS-header row
        if hay != hay.upper() or any(ch.isdigit() for ch in hay):
            return el.get("index")
    if candidates:
        return candidates[0][0].get("index")
    return None


def _find_marker_control(device):
    """The MARKER button on the place panel (never the favorites star)."""
    for el in _elements(device):
        hay = _norm(_label(el))
        if not hay or "marker" not in hay:
            continue
        if ("favorite" in hay or "favourite" in hay or "bookmark" in hay
                or "star" in hay):
            continue
        if hay.startswith("remove") or hay.startswith("delete"):
            continue
        return el.get("index")
    return None


def _await_results(device, location, attempts=6, press_enter=True):
    """Poll for search results, widening the radius while OsmAnd finds nothing."""
    entered = False
    for _ in range(attempts):
        result = _pick_first_result(device, location)
        if result is not None:
            return result
        widen = _find_fuzzy(device, "increase search radius")
        if widen is not None:
            device.click(widen)
            device.settle(2)
            continue
        if press_enter and not entered:
            device.keyboard_enter()
            entered = True
        device.settle(2)
    return None


# ---------------------------------------------------------------------------
# program
# ---------------------------------------------------------------------------

def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon' pair")
    location = str(location)

    # 1) Launch OsmAnd and let the map screen settle.
    device.open_app(binding.get("app", "OsmAnd"))
    device.settle(3)

    # 2) Open the search overlay from the map screen.
    search_btn = None
    for _ in range(3):
        if _find_search_field(device) is not None:
            break
        search_btn = _find_search_button(device)
        if search_btn is not None:
            break
        up = _find(device, description="Navigate up", clickable=True)
        if up is not None:
            device.click(up)
        device.settle(2)
    if search_btn is not None:
        device.click(search_btn)
        device.settle(2)
    elif _find_search_field(device) is None:
        raise RuntimeError("OsmAnd: 'Search' button not found on the map screen")

    # 3) Type the location VERBATIM into the search field.
    field = None
    for _ in range(3):
        field = _find_search_field(device)
        if field is not None:
            break
        device.settle(2)
    if field is None:
        raise RuntimeError("OsmAnd: search input field not found")

    field_el = _element_by_index(device, field)
    if field_el is not None and _norm(field_el.get("text")):
        clear = _find(device, description="Clear", clickable=True)
        if clear is None:
            clear = _find_fuzzy(device, "clear", clickable=True)
        if clear is not None:
            device.click(clear)
            device.settle(1)
            field = _find_search_field(device) or field

    device.click(field)
    device.settle(1)
    field = _find_search_field(device) or field
    device.input_text(location, index=field)
    device.settle(2)

    # 4) Wait for the results; widen the search radius while nothing is found.
    result = _await_results(device, location, attempts=6, press_enter=True)

    # 4b) A compound place name may fail as one query; retry with the primary
    # name only (never for coordinate inputs, which must stay verbatim).
    if result is None and not any(ch.isdigit() for ch in location):
        primary = location.split(",")[0].strip()
        if primary and _norm(primary) != _norm(location):
            retry_field = _find_search_field(device)
            retry_el = _element_by_index(device, retry_field)
            cur = _norm(retry_el.get("text")) if retry_el is not None else ""
            clear = _find(device, description="Clear", clickable=True)
            if clear is None:
                clear = _find_fuzzy(device, "clear", clickable=True)
            if retry_field is not None and (not cur or clear is not None):
                if cur and clear is not None:
                    device.click(clear)
                    device.settle(1)
                    retry_field = _find_search_field(device) or retry_field
                device.click(retry_field)
                device.settle(1)
                retry_field = _find_search_field(device) or retry_field
                device.input_text(primary, index=retry_field)
                device.settle(2)
                result = _await_results(device, location, attempts=4, press_enter=True)

    if result is None:
        raise RuntimeError("OsmAnd: no search results for location %r" % location)

    # 5) Open the first search result's place panel.
    device.click(result)
    device.settle(2)

    # 6) Tap the MARKER control on the place panel (never the favorites star).
    tried = {result}
    marker = None
    for _ in range(4):
        marker = _find_marker_control(device)
        if marker is not None:
            break
        if _find_fuzzy(device, "remove marker") is not None:
            return True  # the place already carries a marker
        if _find_search_field(device) is None:
            # the place panel should be up; give it one more beat
            device.settle(2)
            marker = _find_marker_control(device)
            break
        again = _pick_first_result(device, location, skip=tried)
        if again is None:
            break
        tried.add(again)
        device.click(again)
        device.settle(2)
    if marker is None:
        raise RuntimeError(
            "OsmAnd: 'Marker' control not found on the place panel for %r" % location)

    # The marker is stored on this tap; no confirmation dialog follows.
    device.click(marker)
    device.settle(2)

    # Soft retry: once the marker exists the control reads 'Remove marker'
    # (excluded above), so this only re-taps if the first tap did not take.
    marker2 = _find_marker_control(device)
    if marker2 is not None:
        device.click(marker2)
        device.settle(2)

    return True
