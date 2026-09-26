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
    full_name = f"{first_name} {last_name}"

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

    def _sorted_elements():
        return sorted(
            device.elements(),
            key=lambda el: el.get("index") if el.get("index") is not None else 0,
        )

    def _find_action(labels, clickable=None, editable=None, use_contains=True):
        for label in labels:
            if not label:
                continue

            for key in ("text", "hint", "description"):
                kwargs = {key: label}
                if clickable is not None:
                    kwargs["clickable"] = bool(clickable)
                if editable is not None:
                    kwargs["editable"] = bool(editable)
                idx = device.find(**kwargs)
                if idx is not None:
                    return idx

            if use_contains:
                kwargs = {"contains": label}
                if clickable is not None:
                    kwargs["clickable"] = bool(clickable)
                if editable is not None:
                    kwargs["editable"] = bool(editable)
                idx = device.find(**kwargs)
                if idx is not None:
                    return idx

        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None:
                continue
            if clickable is not None and bool(el.get("clickable")) != bool(clickable):
                continue
            if editable is not None and bool(el.get("editable")) != bool(editable):
                continue
            c = _combined(el)
            if any(label.lower() in c for label in labels if label):
                return idx

        return None

    def _editable_indices(exclude=None, skip_words=None):
        exclude = set(exclude or [])
        skip = [w.lower() for w in (skip_words or [])]
        inds = []
        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None or idx in exclude:
                continue
            if not el.get("editable"):
                continue
            c = _combined(el)
            if any(w in c for w in skip):
                continue
            inds.append(idx)
        return inds

    def _find_editable(labels, exclude=None, fallback_position=None, skip_words=None):
        exclude = set(exclude or [])

        idx = _find_action(labels, editable=True)
        if idx is not None and idx not in exclude:
            return idx

        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None or idx in exclude or not el.get("editable"):
                continue
            c = _combined(el)
            if any(label.lower() in c for label in labels if label):
                return idx

        inds = _editable_indices(exclude, skip_words)
        if fallback_position is not None and 0 <= fallback_position < len(inds):
            return inds[fallback_position]
        if inds:
            return inds[0]

        for direction in ("down", "up"):
            device.scroll(direction=direction)
            device.settle(0.5)

            idx = _find_action(labels, editable=True)
            if idx is not None and idx not in exclude:
                return idx

            inds = _editable_indices(exclude, skip_words)
            if inds:
                if fallback_position is not None and fallback_position < len(inds):
                    return inds[fallback_position]
                return inds[0]

        return None

    def _find_save_core():
        return _find_action(["Save", "Save contact", "Done"], clickable=None, editable=None)

    def _find_save_full():
        idx = _find_save_core()
        if idx is not None:
            return idx

        for label in ("Create", "Add"):
            for key in ("text", "description"):
                idx = device.find(**{key: label})
                if idx is not None:
                    return idx

        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None:
                continue
            c = _combined(el).strip().lower()
            if c in ("save", "save contact", "done", "create", "add"):
                return idx

        return None

    def _find_save_positional():
        clickable_candidates = []
        any_candidates = []

        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None:
                continue
            if el.get("scrollable"):
                continue

            c = _combined(el)
            low = c.lower()
            if any(
                k in low
                for k in (
                    "navigate",
                    "back",
                    "more options",
                    "overflow",
                    "edit contact",
                    "add to favorites",
                    "add phone",
                    "add email",
                    "contact photo",
                    "search",
                    "account",
                    "settings",
                )
            ):
                continue

            if idx <= 6:
                any_candidates.append((idx, c))
                if el.get("clickable"):
                    clickable_candidates.append((idx, c))

        for idx, c in any_candidates:
            if idx == 2 and (not c.strip() or "save" in c.lower()):
                return idx

        for pool in (clickable_candidates, any_candidates):
            for idx, c in pool:
                if not c.strip():
                    return idx
            for idx, c in pool:
                if any(k in c.lower() for k in ("save", "done", "create", "add")):
                    return idx

        if clickable_candidates:
            return clickable_candidates[0][0]
        if any_candidates:
            return any_candidates[0][0]
        return None

    def _looks_like_editor():
        if _find_save_core() is not None:
            return True
        count = 0
        for el in device.elements():
            if el.get("editable"):
                count += 1
                if count >= 2:
                    return True
        return False

    def _looks_like_detail():
        labels = ["Edit contact", "Add to favorites", "Contact info", "About"]
        for label in labels:
            if device.find(text=label) is not None:
                return True
            if device.find(description=label) is not None:
                return True
            if device.find(contains=label) is not None:
                return True

        for el in device.elements():
            c = _combined(el)
            if any(k in c for k in ("edit contact", "add to favorites", "contact info", "about")):
                return True

        return False

    def _name_visible():
        if device.find(text=full_name) is not None:
            return True
        if device.find(contains=full_name) is not None:
            return True
        if device.find(text=number) is not None:
            return True

        for el in device.elements():
            c = _combined(el)
            if full_name.lower() in c or number.lower() in c:
                return True
            if first_name.lower() in c and last_name.lower() in c:
                return True

        return False

    def _navigate_up():
        nav = _find_action(["Navigate up", "Back"], clickable=True)
        if nav is None:
            nav = _find_action(["Navigate up", "Back"], clickable=None)
        if nav is not None:
            device.click(index=nav)
        else:
            device.navigate_back()
        device.settle(1.0)

    def _find_create():
        idx = _find_action(["Create contact", "Add contact", "New contact"], clickable=True)
        if idx is not None:
            return idx

        idx = _find_action(["Create contact", "Add contact", "New contact"], clickable=None)
        if idx is not None:
            return idx

        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None or not el.get("clickable"):
                continue
            c = _combined(el)
            if any(k in c for k in ("create contact", "add contact", "new contact")):
                return idx

        return None

    def _find_create_positional(excluded):
        for el in _sorted_elements():
            idx = el.get("index")
            if idx is None or idx in excluded or not el.get("clickable"):
                continue

            c = _combined(el).lower()
            if any(
                k in c
                for k in (
                    "navigate",
                    "back",
                    "search",
                    "account",
                    "more options",
                    "overflow",
                    "edit contact",
                    "add to favorites",
                    "contact info",
                    "about",
                    "phone",
                    "email",
                    "note",
                )
            ):
                continue

            if not c.strip() or any(k in c for k in ("add", "new", "create")):
                return idx

        return None

    def _open_editor():
        excluded = set()

        for _ in range(3):
            if _looks_like_editor():
                return True

            if _looks_like_detail():
                _navigate_up()
                continue

            create_idx = _find_create()
            if create_idx is None:
                create_idx = _find_create_positional(excluded)

            if create_idx is None:
                device.open_app("Contacts")
                device.settle(1.0)
                create_idx = _find_create()
                if create_idx is None:
                    create_idx = _find_create_positional(excluded)

            if create_idx is None:
                return False

            excluded.add(create_idx)
            device.click(index=create_idx)
            device.settle(1.0)

            if _looks_like_editor():
                return True

            if _looks_like_detail():
                _navigate_up()
                continue

            device.navigate_back()
            device.settle(1.0)

        return False

    def _save_contact():
        for _ in range(4):
            if not _looks_like_editor():
                return _name_visible()

            save_idx = _find_save_full()

            if save_idx is None:
                device.scroll(direction="up")
                device.settle(0.5)
                save_idx = _find_save_full()

            if save_idx is None:
                more_idx = _find_action(["More options", "Overflow menu"], clickable=True)
                if more_idx is not None:
                    device.click(index=more_idx)
                    device.settle(1.0)
                    save_idx = _find_save_full()
                    if save_idx is not None:
                        device.click(index=save_idx)
                        device.settle(1.0)
                        if not _looks_like_editor() and _name_visible():
                            return True
                    if _looks_like_editor():
                        device.navigate_back()
                        device.settle(0.5)
                    continue

            if save_idx is None:
                save_idx = _find_save_positional()

            if save_idx is not None:
                device.click(index=save_idx)
                device.settle(1.0)
                if not _looks_like_editor() and _name_visible():
                    return True
                if not _looks_like_editor():
                    return False

            device.scroll(direction="up")
            device.settle(0.5)

        return (not _looks_like_editor()) and _name_visible()

    device.open_app("Contacts")
    device.settle(1.0)

    if not _open_editor():
        raise RuntimeError("Could not open the Create contact editor")

    first_idx = _find_editable(["First name", "First"], fallback_position=0)
    if first_idx is None:
        raise RuntimeError("Could not find the First name field")
    device.input_text(first_name, index=first_idx)
    device.settle(0.5)

    last_idx = _find_editable(["Last name", "Last"], exclude=[first_idx], fallback_position=0)
    if last_idx is None:
        raise RuntimeError("Could not find the Last name field")
    device.input_text(last_name, index=last_idx)
    device.settle(0.5)

    phone_exclude = [first_idx, last_idx]
    phone_skip = ["email", "note", "company", "label"]
    phone_idx = _find_editable(
        ["Phone", "Phone number", "Mobile"],
        exclude=phone_exclude,
        fallback_position=0,
        skip_words=phone_skip,
    )

    if phone_idx is None:
        add_idx = _find_action(["Add phone number", "Add phone"], clickable=None)
        if add_idx is not None:
            device.click(index=add_idx)
            device.settle(1.0)
            phone_idx = _find_editable(
                ["Phone", "Phone number", "Mobile"],
                exclude=phone_exclude,
                fallback_position=0,
                skip_words=phone_skip,
            )

    if phone_idx is None:
        inds = _editable_indices(phone_exclude, phone_skip)
        if inds:
            phone_idx = inds[0]

    if phone_idx is None:
        raise RuntimeError("Could not find the Phone field")

    device.input_text(number, index=phone_idx)
    device.settle(0.5)

    if not _save_contact():
        if not _looks_like_editor():
            device.open_app("Contacts")
            device.settle(1.0)
            if not _name_visible():
                raise RuntimeError("Could not verify that the contact was saved")
        else:
            raise RuntimeError("Could not find or activate the Save control")

    if _looks_like_detail():
        _navigate_up()

    if _looks_like_editor():
        raise RuntimeError("Still in the contact editor after save")

    if not _name_visible():
        for _ in range(3):
            device.scroll(direction="down")
            device.settle(0.5)
            if _name_visible():
                break

    if not _name_visible():
        device.open_app("Contacts")
        device.settle(1.0)

    if not _name_visible():
        raise RuntimeError("Contact was not visible after saving")

    return True
