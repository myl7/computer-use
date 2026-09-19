PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "The document file name, ending in .odt",
        "required": True
    },
    "title": {
        "type": "string",
        "description": "The title to place on the first line",
        "required": True
    },
    "body": {
        "type": "string",
        "description": "The body sentence to place on the third line",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    # Validate binding
    for key in ("file_name", "title", "body"):
        if key not in binding:
            raise KeyError(f"Missing required binding key: {key}")
    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]
    if not isinstance(file_name, str) or not file_name.endswith(".odt"):
        raise ValueError("file_name must be a string ending in .odt")
    if not isinstance(title, str):
        raise ValueError("title must be a string")
    if not isinstance(body, str):
        raise ValueError("body must be a string")

    # Ensure LibreOffice Writer is active
    win = device.active_window() or ""
    if "LibreOffice Writer" not in win and "LibreOffice Document" not in win:
        device.open_app("LibreOffice Writer")
        device.wait()
        device.settle(1.0)

    # Ensure a document is open
    doc_idx = device.find(role="document-text", editable=True)
    if doc_idx is None:
        doc_idx = device.find(role="paragraph", editable=True)
    if doc_idx is None:
        # Try to create a new document
        device.hotkey("ctrl", "n")
        device.wait()
        device.settle(1.0)
        doc_idx = device.find(role="document-text", editable=True)
        if doc_idx is None:
            doc_idx = device.find(role="paragraph", editable=True)
    if doc_idx is None:
        raise RuntimeError("Could not find an editable document text area in LibreOffice Writer")

    # Type title on the first line
    device.input_text(title, index=doc_idx)
    device.settle(0.5)

    # Blank line: press Enter twice (end title line, then create empty line)
    device.press("enter")
    device.press("enter")
    device.settle(0.5)

    # Type body sentence at the caret (third line)
    device.type_at_caret(body)
    device.settle(0.5)

    # Open Save As dialog
    device.hotkey("ctrl", "shift", "s")
    device.wait()

    # Wait for the filename field to appear
    filename_idx = None
    for _ in range(20):
        filename_idx = device.find(role="text", editable=True)
        if filename_idx is not None:
            break
        device.settle(0.5)

    # Fallback: use File menu if shortcut didn't work
    if filename_idx is None:
        file_menu = device.find(name="File", role="menu")
        if file_menu is not None:
            device.click(index=file_menu)
            device.settle(0.5)
            save_as = device.find(name="Save As...", role="menu-item")
            if save_as is None:
                save_as = device.find(contains="Save As", role="menu-item")
            if save_as is not None:
                device.click(index=save_as)
                device.settle(1.0)
                for _ in range(20):
                    filename_idx = device.find(role="text", editable=True)
                    if filename_idx is not None:
                        break
                    device.settle(0.5)

    if filename_idx is None:
        raise RuntimeError("Save As dialog did not appear or filename field not found")

    # Type the full file name including .odt
    device.input_text(file_name, index=filename_idx)
    device.settle(0.5)

    # Select the Desktop folder
    desktop_idx = device.find(name="Desktop", clickable=True)
    if desktop_idx is None:
        desktop_idx = device.find(name="Desktop")
    if desktop_idx is None:
        desktop_idx = device.find(name="Desktop", role="label")
    if desktop_idx is None:
        desktop_idx = device.find(contains="Desktop")
    if desktop_idx is None:
        raise RuntimeError("Desktop location not found in save dialog")
    device.click(index=desktop_idx)
    device.settle(1.0)

    # Click Save
    save_idx = device.find(name="Save", role="push-button", clickable=True)
    if save_idx is None:
        save_idx = device.find(name="Save", role="push-button")
    if save_idx is None:
        save_idx = device.find(name="Save")
    if save_idx is None:
        raise RuntimeError("Save button not found in save dialog")
    device.click(index=save_idx)
    device.settle(1.0)

    # Handle any confirmation dialogs (format, replace, etc.)
    for _ in range(3):
        device.settle(0.5)
        handled = False
        for label in ("Use ODF Format!", "Use ODF Format", "Keep Current Format", "Replace"):
            idx = device.find(name=label, role="push-button")
            if idx is None:
                idx = device.find(name=label)
            if idx is not None:
                device.click(index=idx)
                device.settle(1.0)
                handled = True
                break
        if not handled:
            break

    return True
