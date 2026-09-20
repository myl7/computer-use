import calendar
import re
from datetime import datetime, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "integer", "description": "Event year"},
    "month": {"type": "integer", "description": "Event month, 1-12"},
    "day": {"type": "integer", "description": "Event day of month"},
    "hour": {"type": "integer", "description": "Start hour, 0-23"},
    "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
    "event_title": {"type": "string", "description": "Event title"},
    "event_description": {"type": "string", "description": "Event description"},
}


def _month_name(month):
    return calendar.month_name[int(month)]


def _find_first(device, candidates):
    for kwargs in candidates:
        clean = {k: v for k, v in kwargs.items() if v is not None}
        if not clean:
            continue
        try:
            idx = device.find(**clean)
            if idx is not None:
                return idx
        except Exception:
            # Some device.find implementations may not accept all criteria together.
            for key in ("description", "text", "hint", "contains"):
                if key not in clean:
                    continue
                sub = {key: clean[key]}
                for extra in ("clickable", "editable"):
                    if extra in clean:
                        sub[extra] = clean[extra]
                try:
                    idx = device.find(**sub)
                    if idx is not None:
                        return idx
                except Exception:
                    pass
    return None


def _scan_first(device, predicate):
    for pos, el in enumerate(device.elements()):
        try:
            if predicate(el):
                return el.get("index", pos)
        except Exception:
            pass
    return None


def _get_current_month_year(device, default_year=None):
    month_entries = [(calendar.month_name[i], i) for i in range(1, 13)]
    month_entries += [(calendar.month_abbr[i], i) for i in range(1, 13)]

    for el in device.elements():
        for key in ("text", "description", "hint"):
            source = str(el.get(key) or "")
            for name, num in month_entries:
                if name in source:
                    m = re.search(r"\b(?:19|20)\d{2}\b", source)
                    if m:
                        return int(m.group(0)), num
                    if default_year is not None:
                        return int(default_year), num
    return None


def _ensure_month(device, year, month):
    target = (int(year), int(month))
    for _ in range(36):
        current = _get_current_month_year(device, default_year=year)
        if current is None or current == target:
            return

        if current < target:
            idx = _find_first(device, [
                {"description": "Next month", "clickable": True},
                {"description": "Go to next month", "clickable": True},
                {"text": "›", "clickable": True},
                {"text": ">", "clickable": True},
                {"text": "Next", "clickable": True},
            ])
        else:
            idx = _find_first(device, [
                {"description": "Previous month", "clickable": True},
                {"description": "Go to previous month", "clickable": True},
                {"text": "‹", "clickable": True},
                {"text": "<", "clickable": True},
                {"text": "Previous", "clickable": True},
            ])

        if idx is None:
            return
        device.click(index=idx)
        device.settle(0.5)


def _find_day(device, day):
    day_str = str(int(day))
    day_pad = f"{int(day):02d}"
    candidates = [
        {"text": day_str, "clickable": True},
        {"text": day_pad, "clickable": True},
        {"description": day_str, "clickable": True},
        {"description": day_pad, "clickable": True},
        {"hint": day_str, "clickable": True},
        {"hint": day_pad, "clickable": True},
        {"text": day_str},
        {"text": day_pad},
    ]
    idx = _find_first(device, candidates)
    if idx is None:
        wanted = {day_str, day_pad}

        def pred(el):
            if not el.get("clickable"):
                return False
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            return text in wanted or desc in wanted or hint in wanted

        idx = _scan_first(device, pred)
    return idx


def _click_day(device, year, month, day):
    current = _get_current_month_year(device, default_year=year)
    if current is not None and current != (int(year), int(month)):
        _ensure_month(device, year, month)

    idx = _find_day(device, day)
    if idx is None:
        _ensure_month(device, year, month)
        idx = _find_day(device, day)

    if idx is None:
        month_name = _month_name(month)
        labels = [
            f"{month_name} {day}",
            f"{day} {month_name}",
            f"{month_name} {day}, {year}",
            f"{year}-{int(month):02d}-{int(day):02d}",
        ]
        candidates = []
        for label in labels:
            candidates.append({"description": label, "clickable": True})
            candidates.append({"text": label, "clickable": True})
        idx = _find_first(device, candidates)

    if idx is None:
        raise RuntimeError(f"Could not find calendar day {day}")

    device.click(index=idx)
    device.settle(1.0)


