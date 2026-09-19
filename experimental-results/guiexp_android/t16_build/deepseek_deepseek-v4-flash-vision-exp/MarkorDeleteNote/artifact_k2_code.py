PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "The note file name including its extension, e.g. note.txt"
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    target_desc = f"File {file_name}"

    def find_file():
        idx = device.find(description=target_desc)
        if idx is not None:
            return idx
        idx = device.find(text=file_name)
        if idx is not None:
            return idx
        return None

    device.open_app("Markor")

    element = find_file()
    if element is None:
        for _ in range(6):
            device.scroll(direction="down")
            element = find_file()
            if element is not None:
                break
    if element is None:
        for _ in range(6):
            device.scroll(direction="up")
            element = find_file()
            if element is not None:
                break
    if element is None:
        raise RuntimeError(f"File item not found: {target_desc}")

    device.long_press(index=element)

    delete_el = device.find(description="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(text="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(hint="Delete", clickable=True)
    if delete_el is None:
        delete_el = device.find(description="Delete")
    if delete_el is None:
        delete_el = device.find(text="Delete")
    if delete_el is None:
        raise RuntimeError("Delete option not found after long press")

    device.click(index=delete_el)

    ok_el = device.find(text="OK", clickable=True)
    if ok_el is None:
        ok_el = device.find(text="OK")
    if ok_el is None:
        ok_el = device.find(text="Delete", clickable=True)
    if ok_el is None:
        ok_el = device.find(text="Yes", clickable=True)
    if ok_el is None:
        raise RuntimeError("Confirmation button not found in delete dialog")

    device.click(index=ok_el)

    return True
