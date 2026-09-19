"""Reusable program: create a new Google Contacts contact with first/last name and phone."""

PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last).",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number stored verbatim in the contact's Phone field.",
        "required": True,
    },
}


def _split_name(name):
    if not isinstance(name, str):
        raise ValueError("binding['name'] must be a string, got %r" % (name,))
    words = name.strip().split()
    if len(words) < 2:
        raise ValueError(
            "binding['name'] must contain a first and a last name, got %r" % (name,)
        )
    return words[0], " ".join(words[1:])


def _opt(device, **criteria):
    """device.find that returns None instead of raising."""
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _find_any(device, alternatives):
    for criteria in alternatives:
        idx = _opt(device, **criteria)
        if idx is not None:
            return idx
    return None


def _click_any(device, what, alternatives):
    idx = _find_any(device, alternatives)
    if idx is None:
        raise RuntimeError("Cannot %s: no element matched any of %r" % (what, alternatives))
    device.click(index=idx)
    return idx


def _type_any(device, what, text, alternatives):
    idx = _find_any(device, alternatives)
    if idx is None:
        raise RuntimeError("Cannot type into %s: no element matched any of %r" % (what, alternatives))
    device.input_text(text, index=idx)
    # Light verification: if the field still looks empty, focus it and retry once.
    try:
        elem = next((e for e in device.elements() if e.get("index") == idx), None)
        if elem is not None and not (elem.get("text") or "").strip():
            device.click(index=idx)
            device.input_text(text, index=idx)
    except Exception:
        pass
    return idx


def program(device, binding: dict) -> bool:
    for key in ("name", "number"):
        if key not in binding or binding[key] is None:
            raise KeyError("binding must provide %r" % key)

    first, last = _split_name(binding["name"])
    number = str(binding["number"])

    # 1) Make sure we are in the Phone (dialer) app, like the recording's first tap.
    if _opt(device, description="Contacts") is None:
        if _find_any(device, [{"text": "Phone"}, {"description": "Phone"}]) is not None:
            _click_any(device, "open the Phone app",
                       [{"text": "Phone"}, {"description": "Phone"}])
        else:
            device.open_app("Phone")
        device.wait()

    # 2) Switch to the Contacts tab inside the dialer.
    _click_any(device, "switch to the Contacts tab",
               [{"description": "Contacts"}, {"text": "Contacts"}])

    # 3) Open the create-contact editor.
    create_alts = [
        {"text": "Create new contact"},
        {"description": "Create new contact"},
        {"contains": "Create new contact"},
        {"description": "Add"},
    ]
    idx = _find_any(device, create_alts)
    if idx is None:
        device.scroll("up")
        idx = _find_any(device, create_alts)
    if idx is None:
        raise RuntimeError("'Create new contact' entry point not found on Contacts tab")
    device.click(index=idx)

    # 4) First name <- first word of binding['name']
    _type_any(device, "the First name field", first, [
        {"hint": "First name", "editable": True},
        {"text": "First name", "editable": True},
        {"contains": "First name", "editable": True},
    ])

    # 5) Last name <- second word of binding['name']
    _type_any(device, "the Last name field", last, [
        {"hint": "Last name", "editable": True},
        {"text": "Last name", "editable": True},
        {"contains": "Last name", "editable": True},
    ])

    # 6) Phone <- binding['number'] (leave the phone label at its default)
    _type_any(device, "the Phone field", number, [
        {"hint": "Phone", "editable": True},
        {"text": "Phone", "editable": True},
        {"contains": "Phone", "editable": True},
    ])

    # 7) Save and confirm the editor closed (recording returns to the dialer).
    _click_any(device, "save the contact",
               [{"text": "Save"}, {"description": "Save"}])
    for _ in range(3):
        device.settle(1)
        if _opt(device, text="Save") is None and _opt(device, description="Save") is None:
            return True
        try:
            device.click(text="Save")
        except Exception:
            pass
    raise RuntimeError("Contact editor did not close after tapping Save")
