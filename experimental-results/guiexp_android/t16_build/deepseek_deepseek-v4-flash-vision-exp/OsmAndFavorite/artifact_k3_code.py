PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to add as a favorite location marker in OsmAnd."
    }
}

def program(device, binding: dict) -> bool:
    location = binding["location"]

    def is_keyboard_open():
        keyboard_markers = {"Sticker Keyboard", "GIF Keyboard", "More features", "Clipboard", "Settings", "Emoji Keyboard", "Keyboard"}
        for e in device.elements():
            combined = ((e.get("description") or "") + " " + (e.get("text") or "")).lower()
            for marker in keyboard_markers:
                if marker.lower() in combined:
                    return True
        return False

    def find_first_result_generic(search_field_index):
        ignore = {"clear", "close", "cancel", "search", "back", "show on map", "show all", "more", "settings", "clipboard", "gif", "sticker", "keyboard", "emoji", "enter", "done"}
        for e in device.elements():
            if e.get("editable"):
                continue
            if not e.get("clickable"):
                continue
            idx = e["index"]
            if search_field_index is not None and idx <= search_field_index:
                continue
            text = (e.get("text") or "").strip()
            desc = (e.get("description") or "").strip()
            if text == "" and desc == "":
                continue
            if text.lower() in ignore or desc.lower() in ignore:
                continue
            return idx
        return None

    def find_favorite_control():
        for e in device.elements():
            if not e.get("clickable"):
                continue
            desc = (e.get("description") or "").lower()
            text = (e.get("text") or "").lower()
            combined = text + " " + desc
            if "favorite" in combined or "favourite" in combined:
                return e["index"]
        add_idx = device.find(text="Add", clickable=True)
        if add_idx is not None:
            return add_idx
        for symbol in ["★", "☆", "⭐", "star"]:
            idx = device.find(text=symbol, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(description=symbol, clickable=True)
            if idx is not None:
                return idx
        return None

    def find_save_button():
        for text in ["Save", "OK", "Add", "Confirm", "Done"]:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx
        for e in device.elements():
            if e.get("clickable"):
                t = (e.get("text") or "").strip()
                if t.lower() in ["save", "ok", "add", "confirm", "done"]:
                    return e["index"]
        return None

    def ensure_name_field():
        for e in device.elements():
            if e.get("editable") and e.get("hint") == "Name":
                if not (e.get("text") or "").strip():
                    device.click(index=e["index"])
                    device.input_text(location, index=e["index"])
                return

    device.open_app("OsmAnd")
    device.settle(2)

    search_btn = device.find(description="Search", clickable=True)
    if search_btn is None:
        search_btn = device.find(text="Search", clickable=True)
    if search_btn is None:
        for e in device.elements():
            if e.get("clickable") and "search" in (e.get("description") or "").lower():
                search_btn = e["index"]
                break
    if search_btn is None:
        raise RuntimeError("Search button not found")
    device.click(index=search_btn)
    device.settle(1.5)

    search_field = device.find(hint="Type to search all", editable=True)
    if search_field is None:
        for e in device.elements():
            if e.get("editable"):
                search_field = e["index"]
                break
    if search_field is None:
        raise RuntimeError("Search field not found")
    device.input_text(location, index=search_field)
    device.settle(2)

    result_idx = device.find(text=location, clickable=True, editable=False)
    if result_idx is None:
        result_idx = find_first_result_generic(search_field)
    if result_idx is None and is_keyboard_open():
        device.navigate_back()
        device.settle(1)
        result_idx = find_first_result_generic(search_field)
    if result_idx is None:
        device.scroll(direction="down")
        device.settle(1)
        result_idx = find_first_result_generic(search_field)
    if result_idx is None:
        raise RuntimeError("First search result not found")
    device.click(index=result_idx)
    device.settle(2)

    fav_idx = find_favorite_control()
    if fav_idx is None and is_keyboard_open():
        device.navigate_back()
        device.settle(1)
        fav_idx = find_favorite_control()
    if fav_idx is None:
        raise RuntimeError("Favorite control not found on place panel")
    device.click(index=fav_idx)
    device.settle(1.5)

    ensure_name_field()
    save_idx = find_save_button()
    if save_idx is None:
        raise RuntimeError("Save button not found in dialog")
    device.click(index=save_idx)
    device.settle(2)

    return True
