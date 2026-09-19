import os
import re

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including extension, e.g. note.txt"
    },
    "text": {
        "type": "string",
        "description": "The note's text content"
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    note_text = binding["text"]

    # Split into base and extension (extension includes the dot)
    base, ext = os.path.splitext(file_name)

    # 1. Open Markor
    device.open_app("Markor")
    device.wait()

    # 2. Tap "Create a new file or folder"
    create_btn = device.find(description="Create a new file or folder", clickable=True)
    if create_btn is None:
        for e in device.elements():
            desc = (e.get("description") or "").lower()
            if "new file" in desc or "create" in desc:
                create_btn = e["index"]
                break
    if create_btn is None:
        raise RuntimeError("Could not find 'Create a new file or folder' button")
    device.click(index=create_btn)
    device.wait()

    # 3. Fill the Name field with the base name
    name_field = device.find(hint="my_note", editable=True)
    if name_field is None:
        editable_fields = [e for e in device.elements() if e.get("editable")]
        if not editable_fields:
            raise RuntimeError("No editable field found in create dialog")
        name_field = None
        for e in editable_fields:
            hint = (e.get("hint") or "").lower()
            if "name" in hint:
                name_field = e["index"]
                break
        if name_field is None:
            name_field = editable_fields[0]["index"]
    device.input_text(base, index=name_field)
    device.wait()

    # 4. Set the extension field
    elems = device.elements()

    # Try an editable extension field
    ext_editable = None
    for e in elems:
        if e.get("editable") and e.get("index") != name_field:
            ext_editable = e["index"]
            break

    if ext_editable is not None:
        current_text = ""
        for e in elems:
            if e.get("index") == ext_editable:
                current_text = e.get("text") or ""
                break
        if current_text:
            device.click(index=ext_editable)
            device.wait()
            device.adb_shell("input", "keyevent", "KEYCODE_MOVE_END")
            for _ in range(len(current_text)):
                device.adb_shell("input", "keyevent", "KEYCODE_DEL")
        device.input_text(ext, index=ext_editable)
        device.wait()
    else:
        # Try a spinner / dropdown for the extension
        ext_spinner = None
        for e in elems:
            text = e.get("text") or ""
            desc = (e.get("description") or "").lower()
            if e.get("clickable") and not e.get("editable"):
                if re.match(r"^\.\w+$", text) or "extension" in desc or "file type" in desc:
                    ext_spinner = e["index"]
                    break
        if ext_spinner is not None:
            current_text = ""
            for e in elems:
                if e.get("index") == ext_spinner:
                    current_text = e.get("text") or ""
                    break
            if current_text != ext:
                device.click(index=ext_spinner)
                device.wait()
                option = device.find(text=ext, clickable=True)
                if option is None:
                    option = device.find(contains=ext, clickable=True)
                if option is None:
                    raise RuntimeError(f"Could not find extension option '{ext}' in dropdown")
                device.click(index=option)
                device.wait()
        else:
            if ext and ext != ".txt":
                raise RuntimeError("Could not set extension field for extension " + ext)

    # 5. Confirm with OK
    ok_btn = device.find(text="OK", clickable=True)
    if ok_btn is None:
        raise RuntimeError("OK button not found")
    device.click(index=ok_btn)
    device.wait()

    # 6. The new document is open; type the note text
    fields = [e for e in device.elements() if e.get("editable") and e.get("clickable")]
    if not fields:
        raise RuntimeError("Markor: no editable note field on DocumentActivity")
    target = next((e for e in fields if not (e.get("text") or "").strip()), fields[0])
    device.input_text(note_text, index=target["index"])
    device.wait()

    # 7. Save by leaving the editor
    device.navigate_back()
    device.wait()

    return True
