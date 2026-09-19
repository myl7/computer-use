PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    },
    "text": {
        "type": "string",
        "description": "Text content to write into the new note",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    file_name = str(binding["file_name"])
    text = str(binding["text"])

    # Split the file name into base name and extension (extension keeps the dot).
    if "." in file_name:
        base_name, extension = file_name.rsplit(".", 1)
        extension = "." + extension
    else:
        base_name, extension = file_name, ""

    # 1. Launch Markor and verify the main screen appeared.
    device.open_app("Markor")
    device.settle(2)
    if device.find(text="Markor") is None and \
            device.find(description="Create a new file or folder") is None:
        raise RuntimeError("Markor main screen not detected after launch")

    # 2. Open the create-new-file dialog via the floating action button.
    fab = device.find(description="Create a new file or folder", clickable=True)
    if fab is None:
        raise RuntimeError("Markor 'Create a new file or folder' button not found")
    device.click(fab)
    device.settle(1)

    # 3. Fill the Name field (file name WITHOUT extension), replacing the placeholder.
    name_idx = device.find(hint="my_note", editable=True)
    if name_idx is None:
        name_idx = device.find(editable=True)
    if name_idx is None:
        raise RuntimeError("Create dialog: note-name EditText not found")
    device.input_text(base_name, index=name_idx)
    device.settle(0.5)

    # 4. Fill the small extension field (extension WITH the dot).
    if extension:
        ext_idx = None
        for el in device.elements():
            if el.get("editable") and el.get("index") != name_idx:
                ext_idx = el.get("index")
                break
        if ext_idx is not None:
            device.input_text(extension, index=ext_idx)
            device.settle(0.5)

    # 5. Confirm the dialog with OK.
    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        raise RuntimeError("Create dialog 'OK' button not found")
    device.click(ok_idx)
    device.settle(1.5)

    # 6. Type the note text into the editor's editable field.
    edit_idx = device.find(editable=True)
    if edit_idx is None:
        # Editor did not open automatically; try to open the note from the list.
        entry = device.find(text=file_name, clickable=True)
        if entry is None:
            entry = device.find(contains=base_name, clickable=True)
        if entry is None:
            raise RuntimeError("Note editor did not open and note entry not found in list")
        device.click(entry)
        device.settle(1.5)
        edit_idx = device.find(editable=True)
        if edit_idx is None:
            raise RuntimeError("Editor opened but no editable text field found")
    device.input_text(text, index=edit_idx)
    device.settle(0.5)

    # 7. Save the note by leaving the editor (navigate back).
    device.navigate_back()
    device.settle(1.5)
    # If an editable field is still in front (e.g. keyboard dismissed first), back out again.
    if device.find(editable=True) is not None:
        device.navigate_back()
        device.settle(1.5)

    return True
