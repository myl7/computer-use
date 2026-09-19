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


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _read_text(device, idx):
    for el in _elements(device):
        if el.get("index") == idx:
            return el.get("text") or el.get("name") or ""
    return ""


def _save_dialog_visible(device):
    """True when the LibreOffice/GTK Save dialog is the active screen."""
    win = device.active_window() or ""
    if "| Save" in win or win.rstrip().endswith("Save"):
        return True
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


def _dialog_name_field(device):
    """Index of the Save dialog's Name entry.

    The element list merges the Writer window and the dialog; the window's
    own editable widgets (release-note infobar) must never be picked.
    """
    # The GTK dialog pre-fills its Name entry with "Untitled ...".
    el = device.find(role="text", editable=True, contains="Untitled")
    if el is not None:
        return el
    # Fallback: take the last editable text that is not the first-run
    # release-note infobar (dialog widgets never carry that text).
    cands = [e for e in _elements(device)
             if e.get("role") == "text" and e.get("editable")]
    cands = [e for e in cands
             if "running version" not in (e.get("text") or "")
             and "release notes" not in (e.get("text") or "").lower()]
    if cands:
        return cands[-1]["index"]
    return None


def _dialog_save_button(device, anchor_index=None):
    """Index of the Save dialog's confirm push-button.

    Both the Writer toolbar and the dialog own a 'Save' push-button; only
    the dialog's may be clicked.  The dialog's is the one flanked by a
    Cancel button; index order relative to the dialog's Name field is used
    as a secondary signal.
    """
    els = _elements(device)
    saves = [e for e in els
             if e.get("role") == "push-button" and e.get("name") == "Save"]
    if not saves:
        saves = [e for e in els
                 if e.get("role") in ("push button", "button")
                 and e.get("name") == "Save"]
    if not saves:
        return None
    if len(saves) == 1:
        return saves[0]["index"]
    cancels = [e["index"] for e in els
               if e.get("role") == "push-button" and e.get("name") == "Cancel"]
    if cancels:
        near = [e for e in saves
                if any(abs(e["index"] - c) <= 25 for c in cancels)]
        if near:
            return near[-1]["index"]
    if anchor_index is not None:
        later = [e for e in saves if e.get("index", 0) > anchor_index]
        if later:
            return later[-1]["index"]
    return saves[-1]["index"]


def _desktop_entry(device):
    for criteria in ({"name": "Desktop", "clickable": True},
                     {"name": "Desktop", "role": "table-cell"},
                     {"name": "Desktop"}):
        el = device.find(**criteria)
        if el is not None:
            return el
    return None


def _click_first_button(device, names):
    for nm in names:
        btn = device.find(name=nm, role="push-button", clickable=True)
        if btn is not None:
            device.click(index=btn)
            device.settle(2)
            return True
    return False


def program(device, binding: dict) -> bool:
    for key in ("file_name", "title", "body"):
        if key not in binding:
            raise KeyError("binding missing required key: %r" % key)

    file_name = binding["file_name"]
    title = binding["title"]
    body = binding["body"]
    base = file_name.rsplit(".", 1)[0]

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
    # 6) File name (including the .odt extension) into the DIALOG's Name
    #    field -- never into the Writer window's own editable widgets.
    # ------------------------------------------------------------------
    name_field = _dialog_name_field(device)
    if name_field is None:
        raise RuntimeError("Save-dialog filename field not found")
    device.input_text(file_name, index=name_field)
    device.settle(0.5)

    # ------------------------------------------------------------------
    # 7) Switch the save folder to Desktop via the places entry.
    # ------------------------------------------------------------------
    desktop = _desktop_entry(device)
    if desktop is None:
        raise RuntimeError("Save dialog: no 'Desktop' place entry found")
    device.click(index=desktop)
    device.settle(1)

    # Defensive: make sure the file name survived the folder switch -- only
    # the dialog's own Name field is inspected and re-typed.
    name_field = _dialog_name_field(device)
    if name_field is not None:
        current = _read_text(device, name_field)
        if base not in current and file_name not in current:
            device.input_text(file_name, index=name_field)
            device.settle(0.5)

    # ------------------------------------------------------------------
    # 8) Confirm the save with the DIALOG's Save button.  The Writer
    #    toolbar also exposes a 'Save' push-button; clicking that one while
    #    the modal dialog is up does nothing (the earlier failure).
    # ------------------------------------------------------------------
    save_btn = _dialog_save_button(device, anchor_index=name_field)
    if save_btn is not None:
        device.click(index=save_btn)
    else:
        if name_field is not None:
            device.click(index=name_field)
            device.settle(0.5)
        device.press("enter")
    device.settle(2)

    # ------------------------------------------------------------------
    # 9) Confirm completion: the Writer window title now shows the file
    #    name.  Absorb format/overwrite prompts and retry a stuck dialog.
    # ------------------------------------------------------------------
    deadline = time.time() + 30
    retries = 0
    while time.time() < deadline:
        win = device.active_window() or ""
        if ((file_name in win) or (base and base in win)) and "| Save" not in win:
            return True

        if _click_first_button(device, ("Use ODF Format!",)):
            continue
        if _click_first_button(device, ("Overwrite", "Replace")):
            continue
        yes = device.find(name="Yes", role="push-button", clickable=True)
        if yes is not None and (device.find(contains="already exists") is not None
                                or device.find(contains="verwrite") is not None):
            device.click(index=yes)
            device.settle(2)
            continue

        if _save_dialog_visible(device) and retries < 2:
            nf = _dialog_name_field(device)
            btn = _dialog_save_button(device, anchor_index=nf)
            if btn is not None:
                device.click(index=btn)
            elif nf is not None:
                device.click(index=nf)
                device.settle(0.5)
                device.press("enter")
            device.settle(2)
            retries += 1
            continue

        time.sleep(0.5)

    win = device.active_window() or ""
    if "| Save" in win or device.find(name="Name:", role="label") is not None:
        raise RuntimeError("Save dialog still open; the save was not confirmed")
    if "LibreOffice" not in win:
        raise RuntimeError("Unexpected active window after save: %r" % (win,))
    # Dialog closed and Writer is active again: best-effort success.
    return True
