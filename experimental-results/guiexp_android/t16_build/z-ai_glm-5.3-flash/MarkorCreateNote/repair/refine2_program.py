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
# All matching is done locally on fresh device.elements() dumps: device.find
# expects string match values and crashed on booleans in a previous version.
#
# Repairs for the "editor EditText not found" failure:
#   * the soft keyboard is hidden (navigate_back) BEFORE the dialog's OK
#     button is located and tapped, so the tap cannot land on an IME key or
#     be occluded by the keyboard;
#   * if a dialog field does not hold its value after the harness' atomic
#     input_text (e.g. the text was appended to the pre-filled placeholder
#     or indexes shifted once the keyboard appeared), the field is focused,
#     wiped via keyevents and retyped via `adb input text`, which addresses
#     the focused field and needs no element index at all;
#   * after OK, the editor is awaited; if the dialog is still up, OK is
#     tapped again (IME hidden first); if we are back on the file list, the
#     new note is opened from the list.
# ---------------------------------------------------------------------------

def _text(el):
    return el.get("text") or ""


def _desc(el):
    return el.get("description") or ""


def _hint(el):
    return el.get("hint") or ""


def _find(device, text=None, desc=None, hint=None, clickable=None, editable=None):
    """Case-insensitive substring match on a fresh dump; None = ignore."""
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
    for _ in range(attempts):
        el = fn()
        if el is not None:
            return el
        device.settle(delay)
    return None


def _editables(device):
    return [el for el in (device.elements() or []) if el.get("editable")]


def _keyboard_visible(device):
    singles = 0
    for el in (device.elements() or []):
        if not el.get("clickable"):
            continue
        label = (_text(el) or _desc(el)).strip()
        if len(label) == 1:
            singles += 1
            if singles >= 4:
                return True
        if label.lower() in ("back", "del", "delete", "space", "shift", "emoji"):
            return True
    return False


def _hide_keyboard(device):
    """Close the IME if it is open (with an IME up, back only hides it)."""
    if _keyboard_visible(device):
        device.navigate_back()
        device.settle(1.0)


def _looks_like_ext_field(el):
    blob = (_hint(el) + " " + _text(el)).lower().strip()
    token = blob.strip(".").strip()
    return token in ("txt", "md", "ext", "extension", "csv", "html", "json",
                     "jpg", "jpeg", "png") or "extension" in blob


def _split_dialog_fields(fields):
    """Return (name_field, extension_field) of the create dialog."""
    if not fields:
        return None, None
    if len(fields) == 1:
        return fields[0], None
    ext_i = None
    for i, el in enumerate(fields):
        if _looks_like_ext_field(el):
            ext_i = i
            break
    if ext_i is None:
        return fields[0], fields[1]
    other = next(i for i in range(len(fields)) if i != ext_i)
    return fields[other], fields[ext_i]


def _field_holds(device, value, exact=True):
    v = value.strip()
    if not v:
        return True
    alts = (v, v.lstrip("."))
    for el in (device.elements() or []):
        if not el.get("editable"):
            continue
        cur = _text(el).strip()
        if exact:
            if cur == v:
                return True
        elif v in cur or cur in alts:
            return True
    return False


def _adb_type(device, value):
    """Type into the currently focused field via adb (no element index).
    adb's `input text` takes %s for spaces; shell metacharacters escaped."""
    specials = "&()<>|;*\\~\"'`?$#![]{}"
    out = []
    for ch in value:
        if ch == " ":
            out.append("%s")
        elif ch in specials:
            out.append("\\" + ch)
        else:
            out.append(ch)
    device.adb_shell("input", "text", "".join(out))


def _clear_focused_field(device, length_hint=0):
    device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
    for _ in range(max(10, min(70, length_hint + 10))):
        device.adb_shell("input", "keyevent", "KEYCODE_DEL")


def _fill_field(device, index, value, exact=True, rounds=3):
    """Enter value into the dialog field at `index` and verify it stuck.
    First try the harness' atomic input_text; if the field does not hold the
    value afterwards, focus it, wipe it via keyevents and retype via adb."""
    v = value.strip()
    if not v:
        return True
    device.input_text(v, index=index)
    device.settle(0.8)
    if _field_holds(device, v, exact):
        return True
    for _ in range(max(0, rounds - 1)):
        device.click(index=index)
        device.settle(0.8)
        _clear_focused_field(device, length_hint=len(v) + 12)
        _adb_type(device, v)
        device.settle(0.8)
        if _field_holds(device, v, exact):
            return True
    return _field_holds(device, v, exact)


def _ok_button(device):
    """The create dialog's confirm button (IME keys can never match)."""
    for labels in (("ok",), ("create", "save"), ("done",)):
        fallback = None
        for el in (device.elements() or []):
            label = (_text(el) or _desc(el)).strip().lower()
            if label not in labels:
                continue
            if len(label) == 1 or label in ("back", "del", "space", "shift"):
                continue  # keyboard key, not a dialog button
            if el.get("clickable"):
                return el
            if fallback is None:
                fallback = el
        if fallback is not None:
            return fallback
    return None


def _open_create_dialog(device):
    for _ in range(3):
        fab = (_find(device, desc="Create a new file or folder", clickable=True)
               or _find(device, desc="create", clickable=True))
        if fab is None:
            return False
        device.click(index=fab["index"])
        if _wait_for(device, lambda: _editables(device) or None, attempts=3):
            return True
        device.settle(1.0)
    return False


