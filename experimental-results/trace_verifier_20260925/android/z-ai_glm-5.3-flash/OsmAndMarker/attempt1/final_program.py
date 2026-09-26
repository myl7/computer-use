import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name "
            "(e.g. 'Schaan, Liechtenstein') or a 'lat, lon' coordinate pair. "
            "It is typed VERBATIM into OsmAnd's search field (never reformatted "
            "or rounded). The first usable search result is opened and the "
            "MARKER control on the place panel (never the favorites star next "
            "to it) stores the marker on the tap. When OsmAnd answers "
            "'Could not find anything: <r> mi', the 'INCREASE SEARCH RADIUS' "
            "button is tapped repeatedly until the place shows up."
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
    "my location", "show on map", "provide feedback", "send",
}

# Variable no-results captions (they carry the current radius, so they are
# matched by prefix rather than exactly).
_NOISE_PREFIXES = (
    "could not find anything",
    "no search results",
    "change the search or increase",
)

_MARKER_EXACT = ("marker", "add marker", "add new marker", "add to markers")
_MARKER_CATEGORY = ("markers", "map markers")

# Labels that indicate a place/context panel is open (possibly on top of the
# search screen), as opposed to the plain search results list.
_PANEL_HINT_WORDS = (
    "details", "measure distance", "search nearby", "online photos",
    "add to favorites", "add to favourites", "remove marker",
    "show on map", "provide feedback",
)

# Words that mark UI chrome rather than a result row.
_UI_CHROME_WORDS = (
    "clear", "voice", "mic", "keyboard", "back", "close", "settings",
    "configure", "zoom", "compass", "layers", "menu", "navigate",
    "send", "feedback", "radius", "increase",
)

# OsmAnd answers 'Could not find anything: 5 mi' when the place lies outside
# the search radius around the current map/GPS position -- which can be
# thousands of miles away from the target.  Each tap on 'INCREASE SEARCH
# RADIUS' widens the radius one step and re-runs the same query, so the
# button is tapped repeatedly until matching rows appear or the radius
# cannot grow further.
_MAX_RADIUS_CLICKS = 16
_SEARCH_ATTEMPTS = 30
_PANEL_ATTEMPTS = 6


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _row_text(e):
    return (e.get("text") or "").strip() or (e.get("description") or "").strip()


def _is_noise(low):
    if low in _NOISE_TEXTS:
        return True
    for pref in _NOISE_PREFIXES:
        if low.startswith(pref):
            return True
    return False


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


def _search_field(device, query=None):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is None:
        idx = device.find(text="Type to search all", editable=True)
    if idx is not None:
        return idx
    els = _elements(device)
    ql = (query or "").strip().lower()
    for e in els:
        if not e.get("editable"):
            continue
        hint = (e.get("hint") or "").lower()
        txt = (e.get("text") or "").lower()
        cls = (e.get("class_name") or "").lower()
        if ("search" in hint or "search" in txt or "edittext" in cls
                or (ql and txt == ql)):
            return e.get("index")
    # the field may lose its editable flag once it holds text; the well-known
    # hint still identifies it
    for e in els:
        hint = (e.get("hint") or "").lower()
        txt = (e.get("text") or "").lower()
        if "type to search" in hint or "type to search" in txt:
            return e.get("index")
    return None


def _field_text(device, query=None):
    fld = _search_field(device, query)
    if fld is None:
        return ""
    for e in _elements(device):
        if e.get("index") == fld:
            return (e.get("text") or "").strip()
    return ""


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
    if _panel_controls_present(device):
        # a place/context panel is open on top: close it first
        try:
            device.navigate_back()
            device.settle(1)
        except Exception:
            pass
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


def _clear_search_text(device, query=None):
    clear_idx = _clear_button(device)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)
        return
    fld = _search_field(device, query)
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
    fld = _search_field(device, query)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    _clear_search_text(device, query)
    fld = _search_field(device, query)
    if fld is None:
        raise RuntimeError("OsmAnd search field disappeared after clearing")
    # the query is typed verbatim: no reformatting, no rounding
    device.input_text(query, index=fld)
    device.settle(2)
    fld2 = _search_field(device, query)
    if fld2 is not None and query.strip():
        cur = _field_text(device, query)
        if not cur:
            device.input_text(query, index=fld2)
            device.settle(2)
    # commit the query: the result list becomes final and the keyboard stops
    # covering rows, so result taps land on the list and not on the keys
    try:
        device.keyboard_enter()
    except Exception:
        pass
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