def _click_new_event(device):
    candidates = [
        {"description": "New Event", "clickable": True},
        {"description": "New event", "clickable": True},
        {"text": "New Event", "clickable": True},
        {"text": "New event", "clickable": True},
        {"hint": "New Event", "clickable": True},
        {"contains": "New Event", "clickable": True},
        {"contains": "New event", "clickable": True},
    ]
    idx = _find_first(device, candidates)

    if idx is None:
        def pred(el):
            if not el.get("clickable"):
                return False
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            hint = str(el.get("hint") or "").lower()
            return "new event" in desc or "new event" in text or "new event" in hint

        idx = _scan_first(device, pred)

    if idx is None:
        raise RuntimeError("Could not find the New Event button")

    device.click(index=idx)
    device.settle(1.0)


def _find_event_option(device):
    candidates = [
        {"text": "Event", "clickable": True},
        {"description": "Event", "clickable": True},
        {"hint": "Event", "clickable": True},
        {"contains": "Event", "clickable": True},
    ]
    idx = _find_first(device, candidates)
    if idx is None:
        def pred(el):
            if not el.get("clickable"):
                return False
            return str(el.get("text") or "").strip().lower() == "event"

        idx = _scan_first(device, pred)
    return idx


def _input_field(device, hint, text):
    candidates = [
        {"hint": hint, "editable": True},
        {"text": hint, "editable": True},
        {"description": hint, "editable": True},
        {"hint": hint},
        {"text": hint},
        {"description": hint},
    ]
    idx = _find_first(device, candidates)

    if idx is None:
        def pred(el):
            if not el.get("editable"):
                return False
            h = str(el.get("hint") or "").strip().lower()
            t = str(el.get("text") or "").strip().lower()
            d = str(el.get("description") or "").strip().lower()
            return h == hint.lower() or t == hint.lower() or d == hint.lower()

        idx = _scan_first(device, pred)

    if idx is None:
        raise RuntimeError(f"Could not find editable field for {hint!r}")

    device.input_text(text=text, index=idx)
    device.settle(0.5)


def _delete_reminder_if_present(device):
    def pred(el):
        if not el.get("clickable"):
            return False
        desc = str(el.get("description") or "").strip()
        cls = str(el.get("class_name") or "")
        return desc == "Delete" and "FrameLayout" in cls

    idx = _scan_first(device, pred)
    if idx is not None:
        device.click(index=idx)
        device.settle(0.5)


def _is_time_text(text):
    text = str(text or "").strip()
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", text))


def _time_items(device):
    items = []
    for pos, el in enumerate(device.elements()):
        cls = str(el.get("class_name") or "")
        text = str(el.get("text") or "").strip()
        if (not cls or "TextView" in cls) and _is_time_text(text):
            items.append((el.get("index", pos), text, bool(el.get("clickable"))))

    items.sort(key=lambda x: x[0])
    clickable = [it for it in items if it[2]]
    return clickable if clickable else items


def _get_time_items(device, min_count=1, max_scroll=3):
    best = []

    for _ in range(2):
        device.scroll("up")

    for _ in range(max_scroll + 1):
        items = _time_items(device)
        if len(items) >= min_count:
            return items
        if len(items) > len(best):
            best = items
        device.scroll("down")

    for _ in range(max_scroll + 1):
        device.scroll("up")
        items = _time_items(device)
        if len(items) >= min_count:
            return items
        if len(items) > len(best):
            best = items

    return best


def _open_time_field(device, which, start_text=None):
    label = "Start time" if which == "start" else "End time"
    idx = _find_first(device, [
        {"text": label, "clickable": True},
        {"description": label, "clickable": True},
        {"hint": label, "clickable": True},
    ])

    if idx is not None:
        device.click(index=idx)
        device.settle(1.0)
        return

    min_count = 1 if which == "start" else 2
    items = _get_time_items(device, min_count=min_count)
    if not items:
        raise RuntimeError(f"Could not find {which} time field")

    if which == "start":
        target = items[0][0]
    else:
        target = None
        if start_text:
            start_text = str(start_text).strip()
            for i, it in enumerate(items):
                if it[1] == start_text and i + 1 < len(items):
                    target = items[i + 1][0]
                    break

        if target is None:
            target = items[1][0] if len(items) >= 2 else items[0][0]

    device.click(index=target)
    device.settle(1.0)


def _confirm_picker(device):
    candidates = [
        {"text": "OK", "clickable": True},
        {"text": "Ok", "clickable": True},
        {"text": "Okay", "clickable": True},
        {"description": "OK", "clickable": True},
        {"description": "Ok", "clickable": True},
    ]
    idx = _find_first(device, candidates)

    if idx is None:
        def pred(el):
            if not el.get("clickable"):
                return False
            text = str(el.get("text") or "").strip().upper()
            desc = str(el.get("description") or "").strip().upper()
            return text in ("OK", "OKAY", "DONE") or desc in ("OK", "OKAY", "DONE")

        idx = _scan_first(device, pred)

    if idx is not None:
        device.click(index=idx)
        device.settle(1.0)
        return

    device.keyboard_enter()
    device.settle(1.0)


