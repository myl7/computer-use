PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not file_name:
        raise ValueError("binding['file_name'] must be non-empty")

    device.open_app("Markor")
    device.settle(2)

    def find_target():
        idx = device.find(description=f"File {file_name}")
        if idx is not None:
            return idx
        idx = device.find(description=file_name)
        if idx is not None:
            return idx
        idx = device.find(text=file_name)
        if idx is not None:
            return idx

        best = None
        for e in device.elements():
            t = (e.get("text") or "").strip()
            d = (e.get("description") or "").strip()
            if t == file_name or d == file_name or d == f"File {file_name}":
                if e.get("clickable") or e.get("long_clickable"):
                    return e.get("index")
                if best is None:
                    best = e.get("index")
        return best

    target_idx = find_target()
    if target_idx is None:
        for _ in range(15):
            device.scroll(direction="down")
            target_idx = find_target()
            if target_idx is not None:
                break
    if target_idx is None:
        for _ in range(30):
            device.scroll(direction="up")
            target_idx = find_target()
            if target_idx is not None:
                break
    if target_idx is None:
        raise RuntimeError(f"Note not found: {file_name}")

    if hasattr(device, "long_press"):
        device.long_press(index=target_idx)
    else:
        device.execute({"action_type": "long_press", "index": target_idx})
    device.settle(1)

    delete_idx = device.find(description="Delete", clickable=True)
    if delete_idx is None:
        delete_idx = device.find(text="Delete", clickable=True)
    if delete_idx is None:
        delete_idx = device.find(description="Delete")
    if delete_idx is None:
        delete_idx = device.find(text="Delete")
    if delete_idx is None:
        for e in device.elements():
            d = (e.get("description") or "").strip()
            t = (e.get("text") or "").strip()
            if (d == "Delete" or t == "Delete") and (
                e.get("clickable") or e.get("long_clickable")
            ):
                delete_idx = e.get("index")
                break
    if delete_idx is None:
        raise RuntimeError("Delete action not found in selection toolbar")
    device.click(index=delete_idx)
    device.settle(1)

    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        ok_idx = device.find(description="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(description="OK")
    if ok_idx is None:
        for e in device.elements():
            t = (e.get("text") or "").strip()
            d = (e.get("description") or "").strip()
            if (t == "OK" or d == "OK") and e.get("clickable"):
                ok_idx = e.get("index")
                break
    if ok_idx is None:
        raise RuntimeError("Confirmation OK button not found")
    device.click(index=ok_idx)
    device.settle(1)

    return True
