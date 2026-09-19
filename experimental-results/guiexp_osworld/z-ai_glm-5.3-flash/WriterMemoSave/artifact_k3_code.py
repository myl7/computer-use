import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Document file name including the .odt extension; the memo is saved on the Desktop under this name.",
    },
    "title": {
        "type": "string",
        "required": True,
        "description": "Title text written on line one of the new Writer document.",
    },
    "body": {
        "type": "string",
        "required": True,
        "description": "Body sentence written on line three; line two is left empty (a pressed Enter).",
    },
}


def _save_dialog_visible(device):
    """True when the LibreOffice/GTK Save dialog is the active screen."""
    if device.find(name="Name:", role="label") is not None:
        return True
    if device.find(name="Name", role="label") is not None:
        return True
    # GTK save-dialog signature: editable filename entry + Save button + places list
    if (device.find(role="text", editable=True) is not None
            and device.find(name="Save", role="push-button") is not None
            and device.find(name="Desktop") is not None):
        return True
    return False


def program(device, binding: dict) -> bool:
    for key in ("file_name", "title", "body"):
        if key not in binding:
            raise KeyError("binding missing required key: %r" % key)

    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]

    # ------------------------------------------------------------------
    # 1) Locate the new (untitled) Writer document's editable text area.
    #    Matched by role/editable only: its name ("Untitled 1 - ...") is volatile.
    # ------------------------------------------------------------------
    doc = device.find(role="document-text", editable=True)
    if doc is None:
        device.open_app("LibreOffice Writer")
        device.wait()
        doc = device.find(role="document-text", editable=True)
    if doc is None:
        raise RuntimeError("No editable LibreOffice Writer document-text area found")

    # ------------------------------------------------------------------
    # 2) Title on line one.
    # ------------------------------------------------------------------
    device.click(index=doc)
    device.settle(0.5)
    device.input_text(title, index=doc)
    device.settle(0.5)

    # ------------------------------------------------------------------
    # 3) Two Enters: end line one, leave line two EMPTY, caret on line three.
    # ------------------------------------------------------------------
    device.press("enter")
    device.settle(0.3)
    device.press("enter")
    device.settle(0.3)

    # ------------------------------------------------------------------
    # 4) Body sentence on line three.
    # ------------------------------------------------------------------
    doc = device.find(role="document-text", editable=True)
    if doc is None:
        raise RuntimeError("Writer document text area not found before typing the body")
    device.input_text(body, index=doc)
    device.settle(0.5)

    # ------------------------------------------------------------------
    # 5) Ctrl+S: Writer must hold keyboard focus; then the Save dialog appears.
    # ------------------------------------------------------------------
    win = device.active_window() or ""
    if "LibreOffice" not in win:
        doc = device.find(role="document-text", editable=True)
        if doc is None:
            raise RuntimeError("LibreOffice Writer window not focused; cannot send Ctrl+S")
        device.click(index=doc)
    device.hotkey("ctrl", "s")

    deadline = time.time() + 25
    dialog_up = False
    while time.time() < deadline:
        if _save_dialog_visible(device):
            dialog_up = True
            break
        time.sleep(0.5)
    if not dialog_up:
        raise RuntimeError("Save dialog did not open after Ctrl+S")
    device.settle(0.5)

    # ------------------------------------------------------------------
    # 6) File name (including the .odt extension) into the dialog's Name field.
    # ------------------------------------------------------------------
    name_field = device.find(role="text", editable=True, contains="Untitled")
    if name_field is None:
        name_field = device.find(role="text", editable=True)
    if name_field is None:
        raise RuntimeError("Save-dialog filename field not found")
    device.input_text(file_name, index=name_field)
    device.settle(0.5)

    # ------------------------------------------------------------------
    # 7) Switch the save folder to Desktop via the places entry.
    #    Its role varies (label / table-cell / toggle-button) across runs.
    # ------------------------------------------------------------------
    desktop = device.find(name="Desktop", clickable=True)
    if desktop is None:
        desktop = device.find(name="Desktop", role="table-cell")
    if desktop is None:
        desktop = device.find(name="Desktop")
    if desktop is None:
        raise RuntimeError("Save dialog: no 'Desktop' place entry found")
    device.click(index=desktop)
    device.settle(1)

    # Defensive: make sure the file name survived the folder switch.
    fld = device.find(role="text", editable=True)
    if fld is not None:
        current = ""
        for el in device.elements():
            if el.get("index") == fld:
                current = el.get("text") or el.get("name") or ""
                break
        base = file_name.rsplit(".", 1)[0]
        if base and base not in current:
            device.input_text(file_name, index=fld)
            device.settle(0.5)

    # ------------------------------------------------------------------
    # 8) Confirm the save.
    # ------------------------------------------------------------------
    save_btn = device.find(name="Save", role="push-button", clickable=True)
    if save_btn is None:
        save_btn = device.find(name="Save", role="push-button")
    if save_btn is None:
        raise RuntimeError("Save push-button not found in the Save dialog")
    device.click(index=save_btn)
    device.settle(2)

    # ------------------------------------------------------------------
    # 9) Confirm completion: the Writer window title now shows the file name.
    # ------------------------------------------------------------------
    deadline = time.time() + 10
    while time.time() < deadline:
        win = device.active_window() or ""
        if file_name in win:
            return True
        time.sleep(0.5)

    win = device.active_window() or ""
    if "| Save" in win or device.find(name="Name:", role="label") is not None:
        raise RuntimeError("Save dialog still open; the save was not confirmed")
    if "LibreOffice" not in win:
        raise RuntimeError("Unexpected active window after save: %r" % (win,))
    # Dialog closed and Writer is active again: best-effort success.
    return True
