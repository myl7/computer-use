PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "description": "Spreadsheet file name ending in .ods; the file is saved on the Desktop",
    },
    "header_a": {
        "type": "string",
        "description": "Header text written into cell A1",
    },
    "header_b": {
        "type": "string",
        "description": "Header text written into cell B1",
    },
    "val_a2": {
        "type": "integer",
        "description": "Number written into cell A2 (below header_a)",
    },
    "val_b2": {
        "type": "integer",
        "description": "Number written into cell B2 (below header_b)",
    },
    "val_a3": {
        "type": "integer",
        "description": "Number written into cell A3",
    },
    "val_b3": {
        "type": "integer",
        "description": "Number written into cell B3",
    },
}

_DESKTOP_DIR = "/home/user/Desktop"


def _safe_find(device, **criteria):
    try:
        return device.find(**criteria)
    except Exception:
        return None


def _find_cell(device, cell_name):
    idx = _safe_find(device, name=cell_name, role="table-cell", editable=True)
    if idx is None:
        idx = _safe_find(device, name=cell_name, role="table-cell")
    if idx is None:
        idx = _safe_find(device, name=cell_name)
    return idx


def _read_cell_text(device, cell_name):
    try:
        els = device.elements()
    except Exception:
        return None
    for el in els:
        try:
            if el.get("role") == "table-cell" and el.get("name") == cell_name:
                return el.get("text")
        except Exception:
            continue
    return None


def _fill_cell(device, cell_name, value):
    expected = str(value)
    idx = _find_cell(device, cell_name)
    if idx is None:
        device.settle(1.0)
        idx = _find_cell(device, cell_name)
    if idx is None:
        raise LookupError(
            "Spreadsheet cell '%s' not found in LibreOffice Calc" % cell_name
        )
    device.click(idx)
    device.input_text(expected, index=idx)
    device.settle(0.3)

    # Verify the commit; retry once if the cell text is readable and wrong.
    for _ in range(1):
        text = _read_cell_text(device, cell_name)
        if text is None:
            break
        if str(text).strip() == expected:
            break
        idx = _find_cell(device, cell_name)
        if idx is None:
            break
        device.click(idx)
        device.input_text(expected, index=idx)
        device.settle(0.3)


def _ensure_calc_sheet(device):
    title = device.active_window() or ""
    if "Calc" not in title:
        device.open_app("LibreOffice Calc")
        device.wait()
        device.settle(2.0)
    for attempt in range(4):
        if _find_cell(device, "A1") is not None:
            return
        if attempt == 1 and "Calc" in (device.active_window() or ""):
            # Calc is running but no fresh sheet grid: open a new spreadsheet.
            device.hotkey("ctrl", "n")
            device.settle(2.0)
        else:
            device.settle(1.0)
    raise RuntimeError("LibreOffice Calc spreadsheet with cell A1 not found")


def _overwrite_prompt_visible(device):
    if _safe_find(device, name="Replace", role="push-button") is not None:
        return True
    if _safe_find(device, name="Replace") is not None:
        return True
    if _safe_find(device, contains="already exists") is not None:
        return True
    return False


def _title_shows_saved(device, file_name):
    title = device.active_window() or ""
    if file_name in title:
        return True
    base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return bool(base) and base in title and "Save" not in title


def _wait_until_saved(device, file_name, attempts=5):
    for _ in range(attempts):
        if _title_shows_saved(device, file_name):
            return True
        if _overwrite_prompt_visible(device):
            raise RuntimeError(
                "Overwrite prompt appeared for '%s'; a file with this name "
                "must not exist yet" % file_name
            )
        device.settle(1.0)
    return False


def _save_dialog_open(device):
    if "| save" in (device.active_window() or "").lower():
        return True
    if (
        _safe_find(device, name="Cancel", role="push-button") is not None
        and _safe_find(device, name="Save", role="push-button") is not None
    ):
        return True
    return False


def _close_save_dialog(device, tries=4):
    for _ in range(tries):
        if not _save_dialog_open(device):
            return True
        device.press("esc")
        device.settle(1.0)
    return not _save_dialog_open(device)


def _open_save_dialog(device):
    # Cancel any stale/partially-typed dialog first so we start from a
    # fresh dialog whose Name entry has focus with its content selected.
    _close_save_dialog(device)
    for _attempt in range(2):
        for keys in (("ctrl", "shift", "s"), ("ctrl", "s")):
            device.hotkey(*keys)
            for _ in range(3):
                device.settle(1.0)
                if _save_dialog_open(device):
                    return
    raise RuntimeError(
        "Save dialog did not open (active window: %r)" % (device.active_window(),)
    )


