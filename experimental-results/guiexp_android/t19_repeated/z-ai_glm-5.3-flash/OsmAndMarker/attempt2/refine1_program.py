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
    "search history", "my places", "recent", "recents", "compass",
    "layers", "search nearby",
}

# Soft-keyboard key labels that must never be mistaken for a result row.
_KEYBOARD_WORDS = {
    "space", "shift", "enter", "search", "next", "done", "go", "del",
    "delete", "backspace", "tab", "abc", "123", "?123", "#+=", "sym",
    "symbol", "numeric", "comma", "period", "caps", "case", "alt",
}

_MARKER_LABELS = {
    "marker", "add marker", "add to markers", "map marker",
    "add map marker", "add to map markers", "add gps marker",
}
_MARKER_EXCLUDE = {"map markers", "markers"}


def _norm(value):
    return (value or "").strip().lower()


def _is_coord_query(text):
    return bool(re.fullmatch(
        r"\s*-?\d+(?:\.\d+)?\s*[,;\s]\s*-?\d+(?:\.\d+)?\s*", str(text)))


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


def _field_scan(els):
    """Index of the search input on the current screen, or None."""
    for el in els:
        if el.get("editable"):
            continue
        for key in ("hint", "text", "description"):
            v = _norm(el.get(key))
            if v and ("search all" in v or "type to search" in v):
                return el["index"]
    for el in els:
        if el.get("editable"):
            return el["index"]
    return None


def _find_search_field(device, tries=3):
    for _ in range(max(1, tries)):
        idx = _field_scan(device.elements())
        if idx is not None:
            return idx
        device.settle(1)
    return None


def _find_search_button(device):
    best = None
    for el in device.elements():
        if el.get("editable") or not el.get("clickable"):
            continue
        parts = [_norm(el.get(k)) for k in ("description", "text", "hint")]
        joined = " ".join(p for p in parts if p)
        if not joined or "radius" in joined or "search all" in joined:
            continue
        if "search" in joined:
            if joined == "search":
                return el["index"]
            if best is None:
                best = el["index"]
    return best


def _search_entry(device):
    """('field'|'button', index) if OsmAnd's UI is usable, else None."""
    idx = _field_scan(device.elements())
    if idx is not None:
        return ("field", idx)
    btn = _find_search_button(device)
    if btn is not None:
        return ("button", btn)
    return None


def _restart_app(device, app):
    """Reload the app: a bare map-canvas a11y tree (no controls exposed)
    recovers only after the activity is recreated."""
    for act in (device.navigate_back, device.navigate_home):
        try:
            act()
            device.settle(1)
        except Exception:
            pass
    try:
        device.open_app(app)
        device.settle(3)
    except Exception:
        pass
    if _search_entry(device) is not None:
        return
    # Hard restart of the app process (no app data is touched).
    for pkg in ("net.osmand.plus", "net.osmand"):
        try:
            device.adb_shell("am", "force-stop", pkg)
        except Exception:
            pass
    device.settle(2)
    try:
        device.open_app(app)
        device.settle(4)
    except Exception:
        pass


def _open_search(device, btn_idx=None):
    for _ in range(3):
        if btn_idx is None:
            btn_idx = _find_search_button(device)
            if btn_idx is None:
                idx = _find_search_field(device, tries=1)
                if idx is not None:
                    return idx
                device.settle(1)
                continue
        try:
            device.click(btn_idx)
        except Exception:
            btn_idx = None
            continue
        device.settle(2)
        idx = _find_search_field(device)
        if idx is not None:
            return idx
        btn_idx = None
    return None


def _clear_query(device):
    for el in device.elements():
        if not el.get("clickable"):
            continue
        for key in ("description", "text"):
            if _norm(el.get(key)) in ("clear", "clear all", "clear search",
                                      "clear query"):
                try:
                    device.click(el["index"])
                    device.settle(1)
                except Exception:
                    pass
                return


def _focus_and_type(device, text, index=None):
    if index is None:
        index = _find_search_field(device)
        if index is None:
            raise RuntimeError("OsmAnd search input field not found")
    try:
        device.click(index)
        device.settle(1)
    except Exception:
        pass
    refound = _find_search_field(device, tries=1)
    if refound is not None:
        index = refound
    # Verbatim: never reformat or round the location string.
    device.input_text(text, index=index)
    device.settle(2)
    return index


def _find_result_candidates(device, query):
    q = str(query).strip()
    ql = q.lower()
    tokens = [t for t in re.split(r"[\s,]+", ql) if t]
    els = device.elements()
    hits = []

    def scan(pred):
        for el in els:
            if el.get("editable"):
                continue
            txt = el.get("text") or ""
            if txt and pred(txt.lower()):
                hits.append(el["index"])

    for a, b in _coord_patterns(q):
        scan(lambda t, a=a.lower(), b=b.lower(): a in t and b in t)
        if hits:
            return _dedupe(hits)

    scan(lambda t: ql in t)
    if hits:
        return _dedupe(hits)

    if tokens:
        scan(lambda t: all(tok in t for tok in tokens))
        if hits:
            return _dedupe(hits)

    # Last resort: first plausible clickable result-looking rows below the field.
    field_idx = None
    for el in els:
        if el.get("editable"):
            field_idx = el["index"]
            break
    if field_idx is None:
        for el in els:
            for key in ("hint", "text"):
                if "search all" in _norm(el.get(key)):
                    field_idx = el["index"]
                    break
            if field_idx is not None:
                break
    for el in els:
        if el.get("editable") or not el.get("clickable"):
            continue
        txt = (el.get("text") or "").strip()
        if len(txt) < 3 or not any(c.isalnum() for c in txt):
            continue
        tl = txt.lower()
        if tl in _CHROME_TEXTS or tl in _KEYBOARD_WORDS:
            continue
        if "search radius" in tl:
            continue
        if field_idx is not None and el["index"] <= field_idx:
            continue
        hits.append(el["index"])
        if len(hits) >= 3:
            break
    return _dedupe(hits)


