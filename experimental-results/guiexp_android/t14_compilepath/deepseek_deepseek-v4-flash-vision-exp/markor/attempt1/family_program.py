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
    # 1. Open Markor
    device.open_app("Markor")

    # 2. Tap "Create a new file or folder"
    create_btn = device.find(description="Create a new file or folder")
    if create_btn is None:
        create_btn = device.find(text="Create a new file or folder")
    if create_btn is None:
        raise RuntimeError("Create button not found")
    device.click(index=create_btn)

    # 3. Find Name and Extension fields in the create dialog
    elements = device.elements()
    editables = [e for e in elements if e.get('editable')]
    if len(editables) < 2:
        raise RuntimeError("Expected two editable fields in create dialog")

    ext_idx = None
    name_idx = None
    for e in editables:
        hint = (e.get('hint') or '').strip()
        desc = (e.get('description') or '').strip()
        text = (e.get('text') or '').strip()
        if (hint.startswith('.') or text.startswith('.')
                or 'extension' in hint.lower() or 'extension' in desc.lower()):
            ext_idx = e['index']
        else:
            if name_idx is None:
                name_idx = e['index']

    if ext_idx is None:
        name_idx = editables[0]['index']
        ext_idx = editables[-1]['index']
    elif name_idx is None:
        for e in editables:
            if e['index'] != ext_idx:
                name_idx = e['index']
                break

    if name_idx is None or ext_idx is None:
        raise RuntimeError("Could not identify name/extension fields")

    # 4. Enter file name stem and extension
    if '.' in binding['file_name']:
        stem = binding['file_name'].rsplit('.', 1)[0]
        ext = '.' + binding['file_name'].rsplit('.', 1)[1]
    else:
        stem = binding['file_name']
        ext = ''

    device.input_text(stem, index=name_idx)
    device.input_text(ext, index=ext_idx)

    # 5. Confirm with OK
    ok_idx = None
    for text in ("OK", "Save", "Create", "Confirm"):
        ok_idx = device.find(text=text)
        if ok_idx is not None:
            break
        ok_idx = device.find(description=text)
        if ok_idx is not None:
            break

    if ok_idx is None:
        elements = device.elements()
        for e in elements:
            if not e.get('clickable'):
                continue
            t = (e.get('text') or '').strip().lower()
            d = (e.get('description') or '').strip().lower()
            if t == 'cancel' or d == 'cancel':
                continue
            if t or d:
                ok_idx = e['index']
                break

    if ok_idx is None:
        raise RuntimeError("OK button not found")
    device.click(index=ok_idx)

    # 6. Editor should now be open; find the content field
    elements = device.elements()
    editables = [e for e in elements if e.get('editable')]
    content_idx = None

    if editables:
        for e in editables:
            desc = (e.get('description') or '').lower()
            hint = (e.get('hint') or '').lower()
            if ('to-do' in desc or 'to-do' in hint
                    or 'write' in hint or 'content' in hint):
                content_idx = e['index']
                break
        if content_idx is None:
            content_idx = editables[0]['index']
    else:
        content_idx = device.find(description="To-Do")

    if content_idx is None:
        raise RuntimeError("Editor content field not found")

    # 7. Enter the note text
    device.input_text(binding['text'], index=content_idx)

    # 8. Save by leaving the editor
    device.navigate_back()
    device.settle()

    return True
