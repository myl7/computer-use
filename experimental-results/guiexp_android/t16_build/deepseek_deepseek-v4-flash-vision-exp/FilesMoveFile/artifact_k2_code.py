import time


PARAMS_SCHEMA = {
    "file_name": {
        "type": "string",
        "required": True,
        "description": "Name (with extension) of the file to move, e.g. 'note.mp3'.",
    },
    "source_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level folder of the device storage the file currently lives in, e.g. 'Download'.",
    },
    "destination_folder": {
        "type": "string",
        "required": True,
        "description": "Top-level folder of the device storage the file must end up in, e.g. 'DCIM'.",
    },
}


# The storage area name is constant for this task family (never a binding value).
STORAGE_ROOT_HINTS = ("sdk_gphone_x86_64", "sdk_gphone")


# ---------------------------------------------------------------------------
# small helpers around the (somewhat loosely typed) device API
# ---------------------------------------------------------------------------
def _safe_find(device, **kw):
    try:
        idx = device.find(**kw)
    except Exception:
        return None
    if idx is None:
        return None
    if isinstance(idx, dict):
        return idx.get("index", idx)
    return idx


def _click_index(device, index, settle=1.2):
    device.click(index=index)
    if settle:
        device.settle(settle)


def _long_press_index(device, index, settle=1.2):
    long_press = getattr(device, "long_press", None)
    if callable(long_press):
        try:
            long_press(index=index)
            if settle:
                device.settle(settle)
            return
        except TypeError:
            pass
        except Exception:
            pass
    # Fall back to the raw android_world action.
    device.execute({"action_type": "long_press", "index": index})
    if settle:
        device.settle(settle)


def _click_text(device, text, settle=1.2, prefer_clickable=True):
    idx = None
    if prefer_clickable:
        idx = _safe_find(device, text=text, clickable=True)
    if idx is None:
        idx = _safe_find(device, text=text)
    if idx is None:
        return False
    _click_index(device, idx, settle)
    return True


# ---------------------------------------------------------------------------
# navigation inside the Files app (DocumentsUI)
# ---------------------------------------------------------------------------
def _open_drawer(device):
    """Tap the hamburger / 'Show roots' button to slide the roots drawer out."""
    for desc in ("Show roots", "Show root", "Open navigation drawer", "Open drawer"):
        idx = _safe_find(device, description=desc)
        if idx is not None:
            _click_index(device, idx, 1.2)
            return True
    for txt in ("Show roots", "Show root"):
        idx = _safe_find(device, text=txt)
        if idx is not None:
            _click_index(device, idx, 1.2)
            return True
    return False


def _storage_root_index(device):
    for hint in STORAGE_ROOT_HINTS:
        idx = _safe_find(device, text=hint)
        if idx is not None:
            return idx
    return _safe_find(device, contains="sdk_gphone")


def _folder_index(device, folder):
    idx = _safe_find(device, text=folder, clickable=True)
    if idx is None:
        idx = _safe_find(device, text=folder)
    return idx


def _navigate_to_folder(device, folder):
    """Open one of the top-level folders of the device storage area."""
    for _ in range(3):
        _open_drawer(device)

        # The drawer usually lists the top-level folders directly.
        idx = _folder_index(device, folder)
        if idx is not None:
            _click_index(device, idx, 1.5)
            return True

        # Otherwise the storage root has to be opened first.
        root = _storage_root_index(device)
        if root is not None:
            _click_index(device, root, 1.5)
            idx = _folder_index(device, folder)
            if idx is not None:
                _click_index(device, idx, 1.5)
                return True
            # maybe the folder is further down the freshly shown list
            for _ in range(3):
                device.scroll("down")
                device.settle(0.8)
                idx = _folder_index(device, folder)
                if idx is not None:
                    _click_index(device, idx, 1.5)
                    return True

    raise RuntimeError("could not open folder %r" % (folder,))