def _find_result_row(device, query, avoid=None, tried=None, tried_idxs=None,
                     failed_texts=None):
    """Best non-editable result row matching the query.

    Returns (index, row_text_lower) or None.  ``tried`` holds (index,
    lower_text) pairs and ``tried_idxs`` plain indexes already tapped for
    this query so a row that did not open the place panel is not tapped
    again; ``failed_texts`` survives re-typing of the same query.  ``avoid``
    (the original verbatim location when ``query`` is a shortened retry)
    gently demotes rows echoing the original query -- they may be history
    entries of the failed first attempt -- while still preferring a genuine
    exact row and still using a lone echo if nothing better exists.
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
    tried_idxs = tried_idxs or set()
    failed_texts = failed_texts or set()

    best = None
    for e in els:
        if e.get("editable"):
            continue
        # never mistake the search input itself (hint 'Type to search all')
        # for a result row
        if "search" in (e.get("hint") or "").lower():
            continue
        idx = e.get("index")
        txt = _row_text(e)
        low = txt.lower()
        if not low or _is_noise(low) or _DIST_RE.match(low):
            continue
        if (idx, low) in tried or idx in tried_idxs or low in failed_texts:
            continue
        score = _score_row(low, ql, toks, primary)
        if score is None:
            continue
        if avoid_low and avoid_low in low:
            score += 1.0
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
                if e.get("editable") or "search" in (e.get("hint") or "").lower():
                    continue
                low = _row_text(e).lower()
                if not low or (e.get("index"), low) in tried:
                    continue
                if (la + "°") in low and (lo + "°") in low:
                    return e.get("index"), low
            for e in els:
                if e.get("editable") or "search" in (e.get("hint") or "").lower():
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
        if "increase search radius" in low or ("increase" in low and "radius" in low):
            return e.get("index")
    return None


def _no_results_present(device):
    """True when OsmAnd's 'Could not find anything: <r> mi' answer is shown."""
    for e in _elements(device):
        low = _row_text(e).lower()
        if not low:
            continue
        for pref in _NOISE_PREFIXES:
            if low.startswith(pref):
                return True
        if low in ("no results found", "no results", "nothing found",
                   "no search results"):
            return True
    return False


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


def _show_on_map_button(device):
    idx = device.find(text="SHOW ON MAP")
    if idx is not None:
        return idx
    for e in _elements(device):
        low = ((e.get("text") or "") + " " + (e.get("description") or "")).strip().lower()
        if low in ("show on map", "show place on map"):
            return e.get("index")
    return None


def _panel_controls_present(device):
    for e in _elements(device):
        low = ((e.get("text") or "") + " " + (e.get("description") or "")).strip().lower()
        if not low:
            continue
        for w in _PANEL_HINT_WORDS:
            if w in low:
                return True
    return False


def _more_button(device):
    for e in _elements(device):
        low = ((e.get("text") or "") + " " + (e.get("description") or "")).strip().lower()
        if low in ("more", "details", "options", "actions", "more details",
                   "⋯", "…"):
            return e.get("index")
    return None


def _marker_near_favorite(device):
    """Deep fallback: the unlabeled flag button next to the favorites star."""
    els = _elements(device)
    fav_idx = None
    for e in els:
        low = ((e.get("text") or "") + " " + (e.get("description") or "")).lower()
        if "favorite" in low or "favourite" in low:
            fav_idx = e.get("index")
            break
    if fav_idx is None:
        return None
    best = None
    for e in els:
        idx = e.get("index")
        if idx is None or idx == fav_idx or not e.get("clickable"):
            continue
        if abs(idx - fav_idx) > 2:
            continue
        if (e.get("text") or "").strip():
            continue
        d = (e.get("description") or "").strip().lower()
        if d and any(w in d for w in ("favorite", "favourite", "share",
                                      "direction", "details", "marker",
                                      "voice", "search")):
            continue
        dist = abs(idx - fav_idx)
        if best is None or dist < best[0]:
            best = (dist, idx)
    return best[1] if best is not None else None


