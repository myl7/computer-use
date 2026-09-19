PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    def find_first_name():
        idx = device.find(hint='First name', editable=True)
        if idx is not None:
            return idx
        idx = device.find(text='First name', editable=True)
        if idx is not None:
            return idx
        idx = device.find(contains='First name', editable=True)
        if idx is not None:
            return idx
        return None

    def find_last_name():
        idx = device.find(hint='Last name', editable=True)
        if idx is not None:
            return idx
        idx = device.find(text='Last name', editable=True)
        if idx is not None:
            return idx
        idx = device.find(contains='Last name', editable=True)
        if idx is not None:
            return idx
        return None

    def find_phone():
        for hint in ('Phone', 'Phone number', 'Mobile'):
            idx = device.find(hint=hint, editable=True)
            if idx is not None:
                return idx
        for text in ('Phone', 'Phone number', 'Mobile'):
            idx = device.find(text=text, editable=True)
            if idx is not None:
                return idx
        for contains in ('Phone', 'Mobile'):
            idx = device.find(contains=contains, editable=True)
            if idx is not None:
                return idx
        return None

    def find_create_contact():
        patterns = [
            {"text": "Create contact", "clickable": True},
            {"description": "Create contact", "clickable": True},
            {"text": "Create new contact", "clickable": True},
            {"description": "Create new contact", "clickable": True},
            {"text": "Add a favorite", "clickable": True},
            {"description": "Add a favorite", "clickable": True},
            {"text": "Add contact", "clickable": True},
            {"description": "Add contact", "clickable": True},
            {"text": "Create", "clickable": True},
            {"description": "Create", "clickable": True},
        ]
        for pat in patterns:
            idx = device.find(**pat)
            if idx is not None:
                return idx
        for contains in ("Create contact", "Create new contact", "Add a favorite", "Add contact"):
            idx = device.find(contains=contains, clickable=True)
            if idx is not None:
                return idx
        idx = device.find(text="+", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="+", clickable=True)
        if idx is not None:
            return idx
        return None

    def find_save():
        for text in ("Save", "Done", "OK"):
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        idx = device.find(description="Save", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains="Save", clickable=True)
        if idx is not None:
            return idx
        return None

    def open_editor():
        opened_contacts = False
        for app in ("Contacts", "Google Contacts"):
            try:
                device.open_app(app)
                opened_contacts = True
                break
            except Exception:
                continue

        create = find_create_contact()
        if create is not None:
            device.click(index=create)
            return

        if opened_contacts:
            tab = device.find(text="Contacts", clickable=True)
            if tab is None:
                tab = device.find(description="Contacts", clickable=True)
            if tab is not None:
                device.click(index=tab)
                create = find_create_contact()
                if create is not None:
                    device.click(index=create)
                    return

        # Fallback: open the Phone app and use its create entry
        try:
            device.open_app("Phone")
        except Exception:
            try:
                device.open_app("Dialer")
            except Exception:
                raise RuntimeError("Could not open Contacts or Phone app")

        fav = device.find(text="Favorites", clickable=True)
        if fav is None:
            fav = device.find(description="Favorites", clickable=True)
        if fav is not None:
            device.click(index=fav)

        create = find_create_contact()
        if create is None:
            raise RuntimeError("Could not find a way to create a new contact")
        device.click(index=create)

    open_editor()

    first_name_field = find_first_name()
    if first_name_field is None:
        raise RuntimeError("First name field not found")

    last_name_field = find_last_name()
    if last_name_field is None:
        raise RuntimeError("Last name field not found")

    first_word, last_word = binding['name'].split()

    device.input_text(first_word, index=first_name_field)
    device.input_text(last_word, index=last_name_field)

    phone_field = find_phone()
    if phone_field is None:
        for _ in range(10):
            device.scroll(direction="down")
            phone_field = find_phone()
            if phone_field is not None:
                break
    if phone_field is None:
        raise RuntimeError("Phone number field not found")

    device.input_text(binding['number'], index=phone_field)

    save_button = find_save()
    if save_button is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_button)

    return True
