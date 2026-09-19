PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim",
    }
}

def program(device, binding: dict) -> bool:
    # Start from a clean launcher state
    device.navigate_home()
    device.open_app("Contacts")
    device.settle(1.0)

    # Find and click the create-contact button
    def find_create_button():
        # Exact text
        for text in ["Create new contact", "Create contact", "New contact", "Add contact", "Add new contact"]:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        # Exact description
        for desc in ["Create new contact", "Create contact", "New contact", "Add contact", "Add new contact"]:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx
        # Contains text
        for text in ["Create new contact", "Create contact", "New contact", "Add contact"]:
            idx = device.find(contains=text, clickable=True)
            if idx is not None:
                return idx
        # Broad fallback
        return device.find(contains="Create", clickable=True)

    create_idx = find_create_button()
    if create_idx is None:
        raise RuntimeError("Could not find create contact button")
    device.click(index=create_idx)
    device.settle(1.0)

    # Helper to find an editable field by hint/text/contains
    def find_editable(hint=None, text=None, contains=None):
        if hint:
            idx = device.find(hint=hint, editable=True)
            if idx is not None:
                return idx
        if text:
            idx = device.find(text=text, editable=True)
            if idx is not None:
                return idx
        if contains:
            idx = device.find(contains=contains, editable=True)
            if idx is not None:
                return idx
        return None

    # Wait for first name field
    first_name_idx = None
    for _ in range(10):
        first_name_idx = find_editable(hint="First name", text="First name")
        if first_name_idx is not None:
            break
        device.settle(0.5)
    if first_name_idx is None:
        raise RuntimeError("First name field not found")

    device.click(index=first_name_idx)
    device.input_text(binding['name'].split()[0], index=first_name_idx)

    # Last name
    last_name_idx = find_editable(hint="Last name", text="Last name")
    if last_name_idx is None:
        raise RuntimeError("Last name field not found")
    device.click(index=last_name_idx)
    device.input_text(binding['name'].split()[1], index=last_name_idx)

    # Phone number, scroll if necessary
    phone_idx = None
    for _ in range(15):
        phone_idx = find_editable(hint="Phone", text="Phone", contains="Phone")
        if phone_idx is not None:
            break
        device.scroll(direction="down")
    if phone_idx is None:
        raise RuntimeError("Phone field not found")

    device.click(index=phone_idx)
    device.input_text(binding['number'], index=phone_idx)

    # Save
    save_idx = None
    for attr in ["text", "description"]:
        if attr == "text":
            save_idx = device.find(text="Save", clickable=True)
        else:
            save_idx = device.find(description="Save", clickable=True)
        if save_idx is not None:
            break
    if save_idx is None:
        save_idx = device.find(contains="Save", clickable=True)
    if save_idx is None:
        raise RuntimeError("Save button not found")

    device.click(index=save_idx)

    return True
