PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number, typed verbatim into the Phone field (label left at default)",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    """Create a new Google Contacts contact for binding['name'] with phone
    number binding['number'], driving the touchscreen UI only.

    Flow: open Contacts -> tap 'Create contact' -> fill First name /
    Last name / Phone in the editor -> tap Save -> verify editor closed.
    """

    def locate(msg, *criteria_list):
        # Try each find-criteria dict in order; raise if none matches.
        for criteria in criteria_list:
            idx = device.find(**criteria)
            if idx is not None:
                return idx
        raise RuntimeError(msg)

    name = str(binding["name"]).strip()
    words = name.split()
    if len(words) != 2:
        raise ValueError("binding['name'] must contain exactly two words, got %r" % name)
    first_name, last_name = words[0], words[1]
    number = str(binding["number"])

    # ------------------------------------------------------------------
    # 1. Bring the Contacts app to the foreground.
    # ------------------------------------------------------------------
    def contacts_ready():
        return (device.find(description="Create contact") is not None
                or device.find(text="Create contact") is not None
                or device.find(contains="Create contact") is not None)

    opened = False
    for app_name in ("Contacts", "Google Contacts"):
        try:
            device.open_app(app_name)
        except Exception:
            continue
        device.settle(2)
        if contacts_ready():
            opened = True
            break
    if not opened:
        # Fallback: launch from the home-screen launcher icon.
        device.navigate_home()
        device.settle(1)
        icon = device.find(description="Contacts", clickable=True)
        if icon is None:
            icon = device.find(text="Contacts", clickable=True)
        if icon is not None:
            device.click(icon)
            device.settle(2)
        if not contacts_ready():
            raise RuntimeError("Contacts app did not open (PeopleActivity not detected)")

    # ------------------------------------------------------------------
    # 2. Tap the 'Create contact' FAB to open the contact editor.
    # ------------------------------------------------------------------
    create = locate(
        "'Create contact' button not found in the Contacts app",
        {"description": "Create contact", "clickable": True},
        {"text": "Create contact", "clickable": True},
        {"contains": "Create contact", "clickable": True},
        {"description": "Create contact"},
        {"text": "Create contact"},
    )
    device.click(create)
    device.settle(2)
    if device.find(editable=True) is None:
        raise RuntimeError("Contact editor did not open after tapping 'Create contact'")

    # ------------------------------------------------------------------
    # 3-5. Fill First name / Last name / Phone in the editor.
    # ------------------------------------------------------------------
    def fill_field(label, value, fallback_pos, msg):
        idx = None
        for _ in range(2):
            idx = device.find(hint=label, editable=True)
            if idx is None:
                idx = device.find(contains=label, editable=True)
            if idx is not None:
                break
            device.scroll(direction="down")
            device.settle(1)
        if idx is None:
            editable = [e for e in device.elements() if e.get("editable")]
            if len(editable) > fallback_pos:
                idx = editable[fallback_pos]["index"]
        if idx is None:
            raise RuntimeError(msg)
        device.click(index=idx)
        device.input_text(value, index=idx)

    fill_field("First name", first_name, 0,
               "Contact editor: 'First name' field not found")
    fill_field("Last name", last_name, 1,
               "Contact editor: 'Last name' field not found")
    fill_field("Phone", number, 2,
               "Contact editor: 'Phone' field not found")

    # ------------------------------------------------------------------
    # 6. Save the contact (checkmark / Save action in the editor app bar).
    # ------------------------------------------------------------------
    save = locate(
        "Save button not found in the contact editor",
        {"description": "Save", "clickable": True},
        {"text": "Save", "clickable": True},
        {"contains": "Save", "clickable": True},
        {"description": "Done", "clickable": True},
        {"text": "Done", "clickable": True},
    )
    device.click(save)
    device.settle(2)

    # Verify the editor closed (contact was saved).
    if device.find(hint="First name", editable=True) is not None:
        retry = device.find(description="Save", clickable=True)
        if retry is None:
            retry = device.find(text="Save", clickable=True)
        if retry is not None:
            device.click(retry)
            device.settle(2)
    if device.find(hint="First name", editable=True) is not None:
        raise RuntimeError("Contact editor still open after save; contact may not have been saved")

    return True
