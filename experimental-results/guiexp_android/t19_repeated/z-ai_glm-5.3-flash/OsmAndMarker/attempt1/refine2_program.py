import re

PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "required": True,
        "description": (
            "Place to mark in OsmAnd: one string, either a place name "
            "(e.g. 'Schaan, Liechtenstein') or a 'lat, lon' coordinate pair. "
            "It is typed VERBATIM into OsmAnd's search field (never reformatted); "
            "if the verbatim query yields no usable result, progressively shorter "
            "variants of the same string (e.g. dropping the country part) are retried, "
            "the search radius is widened, and if the region's offline map is missing "
            "it is downloaded via Menu -> Maps & Resources before searching again."
        ),
    },
}


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _first_idx(*candidates):
    for c in candidates:
        if c is not None:
            return c
    return None


def _map_ready(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    if device.find(hint="Type to search all", editable=True) is not None:
        return True
    return False


def _open_osmand(device, binding):
    app = binding.get("app") or binding.get("app_name") or "OsmAnd"
    last_err = None
    for _ in range(2):
        try:
            device.open_app(app)
        except Exception as exc:
            last_err = exc
            continue
        device.settle(3)
        if _map_ready(device):
            return
    device.settle(3)
    if _map_ready(device):
        return
    msg = "OsmAnd map screen not detected after launching '%s'" % app
    if last_err is not None:
        msg += " (%s)" % last_err
    raise RuntimeError(msg)


def _search_field(device):
    idx = device.find(hint="Type to search all", editable=True)
    if idx is None:
        idx = device.find(text="Type to search all", editable=True)
    if idx is None:
        for e in _elements(device):
            if not e.get("editable"):
                continue
            hint = (e.get("hint") or "").lower()
            cls = e.get("class_name") or ""
            if "search" in hint or cls == "EditText":
                return e.get("index")
    return idx


def _open_search(device):
    idx = device.find(description="Search", clickable=True)
    if idx is None and _search_field(device) is not None:
        return  # search overlay is already open
    if idx is None:
        for e in _elements(device):
            desc = (e.get("description") or "").lower()
            txt = (e.get("text") or "").strip().lower()
            if e.get("clickable") and ("search" in desc or txt == "search"):
                idx = e.get("index")
                break
    if idx is None:
        raise RuntimeError("OsmAnd 'Search' button not found on the map screen")
    device.click(idx)
    device.settle(2)


def _clear_query(device):
    clear_idx = device.find(description="Clear", clickable=True)
    if clear_idx is None:
        clear_idx = device.find(text="Clear", clickable=True)
    if clear_idx is not None:
        device.click(clear_idx)
        device.settle(1)


def _type_query(device, query):
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search input field not found")
    device.click(fld)
    device.settle(1)
    _clear_query(device)
    fld = _search_field(device)
    if fld is None:
        raise RuntimeError("OsmAnd search field disappeared after clearing")
    # query is typed verbatim: no reformatting, no rounding
    device.input_text(query, index=fld)
    device.settle(2)


def _query_variants(location):
    """Verbatim query first, then progressively shorter fallbacks.

    OsmAnd's local index often fails on 'village, country' strings (it only
    surfaces the country row), while the bare village name hits. Coordinate
    pairs are never shortened.
    """
    raw = str(location)
    loc = raw.strip()
    variants = [raw]
    seen = {loc.lower()}
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) > 1 and re.search(r"[A-Za-z]", parts[0]):
        head = parts[0] if len(parts) == 2 else ", ".join(parts[:-1])
        if head.lower() not in seen:
            variants.append(head)
            seen.add(head.lower())
    toks = [t for t in re.split(r"[\s,;]+", loc) if t]
    if len(toks) > 1 and re.search(r"[A-Za-z]", toks[0]) and toks[0].lower() not in seen:
        variants.append(toks[0])
        seen.add(toks[0].lower())
    return variants


