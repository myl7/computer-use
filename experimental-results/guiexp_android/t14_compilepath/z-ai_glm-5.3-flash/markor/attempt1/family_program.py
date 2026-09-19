import re

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    },
    "text": {
        "type": "string",
        "description": "The note's text content",
        "required": True,
    },
}


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _locate(device, needles, only_clickable=False,
            fields=("text", "hint", "description")):
    """Find the index of the first element matching any needle (case-insensitive
    substring) in one of the given fields. Prefers clickable elements when
    only_clickable is True, otherwise returns the first match."""
    lowered = [str(n).lower() for n in needles]
    fallback = None
    for e in _elements(device):
        if only_clickable and not e.get("clickable"):
            continue
        for f in fields:
            val = e.get(f)
            if not val:
                continue
            low = str(val).lower()
            if any(n in low for n in lowered):
                if only_clickable:
                    return e["index"]
                if fallback is None:
                    fallback = e["index"]
                break
    return fallback


def _split_file_name(file_name):
    if not isinstance(file_name, str) or "." not in file_name:
        raise ValueError("file_name must be a string including an extension, e.g. note.txt")
    base, ext = file_name.rsplit(".", 1)
    if not base:
        raise ValueError("file_name must have a non-empty base name")
    return base, "." + ext


def _editable_indexes(device):
    return [e["index"] for e in _elements(device) if e.get("editable")]


def _looks_like_extension_field(e):
    hint = str(e.get("hint") or "")
    text = str(e.get("text") or "")
    for val in (hint, text):
        if re.fullmatch(r"\.[A-Za-z0-9]+", val):
            return True
    return False


def program(device, binding: dict) -> bool:
    base_name, extension = _split_file_name(binding["file_name"])
    note_text = binding["text"]

    # Step 1: launch Markor
    device.open_app("Markor")

    # Step 2: open the create-file dialog via the 'Create a new file or folder' button
    create_idx = _locate(device, ["create a new file", "create new file", "create a new", "create"],
                         only_clickable=True)
    if create_idx is None:
        create_idx = _locate(device, ["create a new file", "create new file", "create"])
    if create_idx is None:
        raise RuntimeError("Markor 'create new file' button not found")
    device.click(index=create_idx)

    # Step 3: file name WITHOUT extension into the Name field
    #         (replaces the pre-filled placeholder)
    editables = _editable_indexes(device)
    if not editables:
        raise RuntimeError("File-creation dialog did not appear (no editable fields)")
    device.input_text(base_name, index=editables[0])

    # Step 4: extension (with the dot) into the small extension field
    ext_idx = None
    for e in _elements(device):
        if e.get("editable") and _looks_like_extension_field(e):
            ext_idx = e["index"]
            break
    if ext_idx is None:
        editables = _editable_indexes(device)
        if len(editables) >= 2:
            ext_idx = editables[1]
    if ext_idx is None:
        raise RuntimeError("Extension field not found in create dialog")
    device.input_text(extension, index=ext_idx)

    # Steps 5-6: make sure the file type is 'Plain Text'
    # (first click opens the file-type selector, second click picks the option)
    type_idx = _locate(device, ["plain text"])
    if type_idx is not None:
        device.click(index=type_idx)
        option_idx = _locate(device, ["plain text"])
        if option_idx is not None and option_idx != type_idx:
            device.click(index=option_idx)

    # Step 7: confirm the dialog with OK
    ok_idx = _locate(device, ["ok"], only_clickable=True)
    if ok_idx is None:
        ok_idx = _locate(device, ["ok"])
    if ok_idx is None:
        raise RuntimeError("OK button not found in create dialog")
    device.click(index=ok_idx)

    # Step 8: type the note content into the editor's text field
    editor_idx = None
    for _ in range(5):
        editables = _editable_indexes(device)
        if editables:
            editor_idx = editables[0] if len(editables) == 1 else editables[-1]
            break
        device.wait()
    if editor_idx is None:
        raise RuntimeError("Editor text field not found after creating the note")
    device.input_text(note_text, index=editor_idx)

    # Step 9: save the note — tap Save if available, then leave the editor
    # (navigating back persists/commits the note in Markor)
    save_idx = _locate(device, ["save"], only_clickable=True)
    if save_idx is not None:
        device.click(index=save_idx)
    device.navigate_back()

    # If a save-confirmation prompt appeared, confirm it
    confirm_idx = _locate(device, ["save", "ok", "yes"], only_clickable=True)
    if confirm_idx is not None:
        device.click(index=confirm_idx)

    return True
