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
# All matching is done locally on fresh device.elements() dumps.
#
# Repairs for the "editor EditText not found" failure (the create dialog was
# cancelled instead of confirmed, so no file and no editor ever appeared):
#   * the editor is now detected as "an editable present AND the main
#     screen's FAB ('Create a new file or folder', which stays in the dump
#     behind the dialog) gone AND no CANCEL button" -- the dialog's own
#     fields are editable elements too and must never be mistaken for the
#     editor;
#   * the extension field is verified EXACTLY (with its leading dot): a
#     pre-filled "txt" plus an appended ".txt" silently produced "txt.txt",
#     after which the OK tap created nothing; a dialog field that does not
#     hold its wanted value is focused, wiped via keyevents and retyped via
#     `adb input text`, which addresses the focused field, needs no element
#     index, and is re-located on a fresh dump before every retry;
#   * before OK is tapped the IME is hidden and the layout gets an extra
#     settle, OK is re-located on a fresh dump immediately before the tap,
#     and the tap is verified (editor open / dialog gone) with bounded
#     re-taps; CANCEL/EXIT/IME keys can never match the OK lookup;
#   * retry rounds recover to the main screen without stray navigate_backs
#     (which armed Markor's "Press 'Back' again to exit" state).
# ---------------------------------------------------------------------------

_OK_TOKENS = ("ok", "yes", "done", "confirm", "save", "create")
_IME_LABELS = ("back", "del", "delete", "space", "shift", "emoji", "cancel",
               "exit", "num", "abc", "sym", "ime")


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
    """Wipe the focused field: cursor to the end, then delete backwards."""
    n = max(16, min(70, int(length_hint or 0) + 10))
    device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
    for _ in range(n):
        device.adb_shell("input", "keyevent", "KEYCODE_DEL")


def _current_field_len(device, value):
    """Best-effort length of the text currently in the focused dialog field."""
    v = value.strip()
    fallback = 0
    for el in (device.elements() or []):
        if not el.get("editable"):
            continue
        t = _text(el)
        if v and v in t:
            return len(t)
        if el.get("focused"):
            fallback = max(fallback, len(t))
    return fallback


def _field_holds_exact(device, value):
    v = value.strip()
    if not v:
        return True
    for el in (device.elements() or []):
        if el.get("editable") and _text(el).strip() == v:
            return True
    return False


def _ext_holds(device, ext, strict=True):
    """True if some editable holds the extension; strict demands the dot."""
    want = ext.strip()
    alts = (want,) if strict else (want, want.lstrip("."))
    for el in (device.elements() or []):
        if el.get("editable") and _text(el).strip() in alts:
            return True
    return False


def _name_field_index(device):
    fields = _editables(device)
    if not fields:
        return None
    if len(fields) == 1:
        return fields[0]["index"]
    name_el, _ext_el = _split_dialog_fields(fields)
    return name_el["index"] if name_el is not None else None


def _ext_field_index(device):
    fields = _editables(device)
    if len(fields) < 2:
        return None
    _name_el, ext_el = _split_dialog_fields(fields)
    return ext_el["index"] if ext_el is not None else None


def _fill_field(device, value, kind, locate, rounds=3):
    """Enter `value` into the dialog field located by `locate()` and verify.

    The harness' atomic input_text is tried first; if the field does not hold
    exactly the wanted value afterwards (text appended to a pre-filled
    placeholder, value landed in the wrong field after an index shift, ...),
    the field is focused, wiped via keyevents and retyped via `adb input
    text`.  `kind` "name" demands an exact match; "ext" demands the extension
    including its leading dot, so a pre-filled "txt" + appended ".txt"
    ("txt.txt") is rejected and corrected instead of silently confirmed.
    """
    v = value.strip()
    if not v:
        return True

    def _holds():
        if kind == "name":
            return _field_holds_exact(device, v)
        return _ext_holds(device, v, strict=True)

    if _holds():
        return True
    for attempt in range(max(1, rounds)):
        idx = locate(device)
        if idx is None:
            break
        if attempt == 0:
            device.input_text(v, index=idx)
        else:
            device.click(index=idx)
            device.settle(0.8)
            _clear_focused_field(device, length_hint=_current_field_len(device, v))
            _adb_type(device, v)
        device.settle(0.8)
        if _holds():
            return True
    if kind == "ext":
        return _ext_holds(device, v, strict=False)
    return _field_holds_exact(device, v)


def _fill_dialog(device, base_name, ext, file_name):
    if not _editables(device):
        return False
    if not _fill_field(device, base_name, "name", _name_field_index):
        return False
    if not ext:
        return True
    if _ext_field_index(device) is None:
        # no separate extension field: the single field holds the full name
        if _field_holds_exact(device, file_name):
            return True
        return _fill_field(device, file_name, "name", _name_field_index)
    if not _fill_field(device, ext, "ext", _ext_field_index):
        return False
    # correction pass: a mis-directed tap may have put a value into the wrong
    # field; re-locate on fresh dumps and re-fill whatever slipped.
    if not _field_holds_exact(device, base_name):
        _fill_field(device, base_name, "name", _name_field_index, rounds=2)
    if not _ext_holds(device, ext, strict=True):
        _fill_field(device, ext, "ext", _ext_field_index, rounds=2)
    return (_field_holds_exact(device, base_name)
            and _ext_holds(device, ext, strict=False))


def _ok_button(device):
    """The create dialog's confirm button; CANCEL/EXIT/IME keys never match."""
    fallback = None
    for el in (device.elements() or []):
        label = (_text(el) or _desc(el)).strip().lower()
        if not label or len(label) == 1 or "." in label:
            continue
        if label in _IME_LABELS:
            continue
        if not any(label == t or (label.startswith(t) and len(label) <= len(t) + 5)
                   for t in _OK_TOKENS):
            continue
        if el.get("clickable"):
            return el
        if fallback is None:
            fallback = el
    return fallback


