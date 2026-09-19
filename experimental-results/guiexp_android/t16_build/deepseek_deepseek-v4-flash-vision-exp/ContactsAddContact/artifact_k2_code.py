PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)"
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim"
    }
}

def program(device, binding: dict) -> bool:
    # Open the Contacts app
    device.open_app("Contacts")
    device.settle(1)

    # Find and click the "Create contact" button
    create_idx = None
    for find_kwargs in [
        {"description": "Create contact", "clickable": True},
        {"text": "Create contact", "clickable": True},
        {"contains": "Create contact", "clickable": True},
        {"description": "Create new contact", "clickable": True},
        {"text": "Create new contact", "clickable": True},
        {"contains": "Create new contact", "clickable": True},
    ]:
        create_idx = device.find(**find_kwargs)
        if create_idx is not None:
            break
    if create_idx is None:
        raise RuntimeError("Could not find 'Create contact' button")
    device.click(index=create_idx)

    # Fill first name
    first_name_idx = device.find(hint="First name", editable=True)
    if first_name_idx is None:
        raise RuntimeError("First name field not found")
    device.input_text(binding["name"].split()[0], index=first_name_idx)

    # Fill last name
    last_name_idx = device.find(hint="Last name", editable=True)
    if last_name_idx is None:
        last_name_idx = device.find(text="Last name", editable=True)
    if last_name_idx is None:
        raise RuntimeError("Last name field not found")
    device.input_text(binding["name"].split()[1], index=last_name_idx)

    # Scroll down to reveal the phone field
    phone_idx = None
    for _ in range(10):
        phone_idx = device.find(hint="Phone", editable=True)
        if phone_idx is None:
            phone_idx = device.find(text="Phone", editable=True)
        if phone_idx is None:
            phone_idx = device.find(contains="Phone", editable=True)
        if phone_idx is not None:
            break
        device.scroll(direction="down")
    if phone_idx is None:
        raise RuntimeError("Phone field not found")

    # Input phone number
    device.input_text(binding["number"], index=phone_idx)

    # Click Save
    save_idx = None
    for find_kwargs in [
        {"text": "Save", "clickable": True},
        {"description": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
    ]:
        save_idx = device.find(**find_kwargs)
        if save_idx is not None:
            break
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)

    return True
