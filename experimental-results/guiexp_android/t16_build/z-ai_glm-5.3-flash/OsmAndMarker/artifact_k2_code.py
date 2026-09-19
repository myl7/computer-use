import re
import time

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "It is typed verbatim into the app's search field."
        ),
    }
}

_STOP_TEXTS = {
    "search",
    "type to search all",
    "increase search radius",
    "clear",
    "clear all",
    "history",
    "categories",
    "favorites",
    "markers",
    "nearby places",
    "on the map",
    "address",
    "my location",
    "no results",
}


def _els(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _txt(el):
    return (el.get("text") or "").strip()


def _find_search_button(device):
    for kwargs in ({"description": "Search", "clickable": True},
                   {"description": "Search"}):
        try:
            idx = device.find(**kwargs)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    for el in _els(device):
        d = (el.get("description") or "").strip().lower()
        t = _txt(el).strip().lower()
        if d == "search" or t == "search":
            return el.get("index")
    return None


def _find_search_field(device):
    for kwargs in ({"hint": "Type to search all", "editable": True},
                   {"text": "Type to search all", "editable": True},
                   {"hint": "Type to search all"},
                   {"text": "Type to search all"}):
        try:
            idx = device.find(**kwargs)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    for el in _els(device):
        if el.get("editable") and (_txt(el) or el.get("hint")):
            return el.get("index")
    for el in _els(device):
        if el.get("editable"):
            return el.get("index")
    return None


def _parse_coord(location):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$",
                 str(location))
    if not m:
        return None
    try:
        return (float(m.group(1)), float(m.group(2)))
    except ValueError:
        return None


def _match_score(txt, loc_lower, parts, coord):
    tl = txt.lower()
    if coord is not None:
        lat, lon = coord
        vals = []
        for m in re.findall(r"-?\d+(?:\.\d+)?", txt):
            try:
                vals.append(float(m))
            except ValueError:
                pass
        hit = False
        for i in range(len(vals)):
            for j in range(len(vals)):
                if i == j:
                    continue
                if abs(vals[i] - lat) <= 1e-3 and abs(vals[j] - lon) <= 1e-3:
                    hit = True
                    break
            if hit:
                break
        if not hit:
            hit = ("%.*f" % (2, abs(lat))) in tl and ("%.*f" % (2, abs(lon))) in tl
        if hit:
            return 0
    if loc_lower and loc_lower in tl:
        return 1
    for k, p in enumerate(parts):
        if p in tl:
            return 2 + k
    return None


def _find_radius_control(device):
    for el in _els(device):
        t = _txt(el).lower()
        if "search radius" in t:
            return el.get("index")
    return None


def _generic_first_row(device, after_index):
    for el in _els(device):
        idx = el.get("index")
        if not isinstance(idx, int):
            continue
        if isinstance(after_index, int) and idx <= after_index:
            continue
        if el.get("editable"):
            continue
        txt = _txt(el)
        if not txt or len(txt) < 3 or txt.lower() in _STOP_TEXTS:
            continue
        if txt.isupper():
            continue
        return idx
    return None


def _wait_for_first_result(device, location, field_idx=None, timeout=25.0,
                           max_radius_taps=4):
    loc = str(location).strip()
    loc_lower = loc.lower()
    parts = [p.strip().lower() for p in loc.split(",") if len(p.strip()) >= 3]
    coord = _parse_coord(loc)

    deadline = time.time() + timeout
    radius_taps = 0
    saw_no_results = False
    while time.time() < deadline:
        best = None
        for el in _els(device):
            if el.get("editable"):
                continue
            txt = _txt(el)
            if not txt or txt.lower() in _STOP_TEXTS:
                continue
            s = _match_score(txt, loc_lower, parts, coord)
            if s is None:
                continue
            idx = el.get("index")
            if not isinstance(idx, int):
                continue
            if best is None or (s, idx) < best:
                best = (s, idx)
        if best is not None:
            return best[1]
        rad = _find_radius_control(device)
        if rad is not None:
            saw_no_results = True
            if radius_taps < max_radius_taps:
                device.click(rad)
                radius_taps += 1
                continue
        device.wait()
    if not saw_no_results:
        fallback = _generic_first_row(device, field_idx)
        if fallback is not None:
            return fallback
    return None


def _find_marker_control(device):
    els = _els(device)
    for el in els:
        if _txt(el).lower() == "marker":
            return el.get("index")
    for el in els:
        d = (el.get("description") or "").strip().lower()
        if d in ("marker", "add marker", "add map marker"):
            return el.get("index")
    for el in els:
        t = _txt(el).lower()
        if "marker" in t and t not in ("markers", "map markers", "map markers list"):
            return el.get("index")
    return None


def program(device, binding: dict) -> bool:
    raw = binding.get("location")
    if raw is None or not str(raw).strip():
        raise ValueError("binding['location'] is required: a place name or 'lat, lon' pair")
    location = str(raw).strip()

    # 1. Launch OsmAnd and wait for its map screen.
    app_name = str(binding.get("app_name") or "OsmAnd")
    launched = False
    for _ in range(2):
        device.open_app(app_name)
        device.settle(3)
        if (device.find(description="Configure map") is not None
                or device.find(description="Search", clickable=True) is not None):
            launched = True
            break
    if not launched:
        raise RuntimeError(
            "OsmAnd map screen not detected after open_app(%r); "
            "app may be missing or failed to launch" % app_name)

    # 2. Open the search overlay via the map screen's Search button.
    search_btn = _find_search_button(device)
    if search_btn is not None:
        device.click(search_btn)
        device.settle(2)

    # Reset any leftover query from a previous search session.
    try:
        clear_idx = device.find(description="Clear", clickable=True)
    except Exception:
        clear_idx = None
    if clear_idx is None:
        try:
            clear_idx = device.find(description="Clear")
        except Exception:
            clear_idx = None
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)

    # 3. Type the location verbatim into the search field.
    field_idx = _find_search_field(device)
    if field_idx is None:
        raise RuntimeError("OsmAnd search input field ('Type to search all') not found")
    try:
        device.click(field_idx)
    except Exception:
        pass
    device.input_text(location, index=field_idx)
    device.settle(2)

    # 4. Open the first search result.
    result_idx = _wait_for_first_result(device, location, field_idx=field_idx)
    if result_idx is None:
        raise RuntimeError("OsmAnd: no search result found for %r" % location)
    device.click(result_idx)
    device.settle(2)

    # 5. Tap the MARKER control on the place panel (not the favorites star).
    marker_idx = None
    deadline = time.time() + 12
    while time.time() < deadline:
        marker_idx = _find_marker_control(device)
        if marker_idx is not None:
            break
        device.wait()
    if marker_idx is None:
        device.scroll("up")
        device.settle(1)
        marker_idx = _find_marker_control(device)
    if marker_idx is None:
        raise RuntimeError("OsmAnd: 'Marker' control not found on the place panel")
    device.click(marker_idx)
    device.settle(2)

    return True
