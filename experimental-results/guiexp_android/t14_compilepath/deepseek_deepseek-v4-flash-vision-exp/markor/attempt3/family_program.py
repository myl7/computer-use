PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt"
    },
    "text": {
        "type": "string",
        "description": "The note's text content"
    }
}

def program(device, binding: dict) -> bool:
    def find_fab():
        # Try common content descriptions for the new-note FAB
        for desc in ["New note", "Create new note", "Add note", "New file", "Create note", "New"]:
            idx = device.find(description=desc)
            if idx is not None:
                return idx
        # Try text labels
        for text in ["+", "New", "Create"]:
            idx = device.find(text=text)
            if idx is not None:
                return idx
        # Fallback: scan clickable elements for a "new/create/add" hint
        elements = device.elements()
        for el in elements:
            if el.get('clickable'):
                desc = (el.get('description') or '').lower()
                text = (el.get('text') or '').lower()
                if 'new' in desc or 'create' in desc or 'add' in desc:
                    return el['index']
        # Last resort: first clickable element
        return device.find(clickable=True)

    def set_text(index, text):
        if index is None:
            return
        # Focus the field, clear any existing text, then type the new value
        device.click(index=index)
        device.adb_shell("input keyevent KEYCODE_CTRL_A")
        device.adb_shell("input keyevent KEYCODE_DEL")
        device.input_text(text, index=index)

    # Step 1: Open Markor
    device.open_app("Markor")
    device.settle(2)

    # Step 2: Tap the FAB to create a new note
    fab = find_fab()
    if fab is None:
        raise RuntimeError("Could not find the new note button")
    device.click(index=fab)

    # Step 3: Fill in the file name and extension in the create dialog
    device.settle(1)
    editables = [el for el in device.elements() if el.get('editable')]
    if len(editables) >= 2:
        name_field = editables[0]['index']
        ext_field = editables[1]['index']
    else:
        name_field = device.find(hint="Name") or device.find(hint="File name")
        ext_field = device.find(hint="extension") or device.find(hint="Extension")
        if name_field is None and editables:
            name_field = editables[0]['index']
        if ext_field is None and len(editables) > 1:
            ext_field = editables[1]['index']

    if name_field is None:
        raise RuntimeError("Could not find the name field")
    if ext_field is None:
        raise RuntimeError("Could not find the extension field")

    # Split file_name into name and extension
    if '.' in binding['file_name']:
        name_part, ext_part = binding['file_name'].rsplit('.', 1)
        ext_with_dot = '.' + ext_part
    else:
        name_part = binding['file_name']
        ext_with_dot = ''

    set_text(name_field, name_part)
    if ext_with_dot:
        set_text(ext_field, ext_with_dot)

    # Step 4: Confirm with OK
    ok_button = None
    for text in ["OK", "Create", "Save", "Confirm", "Done"]:
        ok_button = device.find(text=text)
        if ok_button is not None:
            break
    if ok_button is None:
        elements = device.elements()
        for el in elements:
            if el.get('clickable') and not el.get('editable'):
                text = (el.get('text') or '').strip()
                if text.lower() != 'cancel':
                    ok_button = el['index']
                    break
    if ok_button is None:
        raise RuntimeError("Could not find the OK button")
    device.click(index=ok_button)

    # Step 5: Enter the note text in the editor
    device.settle(1)
    editor_field = device.find(editable=True)
    if editor_field is None:
        editor_field = device.find(hint="Write something") or device.find(hint="Content")
    if editor_field is None:
        raise RuntimeError("Could not find the editor text field")
    set_text(editor_field, binding['text'])

    # Step 6: Save by leaving the editor
    device.navigate_back()

    return True
