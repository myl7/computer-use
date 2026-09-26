PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not file_name:
        raise ValueError("file_name must not be empty")

    device.open_app("Markor")
    device.settle(2)

    def find_file():
        elements = device.elements()
        base_name = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name

        # 1. Exact description with full file name (extension visible)
        full_desc = f"File {file_name}"
        for el in elements:
            desc = (el.get('description') or '').strip()
            if desc == full_desc:
                return el['index']

        # 2. Exact text with full file name
        for el in elements:
            text = (el.get('text') or '').strip()
            if text == file_name or text == f"File {file_name}":
                return el['index']

        # 3. Exact description with base name (extension hidden)
        base_desc = f"File {base_name}"
        for el in elements:
            desc = (el.get('description') or '').strip()
            if desc == base_desc:
                return el['index']

        # 4. Exact text with base name
        for el in elements:
            text = (el.get('text') or '').strip()
            if text == base_name or text == f"File {base_name}":
                return el['index']

        return None

    file_idx = None
    for _ in range(20):
        file_idx = find_file()
        if file_idx is not None:
            break
        device.scroll(direction="down")
        device.settle(1)

    if file_idx is None:
        raise RuntimeError(f"Note '{file_name}' not found in Markor file list")

    # Long-press the target row to enter selection mode / open context menu.
    device.long_press(index=file_idx)
    device.settle(1)

    # Find the Delete option in the selection top bar / context menu.
    delete_idx = device.find(description="Delete", clickable=True)
    if delete_idx is None:
        delete_idx = device.find(text="Delete", clickable=True)
    if delete_idx is None:
        delete_idx = device.find(description="Delete")
    if delete_idx is None:
        delete_idx = device.find(text="Delete")
    if delete_idx is None:
        raise RuntimeError("Delete option not found in context menu")

    device.click(index=delete_idx)
    device.settle(1)

    # Confirm the delete dialog.
    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise RuntimeError("Confirmation OK button not found")

    device.click(index=ok_idx)
    device.settle(1)

    return True
