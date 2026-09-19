PARAMS_SCHEMA = {
    "file_name": {"type": "string", "description": "Name of the file to move, with extension"},
    "source_folder": {"type": "string", "description": "Top-level folder containing the file"},
    "destination_folder": {"type": "string", "description": "Top-level folder to move the file to"},
}


def _find_hamburger(device):
    idx = device.find(description="Show roots")
    if idx is not None:
        return idx
    idx = device.find(description="Show navigation drawer")
    if idx is not None:
        return idx
    idx = device.find(text="Show roots")
    if idx is not None:
        return idx
    for e in device.elements():
        desc = (e.get("description") or "").lower()
        text = (e.get("text") or "").lower()
        if ("drawer" in desc or "roots" in desc or "navigation" in desc) and e.get("clickable", False):
            return e["index"]
    return None


def _find_storage_root(device):
    for name in ["sdk_gphone_x86_64", "Internal storage"]:
        idx = device.find(text=name)
        if idx is not None:
            return idx
    idx = device.find(contains="sdk_gphone_x86_64")
    if idx is not None:
        return idx
    idx = device.find(contains="Internal storage")
    if idx is not None:
        return idx
    for e in device.elements():
        text = (e.get("text") or "")
        desc = (e.get("description") or "")
        if "sdk_gphone_x86_64" in text or "sdk_gphone_x86_64" in desc:
            return e["index"]
        if "internal storage" in text.lower() or "internal storage" in desc.lower():
            return e["index"]
    return None


def _find_overflow(device):
    idx = device.find(description="More options")
    if idx is not None:
        return idx
    idx = device.find(description="More actions")
    if idx is not None:
        return idx
    idx = device.find(text="More options")
    if idx is not None:
        return idx
    for e in device.elements():
        desc = (e.get("description") or "").lower()
        text = (e.get("text") or "").lower()
        if ("more options" in desc or "more actions" in desc or "more" in text) and e.get("clickable", False):
            return e["index"]
    return None


def _find_move_action(device):
    for text in ["Move to...", "Move to", "Move", "Cut"]:
        idx = device.find(text=text)
        if idx is not None:
            return idx
    for text in ["Move to", "Move", "Cut"]:
        idx = device.find(contains=text)
        if idx is not None:
            return idx
    return None


def _find_move_button(device):
    for text in ["Move", "Move here", "Move to here", "Paste", "OK"]:
        idx = device.find(text=text)
        if idx is not None:
            return idx
    for e in device.elements():
        text = (e.get("text") or "").lower()
        if ("move" in text or "paste" in text or "ok" in text) and e.get("clickable", False):
            return e["index"]
    return None


def _scroll_to_find(device, text):
    idx = device.find(text=text)
    if idx is not None:
        return idx
    for _ in range(10):
        device.scroll("down")
        idx = device.find(text=text)
        if idx is not None:
            return idx
    for _ in range(10):
        device.scroll("up")
        idx = device.find(text=text)
        if idx is not None:
            return idx
    raise RuntimeError(f"Element with text '{text}' not found after scrolling")


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    source_folder = binding["source_folder"]
    destination_folder = binding["destination_folder"]

    # Open Files app and normalize to the main screen.
    device.open_app("Files")
    device.navigate_back()

    ham = _find_hamburger(device)
    if ham is None:
        device.open_app("Files")
        ham = _find_hamburger(device)
        if ham is None:
            raise RuntimeError("Hamburger button not found in Files app")
    device.click(index=ham)

    root = _find_storage_root(device)
    if root is None:
        raise RuntimeError("Storage root 'sdk_gphone_x86_64' not found in drawer")
    device.click(index=root)

    # Open the source folder.
    src_idx = _scroll_to_find(device, source_folder)
    device.click(index=src_idx)

    # Locate the target file and long-press it.
    file_idx = _scroll_to_find(device, file_name)
    device.execute({"action_type": "long_press", "index": file_idx})

    # Open the selection bar's overflow menu.
    overflow = _find_overflow(device)
    if overflow is None:
        raise RuntimeError("Overflow menu not found after long-press")
    device.click(index=overflow)

    # Click "Move to..." / "Cut".
    move_action = _find_move_action(device)
    if move_action is None:
        raise RuntimeError("Move/Cut action not found in overflow menu")
    device.click(index=move_action)

    # Now navigate to the destination folder and confirm the move/paste.
    dest_idx = device.find(text=destination_folder)
    if dest_idx is None:
        try:
            dest_idx = _scroll_to_find(device, destination_folder)
        except RuntimeError:
            dest_idx = None

    if dest_idx is None:
        # Try the drawer (works in both the picker and the main Files activity).
        ham2 = _find_hamburger(device)
        if ham2 is not None:
            device.click(index=ham2)
            root2 = _find_storage_root(device)
            if root2 is None:
                raise RuntimeError("Storage root not found in drawer")
            device.click(index=root2)
            dest_idx = _scroll_to_find(device, destination_folder)
        else:
            # No drawer: go back to the storage root and open the destination.
            device.navigate_back()
            dest_idx = _scroll_to_find(device, destination_folder)

    if dest_idx is None:
        raise RuntimeError(f"Destination folder '{destination_folder}' not found")

    device.click(index=dest_idx)

    move_btn = _find_move_button(device)
    if move_btn is None:
        raise RuntimeError("Move/Paste button not found")
    device.click(index=move_btn)

    return True
