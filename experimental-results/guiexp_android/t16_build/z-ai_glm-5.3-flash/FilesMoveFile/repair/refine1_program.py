"""Parameterized trajectory: move a file between two top-level folders of the
sdk_gphone_x86_64 storage area using the Android Files app (DocumentsUI).
Flow: launch app -> roots drawer -> the sdk_gphone_x86_64 storage volume
(explicitly verified; the drawer may also list other volumes such as
sdk_gphone64_arm64, which must NOT be used) -> source folder -> long-press
the file (exact-name match) -> overflow 'Move to…' -> in the destination
picker: roots drawer -> sdk_gphone_x86_64 -> destination folder -> confirm
MOVE. The move is verified at both ends (present in destination, gone from
source)."""

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "File to move, name with extension (e.g. note.mp3)",
    },
    "source_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level source folder inside the sdk_gphone_x86_64 "
                       "storage area (e.g. Download)",
    },
    "destination_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level destination folder inside the same "
                       "sdk_gphone_x86_64 storage area (e.g. DCIM)",
    },
}

_APP_NAME = "Files"
_TARGET_STORAGE = "sdk_gphone_x86_64"
_FALLBACK_STORAGES = ("sdk_gphone_x86_64", "sdk_gphone64_arm64", "sdk_gphone_x86")


def _elements(device):
    try:
        return device.elements() or []
    except Exception:
        return []


def _text_of(element, *fields):
    for f in fields:
        v = element.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _stem(name):
    return name.rsplit(".", 1)[0] if "." in name else name


def _drawer_entry_index(device, storage_name):
    """Index of a drawer entry that names the requested storage volume.
    Prefers an exact label match, then a substring match that is not the
    activity title ('Files on <volume>')."""
    els = _elements(device)
    for e in els:  # exact volume label first
        if _text_of(e, "text", "description", "hint") == storage_name:
            return e["index"]
    for e in els:  # substring, but never the activity title
        t = _text_of(e, "text", "description", "hint").lower()
        if storage_name in t and not t.startswith("files on"):
            return e["index"]
    for e in els:  # last resort: allow the title form
        t = _text_of(e, "text", "description", "hint").lower()
        if storage_name in t:
            return e["index"]
    return None


def _on_target_volume(device, storage_name=_TARGET_STORAGE):
    """True if the current screen title indicates the requested volume."""
    for e in _elements(device):
        t = _text_of(e, "text").lower()
        if t.startswith("files on") and storage_name in t:
            return True
    return False


def _open_drawer(device):
    btn = device.find(description="Show roots", clickable=True)
    if btn is None:
        for e in _elements(device):
            d = _text_of(e, "description", "hint").lower()
            if "roots" in d and e.get("clickable"):
                btn = e["index"]
                break
    if btn is None:
        raise RuntimeError("'Show roots' hamburger button not found")
    device.click(btn)
    device.settle(1)


def _goto_storage(device, storage_name=_TARGET_STORAGE):
    """Open the roots drawer if needed and enter the requested storage volume.
    After entering, verify the screen title actually shows that volume; if a
    sibling volume was opened (e.g. sdk_gphone64_arm64), go back to the drawer
    and pick the right entry. Scrolls the drawer if the entry is below the
    fold."""
    # Already on the right volume?
    if _on_target_volume(device, storage_name):
        return
    for attempt in range(4):
        _open_drawer(device)
        # Try preferred then fallback names, exact-substring matching first.
        candidates = [storage_name] + [s for s in _FALLBACK_STORAGES
                                       if s != storage_name]
        for name in candidates:
            idx = _drawer_entry_index(device, name)
            if idx is not None:
                device.click(idx)
                device.settle(1.5)
                if _on_target_volume(device, storage_name):
                    return
                if name == storage_name:
                    # clicked what looked like the right entry but title
                    # disagrees — keep searching (stale click possible)
                    pass
                _open_drawer(device)
        device.scroll("down")  # volume entry may sit below the fold
        for name in candidates:
            idx = _drawer_entry_index(device, name)
            if idx is not None:
                device.click(idx)
                device.settle(1.5)
                if _on_target_volume(device, storage_name):
                    return
                _open_drawer(device)
        device.scroll("up")
    # Final attempt with whatever is visible
    idx = _drawer_entry_index(device, storage_name)
    if idx is None:
        raise LookupError(
            f"Storage root '{storage_name}' not found in the Files navigation drawer"
        )
    device.click(idx)
    device.settle(1.5)
    if not _on_target_volume(device, storage_name):
        raise LookupError(
            f"Entered the wrong storage volume; title does not show '{storage_name}'"
        )


def _scrolling_find(device, name, stem=None, max_scrolls=8):
    """Find an exact-text element, scrolling down as needed. Falls back to a
    substring match on `stem` (for file rows whose label may be truncated)."""
    for attempt in range(max_scrolls + 1):
        idx = device.find(text=name)
        if idx is not None:
            return idx
        if stem and stem != name:
            idx = device.find(text=stem, contains=True)
            if idx is not None:
                return idx
        if attempt < max_scrolls:
            device.scroll("down")
    return None


