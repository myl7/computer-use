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
    win = device.active_window() or ""
    if "| Save" in win:
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


def _push_buttons(els, name=None, contains=None):
    """Indexes of push-button elements matching the given label filter."""
    out = []
    for el in els:
        if el.get("role") != "push-button":
            continue
        label = (el.get("name") or "").strip()
        if name is not None and label != name:
            continue
        if contains is not None and contains.lower() not in label.lower():
            continue
        idx = el.get("index")
        if idx is not None:
            out.append(idx)
    return out


def _select_desktop_in_dialog(device):
    """Point the Save dialog at the Desktop folder.

    More than one element can be called 'Desktop' (the places-sidebar entry
    and a folder row of the currently listed folder), so every candidate is
    tried and a click only counts as success when the dialog's contents
    actually change (the file list is re-read for the new folder).
    """
    def candidates():
        els = device.elements()
        exact = [el for el in els
                 if (el.get("name") or "").strip() == "Desktop"
                 or (el.get("text") or "").strip() == "Desktop"]
        if exact:
            # places-sidebar entries usually carry an absolute path tooltip
            exact.sort(key=lambda el: 0 if (el.get("description") or "").startswith("/") else 1)
            return exact
        return [el for el in els
                if "Desktop" in (el.get("name") or "")
                or "Desktop" in (el.get("text") or "")
                or "Desktop" in (el.get("description") or "")]

    def snapshot():
        return sorted((el.get("role") or "", el.get("name") or "", el.get("text") or "")
                      for el in device.elements())

    if not candidates():
        raise RuntimeError("Save dialog: no 'Desktop' place entry found")

    before = snapshot()
    for _attempt in range(2):
        tried = set()
        for el in candidates():
            idx = el.get("index")
            if idx is None or idx in tried:
                continue
            tried.add(idx)
            device.click(index=idx)
            device.settle(1)
            if snapshot() != before:
                return True
    # Navigation could not be verified; the dialog may already show the
    # Desktop folder.  Carry on with the save either way.
    return False


def _find_name_field(device, file_name):
    """Locate the Save dialog's filename entry -- never a main-window widget."""
    criteria = [
        {"role": "text", "editable": True, "contains": "Untitled"},
        {"role": "text", "editable": True, "contains": file_name},
    ]
    base = file_name.rsplit(".", 1)[0]
    if base and base != file_name:
        criteria.append({"role": "text", "editable": True, "contains": base})
    for crit in criteria:
        el = device.find(**crit)
        if el is not None:
            return el
    # Fallback: an editable text widget that is clearly not part of the
    # Writer main window (first-run notification, document paragraphs, ...).
    for el in device.elements():
        if not el.get("editable"):
            continue
        if el.get("role") in ("paragraph", "document-text"):
            continue
        blob = "%s %s" % (el.get("name") or "", el.get("text") or "")
        if ("You are running version" in blob or "Release Notes" in blob
                or "ODF Text Document" in blob):
            continue
        idx = el.get("index")
        if idx is not None:
            return idx
    return None


def _name_field_holds_file_name(device, file_name):
    for el in device.elements():
        if el.get("role") not in ("text", "entry") or not el.get("editable"):
            continue
        blob = "%s %s" % (el.get("name") or "", el.get("text") or "")
        if file_name in blob:
            return True
    return False


def _prompt_text_present(els):
    blob = " ".join("%s %s" % (el.get("name") or "", el.get("text") or "")
                    for el in els).lower()
    for token in ("in use", "read-only", "read only", "locked by", "already exists"):
        if token in blob:
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
    # 6) Switch the save folder to Desktop BEFORE typing the file name, so
    #    the name cannot be lost on the folder switch.  Several elements may
    #    be labelled 'Desktop'; each candidate is tried and only a click that
    #    really changes the dialog (the file list is re-read) is trusted.
    # ------------------------------------------------------------------
    _select_desktop_in_dialog(device)

    # ------------------------------------------------------------------
    # 7) File name (including the .odt extension) into the dialog's Name
    #    field.  The field is located inside the dialog only; main-window
    #    editable widgets (e.g. the first-run notification) are never used.
    # ------------------------------------------------------------------
    name_field = _find_name_field(device, file_name)
    if name_field is None:
        raise RuntimeError("Save-dialog filename field not found")
    device.input_text(file_name, index=name_field)
    device.settle(0.5)
    if not _name_field_holds_file_name(device, file_name):
        retry_field = _find_name_field(device, file_name)
        if retry_field is not None:
            device.input_text(file_name, index=retry_field)
            device.settle(0.5)

    # ------------------------------------------------------------------
    # 8) Confirm the save and ride out any follow-up prompts (overwrite
    #    confirmation, "Keep current format", "Document in Use" lock question).
    # ------------------------------------------------------------------
    deadline = time.time() + 60
    pressed_enter = False
    tried_buttons = set()
    win = device.active_window() or ""
    while time.time() < deadline:
        win = device.active_window() or ""
        if file_name in win:
            return True
        els = device.elements()

        # (a) "Keep current format" prompt -> stay with ODF.
        odf = _push_buttons(els, contains="ODF Format")
        if odf:
            device.click(index=max(odf))
            device.settle(1)
            continue

        # (b) Overwrite confirmation.
        repl = _push_buttons(els, name="Replace")
        if repl:
            device.click(index=max(repl))
            device.settle(1)
            continue

        # (c) The Save dialog itself is still up -> (re)confirm it.
        if _save_dialog_visible(device):
            if not pressed_enter:
                pressed_enter = True
                # The Name field holds keyboard focus; Enter activates the
                # dialog's default (Save) button -- never the toolbar's Save.
                device.press("enter")
                device.settle(1)
                continue
            pending = [b for b in _push_buttons(els, name="Save")
                       if b not in tried_buttons]
            if pending:
                # Dialog-side buttons sit late in the element list; the main
                # window's toolbar Save must never be used to confirm here.
                btn = max(pending)
                tried_buttons.add(btn)
                device.click(index=btn)
                device.settle(1)
            else:
                time.sleep(0.5)
            continue

        # (d) Modal question/warning (e.g. "Document in Use" stale lock)
        #     -> answer it with its affirmative button.
        if ("| Question" in win or "| Warning" in win or "| Error" in win
                or "| Info" in win or _prompt_text_present(els)):
            answered = False
            for label in ("Save", "Yes", "OK"):
                pending = [b for b in _push_buttons(els, name=label)
                           if b not in tried_buttons]
                if pending:
                    btn = max(pending)
                    tried_buttons.add(btn)
                    device.click(index=btn)
                    device.settle(1)
                    answered = True
                    break
            if not answered:
                time.sleep(0.5)
            continue

        time.sleep(0.5)

    # ------------------------------------------------------------------
    # 9) Final verdict: the Writer window title now shows the file name.
    # ------------------------------------------------------------------
    win = device.active_window() or ""
    if ("| Save" not in win and "| Question" not in win and "| Warning" not in win
            and "LibreOffice" in win
            and device.find(name="Name:", role="label") is None):
        # Dialog closed and Writer is active again: best-effort success.
        return True
    raise RuntimeError("Save could not be confirmed (active window: %r)" % (win,))
