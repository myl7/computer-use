import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name "
            "(e.g. 'Schaan, Liechtenstein') or a 'lat, lon' coordinate pair. "
            "It is typed VERBATIM into OsmAnd's search field (never reformatted)."
        ),
    },
}


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _map_ready(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    if device.find(hint="Type to search all", editable=True) is not None:
        return True
    return False


def _open_osmand(device, binding):
    app = binding.get("app") or binding.get("app_name") or "OsmAnd"
    last_err = None
    for _ in range(2):
        try:
            device.open_app(app)
        except Exception as exc:
            last_err = exc
            continue
        device.settle(3)
        if _map_ready(device):
            return
    device.settle(3)
    if _map_ready(device):
        return
    msg = "OsmAnd map screen not detected after launching '%s'" % app
    if last_err is not None:
        msg += " (%s)" % last_err
    raise RuntimeError(msg)


def _search_field(device):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is None:
        idx = device.find(text="Type to search all", editable=True)
    if idx is None:
        for e in _elements(device):
            if not e.get("editable"):
                continue
            hint = (e.get("hint") or "").lower()
            cls = e.get("class_name") or ""
            if "search" in hint or cls == "EditText":
                return e.get("index")
    return idx


def _open_search(device):
    idx = device.find(description="Search", clickable=True)
    if idx is None and _search_field(device) is not None:
        return  # search overlay is already open
    if idx is None:
        for e in _elements(device):
            desc = (e.get("description") or "").lower()
            txt = (e.get("text") or "").strip().lower()
            if e.get("clickable") and ("search" in desc or txt == "search"):
                idx = e.get("index")
                break
    if idx is None:
        raise RuntimeError("OsmAnd 'Search' button not found on the map screen")
    device.click(idx)
    device.settle(2)


def _type_query(device, location):
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    clear_idx = device.find(description="Clear", clickable=True)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)
        fld = _search_field(device)
        if fld is None:
            raise RuntimeError("OsmAnd search field disappeared after clearing")
    # location is typed verbatim: no reformatting, no rounding
    device.input_text(location, index=fld)
    device.settle(2)


def _find_result_row(device, location):
    """Index of the first non-editable element whose text matches the query."""
    loc = str(location).strip()
    if not loc:
        return None
    els = _elements(device)

    def scan(tokens):
        for e in els:
            if e.get("editable"):
                continue
            txt = (e.get("text") or "").strip()
            if not txt:
                continue
            low = txt.lower()
            if all(t in low for t in tokens):
                return e.get("index")
        return None

    # 1) verbatim query text
    idx = scan([loc.lower()])
    if idx is not None:
        return idx

    # 2) coordinate queries: OsmAnd reformats to e.g. '47.06888° N, 9.50616° E'
    nums = re.findall(r"-?\d+(?:\.\d+)?", loc)
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
        except ValueError:
            lat = lon = None
        if lat is not None:
            for dec in (5, 4, 3, 2):
                la = "%.*f" % (dec, abs(lat))
                lo = "%.*f" % (dec, abs(lon))
                idx = scan([la + "°", lo + "°"])
                if idx is not None:
                    return idx
                idx = scan([la, lo])
                if idx is not None:
                    return idx

    # 3) all tokens of a place-name query
    toks = [t.lower() for t in re.split(r"[\s,;]+", loc) if t]
    if len(toks) > 1:
        idx = scan(toks)
        if idx is not None:
            return idx

    # 4) primary token only (top result row often shows just the name)
    if toks:
        idx = scan([toks[0]])
        if idx is not None:
            return idx
    return None


def _wait_for_results(device, location):
    for attempt in range(10):
        idx = _find_result_row(device, location)
        if idx is not None:
            return idx
        rad = device.find(text="INCREASE SEARCH RADIUS")
        if rad is None:
            rad = device.find(contains="INCREASE SEARCH RADIUS")
        if rad is not None:
            if attempt >= 6:
                break
            device.click(rad)
            device.settle(2)
            continue
        if attempt == 0:
            device.keyboard_enter()
            device.settle(2)
            continue
        if attempt in (2, 5):
            device.scroll("down")
            device.settle(1)
            continue
        device.wait()
        device.settle(1)
    return _find_result_row(device, location)


def _tap_marker(device, location):
    """Tap the MARKER control on the place panel (NOT the favorites star)."""

    def marker_priority(e):
        txt = (e.get("text") or "").strip().lower()
        desc = (e.get("description") or "").strip().lower()
        if txt == "marker":
            return 0
        if "marker" in txt:
            return 1
        if desc == "marker":
            return 2
        if "marker" in desc:
            return 3
        return 99

    for attempt in range(5):
        best, best_p = None, 99
        for e in _elements(device):
            p = marker_priority(e)
            if p < best_p:
                best, best_p = e, p
        if best is not None:
            device.click(best.get("index"))
            device.settle(1)
            return
        if attempt == 1:
            device.scroll("up")
        elif attempt == 2:
            device.scroll("down")
        elif attempt == 3:
            # maybe an intermediate disambiguation list is showing
            row = _find_result_row(device, location)
            if row is not None:
                device.click(row)
                device.settle(2)
        device.settle(1)
    raise RuntimeError("'Marker' control not found on the place panel")


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon' pair")
    location = str(location)

    _open_osmand(device, binding)
    _open_search(device)
    _type_query(device, location)

    row = _wait_for_results(device, location)
    if row is None:
        raise RuntimeError("No search result found for location %r" % location)
    device.click(row)
    device.settle(2)

    _tap_marker(device, location)
    device.settle(1)
    return True
