PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "The exact note file name to delete, including its extension when present (e.g. note.txt).",
        "required": True,
    }
}


def program(device, binding: dict) -> bool:
    if not isinstance(binding, dict):
        raise ValueError("binding must be a dict")

    raw_file_name = binding.get("file_name", "")
    if raw_file_name is None:
        raw_file_name = ""
    file_name = str(raw_file_name).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is required and must be non-empty")

    def _settle(seconds=0.5):
        try:
            device.settle(seconds)
        except Exception:
            try:
                device.wait()
            except Exception:
                pass

    def _elements():
        try:
            elems = device.elements()
        except Exception:
            return []
        if not isinstance(elems, list):
            return []
        return elems

    def _get_index(element):
        idx = element.get("index")
        if idx is None:
            return None
        try:
            return int(idx)
        except Exception:
            return idx

    def _fields(element, include_text=True):
        keys = ("description", "text", "hint") if include_text else ("description", "hint")
        vals = []
        for key in keys:
            val = element.get(key)
            if val is not None:
                val = str(val).strip()
                if val:
                    vals.append(val)
        return vals

    def _signature():
        return tuple(
            (
                e.get("index"),
                str(e.get("text") or "")[:120],
                str(e.get("description") or "")[:120],
                str(e.get("hint") or "")[:120],
            )
            for e in _elements()
        )

    def _primary_variants(name):
        prim = []

        def add(v):
            v = str(v).strip()
            if v and v not in prim:
                prim.append(v)

        add(name)
        if "/" in name:
            add(name.rsplit("/", 1)[-1])
        return prim

    def _secondary_variants(name):
        prim = _primary_variants(name)
        sec = []

        def add(v):
            v = str(v).strip()
            if v and v not in prim and v not in sec:
                sec.append(v)

        base = name.rsplit("/", 1)[-1]
        if "." in base:
            stem = base.rsplit(".", 1)[0]
            add(stem)
        else:
            add(base + ".md")
        return sec

    primary_variants = _primary_variants(file_name)
    secondary_variants = _secondary_variants(file_name)
    all_variants = primary_variants + [v for v in secondary_variants if v not in primary_variants]

    def _matches_single(element, variant):
        desc = str(element.get("description") or "").lower()
        if desc.startswith("folder "):
            return False

        vals = _fields(element)
        if not vals:
            return False

        targets = [variant, f"File {variant}"]
        target_lowers = [t.lower() for t in targets]
        separators = (",", ";", ":", "-", "|")

        for val in vals:
            lv = val.lower()
            if val in targets or lv in target_lowers:
                return True

            for target in targets:
                if val.startswith(target):
                    rest = val[len(target):]
                    if not rest or rest[0].isspace() or rest[0] in separators:
                        return True

                lt = target.lower()
                if lv.startswith(lt):
                    rest = lv[len(lt):]
                    if not rest or rest[0].isspace() or rest[0] in separators:
                        return True

        return False

    def _find_index_for_variants(variants):
        for variant in variants:
            idx = device.find(description=f"File {variant}", clickable=True)
            if idx is not None:
                return idx

        elems = _elements()

        for variant in variants:
            for e in elems:
                if not e.get("clickable"):
                    continue
                idx = _get_index(e)
                if idx is not None and _matches_single(e, variant):
                    return idx

        for variant in variants:
            for e in elems:
                if e.get("clickable"):
                    continue
                idx = _get_index(e)
                if idx is not None and _matches_single(e, variant):
                    return idx

        return None

    def _find_target_index():
        return _find_index_for_variants(all_variants)

    def _any_file_rows():
        for e in _elements():
            desc = str(e.get("description") or "").lower()
            if desc.startswith("file ") or desc.startswith("folder "):
                return True
        return False

    def _scroll_to_find(variants):
        if not variants:
            return None

        idx = _find_index_for_variants(variants)
        if idx is not None:
            return idx

        seen = set()
        for _ in range(25):
            sig = _signature()
            if sig in seen:
                break
            seen.add(sig)
            try:
                device.scroll(direction="down")
            except Exception:
                break
            idx = _find_index_for_variants(variants)
            if idx is not None:
                return idx

        seen = set()
        for _ in range(25):
            sig = _signature()
            if sig in seen:
                break
            seen.add(sig)
            try:
                device.scroll(direction="up")
            except Exception:
                break
            idx = _find_index_for_variants(variants)
            if idx is not None:
                return idx

        return None

    def _scroll_to_target():
        idx = _scroll_to_find(primary_variants)
        if idx is not None:
            return idx

        idx = _scroll_to_find(secondary_variants)
        if idx is not None:
            return idx

        raise RuntimeError(f"Could not find Markor file row for {file_name!r}")

    def _long_press(index):
        if index is None:
            raise RuntimeError("Cannot long-press a missing element")

        if hasattr(device, "long_press"):
            try:
                device.long_press(index=index)
            except TypeError:
                device.execute({"action_type": "long_press", "index": index})
        else:
            device.execute({"action_type": "long_press", "index": index})
        _settle(0.5)

    def _click(index):
        if index is None:
            raise RuntimeError("Cannot click a missing element")
        device.click(index=index)
        _settle(0.5)

    def _find_more():
        for desc in ("More options", "overflow", "Menu", "Options"):
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx

        for text in ("More options", "Menu", "Options"):
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx

        elems = _elements()
        exact = ("more options", "overflow", "menu", "options")

        for e in elems:
            if not e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e):
                if val.lower() in exact:
                    return idx

        for e in elems:
            if not e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e, include_text=False):
                v = val.lower()
                if "more options" in v or "overflow" in v:
                    return idx

        return None

    def _find_delete():
        delete_descs = ("Delete", "delete", "Trash", "Delete file", "Delete note")
        delete_texts = ("Delete", "DELETE", "Trash", "Delete file", "Delete note")

        for desc in delete_descs:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx

        for text in delete_texts:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx

        elems = _elements()
        exact = ("delete", "trash", "delete file", "delete note")

        for e in elems:
            if not e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e):
                if val.lower() in exact:
                    return idx

        for e in elems:
            if e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e):
                if val.lower() in exact:
                    return idx

        for e in elems:
            if not e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e, include_text=False):
                v = val.lower()
                if "delete" in v or "trash" in v:
                    return idx

        for e in elems:
            if e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e, include_text=False):
                v = val.lower()
                if "delete" in v or "trash" in v:
                    return idx

        return None

    def _click_delete(original_idx=None):
        idx = _find_delete()

        if idx is None:
            more = _find_more()
            if more is not None:
                _click(more)
                idx = _find_delete()

        if idx is None:
            _settle(1)
            idx = _find_delete()

        if idx is None:
            target = _find_target_index()
            if target is not None and (original_idx is None or target == original_idx):
                _long_press(target)
                idx = _find_delete()

        if idx is None:
            raise RuntimeError("Delete action not found after selecting note")

        _click(idx)

    def _find_ok():
        primary = ("OK", "Ok", "ok", "Yes", "Confirm")
        primary_lower = tuple(x.lower() for x in primary)

        for text in primary:
            idx = device.find(text=text, clickable=True)
            if idx is not None:
                return idx

        for desc in primary:
            idx = device.find(description=desc, clickable=True)
            if idx is not None:
                return idx

        elems = _elements()

        for e in elems:
            if not e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e):
                if val in primary or val.lower() in primary_lower:
                    return idx

        has_negative = False
        for e in elems:
            for val in _fields(e):
                if val.lower() in ("cancel", "no", "negative"):
                    has_negative = True
                    break
            if has_negative:
                break

        if has_negative:
            for e in elems:
                if not e.get("clickable"):
                    continue
                idx = _get_index(e)
                if idx is None:
                    continue
                cls = str(e.get("class_name") or "").lower()
                if "button" not in cls or "imagebutton" in cls:
                    continue
                for val in _fields(e):
                    if val.lower() == "delete":
                        return idx

        for e in elems:
            if e.get("clickable"):
                continue
            idx = _get_index(e)
            if idx is None:
                continue
            for val in _fields(e):
                if val in primary or val.lower() in primary_lower:
                    return idx

        return None

    def _confirm_delete():
        idx = _find_ok()
        if idx is None:
            _settle(1)
            idx = _find_ok()
        if idx is None:
            raise RuntimeError("Delete confirmation OK button not found")
        _click(idx)

    device.open_app("Markor")
    _settle(1)

    try:
        target_idx = _scroll_to_target()
    except RuntimeError:
        if _any_file_rows():
            raise
        device.navigate_back()
        _settle(1)
        if not _any_file_rows():
            raise RuntimeError("Could not find Markor file list")
        target_idx = _scroll_to_target()

    _long_press(target_idx)
    _click_delete(target_idx)
    _confirm_delete()
    _settle(0.5)
    return True
