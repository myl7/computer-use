"""Parameterized trajectory: move a file between two top-level folders of the
sdk_gphone_x86_64 storage area using the Android Files app (DocumentsUI).

Flow: launch app -> roots list -> storage volume -> source folder -> long-press
the file row (exact-name match) -> selection-bar overflow 'Move to…' (falls
back to 'Cut') -> destination half: navigate roots list -> storage volume ->
destination folder, then confirm with the picker's MOVE button (or press
Paste after a plain Cut).

Repair notes: the roots list may already be visible on the current screen
(inline on the app's first screen, or because the drawer itself is open) —
in that state the 'Show roots' hamburger is gone, so the drawer step is
skipped instead of failing. The storage volume is chosen by probing the
roots list: entries naming the requested volume (sdk_gphone_x86_64) are
tried first, then any other volume-looking entry (e.g. sdk_gphone64_arm64,
which on some devices is the label of the very storage area the task calls
sdk_gphone_x86_64); a volume counts as the right one only when it actually
contains the source folder and the file. The move is verified at both ends
(present in the destination, gone from the source)."""

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

# Labels that belong to the roots drawer itself (categories/actions), never
# to a storage-volume entry.
_NON_ROOT_LABELS = {
    "recent", "recents", "images", "videos", "audio", "documents",
    "downloads", "download", "settings", "trash", "files",
}


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


def _screen_signature(device):
    return tuple(_text_of(e, "text") for e in _elements(device))


def _scroll_until(device, check, direction="down", max_steps=5):
    """Evaluate check() at the current position, then scroll up to max_steps
    times, stopping early when the view stops moving. Returns the first
    non-None check() result, else None."""
    for attempt in range(max_steps + 1):
        hit = check()
        if hit is not None:
            return hit
        if attempt < max_steps:
            sig = _screen_signature(device)
            device.scroll(direction)
            if _screen_signature(device) == sig:
                return None
    return None


def _scrolling_find(device, name, stem=None, max_scrolls=5):
    """Find an element by exact text, scrolling as needed; only if the exact
    text is nowhere in the list, fall back to a contains-match on `stem`
    (for truncated file rows), restarting from the top."""
    hit = _scroll_until(device, lambda: device.find(text=name), "down", max_scrolls)
    if hit is not None:
        return hit
    if stem and stem != name:
        _scroll_until(device, lambda: None, "up", max_scrolls)
        hit = _scroll_until(
            device,
            lambda: device.find(text=stem, contains=True),
            "down",
            max_scrolls,
        )
        if hit is not None:
            return hit
    return None


def _root_entries(device, storage_name=_TARGET_STORAGE, preferred=None):
    """Storage-volume entries currently visible in the roots list, best match
    first: the preferred (already proven) label, then the requested storage
    name, then other volume-looking names. The activity title
    ('Files on <volume>') is never treated as an entry."""
    found = []
    for e in _elements(device):
        t = _text_of(e, "text", "description", "hint")
        if not t:
            continue
        tl = t.lower()
        if tl.startswith("files on"):
            continue
        if (storage_name and storage_name.lower() in tl) or "sdk_gphone" in tl:
            found.append((e["index"], t))

    def rank(item):
        ll = item[1].lower()
        if preferred and ll == preferred.lower():
            return 0
        if storage_name and ll == storage_name.lower():
            return 1
        if storage_name and storage_name.lower() in ll:
            return 2
        return 3

    return sorted(found, key=rank)


def _roots_list_showing(device):
    """True when the roots drawer (or an inline roots list) appears to be on
    screen: one of its well-known category labels is visible."""
    for e in _elements(device):
        if _text_of(e, "text").lower() in _NON_ROOT_LABELS:
            return True
    return False


def _open_drawer(device, storage_name=_TARGET_STORAGE):
    """Best effort: make storage entries visible, either by confirming the
    roots list is already showing or by opening it with the 'Show roots'
    hamburger. Returns True when a storage entry is visible."""
    if _root_entries(device, storage_name):
        return True
    candidates = []
    idx = device.find(description="Show roots", clickable=True)
    if idx is None:
        idx = device.find(description="Show roots")
    if idx is not None:
        candidates.append(idx)
    for e in _elements(device):
        d = _text_of(e, "description", "hint").lower()
        if e.get("clickable") and ("roots" in d or "drawer" in d
                                   or "navigation" in d):
            candidates.append(e["index"])
    if not _roots_list_showing(device):
        # Last resort: the hamburger is the leading unlabelled chrome button.
        for e in _elements(device):
            if (e.get("clickable")
                    and not _text_of(e, "text", "description", "hint")):
                candidates.append(e["index"])
    tried = set()
    for cand in candidates:
        if cand in tried:
            continue
        tried.add(cand)
        try:
            device.click(cand)
        except Exception:
            continue
        device.settle(1)
        if _root_entries(device, storage_name):
            return True
    return False


