PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]

    # Open Markor
    device.open_app("Markor")
    device.settle(2)

    # Locate the target file row by its exact description "File <name>"
    target_desc = f"File {file_name}"
    element = device.find(description=target_desc)

    # If not visible, scroll down through the list until found
    if element is None:
        for _ in range(10):
            device.scroll(direction="down")
            element = device.find(description=target_desc)
            if element is not None:
                break

    # Fallback: try matching by text (some Markor versions expose the title as text)
    if element is None:
        element = device.find(text=file_name)
        if element is None:
            for _ in range(10):
                device.scroll(direction="down")
                element = device.find(text=file_name)
                if element is not None:
                    break

    if element is None:
        raise RuntimeError(f"File item not found: {file_name}")

    # Long-press the row to open the context menu
    device.long_press(index=element)

    # Click the "Delete" option in the context menu
    delete_el = device.find(description="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(text="Delete", clickable=True)
    if delete_el is None:
        raise RuntimeError("Delete option not found in context menu")
    device.click(index=delete_el)

    # Confirm the deletion dialog by clicking the OK button
    ok_el = device.find(text="OK", clickable=True)
    if ok_el is None:
        ok_el = device.find(description="OK", clickable=True)
    if ok_el is None:
        raise RuntimeError("OK button not found in delete confirmation dialog")
    device.click(index=ok_el)

    return True
