PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "The note file name including its extension, e.g. note.txt"
    },
    "text": {
        "type": "string",
        "description": "The text content to write into the note"
    }
}

def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]

    # Split file name into base and extension (including the dot)
    if "." in file_name:
        base, ext = file_name.rsplit(".", 1)
        ext_with_dot = "." + ext
    else:
        base = file_name
        ext_with_dot = ""

    def set_text(index, new_text):
        """Set the text of a field, clearing it first if necessary."""
        elements = device.elements()
        current_text = ""
        for e in elements:
            if e.get("index") == index:
                current_text = e.get("text") or ""
                break
        if current_text.strip():
            # Long press to select all
            device.execute({"action_type": "long_press", "index": index})
            select_all = device.find(text="Select all")
            if select_all is None:
                select_all = device.find(contains="Select all")
            if select_all is not None:
                device.click(select_all)
                # Delete selection
                device.execute({"action_type": "press_key", "key": "KEYCODE_DEL"})
            else:
                # Fallback: move to end and backspace
                device.execute({"action_type": "press_key", "key": "KEYCODE_MOVE_END"})
                for _ in range(len(current_text) + 5):
                    device.execute({"action_type": "press_key", "key": "KEYCODE_DEL"})
        device.input_text(new_text, index=index)

    # 1. Open Markor
    if not device.open_app("Markor"):
        elements = device.elements()
        if not any("Create a new file or folder" in str(e.get("description", "")) for e in elements):
            raise RuntimeError("Could not open Markor")
    device.settle(2)

    # 2. Tap "Create a new file or folder"
    idx = device.find(description="Create a new file or folder", clickable=True)
    if idx is None:
        idx = device.find(description="Create a new file or folder")
    if idx is None:
        idx = device.find(description="Create", clickable=True)
    if idx is None:
        raise RuntimeError("Create new file button not found")
    device.click(idx)
    device.settle(2)

    # 3. Fill the new-file dialog
    elements = device.elements()
    editables = [e for e in elements if e.get("editable")]
    editables.sort(key=lambda e: e.get("index", 0))

    name_field = None
    ext_field = None

    # Identify the name and extension fields by hints/text where possible
    for e in editables:
        hint = (e.get("hint") or "").lower()
        txt = (e.get("text") or "").lower()
        if "my_note" in hint or "name" in hint:
            name_field = e
        elif hint.startswith(".") or txt.startswith("."):
            ext_field = e

    # Fallbacks by order
    if name_field is None:
        remaining = [e for e in editables if e is not ext_field]
        if remaining:
            name_field = remaining[0]
    if ext_field is None:
        remaining = [e for e in editables if e is not name_field]
        if remaining:
            ext_field = remaining[0]

    if name_field is None:
        raise RuntimeError("Could not find name field in create dialog")

    # Put base name into the Name field
    set_text(name_field["index"], base)

    # Handle extension field
    if ext_field is not None:
        current_ext = (ext_field.get("text") or "").strip()
        if ext_with_dot:
            if current_ext != ext_with_dot:
                set_text(ext_field["index"], ext_with_dot)
        else:
            # No extension desired, clear the ext field if it has text
            if current_ext:
                set_text(ext_field["index"], "")
    else:
        # No separate extension field: put the full file name in the Name field
        set_text(name_field["index"], file_name)

    # Confirm with OK
    ok = device.find(text="OK", clickable=True)
    if ok is None:
        ok = device.find(text="OK")
    if ok is None:
        ok = device.find(contains="OK", clickable=True)
    if ok is None:
        raise RuntimeError("OK button not found")
    device.click(ok)
    device.settle(2)

    # 4. Type the note text in the editor
    elements = device.elements()
    candidates = [
        e for e in elements
        if e.get("editable") and e.get("class_name") == "EditText"
    ]
    if not candidates:
        candidates = [e for e in elements if e.get("editable")]
    if not candidates:
        raise RuntimeError("No editable note content field found")

    # Prefer an empty editable field (the main editor)
    target = next(
        (e for e in candidates if not (e.get("text") or "").strip()),
        candidates[0],
    )
    device.input_text(text, index=target["index"])
    device.settle(2)

    # 5. Save the note explicitly
    save_idx = device.find(description="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(text="Save", clickable=True)
    if save_idx is None:
        save_idx = device.find(contains="Save", clickable=True)
    if save_idx is None:
        # If no save button is found, fall back to leaving the editor
        device.navigate_back()
    else:
        device.click(save_idx)
        device.settle(2)
        device.navigate_back()
    device.settle(2)

    return True
