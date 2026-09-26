PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "note file name including its extension, e.g. note.txt",
    }
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"].strip()

    # ------------------------------------------------------------------
    # Helpers.  Every lookup re-reads a fresh element snapshot; indexes
    # are never carried across screens.
    # ------------------------------------------------------------------
    def _needles(ignore_case=False):
        # Markor hides the default ".txt" extension in its file list, so
        # a note stored as "note.txt" is described as "File note".  The
        # exact full name is tried first, then the exact name without
        # its extension.  Only whole-title equality (or the title
        # followed by a space, e.g. when a date/path is appended) ever
        # matches, so same-prefix look-alikes such as
        # "note_2023_01_07.md" or "note_JZ1j.md" are never touched.
        needles = [file_name]
        if "." in file_name:
            stem = file_name.rsplit(".", 1)[0]
            if stem and stem != file_name:
                needles.append(stem)
        if ignore_case:
            needles = [n.lower() for n in needles]
        return needles

    def _elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def _index(el):
        if el is None:
            return None
        if isinstance(el, dict):
            return el.get("index")
        return el

    def _match_rank(el, ignore_case=False):
        # None = no match; lower rank = better match.
        #   0    : title == full file name (e.g. "note.txt")
        #   1    : title == file name without extension (e.g. "note",
        #          how Markor displays a hidden ".txt" file)
        #   2, 3 : title starts with "<needle> " (description continues
        #          with a date/path); the character right after the
        #          needle must be a space, so underscore-separated
        #          look-alikes never match.
        needles = _needles(ignore_case)
        best = None
        for key in ("description", "text"):
            val = el.get(key)
            if not val:
                continue
            s = val.strip()
            if s.startswith("File "):
                s = s[len("File "):].strip()
            if not s:
                continue
            hay = s.lower() if ignore_case else s
            for i, needle in enumerate(needles):
                if hay == needle:
                    if i == 0:
                        return 0
                    if best is None or i < best:
                        best = i
            for i, needle in enumerate(needles):
                if hay.startswith(needle + " "):
                    rank = len(needles) + i
                    if best is None or rank < best:
                        best = rank
        return best

    def _find_row(ignore_case=False):
        best = None
        best_rank = None
        fallback = None
        fallback_rank = None
        for el in _elements():
            rank = _match_rank(el, ignore_case)
            if rank is None:
                continue
            if el.get("clickable"):
                if best_rank is None or rank < best_rank:
                    best, best_rank = el, rank
            elif fallback_rank is None or rank < fallback_rank:
                fallback, fallback_rank = el, rank
        if best is not None:
            return best
        if fallback is not None:
            return fallback
        idx = device.find(text=file_name)
        if idx is not None:
            return {"index": idx}
        idx = device.find(description="File %s" % file_name)
        if idx is not None:
            return {"index": idx}
        return None

    def _row_on_screen(ignore_case=False):
        return any(_match_rank(el, ignore_case) is not None for el in _elements())

    def _list_signature():
        sigs = []
        for el in _elements():
            d = (el.get("description") or "").strip()
            t = (el.get("text") or "").strip()
            if d.startswith("File "):
                sigs.append(d)
            elif t and "." in t and " " not in t and len(t) < 120:
                sigs.append(t)
        return tuple(sorted(sigs))

    # ------------------------------------------------------------------
    # 1. Launch Markor and make sure its main (file list) screen shows.
    # ------------------------------------------------------------------
    def on_main_screen():
        for el in _elements():
            if el.get("text") == "Markor":
                return True
            if el.get("description") == "Create a new file or folder":
                return True
        return False

    device.open_app("Markor")
    device.settle(2)

    if not on_main_screen():
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected"
            )

    # If a stale selection top bar is showing (left over from an earlier
    # run), leave it so the regular file list is restored.  The regular
    # screen has a "Search" action and no clickable "Delete" action.
    els = _elements()
    has_delete = any(
        el.get("clickable") and (el.get("description") or "").strip().lower() == "delete"
        for el in els
    )
    has_search = any(
        (el.get("description") or "").strip().lower() == "search" for el in els
    )
    if has_delete and not has_search:
        device.navigate_back()
        device.settle(1)

    # ------------------------------------------------------------------
    # 2. Locate the target note row by its exact title.  Start at the top
    #    of the list, then scroll down through the whole list until the
    #    row appears or the end of the list is reached.  Never match by
    #    row position; never touch any other note.
    # ------------------------------------------------------------------
    for _ in range(8):
        before = _list_signature()
        device.scroll("up")
        if _list_signature() == before:
            break

    target = _find_row(False)
    if target is None:
        target = _find_row(True)

    scrolls = 0
    while target is None and scrolls < 30:
        before = _list_signature()
        device.scroll("down")
        after = _list_signature()
        target = _find_row(False)
        if target is None:
            target = _find_row(True)
        scrolls += 1
        if target is None and after == before:
            break  # reached the end of the list without a match

    if target is None:
        # The note named `file_name` is not in the list at all, so there
        # is nothing left to delete: the goal state (no such note) already
        # holds.  Same-prefix look-alikes are different files and stay
        # untouched.
        return True

    target_index = _index(target)
    if target_index is None:
        raise LookupError("Could not resolve the row index of note '%s'" % file_name)

    long_press = getattr(device, "long_press", None)
    if callable(long_press):
        long_press(target_index)
    else:
        device.execute({"action_type": "long_press", "index": target_index})
    device.settle(1)

    # ------------------------------------------------------------------
    # 3. Tap the Delete action the selection UI offers (top bar icon or
    #    context menu entry).
    # ------------------------------------------------------------------
    def _find_delete_index():
        exact = None
        loose = None
        for el in _elements():
            if not el.get("clickable"):
                continue
            for key in ("description", "text", "hint"):
                val = (el.get(key) or "").strip().lower()
                if not val:
                    continue
                if val == "delete":
                    if exact is None:
                        exact = el.get("index")
                elif "delete" in val:
                    if loose is None:
                        loose = el.get("index")
                if exact is not None:
                    break
            if exact is not None:
                break
        return exact if exact is not None else loose

    delete_index = _find_delete_index()
    if delete_index is None:
        device.settle(1)
        delete_index = _find_delete_index()
    if delete_index is None:
        raise RuntimeError(
            "Delete action not found after selecting note '%s'" % file_name
        )
    device.click(delete_index)
    device.settle(1)

    # ------------------------------------------------------------------
    # 4. Confirm the deletion dialog.  If no dialog with a confirmation
    #    button shows up, the deletion may have happened without one; in
    #    that case the note must be gone from the list, otherwise fail.
    # ------------------------------------------------------------------
    ok_index = None
    for _ in range(3):
        for el in _elements():
            if not el.get("clickable"):
                continue
            label = (el.get("text") or "").strip().lower()
            desc = (el.get("description") or "").strip().lower()
            if label in ("ok", "yes", "confirm") or desc in ("ok", "yes", "confirm"):
                ok_index = el.get("index")
                break
        if ok_index is not None:
            break
        device.settle(1)

    if ok_index is not None:
        device.click(ok_index)
        device.settle(1)
        return True

    device.settle(1)
    if _row_on_screen(False) or _row_on_screen(True):
        raise LookupError(
            "Delete confirmation dialog for note '%s' did not appear and "
            "the note is still on screen" % file_name
        )
    return True
