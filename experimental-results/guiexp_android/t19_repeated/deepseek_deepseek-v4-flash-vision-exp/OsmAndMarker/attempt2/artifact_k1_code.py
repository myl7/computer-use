import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": (
            "Place to mark: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "Typed verbatim into the app's search field."
        ),
    }
}


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None:
        raise ValueError("binding is missing required key 'location'")
    location = str(location).strip()
    if not location:
        raise ValueError("binding 'location' is empty")

    _launch_osmand(device)
    _open_search(device)
    _enter_query(device, location)
    _choose_first_result(device, location)
    _tap_marker(device)
    return True


# ---------------------------------------------------------------------------
# low level element helpers
# ---------------------------------------------------------------------------

def _els(device):
    try:
        els = device.elements()
    except Exception:
        els = None
    return list(els) if els else []


def _truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).lower() in ("true", "1", "yes", "on")


def _etext(e):
    parts = []
    for k in ("text", "hint", "description", "content_description", "contentDescription"):
        v = e.get(k)
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _etext_l(e):
    return _etext(e).lower()


def _has_editable(device):
    for e in _els(device):
        if _truthy(e.get("editable")):
            return True
    return False


_OSMAND_CUES = ("osmand", "north is up", "my location", "map style", "compass", "route")


def _looks_like_osmand(device):
    for e in _els(device):
        t = _etext_l(e)
        if any(c in t for c in _OSMAND_CUES):
            return True
    return False


# ---------------------------------------------------------------------------
# launching
# ---------------------------------------------------------------------------

def _launch_osmand(device):
    try:
        device.open_app("OsmAnd")
    except Exception:
        pass
    device.settle(2.5)
    if _looks_like_osmand(device):
        return

    # Fall back to finding the launcher icon.
    for _ in range(4):
        for kw in (
            {"text": "OsmAnd"},
            {"contains": "OsmAnd"},
            {"description": "OsmAnd"},
            {"contains": "OsmAnd", "clickable": True},
        ):
            try:
                idx = device.find(**kw)
            except Exception:
                idx = None
            if idx is not None:
                try:
                    device.click(index=idx)
                except Exception:
                    continue
                device.settle(2.5)
                if _looks_like_osmand(device):
                    return

        # Go home and try to scroll the app drawer to find the icon.
        try:
            device.navigate_home()
        except Exception:
            pass
        device.settle(1.0)
        for d in ("up", "down"):
            try:
                device.scroll(d)
            except Exception:
                pass
            device.settle(0.6)
            try:
                idx = device.find(contains="OsmAnd")
            except Exception:
                idx = None
            if idx is not None:
                try:
                    device.click(index=idx)
                except Exception:
                    continue
                device.settle(2.5)
                if _looks_like_osmand(device):
                    return

    raise RuntimeError("could not launch OsmAnd")


# ---------------------------------------------------------------------------
# opening the search field
# ---------------------------------------------------------------------------

def _open_search(device):
    for _ in range(6):
        els = _els(device)

        # Already showing an editable search field?
        for e in els:
            if _truthy(e.get("editable")) and "search" in _etext_l(e):
                return
        for e in els:
            if _truthy(e.get("editable")):
                return

        target = None
        for e in els:
            if not (_truthy(e.get("clickable")) or _truthy(e.get("editable"))):
                continue
            t = _etext_l(e)
            if "search" in t:
                target = e
                break

        if target is not None:
            idx = target.get("index")
            try:
                if idx is not None:
                    device.click(index=idx)
                else:
                    device.click(contains="search")
            except Exception:
                pass
            device.settle(1.5)
            if _has_editable(device):
                return
        else:
            try:
                device.click(contains="Search")
            except Exception:
                pass
            device.settle(1.5)
            if _has_editable(device):
                return

        device.settle(1.0)


# ---------------------------------------------------------------------------
# entering the query / submitting
# ---------------------------------------------------------------------------

