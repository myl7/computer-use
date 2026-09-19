import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to add as a favorite location marker in OsmAnd."
    }
}


def program(device, binding: dict) -> bool:
    location = binding["location"]
    loc_low = location.strip().lower()
    primary = location.split(",")[0].strip().lower()

    time_re = re.compile(r"^\d{1,2}:\d{2}(\s*[apAP][mM])?$")
    status_markers = (
        "settings notification", "unused apps", "location requests",
        "wifi signal", "phone signal", "battery charging",
    )
    ignore_exact = {
        "clear", "close", "cancel", "search", "back", "show on map", "show all",
        "more", "settings", "clipboard", "gif", "sticker", "keyboard", "emoji",
        "enter", "done", "increase search radius", "navigate up", "navigate down",
        "menu", "layers", "next", "previous", "continue",
    }

    # ------------------------------------------------------------------ utils
    def els():
        return device.elements() or []

    def txt(e):
        return ((e.get("text") or "") + " " + (e.get("description") or "")).strip()

    def is_status(e):
        low = txt(e).lower()
        return any(m in low for m in status_markers)

    def is_clock(e):
        t = (e.get("text") or "").strip()
        d = (e.get("description") or "").strip()
        return bool(time_re.match(t)) or bool(time_re.match(d))

    def is_ignored(e):
        if is_status(e) or is_clock(e):
            return True
        t = (e.get("text") or "").strip().lower()
        d = (e.get("description") or "").strip().lower()
        return t in ignore_exact or d in ignore_exact

    def keyboard_open():
        for e in els():
            if is_status(e):
                continue
            low = txt(e).lower().strip()
            if not low:
                continue
            if low in ("keyboard", "more features", "clipboard"):
                return True
            if low.endswith(" keyboard"):
                return True
        return False

    def search_field():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(editable=True)
        if idx is not None:
            return idx
        for e in els():
            if e.get("editable"):
                return e.get("index")
        return None

    def rows():
        field = search_field()
        out = []
        for e in els():
            idx = e.get("index")
            if idx is None:
                continue
            if field is not None and idx <= field:
                continue
            if e.get("editable"):
                continue
            if is_status(e) or is_clock(e):
                continue
            out.append(e)
        return out

    def matches_location(e):
        low = txt(e).lower()
        if not low:
            return False
        if loc_low and loc_low in low:
            return True
        if primary and primary != loc_low and primary in low:
            return True
        return False

    def matching_row():
        # first pass: rows that the a11y tree flags as clickable
        for e in rows():
            if not e.get("clickable"):
                continue
            if is_ignored(e):
                continue
            if matches_location(e):
                return e.get("index")
        # second pass: the tree sometimes omits the clickable flag on result rows
        for e in rows():
            if is_ignored(e):
                continue
            if matches_location(e):
                return e.get("index")
        return None

    def first_row():
        for e in rows():
            if is_ignored(e):
                continue
            if not txt(e):
                continue
            return e.get("index")
        return None

    def by_text(needle):
        needle = needle.lower()
        for e in els():
            if needle in txt(e).lower():
                return e.get("index")
        return None

    def find_favorite_control():
        best = None
        for e in els():
            if is_status(e) or is_clock(e):
                continue
            low = txt(e).lower()
            if not low:
                continue
            has_fav = ("favorit" in low) or ("favourite" in low)
            has_star = any(s in low for s in ("\u2605", "\u2606", "\u2b50", "star"))
            if not (has_fav or has_star):
                continue
            if "marker" in low and not has_fav:
                continue
            score = 0 if has_fav else 1
            if e.get("clickable"):
                score -= 0.5
            if best is None or score < best[0]:
                best = (score, e.get("index"))
        if best is not None:
            return best[1]
        # last resort: a bare "Add" action that is not the marker control
        for e in els():
            if not e.get("clickable"):
                continue
            t = (e.get("text") or "").strip().lower()
            d = (e.get("description") or "").strip().lower()
            if t == "add" and "marker" not in t and "marker" not in d:
                return e.get("index")
        return None

    def find_save_button():
        for label in ("Save", "OK", "Add", "Confirm", "Done"):
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                return idx
        for e in els():
            if not e.get("clickable"):
                continue
            t = (e.get("text") or "").strip().lower()
            if t in ("save", "ok", "add", "confirm", "done"):
                return e.get("index")
        return None

    # ------------------------------------------------------------------ launch
    device.open_app("OsmAnd")
    device.settle(2)

    search_btn = device.find(description="Search", clickable=True)
    if search_btn is None:
        search_btn = device.find(text="Search", clickable=True)
    if search_btn is None:
        for e in els():
            if e.get("clickable") and "search" in (e.get("description") or "").lower():
                search_btn = e.get("index")
                break
    if search_btn is None:
        raise RuntimeError("Search button not found")
    device.click(index=search_btn)
    device.settle(1.5)

    field = search_field()
    if field is None:
        raise RuntimeError("Search field not found")

    clear_btn = device.find(description="Clear", clickable=True)
    if clear_btn is not None:
        device.click(index=clear_btn)
        device.settle(0.5)
        field = search_field()
        if field is None:
            raise RuntimeError("Search field not found after clearing")

    device.input_text(location, index=field)
    device.settle(2.5)

    # ------------------------------------------------------- choose the result
    selected = False

    for _ in range(3):
        idx = matching_row()
        if idx is not None:
            device.click(index=idx)
            device.settle(2.5)
            selected = True
            break
        inc = by_text("increase search radius")
        if inc is not None:
            device.click(index=inc)
            device.settle(3)
            continue
        break

    if not selected:
        idx = matching_row()
        if idx is not None:
            device.click(index=idx)
            device.settle(2.5)
            selected = True

    if not selected and keyboard_open():
        device.keyboard_enter()
        device.settle(2.5)
        idx = matching_row()
        if idx is not None:
            device.click(index=idx)
            device.settle(2.5)
            selected = True

    # results list did not contain the place: fall back to showing the search on the map
    if not selected:
        idx = by_text("show on map")
        if idx is not None:
            device.click(index=idx)
            device.settle(2.5)
            selected = True

    if not selected:
        idx = first_row()
        if idx is not None:
            device.click(index=idx)
            device.settle(2.5)
            selected = True

    # --------------------------------------- reach place panel, tap the star
    clicked_rows = set()
    fav_idx = None
    for _ in range(4):
        fav_idx = find_favorite_control()
        if fav_idx is not None:
            break
        idx = matching_row()
        if idx is not None and idx not in clicked_rows:
            clicked_rows.add(idx)
            device.click(index=idx)
            device.settle(2.5)
            continue
        idx = first_row()
        if idx is not None and idx not in clicked_rows:
            clicked_rows.add(idx)
            device.click(index=idx)
            device.settle(2.5)
            continue
        break

    if fav_idx is None and keyboard_open():
        device.keyboard_enter()
        device.settle(2)
        fav_idx = find_favorite_control()

    if fav_idx is None:
        more_idx = None
        for e in els():
            if not e.get("clickable"):
                continue
            t = (e.get("text") or "").strip().lower()
            d = (e.get("description") or "").strip().lower()
            if t == "more" or d == "more":
                more_idx = e.get("index")
                break
        if more_idx is not None:
            device.click(index=more_idx)
            device.settle(1.5)
            fav_idx = find_favorite_control()

    if fav_idx is None:
        raise RuntimeError("Favorite control not found on place panel")

    device.click(index=fav_idx)
    device.settle(2)

    # ------------------------------------------- accept the proposed name/save
    for e in els():
        if e.get("editable"):
            if not (e.get("text") or "").strip():
                device.input_text(location, index=e.get("index"))
                device.settle(1)
            break

    save_idx = find_save_button()
    if save_idx is not None:
        device.click(index=save_idx)
        device.settle(2)

    return True
