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

# On-screen keyboard keys sit below the search field in the accessibility
# tree and must never be mistaken for result rows.
_IME_TEXTS = {
    "space", "enter", "done", "next", "go", "send", "shift", "backspace",
    "delete", "abc", "123", "#+=", "?123", "comma", "period", "emoji",
    "mic", "voice", "search key",
}

# Texts that only appear on OsmAnd's search screen (sections/tabs/hint); used
# to confirm the search UI is really open before typing anywhere.
_SEARCH_CHROME = {
    "history", "categories", "address", "addresses", "places",
    "type to search all", "search all",
}

# Buttons that dismiss first-run / overlay dialogs blocking the app.
_DISMISS_TEXTS = {
    "skip", "not now", "close", "dismiss", "cancel", "got it", "no thanks",
}

_MARKER_EXACT = {
    "marker", "add marker", "add to markers", "map marker", "add map marker",
    "add to map markers", "add marker to map", "save marker",
}

_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_RADIO_RE = re.compile(r"[2-5]g")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _coord_patterns(location):
    """Ordered (a, b) substring pairs OsmAnd may display for a 'lat, lon' query."""
    pats = []
    nums = _NUM_RE.findall(str(location))
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


def _shade_expanded(device):
    """True when the Android notification shade appears to cover the app
    (status-bar entries dominate and no real app content is visible)."""
    status = 0
    content = 0
    for el in device.elements():
        desc = (el.get("description") or "").strip()
        txt = (el.get("text") or "").strip()
        if not desc and not txt:
            continue
        dl = desc.lower()
        if ("notification" in dl or "battery" in dl or "phone signal" in dl
                or "location requests" in dl or _TIME_RE.fullmatch(txt)
                or _RADIO_RE.fullmatch(dl)):
            status += 1
            continue
        if el.get("editable") or (desc or txt).strip().lower() in _CHROME_TEXTS:
            continue
        content += 1
    return status >= 5 and content == 0


