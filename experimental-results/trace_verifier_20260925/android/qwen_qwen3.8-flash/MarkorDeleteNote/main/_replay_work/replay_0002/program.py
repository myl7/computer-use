PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}


def _norm(value):
    if value is None:
        return ""
    return str(value).strip()


def _starts_with_delimited(value, target):
    if value == target:
        return True
    if value.startswith(target):
        ch = value[len(target)]
        return ch.isspace() or ch in " ,:;()[]{}"
    return False


def _matches_candidate(el, cand):
    desc = _norm(el.get("description"))
    text = _norm(el.get("text"))
    hint = _norm(el.get("hint"))

    for prefix in ("File ", "Folder "):
        target = prefix + cand
        if _starts_with_delimited(desc, target):
            return True

    if text == cand or desc == cand or hint == cand:
        return True

    return False


def _candidate_groups(file_name):
    file_name = _norm(file_name)
    if not file_name:
        raise ValueError("binding['file_name'] is required")

    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name

    primary = []

    def add_primary(value):
        value = _norm(value)
        if value and value not in primary:
            primary.append(value)

    add_primary(file_name)
    if "/" in file_name:
        add_primary(file_name.rsplit("/", 1)[-1])

    fallback = []

    def add_fallback(value):
        value = _norm(value)
        if value and value not in primary and value not in fallback:
            fallback.append(value)

    add_fallback(stem)
    if "/" in stem:
        add_fallback(stem.rsplit("/", 1)[-1])

    if "." not in file_name:
        add_fallback(file_name + ".md")
        add_fallback(file_name + ".txt")
    else:
        add_fallback(stem + ".md")
        add_fallback(stem + ".txt")

    return primary, fallback


def _signature(elements):
    return tuple(
        (_norm(e.get("text")), _norm(e.get("description")))
        for e in elements[:150]
    )


def _scroll_to_top(device):
    prev = None
    for _ in range(25):
        sig = _signature(device.elements())
        if sig == prev:
            return
        prev = sig
        device.scroll(direction="up")


def _find_target_current(device, candidates):
    if not candidates:
        return None

    els = device.elements()
    fallback = None

    for cand in candidates:
        for el in els:
            if _matches_candidate(el, cand):
                if el.get("clickable"):
                    return el.get("index")
                if fallback is None:
                    fallback = el.get("index")

    return fallback


def _find_target_with_scroll(device, candidates):
    if not candidates:
        return None

    _scroll_to_top(device)
    prev = None

    for _ in range(100):
        idx = _find_target_current(device, candidates)
        if idx is not None:
            return idx

        sig = _signature(device.elements())
        if sig == prev:
            break

        prev = sig
        device.scroll(direction="down")

    return _find_target_current(device, candidates)


def _find_target(device, primary, fallback):
    idx = _find_target_with_scroll(device, primary)
    if idx is not None:
        return idx

    idx = _find_target_with_scroll(device, fallback)
    if idx is not None:
        return idx

    raise RuntimeError("Could not find note row for binding file_name")


def _long_press(device, idx):
    if idx is None:
        raise RuntimeError("Cannot long-press missing element")

    try:
        device.execute({"action_type": "long_press", "index": idx})
    except Exception:
        if hasattr(device, "long_press"):
            try:
                device.long_press(index=idx)
            except TypeError:
                device.long_press(idx)
        else:
            raise RuntimeError("long_press is unavailable")

    device.settle(0.5)


def _search_delete(device):
    for label in ("Delete", "delete"):
        idx = device.find(description=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(hint=label, clickable=True)
        if idx is not None:
            return idx

    fallback = None
    for el in device.elements():
        vals = (
            _norm(el.get("description")),
            _norm(el.get("text")),
            _norm(el.get("hint")),
        )
        if any(v == "Delete" or v == "delete" for v in vals):
            if el.get("clickable"):
                return el.get("index")
            if fallback is None:
                fallback = el.get("index")

    return fallback


def _search_more(device):
    labels = ("More options", "more options", "Options", "options", "Overflow menu")

    for label in labels:
        idx = device.find(description=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(hint=label, clickable=True)
        if idx is not None:
            return idx

    fallback = None
    for el in device.elements():
        vals = tuple(
            v.lower()
            for v in (
                _norm(el.get("description")),
                _norm(el.get("text")),
                _norm(el.get("hint")),
            )
        )
        if any(v in ("more options", "options", "overflow menu") for v in vals):
            if el.get("clickable"):
                return el.get("index")
            if fallback is None:
                fallback = el.get("index")

    return fallback


def _click_delete(device):
    idx = _search_delete(device)
    opened_overflow = False

    if idx is None:
        more = _search_more(device)
        if more is not None:
            device.click(index=more)
            device.settle(0.5)
            opened_overflow = True
            idx = _search_delete(device)

    if idx is None:
        if opened_overflow:
            device.navigate_back()
            device.settle(0.5)
        return False

    device.click(index=idx)
    device.settle(0.5)
    return True


def _search_ok(device):
    labels = ("OK", "Ok", "ok", "YES", "Yes", "yes", "CONFIRM", "Confirm", "confirm")

    for label in labels:
        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(description=label, clickable=True)
        if idx is not None:
            return idx

        idx = device.find(hint=label, clickable=True)
        if idx is not None:
            return idx

    fallback = None
    for el in device.elements():
        vals = tuple(
            v.lower()
            for v in (
                _norm(el.get("description")),
                _norm(el.get("text")),
                _norm(el.get("hint")),
            )
        )
        if any(v in ("ok", "yes", "confirm") for v in vals):
            if el.get("clickable"):
                return el.get("index")
            if fallback is None:
                fallback = el.get("index")

    return fallback


def _click_ok(device):
    for _ in range(3):
        idx = _search_ok(device)
        if idx is not None:
            device.click(index=idx)
            device.settle(0.5)
            return True
        device.settle(0.5)

    return False


def program(device, binding: dict) -> bool:
    file_name = _norm(binding.get("file_name"))
    if not file_name:
        raise ValueError("binding['file_name'] is required")

    primary, fallback = _candidate_groups(file_name)

    device.open_app("Markor")
    device.settle(1)

    try:
        idx = _find_target(device, primary, fallback)
    except RuntimeError:
        device.navigate_back()
        device.settle(1)
        idx = _find_target(device, primary, fallback)

    _long_press(device, idx)

    if not _click_delete(device):
        _long_press(device, idx)
        if not _click_delete(device):
            idx2 = _find_target_current(device, primary + fallback)
            if idx2 is None:
                raise RuntimeError("Could not re-find note after failed delete selection")
            _long_press(device, idx2)
            if not _click_delete(device):
                raise RuntimeError("Delete control not found after selecting note")

    if not _click_ok(device):
        raise RuntimeError("Delete confirmation dialog was not confirmed")

    return True
