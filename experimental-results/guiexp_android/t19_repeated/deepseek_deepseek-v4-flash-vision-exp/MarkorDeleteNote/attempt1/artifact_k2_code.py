PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt"
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not file_name:
        raise ValueError("file_name must not be empty")

    device.open_app("Markor")
    device.settle(2)

    target_desc = f"File {file_name}"
    file_index = None
    for _ in range(20):
        file_index = device.find(description=target_desc)
        if file_index is not None:
            break
        device.scroll(direction='down')
    if file_index is None:
        raise RuntimeError(f"File not found: {target_desc}")

    device.long_press(index=file_index)

    delete_index = device.find(description='Delete', clickable=True)
    if delete_index is None:
        raise RuntimeError("Delete option not found in context menu")
    device.click(index=delete_index)

    ok_index = device.find(text='OK', clickable=True)
    if ok_index is None:
        raise RuntimeError("OK button not found in delete confirmation dialog")
    device.click(index=ok_index)

    return True
