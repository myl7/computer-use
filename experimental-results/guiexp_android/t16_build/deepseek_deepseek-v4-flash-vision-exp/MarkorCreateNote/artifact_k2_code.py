PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "file_name": {
            "type": "string",
            "description": "The note file name including its extension, e.g. note.txt"
        },
        "text": {
            "type": "string",
            "description": "The note's text content"
        }
    },
    "required": ["file_name", "text"]
}

def program(device, binding: dict) -> bool:
    file_name = binding['file_name']
    text = binding['text']
    
    # Split file name into base name and extension (including dot)
    if '.' in file_name:
        base, ext_part = file_name.rsplit('.', 1)
        ext = '.' + ext_part
    else:
        base = file_name
        ext = ''
    
    # 1. Open Markor
    device.open_app('Markor')
    device.settle(2)
    
    # 2. Click "Create a new file or folder"
    create_idx = device.find(description='Create a new file or folder', clickable=True)
    if create_idx is None:
        create_idx = device.find(text='New', clickable=True)
    if create_idx is None:
        create_idx = device.find(contains='Create', clickable=True)
    if create_idx is None:
        raise RuntimeError("Could not find 'Create a new file or folder' button")
    device.click(create_idx)
    device.settle(2)
    
    # 3. Fill the create dialog
    # Find the Name field (hint 'my_note' or containing 'note')
    name_idx = device.find(hint='my_note', editable=True)
    if name_idx is None:
        elements = device.elements()
        for e in elements:
            if e.get('editable'):
                hint = (e.get('hint') or '').lower()
                text_val = (e.get('text') or '').lower()
                if 'my_note' in hint or 'my_note' in text_val or 'note' in hint:
                    name_idx = e['index']
                    break
    if name_idx is None:
        # fallback: first editable element
        elements = device.elements()
        editables = [e for e in elements if e.get('editable')]
        if editables:
            name_idx = editables[0]['index']
    if name_idx is None:
        raise RuntimeError("Could not find Name field in create dialog")
    
    device.input_text(base, index=name_idx)
    
    # Find extension field if we have an extension
    if ext:
        ext_idx = None
        elements = device.elements()
        editables = [e for e in elements if e.get('editable')]
        # Look for an editable element that is not the name field
        for e in editables:
            if e['index'] != name_idx:
                hint = (e.get('hint') or '')
                text_val = (e.get('text') or '')
                # The extension field often has a dot in hint or text, or is empty
                if '.' in hint or '.' in text_val or (not hint and not text_val):
                    ext_idx = e['index']
                    break
        # If not found, take the second editable element
        if ext_idx is None and len(editables) > 1:
            for e in editables:
                if e['index'] != name_idx:
                    ext_idx = e['index']
                    break
        
        if ext_idx is not None:
            device.input_text(ext, index=ext_idx)
        else:
            # Fallback: put the full file name in the name field
            device.input_text(file_name, index=name_idx)
    
    # 4. Click OK
    ok_idx = device.find(text='OK', clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text='Create', clickable=True)
    if ok_idx is None:
        raise RuntimeError("OK button not found in create dialog")
    device.click(ok_idx)
    device.settle(2)
    
    # 5. Input the note text in the editor
    elements = device.elements()
    editables = [e for e in elements if e.get('editable') and e.get('clickable')]
    if not editables:
        editables = [e for e in elements if e.get('editable')]
    if not editables:
        raise RuntimeError("Could not find editable note field on DocumentActivity")
    # Prefer an empty editable field
    target = next((e for e in editables if not (e.get('text') or '').strip()), editables[0])
    device.input_text(text, index=target['index'])
    device.settle(1)
    
    # 6. Save by leaving the editor
    device.navigate_back()
    device.settle(2)
    
    return True
