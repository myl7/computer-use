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

    CONTROL_TEXTS = {
        "map",
        "search",
        "clear",
        "categories",
        "favorite",
        "star",
        "show on map",
        "increase search radius",
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
    )

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

    def _find_control_by_labels(
        labels,
        require_clickable=True,
        exclude=("favorite", "star"),
        allow_nonclick=True,
    ):
        norm_labels = [_norm(label) for label in labels]

        for label in labels:
            if require_clickable:
                for kw in ("text", "description", "hint"):
                    idx = device.find(**{kw: label, "clickable": True})
                    if idx is not None:
                        return idx
            else:
                for kw in ("text", "description", "hint"):
                    idx = device.find(**{kw: label})
                    if idx is not None:
                        return idx

        for el in device.elements():
            if require_clickable and not el.get("clickable"):
                continue
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(ex in disp for ex in exclude):
                continue
            for nl in norm_labels:
                if nl and nl in disp:
                    return el.get("index")

        if require_clickable and allow_nonclick:
            for el in device.elements():
                disp = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if any(ex in disp for ex in exclude):
                    continue
                for nl in norm_labels:
                    if nl and nl in disp:
                        return el.get("index")

        return None

    def _find_search_button():
        idx = device.find(description="Search", clickable=True)
        if idx is None:
            idx = device.find(text="Search", clickable=True)
        if idx is None:
            idx = device.find(hint="Search", clickable=True)

        if idx is None:
            for el in device.elements():
                if el.get("editable") or not el.get("clickable"):
                    continue
                disp = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if disp == "search":
                    return el.get("index")

        if idx is None:
            for el in device.elements():
                if el.get("editable") or not el.get("clickable"):
                    continue
                disp = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if "search" in disp and "categories" not in disp:
                    return el.get("index")

        if idx is None:
            for el in device.elements():
                if el.get("editable"):
                    continue
                disp = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if disp == "search":
                    return el.get("index")

        if idx is None:
            for el in device.elements():
                if el.get("editable"):
                    continue
                disp = _norm(
                    " ".join(
                        [
                            el.get("text") or "",
                            el.get("hint") or "",
                            el.get("description") or "",
                        ]
                    )
                )
                if "search" in disp and "categories" not in disp:
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

        if idx is None:
            for el in device.elements():
                if el.get("editable"):
                    return el.get("index")

        if idx is None:
            idx = device.find(hint="Type to search all")
        if idx is None:
            idx = device.find(contains="Type to search all")

        return idx

    def _clear_field(idx):
        def _empty(field_idx):
            el = _element_by_index(field_idx)
            return el is None or not (el.get("text") or "").strip()

        for _ in range(3):
            if idx is None:
                idx = _find_search_edit()
                if idx is None:
                    return None

            if _empty(idx):
                return idx

            try:
                device.click(index=idx)
            except Exception:
                pass
            device.settle(0.3)

            try:
                device.adb_shell("input", "keyevent", "KEYCODE_SELECT_ALL")
            except Exception:
                pass
            try:
                device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
            except Exception:
                pass
            try:
                for _ in range(25):
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

    def _dismiss_dialogs():
        labels = (
            "Allow",
            "ALLOW",
            "While using",
            "Only this time",
            "OK",
            "Continue",
            "Skip",
            "Accept",
            "Got it",
            "Close",
        )

        for _ in range(6):
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
                    try:
                        device.click(index=idx)
                    except Exception:
                        pass
                    device.settle(0.5)
                    clicked = True
                    break
            if not clicked:
                break

    def _find_first_result(search_idx):
        elements = device.elements()

        raw = location
        norm_raw = _norm(raw)
        compact_raw = _compact(norm_raw)

        raw_tokens = [t for t in re.split(r"[,;\s]+", raw) if t]
        token_pairs = []
        for t in raw_tokens:
            nt = _norm(t)
            ct = _compact(nt)
            if nt or ct:
                token_pairs.append((nt, ct))

        numeric_tokens = [
            t for t in raw_tokens if re.match(r"^[+-]?\d+(?:\.\d+)?$", t)
        ]
        numeric_count = len(numeric_tokens)
        is_coord = numeric_count >= 2

        numeric_prefixes = []
        for t in numeric_tokens:
            digits = re.sub(r"\D", "", t)
            for n in (6, 4, 3):
                if len(digits) >= n:
                    pref = digits[:n]
                    if len(pref) >= 3 and pref not in numeric_prefixes:
                        numeric_prefixes.append(pref)

        strong_threshold = 150 if is_coord else 100
        weak_threshold = 60 if is_coord else 40

        weak_click = None
        weak_nonclick = None
        strong_nonclick = None

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
            compact_all = _compact(all_norm)
            disp_norm = _norm(display)

            score = 0
            matched = 0

            if compact_raw and compact_raw in compact_all:
                score += 300

            if compact_raw and compact_all.startswith(compact_raw):
                score += 50

            for nt, ct in token_pairs:
                if (nt and nt in all_norm) or (ct and ct in compact_all):
                    score += 100
                    matched += 1

            if matched == len(token_pairs) and len(token_pairs) > 1:
                score += 100

            if token_pairs:
                first_nt, first_ct = token_pairs[0]
                if (first_nt and all_norm.startswith(first_nt)) or (
                    first_ct and compact_all.startswith(first_ct)
                ):
                    score += 40

            if is_coord:
                matched_num = 0
                for pref in numeric_prefixes:
                    if pref and (pref in all_norm or pref in compact_all):
                        score += 60
                        matched_num += 1
                if matched_num >= 2:
                    score += 100

                if "°" in display or "\u00b0" in display:
                    score += 40
                if re.search(r"\b[NSEW]\b", display, re.IGNORECASE):
                    score += 10
                if any(c.isdigit() for c in display):
                    score += 5

            if el.get("clickable"):
                score += 20

            if disp_norm in CONTROL_TEXTS:
                if not (
                    matched > 1
                    or (compact_raw and compact_raw == disp_norm)
                    or (
                        compact_raw
                        and compact_raw in compact_all
                        and compact_raw != disp_norm
                    )
                ):
                    continue

            if any(sw in all_norm for sw in SYSTEM_WORDS):
                if not (compact_raw and compact_raw in compact_all):
                    continue

            if any(phrase in all_norm for phrase in EXCLUDED_PHRASES):
                if not (compact_raw and compact_raw in compact_all):
                    continue

            if re.match(r"^\d{1,2}:\d{2}$", disp_norm):
                if not (compact_raw and compact_raw in compact_all):
                    continue

            if score >= strong_threshold:
                if el.get("clickable"):
                    return idx
                if strong_nonclick is None:
                    strong_nonclick = idx
            elif score >= weak_threshold:
                if el.get("clickable"):
                    if weak_click is None:
                        weak_click = idx
                else:
                    if weak_nonclick is None:
                        weak_nonclick = idx

        if strong_nonclick is not None:
            return strong_nonclick
        if weak_click is not None:
            return weak_click
        if weak_nonclick is not None:
            return weak_nonclick
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

    def _click_index(idx):
        if idx is None:
            return False
        try:
            device.click(index=idx)
            device.settle(1)
            return True
        except Exception:
            pass

        el = _element_by_index(idx)
        if el is not None:
            bounds = _parse_bounds(el)
            if bounds:
                x1, y1, x2, y2 = bounds
                x = (x1 + x2) // 2
                y = (y1 + y2) // 2
                try:
                    device.adb_shell("input", "tap", str(x), str(y))
                    device.settle(1)
                    return True
                except Exception:
                    pass

        return False

    def _click_if_found(idx):
        return _click_index(idx)

    def _find_radius_control():
        elements = device.elements()

        for order, el in enumerate(elements):
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if disp == "increase search radius":
                return el.get("index", order)

        for order, el in enumerate(elements):
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if "increase search radius" in disp:
                return el.get("index", order)

        for order, el in enumerate(elements):
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if "increase its radius" in disp or "increase radius" in disp:
                return el.get("index", order)

        idx = device.find(text="INCREASE SEARCH RADIUS")
        if idx is not None:
            return idx
        idx = device.find(contains="INCREASE SEARCH RADIUS")
        if idx is not None:
            return idx
        idx = device.find(contains="increase search radius")
        if idx is not None:
            return idx

        return None

    def _is_no_results():
        for el in device.elements():
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(
                p in disp
                for p in (
                    "could not find",
                    "no search results",
                    "no results",
                    "not found",
                    "provide feedback",
                )
            ):
                return True
        return False

    def _find_fallback_result(search_idx):
        for order, el in enumerate(device.elements()):
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

            disp_norm = _norm(display)
            all_norm = _norm(" ".join([text, hint, desc]))

            if len(disp_norm) <= 1:
                continue
            if disp_norm in CONTROL_TEXTS:
                continue
            if any(sw in all_norm for sw in SYSTEM_WORDS):
                continue
            if any(phrase in all_norm for phrase in EXCLUDED_PHRASES):
                continue
            if any(
                phrase in all_norm
                for phrase in (
                    "provide feedback",
                    "could not find",
                    "change the search",
                    "send",
                    "switch input method",
                    "navigate up",
                    "back",
                    "home",
                    "more options",
                    "options",
                    "menu",
                    "search",
                    "clear",
                    "type to search",
                )
            ):
                continue
            if re.match(r"^\d{1,2}:\d{2}$", disp_norm):
                continue
            if re.match(r"^[a-z]$", disp_norm) or re.match(r"^[0-9]$", disp_norm):
                continue

            return idx

        return None

    def _find_marker_control():
        labels = ("Add marker", "Marker", "Map marker", "Place marker")
        bad = ("favorite", "star", "remove", "delete")

        for label in labels:
            for kw in ("text", "description", "hint"):
                idx = device.find(**{kw: label, "clickable": True})
                if idx is not None:
                    el = _element_by_index(idx)
                    disp = _norm(
                        " ".join(
                            [
                                el.get("text") or "" if el else "",
                                el.get("hint") or "" if el else "",
                                el.get("description") or "" if el else "",
                            ]
                        )
                    )
                    if not any(x in disp for x in bad):
                        return idx

        for el in device.elements():
            if not el.get("clickable"):
                continue
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(x in disp for x in bad):
                continue
            if (
                "add marker" in disp
                or "map marker" in disp
                or "place marker" in disp
                or disp == "marker"
            ):
                return el.get("index")

        for el in device.elements():
            if not el.get("clickable"):
                continue
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(x in disp for x in bad):
                continue
            if "marker" in disp:
                return el.get("index")

        for el in device.elements():
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(x in disp for x in bad):
                continue
            if (
                "add marker" in disp
                or "map marker" in disp
                or "place marker" in disp
                or disp == "marker"
            ):
                return el.get("index")

        for el in device.elements():
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(x in disp for x in bad):
                continue
            if "marker" in disp:
                return el.get("index")

        for label in labels:
            for kw in ("text", "description", "hint"):
                idx = device.find(**{kw: label})
                if idx is not None:
                    el = _element_by_index(idx)
                    disp = _norm(
                        " ".join(
                            [
                                el.get("text") or "" if el else "",
                                el.get("hint") or "" if el else "",
                                el.get("description") or "" if el else "",
                            ]
                        )
                    )
                    if not any(x in disp for x in bad):
                        return idx

        return None

    def _find_location_title():
        raw_tokens = [t for t in re.split(r"[,;\s]+", location) if t]
        first_norm = _norm(raw_tokens[0]) if raw_tokens else ""
        first_compact = _compact(first_norm) if raw_tokens else ""
        compact_raw = _compact(_norm(location))

        first_digits = re.sub(r"\D", "", raw_tokens[0]) if raw_tokens else ""
        first_digit_prefixes = []
        for n in (6, 4, 3):
            if len(first_digits) >= n:
                pref = first_digits[:n]
                if len(pref) >= 3 and pref not in first_digit_prefixes:
                    first_digit_prefixes.append(pref)

        for el in device.elements():
            if el.get("editable"):
                continue

            text = (el.get("text") or "").strip()
            hint = (el.get("hint") or "").strip()
            desc = (el.get("description") or "").strip()
            all_norm = _norm(" ".join([text, hint, desc]))
            compact_all = _compact(all_norm)

            if first_compact and (
                first_compact in compact_all
                or (first_norm and first_norm in all_norm)
            ):
                if any(sw in all_norm for sw in SYSTEM_WORDS):
                    continue
                return el.get("index")

            if compact_raw and compact_raw in compact_all:
                if any(sw in all_norm for sw in SYSTEM_WORDS):
                    continue
                return el.get("index")

            if first_digit_prefixes:
                hit = sum(1 for p in first_digit_prefixes if p in compact_all)
                if hit >= 2:
                    if any(sw in all_norm for sw in SYSTEM_WORDS):
                        continue
                    return el.get("index")

        return None

    def _panel_likely():
        for el in device.elements():
            disp = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if any(
                p in disp
                for p in (
                    "directions",
                    "search nearby",
                    "share",
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
        try:
            out = device.adb_shell("wm", "size")
        except Exception:
            out = ""

        sizes = re.findall(r"(\d+)x(\d+)", out or "")
        if sizes:
            try:
                w, h = map(int, sizes[-1])
            except Exception:
                w, h = 0, 0

            if w > 0 and h > 0:
                x = w // 2
                y1 = int(h * 0.72)
                y2 = int(h * 0.35)
                for args in (
                    ("input", "swipe", str(x), str(y1), str(x), str(y2), "600"),
                    (
                        "input",
                        "touchscreen",
                        "swipe",
                        str(x),
                        str(y1),
                        str(x),
                        str(y2),
                        "600",
                    ),
                ):
                    try:
                        device.adb_shell(*args)
                        device.settle(1)
                        return True
                    except Exception:
                        continue

        try:
            device.scroll("up")
            device.settle(1)
            return True
        except Exception:
            return False

    def _try_more_options():
        more_idx = _find_control_by_labels(
            [
                "More options",
                "Options",
                "Menu",
                "Context menu",
                "Show more",
                "...",
            ],
            require_clickable=True,
            exclude=(),
            allow_nonclick=False,
        )
        if more_idx is None:
            more_idx = _find_control_by_labels(
                [
                    "More options",
                    "Options",
                    "Menu",
                    "Context menu",
                    "Show more",
                    "...",
                ],
                require_clickable=False,
                exclude=(),
                allow_nonclick=False,
            )
        if more_idx is None:
            return False

        if not _click_if_found(more_idx):
            return False

        device.settle(1)

        marker_idx = _find_marker_control()
        if _click_if_found(marker_idx):
            return True

        add_idx = _find_control_by_labels(
            ["Add to map"],
            require_clickable=True,
            exclude=(),
            allow_nonclick=False,
        )
        if add_idx is None:
            add_idx = _find_control_by_labels(
                ["Add to map"],
                require_clickable=False,
                exclude=(),
                allow_nonclick=False,
            )
        if add_idx is not None:
            if _click_if_found(add_idx):
                device.settle(1)
                marker_idx = _find_marker_control()
                if _click_if_found(marker_idx):
                    return True
                device.navigate_back()
                device.settle(0.5)

        device.navigate_back()
        device.settle(0.5)
        return False

    def _try_long_press_marker():
        try:
            out = device.adb_shell("wm", "size")
        except Exception:
            return False

        sizes = re.findall(r"(\d+)x(\d+)", out or "")
        if not sizes:
            return False

        try:
            w, h = map(int, sizes[-1])
        except Exception:
            return False

        if w <= 0 or h <= 0:
            return False

        candidates = [
            (w // 2, int(h * 0.35)),
            (w // 2, int(h * 0.45)),
            (w // 2, int(h * 0.30)),
            (int(w * 0.35), int(h * 0.35)),
            (int(w * 0.65), int(h * 0.35)),
            (w // 2, int(h * 0.50)),
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
                return False

            device.settle(1)

            marker_idx = _find_marker_control()
            if _click_if_found(marker_idx):
                return True

            more_idx = _find_control_by_labels(
                [
                    "More options",
                    "Options",
                    "Menu",
                    "Context menu",
                    "Show more",
                ],
                require_clickable=True,
                exclude=(),
                allow_nonclick=False,
            )
            if more_idx is None:
                more_idx = _find_control_by_labels(
                    [
                        "More options",
                        "Options",
                        "Menu",
                        "Context menu",
                        "Show more",
                    ],
                    require_clickable=False,
                    exclude=(),
                    allow_nonclick=False,
                )
            if more_idx is not None:
                if _click_if_found(more_idx):
                    device.settle(1)

                    marker_idx = _find_marker_control()
                    if _click_if_found(marker_idx):
                        return True

                    add_idx = _find_control_by_labels(
                        ["Add to map"],
                        require_clickable=True,
                        exclude=(),
                        allow_nonclick=False,
                    )
                    if add_idx is None:
                        add_idx = _find_control_by_labels(
                            ["Add to map"],
                            require_clickable=False,
                            exclude=(),
                            allow_nonclick=False,
                        )
                    if add_idx is not None:
                        if _click_if_found(add_idx):
                            device.settle(1)
                            marker_idx = _find_marker_control()
                            if _click_if_found(marker_idx):
                                return True
                            device.navigate_back()
                            device.settle(0.5)

                    device.navigate_back()
                    device.settle(0.5)
            else:
                add_idx = _find_control_by_labels(
                    ["Add to map"],
                    require_clickable=True,
                    exclude=(),
                    allow_nonclick=False,
                )
                if add_idx is None:
                    add_idx = _find_control_by_labels(
                        ["Add to map"],
                        require_clickable=False,
                        exclude=(),
                        allow_nonclick=False,
                    )
                if add_idx is not None:
                    if _click_if_found(add_idx):
                        device.settle(1)
                        marker_idx = _find_marker_control()
                        if _click_if_found(marker_idx):
                            return True
                        device.navigate_back()
                        device.settle(0.5)

        return False

    def _try_marker_flow():
        for _ in range(3):
            marker_idx = _find_marker_control()
            if _click_if_found(marker_idx):
                return True

            if _find_location_title() is not None or _panel_likely():
                _expand_bottom_sheet()
                device.settle(1)
                continue
            break

        if _find_search_edit() is not None:
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(1)

            marker_idx = _find_marker_control()
            if _click_if_found(marker_idx):
                return True

        title_idx = _find_location_title()
        if title_idx is not None:
            if _click_if_found(title_idx):
                device.settle(1)
                marker_idx = _find_marker_control()
                if _click_if_found(marker_idx):
                    return True

                if _find_location_title() is not None or _panel_likely():
                    _expand_bottom_sheet()
                    device.settle(1)
                    marker_idx = _find_marker_control()
                    if _click_if_found(marker_idx):
                        return True

        if _try_more_options():
            return True

        add_idx = _find_control_by_labels(
            ["Add to map"],
            require_clickable=True,
            exclude=(),
            allow_nonclick=False,
        )
        if add_idx is None:
            add_idx = _find_control_by_labels(
                ["Add to map"],
                require_clickable=False,
                exclude=(),
                allow_nonclick=False,
            )
        if add_idx is not None:
            if _click_if_found(add_idx):
                device.settle(1)
                marker_idx = _find_marker_control()
                if _click_if_found(marker_idx):
                    return True
                device.navigate_back()
                device.settle(0.5)

        show_idx = _find_control_by_labels(
            ["Show on map"],
            require_clickable=True,
            exclude=(),
            allow_nonclick=False,
        )
        if show_idx is None:
            show_idx = _find_control_by_labels(
                ["Show on map"],
                require_clickable=False,
                exclude=(),
                allow_nonclick=False,
            )
        if show_idx is not None:
            if _click_if_found(show_idx):
                device.settle(1)
                marker_idx = _find_marker_control()
                if _click_if_found(marker_idx):
                    return True

        if _try_long_press_marker():
            return True

        for direction in ("up", "down", "up", "left", "right"):
            try:
                device.scroll(direction)
            except Exception:
                pass
            device.settle(0.5)

            marker_idx = _find_marker_control()
            if _click_if_found(marker_idx):
                return True

        return False

    device.open_app(app_name)
    device.settle(2)

    _dismiss_dialogs()

    search_idx = _find_search_edit()
    if search_idx is None:
        search_btn = None
        for attempt in range(6):
            _dismiss_dialogs()
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

        if not _click_if_found(search_btn):
            raise RuntimeError("Could not open OsmAnd search")

        search_idx = _find_search_edit()
        if search_idx is None:
            _dismiss_dialogs()
            search_idx = _find_search_edit()

        if search_idx is None:
            search_btn = _find_search_button()
            if search_btn is not None and _click_if_found(search_btn):
                search_idx = _find_search_edit()

    if search_idx is None:
        raise RuntimeError("Could not find OsmAnd search field")

    search_idx = _clear_field(search_idx)
    if search_idx is None:
        raise RuntimeError("Could not clear OsmAnd search field")

    for _ in range(3):
        search_idx = _find_search_edit() or search_idx
        try:
            device.input_text(location, index=search_idx)
        except Exception:
            pass
        device.settle(1)

        el = _element_by_index(search_idx)
        current = (el.get("text") or "") if el else ""
        if current == location or location in current:
            break

        search_idx = _clear_field(search_idx)

    result_idx = None
    radius_clicks = 0

    for attempt in range(60):
        search_idx = _find_search_edit() or search_idx

        if search_idx is None:
            search_btn = _find_search_button()
            if search_btn is not None and _click_if_found(search_btn):
                search_idx = _find_search_edit()

        if search_idx is None:
            continue

        el = _element_by_index(search_idx)
        current = (el.get("text") or "") if el else ""

        if location not in current:
            if el is not None and not current.strip():
                try:
                    device.input_text(location, index=search_idx)
                except Exception:
                    pass
                device.settle(1)
            elif el is not None:
                search_idx = _clear_field(search_idx)
                if search_idx is not None:
                    try:
                        device.input_text(location, index=search_idx)
                    except Exception:
                        pass
                    device.settle(1)

        result_idx = _find_first_result(search_idx)
        if result_idx is not None:
            break

        radius_idx = _find_radius_control()
        if radius_idx is not None and radius_clicks < 40:
            _click_if_found(radius_idx)
            radius_clicks += 1
            device.settle(1)
            continue

        if _is_no_results():
            if attempt % 5 == 0:
                try:
                    device.keyboard_enter()
                except Exception:
                    pass
                device.settle(1)

            if radius_clicks >= 40:
                search_idx = _clear_field(search_idx)
                if search_idx is not None:
                    try:
                        device.input_text(location, index=search_idx)
                    except Exception:
                        pass
                    device.settle(1)
                    radius_clicks = 0
                    continue
        else:
            fallback = _find_fallback_result(search_idx)
            if fallback is not None:
                result_idx = fallback
                break

        if attempt == 3:
            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(1)

        try:
            device.scroll("down" if attempt % 2 == 0 else "up")
        except Exception:
            pass
        device.settle(0.5)

    if result_idx is None:
        raise RuntimeError("Could not find a search result for the requested location")

    for attempt in range(6):
        if not _click_if_found(result_idx):
            search_idx = _find_search_edit()
            if search_idx is not None:
                new_result = _find_first_result(search_idx) or _find_fallback_result(search_idx)
                if new_result is not None:
                    result_idx = new_result
                    continue
            break

        device.settle(2)

        if _try_marker_flow():
            return True

        search_idx = _find_search_edit()
        if search_idx is not None:
            el = _element_by_index(search_idx)
            current = (el.get("text") or "") if el else ""

            if location not in current:
                search_idx = _clear_field(search_idx)
                if search_idx is not None:
                    try:
                        device.input_text(location, index=search_idx)
                    except Exception:
                        pass
                    device.settle(1)

            new_result = _find_first_result(search_idx) or _find_fallback_result(search_idx)
            if new_result is not None:
                result_idx = new_result
                continue

            try:
                device.keyboard_enter()
            except Exception:
                pass
            device.settle(1)

            if _try_marker_flow():
                return True

            break

        if attempt < 3:
            device.navigate_back()
            device.settle(1)

            if _try_marker_flow():
                return True

            search_idx = _find_search_edit()
            if search_idx is not None:
                el = _element_by_index(search_idx)
                current = (el.get("text") or "") if el else ""

                if location not in current:
                    search_idx = _clear_field(search_idx)
                    if search_idx is not None:
                        try:
                            device.input_text(location, index=search_idx)
                        except Exception:
                            pass
                        device.settle(1)

                new_result = (
                    _find_first_result(search_idx) or _find_fallback_result(search_idx)
                    if search_idx is not None
                    else None
                )
                if new_result is not None:
                    result_idx = new_result
                    continue

        break

    raise RuntimeError("Could not find the Marker control for the selected location")
