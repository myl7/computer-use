PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Document file name ending in .odt",
    },
    "title": {
        "type": "string",
        "description": "Title text for line one",
    },
    "body": {
        "type": "string",
        "description": "Body sentence for line three",
    },
}


def program(device, binding: dict) -> bool:
    required = ("file_name", "title", "body")
    for key in required:
        if key not in binding:
            raise KeyError(f"binding missing required key: {key}")
        if binding[key] is None:
            raise ValueError(f"binding key {key} must not be None")

    file_name = str(binding["file_name"]).strip()
    if not file_name:
        raise ValueError("file_name must be non-empty")
    if not file_name.lower().endswith(".odt"):
        file_name = file_name + ".odt"

    title = (
        str(binding["title"])
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )
    body = (
        str(binding["body"])
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
        .strip()
    )
    stem = file_name[:-4] if file_name.lower().endswith(".odt") else file_name

    def find_writer_doc():
        idx = device.find(role="document-text", editable=True)
        if idx is not None:
            return idx
        idx = device.find(role="document-text")
        if idx is not None:
            return idx

        candidates = []
        for el in device.elements():
            if not el.get("editable"):
                continue
            role = str(el.get("role") or "")
            name = str(el.get("name") or "")
            text = str(el.get("text") or "")
            score = 0

            if "document-text" in role:
                score += 20
            elif "text" in role:
                score += 10

            if "LibreOffice" in name or "LibreOffice" in text:
                score += 10
            if "Untitled" in name or "Untitled" in text:
                score += 5
            if "Writer" in name or "Writer" in text:
                score += 5

            if score > 0:
                candidates.append((score, el["index"]))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][1]
        return None

    def is_save_dialog_open():
        try:
            aw = str(device.active_window() or "")
        except Exception:
            aw = ""

        if "Save" in aw:
            return True

        if device.find(name="Name:", role="label") is not None:
            return True

        for el in device.elements():
            role = str(el.get("role") or "").lower()
            name = str(el.get("name") or "")
            if role == "dialog" and "Save" in name:
                return True

        return False

    def find_location_entry(target="Desktop"):
        candidates = []
        for el in device.elements():
            if el.get("editable"):
                continue

            name = str(el.get("name") or "").strip()
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            role = str(el.get("role") or "").strip().lower()
            blob = " ".join((name, text, desc))

            if target not in blob:
                continue

            score = 0
            if name == target or text == target:
                score += 20
            elif blob == target:
                score += 15

            if el.get("clickable"):
                score += 5

            if role in ("label", "toggle-button", "push-button"):
                score += 10
            elif role in ("table-cell", "list-item", "tree-item", "icon"):
                score += 4

            lower_blob = blob.lower()
            if "place" in lower_blob or "bookmark" in lower_blob or "sidebar" in lower_blob:
                score += 8
            if "path" in lower_blob or "folder" in lower_blob:
                score += 2
            if "writer" in lower_blob or "document" in lower_blob:
                score -= 20

            if score > 0:
                candidates.append((score, el["index"]))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][1]

        idx = device.find(name=target, clickable=True)
        if idx is not None:
            return idx
        idx = device.find(name=target)
        if idx is not None:
            return idx
        idx = device.find(contains=target, role="label")
        if idx is not None:
            return idx
        idx = device.find(contains=target, role="table-cell", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains=target, role="toggle-button", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains=target, role="push-button", clickable=True)
        if idx is not None:
            return idx
        return None

    def select_desktop():
        desktop_idx = find_location_entry("Desktop")
        if desktop_idx is None:
            for direction in ("down", "up", "left", "right"):
                try:
                    device.scroll(direction=direction)
                except Exception:
                    pass
                device.settle(0.5)
                desktop_idx = find_location_entry("Desktop")
                if desktop_idx is not None:
                    break

        if desktop_idx is None:
            raise RuntimeError("Could not find Desktop in Save dialog")

        device.click(index=desktop_idx)
        device.settle(0.5)

    def find_name_field():
        candidates = []
        for el in device.elements():
            if not el.get("editable"):
                continue

            role = str(el.get("role") or "").strip().lower()
            if "document-text" in role:
                continue

            name = str(el.get("name") or "").strip()
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            blob = (name + " " + text + " " + desc).lower()

            score = 0
            if role in ("text", "entry"):
                score += 15
            elif role in ("combo-box", "combo box"):
                score += 5
            else:
                score += 2

            if "Name" in name or "Name" in desc:
                score += 15
            if "Untitled" in text:
                score += 10
            if text == "" or text.endswith(".odt") or text.endswith(".txt"):
                score += 3
            if file_name.lower() in text or stem.lower() in text:
                score += 10
            if el.get("clickable"):
                score += 2

            if "search" in blob:
                score -= 20
            if "path" in blob:
                score -= 10
            if "libreoffice" in blob or "writer" in blob:
                score -= 30
            if "Desktop" in text and "Untitled" not in text:
                score -= 5

            if score > 0:
                candidates.append((score, el["index"]))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][1]

        idx = device.find(role="text", editable=True, contains="Untitled")
        if idx is not None:
            return idx
        idx = device.find(role="text", editable=True)
        if idx is not None:
            return idx
        idx = device.find(role="entry", editable=True)
        if idx is not None:
            return idx
        idx = device.find(name="Name:", editable=True)
        if idx is not None:
            return idx
        idx = device.find(contains="Name:", role="text", editable=True)
        if idx is not None:
            return idx
        idx = device.find(role="text", contains="Untitled")
        if idx is not None:
            return idx
        return None

    def enter_filename(text):
        name_idx = find_name_field()
        if name_idx is None:
            raise RuntimeError("Could not find filename field in Save dialog")

        device.click(index=name_idx)
        device.settle(0.2)
        device.hotkey("ctrl", "a")
        device.press("delete")
        if text:
            device.type_at_caret(text)
        device.settle(0.3)

    def find_save_button():
        candidates = []
        for el in device.elements():
            role = str(el.get("role") or "").strip().lower()
            if role != "push-button":
                continue

            name = str(el.get("name") or "").strip()
            text = str(el.get("text") or "").strip()
            score = 0

            if name == "Save" or text == "Save":
                score += 20
            elif "Save" in name or "Save" in text:
                score += 10
            else:
                continue

            if el.get("clickable"):
                score += 5
            if "As" in name or "As" in text:
                score -= 5

            if score > 0:
                candidates.append((score, el["index"]))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][1]

        idx = device.find(name="Save", role="push-button", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(name="Save", role="push-button")
        if idx is not None:
            return idx
        idx = device.find(contains="Save", role="push-button", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains="Save", role="push-button")
        if idx is not None:
            return idx
        idx = device.find(name="Save", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(contains="Save", clickable=True)
        if idx is not None:
            return idx
        return None

    def confirm_save():
        save_idx = find_save_button()
        if save_idx is not None:
            device.click(index=save_idx)
        elif is_save_dialog_open():
            device.press("enter")
        else:
            raise RuntimeError("Could not find Save button")
        device.settle(1.0)

    def find_confirm_button():
        wanted = ("OK", "Use", "Yes", "Overwrite", "Keep", "ODF")
        candidates = []
        for el in device.elements():
            role = str(el.get("role") or "").strip().lower()
            if role != "push-button":
                continue

            name = str(el.get("name") or "").strip()
            text = str(el.get("text") or "").strip()
            label = name or text
            if not label:
                continue
            if label in ("Cancel", "No"):
                continue

            score = 0
            if label in wanted:
                score += 20
            elif any(word in label for word in wanted):
                score += 10
            else:
                continue

            if "ODF" in label.upper():
                score += 10
            if el.get("clickable"):
                score += 5

            if score > 0:
                candidates.append((score, el["index"]))

        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return candidates[0][1]
        return None

    def current_aw():
        try:
            return str(device.active_window() or "").strip()
        except Exception:
            return ""

    def has_double_extension(aw):
        if not aw or not file_name.lower().endswith(".odt"):
            return False
        return aw.lower().startswith(file_name.lower() + ".odt")

    def saved_ok(aw):
        if not aw:
            return False
        lower = aw.lower()
        return file_name.lower() in lower or stem.lower() in lower

    def resolve_save_state():
        for _ in range(3):
            aw = current_aw()
            if saved_ok(aw) and not has_double_extension(aw):
                return

            confirm_idx = find_confirm_button()
            if confirm_idx is not None:
                device.click(index=confirm_idx)
                device.settle(1.0)
                continue

            if is_save_dialog_open():
                save_idx = find_save_button()
                if save_idx is not None:
                    device.click(index=save_idx)
                    device.settle(1.0)
                    continue
                device.press("enter")
                device.settle(1.0)
                continue

            break

    doc_idx = find_writer_doc()
    if doc_idx is None:
        raise RuntimeError("Could not find editable LibreOffice Writer document")

    device.click(index=doc_idx)
    device.settle(0.2)
    device.hotkey("ctrl", "a")
    device.press("delete")
    device.settle(0.2)

    if title:
        device.type_at_caret(title)
    device.press("enter")
    device.press("enter")
    if body:
        device.type_at_caret(body)
    device.settle(0.5)

    doc_idx = find_writer_doc()
    if doc_idx is None:
        raise RuntimeError("Could not re-find LibreOffice Writer document before saving")

    device.click(index=doc_idx)
    device.settle(0.2)
    device.hotkey("ctrl", "s")
    device.wait()

    if not is_save_dialog_open():
        doc_idx = find_writer_doc()
        if doc_idx is not None:
            device.click(index=doc_idx)
            device.settle(0.2)
        device.hotkey("ctrl", "shift", "s")
        device.wait()
        if not is_save_dialog_open():
            raise RuntimeError("Save dialog did not open")

    select_desktop()
    enter_filename(file_name)
    confirm_save()
    resolve_save_state()

    aw = current_aw()
    if has_double_extension(aw):
        doc_idx = find_writer_doc()
        if doc_idx is None:
            raise RuntimeError("Could not find Writer document while correcting extension")

        device.click(index=doc_idx)
        device.settle(0.2)
        device.hotkey("ctrl", "shift", "s")
        device.wait()

        if not is_save_dialog_open():
            raise RuntimeError("Save As dialog did not open while correcting extension")

        select_desktop()
        enter_filename(stem)
        confirm_save()
        resolve_save_state()

        aw = current_aw()
        if has_double_extension(aw):
            raise RuntimeError("Document saved with a doubled extension")

    if aw and not saved_ok(aw):
        raise RuntimeError("Document does not appear to be saved with the requested name")

    return True
