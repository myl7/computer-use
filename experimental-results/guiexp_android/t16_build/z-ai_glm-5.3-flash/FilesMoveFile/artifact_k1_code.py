import re

STORAGE_AREA = "sdk_gphone_x86_64"

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Name (with extension) of the file to move, e.g. note.mp3",
    },
    "source_folder": {
        "type": "string",
        "description": "Top-level folder of the sdk_gphone_x86_64 storage area the file starts in, e.g. Download",
    },
    "destination_folder": {
        "type": "string",
        "description": "Top-level folder of the sdk_gphone_x86_64 storage area the file must end up in, e.g. DCIM",
    },
}


def _find_storage_root(device):
    """Locate the sdk_gphone_x86_64 volume entry (label may be truncated)."""
    idx = device.find(text=STORAGE_AREA, contains=True)
    if idx is None:
        prefix = re.split(r"\d", STORAGE_AREA)[0]  # e.g. 'sdk_gphone'
        idx = device.find(text=prefix, contains=True)
    return idx


def _open_roots_drawer(device):
    """Open the navigation drawer via the hamburger ('Show roots') button."""
    roots = device.find(description="Show roots", clickable=True)
    if roots is None:
        roots = device.find(description="Show roots")
    if roots is None:
        raise LookupError("'Show roots' (hamburger) button not found on screen")
    device.click(roots)
    device.settle(1)


def _find_named_entry(device, name):
    el = device.find(text=name, class_name="TextView")
    if el is None:
        el = device.find(text=name)
    return el


def _scroll_to_entry(device, name, tries=8):
    """Find an entry by exact name, scrolling down as needed."""
    el = _find_named_entry(device, name)
    for _ in range(tries):
        if el is not None:
            return el
        device.scroll("down")
        device.settle(1)
        el = _find_named_entry(device, name)
    return el


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    source_folder = binding["source_folder"]
    destination_folder = binding["destination_folder"]

    # ---- 1. Launch the Files (DocumentsUI) app ----
    device.open_app("Files")
    device.settle(2)
    if (device.find(description="Show roots") is None
            and device.find(text="Downloads") is None
            and device.find(text="Files") is None):
        raise RuntimeError(
            "Files app did not open after open_app('Files'); "
            "no DocumentsUI signature element found on screen")

    # ---- 2. Open the navigation drawer ----
    _open_roots_drawer(device)

    # ---- 3. Enter the sdk_gphone_x86_64 storage root ----
    root = _find_storage_root(device)
    if root is None:
        # drawer may not have been open; retry
        _open_roots_drawer(device)
        root = _find_storage_root(device)
    if root is None:
        raise LookupError(
            f"Storage root '{STORAGE_AREA}' not found in the roots drawer")
    device.click(root)
    device.settle(1.5)

    # ---- 4. Open the source folder ----
    src = _scroll_to_entry(device, source_folder)
    if src is None:
        raise LookupError(
            f"Source folder '{source_folder}' not found under {STORAGE_AREA}")
    device.click(src)
    device.settle(1.5)

    # ---- 5. Long-press the target file row (exact-name match) to select it ----
    target = device.find(text=file_name)
    if target is None:
        device.scroll("down")
        device.settle(1)
        target = device.find(text=file_name)
    if target is None:
        raise LookupError(
            f"File '{file_name}' not visible in source folder '{source_folder}'")
    device.long_press(target)
    device.settle(1)

    # Confirm selection mode engaged (selection bar's overflow appears);
    # otherwise retry on the clickable row itself.
    more = device.find(description="More options", clickable=True)
    if more is None:
        clickable_row = device.find(text=file_name, clickable=True)
        if clickable_row is not None:
            device.long_press(clickable_row)
            device.settle(1)
        more = device.find(description="More options", clickable=True)
    if more is None:
        raise RuntimeError(
            "Selection mode did not activate ('More options' button missing) "
            f"after long-pressing '{file_name}'")
    device.click(more)
    device.settle(1)

    # ---- 6. Cut via the overflow menu's 'Move to…' item ----
    move_item = device.find(text="Move to", contains=True)
    if move_item is None:
        move_item = device.find(text="Cut")
    if move_item is None:
        raise RuntimeError(
            "'Move to…' menu item not found in the selection overflow menu")
    device.click(move_item)
    device.settle(1.5)

    # ---- 7. In the move picker, navigate to the destination folder ----
    _open_roots_drawer(device)

    root = _find_storage_root(device)
    if root is None:
        root = device.find(text="sdk_gphone", contains=True)
    if root is None:
        raise LookupError(
            f"Storage root '{STORAGE_AREA}' not found in the move picker")
    device.click(root)
    device.settle(1)

    dest = _scroll_to_entry(device, destination_folder)
    if dest is None:
        raise LookupError(
            f"Destination folder '{destination_folder}' not found in the move picker")
    device.click(dest)
    device.settle(1)

    # ---- 8. Confirm the move with the MOVE button ----
    move_btn = device.find(text="MOVE", clickable=True)
    if move_btn is None:
        move_btn = device.find(text="MOVE")
    if move_btn is None:
        move_btn = device.find(text="Move", contains=True, clickable=True)
    if move_btn is None:
        raise RuntimeError(
            "MOVE confirmation button not found; destination picker "
            "screen not in expected state")
    device.click(move_btn)
    device.settle(2)

    return True
