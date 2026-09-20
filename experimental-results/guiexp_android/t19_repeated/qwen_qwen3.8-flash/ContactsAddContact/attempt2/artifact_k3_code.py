PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two whitespace-separated words (first name and last name).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
        "required": True,
    },
}

def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise TypeError("binding must be a dict")
    if "name" not in binding or "number" not in binding:
        raise KeyError("binding must contain 'name' and 'number'")

    name = binding["name"]
    number = binding["number"]
    if name is None:
        raise ValueError("binding['name'] must not be None")
    if number is None:
        raise ValueError("binding['number'] must not be None")

    parts = str(name).split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")
    first_name = parts[0]
    last_name = parts[1]
    number_str = str(number)

    def _is_true(value):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _elem_index(elem):
        idx = elem.get("index")
        if idx is None:
            return None
        try:
            return int(idx)
        except (TypeError, ValueError):
            return idx

    def _scroll(direction):
        try:
            device.scroll(direction=direction)
        except Exception:
            pass

    def _find_first(candidates, retries=3, scroll_steps=None):
        attempts = max(1, retries)
        for attempt in range(attempts):
            for crit in candidates:
                idx = device.find(**crit)
                if idx is not None:
                    try:
                        return int(idx)
                    except (TypeError, ValueError):
                        return idx
            if attempt < attempts - 1:
                if scroll_steps:
                    _scroll(scroll_steps[attempt % len(scroll_steps)])
                device.settle(0.3)
        return None

    def _manual_find_editable(label):
        wanted = label.lower()
        for elem in (device.elements() or []):
            if not _is_true(elem.get("editable")):
                continue
            for attr in ("hint", "text", "description"):
                value = elem.get(attr)
                if value and wanted in str(value).lower():
                    return _elem_index(elem)
        return None

    def _find_editable_field(label):
        candidates = [
            {"hint": label, "editable": True},
            {"text": label, "editable": True},
            {"contains": label, "editable": True},
        ]
        idx = _find_first(candidates, retries=4, scroll_steps=("down", "up", "down"))
        if idx is not None:
            return idx
        return _manual_find_editable(label)

    def _manual_find_clickable(*keywords):
        for elem in (device.elements() or []):
            if not _is_true(elem.get("clickable")):
                continue
            combined = " ".join(
                str(elem.get(attr) or "")
                for attr in ("text", "description", "hint")
            ).lower()
            if all(word in combined for word in keywords):
                return _elem_index(elem)
        return None

    def _find_create_contact():
        candidates = [
            {"description": "Create contact", "clickable": True},
            {"text": "Create contact", "clickable": True},
            {"contains": "Create contact", "clickable": True},
        ]
        idx = _find_first(candidates, retries=3, scroll_steps=("down", "up"))
        if idx is not None:
            return idx
        idx = _manual_find_clickable("create", "contact")
        if idx is not None:
            return idx
        idx = _manual_find_clickable("add", "contact")
        if idx is not None:
            return idx
        idx = _find_first(
            [
                {"description": "Create contact"},
                {"text": "Create contact"},
            ],
            retries=1,
        )
        return idx

    def _find_save():
        candidates = [
            {"text": "Save", "clickable": True},
            {"description": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ]
        idx = _find_first(candidates, retries=3, scroll_steps=("up", "down"))
        if idx is not None:
            return idx
        idx = _manual_find_clickable("save")
        if idx is not None:
            return idx
        idx = _find_first(
            [
                {"text": "Save"},
                {"description": "Save"},
            ],
            retries=1,
        )
        return idx

    def _editor_visible():
        return (
            device.find(hint="First name", editable=True) is not None
            or device.find(text="First name", editable=True) is not None
            or device.find(hint="Last name", editable=True) is not None
            or device.find(text="Last name", editable=True) is not None
            or device.find(text="Save", clickable=True) is not None
            or device.find(description="Save", clickable=True) is not None
            or device.find(text="Save") is not None
            or device.find(description="Save") is not None
        )

    def _home_visible():
        return (
            device.find(description="Create contact", clickable=True) is not None
            or device.find(text="Create contact", clickable=True) is not None
            or device.find(text="No contacts yet") is not None
            or device.find(description="Open navigation drawer") is not None
        )

    app_name = binding.get("app_name") or "Contacts"
    device.open_app(app_name)
    device.settle(1.0)

    for _ in range(8):
        if _editor_visible() or _home_visible():
            break
        device.settle(0.5)

    if not _editor_visible():
        opened = False
        for _ in range(5):
            create_idx = _find_create_contact()
            if create_idx is not None:
                device.click(index=create_idx)
                device.settle(1.0)
            if _editor_visible():
                opened = True
                break
            device.settle(0.5)
        if not opened:
            raise RuntimeError("Could not open the contact editor")

    idx = _find_editable_field("First name")
    if idx is None:
        raise RuntimeError("Could not find the editable First name field")
    device.input_text(first_name, index=idx)
    device.settle(0.5)

    idx = _find_editable_field("Last name")
    if idx is None:
        raise RuntimeError("Could not find the editable Last name field")
    device.input_text(last_name, index=idx)
    device.settle(0.5)

    idx = _find_editable_field("Phone")
    if idx is None:
        raise RuntimeError("Could not find the editable Phone field")
    device.input_text(number_str, index=idx)
    device.settle(0.5)

    save_idx = _find_save()
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")
    device.click(index=save_idx)
    device.settle(1.0)

    for _ in range(5):
        if _home_visible() or not _editor_visible():
            return True
        save_idx = _find_save()
        if save_idx is not None:
            device.click(index=save_idx)
        device.settle(1.0)

    if _home_visible() or not _editor_visible():
        return True
    raise RuntimeError("Contact editor remained open after Save")
