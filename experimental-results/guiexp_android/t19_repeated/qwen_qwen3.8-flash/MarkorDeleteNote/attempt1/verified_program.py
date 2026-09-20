PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    file_name = str(binding.get("file_name", "")).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is missing")

    def _s(value):
        return str(value if value is not None else "").strip()

    if "." in file_name:
        stem = file_name.rsplit(".", 1)[0]
        ext = file_name[len(stem):]
    else:
        stem = file_name
        ext = ""

    primary = []
    secondary = []

    def _add_primary(value):
        value = _s(value)
        if value and value not in primary:
            primary.append(value)

    def _add_secondary(value):
        value = _s(value)
        if value and value not in secondary:
            secondary.append(value)

    _add_primary(file_name)
    if not ext:
        _add_primary(file_name + ".md")
        _add_primary(file_name + ".txt")
    else:
        _add_secondary(stem)

    all_candidates = primary + secondary

    device.open_app("Markor")
    device.settle(1.0)

    def _is_file_row(element):
        return _s(element.get("description")).startswith("File ")

    def _element(index):
        if index is None:
            return None
        for element in device.elements():
            if element.get("index") == index:
                return element
        return None

    def _row_index_for(candidates):
        if not candidates:
            return None

        for candidate in candidates:
            idx = device.find(description=f"File {candidate}", clickable=True)
            if idx is not None:
                return idx

        for candidate in candidates:
            idx = device.find(text=candidate, clickable=True)
            if idx is not None:
                return idx

        for candidate in candidates:
            idx = device.find(hint=candidate, clickable=True)
            if idx is not None:
                return idx

        elements = device.elements()

        for candidate in candidates:
            for element in elements:
                description = _s(element.get("description"))
                text = _s(element.get("text"))
                hint = _s(element.get("hint"))
                if (
                    description == f"File {candidate}"
                    or description == candidate
                    or text == candidate
                    or hint == candidate
                ):
                    if element.get("clickable"):
                        return element.get("index")

        for candidate in candidates:
            for element in elements:
                description = _s(element.get("description"))
                text = _s(element.get("text"))
                hint = _s(element.get("hint"))
                if (
                    description == f"File {candidate}"
                    or description == candidate
                    or text == candidate
                    or hint == candidate
                ):
                    return element.get("index")

        return None

    def _signature():
        items = []
        for element in device.elements():
            items.append(
                (
                    _s(element.get("description")),
                    _s(element.get("text")),
                    _s(element.get("hint")),
                    bool(element.get("clickable")),
                )
            )
        return tuple(sorted(items))

    def _scroll_find(direction, candidates, limit=50):
        if not candidates:
            return None

        last_signature = _signature()
        for _ in range(limit):
            device.scroll(direction)
            device.settle(0.3)

            signature = _signature()
            idx = _row_index_for(candidates)
            if idx is not None:
                return idx

            if signature == last_signature:
                break
            last_signature = signature

        return None

    def _find_with_scroll(candidates):
        if not candidates:
            return None

        idx = _row_index_for(candidates)
        if idx is not None:
            return idx

        idx = _scroll_find("down", candidates)
        if idx is not None:
            return idx

        idx = _scroll_find("up", candidates)
        if idx is not None:
            return idx

        idx = _scroll_find("down", candidates)
        return idx

    def _find_target():
        idx = _find_with_scroll(primary)
        if idx is not None:
            return idx

        idx = _find_with_scroll(secondary)
        return idx

    target_idx = _find_target()
    if target_idx is None:
        device.navigate_back()
        device.settle(0.5)
        target_idx = _find_target()

    if target_idx is None:
        raise RuntimeError(f"Could not find Markor file row for {file_name!r}")

    def _long_press(index):
        try:
            device.long_press(index=index)
        except AttributeError:
            device.execute({"action_type": "long_press", "index": index})
        except TypeError:
            device.execute({"action_type": "long_press", "index": index})

    _long_press(target_idx)
    device.settle(1.0)

    def _find_delete():
        for _ in range(8):
            idx = device.find(description="Delete", clickable=True)
            if idx is not None:
                element = _element(idx)
                if element is None or not _is_file_row(element):
                    return idx

            for element in device.elements():
                if not element.get("clickable"):
                    continue
                if _is_file_row(element):
                    continue

                description = _s(element.get("description")).lower()
                hint = _s(element.get("hint")).lower()
                if description == "delete" or hint == "delete":
                    return element.get("index")

            for element in device.elements():
                if not element.get("clickable"):
                    continue
                if _is_file_row(element):
                    continue

                text = _s(element.get("text")).lower()
                if text == "delete":
                    return element.get("index")

            more = device.find(description="More options", clickable=True)
            if more is not None:
                device.click(index=more)
                device.settle(0.5)

                for element in device.elements():
                    if not element.get("clickable"):
                        continue
                    if _is_file_row(element):
                        continue

                    text = _s(element.get("text")).lower()
                    description = _s(element.get("description")).lower()
                    if text == "delete" or description == "delete":
                        return element.get("index")

                device.navigate_back()
                device.settle(0.5)

            device.settle(0.5)

        return None

    delete_idx = _find_delete()
    if delete_idx is None:
        target_idx = _find_target()
        if target_idx is not None:
            _long_press(target_idx)
            device.settle(1.0)
            delete_idx = _find_delete()

    if delete_idx is None:
        raise RuntimeError("Delete action not found after selecting target note")

    device.click(index=delete_idx)
    device.settle(1.0)

    def _dialog_present():
        cancel_texts = ("Cancel", "Close", "Dismiss")

        for element in device.elements():
            if element.get("clickable") and _s(element.get("text")) in cancel_texts:
                return True

        for text in cancel_texts:
            if device.find(text=text, clickable=True) is not None:
                return True

        for description in cancel_texts:
            if device.find(description=description, clickable=True) is not None:
                return True

        return False

    def _find_ok():
        ok_texts = ("OK", "Ok", "ok")
        elements = device.elements()

        for element in elements:
            if element.get("clickable") and _s(element.get("text")) in ok_texts:
                if "Button" in _s(element.get("class_name")):
                    return element.get("index")

        for element in elements:
            if (
                element.get("clickable")
                and _s(element.get("text")) in ok_texts
                and not _is_file_row(element)
            ):
                return element.get("index")

        for text in ok_texts:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                element = _element(idx)
                if element is None or not _is_file_row(element):
                    return idx

        for description in ok_texts:
            idx = device.find(description=description, clickable=True)
            if idx is not None:
                return idx

        return None

    def _confirm():
        for attempt in range(12):
            idx = _find_ok()
            if idx is not None:
                device.click(index=idx)
                return True

            if _dialog_present():
                for text in ("DELETE", "Delete", "Yes", "Confirm"):
                    idx = device.find(text=text, clickable=True)
                    if idx is not None:
                        element = _element(idx)
                        if element is None or not _is_file_row(element):
                            device.click(index=idx)
                            return True

                for description in ("Delete", "Confirm"):
                    idx = device.find(description=description, clickable=True)
                    if idx is not None:
                        element = _element(idx)
                        if element is None or not _is_file_row(element):
                            device.click(index=idx)
                            return True

            device.settle(0.5)

        return False

    if not _confirm():
        raise RuntimeError("Delete confirmation dialog was not completed")

    device.settle(1.0)
    return True
