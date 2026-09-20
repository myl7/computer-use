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

    def _element_by_index(idx):
        if idx is None:
            return None
        for el in device.elements():
            if el.get("index") == idx:
                return el
        return None

    def _find_control(label):
        idx = device.find(text=label)
        if idx is None:
            idx = device.find(contains=label)
        if idx is None:
            idx = device.find(hint=label)
        if idx is None:
            idx = device.find(description=label)
        if idx is None:
            low = _norm(label)
            for el in device.elements():
                blob = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if low and low in blob:
                    return el.get("index")
        return idx

    def _find_search_button():
        idx = device.find(description="Search", clickable=True)
        if idx is None:
            idx = device.find(description="Search")
        if idx is None:
            idx = device.find(text="Search", clickable=True)
        if idx is None:
            idx = device.find(text="Search")
        if idx is None:
            idx = device.find(hint="Search", clickable=True)
        if idx is None:
            for el in device.elements():
                if el.get("editable"):
                    continue
                desc = _norm(el.get("description") or "")
                text = _norm(el.get("text") or "")
                hint = _norm(el.get("hint") or "")
                if desc == "search" or text == "search" or hint == "search":
                    return el.get("index")
            for el in device.elements():
                if el.get("editable") or not el.get("clickable"):
                    continue
                desc = _norm(el.get("description") or "")
                if "search" in desc and "categories" not in desc:
                    return el.get("index")
        return idx

    def _find_search_edit():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is None:
            idx = device.find(contains="Type to search all", editable=True)
        if idx is None:
            idx = device.find(text="Type to search all", editable=True)
        if idx is None:
            idx = device.find(editable=True, contains="Search")
        if idx is None:
            idx = device.find(editable=True, contains="search")
        if idx is None:
            idx = device.find(editable=True, hint="Search")
        if idx is None:
            idx = device.find(editable=True, clickable=True)
        if idx is None:
            idx = device.find(editable=True)
        if idx is None:
            for el in device.elements():
                if el.get("editable"):
                    cls = el.get("class_name") or ""
                    if "EditText" in cls:
                        return el.get("index")
            for el in device.elements():
                if el.get("editable"):
                    return el.get("index")
        if idx is None:
            idx = device.find(hint="Type to search all")
        if idx is None:
            idx = device.find(contains="Type to search all")
        return idx

    def _clear_field(idx):
        for _ in range(5):
            clear_idx = device.find(description="Clear", clickable=True)
            if clear_idx is None:
                clear_idx = device.find(text="Clear", clickable=True)
            if clear_idx is None:
                for el in device.elements():
                    if el.get("clickable") and "clear" in _norm(
                        el.get("description") or ""
                    ):
                        clear_idx = el.get("index")
                        break
            if clear_idx is None:
                break

            device.click(index=clear_idx)
            device.settle(0.5)

            new_idx = _find_search_edit()
            if new_idx is not None:
                idx = new_idx

            el = _element_by_index(idx)
            if el is None or not (el.get("text") or "").strip():
                return idx

        try:
            device.input_text("", index=idx)
            device.settle(0.5)
        except Exception:
            pass

        new_idx = _find_search_edit()
        if new_idx is not None:
            idx = new_idx

        el = _element_by_index(idx)
        if el is None or not (el.get("text") or "").strip():
            return idx

        try:
            device.execute({"action_type": "clear_text", "index": idx})
            device.settle(0.5)
        except Exception:
            pass

        new_idx = _find_search_edit()
        if new_idx is not None:
            idx = new_idx

        el = _element_by_index(idx)
        if el is None or not (el.get("text") or "").strip():
            return idx

        try:
            device.click(index=idx)
            device.settle(0.5)
            device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
            text_len = len((el.get("text") or "").strip()) if el else 0
            for _ in range(min(100, max(20, text_len + 10))):
                device.adb_shell("input", "keyevent", "KEYCODE_DEL")
        except Exception:
            pass

        return _find_search_edit() or idx

    def _find_first_result(search_idx):
        elements = device.elements()

        raw = location
        norm_raw = _norm(raw)
        tokens = [t for t in re.split(r"[,;\s]+", raw) if t]
        norm_tokens = [_norm(t) for t in tokens]

        numeric_prefixes = []
        numeric_count = 0
        for t in tokens:
            if re.match(r"^[+-]?\d+(?:\.\d+)?$", t):
                numeric_count += 1
                tl = t.lower()
                for n in (6, 4, 3):
                    if len(tl) >= n:
                        numeric_prefixes.append(tl[:n])

        is_coord = numeric_count >= 2
        weak_threshold = 25 if is_coord else 50

        control_texts = {
            "categories",
            "clear",
            "search",
            "marker",
            "favorite",
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
        }

        first_weak_idx = None

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

            all_norm = _norm(" ".join([text, hint, desc]))
            disp_norm = _norm(display)

            if disp_norm in control_texts:
                continue

            if (
                "increase search radius" in all_norm
                or "show on map" in all_norm
                or "type to search" in all_norm
                or "no results" in all_norm
                or "not found" in all_norm
            ):
                continue

            score = 0

            if norm_raw and norm_raw in all_norm:
                score += 200

            for nt in norm_tokens:
                if nt and nt in all_norm:
                    score += 100

            for pref in numeric_prefixes:
                if pref and pref in all_norm:
                    score += 60

            if "°" in display or "\u00b0" in display:
                score += 40

            if re.search(r"\b[NSEW]\b", display, re.IGNORECASE):
                score += 10

            if any(c.isdigit() for c in display):
                score += 5

            if el.get("clickable"):
                score += 10

            if score >= 80:
                return idx

            if first_weak_idx is None and score >= weak_threshold:
                first_weak_idx = idx

        return first_weak_idx

    def _find_marker():
        idx = device.find(text="Marker", clickable=True)
        if idx is None:
            idx = device.find(description="Marker", clickable=True)
        if idx is None:
            idx = device.find(text="Marker")
        if idx is None:
            idx = device.find(description="Marker")
        if idx is None:
            idx = device.find(contains="Marker")
        if idx is not None:
            return idx

        for el in device.elements():
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            if (text.lower() == "marker" or desc.lower() == "marker") and el.get(
                "clickable"
            ):
                return el.get("index")

        for el in device.elements():
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            if text.lower() == "marker" or desc.lower() == "marker":
                return el.get("index")

        for el in device.elements():
            text = (el.get("text") or "").lower()
            desc = (el.get("description") or "").lower()
            if "marker" in text or "marker" in desc:
                if (
                    "favorite" not in text
                    and "star" not in text
                    and "favorite" not in desc
                    and "star" not in desc
                ):
                    return el.get("index")

        return None

    device.open_app(app_name)
    device.settle(2)

    search_btn = None
    for attempt in range(6):
        for label in ("Allow", "OK", "Continue", "Skip", "Accept"):
            idx = _find_control(label)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                break

        search_btn = _find_search_button()
        if search_btn is not None:
            break

        if attempt < 2:
            device.settle(1)
            continue

        device.navigate_back()
        device.settle(1)

    if search_btn is None:
        raise RuntimeError("Could not find OsmAnd Search button")

    device.click(index=search_btn)
    device.settle(1)

    search_idx = _find_search_edit()
    if search_idx is None:
        for label in ("Allow", "OK", "Continue", "Skip", "Accept"):
            idx = _find_control(label)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                break
        search_idx = _find_search_edit()

    if search_idx is None:
        search_btn = _find_search_button()
        if search_btn is not None:
            device.click(index=search_btn)
            device.settle(1)
        search_idx = _find_search_edit()

    if search_idx is None:
        raise RuntimeError("Could not find OsmAnd search field")

    search_idx = _clear_field(search_idx)

    try:
        device.click(index=search_idx)
        device.settle(0.5)
    except Exception:
        pass

    device.input_text(location, index=search_idx)
    device.settle(1)

    el = _element_by_index(search_idx)
    current = (el.get("text") or "") if el else ""
    if current != location and location not in current:
        search_idx = _clear_field(search_idx)
        device.input_text(location, index=search_idx)
        device.settle(1)

    result_idx = None
    for attempt in range(8):
        search_idx = _find_search_edit() or search_idx

        el = _element_by_index(search_idx)
        current = (el.get("text") or "") if el else ""

        if current != location and location not in current:
            if el is not None and not current.strip():
                device.input_text(location, index=search_idx)
                device.settle(1)
            elif el is not None:
                search_idx = _clear_field(search_idx)
                device.input_text(location, index=search_idx)
                device.settle(1)

        result_idx = _find_first_result(search_idx)
        if result_idx is not None:
            break

        inc_idx = _find_control("INCREASE SEARCH RADIUS")
        if inc_idx is not None:
            device.click(index=inc_idx)
            device.settle(1)
            continue

        if attempt == 2:
            try:
                device.keyboard_enter()
                device.settle(1)
            except Exception:
                pass

            if _find_search_edit() is None:
                enter_marker = _find_marker()
                if enter_marker is not None:
                    device.click(index=enter_marker)
                    device.settle(1)
                    return True

            continue

        try:
            device.scroll("down" if attempt % 2 == 0 else "up")
            device.settle(0.5)
        except Exception:
            pass

    if result_idx is None:
        raise RuntimeError("Could not find a search result for the requested location")

    marker_idx = None
    for attempt in range(3):
        try:
            device.click(index=result_idx)
        except Exception:
            pass

        device.settle(2)

        marker_idx = _find_marker()
        if marker_idx is not None:
            break

        for _ in range(2):
            try:
                device.scroll("up")
                device.settle(0.5)
            except Exception:
                pass
            marker_idx = _find_marker()
            if marker_idx is not None:
                break

            try:
                device.scroll("down")
                device.settle(0.5)
            except Exception:
                pass
            marker_idx = _find_marker()
            if marker_idx is not None:
                break

        if marker_idx is not None:
            break

        search_idx = _find_search_edit()
        new_result = _find_first_result(search_idx)
        if new_result is not None:
            result_idx = new_result
        else:
            break

    if marker_idx is None:
        raise RuntimeError("Could not find the Marker control for the selected location")

    device.click(index=marker_idx)
    device.settle(1)
    return True
