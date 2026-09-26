import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Note file name including its extension, e.g. note.txt",
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("binding['file_name'] must be a non-empty string")
    stem = os.path.splitext(file_name)[0]

    # ------------------------------------------------------------------
    # Step 1: launch Markor and make sure its main screen is showing
    # ------------------------------------------------------------------
    device.open_app("Markor")
    device.settle(2)

    def on_main_screen():
        return (device.find(text="Markor") is not None
                or device.find(description="Create a new file or folder") is not None)

    if not on_main_screen():
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected")

    # ------------------------------------------------------------------
    # Step 2: locate the target note row by its exact title, never position
    # ------------------------------------------------------------------
    def find_target_row():
        # Markor labels file rows with content-description "File <name>"
        row = device.find(description="File %s" % file_name, clickable=True)
        if row is not None:
            return row
        row = device.find(description="File %s" % file_name, contains=True, clickable=True)
        if row is not None:
            return row
        # description containing the raw file name
        row = device.find(description=file_name, contains=True, clickable=True)
        if row is not None:
            return row
        # fall back to the visible title text
        row = device.find(text=file_name, clickable=True)
        if row is not None:
            return row
        row = device.find(text=file_name, contains=True, clickable=True)
        if row is not None:
            return row
        # some builds title rows with the stem only
        if stem and stem != file_name:
            row = device.find(text=stem, contains=True, clickable=True)
            if row is not None:
                return row
        return None

    target = find_target_row()
    scrolls = 0
    while target is None and scrolls < 6:
        device.scroll("down")
        device.settle(1)
        target = find_target_row()
        scrolls += 1
    if target is None:
        # we may have scrolled past it; sweep back up
        for _ in range(scrolls):
            device.scroll("up")
            device.settle(1)
            target = find_target_row()
            if target is not None:
                break
    if target is None:
        raise LookupError("Note item for '%s' not found on screen" % file_name)

    # ------------------------------------------------------------------
    # Step 3: long-press the row to select it, then tap the Delete action
    # ------------------------------------------------------------------
    device.long_press(index=target)
    device.settle(1)

    # the target note must still be present (i.e. we selected the right row)
    if (device.find(description=file_name, contains=True) is None
            and device.find(text=file_name, contains=True) is None
            and (not stem or device.find(contains=stem) is None)):
        raise RuntimeError(
            "Target note '%s' is not selected/present on screen" % file_name)

    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        delete_btn = device.find(text="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found - ensure the target note '%s' is selected"
            % file_name)
    device.click(delete_btn)
    device.settle(1)

    # ------------------------------------------------------------------
    # Step 4: confirm the "Confirm Delete" dialog with OK
    # ------------------------------------------------------------------
    dialog_title = device.find(text="Confirm Delete")
    name_shown = (device.find(contains=file_name) is not None
                  or (stem and device.find(contains=stem) is not None))
    if dialog_title is None and not name_shown:
        raise LookupError(
            "Delete confirmation dialog for note '%s' not on screen" % file_name)

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(1)

    # soft verification: the deleted note's row should disappear from the list
    gone = device.find(description="File %s" % file_name) is None
    if not gone:
        device.settle(2)
        gone = device.find(description="File %s" % file_name) is None
    if not gone:
        raise RuntimeError("Note '%s' still visible after delete confirmation" % file_name)

    return True
