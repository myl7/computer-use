PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    file_name = str(binding.get("file_name", "")).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is missing")

    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name

    def clean(value):
        if value is None:
            return ""
        return str(value).strip()

    def _long_press(index):
        try:
            device.long_press(index=index)
        except (AttributeError, TypeError):
            device.execute({"action_type": "long_press", "index": index})

    def find_target_index():
        elems = device.elements()
        exact_click = exact_any = None
        stem_file_click = stem_file_any = None
        stem_text_click = stem_text_any = None

        file_desc = f"File {file_name}"
        stem_desc = f"File {stem}" if stem else ""

        for e in elems:
            idx = e.get("index")
            if idx is None:
                continue

            text = clean(e.get("text"))
            desc = clean(e.get("description"))
            hint = clean(e.get("hint"))
            vals = (text, desc, hint)
            clickable = bool(e.get("clickable"))

            if any(v == file_name or v == file_desc for v in vals):
                if clickable and exact_click is None:
                    exact_click = idx
                if exact_any is None:
                    exact_any = idx
            elif stem:
                low_desc = desc.lower()
                if low_desc.startswith("folder") or low_desc.startswith("dir"):
                    continue

                if stem_desc and any(v == stem_desc for v in vals):
                    if clickable and stem_file_click is None:
                        stem_file_click = idx
                    if stem_file_any is None:
                        stem_file_any = idx
                elif any(v == stem for v in vals):
                    if clickable and stem_text_click is None:
                        stem_text_click = idx
                    if stem_text_any is None:
                        stem_text_any = idx

        for idx in (
            exact_click,
            exact_any,
            stem_file_click,
            stem_file_any,
            stem_text_click,
            stem_text_any,
        ):
            if idx is not None:
                return idx

        for val in (file_name, file_desc, stem_desc, stem):
            if not val:
                continue

            idx = device.find(text=val, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(description=val, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(text=val)
            if idx is not None:
                return idx
            idx = device.find(description=val)
            if idx is not None:
                return idx

        return None

    def screen_signature():
        return tuple(
            (
                e.get("index"),
                clean(e.get("text")),
                clean(e.get("description")),
                clean(e.get("hint")),
            )
            for e in device.elements()
        )

    def scroll_to_target(max_scrolls=10):
        idx = find_target_index()
        if idx is not None:
            return idx

        last = screen_signature()
        for _ in range(max_scrolls):
            device.scroll(direction="down")
            device.settle(0.5)

            idx = find_target_index()
            if idx is not None:
                return idx

            sig = screen_signature()
            if sig == last:
                break
            last = sig

        last = screen_signature()
        for _ in range(max_scrolls):
            device.scroll(direction="up")
            device.settle(0.5)

            idx = find_target_index()
            if idx is not None:
                return idx

            sig = screen_signature()
            if sig == last:
                break
            last = sig

        return None

    device.open_app("Markor")
    device.settle(1.0)

    recognizable = False
    for _ in range(3):
        if any(
            result is not None
            for result in (
                device.find(text="Markor"),
                device.find(description="Files"),
                device.find(description="Create a new file or folder"),
            )
        ):
            recognizable = True
            break
        device.settle(1.0)

    if not recognizable:
        files_idx = device.find(description="Files", clickable=True)
        if files_idx is None:
            files_idx = device.find(description="Files")

        if files_idx is not None:
            device.click(index=files_idx)
            device.settle(1.0)

            if not any(
                result is not None
                for result in (
                    device.find(text="Markor"),
                    device.find(description="Files"),
                    device.find(description="Create a new file or folder"),
                )
            ):
                raise RuntimeError("Markor did not open to a recognizable screen")
        else:
            raise RuntimeError("Markor did not open to a recognizable screen")

    idx = scroll_to_target()
    if idx is None:
        raise ValueError(f"Could not find file row for {file_name!r}")

    _long_press(idx)
    device.settle(0.5)

    def find_delete_index():
        elems = device.elements()
        exact_click = exact_any = None
        contains_click = contains_any = None

        for e in elems:
            idx = e.get("index")
            if idx is None:
                continue

            text = clean(e.get("text")).lower()
            desc = clean(e.get("description")).lower()
            hint = clean(e.get("hint")).lower()
            vals = (text, desc, hint)
            clickable = bool(e.get("clickable"))

            if any(v == "delete" for v in vals):
                if clickable and exact_click is None:
                    exact_click = idx
                if exact_any is None:
                    exact_any = idx
            elif any("delete" in v for v in vals):
                if clickable and contains_click is None:
                    contains_click = idx
                if contains_any is None:
                    contains_any = idx

        for idx in (exact_click, exact_any, contains_click, contains_any):
            if idx is not None:
                return idx

        idx = device.find(description="Delete", clickable=True)
        if idx is None:
            idx = device.find(text="Delete", clickable=True)
        if idx is None:
            idx = device.find(description="Delete")
        if idx is None:
            idx = device.find(text="Delete")

        return idx

    delete_idx = None
    for _ in range(3):
        delete_idx = find_delete_index()
        if delete_idx is not None:
            break

        device.settle(0.5)
        retry_idx = find_target_index()
        if retry_idx is not None:
            _long_press(retry_idx)
            device.settle(0.5)

    if delete_idx is None:
        raise RuntimeError("Clickable Delete control not found on current screen")

    device.click(index=delete_idx)
    device.settle(0.5)

    def find_ok_index():
        elems = device.elements()
        ok_click = ok_any = None
        yes_click = yes_any = None

        for e in elems:
            idx = e.get("index")
            if idx is None:
                continue

            text = clean(e.get("text")).upper()
            desc = clean(e.get("description")).upper()
            hint = clean(e.get("hint")).upper()
            vals = (text, desc, hint)
            clickable = bool(e.get("clickable"))

            if any(v == "OK" for v in vals):
                if clickable and ok_click is None:
                    ok_click = idx
                if ok_any is None:
                    ok_any = idx
            elif any(v == "YES" for v in vals):
                if clickable and yes_click is None:
                    yes_click = idx
                if yes_any is None:
                    yes_any = idx

        for idx in (ok_click, ok_any, yes_click, yes_any):
            if idx is not None:
                return idx

        idx = device.find(text="OK", clickable=True)
        if idx is None:
            idx = device.find(text="OK")
        if idx is None:
            idx = device.find(text="Yes", clickable=True)
        if idx is None:
            idx = device.find(text="Yes")

        return idx

    ok_idx = None
    for _ in range(5):
        ok_idx = find_ok_index()
        if ok_idx is not None:
            break
        device.settle(0.5)

    if ok_idx is None:
        raise RuntimeError("Delete confirmation OK button not found")

    device.click(index=ok_idx)
    device.settle(1.0)
    return True