def _save_state(device, file_name):
    if _title_shows_saved(device, file_name):
        return "saved"
    if _overwrite_prompt_visible(device):
        raise RuntimeError(
            "Overwrite prompt appeared for '%s'; a file with this name "
            "must not exist yet" % file_name
        )
    return "open" if _save_dialog_open(device) else "gone"


def _find_name_entry(device):
    for criteria in (
        {"role": "text", "editable": True, "name": "Name"},
        {"role": "text", "editable": True, "name": "File name"},
        {"role": "text", "editable": True, "name": "File Name"},
        {"role": "text", "editable": True, "contains": "Name"},
        {"role": "text", "editable": True, "contains": "name"},
        {"role": "entry", "editable": True},
    ):
        idx = _safe_find(device, **criteria)
        if idx is not None:
            return idx
    # Last resort: scan for editable text widgets, skipping anything that
    # looks like the file chooser's search box (which hijacks typed text).
    try:
        els = device.elements()
    except Exception:
        return None
    fallback = None
    for el in els:
        try:
            if not el.get("editable"):
                continue
            role = str(el.get("role") or "")
            if not any(tok in role for tok in ("text", "entry", "combo", "input")):
                continue
            blob = " ".join(
                str(el.get(key) or "") for key in ("name", "text", "description")
            ).lower()
            if "search" in blob:
                continue
            fallback = el.get("index")
        except Exception:
            continue
    return fallback


def _save_typed_path(device, file_name):
    """Primary strategy.

    A freshly opened Save dialog focuses its Name entry with the current
    content selected: select-all + type at the caret puts the absolute
    target path into the real Name field (no clicking, so the file
    chooser's search box can never steal the text). Enter confirms.
    """
    full_path = _DESKTOP_DIR + "/" + file_name
    device.hotkey("ctrl", "a")
    device.settle(0.2)
    device.type_at_caret(full_path)
    device.settle(0.5)
    for _ in range(2):  # first Enter may only confirm an autocomplete popup
        device.press("enter")
        device.settle(2.0)
        state = _save_state(device, file_name)
        if state == "saved":
            return True
        if state == "gone":
            # Dialog closed but title may lag behind.
            if _wait_until_saved(device, file_name):
                return True
            return False
    return False


def _save_via_widgets(device, file_name, use_full_path):
    """Fallback: click the real Name entry, fill it, set folder, Save."""
    _open_save_dialog(device)
    idx = _find_name_entry(device)
    if idx is None:
        _close_save_dialog(device)
        return False
    device.click(idx)
    device.settle(0.5)
    device.hotkey("ctrl", "a")
    device.settle(0.2)
    device.input_text(
        (_DESKTOP_DIR + "/" + file_name) if use_full_path else file_name,
        index=idx,
    )
    device.settle(0.5)
    if not use_full_path:
        # Only accept the plain-name variant if we can really switch the
        # dialog's folder to Desktop, otherwise the save location is wrong.
        desktop = (
            _safe_find(device, name="Desktop", role="table-cell")
            or _safe_find(device, name="Desktop", role="list-item")
            or _safe_find(device, name="Desktop", role="label")
            or _safe_find(device, name="Desktop")
        )
        if desktop is None:
            _close_save_dialog(device)
            return False
        device.click(desktop)
        device.settle(1.0)
    save_btn = _safe_find(device, name="Save", role="push-button")
    if save_btn is not None:
        device.click(save_btn)
    else:
        device.press("enter")
    device.settle(2.0)
    if _wait_until_saved(device, file_name):
        return True
    if _save_dialog_open(device):
        # A second Enter in case the first only confirmed an autocomplete.
        device.press("enter")
        device.settle(2.0)
        if _wait_until_saved(device, file_name):
            return True
    _close_save_dialog(device)
    return False


def _save_spreadsheet(device, binding):
    file_name = str(binding["file_name"])

    if _wait_until_saved(device, file_name, attempts=1):
        return

    _open_save_dialog(device)

    if _save_typed_path(device, file_name):
        return
    if _save_via_widgets(device, file_name, use_full_path=True):
        return
    if _save_via_widgets(device, file_name, use_full_path=False):
        return

    _close_save_dialog(device)
    raise RuntimeError(
        "Save of '%s' did not complete (active window: %r)"
        % (file_name, device.active_window())
    )


def program(device, binding: dict) -> bool:
    _ensure_calc_sheet(device)

    cells = (
        ("A1", binding["header_a"]),
        ("B1", binding["header_b"]),
        ("A2", binding["val_a2"]),
        ("B2", binding["val_b2"]),
        ("A3", binding["val_a3"]),
        ("B3", binding["val_b3"]),
    )
    for cell_name, value in cells:
        _fill_cell(device, cell_name, value)

    # Guarantee the last typed cell is committed before saving.
    device.press("enter")
    device.settle(0.5)

    _save_spreadsheet(device, binding)
    return True
