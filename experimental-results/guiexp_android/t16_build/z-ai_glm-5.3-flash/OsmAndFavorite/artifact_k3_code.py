import re

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
)

_CONFIRM_LABELS = ("save", "ok", "replace", "overwrite", "apply", "yes", "add")


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
        out = []
        cp = coord_pair()
        if cp is not None:
            lat, lon = cp
            for nd in (5, 4, 6, 3):
                out.append(("%." + str(nd) + "f\u00b0") % lat)
                out.append(("%." + str(nd) + "f") % lat)
            out.append(location)
            for nd in (5, 4, 6, 3):
                out.append(("%." + str(nd) + "f\u00b0") % lon)
                out.append(("%." + str(nd) + "f") % lon)
        else:
            out.append(location)
            parts = [p.strip() for p in location.split(",") if p.strip()]
            if parts and parts[0].lower() != location.lower():
                out.append(parts[0])
        seen, uniq = set(), []
        for n in out:
            k = n.lower()
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
                {"description": "Zoom in"}]) is not None:
            return True
        if scan(lambda el: "configure map" in txt(el, "description").lower()) is not None:
            return True
        if try_find([{"hint": "Type to search all", "editable": True}]) is not None:
            return True
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
                    return
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
        return scan(lambda el: bool(el.get("editable")))

    def open_search_screen():
        for _attempt in range(3):
            if try_find([
                    {"hint": "Type to search all", "editable": True},
                    {"hint": "Type to search all", "contains": True,
                     "editable": True}]) is not None:
                return
            idx = try_find([
                {"description": "Search", "clickable": True},
                {"description": "Search"}])
            if idx is None:
                idx = scan(lambda el: txt(el, "description").strip().lower() == "search")
            if idx is None:
                device.settle(2)
                continue
            device.click(idx)
            device.settle(1.5)
        if try_find([{"hint": "Type to search all", "editable": True}]) is None:
            raise RuntimeError("OsmAnd search screen did not open for location %r" % location)

    def clear_query_if_present():
        if not any(el.get("editable") and txt(el, "text").strip() for el in els()):
            return
        clear = scan(lambda el: txt(el, "description").strip().lower() in (
            "clear", "clear query", "clear search", "clear text"))
        if clear is None:
            clear = scan(lambda el: "clear" in txt(el, "description").lower()
                         and "all" not in txt(el, "description").lower())
        if clear is not None:
            try:
                device.click(clear)
                device.settle(1)
            except Exception:
                pass

    def type_query(text):
        probe = text.strip()[:6].lower()
        field = None
        for _ in range(4):
            field = search_field()
            if field is not None:
                break
            device.settle(2)
        if field is None:
            raise RuntimeError("OsmAnd search input field not found on the search screen")
        for attempt in range(3):
            clear_query_if_present()
            field = search_field()
            if field is None:
                raise RuntimeError("OsmAnd search field lost while typing the query")
            if attempt > 0:
                try:
                    device.click(field)
                except Exception:
                    pass
            device.input_text(text, index=field)
            device.settle(1)
            if not probe or scan(lambda el: probe in txt(el, "text").lower()) is not None:
                break
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

    def retype(text):
        if search_field() is None:
            return False
        clear_query_if_present()
        field = search_field()
        if field is None:
            return False
        device.input_text(text, index=field)
        device.settle(1)
        device.keyboard_enter()
        device.settle(2)
        return True

    def await_result():
        active = list(needles_list)
        radius_clicks = 0
        enter_presses = 0
        short_done = False
        for poll in range(20):
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
            if enter_presses < 3:
                device.keyboard_enter()
                enter_presses += 1
                device.settle(2)
                continue
            device.settle(2)
        raise RuntimeError("OsmAnd search returned no results for location %r" % location)

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
            device.settle(2)
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
        if fav is None:
            raise RuntimeError(
                "Favorites (star) control not found on the OsmAnd place panel for %r"
                % location)
        device.click(fav)
        device.settle(2)
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
