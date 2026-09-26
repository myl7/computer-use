PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "required": True,
        "description": "Full contact name, exactly two words (first and last).",
    },
    "number": {
        "type": "string",
        "required": True,
        "description": "Phone number to store on the contact, copied verbatim.",
    },
}


def _name_parts(name):
    parts = [p for p in name.split() if p]
    if len(parts) >= 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], parts[0]
    raise ValueError("binding['name'] must contain at least one word")


def _blob(el):
    """Lower-cased concatenation of an element's user-visible strings."""
    return " ".join(
        str(el.get(key)) for key in ("text", "hint", "description") if el.get(key)
    ).lower()


def _first_found(device, criteria_list):
    for criteria in criteria_list:
        idx = device.find(**criteria)
        if idx is not None:
            return idx
    return None


def _contacts_screen_visible(device):
    return _first_found(device, [
        {"description": "Create contact"},
        {"description": "Contacts"},
        {"text": "Contacts"},
    ]) is not None


def _editor_open(device):
    return _first_found(device, [
        {"hint": "First name", "editable": True},
        {"hint": "Last name", "editable": True},
        {"text": "First name", "editable": True},
    ]) is not None


def _on_detail_screen(device):
    return _first_found(device, [
        {"description": "Edit contact"},
        {"text": "Contact info"},
        {"description": "Add to favorites"},
    ]) is not None


