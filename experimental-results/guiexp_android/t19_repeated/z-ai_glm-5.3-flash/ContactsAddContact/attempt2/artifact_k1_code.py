PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "required": True,
        "description": (
            "Full contact name, exactly two words. The first word is typed "
            "into the 'First name' field, the second word into the "
            "'Last name' field of the Google Contacts editor."
        ),
    },
    "number": {
        "type": "string",
        "required": True,
        "description": (
            "Phone number as a single verbatim string; typed into the "
            "'Phone' field, phone label left at its default."
        ),
    },
}


def _find_editable_field(device, label):
    """Locate an editable form field by hint, then exact text, then substring."""
    for criteria in ({"hint": label}, {"text": label}, {"contains": label}):
        element = device.find(editable=True, **criteria)
        if element is not None:
            return element
    return None


def _find_button(device, label, allow_contains=True):
    """Locate a clickable button by content description, then exact text."""
    for criteria in ({"description": label}, {"text": label}):
        element = device.find(clickable=True, **criteria)
        if element is not None:
            return element
    if allow_contains:
        element = device.find(clickable=True, contains=label)
        if element is not None:
            return element
    return None


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]

    words = name.split()
    if len(words) != 2:
        raise ValueError(
            "binding['name'] must contain exactly two words (first and last); "
            "got %r" % (name,)
        )
    first_name, last_name = words[0], words[1]

    # --- Step 1: launch the Contacts app --------------------------------
    device.open_app("Contacts")
    device.settle(2)
    if (
        device.find(description="Create contact") is None
        and device.find(text="No contacts yet") is None
    ):
        # Some builds register the app under a different label.
        device.open_app("Google Contacts")
        device.settle(2)
        if (
            device.find(description="Create contact") is None
            and device.find(text="No contacts yet") is None
        ):
            raise RuntimeError(
                "Contacts app did not open; PeopleActivity not detected"
            )

    # --- Step 2: open the create-contact editor -------------------------
    fab = _find_button(device, "Create contact")
    if fab is None:
        raise RuntimeError("'Create contact' button not found on PeopleActivity")
    device.click(fab)
    device.settle(2)

    if _find_editable_field(device, "First name") is None:
        raise RuntimeError(
            "Contact editor did not open ('First name' field not found)"
        )

    # --- Step 3: type the first name -------------------------------------
    first_field = _find_editable_field(device, "First name")
    if first_field is None:
        raise RuntimeError("'First name' field not found in contact editor")
    device.input_text(first_name, index=first_field)

    # --- Step 4: type the last name --------------------------------------
    last_field = _find_editable_field(device, "Last name")
    if last_field is None:
        raise RuntimeError("'Last name' field not found in contact editor")
    device.input_text(last_name, index=last_field)

    # --- Step 5: type the phone number (label left at default) -----------
    phone_field = _find_editable_field(device, "Phone")
    if phone_field is None:
        device.scroll(direction="down")
        phone_field = _find_editable_field(device, "Phone")
    if phone_field is None:
        raise RuntimeError("'Phone' field not found in contact editor")
    device.input_text(number, index=phone_field)

    # --- Step 6: save the contact ----------------------------------------
    save_button = _find_button(device, "Save", allow_contains=False)
    if save_button is None:
        save_button = _find_button(device, "Save", allow_contains=True)
    if save_button is None:
        raise RuntimeError("'Save' button not found in contact editor")
    device.click(save_button)
    device.settle(2)

    # Verify the editor closed (we are back on PeopleActivity).
    if (
        device.find(description="Save", clickable=True) is not None
        or device.find(text="Save", clickable=True) is not None
    ):
        raise RuntimeError("Contact editor still open after save; save failed")

    return True
