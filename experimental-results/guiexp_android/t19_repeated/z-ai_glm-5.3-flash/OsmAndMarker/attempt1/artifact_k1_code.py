import re
import time

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "It is typed verbatim into OsmAnd's search field."
        ),
    },
}

_CONTROL_LABELS = {
    "search", "clear", "back", "navigate up", "close", "go", "done",
    "next", "previous", "keyboard", "voice search", "settings", "more",
}
_HEADER_LABELS = {
    "categories", "history", "my favorites", "favorites", "map markers",
    "markers", "recent searches", "search", "results", "places",
}
_BAD_PARTS = ("search radius", "could not find", "no results", "nothing found")
_DISMISS_LABELS = ("close", "cancel", "dismiss", "no thanks", "got it")


def program(device, binding: dict) -> bool:
    """Add an OsmAnd map marker for binding['location'].

    Opens OsmAnd, searches for the location verbatim, opens the first
    search result and taps the MARKER control on the place panel (not
    the favorites star next to it). The marker is written on the tap.
    """
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required (place name or 'lat, lon')")
    location = str(location).strip()
    app_name = binding.get("app", "OsmAnd")

    # ---------------- helpers ----------------
    def elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def norm(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    def combined(el):
        return " ".join(str(el.get(k) or "") for k in ("text", "hint", "description"))

    def try_find(**criteria):
        try:
            return device.find(**criteria)
        except Exception:
            return None

    # ---------------- 1. launch OsmAnd, wait for the map screen ----------------
    device.open_app(app_name)
    device.settle(3)
    deadline = time.time() + 10
    while time.time() < deadline:
        if (try_find(description="Configure map") is not None
                or try_find(description="Search", clickable=True) is not None):
            break
        device.settle(1)

    # ---------------- 2. open the search overlay ----------------
    search_btn = try_find(description="Search", clickable=True)
    if search_btn is None:
        search_btn = try_find(text="Search", clickable=True)
    if search_btn is None:
        for el in elements():
            if el.get("clickable") and "search" in norm(combined(el)):
                search_btn = el["index"]
                break
    if search_btn is None:
        raise RuntimeError("OsmAnd: 'Search' button not found on the map screen")
    device.click(search_btn)
    device.settle(2)

    # ---------------- 3. type the location verbatim ----------------
    def find_search_field():
        idx = try_find(hint="Type to search all", editable=True)
        if idx is None:
            idx = try_find(text="Type to search all", editable=True)
        if idx is None:
            for el in elements():
                if el.get("editable"):
                    return el["index"]
        return idx

    field = find_search_field()
    if field is None:
        raise RuntimeError("OsmAnd: search input field not found")
    device.click(field)
    device.settle(1)
    device.input_text(location, index=field)
    device.settle(2)
    try:
        device.keyboard_enter()
    except Exception:
        pass
    device.settle(2)

    # ---------------- 4. open the first search result ----------------
    tokens = [t for t in re.split(r"[,\s]+", norm(location)) if t] or [norm(location)]

    def result_groups():
        # Group each clickable row with the non-clickable texts that follow
        # it (result rows keep name / region / distance in child views).
        groups, current = [], None
        for el in elements():
            if el.get("editable"):
                continue
            hay_el = combined(el)
            if el.get("clickable"):
                if current is not None:
                    groups.append(current)
                current = {"index": el["index"], "start": norm(hay_el), "parts": [hay_el]}
            elif current is not None:
                low = norm(hay_el)
                if low in _HEADER_LABELS or any(p in low for p in _BAD_PARTS):
                    continue
                current["parts"].append(hay_el)
        if current is not None:
            groups.append(current)
        return groups

    def group_hay(group):
        return norm(" ".join(group["parts"]))

    def acceptable(hay):
        if not hay:
            return False
        if any(p in hay for p in _BAD_PARTS):
            return False
        return "online" not in hay and "http" not in hay

    def first_result():
        groups = [g for g in result_groups() if g["start"] not in _CONTROL_LABELS]
        for g in groups:  # every token of the query present in the row
            hay = group_hay(g)
            if acceptable(hay) and all(t in hay for t in tokens):
                return g["index"]
        for g in groups:  # relaxed: primary token (place name / coordinate head)
            hay = group_hay(g)
            if acceptable(hay) and tokens[0] in hay:
                return g["index"]
        return None

    def looks_like_no_results():
        return any(any(p in norm(combined(el)) for p in _BAD_PARTS) for el in elements())

    def no_results_control():
        for el in elements():
            if "search radius" in norm(combined(el)):
                return el["index"]
        return None

    result = None
    deadline = time.time() + 20
    while time.time() < deadline:
        result = first_result()
        if result is not None or looks_like_no_results():
            break
        device.settle(1)

    if result is None:
        # No local hits: widen the search radius and re-query, as the
        # recorded no-results state offers exactly this affordance.
        for _ in range(5):
            widen = no_results_control()
            if widen is None:
                break
            device.click(widen)
            device.settle(2)
            try:
                device.keyboard_enter()
            except Exception:
                pass
            inner = time.time() + 6
            while time.time() < inner:
                result = first_result()
                if result is not None:
                    break
                device.settle(1)
            if result is not None:
                break

    if result is None and not looks_like_no_results():
        # Last resort: first plausible result row (covers re-formatted
        # coordinate results that no longer contain the query verbatim).
        for g in result_groups():
            if g["start"] in _CONTROL_LABELS or g["start"] in _HEADER_LABELS:
                continue
            hay = group_hay(g)
            if acceptable(hay) and len(hay) >= 4 and (
                    any(ch.isdigit() for ch in hay) or tokens[0] in hay):
                result = g["index"]
                break

    if result is None:
        raise RuntimeError("OsmAnd: no search result found for %r" % location)

    device.click(result)
    device.settle(3)

    # ---------------- 5. tap MARKER on the place panel ----------------
    def marker_control():
        fallback = None
        for el in elements():
            hay = norm(combined(el))
            if "marker" not in hay or "favorite" in hay or "star" in hay:
                continue
            if el.get("clickable"):
                return el["index"]
            if "map markers" in hay:
                continue
            if hay in ("marker", "add marker", "add to map markers", "mark"):
                if fallback is None:
                    fallback = el["index"]
        return fallback

    marker = None
    deadline = time.time() + 12
    attempt = 0
    while time.time() < deadline:
        marker = marker_control()
        if marker is not None:
            break
        attempt += 1
        if attempt >= 3:
            # Dismiss an unexpected dialog blocking the place panel.
            for el in elements():
                if el.get("clickable") and norm(combined(el)) in _DISMISS_LABELS:
                    try:
                        device.click(el["index"])
                    except Exception:
                        pass
                    break
        device.settle(1)

    if marker is None:
        try:
            device.scroll(direction="up")
        except Exception:
            pass
        device.settle(1)
        marker = marker_control()
    if marker is None:
        raise RuntimeError("OsmAnd: MARKER control not found on the place panel")

    device.click(marker)
    device.settle(2)

    return True
