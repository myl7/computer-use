import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    },
    "text": {
        "type": "string",
        "description": "The note's text content",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]

    # Split file name into base name and extension (with dot)
    base, dot, ext = file_name.rpartition(".")
    if not dot or not base:
        base = file_name
        ext = ""

    # 1. Open Markor
    device.open_app("Markor")
    device.settle(2)
    if device.find(text="Markor") is None and \
       device.find(description="Create a new file or folder") is None:
        raise RuntimeError("Markor main screen not detected")

    # 2. Open the 'create new file' dialog via the floating action button
    idx = device.find(description="Create a new file or folder", clickable=True)
    if idx is None:
        raise RuntimeError("Markor 'Create a new file or folder' button not found")
    device.click(idx)
    device.settle(1)

    # 3. Fill the Name field (replace pre-filled placeholder)
    name_idx = device.find(hint="my_note", editable=True)
    if name_idx is None:
        name_idx = device.find(editable=True)
    if name_idx is None:
        raise RuntimeError("Note-name EditText not found in create dialog")
    device.input_text(base, index=name_idx)

    # 4. Fill the small extension field if there is an extension
    if ext:
        ext_idx = None
        for el in device.elements():
            if el.get("editable") and el.get("index") != name_idx:
                ext_idx = el["index"]
                break
        if ext_idx is not None:
            device.input_text("." + ext, index=ext_idx)
        else:
            # Append extension to name field if no separate field exists
            name_idx = device.find(editable=True)
            device.input_text("." + ext, index=name_idx)

    # 5. Confirm the dialog with OK
    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        raise RuntimeError("Create-note dialog 'OK' button not found")
    device.click(ok_idx)
    device.settle(1)

    # 6. Enter the note text in the editor
    edit = device.find(editable=True)
    if edit is None:
        raise RuntimeError("No editable text field found in editor after note creation")
    device.input_text(text, index=edit)

    # 7. Save the note by leaving the editor
    device.navigate_back()
    device.settle(1)
    device.navigate_back()
    device.settle(1)

    return True
