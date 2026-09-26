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
    device.settle(1)

    # Navigate to the Desktop folder in the Save dialog.
    desktop = None
    for role in ('push-button', 'tree-item', 'button'):
        desktop = device.find(name='Desktop', role=role, clickable=True)
        if desktop is not None:
            break
    if desktop is not None:
        device.click(index=desktop)
        device.settle(0.5)
    else:
        # Fallback: go to Home, then use the location bar to open Desktop.
        home = device.find(name='Home', clickable=True)
        if home is not None:
            device.click(index=home)
            device.settle(0.5)
        device.hotkey('ctrl', 'l')
        device.settle(0.2)
        device.input_text('Desktop')
        device.press('enter')
        device.settle(0.5)

    # Focus the filename field and set the file name.
    name_field = device.find(role='text', editable=True)
    if name_field is None:
        name_field = device.find(role='entry', editable=True)
    if name_field is not None:
        device.input_text(binding['file_name'], index=name_field)
    else:
        # Fallback: use Alt+N to focus the Name field.
        device.hotkey('alt', 'n')
        device.hotkey('ctrl', 'a')
        device.input_text(binding['file_name'])

    # Confirm the save.
    device.press('enter')
    device.settle(1)

    # Handle any confirmation dialog that may appear (e.g. Keep Current Format).
    active = device.active_window().lower()
    if any(word in active for word in ('question', 'confirm', 'warning')):
        for btn_name in ('Yes', 'Save', 'Keep Current Format', 'Use ODF Format', 'OK', 'Replace'):
            btn = device.find(name=btn_name, role='push-button', clickable=True)
            if btn is not None:
                device.click(index=btn)
                device.settle(0.5)
                break
        else:
            device.press('enter')
            device.settle(0.5)

    # Verify the document was saved (title no longer "Untitled").
    device.settle(0.5)
    if 'Untitled' in device.active_window():
        raise RuntimeError("Save failed, window title still contains Untitled")
    return True
