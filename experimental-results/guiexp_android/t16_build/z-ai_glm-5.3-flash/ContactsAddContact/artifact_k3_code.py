PARAMS_SCHEMA = {
    "name": {"type": "string", "description": "Full contact name, exactly two words (first and last)"},
    "number": {"type": "string", "description": "Phone number, one string copied verbatim"},
}


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    words = name.split()
    if len(words) != 2:
        raise ValueError("binding['name'] must be exactly two words")
    first, last = words

    # Launch the Contacts app
    device.open_app("Contacts")
    device.settle(2)

    # If we landed in the dialer or elsewhere, try to get to the Contacts UI
    if (device.find(description="Create contact") is None
            and device.find(text="No contacts yet") is None
            and device.find(description="Contacts") is None):
        device.open_app("Google Contacts")
        device.settle(2)
    if (device.find(description="Create contact") is None
            and device.find(text="No contacts yet") is None
            and device.find(description="Contacts") is None):
        raise RuntimeError("Contacts app did not open")

    # Open the contact editor via the floating "Create contact" button
    btn = device.find(description="Create contact", clickable=True)
    if btn is None:
        device.scroll("down")
        btn = device.find(description="Create contact", clickable=True)
    if btn is None:
        raise LookupError("'Create contact' button not found")
    device.click(btn)
    device.settle(2)

    # First name field
    idx = device.find(hint="First name", editable=True)
    if idx is None:
        idx = device.find(text="First name", editable=True)
    if idx is None:
        raise RuntimeError("First name field not found on contact editor screen")
    device.input_text(first, index=idx)

    # Last name field
    idx = device.find(hint="Last name", editable=True)
    if idx is None:
        idx = device.find(text="Last name", editable=True)
    if idx is None:
        raise LookupError("Last name EditText not found on contact editor screen")
    device.input_text(last, index=idx)

    # Phone field (leave label at default)
    idx = device.find(hint="Phone", editable=True)
    if idx is None:
        idx = device.find(contains="Phone", editable=True)
    if idx is None:
        raise LookupError("Phone number EditText not found in contact editor")
    device.input_text(number, index=idx)
    device.settle(1)

    # Save the contact
    idx = device.find(text="Save", clickable=True)
    if idx is None:
        idx = device.find(description="Save", clickable=True)
    if idx is None:
        device.scroll("down")
        idx = device.find(text="Save", clickable=True)
    if idx is None:
        idx = device.find(description="Save", clickable=True)
    if idx is None:
        raise LookupError("Contact editor 'Save' button not found on screen")
    device.click(idx)
    device.settle(2)

    return True
