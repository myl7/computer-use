PARAMS_SCHEMA = {
    "year": {"type": "int", "description": "Event year"},
    "month": {"type": "int", "description": "Event month, 1-12"},
    "day": {"type": "int", "description": "Event day of month"},
    "hour": {"type": "int", "description": "Event start hour, 0-23"},
    "duration_mins": {"type": "int", "description": "Event length in minutes"},
    "event_title": {"type": "str", "description": "Event title"},
    "event_description": {"type": "str", "description": "Event description"},
}


def program(device, binding: dict) -> bool:
    import re

    APP_NAME = "Simple Calendar Pro"
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    month_abbrs = [name[:3] for name in month_names]

    def _as_int(key):
        try:
            value = binding[key]
        except KeyError as exc:
            raise ValueError(f"binding missing {key}") from exc
        s = str(value).strip()
        m = re.search(r"-?\d+", s)
        if not m:
            raise ValueError(f"binding['{key}'] is not numeric: {value!r}")
        return int(m.group(0))

    year = _as_int("year")
    month = _as_int("month")
    day = _as_int("day")
    hour = _as_int("hour") % 24
    duration = _as_int("duration_mins")
    if duration < 0:
        duration = 0
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")

    end_total = hour * 60 + duration
    end_hour = (end_total // 60) % 24
    end_minute = end_total % 60

    def _parse_month_year(s):
        if not s:
            return None
        s = str(s)
        for i, name in enumerate(month_names):
            if re.search(rf"\b{re.escape(name)}\b", s):
                m = re.search(r"\b(\d{4})\b", s)
                return (int(m.group(1)) if m else 2023, i + 1)
        for i, abbr in enumerate(month_abbrs):
            if re.search(rf"\b{re.escape(abbr)}\b", s):
                m = re.search(r"\b(\d{4})\b", s)
                return (int(m.group(1)) if m else 2023, i + 1)
        return None

    def _current_month_year(els=None):
        if els is None:
            els = device.elements()
        for el in els:
            for attr in ("text", "description", "hint"):
                val = _parse_month_year(el.get(attr))
                if val is not None:
                    return val
        return None

    def _navigate_month(delta):
        desc = "Next month" if delta > 0 else "Previous month"
        idx = device.find(description=desc, clickable=True)
        if idx is None:
            idx = device.find(text=desc, clickable=True)
        if idx is None:
            alt = "Forward" if delta > 0 else "Back"
            idx = device.find(description=alt, clickable=True)
            if idx is None:
                idx = device.find(text=alt, clickable=True)
        if idx is not None:
            device.click(index=idx)
            device.settle(1)
            return
        device.scroll(direction="left" if delta > 0 else "right")
        device.settle(1)

    def _find_date_cell(els=None):
        if els is None:
            els = device.elements()

        day_strs = {str(day), f"{day:02d}"}
        month_name = month_names[month - 1]
        labels = [
            f"{month_name} {day}",
            f"{day} {month_name}",
            f"{month_name} {day}, {year}",
            f"{year}-{month:02d}-{day:02d}",
            f"{month:02d}/{day:02d}/{year}",
            f"{day}/{month}/{year}",
        ]

        for label in labels:
            idx = device.find(clickable=True, contains=label)
            if idx is not None:
                return idx
            idx = device.find(clickable=True, description=label)
            if idx is not None:
                return idx
            idx = device.find(clickable=True, hint=label)
            if idx is not None:
                return idx

        for i, el in enumerate(els):
            if not el.get("clickable"):
                continue
            vals = [el.get("text"), el.get("description"), el.get("hint")]
            for v in vals:
                if v and any(label in str(v) for label in labels):
                    return el.get("index", i)

        cur = _current_month_year(els)
        if cur == (year, month):
            for i, el in enumerate(els):
                if el.get("clickable") and (el.get("text") or "").strip() in day_strs:
                    return el.get("index", i)
            idx = device.find(text=str(day), clickable=True)
            if idx is not None:
                return idx
            idx = device.find(text=f"{day:02d}", clickable=True)
            if idx is not None:
                return idx

        return None

    def _click_target_day():
        target = (year, month)
        blind = 0
        stuck = 0

        for _ in range(80):
            els = device.elements()
            idx = _find_date_cell(els)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                return True

            cur = _current_month_year(els)
            if cur is None:
                blind += 1
                if blind > 6:
                    break
                _navigate_month(1 if blind % 2 else -1)
                continue

            if cur == target:
                device.scroll(direction="down")
                device.settle(1)
                idx = _find_date_cell(device.elements())
                if idx is not None:
                    device.click(index=idx)
                    device.settle(1)
                    return True
                break

            delta = 1 if cur < target else -1
            before = cur
            _navigate_month(delta)
            after = _current_month_year()
            if after == before:
                _navigate_month(-delta)
                after = _current_month_year()
                if after == before:
                    stuck += 1
                    if stuck > 3:
                        break
                else:
                    stuck = 0
            else:
                stuck = 0

        return False

    def _open_new_event():
        idx = device.find(description="New Event", clickable=True)
        if idx is None:
            idx = device.find(text="New Event", clickable=True)
        if idx is None:
            for i, el in enumerate(device.elements()):
                desc = (el.get("description") or "").lower()
                text = (el.get("text") or "").lower()
                if el.get("clickable") and (
                    ("new" in desc and "event" in desc)
                    or ("new" in text and "event" in text)
                ):
                    idx = el.get("index", i)
                    break
        if idx is None:
            raise RuntimeError("Could not find the New Event button")

        device.click(index=idx)
        device.settle(1)

        # If the app opened the event editor directly, skip the bottom-sheet "Event" choice.
        if device.find(hint="Title", editable=True) is not None:
            return
        if device.find(text="Title", editable=True) is not None:
            return

        idx = device.find(text="Event", clickable=True)
        if idx is None:
            for i, el in enumerate(device.elements()):
                if el.get("clickable") and (el.get("text") or "").strip() == "Event":
                    idx = el.get("index", i)
                    break
        if idx is None:
            for i, el in enumerate(device.elements()):
                if not el.get("clickable"):
                    continue
                desc = (el.get("description") or "").lower()
                text = (el.get("text") or "").lower()
                if ("event" in desc or "event" in text) and "new" not in desc and "new" not in text:
                    idx = el.get("index", i)
                    break
        if idx is None:
            raise RuntimeError("Could not find the Event option")

        device.click(index=idx)
        device.settle(1)

    def _fill_text_fields():
        idx = device.find(hint="Title", editable=True)
        if idx is None:
            idx = device.find(text="Title", editable=True)
        if idx is None:
            editables = [el for i, el in enumerate(device.elements()) if el.get("editable")]
            if editables:
                idx = editables[0].get("index", 0)
        if idx is None:
            raise RuntimeError("Could not find the event Title field")
        device.input_text(binding["event_title"], index=idx)
        device.settle(1)

        idx = device.find(hint="Description", editable=True)
        if idx is None:
            idx = device.find(text="Description", editable=True)
        if idx is None:
            editables = [el for i, el in enumerate(device.elements()) if el.get("editable")]
            if len(editables) >= 2:
                idx = editables[1].get("index", 1)
            elif editables:
                idx = editables[-1].get("index", len(editables) - 1)
        if idx is None:
            raise RuntimeError("Could not find the event Description field")
        device.input_text(binding["event_description"], index=idx)
        device.settle(1)

    def _click_ok(required=True):
        for label in ("OK", "Ok", "Okay", "Confirm"):
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                device.click(index=idx)
                device.settle(1)
                return True

        for i, el in enumerate(device.elements()):
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip().upper()
            desc = (el.get("description") or "").strip().upper()
            if text in ("OK", "OKAY", "CONFIRM") or desc in ("OK", "OKAY", "CONFIRM"):
                device.click(index=el.get("index", i))
                device.settle(1)
                return True

        if required:
            raise RuntimeError("Could not find OK button")
        return False

    def _find_date_row():
        month_alt = "|".join(re.escape(m) for m in (month_names + month_abbrs))
        date_re = re.compile(
            rf"({month_alt})\s+\d{{1,2}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}"
        )

        for i, el in enumerate(device.elements()):
            if not el.get("clickable") or el.get("editable"):
                continue
            vals = [el.get("text"), el.get("description"), el.get("hint")]
            for v in vals:
                if v and date_re.search(str(v)):
                    return el.get("index", i), str(v)

        for label in ("Start date", "Start Date", "Date"):
            idx = device.find(description=label, clickable=True)
            if idx is None:
                idx = device.find(text=label, clickable=True)
            if idx is None:
                idx = device.find(contains=label, clickable=True)
            if idx is not None:
                for i, el in enumerate(device.elements()):
                    if el.get("index", i) == idx:
                        return idx, str(el.get("text") or el.get("description") or "")
                return idx, ""

        return None, None

    def _date_text_matches(s):
        if not s:
            return False
        s = str(s)

        if f"{year}-{month:02d}-{day:02d}" in s:
            return True

        month_tokens = (month_names[month - 1], month_abbrs[month - 1])
        has_month = any(re.search(rf"\b{re.escape(t)}\b", s) for t in month_tokens)
        has_day = bool(re.search(rf"\b{day}\b", s) or re.search(rf"\b{day:02d}\b", s))

        if has_month and has_day:
            if re.search(rf"\b{year}\b", s) or year == 2023:
                return True

        if (
            re.search(rf"\b{year}\b", s)
            and re.search(rf"\b{month}\b", s)
            and re.search(rf"\b{day}\b", s)
        ):
            return True

        return False

    def _ensure_event_date():
        for _ in range(2):
            idx, txt = _find_date_row()
            if idx is None:
                return bool(date_set)
            if _date_text_matches(txt):
                return True

            device.click(index=idx)
            device.settle(1)
            if not _click_target_day():
                raise RuntimeError("Could not select date in date picker")
            _click_ok(required=False)

        return False

    def _time_field_indices():
        res = []
        for i, el in enumerate(device.elements()):
            if not el.get("clickable") or el.get("editable"):
                continue
            text = (el.get("text") or "").strip()
            if re.search(r"\d{1,2}:\d{2}", text):
                cls = el.get("class_name") or ""
                if "TextView" in cls:
                    res.append(el.get("index", i))

        if len(res) >= 2:
            return res

        for i, el in enumerate(device.elements()):
            if not el.get("clickable") or el.get("editable"):
                continue
            text = (el.get("text") or "").strip()
            if re.search(r"\d{1,2}:\d{2}", text):
                idx = el.get("index", i)
                if idx not in res:
                    res.append(idx)
        return res

    def _get_time_fields(min_count=2):
        fields = []
        for _ in range(4):
            fields = _time_field_indices()
            if len(fields) >= min_count:
                return fields
            device.scroll(direction="down")
            device.settle(1)
        return fields

    def _click_hour_item(texts, descs):
        cands = []
        for i, el in enumerate(device.elements()):
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            cls = el.get("class_name") or ""
            priority = None
            if desc in descs:
                priority = 0
            elif text in texts and "minute" not in desc.lower():
                priority = 1
            if priority is None:
                continue
            cands.append((priority, 0 if "TextView" in cls else 1, el.get("index", i)))

        if not cands:
            return False
        cands.sort(key=lambda c: (c[0], c[1], -c[2]))
        device.click(index=cands[0][2])
        device.settle(1)
        return True

    def _click_hour_selector():
        cands = []
        for i, el in enumerate(device.elements()):
            if not el.get("clickable"):
                continue
            desc = (el.get("description") or "").strip().lower()
            if desc.endswith("hours"):
                cls = el.get("class_name") or ""
                pref = 0 if "TextView" not in cls else 1
                cands.append((pref, el.get("index", i)))
        if not cands:
            return False
        cands.sort(key=lambda c: (c[0], c[1]))
        device.click(index=cands[0][1])
        device.settle(1)
        return True

    def _click_minute_item(texts, descs, dial_only):
        cands = []
        for i, el in enumerate(device.elements()):
            if not el.get("clickable"):
                continue
            text = (el.get("text") or "").strip()
            desc = (el.get("description") or "").strip()
            cls = el.get("class_name") or ""
            priority = None
            if desc in descs:
                priority = 0
            elif text in texts and desc.lower().endswith("minutes"):
                priority = 1
            if priority is None:
                continue
            if dial_only and "TextView" not in cls:
                continue
            cands.append((priority, 0 if "TextView" in cls else 1, el.get("index", i)))

        if not cands:
            return False
        cands.sort(key=lambda c: (c[0], c[1], -c[2]))
        device.click(index=cands[0][2])
        device.settle(1)
        return True

    def _click_minute_selector():
        cands = []
        for i, el in enumerate(device.elements()):
            if not el.get("clickable"):
                continue
            desc = (el.get("description") or "").strip().lower()
            if desc.endswith("minutes"):
                cls = el.get("class_name") or ""
                pref = 0 if "TextView" not in cls else 1
                cands.append((pref, el.get("index", i)))
        if not cands:
            return False
        cands.sort(key=lambda c: (c[0], c[1]))
        device.click(index=cands[0][1])
        device.settle(1)
        return True

    def _minute_desc_count():
        count = 0
        for el in device.elements():
            if el.get("clickable") and (el.get("description") or "").strip().lower().endswith("minutes"):
                count += 1
        return count

    def _select_hour(hour):
        hour = int(hour) % 24
        texts = {str(hour), f"{hour:02d}"}
        descs = {f"{hour} hours", f"{hour:02d} hours"}

        if _click_hour_item(texts, descs):
            return
        if _click_hour_selector():
            if _click_hour_item(texts, descs):
                return

        for hint in ("Hour", "Hours", "hour"):
            idx = device.find(hint=hint, editable=True)
            if idx is None:
                idx = device.find(text=hint, editable=True)
            if idx is not None:
                device.input_text(str(hour), index=idx)
                device.settle(1)
                return

        for el in device.elements():
            if el.get("clickable") and (el.get("description") or "").strip() in descs:
                return

        raise RuntimeError(f"Could not select hour {hour} in time picker")

    def _select_minute(minute):
        minute = int(minute) % 60
        texts = {str(minute), f"{minute:02d}"}
        descs = {f"{minute} minutes", f"{minute:02d} minutes"}

        if _minute_desc_count() > 2:
            if _click_minute_item(texts, descs, True):
                return

        if _click_minute_selector():
            if _click_minute_item(texts, descs, False):
                return

        for hint in ("Minute", "Minutes", "minute"):
            idx = device.find(hint=hint, editable=True)
            if idx is None:
                idx = device.find(text=hint, editable=True)
            if idx is not None:
                device.input_text(str(minute), index=idx)
                device.settle(1)
                return

        for i, el in enumerate(device.elements()):
            if el.get("editable") and (el.get("description") or "").strip().lower().endswith("minutes"):
                device.input_text(str(minute), index=el.get("index", i))
                device.settle(1)
                return

        if minute == 0:
            return

        raise RuntimeError(f"Could not select minute {minute} in time picker")

    def _pick_time(hour, minute):
        _select_hour(hour)
        _select_minute(minute)
        _click_ok(required=True)

    def _save():
        idx = device.find(description="Save", clickable=True)
        if idx is None:
            idx = device.find(text="Save", clickable=True)
        if idx is None:
            for i, el in enumerate(device.elements()):
                if el.get("clickable") and (
                    (el.get("description") or "").strip().lower() == "save"
                    or (el.get("text") or "").strip().lower() == "save"
                ):
                    idx = el.get("index", i)
                    break
        if idx is None:
            raise RuntimeError("Could not find Save button")
        device.click(index=idx)
        device.settle(2)

    device.open_app(APP_NAME)
    device.settle(2)

    date_set = _click_target_day()

    _open_new_event()
    _fill_text_fields()

    if not _ensure_event_date():
        raise RuntimeError("Could not ensure the event date")

    fields = _get_time_fields(1)
    if not fields:
        raise RuntimeError("Could not find the start time field")
    device.click(index=fields[0])
    device.settle(1)
    _pick_time(hour, 0)

    fields = _get_time_fields(2)
    if len(fields) < 2:
        raise RuntimeError("Could not find the end time field")
    device.click(index=fields[1])
    device.settle(1)
    _pick_time(end_hour, end_minute)

    _save()
    return True
