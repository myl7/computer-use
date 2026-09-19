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


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
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
    phone_field = device.find(hint="Phone", editable=True)
    if phone_field is None:
        phone_field = device.find(contains="Phone", editable=True)
    if phone_field is None:
        device.scroll("down")
        phone_field = device.find(hint="Phone", editable=True)
    if phone_field is None:
        phone_field = device.find(contains="Phone", editable=True)
    if phone_field is None:
        raise LookupError("Phone number field not found in contact editor")
    device.input_text(number, index=phone_field)

    # ---- Step 6: save the contact ------------------------------------------
    save = device.find(text="Save", clickable=True)
    if save is None:
        save = device.find(description="Save", clickable=True)
    if save is None:
        save = device.find(contains="Save", clickable=True)
    if save is None:
        device.scroll("down")
        save = device.find(text="Save", clickable=True)
    if save is None:
        save = device.find(description="Save", clickable=True)
    if save is None:
        raise LookupError("Contact editor 'Save' button not found on screen")
    device.click(save)
    device.settle(2)

    # Verify the editor closed (save succeeded).
    if device.find(text="Save", clickable=True) is not None:
        raise RuntimeError("Saving the contact did not complete; editor still open")

    return True
