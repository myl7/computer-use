PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    file_desc = f"File {file_name}"

    # Launch Markor
    app_name = binding.get("app_name", "Markor")
    device.open_app(app_name)
    device.settle(2)

    # Find the target file row, scrolling down if necessary.
    element = device.find(description=file_desc)
    max_scrolls = 20
    scroll_count = 0
    while element is None and scroll_count < max_scrolls:
        device.scroll(direction="down")
        scroll_count += 1
        element = device.find(description=file_desc)

    if element is None:
        raise RuntimeError(f"File item not found: {file_desc}")

    # Long-press the file row to open its context menu / selection bar.
    device.long_press(index=element)

    # Click the Delete action in the context menu / selection bar.
    delete_el = device.find(description="Delete", clickable=True)
    if delete_el is None:
        raise RuntimeError("Delete option not found in context menu")
    device.click(index=delete_el)

    # Confirm the deletion dialog by clicking OK.
    ok_el = device.find(text="OK", clickable=True)
    if ok_el is None:
        raise RuntimeError("Confirmation OK button not found")
    device.click(index=ok_el)

    return True
