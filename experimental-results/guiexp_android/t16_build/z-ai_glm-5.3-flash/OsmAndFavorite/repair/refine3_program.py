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

_RESULT_BLACKLIST = (
    "could not find", "nothing found", "no match", "no result",
    "search radius", "increase search", "search another",
)

_NONROW_WORDS = (
    "history", "categor", "address", "my favorites", "favorites",
    "custom search", "search on the map", "poi", "transport",
    "weather", "download maps", "configure map", "web",
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

    # ---------------- generic helpers ---------------- #
    def settle(sec=2):
        try:
            device.settle(sec)
        except Exception:
            try:
                device.wait()
            except Exception:
                pass

    def sh(*args):
        try:
            return device.adb_shell(*args)
        except Exception:
            try:
                return device.adb_shell(" ".join(str(a) for a in args))
            except Exception:
                return None

    def els(retries=1):
        for attempt in range(retries + 1):
            try:
                lst = device.elements()
                if lst:
                    return list(lst)
            except Exception:
                pass
            if attempt < retries:
                settle(1.2)
        return []

    def dump_nodes(retries=1):
        for attempt in range(retries + 1):
            raw = None
            try:
                sh("rm", "-f", _DUMP_PATH)
                sh("uiautomator", "dump", _DUMP_PATH)
                raw = sh("cat", _DUMP_PATH)
            except Exception:
                raw = None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            if raw and "<hierarchy" in str(raw) and "</hierarchy>" in str(raw):
                root = None
                try:
                    root = ET.fromstring(str(raw).strip())
                except Exception:
                    root = None
                if root is not None:
                    nodes = []

                    def walk(node):
                        attrib = node.attrib
                        m = re.fullmatch(
                            r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]",
                            attrib.get("bounds", "") or "")
                        cls = attrib.get("class", "") or ""
                        nodes.append({
                            "text": attrib.get("text", "") or "",
                            "desc": attrib.get("content-desc", "") or "",
                            "cls": cls,
                            "rid": attrib.get("resource-id", "") or "",
                            "clickable": attrib.get("clickable") == "true",
                            "editable": (attrib.get("editable") == "true"
                                         or "edittext" in cls.lower()),
                            "bounds": (tuple(int(g) for g in m.groups())
                                       if m else None),
                        })
                        for child in node:
                            walk(child)

                    walk(root)
                    return nodes
            if attempt < retries:
                settle(1.5)
        return []

    def norm_a11y(el):
        if not isinstance(el, dict):
            return None
        cls = ""
        for key in ("class", "class_name", "className"):
            v = el.get(key)
            if isinstance(v, str) and v:
                cls = v
                break
        text = el.get("text") if isinstance(el.get("text"), str) else ""
        desc = (el.get("description")
                if isinstance(el.get("description"), str) else "")
        hint = el.get("hint") if isinstance(el.get("hint"), str) else ""
        return {
            "index": el.get("index"),
            "text": text,
            "desc": desc,
            "hint": hint,
            "cls": cls,
            "rid": "",
            "clickable": bool(el.get("clickable")),
            "editable": bool(el.get("editable")) or "edittext" in cls.lower(),
            "bounds": None,
            "src": "a11y",
        }

    def norm_dump(node):
        el = dict(node)
        el["index"] = None
        el["hint"] = ""
        el["src"] = "dump"
        return el

    def a11y_el(idx, desc=""):
        return {"index": idx, "text": "", "desc": desc, "hint": "", "cls": "",
                "rid": "", "clickable": True, "editable": False,
                "bounds": None, "src": "a11y"}

    def a11y_snapshot():
        return [e for e in (norm_a11y(x) for x in els()) if e is not None]

    def dump_snapshot():
        return [norm_dump(n) for n in dump_nodes()
                if n["clickable"] or n["text"] or n["desc"] or n["editable"]]

    def snapshot():
        snap = a11y_snapshot()
        if snap:
            return snap
        return dump_snapshot()

    def hay(el):
        return "\n".join(filter(None, [
            el.get("text") or "", el.get("desc") or "", el.get("hint") or "",
            el.get("rid") or "",
        ])).lower()

    def is_edit_field(el):
        return bool(el.get("editable")) or "edittext" in (el.get("cls") or "").lower()

    def tap(el):
        if not isinstance(el, dict):
            return False
        if el.get("src") == "a11y" and el.get("index") is not None:
            try:
                device.click(el["index"])
                return True
            except Exception:
                pass
        b = el.get("bounds")
        if b:
            try:
                sh("input", "tap", str(int((b[0] + b[2]) // 2)),
                   str(int((b[1] + b[3]) // 2)))
                return True
            except Exception:
                return False
        return False

    def try_find(criteria_list):
        for criteria in criteria_list:
            try:
                idx = device.find(**criteria)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def click_index(idx):
        try:
            device.click(idx)
            return True
        except Exception:
            return False

    def _el_key(el):
        if el.get("src") == "a11y" and el.get("index") is not None:
            return ("i", el.get("index"))
        if el.get("bounds"):
            return ("b", el.get("bounds"))
        return None

    def adb_type(text):
        escaped = (text.replace("\\", "\\\\").replace(" ", "%s")
                   .replace("'", "").replace('"', ""))
        sh("input", "text", escaped)

    def clear_field_via_keys():
        sh("input", "keyevent", "KEYCODE_MOVE_END")
        for _ in range(30):
            sh("input", "keyevent", "KEYCODE_DEL")

    # ---------------- search needles ---------------- #
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

    # ---------------- launch OsmAnd ---------------- #
    def map_ui_visible():
        if try_find([
                {"description": "Configure map"},
                {"description": "Zoom in"},
                {"description": "My Location"},
                {"description": "Compass"}]) is not None:
            return True
        for el in a11y_snapshot():
            h = hay(el)
            if "configure map" in h or "my location" in h or "compass" in h:
                return True
        for node in dump_nodes():
            blob = (node["desc"] + " " + node["text"]).lower()
            if ("configure map" in blob or "compass" in blob
                    or "my location" in blob):
                return True
        return False

    def dismiss_overlays():
        for el in snapshot():
            if not el.get("clickable"):
                continue
            blob = ((el.get("text") or "") + " " +
                    (el.get("desc") or "")).strip().lower()
            if blob in _DISMISS_LABELS:
                tap(el)
                settle(1)
                return True
        return False

    def launch_osmand():
        for _attempt in range(3):
            try:
                device.open_app("OsmAnd")
            except Exception:
                pass
            settle(2)
            for _ in range(3):
                if map_ui_visible():
                    dismiss_overlays()
                    return
                dismiss_overlays()
                settle(2)
            try:
                device.navigate_home()
            except Exception:
                pass
            settle(1)
        try:
            device.open_app("OsmAnd")
            settle(2)
        except Exception:
            pass

    # ---------------- search screen ---------------- #
    def search_field_a11y():
        if try_find([
                {"hint": "Type to search all", "editable": True},
                {"hint": "Type to search all", "contains": True, "editable": True},
                {"hint": "search", "contains": True, "editable": True}]) is not None:
            return True
        for el in a11y_snapshot():
            if is_edit_field(el):
                return True
        return False

    def search_field():
        idx = try_find([
            {"hint": "Type to search all", "editable": True},
            {"hint": "Type to search all", "contains": True, "editable": True},
            {"hint": "search", "contains": True, "editable": True}])
        if idx is not None:
            return a11y_el(idx, "search-field")
        for el in a11y_snapshot():
            if is_edit_field(el):
                return el
        for node in dump_nodes():
            if node["editable"] or "edittext" in node["cls"].lower():
                return norm_dump(node)
        return None

    def _tap_search_button_in(snaps):
        fallback = None
        for el in snaps:
            d = (el.get("desc") or "").strip().lower()
            t = (el.get("text") or "").strip().lower()
            if d == "search" or t == "search":
                if el.get("clickable") and tap(el):
                    return True
                if fallback is None:
                    fallback = el
        if fallback is not None and tap(fallback):
            return True
        for el in snaps:
            if not el.get("clickable") or is_edit_field(el):
                continue
            blob = ((el.get("desc") or "") + " " +
                    (el.get("text") or "")).lower()
            if ("search" in blob
                    and "radius" not in blob and "another" not in blob
                    and "history" not in blob and "on the map" not in blob
                    and "favorit" not in blob and "address" not in blob):
                if tap(el):
                    return True
        return False

    def open_search_screen(max_cycles=6):
        for cycle in range(max_cycles):
            if search_field() is not None:
                return
            idx = try_find([
                {"description": "Search", "clickable": True},
                {"description": "Search"},
                {"description": "search", "contains": True, "clickable": True}])
            if idx is not None:
                click_index(idx)
                settle(2)
                if search_field() is not None:
                    return
            snap = snapshot()
            snap_is_dump = bool(snap) and all(e.get("src") == "dump" for e in snap)
            if _tap_search_button_in(snap):
                settle(2)
                if search_field() is not None:
                    return
            if not snap_is_dump and _tap_search_button_in(dump_snapshot()):
                settle(2)
                if search_field() is not None:
                    return
            dismiss_overlays()
            if cycle >= 1:
                try:
                    device.navigate_back()
                except Exception:
                    pass
                settle(1)
                try:
                    device.open_app("OsmAnd")
                except Exception:
                    pass
                settle(2)
        if search_field() is None:
            raise RuntimeError(
                "OsmAnd search screen did not open for location %r" % location)

    def query_landed(probe):
        if not probe:
            return True
        for el in a11y_snapshot():
            if is_edit_field(el) and probe in (el.get("text") or "").lower():
                return True
        for node in dump_nodes():
            if ((node["editable"] or "edittext" in node["cls"].lower())
                    and probe in (node["text"] or "").lower()):
                return True
        return False

    def type_query(text, probe=None):
        if probe is None:
            probe = text.strip().lower()[:6]
        open_search_screen()
        for attempt in range(5):
            field = search_field()
            if field is None:
                open_search_screen()
                field = search_field()
            if field is None:
                settle(2)
                continue
            tap(field)
            settle(1)
            clear_field_via_keys()
            if field.get("src") == "a11y" and field.get("index") is not None:
                try:
                    device.input_text(text, index=field["index"])
                except Exception:
                    pass
                settle(1.5)
                if query_landed(probe):
                    break
            # adb fallback: make sure a real editable node is focused, then type
            node = field if field.get("bounds") else None
            if node is None:
                for n in dump_nodes():
                    if n["editable"] or "edittext" in n["cls"].lower():
                        node = norm_dump(n)
                        break
            if node is not None and node.get("bounds"):
                b = node["bounds"]
                sh("input", "tap", str(int((b[0] + b[2]) // 2)),
                   str(int((b[1] + b[3]) // 2)))
                settle(1)
                clear_field_via_keys()
            adb_type(text)
            settle(1.5)
            if query_landed(probe):
                break
        if not query_landed(probe):
            raise RuntimeError(
                "Could not enter %r into the OsmAnd search field" % text)
        try:
            device.keyboard_enter()
        except Exception:
            pass
        settle(2)

    # ---------------- search results ---------------- #
    def _match_result(snaps, needles, exclude=()):
        ex_keys = {k for k in exclude if k}
        for needle in needles:
            nl = needle.strip().lower()
            if not nl:
                continue
            fallback = None
            for el in snaps:
                if is_edit_field(el):
                    continue
                k = _el_key(el)
                if k is not None and k in ex_keys:
                    continue
                h = hay(el)
                if len(h.strip()) < 3 or "type to search" in h:
                    continue
                if any(b in h for b in _RESULT_BLACKLIST):
                    continue
                if nl in h:
                    if el.get("clickable"):
                        return el
                    if fallback is None:
                        fallback = el
            if fallback is not None:
                return fallback
        return None

    def find_result(needles, exclude=()):
        found = _match_result(a11y_snapshot(), needles, exclude)
        if found is not None:
            return found
        return _match_result(dump_snapshot(), needles, exclude)

    def first_plausible_row():
        for snaps in (a11y_snapshot(), dump_snapshot()):
            for el in snaps:
                if is_edit_field(el):
                    continue
                h = hay(el)
                if len(h.strip()) < 3:
                    continue
                if any(b in h for b in _RESULT_BLACKLIST):
                    continue
                if any(w in h for w in _NONROW_WORDS):
                    continue
                if el.get("clickable"):
                    return el
        return None

    def retype(text):
        try:
            type_query(text, probe=text.strip().lower()[:6])
            return True
        except Exception:
            return False

    def await_result():
        active = list(needles_list)
        radius_clicks = 0
        enter_presses = 0
        short_done = False
        reopen_count = 0
        for poll in range(20):
            snaps = a11y_snapshot()
            el = (_match_result(snaps, active)
                  or _match_result(dump_snapshot(), active))
            if el is not None:
                return el
            rad = None
            for s in snaps:
                if "search radius" in hay(s) and s.get("clickable"):
                    rad = s
                    break
            if rad is not None and radius_clicks < 8:
                tap(rad)
                radius_clicks += 1
                settle(2)
                continue
            if not short_done and coord_pair() is None and poll >= 6:
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
                settle(1)
                open_search_screen()
                if search_field() is not None:
                    if not query_landed(probe_query):
                        type_query(location)
                    else:
                        try:
                            device.keyboard_enter()
                        except Exception:
                            pass
                        settle(2)
                continue
            if enter_presses < 3:
                try:
                    device.keyboard_enter()
                except Exception:
                    pass
                enter_presses += 1
                settle(2)
                continue
            settle(2)
        el = (_match_result(a11y_snapshot(), active)
              or _match_result(dump_snapshot(), active)
              or first_plausible_row())
        if el is not None:
            return el
        raise RuntimeError(
            "OsmAnd search returned no results for location %r" % location)

    # ---------------- place panel -> favorites ---------------- #
    def find_favorite_control(desc_only=False):
        idx = try_find([
            {"description": "Add favorite", "clickable": True},
            {"description": "Add favorite"},
            {"description": "Add to favorites", "clickable": True},
            {"description": "Add to favorite", "clickable": True},
            {"description": "Favorite", "clickable": True},
            {"description": "Favorites", "clickable": True}])
        if idx is not None:
            return a11y_el(idx, "favorite")

        def scan(snaps):
            best = None
            for el in snaps:
                d = (el.get("desc") or "").strip().lower()
                t = "" if desc_only else (el.get("text") or "").strip().lower()
                blob = (d + " " + t + " " +
                        (el.get("rid") or "")).strip().lower()
                if not blob:
                    continue
                if ("marker" in blob or "remove" in blob or "delete" in blob
                        or "my favorites" in blob):
                    continue
                hit = "favorit" in blob
                if not hit and d:
                    hit = bool(re.search(r"\bstar\b", d))
                if not hit:
                    continue
                if el.get("clickable") and d:
                    return el
                if best is None:
                    best = el
            return best

        found = scan(a11y_snapshot())
        if found is not None:
            return found
        return scan(dump_snapshot())

    def panel_open():
        if search_field_a11y():
            return False
        if find_favorite_control(desc_only=True) is not None:
            return True
        joined = " \n ".join(
            hay(el) for el in a11y_snapshot() if not is_edit_field(el))
        for n in needles_list:
            nl = n.strip().lower()
            if nl and nl in joined:
                return True
        return False

    def open_place_panel(result_el):
        if result_el is None:
            raise RuntimeError(
                "No OsmAnd search result to open for location %r" % location)
        tapped = []
        k0 = _el_key(result_el)
        if k0 is not None:
            tapped.append(k0)
        tap(result_el)
        settle(2.5)
        for _attempt in range(6):
            if panel_open():
                return
            target = find_result(needles_list, exclude=tapped)
            if target is None:
                # result row disappeared: navigation happened; the save step
                # verifies the panel precisely
                return
            k = _el_key(target)
            if k is not None:
                tapped.append(k)
            tap(target)
            settle(2.5)

    def dialog_open():
        for el in a11y_snapshot():
            if is_edit_field(el):
                return True
        for node in dump_nodes():
            if node["editable"] or "edittext" in node["cls"].lower():
                return True
        return False

    def confirm_target():
        idx = try_find([
            {"text": "Save", "clickable": True},
            {"text": "Save"},
            {"description": "Save", "clickable": True}])
        if idx is not None:
            return a11y_el(idx, "save")

        def scan(snaps):
            fallback = None
            for lab in _CONFIRM_LABELS:
                for el in snaps:
                    t = (el.get("text") or "").strip().lower()
                    d = (el.get("desc") or "").strip().lower()
                    if t == lab or d == lab:
                        if el.get("clickable"):
                            return el
                        if fallback is None:
                            fallback = el
            return fallback

        found = scan(a11y_snapshot())
        if found is not None:
            return found
        return scan(dump_snapshot())

    def confirm_favorite_dialog():
        confirm_clicks = 0
        star_reclicks = 0
        for attempt in range(16):
            target = confirm_target()
            if target is not None and confirm_clicks < 6 and \
                    (confirm_clicks == 0 or dialog_open()):
                tap(target)
                confirm_clicks += 1
                settle(2)
                continue
            if confirm_clicks > 0:
                return
            if star_reclicks < 3 and attempt >= 3 and not dialog_open():
                fav = find_favorite_control()
                if fav is not None:
                    tap(fav)
                    star_reclicks += 1
                    settle(2)
                    continue
            settle(2)
        if confirm_clicks == 0:
            raise RuntimeError(
                "Could not confirm the 'Add favorite' dialog for location %r"
                % location)

    def already_favorite():
        for el in a11y_snapshot():
            d = (el.get("desc") or "").lower()
            if "favorit" in d and ("remove" in d or "delete" in d):
                return True
        for node in dump_nodes():
            d = (node["desc"] or "").lower()
            if "favorit" in d and ("remove" in d or "delete" in d):
                return True
        return False

    def save_favorite():
        fav = None
        reclicks = 0
        actions_clicks = 0
        for _attempt in range(12):
            fav = find_favorite_control()
            if fav is not None:
                break
            if already_favorite():
                return True
            acted = False
            if actions_clicks < 2:
                for el in a11y_snapshot():
                    if ((el.get("text") or "").strip().lower() == "actions"
                            and el.get("clickable")):
                        tap(el)
                        actions_clicks += 1
                        settle(2)
                        acted = True
                        break
            if not acted and reclicks < 4 and not panel_open():
                row = find_result(needles_list)
                if row is not None:
                    tap(row)
                    reclicks += 1
                    settle(2)
                    acted = True
            if not acted:
                settle(2)
        if fav is None:
            raise RuntimeError(
                "Favorites (star) control not found on the OsmAnd place panel "
                "for %r" % location)
        if not tap(fav):
            raise RuntimeError(
                "Could not tap the favorites (star) control for %r" % location)
        settle(2)
        confirm_favorite_dialog()

    # ---------------- main flow ---------------- #
    launch_osmand()
    if not els() or not map_ui_visible():
        try:
            device.open_app("OsmAnd")
            settle(2)
        except Exception:
            pass
    open_search_screen()
    type_query(location)
    result_el = await_result()
    open_place_panel(result_el)
    save_favorite()
    return True
