PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    # Derive base name without extension for matching UI titles
    base_name = file_name
    if '.' in file_name:
        base_name = file_name.rsplit('.', 1)[0]

    # Launch Markor
    device.open_app("Markor")
    device.settle(2)

    # Handle possible "All files access" settings page.
    for _ in range(3):
        # If we can see the file list or main UI, break.
        if (device.find(description="Create a new file or folder") is not None or
                device.find(text="Files") is not None or
                device.find(description="Files") is not None):
            break

        # Look for permission elements.
        perm_row = device.find(text="Allow access to manage all files")
        if perm_row is None:
            perm_row = device.find(description="All files access")
        if perm_row is not None:
            device.click(index=perm_row)
            device.settle(2)

        # Try to accept any confirmation dialog.
        allow_btn = device.find(text="Allow", clickable=True)
        if allow_btn is None:
            allow_btn = device.find(text="Allow")
        if allow_btn is not None:
            device.click(index=allow_btn)
            device.settle(2)

        # Leave the settings screen and ensure Markor is in the foreground.
        device.navigate_back()
        device.settle(2)
        device.open_app("Markor")
        device.settle(2)

    # Helper: scan all elements for the target row by exact title
    def find_target():
        elements = device.elements()
        for el in elements:
            desc = el.get('description') or ''
            text = el.get('text') or ''
            # Description format like 'File <title> ' or 'File <title>'
            if desc.startswith('File '):
                title = desc[5:].strip()
                if title == file_name or title == base_name:
                    return el['index']
            # Also check plain text (some layouts put the title in text)
            if text == file_name or text == base_name:
                return el['index']
        return None

    target_index = find_target()

    if target_index is None:
        # Scroll down a few pages to find the note.
        for _ in range(10):
            device.scroll(direction="down")
            target_index = find_target()
            if target_index is not None:
                break

    if target_index is None:
        # Scroll up in case the note is above the current viewport.
        for _ in range(15):
            device.scroll(direction="up")
            target_index = find_target()
            if target_index is not None:
                break

    if target_index is None:
        raise RuntimeError(f"Note '{file_name}' not found in Markor")

    # Long-press the row to select it and show the selection toolbar.
    device.long_press(index=target_index)

    # Find the Delete action in the selection toolbar / context menu.
    delete_index = None
    for el in device.elements():
        desc = el.get('description') or ''
        text = el.get('text') or ''
        if el.get('clickable') and ('Delete' in desc or 'Delete' in text):
            delete_index = el['index']
            break
    if delete_index is None:
        delete_index = device.find(description="Delete", clickable=True)
    if delete_index is None:
        delete_index = device.find(text="Delete", clickable=True)
    if delete_index is None:
        delete_index = device.find(description="Delete")
    if delete_index is None:
        delete_index = device.find(text="Delete")
    if delete_index is None:
        raise RuntimeError("Delete option not found after long press")

    device.click(index=delete_index)

    # Confirm the deletion dialog.
    ok_index = None
    for el in device.elements():
        if not el.get('clickable'):
            continue
        text = el.get('text') or ''
        if text in ("OK", "Yes", "Delete"):
            ok_index = el['index']
            break
    if ok_index is None:
        ok_index = device.find(text="OK", clickable=True)
    if ok_index is None:
        ok_index = device.find(text="OK")
    if ok_index is None:
        ok_index = device.find(text="Yes", clickable=True)
    if ok_index is None:
        ok_index = device.find(text="Yes")
    if ok_index is None:
        ok_index = device.find(text="Delete", clickable=True)
    if ok_index is None:
        ok_index = device.find(text="Delete")
    if ok_index is None:
        raise RuntimeError("Confirmation button not found in delete dialog")

    device.click(index=ok_index)

    return True
