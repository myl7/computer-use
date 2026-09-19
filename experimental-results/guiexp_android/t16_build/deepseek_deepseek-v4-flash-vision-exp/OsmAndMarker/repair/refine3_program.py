PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    location = binding["location"]
    loc = location.strip()
    primary = loc.split(",")[0].strip() or loc

    # ------------------------- helpers -------------------------

    def combined(el):
        return " ".join([
            el.get("text") or "",
            el.get("description") or "",
            el.get("hint") or "",
        ]).lower()

    def find_editable():
        for el in device.elements():
            if el.get("editable"):
                return el["index"]
        return None

    def field_text(idx):
        els = device.elements()
        if idx is not None and 0 <= idx < len(els):
            return els[idx].get("text") or ""
        return ""

    def dismiss_dialogs():
        for label in ["Allow", "While using the app", "Only this time",
                      "OK", "Got it", "Continue", "AGREE", "Agree",
                      "Yes", "Skip", "Later", "Not now", "Dismiss"]:
            for kw in ({"text": label}, {"description": label}):
                idx = device.find(clickable=True, **kw)
                if idx is not None:
                    device.click(index=idx)
                    device.settle(1)
                    return True
        return False

    def find_result():
        els = device.elements()
        # 1) first clickable, non-editable element that mentions the primary token
        for el in els:
            if el.get("editable") or not el.get("clickable"):
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
        for el in device.elements():
            t = combined(el)
            if "marker" in t and "favorite" not in t:
                return el["index"]
        return None

    def open_search():
        # already on a screen with an editable search input?
        f = find_editable()
        if f is not None:
            return f

        # try tapping a search-labelled control
        candidates = [
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"hint": "Search", "clickable": True},
            {"description": "Search OsmAnd", "clickable": True},
            {"text": "Search OsmAnd", "clickable": True},
            {"description": "Search...", "clickable": True},
            {"contains": "Search", "clickable": True},
        ]
        for kw in candidates:
            idx = device.find(**kw)
            if idx is None:
                continue
            device.click(index=idx)
            device.settle(2)
            for _ in range(4):
                f = find_editable()
                if f is not None:
                    return f
                device.settle(1)
            device.navigate_back()
            device.settle(1)

        # last resort: tap the map / main view then look for a search entry
        for mkw in ({"description": "Map", "clickable": True},
                    {"text": "Map", "clickable": True}):
            midx = device.find(**mkw)
            if midx is None:
                continue
            device.click(index=midx)
            device.settle(2)
            for _ in range(3):
                f = find_editable()
                if f is not None:
                    return f
                sidx = device.find(contains="Search", clickable=True)
                if sidx is not None:
                    device.click(index=sidx)
                    device.settle(2)
                    f = find_editable()
                    if f is not None:
                        return f
                device.settle(1)
            device.navigate_back()
            device.settle(1)
        return None

    # ------------------------- open app -------------------------
    device.open_app("OsmAnd")
    device.settle(3)
    for _ in range(3):
        if not dismiss_dialogs():
            break
        device.settle(1)

    field = open_search()
    if field is None:
        # retry once from a clean state
        device.navigate_back()
        device.settle(1)
        device.open_app("OsmAnd")
        device.settle(3)
        dismiss_dialogs()
        field = open_search()
    if field is None:
        raise RuntimeError("Search input field not found")

    # ---------------------- enter the query ---------------------
    typed = False
    for _ in range(4):
        f = find_editable()
        if f is None:
            f = open_search()
        if f is None:
            break

        cur = field_text(f)
        if loc.lower() in cur.lower() or primary.lower() in cur.lower():
            typed = True
            break

        # clear stale text
        if cur.strip():
            clr = device.find(description="Clear", clickable=True)
            if clr is None:
                clr = device.find(contains="Clear", clickable=True)
            if clr is not None:
                device.click(index=clr)
                device.settle(1)

        # focus the field, then type verbatim
        device.click(index=f)
        device.settle(1)
        f2 = find_editable()
        if f2 is None:
            f2 = f
        device.input_text(text=loc, index=f2)
        device.settle(2)

        chk = field_text(find_editable())
        if loc.lower() in chk.lower() or primary.lower() in chk.lower():
            typed = True
            break

    if not typed:
        f = find_editable()
        if f is None:
            raise RuntimeError("Could not focus the search field")
        device.input_text(text=loc, index=f)
        device.settle(2)

    # -------------------- wait for results ----------------------
    result = None
    for _ in range(8):
        result = find_result()
        if result is not None:
            break
        device.settle(1)

    if result is None:
        device.keyboard_enter()
        device.settle(2)
        for _ in range(8):
            result = find_result()
            if result is not None:
                break
            device.settle(1)

    if result is not None:
        device.click(index=result)
        device.settle(2)
    else:
        # some flows show the place panel directly (no result list)
        if find_marker() is None:
            raise RuntimeError("No search result for %r" % loc)

    # -------------------- place the marker ----------------------
    marker = find_marker()
    if marker is None:
        for _ in range(4):
            device.scroll("down")
            device.settle(1)
            marker = find_marker()
            if marker is not None:
                break
    if marker is None:
        for _ in range(2):
            device.scroll("up")
            device.settle(1)
            marker = find_marker()
            if marker is not None:
                break
    if marker is None:
        for mkw in ({"description": "More", "clickable": True},
                    {"contains": "More", "clickable": True},
                    {"description": "More options", "clickable": True}):
            midx = device.find(**mkw)
            if midx is not None:
                device.click(index=midx)
                device.settle(2)
                marker = find_marker()
                if marker is not None:
                    break

    if marker is None:
        raise RuntimeError("MARKER control not found on place panel")

    device.click(index=marker)
    device.settle(2)
    return True
