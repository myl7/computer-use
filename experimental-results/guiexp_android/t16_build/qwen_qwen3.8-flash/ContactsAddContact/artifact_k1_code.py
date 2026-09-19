PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name with exactly two words: first and last.",
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
    },
}


def program(device, binding: dict) -> bool:
    def _lower(value):
        return str(value).lower() if value is not None else ""

    def _haystack(element):
        return " ".join(
            (
                _lower(element.get("text")),
                _lower(element.get("hint")),
                _lower(element.get("description")),
            )
        )

    def _safe_find(**kwargs):
        try:
            return device.find(**kwargs)
        except Exception:
            return None

    def _find_editable(terms):
        for term in terms:
            for kwargs in (
                {"hint": term, "editable": True},
                {"text": term, "editable": True},
                {"description": term, "editable": True},
                {"contains": term, "editable": True},
            ):
                idx = _safe_find(**kwargs)
                if idx is not None:
                    return idx

        for element in device.elements():
            if not element.get("editable"):
                continue
            haystack = _haystack(element)
            for term in terms:
                if _lower(term) in haystack:
                    return element.get("index")
        return None

    def _find_clickable(terms):
        for term in terms:
            for kwargs in (
                {"text": term, "clickable": True},
                {"description": term, "clickable": True},
                {"contains": term, "clickable": True},
            ):
                idx = _safe_find(**kwargs)
                if idx is not None:
                    return idx

        for element in device.elements():
            if not element.get("clickable"):
                continue
            haystack = _haystack(element)
            for term in terms:
                if _lower(term) in haystack:
                    return element.get("index")
        return None

    def _find_any(terms):
        for element in device.elements():
            haystack = _haystack(element)
            for term in terms:
                if _lower(term) in haystack:
                    return element.get("index")
        return None

    def _first_clickable():
        for element in device.elements():
            if element.get("clickable"):
                return element.get("index")
        return None

    def _first_element_index():
        elements = device.elements()
        if elements:
            return elements[0].get("index")
        return None

    create_terms = ("create contact", "add contact", "new contact")
    editor_terms = ("first name", "last name", "phone")

    def _open_contacts():
        app_names = []
        for candidate in (binding.get("app_name"), "Contacts", "Google Contacts"):
            if candidate and candidate not in app_names:
                app_names.append(candidate)

        last_exc = None
        for app_name in app_names:
            try:
                device.open_app(app_name)
                device.settle(1.0)
                last_exc = None
                if _find_clickable(create_terms) is not None:
                    return
                if _find_editable(editor_terms) is not None:
                    return
            except Exception as exc:
                last_exc = exc

        if last_exc is not None:
            raise last_exc

    def _locate_create():
        idx = _find_clickable(create_terms)
        if idx is None:
            idx = _find_any(create_terms)
        return idx

    _open_contacts()

    create_idx = _locate_create()
    in_editor = _find_editable(editor_terms) is not None

    if create_idx is None and in_editor:
        for _ in range(2):
            if _find_editable(editor_terms) is None:
                break
            device.navigate_back()
            device.settle(1.0)

        _open_contacts()
        create_idx = _locate_create()
        in_editor = _find_editable(editor_terms) is not None

    if create_idx is not None:
        device.click(index=create_idx)
        device.settle(1.0)

        if _find_editable(editor_terms) is None:
            alt_idx = _first_clickable()
            if alt_idx is None:
                alt_idx = _first_element_index()
            if alt_idx is not None and alt_idx != create_idx:
                device.click(index=alt_idx)
                device.settle(1.0)

            if _find_editable(editor_terms) is None:
                raise RuntimeError("Contact editor did not open")

    elif in_editor:
        pass

    else:
        fallback_idx = _first_clickable()
        if fallback_idx is None:
            fallback_idx = _first_element_index()
        if fallback_idx is None:
            raise RuntimeError("Could not find the Create contact control")

        device.click(index=fallback_idx)
        device.settle(1.0)

        if _find_editable(editor_terms) is None:
            raise RuntimeError("Contact editor did not open")

    name_parts = str(binding["name"]).split()
    if not name_parts:
        raise ValueError("binding['name'] must contain a name")

    first_name = name_parts[0]
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
    number = str(binding["number"])

    def _find_field(terms):
        idx = _find_editable(terms)
        if idx is not None:
            return idx

        for direction in ("down", "up"):
            device.scroll(direction)
            idx = _find_editable(terms)
            if idx is not None:
                return idx

        return None

    first_idx = _find_field(("first name", "given name", "first"))
    if first_idx is None:
        raise RuntimeError("First name field not found")
    device.input_text(first_name, index=first_idx)

    if last_name:
        last_idx = _find_field(("last name", "family name", "surname", "last"))
        if last_idx is None:
            raise RuntimeError("Last name field not found")
        device.input_text(last_name, index=last_idx)

    phone_idx = _find_field(("phone", "telephone", "mobile", "number"))
    if phone_idx is None:
        raise RuntimeError("Phone field not found")
    device.input_text(number, index=phone_idx)

    save_terms = ("save", "done", "save contact")
    save_idx = _find_clickable(save_terms)
    if save_idx is None:
        save_idx = _find_any(save_terms)

    if save_idx is None:
        for direction in ("up", "down"):
            device.scroll(direction)
            save_idx = _find_clickable(save_terms)
            if save_idx is None:
                save_idx = _find_any(save_terms)
            if save_idx is not None:
                break

    if save_idx is None:
        raise RuntimeError("Save button not found")

    device.click(index=save_idx)
    return True
