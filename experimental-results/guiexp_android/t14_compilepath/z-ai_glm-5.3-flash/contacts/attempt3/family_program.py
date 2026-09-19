"""Reusable GUI program: create a Google Contacts contact.

Replays the recorded trajectory with dynamic re-locating of every element:
  1. Open the Phone (dialer) app.
  2. Switch to its 'Contacts' tab.
  3. Tap 'Create new contact' -> ContactEditorActivity.
  4. Type word 1 of the binding name into the 'First name' field.
  5. Type word 2 of the binding name into the 'Last name' field.
  6. Type the binding number into the 'Phone' field (label left at default).
  7. Tap 'Save' and verify the editor closed.
"""

PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "required": True,
        "description": (
            "Full contact name, exactly two words: the first word goes to "
            "the 'First name' field, the second to the 'Last name' field."
        ),
    },
    "number": {
        "type": "string",
        "required": True,
        "description": (
            "Phone number as one string, typed verbatim into the 'Phone' "
            "field; the phone label is left at its default."
        ),
    },
}

_CREATE_CRITERIA = (
    {"text": "Create new contact"},
    {"contains": "Create new contact"},
    {"description": "Create new contact"},
    {"contains": "Create new contacts"},
    {"description": "Create new contacts"},
)
_CREATE_NEEDLES = ("create new contact", "create contact")

_FIRST_NAME_CRITERIA = (
    {"hint": "First name"},
    {"text": "First name"},
    {"contains": "First name"},
)
_FIRST_NAME_NEEDLES = ("first name", "first")

_LAST_NAME_CRITERIA = (
    {"hint": "Last name"},
    {"text": "Last name"},
    {"contains": "Last name"},
)
_LAST_NAME_NEEDLES = ("last name", "last")

_PHONE_CRITERIA = (
    {"hint": "Phone"},
    {"text": "Phone"},
    {"contains": "Phone"},
)
_PHONE_NEEDLES = ("phone",)

_SAVE_CRITERIA = (
    {"text": "Save"},
    {"contains": "Save"},
    {"description": "Save"},
)
_SAVE_NEEDLES = ("save",)

_EDITOR_OPEN_CRITERIA = _FIRST_NAME_CRITERIA + _LAST_NAME_CRITERIA

_EDITOR_STILL_OPEN_CRITERIA = (
    {"hint": "First name"},
    {"text": "First name"},
    {"hint": "Last name"},
    {"text": "Last name"},
    {"text": "Save"},
)


def _validate_binding(binding):
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict with keys 'name' and 'number'")
    for key in ("name", "number"):
        if key not in binding:
            raise ValueError("binding is missing required key %r" % key)
    words = str(binding["name"]).split()
    if len(words) != 2:
        raise ValueError(
            "binding['name'] must contain exactly two words, got %r"
            % (binding["name"],)
        )
    number = binding["number"]
    if number is None or str(number) == "":
        raise ValueError("binding['number'] must be a non-empty string")
    return words[0], words[1], str(number)


def _find(device, criteria):
    """Try each find-criteria dict in order; return the first hit or None."""
    for kwargs in criteria:
        try:
            idx = device.find(**kwargs)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    return None


def _scan(device, needles, clickable=None):
    """Scan a fresh element list for text/hint/description containing needles."""
    try:
        elements = device.elements()
    except Exception:
        return None
    for element in elements:
        if clickable is not None and bool(element.get("clickable")) is not clickable:
            continue
        haystack = " ".join(
            str(element.get(key) or "")
            for key in ("text", "hint", "description")
        ).lower()
        if any(needle in haystack for needle in needles):
            return element.get("index")
    return None


def _require(device, criteria, needles, what):
    idx = _find(device, criteria)
    if idx is None:
        idx = _scan(device, needles)
    if idx is None:
        raise RuntimeError("Could not locate %s on the current screen" % what)
    return idx


def _type_into(device, text, criteria, needles, what):
    idx = _require(device, criteria, needles, what)
    device.input_text(text, index=idx)


def _open_contact_editor(device):
    """Get to the ContactEditorActivity via 'Create new contact'."""

    def tap_create():
        idx = _find(device, _CREATE_CRITERIA)
        if idx is None:
            idx = _scan(device, _CREATE_NEEDLES, clickable=True)
        if idx is None:
            return False
        device.click(idx)
        return True

    # Already inside the editor (e.g. a retry) -> nothing to navigate.
    if _find(device, _EDITOR_OPEN_CRITERIA) is not None:
        return
    if tap_create():
        return

    for app_name in ("Phone", "Contacts", "Google Contacts", "Dialer"):
        try:
            device.open_app(app_name)
        except Exception:
            continue
        if _find(device, _EDITOR_OPEN_CRITERIA) is not None:
            return
        if tap_create():
            return
        # The dialer opens on the Phone tab; switch to the Contacts tab.
        tab = _find(device, ({"description": "Contacts"}, {"text": "Contacts"}))
        if tab is None:
            tab = _scan(device, ("contacts",), clickable=True)
        if tab is not None:
            try:
                device.click(tab)
            except Exception:
                continue
            if tap_create():
                return

    raise RuntimeError("Could not open the 'Create new contact' editor")


def program(device, binding: dict) -> bool:
    first_name, last_name, phone_number = _validate_binding(binding)

    # Steps 1-3: Phone app -> Contacts tab -> 'Create new contact'.
    _open_contact_editor(device)

    # Step 4: first word of the name -> 'First name' field.
    _type_into(device, first_name, _FIRST_NAME_CRITERIA, _FIRST_NAME_NEEDLES,
               "the 'First name' field")

    # Step 5: second word of the name -> 'Last name' field.
    _type_into(device, last_name, _LAST_NAME_CRITERIA, _LAST_NAME_NEEDLES,
               "the 'Last name' field")

    # Step 6: number -> 'Phone' field (phone label stays at its default).
    _type_into(device, phone_number, _PHONE_CRITERIA, _PHONE_NEEDLES,
               "the 'Phone' field")

    # Step 7: save the contact.
    save_index = _require(device, _SAVE_CRITERIA, _SAVE_NEEDLES,
                          "the 'Save' button")
    device.click(save_index)

    # A successful save closes the editor; if it is still open, save failed.
    device.settle(2)
    if _find(device, _EDITOR_STILL_OPEN_CRITERIA) is not None:
        raise RuntimeError("Contact editor is still open after tapping Save")

    return True
