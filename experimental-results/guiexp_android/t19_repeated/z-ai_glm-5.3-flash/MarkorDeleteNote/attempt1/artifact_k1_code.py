import os


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # Launch Markor
    device.open_app("Markor")
    device.settle(2)
    on_main = device.find(text="Markor") is not None or \
              device.find(description="Create a new file or folder") is not None
    if not on_main:
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        on_main = device.find(text="Markor") is not None or \
                  device.find(description="Create a new file or folder") is not None
    if not on_main:
        raise RuntimeError("Markor did not launch: app missing or main screen not detected")

    # Locate the target note row by its content description, scrolling if needed
    target = device.find(description="File %s" % file_name, contains=True, clickable=True)
    if target is None:
        device.scroll("down")
        target = device.find(description="File %s" % file_name, contains=True, clickable=True)
    if target is None:
        raise LookupError("Note item 'File %s' not found on screen" % file_name)

    # Long-press to select the note
    device.long_press(target)
    device.settle(1)

    # Verify the target note is selected/present, then click Delete
    if device.find(description=file_name, contains=True) is None and \
       device.find(text=file_name, contains=True) is None:
        raise RuntimeError(f"Target note '{file_name}' is not selected/present on screen")
    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        delete_btn = device.find(text="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError("Delete button not found on current screen")
    device.click(delete_btn)
    device.settle(1)

    # Confirm the delete dialog
    if device.find(text="Confirm Delete") is None and \
       device.find(text=stem, contains=True) is None and \
       device.find(text=file_name, contains=True) is None:
        raise LookupError(f"Delete confirmation dialog for note '{file_name}' not on screen")
    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(1)

    # Verify the note is gone
    device.scroll("up")
    if device.find(description="File %s" % file_name, contains=True) is not None or \
       device.find(text=file_name, contains=True) is not None:
        raise RuntimeError("Note '%s' still present after deletion" % file_name)
    return True


PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
        "required": True,
    }
}
