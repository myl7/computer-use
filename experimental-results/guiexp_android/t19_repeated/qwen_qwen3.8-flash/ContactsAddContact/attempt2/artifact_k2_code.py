PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name with exactly two words: first name and last name.",
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
    },
}


def program(device, binding: dict) -> bool:
    name = str(binding.get("name") or "").strip()
    number = binding.get("number")

    if not name:
        raise ValueError("binding['name'] is required")
    if number is None:
        raise ValueError("binding['number'] is required")

    number = str(number)
    name_parts = name.split()
    if len(name_parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")

    first_name = name_parts[0]
    last_name = name_parts[1]

    def _matches(element, terms):
        values = []
        for key in ("text", "hint", "description"):
            value = element.get(key)
            if value:
                values.append(str(value))
        blob = " ".join(values).lower()
        return any(str(term).lower() in blob for term in terms)

    def _find(terms, **criteria):
        for term in terms:
            for key in ("hint", "text", "description"):
                try:
                    idx = device.find(**{key: term}, **criteria)
                except Exception:
                    idx = None
                if idx is not None:
                    return idx

            try:
                idx = device.find(contains=term, **criteria)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        try:
            elements = device.elements()
        except Exception:
            return None

        for element in elements:
            if criteria.get("clickable") and not element.get("clickable"):
                continue
            if criteria.get("editable") and not element.get("editable"):
                continue

            if _matches(element, terms):
                idx = element.get("index")
                if isinstance(idx, str):
                    try:
                        idx = int(idx)
                    except ValueError:
                        pass
                return idx

        return None

    def _find_scroll(terms, direction="down", attempts=3, **criteria):
        idx = _find(terms, **criteria)
        if idx is not None:
            return idx

        for _ in range(attempts):
            try:
                device.scroll(direction=direction)
            except Exception:
                break

            device.settle(0.5)
            idx = _find(terms, **criteria)
            if idx is not None:
                return idx

        return None

    app_name = binding.get("app_name") or "Contacts"
    if not app_name:
        raise ValueError("Cannot open app: no app name available")

    device.open_app(app_name)
    device.settle(1.0)

    create_idx = _find(["Create contact", "Add contact"], clickable=True)
    if create_idx is None:
        try:
            for element in device.elements():
                if element.get("clickable"):
                    create_idx = element.get("index")
                    break
        except Exception:
            create_idx = None

    if create_idx is None:
        raise RuntimeError("Could not find the Create contact control")

    device.click(index=create_idx)
    device.settle(1.0)

    first_idx = _find_scroll(["First name"], editable=True)
    if first_idx is None:
        raise RuntimeError("Could not find the First name field")

    device.input_text(first_name, index=first_idx)
    device.settle(0.5)

    last_idx = _find_scroll(["Last name"], editable=True)
    if last_idx is None:
        raise RuntimeError("Could not find the Last name field")

    device.input_text(last_name, index=last_idx)
    device.settle(0.5)

    phone_idx = _find_scroll(["Phone", "Phone number", "Mobile"], editable=True)
    if phone_idx is None:
        raise RuntimeError("Could not find the Phone field")

    device.input_text(number, index=phone_idx)
    device.settle(0.5)

    save_idx = _find_scroll(["Save"], direction="up", attempts=2, clickable=True)
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")

    device.click(index=save_idx)
    device.settle(1.0)

    return True
