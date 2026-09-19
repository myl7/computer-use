import re
import xml.etree.ElementTree as ET

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to save as a favorite in OsmAnd: one string, either a place name "
            "such as 'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. It is "
            "typed verbatim into the OsmAnd search field (never reformatted/rounded)."
        ),
    }
}

# Text fragments that identify search-screen status rows rather than real results.
_RESULT_BLACKLIST = (
    "could not find",
    "nothing found",
    "no match",
    "no result",
    "search radius",
    "increase search",
    "search another",
)

# Words that mark chrome/tab rows rather than a tappable search hit.
_NONROW_WORDS = (
    "history",
    "categor",
    "address",
    "my favorites",
    "favorites",
    "custom search",
    "search on the map",
    "poi",
    "transport",
    "weather",
    "download maps",
    "configure map",
    "web",
)

_CONFIRM_LABELS = ("save", "ok", "replace", "overwrite", "apply", "yes", "add", "done")

_DISMISS_LABELS = ("close", "dismiss", "later", "skip", "not now", "no thanks", "got it")

_DUMP_PATH = "/sdcard/osmand_uidump.xml"


def program(device, binding: dict) -> bool:
    """Add a favorite location marker for binding['location'] in the OsmAnd app."""
    if not isinstance(binding, dict):
        raise RuntimeError("binding must be a dict containing a 'location' key")
    location = binding.get("location")
    if location is None:
        raise RuntimeError("binding['location'] is required")
    location = str(location).strip()
    if not location:
        raise RuntimeError("binding['location'] must be a non-empty string")
    probe_query = location.strip().lower()[:6]

    # ---------------------------------------------------------------- #
    # generic device helpers                                           #
    # ---------------------------------------------------------------- #
    def els():
        try:
            return device.elements() or []
        except Exception:
            return []

    def txt(el, key):
        if not isinstance(el, dict):
            return ""
        v = el.get(key)
        return v if isinstance(v, str) else ""

    def scan(pred):
        for el in els():
            try:
                if pred(el):
                    return el.get("index")
            except Exception:
                continue
        return None

    def try_find(criteria_list):
        for criteria in criteria_list:
            try:
                idx = device.find(**criteria)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def sh(*args):
        try:
            return device.adb_shell(*args)
        except Exception:
            try:
                return device.adb_shell(" ".join(str(a) for a in args))
            except Exception:
                return None

    # ---------------------------------------------------------------- #
    # raw ui-dump fallback (only when the a11y element list is unusable)#
    # ---------------------------------------------------------------- #
    def dump_nodes():
        try:
            sh("uiautomator", "dump", _DUMP_PATH)
            raw = sh("cat", _DUMP_PATH)
            if not raw or "<" not in str(raw):
                return []
            root = ET.fromstring(str(raw))
        except Exception:
            return []
        nodes = []

        def walk(node):
            attrib = node.attrib
            m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                             attrib.get("bounds", "") or "")
            cls = attrib.get("class", "") or ""
            nodes.append({
                "text": attrib.get("text", "") or "",
                "desc": attrib.get("content-desc", "") or "",
                "cls": cls,
                "clickable": attrib.get("clickable") == "true",
                "editable": attrib.get("editable") == "true"
                or "edittext" in cls.lower(),
                "bounds": tuple(int(g) for g in m.groups()) if m else None,
            })
            for child in node:
                walk(child)

        walk(root)
        return nodes

    def adb_tap(x, y):
        sh("input", "tap", str(int(x)), str(int(y)))

    def adb_tap_node(node):
        b = node.get("bounds")
        if not b:
            return False
        adb_tap((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
        return True

    def adb_type(text):
        escaped = text.replace(" ", "%s").replace("'", "")
        sh("input", "text", escaped)

    def adb_focus_and_type(text):
        try:
            for node in dump_nodes():
                if node.get("editable"):
                    if not adb_tap_node(node):
                        return False
                    device.settle(1)
                    adb_type(text)
                    device.settle(1)
                    return True
        except Exception:
            pass
        return False

    # ---------------------------------------------------------------- #
    # search needles derived from the binding value                    #
    # ---------------------------------------------------------------- #
    def coord_pair():
        parts = [p.strip() for p in location.split(",") if p.strip()]
        if len(parts) != 2:
            return None
        vals = []
        for p in parts:
            if not re.fullmatch(r"-?\d+(?:\.\d+)?\s*\u00b0?\s*[NSEWnsew]?", p):
                return None
            vals.append(float(re.search(r"-?\d+(?:\.\d+)?", p).group(0)))
        return vals[0], vals[1]

    def build_needles():
        out = [location]
        cp = coord_pair()
        if cp is not None:
            lat, lon = cp
            out.append(location.replace(" ", ""))
            for v in (lat, lon):
                for nd in (6, 5, 4, 3, 2):
                    sv = ("%." + str(nd) + "f") % v
                    out.append(sv + "\u00b0")
                    out.append(sv)
                    out.append(sv.rstrip("0").rstrip("."))
        else:
            parts = [p.strip() for p in location.split(",") if p.strip()]
            out.extend(parts)
        seen, uniq = set(), []
        for n in out:
            k = n.strip().lower()
            if k and k not in seen:
                seen.add(k)
                uniq.append(n)
        return uniq

    needles_list = build_needles()

    # ---------------------------------------------------------------- #
    # launch OsmAnd                                                    #
    # ---------------------------------------------------------------- #
    def map_ui_visible():
        if try_find([
                {"description": "Configure map"},
                {"description": "Search", "clickable": True},
                {"description": "Zoom in"},
                {"description": "Map", "clickable": True}]) is not None:
            return True
        if scan(lambda el: "configure map" in
                (txt(el, "description") + " " + txt(el, "text")).lower()) is not None:
            return True
        if scan(lambda el: txt(el, "description").strip().lower() == "map") is not None:
            return True
        if try_find([{"hint": "Type to search all", "editable": True}]) is not None:
            return True
        return False

    def dismiss_overlays():
        for el in els():
            try:
                if not el.get("clickable"):
                    continue
                blob = (txt(el, "text") + " " + txt(el, "description")).strip().lower()
                if blob in _DISMISS_LABELS:
                    device.click(el.get("index"))
                    device.settle(1)
                    return True
            except Exception:
                continue
        return False

    def launch_osmand():
        for _attempt in range(3):
            try:
                device.open_app("OsmAnd")
            except Exception:
                pass
            device.settle(2)
            for _ in range(3):
                if map_ui_visible():
                    dismiss_overlays()
                    return
                dismiss_overlays()
                device.settle(2)
            try:
                device.navigate_home()
            except Exception:
                pass
            device.settle(1)
        raise RuntimeError("Failed to launch OsmAnd: its map screen was not detected")

    # ---------------------------------------------------------------- #
    # search screen                                                    #
    # ---------------------------------------------------------------- #
    def search_field():
        idx = try_find([
            {"hint": "Type to search all", "editable": True},
            {"hint": "Type to search all", "contains": True, "editable": True},
            {"hint": "search", "contains": True, "editable": True}])
        if idx is not None:
            return idx
        idx = scan(lambda el: bool(el.get("editable")) and "search" in
                   (txt(el, "hint") + " " + txt(el, "description")).lower())
        if idx is not None:
            return idx
        return scan(lambda el: bool(el.get("editable")))

    def open_drawer_and_search(allow_swipe):
        opened = False
        idx = try_find([
            {"description": "Open menu", "clickable": True},
            {"description": "Open navigation drawer", "clickable": True},
            {"description": "Show menu", "clickable": True}])
        if idx is None:
            idx = scan(lambda el: bool(el.get("clickable")) and
                       ("menu" in txt(el, "description").lower()
                        or "drawer" in txt(el, "description").lower()))
        if idx is not None:
            try:
                device.click(idx)
                device.settle(2)
                opened = True
            except Exception:
                opened = False
        if not opened and allow_swipe:
            try:
                device.scroll(direction="right")
                device.settle(1)
                opened = True
            except Exception:
                return False
        if not opened:
            return False
        for _ in range(3):
            s = try_find([{"text": "Search", "clickable": True}, {"text": "Search"}])
            if s is None:
                s = scan(lambda el: txt(el, "text").strip().lower() == "search")
            if s is not None:
                try:
                    device.click(s)
                    device.settle(2)
                    return True
                except Exception:
                    return False
            device.settle(1)
        return False

    def adb_open_search():
        try:
            nodes = dump_nodes()
            for node in nodes:
                d = node["desc"].strip().lower()
                if d == "search" and node["clickable"]:
                    if adb_tap_node(node):
                        device.settle(2)
                        return True
            for node in nodes:
                d = node["desc"].strip().lower()
                if "search" in d and "radius" not in d and node["clickable"]:
                    if adb_tap_node(node):
                        device.settle(2)
                        return True
        except Exception:
            pass
        return False

    def open_search_screen(max_cycles=4):
        for cycle in range(max_cycles):
            if search_field() is not None:
                return
            idx = try_find([
                {"description": "Search", "clickable": True},
                {"description": "Search"},
                {"description": "search", "contains": True, "clickable": True}])
            if idx is None:
                idx = scan(lambda el: txt(el, "description").strip().lower() == "search")
            if idx is not None:
                try:
                    device.click(idx)
                except Exception:
                    pass
                device.settle(1.5)
                if search_field() is not None:
                    return
            if open_drawer_and_search(cycle >= 1) and search_field() is not None:
                return
            if cycle >= 1 and adb_open_search() and search_field() is not None:
                return
            dismiss_overlays()
            if cycle >= 1:
                try:
                    device.navigate_back()
                except Exception:
                    pass
                device.settle(1)
                try:
                    device.open_app("OsmAnd")
                except Exception:
                    pass
                device.settle(2)
        if search_field() is None:
            raise RuntimeError(
                "OsmAnd search screen did not open for location %r" % location)

    def clear_query_if_present():
        if not any(el.get("editable") and txt(el, "text").strip() for el in els()):
            return
        clear = scan(lambda el: bool(el.get("clickable")) and
                     txt(el, "description").strip().lower() in (
                         "clear", "clear query", "clear search", "clear text"))
        if clear is None:
            clear = scan(lambda el: bool(el.get("clickable")) and
                         "clear" in txt(el, "description").lower() and
                         "all" not in txt(el, "description").lower())
        if clear is not None:
            try:
                device.click(clear)
                device.settle(1)
            except Exception:
                pass

    def query_landed(probe):
        if not probe:
            return True
        for el in els():
            if el.get("editable") and probe in txt(el, "text").lower():
                return True
        return False

    def type_query(text):
        probe = text.strip().lower()[:6]
        field = None
        for _ in range(4):
            field = search_field()
            if field is not None:
                break
            device.settle(2)
        if field is None:
            open_search_screen()
            field = search_field()
        if field is None:
            raise RuntimeError(
                "OsmAnd search input field not found on the search screen")
        landed = query_landed(probe)
        for attempt in range(3):
            if landed:
                break
            clear_query_if_present()
            field = search_field()
            if field is not None:
                if attempt > 0:
                    try:
                        device.click(field)
                    except Exception:
                        pass
                try:
                    device.input_text(text, index=field)
                except Exception:
                    pass
                device.settle(1)
                landed = query_landed(probe)
                if landed:
                    break
            adb_focus_and_type(text)
            landed = query_landed(probe)
        if not query_landed(probe):
            raise RuntimeError(
                "Could not enter %r into the OsmAnd search field" % text)
        device.keyboard_enter()
        device.settle(2)

    # ---------------------------------------------------------------- #
    # search results                                                   #
    # ---------------------------------------------------------------- #
    def find_result(needle_list):
        elements = els()
        for needle in needle_list:
            nl = needle.lower()
            if not nl:
                continue
            for el in elements:
                if el.get("editable") or "type to search" in txt(el, "hint").lower():
                    continue
                hay = (txt(el, "text") + "\n" + txt(el, "description")).lower()
                if not hay.strip():
                    continue
                if any(b in hay for b in _RESULT_BLACKLIST):
                    continue
                if nl in hay:
                    return el.get("index")
        return None

    def first_plausible_row():
        for el in els():
            if el.get("editable"):
                continue
            if "type to search" in txt(el, "hint").lower():
                continue
            hay = (txt(el, "text") + "\n" + txt(el, "description")).lower().strip()
            if len(hay) < 3:
                continue
            if any(b in hay for b in _RESULT_BLACKLIST):
                continue
            if any(w in hay for w in _NONROW_WORDS):
                continue
            return el.get("index")
        return None

    def retype(text):
        if search_field() is None:
            return False
        clear_query_if_present()
        field = search_field()
        if field is None:
            return False
        try:
            device.click(field)
        except Exception:
            pass
        try:
            device.input_text(text, index=field)
        except Exception:
            pass
        device.settle(1)
        if not query_landed(text.strip().lower()[:6]):
            adb_focus_and_type(text)
        device.keyboard_enter()
        device.settle(2)
        return True

    def await_result():
        active = list(needles_list)
        radius_clicks = 0
        enter_presses = 0
        short_done = False
        reopen_count = 0
        for poll in range(24):
            idx = find_result(active)
            if idx is not None:
                return idx, active
            rad = scan(lambda el: "search radius"
                       in (txt(el, "text") + " " + txt(el, "description")).lower())
            if rad is not None and radius_clicks < 8:
                device.click(rad)
                radius_clicks += 1
                device.settle(2)
                continue
            if not short_done and (radius_clicks >= 3 or poll >= 8) and coord_pair() is None:
                short_done = True
                parts = [p.strip() for p in location.split(",") if p.strip()]
                short = parts[0] if parts else ""
                if short and short.lower() != location.lower() and retype(short):
                    active.append(short)
                    continue
            if search_field() is None and reopen_count < 3:
                reopen_count += 1
                try:
                    device.navigate_back()
                except Exception:
                    pass
                device.settle(1)
                open_search_screen()
                if search_field() is not None:
                    if not query_landed(probe_query):
                        type_query(location)
                    else:
                        device.keyboard_enter()
                        device.settle(2)
                continue
            if enter_presses < 3:
                device.keyboard_enter()
                enter_presses += 1
                device.settle(2)
                continue
            device.settle(2)
        idx = find_result(active) or first_plausible_row()
        if idx is not None:
            return idx, active
        raise RuntimeError(
            "OsmAnd search returned no results for location %r" % location)

    # ---------------------------------------------------------------- #
    # place panel -> favorites (star) -> save dialog                   #
    # ---------------------------------------------------------------- #
    def find_favorite_control():
        idx = try_find([
            {"description": "Add favorite", "clickable": True},
            {"description": "Add favorite"},
            {"description": "Add to favorites", "clickable": True},
            {"description": "Add to favorite", "clickable": True},
            {"description": "Favorite", "clickable": True},
            {"description": "Favorites", "clickable": True}])
        if idx is not None:
            return idx

        def fav_hit(el):
            d = txt(el, "description").lower()
            t = txt(el, "text").lower()
            if "marker" in d or "marker" in t:
                return False
            return "favorit" in d or "favorit" in t

        clickable = scan(lambda el: fav_hit(el) and bool(el.get("clickable")))
        if clickable is not None:
            return clickable
        return scan(fav_hit)

    def adb_click_favorite():
        try:
            for node in dump_nodes():
                d = node["desc"].lower()
                if "marker" in d:
                    continue
                if "favorit" in d and node["clickable"]:
                    return adb_tap_node(node)
        except Exception:
            pass
        return False

    def dialog_open():
        return scan(lambda el: bool(el.get("editable"))) is not None

    def confirm_target():
        elements = els()
        fallback = None
        for lab in _CONFIRM_LABELS:
            for el in elements:
                t = txt(el, "text").strip().lower()
                d = txt(el, "description").strip().lower()
                if t == lab or d == lab:
                    if bool(el.get("clickable")):
                        return el.get("index")
                    if fallback is None:
                        fallback = el.get("index")
        return fallback

    def confirm_favorite_dialog():
        confirm_clicks = 0
        star_reclicks = 0
        for attempt in range(16):
            target = confirm_target()
            if target is not None and (confirm_clicks == 0 or dialog_open()
                                       or confirm_clicks < 3):
                device.click(target)
                confirm_clicks += 1
                device.settle(2)
                continue
            if confirm_clicks > 0:
                return
            if star_reclicks < 3 and attempt >= 3 and not dialog_open():
                fav = find_favorite_control()
                if fav is not None:
                    device.click(fav)
                    star_reclicks += 1
                    device.settle(2)
                    continue
                if adb_click_favorite():
                    star_reclicks += 1
                    device.settle(2)
                    continue
            device.settle(2)
        if confirm_clicks == 0:
            raise RuntimeError(
                "Could not confirm the 'Add favorite' dialog for location %r" % location)

    def open_panel_and_save(active_needles):
        fav = None
        reclicks = 0
        actions_clicks = 0
        for _attempt in range(12):
            fav = find_favorite_control()
            if fav is not None:
                break
            acted = False
            if actions_clicks < 2:
                act = scan(lambda el: txt(el, "text").strip().lower() == "actions")
                if act is not None:
                    device.click(act)
                    actions_clicks += 1
                    device.settle(2)
                    acted = True
            if not acted and reclicks < 4:
                ridx = find_result(active_needles)
                if ridx is not None:
                    device.click(ridx)
                    reclicks += 1
                    device.settle(2)
                    acted = True
            if not acted:
                device.settle(2)
        clicked_fav = False
        if fav is not None:
            try:
                device.click(fav)
                clicked_fav = True
                device.settle(2)
            except Exception:
                clicked_fav = False
        if not clicked_fav and adb_click_favorite():
            clicked_fav = True
            device.settle(2)
        if not clicked_fav:
            raise RuntimeError(
                "Favorites (star) control not found on the OsmAnd place panel for %r"
                % location)
        confirm_favorite_dialog()

    # ---------------------------------------------------------------- #
    # main flow                                                        #
    # ---------------------------------------------------------------- #
    launch_osmand()
    open_search_screen()
    type_query(location)
    result_idx, active_needles = await_result()
    device.click(result_idx)
    device.settle(2)
    open_panel_and_save(active_needles)
    return True
