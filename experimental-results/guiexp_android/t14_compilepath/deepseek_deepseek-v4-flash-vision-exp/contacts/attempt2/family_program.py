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
    def find_any(*criteria):
        for crit in criteria:
            idx = device.find(**crit)
            if idx is not None:
                return idx
        return None

    def find_in_elements(predicate):
        for el in device.elements():
            if predicate(el):
                return el['index']
        return None

    # Open the Phone app (dialer)
    device.open_app("com.google.android.dialer")
    device.settle()

    # Find and click "Create new contact" button
    create_btn = find_any(
        {"text": "Create new contact"},
        {"description": "Create new contact"},
        {"text": "Add contact"},
        {"description": "Add contact"}
    )
    if create_btn is None:
        create_btn = find_in_elements(
            lambda el: el.get('text') in ('Create new contact', 'Add contact')
            or el.get('description') in ('Create new contact', 'Add contact')
        )
    if create_btn is None:
        # Try navigating to the Contacts tab
        contacts_tab = find_any({"text": "Contacts"}, {"description": "Contacts"})
        if contacts_tab is not None:
            device.click(index=contacts_tab)
            device.settle()
            create_btn = find_any(
                {"text": "Create new contact"},
                {"description": "Create new contact"},
                {"text": "Add contact"},
                {"description": "Add contact"}
            )
            if create_btn is None:
                create_btn = find_in_elements(
                    lambda el: el.get('text') in ('Create new contact', 'Add contact')
                    or el.get('description') in ('Create new contact', 'Add contact')
                )
    if create_btn is None:
        # Try opening the overflow menu
        more_options = find_any({"description": "More options"}, {"text": "More options"})
        if more_options is not None:
            device.click(index=more_options)
            device.settle()
            create_btn = find_any(
                {"text": "Create new contact"},
                {"description": "Create new contact"},
                {"text": "Add contact"},
                {"description": "Add contact"}
            )
            if create_btn is None:
                create_btn = find_in_elements(
                    lambda el: el.get('text') in ('Create new contact', 'Add contact')
                    or el.get('description') in ('Create new contact', 'Add contact')
                )
    if create_btn is None:
        raise Exception("Could not find Create new contact button")
    device.click(index=create_btn)
    device.settle()

    # Fill first name
    first_name_field = find_any({"hint": "First name"}, {"text": "First name"})
    if first_name_field is None:
        first_name_field = find_in_elements(
            lambda el: el.get('hint') == 'First name' or el.get('text') == 'First name'
        )
    if first_name_field is None:
        raise Exception("Could not find First name field")
    device.click(index=first_name_field)
    device.input_text(binding['name'].split()[0], index=first_name_field)
    device.settle()

    # Fill last name
    last_name_field = find_any({"hint": "Last name"}, {"text": "Last name"})
    if last_name_field is None:
        last_name_field = find_in_elements(
            lambda el: el.get('hint') == 'Last name' or el.get('text') == 'Last name'
        )
    if last_name_field is None:
        raise Exception("Could not find Last name field")
    device.click(index=last_name_field)
    device.input_text(binding['name'].split()[1], index=last_name_field)
    device.settle()

    # Scroll down to reveal phone field
    device.scroll(direction='down')
    device.settle()

    # Fill phone number
    phone_field = find_any(
        {"hint": "Phone number"},
        {"hint": "Phone"},
        {"hint": "Mobile"},
        {"text": "Phone number"},
        {"text": "Phone"},
        {"text": "Mobile"}
    )
    if phone_field is None:
        phone_field = find_in_elements(
            lambda el: el.get('hint') in ('Phone number', 'Phone', 'Mobile')
            or el.get('text') in ('Phone number', 'Phone', 'Mobile')
        )
    if phone_field is None:
        raise Exception("Could not find Phone field")
    device.click(index=phone_field)
    device.input_text(binding['number'], index=phone_field)
    device.settle()

    # Save
    save_btn = find_any(
        {"text": "Save"},
        {"description": "Save"},
        {"description": "Save contact"}
    )
    if save_btn is None:
        save_btn = find_in_elements(
            lambda el: el.get('text') == 'Save' or el.get('description') in ('Save', 'Save contact')
        )
    if save_btn is None:
        raise Exception("Could not find Save button")
    device.click(index=save_btn)
    device.settle()

    return True
