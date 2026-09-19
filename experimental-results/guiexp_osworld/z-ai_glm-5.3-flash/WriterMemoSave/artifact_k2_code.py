import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Name of the document to save on the Desktop, including the .odt extension",
    },
    "title": {
        "type": "string",
        "required": True,
        "description": "Title text placed on line one of the memo",
    },
    "body": {
        "type": "string",
        "required": True,
        "description": "Body sentence placed on line three of the memo",
    },
}


def program(device, binding: dict) -> bool:
    """Draft a three-line memo in LibreOffice Writer and save it on the Desktop."""
    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]

    def find_doc():
        # The Writer document area is identified by role/editable only; its
        # name ("Untitled 1 ...") changes once the file is saved.
        return device.find(role="document-text", editable=True)

    # ------------------------------------------------------------------ #
    # 1. Make sure a new, empty LibreOffice Writer document is open.     #
    # ------------------------------------------------------------------ #
    doc = find_doc()
    if doc is None:
        device.open_app("LibreOffice Writer")
        device.wait()
        device.settle(2)
        doc = find_doc()
        if doc is None:
            # Writer may be showing the Start Center: create a new document.
            device.hotkey("ctrl", "n")
            device.settle(2)
            doc = find_doc()
    if doc is None:
        raise RuntimeError("No editable LibreOffice Writer document area found")

    # ------------------------------------------------------------------ #
    # 2. Line 1: the title.                                              #
    # ------------------------------------------------------------------ #
    device.click(doc)
    device.input_text(title, index=doc)
    device.settle(0.5)

    # ------------------------------------------------------------------ #
    # 3. Empty line 2 (a pressed Enter, not a space); caret moves to     #
    #    the start of line 3.                                            #
    # ------------------------------------------------------------------ #
    device.press("enter")
    device.press("enter")

    # ------------------------------------------------------------------ #
    # 4. Line 3: the body sentence, typed at the caret.                  #
    # ------------------------------------------------------------------ #
    doc = find_doc()
    if doc is None:
        raise RuntimeError("LibreOffice Writer document area lost before typing the body")
    device.input_text(body, index=doc)
    device.settle(0.5)

    # ------------------------------------------------------------------ #
    # 5. Ctrl+S opens the Save dialog (document is still untitled).      #
    # ------------------------------------------------------------------ #
    device.hotkey("ctrl", "s")
    device.settle(1.5)

    dialog_open = False
    for _ in range(6):
        if (device.find(name="Name:", role="label") is not None
                or device.find(role="text", editable=True) is not None):
            dialog_open = True
            break
        time.sleep(1)
    if not dialog_open:
        raise RuntimeError("Save dialog did not open after Ctrl+S")

    # ------------------------------------------------------------------ #
    # 6. Switch the save folder to the Desktop via the places entry.     #
    #    (Its role flips between table-cell/toggle-button/label, so      #
    #    match only on the name and clickability.)                       #
    # ------------------------------------------------------------------ #
    desktop = device.find(name="Desktop", clickable=True)
    if desktop is None:
        raise LookupError("Save dialog: no clickable 'Desktop' place entry found")
    device.click(desktop)
    device.settle(1)

    # ------------------------------------------------------------------ #
    # 7. Type the file name (including the .odt extension) into the      #
    #    Name field, the only editable text element of the dialog.       #
    # ------------------------------------------------------------------ #
    name_field = device.find(role="text", editable=True)
    if name_field is None:
        raise RuntimeError("Save-dialog filename field not found")
    device.input_text(file_name, index=name_field)
    device.settle(0.5)

    # ------------------------------------------------------------------ #
    # 8. Confirm the save.                                               #
    # ------------------------------------------------------------------ #
    save_btn = device.find(name="Save", role="push-button", clickable=True)
    if save_btn is None:
        raise RuntimeError("Save dialog confirm button not found")
    device.click(save_btn)
    device.wait()
    device.settle(1)

    # Defensive: if a format-choice dialog pops up, keep ODF (.odt).
    odf_btn = device.find(contains="ODF Format", role="push-button", clickable=True)
    if odf_btn is not None:
        device.click(odf_btn)
        device.settle(1)

    # ------------------------------------------------------------------ #
    # 9. Verify the save completed: the Writer window title now shows    #
    #    the saved file name.                                            #
    # ------------------------------------------------------------------ #
    stem = file_name.rsplit(".", 1)[0]
    saved = False
    last_title = ""
    for _ in range(6):
        last_title = device.active_window() or ""
        if file_name in last_title or stem in last_title:
            saved = True
            break
        time.sleep(1)
    if not saved:
        raise RuntimeError(
            "Save did not complete; active window title: %r" % (last_title,)
        )

    return True
