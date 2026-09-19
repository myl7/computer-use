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


# ---------------------------------------------------------------------------
# Element lookup helpers.
# The previous version crashed with "AttributeError: 'bool' object has no
# attribute 'lower'" because booleans (contains=True / clickable=True) were
# passed into device.find(), which expects string values it .lower()'s.
# All matching is therefore done locally on fresh device.elements() dumps.
# ---------------------------------------------------------------------------

def _text(el):
    return el.get("text") or ""


def _desc(el):
    return el.get("description") or ""


def _hint(el):
    return el.get("hint") or ""


def _find(device, text=None, desc=None, hint=None, clickable=None, editable=None):
    """Locate an element on a fresh dump of the current screen.
    text/desc/hint are case-insensitive substring matches; None = ignore."""
    for el in (device.elements() or []):
        if clickable is not None and bool(el.get("clickable")) != bool(clickable):
            continue
        if editable is not None and bool(el.get("editable")) != bool(editable):
            continue
        if text is not None and text.lower() not in _text(el).lower():
            continue
        if desc is not None and desc.lower() not in _desc(el).lower():
            continue
        if hint is not None and hint.lower() not in _hint(el).lower():
            continue
        return el
    return None


def _wait_for(device, fn, attempts=5, delay=1.0):
    el = None
    for _ in range(attempts):
        el = fn()
        if el is not None:
            return el
        device.settle(delay)
    return el


def _editables(device):
    return [el for el in (device.elements() or []) if el.get("editable")]


def _looks_like_ext_field(el):
    blob = (_hint(el) + " " + _text(el)).lower().strip()
    token = blob.strip(".").strip()
    return token in ("txt", "md", "ext", "extension", "csv", "html", "json",
                     "jpg", "jpeg", "png") or "extension" in blob


def _pick_name_field(device):
    """Create dialog's Name field (file name without extension)."""
    editables = _editables(device)
    if not editables:
        return None
    for el in editables:
        blob = (_hint(el) + " " + _text(el)).lower()
        if "my_note" in blob or "name" in blob:
            return el
    if len(editables) == 2:
        first, second = editables
        if _looks_like_ext_field(first) and not _looks_like_ext_field(second):
            return second
        if _looks_like_ext_field(second) and not _looks_like_ext_field(first):
            return first
    return editables[0]


def _pick_ext_field(device, name_index):
    """Create dialog's small extension field (the other editable)."""
    others = [el for el in _editables(device) if el.get("index") != name_index]
    if not others:
        return None
    for el in others:
        if _looks_like_ext_field(el):
            return el
    return others[0]


def _ok_button(device):
    fallback = None
    for el in (device.elements() or []):
        label = _text(el).strip().lower()
        if label not in ("ok", "create", "done"):
            continue
        if el.get("clickable"):
            return el
        if label == "ok" and fallback is None:
            fallback = el
    return fallback


def _clear_field(device, index, approx_len=0):
    """Best effort: focus the field and delete its current content."""
    try:
        device.click(index=index)
        device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
        for _ in range(max(4, min(80, approx_len + 12))):
            device.adb_shell("input", "keyevent", "KEYCODE_DEL")
    except Exception:
        pass


def _type_text(device, el, value, exact_check=True):
    """Type value into element el and verify it arrived. If the field does
    not hold the value afterwards (e.g. the text was appended to the
    pre-filled placeholder instead of replacing it), clear and retype."""

    def _holds():
        for e in _editables(device):
            cur = _text(e).strip()
            if exact_check:
                if cur == value.strip():
                    return True
            elif value.strip() in cur:
                return True
        return False

    device.input_text(value, index=el["index"])
    if _holds():
        return True
    _clear_field(device, el["index"], approx_len=len(value) + 20)
    device.input_text(value, index=el["index"])
    return _holds()


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]

    # Split the file name into base name and extension (extension keeps dot).
    if "." in file_name:
        base_name, ext = file_name.rsplit(".", 1)
        ext = "." + ext
    else:
        base_name, ext = file_name, ""

    # ---- Step 1: open Markor and wait for its main screen ---------------
    device.open_app("Markor")
    fab = _wait_for(
        device,
        lambda: _find(device, desc="Create a new file or folder", clickable=True),
        attempts=4,
    )
    if fab is None:
        # The app may have resumed on a leftover screen: back out and retry.
        device.navigate_back()
        device.open_app("Markor")
        fab = _wait_for(
            device,
            lambda: _find(device, desc="Create a new file or folder", clickable=True),
            attempts=4,
        )
    if fab is None:
        raise RuntimeError("Markor did not open: 'Create a new file or folder' button not found")

    # ---- Step 2: open the create-new-file dialog -------------------------
    device.click(index=fab["index"])

    # ---- Step 3: Name field <- file name without extension ---------------
    name_el = _wait_for(device, lambda: _pick_name_field(device), attempts=4)
    if name_el is None:
        # First tap may have been lost: tap the FAB again and re-check.
        fab2 = _find(device, desc="Create a new file", clickable=True)
        if fab2 is not None:
            device.click(index=fab2["index"])
        name_el = _wait_for(device, lambda: _pick_name_field(device), attempts=4)
    if name_el is None:
        raise RuntimeError("Markor create-dialog name field not found")
    _type_text(device, name_el, base_name, exact_check=True)

    # ---- Step 3b: extension field <- extension including the dot ---------
    if ext:
        ext_el = _pick_ext_field(device, name_el["index"])
        if ext_el is not None:
            _type_text(device, ext_el, ext, exact_check=True)

    # ---- Step 4: confirm the dialog with OK ------------------------------
    ok_el = _ok_button(device)
    if ok_el is None:
        raise RuntimeError("Create-note dialog 'OK' button not found")
    device.click(index=ok_el["index"])
    for _ in range(3):  # make sure the dialog is really dismissed
        ok_el = _ok_button(device)
        if ok_el is None:
            break
        device.click(index=ok_el["index"])

    # ---- Step 5: type the note text into the editor ----------------------
    edit_el = _wait_for(device, lambda: _find(device, editable=True), attempts=4)
    if edit_el is None:
        # Editor did not open by itself: open the new file from the list.
        item = (_find(device, text=file_name, clickable=True)
                or _find(device, text=file_name)
                or _find(device, desc=file_name))
        if item is not None:
            device.click(index=item["index"])
            edit_el = _wait_for(device, lambda: _find(device, editable=True), attempts=4)
    if edit_el is None:
        raise RuntimeError("Markor editor EditText not found after creating note")
    if not _type_text(device, edit_el, text, exact_check=False):
        device.settle(2)
        if not _type_text(device, edit_el, text, exact_check=False):
            raise RuntimeError("Failed to enter the note text into the editor")

    # ---- Step 6: save by leaving the editor (auto-save on back) ----------
    device.navigate_back()  # dismiss the keyboard if it is showing
    for _ in range(2):
        device.settle(1.0)
        if _find(device, editable=True) is None:
            break
        device.navigate_back()  # still in the editor -> leave it
    device.settle(1.0)

    # ---- Step 7: verify the note shows up in the file list ---------------
    target = file_name.lower()
    base_target = base_name.lower()

    def _listed(substr):
        if not substr:
            return False
        for el in (device.elements() or []):
            blob = (_text(el) + " " + _desc(el)).lower()
            if substr in blob:
                return True
        return False

    if not _listed(target):
        device.scroll(direction="up")
    if not _listed(target):
        device.scroll(direction="down")
    if not _listed(target) and not _listed(base_target):
        raise RuntimeError(f"Note '{file_name}' not visible in the file list after creation")

    return True
