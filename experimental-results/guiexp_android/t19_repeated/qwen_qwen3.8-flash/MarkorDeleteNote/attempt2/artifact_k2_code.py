PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
    }
}


def _clean(value):
    return str(value if value is not None else "").strip()


def _norm_index(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return value


def _safe_elements(device):
    try:
        elements = device.elements()
    except Exception:
        return []
    if elements is None:
        return []
    try:
        return list(elements)
    except Exception:
        return []


def _find(device, **kwargs):
    try:
        return _norm_index(device.find(**kwargs))
    except TypeError:
        allowed = {"text", "contains", "description", "clickable", "editable"}
        filtered = {k: v for k, v in kwargs.items() if k in allowed}
        if not filtered:
            return None
        try:
            return _norm_index(device.find(**filtered))
        except TypeError:
            return None


def _primary_fallback(file_name):
    file_name = _clean(file_name)
    base = file_name.replace("\\", "/").split("/")[-1]

    primary = []
    fallback = []

    def add(target, value):
        value = _clean(value)
        if value and value not in target:
            target.append(value)

    add(primary, file_name)
    add(primary, base)

    if "." in file_name:
        add(fallback, file_name.rsplit(".", 1)[0])
    else:
        add(fallback, file_name + ".md")

    if "." in base:
        add(fallback, base.rsplit(".", 1)[0])
    else:
        add(fallback, base + ".md")

    return primary, fallback


def _matches_title(element, title):
    title = _clean(title)
    if not title:
        return False

    desc = _clean(element.get("description"))
    if desc.lower().startswith("folder "):
        return False

    text = _clean(element.get("text"))
    hint = _clean(element.get("hint"))

    if text == title or hint == title:
        return True
    if desc == f"File {title}":
        return True

    lowered = title.lower()
    if text.lower() == lowered or hint.lower() == lowered:
        return True
    if desc.lower() == f"file {lowered}":
        return True
    if desc.lower().startswith("file "):
        return _clean(desc[5:]).lower() == lowered

    return False


def _find_file_index_candidates(device, candidates):
    for candidate in candidates:
        idx = _find(device, description=f"File {candidate}", clickable=True)
        if idx is not None:
            return idx

    elements = _safe_elements(device)
    best = None

    for candidate_index, candidate in enumerate(candidates):
        for element_index, element in enumerate(elements):
            if not _matches_title(element, candidate):
                continue

            desc = _clean(element.get("description"))
            if desc.lower().startswith("file "):
                type_priority = 0
            else:
                type_priority = 1 if element.get("clickable") else 2

            score = (candidate_index, type_priority, element_index)
            idx = _norm_index(element.get("index"))
            if idx is not None and (best is None or score < best[0]):
                best = (score, idx)

    return best[1] if best else None


def _file_titles(device):
    titles = []
    for element in _safe_elements(device):
        desc = _clean(element.get("description"))
        if desc.lower().startswith("file "):
            title = _clean(desc[5:])
            if title:
                titles.append(title)
    return titles


def _scroll_for_candidates(device, candidates, direction="down", max_scrolls=40):
    idx = _find_file_index_candidates(device, candidates)
    if idx is not None:
        return idx

    seen = {title.lower() for title in _file_titles(device)}
    has_titles = bool(seen)

    for _ in range(max_scrolls):
        device.scroll(direction=direction)
        idx = _find_file_index_candidates(device, candidates)
        if idx is not None:
            return idx

        if not has_titles:
            continue

        changed = False
        for title in _file_titles(device):
            lowered = title.lower()
            if lowered not in seen:
                seen.add(lowered)
                changed = True

        if not changed:
            break

    return None


def _scroll_to_top(device, max_scrolls=40):
    seen = {title.lower() for title in _file_titles(device)}
    has_titles = bool(seen)

    for _ in range(max_scrolls):
        device.scroll(direction="up")

        if not has_titles:
            continue

        changed = False
        for title in _file_titles(device):
            lowered = title.lower()
            if lowered not in seen:
                seen.add(lowered)
                changed = True

        if not changed:
            break


def _find_target(device, file_name):
    primary, fallback = _primary_fallback(file_name)

    idx = _scroll_for_candidates(device, primary, direction="down")
    if idx is not None:
        return idx

    _scroll_to_top(device)
    idx = _scroll_for_candidates(device, fallback, direction="down")
    if idx is not None:
        return idx

    all_candidates = primary + [c for c in fallback if c not in primary]
    idx = _find_file_index_candidates(device, all_candidates)
    if idx is not None:
        return idx

    return _scroll_for_candidates(device, all_candidates, direction="up", max_scrolls=20)


def _long_press(device, idx):
    actions = (
        {"action_type": "long_press", "index": idx},
        {"action_type": "long_press", "element_index": idx},
    )

    last_error = None
    for action in actions:
        try:
            device.execute(action)
            device.settle(1.0)
            return
        except Exception as exc:
            last_error = exc

    if hasattr(device, "long_press"):
        try:
            device.long_press(index=idx)
            device.settle(1.0)
            return
        except TypeError:
            try:
                device.long_press(idx)
                device.settle(1.0)
                return
            except Exception as exc:
                last_error = exc
        except Exception as exc:
            last_error = exc

    raise RuntimeError("Unable to long-press target row") from last_error


def _find_exact(device, *names):
    lowered_names = {name.lower() for name in names}

    for name in names:
        idx = _find(device, description=name, clickable=True)
        if idx is not None:
            return idx
        idx = _find(device, text=name, clickable=True)
        if idx is not None:
            return idx

    elements = _safe_elements(device)

    for prefer_clickable in (True, False):
        for element in elements:
            if prefer_clickable and not element.get("clickable"):
                continue
            for key in ("description", "text", "hint"):
                if _clean(element.get(key)).lower() in lowered_names:
                    idx = _norm_index(element.get("index"))
                    if idx is not None:
                        return idx

    for element in elements:
        for key in ("description", "text", "hint"):
            if _clean(element.get(key)).lower() in lowered_names:
                idx = _norm_index(element.get("index"))
                if idx is not None:
                    return idx

    return None


def _find_delete(device):
    idx = _find_exact(device, "Delete")
    if idx is not None:
        return idx

    more_idx = _find_exact(device, "More options")
    if more_idx is not None:
        device.click(index=more_idx)
        device.settle(1.0)

        idx = _find_exact(device, "Delete")
        if idx is not None:
            return idx

    idx = _find(device, contains="Delete", clickable=True)
    if idx is not None:
        return idx

    idx = _find(device, description="Delete")
    if idx is not None:
        return idx

    idx = _find(device, text="Delete")
    if idx is not None:
        return idx

    elements = _safe_elements(device)
    for prefer_clickable in (True, False):
        for element in elements:
            if prefer_clickable and not element.get("clickable"):
                continue
            for key in ("description", "text", "hint"):
                if "delete" in _clean(element.get(key)).lower():
                    idx = _norm_index(element.get("index"))
                    if idx is not None:
                        return idx

    return None


def _click_delete(device):
    idx = _find_delete(device)
    if idx is None:
        raise RuntimeError("Delete action not found")
    device.click(index=idx)
    device.settle(1.0)


def _find_ok(device):
    idx = _find_exact(device, "OK", "Ok")
    if idx is not None:
        return idx

    idx = _find(device, text="OK", clickable=True)
    if idx is not None:
        return idx

    idx = _find(device, description="OK", clickable=True)
    if idx is not None:
        return idx

    elements = _safe_elements(device)
    for prefer_clickable in (True, False):
        for element in elements:
            if prefer_clickable and not element.get("clickable"):
                continue
            for key in ("text", "description", "hint"):
                if _clean(element.get(key)).lower() == "ok":
                    idx = _norm_index(element.get("index"))
                    if idx is not None:
                        return idx

    return None


def _confirm_delete(device):
    idx = None
    for _ in range(3):
        idx = _find_ok(device)
        if idx is not None:
            break
        device.settle(1.0)

    if idx is None:
        raise RuntimeError("Delete confirmation dialog not found")

    device.click(index=idx)
    device.settle(1.0)


def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")

    file_name = _clean(binding.get("file_name"))
    if not file_name:
        raise ValueError("binding['file_name'] is required")

    device.open_app("Markor")
    device.settle(1.0)

    target_idx = _find_target(device, file_name)
    if target_idx is None:
        raise RuntimeError(f"Could not find note: {file_name}")

    _long_press(device, target_idx)
    _click_delete(device)
    _confirm_delete(device)

    return True
