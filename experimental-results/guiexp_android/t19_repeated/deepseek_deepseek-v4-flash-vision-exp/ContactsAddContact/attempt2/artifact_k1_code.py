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
    # Step 1: Open the Phone app from the launcher
    idx = device.find(text="Phone", description="Phone", clickable=True)
    if idx is None:
        idx = device.find(text="Phone", clickable=True)
    if idx is None:
        raise RuntimeError("Could not find the clickable Phone app icon on the launcher")
    device.click(index=idx)

    # Step 2: Tap "Create new contact"
    idx = device.find(text="Create new contact", clickable=True)
    if idx is None:
        idx = device.find(contains="Create new contact", clickable=True)
    if idx is None:
        raise RuntimeError("Could not find 'Create new contact' entry to open the contact editor")
    device.click(index=idx)

    # Step 3: Focus the First name field
    first_name_idx = device.find(hint='First name', editable=True)
    if first_name_idx is None:
        raise RuntimeError("Could not find First name field")
    device.click(index=first_name_idx)

    # Step 4: Type the first name
    device.input_text(binding['name'].split()[0], index=first_name_idx)

    # Step 5: Focus the Last name field
    last_name_idx = device.find(hint='Last name', editable=True)
    if last_name_idx is None:
        last_name_idx = device.find(contains='Last name', editable=True)
    if last_name_idx is None:
        raise RuntimeError("Could not find Last name field")
    device.click(index=last_name_idx)

    # Step 6: Type the last name
    device.input_text(binding['name'].split()[1], index=last_name_idx)

    # Helper to locate the phone number field
    def find_phone_field():
        idx = device.find(hint='Phone', editable=True)
        if idx is not None:
            return idx
        idx = device.find(text='Phone', editable=True)
        if idx is not None:
            return idx
        for e in device.elements():
            if (e.get('editable') and 'EditText' in e.get('class', '')
                    and e.get('hint') and ('phone' in e['hint'].lower() or 'mobile' in e['hint'].lower())):
                return e['index']
        return None

    # Step 7: Scroll down until the phone field is visible
    phone_idx = find_phone_field()
    if phone_idx is None:
        for _ in range(10):
            device.scroll(direction="down")
            phone_idx = find_phone_field()
            if phone_idx is not None:
                break
        if phone_idx is None:
            raise RuntimeError("Phone number field not found")

    # Step 8: Focus the phone field
    device.click(index=phone_idx)

    # Step 9: Type the phone number
    device.input_text(binding['number'], index=phone_idx)

    # Step 10: Save the contact
    save_idx = device.find(text='Save', clickable=True)
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)

    return True
