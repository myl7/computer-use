PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
    },
    "number": {
        "type": "string",
        "description": "Phone number as a single string, copied verbatim",
    },
}


def _split_name(name):
    """Split a two-word full name into (first, last)."""
    words = (name or "").strip().split()
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], ""
    return words[0], " ".join(words[1:])


def _blob(elem):
    """Lower-cased concatenation of an element's text/hint/description."""
    parts = []
    for key in ("text", "hint", "description", "content_desc"):
        value = elem.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _same_number(a, b):
    """Lenient phone comparison that tolerates display formatting."""
    da, db = _digits(a), _digits(b)
    if not da or not db:
        return False
    if da == db:
        return True
    tail = min(len(da), len(db), 6)
    return tail >= 4 and da[-tail:] == db[-tail:]


# Editor text fields that are NOT the phone field.  NB: "Phonetic name"
# contains the substring "Phone", which is what lured the number into a
# wrong (lost) field in an earlier failed run.
_NON_PHONE_WORDS = (
    "phonetic", "email", "e-mail", "web", "website", "address",
    "organization", "organisation", "company", "note", "birthday",
    "relationship",
)


def _mentions_phone(blob):
    return "phone" in blob and not any(word in blob for word in _NON_PHONE_WORDS)


def _number_in_editable(elems, number):
    """True if the number is currently held by an editable field that is
    not a name/phonetic/email-like field (or shown by a phone-labelled
    read-only element)."""
    target = str(number or "").strip()
    if not target:
        return True
    for elem in elems:
        txt = str(elem.get("text") or "")
        if not txt or not (target in txt or _same_number(txt, target)):
            continue
        blob = _blob(elem)
        if elem.get("editable"):
            if not any(w in blob for w in ("first name", "last name",
                                           "phonetic", "email", "address")):
                return True
        elif _mentions_phone(blob):
            return True
    return False


def _phone_by_position(elems):
    """Compact-editor field order is first name, last name, phone, ...,
    so the phone edit field is the first usable editable after the
    last-name field."""
    anchor_pos = None
    for pos, elem in enumerate(elems):
        if elem.get("editable") and "last name" in _blob(elem):
            anchor_pos = pos
            break
    if anchor_pos is None:
        editables = [e for e in elems if e.get("editable")]
        if len(editables) < 2:
            return None
        try:
            anchor_pos = elems.index(editables[1])
        except ValueError:
            return None
    for elem in elems[anchor_pos + 1:]:
        if not elem.get("editable"):
            continue
        if not any(w in _blob(elem) for w in _NON_PHONE_WORDS):
            return elem["index"]
    return None


def _locate_phone_field(device, max_scrolls=2):
    """Locate the phone-number edit field inside the contact editor.

    Every lookup runs on a fresh full element scan (no find() chains that
    can pile up NOT-FOUND results).  "Phonetic name"-like fields are
    explicitly excluded.
    """
    for round_no in range(max_scrolls + 1):
        elems = device.elements()
        # 1) An editable field whose text/hint mentions a phone number.
        for elem in elems:
            if elem.get("editable") and _mentions_phone(_blob(elem)):
                return elem["index"]
        # 2) Positional fallback: first editable after the last-name field.
        idx = _phone_by_position(elems)
        if idx is not None:
            return idx
        # 3) A tappable phone row: tap it to focus its edit field.
        for elem in elems:
            if elem.get("clickable") and _mentions_phone(_blob(elem)):
                device.click(elem["index"])
                device.settle(1)
                for elem2 in device.elements():
                    if (elem2.get("editable")
                            and _mentions_phone(_blob(elem2))):
                        return elem2["index"]
                break
        if round_no < max_scrolls:
            device.scroll("down")
    return None


def _type_number(device, number):
    """Type the number into the editor's phone field (best effort).

    Returns True if the number is held by a phone field afterwards.
    """
    field = _locate_phone_field(device)
    if field is None:
        return False
    if _number_in_editable(device.elements(), number):
        return True  # already there (e.g. re-entry after saving)
    device.input_text(number, index=field)
    if _number_in_editable(device.elements(), number):
        return True
    # The text did not stick: focus the field with a tap and retry once.
    try:
        device.click(field)
    except Exception:
        pass
    device.settle(1)
    field = _locate_phone_field(device) or field
    device.input_text(number, index=field)
    return _number_in_editable(device.elements(), number)