def _poll_and_click_marker(device, attempts):
    """Poll the current screen for the MARKER control and tap it.

    The place panel may slide in over the search screen (the search field can
    stay visible in the tree), so polling never bails just because the field
    is still there.  If the panel hides the marker control behind a
    'Details'/'More' expander, that control is tapped once.  As a deep
    fallback the unlabeled flag button next to the favorites star is used.
    """
    more_tapped = False
    plain = 0
    for attempt in range(attempts):
        field = _search_field(device)
        panel_open = field is None or _panel_controls_present(device)
        m = _marker_control_index(device, loose=False)
        if m is None and panel_open:
            m = _marker_control_index(device, loose=True)
        if m is None and panel_open and attempt >= 3:
            m = _marker_near_favorite(device)
        if m is not None:
            device.click(m)
            device.settle(2)
            return True
        if not panel_open:
            # plain search results screen, no panel appeared
            plain += 1
            if plain >= 3:
                return False
        else:
            plain = 0
            if attempt == 2 and not more_tapped:
                mb = _more_button(device)
                if mb is not None:
                    more_tapped = True
                    device.click(mb)
                    device.settle(2)
                    continue
            if attempt == 3:
                device.scroll("down")
            elif attempt >= 5:
                device.scroll("up")
        device.settle(1)
    return False


def _click_row_and_mark(device, row_idx):
    """Tap a result row, then tap MARKER on the place panel that opens.

    Returns (marked, still_on_search_screen).
    """
    device.click(row_idx)
    device.settle(2)
    if _poll_and_click_marker(device, _PANEL_ATTEMPTS):
        return True, False
    field = _search_field(device)
    if field is not None and not _panel_controls_present(device):
        # Still on the search screen: this row did not open the place
        # panel by itself.  Some builds only select the row and need the
        # explicit 'SHOW ON MAP' button; try it once before moving on.
        som = _show_on_map_button(device)
        if som is not None:
            device.click(som)
            device.settle(2)
            if _poll_and_click_marker(device, _PANEL_ATTEMPTS):
                return True, False
        return False, True
    if _search_field(device) is None and _panel_controls_present(device):
        # a panel opened but offered no marker control: close it again
        try:
            device.navigate_back()
            device.settle(1)
        except Exception:
            pass
    return False, False


def _plausible_row(device, tried_idxs):
    """First plausible clickable result row below the search field (fallback).

    Used when no row matches the query text but results are clearly listed:
    the knowledge says to pick the first search result.  Only genuinely
    clickable, non-chrome elements with a caption qualify, so status-bar
    items, the feedback 'Send' button and the radius control are never hit.
    """
    els = _elements(device)
    fld = _search_field(device)
    start = fld if fld is not None else -1
    best = None
    for e in els:
        idx = e.get("index")
        if idx is None or idx <= start or idx in tried_idxs:
            continue
        if not e.get("clickable"):
            continue
        if e.get("editable") or (e.get("hint") or ""):
            continue
        low = _row_text(e).lower()
        if not low or len(low) < 2 or _is_noise(low) or _DIST_RE.match(low):
            continue
        if any(w in low for w in _UI_CHROME_WORDS):
            continue
        if best is None or idx < best:
            best = idx
    return best


