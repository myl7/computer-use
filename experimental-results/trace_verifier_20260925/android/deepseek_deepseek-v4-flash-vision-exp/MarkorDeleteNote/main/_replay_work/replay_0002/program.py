import os

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

    # Markor often displays file names without their final extension.
    base_name = os.path.splitext(file_name)[0]

    device.open_app("Markor")
    device.settle(2)

    def matches(name: str) -> bool:
        return name == file_name or name == base_name

    def find_target():
        # Fast path via device.find for both full and base names.
        for candidate in (file_name, base_name):
            idx = device.find(description=f"File {candidate}")
            if idx is not None:
                return idx
            idx = device.find(description=candidate)
            if idx is not None:
                return idx
            idx = device.find(text=candidate)
            if idx is not None:
                return idx

        best = None
        for e in device.elements():
            t = (e.get("text") or "").strip()
            d = (e.get("description") or "").strip()
            # Check exact match, including a "File " prefix stripped from description.
            if matches(t) or matches(d) or matches(d.replace("File ", "", 1)):
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

    # Long-press the target row to enter selection mode.
    if hasattr(device, "long_press"):
        device.long_press(index=target_idx)
    else:
        device.execute({"action_type": "long_press", "index": target_idx})
    device.settle(1)

    # Find the Delete action in the selection toolbar.
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

    # Confirm the deletion dialog. The positive button may be "OK" or "Delete".
    ok_idx = None
    ok_idx = device.find(text="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="OK")
    if ok_idx is None:
        ok_idx = device.find(description="OK", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(description="OK")
    if ok_idx is None:
        ok_idx = device.find(text="Delete", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(text="Delete")
    if ok_idx is None:
        ok_idx = device.find(description="Delete", clickable=True)
    if ok_idx is None:
        ok_idx = device.find(description="Delete")
    if ok_idx is None:
        for e in device.elements():
            t = (e.get("text") or "").strip()
            d = (e.get("description") or "").strip()
            if (t in ("OK", "Delete") or d in ("OK", "Delete")) and e.get("clickable"):
                ok_idx = e.get("index")
                break
    if ok_idx is None:
        raise RuntimeError("Confirmation button not found")
    device.click(index=ok_idx)
    device.settle(1)

    return True
