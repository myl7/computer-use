import re
import calendar


# ---------------------------------------------------------------------------
# binding parameter description
# ---------------------------------------------------------------------------
PARAMS_SCHEMA = {
    "year": {
        "type": "int",
        "description": "Year of the event, e.g. 2023.",
    },
    "month": {
        "type": "int",
        "description": "Month of the event, 1-12.",
    },
    "day": {
        "type": "int",
        "description": "Day of the month, 1-31.",
    },
    "hour": {
        "type": "int",
        "description": "Start hour in 24-hour clock (0-23).",
    },
    "duration_mins": {
        "type": "int",
        "description": "Event duration in minutes; used to derive the end time.",
    },
    "event_title": {
        "type": "string",
        "description": "Title of the calendar event.",
    },
    "event_description": {
        "type": "string",
        "description": "Description of the calendar event.",
    },
}


# ---------------------------------------------------------------------------
# small lookup tables
# ---------------------------------------------------------------------------
_MONTHS = {}
for _i, _name in enumerate(calendar.month_name):
    if _name:
        _MONTHS[_name.lower()] = _i
for _i, _name in enumerate(calendar.month_abbr):
    if _name:
        _MONTHS.setdefault(_name.lower(), _i)
_MONTH_WORDS = sorted(_MONTHS.keys(), key=len, reverse=True)

_TIME_RE = re.compile(r'\b(\d{1,2}):(\d{2})\b')
_YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')
_DATE_NUM_RE = re.compile(r'\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b')

_OK_WORDS = {"ok", "set", "done", "save", "confirm", "yes", "create"}


# ---------------------------------------------------------------------------
# element helpers
# ---------------------------------------------------------------------------
def _elements(device):
    try:
        els = device.elements()
    except Exception:
        els = None
    return list(els) if els else []


def _text(e):
    return str(e.get("text") or "")


def _hint(e):
    return str(e.get("hint") or "")


def _desc(e):
    for k in ("description", "content_desc", "contentDescription", "content-desc"):
        v = e.get(k)
        if v:
            return str(v)
    return ""


def _blob(e):
    return " ".join([_text(e), _hint(e), _desc(e)]).lower()


def _click(device, index, settle=0.8):
    device.click(index=index)
    try:
        device.settle(settle)
    except Exception:
        try:
            device.wait()
        except Exception:
            pass


def _find(device, pred):
    for e in _elements(device):
        try:
            if pred(e):
                return e
        except Exception:
            continue
    return None


def _is_time(text):
    return _TIME_RE.search(text or "") is not None


def _is_date(text):
    text = text or ""
    if not text:
        return False
    if _TIME_RE.search(text) and not _YEAR_RE.search(text):
        return False
    if _YEAR_RE.search(text):
        return True
    low = text.lower()
    for w in _MONTH_WORDS:
        if re.search(r'\b' + re.escape(w) + r'\b', low):
            return True
    if _DATE_NUM_RE.search(text):
        return True
    return False


# ---------------------------------------------------------------------------
# step 1 : create a fresh event
# ---------------------------------------------------------------------------
def _open_new_event(device):
    # already on the event editor?
    if _find(device, lambda e: e.get("editable")):
        return

    fab = None
    # 1) explicit "new event" content description on a clickable element
    for e in _elements(device):
        if not e.get("clickable"):
            continue
        d = _desc(e).lower()
        if ("new event" in d or "create event" in d or "add event" in d
                or "new_event" in d or "add_event" in d):
            fab = e
            break
    # 2) a "+" glyph or a short add label
    if fab is None:
        for e in _elements(device):
            if not e.get("clickable"):
                continue
            t = _text(e).strip()
            d = _desc(e).lower()
            if t in ("+", "\uff0b") or d in ("add", "new", "create",
                                             "new event", "add event"):
                fab = e
                break
    # 3) fall back to the last clickable element (the FAB is usually last)
    if fab is None:
        clicks = [e for e in _elements(device) if e.get("clickable")]
        if clicks:
            fab = clicks[-1]
    if fab is None:
        raise RuntimeError("Could not find the create-event button")
    _click(device, fab["index"])

    if _find(device, lambda e: e.get("editable")):
        return

    # A small menu (New event / New task) may have popped up.
    opt = None
    for e in _elements(device):
        t = _text(e).lower()
        if "task" in t:
            continue
        if "event" in t:
            opt = e
            break
    if opt is None:
        for e in _elements(device):
            d = _desc(e).lower()
            if "task" in d:
                continue
            if "event" in d:
                opt = e
                break
    if opt is not None:
        _click(device, opt["index"])


