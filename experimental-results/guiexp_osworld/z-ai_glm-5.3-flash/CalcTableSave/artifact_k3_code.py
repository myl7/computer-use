PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods; the file is saved on the Desktop",
    },
    "header_a": {
        "type": "string",
        "description": "Header text written into cell A1",
    },
    "header_b": {
        "type": "string",
        "description": "Header text written into cell B1",
    },
    "val_a2": {
        "type": "integer",
        "description": "Number written into cell A2 (below header_a)",
    },
    "val_b2": {
        "type": "integer",
        "description": "Number written into cell B2 (below header_b)",
    },
    "val_a3": {
        "type": "integer",
        "description": "Number written into cell A3",
    },
    "val_b3": {
        "type": "integer",
        "description": "Number written into cell B3",
    },
}

_DESKTOP_DIR = "/home/user/Desktop"


def _safe_find(device, **criteria):
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _find_cell(device, cell_name):
    idx = _safe_find(device, name=cell_name, role="table-cell", editable=True)
    if idx is None:
        idx = _safe_find(device, name=cell_name, role="table-cell")
    if idx is None:
        idx = _safe_find(device, name=cell_name)
    return idx


def _read_cell_text(device, cell_name):
    try:
        els = device.elements()
    except Exception:
        return None
    for el in els:
        try:
            if el.get("role") == "table-cell" and el.get("name") == cell_name:
                return el.get("text")
        except Exception:
            continue
    return None


def _fill_cell(device, cell_name, value):
    expected = str(value)
    idx = _find_cell(device, cell_name)
    if idx is None:
        device.settle(1.0)
        idx = _find_cell(device, cell_name)
    if idx is None:
        raise LookupError(
            "Spreadsheet cell '%s' not found in LibreOffice Calc" % cell_name
        )
    device.click(idx)
    device.input_text(expected, index=idx)
    device.settle(0.3)

    # Verify the commit; retry once if the cell text is readable and wrong.
    for _ in range(1):
        text = _read_cell_text(device, cell_name)
        if text is None:
            break
        if str(text).strip() == expected:
            break
        idx = _find_cell(device, cell_name)
        if idx is None:
            break
        device.click(idx)
        device.input_text(expected, index=idx)
        device.settle(0.3)


def _ensure_calc_sheet(device):
    title = device.active_window() or ""
    if "Calc" not in title:
        device.open_app("LibreOffice Calc")
        device.wait()
        device.settle(2.0)
    for attempt in range(4):
        if _find_cell(device, "A1") is not None:
            return
        if attempt == 1 and "Calc" in (device.active_window() or ""):
            # Calc is running but no fresh sheet grid: open a new spreadsheet.
            device.hotkey("ctrl", "n")
            device.settle(2.0)
        else:
            device.settle(1.0)
    raise RuntimeError("LibreOffice Calc spreadsheet with cell A1 not found")


def _find_name_field(device):
    for criteria in (
        {"role": "text", "editable": True, "name": "File name"},
        {"role": "text", "editable": True, "contains": "name"},
        {"role": "text", "editable": True},
    ):
        idx = _safe_find(device, **criteria)
        if idx is not None:
            return idx
    return None


def _overwrite_prompt_visible(device):
    if _safe_find(device, name="Replace", role="push-button") is not None:
        return True
    if _safe_find(device, name="Replace") is not None:
        return True
    if _safe_find(device, contains="already exists") is not None:
        return True
    return False


def _title_shows_saved(device, file_name):
    title = device.active_window() or ""
    if file_name in title:
        return True
    base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return bool(base) and base in title and "Save" not in title


def _wait_until_saved(device, file_name, attempts=6):
    for _ in range(attempts):
        if _title_shows_saved(device, file_name):
            return True
        if _overwrite_prompt_visible(device):
            raise RuntimeError(
                "Overwrite prompt appeared for '%s'; a file with this name "
                "must not exist yet" % file_name
            )
        device.settle(1.0)
    return False


def _confirm_save(device):
    save_btn = _safe_find(device, name="Save", role="push-button")
    if save_btn is not None:
        device.click(save_btn)
    else:
        device.press("enter")
    device.settle(2.0)


def _save_spreadsheet(device, binding):
    file_name = str(binding["file_name"])

    # Open the Save dialog (Ctrl+S on the unsaved document).
    field = None
    for _ in range(2):
        device.hotkey("ctrl", "s")
        device.settle(1.5)
        field = _find_name_field(device)
        if field is not None:
            break
    if field is None:
        raise RuntimeError("Save dialog did not open: editable Name field not found")

    # Primary strategy: type the absolute target path into the Name field.
    # This sets both the file name (including the .ods extension) and the
    # Desktop folder in one step.
    device.input_text(_DESKTOP_DIR + "/" + file_name, index=field)
    device.settle(0.5)
    _confirm_save(device)

    if _wait_until_saved(device, file_name):
        return

    # Fallback: the dialog is presumably still open. Switch it to the Desktop
    # folder via the places list, then type the plain file name.
    field = _find_name_field(device)
    if field is None:
        raise RuntimeError(
            "Save of '%s' could not be confirmed (active window: %r)"
            % (file_name, device.active_window())
        )
    desktop = (
        _safe_find(device, name="Desktop", role="table-cell")
        or _safe_find(device, name="Desktop", role="label")
        or _safe_find(device, name="Desktop")
    )
    if desktop is not None:
        device.click(desktop)
        device.settle(1.0)
        field = _find_name_field(device)
        if field is None:
            raise RuntimeError(
                "Save dialog Name field not found after selecting Desktop"
            )
    device.input_text(file_name, index=field)
    device.settle(0.5)
    _confirm_save(device)

    if not _wait_until_saved(device, file_name):
        raise RuntimeError(
            "Save of '%s' did not complete (active window: %r)"
            % (file_name, device.active_window())
        )


def program(device, binding: dict) -> bool:
    _ensure_calc_sheet(device)

    cells = (
        ("A1", binding["header_a"]),
        ("B1", binding["header_b"]),
        ("A2", binding["val_a2"]),
        ("B2", binding["val_b2"]),
        ("A3", binding["val_a3"]),
        ("B3", binding["val_b3"]),
    )
    for cell_name, value in cells:
        _fill_cell(device, cell_name, value)

    # Guarantee the last typed cell is committed before saving.
    device.press("enter")
    device.settle(0.5)

    _save_spreadsheet(device, binding)
    return True
