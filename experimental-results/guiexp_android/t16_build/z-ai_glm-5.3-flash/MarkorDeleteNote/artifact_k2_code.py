import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Note file name including its extension, e.g. note.txt",
    },
}


def _find_note_row(device, file_name):
    """Locate the file-list row for `file_name` by its content, never by position."""
    for criteria in (
        {"description": "File %s" % file_name, "contains": True},
        {"description": file_name, "contains": True},
        {"hint": file_name, "contains": True},
        {"text": file_name, "contains": True},
    ):
        idx = device.find(clickable=True, **criteria)
        if idx is not None:
            return idx
    return None


def _on_main_screen(device):
    return (device.find(text="Markor") is not None
            or device.find(description="Create a new file or folder") is not None)


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # --- 1. Launch Markor and make sure the main file list is visible ---
    device.open_app("Markor")
    device.settle(2)
    if not _on_main_screen(device):
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not _on_main_screen(device):
            raise RuntimeError("Markor did not launch: app missing or main screen not detected")

    # --- 2. Locate the target note row; scroll if it is not visible ---
    target = _find_note_row(device, file_name)
    for direction in ("down", "down", "up", "up"):
        if target is not None:
            break
        device.scroll(direction)
        device.settle(1)
        target = _find_note_row(device, file_name)
    if target is None:
        raise LookupError("Note '%s' not found in the Markor file list" % file_name)

    # --- 3. Long-press the row to enter selection mode ---
    device.long_press(index=target)
    device.settle(1)

    # --- 4. Tap the Delete icon shown by the selection top bar ---
    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        delete_btn = device.find(text="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError("Delete button not found in selection top bar")
    device.click(delete_btn)
    device.settle(1)

    # --- 5. Confirm the "Confirm Delete" dialog ---
    if device.find(text="Confirm Delete") is None:
        device.settle(1)
    if device.find(text="Confirm Delete") is not None:
        if device.find(contains=stem) is None and device.find(contains=file_name) is None:
            raise RuntimeError("Delete dialog does not mention note '%s'" % file_name)
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise LookupError("No clickable 'OK' confirmation button in delete dialog")
    device.click(ok)

    # --- 6. Verify the note row is gone from the list ---
    device.settle(2)
    if device.find(description="File %s" % file_name, contains=True) is not None:
        raise RuntimeError("Note '%s' still present after delete" % file_name)
    return True
