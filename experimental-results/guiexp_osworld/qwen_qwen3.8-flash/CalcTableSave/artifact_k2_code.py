PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "file_name": {
            "type": "string",
            "description": "Spreadsheet file name ending in .ods",
        },
        "header_a": {
            "type": "string",
            "description": "Text for cell A1",
        },
        "header_b": {
            "type": "string",
            "description": "Text for cell B1",
        },
        "val_a2": {
            "type": "integer",
            "description": "Integer for cell A2",
        },
        "val_b2": {
            "type": "integer",
            "description": "Integer for cell B2",
        },
        "val_a3": {
            "type": "integer",
            "description": "Integer for cell A3",
        },
        "val_b3": {
            "type": "integer",
            "description": "Integer for cell B3",
        },
    },
    "required": [
        "file_name",
        "header_a",
        "header_b",
        "val_a2",
        "val_b2",
        "val_a3",
        "val_b3",
    ],
    "additionalProperties": False,
}


def program(device, binding: dict) -> bool:
    required = (
        "file_name",
        "header_a",
        "header_b",
        "val_a2",
        "val_b2",
        "val_a3",
        "val_b3",
    )
    missing = [key for key in required if key not in binding]
    if missing:
        raise KeyError("Missing binding keys: " + ", ".join(missing))

    def to_text(value):
        return "" if value is None else str(value)

    def sanitize(value):
        return to_text(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")

    def numeric_text(value):
        s = to_text(value).strip()
        try:
            return str(int(s))
        except Exception:
            try:
                return str(int(float(s)))
            except Exception:
                return s

    header_a = sanitize(binding["header_a"])
    header_b = sanitize(binding["header_b"])
    val_a2 = numeric_text(binding["val_a2"])
    val_b2 = numeric_text(binding["val_b2"])
    val_a3 = numeric_text(binding["val_a3"])
    val_b3 = numeric_text(binding["val_b3"])

    raw_name = sanitize(binding["file_name"]).strip()
    if not raw_name:
        raise RuntimeError("file_name binding is empty")

    desktop = "/home/user/Desktop"
    if "/" in raw_name:
        path = raw_name
        file_name = path.rsplit("/", 1)[-1]
    else:
        file_name = raw_name
        path = desktop.rstrip("/") + "/" + file_name

    if not file_name.lower().endswith(".ods"):
        file_name += ".ods"
        path += ".ods"

    base_name = file_name[:-4] if file_name.lower().endswith(".ods") else file_name

    def active_lower():
        try:
            return str(device.active_window()).lower()
        except Exception:
            return ""

    def elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def safe_find(**criteria):
        try:
            return device.find(**criteria)
        except Exception:
            return None

    def find_cell(ref):
        ref = str(ref)

        idx = safe_find(name=ref, role="table-cell", editable=True)
        if idx is not None:
            return idx

        idx = safe_find(name=ref, role="table-cell")
        if idx is not None:
            return idx

        idx = safe_find(name=ref, role="table cell")
        if idx is not None:
            return idx

        for el in elements():
            if str(el.get("name", "")).strip() == ref:
                role = str(el.get("role", "")).lower()
                if "table-cell" in role or "table cell" in role or role in ("cell", "table cell"):
                    return el.get("index")

        for el in elements():
            if str(el.get("name", "")).strip() == ref:
                return el.get("index")

        return None

    def ensure_spreadsheet():
        device.open_app("LibreOffice Calc")
        device.wait()
        device.settle(1.0)

        if find_cell("A1") is not None and "untitled" not in active_lower():
            device.hotkey("ctrl", "n")
            device.wait()
            device.settle(1.0)

        for _ in range(4):
            if find_cell("A1") is not None:
                return

            idx = safe_find(role="push-button", contains="Spreadsheet")
            if idx is None:
                idx = safe_find(name="Spreadsheet")

            if idx is not None:
                device.click(index=idx)
                device.wait()
                device.settle(1.0)
                continue

            device.hotkey("ctrl", "n")
            device.wait()
            device.settle(1.0)

        if find_cell("A1") is None:
            device.hotkey("ctrl", "n")
            device.wait()
            device.settle(1.0)

        if find_cell("A1") is None:
            raise RuntimeError("Could not open a new LibreOffice Calc spreadsheet")

    def put_cell(ref, value):
        idx = find_cell(ref)
        if idx is None:
            raise RuntimeError(f"Cell {ref} not found")

        device.click(index=idx)
        device.settle(0.2)

        idx = find_cell(ref)
        if idx is None:
            raise RuntimeError(f"Cell {ref} not found after selecting")

        if value:
            device.input_text(value, index=idx)
            device.settle(0.2)
        else:
            device.press("delete")
            device.settle(0.2)

        device.press("enter")
        device.settle(0.2)

    ensure_spreadsheet()

    idx = find_cell("A1")
    if idx is not None:
        device.click(index=idx)
        device.settle(0.2)

    put_cell("A1", header_a)
    put_cell("B1", header_b)
    put_cell("A2", val_a2)
    put_cell("B2", val_b2)
    put_cell("A3", val_a3)
    put_cell("B3", val_b3)

    def save_dialog_open():
        w = active_lower()
        if "save" in w:
            return True

        if safe_find(name="Name:") is not None:
            return True
        if safe_find(contains="Name:") is not None:
            return True
        if safe_find(name="File name") is not None:
            return True
        if safe_find(description="File name") is not None:
            return True
        if safe_find(editable=True, contains="Untitled") is not None:
            return True

        return False

    def open_save_dialog():
        idx = find_cell("A1")
        if idx is not None:
            device.click(index=idx)
            device.settle(0.2)

        for keys in (("ctrl", "s"), ("ctrl", "shift", "s")):
            for _ in range(2):
                device.hotkey(*keys)
                device.wait()
                device.settle(0.8)
                if save_dialog_open():
                    return True

        idx = safe_find(role="menu", name="File")
        if idx is None:
            idx = safe_find(name="File")

        if idx is not None:
            device.click(index=idx)
            device.settle(0.5)

            save_as = safe_find(name="Save As")
            if save_as is None:
                save_as = safe_find(contains="Save As")
            if save_as is None:
                save_as = safe_find(name="Save")
            if save_as is None:
                save_as = safe_find(contains="Save")

            if save_as is not None:
                device.click(index=save_as)
                device.wait()
                device.settle(0.8)
                if save_dialog_open():
                    return True

        return False

    def find_name_field():
        criteria = (
            {"name": "Name", "editable": True},
            {"name": "Name:", "editable": True},
            {"name": "File name", "editable": True},
            {"name": "File name:", "editable": True},
            {"description": "Name", "editable": True},
            {"description": "File name", "editable": True},
            {"role": "text entry", "editable": True},
            {"role": "entry", "editable": True},
            {"role": "text", "editable": True},
            {"contains": "Untitled", "editable": True},
            {"contains": "File name", "editable": True},
        )

        for crit in criteria:
            idx = safe_find(**crit)
            if idx is not None:
                return idx

        best_idx = None
        best_score = 0

        for el in elements():
            if not el.get("editable"):
                continue

            role = str(el.get("role", "")).lower()
            name = str(el.get("name", "")).lower()
            text = str(el.get("text", "")).lower()
            desc = str(el.get("description", "")).lower()

            score = 0

            if name in ("name", "name:", "file name", "file name:"):
                score += 100
            elif "name" in name or "file name" in name:
                score += 60

            if "name" in desc or "file name" in desc:
                score += 30

            if "untitled" in text:
                score += 50

            if role in ("text entry", "entry", "text", "combo box"):
                score += 20
            elif "text" in role or "entry" in role:
                score += 10

            if "filter" in name or "file type" in name or "type" in name:
                score -= 50

            if score > best_score:
                best_score = score
                best_idx = el.get("index")

        return best_idx if best_score > 0 else None

    def field_contains(sub):
        if not sub:
            return True

        if safe_find(editable=True, contains=sub) is not None:
            return True

        needle = sub.lower()
        for el in elements():
            if el.get("editable") and needle in str(el.get("text", "")).lower():
                return True

        return False

    def set_filename_field():
        idx = find_name_field()
        if idx is None:
            raise RuntimeError("Could not find the Save dialog filename field")

        device.input_text(path, index=idx)
        device.settle(0.3)

        if not field_contains(path) and not field_contains(file_name):
            idx = find_name_field()
            if idx is not None:
                device.click(index=idx)
                device.settle(0.2)
                device.hotkey("ctrl", "a")
                device.settle(0.1)
                device.press("delete")
                device.settle(0.1)
                device.type_at_caret(path)
                device.settle(0.3)

        if not field_contains(path) and not field_contains(file_name):
            idx = find_name_field()
            if idx is not None:
                device.click(index=idx)
                device.settle(0.2)
                device.hotkey("ctrl", "a")
                device.settle(0.1)
                device.type_at_caret(path)
                device.settle(0.3)

    def confirm_save():
        btn = safe_find(role="push-button", name="Save")
        if btn is None:
            btn = safe_find(role="push-button", contains="Save")
        if btn is None:
            btn = safe_find(name="Save", role="push-button")

        if btn is not None:
            device.click(index=btn)
            device.wait()
            device.settle(0.8)
            return

        idx = find_name_field()
        if idx is not None:
            device.click(index=idx)
            device.settle(0.2)

        device.press("enter")
        device.wait()
        device.settle(0.8)

    def handle_overwrite():
        criteria = (
            {"role": "push-button", "name": "Yes"},
            {"role": "push-button", "contains": "Yes"},
            {"role": "push-button", "name": "Replace"},
            {"role": "push-button", "contains": "Replace"},
            {"role": "push-button", "name": "Overwrite"},
            {"role": "push-button", "contains": "Overwrite"},
        )

        for crit in criteria:
            idx = safe_find(**crit)
            if idx is not None:
                device.click(index=idx)
                device.wait()
                device.settle(0.8)
                return True

        w = active_lower()
        if "overwrite" in w or "already exists" in w or "replace" in w:
            device.press("enter")
            device.wait()
            device.settle(0.8)
            return True

        return False

    if not open_save_dialog():
        w = active_lower()
        if file_name.lower() in w or (base_name.lower() in w and "untitled" not in w):
            return True
        raise RuntimeError("Could not open the Save dialog")

    set_filename_field()
    confirm_save()

    for _ in range(8):
        if handle_overwrite():
            continue

        if not save_dialog_open():
            return True

        try:
            set_filename_field()
        except RuntimeError:
            pass

        confirm_save()

    if not save_dialog_open():
        return True

    raise RuntimeError("Save dialog did not close")
