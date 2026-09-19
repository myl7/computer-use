PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark, one string: either a place name such as "
            "'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair. "
            "It is typed verbatim into OsmAnd's search field."
        ),
    }
}

_RESULT_EXCLUDE = (
    "increase search radius", "could not find", "search", "clear",
    "navigate up", "history", "categories", "favorites", "favourite",
    "settings", "menu", "drawer", "compass", "configure", "my location",
    "zoom", "directions", "space", "backspace", "delete", "shift",
    "emoji", "microphone", "voice", "keyboard", "next", "done",
)


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required and must be a non-empty string")
    location = str(location).strip()
    loc_low = location.lower()
    tokens = [w for w in loc_low.replace(",", " ").split() if len(w) > 2]

    def els():
        try:
            return device.elements() or []
        except Exception:
            return []

    def low(el, *keys):
        parts = []
        for k in keys:
            v = el.get(k)
            if isinstance(v, str):
                parts.append(v.strip().lower())
        return " ".join(parts)

    def excluded(txt):
        return any(w in txt for w in _RESULT_EXCLUDE)

    def find_search_box():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is None:
            idx = device.find(text="Type to search all", editable=True)
        if idx is not None:
            return idx
        for e in els():
            if e.get("editable"):
                return e.get("index")
        return None

    # ---- 1. launch OsmAnd and wait for the map screen ----
    device.open_app("OsmAnd")
    device.settle(3)
    ready = False
    for _ in range(6):
        if device.find(description="Search") is not None \
                or device.find(description="Configure map") is not None \
                or find_search_box() is not None:
            ready = True
            break
        device.wait()
    if not ready:
        device.open_app("OsmAnd")
        device.settle(3)
        if device.find(description="Search") is None \
                and device.find(description="Configure map") is None \
                and find_search_box() is None:
            raise RuntimeError("OsmAnd map screen not detected after launching the app")

    # ---- 2. open the search overlay via the map screen's Search button ----
    opened = False
    for _ in range(4):
        if find_search_box() is not None:
            opened = True
            break
        idx = device.find(description="Search", clickable=True)
        if idx is None:
            idx = device.find(description="Search")
        if idx is None:
            for e in els():
                d = (e.get("description") or "").strip().lower()
                t = (e.get("text") or "").strip().lower()
                if d == "search" or t == "search":
                    idx = e.get("index")
                    break
        if idx is None:
            device.wait()
            continue
        device.click(index=idx)
        device.settle(2)
        if find_search_box() is not None:
            opened = True
            break
    if not opened:
        raise RuntimeError("OsmAnd: could not open the search screen ('Search' control not effective)")

    # ---- 3. type the location verbatim into the search field ----
    box = None
    for _ in range(4):
        box = find_search_box()
        if box is not None:
            break
        device.wait()
    if box is None:
        raise RuntimeError("OsmAnd: search input field (hint 'Type to search all') not found")
    # clear a stale query if one is present
    for e in els():
        if e.get("editable") and (e.get("text") or "").strip():
            clr = device.find(description="Clear", clickable=True)
            if clr is None:
                for e2 in els():
                    if "clear" in low(e2, "text", "description") and e2.get("clickable"):
                        clr = e2.get("index")
                        break
            if clr is not None:
                device.click(index=clr)
                device.settle(1)
            break
    box = find_search_box() or box
    device.click(index=box)
    device.settle(1)
    box = find_search_box() or box
    device.input_text(location, index=box)
    device.settle(2)

    # ---- 4. open the first search result ----
    def result_score(txt):
        if loc_low and loc_low in txt:
            return 100
        hits = sum(1 for t in tokens if t in txt)
        return 50 + hits if hits else 0

    widened = 0
    picked = False
    for attempt in range(12):
        screen = els()
        edit_pos = None
        for i, e in enumerate(screen):
            if e.get("editable"):
                edit_pos = i
                break
        click_cands = []
        text_cands = []
        for i, e in enumerate(screen):
            if edit_pos is not None and i <= edit_pos:
                continue
            if e.get("editable"):
                continue
            text = (e.get("text") or "").strip()
            desc = (e.get("description") or "").strip()
            txt = (text + " " + desc).lower().strip()
            if e.get("clickable"):
                if len(text) == 1 or len(desc) == 1:
                    continue  # on-screen keyboard key
                if txt and excluded(txt):
                    continue
                click_cands.append((result_score(txt) or 1, i, e))
            else:
                if len(text) < 4 or excluded(txt):
                    continue
                if loc_low and txt == loc_low:
                    continue  # query echo, not a result row
                s = result_score(txt)
                if s:
                    text_cands.append((s, i, e))
        min_score = 50 if attempt < 3 else 1
        best, best_score = None, -1
        if click_cands:
            click_cands.sort(key=lambda t: (-t[0], t[1]))
            best_score, best = click_cands[0][0], click_cands[0][2]
        if (best is None or best_score < min_score) and text_cands:
            text_cands.sort(key=lambda t: (-t[0], t[1]))
            if text_cands[0][0] > best_score:
                best_score, best = text_cands[0][0], text_cands[0][2]
        if best is not None and best_score >= min_score:
            device.click(index=best.get("index"))
            device.settle(2)
            picked = True
            break
        if widened < 4:
            wid = None
            for e in screen:
                if "increase search radius" in low(e, "text", "description"):
                    wid = e
                    break
            if wid is not None:
                device.click(index=wid.get("index"))
                widened += 1
                device.settle(2)
                continue
        device.wait()
    if not picked:
        raise RuntimeError("OsmAnd: no search result could be opened for %r" % location)

    # ---- 5. tap the MARKER control on the place panel (never the favorites star) ----
    def marker_score(e):
        txt = low(e, "text", "description")
        if not txt:
            return 0
        if "favorite" in txt or "favourite" in txt or "star" in txt:
            return 0
        if "marker" not in txt and txt not in ("mark", "add mark", "flag"):
            return 0
        s = 0
        if e.get("clickable"):
            s = 3
        elif "add" in txt:
            s = 2
        if "add" in txt or "create" in txt:
            s += 1
        return s

    marked = False
    for _ in range(10):
        screen = els()
        best, best_score = None, -1
        for e in screen:
            s = marker_score(e)
            if s > best_score:
                best_score, best = s, e
        if best is not None and best_score >= 2:
            device.click(index=best.get("index"))
            device.settle(2)
            marked = True
            break
        # fallback: the marker flag button sits next to the favorites star
        star_pos = None
        for i, e in enumerate(screen):
            txt = low(e, "text", "description")
            if e.get("clickable") and ("favorite" in txt or "favourite" in txt or "star" in txt):
                star_pos = i
                break
        if star_pos is not None:
            done = False
            for j in range(star_pos - 1, -1, -1):
                e = screen[j]
                if not e.get("clickable"):
                    continue
                txt = low(e, "text", "description")
                if txt:
                    if any(w in txt for w in ("share", "directions", "favorite", "favourite", "star")):
                        continue
                    if "marker" not in txt:
                        continue
                device.click(index=e.get("index"))
                device.settle(2)
                marked = True
                done = True
                break
            if done:
                break
        device.wait()
    if not marked:
        raise RuntimeError("OsmAnd: marker control not found on the place panel")

    return True