def _select_time_component(device, component, value):
    value = int(value)
    value_str = str(value)
    value_pad = f"{value:02d}"

    if component == "hour":
        suffixes = ("hours", "hour")
        plural = "hours" if value != 1 else "hour"
    else:
        suffixes = ("minutes", "minute")
        plural = "minutes" if value != 1 else "minute"

    desc_plain = f"{value} {plural}"
    desc_pad = f"{value:02d} {plural}"

    desc_candidates = [
        {"text": value_str, "description": desc_plain, "clickable": True},
        {"text": value_pad, "description": desc_plain, "clickable": True},
        {"text": value_str, "description": desc_pad, "clickable": True},
        {"text": value_pad, "description": desc_pad, "clickable": True},
        {"description": desc_plain, "clickable": True},
        {"description": desc_pad, "clickable": True},
    ]

    idx = _find_first(device, desc_candidates)
    if idx is not None:
        device.click(index=idx)
        device.settle(0.5)
        return

    def header_pred(el):
        if not el.get("clickable"):
            return False
        desc = str(el.get("description") or "").strip()
        cls = str(el.get("class_name") or "")
        text = str(el.get("text") or "").strip()
        return (
            any(desc.endswith(suf) for suf in suffixes)
            and (not cls or "TextView" in cls)
            and (text.isdigit() or bool(re.fullmatch(r"\d{1,2}", text)))
        )

    header_idx = _scan_first(device, header_pred)
    if header_idx is not None:
        device.click(index=header_idx)
        device.settle(0.5)
        idx = _find_first(device, desc_candidates)
        if idx is not None:
            device.click(index=idx)
            device.settle(0.5)
            return

    text_candidates = [
        {"text": value_str, "clickable": True},
        {"text": value_pad, "clickable": True},
    ]
    idx = _find_first(device, text_candidates)
    if idx is not None:
        device.click(index=idx)
        device.settle(0.5)
        return

    def any_pred(el):
        if not el.get("clickable"):
            return False
        desc = str(el.get("description") or "").strip()
        return any(desc.endswith(suf) for suf in suffixes)

    idx = _scan_first(device, any_pred)
    if idx is not None:
        device.click(index=idx)
        device.settle(0.5)
        return

    raise RuntimeError(f"Could not select {component} {value}")


def _set_time(device, hour, minute):
    _select_time_component(device, "hour", hour)
    _select_time_component(device, "minute", minute)
    _confirm_picker(device)


def _is_date_text(text):
    text = str(text or "").strip()
    for name in calendar.month_name[1:]:
        if name in text:
            return True
    for name in calendar.month_abbr[1:]:
        if name in text:
            return True
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        return True
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        return True
    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text):
        return True
    return False


def _date_items(device):
    items = []
    for pos, el in enumerate(device.elements()):
        cls = str(el.get("class_name") or "")
        text = str(el.get("text") or "").strip()
        if (not cls or "TextView" in cls) and _is_date_text(text):
            items.append((el.get("index", pos), text, bool(el.get("clickable"))))

    items.sort(key=lambda x: x[0])
    clickable = [it for it in items if it[2]]
    return clickable if clickable else items


def _get_date_items(device, min_count=1, max_scroll=3):
    best = []

    for _ in range(2):
        device.scroll("up")

    for _ in range(max_scroll + 1):
        items = _date_items(device)
        if len(items) >= min_count:
            return items
        if len(items) > len(best):
            best = items
        device.scroll("down")

    for _ in range(max_scroll + 1):
        device.scroll("up")
        items = _date_items(device)
        if len(items) >= min_count:
            return items
        if len(items) > len(best):
            best = items

    return best


def _open_date_field(device, which):
    label = "Start date" if which == "start" else "End date"
    idx = _find_first(device, [
        {"text": label, "clickable": True},
        {"description": label, "clickable": True},
        {"hint": label, "clickable": True},
    ])

    if idx is not None:
        device.click(index=idx)
        device.settle(1.0)
        return True

    min_count = 1 if which == "start" else 2
    items = _get_date_items(device, min_count=min_count)

    if not items:
        return False

    if which == "start":
        target = items[0][0]
    else:
        if len(items) >= 2:
            target = items[1][0]
        else:
            return False

    device.click(index=target)
    device.settle(1.0)
    return True


