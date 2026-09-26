PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]

    # Launch Markor
    device.open_app("Markor")
    device.settle(2)

    # Locate the target file row, scrolling if necessary.
    target = None
    # Exact text match first, then content-desc fallback.
    target = device.find(text=file_name)
    if target is None:
        target = device.find(description=f"File {file_name}")

    if target is None:
        # Scroll down a few pages to find the note.
        for _ in range(5):
            device.scroll(direction="down")
            target = device.find(text=file_name)
            if target is None:
                target = device.find(description=f"File {file_name}")
            if target is not None:
                break

    if target is None:
        # Scroll up in case the note is above the current viewport.
        for _ in range(10):
            device.scroll(direction="up")
            target = device.find(text=file_name)
            if target is None:
                target = device.find(description=f"File {file_name}")
            if target is not None:
                break

    if target is None:
        raise RuntimeError(f"Note '{file_name}' not found in Markor")

    # Long-press the row to select it and show the selection toolbar.
    device.long_press(index=target)

    # Find the Delete action in the selection toolbar / context menu.
    delete_el = device.find(description="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(text="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(text="Delete")
    if delete_el is None:
        delete_el = device.find(description="Delete")
    if delete_el is None:
        raise RuntimeError("Delete option not found after long press")

    device.click(index=delete_el)

    # Confirm the deletion dialog.
    ok_el = device.find(text="OK", clickable=True)
    if ok_el is None:
        ok_el = device.find(text="OK")
    if ok_el is None:
        ok_el = device.find(text="Yes", clickable=True)
    if ok_el is None:
        raise RuntimeError("Confirmation button not found in delete dialog")

    device.click(index=ok_el)

    return True
