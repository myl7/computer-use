PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    target_desc = f"File {file_name}"

    device.open_app("Markor")

    def find_file_row():
        idx = device.find(description=target_desc)
        if idx is None:
            idx = device.find(text=file_name)
        return idx

    row_index = find_file_row()

    # Scroll down to find the file if not visible
    if row_index is None:
        for _ in range(20):
            device.scroll(direction="down")
            row_index = find_file_row()
            if row_index is not None:
                break

    # If still not found, scroll up (in case we scrolled past it)
    if row_index is None:
        for _ in range(20):
            device.scroll(direction="up")
            row_index = find_file_row()
            if row_index is not None:
                break

    if row_index is None:
        raise RuntimeError(f"File item not found: {target_desc}")

    # Long-press the target row to open the selection/context menu
    device.long_press(index=row_index)

    # Click the Delete action
    delete_el = device.find(description="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(text="Delete", clickable=True)
    if delete_el is None:
        raise RuntimeError("Delete option not found after long-press")
    device.click(index=delete_el)

    # Confirm the deletion dialog
    ok_el = device.find(text="OK", clickable=True)
    if ok_el is None:
        raise RuntimeError("OK button not found in delete confirmation dialog")
    device.click(index=ok_el)

    return True
