import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Note file name including its extension, e.g. note.txt; "
                       "the note whose title matches this exactly is deleted.",
    },
}


def _find_note_row(device, file_name):
    """Locate the target note's list row by content, scrolling if necessary.

    The row is matched by its exact file name (Markor exposes it as the
    content description 'File <name>'), never by row position.
    """
    criteria = [
        {"description": file_name, "contains": True, "clickable": True},
        {"description": "File %s" % file_name, "contains": True, "clickable": True},
        {"text": file_name, "contains": True, "clickable": True},
    ]
    # First try without scrolling (list usually opens at the top).
    for crit in criteria:
        idx = device.find(**crit)
        if idx is not None:
            return idx
    # The target row may need a scroll to become visible.
    for _ in range(6):
        device.scroll("down")
        for crit in criteria:
            idx = device.find(**crit)
            if idx is not None:
                return idx
    # Fallback: in case the list was scrolled past the target.
    for _ in range(3):
        device.scroll("up")
        for crit in criteria:
            idx = device.find(**crit)
            if idx is not None:
                return idx
    return None


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # --- Step 1: launch Markor and make sure its main screen is up ---
    device.open_app("Markor")
    device.settle(2)

    def on_main_screen():
        return (device.find(text="Markor") is not None or
                device.find(description="Create a new file or folder") is not None)

    if not on_main_screen():
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected")

    # --- Step 2: locate the target note row and long-press it to select ---
    target = _find_note_row(device, file_name)
    if target is None:
        raise LookupError("Note item for '%s' not found on screen" % file_name)
    device.long_press(index=target)
    device.settle(1)

    # Deletion acts on the current selection, so the target row must still be
    # present (now selected) before we may touch the delete action.
    if (device.find(description=file_name, contains=True) is None and
            device.find(description="File %s" % file_name, contains=True) is None and
            device.find(text=file_name, contains=True) is None):
        raise RuntimeError(
            "Target note '%s' is not selected/present on screen" % file_name)

    # --- Step 3: click the delete icon shown in the selection top bar ---
    del_btn = device.find(description="Delete", clickable=True)
    if del_btn is None:
        del_btn = device.find(text="Delete", clickable=True)
    if del_btn is None:
        del_btn = device.find(hint="Delete", clickable=True)
    if del_btn is None:
        raise RuntimeError("Delete button not found on current screen")
    device.click(del_btn)
    device.settle(1)

    # --- Step 4: confirm Markor's 'Confirm Delete' dialog ---
    title_ok = device.find(text="Confirm Delete") is not None
    name_ok = (device.find(contains=stem) is not None or
               device.find(contains=file_name) is not None)
    if not (title_ok or name_ok):
        raise LookupError(
            "Delete confirmation dialog for note '%s' not on screen" % file_name)

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(1)

    # --- Final verification: the deleted note's row must be gone ---
    def row_still_present():
        return (device.find(description=file_name, contains=True) is not None or
                device.find(description="File %s" % file_name, contains=True) is not None)

    if row_still_present():
        device.settle(2)
        if row_still_present():
            raise RuntimeError("Note '%s' still present after delete" % file_name)

    return True
