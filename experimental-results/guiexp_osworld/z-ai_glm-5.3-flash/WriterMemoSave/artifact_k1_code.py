PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Target file name for the saved memo, ending in .odt",
        "required": True,
    },
    "title": {
        "type": "string",
        "description": "Title text typed on line one of the memo",
        "required": True,
    },
    "body": {
        "type": "string",
        "description": "Body sentence typed on the third line of the memo",
        "required": True,
    },
}


def _find_writer_doc(device):
    """Locate the editable document-text area of the open LibreOffice Writer window."""
    return device.find(role="document-text", editable=True)


def _save_dialog_visible(device):
    """The GTK Save dialog shows a 'Name:' label and a Cancel push-button."""
    if device.find(name="Name:", role="label") is not None:
        return True
    if device.find(name="Cancel", role="push-button") is not None:
        return True
    return False


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]
    stem = file_name.rsplit(".", 1)[0]

    # ------------------------------------------------------------------
    # 1. Make sure an editable Writer document is available and focused.
    #    Match only by role/editable: the window title ('Untitled 1 ...')
    #    changes after saving, so it must never be part of the lookup.
    # ------------------------------------------------------------------
    doc = _find_writer_doc(device)
    if doc is None:
        device.open_app("LibreOffice Writer")
        device.wait()
        doc = _find_writer_doc(device)
    if doc is None:
        raise RuntimeError("No editable LibreOffice Writer document-text area found")

    # ------------------------------------------------------------------
    # 2. Line 1: the title.
    # ------------------------------------------------------------------
    device.input_text(title, index=doc)
    device.settle(1)

    # ------------------------------------------------------------------
    # 3. Press Enter twice: line 2 stays empty, caret moves to line 3.
    # ------------------------------------------------------------------
    device.press("enter")
    device.press("enter")

    # ------------------------------------------------------------------
    # 4. Line 3: the body sentence (re-locate the document area first).
    # ------------------------------------------------------------------
    doc = _find_writer_doc(device)
    if doc is None:
        raise RuntimeError("LibreOffice Writer editable document-text area not found")
    device.input_text(body, index=doc)
    device.settle(1)

    # ------------------------------------------------------------------
    # 5. Ctrl+S opens the Save dialog (document window must have focus).
    # ------------------------------------------------------------------
    device.hotkey("ctrl", "s")
    device.settle(1.5)

    if not _save_dialog_visible(device):
        # one retry in case the dialog was slow to appear
        device.hotkey("ctrl", "s")
        device.settle(1.5)
    if not _save_dialog_visible(device):
        raise RuntimeError("Save dialog did not open after Ctrl+S")

    # ------------------------------------------------------------------
    # 6. Switch the save folder to Desktop via the places sidebar.
    # ------------------------------------------------------------------
    desktop = device.find(name="Desktop", clickable=True)
    if desktop is None:
        raise LookupError("Save dialog: no clickable 'Desktop' place entry found")
    device.click(desktop)
    device.settle(1)  # let the file chooser switch to the Desktop folder

    # ------------------------------------------------------------------
    # 7. Fill the Name field with the full file name INCLUDING the .odt
    #    extension. The field is found semantically (editable text field,
    #    defaulting to 'Untitled ...'), never by a stale index.
    # ------------------------------------------------------------------
    name_field = device.find(role="text", editable=True, contains="Untitled")
    if name_field is None:
        name_field = device.find(role="text", editable=True)
    if name_field is None:
        raise RuntimeError("Save-dialog filename field not found")
    device.input_text(file_name, index=name_field)
    device.settle(1)

    # ------------------------------------------------------------------
    # 8. Confirm the save.
    # ------------------------------------------------------------------
    save_btn = device.find(name="Save", role="push-button", clickable=True)
    if save_btn is None:
        raise LookupError("Save dialog: no clickable 'Save' push-button found")
    device.click(save_btn)
    device.settle(1.5)

    # If a format-confirmation dialog appears, keep the ODF format.
    odf_btn = device.find(name="Use ODF Format!", role="push-button")
    if odf_btn is not None:
        device.click(odf_btn)
        device.settle(1.5)

    # ------------------------------------------------------------------
    # 9. Verify: the Writer window title should now show the saved name.
    # ------------------------------------------------------------------
    device.settle(1)
    window = device.active_window() or ""
    if stem not in window:
        raise RuntimeError(
            "Save appears to have failed; active window title: %r" % window
        )

    return True
