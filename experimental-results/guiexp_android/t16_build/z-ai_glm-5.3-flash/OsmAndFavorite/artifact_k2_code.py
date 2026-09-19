"""Reusable, parameterized OsmAnd automation: add a favorite marker for a location.

Family goal: 'Add a favorite location marker for {location} in the OsmAnd maps app.'
Flow: launch OsmAnd -> open Search -> type the binding location VERBATIM ->
pick the first search result (retrying via 'INCREASE SEARCH RADIUS' when the
initial radius yields nothing) -> tap the FAVORITES (star) control on the place
panel (NOT the marker control) -> accept the proposed name in the dialog.
"""

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Location to save as an OsmAnd favorite. One string: either a place "
            "name such as 'Schaan, Liechtenstein' or a 'lat, lon' coordinate "
            "pair. It is typed verbatim into the search field; it is never "
            "reformatted or rounded."
        ),
    }
}

_SAVE_EXACT_STRONG = ("save", "add", "apply")
_SAVE_EXACT_WEAK = ("ok", "okay", "done", "accept", "confirm", "yes")


def _norm(value):
    return " ".join(str(value or "").split()).lower()


def _el_text(el):
    return _norm(el.get("text"))


def _el_desc(el):
    return _norm(el.get("description"))


def _needle_in_text(needle, text):
    """Case-insensitive contains that will not match a digit-leading needle
    in the middle of a longer number (e.g. '47°' inside '147°')."""
    if not needle:
        return False
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return False
        if not (needle[0].isdigit() and i > 0 and text[i - 1].isdigit()):
            return True
        start = i + 1


def _coordinate_needles(loc):
    """Degree-formatted needles for matching a 'lat, lon' result row.
    Used only for matching on-screen results; the typed query is never altered."""
    strong, weak = [], []
    parts = [p.strip() for p in str(loc).split(",") if p.strip()]
    if not parts:
        return strong, weak
    try:
        lat = float(parts[0].strip("NSEWnsew° "))
    except ValueError:
        return strong, weak
    for nd in (5, 4, 3):
        s = "%.*f" % (nd, lat)
        for v in (s, s + u"\N{DEGREE SIGN}"):
            if v not in strong:
                strong.append(v)
    if parts[0] not in strong:
        strong.append(parts[0])
    weak.append("%d%s" % (int(abs(lat)), u"\N{DEGREE SIGN}"))
    return strong, weak


def _result_needle_tiers(location):
    """Needles ordered by priority: exact query / coordinate forms / main
    part first; weaker needles (secondary parts, bare degree number) last."""
    loc = str(location).strip()
    full = _norm(loc)
    cstrong, cweak = _coordinate_needles(loc)
    parts = [_norm(p) for p in loc.split(",") if p.strip()]
    tier1 = []
    if full:
        tier1.append(full)
    for n in cstrong:
        if n not in tier1:
            tier1.append(n)
    if parts and parts[0] and parts[0] not in tier1:
        tier1.append(parts[0])
    tier2 = [n for n in cweak if n not in tier1]
    for p in parts:
        if p and p not in tier1 and p not in tier2:
            tier2.append(p)
    tiers = []
    if tier1:
        tiers.append(tier1)
    if tier2:
        tiers.append(tier2)
    return tiers


def _find_result_row(device, location):
    """Return the index of the first search-result row for the location,
    or None. The search field itself (editable) is always excluded."""
    for needles in _result_needle_tiers(location):
        for el in device.elements():
            if el.get("editable"):
                continue
            t = _el_text(el)
            if not t or "increase search radius" in t:
                continue
            for needle in needles:
                if _needle_in_text(needle, t):
                    return el.get("index")
    return None


def _click_increase_radius(device):
    """Click the 'INCREASE SEARCH RADIUS' retry row if the search came up
    empty; returns True if it was clicked."""
    for el in device.elements():
        blob = _el_text(el) + " " + _el_desc(el)
        if "increase search radius" in blob:
            device.click(el.get("index"))
            return True
    return False


def _find_favorite_control(device):
    """Find the FAVORITES (star) control on the place panel, explicitly
    avoiding the marker control next to it."""
    best = None
    for el in device.elements():
        desc = _el_desc(el)
        text = _el_text(el)
        blob = (desc + " " + text).strip()
        if not blob:
            continue
        if "nearest" in blob:
            continue
        if "marker" in blob and "favorit" not in blob:
            continue
        if "favorit" in desc or "favourite" in desc:
            score = 5
        elif "favorit" in text or "favourite" in text:
            score = 4
        elif "fav" in desc and el.get("clickable"):
            score = 2
        else:
            continue
        if el.get("clickable"):
            score += 2
        if "add" in blob:
            score += 1
        if len(text) > 40:
            score -= 3
        if best is None or score > best[0]:
            best = (score, el.get("index"))
    return None if best is None else best[1]


