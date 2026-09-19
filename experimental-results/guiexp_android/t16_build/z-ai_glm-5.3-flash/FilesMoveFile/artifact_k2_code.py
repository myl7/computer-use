PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "File to move, name with extension (e.g. note.mp3)",
    },
    "source_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level folder inside sdk_gphone_x86_64 the file starts in (e.g. Download)",
    },
    "destination_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level folder inside sdk_gphone_x86_64 the file must end up in (e.g. DCIM)",
    },
}

STORAGE_NAME = "sdk_gphone_x86_64"
STORAGE_PREFIX = "sdk_gphone"


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    source_folder = binding["source_folder"]
    destination_folder = binding["destination_folder"]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def find_storage_entry():
        """Locate the storage-root row (drawer / picker), preferring
        clickable exact matches over title-like text."""
        for criteria in (
            {"text": STORAGE_NAME, "clickable": True},
            {"text": STORAGE_NAME},
            {"text": STORAGE_NAME, "contains": True, "clickable": True},
            {"text": STORAGE_PREFIX, "contains": True},
        ):
            idx = device.find(**criteria)
            if idx is not None:
                return idx
        return None

    def open_root_drawer():
        roots = device.find(description="Show roots", clickable=True)
        if roots is not None:
            device.click(roots)
            device.settle(1.5)

    def select_storage_root():
        """Make sure the navigation drawer is open, then enter the
        storage root (works in FilesActivity and in the Move-to picker)."""
        if find_storage_entry() is None:
            open_root_drawer()
        if find_storage_entry() is None:
            # the previous click may have closed an already-open drawer
            open_root_drawer()
        idx = find_storage_entry()
        if idx is None:
            raise LookupError(
                f"storage root '{STORAGE_NAME}' not found in the roots drawer")
        device.click(idx)
        device.settle(1.5)

    def scroll_to_and_click(name, what, tries=10):
        for _ in range(tries):
            idx = device.find(text=name)
            if idx is None:
                idx = device.find(text=name, class_name="TextView")
            if idx is not None:
                device.click(idx)
                device.settle(1.5)
                return
            device.scroll("down")
            device.settle(1)
        raise LookupError(f"{what} '{name}' not found on screen")

    # ------------------------------------------------------------------
    # 1. launch the Files app (DocumentsUI)
    # ------------------------------------------------------------------
    device.open_app("Files")
    device.settle(2)
    if (device.find(description="Show roots") is None
            and device.find(text="Downloads") is None
            and device.find(text="Files") is None):
        raise RuntimeError("Files app (DocumentsUI) did not open after open_app('Files')")

    # ------------------------------------------------------------------
    # 2-3. drawer -> storage root -> source folder
    # ------------------------------------------------------------------
    select_storage_root()
    scroll_to_and_click(source_folder, "source folder")

    # ------------------------------------------------------------------
    # 4. locate the target file row (exact name) and long-press it to
    #    enter selection mode
    # ------------------------------------------------------------------
    def find_file_row():
        idx = device.find(text=file_name, class_name="TextView")
        if idx is None:
            idx = device.find(text=file_name)
        return idx

    target = find_file_row()
    for _ in range(10):
        if target is not None:
            break
        device.scroll("down")
        device.settle(1)
        target = find_file_row()
    if target is None:
        raise LookupError(f"file '{file_name}' not found in '{source_folder}'")

    device.long_press(target)
    device.settle(1)
    if device.find(description="More options", clickable=True) is None:
        # selection may not have registered; retry once on a fresh lookup
        target = find_file_row()
        if target is None:
            raise RuntimeError("file row disappeared while entering selection mode")
        device.long_press(target)
        device.settle(1)
    if device.find(description="More options", clickable=True) is None:
        raise RuntimeError("selection mode not active: 'More options' button missing")

    # ------------------------------------------------------------------
    # 5. open the selection bar's overflow menu
    # ------------------------------------------------------------------
    more = device.find(description="More options", clickable=True)
    device.click(more)
    device.settle(1)

    # ------------------------------------------------------------------
    # 6-9. primary path: 'Move to…' opens the destination picker
    # ------------------------------------------------------------------
    move_item = device.find(text="Move to…")
    if move_item is None:
        move_item = device.find(text="Move to", contains=True)
    if move_item is not None:
        device.click(move_item)
        device.settle(1.5)

        # picker: storage root -> destination folder -> MOVE
        select_storage_root()
        scroll_to_and_click(destination_folder, "destination folder")

        move_btn = device.find(text="MOVE", clickable=True)
        if move_btn is None:
            move_btn = device.find(text="MOVE")
        if move_btn is None:
            raise RuntimeError("MOVE confirmation button not found on picker screen")
        device.click(move_btn)
        device.settle(2)
        return True

    # ------------------------------------------------------------------
    # fallback path: classic cut / paste via the overflow menu
    # ------------------------------------------------------------------
    cut_item = device.find(text="Cut")
    if cut_item is None:
        cut_item = device.find(text="Cut", contains=True)
    if cut_item is None:
        raise RuntimeError("neither 'Move to…' nor 'Cut' found in overflow menu")
    device.click(cut_item)
    device.settle(1)

    select_storage_root()
    scroll_to_and_click(destination_folder, "destination folder")

    paste_btn = device.find(text="Paste", clickable=True)
    if paste_btn is None:
        paste_btn = device.find(text="Paste")
    if paste_btn is None:
        # Paste may live in the overflow menu of the selection bar
        more2 = device.find(description="More options", clickable=True)
        if more2 is not None:
            device.click(more2)
            device.settle(1)
            paste_btn = device.find(text="Paste")
    if paste_btn is None:
        raise RuntimeError("Paste control not found in destination folder")
    device.click(paste_btn)
    device.settle(2)

    return True
