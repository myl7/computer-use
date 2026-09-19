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

    # Split file name into base name and extension (with the dot)
    if "." in file_name:
        base_name, ext = file_name.rsplit(".", 1)
        ext = "." + ext
    else:
        base_name, ext = file_name, ""

    # Step 1: open Markor
    device.open_app("Markor")
    device.settle(2)
    if device.find(text="Markor", contains=True) is None and \
       device.find(description="Create a new file or folder") is None:
        raise RuntimeError("Markor did not open: main screen not detected")

    # Step 2: click the create-new-file FAB
    fab = device.find(description="Create a new file or folder", clickable=True)
    if fab is None:
        raise RuntimeError("Markor 'Create a new file or folder' button not found")
    device.click(fab)
    device.settle(1)

    # Step 3: type base name into the Name field (hint 'my_note')
    name_field = device.find(hint="my_note", editable=True)
    if name_field is None:
        name_field = device.find(editable=True, clickable=True)
    if name_field is None:
        raise RuntimeError("Markor note-name EditText (hint 'my_note') not found")
    device.input_text(base_name, index=name_field)

    # Step 3b: type extension into the small extension field
    if ext:
        ext_field = None
        for el in device.elements():
            if el.get("editable") and el.get("index") != name_field and \
               el.get("hint") != "my_note":
                ext_field = el["index"]
                break
        if ext_field is not None:
            device.input_text(ext, index=ext_field)

    # Step 4: confirm dialog with OK
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        raise RuntimeError("Create-note dialog 'OK' button not found")
    device.click(ok)
    device.settle(1.5)

    # Step 5: type note text into the editor's EditText
    edit = device.find(editable=True)
    if edit is None:
        raise RuntimeError("Markor editor EditText not found after creating note")
    device.input_text(text, index=edit)
    device.settle(1)

    # Step 6: save the note by leaving the editor (auto-save on back)
    device.navigate_back()  # dismiss keyboard if present
    device.settle(1)
    device.navigate_back()  # leave editor back to main list
    device.settle(1.5)

    # Verify the note exists in the file list
    if device.find(text=file_name, contains=True) is None and \
       device.find(description=file_name, contains=True) is None:
        raise RuntimeError(f"Note '{file_name}' not visible in list after creation")

    return True
