PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number for the new contact, one string copied verbatim.",
        "required": True,
    },
}


def _editable_indexes(device):
    """Indexes of all currently visible editable text fields."""
    return [e["index"] for e in device.elements() if e.get("editable")]


def _find_field(device, *hints):
    """Find an editable text field by hint, trying each hint exactly then contains."""
    for hint in hints:
        idx = device.find(hint=hint, editable=True)
        if idx is not None:
            return idx
        idx = device.find(contains=hint, editable=True)
        if idx is not None:
            return idx
    return None


def _find_button(device, *labels):
    """Find a clickable button by text/description, trying each label exactly then contains."""
    for label in labels:
        idx = device.find(description=label, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text=label, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains=label, clickable=True)
        if idx is not None:
            return idx
    return None


def program(device, binding: dict) -> bool:
    name = str(binding.get("name", "")).strip()
    number = str(binding.get("number", "")).strip()
    words = name.split()
    if len(words) != 2:
        raise ValueError("binding['name'] must contain exactly two words, got: %r" % name)
    if not number:
        raise ValueError("binding['number'] must be a non-empty phone number")
    first_name, last_name = words[0], words[1]

    # 1) Launch the Contacts app.
    device.open_app("Contacts")
    device.settle(2)
    if _find_button(device, "Create contact") is None:
        # Some builds register the app under a different launcher label.
        device.open_app("Google Contacts")
        device.settle(2)
    create_btn = _find_button(device, "Create contact", "Create")
    if create_btn is None:
        raise RuntimeError("Contacts app did not open; 'Create contact' button not found")

    # 2) Open the contact editor.
    device.click(create_btn)
    device.settle(2)

    # 3) First name -> 'First name' field.
    first_idx = _find_field(device, "First name")
    if first_idx is None:
        editables = _editable_indexes(device)
        if not editables:
            raise RuntimeError("Contact editor did not open; no editable fields found")
        first_idx = editables[0]
    device.click(index=first_idx)
    device.input_text(first_name, index=first_idx)

    # 4) Last name -> 'Last name' field.
    last_idx = _find_field(device, "Last name")
    if last_idx is None:
        editables = _editable_indexes(device)
        last_idx = editables[1] if len(editables) > 1 else None
    if last_idx is None:
        raise RuntimeError("'Last name' field not found in the contact editor")
    device.input_text(last_name, index=last_idx)

    # 5) Phone number -> 'Phone' field (leave the phone label at its default).
    phone_idx = None
    for _ in range(3):
        phone_idx = _find_field(device, "Phone")
        if phone_idx is not None:
            break
        device.scroll(direction="down")
    if phone_idx is None:
        editables = _editable_indexes(device)
        phone_idx = editables[2] if len(editables) > 2 else None
    if phone_idx is None:
        raise RuntimeError("'Phone' field not found in the contact editor")
    device.input_text(number, index=phone_idx)

    # 6) Save the contact.
    save_btn = _find_button(device, "Save")
    if save_btn is None:
        device.scroll(direction="up")
        save_btn = _find_button(device, "Save")
    if save_btn is None:
        raise RuntimeError("'Save' button not found in the contact editor")
    device.click(save_btn)
    device.settle(2)

    # Verify the editor closed; retry the save once if it is still on screen.
    if _find_field(device, "First name") is not None:
        save_btn = _find_button(device, "Save")
        if save_btn is not None:
            device.click(save_btn)
            device.settle(2)
        if _find_field(device, "First name") is not None:
            raise RuntimeError("Contact editor still open after tapping Save")

    return True
