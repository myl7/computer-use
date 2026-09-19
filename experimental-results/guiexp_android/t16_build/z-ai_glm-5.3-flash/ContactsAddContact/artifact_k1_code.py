PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim.",
        "required": True,
    },
}


def _locate(device, criteria_list, what, allow_scroll=False):
    """Find an element trying several find-criteria in order.

    Optionally retries once after scrolling down (editors can push the
    phone field below the fold on small screens).
    """
    attempts = 2 if allow_scroll else 1
    for attempt in range(attempts):
        for crit in criteria_list:
            idx = device.find(**crit)
            if idx is not None:
                return idx
        if attempt + 1 < attempts:
            device.scroll("down")
            device.settle(1)
    raise RuntimeError("%s not found on current screen" % what)


def program(device, binding: dict) -> bool:
    # ---------- validate binding ----------
    if set(binding.keys()) != {"name", "number"}:
        raise ValueError(
            "binding must contain exactly the keys 'name' and 'number', got %r"
            % sorted(binding.keys())
        )
    name = str(binding["name"]).strip()
    number = str(binding["number"]).strip()
    words = name.split()
    if len(words) != 2:
        raise ValueError(
            "binding['name'] must be exactly two words (first and last), got %r" % name
        )
    first_name, last_name = words[0], words[1]

    # ---------- open the Contacts app ----------
    device.open_app("Contacts")
    device.settle(2)
    if (device.find(description="Create contact") is None
            and device.find(contains="Create contact") is None
            and device.find(text="No contacts yet") is None):
        raise RuntimeError("Contacts app did not open (PeopleActivity not detected)")

    # ---------- tap the 'Create contact' FAB ----------
    create_btn = _locate(
        device,
        [
            {"description": "Create contact", "clickable": True},
            {"description": "create contact", "clickable": True},
            {"contains": "Create contact", "clickable": True},
            {"contains": "Create", "clickable": True},
            {"text": "Create contact"},
        ],
        "'Create contact' button",
    )
    device.click(create_btn)
    device.settle(2)

    # ---------- fill the editor ----------
    # First word -> 'First name' field
    first_field = _locate(
        device,
        [
            {"hint": "First name", "editable": True},
            {"text": "First name", "editable": True},
            {"contains": "First name", "editable": True},
        ],
        "'First name' field",
    )
    device.input_text(first_name, index=first_field)

    # Second word -> 'Last name' field
    last_field = _locate(
        device,
        [
            {"hint": "Last name", "editable": True},
            {"text": "Last name", "editable": True},
            {"contains": "Last name", "editable": True},
        ],
        "'Last name' field",
    )
    device.input_text(last_name, index=last_field)

    # Number -> 'Phone' field (leave phone label at its default)
    phone_field = _locate(
        device,
        [
            {"hint": "Phone", "editable": True},
            {"text": "Phone", "editable": True},
            {"contains": "Phone", "editable": True},
        ],
        "'Phone' field",
        allow_scroll=True,
    )
    device.input_text(number, index=phone_field)

    # ---------- save ----------
    save_btn = _locate(
        device,
        [
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ],
        "'Save' button",
    )
    device.click(save_btn)
    device.settle(2)

    # ---------- verify the editor closed ----------
    if device.find(hint="First name", editable=True) is not None:
        raise RuntimeError("Contact editor still open after tapping Save")
    return True
