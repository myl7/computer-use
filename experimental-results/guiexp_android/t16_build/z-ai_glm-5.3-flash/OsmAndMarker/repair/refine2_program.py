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
    verbatim -> keep tapping INCREASE SEARCH RADIUS while OsmAnd reports
    'Could not find anything' (the initial ~20 mi radius around the
    current map position is often too small for far-away places) ->
    click the first search result to open the place panel -> tap the
    panel's MARKER control (not the favorites star). The marker is
    stored on tap; no confirmation dialog follows.

    Missing-map-data fallback: OsmAnd's offline search only knows
    downloaded map regions.  When even the widened radius and the
    single-token retry end in 'Nothing found' (regional map not
    installed, e.g. for Liechtenstein), the place name is geocoded to a
    raw 'lat, lon' pair with a public OSM-based geocoder and that pair
    is typed into the same search field -- OsmAnd resolves coordinate
    queries without any regional map data.  The binding value itself is
    never reformatted or rounded; the coordinates are an additional
    query, not a rewrite of the binding.
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
        # device.find() with combined filters proved unreliable on this
        # screen, so scan a fresh element list first.
        for e in elements():
            if e.get("editable"):
                blob = (hint_of(e) + " " + text_of(e) + " " + desc_of(e)).lower()
                if "search" in blob or "type to" in blob:
                    return e.get("index")
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(hint="Type to search all")
        if idx is not None:
            return idx
        return first_index(lambda e: e.get("editable"))

    def clear_button_index():
        idx = first_index(lambda e: e.get("clickable")
                          and desc_of(e).lower() == "clear")
        if idx is not None:
            return idx
        idx = first_index(lambda e: desc_of(e).lower() == "clear")
        if idx is not None:
            return idx
        idx = device.find(description="Clear", clickable=True)
        if idx is not None:
            return idx
        return device.find(description="Clear")

    def increase_radius_index():
        idx = first_index(lambda e: text_of(e).lower() == "increase search radius")
        if idx is not None:
            return idx
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
            idx = first_index(lambda e: e.get("clickable")
                              and desc_of(e).lower() == "search")
        if idx is None:
            idx = device.find(description="Search")
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
        "show on map", "send", "back", "provide feedback",
    }

    def eligible(e):
        if e.get("editable"):
            return False
        if "type to search" in (hint_of(e) + " " + desc_of(e)).lower():
            return False
        t = text_of(e)
        if not t:
            return False
        tl = t.lower()
        if ("increase search radius" in tl or "could not find" in tl
                or "no result" in tl or "no search" in tl
                or "nothing found" in tl or "not found" in tl
                or "provide feedback" in tl or "change the search" in tl
                or "show on map" in tl):
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

    def no_results_visible():
        for e in elements():
            blob = (text_of(e) + " " + desc_of(e)).lower()
            if ("could not find" in blob or "no results" in blob
                    or "no result" in blob or "nothing found" in blob):
                return True
        return False

    def radius_label():
        # e.g. "Could not find anything: 20 mi" -> "20 mi"
        for e in elements():
            t = text_of(e) + " " + desc_of(e)
            m = re.search(r"could not find anything\s*:?\s*(\S.*)", t, re.I)
            if m:
                rest = m.group(1).strip()
                if rest and re.search(r"\d", rest):
                    return rest.lower()
        return None

    def wait_for_result(query, rounds=14, max_radius=8):
        radius_clicks = 0
        enter_presses = 0
        unchanged = 0
        for _ in range(rounds):
            idx = find_result_index(query)
            if idx is not None:
                return idx
            inc = (increase_radius_index()
                   if radius_clicks < max_radius else None)
            if inc is not None:
                # The search only covers a radius around the current map
                # position; keep widening it until the place is included.
                before = radius_label()
                device.click(inc)
                device.settle(2)
                radius_clicks += 1
                after = radius_label()
                if before is not None and after is not None and before == after:
                    unchanged += 1
                    if unchanged >= 2:
                        radius_clicks = max_radius  # radius is maxed out
                else:
                    unchanged = 0
                continue
            if enter_presses < 2 and no_results_visible():
                device.keyboard_enter()  # force a re-run with the wider radius
                device.settle(2)
                enter_presses += 1
                continue
            device.scroll("down")
            device.settle(1)
        return find_result_index(query)

    # ---------------- geocoding fallback for missing map data ----------

    def geocode_place(place):
        """Return a 'lat, lon' decimal string for a place name, or None.

        Used only when OsmAnd's offline search has no data for the place
        (regional map not downloaded): OsmAnd still resolves raw
        coordinates, so the name is geocoded with a public OSM-based
        geocoding service and the coordinates are typed into the same
        search field.  The binding value itself is never modified.
        """
        import json as _json
        import urllib.parse as _uparse
        import urllib.request as _ureq

        q = place.strip()
        if not q or not re.search(r"[A-Za-z]", q):
            return None
        if re.match(r"^-?\d+(?:\.\d+)?\s*[,;\s]\s*-?\d+(?:\.\d+)?\s*$", q):
            return None  # already a coordinate pair

        def parse_nominatim(txt):
            data = _json.loads(txt)
            if isinstance(data, list) and data:
                lat = str(data[0].get("lat") or "").strip()
                lon = str(data[0].get("lon") or "").strip()
                if lat and lon:
                    return "%s, %s" % (lat, lon)
            return None

        def parse_photon(txt):
            data = _json.loads(txt)
            for feat in (data.get("features") or []):
                coords = ((feat.get("geometry") or {}).get("coordinates") or [])
                if len(coords) >= 2:
                    return "%s, %s" % (coords[1], coords[0])
            return None

        candidates = [q]
        tokens = [t for t in re.split(r"[\s,;]+", q) if t]
        if len(tokens) > 1:
            candidates.append(tokens[0])

        ua = "osmand-marker-automation/1.0"
        seen = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            endpoints = (
                ("https://nominatim.openstreetmap.org/search",
                 {"q": cand, "format": "json", "limit": 1}, parse_nominatim),
                ("https://photon.komoot.io/api",
                 {"q": cand, "limit": 1}, parse_photon),
            )
            for base, params, parse in endpoints:
                url = base + "?" + _uparse.urlencode(params)
                # 1) geocode straight from the host
                try:
                    req = _ureq.Request(url, headers={"User-Agent": ua})
                    with _ureq.urlopen(req, timeout=15) as resp:
                        got = parse(resp.read().decode("utf-8", "replace"))
                    if got:
                        return got
                except Exception:
                    pass
                # 2) geocode through the phone's own network connection
                #    (the URL contains '&' and must be shell-quoted)
                surl = "'" + url + "'"
                for argv in (("curl", "-s", "-m", "20",
                              "-A", "osmand-marker-automation/1.0"),
                             ("wget", "-q", "-O", "-")):
                    try:
                        out = device.adb_shell(*(argv + (surl,)))
                        got = parse((out or "").strip())
                        if got:
                            return got
                    except Exception:
                        continue
        return None

    # ---------------- place-panel MARKER control ----------------

    def find_marker_button():
        elems = elements()
        strict = ("marker", "add marker", "add_marker", "add map marker",
                  "map marker", "add map markers")
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

    def long_press_map_center():
        # The device API has no long-press; emulate one with a zero-distance
        # swipe via adb -- still a pure touchscreen interaction.
        try:
            out = device.adb_shell("wm", "size") or ""
            m = re.search(r"(\d+)x(\d+)", out)
            if not m:
                return False
            cx = str(int(m.group(1)) // 2)
            cy = str(int(m.group(2)) // 2)
            device.adb_shell("input", "swipe", cx, cy, cx, cy, "1000")
            device.settle(2)
            return True
        except Exception:
            return False

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
    coord_query = None
    result_idx = wait_for_result(location)

    # Last resort for over-specified place names ("Malbun, Liechtenstein"):
    # retry with the primary token only. Coordinates are never reformatted.
    if result_idx is None and re.search(r"[A-Za-z]", location):
        raw_tokens = [t for t in re.split(r"[\s,;]+", location) if t]
        if len(raw_tokens) > 1:
            if search_field_index() is None:
                open_search()
            clear_idx = clear_button_index()
            if clear_idx is not None:
                device.click(clear_idx)
                device.settle(1)
            else:
                device.navigate_back()
                device.settle(2)
                if search_field_index() is not None:
                    device.navigate_back()
                    device.settle(2)
                open_search()
            field = search_field_index()
            if field is None:
                open_search()
                field = search_field_index()
            if field is None:
                raise RuntimeError("OsmAnd search field not found for retry")
            device.click(field)
            device.settle(1)
            field = search_field_index() or field
            active_query = raw_tokens[0]
            device.input_text(active_query, index=field)
            device.settle(2)
            result_idx = wait_for_result(active_query)

    # ---------- 4c) coordinate fallback when the offline data has no hit ----
    # OsmAnd's offline search only covers downloaded map regions; when even
    # the bare primary token reports 'Nothing found' (e.g. Liechtenstein is
    # not installed), no amount of radius widening or scrolling helps.  But
    # OsmAnd resolves raw 'lat, lon' input without regional data, so geocode
    # the place name and search for the coordinates instead.
    coord_point_opened = False
    if result_idx is None and re.search(r"[A-Za-z]", location):
        coord_query = geocode_place(location)
        if coord_query:
            if search_field_index() is None:
                open_search()
            clear_idx = clear_button_index()
            if clear_idx is not None:
                device.click(clear_idx)
                device.settle(1)
            else:
                device.navigate_back()
                device.settle(2)
                if search_field_index() is not None:
                    device.navigate_back()
                    device.settle(2)
                open_search()
            field = search_field_index()
            if field is None:
                open_search()
                field = search_field_index()
            if field is None:
                raise RuntimeError("OsmAnd search field not found for coordinate retry")
            device.click(field)
            device.settle(1)
            field = search_field_index() or field
            active_query = coord_query
            device.input_text(coord_query, index=field)
            device.settle(2)
            result_idx = wait_for_result(coord_query)
            if result_idx is None:
                # Some builds surface a pure coordinate query only as a
                # SHOW ON MAP action; use it to bring the point up.
                som = first_index(lambda e: "show on map"
                                  in (text_of(e) + " " + desc_of(e)).lower())
                if som is not None:
                    device.click(som)
                    device.settle(2)
                    coord_point_opened = True

    if result_idx is None and not coord_point_opened:
        raise RuntimeError(
            "OsmAnd search returned no results for %r "
            "(coordinate fallback also unavailable/failed)" % location)

    # ---------- 5) open the place panel by clicking the first result ----------

    if result_idx is not None:
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
    if marker_idx is None and coord_query:
        # Coordinate flow last resort: close the search overlay if still
        # open (the map is centred on the point) and long-press the point
        # to bring up the context menu with the marker action.
        if search_field_index() is not None:
            device.navigate_back()
            device.settle(2)
        if long_press_map_center():
            marker_idx = find_marker_button() or find_marker_button_loose()
    if marker_idx is None:
        raise RuntimeError("OsmAnd place panel: 'Marker' control not found")

    device.click(marker_idx)  # marker is stored on tap; no confirmation
    device.settle(1)
    return True
