import os

PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods",
        "pattern": r"\.ods$",
    },
    "header_a": {
        "type": "string",
        "description": "Column A header text for cell A1",
    },
    "header_b": {
        "type": "string",
        "description": "Column B header text for cell B1",
    },
    "val_a2": {
        "type": "integer",
        "description": "Integer value for cell A2",
    },
    "val_b2": {
        "type": "integer",
        "description": "Integer value for cell B2",
    },
    "val_a3": {
        "type": "integer",
        "description": "Integer value for cell A3",
    },
    "val_b3": {
        "type": "integer",
        "description": "Integer value for cell B3",
    },
}


def program(device, binding: dict) -> bool:
    required = (
        "file_name",
        "header_a",
        "header_b",
        "val_a2",
        "val_b2",
        "val_a3",
        "val_b3",
    )

    if not isinstance(binding, dict):
        raise TypeError("binding must be a dict")

    for key in required:
        if key not in binding:
            raise KeyError(f"binding missing required key: {key}")

    def _text(value):
        if value is None:
            return ""
        return str(value)

    def _int_text(value):
        try:
            return str(int(value))
        except Exception:
            try:
                return str(int(float(value)))
            except Exception:
                return _text(value).strip()

    def _header_text(value):
        s = _text(value)
        s = s.replace("\t", " ").replace("\n", " ").replace("\r", " ")
        s = s.strip()
        if not s:
            return s

        # Force literal text for formula-like or numeric-looking headers.
        if s[0] in ("=", "+", "-", "@"):
            return "'" + s
        try:
            float(s)
            return "'" + s
        except Exception:
            return s

    def _title():
        try:
            return _text(device.active_window())
        except Exception:
            return ""

    def _is_calc(t=None):
        if t is None:
            t = _title()
        tl = t.lower()
        return "calc" in tl or "spreadsheet" in tl

    def _find(**crit):
        try:
            return device.find(**crit)
        except Exception:
            return None

    def _elements():
        try:
            return device.elements()
        except Exception:
            return []

    def _dialog_open():
        t = _title().lower()
        if not t:
            return False

        if (
            " | save" in t
            or " | save as" in t
            or " | overwrite" in t
            or " | replace" in t
            or " | format" in t
        ):
            return True

        if t.strip() in ("save", "save as", "overwrite", "replace"):
            return True

        if " | " in t:
            last = t.rsplit(" | ", 1)[-1]
            if (
                "save" in last
                or "overwrite" in last
                or "replace" in last
                or "format" in last
            ):
                return True

        return False

    def _find_calc_button():
        crits = (
            {"name": "Calc", "role": "push-button"},
            {"name": "Calc"},
            {"contains": "Calc", "role": "push-button"},
            {"contains": "Calc", "role": "icon"},
            {"contains": "Calc", "role": "link"},
        )

        for crit in crits:
            idx = _find(**crit)
            if idx is not None:
                return idx

        for el in _elements():
            role = _text(el.get("role")).lower()
            name = _text(el.get("name")).lower()
            text = _text(el.get("text")).lower()

            if ("button" in role or "icon" in role or "link" in role) and (
                "calc" in name or "calc" in text
            ):
                idx = el.get("index")
                if idx is not None:
                    return idx

        return None

    def _find_cell(cell_name):
        idx = _find(name=cell_name, role="table-cell")
        if idx is not None:
            return idx

        idx = _find(name=cell_name)
        if idx is not None:
            return idx

        for el in _elements():
            name = _text(el.get("name")).strip()
            text = _text(el.get("text")).strip()
            if name == cell_name or text == cell_name:
                idx = el.get("index")
                if idx is not None:
                    return idx

        return None

    def _find_button(names):
        lower_names = {n.lower() for n in names}

        for name in names:
            idx = _find(role="push-button", name=name)
            if idx is not None:
                return idx

        for name in names:
            if len(name) > 3:
                idx = _find(role="push-button", contains=name)
                if idx is not None:
                    return idx

        for el in _elements():
            role = _text(el.get("role")).lower()
            name = _text(el.get("name")).lower()
            text = _text(el.get("text")).lower()

            if "button" in role and (name in lower_names or text in lower_names):
                idx = el.get("index")
                if idx is not None:
                    return idx

        return None

    def _find_name_field():
        crits = (
            {"name": "Name", "editable": True},
            {"name": "File name", "editable": True},
            {"contains": "Name", "editable": True},
            {"contains": "File name", "editable": True},
            {"contains": "Location", "editable": True},
            {"contains": "Path", "editable": True},
        )

        for crit in crits:
            idx = _find(**crit)
            if idx is not None:
                return idx

        for el in _elements():
            if not el.get("editable"):
                continue

            idx = el.get("index")
            if idx is None:
                continue

            blob = " ".join(
                _text(el.get(k)) for k in ("name", "description", "text", "role")
            ).lower()

            if "path" in blob:
                continue

            if "name" in blob or "untitled" in blob:
                return idx

        return None

    def _desktop_path():
        candidates = []

        try:
            candidates.append(os.path.expanduser("~/Desktop"))
        except Exception:
            pass

        candidates.append("/home/user/Desktop")

        for c in candidates:
            if c and not c.startswith("~"):
                try:
                    if os.path.isdir(c):
                        return c
                except Exception:
                    pass

        return "/home/user/Desktop"

    def _ensure_calc():
        t = _title()

        if _is_calc(t) or _find(role="document-spreadsheet") is not None:
            return t

        if _dialog_open():
            try:
                device.press("escape")
            except Exception:
                pass
            t = _title()
            if _is_calc(t) or _find(role="document-spreadsheet") is not None:
                return t

        idx = _find_calc_button()
        if idx is not None:
            try:
                device.click(index=idx)
            except Exception:
                pass
            t = _title()
            if _is_calc(t) or _find(role="document-spreadsheet") is not None:
                return t

        try:
            device.open_app("LibreOffice Calc")
        except Exception:
            pass

        t = _title()
        if _is_calc(t) or _find(role="document-spreadsheet") is not None:
            return t

        idx = _find_calc_button()
        if idx is not None:
            try:
                device.click(index=idx)
            except Exception:
                pass
            t = _title()
            if _is_calc(t) or _find(role="document-spreadsheet") is not None:
                return t

        try:
            device.hotkey("ctrl", "n")
        except Exception:
            pass

        t = _title()
        if _is_calc(t) or _find(role="document-spreadsheet") is not None:
            return t

        try:
            device.open_app("LibreOffice Calc")
        except Exception:
            pass

        t = _title()
        if _is_calc(t) or _find(role="document-spreadsheet") is not None:
            return t

        raise RuntimeError("Could not open a Calc document")

    ha = _header_text(binding["header_a"])
    hb = _header_text(binding["header_b"])
    va2 = _int_text(binding["val_a2"])
    vb2 = _int_text(binding["val_b2"])
    va3 = _int_text(binding["val_a3"])
    vb3 = _int_text(binding["val_b3"])

    def _fill_data():
        cells = (
            ("A1", ha),
            ("B1", hb),
            ("A2", va2),
            ("B2", vb2),
            ("A3", va3),
            ("B3", vb3),
        )

        ok = True
        for cell_name, value in cells:
            idx = _find_cell(cell_name)
            if idx is None:
                ok = False
                break

            try:
                device.input_text(value, index=idx)
            except Exception:
                ok = False
                break

        if ok:
            return True

        # Keyboard fallback: fill column A, then column B.
        doc = _find(role="document-spreadsheet")
        if doc is not None:
            try:
                device.click(index=doc)
            except Exception:
                pass

        a1 = _find_cell("A1")
        if a1 is not None:
            try:
                device.click(index=a1)
            except Exception:
                pass

        try:
            device.hotkey("ctrl", "home")
        except Exception:
            pass

        def _type_current(value):
            try:
                device.type_at_caret(value)
                return True
            except Exception:
                try:
                    device.input_text(value)
                    return True
                except Exception:
                    return False

        for value in (ha, va2, va3):
            if not _type_current(value):
                return False
            try:
                device.press("enter")
            except Exception:
                pass

        try:
            device.hotkey("ctrl", "home")
        except Exception:
            pass

        try:
            device.press("right")
        except Exception:
            pass

        for value in (hb, vb2, vb3):
            if not _type_current(value):
                return False
            try:
                device.press("enter")
            except Exception:
                pass

        return True

    fname = os.path.basename(_text(binding["file_name"]).strip())
    if not fname:
        raise RuntimeError("file_name is empty")

    if not fname.lower().endswith(".ods"):
        fname += ".ods"

    desktop = _desktop_path()
    abs_path = os.path.join(desktop, fname)
    rel_path = "Desktop/" + fname

    def _save_with_path(path):
        fname_lower = fname.lower()
        base_lower = fname_lower.rsplit(".", 1)[0] if "." in fname_lower else fname_lower

        for _ in range(3):
            t = _title()
            tl = t.lower()

            if fname_lower in tl and not _dialog_open():
                return True

            if not _is_calc(t) and not _dialog_open():
                try:
                    _ensure_calc()
                except Exception:
                    pass

            if not _dialog_open():
                try:
                    device.hotkey("ctrl", "s")
                except Exception:
                    pass

            t = _title()
            tl = t.lower()
            if fname_lower in tl and not _dialog_open():
                return True

            if not _dialog_open():
                try:
                    device.hotkey("ctrl", "shift", "s")
                except Exception:
                    pass

            t = _title()
            tl = t.lower()
            if fname_lower in tl and not _dialog_open():
                return True

            if not _dialog_open():
                continue

            # Handle any already-open format/overwrite dialog before typing path.
            for __ in range(3):
                btn = _find_button(("Use ODF Format!", "ODF Format"))
                if btn is not None:
                    try:
                        device.click(index=btn)
                    except Exception:
                        pass
                    continue

                if "overwrite" in t.lower() or "replace" in t.lower():
                    btn = _find_button(("Yes", "Replace", "Overwrite"))
                    if btn is not None:
                        try:
                            device.click(index=btn)
                        except Exception:
                            pass
                        continue

                break

            idx = _find_name_field()
            typed = False

            if idx is not None:
                try:
                    device.input_text(path, index=idx)
                    typed = True
                except Exception:
                    typed = False

            if not typed:
                try:
                    device.hotkey("ctrl", "l")
                    device.type_at_caret(path)
                    typed = True
                except Exception:
                    pass

            if not typed:
                try:
                    device.hotkey("ctrl", "a")
                    device.type_at_caret(path)
                    typed = True
                except Exception:
                    pass

            if not typed:
                try:
                    device.input_text(path)
                    typed = True
                except Exception:
                    pass

            btn = _find_button(("Save", "Save As"))
            if btn is not None:
                try:
                    device.click(index=btn)
                except Exception:
                    pass
            else:
                try:
                    device.press("enter")
                except Exception:
                    pass

            # Follow-up dialogs: format, overwrite, or still-open save.
            for __ in range(4):
                t = _title()
                tl = t.lower()

                if (
                    fname_lower in tl
                    or (base_lower and base_lower in tl and ".ods" in tl)
                ) and not _dialog_open():
                    return True

                btn = _find_button(("Use ODF Format!", "ODF Format"))
                if btn is not None:
                    try:
                        device.click(index=btn)
                    except Exception:
                        pass
                    continue

                if "overwrite" in tl or "replace" in tl:
                    btn = _find_button(("Yes", "Replace", "Overwrite"))
                    if btn is not None:
                        try:
                            device.click(index=btn)
                        except Exception:
                            pass
                        continue

                if _dialog_open():
                    btn = _find_button(("Save", "Save As"))
                    if btn is not None:
                        try:
                            device.click(index=btn)
                        except Exception:
                            pass
                    else:
                        try:
                            device.press("enter")
                        except Exception:
                            pass
                    continue

                break

            t = _title()
            tl = t.lower()
            if fname_lower in tl:
                return True

            if not _dialog_open() and "untitled" not in tl:
                return True

        return False

    if _dialog_open():
        try:
            device.press("escape")
        except Exception:
            pass

    _ensure_calc()

    if not _fill_data():
        raise RuntimeError("Could not fill the spreadsheet")

    if not _save_with_path(abs_path):
        if not _save_with_path(rel_path):
            if not _save_with_path(fname):
                raise RuntimeError("Could not save the spreadsheet")

    t = _title()
    if fname.lower() in t.lower():
        return True

    if not _dialog_open() and "untitled" not in t.lower():
        return True

    raise RuntimeError("Could not verify saved spreadsheet")
