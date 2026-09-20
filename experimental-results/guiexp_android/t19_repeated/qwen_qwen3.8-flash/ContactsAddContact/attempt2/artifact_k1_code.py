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
    raw_name = binding.get("name")
    raw_number = binding.get("number")

    if raw_name is None or raw_number is None:
        raise ValueError("binding must contain 'name' and 'number'")

    name = str(raw_name).strip()
    number = str(raw_number)

    parts = name.split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")

    first_name = parts[0]
    last_name = " ".join(parts[1:])

    if not number:
        raise ValueError("binding['number'] must not be empty")

    def _truthy(value):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        if value is None:
            return False
        return bool(value)

    def _coerce_index(value):
        if value is None or value is False or isinstance(value, bool):
            return None

        if isinstance(value, dict):
            return _coerce_index(value.get("index"))

        if isinstance(value, (list, tuple)):
            if not value:
                return None
            return _coerce_index(value[0])

        if isinstance(value, int):
            return value if value >= 0 else None

        if isinstance(value, float):
            if value.is_integer() and value >= 0:
                return int(value)
            return None

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                idx = int(stripped)
            except Exception:
                return None
            return idx if idx >= 0 else None

        return value

    def _as_lower(value):
        return str(value or "").lower()

    def _element_matches(el, terms, include_text):
        vals = []
        if include_text:
            vals.append(_as_lower(el.get("text")))
        vals.append(_as_lower(el.get("hint")))
        vals.append(_as_lower(el.get("description")))

        for term in terms:
            t = term.lower()
            for v in vals:
                if v and (t == v or t in v):
                    return True
        return False

    def _find_by_terms(terms, editable=False, clickable=False, max_scrolls=0):
        norm_terms = [str(term).strip() for term in terms if str(term).strip()]
        if not norm_terms:
            return None

        for term in norm_terms:
            if editable:
                for kwargs in (
                    {"hint": term, "editable": True},
                    {"description": term, "editable": True},
                ):
                    try:
                        idx = _coerce_index(device.find(**kwargs))
                    except Exception:
                        idx = None
                    if idx is not None:
                        return idx

            if clickable:
                for kwargs in (
                    {"text": term, "clickable": True},
                    {"description": term, "clickable": True},
                    {"hint": term, "clickable": True},
                    {"contains": term, "clickable": True},
                ):
                    try:
                        idx = _coerce_index(device.find(**kwargs))
                    except Exception:
                        idx = None
                    if idx is not None:
                        return idx

            if not editable and not clickable:
                for kwargs in (
                    {"text": term},
                    {"description": term},
                    {"hint": term},
                    {"contains": term},
                ):
                    try:
                        idx = _coerce_index(device.find(**kwargs))
                    except Exception:
                        idx = None
                    if idx is not None:
                        return idx

        for attempt in range(max_scrolls + 1):
            try:
                els = device.elements() or []
            except Exception:
                els = []

            # First pass: prefer accessibility labels/hints/descriptions over typed text.
            for el in els:
                if not isinstance(el, dict):
                    continue

                idx = _coerce_index(el.get("index"))
                if idx is None:
                    continue

                if editable and not _truthy(el.get("editable")):
                    continue
                if clickable and not _truthy(el.get("clickable")):
                    continue

                if _element_matches(el, norm_terms, include_text=False):
                    return idx

            # Second pass: allow text matching, especially for unlabeled placeholder fields.
            for el in els:
                if not isinstance(el, dict):
                    continue

                idx = _coerce_index(el.get("index"))
                if idx is None:
                    continue

                if editable and not _truthy(el.get("editable")):
                    continue
                if clickable and not _truthy(el.get("clickable")):
                    continue

                # For editable fields, avoid matching already-typed user text when the
                # element already has a non-empty hint/description label.
                if editable and (_as_lower(el.get("hint")) or _as_lower(el.get("description"))):
                    continue

                if _element_matches(el, norm_terms, include_text=True):
                    return idx

            if attempt < max_scrolls:
                try:
                    device.scroll(direction="down")
                    device.settle(0.5)
                except Exception:
                    pass

        return None

    def _try_click(idx):
        idx = _coerce_index(idx)
        if idx is None:
            return False

        try:
            result = device.click(index=idx)
        except Exception:
            try:
                device.settle(0.5)
            except Exception:
                pass
            return False

        try:
            device.settle(1.0)
        except Exception:
            pass

        return result is not False

    def _input_field(terms, value, max_scrolls=0):
        last_error = None

        for _ in range(2):
            idx = _find_by_terms(terms, editable=True, max_scrolls=max_scrolls)
            if idx is None:
                raise RuntimeError("Could not find editable field: " + ", ".join(terms))

            try:
                result = device.input_text(value, index=idx)
            except Exception as exc:
                last_error = exc
                try:
                    device.settle(0.5)
                except Exception:
                    pass
                continue

            try:
                device.settle(0.5)
            except Exception:
                pass

            if result is False:
                last_error = RuntimeError("input_text returned False")
                continue

            return idx

        raise RuntimeError("Failed to input text: " + str(last_error))

    first_terms = ["First name", "Given name"]
    last_terms = ["Last name", "Family name", "Surname"]
    phone_terms = ["Phone", "Phone number", "Mobile", "Number"]
    create_terms = [
        "Create contact",
        "Add contact",
        "New contact",
        "Create new contact",
        "Create a contact",
        "Add a contact",
    ]
    save_terms = ["Save", "Done"]

    def _editor_open():
        return (
            _find_by_terms(first_terms, editable=True, max_scrolls=0) is not None
            or _find_by_terms(last_terms, editable=True, max_scrolls=0) is not None
        )

    opened = False
    open_success = False

    for app_name in ("Contacts", "Google Contacts", "People"):
        try:
            result = device.open_app(app_name)
            if result is False:
                continue
            open_success = True
            try:
                device.settle(1.0)
            except Exception:
                pass
        except Exception:
            continue

        if _editor_open():
            opened = True
            break

        if _find_by_terms(create_terms, clickable=True, max_scrolls=0) is not None:
            opened = True
            break

        if _find_by_terms(create_terms, clickable=False, max_scrolls=0) is not None:
            opened = True
            break

    if not opened:
        if not open_success:
            try:
                result = device.open_app("Contacts")
                if result is False:
                    raise RuntimeError("Could not open Google Contacts")
                open_success = True
                try:
                    device.settle(1.0)
                except Exception:
                    pass
            except Exception:
                raise RuntimeError("Could not open Google Contacts")

        if _editor_open() or _find_by_terms(create_terms, clickable=False, max_scrolls=0) is not None:
            opened = True
        else:
            try:
                els = device.elements() or []
            except Exception:
                els = []

            if els and open_success:
                opened = True
            else:
                raise RuntimeError("Could not open Google Contacts")

    if not _editor_open():
        clicked = False

        for _ in range(3):
            create_idx = _find_by_terms(create_terms, clickable=True, max_scrolls=0)
            if create_idx is None:
                create_idx = _find_by_terms(create_terms, clickable=False, max_scrolls=0)

            if create_idx is not None and _try_click(create_idx):
                clicked = True
                break

            try:
                device.settle(0.5)
            except Exception:
                pass

        if not clicked:
            try:
                els = device.elements() or []
            except Exception:
                els = []

            if els and isinstance(els[0], dict):
                create_idx = _coerce_index(els[0].get("index"))
                if create_idx is not None and _try_click(create_idx) and _editor_open():
                    clicked = True

            if not clicked:
                raise RuntimeError("Could not find the Create contact button")

        if not _editor_open():
            first_idx = _find_by_terms(first_terms, editable=True, max_scrolls=1)
            last_idx = _find_by_terms(last_terms, editable=True, max_scrolls=1)
            if first_idx is None and last_idx is None:
                raise RuntimeError("Contact editor did not open")

    _input_field(first_terms, first_name, max_scrolls=0)
    _input_field(last_terms, last_name, max_scrolls=1)
    _input_field(phone_terms, number, max_scrolls=2)

    save_clicked = False

    for _ in range(3):
        save_idx = _find_by_terms(save_terms, clickable=True, max_scrolls=0)
        if save_idx is None:
            save_idx = _find_by_terms(save_terms, clickable=False, max_scrolls=0)

        if save_idx is None:
            try:
                device.scroll(direction="up")
                device.settle(0.5)
            except Exception:
                pass

            save_idx = _find_by_terms(save_terms, clickable=True, max_scrolls=1)

        if save_idx is None:
            save_idx = _find_by_terms(save_terms, clickable=False, max_scrolls=1)

        if save_idx is not None and _try_click(save_idx):
            save_clicked = True
            break

        try:
            device.settle(0.5)
        except Exception:
            pass

    if not save_clicked:
        raise RuntimeError("Could not click the Save button")

    def _scroll_to_top():
        for _ in range(3):
            try:
                device.scroll(direction="up")
                device.settle(0.3)
            except Exception:
                pass

    def _contact_visible():
        try:
            els = device.elements() or []
        except Exception:
            return False

        f = _as_lower(first_name)
        l = _as_lower(last_name)
        full = _as_lower(name)

        for el in els:
            if not isinstance(el, dict):
                continue

            vals = (
                _as_lower(el.get("text")),
                _as_lower(el.get("hint")),
                _as_lower(el.get("description")),
            )

            for v in vals:
                if v and (f in v or l in v or full in v):
                    return True

        return False

    for _ in range(3):
        _scroll_to_top()

        if not _editor_open():
            return True

        save_idx = _find_by_terms(save_terms, clickable=True, max_scrolls=0)
        if save_idx is None:
            save_idx = _find_by_terms(save_terms, clickable=False, max_scrolls=0)

        if save_idx is None:
            save_idx = _find_by_terms(save_terms, clickable=True, max_scrolls=2)

        if save_idx is None:
            save_idx = _find_by_terms(save_terms, clickable=False, max_scrolls=2)

        if save_idx is not None and _try_click(save_idx):
            continue

        break

    _scroll_to_top()

    if not _editor_open():
        return True

    if _contact_visible() and not _editor_open():
        return True

    raise RuntimeError("Contact was not saved")
