PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)"
    },
    "number": {
        "type": "string",
        "description": "Phone number"
    }
}

def program(device, binding: dict) -> bool:
    """
    Create a new contact in Google Contacts on Android.
    The binding must contain exactly the keys 'name' and 'number'.
    """
    name = binding.get("name")
    number = binding.get("number")
    if not name or not number:
        raise ValueError("binding must contain non-empty 'name' and 'number'")

    parts = name.split()
    if len(parts) != 2:
        raise ValueError("name must be exactly two words (first and last)")
    first_name, last_name = parts[0], parts[1]

    # 1. Launch the Contacts app.
    device.open_app("Contacts")
    device.settle(2)

    # 2. Locate and click the button that opens the contact editor.
    create_btn = None
    # Try the most common descriptors first.
    for criteria in (
        {"description": "Create contact", "clickable": True},
        {"text": "Create contact", "clickable": True},
        {"description": "Create new contact", "clickable": True},
        {"text": "Create new contact", "clickable": True},
        {"description": "Add contact", "clickable": True},
        {"text": "Add contact", "clickable": True},
    ):
        create_btn = device.find(**criteria)
        if create_btn is not None:
            break

    if create_btn is None:
        # Fallback: scan all elements for a clickable element whose
        # description or text contains both a creation verb and "contact".
        elements = device.elements()
        for el in elements:
            if not el.get("clickable"):
                continue
            desc = (el.get("description") or "").lower()
            txt = (el.get("text") or "").lower()
            combined = desc + " " + txt
            if any(kw in combined for kw in ("create", "add", "new")) and "contact" in combined:
                create_btn = el["index"]
                break

    if create_btn is None:
        raise RuntimeError("Could not find 'Create contact' button in Contacts app")

    device.click(index=create_btn)
    device.settle(2)

    # 3. Fill the first name field.
    first_field = device.find(hint="First name", editable=True)
    if first_field is None:
        first_field = device.find(text="First name", editable=True)
    if first_field is None:
        # Fallback: any editable field with hint containing 'first'
        elements = device.elements()
        for el in elements:
            hint = (el.get("hint") or "").lower()
            if el.get("editable") and "first" in hint:
                first_field = el["index"]
                break
    if first_field is None:
        raise RuntimeError("Could not find First name field")
    device.click(index=first_field)
    device.input_text(first_name, index=first_field)

    # 4. Fill the last name field.
    last_field = device.find(hint="Last name", editable=True)
    if last_field is None:
        last_field = device.find(text="Last name", editable=True)
    if last_field is None:
        elements = device.elements()
        for el in elements:
            hint = (el.get("hint") or "").lower()
            if el.get("editable") and "last" in hint:
                last_field = el["index"]
                break
    if last_field is None:
        raise RuntimeError("Could not find Last name field")
    device.click(index=last_field)
    device.input_text(last_name, index=last_field)

    # 5. Scroll down until the phone number field is visible.
    phone_field = None
    for _ in range(10):
        # Look for a field with hint or text containing 'phone' or 'mobile'.
        phone_field = device.find(hint="Phone", editable=True)
        if phone_field is None:
            phone_field = device.find(text="Phone", editable=True)
        if phone_field is None:
            elements = device.elements()
            for el in elements:
                if not el.get("editable"):
                    continue
                hint = (el.get("hint") or "").lower()
                text = (el.get("text") or "").lower()
                combined = hint + " " + text
                if "phone" in combined or "mobile" in combined:
                    phone_field = el["index"]
                    break
        if phone_field is not None:
            break
        device.scroll("down")
        device.settle(0.5)

    if phone_field is None:
        raise RuntimeError("Could not find Phone number field")

    # 6. Enter the phone number.
    device.click(index=phone_field)
    device.input_text(number, index=phone_field)

    # 7. Save the contact.
    save_btn = device.find(text="Save", clickable=True)
    if save_btn is None:
        save_btn = device.find(description="Save", clickable=True)
    if save_btn is None:
        elements = device.elements()
        for el in elements:
            if not el.get("clickable"):
                continue
            txt = (el.get("text") or "").lower()
            desc = (el.get("description") or "").lower()
            if "save" in txt or "save" in desc:
                save_btn = el["index"]
                break
    if save_btn is None:
        raise RuntimeError("Could not find Save button")

    device.click(index=save_btn)
    device.settle(2)

    return True
