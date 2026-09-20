PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": (
            "Place name such as 'Schaan, Liechtenstein' or a 'lat, lon' "
            "coordinate pair. This value is typed verbatim into OsmAnd search."
        ),
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    import re

    raw_location = binding.get("location")
    if raw_location is None or str(raw_location).strip() == "":
        raise ValueError("binding['location'] is required")
    location = str(raw_location)

    def _s(v):
        return "" if v is None else str(v)

    def _norm(v):
        return _s(v).strip().lower()

    def _text(el):
        return _norm(el.get("text"))

    def _hint(el):
        return _norm(el.get("hint"))

    def _desc(el):
        return _norm(el.get("description"))

    def _combined(el):
        return " ".join(
            [
                _s(el.get("text")),
                _s(el.get("hint")),
                _s(el.get("description")),
            ]
        ).strip().lower()

    def _class(el):
        return _norm(el.get("class_name"))

    def _elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def _element_by_index(idx):
        if idx is None:
            return None
        for el in _elements():
            if el.get("index") == idx:
                return el
        return None

    def _top(el):
        b = el.get("bounds") or el.get("bounds_in_screen")
        if isinstance(b, dict):
            for k in ("top", "y", "y1", "y0"):
                v = b.get(k)
                if isinstance(v, (int, float)):
                    return int(v)
        if isinstance(b, str):
            nums = [int(n) for n in re.findall(r"-?\d+", b)]
            if len(nums) >= 2:
                return nums[1]
        for k in ("top", "y", "y1", "y0"):
            v = el.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    def _looks_like_coord(s):
        parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
        numeric = 0
        for p in parts:
            if re.match(r"^-?\d+(\.\d+)?$", p):
                numeric += 1
        return numeric >= 2

    coord_like = _looks_like_coord(location)

    def _tokens():
        toks = [t.strip(" .;:()[]") for t in re.split(r"[,;\s]+", location)]
        toks = [t for t in toks if len(t) > 2]
        if not toks:
            toks = [location.strip()]
        return toks

    def _find_search_input():
        for kw in (
            {"hint": "Type to search all", "editable": True},
            {"text": "Type to search all", "editable": True},
            {"hint": "Search", "editable": True},
            {"text": "Search", "editable": True},
        ):
            try:
                idx = device.find(**kw)
                if idx is not None:
                    return idx
            except Exception:
                pass

        els = _elements()
        for el in els:
            if el.get("editable"):
                comb = _combined(el)
                if "search" in comb or "type to search" in comb:
                    return el.get("index")

        for el in els:
            if el.get("editable") and "edittext" in _class(el):
                return el.get("index")

        for el in els:
            if el.get("editable"):
                return el.get("index")

        return None

    def _find_search_button():
        for kw in (
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"hint": "Search", "clickable": True},
        ):
            try:
                idx = device.find(**kw)
                if idx is not None:
                    return idx
            except Exception:
                pass

        for el in _elements():
            if el.get("clickable"):
                comb = _combined(el)
                if comb == "search" or "search" in comb:
                    return el.get("index")

        for el in _elements():
            if el.get("clickable") and "imagebutton" in _class(el):
                return el.get("index")

        return None

    def _dismiss_dialogs():
        for _ in range(2):
            clicked = False
            for kw in (
                {"text": "OK", "clickable": True},
                {"text": "ALLOW", "clickable": True},
                {"text": "CONTINUE", "clickable": True},
                {"text": "NO THANKS", "clickable": True},
                {"text": "SKIP", "clickable": True},
                {"text": "CLOSE", "clickable": True},
                {"description": "Close", "clickable": True},
                {"description": "OK", "clickable": True},
            ):
                try:
                    idx = device.find(**kw)
                    if idx is not None:
                        device.click(index=idx)
                        device.settle(0.8)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                break

    def _open_search():
        if _find_search_input() is not None:
            return

        idx = _find_search_button()
        if idx is None:
            raise RuntimeError("Could not find OsmAnd search control")

        device.click(index=idx)
        device.settle(1.5)

        if _find_search_input() is None:
            try:
                device.navigate_back()
                device.settle(1.0)
            except Exception:
                pass

            if _find_search_input() is None:
                try:
                    device.open_app("OsmAnd")
                    device.settle(2.0)
                except Exception:
                    pass

            idx = _find_search_button()
            if idx is not None:
                device.click(index=idx)
                device.settle(1.5)

        if _find_search_input() is None:
            raise RuntimeError("OsmAnd search field did not appear")

    def _clear_search(idx):
        for kw in (
            {"description": "Clear", "clickable": True},
            {"text": "Clear", "clickable": True},
            {"contains": "clear", "clickable": True},
        ):
            try:
                c = device.find(**kw)
                if c is not None:
                    device.click(index=c)
                    device.settle(0.5)
                    return
            except Exception:
                pass

        for el in _elements():
            if el.get("clickable") and "clear" in _combined(el):
                device.click(index=el.get("index"))
                device.settle(0.5)
                return

        try:
            device.input_text("", index=idx)
            device.settle(0.3)
        except Exception:
            pass

    def _type_query():
        idx = _find_search_input()
        if idx is None:
            _open_search()
            idx = _find_search_input()

        if idx is None:
            raise RuntimeError("OsmAnd search field not found before typing")

        _clear_search(idx)
        idx = _find_search_input() or idx
        device.input_text(location, index=idx)
        device.settle(1.0)

    def _is_excluded_result(el):
        comb = _combined(el)
        if not comb:
            return True

        if "type to search" in comb or "search radius" in comb:
            return True

        chrome = {
            "search",
            "clear",
            "cancel",
            "back",
            "close",
            "ok",
            "allow",
            "deny",
            "continue",
            "skip",
            "more",
            "menu",
            "layers",
            "settings",
            "gps",
            "map",
            "address",
            "categories",
            "category",
            "nearby",
            "favorites",
            "recent",
            "suggestions",
            "filter",
            "sort",
            "distance",
            "directions",
            "share",
            "edit",
            "delete",
            "rename",
            "hide",
            "show",
            "copy",
            "paste",
            "select all",
            "done",
            "enter",
            "submit",
            "marker",
        }

        if comb in chrome:
            toks = _tokens()
            if any(t.lower() in comb for t in toks):
                return False
            return True

        if any(w in comb for w in ("no result", "nothing found", "not found")):
            return True

        toks = _tokens()
        if ("favorite" in comb or "star" in comb) and not any(
            t.lower() in comb for t in toks
        ):
            return True

        if _class(el) in {"imagebutton", "button"} and (
            "favorite" in comb or "star" in comb
        ):
            return True

        return False

    def _find_first_result():
        els = _elements()
        search_idx = _find_search_input()
        if search_idx is None:
            return None

        toks = _tokens()
        loc_lower = location.strip().lower()

        def scan(els, search_idx, require_after):
            cands = []
            for el in els:
                idx = el.get("index")
                if idx is None:
                    continue
                if el.get("editable"):
                    continue
                if _is_excluded_result(el):
                    continue
                if require_after and search_idx is not None and idx <= search_idx:
                    continue

                comb = _combined(el)
                text = _text(el)
                score = 0

                if loc_lower and loc_lower in comb:
                    score = 5
                elif any(t.lower() in comb for t in toks):
                    score = 4
                elif el.get("clickable") and text:
                    score = 2
                elif el.get("clickable"):
                    score = 2
                elif text:
                    score = 1

                if score:
                    cands.append((score, _top(el), idx))

            return cands

        cands = scan(els, search_idx, True)
        if not cands:
            cands = scan(els, search_idx, False)

        if not cands:
            try:
                device.scroll(direction="down")
                device.settle(0.7)
            except Exception:
                pass

            els = _elements()
            search_idx = _find_search_input()
            if search_idx is None:
                return None

            cands = scan(els, search_idx, True)
            if not cands:
                cands = scan(els, search_idx, False)

        if not cands:
            return None

        if coord_like:
            cands.sort(key=lambda x: (x[1], x[2]))
            clickable = [c for c in cands if c[0] >= 2]
            if clickable:
                return clickable[0][2]
            return cands[0][2]

        cands.sort(key=lambda x: (-x[0], x[1], x[2]))
        return cands[0][2]

    def _find_increase_radius():
        for kw in (
            {"contains": "INCREASE SEARCH RADIUS", "clickable": True},
            {"contains": "increase search radius", "clickable": True},
            {"text": "INCREASE SEARCH RADIUS"},
            {"text": "increase search radius"},
        ):
            try:
                idx = device.find(**kw)
                if idx is not None:
                    return idx
            except Exception:
                pass

        for el in _elements():
            comb = _combined(el)
            if "search radius" in comb:
                if el.get("clickable"):
                    return el.get("index")

        for el in _elements():
            comb = _combined(el)
            if "search radius" in comb:
                return el.get("index")

        return None

    def _find_marker_once():
        for kw in (
            {"text": "Marker", "clickable": True},
            {"description": "Marker", "clickable": True},
            {"text": "Marker"},
            {"description": "Marker"},
            {"contains": "Marker", "clickable": True},
            {"contains": "Marker"},
        ):
            try:
                idx = device.find(**kw)
                if idx is not None:
                    el = _element_by_index(idx)
                    if el is not None and "favorite" in _combined(el):
                        continue
                    return idx
            except Exception:
                pass

        els = _elements()
        best = None

        for el in els:
            comb = _combined(el)
            if "favorite" in comb:
                continue

            t = _text(el)
            d = _desc(el)
            if t == "marker" or d == "marker":
                score = 3 if el.get("clickable") else 2
                if best is None or score > best[0]:
                    best = (score, el.get("index"))

        if best is not None:
            return best[1]

        for el in els:
            comb = _combined(el)
            if "marker" in comb and "favorite" not in comb and el.get("clickable"):
                return el.get("index")

        for el in els:
            comb = _combined(el)
            if "marker" in comb and "favorite" not in comb:
                return el.get("index")

        return None

    def _find_marker_with_scroll():
        for direction in ("up", "left", "right", "down", "up", "down"):
            marker = _find_marker_once()
            if marker is not None:
                return marker
            try:
                device.scroll(direction=direction)
                device.settle(0.5)
            except Exception:
                pass
        return _find_marker_once()

    device.open_app("OsmAnd")
    device.settle(3.0)

    if _find_search_input() is None:
        _dismiss_dialogs()

    _open_search()
    _type_query()

    result_clicked = False

    for _ in range(6):
        result = _find_first_result()
        if result is not None:
            device.click(index=result)
            result_clicked = True
            device.settle(2.0)

            marker = _find_marker_with_scroll()
            if marker is not None:
                device.click(index=marker)
                device.settle(1.0)
                return True

        if result_clicked:
            marker = _find_marker_with_scroll()
            if marker is not None:
                device.click(index=marker)
                device.settle(1.0)
                return True

        try:
            device.keyboard_enter()
            device.settle(1.5)
        except Exception:
            pass

        result = _find_first_result()
        if result is not None:
            continue

        if result_clicked:
            marker = _find_marker_with_scroll()
            if marker is not None:
                device.click(index=marker)
                device.settle(1.0)
                return True

        inc = _find_increase_radius()
        if inc is not None:
            device.click(index=inc)
            device.settle(1.5)
            if _find_first_result() is None:
                _type_query()
            continue

        try:
            device.scroll(direction="down")
            device.settle(1.0)
        except Exception:
            pass

        result = _find_first_result()
        if result is not None:
            continue

        _type_query()

    raise RuntimeError("Could not add marker for the requested location")
