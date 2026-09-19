PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name with exactly two whitespace-separated words.",
        "required": True,
    },
    "number": {
        "type": "string",
        "description": "Phone number to enter verbatim.",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    name = str(binding["name"])
    number = str(binding["number"])

    parts = name.split()
    if len(parts) < 2:
        raise ValueError("binding['name'] must contain a first and last name")

    first_name = parts[0]
    last_name = parts[1]
    app_name = str(binding.get("app_name") or "Contacts")

    def norm(value):
        return str(value or "").strip().lower()

    def scroll(direction):
        try:
            device.scroll(direction=direction)
        except Exception:
            pass

    def go_back():
        try:
            device.navigate_back()
        except Exception:
            pass

    def find_any(*criteria):
        for crit in criteria:
            if not crit:
                continue
            idx = device.find(**crit)
            if idx is not None:
                return idx
        return None

    def find_contains(needles, editable=False, clickable=False):
        for el in device.elements():
            if editable and not el.get("editable"):
                continue
            if clickable and not el.get("clickable"):
                continue

            haystacks = [
                norm(el.get("text")),
                norm(el.get("hint")),
                norm(el.get("description")),
            ]

            for needle in needles:
                n = norm(needle)
                if not n:
                    continue
                for hay in haystacks:
                    if n in hay:
                        return el.get("index")
        return None

    def find_field(labels):
        criteria = []
        for label in labels:
            criteria.extend([
                {"hint": label, "editable": True},
                {"text": label, "editable": True},
                {"contains": label, "editable": True},
                {"description": label, "editable": True},
            ])

        idx = find_any(*criteria)
        if idx is not None:
            return idx
        return find_contains(labels, editable=True)

    def input_field(labels, value):
        for _ in range(3):
            idx = find_field(labels)
            if idx is not None:
                device.input_text(value, index=idx)
                return True

            scroll("down")
            idx = find_field(labels)
            if idx is not None:
                device.input_text(value, index=idx)
                return True

            scroll("up")
            idx = find_field(labels)
            if idx is not None:
                device.input_text(value, index=idx)
                return True

            device.settle(0.5)

        return False

    def click_save():
        criteria = [
            {"text": "Save", "clickable": True},
            {"description": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
        ]

        idx = find_any(*criteria)
        if idx is None:
            idx = find_contains(["Save"], clickable=True)

        if idx is None:
            scroll("up")
            idx = find_any(*criteria)

        if idx is None:
            idx = find_contains(["Save"], clickable=True)

        if idx is None:
            return False

        device.click(index=idx)
        return True

    def click_create():
        labels = ["Create contact", "Add contact", "Create new contact", "New contact"]
        criteria = []
        for label in labels:
            criteria.extend([
                {"description": label, "clickable": True},
                {"text": label, "clickable": True},
                {"contains": label, "clickable": True},
            ])

        idx = find_any(*criteria)
        if idx is None:
            idx = find_contains(labels, clickable=True)

        if idx is None:
            scroll("down")
            idx = find_any(*criteria)

        if idx is None:
            idx = find_contains(labels, clickable=True)

        if idx is None:
            scroll("up")
            idx = find_any(*criteria)

        if idx is None:
            idx = find_contains(labels, clickable=True)

        if idx is None:
            return False

        device.click(index=idx)
        return True

    device.open_app(app_name)
    device.settle(1.0)

    if not click_create():
        for _ in range(2):
            idx = find_any(
                {"text": "Allow", "clickable": True},
                {"text": "Allow all", "clickable": True},
                {"text": "Allow all the time", "clickable": True},
                {"text": "Continue", "clickable": True},
                {"text": "Next", "clickable": True},
                {"text": "Skip", "clickable": True},
                {"text": "Got it", "clickable": True},
            )
            if idx is None:
                break
            device.click(index=idx)
            device.settle(1.0)

        if not click_create():
            if find_field(["First name", "Given name"]) is None:
                raise RuntimeError("Could not find the Create contact button")

    if not input_field(["First name", "Given name"], first_name):
        raise RuntimeError("Could not find or fill the First name field")

    if not input_field(["Last name", "Family name"], last_name):
        raise RuntimeError("Could not find or fill the Last name field")

    if not input_field(["Phone", "Phone number", "Mobile", "Cell"], number):
        raise RuntimeError("Could not find or fill the Phone field")

    if not click_save():
        go_back()
        device.settle(0.5)
        if not click_save():
            raise RuntimeError("Could not find the Save button")

    device.settle(1.0)
    return True