def _find_save_button(device):
    """Find the confirm control of the add-favorite dialog (the dialog's
    proposed name is accepted unchanged)."""
    best = None
    for el in device.elements():
        if not el.get("clickable"):
            continue
        t = _el_text(el)
        d = _el_desc(el)
        score = 0
        if t in _SAVE_EXACT_STRONG:
            score = 6
        elif d in _SAVE_EXACT_STRONG:
            score = 5
        elif t in _SAVE_EXACT_WEAK:
            score = 4
        elif d in _SAVE_EXACT_WEAK:
            score = 3
        elif t.startswith("save") or d.startswith("save"):
            score = 2
        if score and (best is None or score > best[0]):
            best = (score, el.get("index"))
    return None if best is None else best[1]


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if not isinstance(location, str) or not location.strip():
        raise ValueError("binding['location'] must be a non-empty string")
    location = location.strip()

    # --- 1. Launch OsmAnd --------------------------------------------------
    if not device.open_app("OsmAnd"):
        raise RuntimeError("Failed to launch OsmAnd; is it installed?")
    device.settle(2)

    search_btn = None
    for _ in range(4):
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is not None:
            break
        device.settle(1.5)
    if search_btn is None:
        raise RuntimeError("OsmAnd map screen (Search button) not visible after launch")

    # --- 2. Open the search UI ----------------------------------------------
    device.click(search_btn)
    device.settle(1.5)

    # --- 3. Type the location verbatim and run the search --------------------
    search_field = None
    for _ in range(4):
        search_field = device.find(hint="Type to search all", editable=True)
        if search_field is None:
            search_field = device.find(
                hint="search", contains=True, editable=True, clickable=True
            )
        if search_field is not None:
            break
        device.settle(1.5)
    if search_field is None:
        raise LookupError("OsmAnd search field (hint 'Type to search all') not found")

    device.click(search_field)
    device.settle(0.5)
    refocused = device.find(hint="Type to search all", editable=True)
    if refocused is None:
        refocused = device.find(hint="search", contains=True, editable=True)
    device.input_text(location, index=refocused if refocused is not None else search_field)
    device.keyboard_enter()
    device.settle(1.5)

    # --- 4. Select the first search result -----------------------------------
    # Widen the search radius when OsmAnd initially finds nothing nearby.
    result_idx = None
    for attempt in range(8):
        result_idx = _find_result_row(device, location)
        if result_idx is not None:
            break
        if _click_increase_radius(device):
            device.settle(2)
            continue
        if attempt in (2, 5):
            device.keyboard_enter()
        else:
            device.wait()
    if result_idx is None:
        raise LookupError(
            "OsmAnd returned no search results for location %r" % location
        )

    device.click(result_idx)
    device.settle(2)

    # --- 5. Tap the FAVORITES (star) control on the place panel ---------------
    star_idx = None
    for i in range(5):
        star_idx = _find_favorite_control(device)
        if star_idx is not None:
            break
        device.scroll(direction="up" if i % 2 == 0 else "down")
    if star_idx is None:
        raise RuntimeError(
            "Favorites (star) control not found on the place panel for %r" % location
        )
    device.click(star_idx)
    device.settle(1.5)

    # --- 6. Accept the proposed name in the add-favorite dialog ----------------
    save_idx = None
    for i in range(6):
        save_idx = _find_save_button(device)
        if save_idx is not None:
            break
        if i == 2:
            retry_star = _find_favorite_control(device)
            if retry_star is not None:
                device.click(retry_star)
        elif i == 3:
            device.scroll(direction="up")
        elif i == 4:
            device.scroll(direction="down")
        else:
            device.wait()
    if save_idx is None:
        raise RuntimeError(
            "Add-favorite dialog (save control) did not appear after tapping the star"
        )

    device.click(save_idx)
    device.settle(1.5)

    # --- 7. Verify the dialog closed (favorite stored) --------------------------
    for i in range(4):
        if _find_save_button(device) is None:
            return True
        if i < 2:
            again = _find_save_button(device)
            device.click(again)
        else:
            device.wait()
    raise RuntimeError(
        "Favorite dialog did not close after saving; favorite for %r may not "
        "have been stored" % location
    )