def _find_result_row(device, query):
    """Index of the first non-editable element whose text matches the query."""
    q = str(query).strip()
    if not q:
        return None
    els = _elements(device)

    def scan(tokens):
        for e in els:
            if e.get("editable"):
                continue
            txt = (e.get("text") or "").strip()
            if not txt:
                continue
            low = txt.lower()
            if all(t in low for t in tokens):
                return e.get("index")
        return None

    # 1) verbatim query text
    idx = scan([q.lower()])
    if idx is not None:
        return idx

    # 2) coordinate queries: OsmAnd reformats to e.g. '47.06888° N, 9.50616° E'
    nums = re.findall(r"-?\d+(?:\.\d+)?", q)
    if len(nums) >= 2:
        try:
            lat, lon = float(nums[0]), float(nums[1])
        except ValueError:
            lat = lon = None
        if lat is not None:
            for dec in (5, 4, 3, 2):
                la = "%.*f" % (dec, abs(lat))
                lo = "%.*f" % (dec, abs(lon))
                idx = scan([la + "°", lo + "°"])
                if idx is not None:
                    return idx
                idx = scan([la, lo])
                if idx is not None:
                    return idx

    # 3) all tokens of a place-name query
    toks = [t.lower() for t in re.split(r"[\s,;]+", q) if t]
    if len(toks) > 1:
        idx = scan(toks)
        if idx is not None:
            return idx

    # 4) primary token only (top result row often shows just the name)
    if toks:
        idx = scan([toks[0]])
        if idx is not None:
            return idx
    return None


def _wait_for_results(device, query, attempts=10):
    for attempt in range(attempts):
        idx = _find_result_row(device, query)
        if idx is not None:
            return idx
        rad = device.find(text="INCREASE SEARCH RADIUS")
        if rad is None:
            rad = device.find(contains="INCREASE SEARCH RADIUS")
        if rad is not None:
            if attempt >= attempts - 4:
                break
            device.click(rad)
            device.settle(2)
            continue
        if attempt == 0:
            device.keyboard_enter()
            device.settle(2)
            continue
        if attempt in (2, 5):
            device.scroll("down")
            device.settle(1)
            continue
        device.wait()
        device.settle(1)
    return _find_result_row(device, query)


def _tap_marker(device, query):
    """Tap the MARKER control on the place panel (NOT the favorites star)."""

    def marker_priority(e):
        txt = (e.get("text") or "").strip().lower()
        desc = (e.get("description") or "").strip().lower()
        if txt == "marker":
            return 0
        if "marker" in txt:
            return 1
        if desc == "marker":
            return 2
        if "marker" in desc:
            return 3
        return 99

    for attempt in range(6):
        best, best_p = None, 99
        for e in _elements(device):
            p = marker_priority(e)
            if p < best_p:
                best, best_p = e, p
        if best is not None:
            device.click(best.get("index"))
            device.settle(1)
            return
        if attempt == 1:
            device.scroll("up")
        elif attempt == 2:
            device.scroll("down")
        elif attempt == 3:
            # the marker action may live behind an 'Actions' expander
            act = device.find(text="Actions")
            if act is None:
                act = device.find(description="Actions")
            if act is not None:
                device.click(act)
                device.settle(1)
        elif attempt == 4:
            # maybe an intermediate disambiguation list is showing
            row = _find_result_row(device, query)
            if row is not None:
                device.click(row)
                device.settle(2)
        device.settle(1)
    raise RuntimeError("'Marker' control not found on the place panel")


def _country_of(location):
    """Region/country part of a 'place, region' string (None for coordinates)."""
    parts = [p.strip() for p in str(location).split(",") if p.strip()]
    if len(parts) >= 2:
        last = parts[-1]
        if re.search(r"[A-Za-z]", last) and not re.match(r"^-?\d+(?:\.\d+)?$", last):
            return last
    return None


