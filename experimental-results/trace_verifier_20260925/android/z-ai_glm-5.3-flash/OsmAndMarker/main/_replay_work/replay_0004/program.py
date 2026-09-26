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
    },
}


def program(device, binding: dict) -> bool:
    """Add a location marker for binding['location'] in the OsmAnd maps app.

    Flow: open OsmAnd -> open the search overlay -> type the location
    verbatim -> click the first search result to open the place panel ->
    tap the panel's MARKER control (not the favorites star). The marker
    is stored on tap; no confirmation dialog follows.

    A fresh OsmAnd search only covers a small radius (~20 mi) around the
    current map position.  When the place lies outside it, OsmAnd shows a
    "Could not find anything: 20 mi" screen with an INCREASE SEARCH RADIUS
    button; each press widens the radius and re-runs the search.  This flow
    keeps pressing that button (and periodically re-submits the query) until
    the place shows up, instead of giving up after a couple of presses.
    """
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon'")
    location = str(location).strip()

    # ---------------- element helpers ----------------

    def elements():
        return device.elements()

    def text_of(e):
        return (e.get("text") or "").strip()

    def desc_of(e):
        return (e.get("description") or "").strip()

    def hint_of(e):
        return (e.get("hint") or "").strip()

    def first_index(pred):
        for e in elements():
            if pred(e):
                return e.get("index")
        return None

    # ---------------- screen detection ----------------

    def on_map_screen():
        if device.find(description="Configure map") is not None:
            return True
        if device.find(description="Search", clickable=True) is not None:
            return True
        wanted = ("configure map", "compass", "my location", "zoom in",
                  "zoom out", "map")
        return first_index(lambda e: desc_of(e).lower() in wanted) is not None

    def search_field_index():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Type to search all", editable=True)
        if idx is not None:
            return idx
        fallback = None
        for e in elements():
            if e.get("editable"):
                blob = (hint_of(e) + " " + text_of(e)).lower()
                if "search" in blob or "type to" in blob:
                    return e.get("index")
                if fallback is None:
                    fallback = e.get("index")
        return fallback

    def clear_button_index():
        idx = device.find(description="Clear", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="Clear")
        if idx is not None:
            return idx
        return first_index(lambda e: desc_of(e).lower() == "clear")

    def increase_radius_index():
        idx = device.find(text="INCREASE SEARCH RADIUS")
        if idx is not None:
            return idx
        return first_index(lambda e: "increase search radius"
                           in (text_of(e) + " " + desc_of(e)).lower())

    def open_search():
        if search_field_index() is not None:
            return  # search UI already open
        idx = device.find(description="Search", clickable=True)
        if idx is None:
            idx = device.find(description="Search")
        if idx is None:
            idx = first_index(lambda e: e.get("clickable")
                              and desc_of(e).lower() == "search")
        if idx is None:
            idx = first_index(lambda e: e.get("clickable")
                              and "search" in desc_of(e).lower())
        if idx is None:
            raise RuntimeError("OsmAnd 'Search' button not found on the map screen")
        device.click(idx)
        device.settle(2)
        if search_field_index() is None:
            device.settle(2)
        if search_field_index() is None:
            raise RuntimeError("OsmAnd search screen did not open")

    # ---------------- search-result matching ----------------

    chrome_exact = {
        "search", "clear", "categories", "category", "history",
        "my locations", "favorites", "markers", "on the map",
        "navigate up", "type to search all", "increase search radius",
        "could not find anything", "search history", "recently visited",
    }

    def eligible(e):
        if e.get("editable"):
            return False
        t = text_of(e)
        if not t:
            return False
        tl = t.lower()
        if ("increase search radius" in tl or "could not find" in tl
                or "no result" in tl or "nothing found" in tl
                or "not found" in tl):
            return False
        return tl not in chrome_exact

    def coord_matchers(query):
        # OsmAnd reformats typed coordinates, e.g.
        # "47.0688832, 9.5061564" -> "47.06888° N, 9.50616° E".
        q = query.strip()
        if not re.match(r"-?\d+(\.\d+)?\s*[,;\s]\s*-?\d+(\.\d+)?", q):
            return []
        nums = re.findall(r"-?\d+(?:\.\d+)?", q)
        if len(nums) < 2:
            return []
        try:
            lat = float(nums[0])
            lon = float(nums[1])
        except ValueError:
            return []
        la_i, lo_i = str(int(abs(lat))), str(int(abs(lon)))
        la_h = "N" if lat >= 0 else "S"
        lo_h = "E" if lon >= 0 else "W"
        num_part = (r"(?:\.\d+|\s*\u00b0\s*\d+"
                    r"(?:\s*[\u00b0\u2032'\"]\s*\d+(?:\.\d+)?)?)?")
        hemi_la = re.compile(la_i + num_part + r"[^A-Za-z\d]{0,4}" + la_h + r"\b")
        hemi_lo = re.compile(lo_i + num_part + r"[^A-Za-z\d]{0,4}" + lo_h + r"\b")
        pre_la = re.compile(la_h + r"\s*" + la_i + r"(?:\.\d+)?")
        pre_lo = re.compile(lo_h + r"\s*" + lo_i + r"(?:\.\d+)?")
        plain_la = re.compile(la_i + r"\.\d{2,7}")
        plain_lo = re.compile(lo_i + r"\.\d{2,7}")
        raw_la = re.compile(re.escape(nums[0]))
        raw_lo = re.compile(re.escape(nums[1]))
        return [(hemi_la, hemi_lo), (pre_la, pre_lo),
                (plain_la, plain_lo), (raw_la, raw_lo)]

    def find_result_index(query):
        q = str(query).strip()
        ql = q.lower()
        tokens = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
        elems = elements()

        # 1) coordinate-formatted result rows (both parts in one element)
        for ra, rb in coord_matchers(q):
            for e in elems:
                if eligible(e) and ra.search(text_of(e)) and rb.search(text_of(e)):
                    return e.get("index")
            # 1b) lat / lon rendered as adjacent separate TextViews
            for i, e in enumerate(elems):
                if not eligible(e) or not ra.search(text_of(e)):
                    continue
                for j in (i + 1, i + 2, i - 1, i - 2):
                    if 0 <= j < len(elems):
                        nb = elems[j]
                        if eligible(nb) and rb.search(text_of(nb)):
                            return e.get("index")

        # 2) row containing the full query string
        for e in elems:
            if eligible(e) and ql in text_of(e).lower():
                return e.get("index")

        # 3) row containing every token (title + country in one text)
        if len(tokens) > 1:
            for e in elems:
                if eligible(e):
                    t = text_of(e).lower()
                    if all(tok in t for tok in tokens):
                        return e.get("index")

        # 4) row containing the primary token (country may be a separate
        #    subtitle element)
        if tokens:
            head = tokens[0]
            for e in elems:
                if eligible(e):
                    t = text_of(e).lower()
                    if head in t and t != ql:
                        return e.get("index")
        return None

    # ---------------- radius-limited empty-search handling ----------------

    # Total INCREASE SEARCH RADIUS presses available for the whole run.
    # Each press roughly doubles the search radius, so a dozen presses turn
    # the initial ~20 mi radius into a worldwide one.
    radius_left = [22]

    def retype_query(query):
        """Clear the search box and type ``query`` again so the search
        re-runs at the currently widened radius.  Returns True on success."""
        try:
            if search_field_index() is None:
                open_search()
            c = clear_button_index()
            if c is not None:
                device.click(c)
                device.settle(1)
            field = search_field_index()
            if field is None:
                open_search()
                field = search_field_index()
            if field is None:
                return False
            device.click(field)
            device.settle(1)
            field = search_field_index() or field
            device.input_text(str(query), index=field)
            device.settle(2)
            return True
        except Exception:
            return False

    def wait_for_result(query, rounds=24, max_clicks=12):
        clicks = 0
        enters = 0
        retypes = 0
        for _ in range(rounds):
            idx = find_result_index(query)
            if idx is not None:
                return idx
            inc = increase_radius_index()
            if (inc is not None and clicks < max_clicks
                    and radius_left[0] > 0):
                device.click(inc)  # widen radius; OsmAnd re-runs the search
                device.settle(2)
                clicks += 1
                radius_left[0] -= 1
                if clicks % 3 == 0:
                    # make sure the search re-runs at the widened radius
                    device.keyboard_enter()
                    device.settle(2)
                continue
            if enters < 2:
                device.keyboard_enter()
                device.settle(2)
                enters += 1
                continue
            if retypes < 2:
                retypes += 1
                retype_query(query)
                continue
            device.scroll("down")
            device.settle(1)
        return find_result_index(query)

    # ---------------- place-panel MARKER control ----------------

    def find_marker_button():
        elems = elements()
        strict = ("marker", "add marker", "add_marker", "add map marker",
                  "add to markers", "add to map markers")
        for e in elems:
            for v in (text_of(e), desc_of(e)):
                if v.lower() in strict:
                    return e.get("index")
        for e in elems:
            for v in (text_of(e), desc_of(e)):
                vl = v.lower()
                if not vl or "markers" in vl or "map marker" in vl:
                    continue
                if (vl.startswith("add marker") or vl.startswith("marker ")
                        or vl.endswith(" marker")):
                    return e.get("index")
        return None

    def find_marker_button_loose():
        for e in elements():
            v = (text_of(e) + " " + desc_of(e)).lower()
            if ("marker" in v and "markers" not in v
                    and "map marker" not in v and "favorite" not in v
                    and "share" not in v):
                return e.get("index")
        return None

    # ---------- 1) launch OsmAnd ----------

    opened = False
    for _ in range(3):
        device.open_app("OsmAnd")
        device.settle(3)
        if on_map_screen() or search_field_index() is not None:
            opened = True
            break
        for label in ("Skip", "Close", "Dismiss", "Got it", "Later", "No thanks"):
            btn = first_index(lambda e, l=label: text_of(e).lower() == l.lower()
                              or desc_of(e).lower() == l.lower())
            if btn is not None:
                device.click(btn)
                device.settle(1)
                break
    if not opened:
        raise RuntimeError("OsmAnd did not open to its map screen")

    # ---------- 2) open the search overlay, clear any stale query ----------

    open_search()
    clear_idx = clear_button_index()
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)

    # ---------- 3) type the location VERBATIM into the search field ----------

    field = search_field_index()
    if field is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(field)
    device.settle(1)
    field = search_field_index() or field
    device.input_text(location, index=field)
    device.settle(2)

    # ---------- 4) wait for the first search result ----------

    active_query = location
    result_idx = wait_for_result(location)

    # Last resort for over-specified place names ("Malbun, Liechtenstein"):
    # retry with the primary token only. Coordinates are never reformatted.
    if result_idx is None and re.search(r"[A-Za-z]", location):
        raw_tokens = [t for t in re.split(r"[\s,;]+", location) if t]
        if len(raw_tokens) > 1:
            active_query = raw_tokens[0]
            if not retype_query(active_query):
                raise RuntimeError("OsmAnd search field not found for retry")
            result_idx = wait_for_result(active_query, rounds=16, max_clicks=24)

    if result_idx is None:
        raise RuntimeError("OsmAnd search returned no results for %r" % location)

    # ---------- 5) open the place panel by clicking the first result ----------

    device.click(result_idx)
    device.settle(2)

    # ---------- 6) tap the MARKER control on the place panel ----------

    marker_idx = find_marker_button()
    if marker_idx is None:
        device.settle(2)
        marker_idx = find_marker_button()
    if marker_idx is None and search_field_index() is not None:
        # still on the search screen: the first tap may only have dismissed
        # the keyboard -> click the result row again
        retry_idx = find_result_index(active_query)
        if retry_idx is not None:
            device.click(retry_idx)
            device.settle(2)
            marker_idx = find_marker_button()
    if marker_idx is None:
        # the place panel's action row may scroll horizontally
        for direction in ("left", "right"):
            device.scroll(direction)
            device.settle(1)
            marker_idx = find_marker_button()
            if marker_idx is not None:
                break
    if marker_idx is None:
        marker_idx = find_marker_button_loose()
    if marker_idx is None:
        raise RuntimeError("OsmAnd place panel: 'Marker' control not found")

    device.click(marker_idx)  # marker is stored on tap; no confirmation
    device.settle(1)
    return True