def _select_file(device, file_name):
    """Long-press the row whose exact name is ``file_name``."""
    for _ in range(6):
        idx = _safe_find(device, text=file_name)
        if idx is not None:
            _long_press_index(device, idx, 1.5)
            return True
        device.scroll("down")
        device.settle(0.8)
    # try scrolling back up in case we overshot
    for _ in range(6):
        device.scroll("up")
        device.settle(0.8)
        idx = _safe_find(device, text=file_name)
        if idx is not None:
            _long_press_index(device, idx, 1.5)
            return True
    raise RuntimeError("file %r not found in the source folder" % (file_name,))


# ---------------------------------------------------------------------------
# selection-bar / toolbar menus
# ---------------------------------------------------------------------------
def _open_overflow(device):
    for desc in ("More options", "More options.", "Overflow menu"):
        idx = _safe_find(device, description=desc)
        if idx is not None:
            _click_index(device, idx, 1.2)
            return True
    for txt in ("More options",):
        idx = _safe_find(device, text=txt)
        if idx is not None:
            _click_index(device, idx, 1.2)
            return True
    return False


def _menu_click(device, candidates):
    for cand in candidates:
        idx = _safe_find(device, text=cand, clickable=True)
        if idx is None:
            idx = _safe_find(device, text=cand)
        if idx is None:
            idx = _safe_find(device, contains=cand)
        if idx is not None:
            _click_index(device, idx, 1.5)
            return True
    return False


def _choose_move_action(device):
    """Return 'cut' or 'picker' depending on what the overflow offers."""
    if _menu_click(device, ("Cut",)):
        return "cut"
    if _menu_click(device, ("Move to\u2026", "Move to...", "Move to", "Move here")):
        return "picker"
    return None


def _paste(device):
    # Some builds expose Paste directly, otherwise it lives in the overflow.
    idx = _safe_find(device, text="Paste", clickable=True)
    if idx is None:
        idx = _safe_find(device, text="Paste")
    if idx is not None:
        _click_index(device, idx, 2.0)
        return True
    if _open_overflow(device):
        if _menu_click(device, ("Paste",)):
            return True
    return False


def _picker_confirm(device):
    for txt in ("Move", "Move here", "MOVE", "Select", "Select here", "Done", "OK", "Save"):
        idx = _safe_find(device, text=txt, clickable=True)
        if idx is not None:
            _click_index(device, idx, 2.0)
            return True
    for desc in ("Move here", "Move", "Done", "Select", "Select here"):
        idx = _safe_find(device, description=desc)
        if idx is not None:
            _click_index(device, idx, 2.0)
            return True
    return False


def _dismiss_dialogs(device):
    for _ in range(3):
        clicked = False
        for txt in ("Allow", "ALLOW", "While using the app", "Only this time", "Got it"):
            idx = _safe_find(device, text=txt, clickable=True)
            if idx is not None:
                _click_index(device, idx, 1.2)
                clicked = True
                break
        if not clicked:
            break


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def program(device, binding: dict) -> bool:
    file_name = binding["file_name"]
    source = binding["source_folder"]
    destination = binding["destination_folder"]

    # 1. bring the Files app to the foreground
    device.open_app("Files")
    device.settle(2.0)
    _dismiss_dialogs(device)

    # 2. open the source folder inside the device storage area
    _navigate_to_folder(device, source)

    # 3. long-press the target row (matched by its exact name)
    _select_file(device, file_name)

    # 4. selection bar overflow -> Cut (or, if absent, "Move to ...")
    if not _open_overflow(device):
        raise RuntimeError("could not open the selection-bar overflow menu")

    mode = _choose_move_action(device)
    if mode is None:
        raise RuntimeError("neither 'Cut' nor 'Move to' was offered by the menu")

    if mode == "cut":
        # 5a. go to the destination folder and paste there (cut = moved)
        _navigate_to_folder(device, destination)
        if not _paste(device):
            raise RuntimeError("could not paste the cut file")
        device.settle(2.0)
        return True

    # 5b. "Move to ..." picker flow: choose the destination folder, confirm
    _navigate_to_folder(device, destination)
    _picker_confirm(device)
    device.settle(2.0)
    return True
