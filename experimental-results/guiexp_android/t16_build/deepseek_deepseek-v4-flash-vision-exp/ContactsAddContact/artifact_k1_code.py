PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)."
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim."
    }
}

def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    parts = name.split()
    if len(parts) != 2:
        raise ValueError("Contact name must be exactly two words (first and last)")
    first_name, last_name = parts[0], parts[1]

    def find_editable(hint=None, contains=None, text=None):
        if hint is not None:
            idx = device.find(hint=hint, editable=True)
            if idx is not None:
                return idx
        if contains is not None:
            idx = device.find(contains=contains, editable=True)
            if idx is not None:
                return idx
        if text is not None:
            idx = device.find(text=text, editable=True)
            if idx is not None:
                return idx
        return None

    def find_any_editable(hint, contains, text):
        idx = find_editable(hint=hint)
        if idx is not None:
            return idx
        idx = find_editable(contains=contains)
        if idx is not None:
            return idx
        return find_editable(text=text)

    def open_phone_app():
        try:
            device.open_app("Phone")
            return True
        except Exception:
            pass
        for kwargs in [
            {"text": "Phone", "description": "Phone", "clickable": True},
            {"text": "Phone", "clickable": True},
            {"contains": "Phone", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                return True
        return False

    def open_create_contact():
        for kwargs in [
            {"text": "Create new contact", "clickable": True},
            {"contains": "Create new contact", "clickable": True},
            {"description": "Create new contact", "clickable": True},
            {"contains": "Add contact", "clickable": True},
            {"contains": "New contact", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                return True

        for kwargs in [
            {"text": "Contacts", "clickable": True},
            {"description": "Contacts", "clickable": True},
            {"contains": "Contacts", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                device.settle(2)
                for kwargs2 in [
                    {"text": "Create new contact", "clickable": True},
                    {"contains": "Create new contact", "clickable": True},
                    {"description": "Create new contact", "clickable": True},
                    {"contains": "Add contact", "clickable": True},
                    {"contains": "New contact", "clickable": True},
                ]:
                    idx2 = device.find(**kwargs2)
                    if idx2 is not None:
                        device.click(index=idx2)
                        return True
                break

        for kwargs in [
            {"description": "Create new contact", "clickable": True},
            {"description": "Add contact", "clickable": True},
            {"contains": "Add", "clickable": True},
            {"contains": "Create", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                device.settle(2)
                if find_editable(hint="First name") is not None:
                    return True
                for kwargs2 in [
                    {"text": "Create new contact", "clickable": True},
                    {"contains": "Create new contact", "clickable": True},
                    {"description": "Create new contact", "clickable": True},
                ]:
                    idx2 = device.find(**kwargs2)
                    if idx2 is not None:
                        device.click(index=idx2)
                        return True
                break

        for kwargs in [
            {"description": "More options", "clickable": True},
            {"description": "More", "clickable": True},
            {"text": "More", "clickable": True},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                device.settle(2)
                for kwargs2 in [
                    {"text": "Create new contact", "clickable": True},
                    {"contains": "Create new contact", "clickable": True},
                    {"description": "Create new contact", "clickable": True},
                ]:
                    idx2 = device.find(**kwargs2)
                    if idx2 is not None:
                        device.click(index=idx2)
                        return True
                break

        return False

    if not open_phone_app():
        raise RuntimeError("Could not open Phone app")
    device.settle(2)

    if not open_create_contact():
        try:
            device.open_app("Contacts")
            device.settle(2)
            if not open_create_contact():
                raise RuntimeError("Could not find 'Create new contact' entry")
        except Exception:
            raise RuntimeError("Could not find 'Create new contact' entry")

    first_idx = None
    for _ in range(5):
        first_idx = find_any_editable(hint="First name", contains="First name", text="First name")
        if first_idx is not None:
            break
        device.settle(1)
    if first_idx is None:
        raise RuntimeError("Could not find First name field")

    device.click(index=first_idx)
    device.input_text(first_name, index=first_idx)
    device.settle(1)

    last_idx = None
    for _ in range(5):
        last_idx = find_any_editable(hint="Last name", contains="Last name", text="Last name")
        if last_idx is not None:
            break
        device.settle(1)
    if last_idx is None:
        raise RuntimeError("Could not find Last name field")

    device.click(index=last_idx)
    device.input_text(last_name, index=last_idx)
    device.settle(1)

    phone_idx = None
    for _ in range(15):
        phone_idx = find_any_editable(hint="Phone", contains="Phone", text="Phone")
        if phone_idx is not None:
            break
        device.scroll(direction="down")
        device.settle(1)

    if phone_idx is None:
        for _ in range(15):
            device.scroll(direction="up")
            device.settle(1)
            phone_idx = find_any_editable(hint="Phone", contains="Phone", text="Phone")
            if phone_idx is not None:
                break

    if phone_idx is None:
        raise RuntimeError("Could not find Phone field")

    device.click(index=phone_idx)
    device.input_text(number, index=phone_idx)
    device.settle(1)

    save_idx = None
    for kwargs in [
        {"text": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
        {"description": "Save", "clickable": True},
        {"text": "Save"},
        {"description": "Save"},
        {"text": "Done", "clickable": True},
        {"description": "Done", "clickable": True},
    ]:
        save_idx = device.find(**kwargs)
        if save_idx is not None:
            break

    if save_idx is None:
        raise RuntimeError("Could not find Save button")

    device.click(index=save_idx)
    device.settle(2)

    return True
