PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "document file name ending in .odt, one string"
    },
    "title": {
        "type": "string",
        "description": "the title line, one string"
    },
    "body": {
        "type": "string",
        "description": "the body sentence for the third line, one string"
    }
}


def program(device, binding: dict) -> bool:
    # Focus the Writer document body
    doc = device.find(role='document-text', editable=True)
    if doc is None:
        doc = device.find(role='paragraph', editable=True)
    if doc is None:
        doc = device.find(role='text', editable=True)
    if doc is None:
        raise RuntimeError("No editable Writer document body found")
    device.click(index=doc)

    # Title line
    device.input_text(binding['title'], index=doc)

    # Empty line
    device.press('enter')
    device.press('enter')

    # Body line
    device.type_at_caret(binding['body'])

    # Open Save As dialog
    device.hotkey('ctrl', 'shift', 's')
    device.wait()

    # Find the filename field in the Save dialog
    filename_field = device.find(role='text', editable=True, contains='Untitled')
    if filename_field is None:
        filename_field = device.find(role='text', editable=True)
    if filename_field is None:
        # Fallback: File menu -> Save As
        file_menu = device.find(name='File', role='menu')
        if file_menu is None:
            raise RuntimeError("Could not find File menu")
        device.click(index=file_menu)
        save_as = device.find(name='Save As...', role='menu-item', clickable=True)
        if save_as is None:
            raise RuntimeError("Save As... menu item not found")
        device.click(index=save_as)
        device.wait()
        filename_field = device.find(role='text', editable=True, contains='Untitled')
        if filename_field is None:
            filename_field = device.find(role='text', editable=True)
        if filename_field is None:
            raise RuntimeError("Save dialog did not open")

    # Fill the Name field with the full file name including .odt
    device.input_text(binding['file_name'], index=filename_field)

    # Select Desktop as the save location
    desktop = device.find(name='Desktop')
    if desktop is None:
        desktop = device.find(name='Desktop', role='label')
    if desktop is None:
        raise RuntimeError("Desktop location not found in save dialog")
    device.click(index=desktop)
    device.wait()

    # Re-locate the filename field and give it focus, then press Enter
    # to trigger the dialog's default Save button.
    filename_field = device.find(role='text', editable=True, contains=binding['file_name'])
    if filename_field is None:
        filename_field = device.find(role='text', editable=True)
    if filename_field is None:
        raise RuntimeError("Filename field not found after selecting Desktop")
    device.click(index=filename_field)
    device.press('enter')
    device.wait()

    # Handle a possible "Keep Current Format" dialog
    keep_format = device.find(name='Use ODF Text Document!')
    if keep_format is not None:
        device.click(index=keep_format)
        device.wait()

    return True