def _search_and_mark(device, query, avoid, app):
    """Wait for results, tap the best row, then tap MARKER on its panel.

    Results are polled continuously (OsmAnd searches as you type) and always
    take priority: matching rows are tapped even while the radius could still
    grow.  When OsmAnd answers 'Could not find anything: <r> mi' the place
    lies outside the search radius around the current map/GPS position --
    possibly thousands of miles away -- so the 'INCREASE SEARCH RADIUS'
    button is tapped whenever it is visible (up to _MAX_RADIUS_CLICKS times);
    each tap widens the radius one step and re-runs the same query.  The
    radius check runs unconditionally: the no-results screen may not expose
    the search field to the accessibility tree at all, and re-typing the
    query would reset the radius back to its smallest step.  Rows that were
    tapped without opening a place panel are remembered and never tapped
    again; after several dead taps the keyboard is hidden with BACK so the
    rows become tappable.
    """
    tried = set()
    tried_idxs = set()
    failed_texts = set()
    radius_clicks = 0
    enters = 0
    generic_clicks = 0
    dead_rows = 0
    recoveries = 0
    panel_recoveries = 0
    gone_streak = 0
    for attempt in range(_SEARCH_ATTEMPTS):
        field = _search_field(device, query)
        # (1) a place panel opened directly over/instead of the search screen
        # (never confuse it with the 'Could not find anything' screen, which
        # also shows panel-like captions such as 'SHOW ON MAP')
        if (field is None and _panel_controls_present(device)
                and not _no_results_present(device)
                and _radius_button(device) is None):
            if _poll_and_click_marker(device, 4):
                return True
            if panel_recoveries < 2:
                panel_recoveries += 1
                try:
                    device.navigate_back()
                    device.settle(1)
                except Exception:
                    pass
                try:
                    _ensure_search_overlay(device, app)
                except Exception:
                    pass
                try:
                    _type_query(device, query)
                    tried = set()
                    tried_idxs = set()
                    gone_streak = 0
                except Exception:
                    pass
                continue
            return False
        # (2) results take priority -- never block on the radius button while
        # matching rows are already listed
        found = _find_result_row(device, query, avoid=avoid, tried=tried,
                                 tried_idxs=tried_idxs,
                                 failed_texts=failed_texts)
        if found is not None:
            row_idx, row_low = found
            marked, in_overlay = _click_row_and_mark(device, row_idx)
            if marked:
                return True
            tried.add((row_idx, row_low))
            tried_idxs.add(row_idx)
            failed_texts.add(row_low)
            dead_rows += 1
            if not in_overlay:
                dead_rows = 0
                # the row tap dismissed the search: restore it and re-run
                try:
                    _ensure_search_overlay(device, app)
                except Exception:
                    pass
                try:
                    _type_query(device, query)
                    tried = set()
                    tried_idxs = set()
                except Exception:
                    pass
            elif dead_rows >= 2:
                # several rows opened nothing: the soft keyboard is probably
                # covering the list -- hide it and retry on the same screen
                dead_rows = 0
                device.navigate_back()
                device.settle(2)
                if _search_field(device, query) is None:
                    try:
                        _ensure_search_overlay(device, app)
                    except Exception:
                        pass
                    try:
                        _type_query(device, query)
                        tried = set()
                        tried_idxs = set()
                    except Exception:
                        pass
            continue
        # (3) 'Could not find anything: <r> mi': widen the radius.  This
        # check is unconditional -- the no-results screen may not expose the
        # search field, and re-typing here would reset the radius.
        if radius_clicks < _MAX_RADIUS_CLICKS:
            rad = _radius_button(device)
            if rad is not None:
                device.click(rad)
                radius_clicks += 1
                device.settle(3)
                continue
        # (4) search field gone (and no panel, no results, no radius button)
        if field is None:
            gone_streak += 1
            m = _marker_control_index(device, loose=False)
            if m is not None:
                device.click(m)
                device.settle(2)
                return True
            if gone_streak >= 3:
                return False
            if recoveries < 2:
                recoveries += 1
                try:
                    _ensure_search_overlay(device, app)
                except Exception:
                    pass
                try:
                    _type_query(device, query)
                    tried = set()
                    tried_idxs = set()
                    gone_streak = 0
                except Exception:
                    pass
            else:
                device.settle(2)
            continue
        gone_streak = 0
        # (5) generic plausible row, but never on the no-results screen
        no_results = _no_results_present(device)
        if attempt >= 3 and generic_clicks < 2 and not no_results:
            g = _plausible_row(device, tried_idxs)
            if g is not None:
                generic_clicks += 1
                marked, in_overlay = _click_row_and_mark(device, g)
                if marked:
                    return True
                tried_idxs.add(g)
                if not in_overlay:
                    try:
                        _ensure_search_overlay(device, app)
                    except Exception:
                        pass
                    try:
                        _type_query(device, query)
                        tried = set()
                        tried_idxs = set()
                    except Exception:
                        pass
                continue
        # (6) commit the query with enter while nothing is listed yet
        if enters < 3 and attempt in (2, 6, 12) and not no_results:
            device.keyboard_enter()
            enters += 1
            device.settle(2)
            continue
        if attempt % 6 == 5:
            device.scroll("down")
        device.settle(2)
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
    first_err = None
    for q in [location] + _query_variants(location):
        try:
            _ensure_search_overlay(device, app)
            _type_query(device, q)  # clears any previous query first
            avoid = None if q == location else location
            if _search_and_mark(device, q, avoid, app):
                device.settle(1)
                return True
        except Exception as exc:
            if first_err is None:
                first_err = exc
        try:
            device.navigate_back()
            device.settle(1)
        except Exception:
            pass
    msg = "Could not add a map marker for location %r" % location
    if first_err is not None:
        msg += " (last error: %s)" % first_err
    raise RuntimeError(msg)
