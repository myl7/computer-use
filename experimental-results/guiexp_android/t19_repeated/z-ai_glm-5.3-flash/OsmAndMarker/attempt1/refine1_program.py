import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name "
            "(e.g. 'Schaan, Liechtenstein') or a 'lat, lon' coordinate pair. "
            "It is typed VERBATIM into OsmAnd's search field (never reformatted); "
            "if the verbatim query yields no usable result, progressively shorter "
            "variants of the same string (e.g. dropping the country part) are retried."
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


def _clear_query(device):
    clear_idx = device.find(description="Clear", clickable=True)
    if clear_idx is None:
        clear_idx = device.find(text="Clear", clickable=True)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)


def _type_query(device, query):
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    _clear_query(device)
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search field disappeared after clearing")
    # query is typed verbatim: no reformatting, no rounding
    device.input_text(query, index=fld)
    device.settle(2)


def _query_variants(location):
    """Verbatim query first, then progressively shorter fallbacks.

    OsmAnd's local index often fails on 'village, country' strings (it only
    surfaces the country row), while the bare village name hits. Coordinate
    pairs are never shortened.
    """
    raw = str(location)
    loc = raw.strip()
    variants = [raw]
    seen = {loc.lower()}
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) > 1 and re.search(r"[A-Za-z]", parts[0]):
        head = parts[0] if len(parts) == 2 else ", ".join(parts[:-1])
        if head.lower() not in seen:
            variants.append(head)
            seen.add(head.lower())
    toks = [t for t in re.split(r"[\s,;]+", loc) if t]
    if len(toks) > 1 and re.search(r"[A-Za-z]", toks[0]) and toks[0].lower() not in seen:
        variants.append(toks[0])
        seen.add(toks[0].lower())
    return variants


def _find_result_row(device, query):
    """Index of the first non-editable element whose text matches the query."""
    q = str(query).strip()
    if not q:
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
    idx = scan([q.lower()])
    if idx is not None:
        return idx

    # 2) coordinate queries: OsmAnd reformats to e.g. '47.06888° N, 9.50616° E'
    nums = re.findall(r"-?\d+(?:\.\d+)?", q)
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
    toks = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
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


def _wait_for_results(device, query, attempts=10):
    for attempt in range(attempts):
        idx = _find_result_row(device, query)
        if idx is not None:
            return idx
        rad = device.find(text="INCREASE SEARCH RADIUS")
        if rad is None:
            rad = device.find(contains="INCREASE SEARCH RADIUS")
        if rad is not None:
            if attempt >= attempts - 4:
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
    return _find_result_row(device, query)


def _tap_marker(device, query):
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

    for attempt in range(6):
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
            # the marker action may live behind an 'Actions' expander
            act = device.find(text="Actions")
            if act is None:
                act = device.find(description="Actions")
            if act is not None:
                device.click(act)
                device.settle(1)
        elif attempt == 4:
            # maybe an intermediate disambiguation list is showing
            row = _find_result_row(device, query)
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

    # Try the verbatim string first; if the local index only surfaces a
    # coarser hit (e.g. just the country for 'village, country'), retry
    # with shorter variants derived from the same binding value.
    row = None
    used_query = None
    variants = _query_variants(location)
    for i, variant in enumerate(variants):
        _type_query(device, variant)
        row = _wait_for_results(device, variant, attempts=10 if i == 0 else 6)
        if row is not None:
            used_query = variant
            break

    if row is None:
        raise RuntimeError(
            "No search result found for location %r (queries tried: %r)"
            % (location, variants)
        )
    device.click(row)
    device.settle(2)

    _tap_marker(device, used_query or location)
    device.settle(1)
    return True
