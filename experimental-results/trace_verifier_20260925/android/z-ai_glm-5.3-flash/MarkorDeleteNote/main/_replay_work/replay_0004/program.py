import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "note file name including its extension, e.g. note.txt",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    stem = os.path.splitext(file_name)[0]

    # ------------------------------------------------------------------
    # Step 1: launch Markor and make sure its main screen is showing.
    # ------------------------------------------------------------------
    device.open_app("Markor")
    device.settle(2)

    def on_main_screen():
        return (device.find(text="Markor") is not None
                or device.find(description="Create a new file or folder") is not None)

    if not on_main_screen():
        # Launch may have been slow or the app was in a weird state: retry
        # from the home screen.
        device.navigate_home()
        device.settle(1)
        device.open_app("Markor")
        device.settle(3)
        if not on_main_screen():
            raise RuntimeError(
                "Markor did not launch: app missing or main screen not detected"
            )

    # ------------------------------------------------------------------
    # Step 2: locate the target note row by its exact name and long-press
    # it to enter selection mode. Never rely on row position.
    # ------------------------------------------------------------------
    def find_target_row():
        # Markor labels file list rows with a content description like
        # 'File <name>'.  The shown name may omit the extension and can
        # carry stray whitespace, so compare stripped descriptions against
        # both the full file name and its stem.  Exact (stripped) equality
        # keeps similarly named notes (e.g. 'edited_<stem>.md' or
        # '<stem>_2023_03_11.md') from ever matching.
        labels = []
        for label in (file_name, stem):
            if label and label not in labels:
                labels.append(label)
        rows = device.elements()

        def desc_of(el):
            return (el.get("description") or "").strip()

        def text_of(el):
            return (el.get("text") or "").strip()

        checks = (
            lambda el, label: desc_of(el) == "File " + label or desc_of(el) == label,
            lambda el, label: text_of(el) == label,
            lambda el, label: (desc_of(el).startswith("File ")
                               and (desc_of(el)[5:].strip() == label
                                    or desc_of(el)[5:].strip().startswith(label + " "))),
        )
        for require_clickable in (True, False):
            for check in checks:
                for label in labels:
                    for el in rows:
                        if require_clickable and not el.get("clickable"):
                            continue
                        if check(el, label):
                            return el.get("index")
        return None

    target = find_target_row()
    if target is None:
        # The row may be off-screen: scroll down and look again.
        for _ in range(3):
            device.scroll("down")
            device.settle(1)
            target = find_target_row()
            if target is not None:
                break
    if target is None:
        # It may also sit above the current viewport: scroll back up.
        for _ in range(3):
            device.scroll("up")
            device.settle(1)
            target = find_target_row()
            if target is not None:
                break
    if target is None:
        raise LookupError(
            "Note item for '%s' not found on screen (tried scrolling)" % file_name
        )

    def long_press(index):
        if hasattr(device, "long_press"):
            device.long_press(index)
        else:
            device.execute({"action_type": "long_press", "index": index,
                            "duration_seconds": 1.0})

    long_press(target)
    device.settle(1)

    # ------------------------------------------------------------------
    # Step 3: tap the Delete action shown in the selection top bar.
    # It only acts on the currently selected note, so verify the target
    # is still present/selected first.
    # ------------------------------------------------------------------
    def find_delete_button():
        btn = device.find(description="Delete", clickable=True)
        if btn is not None:
            return btn
        btn = device.find(text="Delete", clickable=True)
        if btn is not None:
            return btn
        for el in device.elements():
            desc = (el.get("description") or "").strip().lower()
            txt = (el.get("text") or "").strip().lower()
            if el.get("clickable") and ("delete" in desc or "delete" in txt):
                return el.get("index")
        return None

    delete_btn = find_delete_button()
    if delete_btn is None:
        # Selection may not have taken effect: retry the long-press once.
        target = find_target_row()
        if target is not None:
            long_press(target)
            device.settle(1)
        delete_btn = find_delete_button()
    if delete_btn is None:
        raise RuntimeError(
            "Delete button not found - ensure the target note (%s) is "
            "selected first" % file_name
        )
    device.click(delete_btn)
    device.settle(1)  # wait for the confirmation dialog

    # ------------------------------------------------------------------
    # Step 4: confirm the deletion in the dialog.
    # ------------------------------------------------------------------
    def find_confirm_button():
        for label in ("OK", "Yes", "Confirm", "Delete"):
            btn = device.find(text=label, clickable=True)
            if btn is not None:
                return btn
        for el in device.elements():
            txt = (el.get("text") or "").strip().upper()
            desc = (el.get("description") or "").strip().upper()
            if el.get("clickable") and (txt in ("OK", "YES", "CONFIRM")
                                        or desc in ("OK", "YES", "CONFIRM")):
                return el.get("index")
        return None

    ok_btn = find_confirm_button()
    if ok_btn is None:
        device.settle(2)  # dialog may still be coming up
        ok_btn = find_confirm_button()
    if ok_btn is None:
        raise LookupError(
            "No confirmation button found in the delete dialog for note '%s'"
            % file_name
        )
    device.click(ok_btn)
    device.settle(2)

    # ------------------------------------------------------------------
    # Step 5: verify the note is gone from the list.
    # ------------------------------------------------------------------
    for _ in range(3):
        if find_target_row() is None:
            return True
        device.settle(2)
    raise RuntimeError(
        "Note '%s' is still listed after the delete flow" % file_name
    )
