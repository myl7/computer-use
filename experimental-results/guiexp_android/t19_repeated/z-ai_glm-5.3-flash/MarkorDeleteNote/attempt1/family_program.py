import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]

    # ------------------------------------------------------------------
    # 1. Launch Markor and ensure its main (file list) screen is showing.
    # ------------------------------------------------------------------
    def on_main_screen():
        return (device.find(text="Markor") is not None or
                device.find(description="Create a new file or folder") is not None)

    device.open_app("Markor")
    device.settle(2)

    if not on_main_screen():
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected"
            )

    # ------------------------------------------------------------------
    # 2. Locate the target note row by its exact title and long-press it
    #    to enter selection mode. Never rely on row position; the list
    #    may need scrolling before the row is visible.
    # ------------------------------------------------------------------
    def find_note():
        hit = device.find(description="File %s" % file_name, clickable=True)
        if hit is None:
            hit = device.find(description="File %s" % file_name,
                              contains=True, clickable=True)
        if hit is None:
            hit = device.find(description=file_name, contains=True, clickable=True)
        return hit

    target = find_note()
    scrolls = 0
    while target is None and scrolls < 4:
        device.scroll("down")
        target = find_note()
        scrolls += 1

    if target is None:
        raise LookupError("Note item 'File %s' not found on screen" % file_name)

    device.long_press(target)
    device.settle(1)

    # ------------------------------------------------------------------
    # 3. Tap the Delete action shown by the selection top bar. It only
    #    deletes the currently selected note, so the target must still
    #    be the selected one.
    # ------------------------------------------------------------------
    if (device.find(description="File %s" % file_name) is None and
            device.find(description=file_name, contains=True) is None):
        raise RuntimeError(
            "Target note '%s' is not selected/present on screen" % file_name
        )

    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        device.settle(1)
        delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found on current screen - ensure the target "
            "note (%s) is selected first" % file_name
        )
    device.click(delete_btn)
    device.settle(1)  # wait for the Confirm Delete dialog

    # ------------------------------------------------------------------
    # 4. Confirm the deletion in the "Confirm Delete" dialog.
    # ------------------------------------------------------------------
    stem = os.path.splitext(file_name)[0]
    dialog_visible = (
        device.find(text="Confirm Delete") is not None or
        device.find(contains=stem) is not None or
        device.find(contains=file_name) is not None
    )
    if not dialog_visible:
        raise LookupError(
            "Delete confirmation dialog for note '%s' not on screen" % file_name
        )

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(1)

    return True
