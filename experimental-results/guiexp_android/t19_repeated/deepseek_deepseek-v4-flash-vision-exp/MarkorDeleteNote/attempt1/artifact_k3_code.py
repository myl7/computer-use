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
        # Try exact text first (the row title)
        idx = device.find(text=file_name)
        if idx is not None:
            return idx
        # Try accessibility description "File <name>"
        idx = device.find(description=f"File {file_name}")
        if idx is not None:
            return idx
        return None

    file_idx = None
    # The target row may need scrolling to become visible.
    for _ in range(20):
        file_idx = find_file()
        if file_idx is not None:
            break
        device.scroll(direction="down")
        device.settle(1)

    if file_idx is None:
        raise RuntimeError(f"Note '{file_name}' not found in Markor file list")

    # Long-press the target row to open the context menu.
    device.long_press(index=file_idx)
    device.settle(1)

    # Find the Delete option in the context menu.
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
