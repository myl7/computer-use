PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including extension, e.g. 'note.txt'"
    },
    "text": {
        "type": "string",
        "description": "The note's text content"
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding['file_name']
    # Split the file name into base and extension (including the dot)
    if '.' in file_name:
        base, dot, ext = file_name.rpartition('.')
        ext_with_dot = dot + ext
    else:
        base = file_name
        ext_with_dot = ""

    # Open Markor
    device.open_app("Markor")

    # Tap the button that starts creating a new note
    new_clicked = False
    elems = device.elements()
    for e in elems:
        if e.get('clickable'):
            s = ' '.join([str(e.get('description', '')), str(e.get('hint', ''))]).lower()
            if any(k in s for k in ('new', 'add', 'create')):
                device.click(index=e['index'])
                new_clicked = True
                break
    if not new_clicked:
        # Fallback to the recorded index for the new-note action
        device.click(index=1)

    # The create dialog is open. Fill the name field (without extension).
    elems = device.elements()
    editable = [e for e in elems if e.get('editable')]
    if editable:
        device.input_text(base, index=editable[0]['index'])
    else:
        # Fallback to the recorded index for the name field
        device.input_text(base, index=1)

    # Fill the extension field (with the dot).
    elems = device.elements()
    editable = [e for e in elems if e.get('editable')]
    if len(editable) >= 2:
        ext_field = None
        for e in editable:
            if e.get('text', '') != base:
                ext_field = e
                break
        if ext_field is None:
            ext_field = editable[-1]
        if ext_with_dot:
            device.input_text(ext_with_dot, index=ext_field['index'])
    elif len(editable) == 1:
        if ext_with_dot:
            raise RuntimeError("Extension field not found in create dialog")
    else:
        raise RuntimeError("No editable fields found in create dialog")

    # Confirm with OK (positive button)
    ok_clicked = False
    for candidate in ("OK", "Save", "Create", "Confirm", "Done"):
        idx = device.find(text=candidate)
        if idx is not None:
            device.click(index=idx)
            ok_clicked = True
            break
        idx = device.find(description=candidate)
        if idx is not None:
            device.click(index=idx)
            ok_clicked = True
            break
    if not ok_clicked:
        elems = device.elements()
        for e in elems:
            if e.get('clickable') and e.get('text', '') in ("OK", "Save", "Create", "Confirm", "Done"):
                device.click(index=e['index'])
                ok_clicked = True
                break
    if not ok_clicked:
        # Fallback to the recorded index of the OK button
        device.click(index=11)

    # The document editor is now open. Enter the note text.
    elems = device.elements()
    editable = [e for e in elems if e.get('editable')]
    if editable:
        device.input_text(binding['text'], index=editable[0]['index'])
    else:
        idx = device.find(editable=True)
        if idx is None:
            raise RuntimeError("Editor not found")
        device.input_text(binding['text'], index=idx)

    # Save by leaving the editor.
    device.navigate_back()
    # If the first back dismissed the keyboard, press back again to leave.
    elems = device.elements()
    if any(e.get('editable') for e in elems):
        device.navigate_back()

    return True
