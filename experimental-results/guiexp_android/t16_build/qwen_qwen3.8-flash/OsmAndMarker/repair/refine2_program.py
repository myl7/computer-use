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
    from xml.etree import ElementTree

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

    CONTROL_EXACT = {
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

    BAD_CONTAINS = (
        "increase search radius",
        "show on map",
        "type to search",
        "no results",
        "not found",
        "search categories",
        "clear text",
        "clear search",
    )

    POPUP_LABELS = (
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
    )

    SEARCH_BUTTON_BAD = (
        "type to search",
        "search categories",
        "clear search",
        "clear text",
        "increase search radius",
    )

    ACTION_WORDS = (
        "navigate",
        "directions",
        "share",
        "details",
        "add to map",
    )

    MARKER_LABELS = (
        "Add marker",
        "Create marker",
        "Map marker",
        "Place marker",
        "Marker on map",
        "Add map marker",
        "Marker",
    )

    MARKER_NORM_LABELS = tuple(_norm(x) for x in MARKER_LABELS)
    MARKER_STRICT_NORM_LABELS = tuple(
        _norm(x) for x in MARKER_LABELS if _norm(x) != "marker"
    )

    def _elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def _find(**kwargs):
        try:
            return device.find(**kwargs)
        except Exception:
            return None

    def _element_by_index(idx):
        if idx is None or idx < 0:
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

    def _click_idx(idx, wait=1):
        if idx is None or idx < 0:
            return False
        try:
            device.click(index=idx)
            device.settle(wait)
            return True
        except Exception:
            return False

    def _input_text(text, idx=None):
        if idx is not None and idx >= 0:
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

    def _adb(*args):
        try:
            out = device.adb_shell(*args)
            if isinstance(out, bytes):
                out = out.decode("utf-8", "ignore")
            return out or ""
        except Exception:
            return ""

    def _screen_size():
        out = _adb("wm", "size")
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 1080, 2400

    def _adb_tap(x, y):
        try:
            device.adb_shell("input", "tap", str(int(x)), str(int(y)))
            device.settle(1)
            return True
        except Exception:
            return False

    def _adb_swipe(x1, y1, x2, y2, duration=500):
        try:
            device.adb_shell(
                "input",
                "swipe",
                str(int(x1)),
                str(int(y1)),
                str(int(x2)),
                str(int(y2)),
                str(int(duration)),
            )
            device.settle(1)
            return True
        except Exception:
            return False

    def _dump_nodes():
        try:
            _adb("uiautomator", "dump", "/sdcard/osmand_ui.xml")
            xml = _adb("cat", "/sdcard/osmand_ui.xml")
            if not xml:
                return []
            start = xml.find("<?xml")
            if start < 0:
                start = xml.find("<hierarchy")
            if start < 0:
                return []
            xml = xml[start:]
            root = ElementTree.fromstring(xml)
        except Exception:
            return []

        nodes = []
        for n in root.iter("node"):
            a = n.attrib
            bounds = a.get("bounds", "")
            center = None
            m = re.findall(r"\d+", bounds)
            if len(m) == 4:
                x1, y1, x2, y2 = map(int, m)
                if x2 > x1 and y2 > y1:
                    center = ((x1 + x2) // 2, (y1 + y2) // 2)
            nodes.append(
                {
                    "resource_id": a.get("resource-id", "") or "",
                    "text": a.get("text", "") or "",
                    "desc": a.get("content-desc", "") or "",
                    "class": a.get("class", "") or "",
                    "bounds": bounds,
                    "center": center,
                    "clickable": str(a.get("clickable", "false")).lower() == "true",
                }
            )
        return nodes

    def _node_blob(node):
        return _norm(
            " ".join(
                [
                    node.get("text") or "",
                    node.get("desc") or "",
                    node.get("resource_id") or "",
                    node.get("class") or "",
                ]
            )
        )

    def _node_compact(node):
        return _compact(
            " ".join([node.get("text") or "", node.get("desc") or ""])
        )

    def _node_y(node):
        c = node.get("center")
        return c[1] if c else 999999

    def _tap_node(node):
        if not node:
            return False
        c = node.get("center")
        if not c:
            return False
        return _adb_tap(c[0], c[1])

    def _is_editable_idx(idx):
        if idx is None or idx < 0:
            return False
        el = _element_by_index(idx)
        if el is None:
            return False
        if el.get("editable"):
            return True
        return "EditText" in (el.get("class_name") or "")

    def _find_search_edit_idx():
        for kwargs in (
            {"hint": "Type to search all", "editable": True},
            {"contains": "Type to search all", "editable": True},
            {"text": "Type to search all", "editable": True},
            {"editable": True, "contains": "Search"},
            {"editable": True, "contains": "search"},
            {"editable": True, "hint": "Search"},
            {"editable": True, "clickable": True},
            {"editable": True},
        ):
            idx = _find(**kwargs)
            if idx is not None and _is_editable_idx(idx):
                return idx

        best = None
        for el in _elements():
            cls = el.get("class_name") or ""
            if not (el.get("editable") or "EditText" in cls):
                continue
            blob = _blob(el)
            if "type to search all" in blob or "search" in blob:
                return el.get("index")
            if best is None:
                best = el.get("index")
        return best

    def _find_search_button_idx():
        for kwargs in (
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"hint": "Search", "clickable": True},
            {"description": "Search"},
            {"text": "Search"},
            {"contains": "Search", "clickable": True},
            {"contains": "Search"},
        ):
            idx = _find(**kwargs)
            if idx is None or _is_editable_idx(idx):
                continue
            el = _element_by_index(idx)
            if el is None:
                continue
            blob = _blob(el)
            if any(b in blob for b in SEARCH_BUTTON_BAD):
                continue
            return idx

        for el in _elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            blob = _blob(el)
            if "search" in blob and not any(b in blob for b in SEARCH_BUTTON_BAD):
                return el.get("index")
        return None

    def _is_search_button_node(node):
        cls = node.get("class") or ""
        if "EditText" in cls:
            return False
        if not node.get("clickable"):
            return False
        blob = _node_blob(node)
        if any(b in blob for b in SEARCH_BUTTON_BAD):
            return False
        rid = (node.get("resource_id") or "").lower()
        return "search" in rid or "search" in blob or "magnifier" in blob

    def _find_search_edit_node(nodes=None):
        if nodes is None:
            nodes = _dump_nodes()
        for node in nodes:
            cls = node.get("class") or ""
            rid = (node.get("resource_id") or "").lower()
            blob = _node_blob(node)
            if (
                "EditText" in cls
                or "edittext" in cls.lower()
                or "search_text" in rid
                or "type to search all" in blob
            ):
                return node
        for node in nodes:
            if "EditText" in (node.get("class") or ""):
                return node
        return None

    def _search_edit_present():
        if _find_search_edit_idx() is not None:
            return True
        return _find_search_edit_node() is not None

    def _dismiss_popups():
        popup_norms = [_norm(x) for x in POPUP_LABELS]
        for _ in range(3):
            clicked = False
            for label in POPUP_LABELS:
                idx = _find(text=label, clickable=True)
                if idx is None:
                    idx = _find(description=label, clickable=True)
                if idx is None:
                    idx = _find(contains=label, clickable=True)
                if idx is not None and _click_idx(idx, 1):
                    clicked = True
                    break

            if not clicked:
                for el in _elements():
                    if not el.get("clickable"):
                        continue
                    blob = _blob(el)
                    if any(lbl in blob for lbl in popup_norms):
                        if _click_idx(el.get("index"), 1):
                            clicked = True
                            break

            if not clicked:
                break

    def _open_search():
        for _ in range(8):
            _dismiss_popups()

            idx = _find_search_edit_idx()
            if idx is not None:
                return idx

            btn = _find_search_button_idx()
            if btn is not None and _click_idx(btn, 1):
                idx = _find_search_edit_idx()
                if idx is not None:
                    return idx

            try:
                device.adb_shell("input", "keyevent", "KEYCODE_SEARCH")
                device.settle(1)
            except Exception:
                pass

            idx = _find_search_edit_idx()
            if idx is not None:
                return idx

            nodes = _dump_nodes()

            for node in nodes:
                if _is_search_button_node(node):
                    if _tap_node(node):
                        idx = _find_search_edit_idx()
                        if idx is not None:
                            return idx
                        edit_node = _find_search_edit_node()
                        if edit_node is not None:
                            _tap_node(edit_node)
                            device.settle(1)
                            idx = _find_search_edit_idx()
                            if idx is not None:
                                return idx
                            return -1

            edit_node = _find_search_edit_node(nodes)
            if edit_node is not None:
                _tap_node(edit_node)
                device.settle(1)
                idx = _find_search_edit_idx()
                if idx is not None:
                    return idx
                return -1

            w, h = _screen_size()
            candidates = [
                (0.15 * w, 0.88 * h),
                (0.50 * w, 0.90 * h),
                (0.12 * w, 0.92 * h),
                (0.25 * w, 0.88 * h),
                (0.50 * w, 0.82 * h),
                (0.90 * w, 0.12 * h),
                (0.50 * w, 0.12 * h),
            ]

            for x, y in candidates:
                if _adb_tap(x, y):
                    idx = _find_search_edit_idx()
                    if idx is not None:
                        return idx
                    nodes2 = _dump_nodes()
                    edit_node = _find_search_edit_node(nodes2)
                    if edit_node is not None:
                        _tap_node(edit_node)
                        device.settle(1)
                        idx = _find_search_edit_idx()
                        if idx is not None:
                            return idx
                        return -1

            device.navigate_back()
            device.settle(1)

        raise RuntimeError("Could not open OsmAnd search")

    def _field_text(idx):
        if idx is None or idx < 0:
            return ""
        el = _element_by_index(idx)
        if el is None:
            return ""
        return (el.get("text") or "").strip()

    def _clear_field_any(idx):
        if idx is None or idx < 0:
            try:
                device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
                for _ in range(120):
                    device.adb_shell("input", "keyevent", "KEYCODE_DEL")
                device.settle(0.3)
            except Exception:
                pass
            return

        for _ in range(4):
            if not _field_text(idx):
                return

            clear_idx = _find(description="Clear", clickable=True)
            if clear_idx is None:
                clear_idx = _find(text="Clear", clickable=True)
            if clear_idx is None:
                clear_idx = _find(contains="Clear", clickable=True)
            if clear_idx is not None and _click_idx(clear_idx, 0.5):
                continue

            if _input_text("", idx):
                continue

            try:
                device.click(index=idx)
                device.settle(0.3)
                device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
                n = len(_field_text(idx)) + 10
                for _ in range(min(160, max(30, n))):
                    device.adb_shell("input", "keyevent", "KEYCODE_DEL")
                device.settle(0.3)
            except Exception:
                pass

    def _typed_ok(search_idx):
        compact_loc = _compact(location)
        if not compact_loc:
            return True

        if search_idx is not None and search_idx >= 0:
            return compact_loc in _compact(_field_text(search_idx))

        node = _find_search_edit_node()
        if node is None:
            return False
        val = " ".join([node.get("text") or "", node.get("desc") or ""])
        return compact_loc in _compact(val)

    def _type_location(search_idx):
        for _ in range(4):
            if search_idx == -1:
                node = _find_search_edit_node()
                if node is not None:
                    _tap_node(node)
                    device.settle(0.5)
                _clear_field_any(-1)

                typed = _input_text(location, None)
                if not typed:
                    try:
                        device.adb_shell(
                            "input", "text", location.replace(" ", "%s")
                        )
                        device.settle(1)
                        typed = True
                    except Exception:
                        pass

                device.settle(1)
                if _typed_ok(-1):
                    return -1
                continue

            if search_idx is not None and search_idx >= 0 and _is_editable_idx(search_idx):
                _click_idx(search_idx, 0.3)
            else:
                new_idx = _find_search_edit_idx()
                if new_idx is not None:
                    search_idx = new_idx
                    _click_idx(search_idx, 0.3)
                else:
                    node = _find_search_edit_node()
                    if node is not None:
                        _tap_node(node)
                        device.settle(0.5)
                        new_idx = _find_search_edit_idx()
                        if new_idx is not None:
                            search_idx = new_idx
                            _click_idx(search_idx, 0.3)
                        else:
                            search_idx = -1
                    else:
                        raise RuntimeError("Could not find OsmAnd search field")

            _clear_field_any(search_idx)

            typed = False
            if search_idx is not None and search_idx >= 0:
                typed = _input_text(location, search_idx)
            if not typed:
                typed = _input_text(location, None)
            if not typed:
                try:
                    device.adb_shell("input", "text", location.replace(" ", "%s"))
                    device.settle(1)
                    typed = True
                except Exception:
                    pass

            device.settle(1)
            if _typed_ok(search_idx):
                return search_idx

        return search_idx

    def _match_data():
        raw = location
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
        return compact_raw, norm_tokens, numeric_prefixes, threshold, weak_threshold

    def _find_first_result_idx(search_idx=None):
        elements = _elements()
        compact_raw, norm_tokens, numeric_prefixes, threshold, weak_threshold = _match_data()
        first_weak = None

        for order, el in enumerate(elements):
            idx = el.get("index")
            if idx is None:
                idx = order

            if search_idx is not None and search_idx >= 0 and idx == search_idx:
                continue
            if el.get("editable") or "EditText" in (el.get("class_name") or ""):
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

            if disp_norm in CONTROL_EXACT or disp_compact in CONTROL_EXACT:
                continue
            if any(b in blob for b in BAD_CONTAINS):
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

    def _find_first_result_node():
        nodes = _dump_nodes()
        compact_raw, norm_tokens, numeric_prefixes, threshold, weak_threshold = _match_data()

        candidates = []
        weak = []

        for node in nodes:
            cls = node.get("class") or ""
            if "EditText" in cls:
                continue

            disp = (node.get("text") or "").strip() or (node.get("desc") or "").strip()
            if not disp:
                continue

            blob = _node_blob(node)
            compact_blob = _node_compact(node)
            disp_norm = _norm(disp)
            disp_compact = _compact(disp)

            if disp_norm in CONTROL_EXACT or disp_compact in CONTROL_EXACT:
                continue
            if any(b in blob for b in BAD_CONTAINS):
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

            if "°" in disp or "\u00b0" in disp:
                score += 40

            if re.search(r"\b[NSEW]\b", disp, re.IGNORECASE):
                score += 10

            if any(c.isdigit() for c in disp):
                score += 5

            if node.get("clickable"):
                score += 20

            if norm_tokens and matched_tokens == len(norm_tokens):
                score += 100

            item = (-score, _node_y(node), node)
            if score >= threshold:
                candidates.append(item)
            elif score >= weak_threshold:
                weak.append(item)

        if candidates:
            candidates.sort()
            return candidates[0][2]

        if weak:
            weak.sort()
            return weak[0][2]

        for node in sorted(nodes, key=_node_y):
            if not node.get("clickable"):
                continue
            if "EditText" in (node.get("class") or ""):
                continue
            disp = (node.get("text") or "").strip() or (node.get("desc") or "").strip()
            if not disp:
                continue
            blob = _node_blob(node)
            if any(b in blob for b in BAD_CONTAINS):
                continue
            if _norm(disp) in CONTROL_EXACT:
                continue
            return node

        return None

    def _place_panel_visible():
        compact_raw, norm_tokens, _, _, _ = _match_data()
        first_norm = norm_tokens[0] if norm_tokens else ""

        has_loc = False
        has_action = False

        for el in _elements():
            if el.get("editable"):
                continue

            blob = _blob(el)
            compact_blob = _compact(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )

            if compact_raw and compact_raw in compact_blob:
                has_loc = True
            elif first_norm and first_norm in blob:
                has_loc = True

            if any(a in blob for a in ACTION_WORDS):
                has_action = True

        if has_loc and has_action:
            return True

        if has_loc and not _search_edit_present():
            return True

        return False

    def _select_result(search_idx):
        if _place_panel_visible():
            return True

        for _ in range(6):
            idx = _find_first_result_idx(search_idx)
            if idx is not None and _click_idx(idx, 2):
                if _place_panel_visible():
                    return True
                return True

            try:
                device.keyboard_enter()
                device.settle(2)
            except Exception:
                pass

            if _place_panel_visible():
                return True

            try:
                device.scroll(direction="down")
                device.settle(0.5)
            except Exception:
                pass

            node = _find_first_result_node()
            if node is not None and _tap_node(node):
                if _place_panel_visible():
                    return True
                return True

        return False

    def _is_favorite_blob(blob):
        return any(x in blob for x in ("favorite", "favourite", "star"))

    def _is_favorite_idx(idx):
        el = _element_by_index(idx)
        if el is None:
            return False
        return _is_favorite_blob(_blob(el))

    def _find_marker_idx(strict=False):
        labels = MARKER_STRICT_NORM_LABELS if strict else MARKER_NORM_LABELS
        raw_labels = MARKER_LABELS if not strict else tuple(
            x for x in MARKER_LABELS if _norm(x) != "marker"
        )

        for label in raw_labels:
            idx = _find(text=label, clickable=True)
            if idx is None:
                idx = _find(description=label, clickable=True)
            if idx is None:
                idx = _find(contains=label, clickable=True)
            if idx is not None and not _is_favorite_idx(idx):
                return idx

        for el in _elements():
            if not el.get("clickable"):
                continue
            blob = _blob(el)
            if any(lbl in blob for lbl in labels) and not _is_favorite_blob(blob):
                return el.get("index")

        exact_norm = {_norm(x) for x in MARKER_LABELS}
        if strict:
            exact_norm.discard("marker")

        for el in _elements():
            blob = _blob(el)
            if blob in exact_norm and not _is_favorite_blob(blob):
                return el.get("index")

        return None

    def _find_marker_node(strict=False):
        labels = MARKER_STRICT_NORM_LABELS if strict else MARKER_NORM_LABELS
        nodes = _dump_nodes()

        for node in nodes:
            blob = _node_blob(node)
            if any(lbl in blob for lbl in labels) and not _is_favorite_blob(blob):
                return node

        for node in nodes:
            rid = (node.get("resource_id") or "").lower()
            if (
                "marker" in rid
                and "favorite" not in rid
                and "favourite" not in rid
                and "star" not in rid
            ):
                return node

        return None

    def _try_click_marker(require_panel=False, allow_plain=False):
        if allow_plain:
            strict = False
        elif require_panel:
            strict = not _place_panel_visible()
        else:
            panel = _place_panel_visible()
            if panel:
                strict = False
            elif _search_edit_present():
                strict = True
            else:
                strict = False

        for _ in range(6):
            idx = _find_marker_idx(strict=strict)
            if idx is not None and _click_idx(idx, 1):
                return True

            node = _find_marker_node(strict=strict)
            if node is not None and _tap_node(node):
                return True

            try:
                device.scroll(direction="up")
                device.settle(0.5)
            except Exception:
                pass

            w, h = _screen_size()
            _adb_swipe(w // 2, int(h * 0.70), w // 2, int(h * 0.35), 500)

            idx = _find_marker_idx(strict=strict)
            if idx is not None and _click_idx(idx, 1):
                return True

            node = _find_marker_node(strict=strict)
            if node is not None and _tap_node(node):
                return True

            try:
                device.scroll(direction="down")
                device.settle(0.5)
            except Exception:
                pass

        return False

    def _find_add_to_map_node():
        for node in _dump_nodes():
            blob = _node_blob(node)
            if "add to map" in blob and not _is_favorite_blob(blob):
                return node
        return None

    def _try_add_to_map():
        labels = ("Add to map", "Add to Map", "add to map")

        for label in labels:
            idx = _find(text=label, clickable=True)
            if idx is None:
                idx = _find(description=label, clickable=True)
            if idx is None:
                idx = _find(contains=label, clickable=True)
            if idx is not None:
                if _click_idx(idx, 1) and _try_click_marker(allow_plain=True):
                    return True
                return False

        for el in _elements():
            if el.get("clickable") and "add to map" in _blob(el):
                idx = el.get("index")
                if _click_idx(idx, 1) and _try_click_marker(allow_plain=True):
                    return True
                return False

        node = _find_add_to_map_node()
        if node is not None:
            if _tap_node(node) and _try_click_marker(allow_plain=True):
                return True

        return False

    def _find_more_node():
        for node in _dump_nodes():
            blob = _node_blob(node)
            if blob in ("more", "more options") or blob.startswith("more options"):
                return node
        return None

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
                    if _try_click_marker(allow_plain=True):
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
                    if _try_click_marker(allow_plain=True):
                        return True
                    device.navigate_back()
                    device.settle(1)
                return False

        node = _find_more_node()
        if node is not None:
            if _tap_node(node):
                if _try_click_marker(allow_plain=True):
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

        if map_idx is not None:
            try:
                device.execute({"action_type": "long_press", "index": map_idx})
                device.settle(2)
            except Exception:
                pass
        else:
            w, h = _screen_size()
            _adb_swipe(w // 2, h // 2, w // 2, h // 2, 1000)

        if _try_click_marker(allow_plain=True):
            return True
        if _try_add_to_map():
            return True
        return False

    device.open_app(app_name)
    device.settle(2)
    _dismiss_popups()

    search_idx = _open_search()
    search_idx = _type_location(search_idx)
    device.settle(2)

    selected = _select_result(search_idx)

    if not selected:
        try:
            device.keyboard_enter()
            device.settle(2)
        except Exception:
            pass
        selected = _select_result(search_idx)

    if selected:
        if _place_panel_visible() and _try_click_marker(allow_plain=True):
            return True
        if _try_click_marker(require_panel=True):
            return True
        if not _search_edit_present() and _try_click_marker(allow_plain=True):
            return True

    for attempt in range(5):
        if _place_panel_visible() and _try_click_marker(allow_plain=True):
            return True

        if _try_click_marker(require_panel=True):
            return True

        if not _place_panel_visible():
            if _search_edit_present():
                selected = _select_result(search_idx)
            else:
                selected = True

            if selected and _try_click_marker(allow_plain=True):
                return True

        if _try_click_marker(allow_plain=True):
            return True

        if _try_add_to_map():
            return True

        if _try_more_then_marker():
            return True

        if _try_long_press_map():
            return True

        if attempt == 2:
            try:
                device.navigate_back()
                device.settle(1)
                device.navigate_back()
                device.settle(1)
                device.open_app(app_name)
                device.settle(2)
                _dismiss_popups()
                search_idx = _open_search()
                search_idx = _type_location(search_idx)
                selected = _select_result(search_idx)
                if selected and _try_click_marker(allow_plain=True):
                    return True
            except Exception:
                pass

    raise RuntimeError("Could not add the OsmAnd location marker")
