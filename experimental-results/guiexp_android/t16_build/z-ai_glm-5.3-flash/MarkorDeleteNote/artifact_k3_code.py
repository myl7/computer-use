import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # ------------------------------------------------------------------
    # Step 1: launch Markor and make sure its main screen is showing.
    # ------------------------------------------------------------------
    device.open_app("Markor")
    device.settle(2)

    def on_main_screen():
        return (device.find(text="Markor") is not None
                or device.find(description="Create a new file or folder") is not None)

    if not on_main_screen():
        # Launch may have been slow or the app was in a weird state: retry
        # from the home screen.
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected"
            )

    # ------------------------------------------------------------------
    # Step 2: locate the target note row by its exact name and long-press
    # it to enter selection mode. Never rely on row position.
    # ------------------------------------------------------------------
    def find_target_row():
        # Markor names file list items via content description "File <name>".
        row = device.find(description="File %s" % file_name, clickable=True)
        if row is None:
            row = device.find(description="File %s" % file_name,
                              contains=True, clickable=True)
        if row is None:
            # Fallbacks: plain text of the row, or description equal to name.
            row = device.find(text=file_name, clickable=True)
        if row is None:
            row = device.find(description=file_name, contains=True, clickable=True)
        return row

    target = find_target_row()
    if target is None:
        # The row may be off-screen: scroll down and look again.
        device.scroll("down")
        device.settle(1)
        target = find_target_row()
    if target is None:
        raise LookupError(
            "Note item for '%s' not found on screen (tried scrolling)" % file_name
        )

    device.long_press(target)
    device.settle(1)

    # ------------------------------------------------------------------
    # Step 3: tap the Delete action shown in the selection top bar.
    # It only acts on the currently selected note, so verify the target
    # is still present/selected first.
    # ------------------------------------------------------------------
    def find_delete_button():
        btn = device.find(description="Delete", clickable=True)
        if btn is None:
            btn = device.find(text="Delete", clickable=True)
        return btn

    delete_btn = find_delete_button()
    if delete_btn is None:
        # Selection may not have taken effect: retry the long-press once.
        target = find_target_row()
        if target is not None:
            device.long_press(target)
            device.settle(1)
        delete_btn = find_delete_button()
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found - ensure the target note (%s) is "
            "selected first" % file_name
        )
    device.click(delete_btn)
    device.settle(1)  # wait for the "Confirm Delete" dialog

    # ------------------------------------------------------------------
    # Step 4: confirm the deletion in the dialog.
    # ------------------------------------------------------------------
    dialog_ok = (device.find(text="Confirm Delete") is not None
                 or device.find(contains=file_name) is not None
                 or (stem and device.find(contains=stem) is not None))
    if not dialog_ok:
        raise LookupError(
            "Delete confirmation dialog for note '%s' not on screen" % file_name
        )

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError(
            "No clickable 'OK' confirmation button found in delete dialog"
        )
    device.click(ok_btn)
    device.settle(2)

    return True
