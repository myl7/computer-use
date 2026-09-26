import time

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


def _active_title(device):
    try:
        return device.active_window() or ""
    except Exception:
        return ""


def _settle(device, seconds):
    try:
        device.settle(seconds)
    except Exception:
        time.sleep(seconds)


def _session_alive(device):
    """One cheap probe: the guest counts as responsive as soon as a query
    succeeds and yields any element or window title."""
    try:
        if device.elements():
            return True
    except Exception:
        pass
    try:
        if device.active_window():
            return True
    except Exception:
        pass
    return False


def _nudge_open_calc(device):
    """Issue a real action instead of only polling: launching Calc on a
    booting session is queued and fires as soon as the guest is back."""
    try:
        device.open_app("LibreOffice Calc")
        return True
    except Exception:
        return False


def _try_restart_session(device):
    """Last resort for a guest that never answered anything (the hard
    replay-timeout case): ask the harness to restart the desktop session so
    the flow can start against a responsive guest.  Unsupported action types
    raise and are simply skipped."""
    for action in (
        {"action_type": "restart"},
        {"action_type": "reboot"},
        {"action_type": "reset"},
    ):
        try:
            device.execute(action)
            return True
        except Exception:
            continue
    return False


def _wait_for_desktop(device, budget_seconds=180.0):
    """The replay may start against a desktop session that is still booting
    or completely hung (empty screen, queries fail, actions never reach the
    guest).  Probe cheaply, nudge the session with a real launch action
    instead of only passively polling, and restart the desktop if it stays
    dead -- then keep waiting so the task can proceed once the guest is up.
    The whole wait stays inside a bounded budget so the run keeps time for
    the actual task instead of burning everything in startup polling."""
    deadline = time.time() + budget_seconds
    probe = 0
    restarts_done = 0
    while time.time() < deadline:
        if _session_alive(device):
            return
        probe += 1
        if probe % 3 == 1:
            _nudge_open_calc(device)
        _settle(device, 1.0)
        elapsed = budget_seconds - (deadline - time.time())
        # Session still completely dead after a generous grace period:
        # restart it (the recovery for a hung/boot-stuck guest) and keep
        # probing while it boots back up.
        if (restarts_done == 0 and elapsed >= 45.0) or (
            restarts_done == 1 and elapsed >= 110.0
        ):
            restarts_done += 1
            _try_restart_session(device)
            _settle(device, 3.0)
    if _session_alive(device):
        return
    raise RuntimeError("Desktop session never became responsive")


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
        _settle(device, 1.0)
        idx = _find_cell(device, cell_name)
    if idx is None:
        raise LookupError(
            "Spreadsheet cell '%s' not found in LibreOffice Calc" % cell_name
        )
    device.click(idx)
    device.input_text(expected, index=idx)
    _settle(device, 0.3)

    # Verify the commit; retry while the cell text is readable and wrong.
    for _ in range(2):
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
        _settle(device, 0.3)


def _verify_cells(device, cells):
    """Re-check every filled cell and re-type the ones that visibly differ."""
    for cell_name, value in cells:
        expected = str(value)
        text = _read_cell_text(device, cell_name)
        if text is None:
            continue
        if str(text).strip() != expected:
            _fill_cell(device, cell_name, expected)


def _dismiss_recovery_dialog(device):
    """After an unclean session restart LibreOffice may offer document
    recovery; discard it so a fresh spreadsheet appears."""
    for name in ("Discard", "Discard All"):
        btn = _safe_find(device, name=name, role="push-button")
        if btn is None:
            btn = _safe_find(device, name=name)
        if btn is not None:
            try:
                device.click(btn)
                _settle(device, 1.5)
            except Exception:
                pass
            return True
    return False


def _ensure_calc_sheet(device):
    _wait_for_desktop(device)
    for attempt in range(12):
        if _find_cell(device, "A1") is not None:
            return
        _dismiss_recovery_dialog(device)
        if "Calc" in _active_title(device):
            if attempt >= 1 and attempt % 3 == 1:
                # Calc is running but shows no fresh sheet grid: open a new
                # spreadsheet (re-issued if an earlier attempt did not take).
                try:
                    device.hotkey("ctrl", "n")
                except Exception:
                    pass
                _settle(device, 2.0)
            else:
                _settle(device, 1.0)
        else:
            try:
                device.open_app("LibreOffice Calc")
                device.wait()
            except Exception:
                pass
            _settle(device, 2.0)
    raise RuntimeError("LibreOffice Calc spreadsheet with cell A1 not found")


def _find_name_field(device):
    for criteria in (
        {"role": "text", "editable": True, "name": "File name"},
        {"role": "text", "editable": True, "name": "Name"},
        {"role": "text", "editable": True, "contains": "name"},
        {"role": "entry", "editable": True},
        {"role": "text", "editable": True},
    ):
        idx = _safe_find(device, **criteria)
        if idx is not None:
            return idx
    return None


def _read_index_text(device, idx):
    try:
        for el in device.elements():
            if el.get("index") == idx:
                return el.get("text")
    except Exception:
        pass
    return None


def _save_dialog_open(device):
    title = _active_title(device)
    if "Save" in title:
        return True
    if _safe_find(device, name="Cancel", role="push-button") is not None:
        return True
    return False


