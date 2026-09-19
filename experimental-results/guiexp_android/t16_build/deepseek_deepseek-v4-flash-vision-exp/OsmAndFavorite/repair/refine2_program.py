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

    def is_ignored_element(e):
        text = (e.get("text") or "").strip()
        desc = (e.get("description") or "").strip()
        combined = (text + " " + desc).lower()
        if any(s in combined for s in ["settings notification", "location requests", "wifi signal", "phone signal", "battery charging"]):
            return True
        ignore = {"clear", "close", "cancel", "search", "back", "show on map", "show all", "more", "settings", "clipboard", "gif", "sticker", "keyboard", "emoji", "enter", "done", "increase search radius", "navigate up"}
        if text.lower() in ignore or desc.lower() in ignore:
            return True
        return False

    def find_search_field():
        idx = device.find(hint="Type to search all", editable=True)
        if idx is not None:
            return idx
        idx = device.find(editable=True)
        if idx is not None:
            return idx
        for e in device.elements():
            if e.get("editable"):
                return e["index"]
        return None

    def find_first_result_generic(search_field_index):
        for e in device.elements():
            if e.get("editable"):
                continue
            if not e.get("clickable"):
                continue
            idx = e["index"]
            if search_field_index is not None and idx <= search_field_index:
                continue
            if is_ignored_element(e):
                continue
            text = (e.get("text") or "").strip()
            desc = (e.get("description") or "").strip()
            if text == "" and desc == "":
                continue
            return idx
        return None

    def find_matching_result(search_field_index):
        location_lower = location.lower()
        primary = location.split(',')[0].strip().lower()
        for e in device.elements():
            if e.get("editable"):
                continue
            if not e.get("clickable"):
                continue
            idx = e["index"]
            if search_field_index is not None and idx <= search_field_index:
                continue
            if is_ignored_element(e):
                continue
            text = (e.get("text") or "").strip()
            desc = (e.get("description") or "").strip()
            if text == "" and desc == "":
                continue
            text_lower = text.lower()
            desc_lower = desc.lower()
            if text_lower == location_lower or desc_lower == location_lower:
                return idx
            if location_lower in text_lower or location_lower in desc_lower:
                return idx
            if primary and primary != location_lower:
                if text_lower == primary:
                    return idx
                if primary in desc_lower:
                    return idx
        return None

    def find_show_on_map():
        idx = device.find(text="SHOW ON MAP", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="SHOW ON MAP", clickable=True)
        if idx is not None:
            return idx
        for e in device.elements():
            if e.get("clickable") and "show on map" in (e.get("text") or "").lower():
                return e["index"]
        return None

    def find_increase_search_radius():
        idx = device.find(text="INCREASE SEARCH RADIUS")
        if idx is not None:
            return idx
        idx = device.find(description="INCREASE SEARCH RADIUS")
        if idx is not None:
            return idx
        for e in device.elements():
            t = (e.get("text") or "").strip().lower()
            d = (e.get("description") or "").strip().lower()
            if "increase search radius" in t or "increase search radius" in d:
                return e["index"]
        return None

    def find_favorite_control():
        for e in device.elements():
            if not e.get("clickable"):
                continue
            desc = (e.get("description") or "").lower()
            text = (e.get("text") or "").lower()
            combined = text + " " + desc
            if "marker" in combined and "favorite" not in combined and "favourite" not in combined:
                continue
            if "favorite" in combined or "favourite" in combined:
                return e["index"]
        add_idx = device.find(text="Add", clickable=True)
        if add_idx is not None:
            for e in device.elements():
                if e["index"] == add_idx:
                    combined = ((e.get("description") or "") + " " + (e.get("text") or "")).lower()
                    if "marker" not in combined:
                        return add_idx
                    break
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

    search_field = find_search_field()
    if search_field is None:
        raise RuntimeError("Search field not found")

    clear_btn = device.find(description="Clear", clickable=True)
    if clear_btn is not None:
        device.click(index=clear_btn)
        device.settle(0.5)

    device.input_text(location, index=search_field)
    device.settle(2)

    search_field = find_search_field()

    result_idx = find_matching_result(search_field)
    if result_idx is None:
        for _ in range(3):
            increase_idx = find_increase_search_radius()
            if increase_idx is None:
                break
            device.click(index=increase_idx)
            device.settle(2)
            search_field = find_search_field()
            result_idx = find_matching_result(search_field)
            if result_idx is not None:
                break
        if result_idx is None:
            result_idx = find_first_result_generic(search_field)

    if result_idx is not None:
        device.click(index=result_idx)
        device.settle(2)
    else:
        show_on_map = find_show_on_map()
        if show_on_map is not None:
            device.click(index=show_on_map)
            device.settle(2)
        else:
            if is_keyboard_open():
                device.keyboard_enter()
                device.settle(2)
                result_idx = find_matching_result(None)
                if result_idx is not None:
                    device.click(index=result_idx)
                    device.settle(2)
                else:
                    show_on_map = find_show_on_map()
                    if show_on_map is not None:
                        device.click(index=show_on_map)
                        device.settle(2)

    fav_idx = find_favorite_control()
    if fav_idx is None and is_keyboard_open():
        device.navigate_back()
        device.settle(1)
        fav_idx = find_favorite_control()
    if fav_idx is None:
        if is_keyboard_open():
            device.keyboard_enter()
            device.settle(1.5)
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
