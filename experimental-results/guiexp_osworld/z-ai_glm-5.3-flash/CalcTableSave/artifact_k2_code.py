PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Spreadsheet file name ending in .ods; the file is saved on the Desktop",
    },
    "header_a": {
        "type": "string",
        "required": True,
        "description": "Column A header text written into cell A1",
    },
    "header_b": {
        "type": "string",
        "required": True,
        "description": "Column B header text written into cell B1",
    },
    "val_a2": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell A2 (below header_a)",
    },
    "val_b2": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell B2 (below header_b)",
    },
    "val_a3": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell A3",
    },
    "val_b3": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell B3",
    },
}

_DESKTOP_DIR = "/home/user/Desktop"


def program(device, binding: dict) -> bool:
    """Fill a new LibreOffice Calc spreadsheet (headers in row 1, values in
    rows 2-3) and save it to the Desktop as binding['file_name']."""

    # ------------------------------------------------------------------
    # Helpers: every lookup is by a11y name/role, re-resolved per screen.
    # ------------------------------------------------------------------

    def window_title():
        try:
            return device.active_window() or ""
        except Exception:
            return ""

    def first_index(criteria_list):
        for criteria in criteria_list:
            try:
                idx = device.find(**criteria)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None

    def find_cell(cell_name):
        return first_index([
            {"name": cell_name, "role": "table-cell", "editable": True},
            {"name": cell_name, "role": "table-cell"},
            {"name": cell_name, "editable": True},
        ])

    def fill_cell(cell_name, value):
        idx = find_cell(cell_name)
        if idx is None:
            raise LookupError(
                f"Cell {cell_name} (table-cell, editable) not found on the Calc sheet"
            )
        device.click(index=idx)
        device.input_text(str(value), index=idx)
        # Commit the cell: Enter confirms the entry and moves the cursor down,
        # so the sheet is never left in cell-edit mode.
        device.press("enter")
        device.settle(0.5)

    def save_dialog_present():
        try:
            if device.find(contains="Save", role="dialog") is not None:
                return True
        except Exception:
            pass
        return "Save" in window_title()

    def find_name_field():
        field = first_index([
            {"role": "text", "editable": True, "name": "File name"},
            {"role": "text", "editable": True, "contains": "name"},
            {"role": "text", "editable": True, "name": "Name"},
        ])
        if field is None and save_dialog_present():
            field = first_index([{"role": "text", "editable": True}])
        return field

    # ---------------- 1. Make sure a new Calc spreadsheet is open ----------------

    file_name = str(binding["file_name"])
    base_name = file_name.rsplit(".", 1)[0]

    a1 = find_cell("A1")
    if a1 is None:
        device.open_app("LibreOffice Calc")
        device.wait()
        device.settle(2)
        a1 = find_cell("A1")
    if a1 is None and "Calc" in window_title():
        # LibreOffice is running without a spreadsheet -> create a new one.
        device.hotkey("ctrl", "n")
        device.settle(2)
        a1 = find_cell("A1")
    if a1 is None:
        for _ in range(4):
            device.wait()
            device.settle(2)
            a1 = find_cell("A1")
            if a1 is not None:
                break
    if a1 is None:
        raise LookupError(
            "LibreOffice Calc spreadsheet with an editable cell A1 not found"
        )

    # ---------------- 2. Headers in row 1, values in rows 2 and 3 ----------------

    fill_cell("A1", binding["header_a"])
    fill_cell("B1", binding["header_b"])
    fill_cell("A2", binding["val_a2"])
    fill_cell("B2", binding["val_b2"])
    fill_cell("A3", binding["val_a3"])
    fill_cell("B3", binding["val_b3"])
    device.press("enter")          # ensure the grid, not a cell editor, has focus
    device.settle(0.5)

    # --------------------------- 3. Save to Desktop ------------------------------

    device.hotkey("ctrl", "s")
    device.settle(1.5)

    dialog_up = False
    for _ in range(6):
        if save_dialog_present():
            dialog_up = True
            break
        device.settle(1)
    if not dialog_up:
        raise RuntimeError("Save dialog did not open after Ctrl+S")

    name_field = None
    for _ in range(5):
        name_field = find_name_field()
        if name_field is not None:
            break
        device.settle(1)
    if name_field is None:
        raise RuntimeError("Save dialog: editable Name field not found")

    save_path = _DESKTOP_DIR.rstrip("/") + "/" + file_name
    device.click(index=name_field)
    device.hotkey("ctrl", "a")     # select the pre-filled default name so it is replaced
    device.input_text(save_path, index=name_field)
    device.settle(0.5)

    device.press("enter")          # confirm the save dialog
    device.settle(2)

    if base_name not in window_title():
        # Dialog may still be open (Enter did not confirm): click its Save button.
        save_btn = first_index([{"name": "Save", "role": "push-button"}])
        if save_btn is not None:
            device.click(index=save_btn)
            device.settle(2)
        if base_name not in window_title():
            raise RuntimeError(
                f"Saving '{save_path}' did not complete; active window: {window_title()!r}"
            )

    return True
