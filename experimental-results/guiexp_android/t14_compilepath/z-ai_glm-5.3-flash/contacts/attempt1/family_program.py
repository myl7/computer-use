PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number for the contact, copied verbatim.",
        "required": True,
    },
}


def _locate(device, **criteria):
    """Find an element by criteria; raise if it cannot be found."""
    idx = device.find(**criteria)
    if idx is None:
        raise RuntimeError("Required UI element not found: %r" % (criteria,))
    return idx


def _find_any(device, *criteria_sets):
    """Try several find-criteria dicts in order; return first hit or None."""
    for criteria in criteria_sets:
        idx = device.find(**criteria)
        if idx is not None:
            return idx
    return None


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]

    words = name.split()
    if len(words) != 2:
        raise ValueError(
            "binding['name'] must contain exactly two words, got %r" % (name,)
        )
    first_name, last_name = words[0], words[1]

    # Open the contacts UI (the recorded flow runs inside the Phone/Contacts app).
    try:
        device.open_app("Contacts")
    except Exception:
        device.open_app("Phone")

    # If a 'Contacts' tab exists (dialer UI), make sure it is selected.
    tab_idx = _find_any(
        device,
        {"description": "Contacts", "clickable": True},
        {"text": "Contacts", "clickable": True},
    )
    if tab_idx is not None:
        device.click(index=tab_idx)

    # Open the create-contact form (FAB / 'Create new contact' entry point).
    create_idx = _find_any(
        device,
        {"contains": "Create new contact"},
        {"contains": "Create contact"},
        {"description": "Create contact"},
    )
    if create_idx is None:
        device.scroll(direction="down")
        create_idx = _find_any(
            device,
            {"contains": "Create new contact"},
            {"contains": "Create contact"},
            {"description": "Create contact"},
        )
    if create_idx is None:
        raise RuntimeError("'Create new contact' entry point not found")
    device.click(index=create_idx)

    # First name field.
    fn_idx = _find_any(device, {"hint": "First name"}, {"text": "First name"})
    if fn_idx is None:
        raise RuntimeError("'First name' field not found")
    device.input_text(first_name, index=fn_idx)

    # Last name field.
    ln_idx = _find_any(device, {"hint": "Last name"}, {"text": "Last name"})
    if ln_idx is None:
        raise RuntimeError("'Last name' field not found")
    device.input_text(last_name, index=ln_idx)

    # Phone field (keep the default phone label).
    ph_idx = _find_any(device, {"hint": "Phone"}, {"text": "Phone"})
    if ph_idx is None:
        raise RuntimeError("'Phone' field not found")
    device.input_text(number, index=ph_idx)

    # Save the contact.
    save_idx = _find_any(
        device,
        {"text": "Save"},
        {"description": "Save"},
        {"contains": "Save"},
    )
    if save_idx is None:
        raise RuntimeError("'Save' button not found")
    device.click(index=save_idx)

    # The editor should dismiss after saving; fail loudly if it did not.
    device.wait()
    if device.find(text="Save") is not None:
        raise RuntimeError("Contact editor did not close after saving")

    return True
