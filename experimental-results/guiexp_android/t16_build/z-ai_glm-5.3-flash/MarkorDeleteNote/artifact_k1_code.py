import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    },
}

_MAX_SCROLL_TRIES = 4


def _find_row(device, file_name):
    """Locate the file-list row of the note named file_name (by title, never position)."""
    exact_desc = "File %s" % file_name
    for kwargs in (
        {"description": exact_desc, "clickable": True},
        {"text": file_name, "clickable": True},
        {"description": file_name, "contains": True, "clickable": True},
        {"text": file_name, "contains": True, "clickable": True},
    ):
        idx = device.find(**kwargs)
        if idx is not None:
            return idx
    return None


def _scroll_to_row(device, file_name):
    idx = _find_row(device, file_name)
    if idx is not None:
        return idx
    directions = ["down", "up"]
    for i in range(_MAX_SCROLL_TRIES):
        device.scroll(directions[i % 2])
        idx = _find_row(device, file_name)
        if idx is not None:
            return idx
    return None


def _note_visible(device, file_name):
    if device.find(description="File %s" % file_name) is not None:
        return True
    if device.find(text=file_name) is not None:
        return True
    if device.find(description=file_name, contains=True) is not None:
        return True
    return False


def _note_gone(device, file_name):
    return (device.find(description="File %s" % file_name) is None and
            device.find(text=file_name) is None)


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # --- Step 1: launch Markor and make sure its main screen is up ---
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

    # --- Step 2: long-press the target note row (match by exact title, scroll if needed) ---
    target = _scroll_to_row(device, file_name)
    if target is None:
        raise LookupError("Note item 'File %s' not found on screen" % file_name)
    device.long_press(index=target)

    # --- Step 3: click the delete action in the selection top bar ---
    if not _note_visible(device, file_name):
        raise RuntimeError(f"Target note '{file_name}' is not selected/present on screen")
    idx = device.find(description="Delete", clickable=True)
    if idx is None:
        idx = device.find(text="Delete", clickable=True)
    if idx is None:
        raise RuntimeError("Delete button not found on current screen")
    device.click(idx)

    # --- Step 4: confirm the "Confirm Delete" dialog ---
    if device.find(text="Confirm Delete") is None or \
            (device.find(contains=stem) is None and
             device.find(contains=file_name) is None):
        raise LookupError(
            f"Delete confirmation dialog for note '{file_name}' not on screen"
        )
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok)

    # --- Verify the note is gone from the list ---
    device.settle(2)
    if not _note_gone(device, file_name):
        raise RuntimeError(f"Note '{file_name}' still present after deletion")

    return True