def _save_index_in(elems):
    """Index of the editor's save control in a scanned element list.

    The editor's save control is a checkmark carrying the (content
    description) 'Save' -- occasionally a plain 'Save' button.  Elements
    merely REPORTING a save ('Contact saved' snackbar) are ignored.
    """
    exact_clickable = None
    exact = None
    loose = None
    for elem in elems:
        blob = _blob(elem).strip()
        if not blob or "saved" in blob or "save" not in blob:
            continue
        if blob == "save":
            if elem.get("clickable") and exact_clickable is None:
                exact_clickable = elem["index"]
            if exact is None:
                exact = elem["index"]
        elif elem.get("clickable") and loose is None:
            loose = elem["index"]
    if exact_clickable is not None:
        return exact_clickable
    if exact is not None:
        return exact
    return loose


def _editor_still_open(elems):
    """Heuristic: the contact editor is open iff a save control is visible
    or at least two editable fields are on screen (the saved-contact detail
    and the contacts list have none)."""
    editables = 0
    for elem in elems:
        if _blob(elem).strip() == "save":
            return True
        if elem.get("editable"):
            editables += 1
    return editables >= 2


def _save_contact(device, max_attempts=3):
    """Click the editor's save control until the editor closes.

    Repaired: all state checks run on full element scans.  The failed run
    assumed the save had not happened and burned the rest of the run
    hammering find('Save') on the saved contact's detail screen -- where no
    Save button exists (the editor's save control vanishes once saved).
    That NOT-FOUND loop is exactly what this avoids.
    """
    for _ in range(max_attempts):
        elems = device.elements()
        save_idx = _save_index_in(elems)
        if save_idx is None:
            if not _editor_still_open(elems):
                return True  # editor already closed -> saved
            device.scroll("down")
            device.settle(1)
            continue
        device.click(save_idx)
        device.settle(2)
    elems = device.elements()
    return _save_index_in(elems) is None and not _editor_still_open(elems)


def _add_phone_index_in(elems):
    for elem in elems:
        low = (str(elem.get("text") or "") + " "
               + str(elem.get("description") or "")).lower()
        if "add phone" in low:
            return elem["index"]
    return None


def _open_contact_detail(device, name):
    """Open the contact's detail screen by tapping its name (works from the
    contacts list; harmless on the detail screen itself)."""
    full = str(name or "").strip()
    if not full:
        return False
    elems = device.elements()
    full_low = full.lower()
    for elem in elems:
        txt = str(elem.get("text") or "").strip()
        if txt and txt.lower() == full_low:
            device.click(elem["index"])
            device.settle(2)
            return True
    for word in [w for w in full.split() if w]:
        w_low = word.lower()
        for elem in elems:
            txt = str(elem.get("text") or "").strip().lower()
            if txt and w_low in txt:
                device.click(elem["index"])
                device.settle(2)
                return True
    return False


def _phone_stored_on_screen(device, number):
    """True if the number shows up on the current screen as a stored phone
    number: visible as read-only text, above the 'Add phone number'
    placeholder, or accompanied by Call/Text/Video actions."""
    elems = device.elements()
    target = str(number or "").strip()
    if not target:
        return False
    num_pos = None
    add_phone_pos = None
    call_seen = False
    for pos, elem in enumerate(elems):
        txt = str(elem.get("text") or "")
        desc = str(elem.get("description") or "")
        if add_phone_pos is None and "add phone number" in txt.lower():
            add_phone_pos = pos
        if (txt.strip().lower() in ("call", "text", "video", "make video call")
                or desc.strip().lower() in ("call", "text", "make video call",
                                            "video call")):
            call_seen = True
        if (num_pos is None and txt and not elem.get("editable")
                and (target in txt or _same_number(txt, target))):
            num_pos = pos
    if num_pos is None:
        return False
    if add_phone_pos is None or call_seen:
        return True
    return num_pos < add_phone_pos


def _add_phone_from_detail(device, number, name):
    """Store the number on the saved contact via its 'Add phone number'
    entry on the contact detail screen."""
    entry = _add_phone_index_in(device.elements())
    if entry is None:
        device.scroll("down")
        entry = _add_phone_index_in(device.elements())
    if entry is None:
        # Maybe we are on the contacts list instead of the detail screen:
        # open the contact by tapping its name.
        if _open_contact_detail(device, name):
            entry = _add_phone_index_in(device.elements())
    if entry is None:
        device.scroll("down")
        entry = _add_phone_index_in(device.elements())
    if entry is None:
        raise RuntimeError("'Add phone number' entry not found on the contact screen")
    device.click(entry)
    device.settle(2)
    if not _editor_still_open(device.elements()):
        # The tap may have missed; retry once (the tappable-row fallback in
        # the locator also re-opens the phone entry if needed).
        entry = _add_phone_index_in(device.elements())
        if entry is not None:
            device.click(entry)
            device.settle(2)
    if not _type_number(device, number):
        device.scroll("down")
        if not _type_number(device, number):
            raise RuntimeError("Could not type the phone number into the editor")
    if not _save_contact(device):
        raise RuntimeError("Saving the phone number did not complete; editor still open")