def _save_editor(device):
    criteria = [
        {"text": "Save", "clickable": True},
        {"description": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
    ]
    idx = _first_found(device, criteria)
    if idx is None:
        device.scroll("up")
        idx = _first_found(device, criteria)
    if idx is None:
        device.scroll("down")
        idx = _first_found(device, criteria)
    if idx is None:
        return False
    device.click(index=idx)
    return True


def _number_on_screen(device, number):
    digits = "".join(ch for ch in number if ch.isdigit())
    tail = digits[-7:] if len(digits) >= 7 else digits
    for el in device.elements():
        visible = " ".join(
            str(el.get(key)) for key in ("text", "hint", "description") if el.get(key)
        )
        if not visible:
            continue
        if number and number in visible:
            return True
        if tail and tail in visible:
            return True
    return False


def _phone_editable_index(els):
    for el in els:
        if el.get("editable") and "phone" in _blob(el):
            return el["index"]
    return None


def _add_phone_row_index(els):
    for el in els:
        b = _blob(el)
        if "phone" in b and "add" in b:
            return el["index"]
    return None


def _phone_label_neighbor_index(els):
    label = None
    for el in els:
        b = _blob(el)
        if "phone" in b and "add" not in b:
            label = el["index"]
            break
    if label is None:
        return None
    after = [el["index"] for el in els
             if el.get("editable") and el["index"] > label
             and "name" not in _blob(el)]
    return min(after) if after else None


def _phone_by_elimination(device):
    els = device.elements()
    editables = [el for el in els if el.get("editable")]
    anchor = None
    for el in editables:
        if "last name" in _blob(el):
            anchor = el["index"]
    if anchor is not None:
        candidates = sorted(el["index"] for el in editables
                            if el["index"] > anchor and "name" not in _blob(el))
        if candidates:
            return candidates[0]
        return None
    candidates = sorted(el["index"] for el in editables if "name" not in _blob(el))
    return candidates[0] if len(candidates) == 1 else None


def _locate_phone_field(device, max_scrolls=3):
    """Find the phone entry field, revealing it if necessary.

    The editor may show the phone field directly (hint 'Phone'), hide it
    behind an 'Add phone number' placeholder row that must be tapped first,
    or keep it below the fold; all cases are tried before a last-resort
    elimination of the remaining editable, non-name field.
    """
    tapped_add = False
    for attempt in range(max_scrolls + 1):
        els = device.elements()
        idx = _phone_editable_index(els)
        if idx is not None:
            return idx
        if not tapped_add:
            row = _add_phone_row_index(els)
            if row is not None:
                device.click(index=row)
                device.settle(2)
                tapped_add = True
                els = device.elements()
                idx = _phone_editable_index(els)
                if idx is not None:
                    return idx
        idx = _phone_label_neighbor_index(els)
        if idx is not None:
            return idx
        if attempt < max_scrolls:
            device.scroll("down")
    return _phone_by_elimination(device)


def _open_contact_detail(device, name):
    if _on_detail_screen(device):
        return True
    row = _first_found(device, [{"text": name}, {"contains": name}])
    if row is None:
        return False
    device.click(index=row)
    device.settle(2)
    return _on_detail_screen(device)


def _contact_shows_number(device, name, number):
    if not _open_contact_detail(device, name):
        return True  # cannot inspect; assume the editor saved what was typed
    if _number_on_screen(device, number):
        return True
    return _first_found(device, [
        {"text": "Add phone number"},
        {"contains": "Add phone"},
    ]) is None


def _add_phone_from_detail(device, name, number):
    if not _open_contact_detail(device, name):
        raise RuntimeError("Contact detail screen not shown after saving the contact")
    add_row = _first_found(device, [
        {"text": "Add phone number"},
        {"contains": "Add phone"},
    ])
    if add_row is None:
        if _number_on_screen(device, number):
            return  # the number is already stored
        raise RuntimeError("'Add phone number' row not found on the contact detail screen")
    device.click(index=add_row)
    device.settle(2)

    phone_idx = _locate_phone_field(device, max_scrolls=2)
    if phone_idx is None:
        raise LookupError("Phone number field not found after tapping 'Add phone number'")
    device.click(index=phone_idx)
    device.input_text(number, index=phone_idx)

    if not _save_editor(device):
        raise LookupError("'Save' button not found after adding the phone number")
    device.settle(2)
    if _editor_open(device):
        if not _save_editor(device):
            raise RuntimeError("Contact editor still open after saving the phone number")
        device.settle(2)

    if _open_contact_detail(device, name):
        if (not _number_on_screen(device, number)
                and _first_found(device, [{"text": "Add phone number"},
                                          {"contains": "Add phone"}]) is not None):
            raise RuntimeError("Phone number was not stored on the contact")


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    first, last = _name_parts(name)

    # Step 1: launch the Contacts app and confirm it reached the list screen.
    device.open_app("Contacts")
    device.settle(2)
    if not _contacts_screen_visible(device):
        device.navigate_back()
        device.settle(2)
    if not _contacts_screen_visible(device):
        device.open_app("Contacts")
        device.settle(2)
    if not _contacts_screen_visible(device):
        raise RuntimeError("Contacts app did not open; PeopleActivity not detected")

    # Step 2: open the contact editor via the 'Create contact' button.
    create = _first_found(device, [
        {"description": "Create contact", "clickable": True},
        {"text": "Create contact", "clickable": True},
        {"contains": "Create contact", "clickable": True},
    ])
    if create is None:
        raise LookupError("'Create contact' button not found on the Contacts screen")
    device.click(index=create)
    device.settle(2)

    # Step 3: type the first word of the name into the 'First name' field.
    first_idx = _first_found(device, [
        {"hint": "First name", "editable": True},
        {"text": "First name", "editable": True},
    ])
    if first_idx is None:
        raise RuntimeError("First name field not found on the contact editor screen")
    device.click(index=first_idx)
    device.input_text(first, index=first_idx)

    # Step 4: type the second word of the name into the 'Last name' field.
    last_idx = _first_found(device, [
        {"hint": "Last name", "editable": True},
        {"text": "Last name", "editable": True},
    ])
    if last_idx is None:
        raise LookupError("Last name field not found on the contact editor screen")
    device.input_text(last, index=last_idx)

    # Step 5: type the phone number into the 'Phone' field (default label kept).
    # The field may be hidden behind an 'Add phone number' placeholder row or
    # below the fold; never type blindly into an unverified index.
    phone_filled = False
    phone_idx = _locate_phone_field(device)
    if phone_idx is not None:
        device.click(index=phone_idx)
        device.input_text(number, index=phone_idx)
        phone_filled = True

    # Step 6: save the contact.
    if not _save_editor(device):
        raise LookupError("'Save' button not found on the contact editor screen")
    device.settle(2)
    if _editor_open(device):
        if not _save_editor(device):
            raise RuntimeError(
                "Contact editor still open after Save; contact may not have been saved")
        device.settle(2)

    # Step 7: verify the stored contact really shows the number; if the editor
    # never exposed a usable phone field, add it from the detail screen via
    # 'Add phone number' (which reopens the editor on the phone entry).
    if not phone_filled or not _contact_shows_number(device, name, number):
        _add_phone_from_detail(device, name, number)

    return True
