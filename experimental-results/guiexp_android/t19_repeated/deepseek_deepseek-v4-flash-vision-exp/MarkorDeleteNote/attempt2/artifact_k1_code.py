PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not file_name:
        raise ValueError("file_name is required")

    # Open Markor
    device.open_app("Markor")

    # Helper: find the target row in the current element list
    def find_target_row():
        idx = device.find(text=file_name)
        if idx is not None:
            return idx
        idx = device.find(description=f"File {file_name}")
        return idx

    # The target row may not be visible; scroll down until it appears
    target_idx = find_target_row()
    max_scrolls = 10
    scrolls = 0
    while target_idx is None and scrolls < max_scrolls:
        device.scroll(direction='down')
        target_idx = find_target_row()
        scrolls += 1

    if target_idx is None:
        raise RuntimeError(f"File item not found: {file_name}")

    # Long-press the target row to open its context menu / selection mode
    device.long_press(index=target_idx)

    # Locate and click the Delete option
    delete_idx = device.find(description='Delete', clickable=True)
    if delete_idx is None:
        delete_idx = device.find(text='Delete', clickable=True)
    if delete_idx is None:
        delete_idx = device.find(contains='Delete', clickable=True)
    if delete_idx is None:
        raise RuntimeError("Delete option not found")

    device.click(index=delete_idx)

    # Confirm the deletion dialog by clicking OK
    ok_idx = device.find(text='OK', clickable=True)
    if ok_idx is None:
        ok_idx = device.find(description='OK', clickable=True)
    if ok_idx is None:
        # Some dialogs use "Delete" as the positive button
        ok_idx = device.find(text='Delete', clickable=True)
    if ok_idx is None:
        raise RuntimeError("OK button not found in delete confirmation dialog")

    device.click(index=ok_idx)

    return True