def program(device, binding: dict) -> bool:
    name = str(binding["name"])
    number = str(binding["number"])
    first, last = _split_name(name)

    # ---- Step 1: open the Contacts app -------------------------------------
    device.open_app("Contacts")
    device.settle(2)
    if (device.find(description="Create contact") is None
            and device.find(text="Create contact") is None
            and device.find(description="Contacts") is None):
        # Retry once in case the launch was slow.
        device.open_app("Contacts")
        device.settle(2)
        if (device.find(description="Create contact") is None
                and device.find(text="Create contact") is None
                and device.find(description="Contacts") is None):
            raise RuntimeError("Contacts app did not open (PeopleActivity not detected)")

    # ---- Step 2: tap the "Create contact" button ---------------------------
    create = device.find(description="Create contact", clickable=True)
    if create is None:
        create = device.find(text="Create contact", clickable=True)
    if create is None:
        create = device.find(contains="Create contact", clickable=True)
    if create is None:
        # Fallback on a full element scan.
        for elem in device.elements():
            blob = _blob(elem)
            if elem.get("clickable") and ("create contact" in blob
                                          or "new contact" in blob):
                create = elem["index"]
                break
    if create is None:
        raise LookupError("'Create contact' button not found on the Contacts screen")
    device.click(create)
    device.settle(2)

    # ---- Step 3: type the first name ---------------------------------------
    first_field = device.find(hint="First name", editable=True)
    if first_field is None:
        first_field = device.find(text="First name", editable=True)
    if first_field is None:
        first_field = device.find(contains="First", editable=True)
    if first_field is None:
        # Fallback: the first editable field on the editor form.
        first_field = device.find(editable=True)
    if first_field is None:
        raise RuntimeError("First name field not found on contact editor screen")
    device.input_text(first, index=first_field)

    # ---- Step 4: type the last name ----------------------------------------
    last_field = device.find(hint="Last name", editable=True)
    if last_field is None:
        last_field = device.find(text="Last name", editable=True)
    if last_field is None:
        last_field = device.find(contains="Last", editable=True)
    if last_field is None:
        # Fallback: the second editable field on the editor form.
        editables = [e for e in device.elements() if e.get("editable")]
        if len(editables) >= 2:
            last_field = editables[1]["index"]
    if last_field is None:
        raise LookupError("Last name field not found on contact editor screen")
    device.input_text(last, index=last_field)

    # ---- Step 5: type the phone number -------------------------------------
    # The phone field is located robustly (hint / full element scan /
    # position after the last-name field / tap-to-focus, with scrolling);
    # "Phonetic name"-like fields are explicitly excluded so the number can
    # never be typed into a wrong field.  If it cannot be filled, the
    # contact is still saved and Step 7 stores the number through the
    # detail screen's 'Add phone number' entry.
    _type_number(device, number)

    # ---- Step 6: save the contact ------------------------------------------
    # Repaired: the save control is located and re-checked with full element
    # scans only.  The failed run wrongly assumed the save had not happened
    # (the click on the editor's save checkmark DID save and landed on the
    # detail screen) and burned the run in a find('Save') NOT-FOUND loop on
    # a screen where no Save button exists.
    if not _save_contact(device):
        raise RuntimeError("Saving the contact did not complete; editor still open")

    # ---- Step 7: verify the number was stored as a phone number ------------
    device.settle(1)
    if _phone_stored_on_screen(device, number):
        return True
    # The number is missing (or was not stored as a phone): make sure the
    # editor is really closed, open the saved contact's detail screen and
    # store the number through its 'Add phone number' entry.
    if _editor_still_open(device.elements()):
        if not _save_contact(device):
            raise RuntimeError("Contact editor did not close after saving")
        device.settle(1)
        if _phone_stored_on_screen(device, number):
            return True
    _open_contact_detail(device, name)
    if _phone_stored_on_screen(device, number):
        return True
    _add_phone_from_detail(device, number, name)
    if not _phone_stored_on_screen(device, number):
        raise RuntimeError("Phone number was not stored on the contact")
    return True
