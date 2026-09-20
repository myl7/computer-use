import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods",
        "required": True,
    },
    "header_a": {
        "type": "string",
        "description": "Column A header text for cell A1",
        "required": True,
    },
    "header_b": {
        "type": "string",
        "description": "Column B header text for cell B1",
        "required": True,
    },
    "val_a2": {
        "type": "integer",
        "description": "Integer value for cell A2",
        "required": True,
    },
    "val_b2": {
        "type": "integer",
        "description": "Integer value for cell B2",
        "required": True,
    },
    "val_a3": {
        "type": "integer",
        "description": "Integer value for cell A3",
        "required": True,
    },
    "val_b3": {
        "type": "integer",
        "description": "Integer value for cell B3",
        "required": True,
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
    for key in required:
        if key not in binding:
            raise KeyError(f"Missing required binding key: {key}")

    file_name = str(binding["file_name"])
    if not file_name.lower().endswith(".ods"):
        file_name += ".ods"

    header_a = str(binding["header_a"])
    header_b = str(binding["header_b"])

    def _as_int_str(value):
        try:
            return str(int(value))
        except (ValueError, TypeError):
            return str(value)

    val_a2 = _as_int_str(binding["val_a2"])
    val_b2 = _as_int_str(binding["val_b2"])
    val_a3 = _as_int_str(binding["val_a3"])
    val_b3 = _as_int_str(binding["val_b3"])

    def _title():
        try:
            return device.active_window() or ""
        except Exception:
            return ""

    def _is_calc():
        t = _title()
        if "Start Center" in t:
            return False
        if "LibreOffice Calc" in t or "Calc" in t:
            return True
        try:
            if device.find(name="A1", role="table-cell") is not None:
                return True
        except Exception:
            pass
        return False

    def _is_save_dialog():
        return "Save" in _title()

    def _find_cell(cell_name):
        criteria = [
            {"name": cell_name, "role": "table-cell", "editable": True},
            {"name": cell_name, "role": "table-cell", "clickable": True},
            {"name": cell_name, "role": "table-cell"},
            {"name": cell_name, "editable": True},
        ]
        for crit in criteria:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx

        name_box_criteria = [
            {"name": "Name Box", "editable": True},
            {"contains": "Name Box", "editable": True},
            {"description": "Name Box", "editable": True},
            {"name": "Name", "editable": True},
        ]
        for crit in name_box_criteria:
            try:
                nb = device.find(**crit)
            except Exception:
                nb = None
            if nb is not None:
                try:
                    device.input_text(cell_name, index=nb)
                    device.press("enter")
                    device.settle(0.2)
                except Exception:
                    pass
                try:
                    idx = device.find(name=cell_name, role="table-cell")
                    if idx is None:
                        idx = device.find(name=cell_name)
                    if idx is not None:
                        return idx
                except Exception:
                    pass
        return None

    def _fill_cell(cell_name, text):
        idx = _find_cell(cell_name)
        if idx is None:
            raise RuntimeError(f"Could not find Calc cell {cell_name}")

        try:
            device.click(index=idx)
            device.settle(0.1)
        except Exception:
            pass

        idx = _find_cell(cell_name) or idx
        text = str(text)

        if text == "":
            try:
                device.press("delete")
                device.press("enter")
            except Exception:
                try:
                    device.input_text("", index=idx)
                    device.press("enter")
                except Exception:
                    pass
        else:
            try:
                device.input_text(text, index=idx)
            except Exception:
                try:
                    device.click(index=idx)
                    device.settle(0.1)
                    device.type_at_caret(text)
                except Exception:
                    pass
            try:
                device.press("enter")
            except Exception:
                pass

        device.settle(0.1)

    def _ensure_new_calc():
        try:
            device.open_app("LibreOffice Calc")
        except Exception:
            pass
        device.settle(1)

        for _ in range(6):
            if _is_calc():
                break

            if "Start Center" in _title():
                start_idx = None
                for crit in [
                    {"name": "Spreadsheet", "role": "button"},
                    {"contains": "Spreadsheet", "role": "button"},
                    {"name": "Calc", "role": "button"},
                    {"contains": "Calc", "role": "button"},
                    {"name": "Spreadsheet"},
                    {"contains": "Spreadsheet"},
                ]:
                    try:
                        start_idx = device.find(**crit)
                    except Exception:
                        start_idx = None
                    if start_idx is not None:
                        break

                if start_idx is not None:
                    try:
                        device.click(index=start_idx)
                        device.settle(1)
                    except Exception:
                        pass
                    if _is_calc():
                        break

            device.settle(0.5)

        if not _is_calc():
            try:
                device.open_app("LibreOffice Calc")
                device.settle(1)
            except Exception:
                pass
            for _ in range(4):
                if _is_calc():
                    break
                device.settle(0.5)

        if not _is_calc():
            try:
                device.hotkey("ctrl", "n")
                device.settle(1)
            except Exception:
                pass

        if not _is_calc():
            raise RuntimeError("Could not open LibreOffice Calc")

        if "Untitled" not in _title():
            try:
                device.hotkey("ctrl", "n")
                device.settle(1)
            except Exception:
                pass
            if not _is_calc():
                raise RuntimeError("Could not create a new spreadsheet")

    def _find_name_field():
        criteria = [
            {"name": "Name", "editable": True},
            {"name": "File name", "editable": True},
            {"description": "Name", "editable": True},
            {"contains": "File name", "editable": True},
            {"contains": "Name", "editable": True},
            {"contains": "Untitled", "editable": True},
            {"role": "text entry", "editable": True},
            {"role": "combo box", "editable": True},
            {"editable": True},
        ]
        for crit in criteria:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def _wait_for_name_field():
        for _ in range(8):
            if _is_save_dialog():
                idx = _find_name_field()
                if idx is not None:
                    return idx
            device.settle(0.5)
        return None

    def _accept_overwrite():
        t = _title()
        yes = None
        for crit in [
            {"name": "Yes", "role": "button"},
            {"contains": "Yes", "role": "button"},
            {"name": "OK", "role": "button"},
            {"contains": "OK", "role": "button"},
            {"name": "Replace", "role": "button"},
            {"contains": "Replace", "role": "button"},
        ]:
            try:
                yes = device.find(**crit)
            except Exception:
                yes = None
            if yes is not None:
                break

        if yes is None:
            return False

        if _is_save_dialog():
            if not any(word in t for word in ("Confirm", "Overwrite", "Replace", "Save")):
                return False

        try:
            device.click(index=yes)
            device.wait()
            device.settle(0.5)
            return True
        except Exception:
            try:
                device.press("enter")
                device.wait()
                device.settle(0.5)
                return True
            except Exception:
                return False

    def _save_to_desktop():
        if not _is_calc():
            raise RuntimeError("LibreOffice Calc is not active before saving")

        idx = None
        if _is_save_dialog():
            idx = _wait_for_name_field()
        else:
            try:
                device.hotkey("ctrl", "s")
                device.wait()
            except Exception:
                pass

            idx = _wait_for_name_field()
            if idx is None:
                try:
                    device.hotkey("ctrl", "s")
                    device.wait()
                except Exception:
                    pass
                idx = _wait_for_name_field()

        if idx is None:
            raise RuntimeError("Could not open the Save dialog")

        home = os.path.expanduser("~")
        if not home or home == "~":
            home = "/home/user"
        path = os.path.join(home, "Desktop", file_name)

        try:
            device.input_text(path, index=idx)
        except Exception:
            try:
                device.click(index=idx)
                device.settle(0.1)
                device.type_at_caret(path)
            except Exception:
                pass

        device.settle(0.2)

        try:
            device.press("enter")
            device.wait()
        except Exception:
            pass

        _accept_overwrite()

        for _ in range(3):
            if not _is_save_dialog():
                break

            idx2 = _find_name_field()
            if idx2 is not None:
                try:
                    device.click(index=idx2)
                    device.settle(0.1)
                    device.press("enter")
                    device.wait()
                    _accept_overwrite()
                    device.settle(0.5)
                    continue
                except Exception:
                    pass

            save_btn = None
            for crit in [
                {"name": "Save", "role": "button"},
                {"contains": "Save", "role": "button"},
                {"name": "OK", "role": "button"},
                {"contains": "OK", "role": "button"},
            ]:
                try:
                    save_btn = device.find(**crit)
                except Exception:
                    save_btn = None
                if save_btn is not None:
                    break

            if save_btn is not None:
                try:
                    device.click(index=save_btn)
                    device.wait()
                except Exception:
                    try:
                        device.press("enter")
                        device.wait()
                    except Exception:
                        pass
            else:
                try:
                    device.press("enter")
                    device.wait()
                except Exception:
                    pass

            _accept_overwrite()
            device.settle(0.5)

        for _ in range(10):
            if not _is_save_dialog():
                if _accept_overwrite():
                    continue
                break

            idx2 = _find_name_field()
            if idx2 is not None:
                try:
                    device.click(index=idx2)
                    device.settle(0.1)
                    device.press("enter")
                    device.wait()
                except Exception:
                    pass

            _accept_overwrite()
            device.settle(0.5)

        if _is_save_dialog():
            raise RuntimeError("Save dialog did not close")

        t = _title()
        if file_name in t or os.path.basename(file_name) in t or ".ods" in t:
            return True

        if _is_calc():
            return True

        raise RuntimeError("Save did not appear to complete")

    _ensure_new_calc()

    _fill_cell("A1", header_a)
    _fill_cell("B1", header_b)
    _fill_cell("A2", val_a2)
    _fill_cell("B2", val_b2)
    _fill_cell("A3", val_a3)
    _fill_cell("B3", val_b3)

    _save_to_desktop()
    return True
