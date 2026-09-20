PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Output document file name, including the .odt extension.",
    },
    "title": {
        "type": "string",
        "description": "Title line for line one of the memo.",
    },
    "body": {
        "type": "string",
        "description": "Body sentence for line three of the memo.",
    },
}


def program(device, binding: dict) -> bool:
    def _lower(value):
        return str(value if value is not None else "").lower()

    def _find_writer_doc():
        idx = device.find(role="document-text", editable=True)
        if idx is not None:
            return idx

        idx = device.find(role="document-text")
        if idx is not None:
            return idx

        best_index = None
        best_score = -1

        for el in device.elements():
            role = _lower(el.get("role"))
            name = _lower(el.get("name"))
            desc = _lower(el.get("description"))
            text = _lower(el.get("text"))

            if not el.get("editable"):
                continue

            if role not in ("document-text", "document", "text", "entry"):
                continue

            score = 0
            if role in ("document-text", "document"):
                score += 100

            if (
                "document" in name
                or "document" in desc
                or "document" in text
                or "writer" in name
                or "writer" in desc
            ):
                score += 50
            else:
                continue

            if el.get("clickable"):
                score += 1

            if score > best_score:
                best_score = score
                best_index = el.get("index")

        return best_index

    def _ensure_writer_doc():
        idx = _find_writer_doc()
        if idx is not None:
            return idx

        for app_name in ("LibreOffice Writer", "LibreOffice", "libreoffice", "soffice"):
            try:
                device.open_app(app_name)
                device.settle(2)
                break
            except Exception:
                pass

        idx = _find_writer_doc()
        if idx is not None:
            return idx

        win = _lower(device.active_window())
        if "start center" in win or "libreoffice" in win:
            device.hotkey("ctrl", "n")
            device.settle(2)

        idx = _find_writer_doc()
        if idx is None:
            raise RuntimeError("Could not locate an editable LibreOffice Writer document area")

        return idx

    for key in ("file_name", "title", "body"):
        if key not in binding:
            raise RuntimeError(f"binding is missing required key: {key}")
        if binding[key] is None:
            raise RuntimeError(f"binding value for {key} must not be None")

    file_name = str(binding["file_name"]).strip()
    title = str(binding["title"])
    body = str(binding["body"])

    if not file_name:
        raise RuntimeError("file_name must be non-empty")

    if not file_name.lower().endswith(".odt"):
        file_name += ".odt"

    title = title.replace("\n", " ").replace("\r", " ")
    body = body.replace("\n", " ").replace("\r", " ")

    doc_idx = _ensure_writer_doc()
    device.click(index=doc_idx)
    device.settle(0.5)

    device.hotkey("ctrl", "a")
    device.press("delete")
    device.settle(0.5)

    if title:
        device.type_at_caret(title)

    device.press("enter")
    device.press("enter")

    if body:
        device.type_at_caret(body)

    device.settle(0.5)

    doc_idx = _find_writer_doc()
    if doc_idx is not None:
        device.click(index=doc_idx)
        device.settle(0.3)

    def _is_save_dialog_title(window_title):
        w = _lower(window_title).strip()
        if " | save" in w:
            return True
        if w.endswith("save") or w.endswith("save as") or w.endswith("save dialog"):
            return True
        if "save dialog" in w:
            return True
        return False

    def _save_dialog_open():
        if _is_save_dialog_title(device.active_window()):
            return True

        if device.find(name="Name:", role="label") is not None:
            if device.find(role="text", editable=True, contains="Untitled") is not None:
                return True
            if device.find(role="entry", editable=True, contains="Untitled") is not None:
                return True
            if device.find(name="Save", role="push-button", clickable=True) is not None:
                return True
            if device.find(name="Save", role="button", clickable=True) is not None:
                return True

        return False

    def _is_bad_name_field(name, desc):
        return (
            "search" in name
            or "search" in desc
            or "filter" in name
            or "filter" in desc
            or "location" in name
            or "location" in desc
            or "path" in name
            or "path" in desc
            or "type" in name
            or "type" in desc
            or "file type" in name
            or "file type" in desc
            or "format" in name
            or "format" in desc
        )

    def _is_name_candidate(el):
        role = _lower(el.get("role"))
        if role not in ("text", "entry", "text-field", "combo-box"):
            return False

        if not el.get("editable"):
            return False

        name = _lower(el.get("name"))
        desc = _lower(el.get("description"))

        if _is_bad_name_field(name, desc):
            return False

        if role == "combo-box":
            if not ("name" in name or "name" in desc or "filename" in name or "filename" in desc):
                return False

        return True

    def _find_name_field():
        idx = device.find(role="text", editable=True, contains="Untitled")
        if idx is not None:
            return idx

        idx = device.find(role="entry", editable=True, contains="Untitled")
        if idx is not None:
            return idx

        for crit in (
            {"role": "text", "editable": True, "name": "Name:"},
            {"role": "entry", "editable": True, "name": "Name:"},
            {"role": "text", "editable": True, "description": "Name:"},
            {"role": "entry", "editable": True, "description": "Name:"},
            {"role": "text", "editable": True, "name": "Name"},
            {"role": "entry", "editable": True, "name": "Name"},
            {"role": "text", "editable": True, "description": "Name"},
            {"role": "entry", "editable": True, "description": "Name"},
        ):
            idx = device.find(**crit)
            if idx is not None:
                return idx

        candidates = []

        for el in device.elements():
            if not _is_name_candidate(el):
                continue

            role = _lower(el.get("role"))
            name = _lower(el.get("name"))
            desc = _lower(el.get("description"))
            text = _lower(el.get("text"))

            score = 0

            if "name" in name or "name" in desc or "filename" in name or "filename" in desc:
                score += 5

            if "untitled" in text:
                score += 3

            if ".odt" in text and role != "combo-box":
                score += 1

            if el.get("clickable"):
                score += 1

            candidates.append((score, el.get("index")))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            best_score, best_idx = candidates[0]
            if best_score >= 0 and best_idx is not None:
                return best_idx

        for el in device.elements():
            if _is_name_candidate(el):
                idx = el.get("index")
                if idx is not None:
                    return idx

        return None

    opened = False

    for _ in range(3):
        device.hotkey("ctrl", "s")
        device.settle(1)

        if _save_dialog_open():
            opened = True
            break

        doc_idx = _find_writer_doc()
        if doc_idx is not None:
            device.click(index=doc_idx)
            device.settle(0.3)

    if not opened:
        raise RuntimeError("Ctrl+S did not open the LibreOffice Save dialog")

    def _find_desktop():
        for crit in (
            {"name": "Desktop", "role": "label"},
            {"name": "Desktop", "role": "table-cell", "clickable": True},
            {"name": "Desktop", "role": "toggle-button"},
            {"name": "Desktop", "role": "button", "clickable": True},
            {"name": "Desktop", "clickable": True},
            {"name": "Desktop"},
        ):
            idx = device.find(**crit)
            if idx is not None:
                return idx, _lower(crit.get("role"))

        for el in device.elements():
            name = _lower(el.get("name"))
            text = _lower(el.get("text"))
            desc = _lower(el.get("description"))

            if name == "desktop" or text == "desktop" or "desktop" in name or "desktop" in text:
                role = _lower(el.get("role"))
                if (
                    role in ("label", "table-cell", "toggle-button", "button", "list-item", "icon")
                    or el.get("clickable")
                ):
                    idx = el.get("index")
                    if idx is not None:
                        return idx, role

        return None, None

    desktop_clicked = False

    for _ in range(4):
        desktop_idx, desktop_role = _find_desktop()

        if desktop_idx is not None:
            device.click(index=desktop_idx)
            device.settle(0.5)

            if desktop_role in ("table-cell", "list-item", "icon", "file"):
                desktop_idx2, _ = _find_desktop()
                if desktop_idx2 is not None:
                    device.click(index=desktop_idx2)
                    device.settle(1)
            else:
                device.settle(0.5)

            desktop_clicked = True
            break

        device.scroll(direction="down")
        device.settle(0.5)

    if not desktop_clicked:
        home_idx = None
        for crit in (
            {"name": "Home", "role": "label"},
            {"name": "Home", "role": "button", "clickable": True},
            {"name": "Home", "clickable": True},
            {"name": "Home"},
        ):
            home_idx = device.find(**crit)
            if home_idx is not None:
                break

        if home_idx is not None:
            device.click(index=home_idx)
            device.settle(1)

            desktop_idx, desktop_role = _find_desktop()
            if desktop_idx is not None:
                device.click(index=desktop_idx)
                device.settle(0.5)

                if desktop_role in ("table-cell", "list-item", "icon", "file"):
                    desktop_idx2, _ = _find_desktop()
                    if desktop_idx2 is not None:
                        device.click(index=desktop_idx2)
                        device.settle(1)
                else:
                    device.settle(0.5)

                desktop_clicked = True

        if not desktop_clicked:
            in_desktop = False
            for el in device.elements():
                text = _lower(el.get("text"))
                desc = _lower(el.get("description"))
                if "desktop" in text or "desktop" in desc:
                    in_desktop = True
                    break

            if not in_desktop:
                raise RuntimeError("Could not change the Save dialog folder to Desktop")

            device.settle(0.5)

    name_idx = _find_name_field()
    if name_idx is None:
        raise RuntimeError("Could not find the filename Name field in the Save dialog")

    device.input_text(file_name, index=name_idx)
    device.settle(0.5)

    def _find_save_button():
        for crit in (
            {"name": "Save", "role": "push-button", "clickable": True},
            {"name": "Save", "role": "button", "clickable": True},
            {"name": "Save", "role": "push-button"},
            {"name": "Save", "role": "button"},
            {"name": "Save", "clickable": True},
            {"name": "Save"},
        ):
            idx = device.find(**crit)
            if idx is not None:
                return idx

        for el in device.elements():
            name = _lower(el.get("name"))
            text = _lower(el.get("text"))
            role = _lower(el.get("role"))

            if name == "save" or text == "save":
                if role in ("push-button", "button") or el.get("clickable"):
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        for el in device.elements():
            name = _lower(el.get("name"))
            text = _lower(el.get("text"))

            if name == "save" or text == "save":
                idx = el.get("index")
                if idx is not None:
                    return idx

        return None

    save_idx = _find_save_button()
    if save_idx is not None:
        device.click(index=save_idx)
        device.settle(1)
    else:
        device.press("enter")
        device.settle(1)

    def _find_confirm_button():
        for crit in (
            {"name": "Replace", "role": "push-button", "clickable": True},
            {"name": "Overwrite", "role": "push-button", "clickable": True},
            {"name": "Yes", "role": "push-button", "clickable": True},
            {"name": "Replace", "role": "button", "clickable": True},
            {"name": "Overwrite", "role": "button", "clickable": True},
            {"name": "Yes", "role": "button", "clickable": True},
            {"name": "Replace", "clickable": True},
            {"name": "Overwrite", "clickable": True},
            {"name": "Yes", "clickable": True},
            {"name": "Replace"},
            {"name": "Overwrite"},
            {"name": "Yes"},
        ):
            idx = device.find(**crit)
            if idx is not None:
                return idx

        for el in device.elements():
            name = _lower(el.get("name"))
            text = _lower(el.get("text"))
            role = _lower(el.get("role"))

            if name in ("replace", "overwrite", "yes") or text in ("replace", "overwrite", "yes"):
                if role in ("push-button", "button") or el.get("clickable"):
                    idx = el.get("index")
                    if idx is not None:
                        return idx

        return None

    for _ in range(4):
        confirm_idx = _find_confirm_button()
        if confirm_idx is not None:
            device.click(index=confirm_idx)
            device.settle(1)
            continue

        win = _lower(device.active_window())
        if any(term in win for term in ("confirm", "exists", "replace", "overwrite", "format")):
            device.press("enter")
            device.settle(1)
            continue

        if _save_dialog_open():
            save_idx = _find_save_button()
            if save_idx is not None:
                device.click(index=save_idx)
                device.settle(1)
            else:
                device.press("enter")
                device.settle(1)
            continue

        break

    if _save_dialog_open():
        save_idx = _find_save_button()
        if save_idx is not None:
            device.click(index=save_idx)
            device.settle(1)

    if _save_dialog_open():
        raise RuntimeError("The Save dialog did not close after confirming")

    win = device.active_window()
    win_l = _lower(win)
    base_name = file_name.rsplit(".", 1)[0].lower()

    if (
        "libreoffice" not in win_l
        and "writer" not in win_l
        and file_name.lower() not in win_l
        and base_name not in win_l
    ):
        raise RuntimeError("The document does not appear to be open in LibreOffice Writer after saving")

    return True
