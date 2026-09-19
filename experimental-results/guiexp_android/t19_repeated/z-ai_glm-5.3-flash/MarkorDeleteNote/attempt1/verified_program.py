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
    # Helpers. All matching is done by scanning fresh element lists so we
    # are independent of device.find() semantics, and by exact title so
    # similar notes (e.g. '<name>_copy.md', '1JyG_<name>.md') are never
    # touched.
    # ------------------------------------------------------------------
    def norm(s):
        return " ".join((s or "").split())

    def row_kind(el):
        """Return 'file' | 'selected' | None for a list row of our target."""
        d = norm(el.get("description"))
        if d == "File %s" % file_name:
            return "file"
        if d == "Selected %s" % file_name:
            return "selected"
        return None

    def find_row(want="any"):
        """Locate the target row on the current screen (never by position)."""
        for el in device.elements():
            kind = row_kind(el)
            if kind is None or not el.get("clickable"):
                continue
            if want == "any" or want == kind:
                return el
        return None

    def on_main_screen():
        for el in device.elements():
            if norm(el.get("description")) in ("Create a new file or folder", "Files"):
                return True
        return False

    # ------------------------------------------------------------------
    # 1. Launch Markor and ensure its main (file list) screen is showing.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 2. Locate the target note row by its exact title, scrolling if
    #    needed, then long-press it to enter selection mode.
    # ------------------------------------------------------------------
    target = find_row("file")
    scrolls = 0
    while target is None and scrolls < 6:
        device.scroll("down")
        target = find_row("file")
        scrolls += 1

    if target is None:
        raise LookupError("Note '%s' not found in the file list" % file_name)

    device.long_press(target["index"])
    device.settle(1)

    # The long-press works: the row's description flips from
    # 'File <name>' to 'Selected <name>' (and a selection bar with a
    # Delete action appears). Verify via that state, retrying once.
    if find_row("selected") is None:
        retry = find_row("file")
        if retry is not None:
            device.long_press(retry["index"])
            device.settle(1)
        if find_row("selected") is None:
            # Accept as selected if the plain row is gone from matching or a
            # selection bar with Delete is visible; otherwise fail.
            still_plain = find_row("file")
            delete_probe = device.find(description="Delete", clickable=True)
            if still_plain is not None and delete_probe is None:
                raise RuntimeError(
                    "Could not select note '%s' via long-press" % file_name
                )

    # ------------------------------------------------------------------
    # 3. Tap the Delete action shown by the selection top bar.
    # ------------------------------------------------------------------
    delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        device.settle(1)
        delete_btn = device.find(description="Delete", clickable=True)
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found after selecting note '%s'" % file_name
        )
    device.click(delete_btn)
    device.settle(1)  # wait for the confirmation dialog (if any)

    # ------------------------------------------------------------------
    # 4. Confirm the deletion dialog. Button label varies (OK / Yes /
    #    Delete / Confirm), so scan for a clickable confirmation control.
    # ------------------------------------------------------------------
    def find_confirm():
        # Prefer unambiguous positive answers first ('Delete' last, since a
        # selection-bar Delete may still linger behind the dialog).
        for want in ("ok", "yes", "confirm"):
            for el in device.elements():
                if not el.get("clickable"):
                    continue
                label = norm(el.get("text") or el.get("description") or "").lower()
                if label == want:
                    return el
        for el in device.elements():
            if not el.get("clickable"):
                continue
            label = norm(el.get("text") or el.get("description") or "").lower()
            if label in ("delete", "confirm delete", "yes, delete", "move to trash"):
                return el
        return None

    confirm = find_confirm()
    attempts = 0
    while confirm is None and attempts < 2:
        device.settle(1)
        confirm = find_confirm()
        attempts += 1

    if confirm is not None:
        device.click(confirm["index"])
        device.settle(1)
    # else: some builds delete immediately without a dialog; the check
    # below validates the outcome either way.

    # ------------------------------------------------------------------
    # 5. Verify the note is gone from the list (exact title, so similarly
    #    named notes are ignored).
    # ------------------------------------------------------------------
    device.settle(1)
    leftover = find_row("any")
    checks = 0
    while leftover is not None and checks < 2:
        device.scroll("down")
        leftover = find_row("any")
        checks += 1

    if leftover is not None:
        raise RuntimeError("Note '%s' is still present after delete" % file_name)

    return True
