import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Document file name ending in .odt, one string"
    },
    "title": {
        "type": "string",
        "description": "The title line, one string"
    },
    "body": {
        "type": "string",
        "description": "The body sentence for the third line, one string"
    }
}

def program(device, binding: dict) -> bool:
    # Ensure LibreOffice Writer is open and focused.
    doc = device.find(role='document-text', editable=True)
    if doc is None:
        device.open_app("LibreOffice Writer")
        device.wait()
        doc = device.find(role='document-text', editable=True)
        if doc is None:
            raise RuntimeError("No editable document text element found")

    # Click into the document to ensure focus.
    device.click(index=doc)

    # Type the title line.
    device.input_text(binding['title'], index=doc)

    # Press Enter for the title line, then Enter again for the empty line.
    device.press("enter")
    device.press("enter")

    # Type the body sentence at the current caret.
    body = binding["body"]
    if not body:
        raise ValueError("binding['body'] is empty; nothing to type")
    device.type_at_caret(body)

    # Open Save As dialog.
    device.hotkey("ctrl", "shift", "s")

    # Wait for the Save dialog to appear.
    device.settle(1.0)

    # Find the filename field. It is an editable text field.
    filename_field = device.find(role='text', editable=True, text='Untitled 1')
    if filename_field is None:
        filename_field = device.find(role='text', editable=True)
    if filename_field is None:
        raise LookupError("Save dialog: editable filename field not found")

    # Type the filename stem (without .odt extension) into the field.
    target = binding['file_name'].rsplit('.', 1)[0]
    device.input_text(target, index=filename_field)

    # Find and click the Desktop location in the file picker.
    desktop = device.find(name='Desktop', role='label')
    if desktop is None:
        desktop = device.find(name='Desktop')
    if desktop is None:
        raise RuntimeError("Desktop location not found in save dialog")
    device.click(index=desktop)

    # Find and click the Save button.
    save_btn = device.find(name="Save", role="push-button")
    if save_btn is None:
        raise RuntimeError("Save button not found on the current screen")
    device.click(index=save_btn)

    # Wait for the save to complete.
    device.settle(1.0)

    return True
