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
        for text in ["Create new contact", "Create contact", "New contact", "Add contact", "Add new contact"]:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        for desc in ["Create new contact", "Create contact", "New contact", "Add contact", "Add new contact"]:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx
        for text in ["Create new contact", "Create contact", "New contact", "Add contact"]:
            idx = device.find(contains=text, clickable=True)
            if idx is not None:
                return idx
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

    # Exclude name fields when looking for phone
    exclude_indexes = {first_name_idx, last_name_idx}

    # Phone field finder
    def find_phone_field():
        # Try exact hint
        for hint in ["Phone", "Phone number", "Mobile", "Home", "Work", "Number"]:
            idx = device.find(hint=hint, editable=True)
            if idx is not None and idx not in exclude_indexes:
                return idx
        # Try exact text
        for text in ["Phone", "Phone number", "Mobile", "Home", "Work", "Number"]:
            idx = device.find(text=text, editable=True)
            if idx is not None and idx not in exclude_indexes:
                return idx
        # Try contains
        for contains in ["Phone", "Mobile", "Number"]:
            idx = device.find(contains=contains, editable=True)
            if idx is not None and idx not in exclude_indexes:
                return idx
        # Label-based: find a non-editable label and the next editable field
        elements = device.elements()
        for el in elements:
            label = el.get('text') or el.get('description') or ''
            if label in ["Phone", "Phone number", "Mobile", "Home", "Work"]:
                label_index = el['index']
                candidates = [e for e in elements if e.get('editable') and e['index'] > label_index and e['index'] not in exclude_indexes]
                if candidates:
                    candidates.sort(key=lambda e: e['index'])
                    return candidates[0]['index']
        return None

    # Add-phone button finder
    def find_add_phone_button():
        for text in ["Add phone number", "Add another phone", "Add phone", "Add number", "Add another phone number"]:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        for desc in ["Add phone number", "Add another phone", "Add phone", "Add number", "Add another phone number"]:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx
        for contains in ["Add phone", "Add number", "Add another phone"]:
            idx = device.find(contains=contains, clickable=True)
            if idx is not None:
                return idx
        # Also try without clickable constraint
        for text in ["Add phone number", "Add another phone", "Add phone", "Add number"]:
            idx = device.find(text=text)
            if idx is not None:
                return idx
        for contains in ["Add phone", "Add number"]:
            idx = device.find(contains=contains)
            if idx is not None:
                return idx
        # Scan all elements for anything with "phone" in text/desc and clickable
        elements = device.elements()
        for el in elements:
            label = (el.get('text') or '') + ' ' + (el.get('description') or '')
            if 'phone' in label.lower() and el.get('clickable'):
                return el['index']
        return None

    def enter_phone_number(number):
        # Try to find an existing phone field
        phone_idx = find_phone_field()
        if phone_idx is not None:
            device.click(index=phone_idx)
            device.input_text(number, index=phone_idx)
            return

        # Try to find an "Add phone number" button and click it
        add_idx = find_add_phone_button()
        if add_idx is not None:
            device.click(index=add_idx)
            device.settle(0.5)
            phone_idx = find_phone_field()
            if phone_idx is not None:
                device.click(index=phone_idx)
                device.input_text(number, index=phone_idx)
                return

        # Scroll down to reveal hidden fields/buttons
        for _ in range(5):
            device.scroll(direction="down")
            device.settle(0.3)
            phone_idx = find_phone_field()
            if phone_idx is not None:
                device.click(index=phone_idx)
                device.input_text(number, index=phone_idx)
                return
            add_idx = find_add_phone_button()
            if add_idx is not None:
                device.click(index=add_idx)
                device.settle(0.5)
                phone_idx = find_phone_field()
                if phone_idx is not None:
                    device.click(index=phone_idx)
                    device.input_text(number, index=phone_idx)
                    return

        # Try scrolling up (in case we scrolled past it)
        for _ in range(5):
            device.scroll(direction="up")
            device.settle(0.3)
            phone_idx = find_phone_field()
            if phone_idx is not None:
                device.click(index=phone_idx)
                device.input_text(number, index=phone_idx)
                return
            add_idx = find_add_phone_button()
            if add_idx is not None:
                device.click(index=add_idx)
                device.settle(0.5)
                phone_idx = find_phone_field()
                if phone_idx is not None:
                    device.click(index=phone_idx)
                    device.input_text(number, index=phone_idx)
                    return

        raise RuntimeError("Phone field not found")

    enter_phone_number(binding['number'])

    # Save
    def find_save():
        for text in ["Save", "Done"]:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        for desc in ["Save", "Done"]:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx
        idx = device.find(contains="Save", clickable=True)
        if idx is not None:
            return idx
        return None

    save_idx = find_save()
    if save_idx is None:
        for _ in range(5):
            device.scroll(direction="up")
            save_idx = find_save()
            if save_idx is not None:
                break
    if save_idx is None:
        raise RuntimeError("Save button not found")

    device.click(index=save_idx)
    device.settle(0.5)

    return True