# ---------------------------------------------------------------------------
# step 2 : title and description (first two editable fields, in order)
# ---------------------------------------------------------------------------
def _fill_title_description(device, title, description):
    edits = [e for e in _elements(device) if e.get("editable")]
    if len(edits) < 2:
        raise RuntimeError("Expected title and description text fields")
    title_el, desc_el = edits[0], edits[1]

    _click(device, title_el["index"])
    device.input_text(title, index=title_el["index"])
    try:
        device.settle(0.4)
    except Exception:
        pass

    _click(device, desc_el["index"])
    device.input_text(description, index=desc_el["index"])
    try:
        device.settle(0.4)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# step 3 : date picker
# ---------------------------------------------------------------------------
def _open_date_row(device):
    cand = _find(device, lambda e: (not e.get("editable"))
                 and e.get("clickable")
                 and _is_date(_text(e)))
    if cand is None:
        cand = _find(device, lambda e: (not e.get("editable"))
                     and _is_date(_text(e)))
    if cand is None:
        cand = _find(device, lambda e: "date" in _desc(e).lower()
                     and e.get("clickable"))
    if cand is None:
        raise RuntimeError("Could not find the start-date row")
    _click(device, cand["index"])


def _read_shown_month(device):
    month = None
    year = None
    for e in _elements(device):
        for src in (_text(e), _desc(e)):
            s = (src or "").strip()
            if not s:
                continue
            m = re.search(r'([A-Za-z]{3,9})\.?\s*,?\s*(\d{4})', s)
            if m and m.group(1).lower() in _MONTHS:
                month = _MONTHS[m.group(1).lower()]
                year = int(m.group(2))
                continue
            m = re.fullmatch(r'([A-Za-z]{3,9})\.?', s)
            if m and m.group(1).lower() in _MONTHS:
                month = _MONTHS[m.group(1).lower()]
                continue
            m = re.fullmatch(r'(\d{4})', s)
            if m and 1900 <= int(m.group(1)) <= 2100:
                year = int(m.group(1))
    if month is None:
        return None
    return (year, month)


def _nav_month(device, forward):
    keys = ("next",) if forward else ("previous", "prev", "back")
    for e in _elements(device):
        d = _desc(e).lower()
        if any(k in d for k in keys) and ("month" in d or "year" in d or d.strip() in keys):
            _click(device, e["index"])
            return True
    for e in _elements(device):
        d = _desc(e).lower()
        if any(k in d for k in keys):
            _click(device, e["index"])
            return True
    for e in _elements(device):
        t = _text(e).strip()
        if forward and t in (">", "\u203a", "\u25b6", ">>", "\u2192", "\u279c"):
            _click(device, e["index"])
            return True
        if (not forward) and t in ("<", "\u2039", "\u25c0", "<<", "\u2190", "\u2798"):
            _click(device, e["index"])
            return True
    return False


def _click_day(device, day):
    ds = str(day)
    for e in _elements(device):
        if _text(e).strip() == ds and e.get("clickable"):
            _click(device, e["index"])
            return True
    for e in _elements(device):
        t = _text(e).strip()
        if t == ds or t.startswith(ds + " "):
            _click(device, e["index"])
            return True
    return False


def _confirm_with_ok(device):
    for e in _elements(device):
        t = _text(e).strip().lower()
        d = _desc(e).strip().lower()
        if t in _OK_WORDS or d in _OK_WORDS:
            _click(device, e["index"])
            return True
        if t.startswith("ok") or d.startswith("ok"):
            _click(device, e["index"])
            return True
    return False


def _set_date(device, year, month, day):
    _open_date_row(device)

    target_val = year * 12 + (month - 1)
    for _ in range(36):
        shown = _read_shown_month(device)
        if shown is None:
            break
        shown_year, shown_month = shown
        if shown_year is None:
            shown_year = year
        shown_val = shown_year * 12 + (shown_month - 1)
        if shown_val == target_val:
            break
        if not _nav_month(device, shown_val < target_val):
            break

    _click_day(device, day)
    _confirm_with_ok(device)