def _harness_ok_index(device):
    for probe in ({"text": "OK"}, {"description": "OK"}, {"text": "Ok"},
                  {"description": "ok"}, {"contains": "OK"}):
        try:
            idx = device.find(**probe)
        except Exception:
            idx = None
        if idx is not None:
            return idx
    return None


def _in_editor(device):
    """True when a note editor is open (not the create dialog, whose fields
    are editable elements as well, and behind which the main screen's FAB
    stays visible in the dump)."""
    if _find(device, editable=True) is None:
        return False
    if _find(device, desc="Create a new file or folder") is not None:
        return False
    if _find(device, text="cancel", clickable=True) is not None:
        return False
    return True


def _create_dialog_open(device):
    if not _editables(device):
        return False
    return (_find(device, desc="Create a new file or folder") is not None
            or _ok_button(device) is not None)


def _on_main(device):
    return ((_find(device, desc="Create a new file or folder", clickable=True)
             is not None)
            or _find(device, desc="create", clickable=True) is not None)


def _open_main(device):
    device.open_app("Markor")
    for _ in range(3):
        if _wait_for(device, _on_main, attempts=2) is not None:
            _hide_keyboard(device)
            return True
        device.navigate_back()  # leave a stale editor / settings screen
        device.settle(1.0)
    device.open_app("Markor")
    device.settle(1.0)
    return _on_main(device)


def _recover_main(device):
    for _ in range(2):
        if _on_main(device):
            return True
        device.navigate_back()
        device.settle(1.0)
    if not _on_main(device):
        device.open_app("Markor")
        device.settle(1.0)
    return _on_main(device)


def _open_create_dialog(device):
    if _create_dialog_open(device):
        return True
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


def _cancel_dialog(device):
    for _ in range(2):
        if not _editables(device):
            return
        device.navigate_back()
        device.settle(1.0)


def _confirm_dialog(device):
    """Hide the IME, tap the dialog's OK button, verify the outcome.
    Returns 'editor' (note editor open), 'closed' (dialog gone) or 'stuck'."""
    _hide_keyboard(device)
    device.settle(1.0)  # let the layout settle so the OK tap cannot land stale
    for attempt in range(3):
        if _in_editor(device):
            return "editor"
        if not _editables(device):
            return "closed"
        ok_el = _ok_button(device)
        if ok_el is not None:
            device.click(index=ok_el["index"])
        elif attempt >= 1:
            idx = _harness_ok_index(device)
            if idx is None:
                device.settle(1.0)
                continue
            device.click(index=idx)
        else:
            device.settle(1.0)
            continue
        device.settle(1.5)
        for _ in range(2):
            if _in_editor(device):
                return "editor"
            if not _editables(device):
                return "closed"
            device.settle(1.0)
    return "stuck"


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


def _wait_for_editor(device, file_name, base_name, attempts=4):
    for _ in range(attempts):
        if _in_editor(device):
            return True
        item = _find_file_item(device, file_name, base_name)
        if item is not None:
            device.click(index=item["index"])
            device.settle(1.5)
            if _in_editor(device):
                return True
        device.settle(1.0)
    return _in_editor(device)


def _open_file_from_list(device, file_name, base_name, attempts=3):
    for _ in range(attempts):
        if _in_editor(device):
            return True
        item = _find_file_item(device, file_name, base_name)
        if item is not None:
            device.click(index=item["index"])
            device.settle(1.5)
        else:
            device.scroll(direction="up")
            device.settle(1.0)
    return _in_editor(device)


def _type_into_editor(device, text):
    target = text.strip()
    if not target:
        return True
    for attempt in range(3):
        el = _find(device, editable=True)
        if el is None:
            return False
        if target in _text(el):
            return True
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
    """Markor auto-saves on back: the first back hides the IME, the next one
    leaves the editor."""
    for _ in range(4):
        if _find(device, editable=True) is None:
            return
        device.navigate_back()
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

    # ---- Step 1: open Markor on its main screen --------------------------
    if not _open_main(device):
        raise RuntimeError("Markor did not open: main screen not found")

    # ---- Steps 2-4: create dialog -> name/ext fields -> OK ---------------
    editor_ready = False
    for _round in range(3):
        if not _on_main(device):
            _recover_main(device)
        if not _open_create_dialog(device):
            _cancel_dialog(device)
            continue
        if not _fill_dialog(device, base_name, ext, file_name):
            _cancel_dialog(device)  # cancel the dialog and retry fresh
            continue
        state = _confirm_dialog(device)
        if state == "editor":
            editor_ready = True
            break
        if state == "closed" and _wait_for_editor(device, file_name, base_name):
            editor_ready = True
            break
        _cancel_dialog(device)
    if not _in_editor(device):
        _cancel_dialog(device)
        if not _open_file_from_list(device, file_name, base_name):
            raise RuntimeError("Markor editor EditText not found after creating note")

    # ---- Step 5: type the note text into the editor ----------------------
    if not _type_into_editor(device, text):
        raise RuntimeError("Failed to enter the note text into the editor")

    # ---- Step 6: save by leaving the editor (auto-save on back) ----------
    _save_and_leave_editor(device)
    device.settle(1.0)

    # ---- Step 7: verify the note shows up in the file list ---------------
    if not _note_listed(device, file_name, base_name):
        device.open_app("Markor")
        device.settle(1.0)
    if not _note_listed(device, file_name, base_name):
        raise RuntimeError(f"Note '{file_name}' not visible in the file list after creation")

    return True
