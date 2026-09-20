import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods",
        "pattern": "\\.ods$",
    },
    "header_a": {
        "type": "string",
        "description": "Column A header text for cell A1",
    },
    "header_b": {
        "type": "string",
        "description": "Column B header text for cell B1",
    },
    "val_a2": {
        "type": "integer",
        "description": "Integer value for cell A2",
    },
    "val_b2": {
        "type": "integer",
        "description": "Integer value for cell B2",
    },
    "val_a3": {
        "type": "integer",
        "description": "Integer value for cell A3",
    },
    "val_b3": {
        "type": "integer",
        "description": "Integer value for cell B3",
    },
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

    if not isinstance(binding, dict):
        raise TypeError("binding must be a dict")

    for key in required:
        if key not in binding:
            raise KeyError(f"binding missing required key: {key}")

    def _text(value):
        if value is None:
            return ""
        return str(value)

    def _int_text(value):
        try:
            return str(int(value))
        except Exception:
            try:
                return str(int(float(value)))
            except Exception:
                return _text(value).strip()

    def _header_text(value):
        s = _text(value)
        s = s.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        if s == "":
            return s
        # Force literal text in the cell rather than a formula/number.
        return "'" + s

    def _title():
        try:
            return _text(device.active_window())
        except Exception:
            return ""

    def _has_calc():
        return "libreoffice calc" in _title().lower()

    def _find_cell(cell_name):
        attempts = (
            {"name": cell_name, "role": "table-cell", "editable": True},
            {"name": cell_name, "role": "table-cell"},
            {"name": cell_name, "role": "table cell"},
            {"name": cell_name, "role": "cell"},
        )

        for crit in attempts:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        try:
            elems = device.elements()
        except Exception:
            elems = []

        for el in elems:
            role = _text(el.get("role")).lower()
            if role in ("table-cell", "table cell", "cell"):
                if el.get("name") == cell_name or el.get("text") == cell_name:
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        for el in elems:
            role = _text(el.get("role")).lower()
            if role in ("table-cell", "table cell", "cell"):
                if _text(el.get("name")).strip() == cell_name or _text(el.get("text")).strip() == cell_name:
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        return None

    def _wait_for_cell(cell_name, attempts=5):
        for _ in range(attempts):
            idx = _find_cell(cell_name)
            if idx is not None:
                return idx
            device.settle(0.5)
        return None

    def _ensure_new_spreadsheet():
        for _ in range(2):
            try:
                device.open_app("LibreOffice Calc")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)
            if _has_calc():
                break

        if not _has_calc():
            idx = None
            for crit in (
                {"name": "Calc", "role": "push-button"},
                {"contains": "Calc", "role": "push-button"},
                {"name": "Calc"},
                {"contains": "Calc"},
            ):
                try:
                    idx = device.find(**crit)
                except Exception:
                    idx = None
                if idx is not None:
                    break

            if idx is not None:
                try:
                    device.click(index=idx)
                except Exception:
                    pass
                device.wait()
                device.settle(1.0)

        if not _has_calc():
            try:
                device.hotkey("ctrl", "n")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)

        if _has_calc():
            try:
                device.hotkey("ctrl", "n")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)

        if _wait_for_cell("A1", 3) is None:
            try:
                device.open_app("LibreOffice Calc")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)
            try:
                device.hotkey("ctrl", "n")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)

        if _wait_for_cell("A1", 3) is None:
            try:
                device.press("escape")
            except Exception:
                pass
            device.settle(0.3)
            try:
                device.hotkey("ctrl", "n")
            except Exception:
                pass
            device.wait()
            device.settle(1.0)

    def _set_cell(cell_name, text):
        if _wait_for_cell(cell_name, 5) is None:
            raise RuntimeError(f"Cell {cell_name} not found")

        for _ in range(3):
            idx = _find_cell(cell_name)
            if idx is None:
                device.settle(0.5)
                continue

            try:
                device.click(index=idx)
                device.settle(0.2)
            except Exception:
                pass

            idx2 = _find_cell(cell_name)
            if idx2 is None:
                idx2 = idx

            try:
                device.input_text(text, index=idx2)
                device.settle(0.2)
                try:
                    device.press("enter")
                    device.settle(0.2)
                except Exception:
                    pass
                return
            except Exception:
                try:
                    device.click(index=idx2)
                    device.settle(0.2)
                    device.type_at_caret(text)
                    device.press("enter")
                    device.settle(0.2)
                    return
                except Exception:
                    device.settle(0.5)

        raise RuntimeError(f"Could not set cell {cell_name}")

    def _save_dialog_open():
        t = _title().lower()
        if "save" in t or "overwrite" in t or "replace" in t:
            return True

        checks = (
            {"role": "label", "contains": "Name:"},
            {"role": "label", "contains": "File name"},
            {"name": "Name", "editable": True},
            {"name": "File name", "editable": True},
        )

        for crit in checks:
            try:
                if device.find(**crit) is not None:
                    return True
            except Exception:
                pass

        try:
            yes = device.find(role="push-button", name="Yes")
            no = device.find(role="push-button", name="No")
            if yes is not None and no is not None:
                return True
        except Exception:
            pass

        return False

    def _wait_save_dialog(attempts=6):
        for _ in range(attempts):
            if _save_dialog_open():
                return True
            device.settle(0.5)
        return False

    def _open_save_dialog():
        if _save_dialog_open():
            return True

        for _ in range(3):
            try:
                device.hotkey("ctrl", "s")
            except Exception:
                pass

            if _wait_save_dialog():
                return True

            idx = _find_cell("A1")
            if idx is not None:
                try:
                    device.click(index=idx)
                    device.settle(0.3)
                except Exception:
                    pass

        return False

    def _find_name_field():
        crits = (
            {"name": "Name", "editable": True},
            {"name": "File name", "editable": True},
            {"name": "Name:", "editable": True},
            {"description": "Name", "editable": True},
            {"contains": "File name", "editable": True},
            {"contains": "Name", "editable": True},
            {"contains": "Untitled", "editable": True},
        )

        for crit in crits:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        try:
            elems = device.elements()
        except Exception:
            elems = []

        candidates = []
        for el in elems:
            if not el.get("editable"):
                continue

            idx = el.get("index")
            if idx is None:
                continue

            blob = " ".join(
                _text(el.get(k)) for k in ("name", "description", "text", "role")
            ).lower()

            if "path" in blob:
                continue

            if "name" in blob or "untitled" in blob:
                return idx

            candidates.append(idx)

        return candidates[0] if candidates else None

    def _find_save_button():
        crits = (
            {"role": "push-button", "name": "Save"},
            {"role": "push-button", "contains": "Save"},
            {"name": "Save", "role": "push-button"},
        )

        for crit in crits:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        try:
            for el in device.elements():
                role = _text(el.get("role")).lower()
                name = _text(el.get("name")).lower()
                if "button" in role and name == "save":
                    idx = el.get("index")
                    if idx is not None:
                        return idx
        except Exception:
            pass

        return None

    def _find_yes_button():
        crits = (
            {"role": "push-button", "name": "Yes"},
            {"role": "push-button", "contains": "Yes"},
            {"role": "push-button", "name": "Replace"},
            {"role": "push-button", "contains": "Replace"},
            {"role": "push-button", "name": "Overwrite"},
            {"role": "push-button", "contains": "Overwrite"},
        )

        for crit in crits:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        try:
            for el in device.elements():
                role = _text(el.get("role")).lower()
                name = _text(el.get("name")).lower()
                if "button" in role and name in ("yes", "replace", "overwrite"):
                    idx = el.get("index")
                    if idx is not None:
                        return idx
        except Exception:
            pass

        return None

    def _confirm_save():
        if not _save_dialog_open():
            try:
                device.press("enter")
                device.wait()
                device.settle(1.0)
            except Exception:
                pass
            return

        name_idx = _find_name_field()

        if name_idx is None:
            yes = _find_yes_button()
            if yes is not None:
                try:
                    device.click(index=yes)
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass
                return

        if name_idx is not None:
            try:
                device.click(index=name_idx)
                device.settle(0.2)
            except Exception:
                pass

            try:
                device.press("enter")
                device.wait()
                device.settle(1.0)
            except Exception:
                pass
        else:
            btn = _find_save_button()
            if btn is not None:
                try:
                    device.click(index=btn)
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass
            else:
                try:
                    device.press("enter")
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass

        if _save_dialog_open():
            yes = _find_yes_button()
            if yes is not None:
                try:
                    device.click(index=yes)
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass
                return

            name_idx2 = _find_name_field()
            if name_idx2 is not None:
                try:
                    device.click(index=name_idx2)
                    device.settle(0.2)
                except Exception:
                    pass
                try:
                    device.press("enter")
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass

            btn = _find_save_button()
            if btn is not None:
                try:
                    device.click(index=btn)
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass

        if _save_dialog_open():
            yes = _find_yes_button()
            if yes is not None:
                try:
                    device.click(index=yes)
                    device.wait()
                    device.settle(1.0)
                except Exception:
                    pass
                return

            name_idx3 = _find_name_field()
            if name_idx3 is not None:
                try:
                    device.click(index=name_idx3)
                    device.settle(0.2)
                except Exception:
                    pass

            try:
                device.press("enter")
                device.wait()
                device.settle(1.0)
            except Exception:
                pass

    def _desktop_path():
        candidates = []

        try:
            candidates.append(os.path.expanduser("~/Desktop"))
        except Exception:
            pass

        candidates.append("/home/user/Desktop")

        for c in candidates:
            if c and not c.startswith("~"):
                try:
                    if os.path.isdir(c):
                        return c
                except Exception:
                    pass

        for c in candidates:
            if c and not c.startswith("~"):
                return c

        return "/home/user/Desktop"

    _ensure_new_spreadsheet()

    if _wait_for_cell("A1", 5) is None:
        raise RuntimeError("Could not open a new LibreOffice Calc spreadsheet")

    _set_cell("A1", _header_text(binding["header_a"]))
    _set_cell("B1", _header_text(binding["header_b"]))
    _set_cell("A2", _int_text(binding["val_a2"]))
    _set_cell("B2", _int_text(binding["val_b2"]))
    _set_cell("A3", _int_text(binding["val_a3"]))
    _set_cell("B3", _int_text(binding["val_b3"]))

    try:
        device.press("enter")
        device.settle(0.2)
    except Exception:
        pass

    if not _open_save_dialog():
        raise RuntimeError("Could not open the Save dialog")

    fname = _text(binding["file_name"]).strip()
    if not fname:
        raise RuntimeError("file_name is empty")

    fname = os.path.basename(fname)
    if not fname.lower().endswith(".ods"):
        fname += ".ods"

    path = _desktop_path().rstrip("/") + "/" + fname

    idx = None
    for _ in range(6):
        idx = _find_name_field()
        if idx is not None:
            break
        device.settle(0.5)

    if idx is None:
        raise RuntimeError("Could not find the filename field in the Save dialog")

    try:
        device.input_text(path, index=idx)
        device.settle(0.2)
    except Exception:
        try:
            device.click(index=idx)
            device.settle(0.2)
            device.hotkey("ctrl", "a")
            device.press("delete")
            device.type_at_caret(path)
            device.settle(0.2)
        except Exception:
            raise RuntimeError("Could not enter the save path")

    if _save_dialog_open():
        try:
            check = device.find(editable=True, contains=fname)
        except Exception:
            check = None

        if check is None:
            idx2 = _find_name_field()
            if idx2 is None:
                idx2 = idx

            try:
                device.click(index=idx2)
                device.settle(0.2)
                device.hotkey("ctrl", "a")
                device.press("delete")
                device.type_at_caret(path)
                device.settle(0.2)
            except Exception:
                try:
                    device.input_text(path, index=idx2)
                    device.settle(0.2)
                except Exception:
                    pass

    _confirm_save()

    if _save_dialog_open():
        _confirm_save()

    fname_lower = fname.lower()
    base_lower = fname_lower.rsplit(".", 1)[0] if "." in fname_lower else fname_lower

    for _ in range(12):
        if _save_dialog_open():
            _confirm_save()
            device.settle(0.5)
            continue

        t = _title().lower()
        if fname_lower in t or (base_lower in t and ".ods" in t):
            return True

        device.settle(0.5)

    raise RuntimeError("The spreadsheet was not saved as expected")