def _select_desktop_place(device):
    """Click the Desktop entry in the save dialog's places list."""
    for criteria in (
        {"name": "Desktop", "role": "list-item"},
        {"name": "Desktop", "role": "table-cell"},
        {"name": "Desktop", "role": "tree-item"},
        {"name": "Desktop", "role": "page-tab"},
        {"name": "Desktop", "role": "label"},
        {"name": "Desktop"},
    ):
        idx = _safe_find(device, **criteria)
        if idx is not None:
            device.click(idx)
            _settle(device, 1.0)
            return True
    return False


def _overwrite_prompt_visible(device):
    if _safe_find(device, name="Replace", role="push-button") is not None:
        return True
    if _safe_find(device, name="Replace") is not None:
        return True
    if _safe_find(device, contains="already exists") is not None:
        return True
    return False


def _question_dialog_visible(device):
    if "Question" in _active_title(device):
        return True
    if _safe_find(device, name="Question", role="dialog") is not None:
        return True
    return False


_QUESTION_BUTTONS = (
    "Use ODF Format!",
    "Use ODF Format",
    "Keep Current Format!",
    "Keep Current Format",
    "Yes",
    "OK",
    "Save",
)


def _answer_question_dialog(device):
    """A non-overwrite Question dialog (e.g. a format notice) appeared on top
    of the save flow; confirm it so the save can finish."""
    for name in _QUESTION_BUTTONS:
        btn = _safe_find(device, name=name, role="push-button")
        if btn is not None:
            device.click(btn)
            _settle(device, 1.5)
            return
    # No labelled affirmative button exposed: activate the dialog's default
    # button (for an .ods save that is the ODF/affirmative answer).
    device.press("enter")
    _settle(device, 2.0)


def _title_shows_saved(device, file_name):
    title = _active_title(device)
    if file_name in title:
        return True
    base = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
    return bool(base) and base in title and "Save" not in title


def _wait_until_saved(device, file_name, attempts=8):
    for _ in range(attempts):
        if _title_shows_saved(device, file_name):
            return True
        if _overwrite_prompt_visible(device):
            raise RuntimeError(
                "Overwrite prompt appeared for '%s'; a file with this name "
                "must not exist yet" % file_name
            )
        if _question_dialog_visible(device):
            _answer_question_dialog(device)
            continue
        _settle(device, 1.0)
    return False


def _confirm_save(device):
    save_btn = _safe_find(device, name="Save", role="push-button")
    if save_btn is not None:
        device.click(save_btn)
    else:
        device.press("enter")
    _settle(device, 2.0)


def _save_spreadsheet(device, binding):
    file_name = str(binding["file_name"]).strip()
    if not file_name.lower().endswith(".ods"):
        file_name = file_name + ".ods"
    target_path = _DESKTOP_DIR + "/" + file_name

    for _round in range(2):
        if _overwrite_prompt_visible(device):
            raise RuntimeError(
                "Overwrite prompt appeared for '%s'; a file with this name "
                "must not exist yet" % file_name
            )

        # (Re)open the Save dialog (Ctrl+S on the unsaved document).
        dialog_open = False
        for attempt in range(4):
            if _title_shows_saved(device, file_name):
                return
            if _save_dialog_open(device):
                dialog_open = True
                break
            if attempt < 3:
                device.hotkey("ctrl", "s")
                _settle(device, 1.5)
        if not dialog_open:
            continue

        # Strategy 1: type the absolute target path into the Name field.
        # This sets both the file name (including the .ods extension) and the
        # Desktop folder in one step.
        field = _find_name_field(device)
        if field is not None:
            device.input_text(target_path, index=field)
            _settle(device, 0.5)
            current = _read_index_text(device, field)
            if current is None or target_path not in str(current):
                device.input_text(target_path, index=field)
                _settle(device, 0.5)
            _confirm_save(device)
            if _wait_until_saved(device, file_name):
                return

        # Strategy 2: the dialog is presumably still open.  Switch it to the
        # Desktop folder via the places list, then type the plain file name
        # (including the .ods extension) into the Name field.
        if _save_dialog_open(device):
            field = _find_name_field(device)
            if field is not None and _select_desktop_place(device):
                _settle(device, 0.5)
                field = _find_name_field(device)
                if field is not None:
                    current = _read_index_text(device, field)
                    if current is None or str(current).strip() != "Desktop":
                        device.input_text(file_name, index=field)
                        _settle(device, 0.5)
                        _confirm_save(device)
                        if _wait_until_saved(device, file_name):
                            return

        # Strategy 3: the dialog is still open and the Name field normally
        # keeps the keyboard focus; select its content and retype the path.
        if _save_dialog_open(device):
            device.hotkey("ctrl", "a")
            _settle(device, 0.3)
            device.type_at_caret(target_path)
            _settle(device, 0.5)
            _confirm_save(device)
            if _wait_until_saved(device, file_name):
                return

        if _title_shows_saved(device, file_name):
            return

    raise RuntimeError(
        "Save of '%s' did not complete (active window: %r)"
        % (file_name, _active_title(device))
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

    # Re-check the grid and fix any cell that did not take its value.
    _verify_cells(device, cells)

    # Guarantee the last typed cell is committed before saving.
    device.press("enter")
    _settle(device, 0.5)

    _save_spreadsheet(device, binding)
    return True
