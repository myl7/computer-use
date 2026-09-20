import calendar
import re
from datetime import datetime

PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "year": {"type": "integer", "description": "Event year"},
        "month": {"type": "integer", "description": "Event month, 1-12"},
        "day": {"type": "integer", "description": "Event day of month"},
        "hour": {"type": "integer", "description": "Event start hour, 0-23"},
        "duration_mins": {"type": "integer", "description": "Event duration in minutes"},
        "event_title": {"type": "string", "description": "Event title"},
        "event_description": {"type": "string", "description": "Event description"},
    },
    "required": ["year", "month", "day", "hour", "duration_mins", "event_title", "event_description"],
}


def program(device, binding: dict) -> bool:
    def _as_int(key, min_value=None, max_value=None):
        try:
            value = int(binding[key])
        except Exception as exc:
            raise ValueError(f"binding['{key}'] must be an integer") from exc
        if min_value is not None and value < min_value:
            raise ValueError(f"binding['{key}'] must be >= {min_value}")
        if max_value is not None and value > max_value:
            raise ValueError(f"binding['{key}'] must be <= {max_value}")
        return value

    year = _as_int("year")
    month = _as_int("month", 1, 12)
    day = _as_int("day", 1, 31)
    hour = _as_int("hour", 0, 23)
    duration_mins = _as_int("duration_mins", 0)
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    try:
        datetime(year, month, day)
    except ValueError as exc:
        raise ValueError("binding does not contain a valid calendar date") from exc

    month_names = list(calendar.month_name)
    month_abbrs = list(calendar.month_abbr)
    day_str = str(day)
    day_padded = f"{day:02d}"
    year_str = str(year)
    month_name = month_names[month]
    month_abbr = month_abbrs[month]

    selected_day = False

    def element_by_index(idx):
        if idx is None:
            return None
        for el in device.elements():
            if el.get("index") == idx:
                return el
        return None

    def click_element(idx, settle=0.5):
        if idx is None:
            return False
        device.click(index=idx)
        device.settle(settle)
        return True

    def is_time_text(s):
        s = str(s or "").strip()
        if ":" not in s:
            return False
        parts = s.split(":")
        if len(parts) != 2:
            return False
        if not parts[0].isdigit() or not parts[1].isdigit():
            return False
        try:
            h = int(parts[0])
            m = int(parts[1])
        except Exception:
            return False
        return 0 <= h <= 23 and 0 <= m <= 59

    def parse_month_year(s):
        s = str(s or "").strip()
        if not s:
            return None

        m = re.search(r"([A-Za-z]{3,})\s+(?:\d{1,2},?\s+)?(20\d{2}|19\d{2})", s)
        if m:
            name = m.group(1).lower()
            yr = int(m.group(2))
            for i in range(1, 13):
                if month_names[i].lower().startswith(name):
                    return i, yr
            for i in range(1, 13):
                if month_abbrs[i].lower() == name:
                    return i, yr

        m = re.search(r"(20\d{2}|19\d{2})\s+([A-Za-z]{3,})", s)
        if m:
            yr = int(m.group(1))
            name = m.group(2).lower()
            for i in range(1, 13):
                if month_names[i].lower().startswith(name):
                    return i, yr
            for i in range(1, 13):
                if month_abbrs[i].lower() == name:
                    return i, yr

        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", s)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            yr = int(m.group(3))
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            if 1 <= a <= 12:
                return a, yr
            if 1 <= b <= 12:
                return b, yr

        m = re.search(r"\b(\d{1,2})/(\d{2,4})\b", s)
        if m:
            mo = int(m.group(1))
            yr = int(m.group(2))
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            if 1 <= mo <= 12:
                return mo, yr

        return None

    def find_header_month_year():
        for el in device.elements():
            text = str(el.get("text") or "").strip()
            if text:
                parsed = parse_month_year(text)
                if parsed:
                    return parsed
        for el in device.elements():
            desc = str(el.get("description") or "").strip()
            if desc:
                parsed = parse_month_year(desc)
                if parsed:
                    return parsed
        return None

    def looks_like_date(text, desc, hint):
        s = " ".join([str(text or ""), str(desc or ""), str(hint or "")]).strip()
        if not s:
            return False
        s_lower = s.lower()
        if re.search(r"\b\d{1,2}:\d{2}\b", s_lower):
            return False
        if re.search(r"\b(19|20)\d{2}\b", s):
            return True
        for mn in month_names[1:]:
            if mn and mn.lower() in s_lower:
                return True
        for ab in month_abbrs[1:]:
            if ab and ab.lower() in s_lower:
                return True
        if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", s):
            return True
        if re.search(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+(19|20)\d{2}\b", s):
            return True
        return False

    def click_ok_dialog():
        labels = ("OK", "Ok", "Okay", "Done", "Set", "Confirm")
        for label in labels:
            idx = device.find(text=label, clickable=True)
            if idx is not None:
                return click_element(idx, 0.5)
        for label in labels:
            idx = device.find(description=label, clickable=True)
            if idx is not None:
                return click_element(idx, 0.5)
        for el in device.elements():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            if text in labels or desc in labels:
                return click_element(el.get("index"), 0.5)
        for el in device.elements():
            if el.get("clickable") and str(el.get("class_name") or "").endswith("Button"):
                text = str(el.get("text") or "").strip()
                desc = str(el.get("description") or "").strip()
                if text in labels or desc in labels:
                    return click_element(el.get("index"), 0.5)
        return False

    def find_save():
        for kwargs in (
            {"description": "Save", "clickable": True},
            {"text": "Save", "clickable": True},
            {"contains": "Save", "clickable": True},
            {"description": "Save event", "clickable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        for el in device.elements():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "save" in desc or text == "save":
                return el.get("index")
        return None

    def find_new_event():
        for kwargs in (
            {"description": "New Event", "clickable": True},
            {"text": "New Event", "clickable": True},
            {"contains": "New Event", "clickable": True},
            {"description": "Add event", "clickable": True},
            {"text": "Add event", "clickable": True},
        ):
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        for el in device.elements():
            if not el.get("clickable"):
                continue
            desc = str(el.get("description") or "").lower()
            text = str(el.get("text") or "").lower()
            if "new event" in desc or "new event" in text or "add event" in desc:
                return el.get("index")
        return None

    def find_event_option():
        idx = device.find(text="Event", clickable=True)
        if idx is not None:
            return idx
        idx = device.find(description="Event", clickable=True)
        if idx is not None:
            return idx
        for el in device.elements():
            if el.get("clickable") and str(el.get("text") or "").strip() == "Event":
                return el.get("index")
        return None

    def find_title():
        idx = device.find(hint="Title", editable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Title", editable=True)
        if idx is not None:
            return idx
        editables = []
        for el in device.elements():
            if el.get("editable"):
                editables.append(el)
                combined = (str(el.get("hint") or "") + " " + str(el.get("text") or "")).lower()
                if "title" in combined:
                    return el.get("index")
        if editables:
            return editables[0].get("index")
        return None

    def find_desc():
        idx = device.find(hint="Description", editable=True)
        if idx is not None:
            return idx
        idx = device.find(text="Description", editable=True)
        if idx is not None:
            return idx
        editables = [el for el in device.elements() if el.get("editable")]
        editables.sort(key=lambda e: e.get("index", 0))
        for el in editables:
            combined = (str(el.get("hint") or "") + " " + str(el.get("text") or "")).lower()
            if "desc" in combined:
                return el.get("index")
        if len(editables) >= 2:
            return editables[1].get("index")
        title_idx = find_title()
        if title_idx is not None:
            later = [el for el in editables if el.get("index", -1) > title_idx]
            if later:
                return later[0].get("index")
        return None

    def delete_reminder():
        for el in device.elements():
            if (
                el.get("clickable")
                and str(el.get("description") or "").strip() == "Delete"
                and str(el.get("class_name") or "").endswith("FrameLayout")
            ):
                return click_element(el.get("index"), 0.3)
        return False

    def find_time_fields(count=2):
        def collect():
            res = []
            for el in device.elements():
                if not el.get("clickable"):
                    continue
                cls = str(el.get("class_name") or "")
                text = str(el.get("text") or "").strip()
                if "TextView" in cls and is_time_text(text):
                    res.append(el.get("index", 0))
                elif is_time_text(text) and "Button" not in cls:
                    res.append(el.get("index", 0))
            return res

        for _ in range(4):
            res = collect()
            if len(res) >= count:
                return res[:count]
            device.scroll(direction="down")
            device.settle(0.2)

        for _ in range(2):
            res = collect()
            if len(res) >= count:
                return res[:count]
            device.scroll(direction="up")
            device.settle(0.2)

        return res[:count] if res else []

    def click_time_picker(hour_to_set, minute_to_set):
        def click_hour(h):
            hour_str = str(h)
            hour_pad = f"{h:02d}"
            descs = []
            for t in (hour_str, hour_pad):
                descs.extend([f"{t} hours", f"{t} hour"])
            for d in descs:
                idx = device.find(description=d, clickable=True)
                if idx is not None:
                    return click_element(idx, 0.3)
            for t in (hour_str, hour_pad):
                for el in device.elements():
                    if not el.get("clickable"):
                        continue
                    if str(el.get("text") or "").strip() != t:
                        continue
                    desc = str(el.get("description") or "").lower()
                    cls = str(el.get("class_name") or "")
                    if "hour" in desc or ("TextView" in cls and not desc.endswith("minutes")):
                        return click_element(el.get("index"), 0.3)
            for d in descs:
                if device.find(description=d) is not None:
                    return True
            return False

        def click_minute(m):
            minute_str = str(m)
            minute_pad = f"{m:02d}"
            descs = []
            for t in (minute_str, minute_pad):
                descs.extend([f"{t} minutes", f"{t} minute"])

            selector_idx = None
            selector_desc = None
            for el in device.elements():
                if not el.get("clickable"):
                    continue
                desc = str(el.get("description") or "").strip()
                text = str(el.get("text") or "").strip()
                cls = str(el.get("class_name") or "")
                if desc.lower().endswith(" minutes") and re.fullmatch(r"\d{1,2}", text):
                    if selector_idx is None or cls.endswith("View"):
                        selector_idx = el.get("index")
                        selector_desc = desc

            if selector_idx is not None:
                click_element(selector_idx, 0.2)

            option_idx = None
            for d in descs:
                for el in device.elements():
                    if not el.get("clickable"):
                        continue
                    if str(el.get("description") or "").strip() != d:
                        continue
                    idx = el.get("index")
                    if selector_idx is not None and idx == selector_idx:
                        continue
                    text = str(el.get("text") or "").strip()
                    if text in (minute_str, minute_pad) or d.endswith(" minutes"):
                        option_idx = idx
                        break
                if option_idx is not None:
                    break

            if option_idx is None:
                for el in device.elements():
                    if not el.get("clickable"):
                        continue
                    text = str(el.get("text") or "").strip()
                    desc = str(el.get("description") or "").lower()
                    if text in (minute_str, minute_pad) and "minute" in desc:
                        option_idx = el.get("index")
                        break

            if option_idx is not None:
                return click_element(option_idx, 0.2)

            if selector_idx is not None:
                sel_el = element_by_index(selector_idx)
                if sel_el and sel_el.get("editable"):
                    device.input_text(minute_pad, index=selector_idx)
                    device.settle(0.2)
                    return True

            for el in device.elements():
                if not el.get("editable"):
                    continue
                desc = str(el.get("description") or "").lower()
                text = str(el.get("text") or "").strip()
                if "minute" in desc or re.fullmatch(r"\d{2}", text):
                    device.input_text(minute_pad, index=el.get("index"))
                    device.settle(0.2)
                    return True

            if selector_desc and selector_desc.lower() in [d.lower() for d in descs]:
                return True
            if m == 0:
                return True
            return False

        if hour_to_set is not None:
            if not click_hour(hour_to_set):
                for el in device.elements():
                    if el.get("clickable"):
                        desc = str(el.get("description") or "").lower()
                        text = str(el.get("text") or "").strip()
                        if desc.endswith(" hours") and re.fullmatch(r"\d{1,2}", text):
                            click_element(el.get("index"), 0.2)
                            break
                if not click_hour(hour_to_set):
                    raise RuntimeError(f"Could not set hour {hour_to_set} in time picker")

        if minute_to_set is not None:
            if not click_minute(minute_to_set):
                raise RuntimeError(f"Could not set minute {minute_to_set} in time picker")

        if not click_ok_dialog():
            raise RuntimeError("Could not confirm time picker")

    def find_date_row(after_index=None):
        candidates = []
        for el in device.elements():
            if not el.get("clickable") or el.get("editable"):
                continue
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            combined = (text + " " + desc + " " + hint).lower()
            score = 0
            if "start date" in combined:
                score += 100
            if looks_like_date(text, desc, hint):
                score += 50
            if combined in ("today", "tomorrow"):
                score += 40
            if score:
                if day_str in combined or day_padded in combined:
                    score += 20
                if month_name.lower() in combined or month_abbr.lower() in combined:
                    score += 20
                if year_str in combined:
                    score += 10
                if "TextView" in str(el.get("class_name") or ""):
                    score += 5
                candidates.append((score, el.get("index", 0)))

        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1]))
            return candidates[0][1]

        if after_index is not None:
            rows = []
            for el in device.elements():
                idx = el.get("index")
                if idx is None or idx <= after_index:
                    continue
                if not el.get("clickable") or el.get("editable"):
                    continue
                text = str(el.get("text") or "").strip()
                desc = str(el.get("description") or "").strip()
                combined = (text + " " + desc).lower()
                if is_time_text(text):
                    continue
                t = text.lower()
                if t in ("event", "task", "type", "calendar", "reminder", "repeat", "all-day", "all day", "save", "delete", "new"):
                    continue
                if "date" not in combined and t not in ("today", "tomorrow"):
                    continue
                if any(w in combined for w in ("reminder", "repeat", "all-day", "all day", "save", "delete", "new", "task", "type", "calendar")):
                    continue
                if "event" in combined and "date" not in combined:
                    continue
                rows.append(idx)
            if rows:
                rows.sort()
                return rows[0]

        return None

    def date_row_matches(after_index=None):
        idx = find_date_row(after_index)
        if idx is None:
            return selected_day
        el = element_by_index(idx)
        if el is None:
            return selected_day
        combined = " ".join([
            str(el.get("text") or ""),
            str(el.get("description") or ""),
            str(el.get("hint") or ""),
        ]).lower()

        day_ok = bool(re.search(rf"\b{day_str}\b", combined)) or (day_padded in combined)
        month_ok = month_name.lower() in combined or month_abbr.lower() in combined
        year_ok = year_str in combined
        has_any_month = any(m.lower() in combined for m in month_names[1:] if m)
        has_any_year = bool(re.search(r"\b(19|20)\d{2}\b", combined))

        if day_ok:
            if has_any_year and not year_ok:
                return False
            if has_any_month and not month_ok:
                return False
            if month_ok or year_ok:
                return True
            if selected_day:
                return True
        return False

    def set_date_in_picker():
        def find_day_in_picker():
            candidates = []
            for el in device.elements():
                if not el.get("clickable"):
                    continue
                text = str(el.get("text") or "").strip()
                desc = str(el.get("description") or "").strip()
                score = 0
                desc_low = desc.lower()
                if desc_low:
                    if year_str in desc_low and month_name.lower() in desc_low and (day_str in desc or day_padded in desc):
                        score += 100
                    elif month_name.lower() in desc_low and (day_str in desc or day_padded in desc):
                        score += 80
                    elif month_abbr.lower() in desc_low and (day_str in desc or day_padded in desc):
                        score += 70
                if text == day_str or text == day_padded:
                    score += 40
                if score:
                    candidates.append((score, el.get("index", 0), el))

            if not candidates:
                return None, 0

            candidates.sort(key=lambda x: (-x[0], x[1]))
            best_score, best_idx, _ = candidates[0]
            if best_score >= 70:
                return best_idx, best_score

            text_candidates = [
                el for score, idx, el in candidates
                if str(el.get("text") or "").strip() in (day_str, day_padded)
            ]
            if text_candidates:
                text_candidates.sort(key=lambda e: e.get("index", 0))
                chosen = text_candidates[len(text_candidates) // 2]
                return chosen.get("index"), 40

            return best_idx, best_score

        def click_nav(which):
            if which == "next":
                descs = ("Next month", "Go to next month", "next month", "Next")
                texts = (">", "›", "Next")
            else:
                descs = ("Previous month", "Go to previous month", "previous month", "Previous")
                texts = ("<", "‹", "Previous")

            for d in descs:
                idx = device.find(description=d, clickable=True)
                if idx is not None:
                    return click_element(idx, 0.3)
            for t in texts:
                idx = device.find(text=t, clickable=True)
                if idx is not None:
                    return click_element(idx, 0.3)
            try:
                device.scroll(direction="left" if which == "next" else "right")
                device.settle(0.2)
                return True
            except Exception:
                return False

        idx, score = find_day_in_picker()
        hdr = find_header_month_year()
        if idx is not None and (score >= 70 or (hdr is not None and hdr[0] == month and hdr[1] == year)):
            click_element(idx, 0.3)
            return click_ok_dialog()

        for _ in range(180):
            idx, score = find_day_in_picker()
            hdr = find_header_month_year()

            if idx is not None and (score >= 70 or (hdr is not None and hdr[0] == month and hdr[1] == year)):
                click_element(idx, 0.3)
                return click_ok_dialog()

            if hdr is not None:
                cur_m, cur_y = hdr
                if cur_y < year or (cur_y == year and cur_m < month):
                    if click_nav("next"):
                        continue
                elif cur_y > year or (cur_y == year and cur_m > month):
                    if click_nav("prev"):
                        continue
                else:
                    if idx is not None:
                        click_element(idx, 0.3)
                        return click_ok_dialog()
                    break
            else:
                if idx is not None:
                    click_element(idx, 0.3)
                    return click_ok_dialog()
                idx2 = device.find(text=day_str, clickable=True)
                if idx2 is None:
                    idx2 = device.find(text=day_padded, clickable=True)
                if idx2 is not None:
                    click_element(idx2, 0.3)
                    return click_ok_dialog()
                try:
                    device.scroll(direction="down")
                    device.settle(0.2)
                except Exception:
                    pass

        idx, score = find_day_in_picker()
        if idx is not None:
            click_element(idx, 0.3)
            return click_ok_dialog()

        idx2 = device.find(text=day_str, clickable=True)
        if idx2 is None:
            idx2 = device.find(text=day_padded, clickable=True)
        if idx2 is not None:
            click_element(idx2, 0.3)
            return click_ok_dialog()

        return False

    def try_set_start_date(after_index=None):
        idx = find_date_row(after_index)
        if idx is None:
            return False
        if not click_element(idx, 0.5):
            return False
        return set_date_in_picker()

    def try_select_day_on_main():
        candidates = []
        for el in device.elements():
            if not el.get("clickable"):
                continue
            text = str(el.get("text") or "").strip()
            desc = str(el.get("description") or "").strip()
            hint = str(el.get("hint") or "").strip()
            desc_low = desc.lower()
            hint_low = hint.lower()
            score = 0

            if desc_low:
                if year_str in desc_low and month_name.lower() in desc_low and (day_str in desc or day_padded in desc):
                    score += 100
                elif month_name.lower() in desc_low and (day_str in desc or day_padded in desc):
                    score += 85
                elif month_abbr.lower() in desc_low and (day_str in desc or day_padded in desc):
                    score += 75
                elif year_str in desc_low and (day_str in desc or day_padded in desc):
                    score += 65

            if text == day_str or text == day_padded:
                score += 50
                if month_name.lower() in desc_low or month_abbr.lower() in desc_low:
                    score += 25
                if year_str in desc_low:
                    score += 15

            if hint_low == day_str or hint_low == day_padded:
                score += 20

            if score:
                candidates.append((score, el.get("index", 0), el))

        if not candidates:
            return False

        candidates.sort(key=lambda x: (-x[0], x[1]))
        best_score, best_idx, best_el = candidates[0]
        header = find_header_month_year()
        header_matches = header is not None and header[0] == month and header[1] == year

        if best_score >= 75:
            return click_element(best_idx, 0.5)

        text_candidates = [
            el for score, idx, el in candidates
            if str(el.get("text") or "").strip() in (day_str, day_padded)
        ]
        if text_candidates and (header_matches or header is None):
            text_candidates.sort(key=lambda e: e.get("index", 0))
            chosen = text_candidates[len(text_candidates) // 2]
            return click_element(chosen.get("index"), 0.5)

        if best_score >= 65 and (header_matches or header is None):
            return click_element(best_idx, 0.5)

        return False

    device.open_app("Simple Calendar Pro")
    device.settle(1.0)

    selected_day = try_select_day_on_main()

    new_idx = find_new_event()
    if new_idx is None:
        raise RuntimeError("Could not find the New Event button")
    click_element(new_idx, 1.0)

    event_idx = find_event_option()
    if event_idx is not None:
        click_element(event_idx, 1.0)

    title_idx = find_title()
    if title_idx is None:
        event_idx = find_event_option()
        if event_idx is not None:
            click_element(event_idx, 1.0)
        title_idx = find_title()
    if title_idx is None:
        raise RuntimeError("Could not find the event Title field")

    device.input_text(event_title, index=title_idx)
    device.settle(0.3)

    desc_idx = find_desc()
    if desc_idx is None:
        raise RuntimeError("Could not find the event Description field")

    device.input_text(event_description, index=desc_idx)
    device.settle(0.3)

    delete_reminder()
    refreshed_desc_idx = find_desc()
    if refreshed_desc_idx is not None:
        desc_idx = refreshed_desc_idx

    if not selected_day:
        if not try_set_start_date(desc_idx):
            raise RuntimeError("Could not set the event start date")
    else:
        if not date_row_matches(desc_idx):
            if not try_set_start_date(desc_idx):
                raise RuntimeError("Could not set the event start date")

    times = find_time_fields(2)
    if len(times) < 2:
        raise RuntimeError("Could not find start and end time fields")
    click_element(times[0], 0.5)
    click_time_picker(hour, 0)

    times = find_time_fields(2)
    if len(times) < 2:
        raise RuntimeError("Could not find start and end time fields after setting start time")
    click_element(times[1], 0.5)

    end_total = hour * 60 + duration_mins
    end_hour = (end_total // 60) % 24
    end_minute = end_total % 60
    click_time_picker(end_hour, end_minute)

    save_idx = find_save()
    if save_idx is None:
        raise RuntimeError("Could not find the Save button")
    click_element(save_idx, 1.0)

    return True
