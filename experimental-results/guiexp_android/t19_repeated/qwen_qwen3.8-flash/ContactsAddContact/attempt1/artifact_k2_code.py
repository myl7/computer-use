PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words: first name and last name.",
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
    },
}


def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")

    name = binding.get("name")
    number = binding.get("number")

    if name is None:
        raise ValueError("binding['name'] is required")
    if number is None:
        raise ValueError("binding['number'] is required")

    name = str(name).strip()
    parts = name.split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")

    first_name = parts[0]
    last_name = parts[1]
    number = str(number)

    def _blob(el):
        parts = []
        for key in (
            "text",
            "hint",
            "description",
            "label",
            "contentDescription",
            "content-desc",
            "resource-id",
        ):
            val = el.get(key)
            if val is not None:
                parts.append(str(val))
        return " ".join(parts).lower()

    def _find_editable(terms):
        elements = device.elements()
        for el in elements:
            if not el.get("editable"):
                continue
            blob = _blob(el)
            if any(term in blob for term in terms):
                idx = el.get("index")
                if idx is not None:
                    return idx
        return None

    def _find_clickable(terms):
        elements = device.elements()
        matches = []

        for el in elements:
            if not el.get("clickable"):
                continue
            blob = _blob(el)
            if any(term in blob for term in terms):
                matches.append(el)

        if not matches:
            return None

        for el in matches:
            for key in ("text", "description", "hint"):
                value = str(el.get(key) or "").strip().lower()
                if value and value in terms:
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        idx = matches[0].get("index")
        return idx

    def _editable_at(position):
        elements = device.elements()
        editables = [
            el.get("index")
            for el in elements
            if el.get("editable") and el.get("index") is not None
        ]
        if len(editables) > position:
            return editables[position]
        return None

    def _next_editable_after(idx):
        if idx is None:
            return None

        elements = device.elements()
        editables = [
            el.get("index")
            for el in elements
            if el.get("editable") and el.get("index") is not None
        ]

        try:
            pos = editables.index(idx)
        except ValueError:
            return None

        if pos + 1 < len(editables):
            return editables[pos + 1]
        return None

    def _find_first_name():
        idx = _find_editable(["first name", "first"])
        if idx is not None:
            return idx
        return _editable_at(0)

    def _find_last_name():
        idx = _find_editable(["last name", "last"])
        if idx is not None:
            return idx

        idx = _next_editable_after(_find_first_name())
        if idx is not None:
            return idx

        return _editable_at(1)

    def _find_phone():
        idx = _find_editable(["phone", "telephone", "mobile"])
        if idx is not None:
            return idx

        idx = _next_editable_after(_find_last_name())
        if idx is not None:
            return idx

        return _editable_at(2)

    def _find_with_scroll(finder, direction="down", max_scrolls=3):
        idx = finder()
        if idx is not None:
            return idx

        for _ in range(max_scrolls):
            device.scroll(direction=direction)
            device.settle(0.5)
            idx = finder()
            if idx is not None:
                return idx

        return None

    def _input_field(text, finder, label):
        idx = _find_with_scroll(finder, direction="down", max_scrolls=3)
        if idx is None:
            raise RuntimeError(f"Could not find {label} field")

        device.input_text(text, index=idx)
        device.settle(0.5)

    device.open_app("Contacts")
    device.settle(1.5)

    def _find_create():
        idx = _find_clickable(["create contact", "add contact", "new contact", "create"])
        if idx is not None:
            return idx

        for kwargs in (
            {"description": "Create contact", "clickable": True},
            {"text": "Create contact", "clickable": True},
            {"hint": "Create contact", "clickable": True},
            {"contains": "Create contact", "clickable": True},
            {"description": "Add contact", "clickable": True},
            {"text": "Add contact", "clickable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        return None

    create_idx = _find_with_scroll(_find_create, direction="down", max_scrolls=2)

    if create_idx is None:
        elements = device.elements()
        for el in elements:
            if el.get("clickable") and el.get("index") is not None:
                create_idx = el.get("index")
                break

    if create_idx is None:
        raise RuntimeError("Could not find the Create contact button")

    device.click(index=create_idx)
    device.settle(1.5)

    _input_field(first_name, _find_first_name, "First name")
    _input_field(last_name, _find_last_name, "Last name")
    _input_field(number, _find_phone, "Phone")

    def _find_save():
        idx = _find_clickable(["save", "done", "complete"])
        if idx is not None:
            return idx

        for kwargs in (
            {"text": "Save", "clickable": True},
            {"description": "Save", "clickable": True},
            {"hint": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
            {"text": "Save"},
            {"description": "Save"},
            {"contains": "Save"},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        return None

    save_idx = _find_with_scroll(_find_save, direction="up", max_scrolls=3)

    if save_idx is None:
        raise RuntimeError("Could not find the Save button")

    device.click(index=save_idx)
    device.settle(1.5)

    return True
