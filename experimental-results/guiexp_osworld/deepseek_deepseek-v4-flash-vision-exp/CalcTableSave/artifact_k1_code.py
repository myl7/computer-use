PARAMS_SCHEMA = {
    "file_name": {"type": "string", "description": "Spreadsheet file name ending in .ods"},
    "header_a": {"type": "string", "description": "Column A header text"},
    "header_b": {"type": "string", "description": "Column B header text"},
    "val_a2": {"type": "integer", "description": "Value for cell A2"},
    "val_b2": {"type": "integer", "description": "Value for cell B2"},
    "val_a3": {"type": "integer", "description": "Value for cell A3"},
    "val_b3": {"type": "integer", "description": "Value for cell B3"},
}


def _find_name_field(device):
    for role in ('text', 'paragraph', 'entry', 'text-field'):
        idx = device.find(role=role, editable=True)
        if idx is not None:
            return idx
    idx = device.find(editable=True)
    if idx is not None:
        return idx
    return None


def _click_desktop(device):
    elements = device.elements()
    candidates = []
    for el in elements:
        if not el.get('clickable'):
            continue
        name = el.get('name') or ''
        text = el.get('text') or ''
        if name == 'Desktop' or text == 'Desktop':
            candidates.append(el)
    if not candidates:
        for el in elements:
            if not el.get('clickable'):
                continue
            name = el.get('name') or ''
            text = el.get('text') or ''
            if 'Desktop' in name or 'Desktop' in text:
                candidates.append(el)
    if not candidates:
        idx = device.find(name='Desktop')
        if idx is not None:
            device.click(index=idx)
            device.settle(0.3)
            device.press('enter')
            device.settle(0.3)
            return
        raise RuntimeError("Save dialog: Desktop destination not found")

    preferred = None
    fallback = None
    for el in candidates:
        role = el.get('role', '')
        if role in ('toggle-button', 'push-button', 'list-item'):
            preferred = el
            break
        elif role == 'table-cell':
            fallback = el

    if preferred is not None:
        device.click(index=preferred['index'])
        device.settle(0.3)
        device.press('enter')
        device.settle(0.3)
    elif fallback is not None:
        device.click(index=fallback['index'])
        device.settle(0.3)
        device.press('enter')
        device.settle(0.3)
    else:
        el = candidates[0]
        device.click(index=el['index'])
        device.settle(0.3)
        device.press('enter')
        device.settle(0.3)


def program(device, binding: dict) -> bool:
    # Ensure a LibreOffice Calc spreadsheet is available.
    a1 = device.find(name='A1', role='table-cell', editable=True)
    if a1 is None:
        device.open_app('LibreOffice Calc')
        device.wait()
        device.hotkey('ctrl', 'n')
        device.settle(1)
        a1 = device.find(name='A1', role='table-cell', editable=True)
        if a1 is None:
            raise RuntimeError("Could not open a new spreadsheet")

    # Enter headers.
    device.input_text(binding['header_a'], index=a1)

    b1 = device.find(name='B1', role='table-cell', editable=True)
    if b1 is None:
        raise RuntimeError("Cell B1 not found")
    device.click(index=b1)
    device.input_text(binding['header_b'], index=b1)
    device.press('enter')

    # Enter values.
    a2 = device.find(name='A2', role='table-cell', editable=True)
    if a2 is None:
        raise RuntimeError("Cell A2 not found")
    device.click(index=a2)
    device.input_text(str(binding['val_a2']), index=a2)
    device.press('tab')

    b2 = device.find(name='B2', role='table-cell', editable=True)
    if b2 is None:
        raise RuntimeError("Cell B2 not found")
    device.click(index=b2)
    device.input_text(str(binding['val_b2']), index=b2)
    device.press('enter')

    a3 = device.find(name='A3', role='table-cell', editable=True)
    if a3 is None:
        raise RuntimeError("Cell A3 not found")
    device.click(index=a3)
    device.input_text(str(binding['val_a3']), index=a3)
    device.press('tab')

    b3 = device.find(name='B3', role='table-cell', editable=True)
    if b3 is None:
        raise RuntimeError("Cell B3 not found")
    device.click(index=b3)
    device.input_text(str(binding['val_b3']), index=b3)
    device.press('enter')

    # Save As.
    device.hotkey('ctrl', 'shift', 's')
    device.settle(1)

    name_field = _find_name_field(device)
    if name_field is None:
        raise RuntimeError("Save dialog: no editable filename field found")
    device.input_text(binding['file_name'], index=name_field)

    _click_desktop(device)

    save_btn = device.find(name='Save', role='push-button', clickable=True)
    if save_btn is not None:
        device.click(index=save_btn)
    else:
        device.hotkey('alt', 's')
    device.settle(1)

    return True
