PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)."
    },
    "number": {
        "type": "string",
        "description": "Phone number, copied verbatim."
    }
}


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    parts = name.split()
    if len(parts) < 2:
        raise ValueError(f"Name must contain at least two words: {name!r}")
    first_name = parts[0]
    last_name = parts[1]

    def try_click(*args, **kwargs):
        idx = device.find(*args, **kwargs)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.0)
            return True
        return False

    def editor_open():
        return (
            device.find(hint='First name', editable=True) is not None
            or device.find(text='First name', editable=True) is not None
        )

    # --- 1. Open the contact editor ---
    opened = False

    # Try the Contacts app first (works for instance 2).
    try:
        device.open_app("Contacts")
        device.settle(2.0)
    except Exception:
        pass

    if editor_open():
        opened = True
    else:
        # People app / Contacts empty state: "Create contact"
        for kwargs in [
            {"description": "Create contact", "clickable": True},
            {"text": "Create contact", "clickable": True},
            {"contains": "Create contact", "clickable": True},
            {"text": "Add contact", "clickable": True},
            {"description": "Add contact", "clickable": True},
            {"description": "Add", "clickable": True},
            {"text": "Add", "clickable": True},
        ]:
            if try_click(**kwargs):
                device.settle(1.0)
                if editor_open():
                    opened = True
                    break

    # Fallback: dialer route (instances 1 and 3).
    if not opened:
        try:
            device.open_app("Phone")
            device.settle(2.0)
        except Exception:
            pass

        if editor_open():
            opened = True
        else:
            # Direct "Create new contact" in dialer.
            for kwargs in [
                {"text": "Create new contact", "clickable": True},
                {"contains": "Create new contact", "clickable": True},
                {"description": "Create new contact", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    if editor_open():
                        opened = True
                        break

        if not opened:
            # Maybe we need to switch to Favorites tab first (instance 3).
            for kwargs in [
                {"description": "Favorites", "clickable": True},
                {"text": "Favorites", "clickable": True},
                {"contains": "Favorites", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    break

            # Now try "Add a favorite" (opens editor in instance 3).
            for kwargs in [
                {"text": "Add a favorite", "clickable": True},
                {"description": "Add a favorite", "clickable": True},
                {"contains": "Add a favorite", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    if editor_open():
                        opened = True
                        break

        if not opened:
            # Maybe switch to Contacts tab in dialer and try again.
            for kwargs in [
                {"description": "Contacts", "clickable": True},
                {"text": "Contacts", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    break

            for kwargs in [
                {"text": "Create new contact", "clickable": True},
                {"contains": "Create new contact", "clickable": True},
                {"text": "Add a favorite", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    if editor_open():
                        opened = True
                        break

    if not opened or not editor_open():
        raise RuntimeError("Could not open the contact editor (First name field not found)")

    # --- 2. Fill in the name fields ---
    first_idx = device.find(hint='First name', editable=True)
    if first_idx is None:
        first_idx = device.find(text='First name', editable=True)
    if first_idx is None:
        raise RuntimeError("First name field not found")
    device.input_text(first_name, index=first_idx)
    device.settle(0.5)

    last_idx = device.find(hint='Last name', editable=True)
    if last_idx is None:
        last_idx = device.find(text='Last name', editable=True)
    if last_idx is None:
        raise RuntimeError("Last name field not found")
    device.input_text(last_name, index=last_idx)
    device.settle(0.5)

    # --- 3. Fill in the phone number ---
    phone_idx = None
    for _ in range(10):
        phone_idx = device.find(hint='Phone', editable=True)
        if phone_idx is None:
            phone_idx = device.find(hint='Mobile', editable=True)
        if phone_idx is None:
            phone_idx = device.find(text='Phone', editable=True)
        if phone_idx is not None:
            break
        device.scroll("down")
        device.settle(0.5)

    if phone_idx is None:
        raise RuntimeError("Phone number field not found")

    device.input_text(number, index=phone_idx)
    device.settle(0.5)

    # --- 4. Save ---
    save_idx = device.find(text='Save', clickable=True)
    if save_idx is None:
        save_idx = device.find(description='Save', clickable=True)
    if save_idx is None:
        save_idx = device.find(contains='Save', clickable=True)
    if save_idx is None:
        # try scrolling up to reveal top bar
        for _ in range(5):
            device.scroll("up")
            device.settle(0.5)
            save_idx = device.find(text='Save', clickable=True)
            if save_idx is None:
                save_idx = device.find(description='Save', clickable=True)
            if save_idx is not None:
                break
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    device.settle(1.5)

    return True
