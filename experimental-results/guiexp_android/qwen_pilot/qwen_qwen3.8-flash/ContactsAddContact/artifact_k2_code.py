PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name with exactly two words (first name and last name).",
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
    },
}


def _text(value):
    return "" if value is None else str(value)


def _flag(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _normalize_index(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _matches(el, text=None, contains=None, hint=None, description=None, clickable=None, editable=None):
    if text is not None and _text(el.get("text")) != text:
        return False
    if hint is not None and _text(el.get("hint")) != hint:
        return False
    if description is not None and _text(el.get("description")) != description:
        return False
    if contains is not None:
        needle = _text(contains).lower()
        if not any(needle in _text(el.get(attr)).lower() for attr in ("text", "hint", "description")):
            return False
    if clickable is not None and _flag(el.get("clickable")) != clickable:
        return False
    if editable is not None and _flag(el.get("editable")) != editable:
        return False
    return True


def _find_element(device, **criteria):
    try:
        idx = device.find(**criteria)
        if idx is not None:
            return _normalize_index(idx)
    except Exception:
        pass

    try:
        elements = device.elements()
    except Exception:
        return None

    for el in elements:
        if _matches(el, **criteria):
            idx = _normalize_index(el.get("index"))
            if idx is not None:
                return idx
    return None


def _find_with_scroll(device, direction="down", max_scrolls=2, **criteria):
    idx = _find_element(device, **criteria)
    if idx is not None:
        return idx

    for _ in range(max_scrolls):
        try:
            device.scroll(direction=direction)
        except Exception:
            break
        idx = _find_element(device, **criteria)
        if idx is not None:
            return idx

    opposite = "up" if direction == "down" else "down"
    for _ in range(max_scrolls):
        try:
            device.scroll(direction=opposite)
        except Exception:
            break
        idx = _find_element(device, **criteria)
        if idx is not None:
            return idx

    return None


def _find_editable_field(device, labels):
    short_labels = []
    for label in labels:
        parts = label.split()
        token = parts[0] if parts else label
        if token not in short_labels:
            short_labels.append(token)

    for label in labels:
        idx = _find_element(device, hint=label, editable=True)
        if idx is not None:
            return idx
        idx = _find_element(device, text=label, editable=True)
        if idx is not None:
            return idx

    for label in labels:
        idx = _find_element(device, contains=label, editable=True)
        if idx is not None:
            return idx

    for label in short_labels:
        idx = _find_element(device, contains=label, editable=True)
        if idx is not None:
            return idx

    for label in labels:
        idx = _find_element(device, hint=label)
        if idx is not None:
            return idx
        idx = _find_element(device, text=label)
        if idx is not None:
            return idx

    for label in short_labels:
        idx = _find_element(device, hint=label)
        if idx is not None:
            return idx
        idx = _find_element(device, text=label)
        if idx is not None:
            return idx

    for direction in ("down", "up"):
        for label in labels + short_labels:
            idx = _find_with_scroll(device, direction=direction, max_scrolls=2, contains=label, editable=True)
            if idx is not None:
                return idx

    for direction in ("down", "up"):
        for label in labels + short_labels:
            idx = _find_with_scroll(device, direction=direction, max_scrolls=2, contains=label)
            if idx is not None:
                return idx

    return None


def _find_clickable_label(device, labels):
    short_labels = []
    for label in labels:
        parts = label.split()
        token = parts[0] if parts else label
        if token not in short_labels:
            short_labels.append(token)

    for label in labels:
        idx = _find_element(device, description=label, clickable=True)
        if idx is not None:
            return idx
        idx = _find_element(device, text=label, clickable=True)
        if idx is not None:
            return idx
        idx = _find_element(device, contains=label, clickable=True)
        if idx is not None:
            return idx

    for label in short_labels:
        idx = _find_element(device, contains=label, clickable=True)
        if idx is not None:
            return idx

    try:
        elements = device.elements()
    except Exception:
        elements = []

    for label in labels + short_labels:
        needle = label.lower()
        for el in elements:
            if not _flag(el.get("clickable")):
                continue
            for attr in ("description", "text", "hint"):
                if needle in _text(el.get(attr)).lower():
                    idx = _normalize_index(el.get("index"))
                    if idx is not None:
                        return idx

    return None


def _input_with_retry(device, labels, text, error_message):
    for _ in range(3):
        idx = _find_editable_field(device, labels)
        if idx is not None:
            device.input_text(text, index=idx)
            device.settle(1.0)
            return
        device.settle(1.0)
    raise RuntimeError(error_message)


def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise TypeError("binding must be a dict")
    if "name" not in binding or "number" not in binding:
        raise KeyError("binding must contain keys 'name' and 'number'")

    raw_name = binding["name"]
    raw_number = binding["number"]

    if raw_name is None:
        raise ValueError("binding['name'] is missing")
    if raw_number is None:
        raise ValueError("binding['number'] is missing")

    name = _text(raw_name).strip()
    number = _text(raw_number)

    parts = name.split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")

    first_name = parts[0]
    last_name = parts[1]

    device.open_app("Contacts")
    device.settle(1.0)

    create_labels = ["Create contact", "Add contact", "Create"]
    create_idx = None
    for attempt in range(3):
        create_idx = _find_clickable_label(device, create_labels)
        if create_idx is not None:
            break

        if attempt == 0:
            try:
                device.navigate_home()
                device.settle(1.0)
                device.open_app("Contacts")
                device.settle(1.0)
            except Exception:
                pass
        else:
            device.settle(1.0)

    if create_idx is None:
        raise RuntimeError("Could not find the Create contact button")

    device.click(index=create_idx)
    device.settle(1.0)

    _input_with_retry(
        device,
        ["First name", "First"],
        first_name,
        "Could not find the First name field",
    )

    _input_with_retry(
        device,
        ["Last name", "Last"],
        last_name,
        "Could not find the Last name field",
    )

    _input_with_retry(
        device,
        ["Phone", "Phone number"],
        number,
        "Could not find the Phone field",
    )

    save_idx = _find_clickable_label(device, ["Save", "Done"])
    if save_idx is None:
        save_idx = _find_with_scroll(device, direction="up", max_scrolls=2, contains="Save", clickable=True)
    if save_idx is None:
        save_idx = _find_with_scroll(device, direction="up", max_scrolls=2, contains="Done", clickable=True)
    if save_idx is None:
        save_idx = _find_with_scroll(device, direction="down", max_scrolls=2, contains="Save", clickable=True)
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")

    device.click(index=save_idx)
    device.settle(1.0)
    return True
