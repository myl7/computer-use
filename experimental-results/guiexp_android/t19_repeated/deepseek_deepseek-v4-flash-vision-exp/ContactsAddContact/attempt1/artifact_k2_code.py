PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
        "required": True
    },
    "number": {
        "type": "string",
        "description": "Phone number string copied verbatim",
        "required": True
    }
}


def program(device, binding: dict) -> bool:
    # Open the Contacts app
    device.open_app("Contacts")
    device.settle(1)

    # Find and click the "Create contact" button
    create_idx = device.find(description="Create contact", clickable=True)
    if create_idx is None:
        create_idx = device.find(description="Create new contact", clickable=True)
    if create_idx is None:
        create_idx = device.find(text="Create contact", clickable=True)
    if create_idx is None:
        create_idx = device.find(text="Create new contact", clickable=True)
    if create_idx is None:
        els = device.elements()
        for e in els:
            if e.get("clickable"):
                text = (e.get("text") or "") + " " + (e.get("description") or "")
                if "create" in text.lower() and "contact" in text.lower():
                    create_idx = e["index"]
                    break
    if create_idx is None:
        raise RuntimeError("Could not find 'Create contact' button to open the contact editor")

    device.click(index=create_idx)
    device.settle(2)

    # Parse the binding
    name_words = binding["name"].split()
    if len(name_words) != 2:
        raise ValueError("Name must contain exactly two words")
    first_name = name_words[0]
    last_name = name_words[1]
    number = binding["number"]

    # Fill first name
    first_idx = device.find(hint="First name", editable=True)
    if first_idx is None:
        first_idx = device.find(contains="First name", editable=True)
    if first_idx is None:
        els = device.elements()
        for e in els:
            if e.get("editable") and "first" in (e.get("hint") or "").lower():
                first_idx = e["index"]
                break
    if first_idx is None:
        raise RuntimeError("First name field not found")
    device.click(index=first_idx)
    device.input_text(first_name, index=first_idx)

    # Fill last name
    last_idx = device.find(hint="Last name", editable=True)
    if last_idx is None:
        last_idx = device.find(contains="Last name", editable=True)
    if last_idx is None:
        last_idx = device.find(text="Last name", editable=True)
    if last_idx is None:
        els = device.elements()
        for e in els:
            if e.get("editable") and "last" in (e.get("hint") or "").lower():
                last_idx = e["index"]
                break
    if last_idx is None:
        device.scroll(direction="down")
        device.settle(0.5)
        last_idx = device.find(hint="Last name", editable=True)
        if last_idx is None:
            last_idx = device.find(contains="Last name", editable=True)
    if last_idx is None:
        raise RuntimeError("Last name field not found")
    device.click(index=last_idx)
    device.input_text(last_name, index=last_idx)

    # Scroll down until the phone field is visible
    phone_idx = None
    for _ in range(10):
        phone_idx = device.find(hint="Phone", editable=True)
        if phone_idx is None:
            phone_idx = device.find(contains="Phone", editable=True)
        if phone_idx is None:
            phone_idx = device.find(text="Phone", editable=True)
        if phone_idx is None:
            els = device.elements()
            for e in els:
                if e.get("editable"):
                    hint = (e.get("hint") or "").lower()
                    text = (e.get("text") or "").lower()
                    if "phone" in hint or "mobile" in hint or "tel" in hint or "number" in hint:
                        phone_idx = e["index"]
                        break
        if phone_idx is not None:
            break
        device.scroll(direction="down")
        device.settle(0.5)
    if phone_idx is None:
        raise RuntimeError("Phone number field not found")

    device.click(index=phone_idx)
    device.input_text(number, index=phone_idx)

    # Find and click Save
    save_idx = device.find(text="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(description="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(contains="Save", clickable=True)
    if save_idx is None:
        els = device.elements()
        for e in els:
            if e.get("clickable") and (e.get("text") == "Save" or e.get("description") == "Save"):
                save_idx = e["index"]
                break
    if save_idx is None:
        raise RuntimeError("Save button not found")

    device.click(index=save_idx)
    device.settle(2)

    return True