def _on_map_screen(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    return False


def _drawer_visible(device):
    keys = ("maps & resources", "download maps", "my places", "configure screen",
            "plugins", "trip recording", "measure distance", "settings")
    for e in _elements(device):
        t = (e.get("text") or "").strip().lower()
        if t in keys:
            return True
    return False


def _menu_button(device):
    for desc in ("Drawer", "Menu", "Open menu", "Main menu", "Navigation menu"):
        idx = device.find(description=desc, clickable=True)
        if idx is not None:
            return idx
    for e in _elements(device):
        if not e.get("clickable"):
            continue
        desc = (e.get("description") or "").strip().lower()
        txt = (e.get("text") or "").strip().lower()
        if ("drawer" in desc or "main menu" in desc
                or desc in ("menu", "open menu", "show menu")):
            return e.get("index")
    return None


def _unlabelled_button(device):
    for e in _elements(device):
        if not e.get("clickable") or e.get("editable"):
            continue
        cls = e.get("class_name") or ""
        desc = (e.get("description") or "").strip()
        txt = (e.get("text") or "").strip()
        if ("ImageButton" in cls or "ImageView" in cls) and not desc and not txt:
            return e.get("index")
    return None


def _open_drawer(device):
    for attempt in range(4):
        if _drawer_visible(device):
            return True
        idx = _menu_button(device) if attempt < 2 else _unlabelled_button(device)
        if idx is not None:
            try:
                device.click(idx)
            except Exception:
                pass
            device.settle(2)
            if _drawer_visible(device):
                return True
        try:
            device.scroll("right")
        except Exception:
            pass
        device.settle(2)
    return _drawer_visible(device)


def _open_maps_resources(device):
    for _ in range(4):
        idx = device.find(text="Maps & Resources")
        idx = _first_idx(idx, device.find(text="Download maps"))
        if idx is None:
            for e in _elements(device):
                t = (e.get("text") or "").strip().lower()
                if ("maps & resources" in t or "download maps" in t
                        or ("maps" in t and "resource" in t)
                        or ("download" in t and "map" in t)):
                    idx = e.get("index")
                    break
        if idx is not None:
            device.click(idx)
            device.settle(3)
            return True
        device.scroll("down")
        device.settle(1)
    return False


def _back_to_map(device, app="OsmAnd"):
    for _ in range(5):
        if _drawer_visible(device):
            try:
                device.navigate_back()
            except Exception:
                pass
            device.settle(2)
            continue
        if _on_map_screen(device):
            return True
        try:
            device.navigate_back()
        except Exception:
            pass
        device.settle(2)
    if _on_map_screen(device):
        return True
    try:
        device.open_app(app)
        device.settle(3)
    except Exception:
        pass
    return _on_map_screen(device)


def _on_map_screen(device):
    if device.find(description="Configure map") is not None:
        return True
    if device.find(description="Search", clickable=True) is not None:
        return True
    return False


def _download_search_icon(device):
    idx = _first_idx(device.find(description="Search", clickable=True),
                     device.find(description="Search"))
    if idx is not None:
        return idx
    for e in _elements(device):
        desc = (e.get("description") or "").strip().lower()
        txt = (e.get("text") or "").strip().lower()
        if e.get("clickable") and ("search" in desc or txt == "search"):
            return e.get("index")
    return None


def _download_search_field(device):
    for attempt in range(3):
        for e in _elements(device):
            if e.get("editable"):
                return e.get("index")
        if attempt == 0:
            icon = _download_search_icon(device)
            if icon is not None:
                try:
                    device.click(icon)
                except Exception:
                    pass
                device.settle(2)
        else:
            device.settle(2)
    return None


def _download_region_row(device, country, attempts=6):
    key = country.strip().lower()
    for attempt in range(attempts):
        for e in _elements(device):
            if e.get("editable"):
                continue
            t = (e.get("text") or "").strip()
            if not t:
                continue
            low = t.lower()
            if low == key or (key in low and len(low) <= len(key) + 40):
                return e.get("index")
        if attempt < attempts - 1:
            device.scroll("down")
            device.settle(1)
    return None


def _download_busy(device):
    for e in _elements(device):
        blob = ((e.get("description") or "") + " " + (e.get("text") or "")).strip().lower()
        if not blob:
            continue
        if "%" in blob or ((" of " in blob) and ("mb" in blob or "kb" in blob or "gb" in blob)):
            return True
        desc = (e.get("description") or "").strip().lower()
        if "stop" in desc or "pause" in desc or ("cancel" in desc and "download" in desc):
            return True
    return False


def _dismiss_dialog(device):
    for label in ("Download", "DOWNLOAD", "OK", "Yes", "YES", "Install",
                  "INSTALL", "Continue", "CONTINUE"):
        idx = device.find(text=label, clickable=True)
        if idx is not None:
            device.click(idx)
            device.settle(2)
            return True
    frame = _first_idx(device.find(text="Cancel"), device.find(text="Close"),
                       device.find(description="Cancel"))
    if frame is not None:
        for label in ("Download", "DOWNLOAD", "OK", "Yes", "Install"):
            idx = _first_idx(device.find(text=label, clickable=True),
                             device.find(description=label, clickable=True))
            if idx is not None:
                device.click(idx)
                device.settle(2)
                return True
    return False


def _download_gone(device, country):
    return _download_region_row(device, country, attempts=1) is None


def _start_download(device, country):
    row = _download_region_row(device, country)
    if row is None:
        return False
    target = None
    for e in _elements(device):
        if e.get("editable"):
            continue
        idx = e.get("index", -1)
        if idx < row:
            continue
        blob = ((e.get("description") or "") + " " + (e.get("text") or "")).strip().lower()
        if "download" in blob:
            target = e.get("index")
            break
    if target is None:
        target = row
    try:
        device.click(target)
    except Exception:
        pass
    device.settle(2)
    # a confirmation dialog or a region detail page may have appeared
    _dismiss_dialog(device)
    for _ in range(3):
        if _download_busy(device) or _download_gone(device, country):
            return True
        device.settle(2)
    row2 = _download_region_row(device, country, attempts=1)
    if row2 is not None and row2 != target:
        try:
            device.click(row2)
        except Exception:
            pass
        device.settle(2)
        _dismiss_dialog(device)
        for _ in range(2):
            if _download_busy(device) or _download_gone(device, country):
                return True
            device.settle(2)
    return True


def _wait_download_done(device, country, max_iters=20):
    seen_busy = False
    for i in range(max_iters):
        busy = _download_busy(device)
        if busy:
            seen_busy = True
        elif seen_busy or i >= 5:
            device.settle(2)
            if not _download_busy(device):
                return True
        device.wait()
        device.settle(2)
    return True


def _download_missing_map(device, location, app):
    """Download the offline map for the region part of the location string."""
    country = _country_of(location)
    if not country:
        return False
    if not _back_to_map(device, app):
        return False
    if not _open_drawer(device):
        return False
    if not _open_maps_resources(device):
        return False
    fld = _download_search_field(device)
    if fld is None:
        return False
    try:
        device.click(fld)
        device.settle(1)
        device.input_text(country, index=fld)
    except Exception:
        return False
    device.settle(2)
    if _download_region_row(device, country, attempts=1) is None:
        try:
            device.keyboard_enter()
        except Exception:
            pass
        device.settle(2)
    if not _start_download(device, country):
        return False
    _wait_download_done(device, country)
    return _back_to_map(device, app)


def _exhaust_radius(device, query, extra=6):
    for _ in range(extra):
        idx = _find_result_row(device, query)
        if idx is not None:
            return idx
        rad = device.find(text="INCREASE SEARCH RADIUS")
        if rad is None:
            rad = device.find(contains="INCREASE SEARCH RADIUS")
        if rad is None:
            break
        device.click(rad)
        device.settle(2)
    return _find_result_row(device, query)


def _run_variants(device, variants):
    for i, variant in enumerate(variants):
        _type_query(device, variant)
        row = _wait_for_results(device, variant, attempts=10 if i == 0 else 6)
        if row is not None:
            return row, variant
    return None, None


def program(device, binding: dict) -> bool:
    location = binding.get("location")
    if location is None or not str(location).strip():
        raise ValueError("binding['location'] is required: place name or 'lat, lon' pair")
    location = str(location)
    app = binding.get("app") or binding.get("app_name") or "OsmAnd"

    _open_osmand(device, binding)
    _open_search(device)

    # Try the verbatim string first; if the local index only surfaces a
    # coarser hit (e.g. just the country for 'village, country'), retry
    # with shorter variants derived from the same binding value.
    variants = _query_variants(location)
    row, used = _run_variants(device, variants)

    if row is None:
        # No query matched: widen the radius on the last query's result
        # screen in case the place is just outside the current radius.
        last = variants[-1]
        row = _exhaust_radius(device, last)
        if row is not None:
            used = last

    if row is None:
        # Still nothing (e.g. 'Could not find anything: 20 mi'): the region's
        # offline map is probably missing. Download it via Menu ->
        # Maps & Resources, then search again with the same queries.
        downloaded = False
        try:
            downloaded = _download_missing_map(device, location, app)
        except Exception:
            downloaded = False
        if downloaded:
            _open_search(device)
            row, used = _run_variants(device, variants)
            if row is None:
                last = variants[-1]
                row = _exhaust_radius(device, last)
                if row is not None:
                    used = last

    if row is None:
        raise RuntimeError(
            "No search result found for location %r (queries tried: %r)"
            % (location, variants)
        )
    device.click(row)
    device.settle(2)

    _tap_marker(device, used or location)
    device.settle(1)
    return True
