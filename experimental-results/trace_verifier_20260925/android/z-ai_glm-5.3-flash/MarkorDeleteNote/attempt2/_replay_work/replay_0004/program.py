import os
import re

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Note file name including its extension, e.g. note.txt",
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("binding['file_name'] must be a non-empty string")
    stem = os.path.splitext(file_name)[0]

    # ------------------------------------------------------------------
    # Step 1: launch Markor and make sure its main screen is showing
    # ------------------------------------------------------------------
    device.open_app("Markor")
    device.settle(2)

    def on_main_screen():
        return (device.find(text="Markor") is not None
                or device.find(description="Create a new file or folder") is not None)

    if not on_main_screen():
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected")

    # ------------------------------------------------------------------
    # Step 2: locate the target note row by its exact title, never position.
    #
    # Markor labels file rows "File <name>", but the name shown in a row is
    # not guaranteed to carry the file's extension (e.g. the note behind
    # binding 'brave_fish_2023_03_28.txt' can be listed as
    # 'File brave_fish_2023_03_28').  So the row title is matched against
    # the full name AND the bare stem, but always anchored/exact: a plain
    # substring search for the stem would also hit unrelated notes whose
    # names merely contain it (e.g. '2023_09_08_brave_fish_2023_03_28.md').
    # ------------------------------------------------------------------
    stem_no_ext_re = re.compile(r"(?:File\s+)?%s\s*$" % re.escape(stem))
    stem_with_ext_re = re.compile(r"(?:File\s+)?%s\.[A-Za-z0-9]+\s*$" % re.escape(stem))
    full_name_re = re.compile(
        r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_.])" % re.escape(file_name))

    def candidate_strings(elem):
        out = []
        for key in ("description", "text", "hint"):
            v = elem.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
        return out

    def match_exact(c):
        return c == file_name or c == "File %s" % file_name

    def match_full_name(c):
        return full_name_re.search(c) is not None

    def match_stem_no_ext(c):
        return stem_no_ext_re.fullmatch(c) is not None

    def match_stem_with_ext(c):
        return stem_with_ext_re.fullmatch(c) is not None

    MATCHERS = (match_exact, match_full_name, match_stem_no_ext, match_stem_with_ext)

    def find_target_row():
        elems = device.elements()
        for match in MATCHERS:
            for e in elems:
                if e.get("clickable") and any(match(c) for c in candidate_strings(e)):
                    return e["index"]
        for match in MATCHERS:
            for e in elems:
                if any(match(c) for c in candidate_strings(e)):
                    return e["index"]
        return None

    target = find_target_row()
    scrolls = 0
    while target is None and scrolls < 6:
        device.scroll("down")
        device.settle(1)
        target = find_target_row()
        scrolls += 1
    if target is None:
        # we may have scrolled past it; sweep back up
        for _ in range(scrolls):
            device.scroll("up")
            device.settle(1)
            target = find_target_row()
            if target is not None:
                break
    if target is None:
        raise LookupError("Note item for '%s' not found on screen" % file_name)

    # ------------------------------------------------------------------
    # Step 3: long-press the row to select it, then tap the Delete action
    # shown by the selection top bar
    # ------------------------------------------------------------------
    def long_press_row(idx):
        long_press = getattr(device, "long_press", None)
        if callable(long_press):
            long_press(index=idx)
        else:
            device.execute({"action_type": "long_press", "index": idx})

    def find_delete_action():
        idx = device.find(description="Delete", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Delete", clickable=True)
        if idx is not None:
            return idx
        elems = device.elements()
        for e in elems:
            blob = ((e.get("description") or "") + " "
                    + (e.get("text") or "")).lower()
            if "delete" in blob and e.get("clickable"):
                return e["index"]
        for e in elems:
            blob = ((e.get("description") or "") + " "
                    + (e.get("text") or "")).lower()
            if "delete" in blob:
                return e["index"]
        return None

    long_press_row(target)
    device.settle(1)

    delete_btn = find_delete_action()
    if delete_btn is None:
        # the long-press may not have registered; re-locate the row and retry once
        target = find_target_row()
        if target is None:
            raise RuntimeError(
                "Target note '%s' is no longer on screen after long-press" % file_name)
        long_press_row(target)
        device.settle(1)
        delete_btn = find_delete_action()
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found - ensure the target note '%s' is selected"
            % file_name)
    device.click(delete_btn)
    device.settle(1)

    # ------------------------------------------------------------------
    # Step 4: confirm the delete dialog with OK
    # ------------------------------------------------------------------
    def row_gone():
        for e in device.elements():
            if not e.get("clickable"):
                continue
            for c in candidate_strings(e):
                if any(match(c) for match in MATCHERS):
                    return False
        return True

    def find_ok_button():
        idx = device.find(text="OK", clickable=True)
        if idx is not None:
            return idx
        elems = device.elements()
        # a real confirmation dialog shows non-clickable text about deleting
        dialog_seen = False
        for e in elems:
            blob = ((e.get("text") or "") + " "
                    + (e.get("description") or "")).lower()
            if ("delete" in blob or "confirm" in blob) and not e.get("clickable"):
                dialog_seen = True
                break
        for e in elems:
            t = (e.get("text") or "").strip().lower()
            if t in ("ok", "yes", "confirm") and e.get("clickable"):
                return e["index"]
        if dialog_seen:
            for e in elems:
                t = (e.get("text") or "").strip().lower()
                if t == "delete" and e.get("clickable"):
                    return e["index"]
        return None

    ok_btn = find_ok_button()
    if ok_btn is None and row_gone():
        return True  # note was deleted outright, no confirmation dialog shown
    if ok_btn is None:
        # the delete tap may not have landed; trigger the delete action once more
        retry_btn = find_delete_action()
        if retry_btn is not None:
            device.click(retry_btn)
            device.settle(1)
            ok_btn = find_ok_button()
    if ok_btn is None and row_gone():
        return True
    if ok_btn is None:
        raise LookupError(
            "No clickable 'OK' confirmation button found in delete dialog for '%s'"
            % file_name)
    device.click(ok_btn)
    device.settle(1)

    # ------------------------------------------------------------------
    # Step 5: soft verification - the deleted note's row must disappear
    # ------------------------------------------------------------------
    if not row_gone():
        device.settle(2)
        if not row_gone():
            raise RuntimeError(
                "Note '%s' still visible after delete confirmation" % file_name)

    return True
