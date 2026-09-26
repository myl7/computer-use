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
_DIST_RE = re.compile(r"^\s*[\d.,]+\s*(?:mi|km|m|ft|yd|nmi|nm)\.?\s*$", re.I)

# UI labels that must never be mistaken for a search result row.
_NOISE_TEXTS = {
    "search", "search online", "search on map", "categories", "category",
    "address", "addresses", "cities", "city", "streets", "street",
    "postcodes", "postcode", "poi", "pois", "custom search", "transport",
    "favorites", "favourites", "history", "markers", "map markers",
    "tracks", "recordings", "increase search radius", "refine search",
    "no results found", "no results", "nothing found", "searching",
    "type to search all", "recent", "recents", "recent searches",
    "my location",
}

_MARKER_EXACT = ("marker", "add marker", "add new marker", "add to markers")
_MARKER_CATEGORY = ("markers", "map markers")


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _row_text(e):
    return (e.get("text") or "").strip() or (e.get("description") or "").strip()


def _map_ready(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    if device.find(hint="Type to search all", editable=True) is not None:
        return True
    return False


def _open_osmand(device, app):
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
            txt = (e.get("text") or "").lower()
            cls = e.get("class_name") or ""
            if "search" in hint or "search" in txt or cls == "EditText":
                return e.get("index")
    return idx


def _open_search(device):
    idx = device.find(description="Search", clickable=True)
    if idx is None and _search_field(device) is not None:
        return  # search overlay is already open
    if idx is None:
        for e in _elements(device):
            if e.get("editable"):
                continue
            desc = (e.get("description") or "").lower()
            txt = (e.get("text") or "").strip().lower()
            if e.get("clickable") and ("search" in desc or txt == "search"):
                idx = e.get("index")
                break
    if idx is None:
        raise RuntimeError("OsmAnd 'Search' button not found on the map screen")
    device.click(idx)
    device.settle(2)


def _ensure_search_overlay(device, app):
    if _search_field(device) is not None:
        return
    for attempt in range(3):
        try:
            _open_search(device)
        except Exception:
            pass
        if _search_field(device) is not None:
            return
        if attempt == 0:
            device.navigate_back()
            device.settle(1)
        elif attempt == 1:
            try:
                device.open_app(app)
            except Exception:
                pass
            device.settle(2)
    _open_search(device)


def _clear_button(device):
    idx = device.find(description="Clear", clickable=True)
    if idx is not None:
        return idx
    for e in _elements(device):
        if not e.get("clickable"):
            continue
        desc = (e.get("description") or "").strip().lower()
        txt = (e.get("text") or "").strip().lower()
        if desc.startswith("clear") or txt == "clear":
            return e.get("index")
    return None


def _clear_search_text(device):
    clear_idx = _clear_button(device)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)
        return
    fld = _search_field(device)
    if fld is None:
        return
    txt = ""
    for e in _elements(device):
        if e.get("index") == fld:
            txt = e.get("text") or ""
            break
    if txt.strip():
        # no clear control visible: wipe the focused field with key events
        try:
            device.adb_shell("input", "keyevent", "123")  # MOVE_END
            for _ in range(len(txt) + 2):
                device.adb_shell("input", "keyevent", "67")  # DEL
        except Exception:
            pass
        device.settle(1)


def _type_query(device, query):
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    _clear_search_text(device)
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search field disappeared after clearing")
    # the query is typed verbatim: no reformatting, no rounding
    device.input_text(query, index=fld)
    device.settle(2)
    fld2 = _search_field(device)
    if fld2 is not None and query.strip():
        cur = ""
        for e in _elements(device):
            if e.get("index") == fld2:
                cur = (e.get("text") or "").strip()
                break
        if not cur:
            device.input_text(query, index=fld2)
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


def _score_row(low, ql, toks, primary):
    if low == ql:
        return 0
    if primary and low == primary:
        return 1
    if ql in low:
        return 2
    if (
        primary
        and low.startswith(primary)
        and not low[len(primary):len(primary) + 1].isalnum()
    ):
        return 3
    if toks and all(t in low for t in toks):
        return 4
    if primary and _word_boundary_match(low, primary):
        return 5
    return None


