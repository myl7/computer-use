import os


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not file_name:
        raise ValueError("binding['file_name'] must be a non-empty file name")

    # --- Launch Markor ---
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

    # --- Locate the target note row by its exact content description ---
    def find_row():
        # exact match first, then contains (description is "File <name>")
        idx = device.find(description="File %s" % file_name, clickable=True)
        if idx is not None:
            return idx
        return device.find(description="File %s" % file_name, contains=True, clickable=True)

    target = find_row()

    # Scroll through the list if not immediately visible
    if target is None:
        for _ in range(5):
            device.scroll("down")
            target = find_row()
            if target is not None:
                break

    # Fallback: use Markor's search to narrow down the list
    if target is None:
        search_btn = device.find(description="Search", clickable=True)
        if search_btn is not None:
            device.click(search_btn)
            device.settle(1)
            search_box = device.find(editable=True)
            if search_box is not None:
                device.input_text(os.path.splitext(file_name)[0], index=search_box)
                device.settle(2)
                target = find_row()
                if target is None:
                    target = device.find(contains=os.path.splitext(file_name)[0], clickable=True)
                # close search to return to the list view
                device.navigate_back()
                device.settle(1)

    if target is None:
        raise LookupError("Note item for '%s' not found on screen" % file_name)

    # --- Long-press the target row to enter selection mode ---
    device.long_press(index=target)
    device.settle(1)

    # Verify the target note is selected
    if device.find(description="File %s" % file_name, contains=True) is None:
        raise RuntimeError("Target note '%s' is not selected/present on screen" % file_name)

    # --- Click the Delete action in the selection top bar ---
    del_btn = device.find(description="Delete", clickable=True)
    if del_btn is None:
        raise RuntimeError("Delete button not found on current screen")
    device.click(del_btn)
    device.settle(1)

    # --- Confirm the delete dialog ---
    stem = os.path.splitext(file_name)[0]
    if device.find(text="Confirm Delete") is None and \
       device.find(text="Delete") is None:
        raise RuntimeError("Delete confirmation dialog did not appear")

    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise LookupError("No clickable 'OK' confirmation button found in delete dialog")
    device.click(ok_btn)
    device.settle(2)

    # --- Verify the note is gone ---
    if device.find(description="File %s" % file_name, contains=True) is not None:
        raise RuntimeError("Note '%s' still present after deletion" % file_name)

    return True


PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}
