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

    def _elements():
        return device.elements()

    def _element_by_index(idx):
        if idx is None:
            return None
        for el in _elements():
            if el.get("index") == idx:
                return el
        return None

    def _try_find(**kwargs):
        try:
            return device.find(**kwargs)
        except Exception:
            return None

    def _bounds(el):
        if not el:
            return None

        for key in ("bounds", "bounds_in_screen", "visible_bounds", "visibleBounds"):
            b = el.get(key)
            if isinstance(b, str):
                m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
                if m:
                    return tuple(int(x) for x in m.groups())
            elif isinstance(b, dict):
                x1 = b.get("left", b.get("x1", b.get("x")))
                y1 = b.get("top", b.get("y1", b.get("y")))
                x2 = b.get("right", b.get("x2"))
                y2 = b.get("bottom", b.get("y2"))
                try:
                    if x1 is not None and y1 is not None and x2 is not None and y2 is not None:
                        return int(x1), int(y1), int(x2), int(y2)
                except Exception:
                    pass
            elif isinstance(b, (list, tuple)) and len(b) == 4:
                try:
                    return tuple(int(x) for x in b)
                except Exception:
                    pass

        return None

    def _center(el):
        b = _bounds(el)
        if not b:
            return None
        x1, y1, x2, y2 = b
        return (x1 + x2) // 2, (y1 + y2) // 2

    def _click_index(idx):
        if idx is None:
            return False

        el = _element_by_index(idx)
        c = _center(el) if el else None

        if el is not None and not el.get("clickable") and c is not None:
            try:
                device.adb_shell("input", "tap", str(c[0]), str(c[1]))
                device.settle(0.5)
                return True
            except Exception:
                pass

        try:
            device.click(index=idx)
            return True
        except Exception:
            if c is not None:
                try:
                    device.adb_shell("input", "tap", str(c[0]), str(c[1]))
                    device.settle(0.5)
                    return True
                except Exception:
                    pass
            return False

    def _find_control(label, clickable=None):
        low = _norm(label)
        if not low:
            return None

        kwargs = {}
        if clickable is not None:
            kwargs["clickable"] = clickable

        for key in ("text", "description", "hint"):
            idx = _try_find(**{key: label}, **kwargs)
            if idx is not None:
                return idx

        for el in _elements():
            if clickable and not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            hint = (el.get("hint") or "").strip()
            display = text or desc or hint
            if _norm(display) == low:
                return el.get("index")

        idx = _try_find(contains=label, **kwargs)
        if idx is not None:
            return idx

        for el in _elements():
            if clickable and not el.get("clickable"):
                continue
            blob = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if low in blob:
                return el.get("index")

        return None

    def _find_search_button():
        idx = _try_find(description="Search", clickable=True)
        if idx is not None:
            return idx
        idx = _try_find(text="Search", clickable=True)
        if idx is not None:
            return idx
        idx = _try_find(hint="Search", clickable=True)
        if idx is not None:
            return idx
        idx = _try_find(description="Search")
        if idx is not None:
            return idx
        idx = _try_find(text="Search")
        if idx is not None:
            return idx

        for el in _elements():
            if el.get("editable"):
                continue
            desc = _norm(el.get("description") or "")
            text = _norm(el.get("text") or "")
            hint = _norm(el.get("hint") or "")
            if desc == "search" or text == "search" or hint == "search":
                return el.get("index")

        for el in _elements():
            if el.get("editable") or not el.get("clickable"):
                continue
            desc = _norm(el.get("description") or "")
            if "search" in desc and "categories" not in desc:
                return el.get("index")

        return None

    def _find_search_edit(strict=True):
        idx = _try_find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = _try_find(contains="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = _try_find(text="Type to search all", editable=True)
        if idx is not None:
            return idx

        norm_loc = _norm(location)
        for el in _elements():
            if not el.get("editable"):
                continue
            text = el.get("text") or ""
            if location in text or (norm_loc and norm_loc in _norm(text)):
                return el.get("index")

        for el in _elements():
            if not el.get("editable"):
                continue
            blob = _norm(
                " ".join(
                    [
                        el.get("text") or "",
                        el.get("hint") or "",
                        el.get("description") or "",
                    ]
                )
            )
            if "search" in blob or "type to search" in blob:
                return el.get("index")

        if strict:
            return None

        idx = _try_find(editable=True, clickable=True)
        if idx is not None:
            return idx
        idx = _try_find(editable=True)
        if idx is not None:
            return idx

        for el in _elements():
            if el.get("editable"):
                return el.get("index")

        return None

    def _is_search_field(idx):
        el = _element_by_index(idx)
        if not el:
            return False
        blob = _norm(
            " ".join(
                [
                    el.get("hint") or "",
                    el.get("description") or "",
                ]
            )
        )
        return "search" in blob or "type to search" in blob

    def _search_field_idx():
        idx = _find_search_edit(strict=True)
        if idx is not None:
            return idx
        idx = _find_search_edit(strict=False)
        if idx is not None and _is_search_field(idx):
            return idx
        return None

    def _find_any_editable():
        for el in _elements():
            if el.get("editable"):
                return el.get("index")
        return None

    def _clear_field(idx):
        if idx is None:
            return None

        for _ in range(3):
            clear_idx = _find_control("Clear", clickable=True)
            if clear_idx is None:
                for el in _elements():
                    if el.get("clickable") and "clear" in _norm(el.get("description") or ""):
                        clear_idx = el.get("index")
                        break

            if clear_idx is not None:
                _click_index(clear_idx)
                new_idx = _find_search_edit(strict=True)
                if new_idx is not None:
                    idx = new_idx

                el = _element_by_index(idx)
                if el is None or not (el.get("text") or "").strip():
                    return idx
            else:
                break

        try:
            device.input_text("", index=idx)
            device.settle(0.5)
        except Exception:
            pass

        new_idx = _find_search_edit(strict=True)
        if new_idx is not None:
            idx = new_idx

        el = _element_by_index(idx)
        if el is None or not (el.get("text") or "").strip():
            return idx

        try:
            _click_index(idx)
            device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
            text_len = len((el.get("text") or "").strip()) if el else 0
            for _ in range(min(100, max(20, text_len + 10))):
                device.adb_shell("input", "keyevent", "KEYCODE_DEL")
            device.settle(0.5)
        except Exception:
            pass

        new_idx = _find_search_edit(strict=True)
        if new_idx is not None:
            idx = new_idx

        return idx

    def _find_first_result(search_idx):
        elements = _elements()

        raw = location
        norm_raw = _norm(raw)
        tokens = [t for t in re.split(r"[,;\s]+", raw) if t]
        norm_tokens = [_norm(t) for t in tokens]
        numeric_tokens = [t.lower() for t in tokens if re.match(r"^[+-]?\d+(?:\.\d+)?$", t)]
        is_coord = len(numeric_tokens) >= 2

        control_texts = {
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
            "details",
            "share",
            "actions",
        }

        bad_contains = (
            "no results",
            "not found",
            "increase search radius",
            "show on map",
            "type to search",
            "search categories",
            "categories",
            "recent",
            "history",
            "addresses",
            "places",
            "points of interest",
            "sort",
            "filter",
            "more",
            "menu",
            "home",
            "marker",
            "markers",
            "favorite",
            "favourite",
            "star",
            "navigate",
            "details",
            "share",
            "actions",
            "configure map",
            "back",
            "ok",
            "cancel",
            "allow",
            "deny",
        )

        first_high = None
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

            all_norm = _norm(" ".join([text, hint, desc]))
            disp_norm = _norm(display)

            score = 0

            if norm_raw and norm_raw in all_norm:
                score += 300

            token_hits = 0
            for nt in norm_tokens:
                if nt and nt in all_norm:
                    token_hits += 1
            score += token_hits * 100

            numeric_match_count = 0
            for t in numeric_tokens:
                matched = False
                if t in all_norm:
                    matched = True
                else:
                    for n in (6, 4, 3):
                        if len(t) >= n and t[:n] in all_norm:
                            matched = True
                            break
                if matched:
                    numeric_match_count += 1

            if numeric_match_count >= 2:
                score += 150
            elif numeric_match_count == 1:
                score += 60

            if "°" in display or "\u00b0" in display:
                score += 40

            if re.search(r"\b[NSEW]\b", display, re.IGNORECASE):
                score += 10

            if any(c.isdigit() for c in display):
                score += 5

            if el.get("clickable"):
                score += 20

            generic = disp_norm in control_texts or any(b in all_norm for b in bad_contains)
            if generic and score < 150:
                continue

            if score >= 150 and first_high is None:
                first_high = idx

            if is_coord:
                if (
                    first_weak is None
                    and (numeric_match_count >= 2 or (norm_raw and norm_raw in all_norm))
                    and score >= 80
                ):
                    first_weak = idx
            else:
                if first_weak is None and score >= 50:
                    first_weak = idx

        return first_high or first_weak

    def _marker_word_present(blob):
        return bool(re.search(r"(^|[^a-z])marker([^a-z]|$)", blob))

    def _find_marker_control():
        best_idx = None
        best_score = 0

        for el in _elements():
            if el.get("editable"):
                continue

            text = (el.get("text") or "").strip()
            hint = (el.get("hint") or "").strip()
            desc = (el.get("description") or "").strip()
            rid = el.get("resource-id") or el.get("resourceId") or el.get("id") or ""
            blob = _norm(" ".join([text, hint, desc, rid]))

            if not blob:
                continue

            if any(
                bad in blob
                for bad in ("favorite", "favourite", "star", "remove", "delete", "share")
            ):
                continue

            has_word = _marker_word_present(blob)
            has_add = "add marker" in blob or "map marker" in blob or "place marker" in blob

            if not has_word and not has_add:
                continue

            score = 0
            if has_add:
                score += 80
            if has_word:
                score += 50
            if text.lower() == "marker" or desc.lower() == "marker":
                score += 60
            if el.get("clickable"):
                score += 30

            if score > best_score:
                best_score = score
                best_idx = el.get("index")

        if best_score >= 60:
            return best_idx
        return None

    def _has_remove_marker():
        for el in _elements():
            text = el.get("text") or ""
            hint = el.get("hint") or ""
            desc = el.get("description") or ""
            rid = el.get("resource-id") or el.get("resourceId") or el.get("id") or ""
            blob = _norm(" ".join([text, hint, desc, rid]))
            if "remove marker" in blob or "delete marker" in blob or "edit marker" in blob:
                return True
        return False

    def _handle_marker_dialog(loc):
        edit_idx = _find_any_editable()

        if edit_idx is not None and not _is_search_field(edit_idx):
            _clear_field(edit_idx)
            _click_index(edit_idx)
            try:
                device.input_text(loc, index=edit_idx)
            except Exception:
                pass
            device.settle(0.5)

            for label in ("OK", "Save", "Apply", "Confirm", "Done", "Create", "Add", "Set"):
                idx = _find_control(label, clickable=True)
                if idx is not None:
                    _click_index(idx)
                    device.settle(0.5)
                    return True

            try:
                device.keyboard_enter()
                device.settle(0.5)
                return True
            except Exception:
                pass

            return True

        if _search_field_idx() is None:
            for label in ("OK", "Save", "Apply", "Confirm", "Done"):
                idx = _find_control(label, clickable=True)
                if idx is not None:
                    _click_index(idx)
                    device.settle(0.5)
                    return True

        return False

    def _click_marker(idx):
        el = _element_by_index(idx)
        _click_index(idx)
        device.settle(0.5)

        if _has_remove_marker():
            return True

        if _handle_marker_dialog(location):
            return True

        if el is not None and el.get("clickable"):
            text = el.get("text") or ""
            hint = el.get("hint") or ""
            desc = el.get("description") or ""
            rid = el.get("resource-id") or el.get("resourceId") or el.get("id") or ""
            blob = _norm(" ".join([text, hint, desc, rid]))
            if _marker_word_present(blob) and not any(
                bad in blob for bad in ("favorite", "favourite", "star", "remove", "delete")
            ):
                return True

        return False

    def _adb_long_press_center(cx, cy):
        try:
            device.adb_shell("input", "swipe", str(cx), str(cy), str(cx), str(cy), "1000")
            device.settle(0.5)
            return True
        except Exception:
            return False

    def _adb_long_press_element(idx):
        el = _element_by_index(idx)
        c = _center(el) if el else None
        if c is None:
            return False
        return _adb_long_press_center(c[0], c[1])

    def _adb_long_press_screen_center():
        try:
            out = device.adb_shell("wm", "size")
            m = re.search(r"(\d+)x(\d+)", out or "")
            if m:
                w = int(m.group(1))
                h = int(m.group(2))
                return _adb_long_press_center(w // 2, h // 2)
        except Exception:
            pass
        return False

    def _find_map_surface():
        idx = _try_find(description="Map", clickable=True)
        if idx is not None:
            return idx
        idx = _try_find(description="Map")
        if idx is not None:
            return idx

        for el in _elements():
            if el.get("editable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            hint = (el.get("hint") or "").strip()
            display = text or desc or hint
            if _norm(display) == "map":
                return el.get("index")

        for el in _elements():
            if el.get("editable"):
                continue
            cls = el.get("class_name") or ""
            if "Map" in cls:
                return el.get("index")

        best = None
        best_area = 0
        for el in _elements():
            if el.get("editable"):
                continue
            if not (el.get("clickable") or el.get("scrollable")):
                continue
            b = _bounds(el)
            if not b:
                continue
            area = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
            if area > best_area:
                best_area = area
                best = el.get("index")

        return best

    def _long_press_map():
        map_idx = _find_map_surface()

        if map_idx is not None:
            try:
                device.execute({"action_type": "long_press", "index": map_idx})
                device.settle(0.5)
                return True
            except Exception:
                pass

            if _adb_long_press_element(map_idx):
                return True

        return _adb_long_press_screen_center()

    def _adb_tap_marker():
        marker_idx = _find_marker_control()
        if marker_idx is not None:
            if _click_marker(marker_idx):
                return True

        try:
            device.adb_shell("uiautomator", "dump", "/sdcard/osmand_marker_dump.xml")
            xml = device.adb_shell("cat", "/sdcard/osmand_marker_dump.xml")
            if not xml:
                return False

            nodes = re.findall(r"<node[^>]*>", xml)

            def _attr(node, name):
                m = re.search(name + r'="([^"]*)"', node)
                return m.group(1) if m else ""

            best_click = None
            best_any = None

            for node in nodes:
                cd = _attr(node, "content-desc")
                txt = _attr(node, "text")
                rid = _attr(node, "resource-id")
                clickable = _attr(node, "clickable")

                blob = _norm(" ".join([cd, txt]))
                rid_norm = _norm(rid)

                if any(
                    bad in blob or bad in rid_norm
                    for bad in ("favorite", "favourite", "star", "remove", "delete", "share")
                ):
                    continue

                has_label = _marker_word_present(blob) or "add marker" in blob
                has_rid = "marker" in rid_norm and "markers" not in rid_norm

                if has_label or (has_rid and clickable == "true"):
                    bounds = _attr(node, "bounds")
                    m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                    if m:
                        x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
                        center = ((x1 + x2) // 2, (y1 + y2) // 2)
                        if clickable == "true":
                            best_click = center
                            break
                        if best_any is None:
                            best_any = center

            chosen = best_click or best_any
            if chosen is None:
                return False

            device.adb_shell("input", "tap", str(chosen[0]), str(chosen[1]))
            device.settle(0.5)

            if _has_remove_marker():
                return True

            if _handle_marker_dialog(location):
                return True

            return True
        except Exception:
            return False

    def _try_panel_marker():
        if _search_field_idx() is not None:
            return False

        if _has_remove_marker():
            return True

        marker_idx = _find_marker_control()
        if marker_idx is not None:
            if _click_marker(marker_idx):
                return True

        for label in ("Details", "Actions", "More", "Options", "Menu", "Show more", "Configure"):
            idx = _find_control(label, clickable=True)
            if idx is None:
                idx = _find_control(label)
            if idx is not None:
                _click_index(idx)
                device.settle(0.5)

                if _has_remove_marker():
                    return True

                marker_idx = _find_marker_control()
                if marker_idx is not None and _click_marker(marker_idx):
                    return True

                device.navigate_back()
                device.settle(0.5)

        for direction in ("up", "down", "up", "down"):
            try:
                device.scroll(direction)
            except Exception:
                pass
            device.settle(0.5)

            if _has_remove_marker():
                return True

            marker_idx = _find_marker_control()
            if marker_idx is not None and _click_marker(marker_idx):
                return True

        return False

    def _try_longpress_add_marker():
        if _search_field_idx() is not None:
            device.navigate_back()
            device.settle(0.5)

        if _has_remove_marker():
            return True

        for _ in range(3):
            if not _long_press_map():
                break

            if _has_remove_marker():
                return True

            marker_idx = _find_marker_control()

            if marker_idx is None:
                for label in ("Actions", "More", "Options", "Menu"):
                    idx = _find_control(label, clickable=True)
                    if idx is not None:
                        _click_index(idx)
                        device.settle(0.5)
                        marker_idx = _find_marker_control()
                        if marker_idx is not None:
                            break
                        device.navigate_back()
                        device.settle(0.5)

            if marker_idx is not None:
                if _click_marker(marker_idx):
                    return True

            if _adb_tap_marker():
                return True

        return False

    def _dismiss_popups():
        for _ in range(8):
            clicked = False
            for label in (
                "Allow",
                "OK",
                "Continue",
                "Skip",
                "Accept",
                "Close",
                "Dismiss",
                "Got it",
                "Next",
                "Start",
                "Explore",
            ):
                idx = _find_control(label, clickable=True)
                if idx is None:
                    idx = _find_control(label)
                if idx is not None:
                    _click_index(idx)
                    device.settle(0.5)
                    clicked = True
                    break
            if not clicked:
                break

    device.open_app(app_name)
    device.settle(2)
    _dismiss_popups()

    search_idx = _search_field_idx()
    if search_idx is None:
        search_idx = _find_search_edit(strict=False)

    if search_idx is None:
        for attempt in range(6):
            _dismiss_popups()
            search_btn = _find_search_button()
            if search_btn is not None:
                _click_index(search_btn)
                device.settle(0.5)
                search_idx = _search_field_idx()
                if search_idx is None:
                    search_idx = _find_search_edit(strict=False)
                if search_idx is not None:
                    break

            if attempt < 2:
                device.settle(0.5)
                continue

            device.navigate_back()
            device.settle(0.5)

    if search_idx is None:
        raise RuntimeError("Could not find OsmAnd search field")

    search_idx = _clear_field(search_idx)
    _click_index(search_idx)

    try:
        device.input_text(location, index=search_idx)
    except Exception:
        pass
    device.settle(0.5)

    el = _element_by_index(search_idx)
    current = (el.get("text") or "") if el else ""
    if location not in current and _norm(location) not in _norm(current):
        search_idx = _clear_field(search_idx)
        _click_index(search_idx)
        device.input_text(location, index=search_idx)
        device.settle(0.5)

    result_selected = False
    clicked_result = False

    for attempt in range(12):
        search_idx = _search_field_idx()
        if search_idx is None:
            search_idx = _find_search_edit(strict=False)

        if search_idx is None:
            if clicked_result or attempt >= 2:
                result_selected = True
                break

        if search_idx is not None:
            el = _element_by_index(search_idx)
            current = (el.get("text") or "") if el else ""

            if location not in current and _norm(location) not in _norm(current):
                search_idx = _clear_field(search_idx)
                _click_index(search_idx)
                try:
                    device.input_text(location, index=search_idx)
                except Exception:
                    pass
                device.settle(0.5)
                continue

        result_idx = _find_first_result(search_idx)

        if result_idx is not None:
            _click_index(result_idx)
            clicked_result = True
            device.settle(0.5)

            search_idx = _search_field_idx()
            if search_idx is None:
                search_idx = _find_search_edit(strict=False)

            if search_idx is not None:
                try:
                    device.keyboard_enter()
                    device.settle(0.5)
                except Exception:
                    pass

                search_idx = _search_field_idx()
                if search_idx is None:
                    search_idx = _find_search_edit(strict=False)

                if search_idx is not None:
                    result_idx2 = _find_first_result(search_idx)
                    if result_idx2 is not None and result_idx2 != result_idx:
                        _click_index(result_idx2)
                        clicked_result = True
                        device.settle(0.5)
                        search_idx = _search_field_idx()
                        if search_idx is None:
                            search_idx = _find_search_edit(strict=False)

            if search_idx is None:
                result_selected = True
                break

            if _find_marker_control() is not None or _has_remove_marker():
                result_selected = True
                break

        else:
            if attempt == 1:
                try:
                    device.keyboard_enter()
                    device.settle(0.5)
                except Exception:
                    pass
                continue

            inc_idx = _find_control("INCREASE SEARCH RADIUS", clickable=True)
            if inc_idx is None:
                inc_idx = _find_control("Increase search radius", clickable=True)
            if inc_idx is None:
                inc_idx = _find_control("Increase search radius")

            if inc_idx is not None:
                _click_index(inc_idx)
                device.settle(0.5)
                continue

            try:
                device.scroll("down" if attempt % 2 == 0 else "up")
            except Exception:
                pass
            device.settle(0.5)

    if not result_selected:
        if _search_field_idx() is not None:
            raise RuntimeError("Could not find a search result for the requested location")

    if _try_panel_marker():
        return True

    if _try_longpress_add_marker():
        return True

    if _adb_tap_marker():
        return True

    raise RuntimeError("Could not add the requested OsmAnd map marker")
