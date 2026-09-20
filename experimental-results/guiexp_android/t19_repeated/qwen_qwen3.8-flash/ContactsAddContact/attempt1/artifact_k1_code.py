PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name with exactly two words: first name and last name.",
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim into the new contact's Phone field.",
    },
}


def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise TypeError("binding must be a dict")
    if "name" not in binding or "number" not in binding:
        raise KeyError("binding must contain keys 'name' and 'number'")

    raw_name = binding["name"]
    raw_number = binding["number"]
    if raw_name is None or raw_number is None:
        raise ValueError("binding['name'] and binding['number'] must not be None")

    name = str(raw_name)
    number = str(raw_number)

    parts = name.split()
    if len(parts) != 2:
        raise ValueError("binding['name'] must contain exactly two words: first and last")
    first_name, last_name = parts

    def _element_matches(el, exact, contains, editable, clickable):
        if editable is not None and bool(el.get("editable")) != bool(editable):
            return False
        if clickable is not None and bool(el.get("clickable")) != bool(clickable):
            return False

        vals = []
        for key in ("hint", "text", "description"):
            val = el.get(key)
            if val is not None:
                vals.append(str(val))
        joined = " ".join(vals).lower()

        for value in exact:
            if str(value) in vals:
                return True
        for value in contains:
            if str(value).lower() in joined:
                return True
        return False

    def _find(exact=(), contains=(), editable=None, clickable=None):
        exact = tuple(exact)
        contains = tuple(contains)

        for value in exact:
            for attr in ("hint", "text", "description"):
                kwargs = {attr: value}
                if editable is not None:
                    kwargs["editable"] = editable
                if clickable is not None:
                    kwargs["clickable"] = clickable
                try:
                    idx = device.find(**kwargs)
                    if idx is not None:
                        return idx
                except Exception:
                    pass

        for value in contains:
            kwargs = {"contains": value}
            if editable is not None:
                kwargs["editable"] = editable
            if clickable is not None:
                kwargs["clickable"] = clickable
            try:
                idx = device.find(**kwargs)
                if idx is not None:
                    return idx
            except Exception:
                pass

        try:
            elements = device.elements()
            if elements is None:
                elements = []
        except Exception:
            elements = []

        for el in elements:
            if _element_matches(el, exact, contains, editable, clickable):
                idx = el.get("index")
                if idx is not None:
                    return idx
        return None

    def _find_with_scroll(finder, directions=("down", "down", "up", "up")):
        idx = finder()
        if idx is not None:
            return idx
        for direction in directions:
            try:
                device.scroll(direction=direction)
            except Exception:
                pass
            device.settle(0.5)
            idx = finder()
            if idx is not None:
                return idx
        return None

    def _find_create_contact():
        idx = _find(
            exact=("Create contact",),
            contains=("create contact", "add contact"),
            clickable=True,
        )
        if idx is not None:
            return idx
        return _find(
            exact=("Create contact",),
            contains=("create contact", "add contact"),
        )

    def _find_save():
        idx = _find(exact=("Save",), contains=("save",), clickable=True)
        if idx is not None:
            return idx
        return _find(exact=("Save",), contains=("save",))

    def _input_editable(value, exact, contains, error, directions=("down", "down", "up")):
        idx = _find_with_scroll(
            lambda: _find(exact=exact, contains=contains, editable=True),
            directions=directions,
        )
        if idx is None:
            raise RuntimeError(error)
        device.input_text(value, index=idx)
        device.settle(0.5)

    def _open_to_editor():
        first_exact = ("First name",)
        first_contains = ("first name",)
        last_error = None

        for app_name in ("Contacts", "Google Contacts", "People"):
            try:
                device.open_app(app_name)
                device.settle(2.0)

                for _ in range(2):
                    create_idx = _find_with_scroll(
                        _find_create_contact,
                        directions=("up", "down", "down"),
                    )
                    if create_idx is not None:
                        device.click(index=create_idx)
                        device.settle(2.0)

                    first_idx = _find_with_scroll(
                        lambda: _find(
                            exact=first_exact,
                            contains=first_contains,
                            editable=True,
                        ),
                        directions=("down", "down", "up"),
                    )
                    if first_idx is not None:
                        return first_idx
            except Exception as exc:
                last_error = exc
                continue

        raise RuntimeError("Could not reach the contact editor") from last_error

    first_idx = _open_to_editor()
    device.input_text(first_name, index=first_idx)
    device.settle(0.5)

    _input_editable(
        last_name,
        exact=("Last name",),
        contains=("last name",),
        error="Could not find the editable Last name field",
        directions=("down", "down", "up"),
    )

    _input_editable(
        number,
        exact=("Phone", "Phone number"),
        contains=("phone",),
        error="Could not find the editable Phone field",
        directions=("down", "down", "up"),
    )

    save_idx = _find_with_scroll(
        _find_save,
        directions=("up", "up", "down", "down"),
    )
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")

    device.click(index=save_idx)
    device.settle(2.0)
    return True