_NAV_WORDS = {"back", "cancel", "clear", "more", "menu", "search", "close", "up"}


def _enter_query(device, location):
    # Find the search input field.
    idx = None
    for e in _els(device):
        if _truthy(e.get("editable")):
            idx = e.get("index")
            break

    if idx is None:
        try:
            idx = device.find(editable=True)
        except Exception:
            idx = None
    if idx is None:
        try:
            idx = device.find(hint="Search")
        except Exception:
            idx = None
    if idx is None:
        try:
            idx = device.find(contains="Search")
        except Exception:
            idx = None
    if idx is None:
        raise RuntimeError("search field not found")

    device.input_text(location, index=idx)
    device.settle(1.0)

    try:
        device.keyboard_enter()
    except Exception:
        pass
    device.settle(2.0)

    # If a dedicated submit button remains visible, press it.
    for _ in range(3):
        submit = None
        for e in _els(device):
            if not _truthy(e.get("clickable")):
                continue
            if _etext_l(e).strip() == "search":
                submit = e
                break
        if submit is None:
            return
        try:
            device.click(index=submit.get("index"))
        except Exception:
            return
        device.settle(2.0)


# ---------------------------------------------------------------------------
# choosing the first search result
# ---------------------------------------------------------------------------

def _choose_first_result(device, location):
    loc_l = location.lower()
    tokens = [t for t in re.split(r"[,\s]+", loc_l) if len(t) >= 3]

    for _ in range(8):
        candidates = []
        for e in _els(device):
            if not _truthy(e.get("clickable")):
                continue
            t = (e.get("text") or "").strip() or _etext(e).strip()
            if not t:
                continue
            tl = t.lower().strip()
            if tl in _NAV_WORDS:
                continue
            if "navigate up" in tl:
                continue
            candidates.append((e, tl))

        if not candidates:
            device.settle(1.2)
            continue

        pick = None
        # 1) whole-string match
        for e, tl in candidates:
            if loc_l and (loc_l in tl or tl in loc_l):
                pick = e
                break
        # 2) significant token match
        if pick is None:
            for e, tl in candidates:
                if any(tok in tl for tok in tokens):
                    pick = e
                    break
        # 3) first item (results are ordered best-first)
        if pick is None:
            pick = candidates[0][0]

        idx = pick.get("index")
        try:
            if idx is not None:
                device.click(index=idx)
            else:
                device.click(clickable=True)
        except Exception:
            try:
                device.click(clickable=True)
            except Exception:
                pass
        device.settle(2.5)
        return

    raise RuntimeError("no search result found for %r" % location)


# ---------------------------------------------------------------------------
# tapping the MARKER control (not the favourites star)
# ---------------------------------------------------------------------------

_FAV_STOP = ("favorite", "favourite", "star", "favourites", "favorites")


def _tap_marker(device):
    for _ in range(8):
        els = _els(device)

        # Direct marker control (skip favourites).
        target = None
        for e in els:
            tl = _etext_l(e)
            if any(b in tl for b in _FAV_STOP):
                continue
            if "marker" in tl:
                target = e
                break
        if target is not None:
            idx = target.get("index")
            try:
                if idx is not None:
                    device.click(index=idx)
                else:
                    device.click(contains="marker")
            except Exception:
                pass
            device.settle(2.5)
            return

        # Otherwise try to open an overflow / "More" menu and look again.
        more = None
        for e in els:
            if not _truthy(e.get("clickable")):
                continue
            tl = _etext_l(e).strip()
            if tl in ("more", "更多", "…", "...", "actions", "menu", "options",
                      "more options", "overflow"):
                more = e
                break
            if tl.startswith("more ") and len(tl) <= 20:
                more = e
                break
        if more is not None:
            try:
                device.click(index=more.get("index"))
            except Exception:
                pass
            device.settle(1.5)
            continue

        device.settle(1.2)

    raise RuntimeError("marker control not found in place panel")
