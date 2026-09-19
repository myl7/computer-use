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


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    first, last = _name_parts(name)

    # Step 1: launch the Contacts app and confirm it reached the list screen.
    device.open_app("Contacts")
    device.settle(2)
    if (device.find(description="Create contact") is None
            and device.find(description="Contacts") is None
            and device.find(text="Contacts") is None):
        raise RuntimeError("Contacts app did not open; PeopleActivity not detected")

    # Step 2: open the contact editor via the 'Create contact' button.
    create = device.find(description="Create contact", clickable=True)
    if create is None:
        create = device.find(text="Create contact", clickable=True)
    if create is None:
        create = device.find(contains="Create contact", clickable=True)
    if create is None:
        raise LookupError("'Create contact' button not found on the Contacts screen")
    device.click(create)
    device.settle(2)

    # Step 3: type the first word of the name into the 'First name' field.
    first_idx = device.find(hint="First name", editable=True)
    if first_idx is None:
        first_idx = device.find(text="First name", editable=True)
    if first_idx is None:
        raise RuntimeError("First name field not found on the contact editor screen")
    device.click(index=first_idx)
    device.input_text(first, index=first_idx)

    # Step 4: type the second word of the name into the 'Last name' field.
    last_idx = device.find(hint="Last name", editable=True)
    if last_idx is None:
        last_idx = device.find(text="Last name", editable=True)
    if last_idx is None:
        raise LookupError("Last name field not found on the contact editor screen")
    device.input_text(last, index=last_idx)

    # Step 5: type the phone number into the 'Phone' field (default label kept).
    phone_idx = device.find(hint="Phone", editable=True)
    if phone_idx is None:
        phone_idx = device.find(contains="Phone", editable=True)
    if phone_idx is None:
        device.scroll("down")
        phone_idx = device.find(hint="Phone", editable=True)
    if phone_idx is None:
        phone_idx = device.find(contains="Phone", editable=True)
    if phone_idx is None:
        raise LookupError("Phone number field not found in the contact editor")
    device.input_text(number, index=phone_idx)

    # Step 6: save the contact.
    save = device.find(text="Save", clickable=True)
    if save is None:
        save = device.find(description="Save", clickable=True)
    if save is None:
        device.scroll("down")
        save = device.find(text="Save", clickable=True)
    if save is None:
        save = device.find(description="Save", clickable=True)
    if save is None:
        raise LookupError("'Save' button not found on the contact editor screen")
    device.click(save)
    device.settle(2)

    # Verify the editor closed (contact saved back to the list).
    if device.find(hint="First name", editable=True) is not None:
        raise RuntimeError("Contact editor still open after Save; contact may not have been saved")

    return True
