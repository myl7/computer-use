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
        raise ValueError("Name must contain at least two words: %r" % (name,))
    first_name = parts[0]
    last_name = parts[1]

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #
    def txt(el, *keys):
        return " ".join(str(el.get(k) or "") for k in keys)

    def digits_of(s):
        return "".join(c for c in s if c.isdigit())

    def try_click(*args, **kwargs):
        idx = device.find(*args, **kwargs)
        if idx is not None:
            device.click(index=idx)
            device.settle(1.0)
            return True
        return False

    def editor_open():
        for probe in (
            {"hint": "First name", "editable": True},
            {"text": "First name", "editable": True},
            {"hint": "Last name", "editable": True},
            {"text": "Last name", "editable": True},
        ):
            if device.find(**probe) is not None:
                return True
        return False

    # ------------------------------------------------------------------ #
    # phone field discovery
    #
    # On some builds the phone row exposes its label dropdown with
    # hint/text/description == 'Label' and the actual number input carries
    # no phone hint at all, so a plain hint/text lookup for 'Phone'/'Mobile'
    # finds nothing.  In that case we walk to the editable element that
    # follows the label dropdown.
    # ------------------------------------------------------------------ #
    def find_phone_input():
        exact_patterns = [
            {"hint": "Phone", "editable": True},
            {"hint": "Mobile", "editable": True},
            {"hint": "Phone number", "editable": True},
            {"text": "Phone", "editable": True},
            {"text": "Mobile", "editable": True},
            {"text": "Phone number", "editable": True},
            {"description": "Phone", "editable": True},
            {"description": "Mobile", "editable": True},
            {"description": "Phone number", "editable": True},
            {"contains": "Phone", "editable": True},
            {"contains": "Mobile", "editable": True},
        ]
        for p in exact_patterns:
            idx = device.find(**p)
            if idx is not None:
                return idx

        elements = device.elements() or []

        # (a) locate the phone "Label" dropdown and take the next editable
        label_pos = None
        for i, el in enumerate(elements):
            vals = [str(el.get(k) or "").strip().lower()
                    for k in ("hint", "text", "description")]
            if "label" in vals:
                label_pos = i
                break
        if label_pos is None:
            for i, el in enumerate(elements):
                low = txt(el, "hint", "text", "description").lower()
                if "label" in low:
                    label_pos = i
                    break
        if label_pos is not None:
            for j in range(label_pos + 1, len(elements)):
                if elements[j].get("editable"):
                    return elements[j]["index"]
            for j in range(label_pos - 1, -1, -1):
                if elements[j].get("editable"):
                    return elements[j]["index"]

        # (b) editable that mentions phone/mobile but is not another field
        label_words = {"mobile", "home", "work", "phone", "other",
                       "custom", "label", "fax", "main"}
        for el in elements:
            if not el.get("editable"):
                continue
            low_hint = str(el.get("hint") or "").lower()
            low_text = str(el.get("text") or "").strip().lower()
            low_desc = str(el.get("description") or "").lower()
            low = low_hint + " " + low_text + " " + low_desc
            if any(t in low for t in
                   ("first name", "last name", "email", "address", "label")):
                continue
            # a bare label selector (e.g. text "Mobile") is not the number box
            if low_text in label_words and "phone" not in low_hint:
                continue
            if "phone" in low or "mobile" in low:
                return el["index"]

        return None

    def field_has_number(index, value):
        want = digits_of(value)
        if not want:
            return False
        for el in device.elements() or []:
            if el.get("index") == index:
                have = digits_of(str(el.get("text") or ""))
                return want in have or (len(want) >= 4 and want[-4:] in have)
        return False

    def number_on_screen(value):
        want = digits_of(value)
        if len(want) < 4:
            return False
        tail = want[-6:]
        for el in device.elements() or []:
            have = digits_of(str(el.get("text") or "") + " " +
                             str(el.get("description") or ""))
            if tail in have:
                return True
        return False

    def type_number(index):
        try:
            device.click(index=index)
            device.settle(0.4)
            device.input_text(number, index=index)
            device.settle(0.6)
        except Exception:
            return False
        return field_has_number(index, number) or number_on_screen(number)

    def enter_phone_number():
        for _ in range(8):
            idx = find_phone_input()
            if idx is not None and type_number(idx):
                return True
            # Reveal an "Add phone number" row if one is offered.
            clicked = False
            for kwargs in [
                {"text": "Add phone number"},
                {"contains": "Add phone number"},
                {"description": "Add phone number"},
                {"text": "Add phone number", "clickable": True},
                {"contains": "Add phone number", "clickable": True},
                {"description": "Add phone number", "clickable": True},
                {"text": "Add phone", "clickable": True},
            ]:
                if try_click(**kwargs):
                    clicked = True
                    device.settle(1.0)
                    break
            if not clicked:
                device.scroll("down")
                device.settle(0.5)
        return False

    # ------------------------------------------------------------------ #
    # 1. open the contact editor
    # ------------------------------------------------------------------ #
    opened = False
    try:
        device.open_app("Contacts")
        device.settle(2.0)
    except Exception:
        pass

    if editor_open():
        opened = True
    else:
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
                if editor_open():
                    opened = True
                    break

    if not opened:
        try:
            device.open_app("Phone")
            device.settle(2.0)
        except Exception:
            pass

        if editor_open():
            opened = True
        else:
            for kwargs in [
                {"text": "Create new contact", "clickable": True},
                {"contains": "Create new contact", "clickable": True},
                {"description": "Create new contact", "clickable": True},
            ]:
                if try_click(**kwargs):
                    if editor_open():
                        opened = True
                        break

        if not opened:
            for kwargs in [
                {"description": "Favorites", "clickable": True},
                {"text": "Favorites", "clickable": True},
                {"contains": "Favorites", "clickable": True},
            ]:
                if try_click(**kwargs):
                    device.settle(1.0)
                    break
            for kwargs in [
                {"text": "Add a favorite", "clickable": True},
                {"description": "Add a favorite", "clickable": True},
                {"contains": "Add a favorite", "clickable": True},
            ]:
                if try_click(**kwargs):
                    if editor_open():
                        opened = True
                        break

        if not opened:
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
                    if editor_open():
                        opened = True
                        break

    if not opened or not editor_open():
        raise RuntimeError("Could not open the contact editor (First name field not found)")

    # ------------------------------------------------------------------ #
    # 2. fill in the name fields
    # ------------------------------------------------------------------ #
    first_idx = device.find(hint="First name", editable=True)
    if first_idx is None:
        first_idx = device.find(text="First name", editable=True)
    if first_idx is None:
        raise RuntimeError("First name field not found")
    device.input_text(first_name, index=first_idx)
    device.settle(0.5)

    last_idx = device.find(hint="Last name", editable=True)
    if last_idx is None:
        last_idx = device.find(text="Last name", editable=True)
    if last_idx is None:
        raise RuntimeError("Last name field not found")
    device.input_text(last_name, index=last_idx)
    device.settle(0.5)

    # ------------------------------------------------------------------ #
    # 3. fill in the phone number
    # ------------------------------------------------------------------ #
    enter_phone_number()

    # ------------------------------------------------------------------ #
    # 4. save
    # ------------------------------------------------------------------ #
    def click_save():
        save_idx = device.find(text="Save", clickable=True)
        if save_idx is None:
            save_idx = device.find(description="Save", clickable=True)
        if save_idx is None:
            save_idx = device.find(contains="Save", clickable=True)
        if save_idx is None:
            for _ in range(6):
                device.scroll("up")
                device.settle(0.3)
                save_idx = device.find(text="Save", clickable=True)
                if save_idx is None:
                    save_idx = device.find(description="Save", clickable=True)
                if save_idx is not None:
                    break
        if save_idx is None:
            return False
        device.click(index=save_idx)
        device.settle(1.5)
        return True

    if not click_save():
        raise RuntimeError("Save button not found")

    # ------------------------------------------------------------------ #
    # 5. verify the number landed; if not, repair via the detail screen
    # ------------------------------------------------------------------ #
    device.settle(0.5)
    for _ in range(4):
        if number_on_screen(number):
            break
        reopened = False
        for kwargs in [
            {"description": "Edit contact", "clickable": True},
            {"text": "Edit contact", "clickable": True},
            {"text": "Add phone number"},
            {"contains": "Add phone number"},
            {"description": "Add phone number"},
        ]:
            if try_click(**kwargs):
                reopened = True
                break
        if not reopened:
            break
        device.settle(1.0)
        if not editor_open() and find_phone_input() is None:
            break
        enter_phone_number()
        if not click_save():
            break
        device.settle(1.0)

    if not number_on_screen(number):
        raise RuntimeError("Phone number was not saved on the contact")

    return True
