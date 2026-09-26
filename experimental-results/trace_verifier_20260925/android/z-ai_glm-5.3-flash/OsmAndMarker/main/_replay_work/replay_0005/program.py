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


class _DeadEnd(Exception):
    """Recoverable flow failure: restart OsmAnd and retry the flow."""


def program(device, binding: dict) -> bool:
    """Add a location marker for binding['location'] in the OsmAnd maps app.

    Flow: open OsmAnd -> open the search overlay -> type the location
    verbatim -> click the first search result to open the place panel ->
    tap the panel's MARKER control (not the favorites star). The marker
    is stored on tap; no confirmation dialog follows.

    Repairs added after the Nendeln failure (breakpoint showed a bare,
    status-bar-only accessibility tree and a search UI that never became
    usable):
    * OsmAnd's a11y tree sometimes collapses to a bare status-bar-only
      dump with no app controls.  Such a broken tree is detected and
      recovered by force-stopping and relaunching OsmAnd; the whole
      search-and-mark flow is then retried (up to 3 attempts).
    * Opening the search overlay is verified: the 'Type to search all'
      input must really appear; several search entry points are tried.
    * After typing, an echo check verifies the query actually landed;
      if not, the field is cleared and the query retyped.
    * 'INCREASE SEARCH RADIUS' is matched case-insensitively (the button
      may be rendered 'Increase search radius', which the old exact
      text find missed).
    * After clicking a search result, opening the place panel is retried
      (the first tap may only dismiss the keyboard), the panel is
      scrolled/expanded, an 'Actions' entry is tried and the result row
      is clicked again before the MARKER control is declared missing.

    A fresh OsmAnd search only covers a small radius (~20 mi) around the
    current map position.  When the place lies outside it, OsmAnd shows a
    'Could not find anything' screen with an INCREASE SEARCH RADIUS
    button; each press widens the radius and re-runs the search.  This
    flow keeps pressing that button (and periodically re-submits the
    query) until the place shows up, instead of giving up after a couple
    of presses.
    """
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon'")
    location = str(location).strip()

    # ---------------- element helpers ----------------

    def elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def safe_find(**kw):
        try:
            return device.find(**kw)
        except Exception:
            return None

    def text_of(e):
        return (e.get("text") or "").strip()

    def desc_of(e):
        return (e.get("description") or "").strip()

    def hint_of(e):
        return (e.get("hint") or "").strip()

    def blob_of(e):
        return (text_of(e) + " " + desc_of(e)).strip()

    def first_index(pred):
        for e in elements():
            if pred(e):
                return e.get("index")
        return None

    def norm(s):
        return re.sub(r"[\s,;]+", " ", (s or "").strip().lower())

    # ---------------- broken-tree detection & recovery ----------------

    app_keys = ("search", "type to search", "configure map", "compass",
                "zoom", "my location", "categories", "history",
                "directions", "favorites", "map markers", "plan route",
                "navigate", "increase search radius", "could not find",
                "clear")

    def tree_broken():
        els = elements()
        if not els:
            return True
        for e in els:
            if e.get("editable"):
                return False
            v = (desc_of(e) + " " + text_of(e) + " " + hint_of(e)).lower()
            for k in app_keys:
                if k in v:
                    return False
        return len(els) < 10

    def surely_broken():
        if not tree_broken():
            return False
        device.settle(2)
        return tree_broken()

    def dismiss_dialogs():
        for label in ("Skip", "Close", "Dismiss", "Got it", "Later",
                      "No thanks"):
            btn = first_index(lambda e, l=label: text_of(e).lower() == l.lower()
                              or desc_of(e).lower() == l.lower())
            if btn is not None:
                try:
                    device.click(btn)
                except Exception:
                    pass
                device.settle(1)

    def force_stop():
        for pkg in ("net.osmand", "net.osmand.plus"):
            try:
                device.adb_shell("am", "force-stop", pkg)
            except Exception:
                pass
        device.settle(2)

    def restart_app():
        force_stop()
        for _ in range(2):
            try:
                device.navigate_back()
                device.settle(1)
            except Exception:
                pass
        try:
            device.navigate_home()
            device.settle(1)
        except Exception:
            pass
        device.open_app("OsmAnd")
        device.settle(4)
        dismiss_dialogs()
        if tree_broken():
            force_stop()
            device.open_app("OsmAnd")
            device.settle(4)
            dismiss_dialogs()

    # ---------------- screen detection ----------------

    def search_field_index():
        idx = safe_find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = safe_find(text="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = safe_find(hint="Type to search all")
        if idx is not None:
            return idx
        idx = safe_find(text="Type to search all")
        if idx is not None:
            return idx
        fallback = None
        for e in elements():
            if e.get("editable"):
                b = (hint_of(e) + " " + text_of(e)).lower()
                if "search" in b or "type to" in b:
                    return e.get("index")
                if fallback is None:
                    fallback = e.get("index")
        return fallback

    def clear_button_index():
        idx = safe_find(description="Clear", clickable=True)
        if idx is not None:
            return idx
        idx = safe_find(description="Clear")
        if idx is not None:
            return idx
        return first_index(lambda e: desc_of(e).lower() == "clear"
                           or text_of(e).lower() == "clear")

    def increase_radius_index():
        # case-insensitive: the button may read 'Increase search radius'
        return first_index(lambda e: "increase search radius"
                           in (text_of(e) + " " + desc_of(e)).lower())

    def search_button_candidates():
        exact_c, exact, sub_c = [], [], []
        for e in elements():
            v = (desc_of(e) + " " + text_of(e)).strip().lower()
            if "search" not in v:
                continue
            if v == "search":
                if e.get("clickable"):
                    exact_c.append(e.get("index"))
                else:
                    exact.append(e.get("index"))
            elif e.get("clickable"):
                sub_c.append(e.get("index"))
        return exact_c + exact + sub_c

    def open_search():
        if search_field_index() is not None:
            return True  # search UI already open
        tried = set()
        for _ in range(6):
            pick = None
            for i in search_button_candidates():
                if i not in tried:
                    pick = i
                    break
            if pick is None:
                break
            tried.add(pick)
            try:
                device.click(pick)
            except Exception:
                continue
            device.settle(2)
            if search_field_index() is not None:
                return True
            device.settle(2)
            if search_field_index() is not None:
                return True
        return False

    # ---------------- search-result matching ----------------

    chrome_exact = {
        "search", "clear", "categories", "category", "history",
        "my locations", "favorites", "markers", "on the map",
        "navigate up", "type to search all", "increase search radius",
        "could not find anything", "search history", "recently visited",
    }

    def eligible(e, qn):
        if e.get("editable"):
            return False
        b = blob_of(e)
        if not b:
            return False
        bl = b.lower()
        if norm(b) == qn:  # the search box echo of the typed query
            return False
        if ("increase search radius" in bl or "could not find" in bl
                or "no result" in bl or "nothing found" in bl
                or "not found" in bl):
            return False
        return bl not in chrome_exact

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
        qn = norm(q)
        tokens = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
        elems = elements()

        # 1) coordinate-formatted result rows (both parts in one element)
        for ra, rb in coord_matchers(q):
            for e in elems:
                if eligible(e, qn) and ra.search(blob_of(e)) and rb.search(blob_of(e)):
                    return e.get("index")
            # 1b) lat / lon rendered as adjacent separate TextViews
            for i, e in enumerate(elems):
                if not eligible(e, qn) or not ra.search(blob_of(e)):
                    continue
                for j in (i + 1, i + 2, i - 1, i - 2):
                    if 0 <= j < len(elems):
                        nb = elems[j]
                        if eligible(nb, qn) and rb.search(blob_of(nb)):
                            return e.get("index")

        # 2) row containing the full query string
        for e in elems:
            if eligible(e, qn) and ql in blob_of(e).lower():
                return e.get("index")

        # 3) row containing every token (title + country in one text)
        if len(tokens) > 1:
            for e in elems:
                if eligible(e, qn):
                    b = blob_of(e).lower()
                    if all(tok in b for tok in tokens):
                        return e.get("index")

        # 4) row containing the primary token (country may be a separate
        #    subtitle element)
        if tokens:
            head = tokens[0]
            for e in elems:
                if eligible(e, qn) and head in blob_of(e).lower():
                    return e.get("index")
        return None

    # ---------------- radius-limited empty-search handling ----------------

    # Total INCREASE SEARCH RADIUS presses available for the whole run.
    radius_left = [22]

    def typed_echo_present(query):
        qn = norm(str(query))
        if not qn:
            return True
        frag = qn.split(" ")[0][:6]
        if len(frag) < 3:
            frag = qn[:6]
        for e in elements():
            b = norm(text_of(e) + " " + hint_of(e) + " " + desc_of(e))
            if frag and frag in b:
                return True
        return False

    def retype_query(query):
        """Clear the search box and type ``query`` again so the search
        re-runs at the currently widened radius.  Returns True on success."""
        try:
            if search_field_index() is None:
                if not open_search():
                    return False
            c = clear_button_index()
            if c is not None:
                device.click(c)
                device.settle(1)
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
            if surely_broken():
                return None
            idx = find_result_index(query)
            if idx is not None:
                return idx
            inc = increase_radius_index()
            if (inc is not None and clicks < max_clicks
                    and radius_left[0] > 0):
                try:
                    device.click(inc)  # widen radius; OsmAnd re-runs search
                except Exception:
                    pass
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

    def actions_button_index():
        for e in elements():
            v = (text_of(e) + " " + desc_of(e)).strip().lower()
            if v in ("actions", "more actions", "more"):
                return e.get("index")
        return None

    def try_open_panel_and_get_marker(query, first_idx):
        idx = first_idx
        for _ in range(4):
            if idx is None:
                idx = find_result_index(query)
            if idx is None:
                return None
            try:
                device.click(idx)
            except Exception:
                idx = None
                continue
            device.settle(2)
            idx = None
            m = find_marker_button()
            if m is not None:
                return m
            # the first tap may only have dismissed the keyboard
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(1)
            m = find_marker_button()
            if m is not None:
                return m
            # the panel may be collapsed or its action row scrolled aside
            for d in ("up", "left", "right"):
                device.scroll(d)
                device.settle(1)
                m = find_marker_button()
                if m is not None:
                    return m
            act = actions_button_index()
            if act is not None:
                try:
                    device.click(act)
                except Exception:
                    pass
                device.settle(1)
                m = find_marker_button()
                if m is not None:
                    return m
            device.settle(1)
            if surely_broken():
                return None
        return None

    # ---------------- one full search-and-mark attempt ----------------

    def run_flow():
        if surely_broken():
            raise _DeadEnd("a11y tree unusable before search")
        if search_field_index() is None:
            if not open_search():
                raise _DeadEnd("OsmAnd search screen did not open")
        # clear any stale query
        c = clear_button_index()
        if c is not None:
            device.click(c)
            device.settle(1)
        field = search_field_index()
        if field is None:
            if not open_search():
                raise _DeadEnd("OsmAnd search input field not found")
            field = search_field_index()
        if field is None:
            raise _DeadEnd("OsmAnd search input field not found")
        device.click(field)
        device.settle(1)
        field = search_field_index() or field
        device.input_text(location, index=field)
        device.settle(2)
        if not typed_echo_present(location):
            # the text did not land in the (right) field: clear and retype
            c = clear_button_index()
            if c is not None:
                device.click(c)
                device.settle(1)
            field = search_field_index()
            if field is not None:
                device.click(field)
                device.settle(1)
                device.input_text(location, index=field)
                device.settle(2)

        active_query = location
        result_idx = wait_for_result(location)

        # Last resort for over-specified place names ("Nendeln, Liechtenstein"):
        # retry with the primary token only. Coordinates are never reformatted.
        if result_idx is None and re.search(r"[A-Za-z]", location):
            raw_tokens = [t for t in re.split(r"[\s,;]+", location) if t]
            if len(raw_tokens) > 1:
                active_query = raw_tokens[0]
                if not retype_query(active_query):
                    raise _DeadEnd("OsmAnd search field not found for retry")
                result_idx = wait_for_result(active_query, rounds=16,
                                             max_clicks=24)

        if result_idx is None:
            raise _DeadEnd("OsmAnd search returned no results for %r" % location)

        marker_idx = try_open_panel_and_get_marker(active_query, result_idx)
        if marker_idx is None:
            raise _DeadEnd("OsmAnd place panel: 'Marker' control not found")

        device.click(marker_idx)  # marker is stored on tap; no confirmation
        device.settle(1)

    # ---------- 1) launch OsmAnd ----------

    device.open_app("OsmAnd")
    device.settle(3)
    dismiss_dialogs()
    relaunches = 0
    while tree_broken() and relaunches < 3:
        relaunches += 1
        restart_app()
    if tree_broken():
        raise RuntimeError("OsmAnd did not open to a usable screen "
                           "(accessibility tree stays empty)")

    # ---------- 2..6) search the place and tap MARKER, with restarts ----

    last_err = None
    for attempt in range(3):
        try:
            run_flow()
            return True
        except _DeadEnd as e:
            last_err = str(e)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
        restart_app()
    raise RuntimeError("OsmAnd marker flow failed after restarts: %s" % last_err)