def _next_root_entry(device, storage_name, tried, preferred=None, max_scrolls=3):
    """Next untried storage entry from the roots list, scrolling it if the
    remaining entries sit below the fold. (None, None) when exhausted."""
    for attempt in range(max_scrolls + 1):
        _open_drawer(device, storage_name)
        for idx, label in _root_entries(device, storage_name, preferred):
            if label not in tried:
                return idx, label
        if attempt < max_scrolls:
            sig = _screen_signature(device)
            device.scroll("down")
            if _screen_signature(device) == sig:
                break
    return None, None


def _open_folder_and_find_file(device, folder, file_name):
    """Open `folder` from the current listing and confirm the file row is
    present. Leaves the view inside the folder either way."""
    idx = _scrolling_find(device, folder)
    if idx is None:
        return False
    device.click(idx)
    device.settle(1.5)
    return _scrolling_find(device, file_name, stem=_stem(file_name)) is not None


def _goto_source_file(device, storage_name, source_folder, file_name):
    """Enter the storage volume that actually contains source_folder/file_name
    and open that folder. Entries naming `storage_name` are tried first; any
    other volume-looking entry is probed afterwards, because the task's
    storage area can be labelled differently on the device (e.g. only
    'sdk_gphone64_arm64' may be listed). Returns the label of the volume."""
    tried = set()
    saw_root = False
    while True:
        idx, label = _next_root_entry(device, storage_name, tried)
        if idx is None:
            break
        saw_root = True
        tried.add(label)
        device.click(idx)
        device.settle(1.5)
        if _root_entries(device, storage_name):
            continue  # click did not leave the roots list
        if _open_folder_and_find_file(device, source_folder, file_name):
            return label
    if not saw_root:
        raise LookupError(
            f"Storage root '{storage_name}' not found in the Files navigation "
            f"drawer"
        )
    raise LookupError(
        f"File '{file_name}' not found inside '{source_folder}' in any "
        f"storage root of the Files app"
    )


def _navigate_to_folder(device, storage_name, preferred, folder, label="Folder"):
    """Open a top-level `folder` of the storage volume, starting from wherever
    the Files app currently is: roots list -> volume entry -> folder."""
    tried = set()
    saw_root = False
    while True:
        idx, vol = _next_root_entry(device, storage_name, tried, preferred=preferred)
        if idx is None:
            break
        saw_root = True
        tried.add(vol)
        device.click(idx)
        device.settle(1.5)
        if _root_entries(device, storage_name):
            continue
        fidx = _scrolling_find(device, folder)
        if fidx is not None:
            device.click(fidx)
            device.settle(1.5)
            return
    if not saw_root:
        raise LookupError(
            f"{label} '{folder}' could not be reached: no storage root found "
            f"in the Files navigation drawer"
        )
    raise LookupError(f"{label} '{folder}' not found in the storage listing")


def _selection_active(device):
    if device.find(description="More options") is not None:
        return True
    for e in _elements(device):
        t = _text_of(e, "text", "description").lower()
        if "selected" in t:
            return True
    return False


def _select_file(device, file_name):
    """Long-press the file row (exact-name match first) to enter selection
    mode."""
    idx = _scrolling_find(device, file_name, stem=_stem(file_name))
    if idx is None:
        raise LookupError(
            f"File '{file_name}' not found in the source folder listing"
        )
    calls = (
        lambda: device.long_press(idx),
        lambda: device.long_press(index=idx),
        lambda: device.execute({"action_type": "long_press", "index": idx,
                                "duration_seconds": 1.5}),
    )
    pressed = False
    for call in calls:
        try:
            call()
            pressed = True
        except Exception:
            continue
        device.settle(1)
        if _selection_active(device):
            return
    if not pressed:
        raise RuntimeError("device.long_press unavailable; cannot select the file row")
    # Selection state could not be confirmed; the overflow lookup below fails
    # loudly if the row is not actually selected.


def _open_overflow(device):
    idx = device.find(description="More options", clickable=True)
    if idx is None:
        idx = device.find(description="More options")
    if idx is None:
        for e in _elements(device):
            d = _text_of(e, "description", "hint").lower()
            if e.get("clickable") and ("more options" in d or "overflow" in d
                                       or d == "more"):
                idx = e["index"]
                break
    if idx is None:
        raise RuntimeError(
            "Overflow 'More options' button not found — was the file row selected?"
        )
    device.click(idx)
    device.settle(1)


