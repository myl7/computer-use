import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "pattern": r".+\.ods$",
        "description": "Target spreadsheet file name ending in .ods; saved onto the Desktop.",
    },
    "header_a": {
        "type": "string",
        "required": True,
        "description": "Column A header text written into cell A1.",
    },
    "header_b": {
        "type": "string",
        "required": True,
        "description": "Column B header text written into cell B1.",
    },
    "val_a2": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell A2 (under header_a).",
    },
    "val_b2": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell B2 (under header_b).",
    },
    "val_a3": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell A3.",
    },
    "val_b3": {
        "type": "integer",
        "required": True,
        "description": "Integer value written into cell B3.",
    },
}

_DESKTOP_DIR = "/home/user/Desktop"
_REQUIRED_KEYS = ("file_name", "header_a", "header_b",
                  "val_a2", "val_b2", "val_a3", "val_b3")


def _wait_for_save_dialog(device, timeout=10.0):
    """Return True once the Save dialog (or a Save-titled window) is visible."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if device.find(contains="Save", role="dialog") is not None:
            return True
        if "Save" in (device.active_window() or ""):
            return True
        time.sleep(0.4)
    return False


def _find_name_field(device):
    """Locate the editable Name/file-name entry of the Save dialog."""
    return (
        device.find(name="File name", role="text", editable=True)
        or device.find(contains="File name", role="text", editable=True)
        or device.find(name="Name", role="text", editable=True)
        or device.find(contains="Name", role="text", editable=True)
        or device.find(role="text", editable=True)
    )


def _fill_cell(device, cell_name, value):
    """Click the named spreadsheet cell and type the committed value into it."""
    idx = device.find(name=cell_name, role="table-cell", editable=True)
    if idx is None:
        idx = device.find(name=cell_name, role="table-cell")
    if idx is None:
        raise LookupError("Cell %s (table-cell, editable) not found on the Calc sheet"
                          % cell_name)
    device.click(idx)
    device.input_text(str(value), index=idx)  # input_text commits the cell
    device.settle(0.3)


def program(device, binding: dict) -> bool:
    missing = [key for key in _REQUIRED_KEYS if key not in binding]
    if missing:
        raise ValueError("binding is missing required keys: %s" % ", ".join(missing))

    file_name = str(binding["file_name"])
    if not file_name.lower().endswith(".ods"):
        raise ValueError("file_name must end with .ods, got %r" % file_name)

    # --- 1. Make sure LibreOffice Calc is in the foreground with a fresh sheet ---
    title = device.active_window() or ""
    if "LibreOffice Calc" not in title:
        device.open_app("LibreOffice Calc")
        device.settle(2)
        title = device.active_window() or ""
        if "LibreOffice Calc" not in title:
            raise RuntimeError("LibreOffice Calc did not come to the foreground "
                               "(active window=%r)" % title)

    # --- 2. Fill the sheet: headers in row 1, numbers underneath in rows 2/3 ---
    cells = [
        ("A1", binding["header_a"]),
        ("B1", binding["header_b"]),
        ("A2", binding["val_a2"]),
        ("B2", binding["val_b2"]),
        ("A3", binding["val_a3"]),
        ("B3", binding["val_b3"]),
    ]
    for cell_name, value in cells:
        _fill_cell(device, cell_name, value)

    # --- 3. Open the Save dialog ---
    device.hotkey("ctrl", "s")
    device.settle(1.0)
    if not _wait_for_save_dialog(device):
        raise RuntimeError("Save dialog did not open after Ctrl+S")
    device.settle(0.5)

    # --- 4. Point the Name field at /home/user/Desktop/<file_name> (incl. .ods) ---
    full_path = _DESKTOP_DIR.rstrip("/") + "/" + file_name
    field = _find_name_field(device)
    if field is None:
        raise RuntimeError("Save dialog did not open: editable Name field not found")
    device.click(field)
    device.hotkey("ctrl", "a")  # select any pre-filled default name so it is replaced
    device.settle(0.3)
    device.input_text(full_path, index=field)
    device.settle(0.5)

    # --- 5. Confirm the save ---
    device.press("enter")
    device.settle(2)

    # --- 6. Verify: the Calc window title must now carry the saved file name.
    #         If an overwrite/keep-format prompt had appeared instead, the title
    #         would not contain the file stem, and we treat that as a failure. ---
    stem = file_name.rsplit(".", 1)[0]
    final_title = device.active_window() or ""
    if stem not in final_title or "Save" in final_title:
        raise RuntimeError("Save did not complete as expected; active window=%r"
                           % final_title)

    return True
