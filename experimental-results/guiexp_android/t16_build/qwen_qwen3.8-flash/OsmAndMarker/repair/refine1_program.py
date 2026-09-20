PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": (
            "Place to mark, typed verbatim into OsmAnd's search field. "
            "Either a place name such as 'Schaan, Liechtenstein' or a "
            "'lat, lon' coordinate pair."
        ),
    }
}


def program(device, binding: dict) -> bool:
    import re
    import unicodedata

    if "location" not in binding:
        raise ValueError("binding must contain 'location'")

    loc = binding["location"]
    if loc is None:
        raise ValueError("binding['location'] is None")

    if isinstance(loc, (list, tuple)):
        location = ", ".join(str(x) for x in loc)
    else:
        location = str(loc)

    if not location.strip():
        raise ValueError("binding['location'] is empty")

    app_name = binding.get("app_name", "OsmAnd") or "OsmAnd"

    def _norm(s):
        if s is None:
            return ""
        s = unicodedata.normalize("NFKD", str(s))
        s = s.encode("ascii", "ignore").decode("ascii")
        return s.lower()

    def _compact(s):
        return re.sub(r"[^a-z0-9.+-]", "", _norm(s))

    def _elements():
        try:
            return device.elements()
        except Exception:
            return []

    def _find(**kwargs):
        try:
            return device.find(**kwargs)
        except Exception:
            return None

    def _element_by_index(idx):
        if idx is None:
            return None
        for el in _elements():
            if el.get("index") == idx:
                return el
        return None

    def _blob(el):
        return _norm(
            " ".join(
                [
                    el.get("text") or "",
                    el.get("hint") or "",
                    el.get("description") or "",
                ]
            )
        )

    def _compact_blob(el):
        return _compact(
            " ".join(
                [
                    el.get("text") or "",
                    el.get("hint") or "",
                    el.get("description") or "",
                ]
            )
        )

    def _click_idx(idx, wait=1):
        if idx is None:
            return False
        try:
            device.click(index=idx)
            device.settle(wait)
            return True
        except Exception:
            return False

    def _input_text(text, idx=None):
        if idx is not None:
            try:
                device.input_text(text, index=idx)
                device.settle(1)
                return True
            except Exception:
                pass
        try:
            device.input_text(text)
            device.settle(1)
            return True
        except Exception:
            return False

    def _dismiss_popups():
        for _ in range(3):
            clicked = False
            for label in (
                "Allow",
                "OK",
                "Continue",
                "Skip",
                "Accept",
                "Got it",
                "Maybe later",
                "No thanks",
                "Close",
                "Dismiss",
            ):
                idx = _find(text=label, clickable=True)
                if idx is None:
                    idx = _find(description=label, clickable=True)
                if idx is None:
                    idx = _find(contains=label, clickable=True)
                if idx is None:
                    idx = _find_control(label)
                if idx is not None and _click_idx(idx, 1):
                    clicked = True
                    break
            if not clicked:
                break

    def _find_control(label):
        idx = _find(text=label)
        if idx is None:
            idx = _find(contains=label)
        if idx is None:
            idx = _find(hint=label)
        if idx is None:
            idx = _find(description=label)
        if idx is None:
            low = _norm(label)
            for el in _elements():
                blob = _blob(el)
                if low and low in blob:
                    return el.get("index")
        return idx

    def _find_search_button():
        idx = _find(description="Search", clickable=True)
        if idx is None:
            idx = _find(text="Search", clickable=True)
        if idx is None:
            idx = _find(hint="Search", clickable=True)
        if idx is None:
            idx = _find(description="Search")
        if idx is None:
            idx = _find(text="Search")
        if idx is None:
            idx = _find(contains="Search", clickable=True)

        if idx is None:
            for el in _elements():
                if el.get("editable"):
                    continue
                blob = _blob(el)
                if blob in ("search", "search button", "magnifier"):
                    return el.get("index")

        if idx is None:
            for el in _elements():
                if el.get("editable") or not el.get("clickable"):
                    continue
                blob = _blob(el)
                if "search" in blob and "categories" not in blob:
                    return el.get("index")

        return idx

    def _find_search_edit():
        idx = _find(hint="Type to search all", editable=True)
        if idx is None:
            idx = _find(contains="Type to search all", editable=True)
        if idx is None:
            idx = _find(text="Type to search all", editable=True)
        if idx is None:
            idx = _find(editable=True, contains="Search")
        if idx is None:
            idx = _find(editable=True, contains="search")
        if idx is None:
            idx = _find(editable=True, hint="Search")
        if idx is None:
            idx = _find(editable=True, clickable=True)
        if idx is None:
            idx = _find(editable=True)

        if idx is None:
            for el in _elements():
                if el.get("editable") and "EditText" in (el.get("class_name") or ""):
                    return el.get("index")
        if idx is None:
            for el in _elements():
                if el.get("editable"):
                    return el.get("index")

        if idx is None:
            idx = _find(hint="Type to search all")
        if idx is None:
            idx = _find(contains="Type to search all")
        return idx

    def _field_text(idx):
        el = _element_by_index(idx)
        if el is None:
            return ""
        return (el.get("text") or "").strip()

    def _clear_field(idx):
        if idx is None:
            idx = _find_search_edit()
        if idx is None:
            return None

        for _ in range(4):
            idx = _find_search_edit() or idx
            if not _field_text(idx):
                return idx

            clear_idx = _find(description="Clear", clickable=True)
            if clear_idx is None:
                clear_idx = _find(text="Clear", clickable=True)
            if clear_idx is None:
                clear_idx = _find(contains="Clear", clickable=True)
            if clear_idx is None:
                for el in _elements():
                    if el.get("clickable") and "clear" in _blob(el):
                        clear_idx = el.get("index")
                        break

            if clear_idx is not None and _click_idx(clear_idx, 0.5):
                continue

            _input_text("", idx)
            if not _field_text(idx):
                return idx

            try:
                device.click(index=idx)
                device.settle(0.3)
                device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
                n = len(_field_text(idx)) + 10
                for _ in range(min(120, max(20, n))):
                    device.adb_shell("input", "keyevent", "KEYCODE_DEL")
                device.settle(0.3)
            except Exception:
                pass

            idx = _find_search_edit() or idx
            if not _field_text(idx):
                return idx

        return idx

    def _open_search():
        for _ in range(4):
            _dismiss_popups()

            search_idx = _find_search_edit()
            if search_idx is not None:
                return search_idx

            search_btn = _find_search_button()
            if search_btn is None:
                map_idx = _find(description="Map")
                if map_idx is None:
                    map_idx = _find(text="Map")
                if map_idx is not None:
                    _click_idx(map_idx, 1)
                    search_btn = _find_search_button()

            if search_btn is not None and _click_idx(search_btn, 1):
                search_idx = _find_search_edit()
                if search_idx is not None:
                    return search_idx

                search_btn = _find_search_button()
                if search_btn is not None and _click_idx(search_btn, 1):
                    search_idx = _find_search_edit()
                    if search_idx is not None:
                        return search_idx

            device.navigate_back()
            device.settle(1)

        raise RuntimeError("Could not open OsmAnd search")

    def _type_location(search_idx):
        for _ in range(3):
            search_idx = _clear_field(search_idx)
            if search_idx is None:
                search_idx = _find_search_edit()
            if search_idx is None:
                raise RuntimeError("Could not find OsmAnd search field")

            _click_idx(search_idx, 0.3)
            _input_text(location, search_idx)

            current = _field_text(search_idx)
            if _compact(location) and _compact(location) in _compact(current):
                return search_idx

            if not current.strip():
                _input_text(location, None)
                search_idx = _find_search_edit() or search_idx
                current = _field_text(search_idx)
                if _compact(location) and _compact(location) in _compact(current):
                    return search_idx

        return search_idx

    def _find_first_result(search_idx=None):
        elements = _elements()

        raw = location
        norm_raw = _norm(raw)
        compact_raw = _compact(raw)

        tokens = [t for t in re.split(r"[,;\s]+", raw) if t]
        norm_tokens = [_norm(t) for t in tokens if _norm(t)]

        numeric_prefixes = []
        numeric_count = 0
        for t in tokens:
            if re.match(r"^[+-]?\d+(?:\.\d+)?$", t):
                numeric_count += 1
                tc = _compact(t)
                for n in (6, 4, 3):
                    if len(tc) >= n:
                        numeric_prefixes.append(tc[:n])

        is_coord = numeric_count >= 2
        threshold = 60 if is_coord else 80
        weak_threshold = 50 if is_coord else 60

        control_exact = {
            "categories",
            "clear",
            "search",
            "marker",
            "favorite",
            "favourite",
            "star",
            "show on map",
            "increase search radius",
            "navigate up",
            "configure map",
            "map",
            "back",
            "ok",
            "cancel",
            "allow",
            "deny",
            "addresses",
            "places",
            "points of interest",
            "recent",
            "history",
            "sort",
            "filter",
            "more",
            "menu",
            "home",
            "navigate",
            "directions",
            "share",
            "details",
            "add to map",
            "add marker",
            "create marker",
            "map marker",
            "place marker",
            "marker on map",
        }

        bad_contains = (
            "increase search radius",
            "show on map",
            "type to search",
            "no results",
            "not found",
            "search categories",
            "clear text",
            "clear search",
        )

        first_weak = None

        for order, el in enumerate(elements):
            idx = el.get("index", order)

            if search_idx is not None and idx == search_idx:
                continue
            if el.get("editable"):
                continue

            text = (el.get("text") or "").strip()
            hint = (el.get("hint") or "").strip()
            desc = (el.get("description") or "").strip()
            display = text or desc or hint
            if not display:
                continue

            blob = _norm(" ".join([text, hint, desc]))
            compact_blob = _compact(" ".join([text, hint, desc]))
            disp_norm = _norm(display)
            disp_compact = _compact(display)

            if disp_norm in control_exact or disp_compact in control_exact:
                continue
            if any(b in blob for b in bad_contains):
                continue

            score = 0
            matched_tokens = 0
            matched_prefix = False

            for nt in norm_tokens:
                if nt and nt in blob:
                    score += 80
                    matched_tokens += 1

            for pref in numeric_prefixes:
                if pref and pref in compact_blob:
                    score += 40
                    matched_prefix = True

            if compact_raw and compact_raw in compact_blob:
                score += 250

            if compact_raw and disp_compact == compact_raw:
                score += 300

            if "°" in display or "\u00b0" in display:
                score += 40

            if re.search(r"\b[NSEW]\b", display, re.IGNORECASE):
                score += 10

            if any(c.isdigit() for c in display):
                score += 5

            if el.get("clickable"):
                score += 20

            if norm_tokens and matched_tokens == len(norm_tokens):
                score += 100

            if (
                score >= threshold
                and (
                    matched_tokens > 0
                    or matched_prefix
                    or (compact_raw and compact_raw in compact_blob)
                )
            ):
                return idx

            if (
                first_weak is None
                and score >= weak_threshold
                and (matched_tokens > 0 or matched_prefix)
            ):
                first_weak = idx

        return first_weak

    def _panel_open():
        compact_raw = _compact(location)
        tokens = [t for t in re.split(r"[,;\s]+", location) if t]
        first_norm = _norm(tokens[0]) if tokens else ""

        for el in _elements():
            if el.get("editable"):
                continue
            blob = _blob(el)
            compact_blob = _compact_blob(el)
            if compact_raw and compact_raw in compact_blob:
                return True
            if first_norm and first_norm in blob:
                return True
        return False

    def _find_marker():
        labels = (
            "Marker",
            "Add marker",
            "Create marker",
            "Map marker",
            "Place marker",
            "Marker on map",
            "Add map marker",
        )

        for label in labels:
            idx = _find(text=label, clickable=True)
            if idx is not None:
                return idx
            idx = _find(description=label, clickable=True)
            if idx is not None:
                return idx
            idx = _find(contains=label, clickable=True)
            if idx is not None:
                return idx

        marker_norm_labels = [_norm(x) for x in labels]
        exclude = (
            "favorite",
            "favourite",
            "star",
            "navigate",
            "directions",
            "share",
            "details",
            "show on map",
            "search",
            "clear",
            "categories",
            "increase search radius",
            "configure map",
            "back",
            "ok",
            "cancel",
            "allow",
            "deny",
        )

        for el in _elements():
            if not el.get("clickable"):
                continue
            blob = _blob(el)
            if any(lbl in blob for lbl in marker_norm_labels):
                if not any(ex in blob for ex in exclude):
                    return el.get("index")

        exact_norm = {
            "marker",
            "add marker",
            "create marker",
            "map marker",
            "place marker",
            "marker on map",
            "add map marker",
        }

        for el in _elements():
            blob = _blob(el)
            if blob in exact_norm and not any(ex in blob for ex in exclude):
                return el.get("index")

        return None

    def _try_click_marker():
        idx = _find_marker()
        if idx is None:
            return False
        return _click_idx(idx, 1)

    def _try_add_to_map():
        labels = ("Add to map", "Add to Map", "add to map")

        for label in labels:
            idx = _find(text=label, clickable=True)
            if idx is None:
                idx = _find(description=label, clickable=True)
            if idx is None:
                idx = _find(contains=label, clickable=True)
            if idx is not None:
                if _click_idx(idx, 1) and _try_click_marker():
                    return True
                return False

        for el in _elements():
            if not el.get("clickable"):
                continue
            blob = _blob(el)
            if "add to map" in blob:
                idx = el.get("index")
                if _click_idx(idx, 1) and _try_click_marker():
                    return True
                return False

        return False

    def _try_more_then_marker():
        labels = ("More", "More options")

        for label in labels:
            idx = _find(text=label, clickable=True)
            if idx is None:
                idx = _find(description=label, clickable=True)
            if idx is None:
                idx = _find(contains=label, clickable=True)
            if idx is not None:
                if _click_idx(idx, 1):
                    if _try_click_marker():
                        return True
                    device.navigate_back()
                    device.settle(1)
                return False

        for el in _elements():
            if not el.get("clickable"):
                continue
            blob = _blob(el)
            if blob in ("more", "more options") or blob.startswith("more options"):
                idx = el.get("index")
                if _click_idx(idx, 1):
                    if _try_click_marker():
                        return True
                    device.navigate_back()
                    device.settle(1)
                return False

        return False

    def _try_long_press_map():
        map_idx = _find(description="Map")
        if map_idx is None:
            map_idx = _find(text="Map")
        if map_idx is None:
            map_idx = _find(hint="Map")
        if map_idx is None:
            return False

        try:
            device.execute({"action_type": "long_press", "index": map_idx})
            device.settle(2)
        except Exception:
            pass

        if _try_click_marker():
            return True
        if _try_add_to_map():
            return True
        return False

    device.open_app(app_name)
    device.settle(2)
    _dismiss_popups()

    search_idx = _open_search()
    search_idx = _type_location(search_idx)

    if _try_click_marker():
        return True

    try:
        device.keyboard_enter()
        device.settle(1)
    except Exception:
        pass

    if _try_click_marker():
        return True

    selected_result = False

    for _attempt in range(8):
        if _try_click_marker():
            return True

        search_idx = _find_search_edit()

        if search_idx is None:
            for _ in range(4):
                if _try_click_marker():
                    return True
                try:
                    device.scroll(direction="up")
                    device.settle(0.5)
                except Exception:
                    pass

            if _try_click_marker():
                return True

            if selected_result or _panel_open():
                if _try_add_to_map():
                    return True
                if _try_more_then_marker():
                    return True
                if _try_long_press_map():
                    return True

            device.navigate_back()
            device.settle(1)
            selected_result = False

            search_idx = _find_search_edit()
            if search_idx is None:
                try:
                    search_idx = _open_search()
                    search_idx = _type_location(search_idx)
                    try:
                        device.keyboard_enter()
                        device.settle(1)
                    except Exception:
                        pass
                except Exception:
                    pass
            continue

        current = _field_text(search_idx)
        if not current.strip() or _compact(location) not in _compact(current):
            search_idx = _type_location(search_idx)
            try:
                device.keyboard_enter()
                device.settle(1)
            except Exception:
                pass

        result_idx = _find_first_result(search_idx)

        if result_idx is None:
            try:
                device.keyboard_enter()
                device.settle(1)
            except Exception:
                pass
            result_idx = _find_first_result(search_idx)

        if result_idx is None:
            try:
                device.scroll(direction="down")
                device.settle(0.5)
            except Exception:
                pass
            result_idx = _find_first_result(search_idx)

        if result_idx is None:
            device.navigate_back()
            device.settle(1)
            selected_result = False
            try:
                search_idx = _open_search()
                search_idx = _type_location(search_idx)
                try:
                    device.keyboard_enter()
                    device.settle(1)
                except Exception:
                    pass
            except Exception:
                pass
            continue

        if _click_idx(result_idx, 2):
            selected_result = True
        else:
            selected_result = False

        if _try_click_marker():
            return True

        for _ in range(5):
            try:
                device.scroll(direction="up")
                device.settle(0.5)
            except Exception:
                pass
            if _try_click_marker():
                return True

        for _ in range(2):
            try:
                device.scroll(direction="down")
                device.settle(0.5)
            except Exception:
                pass
            if _try_click_marker():
                return True

        if _try_add_to_map():
            return True
        if _try_more_then_marker():
            return True
        if _try_long_press_map():
            return True

        device.navigate_back()
        device.settle(1)
        selected_result = False

        if _find_search_edit() is None:
            try:
                search_idx = _open_search()
                search_idx = _type_location(search_idx)
                try:
                    device.keyboard_enter()
                    device.settle(1)
                except Exception:
                    pass
            except Exception:
                pass

    raise RuntimeError("Could not add the OsmAnd location marker")
