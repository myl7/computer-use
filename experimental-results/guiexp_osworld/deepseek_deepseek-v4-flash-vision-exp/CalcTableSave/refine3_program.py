import os

PARAMS_SCHEMA = {
    "file_name": {"type": "string", "description": "Spreadsheet file name ending in .ods"},
    "header_a": {"type": "string", "description": "Column A header text"},
    "header_b": {"type": "string", "description": "Column B header text"},
    "val_a2": {"type": "integer", "description": "Integer for cell A2"},
    "val_b2": {"type": "integer", "description": "Integer for cell B2"},
    "val_a3": {"type": "integer", "description": "Integer for cell A3"},
    "val_b3": {"type": "integer", "description": "Integer for cell B3"},
}

def program(device, binding: dict) -> bool:
    # Verify a spreadsheet is available
    if device.find(name='A1', role='table-cell') is None:
        raise RuntimeError("No LibreOffice Calc spreadsheet found (cell A1 missing)")

    # Fill the cells in order: A1, B1, A2, B2, A3, B3
    cells = [
        ('A1', binding['header_a']),
        ('B1', binding['header_b']),
        ('A2', binding['val_a2']),
        ('B2', binding['val_b2']),
        ('A3', binding['val_a3']),
        ('B3', binding['val_b3']),
    ]
    for cell_name, value in cells:
        idx = device.find(name=cell_name, role='table-cell', editable=True)
        if idx is None:
            raise RuntimeError(f"Cell {cell_name} not found or not editable")
        device.input_text(str(value), index=idx)

    # Open the Save As dialog
    device.hotkey('ctrl', 'shift', 's')
    device.wait()
    device.settle(0.5)

    # Build the full path to the Desktop.
    home = os.path.expanduser('~')
    full_path = os.path.join(home, 'Desktop', binding['file_name'])

    # Type the full path directly into the filename field.
    # The Name field is focused by default in the Save As dialog;
    # input_text selects the existing text and replaces it.
    device.input_text(full_path)
    device.settle(0.2)

    # Accept the path and save.
    device.press('enter')
    device.settle(0.5)

    # Check if the save already happened (title changed).
    title = device.active_window()
    if binding['file_name'] in title:
        return True

    # Otherwise, click the Save button.
    save_btn = device.find(name='Save', role='push-button', clickable=True)
    if save_btn is None:
        save_btn = device.find(name='Save', clickable=True)
    if save_btn is not None:
        device.click(index=save_btn)
    else:
        device.hotkey('alt', 's')
    device.settle(1)

    # Verify the save completed.
    title = device.active_window()
    if binding['file_name'] in title:
        return True

    # If still not saved, try pressing Enter as a fallback.
    device.press('enter')
    device.settle(1)
    title = device.active_window()
    if binding['file_name'] in title:
        return True

    raise RuntimeError("Save did not complete; window title not updated")
