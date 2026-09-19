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
    def open_phone_app():
        idx = device.find(text="Phone", clickable=True)
        if idx is None:
            idx = device.find(description="Phone", clickable=True)
        if idx is None:
            idx = device.find(text="Dialer", clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(2)
        else:
            device.open_app("Phone")
            device.settle(2)

    def find_phone_field():
        idx = device.find(hint='Phone', editable=True)
        if idx is not None:
            return idx
        idx = device.find(text='Phone', editable=True)
        if idx is not None:
            return idx
        elements = device.elements()
        for e in elements:
            if e.get('editable') and e.get('hint') and ('phone' in e['hint'].lower() or 'mobile' in e['hint'].lower()):
                return e['index']
        return None

    open_phone_app()

    idx = device.find(text="Create new contact", clickable=True)
    if idx is None:
        idx = device.find(contains="Create new contact", clickable=True)
    if idx is None:
        raise RuntimeError("Could not find 'Create new contact' entry")
    device.click(index=idx)
    device.settle(2)

    first_name_idx = device.find(hint='First name', editable=True)
    if first_name_idx is None:
        raise RuntimeError("First name field not found")
    device.click(index=first_name_idx)
    device.input_text(binding['name'].split()[0], index=first_name_idx)

    last_name_idx = device.find(hint='Last name', editable=True)
    if last_name_idx is None:
        raise RuntimeError("Last name field not found")
    device.click(index=last_name_idx)
    device.input_text(binding['name'].split()[1], index=last_name_idx)

    phone_idx = find_phone_field()
    for _ in range(10):
        if phone_idx is not None:
            break
        device.scroll(direction="down")
        phone_idx = find_phone_field()
    if phone_idx is None:
        raise RuntimeError("Phone number field not found")

    device.click(index=phone_idx)
    device.input_text(binding['number'], index=phone_idx)

    save_idx = device.find(text='Save', clickable=True)
    if save_idx is None:
        save_idx = device.find(description='Save', clickable=True)
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    device.settle(2)

    return True