def _fill_dialog(device, base_name, ext, file_name):
    fields = _editables(device)
    if not fields:
        return False
    if len(fields) == 1:
        return _fill_field(device, fields[0]["index"], file_name, exact=True)
    name_el, ext_el = _split_dialog_fields(fields)
    if name_el is None:
        return False
    if not _fill_field(device, name_el["index"], base_name, exact=True):
        return False
    if not (ext and ext_el is not None):
        return True
    # Fresh dump: the extension field is the editable that does NOT hold the
    # base name (indexes may have shifted once the keyboard appeared).
    fresh = _editables(device)
    ext_idx = ext_el["index"]
    if fresh:
        held = [el for el in fresh if base_name.strip() in _text(el).strip()]
        pool = [el for el in fresh if el not in held] or fresh
        ext_pick = None
        for el in pool:
            if _looks_like_ext_field(el):
                ext_pick = el
                break
        if ext_pick is None and len(pool) == 1:
            ext_pick = pool[0]
        if ext_pick is not None:
            ext_idx = ext_pick["index"]
    _fill_field(device, ext_idx, ext, exact=False, rounds=2)
    return True


def _confirm_dialog(device):
    """Hide the IME so OK is visible/clickable, then tap it."""
    _hide_keyboard(device)
    if not _editables(device):
        return False  # back closed the whole dialog -> caller retries
    ok_el = _ok_button(device)
    if ok_el is None:
        return False
    device.click(index=ok_el["index"])
    device.settle(1.0)
    return True


def _find_file_item(device, file_name, base_name):
    lookups = [
        lambda: _find(device, text=file_name, clickable=True),
        lambda: _find(device, text=file_name),
        lambda: _find(device, desc=file_name),
    ]
    if base_name:
        lookups += [
            lambda: _find(device, text=base_name, clickable=True),
            lambda: _find(device, text=base_name),
        ]
    for lookup in lookups:
        el = lookup()
        if el is not None:
            return el
    return None


def _wait_for_editor(device, file_name, base_name, attempts=6):
    ok_retries = 0
    item_clicks = 0
    for _ in range(attempts):
        if _find(device, editable=True) is not None:
            return True
        if _editables(device) and ok_retries < 3:
            # Dialog still open (OK was missed): hide IME, tap OK again.
            ok_retries += 1
            _confirm_dialog(device)
            continue
        if item_clicks < 2:
            item = _find_file_item(device, file_name, base_name)
            if item is not None:
                item_clicks += 1
                device.click(index=item["index"])
        device.settle(1.5)
    return _find(device, editable=True) is not None


def _type_into_editor(device, text):
    target = text.strip()
    if not target:
        return True
    for attempt in range(3):
        el = _find(device, editable=True)
        if el is None:
            return False
        idx = el["index"]
        if attempt:
            # focus and wipe first so a retry cannot duplicate the text
            device.click(index=idx)
            device.settle(0.8)
            _clear_focused_field(device, length_hint=len(target) + 12)
        device.input_text(target, index=idx)
        device.settle(1.0)
        el = _find(device, editable=True)
        if el is not None and target in _text(el):
            return True
    return False


def _save_and_leave_editor(device):
    for _ in range(3):
        if _find(device, editable=True) is None:
            break
        device.navigate_back()  # first back hides the IME, next leaves the editor
        device.settle(1.0)


def _blob_listed(device, substr):
    s = substr.lower()
    if not s:
        return False
    for el in (device.elements() or []):
        blob = (_text(el) + " " + _desc(el)).lower()
        if s in blob:
            return True
    return False


def _note_listed(device, file_name, base_name):
    for direction in (None, "up", "down"):
        if _blob_listed(device, file_name.lower()):
            return True
        if base_name and _blob_listed(device, base_name.lower()):
            return True
        if direction:
            device.scroll(direction=direction)
            device.settle(1.0)
    return False


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]

    # Split into base name and extension (extension keeps the dot).
    if "." in file_name:
        base_name, ext = file_name.rsplit(".", 1)
        ext = "." + ext
    else:
        base_name, ext = file_name, ""
    if not base_name:
        base_name, ext = file_name, ""

    def _on_main():
        return (_find(device, desc="Create a new file or folder", clickable=True)
                or _find(device, desc="create", clickable=True))

    # ---- Step 1: open Markor on its main screen --------------------------
    device.open_app("Markor")
    if _wait_for(device, _on_main, attempts=4) is None:
        device.navigate_back()
        device.open_app("Markor")
        if _wait_for(device, _on_main, attempts=4) is None:
            raise RuntimeError("Markor did not open: main screen not found")
    _hide_keyboard(device)

    # ---- Steps 2-4: create dialog -> name/ext fields -> OK ---------------
    editor_ready = False
    for _attempt in range(2):
        if not _open_create_dialog(device):
            device.navigate_back()
            device.settle(1.0)
            device.open_app("Markor")
            continue
        if not _fill_dialog(device, base_name, ext, file_name):
            device.navigate_back()  # cancel the dialog, retry fresh
            device.settle(1.0)
            continue
        if not _confirm_dialog(device):
            device.navigate_back()
            device.settle(1.0)
            continue
        if _wait_for_editor(device, file_name, base_name):
            editor_ready = True
            break
        device.navigate_back()
        device.settle(1.0)
    if not editor_ready and _find(device, editable=True) is None:
        raise RuntimeError("Markor editor EditText not found after creating note")

    # ---- Step 5: type the note text into the editor ----------------------
    if not _type_into_editor(device, text):
        raise RuntimeError("Failed to enter the note text into the editor")

    # ---- Step 6: save by leaving the editor (auto-save on back) ----------
    _save_and_leave_editor(device)
    device.settle(1.0)

    # ---- Step 7: verify the note shows up in the file list ---------------
    if not _note_listed(device, file_name, base_name):
        raise RuntimeError(f"Note '{file_name}' not visible in the file list after creation")

    return True
