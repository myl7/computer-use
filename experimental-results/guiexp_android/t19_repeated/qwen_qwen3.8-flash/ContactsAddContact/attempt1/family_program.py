PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two whitespace-separated words (first and last).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim into the Phone field.",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    if "name" not in binding or "number" not in binding:
        raise ValueError("binding must contain 'name' and 'number'")
    if binding["name"] is None or binding["number"] is None:
        raise ValueError("binding['name'] and binding['number'] must not be None")

    name = str(binding["name"])
    number = str(binding["number"])
    parts = name.split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")
    if not number.strip():
        raise ValueError("binding['number'] must not be empty")

    first_name, last_name = parts[0], parts[1]

    def _lower(value):
        return str(value).lower() if value is not None else ""

    def _combined(el):
        return " ".join(
            [
                _lower(el.get("text")),
                _lower(el.get("hint")),
                _lower(el.get("description")),
            ]
        )

    def _find_clickable(labels):
        for label in labels:
            idx = device.find(description=label, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(contains=label, clickable=True)
            if idx is not None:
                return idx

        for el in device.elements():
            if not el.get("clickable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            if any(label.lower() in _combined(el) for label in labels):
                return idx

        return None

    def _find_editable_once(labels, fallback_order=None):
        for label in labels:
            idx = device.find(hint=label, editable=True)
            if idx is not None:
                return idx
            idx = device.find(text=label, editable=True)
            if idx is not None:
                return idx
            idx = device.find(contains=label, editable=True)
            if idx is not None:
                return idx

        editable_elements = []
        for el in device.elements():
            if not el.get("editable"):
                continue
            idx = el.get("index")
            if idx is None:
                continue
            editable_elements.append(el)
            if any(label.lower() in _combined(el) for label in labels):
                return idx

        if fallback_order is not None and fallback_order < len(editable_elements):
            editable_elements.sort(key=lambda item: item.get("index", 0))
            return editable_elements[fallback_order].get("index")

        return None

    def _find_editable(labels, fallback_order=None):
        idx = _find_editable_once(labels, fallback_order)
        if idx is not None:
            return idx

        for direction in ("down", "up", "down"):
            device.scroll(direction=direction)
            idx = _find_editable_once(labels, fallback_order)
            if idx is not None:
                return idx

        return None

    def _looks_like_editor():
        if _find_clickable(["Save", "Save contact"]) is not None:
            return True

        editable = [
            el
            for el in device.elements()
            if el.get("editable") and el.get("index") is not None
        ]
        return len(editable) >= 3

    device.open_app("Contacts")
    device.settle(1.0)

    create_idx = _find_clickable(["Create contact", "Add contact", "New contact"])
    if create_idx is None:
        if not _looks_like_editor():
            raise RuntimeError("Could not find the Create contact button")
    else:
        device.click(index=create_idx)
        device.settle(1.0)
        if (
            _find_clickable(["Create contact", "Add contact", "New contact"]) is not None
            and not _looks_like_editor()
        ):
            raise RuntimeError("Create contact did not open the contact editor")

    first_idx = _find_editable(["First name", "First"], fallback_order=0)
    if first_idx is None:
        raise RuntimeError("Could not find the First name field")
    device.input_text(first_name, index=first_idx)

    last_idx = _find_editable(["Last name", "Last"], fallback_order=1)
    if last_idx is None:
        raise RuntimeError("Could not find the Last name field")
    device.input_text(last_name, index=last_idx)

    phone_idx = _find_editable(["Phone", "Phone number", "Mobile"], fallback_order=2)
    if phone_idx is None:
        raise RuntimeError("Could not find the Phone field")
    device.input_text(number, index=phone_idx)

    save_idx = _find_clickable(["Save", "Save contact"])
    if save_idx is None:
        device.scroll(direction="up")
        save_idx = _find_clickable(["Save", "Save contact"])
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")

    device.click(index=save_idx)
    device.settle(1.0)
    return True