def _on_map_screen(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    if device.find(description="Map") is not None:
        return True
    for el in device.elements():
        if el.get("editable"):
            continue
        blob = ((el.get("description") or "") + " "
                + (el.get("text") or "")).strip().lower()
        if "configure map" in blob or "my location" in blob or blob == "search":
            return True
    return False


def _find_search_field(device):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is not None:
        return idx
    idx = device.find(text="Type to search all", editable=True)
    if idx is not None:
        return idx
    # Other OsmAnd versions label the field differently; any editable whose
    # hint/text mentions "search" is the search field.
    for el in device.elements():
        if not el.get("editable"):
            continue
        for key in ("hint", "text"):
            v = (el.get(key) or "").strip().lower()
            if v and "search" in v:
                return el["index"]
    idx = device.find(editable=True, clickable=True)
    if idx is not None:
        return idx
    return device.find(editable=True)


def _search_ui_open(device):
    """True only when OsmAnd's search screen is actually open: an editable
    field plus search-screen evidence (a search hint/query in the field, or
    the categories/history chrome). Guards against typing into random
    editable elements of dialogs or other screens."""
    els = device.elements()
    field = None
    for el in els:
        if el.get("editable"):
            field = el
            break
    if field is None:
        return False
    blob = ((field.get("hint") or "") + " "
            + (field.get("text") or "")).strip().lower()
    if "search" in blob:
        return True
    if (field.get("text") or "").strip():
        # A query is already sitting in the field: we are inside search.
        return True
    for el in els:
        if el.get("editable"):
            continue
        for key in ("text", "description"):
            if (el.get(key) or "").strip().lower() in _SEARCH_CHROME:
                return True
    return False


def _find_search_button(device):
    idx = device.find(description="Search", clickable=True)
    if idx is not None:
        return idx
    idx = device.find(description="Search")
    if idx is not None:
        return idx
    for el in device.elements():
        if el.get("editable"):
            continue
        for key in ("description", "text"):
            v = (el.get(key) or "").strip().lower()
            if v == "search" or (v.startswith("search") and el.get("clickable")):
                return el["index"]
    return None


def _dismiss_dialog(device):
    """Click an obvious dismiss control of a first-run/overlay dialog."""
    for el in device.elements():
        if el.get("editable") or not el.get("clickable"):
            continue
        for key in ("text", "description"):
            v = (el.get(key) or "").strip().lower()
            if v in _DISMISS_TEXTS:
                device.click(el["index"])
                device.settle(1)
                return True
    return False


def _open_search(device):
    for _ in range(4):
        if _search_ui_open(device):
            return True
        if _shade_expanded(device):
            # The notification shade is covering the app; collapse it first.
            device.navigate_back()
            device.settle(2)
            continue
        btn = _find_search_button(device)
        if btn is not None:
            device.click(btn)
            device.settle(2)
            # We explicitly asked for search: accept any editable field that
            # appeared, even if its hint is not the usual placeholder.
            if _find_search_field(device) is not None:
                return True
            continue
        if _dismiss_dialog(device):
            continue
        # No search control visible: some overlay/panel may cover the map.
        device.navigate_back()
        device.settle(2)
    return _search_ui_open(device)


def _clear_query(device):
    for el in device.elements():
        if el.get("editable"):
            continue
        for key in ("description", "text"):
            v = (el.get(key) or "").strip().lower()
            if v in ("clear", "clear all", "clear search", "clear text",
                     "clear query"):
                device.click(el["index"])
                device.settle(1)
                return


def _query_on_screen(device, location):
    loc_l = str(location).strip().lower()
    if not loc_l:
        return False
    for el in device.elements():
        t = (el.get("text") or "").strip().lower()
        if t and loc_l in t:
            return True
    return False


def _query_in_field(device, location):
    """True when the editable search field itself shows the query."""
    loc_l = str(location).strip().lower()
    if not loc_l:
        return False
    for el in device.elements():
        if el.get("editable"):
            t = (el.get("text") or "").strip().lower()
            return bool(t) and loc_l in t
    return False


def _type_location(device, location):
    field = _find_search_field(device)
    if field is None:
        return False
    if _query_in_field(device, location):
        return True
    device.click(field)
    device.settle(1)
    refield = _find_search_field(device)
    if refield is not None:
        field = refield
    # Verbatim: never reformat or round the location string.
    device.input_text(location, index=field)
    device.settle(2)
    if _query_in_field(device, location) or _query_on_screen(device, location):
        return True
    # The text did not land (or landed partially): clear and retype once.
    _clear_query(device)
    refield = _find_search_field(device)
    if refield is None:
        return False
    device.click(refield)
    device.settle(1)
    refield = _find_search_field(device)
    if refield is None:
        return False
    device.input_text(location, index=refield)
    device.settle(2)
    return _query_in_field(device, location) or _query_on_screen(device, location)


def _find_result_candidates(device, location, query_ok=True):
    els = device.elements()
    field_idx = None
    for el in els:
        if el.get("editable"):
            field_idx = el["index"]
            break
    if field_idx is None:
        # Not on the search screen: anything picked here would be map chrome
        # or status-bar entries (e.g. the clock), never a search result.
        return []
    loc = str(location).strip()
    loc_l = loc.lower()
    tokens = [t for t in re.split(r"[\s,]+", loc_l) if t]

    rows = []
    for el in els:
        if el.get("editable") or el["index"] <= field_idx:
            continue
        txt = (el.get("text") or "").strip()
        if len(txt) < 3 or _TIME_RE.fullmatch(txt):
            continue
        tl = txt.lower()
        if tl in _CHROME_TEXTS or tl in _IME_TEXTS or "search radius" in tl:
            continue
        rows.append((el["index"], tl, el))

    def pick(pred):
        out, seen = [], set()
        for idx, tl, _el in rows:
            if pred(tl) and idx not in seen:
                seen.add(idx)
                out.append((idx, tl))
        return out

    # 1) Coordinate pairs, most specific display format first.
    for a, b in _coord_patterns(loc):
        a_l, b_l = a.lower(), b.lower()
        hits = pick(lambda t, a=a_l, b=b_l: a in t and b in t)
        if hits:
            return hits

    # 2) Full query substring.
    hits = pick(lambda t: loc_l in t)
    if hits:
        return hits

    # 3) All tokens inside one row.
    if tokens:
        hits = pick(lambda t: all(tok in t for tok in tokens))
        if hits:
            return hits

    # 4) Name/subtitle split across two adjacent elements.
    if len(tokens) >= 2:
        tl_by_idx = {idx: tl for idx, tl, _el in rows}
        out, seen = [], set()
        for idx, tl, _el in rows:
            if tokens[0] in tl and idx not in seen:
                for j in (idx + 1, idx + 2):
                    if tokens[1] in tl_by_idx.get(j, ""):
                        seen.add(idx)
                        out.append((idx, tl))
                        break
        if out:
            return out

    # 5) Primary token in a clickable row.
    if tokens:
        out = [(idx, tl) for idx, tl, el in rows
               if tokens[0] in tl and el.get("clickable")]
        if out:
            return out

    # 6) Last resort, only when the query is visibly in the field (the search
    #    actually ran): first clickable result-looking rows.
    if query_ok:
        return [(idx, tl) for idx, tl, el in rows if el.get("clickable")][:3]
    return []


def _find_radius_button(device):
    for el in device.elements():
        if el.get("editable"):
            continue
        for key in ("text", "description"):
            v = (el.get(key) or "").strip().lower()
            if v and "search radius" in v:
                return el["index"]
    return None


def _await_results(device, location):
    entered = False
    widened = 0
    retyped = False
    scrolled = False
    reopened = False
    for _ in range(8):
        query_ok = _query_on_screen(device, location)
        candidates = _find_result_candidates(device, location, query_ok)
        if candidates:
            return candidates
        if _find_search_field(device) is None:
            # The search screen is gone (closed or covered): reopen it once
            # and retype instead of poking at random elements.
            if not reopened and _open_search(device):
                reopened = True
                _type_location(device, location)
                continue
            return []
        radius = _find_radius_button(device)
        if radius is not None and widened < 3:
            device.click(radius)
            device.settle(2)
            widened += 1
            continue
        if (not query_ok and not retyped
                and not _query_in_field(device, location)):
            retyped = True
            if _type_location(device, location):
                continue
        if not entered:
            device.keyboard_enter()
            device.settle(2)
            entered = True
            continue
        if not scrolled:
            device.scroll(direction="down")
            device.settle(1)
            scrolled = True
            continue
        device.wait()
        device.settle(1)
    return []


def _marker_match(value):
    v = (value or "").strip().lower()
    if not v:
        return False
    if v in _MARKER_EXACT:
        return True
    # Substring fallback, but never the "Map markers" list/screen entries.
    return "marker" in v and ("add" in v or "save" in v
                              or v in ("marker", "map marker"))


def _scan_marker(device):
    for el in device.elements():
        if el.get("editable"):
            continue
        for key in ("text", "description"):
            if _marker_match(el.get(key)):
                return el["index"]
    return None


def _place_panel_open(device):
    for el in device.elements():
        if el.get("editable"):
            continue
        for key in ("text", "description"):
            v = (el.get(key) or "").strip().lower()
            if not v:
                continue
            if ("directions" in v or "details" in v or "share" in v
                    or "favorite" in v or "favourite" in v or _marker_match(v)):
                return True
    return False


def _find_marker(device):
    idx = _scan_marker(device)
    if idx is not None:
        return idx
    if _place_panel_open(device):
        # The panel's action row may be scrolled out of view; nudge it.
        for direction in ("left", "right"):
            device.scroll(direction=direction)
            device.settle(1)
            idx = _scan_marker(device)
            if idx is not None:
                return idx
    return None


def _launch_on_map(device, app):
    for _ in range(3):
        device.open_app(app)
        device.settle(3)
        if _on_map_screen(device) or _find_search_field(device) is not None:
            return
        # A first-run dialog or overlay may be covering the map.
        if _dismiss_dialog(device):
            if _on_map_screen(device) or _find_search_field(device) is not None:
                return
        device.wait()
        device.settle(2)
    raise RuntimeError(f"OsmAnd map screen not detected after opening {app!r}")


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] (place name or 'lat, lon') is required")
    location = str(location).strip()

    app = binding.get("app_name") or binding.get("app") or "OsmAnd"

    # 1) Launch OsmAnd and confirm its map screen is in the foreground.
    _launch_on_map(device, app)

    # 2)-5) Open the search overlay (verified open before typing), type the
    #    location verbatim, open the first result and tap the MARKER control
    #    on the place panel (never the favorites star next to it).
    marker_idx = None
    tried_texts = set()
    problem = f"OsmAnd: no search results found for {location!r}"
    for _attempt in range(3):
        if _attempt > 0:
            # Leave any stale search/panel state so the retry starts from the
            # map and the query lands in a freshly opened search field.
            device.navigate_back()
            device.settle(1)
        if not _open_search(device):
            # Recover: reset to the home screen, bring the app back and retry.
            device.navigate_home()
            device.settle(2)
            device.open_app(app)
            device.settle(3)
            if not _open_search(device):
                raise RuntimeError("OsmAnd search screen could not be opened")
        _clear_query(device)
        if not _type_location(device, location):
            _clear_query(device)
            if not _type_location(device, location):
                problem = (f"OsmAnd: could not type {location!r} into the "
                           f"search field")
                continue

        candidates = _await_results(device, location)
        if not candidates:
            problem = f"OsmAnd: no search results found for {location!r}"
            continue
        problem = "OsmAnd: 'Marker' control not found on the place panel"
        picks = 0
        while candidates and marker_idx is None and picks < 5:
            idx, text = candidates.pop(0)
            if text and text in tried_texts:
                continue
            picks += 1
            if text:
                tried_texts.add(text)
            device.click(idx)
            device.settle(2)
            marker_idx = _find_marker(device)
            if marker_idx is not None:
                break
            if _find_search_field(device) is not None:
                # Click did not leave the results list: try the next row.
                continue
            # We left the results list without a usable place panel.
            if _shade_expanded(device):
                device.navigate_back()
                device.settle(2)
                marker_idx = _scan_marker(device)
                if marker_idx is not None:
                    break
            device.navigate_back()
            device.settle(2)
            if _find_search_field(device) is not None:
                # Back on the results list: try the next candidate.
                fresh = _await_results(device, location)
                candidates = [c for c in fresh
                              if not (c[1] and c[1] in tried_texts)]
        if marker_idx is not None:
            break

    if marker_idx is None:
        raise RuntimeError(problem)

    # The marker is stored on this tap; no confirmation dialog follows.
    device.click(marker_idx)
    device.settle(1)
    return True