def _set_date_in_picker(device, year, month, day):
    target = (int(year), int(month))

    for _ in range(36):
        current = _get_current_month_year(device, default_year=year)
        if current is None or current == target:
            break

        if current[0] != year:
            cur_month_name = calendar.month_name[current[1]]
            header = _find_first(device, [
                {"text": f"{cur_month_name} {current[0]}", "clickable": True},
                {"description": f"{cur_month_name} {current[0]}", "clickable": True},
                {"text": str(current[0]), "clickable": True},
                {"contains": str(current[0]), "clickable": True},
            ])

            if header is None:
                def pred(el):
                    if not el.get("clickable"):
                        return False
                    text = str(el.get("text") or "")
                    return bool(re.search(r"\b(?:19|20)\d{2}\b", text))

                header = _scan_first(device, pred)

            if header is not None:
                device.click(index=header)
                device.settle(0.5)
                y_idx = _find_first(device, [
                    {"text": str(year), "clickable": True},
                    {"description": str(year), "clickable": True},
                ])
                if y_idx is not None:
                    device.click(index=y_idx)
                    device.settle(0.5)
                    continue

            break

        if current < target:
            idx = _find_first(device, [
                {"description": "Next month", "clickable": True},
                {"description": "Go to next month", "clickable": True},
                {"text": "›", "clickable": True},
                {"text": ">", "clickable": True},
                {"text": "Next", "clickable": True},
            ])
        else:
            idx = _find_first(device, [
                {"description": "Previous month", "clickable": True},
                {"description": "Go to previous month", "clickable": True},
                {"text": "‹", "clickable": True},
                {"text": "<", "clickable": True},
                {"text": "Previous", "clickable": True},
            ])

        if idx is None:
            break

        device.click(index=idx)
        device.settle(0.5)

    day_str = str(int(day))
    day_pad = f"{int(day):02d}"
    month_name = _month_name(month)

    candidates = [
        {"description": f"{month_name} {day}", "clickable": True},
        {"description": f"{day} {month_name}", "clickable": True},
        {"text": day_str, "clickable": True},
        {"text": day_pad, "clickable": True},
        {"description": day_str, "clickable": True},
        {"description": day_pad, "clickable": True},
        {"text": day_str},
        {"text": day_pad},
    ]
    idx = _find_first(device, candidates)

    if idx is None:
        wanted = {day_str, day_pad}

        def pred(el):
            if not el.get("clickable"):
                return False
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            return text in wanted or desc in wanted

        idx = _scan_first(device, pred)

    if idx is None:
        raise RuntimeError(f"Could not find date {day} in date picker")

    device.click(index=idx)
    device.settle(0.5)
    _confirm_picker(device)


def _set_end_date_if_needed(device, end_dt, start_dt):
    if end_dt.date() == start_dt.date():
        return

    if _open_date_field(device, "end"):
        _set_date_in_picker(device, end_dt.year, end_dt.month, end_dt.day)


def _save_event(device):
    candidates = [
        {"description": "Save", "clickable": True},
        {"text": "Save", "clickable": True},
        {"description": "Save event", "clickable": True},
        {"text": "Save event", "clickable": True},
        {"contains": "Save", "clickable": True},
    ]
    idx = _find_first(device, candidates)

    if idx is None:
        def pred(el):
            if not el.get("clickable"):
                return False
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            return "save" in desc or text == "save"

        idx = _scan_first(device, pred)

    if idx is None:
        raise RuntimeError("Could not find Save button")

    device.click(index=idx)
    device.settle(1.0)


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    app_name = binding.get("app_name") or "Simple Calendar Pro"

    device.open_app(app_name)
    device.settle(1.0)

    _click_day(device, year, month, day)
    _click_new_event(device)

    title_present = _find_first(device, [
        {"hint": "Title", "editable": True},
        {"text": "Title", "editable": True},
    ]) is not None

    if not title_present:
        event_idx = _find_event_option(device)
        if event_idx is None:
            raise RuntimeError("Could not find Event option after New Event")
        device.click(index=event_idx)
        device.settle(1.0)

    _input_field(device, "Title", event_title)
    _input_field(device, "Description", event_description)
    _delete_reminder_if_present(device)

    start_dt = datetime(year, month, day, hour, 0)
    end_dt = start_dt + timedelta(minutes=duration_mins)

    _open_time_field(device, "start")
    _set_time(device, start_dt.hour, start_dt.minute)

    _set_end_date_if_needed(device, end_dt, start_dt)

    start_text = f"{start_dt.hour:02d}:{start_dt.minute:02d}"
    _open_time_field(device, "end", start_text=start_text)
    _set_time(device, end_dt.hour, end_dt.minute)

    _save_event(device)
    return True
