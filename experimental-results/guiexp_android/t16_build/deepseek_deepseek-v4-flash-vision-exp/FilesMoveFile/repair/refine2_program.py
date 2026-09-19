from typing import Dict

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Name of the file to move, with extension, e.g. note.mp3",
        "required": True,
    },
    "source_folder": {
        "type": "string",
        "description": "Folder the file starts in, e.g. Download",
        "required": True,
    },
    "destination_folder": {
        "type": "string",
        "description": "Folder the file must end up in, e.g. DCIM",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    source_folder = binding["source_folder"]
    destination_folder = binding["destination_folder"]

    def do_cut_paste() -> bool:
        # Go back to the storage root.
        device.navigate_back()
        device.settle(1)

        # Open the destination folder.
        dest_el = device.find(text=destination_folder)
        if dest_el is None:
            device.scroll("down")
            device.settle(1)
            dest_el = device.find(text=destination_folder)
        if dest_el is None:
            raise RuntimeError(f"Destination folder '{destination_folder}' not found")
        device.click(index=dest_el)
        device.settle(1)

        # Paste.
        paste_el = device.find(text="Paste", clickable=True)
        if paste_el is None:
            paste_el = device.find(description="Paste", clickable=True)
        if paste_el is None:
            paste_el = device.find(text="Paste")
        if paste_el is None:
            # Try overflow menu.
            more_options = device.find(description="More options", clickable=True)
            if more_options is not None:
                device.click(index=more_options)
                device.settle(0.5)
                paste_el = device.find(text="Paste", clickable=True)
                if paste_el is None:
                    paste_el = device.find(text="Paste")
        if paste_el is None:
            raise RuntimeError("Could not find 'Paste' button")
        device.click(index=paste_el)
        device.settle(2)
        return True

    def do_move_dialog() -> bool:
        # Find the storage root in the move picker.
        root_el = device.find(contains="sdk_gphone")
        if root_el is None:
            # Try opening the drawer.
            show_roots = device.find(description="Show roots", clickable=True)
            if show_roots is None:
                show_roots = device.find(text="Show roots", clickable=True)
            if show_roots is not None:
                device.click(index=show_roots)
                device.settle(1)
                root_el = device.find(contains="sdk_gphone")
        if root_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                root_el = device.find(contains="sdk_gphone")
                if root_el is not None:
                    break
        if root_el is None:
            raise RuntimeError("Could not find storage root in move dialog")
        device.click(index=root_el)
        device.settle(1)

        # Find the destination folder.
        dest_el = device.find(text=destination_folder)
        if dest_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                dest_el = device.find(text=destination_folder)
                if dest_el is not None:
                    break
        if dest_el is None:
            raise RuntimeError(f"Destination folder '{destination_folder}' not found in move dialog")
        device.click(index=dest_el)
        device.settle(1)

        # Click the Move button.
        move_btn = device.find(text="Move", clickable=True)
        if move_btn is None:
            move_btn = device.find(text="Move here", clickable=True)
        if move_btn is None:
            move_btn = device.find(description="Move", clickable=True)
        if move_btn is None:
            move_btn = device.find(description="Move here", clickable=True)
        if move_btn is None:
            raise RuntimeError("Could not find 'Move' button in move dialog")
        device.click(index=move_btn)
        device.settle(1)

        # Handle a possible confirmation dialog.
        confirm_btn = device.find(text="Move", clickable=True)
        if confirm_btn is None:
            confirm_btn = device.find(text="Move here", clickable=True)
        if confirm_btn is not None:
            device.click(index=confirm_btn)
            device.settle(2)
        return True

    # Make sure Files app is in the foreground.
    if device.find(description="Show roots", clickable=True) is None:
        device.open_app("Files")
        device.settle(2)

    # Open the storage roots drawer (hamburger).
    show_roots = device.find(description="Show roots", clickable=True)
    if show_roots is None:
        show_roots = device.find(text="Show roots", clickable=True)
    if show_roots is None:
        raise RuntimeError("Could not find 'Show roots' button to open the storage roots drawer")
    device.click(index=show_roots)
    device.settle(1)

    # Click the device storage root (sdk_gphone_x86_64).
    root_index = None
    for el in device.elements():
        text = el.get("text") or ""
        if "sdk_gphone" in text:
            root_index = el["index"]
            break
    if root_index is None:
        root_index = device.find(contains="sdk_gphone")
    if root_index is None:
        raise RuntimeError("Could not find storage root in drawer")
    device.click(index=root_index)
    device.settle(1)

    # Open the source folder.
    source_el = device.find(text=source_folder)
    if source_el is None:
        device.scroll("down")
        device.settle(1)
        source_el = device.find(text=source_folder)
    if source_el is None:
        raise RuntimeError(f"Source folder '{source_folder}' not found")
    device.click(index=source_el)
    device.settle(1)

    # Long-press the target file.
    file_el = device.find(text=file_name)
    if file_el is None:
        for _ in range(3):
            device.scroll("down")
            device.settle(1)
            file_el = device.find(text=file_name)
            if file_el is not None:
                break
    if file_el is None:
        raise RuntimeError(f"File '{file_name}' not found in source folder")
    device.long_press(index=file_el)
    device.settle(1)

    # Open the selection bar's overflow menu if present.
    more_options = device.find(description="More options", clickable=True)
    if more_options is None:
        more_options = device.find(text="More options", clickable=True)
    if more_options is not None:
        device.click(index=more_options)
        device.settle(0.5)

    # Choose "Cut" or "Move to…".
    cut_el = device.find(text="Cut", clickable=True)
    if cut_el is None:
        cut_el = device.find(text="Cut")
    if cut_el is not None:
        device.click(index=cut_el)
        device.settle(1)
        return do_cut_paste()

    move_el = device.find(text="Move to…", clickable=True)
    if move_el is None:
        move_el = device.find(text="Move to…")
    if move_el is None:
        raise RuntimeError("Could not find 'Cut' or 'Move to…' menu item")
    device.click(index=move_el)
    device.settle(1)
    return do_move_dialog()
