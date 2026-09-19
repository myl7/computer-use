import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
        "required": True,
    },
}


def _on_main_screen(device):
    return (
        device.find(text="Markor") is not None
        or device.find(description="Create a new file or folder") is not None
    )


def _find_target_row(device, file_name):
    # Prefer an exact content-description match ("File <name>"), fall back to
    # a contains match, and do not require the clickable flag in the last resort.
    row = device.find(description="File %s" % file_name, clickable=True)
    if row is not None:
        return row
    row = device.find(description="File %s" % file_name, contains=True, clickable=True)
    if row is not None:
        return row
    row = device.find(description="File %s" % file_name)
    if row is not None:
        return row
    return device.find(description="File %s" % file_name, contains=True)


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("binding['file_name'] must be a non-empty string")

    # --- Step 1: launch Markor and make sure the main file list is showing ---
    device.open_app("Markor")
    device.settle(2)

    if not _on_main_screen(device):
        # A leftover editor/dialog may be in the way; go back once.
        device.navigate_back()
        device.settle(1)
    if not _on_main_screen(device):
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
    if not _on_main_screen(device):
        raise RuntimeError("Markor did not launch: app missing or main screen not detected")

    # --- Step 2: locate the target note row by its exact title and long-press it ---
    target = _find_target_row(device, file_name)

    scrolls = 0
    while target is None and scrolls < 6:
        device.scroll("down")
        scrolls += 1
        target = _find_target_row(device, file_name)

    if target is None:
        # The list may already be scrolled towards the bottom; sweep back up.
        ups = 0
        while target is None and ups < 6:
            device.scroll("up")
            ups += 1
            target = _find_target_row(device, file_name)

    if target is None:
        raise LookupError("Note item 'File %s' not found on screen" % file_name)

    device.long_press(index=target)
    device.settle(1)

    # --- Step 3: with the note selected, tap the Delete action in the top bar ---
    if device.find(description="File %s" % file_name, contains=True) is None and \
       device.find(description="File %s" % file_name) is None:
        raise RuntimeError("Target note '%s' is not selected/present on screen" % file_name)

    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        delete_btn = device.find(text="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError("Delete button not found on current screen")
    device.click(delete_btn)
    device.settle(1)

    # --- Step 4: confirm the "Confirm Delete" dialog ---
    stem = os.path.splitext(file_name)[0]
    dialog_open = device.find(text="Confirm Delete") is not None
    if not dialog_open:
        # Fallback: an OK button together with the note's name on screen.
        dialog_open = (
            device.find(text="OK", clickable=True) is not None
            and (device.find(contains=stem) is not None
                 or device.find(contains=file_name) is not None)
        )
    if not dialog_open:
        raise LookupError("Delete confirmation dialog for note '%s' not on screen" % file_name)

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(2)

    # --- Verify the note is actually gone from the list ---
    still_there = device.find(description="File %s" % file_name) is not None
    if still_there:
        device.scroll("down")
        still_there = device.find(description="File %s" % file_name) is not None
    if still_there:
        raise RuntimeError("Note '%s' still visible after deletion" % file_name)

    return True
