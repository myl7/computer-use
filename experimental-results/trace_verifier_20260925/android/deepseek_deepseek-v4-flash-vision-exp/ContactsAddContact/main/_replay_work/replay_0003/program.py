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
    },
}


def _s(value):
    return (value or "").strip()


def _elements(device):
    try:
        elems = device.elements()
    except Exception:
        return []
    return [e for e in elems if isinstance(e, dict)]


def _find_exact(device, keywords, editable=None):
    for kw in keywords:
        for attr in ("hint", "text", "description"):
            kwargs = {attr: kw}
            if editable is not None:
                kwargs["editable"] = editable
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
    return None


def _find_contains(device, keywords, editable=None):
    for kw in keywords:
        kwargs = {"contains": kw}
        if editable is not None:
            kwargs["editable"] = editable
        idx = device.find(**kwargs)
        if idx is not None:
            return idx
    return None


def _find_create_contact(device):
    patterns = [
        {"text": "Create contact", "clickable": True},
        {"description": "Create contact", "clickable": True},
        {"text": "Create new contact", "clickable": True},
        {"description": "Create new contact", "clickable": True},
        {"text": "Add contact", "clickable": True},
        {"description": "Add contact", "clickable": True},
        {"text": "New contact", "clickable": True},
        {"description": "New contact", "clickable": True},
    ]
    for pat in patterns:
        idx = device.find(**pat)
        if idx is not None:
            return idx
    for contains in ("Create contact", "Create new contact", "Add contact", "New contact"):
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


def _open_new_contact_editor(device):
    for app in ("Contacts", "Google Contacts"):
        try:
            device.open_app(app)
            break
        except Exception:
            continue
    else:
        raise RuntimeError("Could not open Contacts app")
    device.wait()

    create = _find_create_contact(device)
    if create is not None:
        device.click(index=create)
        device.wait()
        return

    for kwargs in ({"text": "Contacts", "clickable": True},
                   {"description": "Contacts", "clickable": True},
                   {"contains": "Contacts", "clickable": True}):
        tab = device.find(**kwargs)
        if tab is not None:
            device.click(index=tab)
            device.wait()
            create = _find_create_contact(device)
            if create is not None:
                device.click(index=create)
                device.wait()
                return

    for app in ("Phone", "Dialer"):
        try:
            device.open_app(app)
        except Exception:
            continue
        device.wait()
        create = _find_create_contact(device)
        if create is not None:
            device.click(index=create)
            device.wait()
            return
    raise RuntimeError("Could not find a way to create a new contact")


def _find_name_field(device, which):
    if which == "first":
        keys = ("First name", "First Name", "Given name", "Given Name")
    else:
        keys = ("Last name", "Last Name", "Family name", "Family Name", "Surname")
    idx = _find_exact(device, keys, editable=True)
    if idx is not None:
        return idx
    idx = _find_contains(device, keys, editable=True)
    return idx


def _find_add_phone(device):
    texts = ("Add phone number", "Add phone", "Add another phone", "Add new phone")
    for t in texts:
        idx = device.find(text=t, clickable=True)
        if idx is not None:
            return idx
    for t in texts:
        idx = device.find(description=t, clickable=True)
        if idx is not None:
            return idx
    for t in texts:
        idx = device.find(contains=t, clickable=True)
        if idx is not None:
            return idx
    return None


def _find_save(device):
    for text in ("Save", "Done", "Save contact", "OK"):
        idx = device.find(text=text, clickable=True)
        if idx is not None:
            return idx
    for text in ("Save", "Done", "Save contact", "OK"):
        idx = device.find(description=text, clickable=True)
        if idx is not None:
            return idx
    for text in ("Save", "Done"):
        idx = device.find(contains=text, clickable=True)
        if idx is not None:
            return idx
    return None


def _phone_score(element):
    text = _s(element.get("text"))
    hint = _s(element.get("hint"))
    desc = _s(element.get("description"))
    blob = (text + " " + hint + " " + desc).lower()
    phoney = any(k in blob for k in ("phone", "mobile", "telephone", "cell"))
    if text == "" and phoney:
        base = 0
    elif text == "" and hint == "" and desc == "":
        base = 1
    elif text == "":
        base = 2
    elif phoney:
        base = 3
    else:
        base = 4
    return base, phoney


def _phone_candidates(device, name_indices):
    out = []
    for e in _elements(device):
        if not e.get("editable"):
            continue
        idx = e.get("index")
        if idx is None or idx in name_indices:
            continue
        text = _s(e.get("text"))
        hint = _s(e.get("hint"))
        desc = _s(e.get("description"))
        blob = (text + " " + hint + " " + desc).lower()
        if any(k in blob for k in ("first name", "last name", "given name",
                                   "family name", "surname")):
            continue
        base, phoney = _phone_score(e)
        out.append((base, 0 if phoney else 1, idx, e))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return out


def _input_and_verify(device, idx, number):
    if idx is None:
        return False
    device.input_text(number, index=idx)
    device.wait()
    for e in _elements(device):
        if number and number in _s(e.get("text")):
            return True
    return False


def _fill_phone(device, number, name_indices):
    # A candidate that already looks like a (empty) phone field is the best bet.
    cands = _phone_candidates(device, name_indices)
    if cands and cands[0][0] == 0:
        if _input_and_verify(device, cands[0][2], number):
            return True

    # Maybe a phone row has to be added first.
    add_idx = _find_add_phone(device)
    if add_idx is not None:
        before = {e.get("index") for e in _elements(device) if e.get("editable")}
        device.click(index=add_idx)
        device.wait()
        new_ids = []
        for e in _elements(device):
            if not e.get("editable"):
                continue
            idx = e.get("index")
            if idx is None or idx in before or idx in name_indices:
                continue
            new_ids.append(idx)
        for idx in new_ids:
            if _input_and_verify(device, idx, number):
                return True
        cands = _phone_candidates(device, name_indices)
        for base, _flag, idx, _e in cands:
            if base <= 2 and _input_and_verify(device, idx, number):
                return True
        raise RuntimeError("Phone field not found after adding a phone row")

    # Last resort: pick among plain empty editable fields, preferring the
    # first one (phone rows sit above email/other rows).
    for base, _flag, idx, _e in cands:
        if base <= 2 and _input_and_verify(device, idx, number):
            return True

    raise RuntimeError("Phone number field not found")


def program(device, binding: dict) -> bool:
    name = _s(binding.get("name"))
    number = _s(binding.get("number"))
    if not name or not number:
        raise ValueError("binding must contain name and number")

    words = name.split()
    first_word = words[0]
    last_word = words[-1]

    _open_new_contact_editor(device)

    first_idx = _find_name_field(device, "first")
    last_idx = _find_name_field(device, "last")
    if first_idx is None and last_idx is None:
        raise RuntimeError("Could not locate name fields")

    if first_idx is not None:
        device.input_text(first_word, index=first_idx)
    if last_idx is not None and last_word:
        device.input_text(last_word, index=last_idx)

    name_indices = set()
    if first_idx is not None:
        name_indices.add(first_idx)
    if last_idx is not None:
        name_indices.add(last_idx)

    _fill_phone(device, number, name_indices)

    save_idx = _find_save(device)
    if save_idx is None:
        raise RuntimeError("Save button not found")
    device.click(index=save_idx)
    device.wait()

    return True
