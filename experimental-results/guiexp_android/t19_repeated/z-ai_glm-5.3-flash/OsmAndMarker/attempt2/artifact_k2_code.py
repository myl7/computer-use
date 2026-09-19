import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark, one string: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "It is typed verbatim into OsmAnd's search field."
        ),
    }
}


def _els(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _launch_osmand(device, binding):
    app_name = str(binding.get("app") or binding.get("app_name") or "OsmAnd")
    for _ in range(2):
        device.open_app(app_name)
        device.settle(3)
        for desc in ("Configure map", "Map", "Search"):
            try:
                if device.find(description=desc) is not None:
                    return
            except Exception:
                pass
    raise RuntimeError(
        "OsmAnd map screen not detected after launching %r; "
        "app may be missing or launch failed" % app_name
    )


def _open_search(device):
    idx = None
    try:
        idx = device.find(description="Search", clickable=True)
    except Exception:
        idx = None
    if idx is None:
        try:
            idx = device.find(description="Search")
        except Exception:
            idx = None
    if idx is None:
        raise RuntimeError("OsmAnd: 'Search' button not found on the map screen")
    device.click(idx)
    device.settle(1)


def _find_search_field(device):
    for kwargs in (
        {"hint": "Type to search all", "editable": True},
        {"text": "Type to search all", "editable": True},
        {"class_name": "EditText", "editable": True, "clickable": True},
        {"class_name": "EditText", "editable": True},
    ):
        try:
            idx = device.find(**kwargs)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    for e in _els(device):
        if e.get("editable"):
            return e.get("index")
    raise RuntimeError("OsmAnd search input field not found")


def _type_query(device, location):
    idx = _find_search_field(device)
    try:
        device.click(idx)
    except Exception:
        pass
    device.input_text(location, index=idx)
    device.settle(2)


def _result_index(device, location):
    """Locate the first search-result row for the (verbatim) query."""
    loc = str(location).strip()
    preds = []
    nums = re.findall(r"-?\d+(?:\.\d+)?", loc)
    if len(nums) >= 2:
        try:
            lat = float(nums[0])
            lon = float(nums[1])
            la = "%.5f\u00b0 %s" % (abs(lat), "N" if lat >= 0 else "S")
            lo = "%.5f\u00b0 %s" % (abs(lon), "E" if lon >= 0 else "W")
            la5 = "%.5f" % abs(lat)
            lo5 = "%.5f" % abs(lon)
            preds.append(lambda t: la in t and lo in t)
            preds.append(lambda t: la5 in t and lo5 in t)

            def near(t):
                vals = []
                for m in re.findall(r"-?\d+(?:\.\d+)?", t):
                    try:
                        vals.append(float(m))
                    except ValueError:
                        pass
                if len(vals) < 2:
                    return False
                lat_ok = any(min(abs(v - lat), abs(v + lat)) < 5e-4 for v in vals)
                lon_ok = any(min(abs(v - lon), abs(v + lon)) < 5e-4 for v in vals)
                return lat_ok and lon_ok

            preds.append(near)
        except ValueError:
            pass
    preds.append(lambda t: loc in t)
    first_token = re.split(r"[,]", loc)[0].strip()
    if first_token:
        preds.append(lambda t: first_token in t)

    els = _els(device)
    for pred in preds:
        for e in els:
            t = e.get("text") or ""
            if not t or e.get("editable"):
                continue
            try:
                if pred(t):
                    return e.get("index")
            except Exception:
                continue
    return None


def _pick_first_result(device, location):
    idx = _result_index(device, location)
    widen_tries = 0
    while idx is None and widen_tries < 4:
        widen = None
        try:
            widen = device.find(text="INCREASE SEARCH RADIUS")
            if widen is None:
                widen = device.find(contains="SEARCH RADIUS")
        except Exception:
            widen = None
        if widen is None:
            break
        device.click(widen)
        device.settle(2)
        idx = _result_index(device, location)
        widen_tries += 1
    if idx is None:
        raise LookupError(
            "OsmAnd: no search result found for %r" % location
        )
    device.click(idx)
    device.settle(2)


def _marker_index(device):
    exact, loose = [], []
    for e in _els(device):
        parts = [e.get("text") or "", e.get("description") or ""]
        blob = " ".join(p for p in parts if p).strip()
        low = blob.lower()
        if not low:
            continue
        if low == "marker" or low == "add marker" or low.startswith("marker"):
            exact.append(e.get("index"))
        elif "marker" in low:
            loose.append(e.get("index"))
    if exact:
        return exact[0]
    if loose:
        return loose[0]
    return None


def _tap_marker(device):
    idx = _marker_index(device)
    if idx is None:
        device.scroll(direction="up")
        idx = _marker_index(device)
    if idx is None:
        device.scroll(direction="down")
        idx = _marker_index(device)
    if idx is None:
        raise LookupError(
            "OsmAnd: 'Marker' control not found on the place panel "
            "(context menu may not be open)"
        )
    device.click(idx)
    device.settle(1)


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required (place name or 'lat, lon')")
    location = str(location).strip()

    _launch_osmand(device, binding)
    _open_search(device)
    _type_query(device, location)
    _pick_first_result(device, location)
    _tap_marker(device)
    return True
