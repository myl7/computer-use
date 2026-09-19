import re

PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number, copied verbatim",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    words = name.split()
    if len(words) != 2:
        raise ValueError("binding['name'] must contain exactly two words")
    first_name, last_name = words

    # --- Open the Contacts app ---
    launched = False
    for app_name in ("Contacts", "Google Contacts"):
        try:
            device.open_app(app_name)
            device.settle(2)
            if (
                device.find(description="Create contact") is not None
                or device.find(text="No contacts yet") is not None
                or device.find(hint="Search contacts") is not None
            ):
                launched = True
                break
        except Exception:
            continue
    if not launched:
        raise RuntimeError("Contacts app did not open")

    # --- Start creating a new contact (FAB / 'Create contact') ---
    fab = (
        device.find(description="Create contact", clickable=True)
        or device.find(contains="Create contact", clickable=True)
    )
    if fab is None:
        # fall back: clickable, non-scrollable button near bottom
        candidates = [
            el for el in device.elements()
            if el.get("clickable") and not el.get("scrollable")
        ]
        if not candidates:
            raise RuntimeError("'Create contact' button not found")
        fab = candidates[-1]["index"]
    device.click(fab)
    device.settle(2)

    # --- Verify we are in the contact editor ---
    first_field = device.find(hint="First name", editable=True)
    if first_field is None:
        first_field = device.find(contains="First name", editable=True)
    if first_field is None:
        raise RuntimeError("Contact editor 'First name' field not found")

    device.input_text(first_name, index=first_field)
    device.settle(1)

    last_field = device.find(hint="Last name", editable=True)
    if last_field is None:
        last_field = device.find(contains="Last name", editable=True)
    if last_field is None:
        raise RuntimeError("Contact editor 'Last name' field not found")

    device.input_text(last_name, index=last_field)
    device.settle(1)

    phone_field = device.find(hint="Phone", editable=True)
    if phone_field is None:
        phone_field = device.find(contains="Phone", editable=True)
    if phone_field is None:
        raise RuntimeError("Contact editor 'Phone' field not found")

    device.input_text(number, index=phone_field)
    device.settle(1)

    # --- Save the contact ---
    save = (
        device.find(description="Save", clickable=True)
        or device.find(text="Save", clickable=True)
        or device.find(contains="Save", clickable=True)
    )
    if save is None:
        raise RuntimeError("Save button not found in contact editor")
    device.click(save)
    device.settle(2)

    return True
