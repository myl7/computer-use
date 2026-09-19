PARAMS_SCHEMA = {
    "file_name": {"type": "str", "description": "note file name including extension, e.g. note.txt"},
    "text": {"type": "str", "description": "text content of the note"},
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    text = binding["text"]
    name_part = file_name.rsplit(".", 1)[0]
    ext_part = "." + file_name.rsplit(".", 1)[1]

    device.open_app("Markor")

    # open create dialog
    idx = device.find(description="Create a new file or folder")
    if idx is None:
        raise RuntimeError("create button not found")
    device.click(idx)

    # fill name field (replace pre-filled placeholder)
    name_idx = device.find(hint=name_part)
    if name_idx is None:
        # fallback: first editable field
        for el in device.elements():
            if el.get("editable"):
                name_idx = el["index"]
                break
    if name_idx is None:
        raise RuntimeError("name field not found")
    device.input_text(name_part, index=name_idx)

    # fill extension field
    ext_idx = device.find(hint=ext_part)
    if ext_idx is None:
        candidates = [el["index"] for el in device.elements()
                      if el.get("editable") and el["index"] != name_idx]
        if not candidates:
            raise RuntimeError("extension field not found")
        ext_idx = candidates[0]
    device.input_text(ext_part, index=ext_idx)

    # select Plain Text template if present, then OK
    plain = device.find(text="Plain Text")
    if plain is not None:
        device.click(plain)
    ok_idx = device.find(text="OK")
    if ok_idx is None:
        raise RuntimeError("OK button not found")
    device.click(ok_idx)

    # editor: type text
    editor_idx = device.find(editable=True)
    if editor_idx is None:
        raise RuntimeError("editor not found")
    device.input_text(text, index=editor_idx)

    # save
    save_idx = device.find(description="Save")
    if save_idx is None:
        raise RuntimeError("save button not found")
    device.click(save_idx)

    # leave editor to ensure save
    device.navigate_back()

    return True
