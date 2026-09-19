import time

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods",
        "required": True,
    },
    "header_a": {
        "type": "string",
        "description": "Column A header text",
        "required": True,
    },
    "header_b": {
        "type": "string",
        "description": "Column B header text",
        "required": True,
    },
    "val_a2": {
        "type": "integer",
        "description": "Integer for cell A2",
        "required": True,
    },
    "val_b2": {
        "type": "integer",
        "description": "Integer for cell B2",
        "required": True,
    },
    "val_a3": {
        "type": "integer",
        "description": "Integer for cell A3",
        "required": True,
    },
    "val_b3": {
        "type": "integer",
        "description": "Integer for cell B3",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    def ensure_new_spreadsheet():
        if device.find(name="A1", role="table-cell", editable=True) is not None:
            return
        title = device.active_window()
        if "LibreOffice Calc" in title and "Untitled" not in title:
            device.hotkey("ctrl", "n")
            device.wait()
            return
        device.open_app("libreoffice-calc")
        device.wait()
        if device.find(name="A1", role="table-cell", editable=True) is None:
            device.hotkey("ctrl", "n")
            device.wait()

    def set_cell(name, value):
        cell = device.find(name=name, role="table-cell", editable=True)
        if cell is None:
            raise RuntimeError(f"Cell {name} not found or not editable")
        device.input_text(str(value), index=cell)

    def find_desktop():
        target = device.find(name="Desktop", clickable=True)
        if target is not None:
            return target
        target = device.find(text="Desktop", clickable=True)
        if target is not None:
            return target
        target = device.find(contains="Desktop", clickable=True)
        if target is not None:
            return target
        for role in ["toggle-button", "list-item", "table-cell", "push-button", "button", "label"]:
            target = device.find(name="Desktop", role=role, clickable=True)
            if target is not None:
                return target
        elements = device.elements()
        for e in elements:
            if "Desktop" in (e.get("name") or "") or "Desktop" in (e.get("text") or ""):
                if e.get("clickable"):
                    return e["index"]
        return None

    def find_location_field():
        for name in ["Location", "Location:", "Path", "Path:", "File location"]:
            field = device.find(name=name, editable=True)
            if field is not None:
                return field
        elements = device.elements()
        editable = [e for e in elements if e.get("editable")]
        for e in editable:
            text = e.get("text") or ""
            if text == "" or "~" in text or "/" in text:
                return e["index"]
        for e in editable:
            if "location" in ((e.get("name") or "") + (e.get("text") or "")).lower():
                return e["index"]
        if editable:
            return editable[0]["index"]
        return None

    def click_desktop():
        desktop = find_desktop()
        if desktop is not None:
            device.click(index=desktop)
            device.wait()
            device.settle(0.5)
            return
        device.hotkey("ctrl", "l")
        device.wait()
        loc = find_location_field()
        if loc is None:
            raise RuntimeError("Save dialog: could not open location bar")
        device.input_text("~/Desktop", index=loc)
        device.press("enter")
        device.wait()
        device.settle(0.5)

    def find_save_filename_field():
        for name in ["Name", "Name:", "File name", "File name:", "Filename"]:
            field = device.find(name=name, editable=True)
            if field is not None:
                return field
        field = device.find(role="text", editable=True)
        if field is not None:
            return field
        field = device.find(role="combo box", editable=True)
        if field is not None:
            return field
        elements = device.elements()
        candidates = [e for e in elements if e.get("editable")]
        for e in candidates:
            name = (e.get("name") or "") + " " + (e.get("text") or "")
            if any(k in name.lower() for k in ["name", "file", "untitled"]):
                return e["index"]
        for e in candidates:
            if e.get("role") == "text":
                return e["index"]
        for e in candidates:
            text = e.get("text") or ""
            if not text or "untitled" in text.lower():
                return e["index"]
        if candidates:
            return candidates[0]["index"]
        return None

    def save_to_desktop():
        device.hotkey("ctrl", "shift", "s")
        device.wait()
        device.settle(0.5)

        click_desktop()

        field = find_save_filename_field()
        if field is None:
            raise RuntimeError("Save dialog: no editable filename field found")
        device.click(index=field)
        device.hotkey("ctrl", "a")
        device.input_text(binding["file_name"], index=field)
        device.settle(0.5)

        save_btn = device.find(name="Save", role="push-button", clickable=True)
        if save_btn is None:
            save_btn = device.find(name="Save", clickable=True)
        if save_btn is not None:
            device.click(index=save_btn)
        else:
            device.hotkey("alt", "s")
        device.wait()
        device.settle(1)

    ensure_new_spreadsheet()

    set_cell("A1", binding["header_a"])
    set_cell("B1", binding["header_b"])
    set_cell("A2", binding["val_a2"])
    set_cell("B2", binding["val_b2"])
    set_cell("A3", binding["val_a3"])
    set_cell("B3", binding["val_b3"])

    save_to_desktop()

    return True
