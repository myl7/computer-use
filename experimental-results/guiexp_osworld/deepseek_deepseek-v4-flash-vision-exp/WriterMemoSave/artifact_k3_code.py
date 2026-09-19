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

    # Verify the dialog appeared
    save_button = device.find(name='Save', role='push-button')
    if save_button is None:
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
        save_button = device.find(name='Save', role='push-button')
        if save_button is None:
            raise RuntimeError("Save dialog did not open")

    # Fill filename field with the stem (extension is auto-added)
    file_stem = binding['file_name'].rsplit('.', 1)[0]
    filename_field = device.find(role='text', editable=True, contains='Untitled')
    if filename_field is None:
        filename_field = device.find(role='text', editable=True)
    if filename_field is None:
        raise RuntimeError("Could not find filename field in Save dialog")
    device.input_text(file_stem, index=filename_field)

    # Select Desktop as the save location
    desktop = device.find(name='Desktop')
    if desktop is None:
        desktop = device.find(name='Desktop', role='label')
    if desktop is None:
        raise RuntimeError("Desktop location not found in save dialog")
    device.click(index=desktop)

    # Click Save (re-locate because the screen may have refreshed)
    save_button = device.find(name='Save', role='push-button')
    if save_button is None:
        raise RuntimeError("Save button not found in save dialog")
    device.click(index=save_button)
    device.wait()

    return True
