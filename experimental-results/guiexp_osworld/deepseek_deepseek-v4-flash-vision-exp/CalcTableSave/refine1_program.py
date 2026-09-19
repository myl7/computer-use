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

    # Locate and set the filename field
    field = None
    for name in ('Name', 'File name', 'Filename'):
        field = device.find(role='text', editable=True, name=name)
        if field is not None:
            break
    if field is None:
        # Fallback: find an editable text field whose text contains 'Untitled'
        elements = device.elements()
        for el in elements:
            if el.get('role') == 'text' and el.get('editable') and 'Untitled' in (el.get('text') or ''):
                field = el['index']
                break
    if field is None:
        # Fallback: any editable text field
        elements = device.elements()
        for el in elements:
            if el.get('role') == 'text' and el.get('editable'):
                field = el['index']
                break
    if field is None:
        raise RuntimeError("Save dialog: filename field not found")
    device.input_text(binding['file_name'], index=field)

    # Select Desktop as the save location
    desktop = device.find(name='Desktop', clickable=True)
    if desktop is None:
        desktop = device.find(contains='Desktop', clickable=True)
    if desktop is None:
        elements = device.elements()
        for el in elements:
            if el.get('clickable') and ('Desktop' in (el.get('name') or '') or 'Desktop' in (el.get('text') or '')):
                desktop = el['index']
                break
    if desktop is None:
        raise RuntimeError("Save dialog: Desktop entry not found")
    device.click(index=desktop)
    device.settle(0.5)

    # Confirm the save
    save_btn = device.find(name='Save', role='push-button', clickable=True)
    if save_btn is None:
        save_btn = device.find(name='Save', clickable=True)
    if save_btn is not None:
        device.click(index=save_btn)
    else:
        device.hotkey('alt', 's')
    device.settle(1)

    return True