def _open_folder(device, folder, label):
    idx = _scrolling_find(device, folder)
    if idx is None:
        raise LookupError(f"{label} '{folder}' not found in the storage listing")
    device.click(idx)
    device.settle(1.5)


def _select_file(device, file_name):
    idx = _scrolling_find(device, file_name, stem=_stem(file_name))
    if idx is None:
        raise LookupError(
            f"File '{file_name}' not found in the source folder listing"
        )
    pressed = False
    for call in (
        lambda: device.long_press(idx),
        lambda: device.long_press(index=idx),
        lambda: device.execute({"action_type": "long_press",
                                "index": idx, "duration_seconds": 1.5}),
    ):
        try:
            call()
            pressed = True
            break
        except Exception:
            continue
    if not pressed:
        raise RuntimeError("device.long_press unavailable; cannot select the file row")
    device.settle(1)


def _open_overflow(device):
    idx = device.find(description="More options", clickable=True)
    if idx is None:
        for e in _elements(device):
            d = _text_of(e, "description", "hint").lower()
            if "more" in d and e.get("clickable"):
                idx = e["index"]
                break
    if idx is None:
        raise RuntimeError(
            "Overflow 'More options' button not found — was the file row selected?"
        )
    device.click(idx)
    device.settle(1)


def _click_move_to(device):
    for idx in (
        device.find(text="Move to…"),
        device.find(text="Move to", contains=True),
        device.find(text="Cut"),
        device.find(text="Cut", contains=True),
    ):
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return
    for e in _elements(device):
        t = _text_of(e, "text").lower()
        if t.startswith(("move", "cut")):
            device.click(e["index"])
            device.settle(1)
            return
    raise RuntimeError(
        "Neither 'Move to…' nor 'Cut' was found in the selection overflow menu"
    )


def _click_move_confirm(device):
    for _ in range(3):
        els = _elements(device)
        for e in els:  # clickable confirmation button preferred
            if e.get("clickable") and _text_of(e, "text").upper() in ("MOVE", "PASTE"):
                device.click(e["index"])
                device.settle(2)
                return
        for e in els:
            if _text_of(e, "text").upper() in ("MOVE", "PASTE"):
                device.click(e["index"])
                device.settle(2)
                return
        device.wait()
    raise RuntimeError("MOVE/PASTE confirmation button not found on the picker screen")


def _file_visible(device, file_name):
    if device.find(text=file_name) is not None:
        return True
    s = _stem(file_name)
    return s != file_name and device.find(text=s, contains=True) is not None


def _verify_move(device, file_name, source_folder, destination_folder):
    # After MOVE the picker returns to FilesActivity; the moved file is
    # usually visible immediately (destination is shown).
    for _ in range(2):
        if _file_visible(device, file_name):
            break
        device.scroll("down")
    if not _file_visible(device, file_name):
        _goto_storage(device)
        _open_folder(device, destination_folder, "Destination folder")
        for _ in range(3):
            if _file_visible(device, file_name):
                break
            device.scroll("down")
    if not _file_visible(device, file_name):
        raise RuntimeError(
            f"Move verification failed: '{file_name}' not found in "
            f"'{destination_folder}' after the move"
        )
    # A move, not a copy: the oracle checks both ends.
    if destination_folder != source_folder:
        _goto_storage(device)
        _open_folder(device, source_folder, "Source folder")
        for _ in range(10):
            if device.find(text=file_name) is not None:
                raise RuntimeError(
                    f"Move verification failed: '{file_name}' is still present "
                    f"in the source folder '{source_folder}'"
                )
            device.scroll("down")
    return True


def program(device, binding: dict) -> bool:
    file_name = binding.get("file_name")
    source_folder = binding.get("source_folder")
    destination_folder = binding.get("destination_folder")
    if not file_name or not source_folder or not destination_folder:
        raise KeyError(
            "binding must provide file_name, source_folder and destination_folder"
        )

    # 1) Launch the Files (DocumentsUI) app.
    device.open_app(_APP_NAME)
    device.settle(2)
    if (device.find(description="Show roots") is None
            and device.find(text="Downloads") is None
            and device.find(text="Files", contains=True) is None):
        raise RuntimeError(
            f"App '{_APP_NAME}' did not open; no DocumentsUI signature element found"
        )

    # 2) Hamburger drawer -> sdk_gphone_x86_64 storage root (not the first
    #    screen). The drawer can list sibling volumes; the correct one is
    #    verified via the screen title.
    _goto_storage(device, _TARGET_STORAGE)

    # 3) Open the source folder.
    _open_folder(device, source_folder, "Source folder")

    # 4) Long-press the target file row (matched by its exact name) to select it.
    _select_file(device, file_name)

    # 5) Selection-bar overflow -> 'Move to…' (cut half of the move).
    _open_overflow(device)
    _click_move_to(device)

    # 6) Destination picker: roots drawer -> storage root -> destination folder.
    _goto_storage(device, _TARGET_STORAGE)
    _open_folder(device, destination_folder, "Destination folder")

    # 7) Confirm the move (paste half).
    _click_move_confirm(device)

    # 8) Verify both ends: file in destination, gone from source.
    return _verify_move(device, file_name, source_folder, destination_folder)
