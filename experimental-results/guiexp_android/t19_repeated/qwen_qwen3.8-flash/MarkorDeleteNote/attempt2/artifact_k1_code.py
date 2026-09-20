PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
    }
}


def _clean(value):
    return (value or "").strip()


def _candidate_names(file_name):
    file_name = _clean(file_name)
    if not file_name:
        return []

    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    names = []
    for name in (file_name, stem):
        name = _clean(name)
        if name and name not in names:
            names.append(name)
    return names


def _is_folder(element):
    return _clean(element.get("description")).lower().startswith("folder ")


def _is_file_element(element):
    return _clean(element.get("description")).lower().startswith("file ")


def _matches_exact(value, candidates):
    value = _clean(value)
    if not value:
        return False

    for name in candidates:
        if value == name or value == f"File {name}":
            return True
    return False


def _find_file_row(device, file_name):
    candidates = _candidate_names(file_name)
    if not candidates:
        raise ValueError("binding['file_name'] is missing")

    elements = device.elements()

    clickable_idx = None
    any_idx = None
    for element in elements:
        idx = element.get("index")
        if idx is None or _is_folder(element):
            continue

        fields = (
            element.get("text"),
            element.get("description"),
            element.get("hint"),
        )
        if any(_matches_exact(field, candidates) for field in fields):
            if element.get("clickable") and clickable_idx is None:
                clickable_idx = idx
            if any_idx is None:
                any_idx = idx

    if clickable_idx is not None:
        return clickable_idx
    if any_idx is not None:
        return any_idx

    lower_candidates = [name.lower() for name in candidates]
    lower_clickable = []
    lower_any = []
    for element in elements:
        idx = element.get("index")
        if idx is None or _is_folder(element):
            continue

        fields = [
            _clean(element.get("text")).lower(),
            _clean(element.get("description")).lower(),
            _clean(element.get("hint")).lower(),
        ]
        if any(
            field == name or field == f"file {name}"
            for field in fields
            for name in lower_candidates
        ):
            if element.get("clickable"):
                lower_clickable.append(idx)
            else:
                lower_any.append(idx)

    if len(lower_clickable) == 1:
        return lower_clickable[0]
    if len(lower_any) == 1:
        return lower_any[0]

    for name in candidates:
        idx = device.find(description=f"File {name}", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text=name, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(hint=name, clickable=True)
        if idx is not None:
            return idx

    return None


def _screen_signature(device):
    try:
        return tuple(
            sorted(
                (
                    str(element.get("text") or ""),
                    str(element.get("description") or ""),
                    str(element.get("hint") or ""),
                )
                for element in device.elements()
            )
        )
    except Exception:
        return None


def _scroll_until(device, finder, direction, max_scrolls):
    idx = finder()
    if idx is not None:
        return idx

    last_sig = _screen_signature(device)
    for _ in range(max_scrolls):
        device.scroll(direction=direction)
        idx = finder()
        if idx is not None:
            return idx

        sig = _screen_signature(device)
        if sig is not None and sig == last_sig:
            break
        last_sig = sig

    return None


def _looks_like_markor(device):
    return any(
        [
            device.find(text="Markor") is not None,
            device.find(description="Files") is not None,
            device.find(description="Create a new file or folder") is not None,
        ]
    )


def _long_press(device, idx):
    if hasattr(device, "long_press"):
        device.long_press(index=idx)
    else:
        device.execute({"action_type": "long_press", "index": idx})


def _find_delete_action(device):
    for _ in range(3):
        elements = device.elements()

        for element in elements:
            idx = element.get("index")
            if idx is None or not element.get("clickable") or _is_file_element(element):
                continue

            fields = (
                _clean(element.get("description")),
                _clean(element.get("hint")),
                _clean(element.get("text")),
            )
            if any(field == "Delete" or field.lower() == "delete" for field in fields):
                return idx

        for element in elements:
            idx = element.get("index")
            if idx is None or not element.get("clickable") or _is_file_element(element):
                continue

            desc = _clean(element.get("description")).lower()
            hint = _clean(element.get("hint")).lower()
            text = _clean(element.get("text")).lower()
            if "delete" in desc or "delete" in hint or text == "delete":
                return idx

        for element in elements:
            idx = element.get("index")
            if idx is None or _is_file_element(element):
                continue

            fields = (
                _clean(element.get("description")),
                _clean(element.get("hint")),
                _clean(element.get("text")),
            )
            if any(field == "Delete" or field.lower() == "delete" for field in fields):
                return idx

        idx = device.find(description="Delete", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(hint="Delete", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Delete", clickable=True)
        if idx is not None:
            return idx

        device.settle(0.5)

    return None


def _dialog_present(elements):
    for element in elements:
        if _is_file_element(element):
            continue

        fields = (
            _clean(element.get("text")).lower(),
            _clean(element.get("description")).lower(),
            _clean(element.get("hint")).lower(),
        )
        if any(field in ("cancel", "discard", "no") for field in fields):
            return True
    return False


def _find_confirm_button(device):
    for _ in range(5):
        elements = device.elements()

        ok_idx = None
        for element in elements:
            idx = element.get("index")
            if idx is None or not element.get("clickable") or _is_file_element(element):
                continue

            fields = (
                _clean(element.get("text")),
                _clean(element.get("description")),
                _clean(element.get("hint")),
            )
            if any(field == "OK" or field.lower() == "ok" for field in fields):
                ok_idx = idx

        if ok_idx is not None:
            return ok_idx

        ok_idx = None
        for element in elements:
            idx = element.get("index")
            if idx is None or _is_file_element(element):
                continue

            fields = (
                _clean(element.get("text")),
                _clean(element.get("description")),
                _clean(element.get("hint")),
            )
            if any(field == "OK" or field.lower() == "ok" for field in fields):
                ok_idx = idx

        if ok_idx is not None:
            return ok_idx

        if _dialog_present(elements):
            cand = None
            for element in elements:
                idx = element.get("index")
                if idx is None or not element.get("clickable") or _is_file_element(element):
                    continue

                text = _clean(element.get("text")).upper()
                desc = _clean(element.get("description")).lower()
                hint = _clean(element.get("hint")).lower()

                if text == "DELETE" and desc != "delete":
                    cand = idx
                elif text in ("YES", "CONFIRM") or desc in ("yes", "confirm") or hint in ("yes", "confirm"):
                    cand = idx

            if cand is not None:
                return cand

            cand = None
            for element in elements:
                idx = element.get("index")
                if idx is None or not element.get("clickable") or _is_file_element(element):
                    continue

                text = _clean(element.get("text")).upper()
                if text in ("DELETE", "YES", "CONFIRM"):
                    cand = idx

            if cand is not None:
                return cand

        idx = device.find(text="OK", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="OK", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(hint="OK", clickable=True)
        if idx is not None:
            return idx

        if _dialog_present(device.elements()):
            idx = device.find(text="DELETE", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(text="YES", clickable=True)
            if idx is not None:
                return idx
            idx = device.find(text="CONFIRM", clickable=True)
            if idx is not None:
                return idx

        device.settle(0.5)

    return None


def program(device, binding: dict) -> bool:
    raw_file_name = binding.get("file_name", "")
    if raw_file_name is None:
        raise ValueError("binding['file_name'] is missing")

    file_name = str(raw_file_name).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is missing")

    device.open_app("Markor")
    device.settle(1.0)

    if not _looks_like_markor(device):
        device.navigate_back()
        device.settle(1.0)
        if not _looks_like_markor(device) and _find_file_row(device, file_name) is None:
            raise RuntimeError("Markor did not open to a recognizable screen")

    def finder():
        return _find_file_row(device, file_name)

    idx = _scroll_until(device, finder, "down", 30)
    if idx is None:
        idx = _scroll_until(device, finder, "up", 30)

    if idx is None:
        raise ValueError(f"Could not find note {file_name!r}")

    delete_idx = None
    for _ in range(3):
        _long_press(device, idx)
        device.settle(0.5)

        delete_idx = _find_delete_action(device)
        if delete_idx is not None:
            break

        new_idx = finder()
        if new_idx is None:
            new_idx = _scroll_until(device, finder, "down", 10)
        if new_idx is None:
            break
        idx = new_idx

    if delete_idx is None:
        raise RuntimeError("Clickable Delete control not found after selecting the target note")

    device.click(index=delete_idx)
    device.settle(0.5)

    ok_idx = _find_confirm_button(device)
    if ok_idx is None:
        device.settle(1.0)
        ok_idx = _find_confirm_button(device)

    if ok_idx is None:
        raise RuntimeError("Delete confirmation OK button not found")

    device.click(index=ok_idx)
    device.settle(0.5)

    return True
