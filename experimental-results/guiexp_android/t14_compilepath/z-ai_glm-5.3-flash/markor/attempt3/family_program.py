import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "str",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    },
    "text": {
        "type": "str",
        "description": "Text content of the note",
        "required": True,
    },
}


def _find_idx(device, **kw):
    """Safe wrapper around device.find that never raises."""
    try:
        return device.find(**kw)
    except Exception:
        return None


def _click_any(device, criteria_list):
    """Try a list of find-criteria dicts; click the first match.

    Returns True if something was clicked, else False.
    """
    for kw in criteria_list:
        idx = _find_idx(device, **kw)
        if idx is not None:
            device.click(index=idx)
            return True
    return False


def _editables(device):
    """All currently visible editable elements, ordered by index."""
    try:
        elems = [e for e in device.elements() if e.get("editable")]
    except Exception:
        return []
    elems.sort(key=lambda e: e.get("index", 0))
    return elems


def _split_file_name(file_name):
    """Split 'note.txt' -> ('note', '.txt')."""
    if "." in file_name:
        base, ext = file_name.rsplit(".", 1)
        return base, "." + ext
    return file_name, ".txt"


def _ok_button_index(device):
    """Locate the OK button of the create dialog."""
    for kw in ({"text": "OK"}, {"text": "Ok"}, {"description": "OK"},
               {"contains": "OK"}, {"contains": "Ok"}):
        idx = _find_idx(device, **kw)
        if idx is not None:
            return idx
    for e in device.elements():
        label = str(e.get("text") or e.get("description") or "").strip().upper()
        if label == "OK" and e.get("clickable", True):
            return e.get("index")
    return None


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]
    base, ext = _split_file_name(file_name)

    # ---- step 1: open Markor -------------------------------------------
    device.open_app("Markor")

    # ---- step 2: tap the "create new note" FAB --------------------------
    clicked_fab = _click_any(device, [
        {"description": "Create new note"},
        {"description": "create new note"},
        {"description": "New note"},
        {"hint": "Create new note"},
        {"text": "Create new note"},
        {"contains": "new note"},
        {"contains": "Create new"},
    ])
    if not clicked_fab:
        # Fallback: recorded position of the FAB on a fresh MainActivity.
        device.click(index=1)

    # ---- steps 3-4: fill name (without extension) + extension fields ----
    dialog_editables = _editables(device)

    name_idx = _find_idx(device, hint="Name")
    if name_idx is None:
        if not dialog_editables:
            raise RuntimeError("Markor create-dialog name field not found")
        name_idx = dialog_editables[0]["index"]
    device.input_text(base, index=name_idx)

    ext_idx = _find_idx(device, hint="Extension")
    if ext_idx is None:
        if len(dialog_editables) > 1:
            ext_idx = dialog_editables[1]["index"]
        else:
            refreshed = _editables(device)
            if len(refreshed) < 2:
                raise RuntimeError("Markor create-dialog extension field not found")
            ext_idx = refreshed[1]["index"]
    device.input_text(ext, index=ext_idx)

    # ---- step 5: confirm with OK ----------------------------------------
    ok_idx = _ok_button_index(device)
    if ok_idx is None:
        raise RuntimeError("Markor create-dialog OK button not found")
    device.click(index=ok_idx)

    # ---- steps 6-7: open the freshly created note from the list ---------
    open_criteria = [
        {"text": file_name},
        {"contains": file_name},
        {"description": file_name},
        {"contains": base},
    ]
    opened = _click_any(device, open_criteria)
    if not opened:
        device.scroll("down")
        opened = _click_any(device, open_criteria)
    if not opened and _editables(device):
        # The editor is already in the foreground (file auto-opened).
        opened = True
    if not opened:
        # Fallback: recorded position of the note entry in the file list.
        device.click(index=11)

    # ---- step 8: type the note text into the editor ----------------------
    editor_idx = _find_idx(device, editable=True)
    if editor_idx is None:
        editable = _editables(device)
        if not editable:
            raise RuntimeError("Markor editor field not found in DocumentActivity")
        editor_idx = editable[0]["index"]
    device.input_text(text, index=editor_idx)

    # ---- step 9: save the note by leaving the editor ---------------------
    device.navigate_back()
    device.wait()

    return True
