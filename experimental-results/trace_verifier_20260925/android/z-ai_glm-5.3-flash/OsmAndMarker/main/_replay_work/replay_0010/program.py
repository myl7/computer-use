import re
import xml.etree.ElementTree as ET

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

    Kept behaviour (instances that already pass):
    * opening the search overlay is verified and several entry points
      are tried; the typed query is echo-checked and retyped if it did
      not land; the query is never reformatted or rounded;
    * 'INCREASE SEARCH RADIUS' is matched case-insensitively and pressed
      (with periodic re-submits) until the place shows up -- a fresh
      OsmAnd search only covers ~20 mi around the map position;
    * the place panel opening is retried (the first tap may dismiss the
      keyboard), the panel is scrolled/expanded, 'Actions' is tried and
      the result row is clicked again before MARKER is declared missing;
    * over-specified place names retry with the primary token only
      (coordinates are never reformatted).

    Repairs for the Nendeln failure (the accessibility tree stayed a bare
    status-bar-only dump, so the old code typed the query into the
    status-bar clock and never opened any search UI):
    * a tree only counts as usable when REAL OsmAnd controls are visible
      (search/compass/zoom/actions/... or an editable field) AND OsmAnd
      holds the window focus -- a >=10-element status-bar-only dump is no
      longer mistaken for the app UI;
    * status-bar junk (clock, battery, wifi/signal, notifications) is
      blacklisted: never clicked, never typed into, never matched as the
      search field or as a search result;
    * bare-tree recovery ladder: tap the map view node (dismiss shades,
      refocus OsmAnd's window), press back, go home and re-open the app,
      force-stop and relaunch -- re-checking after every step;
    * if the accessibility tree stays bare, the whole flow is re-driven
      from fresh `adb shell uiautomator dump` snapshots: elements are
      located by text/content-desc/resource-id and tapped by screen
      coordinates (`adb shell input tap`); text is typed with
      `adb shell input text` (spaces as %s), still verbatim.
    """
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon'")
    location = str(location).strip()

    # ---------------- mode, budgets & element sources ----------------

    mode = ["a11y"]        # "a11y": device.elements(); "dump": uiautomator
    radius_left = [22]     # INCREASE SEARCH RADIUS presses for the whole run
    a11y_fails = [0]

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

    def rid_of(e):
        return (e.get("rid") or "").strip().lower()

    def blob_of(e):
        return (text_of(e) + " " + desc_of(e)).strip()

    def norm(s):
        return re.sub(r"[\s,;]+", " ", (s or "").strip().lower())

    def raw_dump():
        try:
            device.adb_shell("rm", "-f", "/sdcard/aw_dump.xml")
        except Exception:
            pass
        for _ in range(3):
            try:
                device.adb_shell("uiautomator", "dump", "/sdcard/aw_dump.xml")
                raw = device.adb_shell("cat", "/sdcard/aw_dump.xml")
            except Exception:
                raw = ""
            if raw and "<" in raw:
                i = raw.find("<hierarchy")
                if i < 0:
                    i = raw.find("<")
                return raw[i:]
            device.settle(1)
        return ""

    def dump_elems():
        raw = raw_dump()
        if not raw:
            return []
        try:
            root = ET.fromstring(raw)
        except Exception:
            return []
        out = []

        def walk(n):
            a = n.attrib
            pkg = a.get("package") or ""
            if pkg.startswith("net.osmand") or not pkg:
                m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]",
                             a.get("bounds") or "")
                if m:
                    x1, y1, x2, y2 = (int(g) for g in m.groups())
                    out.append({
                        "index": ("xy", (x1 + x2) // 2, (y1 + y2) // 2),
                        "text": a.get("text") or "",
                        "hint": "",
                        "description": a.get("content-desc") or "",
                        "rid": a.get("resource-id") or "",
                        "clickable": a.get("clickable") == "true",
                        "editable": (a.get("class") or "").endswith("EditText"),
                    })
            for c in list(n):
                walk(c)

        walk(root)
        return out

    def elems():
        return dump_elems() if mode[0] == "dump" else elements()

    def do_click(target):
        if target is None:
            return False
        if isinstance(target, tuple):
            try:
                device.adb_shell("input", "tap",
                                 str(target[1]), str(target[2]))
                device.settle(1)
                return True
            except Exception:
                return False
        try:
            device.click(target)
            return True
        except Exception:
            return False

    def first_index(pred, els=None):
        for e in (elems() if els is None else els):
            if pred(e):
                return e.get("index")
        return None

    # ---------------- status-bar junk & app-UI detection ---------------

    _JUNK_WORDS = ("notification", "battery", "charging", "percent",
                   "wifi signal", "wi-fi signal", "signal full",
                   "phone signal", "serial console", "location requests",
                   "screen recording", "airplane mode", "alarm set",
                   "usb", "vpn")

    def is_status_junk(e):
        t = text_of(e)
        if re.match(r"^\d{1,2}:\d{2}$", t):
            return True
        v = (t + " " + desc_of(e)).strip().lower()
        if not v:
            return False
        return any(w in v for w in _JUNK_WORDS)

    APP_KEYS = ("search", "type to search", "compass", "zoom",
                "my location", "configure map", "map markers", "map marker",
                "plan route", "directions", "favorites", "favourites",
                "categories", "history", "navigate", "layers", "actions",
                "add marker", "marker", "increase search radius",
                "could not find", "share", "download", "recents", "measure")

    def a11y_app_present(els=None):
        if els is None:
            els = elements()
        if not els:
            return False
        for e in els:
            if e.get("editable"):
                return True
            v = (text_of(e) + " " + desc_of(e) + " " + hint_of(e)).lower()
            for k in APP_KEYS:
                if k in v:
                    return True
        return False

    def osmand_in_focus():
        try:
            out = device.adb_shell("dumpsys", "window", "windows") or ""
        except Exception:
            return True
        if not out.strip():
            return True
        focused = [ln.strip() for ln in out.splitlines()
                   if ("mCurrentFocus" in ln or "mFocusedApp" in ln
                       or "mFocusedWindow" in ln)]
        if not focused:
            return True  # cannot tell: do not block the flow
        return any("net.osmand" in ln for ln in focused)

    def a11y_ready():
        return a11y_app_present() and osmand_in_focus()

    def dump_app_present(els=None):
        if els is None:
            els = dump_elems()
        return bool(els)

    def tree_broken(els=None):
        if mode[0] == "dump":
            if els is None:
                els = dump_elems()
            return not dump_app_present(els)
        if els is None:
            els = elements()
        return not (a11y_app_present(els) and osmand_in_focus())

    def surely_broken():
        if not tree_broken():
            return False
        device.settle(2)
        return tree_broken()

    # ---------------- broken-tree recovery ----------------

    def force_stop():
        for pkg in ("net.osmand", "net.osmand.plus"):
            try:
                device.adb_shell("am", "force-stop", pkg)
            except Exception:
                pass
        device.settle(2)

    def dismiss_dialogs():
        labels = ("skip", "close", "dismiss", "got it", "later",
                  "no thanks", "cancel")
        for _ in range(3):
            hit = False
            for e in elems():
                v = text_of(e).lower() or desc_of(e).lower()
                if v in labels:
                    if do_click(e.get("index")):
                        hit = True
                        device.settle(1)
                        break
            if not hit:
                return

    def tap_map_node():
        m = first_index(lambda e: e.get("clickable")
                        and (desc_of(e) + " " + text_of(e)).strip().lower()
                        == "map")
        if m is None:
            return False
        return do_click(m)

    def nudge_recover():
        # a) tap the map view: dismiss shades/overlays, refocus OsmAnd
        try:
            tap_map_node()
            device.settle(2)
        except Exception:
            pass
        if a11y_ready():
            els = elements()
            for e in els:  # close a context menu the tap may have opened
                v = (text_of(e) + " " + desc_of(e)).lower()
                if "directions" in v or "actions" in v:
                    try:
                        device.navigate_back()
                        device.settle(1)
                    except Exception:
                        pass
                    break
            return
        # b) back: close the notification shade / stray overlay
        try:
            device.navigate_back()
            device.settle(2)
        except Exception:
            pass
        if a11y_ready():
            return
        # c) go home and re-foreground OsmAnd
        try:
            device.navigate_home()
            device.settle(1)
            device.open_app("OsmAnd")
            device.settle(4)
            dismiss_dialogs()
        except Exception:
            pass
        if a11y_ready():
            return
        # d) fresh process
        force_stop()
        try:
            device.open_app("OsmAnd")
            device.settle(4)
            dismiss_dialogs()
        except Exception:
            pass

    def restart_app():
        force_stop()
        try:
            device.navigate_home()
            device.settle(1)
        except Exception:
            pass
        try:
            device.open_app("OsmAnd")
            device.settle(4)
            dismiss_dialogs()
        except Exception:
            pass
        for _ in range(3):
            if a11y_ready() or dump_app_present():
                break
            device.settle(2)
        if not (a11y_ready() or dump_app_present()):
            nudge_recover()

    def pick_mode():
        if a11y_fails[0] < 2 and a11y_ready():
            mode[0] = "a11y"
            return True
        if dump_app_present():
            mode[0] = "dump"
            return True
        if a11y_ready():
            mode[0] = "a11y"
            return True
        return False

    # ---------------- search screen ----------------

    def search_field_index():
        if mode[0] == "dump":
            for e in elems():
                if e.get("editable"):
                    return e.get("index")
            return None
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
            if e.get("editable") and not is_status_junk(e):
                b = (hint_of(e) + " " + text_of(e)).lower()
                if "search" in b or "type to" in b:
                    return e.get("index")
                if fallback is None:
                    fallback = e.get("index")
        return fallback

    def clear_button_index():
        if mode[0] == "a11y":
            idx = safe_find(description="Clear", clickable=True)
            if idx is not None:
                return idx
            idx = safe_find(description="Clear")
            if idx is not None:
                return idx
        return first_index(lambda e: desc_of(e).lower() == "clear"
                           or text_of(e).lower() == "clear")

    def increase_radius_index(els=None):
        # case-insensitive: the button may read 'Increase search radius'
        return first_index(
            lambda e: "increase search radius"
            in (text_of(e) + " " + desc_of(e)).lower(), els)

    def search_button_candidates():
        exact_c, exact, sub_c, sub = [], [], [], []
        for e in elems():
            if is_status_junk(e):
                continue
            v = (desc_of(e) + " " + text_of(e) + " " + rid_of(e))
            v = v.strip().lower()
            if "search" not in v or "type to search" in v:
                continue
            if v == "search":
                if e.get("clickable"):
                    exact_c.append(e.get("index"))
                else:
                    exact.append(e.get("index"))
            elif e.get("clickable"):
                sub_c.append(e.get("index"))
            else:
                sub.append(e.get("index"))
        return exact_c + exact + sub_c + sub

    def open_search():
        if search_field_index() is not None:
            return True
        for pass_no in range(2):
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
                if not do_click(pick):
                    continue
                device.settle(2)
                if search_field_index() is not None:
                    return True
                device.settle(2)
                if search_field_index() is not None:
                    return True
            if pass_no == 0:
                # open the navigation drawer; it has a 'Search' entry too
                drawer = first_index(
                    lambda e: e.get("clickable")
                    and (desc_of(e) + " " + text_of(e)).strip().lower()
                    in ("navigate up", "drawer", "open drawer"))
                if drawer is not None:
                    do_click(drawer)
                    device.settle(2)
                    if search_field_index() is not None:
                        return True
        return False

    # ---------------- search-result matching ----------------

    chrome_exact = {
        "search", "clear", "categories", "category", "history",
        "my locations", "favorites", "favourites", "markers",
        "on the map", "navigate up", "type to search all",
        "increase search radius", "could not find anything",
        "search history", "recently visited", "recents", "actions",
        "more actions", "directions", "map markers",
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
        if is_status_junk(e):
            return False
        return norm(b) not in chrome_exact and bl not in chrome_exact

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

    def find_result_index(query, els=None):
        if els is None:
            els = elems()
        q = str(query).strip()
        ql = q.lower()
        qn = norm(q)
        tokens = [t.lower() for t in re.split(r"[\s,;]+", q) if t]

        # 1) coordinate-formatted result rows (both parts in one element)
        for ra, rb in coord_matchers(q):
            for e in els:
                if eligible(e, qn) and ra.search(blob_of(e)) and rb.search(blob_of(e)):
                    return e.get("index")
            # 1b) lat / lon rendered as adjacent separate TextViews
            for i, e in enumerate(els):
                if not eligible(e, qn) or not ra.search(blob_of(e)):
                    continue
                for j in (i + 1, i + 2, i - 1, i - 2):
                    if 0 <= j < len(els):
                        nb = els[j]
                        if eligible(nb, qn) and rb.search(blob_of(nb)):
                            return e.get("index")

        # 2) row containing the full query string
        for e in els:
            if eligible(e, qn) and ql in blob_of(e).lower():
                return e.get("index")

        # 3) row containing every token (title + country in one text)
        if len(tokens) > 1:
            for e in els:
                if eligible(e, qn):
                    b = blob_of(e).lower()
                    if all(tok in b for tok in tokens):
                        return e.get("index")

        # 4) row containing the primary token (country may be a separate
        #    subtitle element)
        if tokens:
            head = tokens[0]
            for e in els:
                if eligible(e, qn) and head in blob_of(e).lower():
                    return e.get("index")
        return None

    # ---------------- typing & radius-limited empty search --------------

    def typed_echo_present(query):
        qn = norm(str(query))
        if not qn:
            return True
        frag = qn.split(" ")[0][:6]
        if len(frag) < 3:
            frag = qn[:6]
        for e in elems():
            b = norm(text_of(e) + " " + hint_of(e) + " " + desc_of(e))
            if frag and frag in b:
                return True
        return False

    def type_query(query, field=None):
        if field is None:
            field = search_field_index()
        if field is None:
            return False
        if mode[0] == "a11y" and not isinstance(field, tuple):
            try:
                device.click(field)
                device.settle(1)
                f2 = search_field_index()
                if f2 is not None and not isinstance(f2, tuple):
                    field = f2
                device.input_text(str(query), index=field)
                device.settle(2)
                return True
            except Exception:
                pass
        # generic path (dump mode / a11y fallback): focus, clear, type
        try:
            do_click(field)
            device.settle(1)
        except Exception:
            pass
        try:
            felem = None
            for e in elems():
                if e.get("index") == field:
                    felem = e
                    break
            if felem is not None and text_of(felem):
                try:
                    device.adb_shell("input", "keyevent", "123")  # MOVE_END
                except Exception:
                    pass
                for _ in range(30):
                    try:
                        device.adb_shell("input", "keyevent", "67")  # DEL
                    except Exception:
                        break
        except Exception:
            pass
        payload = str(query).replace(" ", "%s")
        for attempt in range(3):
            try:
                if attempt == 0:
                    device.adb_shell("input", "text", payload)
                elif attempt == 1:
                    device.input_text(str(query))
                else:
                    device.execute({"action_type": "input_text",
                                    "text": str(query)})
                device.settle(2)
                return True
            except Exception:
                continue
        return False

    def retype_query(query):
        """Clear the search box and type ``query`` again so the search
        re-runs at the currently widened radius."""
        try:
            if search_field_index() is None:
                if not open_search():
                    return False
            c = clear_button_index()
            if c is not None:
                do_click(c)
                device.settle(1)
            return type_query(query)
        except Exception:
            return False

    def wait_for_result(query, rounds=24, max_clicks=12):
        clicks = 0
        enters = 0
        retypes = 0
        for _ in range(rounds):
            els = elems()
            if tree_broken(els):
                device.settle(2)
                if tree_broken():
                    return None
                els = elems()
            idx = find_result_index(query, els)
            if idx is not None:
                return idx
            inc = increase_radius_index(els)
            if (inc is not None and clicks < max_clicks
                    and radius_left[0] > 0):
                do_click(inc)  # widen radius; OsmAnd re-runs the search
                device.settle(2)
                clicks += 1
                radius_left[0] -= 1
                if clicks % 3 == 0:
                    try:
                        device.keyboard_enter()
                        device.settle(2)
                    except Exception:
                        pass
                continue
            if enters < 2:
                try:
                    device.keyboard_enter()
                    device.settle(2)
                except Exception:
                    pass
                enters += 1
                continue
            if retypes < 2:
                retypes += 1
                retype_query(query)
                continue
            try:
                device.scroll("down")
                device.settle(1)
            except Exception:
                pass
        return find_result_index(query)

    # ---------------- place-panel MARKER control ----------------

    def find_marker_button(els=None):
        if els is None:
            els = elems()
        strict = ("marker", "add marker", "add_marker", "add map marker",
                  "map marker", "add to markers", "add to map markers")
        for e in els:
            for v in (text_of(e), desc_of(e)):
                if v.lower().strip() in strict:
                    return e.get("index")
        for e in els:
            for v in (text_of(e), desc_of(e)):
                vl = v.lower().strip()
                if not vl or "markers" in vl or "favorite" in vl \
                        or "favourite" in vl or "share" in vl:
                    continue
                if (vl.startswith("add marker") or vl.startswith("marker ")
                        or vl.endswith(" marker")):
                    return e.get("index")
        for e in els:
            v = (text_of(e) + " " + desc_of(e)).lower()
            if ("marker" in v and "markers" not in v
                    and "map marker" not in v and "favorite" not in v
                    and "favourite" not in v and "share" not in v):
                return e.get("index")
        return None

    def actions_button_index(els=None):
        if els is None:
            els = elems()
        for e in els:
            v = (text_of(e) + " " + desc_of(e)).strip().lower()
            if v in ("actions", "more actions", "more"):
                return e.get("index")
        for e in els:
            v = (text_of(e) + " " + desc_of(e)).strip().lower()
            if "action" in v and e.get("clickable"):
                return e.get("index")
        return None

    def try_open_panel_and_get_marker(query, first_idx):
        idx = first_idx
        for _ in range(4):
            if idx is None:
                idx = find_result_index(query)
            if idx is None:
                return None
            if not do_click(idx):
                idx = None
                continue
            device.settle(2)
            idx = None
            els = elems()
            m = find_marker_button(els)
            if m is not None:
                return m
            # the first tap may only have dismissed the keyboard
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(1)
            els = elems()
            m = find_marker_button(els)
            if m is not None:
                return m
            # the panel may be collapsed or its action row scrolled aside
            for d in ("up", "left", "right"):
                try:
                    device.scroll(d)
                    device.settle(1)
                except Exception:
                    pass
                els = elems()
                m = find_marker_button(els)
                if m is not None:
                    return m
            act = actions_button_index(els)
            if act is not None:
                do_click(act)
                device.settle(1)
                els = elems()
                m = find_marker_button(els)
                if m is not None:
                    return m
            device.settle(1)
            if surely_broken():
                return None
        return None

    # ---------------- one full search-and-mark attempt ----------------

    def run_flow():
        if surely_broken():
            raise _DeadEnd("app UI not visible before search")
        if search_field_index() is None:
            if not open_search():
                raise _DeadEnd("OsmAnd search screen did not open")
        # clear any stale query
        c = clear_button_index()
        if c is not None:
            do_click(c)
            device.settle(1)
        field = search_field_index()
        if field is None:
            if not open_search():
                raise _DeadEnd("OsmAnd search input field not found")
            field = search_field_index()
        if field is None:
            raise _DeadEnd("OsmAnd search input field not found")
        if not type_query(location, field):
            raise _DeadEnd("could not type into OsmAnd search field")
        if not typed_echo_present(location):
            # the text did not land in the (right) field: clear and retype
            c = clear_button_index()
            if c is not None:
                do_click(c)
                device.settle(1)
            type_query(location)
            typed_echo_present(location)  # best effort; flow continues

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

        do_click(marker_idx)  # marker is stored on tap; no confirmation
        device.settle(2)

    # ---------- 1) launch OsmAnd ----------

    device.open_app("OsmAnd")
    device.settle(3)
    dismiss_dialogs()
    for _ in range(4):
        if a11y_ready():
            break
        device.settle(2)
    relaunches = 0
    while not a11y_ready() and relaunches < 3:
        relaunches += 1
        nudge_recover()
        dismiss_dialogs()
        for _ in range(2):
            if a11y_ready():
                break
            device.settle(2)

    # ---------- 2) search the place and tap MARKER, with recovery ----

    last_err = None
    for attempt in range(5):
        if not pick_mode():
            if attempt >= 2:
                break
            nudge_recover()
            dismiss_dialogs()
            continue
        try:
            run_flow()
            return True
        except _DeadEnd as e:
            last_err = str(e)
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
        if mode[0] == "a11y":
            a11y_fails[0] += 1
        restart_app()
    raise RuntimeError("OsmAnd marker flow failed after restarts: %s"
                       % (last_err or "OsmAnd UI never became usable"))