def _await_results(device, query):
    entered = False
    widened = 0
    for _ in range(10):
        candidates = _find_result_candidates(device, query)
        if candidates:
            return candidates
        radius = None
        for el in device.elements():
            v = _norm(el.get("text")) or _norm(el.get("description"))
            if "search radius" in v:
                radius = el["index"]
                break
        if radius is not None and widened < 3:
            try:
                device.click(radius)
            except Exception:
                pass
            device.settle(2)
            widened += 1
            continue
        if not entered:
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(2)
            entered = True
            continue
        device.wait()
        device.settle(1)
    return []


def _marker_scan(device):
    els = device.elements()
    # Pass 1: exact marker-control labels.
    for el in els:
        if el.get("editable"):
            continue
        for key in ("text", "description", "hint"):
            v = _norm(el.get(key))
            if not v or "favorite" in v or "favourite" in v:
                continue
            if v in _MARKER_LABELS:
                return el["index"]
    # Pass 2: anything marker-related on the place panel.
    for el in els:
        if el.get("editable"):
            continue
        for key in ("text", "description", "hint"):
            v = _norm(el.get(key))
            if not v or "favorite" in v or "favourite" in v:
                continue
            if "marker" in v and v not in _MARKER_EXCLUDE:
                return el["index"]
    return None


def _find_clickable_contains(device, needle, require_clickable=True):
    for el in device.elements():
        if el.get("editable"):
            continue
        if require_clickable and not el.get("clickable"):
            continue
        for key in ("text", "description"):
            if needle in _norm(el.get(key)):
                return el["index"]
    return None


def _find_marker(device):
    idx = _marker_scan(device)
    if idx is not None:
        return idx
    # Some place panels hide the marker action behind an 'Actions' button.
    actions = _find_clickable_contains(device, "action")
    if actions is not None:
        try:
            device.click(actions)
            device.settle(1)
        except Exception:
            actions = None
        if actions is not None:
            idx = _marker_scan(device)
            if idx is not None:
                return idx
            try:
                device.navigate_back()
                device.settle(1)
            except Exception:
                pass
            idx = _marker_scan(device)
            if idx is not None:
                return idx
    # If the panel's action row is scrolled, nudge it.
    panel_row = (_find_clickable_contains(device, "directions",
                                          require_clickable=False) is not None
                 or _find_clickable_contains(device, "share",
                                             require_clickable=False) is not None)
    if panel_row:
        device.scroll(direction="left")
        device.settle(1)
        idx = _marker_scan(device)
        if idx is not None:
            return idx
    device.settle(1)
    return _marker_scan(device)


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] (place name or 'lat, lon') is required")
    location = str(location).strip()

    app = binding.get("app_name") or binding.get("app") or "OsmAnd"

    # 1) Launch OsmAnd and make sure its UI is actually exposed to the
    #    accessibility tree.  A bare map canvas with no controls (stale or
    #    collapsed tree) recovers only after the app is restarted.
    try:
        device.open_app(app)
    except Exception:
        pass
    device.settle(3)
    entry = _search_entry(device)
    waited = 0
    while entry is None and waited < 2:
        device.wait()
        device.settle(2)
        entry = _search_entry(device)
        waited += 1
    restarts = 0
    while entry is None and restarts < 2:
        _restart_app(device, app)
        entry = _search_entry(device)
        restarts += 1
    if entry is None:
        raise RuntimeError(f"OsmAnd UI did not expose its search controls after opening {app!r}")

    # 2) Open the search overlay (tap the map screen's Search button).
    field = entry[1] if entry[0] == "field" else None
    if field is None:
        field = _open_search(device, entry[1])
    if field is None:
        raise RuntimeError("OsmAnd search screen could not be opened")

    # 3) Clear any stale query, then type the location verbatim.
    _clear_query(device)
    _focus_and_type(device, location, index=field)

    # 4) Wait for the results list (widen radius / press enter if needed).
    current_query = location
    candidates = _await_results(device, current_query)
    if not candidates and not _is_coord_query(location):
        # Recovery only: retry the place part of the query, unformatted.
        alt = location.split(",")[0].strip()
        if alt and alt.lower() != location.lower():
            field = _find_search_field(device, tries=1)
            if field is not None:
                _clear_query(device)
                _focus_and_type(device, alt, index=field)
                current_query = alt
                candidates = _await_results(device, current_query)
    if not candidates:
        raise RuntimeError(f"OsmAnd: no search results found for {location!r}")

    # 5) Open the first result, then tap 'Marker' on the place panel
    #    (the MARKER control, never the favorites star next to it).
    marker_idx = None
    tried = set()
    cand = list(candidates)
    for _ in range(5):
        cand = [i for i in cand if i not in tried]
        if not cand:
            fresh = _find_result_candidates(device, current_query)
            cand = [i for i in fresh if i not in tried]
        if not cand:
            break
        idx = cand.pop(0)
        tried.add(idx)
        try:
            device.click(idx)
        except Exception:
            continue
        device.settle(2)
        marker_idx = _find_marker(device)
        if marker_idx is not None:
            break
        if _field_scan(device.elements()) is None:
            # A panel opened without a usable marker control; close it and
            # fall through to the next candidate.
            try:
                device.navigate_back()
                device.settle(1)
            except Exception:
                pass
    if marker_idx is None:
        raise RuntimeError("OsmAnd: 'Marker' control not found on the place panel")

    # The marker is stored on this tap; no confirmation dialog follows.
    device.click(marker_idx)
    device.settle(2)
    return True
