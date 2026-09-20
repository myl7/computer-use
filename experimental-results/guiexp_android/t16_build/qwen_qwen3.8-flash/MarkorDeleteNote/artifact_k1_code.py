PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Note file name including its extension, e.g. note.txt",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    value = binding.get("file_name")
    if value is None:
        raise ValueError("binding['file_name'] is missing")

    file_name = str(value).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is empty")

    stem = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    primary_lower = file_name.lower()
    stem_lower = stem.lower() if stem and stem != file_name else None

    def _clean(v):
        return str(v).strip().lower() if v is not None else ""

    def _screen_sig():
        vals = []
        try:
            for el in device.elements():
                for key in ("text", "description", "hint"):
                    s = _clean(el.get(key))
                    if s:
                        vals.append(s)
        except Exception:
            pass
        return tuple(vals)

    def _find_row_current(allow_stem):
        idx = device.find(description=f"File {file_name}", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text=file_name, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(hint=file_name, clickable=True)
        if idx is not None:
            return idx

        if allow_stem and stem_lower:
            idx = device.find(description=f"File {stem}", clickable=True)
            if idx is not None:
                return idx

        search_lower = [primary_lower]
        search_desc = [f"file {primary_lower}"]
        if allow_stem and stem_lower:
            search_lower.append(stem_lower)
            search_desc.append(f"file {stem_lower}")

        try:
            els = device.elements()
        except Exception:
            els = []

        for n_low, d_low in zip(search_lower, search_desc):
            for el in els:
                if not el.get("clickable"):
                    continue
                s = _clean(el.get("description"))
                if s == n_low or s == d_low:
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        for n_low, d_low in zip(search_lower, search_desc):
            for el in els:
                if not el.get("clickable"):
                    continue
                desc = _clean(el.get("description"))
                if stem_lower and n_low == stem_lower and desc.startswith("folder"):
                    continue
                for key in ("text", "hint"):
                    s = _clean(el.get(key))
                    if s == n_low or s == d_low:
                        idx = el.get("index")
                        if idx is not None:
                            return idx

        for n_low, d_low in zip(search_lower, search_desc):
            for el in els:
                s = _clean(el.get("description"))
                if s == n_low or s == d_low:
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        for n_low, d_low in zip(search_lower, search_desc):
            for el in els:
                desc = _clean(el.get("description"))
                if stem_lower and n_low == stem_lower and desc.startswith("folder"):
                    continue
                for key in ("text", "hint"):
                    s = _clean(el.get(key))
                    if s == n_low or s == d_low:
                        idx = el.get("index")
                        if idx is not None:
                            return idx

        return None

    def _scroll_find(allow_stem):
        for _ in range(60):
            idx = _find_row_current(allow_stem)
            if idx is not None:
                return idx

            before = _screen_sig()
            try:
                device.scroll(direction="down")
                device.settle(0.2)
            except Exception:
                break

            if _screen_sig() == before:
                break

        return _find_row_current(allow_stem)

    def _scroll_to_top():
        for _ in range(60):
            before = _screen_sig()
            try:
                device.scroll(direction="up")
                device.settle(0.2)
            except Exception:
                break

            if _screen_sig() == before:
                break

    def _scroll_to_target():
        idx = _scroll_find(False)
        if idx is not None:
            return idx

        if not stem_lower:
            raise ValueError(f"Could not find file row for {file_name!r}")

        _scroll_to_top()
        idx = _scroll_find(True)
        if idx is not None:
            return idx

        raise ValueError(f"Could not find file row for {file_name!r}")

    def _long_press(index):
        lp = getattr(device, "long_press", None)
        if callable(lp):
            try:
                lp(index=index)
            except TypeError:
                try:
                    lp(index)
                except TypeError:
                    device.execute({"action_type": "long_press", "index": index})
        else:
            device.execute({"action_type": "long_press", "index": index})
        device.settle(0.5)

    def _click_delete():
        for _ in range(5):
            idx = device.find(description="Delete", clickable=True)

            if idx is None:
                try:
                    els = device.elements()
                except Exception:
                    els = []
                for el in els:
                    if el.get("clickable") and _clean(el.get("description")) == "delete":
                        idx = el.get("index")
                        if idx is not None:
                            break

            if idx is None:
                idx = device.find(text="Delete", clickable=True)
            if idx is None:
                idx = device.find(hint="Delete", clickable=True)

            if idx is None:
                try:
                    els = device.elements()
                except Exception:
                    els = []
                for el in els:
                    if not el.get("clickable"):
                        continue
                    for key in ("text", "hint"):
                        if _clean(el.get(key)) == "delete":
                            idx = el.get("index")
                            if idx is not None:
                                break
                    if idx is not None:
                        break

            if idx is not None:
                device.click(index=idx)
                device.settle(0.5)
                return

            device.settle(0.5)

        raise RuntimeError("Clickable Delete control not found after selecting note")

    def _confirm():
        candidates = ("OK", "YES", "CONFIRM")

        for _ in range(5):
            for label in candidates:
                idx = device.find(text=label, clickable=True)
                if idx is None:
                    idx = device.find(description=label, clickable=True)
                if idx is None:
                    idx = device.find(hint=label, clickable=True)
                if idx is not None:
                    device.click(index=idx)
                    device.settle(0.5)
                    return

            try:
                els = device.elements()
            except Exception:
                els = []

            for label in candidates:
                low = label.lower()
                for el in els:
                    if not el.get("clickable"):
                        continue
                    for key in ("text", "description", "hint"):
                        if _clean(el.get(key)) == low:
                            idx = el.get("index")
                            if idx is not None:
                                device.click(index=idx)
                                device.settle(0.5)
                                return

            device.settle(0.5)

        raise RuntimeError("Delete confirmation button not found")

    device.open_app("Markor")
    device.settle(1.0)

    recognizable = (
        device.find(text="Markor") is not None
        or device.find(description="Files") is not None
        or device.find(description="Create a new file or folder") is not None
    )
    if not recognizable:
        device.open_app("Markor")
        device.settle(1.0)

    target_idx = _scroll_to_target()
    _long_press(target_idx)
    _click_delete()
    _confirm()

    return True
