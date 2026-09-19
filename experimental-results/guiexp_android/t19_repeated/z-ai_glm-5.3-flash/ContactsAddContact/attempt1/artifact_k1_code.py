PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last); "
                       "first word -> 'First name' field, second word -> 'Last name' field",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim, typed into the 'Phone' field "
                       "(phone label left at its default)",
        "required": True,
    },
}


def _check(condition, message):
    if not condition:
        raise RuntimeError(message)


def _find_editable_field(device, *hints):
    """Locate an editable text field by any of the given hint labels."""
    for hint in hints:
        idx = device.find(hint=hint, editable=True)
        if idx is not None:
            return idx
    wanted = {h.strip().lower() for h in hints}
    for el in device.elements():
        if not el.get("editable"):
            continue
        label = (el.get("hint") or el.get("text") or "").strip().lower()
        if label in wanted:
            return el.get("index")
    return None


def _find_clickable_by_label(device, label):
    """Locate a clickable element by content description or text (exact first, then substring)."""
    idx = device.find(description=label, clickable=True)
    if idx is not None:
        return idx
    idx = device.find(text=label, clickable=True)
    if idx is not None:
        return idx
    target = label.lower()
    for el in device.elements():
        if not el.get("clickable"):
            continue
        desc = (el.get("description") or "").strip().lower()
        txt = (el.get("text") or "").strip().lower()
        if target in desc or target in txt:
            return el.get("index")
    return None


def program(device, binding: dict) -> bool:
    name = str(binding["name"]).strip()
    number = str(binding["number"])
    words = name.split()
    _check(len(words) == 2,
           "binding['name'] must be exactly two words (first and last), got: %r" % name)
    first_name, last_name = words

    # --- Step 1: launch the Contacts app (PeopleActivity) ---
    opened = False
    for app_name in ("Contacts", "Google Contacts"):
        device.open_app(app_name)
        device.settle(2)
        if (device.find(description="Create contact") is not None
                or device.find(text="No contacts yet") is not None
                or device.find(hint="Search contacts") is not None):
            opened = True
            break
    _check(opened, "Contacts app did not open; PeopleActivity not detected")

    # --- Step 2: tap the 'Create contact' floating action button -> ContactEditorActivity ---
    fab = _find_clickable_by_label(device, "Create contact")
    _check(fab is not None, "'Create contact' button not found on the contacts list screen")
    device.click(fab)
    device.settle(2)

    # --- Step 3: first word of the name into the 'First name' field ---
    first_field = _find_editable_field(device, "First name")
    _check(first_field is not None,
           "Contact editor did not open ('First name' field not found)")
    device.input_text(first_name, index=first_field)

    # --- Step 4: second word of the name into the 'Last name' field ---
    last_field = _find_editable_field(device, "Last name")
    _check(last_field is not None, "'Last name' field not found in the contact editor")
    device.input_text(last_name, index=last_field)

    # --- Step 5: phone number into the 'Phone' field (label left at default) ---
    phone_field = _find_editable_field(device, "Phone")
    if phone_field is None:
        device.scroll("down")
        phone_field = _find_editable_field(device, "Phone")
    _check(phone_field is not None, "'Phone' field not found in the contact editor")
    device.input_text(number, index=phone_field)

    # --- Step 6: save the contact (returns to PeopleActivity) ---
    save = _find_clickable_by_label(device, "Save")
    _check(save is not None, "'Save' button not found in the contact editor")
    device.click(save)
    device.settle(2)

    # Handle a possible duplicate-contact confirmation dialog.
    if device.find(hint="First name", editable=True) is not None:
        confirm = _find_clickable_by_label(device, "Save anyway")
        if confirm is not None:
            device.click(confirm)
            device.settle(2)

    _check(device.find(hint="First name", editable=True) is None,
           "Contact editor still open after saving; contact was not saved")
    return True
