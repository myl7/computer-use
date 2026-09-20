PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": (
            "Place to mark. Either a place name such as 'Schaan, Liechtenstein' "
            "or a 'lat, lon' coordinate pair. Typed verbatim into OsmAnd search."
        ),
    }
}


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None:
        raise ValueError("binding must contain 'location'")
    location = str(location)
    if not location:
        raise ValueError("'location' must not be empty")

    def _find_any(crits):
        for crit in crits:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def _find_control(labels):
        for label in labels:
            idx = _find_any(
                [
                    {"text": label},
                    {"contains": label},
                    {"description": label},
                ]
            )
            if idx is not None:
                return idx

        try:
            elements = device.elements()
        except Exception:
            elements = []

        for e in elements:
            hay = (
                f"{e.get('text') or ''} "
                f"{e.get('hint') or ''} "
                f"{e.get('description') or ''}"
            ).lower()
            for label in labels:
                if label.lower() in hay:
                    return e.get("index")
        return None

    def _find_search_field():
        idx = _find_any(
            [
                {"editable": True, "hint": "Type to search all"},
                {"editable": True, "text": "Type to search all"},
                {"editable": True, "contains": "Type to search"},
                {"editable": True, "contains": "Search"},
                {"editable": True, "contains": "search"},
                {"editable": True, "clickable": True},
            ]
        )
        if idx is not None:
            return idx

        try:
            elements = device.elements()
        except Exception:
            elements = []

        for e in elements:
            if not e.get("editable"):
                continue
            label = (
                f"{e.get('text') or ''} "
                f"{e.get('hint') or ''} "
                f"{e.get('description') or ''}"
            ).lower()
            cls = str(e.get("class_name") or "").lower()
            if "search" in label or "type" in label or "coordinate" in label or "edit" in cls:
                return e.get("index")

        return _find_any([{"editable": True}])

    def _ensure_search_screen():
        for _ in range(5):
            idx = _find_search_field()
            if idx is not None:
                return idx

            search_idx = _find_any(
                [
                    {"description": "Search", "clickable": True},
                    {"text": "Search", "clickable": True},
                    {"contains": "Search", "clickable": True},
                    {"description": "search", "clickable": True},
                ]
            )
            if search_idx is not None:
                try:
                    device.click(index=search_idx)
                except Exception:
                    pass
                device.settle(1.5)
                continue

            up_idx = _find_any(
                [
                    {"description": "Navigate up", "clickable": True},
                    {"text": "Navigate up", "clickable": True},
                    {"contains": "Navigate up", "clickable": True},
                ]
            )
            if up_idx is not None:
                try:
                    device.click(index=up_idx)
                except Exception:
                    pass
                device.settle(1.0)
                continue

            try:
                device.open_app(str(binding.get("app_name") or "OsmAnd"))
            except Exception:
                pass
            device.settle(2.0)

        idx = _find_search_field()
        if idx is None:
            raise RuntimeError("Could not open OsmAnd search field")
        return idx

    def _clear_search():
        clear_idx = _find_any(
            [
                {"description": "Clear", "clickable": True},
                {"text": "Clear", "clickable": True},
                {"contains": "Clear", "clickable": True},
                {"description": "clear", "clickable": True},
            ]
        )
        if clear_idx is not None:
            try:
                device.click(index=clear_idx)
            except Exception:
                pass
            device.settle(1.0)
            return True

        idx = _find_search_field()
        if idx is not None:
            try:
                device.input_text("", index=idx)
            except Exception:
                pass
            device.settle(0.5)
            return True
        return False

    def _type_location():
        idx = _find_search_field()
        if idx is None:
            idx = _ensure_search_screen()

        try:
            device.click(index=idx)
        except Exception:
            pass
        device.settle(0.5)

        device.input_text(location, index=idx)
        device.settle(1.5)

    def _norm(value):
        return "".join(
            ch for ch in str(value).lower() if ch.isalnum() or ch in ".,- "
        )

    def _norm_nospace(value):
        return "".join(ch for ch in _norm(value) if ch != " ")

    def _match_parts(value):
        parts = [value]
        if "," in value:
            left, right = value.split(",", 1)
            parts.extend([left, right])
        else:
            parts.append(value.split(" ", 1)[0])
        parts.append(value.replace(",", " "))

        out = []
        for part in parts:
            part = str(part).strip()
            if not part:
                continue
            for variant in (part, part.replace(",", " ")):
                out.append(_norm(variant))
                out.append(_norm_nospace(variant))

        seen = set()
        result = []
        for part in out:
            if len(part) > 1 and part not in seen:
                seen.add(part)
                result.append(part)
        return result

    def _is_known_control(e):
        text = str(e.get("text") or "").strip().lower()
        desc = str(e.get("description") or "").strip().lower()
        hint = str(e.get("hint") or "").strip().lower()

        known_desc = {
            "clear",
            "navigate up",
            "search",
            "categories",
            "back",
            "close",
            "menu",
            "filter",
            "sort",
            "voice",
            "more",
            "options",
        }
        known_text = {
            "clear",
            "search",
            "categories",
            "navigate up",
            "back",
            "close",
            "menu",
            "filter",
            "sort",
            "voice",
            "more",
            "options",
            "increase search radius",
        }

        if desc in known_desc:
            return True
        if text in known_text:
            return True
        if "increase search radius" in text or "increase search radius" in desc or "increase search radius" in hint:
            return True
        if "navigate up" in text or "navigate up" in desc:
            return True
        if "type to search" in hint or "type to search" in text:
            return True
        if "favorite" in desc or "star" in desc:
            return True
        if "favorite" in hint or "star" in hint:
            return True
        if text in {"favorite", "star", "favorites", "add to favorites"}:
            return True
        if "add to favorites" in text:
            return True
        return False

    def _get_result_candidates(search_idx=None):
        try:
            elements = device.elements()
        except Exception:
            elements = []

        if search_idx is None:
            search_idx = _find_search_field()

        parts = _match_parts(location)
        candidates = []

        for e in elements:
            if e.get("editable"):
                continue
            if search_idx is not None and e.get("index") == search_idx:
                continue
            if _is_known_control(e):
                continue

            hay = (
                f"{e.get('text') or ''} "
                f"{e.get('hint') or ''} "
                f"{e.get('description') or ''}"
            ).strip()
            if not hay:
                continue
            candidates.append((e, hay))

        def score(item):
            e, hay = item
            nh = _norm(hay)
            nns = _norm_nospace(hay)
            s = 0

            for part in parts:
                if part in nh or part in nns:
                    s += 100
                    break

            for part in parts:
                if nh.startswith(part) or nns.startswith(part):
                    s += 20
                    break

            if e.get("clickable"):
                s += 10
            if search_idx is not None and e.get("index", -1) > search_idx:
                s += 5
            return s

        candidates.sort(key=lambda item: (-score(item), item[0].get("index", 0)))
        return [e for e, _ in candidates]

    def _select_first_result():
        for _ in range(4):
            search_idx = _find_search_field()
            candidates = _get_result_candidates(search_idx)

            for result in candidates[:3]:
                idx = result.get("index")
                if idx is None:
                    continue
                try:
                    device.click(index=idx)
                    device.settle(2.0)
                    return True
                except Exception:
                    pass

            inc_idx = _find_control(
                [
                    "INCREASE SEARCH RADIUS",
                    "Increase search radius",
                    "increase search radius",
                ]
            )
            if inc_idx is not None:
                try:
                    device.click(index=inc_idx)
                except Exception:
                    pass
                device.settle(2.0)
                continue

            device.settle(1.0)
            try:
                _type_location()
            except Exception:
                pass

        return False

    def _find_marker_index():
        idx = _find_any(
            [
                {"text": "Marker"},
                {"description": "Marker"},
            ]
        )
        if idx is not None:
            return idx

        try:
            elements = device.elements()
        except Exception:
            elements = []

        for e in elements:
            text = str(e.get("text") or "").strip()
            desc = str(e.get("description") or "").strip()
            if text.lower() == "marker" or desc.lower() == "marker":
                return e.get("index")

        candidates = []
        for e in elements:
            hay = (
                f"{e.get('text') or ''} "
                f"{e.get('hint') or ''} "
                f"{e.get('description') or ''}"
            ).lower()
            if "marker" in hay and "favorite" not in hay and "star" not in hay:
                candidates.append(e)

        if candidates:
            for e in candidates:
                if e.get("clickable"):
                    return e.get("index")
            return candidates[0].get("index")

        return _find_any([{"contains": "Marker"}, {"contains": "marker"}])

    def _open_marker():
        for _ in range(3):
            idx = _find_marker_index()
            if idx is not None:
                try:
                    device.click(index=idx)
                    device.settle(2.0)
                    return True
                except Exception:
                    pass

            try:
                device.scroll("down")
            except Exception:
                pass
            device.settle(1.0)

            idx = _find_marker_index()
            if idx is not None:
                try:
                    device.click(index=idx)
                    device.settle(2.0)
                    return True
                except Exception:
                    pass

            try:
                device.scroll("up")
            except Exception:
                pass
            device.settle(1.0)

        return False

    try:
        device.open_app(str(binding.get("app_name") or "OsmAnd"))
    except Exception:
        pass
    device.settle(2.0)

    for _ in range(4):
        try:
            _ensure_search_screen()
            _clear_search()
            _type_location()

            if _select_first_result():
                if _open_marker():
                    return True
        except Exception:
            pass

        try:
            device.navigate_back()
        except Exception:
            pass
        device.settle(1.0)

    raise RuntimeError("Failed to add location marker in OsmAnd")
