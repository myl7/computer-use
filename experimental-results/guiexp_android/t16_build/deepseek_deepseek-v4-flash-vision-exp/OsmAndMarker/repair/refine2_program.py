PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark",
        "required": True
    }
}


def program(device, binding: dict) -> bool:
    location = binding["location"]
    loc = location.strip()
    primary = loc.split(",")[0].strip()
    if not primary:
        primary = loc

    # ------------------------- helpers -------------------------

    def combined(el):
        return " ".join([
            el.get("text") or "",
            el.get("description") or "",
            el.get("hint") or "",
        ]).lower()

    def find_search_field():
        for hint in ["Type to search all", "Type to search", "Search OsmAnd"]:
            idx = device.find(hint=hint, editable=True)
            if idx is not None:
                return idx
        return device.find(editable=True)

    def find_search_button():
        for kwargs in [
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"contains": "Search", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        return None

    def field_text(field_idx):
        if field_idx is None:
            return ""
        els = device.elements()
        if 0 <= field_idx < len(els):
            return els[field_idx].get("text") or ""
        return ""

    def is_typed():
        f = find_search_field()
        if f is not None:
            els = device.elements()
            if 0 <= f < len(els):
                t = (els[f].get("text") or "").lower()
                if primary.lower() in t or loc.lower() in t:
                    return True
        # results may already be visible (field replaced by a results list)
        for el in device.elements():
            if el.get("editable"):
                continue
            if primary.lower() in combined(el):
                return True
        return False

    def find_result():
        els = device.elements()
        # 1) first clickable, non-editable element that mentions the primary token
        for el in els:
            if el.get("editable"):
                continue
            if not el.get("clickable"):
                continue
            if primary.lower() in combined(el):
                return el["index"]
        # 2) full query string
        for el in els:
            if el.get("editable") or not el.get("clickable"):
                continue
            if loc.lower() in combined(el):
                return el["index"]
        return None

    def find_marker():
        els = device.elements()
        for el in els:
            if not el.get("clickable"):
                continue
            t = ((el.get("text") or "") + " " + (el.get("description") or "")).lower()
            if "marker" in t and "favorite" not in t:
                return el["index"]
        return None

    # ------------------------- open app -------------------------
    device.open_app("OsmAnd")
    device.settle(2)

    # --------------------- reach search field -------------------
    field = None
    for hint in ["Type to search all", "Type to search"]:
        field = device.find(hint=hint, editable=True)
        if field is not None:
            break

    if field is None:
        btn = find_search_button()
        if btn is None:
            device.navigate_back()
            device.settle(1)
            btn = find_search_button()
        if btn is None:
            raise RuntimeError("Search button not found")

        device.click(index=btn)
        device.settle(2)

        # poll until the search field actually appears
        for _ in range(6):
            for hint in ["Type to search all", "Type to search"]:
                field = device.find(hint=hint, editable=True)
                if field is not None:
                    break
            if field is not None:
                break
            device.settle(1)

    if field is None:
        field = device.find(editable=True)

    if field is None:
        raise RuntimeError("Search input field not found")

    # ------------------- clear + enter query --------------------
    typed = False
    for _attempt in range(4):
        f = find_search_field()
        if f is None:
            break

        cur = field_text(f)
        if primary.lower() in cur.lower() or loc.lower() in cur.lower():
            typed = True
            break

        # clear stale text if present
        if cur.strip() and "type to search" not in cur.lower():
            clr = device.find(description="Clear", clickable=True)
            if clr is None:
                clr = device.find(contains="Clear", clickable=True)
            if clr is not None:
                device.click(index=clr)
                device.settle(1)
                f = find_search_field()
                if f is None:
                    break

        # focus the field, then type
        device.click(index=f)
        device.settle(1)
        f2 = find_search_field()
        if f2 is None:
            f2 = f
        device.input_text(text=loc, index=f2)
        device.settle(1)

        if is_typed():
            typed = True
            break

    # ----------------- wait for / submit search -----------------
    result = None
    for _ in range(4):
        result = find_result()
        if result is not None:
            break
        device.settle(1)

    if result is None:
        device.keyboard_enter()
        device.settle(2)
        for _ in range(6):
            result = find_result()
            if result is not None:
                break
            device.settle(1)

    if result is None:
        raise RuntimeError("No search result for %r" % loc)

    device.click(index=result)
    device.settle(2)

    # --------------------- find MARKER control ------------------
    marker = find_marker()

    if marker is None:
        som = device.find(contains="SHOW ON MAP", clickable=True)
        if som is None:
            som = device.find(description="SHOW ON MAP", clickable=True)
        if som is not None:
            device.click(index=som)
            device.settle(2)
            marker = find_marker()

    if marker is None:
        for direction in ("up", "down"):
            if marker is not None:
                break
            for _ in range(3):
                device.scroll(direction)
                device.settle(1)
                marker = find_marker()
                if marker is not None:
                    break

    if marker is None:
        raise RuntimeError("MARKER control not found on place panel")

    device.click(index=marker)
    device.settle(2)

    return True