# ---------------------------------------------------------------------------
# step 4 : time pickers
# ---------------------------------------------------------------------------
def _time_rows(device):
    rows = []
    seen = set()
    for e in _elements(device):
        t = _text(e)
        h = _hint(e)
        if _is_time(t) or _is_time(h):
            key = e.get("index")
            if key not in seen:
                seen.add(key)
                rows.append(e)
    return rows


def _open_time_row(device, which):
    want = "start" if which == 0 else "end"
    cand = _find(device, lambda e: (want in _blob(e)) and ("time" in _blob(e))
                 and e.get("clickable"))
    if cand is None:
        cand = _find(device, lambda e: (want in _blob(e)) and ("time" in _blob(e)))
    if cand is not None:
        _click(device, cand["index"])
        return
    rows = _time_rows(device)
    if len(rows) > which:
        _click(device, rows[which]["index"])
        return
    if rows:
        _click(device, rows[-1]["index"])
        return
    raise RuntimeError("Could not find the %s-time row" % want)


def _click_number(device, n, first=True):
    wanted = (str(n), "%02d" % n)
    cands = []
    for e in _elements(device):
        t = _text(e).strip()
        if t in wanted and e.get("clickable"):
            cands.append(e)
    if not cands:
        for e in _elements(device):
            t = _text(e).strip()
            if t in wanted:
                cands.append(e)
    if not cands:
        return False
    chosen = cands[0] if first else cands[-1]
    _click(device, chosen["index"])
    return True


def _fill_time_dialog(device, hh, mm):
    # (a) directly editable hour / minute fields
    edits = [e for e in _elements(device) if e.get("editable")]
    if len(edits) >= 2:
        _click(device, edits[0]["index"])
        device.input_text("%02d" % hh, index=edits[0]["index"])
        _click(device, edits[1]["index"])
        device.input_text("%02d" % mm, index=edits[1]["index"])
        try:
            device.settle(0.4)
        except Exception:
            pass
        return

    # (b) switch to keyboard/text input mode, then type
    toggle = _find(device, lambda e: e.get("clickable") and (
        "keyboard" in _desc(e).lower()
        or "text input" in _desc(e).lower()
        or "input" in _desc(e).lower()))
    if toggle is not None:
        _click(device, toggle["index"])
        edits = [e for e in _elements(device) if e.get("editable")]
        if len(edits) >= 2:
            _click(device, edits[0]["index"])
            device.input_text("%02d" % hh, index=edits[0]["index"])
            _click(device, edits[1]["index"])
            device.input_text("%02d" % mm, index=edits[1]["index"])
            try:
                device.settle(0.4)
            except Exception:
                pass
            return

    # (c) pick the numbers off the clock / grid
    _click_number(device, hh, first=True)
    _click_number(device, mm, first=False)


def _set_time(device, which, hh, mm):
    _open_time_row(device, which)
    _fill_time_dialog(device, hh % 24, mm % 60)
    _confirm_with_ok(device)


# ---------------------------------------------------------------------------
# step 5 : save
# ---------------------------------------------------------------------------
def _save(device):
    for e in _elements(device):
        d = _desc(e).lower()
        t = _text(e).lower()
        if d in ("save", "done", "confirm") or t in ("save", "done", "confirm"):
            _click(device, e["index"], settle=1.5)
            return True
    for e in _elements(device):
        d = _desc(e).lower()
        t = _text(e).lower()
        if "save" in d or "save" in t or d == "check":
            _click(device, e["index"], settle=1.5)
            return True
    # last resort: leave the screen (the editor persists on exit)
    try:
        device.navigate_back()
        device.settle(1.5)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------
def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration = int(binding["duration_mins"])
    title = str(binding["event_title"])
    description = str(binding["event_description"])

    total_end = ((hour % 24) * 60 + duration) % (24 * 60)
    end_hour = total_end // 60
    end_min = total_end % 60

    # make sure we start from a clean home screen
    try:
        device.navigate_home()
        device.settle(1)
    except Exception:
        pass

    device.open_app("Simple Calendar Pro")
    try:
        device.settle(2)
    except Exception:
        device.wait()

    # 1. reach the new-event editor
    _open_new_event(device)
    if not _find(device, lambda e: e.get("editable")):
        raise RuntimeError("Did not reach the event editor")

    # 2. title + description
    _fill_title_description(device, title, description)

    # 3. start date
    _set_date(device, year, month, day)

    # 4. start time, then end time (start + duration)
    _set_time(device, 0, hour, 0)
    _set_time(device, 1, end_hour, end_min)

    # 5. save
    _save(device)

    return True
