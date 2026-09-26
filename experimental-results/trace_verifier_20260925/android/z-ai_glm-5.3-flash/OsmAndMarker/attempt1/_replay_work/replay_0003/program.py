import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name "
            "(e.g. 'Schaan, Liechtenstein') or a 'lat, lon' coordinate pair. "
            "It is typed VERBATIM into OsmAnd's search field (never reformatted "
            "or rounded). If the verbatim query surfaces no usable result, the "
            "field is cleared and the place's primary name token is retried."
        ),
    },
}

_COORD_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")


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


def _clear_button(device):
    idx = device.find(description="Clear", clickable=True)
    if idx is not None:
        return idx
    for e in _elements(device):
        if not e.get("clickable"):
            continue
        desc = (e.get("description") or "").strip().lower()
        if desc in ("clear", "clear all", "clear search", "clear text"):
            return e.get("index")
    return None


def _type_query(device, query):
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    clear_idx = _clear_button(device)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)
        fld = _search_field(device)
        if fld is None:
            raise RuntimeError("OsmAnd search field disappeared after clearing")
    # the query is typed verbatim: no reformatting, no rounding
    device.input_text(query, index=fld)
    device.settle(2)


def _is_coordinate(location):
    return bool(_COORD_RE.match(str(location)))


def _query_variants(location):
    """Shortened retry queries for place names (coordinates are never altered).

    OsmAnd's full-string search can rank unrelated POIs that merely contain
    the region/country name (e.g. every POI with 'Liechtenstein') above the
    place itself, so the leading place token is retried on the same field.
    """
    loc = str(location).strip()
    if not loc or _is_coordinate(loc):
        return []
    variants = []
    seg = loc.split(",")[0].strip()
    if seg and seg.lower() != loc.lower():
        variants.append(seg)
    toks = [t for t in re.split(r"[\s,;]+", loc) if t]
    if toks:
        first = toks[0]
        if first.lower() != loc.lower() and all(
            v.lower() != first.lower() for v in variants
        ):
            variants.append(first)
    return variants[:2]


def _word_boundary_match(low, word):
    if not word:
        return False
    return (
        re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low)
        is not None
    )


def _find_result_row(device, query, avoid=None, allow_avoid=True):
    """Index of the best non-editable result row matching the query.

    ``avoid`` (the original verbatim location when ``query`` is a shortened
    retry) demotes rows that merely echo the original query, e.g. a recent
    search entry, so a real result row is preferred.
    """
    q = str(query).strip()
    if not q:
        return None
    els = _elements(device)
    ql = q.lower()

    toks = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
    primary = toks[0] if toks else ""
    full_query_matters = ql != primary
    avoid_low = str(avoid).strip().lower() if avoid else None

    best_idx, best_score = None, None
    for e in els:
        if e.get("editable"):
            continue
        txt = (e.get("text") or "").strip()
        if not txt:
            continue
        low = txt.lower()
        if avoid_low and avoid_low in low and not allow_avoid:
            continue
        if low == ql:
            score = 0
        elif primary and low == primary:
            score = 1
        elif full_query_matters and ql in low:
            score = 2
        elif (
            primary
            and low.startswith(primary)
            and not low[len(primary):len(primary) + 1].isalnum()
        ):
            score = 3
        elif toks and all(t in low for t in toks):
            score = 4
        elif primary and _word_boundary_match(low, primary):
            score = 5
        else:
            continue
        if avoid_low and avoid_low in low:
            score += 50
        if best_score is None or score < best_score:
            best_idx, best_score = e.get("index"), score
    if best_idx is not None:
        return best_idx

    # coordinate queries: OsmAnd reformats to e.g. '47.06888° N, 9.50616° E'
    nums = re.findall(r"-?\d+(?:\.\d+)?", q)
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
        except ValueError:
            return None
        for dec in (5, 4, 3, 2):
            la = "%.*f" % (dec, abs(lat))
            lo = "%.*f" % (dec, abs(lon))
            for e in els:
                if e.get("editable"):
                    continue
                low = (e.get("text") or "").lower()
                if (la + "°") in low and (lo + "°") in low:
                    return e.get("index")
            for e in els:
                if e.get("editable"):
                    continue
                low = (e.get("text") or "").lower()
                if la in low and lo in low:
                    return e.get("index")
    return None


def _wait_for_results(device, query, avoid=None, max_attempts=6):
    for attempt in range(max_attempts):
        idx = _find_result_row(device, query, avoid=avoid, allow_avoid=False)
        if idx is not None:
            return idx
        rad = device.find(text="INCREASE SEARCH RADIUS")
        if rad is None:
            rad = device.find(contains="INCREASE SEARCH RADIUS")
        if rad is not None:
            if attempt >= max_attempts - 2:
                break
            device.click(rad)
            device.settle(2)
            continue
        if attempt == 0:
            device.keyboard_enter()
            device.settle(2)
            continue
        if attempt in (2, 4):
            device.scroll("down")
            device.settle(1)
            continue
        device.wait()
        device.settle(1)
    # last resort: accept rows that merely echo the original query
    return _find_result_row(device, query, avoid=avoid, allow_avoid=True)


def _tap_marker(device, query, avoid=None):
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
            if p < best_p or (
                p == best_p
                and best is not None
                and e.get("clickable")
                and not best.get("clickable")
            ):
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
            row = _find_result_row(device, query, avoid=avoid, allow_avoid=True)
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

    # The verbatim string is tried first; for place names OsmAnd may rank
    # unrelated POIs that merely contain the region name (e.g. every POI with
    # 'Liechtenstein') above the place itself, so the primary place token is
    # retried on the same field before giving up.
    row, used_query, used_avoid = None, location, None
    for q in [location] + _query_variants(location):
        if _search_field(device) is None:
            _open_search(device)
        avoid = None if q == location else location
        _type_query(device, q)  # clears any previous query first
        row = _wait_for_results(device, q, avoid=avoid)
        if row is not None:
            used_query, used_avoid = q, avoid
            break
    if row is None:
        raise RuntimeError("No search result found for location %r" % location)

    device.click(row)
    device.settle(2)

    _tap_marker(device, used_query, used_avoid)
    device.settle(1)
    return True