def _find_result_row(device, query, avoid=None, tried=None):
    """Best non-editable result row matching the query.

    Returns (index, row_text_lower) or None.  ``tried`` holds (index,
    lower_text) pairs already tapped for this query so a row that did not
    open the place panel is not tapped again.  ``avoid`` (the original
    verbatim location when ``query`` is a shortened retry) demotes rows
    that merely echo the original query instead of skipping them, so a
    real result row is preferred but a lone echo can still be used.
    """
    q = str(query).strip()
    if not q:
        return None
    els = _elements(device)
    ql = q.lower()
    toks = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
    primary = toks[0] if toks else ""
    avoid_low = str(avoid).strip().lower() if avoid else None
    tried = tried or set()

    best = None
    for e in els:
        if e.get("editable"):
            continue
        idx = e.get("index")
        txt = _row_text(e)
        low = txt.lower()
        if not low or low in _NOISE_TEXTS or _DIST_RE.match(low):
            continue
        if (idx, low) in tried:
            continue
        score = _score_row(low, ql, toks, primary)
        if score is None:
            continue
        if avoid_low and avoid_low in low:
            score += 50
        if e.get("clickable"):
            score -= 0.5
        cand = (score, len(txt), idx, low)
        if best is None or cand[:2] < best[:2]:
            best = cand
    if best is not None:
        return best[2], best[3]

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
                low = _row_text(e).lower()
                if not low or (e.get("index"), low) in tried:
                    continue
                if (la + "°") in low and (lo + "°") in low:
                    return e.get("index"), low
            for e in els:
                if e.get("editable"):
                    continue
                low = _row_text(e).lower()
                if not low or (e.get("index"), low) in tried:
                    continue
                if la in low and lo in low:
                    return e.get("index"), low
    return None


def _radius_button(device):
    for e in _elements(device):
        low = ((e.get("text") or "") + " " + (e.get("description") or "")).lower()
        if "increase search radius" in low:
            return e.get("index")
    return None


def _marker_control_index(device, loose=False):
    """Index of the MARKER control (never the favorites star next to it)."""
    best_idx, best_p = None, 99.0
    for e in _elements(device):
        t = (e.get("text") or "").strip().lower()
        d = (e.get("description") or "").strip().lower()
        if t in _MARKER_CATEGORY or d in _MARKER_CATEGORY:
            continue  # 'Markers' list/category entry, not the add-marker action
        p = None
        if t in _MARKER_EXACT or d in _MARKER_EXACT:
            p = 0.0
        elif loose:
            if "marker" in t:
                p = 1.0
            elif "marker" in d:
                p = 2.0
        if p is None:
            continue
        if e.get("clickable"):
            p -= 0.5
        if p < best_p:
            best_idx, best_p = e.get("index"), p
    return best_idx


def _click_row_and_mark(device, row_idx):
    """Tap a result row, then tap MARKER on the place panel that opens.

    Returns (marked, still_on_search_screen).
    """
    device.click(row_idx)
    device.settle(2)
    for attempt in range(5):
        m = _marker_control_index(device, loose=False)
        if m is not None:
            device.click(m)
            device.settle(1)
            return True, False
        if _search_field(device) is not None:
            # still on the search screen: this row did not open the place panel
            return False, True
        m = _marker_control_index(device, loose=True)
        if m is not None:
            device.click(m)
            device.settle(1)
            return True, False
        if attempt == 1:
            device.scroll("down")
        elif attempt == 2:
            device.scroll("up")
        else:
            device.wait()
        device.settle(1)
    return False, False


def _search_and_mark(device, query, avoid, app):
    """Wait for results, tap the best row, then tap MARKER on its panel.

    Results are polled continuously (OsmAnd searches as you type); the
    keyboard enter key is only used when no row is visible yet, the list is
    scrolled to reveal more rows, and an 'increase search radius' button is
    only clicked if it actually appears.  Never blocks on a missing button.
    """
    tried = set()
    radius_clicks = 0
    for attempt in range(10):
        found = _find_result_row(device, query, avoid=avoid, tried=tried)
        if found is not None:
            row_idx, row_low = found
            marked, in_overlay = _click_row_and_mark(device, row_idx)
            if marked:
                return True
            tried.add((row_idx, row_low))
            if not in_overlay:
                _ensure_search_overlay(device, app)
            continue
        if attempt in (0, 3):
            device.keyboard_enter()
            device.settle(2)
            continue
        if radius_clicks < 2:
            rad = _radius_button(device)
            if rad is not None:
                device.click(rad)
                radius_clicks += 1
                device.settle(2)
                continue
        if attempt % 2 == 1:
            device.scroll("down")
        else:
            device.wait()
        device.settle(1)
    return False


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon' pair")
    location = str(location)
    app = binding.get("app") or binding.get("app_name") or "OsmAnd"

    _open_osmand(device, app)

    # The verbatim string is tried first; for place names OsmAnd may rank
    # unrelated POIs that merely contain the region name (e.g. every POI with
    # 'Liechtenstein') above the place itself, so the primary place token is
    # retried on the same field before giving up.
    for q in [location] + _query_variants(location):
        _ensure_search_overlay(device, app)
        _type_query(device, q)  # clears any previous query first
        avoid = None if q == location else location
        if _search_and_mark(device, q, avoid, app):
            device.settle(1)
            return True
        device.navigate_back()
        device.settle(1)
    raise RuntimeError("Could not add a map marker for location %r" % location)
