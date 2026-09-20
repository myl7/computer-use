PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Markor note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    def _index_value(value):
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return value

    def _is_clickable(element):
        value = element.get("clickable")
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    def _get_str(element, key):
        return str(element.get(key) or "").strip()

    def _get_elements():
        try:
            elements = device.elements()
        except Exception:
            return []
        if elements is None:
            return []
        try:
            return list(elements)
        except Exception:
            return []

    def _find_safe(text=None, description=None, clickable=None):
        kwargs = {}
        if text is not None:
            kwargs["text"] = text
        if description is not None:
            kwargs["description"] = description
        if clickable is not None:
            kwargs["clickable"] = clickable
        try:
            return device.find(**kwargs)
        except Exception:
            if "clickable" in kwargs:
                kwargs.pop("clickable")
                try:
                    return device.find(**kwargs)
                except Exception:
                    return None
            return None

    def _candidate_names(file_name):
        names = []

        def add(name):
            name = str(name).strip()
            if name and name not in names:
                names.append(name)

        add(file_name)
        if "." in file_name:
            stem = file_name.rsplit(".", 1)[0]
            add(stem)
            if stem:
                add(stem + ".md")
        else:
            add(file_name + ".md")
        return names

    def _screen_fingerprint(elements):
        parts = []
        for el in elements:
            if not isinstance(el, dict):
                continue
            parts.append(
                (
                    _get_str(el, "text"),
                    _get_str(el, "description"),
                    _get_str(el, "hint"),
                )
            )
        return tuple(sorted(parts))

    def _find_current_file_index(file_name):
        elements = _get_elements()
        if not elements:
            return None

        candidates = _candidate_names(file_name)
        lower_candidates = [c.lower() for c in candidates]

        clickable_match = None
        any_match = None

        for el in elements:
            if not isinstance(el, dict):
                continue
            idx = _index_value(el.get("index"))
            if idx is None:
                continue

            desc = _get_str(el, "description")
            text = _get_str(el, "text")
            hint = _get_str(el, "hint")

            for cand in candidates:
                if (
                    desc == f"File {cand}"
                    or desc == f"Selected {cand}"
                    or desc == cand
                    or text == cand
                    or hint == cand
                ):
                    if _is_clickable(el) and clickable_match is None:
                        clickable_match = idx
                    if any_match is None:
                        any_match = idx
                    break

            if clickable_match is not None:
                return clickable_match

        if any_match is not None:
            return any_match

        for el in elements:
            if not isinstance(el, dict):
                continue
            idx = _index_value(el.get("index"))
            if idx is None:
                continue

            desc = _get_str(el, "description").lower()
            text = _get_str(el, "text").lower()
            hint = _get_str(el, "hint").lower()

            for lc in lower_candidates:
                if (
                    desc == f"file {lc}"
                    or desc == f"selected {lc}"
                    or desc == lc
                    or text == lc
                    or hint == lc
                ):
                    if _is_clickable(el) and clickable_match is None:
                        clickable_match = idx
                    if any_match is None:
                        any_match = idx
                    break

            if clickable_match is not None:
                return clickable_match

        if any_match is not None:
            return any_match

        return None

    def _find_file_index(file_name):
        idx = _find_current_file_index(file_name)
        if idx is not None:
            return idx

        def search(direction, max_steps):
            prev = None
            for _ in range(max_steps):
                try:
                    device.scroll(direction=direction)
                except Exception:
                    break

                idx = _find_current_file_index(file_name)
                if idx is not None:
                    return idx

                elements = _get_elements()
                if not elements:
                    break

                fp = _screen_fingerprint(elements)
                if fp == prev:
                    break
                prev = fp

            return None

        idx = search("down", 50)
        if idx is not None:
            return idx

        idx = search("up", 50)
        if idx is not None:
            return idx

        idx = search("down", 50)
        return idx

    def _long_press(index):
        try:
            device.execute({"action_type": "long_press", "index": index})
        except Exception:
            if hasattr(device, "long_press"):
                device.long_press(index=index)
            else:
                raise
        device.settle(1.0)

    def _best_delete_index(elements):
        best_idx = None
        best_score = -100

        for el in elements:
            if not isinstance(el, dict):
                continue
            idx = _index_value(el.get("index"))
            if idx is None:
                continue

            desc = _get_str(el, "description").lower()
            text = _get_str(el, "text").lower()
            hint = _get_str(el, "hint").lower()

            if not (
                desc == "delete"
                or text == "delete"
                or hint == "delete"
                or desc.startswith("delete")
                or text.startswith("delete")
                or hint.startswith("delete")
            ):
                continue

            score = 0
            if _is_clickable(el):
                score += 2
            if desc.startswith("file") or desc.startswith("folder"):
                score -= 10
            if text == "delete" and desc.startswith("file"):
                score -= 10
            if desc == "delete":
                score += 2
            if hint == "delete":
                score += 1

            if score > best_score:
                best_score = score
                best_idx = idx

        if best_idx is not None and best_score >= 0:
            return best_idx
        return None

    def _find_delete_action():
        idx = _find_safe(description="Delete", clickable=True)
        if idx is not None:
            return idx

        elements = _get_elements()
        idx = _best_delete_index(elements)
        if idx is not None:
            return idx

        idx = _find_safe(description="Delete")
        if idx is not None:
            return idx

        more = _find_safe(description="More options", clickable=True)
        if more is None:
            more = _find_safe(text="More options", clickable=True)

        if more is None:
            for el in elements:
                if not isinstance(el, dict) or not _is_clickable(el):
                    continue
                idx = _index_value(el.get("index"))
                if idx is None:
                    continue

                desc = _get_str(el, "description").lower()
                text = _get_str(el, "text").lower()
                hint = _get_str(el, "hint").lower()

                if (
                    "more options" in desc
                    or "more options" in text
                    or "more options" in hint
                    or desc == "overflow"
                    or text == "overflow"
                ):
                    more = idx
                    break

        if more is not None:
            try:
                device.click(index=more)
                device.settle(1.0)
            except Exception:
                pass

            idx = _find_safe(text="Delete", clickable=True)
            if idx is None:
                idx = _find_safe(description="Delete", clickable=True)
            if idx is not None:
                return idx

            elements = _get_elements()
            idx = _best_delete_index(elements)
            if idx is not None:
                return idx

            idx = _find_safe(description="Delete")
            if idx is not None:
                return idx

            try:
                device.navigate_back()
                device.settle(0.5)
            except Exception:
                pass

        return None

    def _scan_confirmation(elements):
        ok_idx = None
        ok_score = -100
        delete_idx = None
        delete_score = -100
        cancel_present = False

        affirmative = {"ok", "yes", "confirm"}
        cancel_labels = {"cancel", "dismiss"}

        for el in elements:
            if not isinstance(el, dict):
                continue
            idx = _index_value(el.get("index"))
            if idx is None:
                continue

            desc = _get_str(el, "description").lower()
            text = _get_str(el, "text").lower()
            hint = _get_str(el, "hint").lower()
            vals = (text, desc, hint)

            if any(v in cancel_labels for v in vals):
                cancel_present = True

            if any(v in affirmative for v in vals):
                score = 0
                if _is_clickable(el):
                    score += 2
                if desc.startswith("file") or desc.startswith("folder"):
                    score -= 10
                if "ok" in vals:
                    score += 1
                if score > ok_score:
                    ok_score = score
                    ok_idx = idx

            if any(v == "delete" for v in vals):
                score = 0
                if _is_clickable(el):
                    score += 2
                if desc.startswith("file") or desc.startswith("folder"):
                    score -= 10
                if score > delete_score:
                    delete_score = score
                    delete_idx = idx

        return ok_idx, ok_score, delete_idx, delete_score, cancel_present

    def _click_delete_confirmation():
        for _ in range(6):
            elements = _get_elements()
            ok_idx, ok_score, delete_idx, delete_score, cancel_present = _scan_confirmation(elements)

            if ok_idx is not None and ok_score >= 0:
                device.click(index=ok_idx)
                return

            if cancel_present and delete_idx is not None and delete_score >= 0:
                device.click(index=delete_idx)
                return

            if ok_idx is None and delete_idx is None:
                idx = _find_safe(text="OK", clickable=True)
                if idx is None:
                    idx = _find_safe(text="OK")
                if idx is None:
                    idx = _find_safe(description="OK", clickable=True)
                if idx is None:
                    idx = _find_safe(description="OK")
                if idx is not None:
                    device.click(index=idx)
                    return

            device.settle(0.5)

        raise RuntimeError("Delete confirmation button not found")

    raw = binding.get("file_name")
    if raw is None:
        raise ValueError("binding['file_name'] is missing")

    file_name = str(raw).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is empty")

    device.open_app("Markor")
    device.settle(1.0)

    target_index = _find_file_index(file_name)
    if target_index is None:
        device.open_app("Markor")
        device.settle(1.0)
        target_index = _find_file_index(file_name)

    if target_index is None:
        raise ValueError(f"Could not find Markor note {file_name!r}")

    _long_press(target_index)

    delete_index = _find_delete_action()
    if delete_index is None:
        retry_index = _find_current_file_index(file_name)
        if retry_index is not None:
            _long_press(retry_index)
            delete_index = _find_delete_action()

    if delete_index is None:
        raise RuntimeError("Delete action not available after selecting the note")

    device.click(index=delete_index)
    device.settle(1.0)

    _click_delete_confirmation()
    device.settle(1.0)

    return True
