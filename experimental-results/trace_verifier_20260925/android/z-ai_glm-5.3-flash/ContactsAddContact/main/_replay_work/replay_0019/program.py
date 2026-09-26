PARAMS_SCHEMA = {
    "name": {"type": "string", "description": "Full contact name, exactly two words (first and last)"},
    "number": {"type": "string", "description": "Phone number, one string copied verbatim"},
}


def program(device, binding: dict) -> bool:
    name = binding["name"]
    number = binding["number"]
    words = name.split()
    if len(words) != 2:
        raise ValueError("binding['name'] must be exactly two words")
    first, last = words

    def contacts_ready():
        if device.find(description="Create contact") is not None:
            return True
        if device.find(text="No contacts yet") is not None:
            return True
        if device.find(description="Contacts") is not None:
            return True
        return False

    # Launch the Contacts app. The accessibility tree is sometimes
    # empty/stale right after launch, so poll for elements to appear and,
    # if the screen never becomes usable, go home and relaunch fresh.
    # (Do NOT fall back to "Google Contacts": that resolves to an unrelated
    # OptInActivity and gets the flow stuck.)
    opened = False
    for attempt in range(3):
        device.open_app("Contacts")
        device.settle(2)
        for _ in range(4):
            if contacts_ready():
                opened = True
                break
            # Tree may be empty or stale; give it time to populate.
            device.settle(2)
        if opened:
            break
        # Stuck on a wrong/empty activity: exit it and relaunch.
        device.navigate_home()
        device.settle(1)
    if not opened:
        raise RuntimeError("Contacts app did not open")

    # Open the contact editor via the floating "Create contact" button
    btn = device.find(description="Create contact", clickable=True)
    if btn is None:
        btn = device.find(description="Create contact")
    if btn is None:
        device.scroll("down")
        btn = device.find(description="Create contact", clickable=True)
    if btn is None:
        btn = device.find(contains="Create contact", clickable=True)
    if btn is None:
        raise LookupError("'Create contact' button not found")
    device.click(btn)
    device.settle(2)

    # If prompted where to save the contact, pick the first listed account
    if device.find(text="Choose an account") is not None:
        target = None
        for el in device.elements():
            t = el.get("text") or ""
            if el.get("clickable") and "@" in t:
                target = el["index"]
                break
        if target is not None:
            device.click(target)
            device.settle(2)

    # First name field
    idx = device.find(hint="First name", editable=True)
    if idx is None:
        idx = device.find(text="First name", editable=True)
    if idx is None:
        device.scroll("down")
        idx = device.find(hint="First name", editable=True)
    if idx is None:
        idx = device.find(text="First name", editable=True)
    if idx is None:
        raise RuntimeError("First name field not found on contact editor screen")
    device.input_text(first, index=idx)

    # Last name field
    idx = device.find(hint="Last name", editable=True)
    if idx is None:
        idx = device.find(text="Last name", editable=True)
    if idx is None:
        device.scroll("down")
        idx = device.find(hint="Last name", editable=True)
    if idx is None:
        idx = device.find(text="Last name", editable=True)
    if idx is None:
        raise LookupError("Last name EditText not found on contact editor screen")
    device.input_text(last, index=idx)

    # Phone field (leave label at default)
    idx = device.find(hint="Phone", editable=True)
    if idx is None:
        idx = device.find(contains="Phone", editable=True)
    if idx is None:
        device.scroll("down")
        idx = device.find(hint="Phone", editable=True)
    if idx is None:
        idx = device.find(contains="Phone", editable=True)
    if idx is None:
        raise LookupError("Phone number EditText not found in contact editor")
    device.input_text(number, index=idx)
    device.settle(1)

    # Save the contact
    idx = device.find(text="Save", clickable=True)
    if idx is None:
        idx = device.find(description="Save", clickable=True)
    if idx is None:
        device.scroll("down")
        idx = device.find(text="Save", clickable=True)
    if idx is None:
        idx = device.find(description="Save", clickable=True)
    if idx is None:
        idx = device.find(contains="Save", clickable=True)
    if idx is None:
        raise LookupError("Contact editor 'Save' button not found on screen")
    device.click(idx)
    device.settle(2)

    return True
