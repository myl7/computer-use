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
    # Extract binding values
    name_parts = binding["name"].split()
    if len(name_parts) != 2:
        raise RuntimeError("Name must be exactly two words")
    first = name_parts[0]
    last = name_parts[1]
    number = binding["number"]

    # If we are not already on the contact editor, open the Contacts app
    # and tap the "Create new contact" button.
    if device.find(hint="First name") is None:
        # Open the Google Contacts app
        try:
            device.open_app("Contacts")
        except Exception:
            device.open_app("Google Contacts")
        device.wait()

        # Locate the "Create new contact" button
        create_btn = None
        for text in ("Create new contact", "Create contact", "Add contact", "Add new contact"):
            create_btn = device.find(text=text)
            if create_btn is not None:
                break
            create_btn = device.find(description=text)
            if create_btn is not None:
                break

        if create_btn is None:
            # Fallback: search clickable elements whose description contains "create"/"add"
            elements = device.elements()
            for e in elements:
                desc = (e.get("description") or "").lower()
                if e.get("clickable") and ("create" in desc or "add" in desc):
                    create_btn = e["index"]
                    break

        if create_btn is None:
            raise RuntimeError("Could not find the 'Create new contact' button")

        device.click(index=create_btn)
        device.wait()

    # Fill First name
    first_field = device.find(hint="First name")
    if first_field is None:
        elements = device.elements()
        for e in elements:
            if e.get("editable") and e.get("hint") and "first" in e.get("hint").lower():
                first_field = e["index"]
                break
    if first_field is None:
        raise RuntimeError("First name field not found")
    device.input_text(first, index=first_field)
    device.wait()

    # Fill Last name
    last_field = device.find(hint="Last name")
    if last_field is None:
        elements = device.elements()
        for e in elements:
            if e.get("editable") and e.get("hint") and "last" in e.get("hint").lower():
                last_field = e["index"]
                break
    if last_field is None:
        raise RuntimeError("Last name field not found")
    device.input_text(last, index=last_field)
    device.wait()

    # Find and fill Phone field
    phone_field = device.find(hint="Phone")
    if phone_field is None:
        device.scroll(direction="down")
        device.wait()
        phone_field = device.find(hint="Phone")
    if phone_field is None:
        elements = device.elements()
        for e in elements:
            if e.get("editable") and e.get("hint") and "phone" in e.get("hint").lower():
                phone_field = e["index"]
                break
    if phone_field is None:
        raise RuntimeError("Phone field not found")
    device.input_text(number, index=phone_field)
    device.wait()

    # Save the contact
    save_btn = None
    for text in ("Save", "Save contact", "Done"):
        save_btn = device.find(text=text)
        if save_btn is not None:
            break
        save_btn = device.find(description=text)
        if save_btn is not None:
            break

    if save_btn is None:
        # Fallback: clickable element whose description contains "save"
        elements = device.elements()
        for e in elements:
            desc = (e.get("description") or "").lower()
            if e.get("clickable") and "save" in desc:
                save_btn = e["index"]
                break

    if save_btn is None:
        # Keyboard might be covering the toolbar; dismiss it and retry
        device.navigate_back()
        device.wait()
        save_btn = device.find(text="Save")
        if save_btn is None:
            save_btn = device.find(description="Save")
        if save_btn is None:
            elements = device.elements()
            for e in elements:
                desc = (e.get("description") or "").lower()
                if e.get("clickable") and "save" in desc:
                    save_btn = e["index"]
                    break

    if save_btn is None:
        raise RuntimeError("Save button not found")

    device.click(index=save_btn)
    device.wait()

    return True
