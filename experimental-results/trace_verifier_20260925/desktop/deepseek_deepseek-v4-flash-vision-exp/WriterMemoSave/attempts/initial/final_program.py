import time


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


def _find_first(device, candidates):
    """Return the first element index matching any candidate criteria."""
    for kwargs in candidates:
        try:
            idx = device.find(**kwargs)
        except TypeError:
            idx = None
        if idx is not None:
            return idx
    return None


def _find_document(device):
    return _find_first(device, [
        {"role": "document-text", "editable": True},
        {"role": "paragraph", "editable": True},
        {"role": "text", "editable": True},
    ])


def _find_name_field(device):
    return _find_first(device, [
        {"role": "text", "editable": True, "contains": "Untitled"},
        {"role": "entry", "editable": True},
        {"role": "text", "editable": True},
    ])


def _find_dialog_save(device, after_index=None):
    """Find the Save push-button that belongs to the open dialog (not the
    toolbar Save).  Dialog elements are listed after the main window ones, so
    take the Save push-button with the largest index that comes after the
    filename field."""
    best = None
    try:
        elements = device.elements()
    except Exception:
        elements = []
    for el in elements:
        if (el.get("role") or "") != "push-button":
            continue
        if (el.get("name") or "") != "Save":
            continue
        idx = el.get("index")
        if idx is None:
            continue
        if after_index is not None and idx <= after_index:
            continue
        if best is None or idx > best:
            best = idx
    if best is None and after_index is None:
        best = device.find(name="Save", role="push-button")
    return best


def _dismiss_post_save_dialogs(device, rounds=6):
    """After pressing Save, LibreOffice may pop a modal 'Question' dialog
    asking which file format to use, an overwrite confirmation, or a file-lock
    dialog.  Handle those before returning."""
    for _ in range(rounds):
        device.wait()

        # ODF format / overwrite confirmation
        idx = _find_first(device, [
            {"name": "Use ODF Format!", "role": "push-button"},
            {"name": "Use ODF Format", "role": "push-button"},
            {"name": "ODF Format!", "role": "push-button"},
            {"name": "Keep Current Format!", "role": "push-button"},
            {"name": "Keep Current Format", "role": "push-button"},
            {"role": "push-button", "contains": "ODF"},
        ])
        if idx is None:
            idx = _find_first(device, [
                {"name": "Yes", "role": "push-button"},
                {"name": "Overwrite", "role": "push-button"},
                {"role": "push-button", "contains": "Overwrite"},
            ])
        if idx is not None:
            device.click(index=idx)
            device.wait()
            continue

        # File-lock dialog: a label/alert containing "locked" and a
        # push-button named "Save".  Use the label as an anchor so we don't
        # accidentally click a toolbar Save button.
        locked_label = _find_first(device, [
            {"role": "label", "contains": "locked"},
            {"role": "alert", "contains": "locked"},
            {"contains": "locked for editing"},
        ])
        if locked_label is not None:
            save_btn = _find_dialog_save(device, after_index=locked_label)
            if save_btn is not None:
                device.click(index=save_btn)
                device.wait()
                continue

        # No more dialogs to dismiss.
        return


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]

    # Focus the Writer document body
    doc = _find_document(device)
    if doc is None:
        raise RuntimeError("No editable Writer document body found")
    device.click(index=doc)

    # Title line
    device.input_text(title, index=doc)

    # Empty line (two Enters: one ends the title line, one makes it empty)
    device.press("enter")
    device.press("enter")

    # Body line
    device.type_at_caret(body)

    # Open Save As
    device.hotkey("ctrl", "shift", "s")
    device.wait()

    name_field = _find_name_field(device)
    if name_field is None:
        # Fallback: File menu -> Save As
        file_menu = _find_first(device, [
            {"name": "File", "role": "menu"},
            {"name": "File"},
        ])
        if file_menu is not None:
            device.click(index=file_menu)
            device.wait()
            save_as = _find_first(device, [
                {"name": "Save As...", "role": "menu-item"},
                {"role": "menu-item", "contains": "Save As"},
            ])
            if save_as is not None:
                device.click(index=save_as)
                device.wait()
                name_field = _find_name_field(device)
    if name_field is None:
        raise RuntimeError("Save As dialog did not open")

    # Fill the Name field with the full file name (including .odt)
    device.input_text(file_name, index=name_field)

    # Choose the Desktop folder
    desktop = _find_first(device, [
        {"name": "Desktop"},
        {"name": "Desktop", "role": "label"},
        {"role": "label", "contains": "Desktop"},
    ])
    if desktop is not None:
        device.click(index=desktop)
        device.wait()

    # Re-locate the filename field (the screen may have refreshed)
    current_name = _find_first(device, [
        {"role": "text", "editable": True, "contains": file_name},
        {"role": "entry", "editable": True},
        {"role": "text", "editable": True},
    ])
    anchor = current_name if current_name is not None else name_field

    # Confirm with the dialog's own Save button (never the toolbar Save)
    save_idx = _find_dialog_save(device, after_index=anchor)
    if save_idx is not None:
        device.click(index=save_idx)
    else:
        device.press("enter")
    device.wait()

    # Handle the "Use ODF Format!" / overwrite / file-lock confirmation dialog
    _dismiss_post_save_dialogs(device)
    device.wait()

    return True