def _click_move_or_cut(device):
    """Invoke the cut half of the move from the selection overflow menu.
    Returns 'picker' when a 'Move to…' style item opens the destination
    picker, 'cut' when a plain 'Cut' item keeps the app in browse mode."""
    for text in ("Move to…", "Move to", "Cut"):
        idx = device.find(text=text)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return "cut" if text == "Cut" else "picker"
    for text, mode in (("Move to", "picker"), ("move to", "picker"),
                       ("Cut", "cut"), ("cut", "cut")):
        idx = device.find(text=text, contains=True)
        if idx is not None:
            device.click(idx)
            device.settle(1)
            return mode
    for e in _elements(device):
        t = _text_of(e, "text").lower()
        if t.startswith("move"):
            device.click(e["index"])
            device.settle(1)
            return "picker"
        if t == "cut":
            device.click(e["index"])
            device.settle(1)
            return "cut"
    raise RuntimeError(
        "Neither 'Move to…' nor 'Cut' was found in the selection overflow menu"
    )


def _is_confirm_label(t):
    tl = t.strip().upper()
    return tl in ("MOVE", "PASTE") or tl.startswith(("MOVE HERE", "PASTE HERE"))


def _click_move_confirm(device):
    """Press the destination picker's MOVE/PASTE confirmation button."""
    for _ in range(3):
        els = _elements(device)
        for e in els:
            if e.get("clickable") and _is_confirm_label(
                    _text_of(e, "text", "description")):
                device.click(e["index"])
                device.settle(2)
                return
        for e in els:
            if _is_confirm_label(_text_of(e, "text", "description")):
                device.click(e["index"])
                device.settle(2)
                return
        device.wait()
    raise RuntimeError("MOVE/PASTE confirmation button not found on the picker screen")


def _click_paste(device):
    """Complete a plain Cut: press the Paste control offered in the
    destination folder (bottom bar, toolbar or overflow menu)."""
    for e in _elements(device):
        t = _text_of(e, "text", "description").lower()
        if t.startswith("paste"):
            device.click(e["index"])
            device.settle(2)
            return
    try:
        _open_overflow(device)
    except RuntimeError:
        pass
    for e in _elements(device):
        t = _text_of(e, "text", "description").lower()
        if t.startswith("paste"):
            device.click(e["index"])
            device.settle(2)
            return
    raise RuntimeError("Paste action not found after Cut")


def _file_visible(device, file_name):
    if device.find(text=file_name) is not None:
        return True
    s = _stem(file_name)
    return s != file_name and device.find(text=s, contains=True) is not None


def _verify_move(device, file_name, source_folder, destination_folder,
                 storage_name, volume_label):
    # The oracle checks both ends: the file must be in the destination folder
    # and must no longer be in the source folder (a move, not a copy).
    _navigate_to_folder(device, storage_name, volume_label, destination_folder,
                        "Destination folder")

    def _dest_hit():
        return True if _file_visible(device, file_name) else None

    if _scroll_until(device, _dest_hit, "down", 5) is None:
        raise RuntimeError(
            f"Move verification failed: '{file_name}' not found in "
            f"'{destination_folder}' after the move"
        )
    if destination_folder != source_folder:
        _navigate_to_folder(device, storage_name, volume_label, source_folder,
                            "Source folder")

        def _src_still():
            return True if device.find(text=file_name) is not None else None

        if _scroll_until(device, _src_still, "down", 7) is not None:
            raise RuntimeError(
                f"Move verification failed: '{file_name}' is still present in "
                f"the source folder '{source_folder}'"
            )
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
            and device.find(text="Recent") is None
            and device.find(text="Downloads") is None
            and device.find(text="Files", contains=True) is None
            and not _root_entries(device, _TARGET_STORAGE)):
        raise RuntimeError(
            f"App '{_APP_NAME}' did not open; no DocumentsUI signature element found"
        )

    # 2) Reach the source file: roots list (drawer or inline — skipped when it
    #    is already showing) -> storage volume (the entry naming
    #    sdk_gphone_x86_64 is preferred; if only sibling volumes are listed,
    #    the volume actually containing the file is used) -> source folder.
    volume_label = _goto_source_file(device, _TARGET_STORAGE, source_folder,
                                     file_name)

    # 3) Long-press the target file row (matched by its exact name) to select it.
    _select_file(device, file_name)

    # 4) Selection-bar overflow -> 'Move to…' (or 'Cut'): cut half of the move.
    _open_overflow(device)
    mode = _click_move_or_cut(device)

    # 5) Destination half: open the destination folder inside the same storage
    #    volume, then confirm (picker MOVE button, or Paste after a plain Cut).
    _navigate_to_folder(device, _TARGET_STORAGE, volume_label, destination_folder,
                        "Destination folder")
    if mode == "picker":
        _click_move_confirm(device)
    else:
        _click_paste(device)

    # 6) Verify both ends: file in destination, gone from source.
    return _verify_move(device, file_name, source_folder, destination_folder,
                        _TARGET_STORAGE, volume_label)
