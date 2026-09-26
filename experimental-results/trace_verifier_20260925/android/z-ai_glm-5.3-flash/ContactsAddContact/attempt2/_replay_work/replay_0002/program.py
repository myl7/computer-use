PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
    },
    "number": {
        "type": "string",
        "description": "Phone number as a single string, copied verbatim",
    },
}


def _split_name(name):
    """Split a two-word full name into (first, last)."""
    words = (name or "").strip().split()
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], ""
    return words[0], " ".join(words[1:])


def _blob(elem):
    """Lower-cased concatenation of an element's text/hint/description."""
    parts = []
    for key in ("text", "hint", "description", "content_desc"):
        value = elem.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_number(a, b):
    """Lenient phone comparison that tolerates display formatting."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    tail = min(len(da), len(db), 6)
    return tail >= 4 and da[-tail:] == db[-tail:]


# Editor text fields that are NOT the phone field.  NB: "Phonetic name"
# contains the substring "Phone", which is what lured the number into a
# wrong (lost) field in the failed run.
_NON_PHONE_WORDS = (
    "phonetic", "email", "e-mail", "web", "website", "address",
    "organization", "organisation", "company", "note", "birthday",
    "relationship",
)


def _mentions_phone(blob):
    return "phone" in blob and not any(word in blob for word in _NON_PHONE_WORDS)


def _find_by(device, **criteria):
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _number_visible(device, number):
    """True if the phone number is visible somewhere on the current screen."""
    target = str(number or "").strip()
    if not target:
        return True
    if _find_by(device, text=target) is not None:
        return True
    if _find_by(device, contains=target) is not None:
        return True
    for elem in device.elements():
        txt = str(elem.get("text") or "")
        if txt and (target in txt or _same_number(txt, target)):
            return True
    return False


def _phone_by_position(elems):
    """Compact-editor field order is first name, last name, phone, ...,
    so the phone edit field is the first usable editable after the
    last-name field."""
    editables = [e for e in elems if e.get("editable")]
    anchor_pos = None
    for pos, elem in enumerate(elems):
        if elem.get("editable") and "last name" in _blob(elem):
            anchor_pos = pos
            break
    if anchor_pos is None:
        if len(editables) < 2:
            return None
        try:
            anchor_pos = elems.index(editables[1])
        except ValueError:
            return None
    for elem in elems[anchor_pos + 1:]:
        blob = _blob(elem)
        if elem.get("editable") and not any(w in blob for w in _NON_PHONE_WORDS):
            return elem["index"]
        if blob and _mentions_phone(blob):
            return elem["index"]
    return None


def _locate_phone_field(device, max_scrolls=2):
    """Locate the phone-number edit field inside the contact editor."""
    for round_no in range(max_scrolls + 2):
        idx = _find_by(device, hint="Phone", editable=True)
        if idx is None:
            idx = _find_by(device, hint="Phone")
        if idx is None:
            elems = device.elements()
            for elem in elems:
                if elem.get("editable") and _mentions_phone(_blob(elem)):
                    idx = elem["index"]
                    break
            if idx is None:
                idx = _phone_by_position(elems)
            if idx is None:
                # A tappable phone row: tap it to focus its edit field.
                for elem in elems:
                    if elem.get("clickable") and _mentions_phone(_blob(elem)):
                        device.click(elem["index"])
                        device.settle(1)
                        for elem2 in device.elements():
                            if (elem2.get("editable")
                                    and _mentions_phone(_blob(elem2))):
                                return elem2["index"]
                        break
        if idx is None:
            idx = _find_by(device, text="Phone", editable=True)
        if idx is None:
            idx = _find_by(device, text="Phone")
        if idx is not None:
            return idx
        if round_no < max_scrolls + 1:
            device.scroll("down")
    # Last resort: any element that mentions a phone at all.
    for elem in device.elements():
        if _mentions_phone(_blob(elem)):
            return elem["index"]
    return None


def _type_number(device, number):
    """Type the number into the editor's phone field (best effort).

    Returns True if the number is visible on screen afterwards.
    """
    field = _locate_phone_field(device)
    if field is None:
        return False
    device.input_text(number, index=field)
    if _number_visible(device, number):
        return True
    # The text did not stick: focus the field with a tap and retry once.
    try:
        device.click(field)
    except Exception:
        pass
    device.settle(1)
    field = _locate_phone_field(device) or field
    device.input_text(number, index=field)
    return _number_visible(device, number)


def _find_save(device):
    for criteria in (
        {"text": "Save", "clickable": True},
        {"description": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
        {"text": "Save"},
        {"description": "Save"},
        {"contains": "Save"},
    ):
        idx = _find_by(device, **criteria)
        if idx is not None:
            return idx
    return None


def _find_add_phone(device):
    for criteria in (
        {"text": "Add phone number"},
        {"contains": "Add phone"},
        {"description": "Add phone number"},
        {"contains": "phone number"},
    ):
        idx = _find_by(device, **criteria)
        if idx is not None:
            return idx
    return None


def _phone_stored_on_screen(device, number):
    """True if the number shows up in the phone section of the contact
    detail screen: above the 'Add phone number' placeholder, or the
    placeholder is gone because a phone number exists."""
    elems = device.elements()
    target = str(number or "").strip()
    num_pos = None
    add_phone_pos = None
    for pos, elem in enumerate(elems):
        txt = str(elem.get("text") or "")
        if add_phone_pos is None and "add phone number" in txt.lower():
            add_phone_pos = pos
        if (num_pos is None and txt
                and (target in txt or _same_number(txt, target))):
            num_pos = pos
    if num_pos is None:
        return False
    if add_phone_pos is None:
        return True
    return num_pos < add_phone_pos


def _add_phone_from_detail(device, number, name):
    """Store the number on the saved contact via its 'Add phone number'
    entry on the contact detail screen."""
    entry = _find_add_phone(device)
    if entry is None:
        device.scroll("down")
        entry = _find_add_phone(device)
    if entry is None:
        # Maybe we are on the contacts list instead of the detail screen:
        # open the contact by tapping its name.
        for word in [w for w in str(name or "").split() if w]:
            target = _find_by(device, text=word)
            if target is None:
                target = _find_by(device, contains=word)
            if target is not None:
                device.click(target)
                device.settle(2)
                entry = _find_add_phone(device)
                break
    if entry is None:
        raise RuntimeError("'Add phone number' entry not found on the contact screen")
    device.click(entry)
    device.settle(2)
    _type_number(device, number)
    save = _find_save(device)
    if save is None:
        device.scroll("down")
        save = _find_save(device)
    if save is None:
        raise LookupError("'Save' button not found while adding the phone number")
    device.click(save)
    device.settle(2)
    if _find_save(device) is not None:
        raise RuntimeError("Saving the phone number did not complete; editor still open")


def program(device, binding: dict) -> bool:
    name = str(binding["name"])
    number = str(binding["number"])
    first, last = _split_name(name)

    # ---- Step 1: open the Contacts app -------------------------------------
    device.open_app("Contacts")
    device.settle(2)
    if (device.find(description="Create contact") is None
            and device.find(text="Create contact") is None
            and device.find(description="Contacts") is None):
        # Retry once in case the launch was slow.
        device.open_app("Contacts")
        device.settle(2)
        if (device.find(description="Create contact") is None
                and device.find(text="Create contact") is None
                and device.find(description="Contacts") is None):
            raise RuntimeError("Contacts app did not open (PeopleActivity not detected)")

    # ---- Step 2: tap the "Create contact" button ---------------------------
    create = device.find(description="Create contact", clickable=True)
    if create is None:
        create = device.find(text="Create contact", clickable=True)
    if create is None:
        create = device.find(contains="Create contact", clickable=True)
    if create is None:
        raise LookupError("'Create contact' button not found on the Contacts screen")
    device.click(create)
    device.settle(2)

    # ---- Step 3: type the first name ---------------------------------------
    first_field = device.find(hint="First name", editable=True)
    if first_field is None:
        first_field = device.find(text="First name", editable=True)
    if first_field is None:
        first_field = device.find(contains="First", editable=True)
    if first_field is None:
        # Fallback: the first editable field on the editor form.
        first_field = device.find(editable=True)
    if first_field is None:
        raise RuntimeError("First name field not found on contact editor screen")
    device.input_text(first, index=first_field)

    # ---- Step 4: type the last name ----------------------------------------
    last_field = device.find(hint="Last name", editable=True)
    if last_field is None:
        last_field = device.find(text="Last name", editable=True)
    if last_field is None:
        last_field = device.find(contains="Last", editable=True)
    if last_field is None:
        # Fallback: the second editable field on the editor form.
        editables = [e for e in device.elements() if e.get("editable")]
        if len(editables) >= 2:
            last_field = editables[1]["index"]
    if last_field is None:
        raise LookupError("Last name field not found on contact editor screen")
    device.input_text(last, index=last_field)

    # ---- Step 5: type the phone number -------------------------------------
    # Repaired: the phone field is located robustly (hint / full element scan
    # / position after the last-name field / tap-to-focus, with scrolling),
    # and "Phonetic name"-like fields are explicitly excluded so the number
    # can never again be typed into a wrong field.
    _type_number(device, number)

    # ---- Step 6: save the contact ------------------------------------------
    save = _find_save(device)
    if save is None:
        device.scroll("down")
        save = _find_save(device)
    if save is None:
        raise LookupError("Contact editor 'Save' button not found on screen")
    device.click(save)
    device.settle(2)

    # Verify the editor closed (save succeeded).
    if _find_save(device) is not None:
        raise RuntimeError("Saving the contact did not complete; editor still open")

    # ---- Step 7: verify the number was stored as a phone number ------------
    device.settle(1)
    if _phone_stored_on_screen(device, number):
        return True
    # The number is missing (or was not stored as a phone): reopen the saved
    # contact through its "Add phone number" entry and store the number there.
    _add_phone_from_detail(device, number, name)
    if not _phone_stored_on_screen(device, number):
        raise RuntimeError("Phone number was not stored on the contact")
    return True
