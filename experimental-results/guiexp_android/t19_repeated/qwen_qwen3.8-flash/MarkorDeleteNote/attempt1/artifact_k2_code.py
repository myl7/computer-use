PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "The note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    if binding is None:
        binding = {}

    file_name = str(binding.get("file_name", "")).strip()
    if not file_name:
        raise ValueError("binding['file_name'] is missing")

    candidates = []

    def add_candidate(value):
        value = str(value).strip()
        if value and value not in candidates:
            candidates.append(value)

    add_candidate(file_name)
    if "." in file_name:
        add_candidate(file_name.rsplit(".", 1)[0])
    else:
        add_candidate(file_name + ".md")
    if file_name.lower().endswith(".md"):
        add_candidate(file_name[:-3])

    def _long_press(index):
        if index is None:
            raise RuntimeError("Cannot long-press missing element")
        if hasattr(device, "long_press"):
            try:
                device.long_press(index=index)
                return
            except (AttributeError, TypeError):
                pass
        device.execute({"action_type": "long_press", "index": index})

    def _scroll(direction):
        try:
            device.scroll(direction=direction)
            return True
        except Exception:
            return False

    def _screen_fingerprint():
        elems = device.elements()
        return tuple(
            sorted(
                (
                    str(e.get("text") or "").strip(),
                    str(e.get("description") or "").strip(),
                    str(e.get("hint") or "").strip(),
                )
                for e in elems
            )
        )

    def _matches(e, candidate):
        if not candidate:
            return False

        text = str(e.get("text") or "").strip()
        desc = str(e.get("description") or "").strip()
        hint = str(e.get("hint") or "").strip()

        if text == candidate or desc == candidate or hint == candidate:
            return True
        if desc == f"File {candidate}" or text == f"File {candidate}":
            return True
        if desc == f"Selected {candidate}" or text == f"Selected {candidate}":
            return True

        lc = candidate.lower()
        if text.lower() == lc or desc.lower() == lc or hint.lower() == lc:
            return True
        if desc.lower() == f"file {lc}" or text.lower() == f"file {lc}":
            return True
        if desc.lower() == f"selected {lc}" or text.lower() == f"selected {lc}":
            return True

        return False

    def _is_file_like(e):
        desc = str(e.get("description") or "").strip().lower()
        if desc.startswith("folder") or desc.startswith("folder "):
            return False
        return True

    def _find_row(cands):
        for c in cands:
            idx = device.find(description=f"File {c}", clickable=True)
            if idx is not None:
                return idx, c
            idx = device.find(description=f"File {c}")
            if idx is not None:
                return idx, c

        fallback = None
        for c in cands:
            for e in device.elements():
                idx = e.get("index")
                if idx is None:
                    continue
                if not _matches(e, c):
                    continue
                if not _is_file_like(e):
                    continue
                if e.get("clickable"):
                    return idx, c
                if fallback is None:
                    fallback = (idx, c)

        return fallback if fallback is not None else (None, None)

    def _find_with_scroll(cands):
        idx, c = _find_row(cands)
        if idx is not None:
            return idx, c

        prev = _screen_fingerprint()
        for _ in range(50):
            if not _scroll("down"):
                break
            idx, c = _find_row(cands)
            if idx is not None:
                return idx, c
            cur = _screen_fingerprint()
            if cur == prev:
                break
            prev = cur

        prev = _screen_fingerprint()
        for _ in range(50):
            if not _scroll("up"):
                break
            idx, c = _find_row(cands)
            if idx is not None:
                return idx, c
            cur = _screen_fingerprint()
            if cur == prev:
                break
            prev = cur

        return None, None

    def _looks_like_delete_control(e, require_clickable=True):
        if require_clickable and not e.get("clickable"):
            return False

        desc = str(e.get("description") or "").strip().lower()
        text = str(e.get("text") or "").strip().lower()
        hint = str(e.get("hint") or "").strip().lower()

        if desc.startswith("file") or desc.startswith("folder"):
            return False

        values = (desc, text, hint)
        if any(v in ("delete", "delete file", "delete note") for v in values):
            return True
        if any(v.startswith("delete") for v in values):
            return True
        return False

    def _find_delete():
        for v in ("Delete", "delete", "Delete file", "Delete note"):
            idx = device.find(description=v, clickable=True)
            if idx is not None:
                return idx
            idx = device.find(hint=v, clickable=True)
            if idx is not None:
                return idx

        for e in device.elements():
            if _looks_like_delete_control(e, True):
                idx = e.get("index")
                if idx is not None:
                    return idx

        for e in device.elements():
            if _looks_like_delete_control(e, False):
                idx = e.get("index")
                if idx is not None:
                    return idx

        more_idx = None
        for v in ("More options", "Overflow menu", "Menu"):
            more_idx = device.find(description=v, clickable=True)
            if more_idx is None:
                more_idx = device.find(text=v, clickable=True)
            if more_idx is None:
                more_idx = device.find(hint=v, clickable=True)
            if more_idx is not None:
                break

        if more_idx is not None:
            device.click(index=more_idx)
            device.settle(1.0)

            for v in ("Delete", "delete"):
                idx = device.find(description=v, clickable=True)
                if idx is not None:
                    return idx
                idx = device.find(hint=v, clickable=True)
                if idx is not None:
                    return idx

            for e in device.elements():
                if _looks_like_delete_control(e, True):
                    idx = e.get("index")
                    if idx is not None:
                        return idx

            for e in device.elements():
                if _looks_like_delete_control(e, False):
                    idx = e.get("index")
                    if idx is not None:
                        return idx

        return None

    def _has_cancel():
        for e in device.elements():
            desc = str(e.get("description") or "").strip().lower()
            text = str(e.get("text") or "").strip().lower()
            hint = str(e.get("hint") or "").strip().lower()

            if desc.startswith("file") or desc.startswith("folder"):
                continue

            if text in ("cancel", "no") or desc in ("cancel", "no") or hint in ("cancel", "no"):
                return True
        return False

    def _looks_like_affirmative(e, labels, require_clickable=True):
        if require_clickable and not e.get("clickable"):
            return False

        desc = str(e.get("description") or "").strip().lower()
        text = str(e.get("text") or "").strip().lower()
        hint = str(e.get("hint") or "").strip().lower()

        if desc.startswith("file") or desc.startswith("folder"):
            return False

        values = (desc, text, hint)
        return any(v in labels for v in values)

    def _find_confirm():
        for _ in range(6):
            for label in ("OK", "Yes", "Confirm"):
                idx = device.find(description=label, clickable=True)
                if idx is not None:
                    return idx
                idx = device.find(hint=label, clickable=True)
                if idx is not None:
                    return idx

            for e in device.elements():
                if _looks_like_affirmative(e, ("ok", "yes", "confirm"), True):
                    idx = e.get("index")
                    if idx is not None:
                        return idx

            if _has_cancel():
                for label in ("Delete",):
                    idx = device.find(description=label, clickable=True)
                    if idx is not None:
                        return idx
                    idx = device.find(hint=label, clickable=True)
                    if idx is not None:
                        return idx

                for e in device.elements():
                    if _looks_like_affirmative(e, ("delete",), True):
                        idx = e.get("index")
                        if idx is not None:
                            return idx

                for e in device.elements():
                    if _looks_like_affirmative(e, ("ok", "yes", "confirm", "delete"), False):
                        idx = e.get("index")
                        if idx is not None:
                            return idx

            for e in device.elements():
                if _looks_like_affirmative(e, ("ok", "yes", "confirm"), False):
                    idx = e.get("index")
                    if idx is not None:
                        return idx

            device.settle(0.5)

        return None

    device.open_app("Markor")
    device.settle(1.0)

    target_idx, matched_candidate = _find_with_scroll(candidates)
    if target_idx is None:
        raise RuntimeError(f"Could not find Markor note {file_name!r}")

    _long_press(target_idx)
    device.settle(1.0)

    delete_idx = _find_delete()
    if delete_idx is None:
        retry_cands = [matched_candidate] if matched_candidate else candidates
        target_idx2, _ = _find_with_scroll(retry_cands)
        if target_idx2 is not None:
            _long_press(target_idx2)
            device.settle(1.0)
            delete_idx = _find_delete()

    if delete_idx is None:
        raise RuntimeError("Could not find Delete action after selecting note")

    device.click(index=delete_idx)
    device.settle(1.0)

    confirm_idx = _find_confirm()
    if confirm_idx is None:
        raise RuntimeError("Could not find delete confirmation button")

    device.click(index=confirm_idx)
    device.settle(1.0)

    for _ in range(3):
        if _find_confirm() is None:
            break
        device.settle(0.5)
        confirm_idx = _find_confirm()
        if confirm_idx is not None:
            device.click(index=confirm_idx)
            device.settle(1.0)

    if matched_candidate:
        still_idx, _ = _find_row([matched_candidate])
        if still_idx is not None:
            raise RuntimeError(f"Note {file_name!r} still present after deletion")

    return True
