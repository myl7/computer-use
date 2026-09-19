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

    def settle(t=1):
        device.settle(t)

    def is_at_storage_root():
        # The storage root title contains "Files on sdk_gphone".
        return device.find(contains="Files on sdk_gphone") is not None

    def find_storage_root_element():
        """Find the storage root element, preferring the actual storage name
        over the 'Files on ...' title."""
        for el in device.elements():
            text = el.get("text") or ""
            if "sdk_gphone" in text and not text.startswith("Files on"):
                return el["index"]
        # Fallback to description
        for el in device.elements():
            desc = el.get("description") or ""
            if "sdk_gphone" in desc and not desc.startswith("Files on"):
                return el["index"]
        return None

    def find_non_editable_text(text):
        for el in device.elements():
            if el.get("text") == text and not el.get("editable"):
                return el["index"]
        return None

    def open_storage_root():
        show_roots = device.find(description="Show roots")
        if show_roots is None:
            show_roots = device.find(text="Show roots")
        if show_roots is None:
            device.open_app("Files")
            device.settle(2)
            show_roots = device.find(description="Show roots")
            if show_roots is None:
                show_roots = device.find(text="Show roots")
        if show_roots is None:
            raise RuntimeError("Could not find 'Show roots' button")
        device.click(index=show_roots)
        device.settle(1)

        root_index = find_storage_root_element()
        if root_index is None:
            root_index = device.find(contains="sdk_gphone")
        if root_index is None:
            raise RuntimeError("Could not find storage root in drawer")
        device.click(index=root_index)
        device.settle(1)

    def open_folder(folder_name):
        def find_folder_el():
            # Prefer a clickable element whose text exactly matches.
            el = device.find(text=folder_name, clickable=True)
            if el is not None:
                return el
            # A clickable row may expose the folder name as a content description.
            el = device.find(description=folder_name, clickable=True)
            if el is not None:
                return el
            # The row may contain the folder name in its text or description.
            el = device.find(contains=folder_name, clickable=True)
            if el is not None:
                return el
            # Fall back to non-clickable text/description nodes.
            el = device.find(text=folder_name)
            if el is not None:
                return el
            el = device.find(description=folder_name)
            if el is not None:
                return el
            return None

        folder_el = find_folder_el()
        if folder_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                folder_el = find_folder_el()
                if folder_el is not None:
                    break
        if folder_el is None:
            raise RuntimeError(f"Folder '{folder_name}' not found")

        device.click(index=folder_el)
        device.settle(1)

        # If the click didn't leave the storage root, try to find a clickable row
        # that contains the folder name by scanning the element list.
        if is_at_storage_root():
            for el in device.elements():
                if el.get("clickable") and folder_name in (el.get("text") or ""):
                    if el["index"] != folder_el:
                        device.click(index=el["index"])
                        device.settle(1)
                        return
            for el in device.elements():
                if el.get("clickable") and folder_name in (el.get("description") or ""):
                    if el["index"] != folder_el:
                        device.click(index=el["index"])
                        device.settle(1)
                        return
            # If still at root, clicking the non-clickable text node may not
            # trigger the row. As a last resort, try the original text node again.
            device.click(index=folder_el)
            device.settle(1)

    def find_file(name):
        file_el = device.find(text=name)
        if file_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                file_el = device.find(text=name)
                if file_el is not None:
                    break
        if file_el is None:
            # Fallback: use the Search feature to locate the file.
            search_el = device.find(description="Search", clickable=True)
            if search_el is None:
                search_el = device.find(text="Search", clickable=True)
            if search_el is not None:
                device.click(index=search_el)
                device.settle(1)
                input_el = device.find(editable=True)
                if input_el is None:
                    input_el = device.find(description="Search", editable=True)
                if input_el is not None:
                    device.input_text(name, index=input_el)
                    device.settle(1)
                    device.keyboard_enter()
                    device.settle(2)
                    file_el = find_non_editable_text(name)
                    if file_el is None:
                        for el in device.elements():
                            if name in (el.get("text") or "") and not el.get("editable"):
                                file_el = el["index"]
                                break
        if file_el is None:
            raise RuntimeError(f"File '{name}' not found")
        return file_el

    def do_cut_paste():
        device.navigate_back()
        device.settle(1)

        open_folder(destination_folder)

        paste_el = device.find(text="Paste", clickable=True)
        if paste_el is None:
            paste_el = device.find(description="Paste", clickable=True)
        if paste_el is None:
            paste_el = device.find(text="Paste")
        if paste_el is None:
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

    def do_move_dialog():
        # Open the drawer to reveal storage roots.
        show_roots = device.find(description="Show roots", clickable=True)
        if show_roots is None:
            show_roots = device.find(text="Show roots", clickable=True)
        if show_roots is not None:
            device.click(index=show_roots)
            device.settle(1)

        root_el = find_storage_root_element()
        if root_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                root_el = find_storage_root_element()
                if root_el is not None:
                    break
        if root_el is None:
            root_el = device.find(contains="sdk_gphone")
        if root_el is None:
            raise RuntimeError("Could not find storage root in move dialog")
        device.click(index=root_el)
        device.settle(1)

        # Find and click the destination folder in the picker.
        dest_el = device.find(text=destination_folder, clickable=True)
        if dest_el is None:
            dest_el = device.find(description=destination_folder, clickable=True)
        if dest_el is None:
            dest_el = device.find(contains=destination_folder, clickable=True)
        if dest_el is None:
            dest_el = device.find(text=destination_folder)
        if dest_el is None:
            for _ in range(3):
                device.scroll("down")
                device.settle(1)
                dest_el = device.find(text=destination_folder, clickable=True)
                if dest_el is None:
                    dest_el = device.find(description=destination_folder, clickable=True)
                if dest_el is None:
                    dest_el = device.find(contains=destination_folder, clickable=True)
                if dest_el is None:
                    dest_el = device.find(text=destination_folder)
                if dest_el is not None:
                    break
        if dest_el is None:
            raise RuntimeError(f"Destination folder '{destination_folder}' not found in move dialog")
        device.click(index=dest_el)
        device.settle(1)

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

        confirm_btn = device.find(text="Move", clickable=True)
        if confirm_btn is None:
            confirm_btn = device.find(text="Move here", clickable=True)
        if confirm_btn is not None:
            device.click(index=confirm_btn)
            device.settle(2)
        return True

    # Main flow: navigate to the storage root and open the source folder.
    open_storage_root()

    open_folder(source_folder)

    # Long-press the target file.
    file_el = find_file(file_name)
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
        move_el = device.find(text="Move to", clickable=True)
    if move_el is None:
        move_el = device.find(text="Move to")
    if move_el is None:
        raise RuntimeError("Could not find 'Cut' or 'Move to…' menu item")
    device.click(index=move_el)
    device.settle(1)
    return do_move_dialog()
