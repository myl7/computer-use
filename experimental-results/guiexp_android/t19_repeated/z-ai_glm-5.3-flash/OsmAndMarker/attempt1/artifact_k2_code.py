import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. It is typed "
            "verbatim into the search field (never reformatted or rounded)."
        ),
    }
}


def _blob(e):
    parts = []
    for k in ("text", "hint", "description"):
        v = e.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _coord_patterns(location):
    """Display patterns OsmAnd renders for 'lat, lon' queries (the typed input itself is never reformatted)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(location))
    pats = []
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
        except ValueError:
            return pats
        la, lo = abs(lat), abs(lon)
        la_h = "N" if lat >= 0 else "S"
        lo_h = "E" if lon >= 0 else "W"
        for dp in (5, 4, 6):
            a, b = f"{la:.{dp}f}", f"{lo:.{dp}f}"
            pats.append((f"{a}\u00b0 {la_h}", f"{b}\u00b0 {lo_h}"))
            pats.append((a, b))
    return pats


def _find_search_field(device):
    for kwargs in (
        {"hint": "Type to search all", "editable": True},
        {"text": "Type to search all", "editable": True},
        {"class_name": "EditText", "editable": True, "clickable": True},
        {"class_name": "android.widget.EditText", "editable": True},
    ):
        idx = device.find(**kwargs)
        if idx is not None:
            return idx
    return None


def _find_first_result(device, location):
    loc = str(location).strip()
    loc_n = _norm(loc)
    els = device.elements()

    def scan(pred):
        for e in els:
            if e.get("editable"):
                continue
            if _norm(e.get("hint")) == "type to search all":
                continue
            t = _norm(_blob(e))
            if not t:
                continue
            if pred(t, e):
                return e.get("index")
        return None

    # 1) formatted coordinate result rows (both parts in one element)
    for a, b in _coord_patterns(loc):
        a_n, b_n = _norm(a), _norm(b)
        hit = scan(lambda t, e, a=a_n, b=b_n: a in t and b in t)
        if hit is not None:
            return hit

    # 2) verbatim location text (place name or raw coordinate pair)
    if loc_n:
        hit = scan(lambda t, e: loc_n in t)
        if hit is not None:
            return hit

    # 3) formatted latitude alone (row split across elements)
    for a, _b in _coord_patterns(loc):
        a_n = _norm(a)
        hit = scan(lambda t, e, a=a_n: a in t)
        if hit is not None:
            return hit

    # 4) primary name (first comma part) of a place-name query
    parts = [p for p in re.split(r"[,;]", loc) if p.strip()]
    if parts:
        first = _norm(parts[0])
        if first:
            hit = scan(lambda t, e: first in t)
            if hit is not None:
                return hit

    return None


def _find_marker_control(device):
    idx = device.find(text="Marker")
    if idx is not None:
        return idx
    idx = device.find(description="Marker")
    if idx is not None:
        return idx
    for e in device.elements():
        if e.get("editable"):
            continue
        if "marker" in _blob(e).lower():
            return e.get("index")
    return None


def _clear_stale_query(device, field):
    """If the search field already holds a query, clear it via the field's X button."""
    try:
        fld = next((e for e in device.elements() if e.get("index") == field), None)
    except Exception:
        return field
    if fld is None or not _norm(fld.get("text")):
        return field
    clear_btn = device.find(description="Clear", clickable=True)
    if clear_btn is not None:
        device.click(clear_btn)
        device.settle(1)
        return _find_search_field(device) or field
    return field


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: a place name or 'lat, lon' pair")
    location = str(location).strip()

    app_name = binding.get("app") or "OsmAnd"

    # 1) Launch OsmAnd and wait for its map screen.
    on_map = False
    for _ in range(2):
        device.open_app(app_name)
        device.settle(3)
        if (device.find(description="Configure map") is not None
                or device.find(description="Map") is not None
                or device.find(description="Search", clickable=True) is not None):
            on_map = True
            break
    if not on_map:
        raise RuntimeError(f"OsmAnd map screen not detected after opening '{app_name}'")

    # 2) Open the search overlay (skip if it is already open).
    search_btn = device.find(description="Search", clickable=True)
    if search_btn is None:
        search_btn = device.find(description="Search")
    if search_btn is not None:
        device.click(search_btn)
        device.settle(1)

    field = _find_search_field(device)
    if field is None:
        device.settle(2)
        field = _find_search_field(device)
    if field is None:
        raise RuntimeError("OsmAnd: search input field ('Type to search all') not found")

    # 3) Clear any stale query, then type the location VERBATIM (never reformat/round).
    field = _clear_stale_query(device, field)
    device.click(field)
    device.input_text(location, index=field)
    device.settle(2)

    # 4) Open the first search result.
    result = _find_first_result(device, location)
    if result is None:
        device.keyboard_enter()
        device.settle(2)
        result = _find_first_result(device, location)
    if result is None:
        # One retry: re-focus, clear and retype the query.
        field = _find_search_field(device)
        if field is not None:
            field = _clear_stale_query(device, field)
            device.click(field)
            device.input_text(location, index=field)
            device.settle(2)
            result = _find_first_result(device, location)
    if result is None:
        raise RuntimeError(f"OsmAnd: no search result found for {location!r}")
    device.click(result)
    device.settle(2)

    # 5) Tap the MARKER control on the place panel (NOT the favorites star next to it).
    marker = _find_marker_control(device)
    if marker is None:
        device.settle(2)
        marker = _find_marker_control(device)
    if marker is None:
        raise RuntimeError("OsmAnd: 'Marker' control not found on the place panel")
    device.click(marker)
    device.settle(1)

    return True
