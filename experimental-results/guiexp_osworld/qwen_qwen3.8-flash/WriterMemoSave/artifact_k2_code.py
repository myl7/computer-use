PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Document file name to save on the Desktop, ending in .odt",
        "required": True,
    },
    "title": {
        "type": "string",
        "description": "Text for the first line (title)",
        "required": True,
    },
    "body": {
        "type": "string",
        "description": "Text for the third line (body sentence)",
        "required": True,
    },
}


def program(device, binding: dict) -> bool:
    required = ("file_name", "title", "body")
    for key in required:
        if key not in binding:
            raise ValueError(f"binding missing required key: {key}")
        if not isinstance(binding[key], str):
            raise ValueError(f"binding key {key} must be a string")

    file_name = binding["file_name"].strip()
    file_name = file_name.replace("\r", " ").replace("\n", " ").strip()
    if "/" in file_name or "\\" in file_name:
        file_name = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if not file_name:
        raise ValueError("file_name must not be empty")
    if not file_name.lower().endswith(".odt"):
        file_name += ".odt"
    if file_name.lower() == ".odt":
        raise ValueError("file_name must include a base name")

    title = binding["title"].replace("\r", " ").replace("\n", " ")
    body = binding["body"].replace("\r", " ").replace("\n", " ")

    def find_doc():
        for kwargs in (
            {"role": "document-text", "editable": True},
            {"role": "document-text"},
            {"name": "Untitled 1 - LibreOffice Document", "role": "document-text"},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        best = None
        best_score = -1
        for el in device.elements():
            if (el.get("role") or "") != "document-text":
                continue
            name = el.get("name") or ""
            score = 10
            if el.get("editable"):
                score += 5
            if "Writer" in name or "Document" in name or "Untitled" in name:
                score += 2
            if score > best_score:
                best_score = score
                best = el["index"]
        return best

    def ensure_writer():
        for _ in range(5):
            doc = find_doc()
            if doc is not None:
                return doc
            device.wait()

        for app in ("libreoffice --writer", "LibreOffice Writer", "writer"):
            try:
                device.open_app(app)
                device.settle(2)
            except Exception:
                pass
            for _ in range(5):
                doc = find_doc()
                if doc is not None:
                    return doc
                device.wait()

        for kwargs in (
            {"name": "Writer", "role": "push-button"},
            {"contains": "Writer", "role": "push-button"},
            {"name": "Writer", "role": "table-cell"},
            {"contains": "Writer", "role": "table-cell"},
            {"name": "Writer", "role": "link"},
            {"contains": "Writer", "role": "link"},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                try:
                    device.click(index=idx)
                    device.settle(2)
                except Exception:
                    pass
                for _ in range(5):
                    doc = find_doc()
                    if doc is not None:
                        return doc
                    device.wait()

        raise RuntimeError("Could not find or open a LibreOffice Writer document")

    def find_name_field():
        for kwargs in (
            {"name": "Name:", "editable": True},
            {"name": "Name", "editable": True},
            {"contains": "Name", "editable": True},
            {"contains": "file name", "editable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        best = None
        best_score = -1
        for el in device.elements():
            if not el.get("editable"):
                continue
            role = (el.get("role") or "").lower()
            if role not in (
                "text",
                "entry",
                "combo-box",
                "combo-box-entry",
                "file-chooser-entry",
                "search-text",
            ):
                continue

            name = (el.get("name") or "").lower()
            text = (el.get("text") or "").lower()
            desc = (el.get("description") or "").lower()

            score = 0
            if "name" in name or "name" in desc:
                score += 15
            if "file name" in name or "file name" in desc:
                score += 20
            if role in ("text", "entry"):
                score += 10
            if "untitled" in text:
                score += 12
            if text == "":
                score += 3
            if "search" in name or "search" in text or "search" in desc:
                score -= 25
            if "location" in name or "location" in text or "location" in desc:
                score -= 15
            if "path" in name or "path" in text or "path" in desc:
                score -= 10
            if text.startswith("/") or text.startswith("~"):
                score -= 15

            if score > best_score:
                best_score = score
                best = el["index"]

        return best if best_score >= 0 else None

    def find_save_button_index():
        for kwargs in (
            {"name": "Save", "role": "push-button", "clickable": True},
            {"name": "Save", "role": "push-button"},
            {"name": "Save", "role": "button", "clickable": True},
            {"name": "Save", "role": "button"},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        best = None
        best_score = -1
        for el in device.elements():
            role = (el.get("role") or "").lower()
            if role not in ("push-button", "button", "toggle-button"):
                continue

            name = (el.get("name") or "").lower()
            text = (el.get("text") or "").lower()
            desc = (el.get("description") or "").lower()
            label = name or text or desc

            score = 0
            if label == "save":
                score += 30
            elif label.startswith("save"):
                score += 20
            elif "save" in label:
                score += 10
            else:
                continue

            if el.get("clickable"):
                score += 5
            if "as" in label:
                score -= 5
            if "cancel" in label or "close" in label:
                score -= 20

            if score > best_score:
                best_score = score
                best = el["index"]

        return best if best_score > 0 else None

    def save_dialog_present():
        win = str(device.active_window() or "")
        if "| Save" in win or win.strip() == "Save" or "Save As" in win:
            return True

        if find_name_field() is None:
            return False

        for kwargs in (
            {"name": "Cancel", "role": "push-button", "clickable": True},
            {"name": "Cancel", "role": "push-button"},
            {"contains": "Cancel", "role": "push-button"},
        ):
            if device.find(**kwargs) is not None:
                return True

        for el in device.elements():
            role = (el.get("role") or "").lower()
            if role not in ("push-button", "button", "toggle-button"):
                continue
            label = (
                (el.get("name") or "")
                or (el.get("text") or "")
                or (el.get("description") or "")
            ).lower()
            if label == "cancel" or label.startswith("cancel"):
                return True

        return False

    def wait_for_save_dialog():
        for _ in range(10):
            if save_dialog_present():
                return True
            device.wait()
        return False

    def find_location(loc):
        for kwargs in (
            {"name": loc, "role": "label"},
            {"name": loc, "role": "static"},
            {"name": loc, "role": "table-cell", "clickable": True},
            {"name": loc, "role": "toggle-button", "clickable": True},
            {"name": loc, "role": "push-button", "clickable": True},
            {"name": loc, "clickable": True},
            {"contains": loc, "role": "label"},
            {"contains": loc, "role": "static"},
            {"contains": loc, "role": "table-cell", "clickable": True},
            {"contains": loc, "role": "toggle-button", "clickable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        best = None
        best_score = -1
        for el in device.elements():
            name = el.get("name") or ""
            text = el.get("text") or ""
            desc = el.get("description") or ""
            role = el.get("role") or ""
            haystack = f"{name} {text} {desc}".lower()
            if loc.lower() not in haystack:
                continue

            score = 0
            if name.lower() == loc.lower():
                score += 25
            if text.lower() == loc.lower():
                score += 20
            if el.get("clickable"):
                score += 8
            if role in (
                "label",
                "static",
                "toggle-button",
                "push-button",
                "tree-item",
                "list-item",
                "menu-item",
                "link",
                "icon",
            ):
                score += 12
            elif role == "table-cell":
                score += 2

            if score > best_score:
                best_score = score
                best = el["index"]

        return best

    def find_location_entry():
        for kwargs in (
            {"name": "Location", "editable": True},
            {"contains": "Location", "editable": True},
            {"name": "Path", "editable": True},
            {"contains": "Path", "editable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx

        for el in device.elements():
            if not el.get("editable"):
                continue
            role = (el.get("role") or "").lower()
            if role not in (
                "text",
                "entry",
                "combo-box",
                "combo-box-entry",
                "file-chooser-entry",
                "search-text",
            ):
                continue
            name = (el.get("name") or "").lower()
            desc = (el.get("description") or "").lower()
            text = (el.get("text") or "").lower()
            if (
                "location" in name
                or "location" in desc
                or "path" in name
                or "path" in desc
                or text.startswith("/")
            ):
                return el["index"]

        return None

    def click_save_button():
        idx = find_save_button_index()
        if idx is not None:
            device.click(index=idx)
            return True
        return False

    def navigate_to_desktop():
        desktop_idx = find_location("Desktop")

        if desktop_idx is None:
            device.scroll("down")
            device.settle(0.5)
            desktop_idx = find_location("Desktop")

        if desktop_idx is None:
            device.scroll("up")
            device.settle(0.5)
            desktop_idx = find_location("Desktop")

        if desktop_idx is not None:
            device.click(index=desktop_idx)
            device.settle(0.5)
            return

        device.hotkey("ctrl", "l")
        device.settle(0.5)
        loc_idx = find_location_entry()
        if loc_idx is not None:
            device.input_text("~/Desktop", index=loc_idx)
            device.press("enter")
            device.settle(1)
            return

        if find_name_field() is None:
            device.type_at_caret("~/Desktop")
            device.press("enter")
            device.settle(1)
            return

        raise RuntimeError("Could not navigate to Desktop")

    doc_idx = ensure_writer()

    device.click(index=doc_idx)
    device.hotkey("ctrl", "a")
    device.press("delete")
    device.settle(0.3)

    device.input_text(f"{title}\n\n{body}", index=doc_idx)
    device.settle(0.5)

    initial_win = str(device.active_window() or "")

    device.click(index=doc_idx)
    device.hotkey("ctrl", "s")
    device.settle(1)

    if not wait_for_save_dialog():
        doc_idx = find_doc()
        if doc_idx is not None:
            device.click(index=doc_idx)
        device.hotkey("ctrl", "s")
        device.settle(1)
        if not wait_for_save_dialog():
            raise RuntimeError("Save dialog did not open")

    navigate_to_desktop()

    name_idx = find_name_field()
    if name_idx is None:
        device.wait()
        name_idx = find_name_field()
    if name_idx is None:
        raise RuntimeError("Save dialog Name field not found")

    device.input_text(file_name, index=name_idx)
    device.settle(0.5)

    if not click_save_button():
        device.press("enter")
    device.settle(1)

    stem = file_name.rsplit(".", 1)[0]

    for _ in range(8):
        win = str(device.active_window() or "")

        if save_dialog_present():
            if click_save_button():
                device.settle(1)
                continue
            device.press("enter")
            device.settle(1)
            continue

        handled = False
        for kwargs in (
            {"name": "Replace", "role": "push-button", "clickable": True},
            {"contains": "Replace", "role": "push-button"},
            {"name": "Yes", "role": "push-button", "clickable": True},
            {"name": "OK", "role": "push-button", "clickable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                handled = True
                break

        if handled:
            continue

        if file_name in win:
            return True
        if stem in win and win != initial_win:
            return True
        if "LibreOffice Writer" in win and win != initial_win and "Untitled" not in win:
            return True

        device.wait()

    win = str(device.active_window() or "")
    if file_name in win or (stem in win and win != initial_win):
        return True
    if "Save" not in win and "LibreOffice Writer" in win and win != initial_win:
        return True

    raise RuntimeError("Document was not saved")
