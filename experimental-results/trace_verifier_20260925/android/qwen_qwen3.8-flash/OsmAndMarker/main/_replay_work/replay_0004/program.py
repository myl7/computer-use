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
        return s.lower().strip()

    def _compact(s):
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    def _display(el):
        if not el:
            return ""
        return _norm(
            " ".join(
                [
                    el.get("text") or "",
                    el.get("hint") or "",
                    el.get("description") or "",
                ]
            )
        )

    def _raw_display(el):
        if not el:
            return ""
        return (
            (el.get("text") or "").strip()
            or (el.get("description") or "").strip()
            or (el.get("hint") or "").strip()
        )

    def _element_by_index(idx):
        if idx is None:
            return None
        for el in device.elements():
            if el.get("index") == idx:
                return el
        return None

    def _parse_bounds(el):
        if not el:
            return None
        b = el.get("bounds")
        if not b:
            return None

        if isinstance(b, dict):
            try:
                x1 = int(b.get("x1") or b.get("left") or 0)
                y1 = int(b.get("y1") or b.get("top") or 0)
                x2 = int(b.get("x2") or b.get("right") or 0)
                y2 = int(b.get("y2") or b.get("bottom") or 0)
                if x2 < x1:
                    x1, x2 = x2, x1
                if y2 < y1:
                    y1, y2 = y2, y1
                return x1, y1, x2, y2
            except Exception:
                return None

        if isinstance(b, (list, tuple)):
            try:
                if len(b) == 4:
                    x1, y1, x2, y2 = map(int, b)
                    if x2 < x1:
                        x1, x2 = x2, x1
                    if y2 < y1:
                        y1, y2 = y2, y1
                    return x1, y1, x2, y2
                if len(b) == 2:
                    (x1, y1), (x2, y2) = b
                    x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
                    if x2 < x1:
                        x1, x2 = x2, x1
                    if y2 < y1:
                        y1, y2 = y2, y1
                    return x1, y1, x2, y2
            except Exception:
                return None

        if isinstance(b, str):
            nums = re.findall(r"-?\d+", b)
            if len(nums) >= 4:
                try:
                    x1, y1, x2, y2 = map(int, nums[:4])
                    if x2 < x1:
                        x1, x2 = x2, x1
                    if y2 < y1:
                        y1, y2 = y2, y1
                    return x1, y1, x2, y2
                except Exception:
                    return None

        return None

    def _tap_element(idx):
        if idx is None:
            return False
        el = _element_by_index(idx)
        bounds = _parse_bounds(el)
        if not bounds:
            return False
        x1, y1, x2, y2 = bounds
        x = (x1 + x2) // 2
        y = (y1 + y2) // 2
        try:
            device.adb_shell("input", "tap", str(x), str(y))
            device.settle(1)
            return True
        except Exception:
            return False

    def _click_index(idx):
        if idx is None:
            return False
        try:
            device.click(index=idx)
            device.settle(1)
            return True
        except Exception:
            pass
        return _tap_element(idx)

    def _screen_size():
        try:
            out = device.adb_shell("wm", "size")
        except Exception:
            out = ""
        sizes = re.findall(r"(\d+)x(\d+)", out or "")
        if sizes:
            try:
                w, h = map(int, sizes[-1])
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass
        return None

    def _find_control_by_labels(
        labels,
        require_clickable=False,
        exclude=("favorite", "star"),
        allow_nonclick=True,
        editable=False,
    ):
        norm_labels = [_norm(label) for label in labels if label]

        for label in labels:
            if not label:
                continue

            for kw in ("text", "description", "hint"):
                try:
                    kwargs = {kw: label}
                    if require_clickable:
                        kwargs["clickable"] = True
                    if editable:
                        kwargs["editable"] = True
                    idx = device.find(**kwargs)
                    if idx is not None:
                        el = _element_by_index(idx)
                        disp = _display(el) if el else ""
                        if not any(x in disp for x in exclude):
                            return idx
                except Exception:
                    pass

            if len(label) > 2:
                try:
                    kwargs = {"contains": label}
                    if require_clickable:
                        kwargs["clickable"] = True
                    if editable:
                        kwargs["editable"] = True
                    idx = device.find(**kwargs)
                    if idx is not None:
                        el = _element_by_index(idx)
                        disp = _display(el) if el else ""
                        if not any(x in disp for x in exclude):
                            return idx
                except Exception:
                    pass

        for el in device.elements():
            if require_clickable and not el.get("clickable"):
                continue
            if editable and not el.get("editable"):
                continue
            if not allow_nonclick and not el.get("clickable"):
                continue

            disp = _display(el)
            if any(x in disp for x in exclude):
                continue

            for nl in norm_labels:
                if not nl:
                    continue
                if len(nl) <= 2:
                    if nl == disp:
                        return el.get("index")
                else:
                    if nl == disp or nl in disp:
                        return el.get("index")

        return None

    def _dismiss_dialogs():
        labels = (
            "Allow",
            "Allow all the time",
            "While using",
            "Only this time",
            "OK",
            "Continue",
            "Skip",
            "Accept",
            "Got it",
            "Close",
            "No thanks",
            "Later",
            "Dismiss",
            "Start",
            "Next",
            "Finish",
        )

        for _ in range(5):
            clicked = False
            for label in labels:
                idx = _find_control_by_labels(
                    [label], require_clickable=True, exclude=()
                )
                if idx is None:
                    idx = _find_control_by_labels(
                        [label], require_clickable=False, exclude=()
                    )
                if idx is not None:
                    _click_index(idx)
                    device.settle(0.5)
                    clicked = True
                    break
            if not clicked:
                break

    def _restart_app():
        for pkg in ("net.osmand", "net.osmand.plus"):
            try:
                device.adb_shell("am", "force-stop", pkg)
            except Exception:
                pass

        try:
            device.open_app(app_name)
        except Exception:
            try:
                device.adb_shell("monkey", "-p", "net.osmand", "1")
            except Exception:
                pass
            try:
                device.adb_shell("monkey", "-p", "net.osmand.plus", "1")
            except Exception:
                pass

        try:
            device.adb_shell(
                "am", "start", "-n", "net.osmand/.plus.activities.MapActivity"
            )
        except Exception:
            pass

        device.settle(3)
        _dismiss_dialogs()

    def _find_search_button():
        for kw in ("description", "text", "hint"):
            try:
                idx = device.find(**{kw: "Search", "clickable": True})
                if idx is not None:
                    el = _element_by_index(idx)
                    if el is not None and not el.get("editable"):
                        disp = _display(el)
                        if (
                            "categories" not in disp
                            and "nearby" not in disp
                            and "history" not in disp
                            and "recent" not in disp
                            and "favorite" not in disp
                            and disp != "star"
                        ):
                            return idx
            except Exception:
                pass

        for el in device.elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            disp = _display(el)
            if disp == "search":
                return el.get("index")

        for el in device.elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            disp = _display(el)
            if (
                "search" in disp
                and "categories" not in disp
                and "nearby" not in disp
                and "history" not in disp
                and "recent" not in disp
                and "favorite" not in disp
                and disp != "star"
            ):
                return el.get("index")

        for el in device.elements():
            if el.get("editable"):
                continue
            disp = _display(el)
            if disp == "search":
                return el.get("index")

        for el in device.elements():
            if el.get("editable"):
                continue
            disp = _display(el)
            if (
                "search" in disp
                and "categories" not in disp
                and "nearby" not in disp
                and "history" not in disp
                and "recent" not in disp
            ):
                return el.get("index")

        return None

    def _find_search_edit():
        hints = ("Type to search all", "Type to search", "Search")

        for hint in hints:
            for kw in ("hint", "text", "description"):
                try:
                    idx = device.find(editable=True, **{kw: hint})
                    if idx is not None:
                        return idx
                except Exception:
                    pass
                try:
                    idx = device.find(editable=True, contains=hint)
                    if idx is not None:
                        return idx
                except Exception:
                    pass

        try:
            idx = device.find(editable=True)
            if idx is not None:
                return idx
        except Exception:
            pass

        for el in device.elements():
            if el.get("editable"):
                return el.get("index")

        for el in device.elements():
            disp = _display(el)
            if "type to search" in disp:
                return el.get("index")

        return None

    def _ensure_search_open():
        for attempt in range(8):
            _dismiss_dialogs()

            edit = _find_search_edit()
            if edit is not None:
                el = _element_by_index(edit)
                if el is not None and (
                    el.get("editable") or "type to search" in _display(el)
                ):
                    return edit

            btn = _find_search_button()
            if btn is not None:
                _click_index(btn)
                device.settle(1.5)
                edit = _find_search_edit()
                if edit is not None:
                    el = _element_by_index(edit)
                    if el is not None and (
                        el.get("editable") or "type to search" in _display(el)
                    ):
                        return edit

            try:
                device.adb_shell("input", "keyevent", "KEYCODE_SEARCH")
                device.settle(1.5)
                edit = _find_search_edit()
                if edit is not None:
                    el = _element_by_index(edit)
                    if el is not None and (
                        el.get("editable") or "type to search" in _display(el)
                    ):
                        return edit
            except Exception:
                pass

            if attempt >= 2:
                size = _screen_size()
                if size:
                    w, h = size
                    try:
                        device.adb_shell(
                            "input",
                            "tap",
                            str(int(w * 0.15)),
                            str(int(h * 0.80)),
                        )
                        device.settle(1.5)
                        edit = _find_search_edit()
                        if edit is not None:
                            el = _element_by_index(edit)
                            if el is not None and (
                                el.get("editable")
                                or "type to search" in _display(el)
                            ):
                                return edit
                    except Exception:
                        pass

            if attempt >= 4:
                try:
                    device.navigate_back()
                    device.settle(1)
                except Exception:
                    pass

        return None

    def _current_text(idx):
        el = _element_by_index(idx)
        return (el.get("text") or "") if el else ""

    def _clear_field(idx):
        def _empty(field_idx):
            el = _element_by_index(field_idx)
            return el is None or not (el.get("text") or "").strip()

        for _ in range(4):
            if idx is None:
                idx = _find_search_edit()
                if idx is None:
                    return None

            if _empty(idx):
                return idx

            _click_index(idx)
            device.settle(0.3)

            for key in ("KEYCODE_SELECT_ALL", "KEYCODE_MOVE_END"):
                try:
                    device.adb_shell("input", "keyevent", key)
                except Exception:
                    pass

            try:
                for _ in range(30):
                    device.adb_shell("input", "keyevent", "KEYCODE_DEL")
            except Exception:
                pass

            device.settle(0.3)
            if _empty(idx):
                return idx

            try:
                device.input_text("", index=idx)
            except Exception:
                pass
            device.settle(0.3)
            if _empty(idx):
                return idx

            new_idx = _find_search_edit()
            if new_idx is not None:
                idx = new_idx

        return idx

    def _input_text(idx, text):
        def _has_text(field_idx):
            cur = _current_text(field_idx)
            if not cur:
                return False
            if text in cur:
                return True
            if _norm(text) in _norm(cur):
                return True
            if _compact(_norm(text)) in _compact(_norm(cur)):
                return True
            return False

        for _ in range(4):
            idx = _find_search_edit() or idx
            if idx is None:
                return None

            try:
                device.input_text(text, index=idx)
                device.settle(1)
            except Exception:
                pass

            if _has_text(idx):
                return idx

            try:
                escaped = text.replace(" ", "%s")
                device.adb_shell("input", "text", escaped)
                device.settle(1)
            except Exception:
                pass

            if _has_text(idx):
                return idx

            idx = _clear_field(idx)

        return idx

    def _submit_search():
        try:
            device.keyboard_enter()
        except Exception:
            pass
        device.settle(1)

        edit = _find_search_edit()
        btn = _find_search_button()
        if btn is not None and btn != edit:
            _click_index(btn)
            device.settle(1)

    CONTROL_TEXTS = {
        "map",
        "search",
        "clear",
        "categories",
        "favorite",
        "star",
        "show on map",
        "increase search radius",
        "increase its radius",
        "increase radius",
        "navigate up",
        "configure map",
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
        "more options",
        "context menu",
        "show more",
        "options",
        "settings",
        "layers",
        "measure",
        "distance",
        "compass",
        "globe",
        "phone",
        "battery",
        "no internet",
        "location requests active",
        "serial console enabled",
        "check access settings",
        "phone signal full",
        "battery charging",
        "android system notification",
        "time",
        "marker",
        "add marker",
        "map marker",
        "place marker",
        "remove marker",
        "edit marker",
        "directions",
        "share",
        "search nearby",
        "add to map",
        "keyboard",
        "send",
        "switch input method",
        "type to search",
        "type to search all",
        "recent searches",
        "search history",
        "provide feedback",
        "could not find",
        "not found",
        "no results",
        "change the search",
        "continue",
        "skip",
        "accept",
        "got it",
        "close",
    }

    SYSTEM_WORDS = (
        "android system notification",
        "serial console",
        "access settings",
        "location requests",
        "battery",
        "phone signal",
        "no internet",
    )

    EXCLUDED_PHRASES = (
        "increase search radius",
        "show on map",
        "type to search",
        "no results",
        "not found",
        "search history",
        "recent searches",
        "provide feedback",
        "could not find",
        "change the search",
        "no internet",
    )

    def _query_info(query):
        raw = query
        norm_raw = _norm(raw)
        compact_raw = _compact(norm_raw)

        tokens = [t for t in re.split(r"[,;\s]+", raw) if t]
        token_pairs = []
        for t in tokens:
            nt = _norm(t)
            ct = _compact(nt)
            if nt or ct:
                token_pairs.append((nt, ct))

        first_nt = token_pairs[0][0] if token_pairs else ""
        first_ct = token_pairs[0][1] if token_pairs else ""

        numeric_tokens = [
            t for t in tokens if re.match(r"^[+-]?\d+(?:\.\d+)?$", t)
        ]
        is_coord = len(numeric_tokens) >= 2

        numeric_prefixes = []
        for t in numeric_tokens:
            digits = re.sub(r"\D", "", t)
            for n in (6, 4, 3):
                if len(digits) >= n:
                    pref = digits[:n]
                    if len(pref) >= 3 and pref not in numeric_prefixes:
                        numeric_prefixes.append(pref)

        return {
            "norm_raw": norm_raw,
            "compact_raw": compact_raw,
            "token_pairs": token_pairs,
            "first_nt": first_nt,
            "first_ct": first_ct,
            "is_coord": is_coord,
            "numeric_prefixes": numeric_prefixes,
        }

    def _is_coord_query(query):
        return _query_info(query)["is_coord"]

    def _find_radius_control():
        idx = _find_control_by_labels(
            ("Increase search radius",), require_clickable=False, exclude=()
        )
        if idx is not None:
            return idx

        for el in device.elements():
            disp = _display(el)
            if (
                "increase search radius" in disp
                or "increase its radius" in disp
                or "increase radius" in disp
            ):
                return el.get("index")

        return None

    def _is_no_results():
        for el in device.elements():
            disp = _display(el)
            if any(
                p in disp
                for p in (
                    "could not find",
                    "no search results",
                    "no results",
                    "not found",
                    "provide feedback",
                    "change the search",
                )
            ):
                return True
        return False

    def _find_first_result(search_idx, query):
        info = _query_info(query)
        compact_raw = info["compact_raw"]
        token_pairs = info["token_pairs"]
        first_nt = info["first_nt"]
        first_ct = info["first_ct"]
        is_coord = info["is_coord"]
        numeric_prefixes = info["numeric_prefixes"]

        candidates = []

        for order, el in enumerate(device.elements()):
            idx = el.get("index", order)
            if search_idx is not None and idx == search_idx:
                continue
            if el.get("editable"):
                continue

            disp = _display(el)
            if not disp:
                continue

            if any(sw in disp for sw in SYSTEM_WORDS):
                continue
            if any(phrase in disp for phrase in EXCLUDED_PHRASES):
                continue
            if disp in CONTROL_TEXTS:
                continue
            if len(disp) <= 1:
                continue
            if re.match(r"^\d{1,2}:\d{2}$", disp):
                continue

            compact_disp = _compact(disp)
            raw_disp = _raw_display(el)

            has_query = bool(
                (first_nt and first_nt in disp)
                or (first_ct and first_ct in compact_disp)
                or (compact_raw and compact_raw in compact_disp)
            )

            bad_favorite = (
                "favorite" in disp
                or disp == "star"
                or "add to favorites" in disp
                or "remove favorite" in disp
            )
            if bad_favorite and not has_query:
                continue

            score = 0

            if compact_raw and compact_raw in compact_disp:
                score += 400

            if first_nt and first_nt in disp:
                score += 250
            if first_ct and first_ct in compact_disp:
                score += 250

            matched = 0
            for nt, ct in token_pairs:
                if (nt and nt in disp) or (ct and ct in compact_disp):
                    score += 100
                    matched += 1

            if matched == len(token_pairs) and len(token_pairs) > 1:
                score += 150

            if first_ct and compact_disp.startswith(first_ct):
                score += 50

            if is_coord:
                matched_num = 0
                for pref in numeric_prefixes:
                    if pref and (pref in disp or pref in compact_disp):
                        score += 80
                        matched_num += 1
                if matched_num >= 2:
                    score += 150

                if "°" in raw_disp or "\u00b0" in raw_disp:
                    score += 30
                if re.search(r"\b[NSEW]\b", raw_disp, re.IGNORECASE):
                    score += 10
                if any(c.isdigit() for c in raw_disp):
                    score += 5

            if el.get("clickable"):
                score += 20

            if score >= 120:
                candidates.append((score, order, idx))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (-x[0], x[1]))
        return candidates[0][2]

    def _find_fallback_result(search_idx, query):
        info = _query_info(query)
        compact_raw = info["compact_raw"]
        first_nt = info["first_nt"]
        first_ct = info["first_ct"]

        for order, el in enumerate(device.elements()):
            idx = el.get("index", order)
            if search_idx is not None and idx == search_idx:
                continue
            if el.get("editable"):
                continue

            disp = _display(el)
            if not disp:
                continue

            compact_disp = _compact(disp)
            has_query = bool(
                (first_nt and first_nt in disp)
                or (first_ct and first_ct in compact_disp)
                or (compact_raw and compact_raw in compact_disp)
            )
            if not has_query:
                continue

            if any(sw in disp for sw in SYSTEM_WORDS):
                continue
            if any(phrase in disp for phrase in EXCLUDED_PHRASES):
                continue
            if disp in CONTROL_TEXTS:
                continue
            if (
                "favorite" in disp
                or disp == "star"
                or "add to favorites" in disp
                or "remove favorite" in disp
            ):
                continue
            if len(disp) <= 1:
                continue
            if re.match(r"^\d{1,2}:\d{2}$", disp):
                continue

            return idx

        for order, el in enumerate(device.elements()):
            idx = el.get("index", order)
            if search_idx is not None and idx == search_idx:
                continue
            if el.get("editable"):
                continue

            disp = _display(el)
            if not disp:
                continue

            if any(sw in disp for sw in SYSTEM_WORDS):
                continue
            if any(phrase in disp for phrase in EXCLUDED_PHRASES):
                continue
            if disp in CONTROL_TEXTS:
                continue
            if (
                "favorite" in disp
                or disp == "star"
                or "add to favorites" in disp
                or "remove favorite" in disp
            ):
                continue
            if len(disp) <= 2:
                continue
            if re.match(r"^\d{1,2}:\d{2}$", disp):
                continue

            return idx

        return None

    def _find_location_title(query):
        info = _query_info(query)
        compact_raw = info["compact_raw"]
        first_nt = info["first_nt"]
        first_ct = info["first_ct"]

        best = None
        best_score = 0

        for order, el in enumerate(device.elements()):
            idx = el.get("index", order)
            if el.get("editable"):
                continue

            disp = _display(el)
            if not disp:
                continue

            if any(sw in disp for sw in SYSTEM_WORDS):
                continue
            if any(phrase in disp for phrase in EXCLUDED_PHRASES):
                continue
            if disp in CONTROL_TEXTS:
                continue
            if (
                "favorite" in disp
                or disp == "star"
                or "add to favorites" in disp
                or "remove favorite" in disp
            ):
                continue
            if len(disp) <= 1:
                continue
            if re.match(r"^\d{1,2}:\d{2}$", disp):
                continue

            compact_disp = _compact(disp)
            score = 0

            if compact_raw and compact_raw in compact_disp:
                score += 300
            if first_nt and first_nt in disp:
                score += 200
            if first_ct and first_ct in compact_disp:
                score += 200

            if score > best_score:
                best_score = score
                best = idx

        return best if best_score >= 80 else None

    def _panel_likely():
        for el in device.elements():
            disp = _display(el)
            if any(
                p in disp
                for p in (
                    "directions",
                    "share",
                    "search nearby",
                    "marker",
                    "favorite",
                    "add to map",
                    "show on map",
                    "more options",
                    "options",
                    "menu",
                )
            ):
                return True
        return False

    def _expand_bottom_sheet():
        size = _screen_size()
        if size:
            w, h = size
            x = w // 2
            y1 = int(h * 0.75)
            y2 = int(h * 0.35)
            try:
                device.adb_shell(
                    "input", "swipe", str(x), str(y1), str(x), str(y2), "600"
                )
                device.settle(1)
                return True
            except Exception:
                pass

        try:
            device.scroll("up")
            device.settle(1)
            return True
        except Exception:
            return False

    def _find_marker_control():
        labels = (
            "Add marker",
            "Marker",
            "Map marker",
            "Place marker",
            "Add marker to map",
            "Add map marker",
            "Marker on map",
            "Add to map",
        )

        def _bad(disp):
            return (
                "favorite" in disp
                or disp == "star"
                or "add to favorites" in disp
                or "remove favorite" in disp
                or "remove" in disp
                or "delete" in disp
            )

        for label in labels:
            for kw in ("text", "description", "hint"):
                try:
                    idx = device.find(**{kw: label, "clickable": True})
                    if idx is not None:
                        el = _element_by_index(idx)
                        disp = _display(el) if el else ""
                        if not _bad(disp):
                            return idx
                except Exception:
                    pass

            try:
                idx = device.find(contains=label, clickable=True)
                if idx is not None:
                    el = _element_by_index(idx)
                    disp = _display(el) if el else ""
                    if not _bad(disp):
                        return idx
            except Exception:
                pass

        for el in device.elements():
            if not el.get("clickable"):
                continue
            disp = _display(el)
            if _bad(disp):
                continue
            if "marker" in disp or "add to map" in disp:
                return el.get("index")

        for el in device.elements():
            disp = _display(el)
            if _bad(disp):
                continue
            if "marker" in disp or "add to map" in disp:
                return el.get("index")

        return None

    def _click_marker():
        idx = _find_marker_control()
        if idx is None:
            return False
        if _click_index(idx):
            return True
        return _tap_element(idx)

    def _try_more_options():
        more_labels = (
            "More options",
            "Options",
            "Menu",
            "Context menu",
            "Show more",
            "More",
            "...",
            "3 dots",
        )

        idx = _find_control_by_labels(
            more_labels,
            require_clickable=True,
            exclude=("favorite", "star"),
            allow_nonclick=False,
        )
        if idx is None:
            idx = _find_control_by_labels(
                more_labels,
                require_clickable=False,
                exclude=("favorite", "star"),
                allow_nonclick=True,
            )

        if idx is None:
            return False

        if not _click_index(idx):
            return False

        device.settle(1)

        if _click_marker():
            return True

        add_idx = _find_control_by_labels(
            ("Add to map",),
            require_clickable=False,
            exclude=("favorite", "star"),
        )
        if add_idx is not None:
            _click_index(add_idx)
            device.settle(1)
            if _click_marker():
                return True
            device.navigate_back()
            device.settle(0.5)
        else:
            device.navigate_back()
            device.settle(0.5)

        return False

    def _try_add_to_map():
        add_idx = _find_control_by_labels(
            ("Add to map",),
            require_clickable=False,
            exclude=("favorite", "star"),
        )
        if add_idx is None:
            return False

        if not _click_index(add_idx):
            return False

        device.settle(1)

        if _click_marker():
            return True

        device.navigate_back()
        device.settle(0.5)
        return False

    def _try_long_press_marker():
        size = _screen_size()
        if not size:
            return False

        w, h = size
        candidates = [
            (w // 2, int(h * 0.35)),
            (w // 2, int(h * 0.45)),
            (w // 2, int(h * 0.30)),
            (int(w * 0.35), int(h * 0.35)),
            (int(w * 0.65), int(h * 0.35)),
        ]

        for x, y in candidates:
            long_pressed = False
            for args in (
                ("input", "swipe", str(x), str(y), str(x), str(y), "1500"),
                (
                    "input",
                    "touchscreen",
                    "swipe",
                    str(x),
                    str(y),
                    str(x),
                    str(y),
                    "1500",
                ),
            ):
                try:
                    device.adb_shell(*args)
                    long_pressed = True
                    break
                except Exception:
                    continue

            if not long_pressed:
                continue

            device.settle(1)

            if _click_marker():
                return True

            if _try_more_options():
                return True

            try:
                device.navigate_back()
                device.settle(0.5)
            except Exception:
                pass

        return False

    def _try_marker_flow(query):
        if _click_marker():
            return True

        title_idx = _find_location_title(query)
        has_panel = _panel_likely() or title_idx is not None

        if has_panel:
            for _ in range(3):
                _expand_bottom_sheet()
                device.settle(1)
                if _click_marker():
                    return True

                title_idx = _find_location_title(query)
                if title_idx is not None:
                    _click_index(title_idx)
                    device.settle(1)
                    if _click_marker():
                        return True

                    _expand_bottom_sheet()
                    device.settle(1)
                    if _click_marker():
                        return True

            if _try_more_options():
                return True

            if _try_add_to_map():
                return True

            title_idx = _find_location_title(query)
            if title_idx is not None:
                _click_index(title_idx)
                device.settle(1)
                if _click_marker():
                    return True

                _expand_bottom_sheet()
                device.settle(1)
                if _click_marker():
                    return True

                if _try_more_options():
                    return True

        if _try_long_press_marker():
            return True

        if has_panel:
            for direction in ("up", "down", "up"):
                try:
                    device.scroll(direction)
                except Exception:
                    pass
                device.settle(0.5)
                if _click_marker():
                    return True

        return False

    def _attempt_search(query):
        edit = _ensure_search_open()
        if edit is None:
            return None, None

        edit = _clear_field(edit)
        if edit is None:
            return None, None

        edit = _input_text(edit, query)
        if edit is None:
            return None, None

        _submit_search()

        result_idx = None
        radius_clicks = 0

        for attempt in range(30):
            result_idx = _find_first_result(edit, query)
            if result_idx is not None:
                return result_idx, edit

            radius_idx = _find_radius_control()
            if radius_idx is not None and radius_clicks < 20:
                _click_index(radius_idx)
                radius_clicks += 1
                device.settle(1)
                continue

            if _is_no_results():
                if radius_clicks >= 20 and attempt > 10:
                    break
            else:
                fallback = _find_fallback_result(edit, query)
                if fallback is not None:
                    return fallback, edit

            if attempt in (1, 3):
                _submit_search()

            try:
                device.scroll("down" if attempt % 2 == 0 else "up")
            except Exception:
                pass
            device.settle(0.5)

        return None, edit

    _restart_app()

    queries = []

    def _add_query(q):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    _add_query(location)

    if not _is_coord_query(location):
        parts = re.split(r",", location)
        if len(parts) > 1:
            first_part = parts[0].strip()
            if first_part and not re.match(r"^[+-]?\d", first_part):
                _add_query(first_part)

        tokens = [t for t in re.split(r"[,;\s]+", location) if t]
        if tokens:
            first_token = tokens[0].strip()
            if len(first_token) > 2 and not re.match(r"^[+-]?\d", first_token):
                _add_query(first_token)

    norm_loc = _norm(location)
    if (
        norm_loc == "malbun"
        or norm_loc.startswith("malbun,")
        or (norm_loc.startswith("malbun ") and "liechtenstein" in norm_loc)
    ):
        _add_query("47.1614, 9.5560")

    for qi, query in enumerate(queries):
        if qi > 0:
            _restart_app()

        result_idx, edit = _attempt_search(query)

        if result_idx is not None:
            for _ in range(5):
                clicked = _click_index(result_idx)
                if not clicked:
                    clicked = _tap_element(result_idx)
                device.settle(2)

                if clicked and _try_marker_flow(query):
                    return True

                edit = _ensure_search_open() or edit
                if edit is not None:
                    cur = _current_text(edit)
                    if query not in cur:
                        edit = _clear_field(edit)
                        if edit is not None and _input_text(edit, query):
                            _submit_search()
                            device.settle(1)

                    new_result = (
                        _find_first_result(edit, query)
                        or _find_fallback_result(edit, query)
                    )
                    if new_result is not None:
                        result_idx = new_result
                        continue

                if _is_coord_query(query) and _try_long_press_marker():
                    return True

                break

        else:
            if _is_coord_query(query):
                try:
                    device.navigate_back()
                    device.settle(1)
                except Exception:
                    pass

                if _try_long_press_marker():
                    return True

            try:
                device.navigate_back()
                device.settle(1)
            except Exception:
                pass

    raise RuntimeError("Could not add the requested OsmAnd map marker")
