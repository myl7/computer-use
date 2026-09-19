import calendar
import re
from datetime import datetime, timedelta

PARAMS_SCHEMA = {
    "year": {"type": "int", "required": True, "min": 1900, "max": 2100,
             "description": "Year of the event"},
    "month": {"type": "int", "required": True, "min": 1, "max": 12,
              "description": "Month of the event (1-12)"},
    "day": {"type": "int", "required": True, "min": 1, "max": 31,
            "description": "Day of month of the event"},
    "hour": {"type": "int", "required": True, "min": 0, "max": 23,
             "description": "Start hour of the event (24-hour clock)"},
    "duration_mins": {"type": "int", "required": True, "min": 1,
                      "description": "Event length in minutes"},
    "event_title": {"type": "str", "required": True,
                    "description": "Title of the event"},
    "event_description": {"type": "str", "required": True,
                          "description": "Description of the event"},
}


def program(device, binding: dict) -> bool:
    year = int(binding["year"])
    month = int(binding["month"])
    day = int(binding["day"])
    hour = int(binding["hour"])
    duration_mins = int(binding["duration_mins"])
    event_title = str(binding["event_title"])
    event_description = str(binding["event_description"])

    time_pat = re.compile(r"\d{1,2}:\d{2}")
    month_words = [calendar.month_name[m].lower() for m in range(1, 13)] + \
                  [calendar.month_abbr[m].lower() for m in range(1, 13)]

    # ---------------- low-level helpers ----------------
    def elems():
        try:
            return device.elements()
        except Exception:
            return []

    def text_of(e):
        return (e.get("text") or "").strip()

    def desc_of(e):
        return (e.get("description") or "").strip()

    def hint_of(e):
        return (e.get("hint") or "").strip()

    def find_idx(**kw):
        try:
            return device.find(**kw)
        except Exception:
            return None

    def editable_fields():
        return [e for e in elems() if e.get("editable")]

    def event_screen_open():
        if find_idx(hint="Title") is not None or find_idx(hint="Description") is not None:
            return True
        for e in elems():
            if e.get("editable"):
                h = hint_of(e).lower()
                if h in ("title", "description", ""):
                    return True
        return False

    def find_ok():
        i = find_idx(text="OK")
        if i is not None:
            return i
        for e in elems():
            if text_of(e).lower() == "ok" and e.get("clickable"):
                return e["index"]
        for e in elems():
            if text_of(e).lower() == "ok":
                return e["index"]
        return None

    def dismiss_stray_dialog():
        for cand in ("Cancel", "CANCEL", "cancel"):
            i = find_idx(text=cand)
            if i is not None:
                device.click(index=i)
                return

    # ---------------- event-screen rows ----------------
    def is_date_text(t):
        tl = t.lower()
        if any(w in tl for w in month_words):
            return True
        if re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}(\s*[./-]\s*\d{2,4})?", t):
            return True
        if str(year) in t and re.search(r"\d", t):
            return True
        return False

    def locate_rows():
        times, dates = [], []
        for e in elems():
            if e.get("editable"):
                continue
            t = text_of(e)
            if not t:
                continue
            if time_pat.search(t):
                times.append(e)
            elif is_date_text(t):
                dates.append(e)
        if len(times) < 2:
            raise RuntimeError(
                "Event screen: expected start and end time rows "
                "(found %d time-like elements)" % len(times))
        start_date = dates[0] if dates else None
        end_date = dates[1] if len(dates) > 1 else None
        return start_date, times[0], end_date, times[1]

    def click_row(row, label):
        if row is None:
            raise RuntimeError("%s row not found on the event screen" % label)
        device.click(index=row["index"])

    def date_row_shows(row, y, m, d):
        if row is None:
            return False
        t = text_of(row)
        if re.search(r"(?<!\d)%d(?!\d)" % d, t) is None:
            return False
        tl = t.lower()
        if calendar.month_name[m].lower() in tl or calendar.month_abbr[m].lower() in tl:
            return True
        if str(y) in t:
            return True
        return re.search(r"\d{1,2}\s*[./-]\s*\d{1,2}", t) is not None

    def time_row_shows(row, h, minute):
        if row is None:
            return False
        t = text_of(row)
        mm = "%02d" % minute
        if re.search(r"(?<!\d)%d:%s(?!\d)" % (h, mm), t) is not None:
            return True
        if re.search(r"(?<!\d)%02d:%s(?!\d)" % (h, mm), t) is not None:
            return True
        h12 = h % 12 or 12
        ampm = "am" if h < 12 else "pm"
        tl = t.lower()
        return re.search(r"(?<!\d)%d:%s" % (h12, mm), tl) is not None and ampm in tl

    # ---------------- date picker ----------------
    def find_arrow(which):
        for d in (which.capitalize(), which, which.upper()):
            i = find_idx(description=d)
            if i is not None:
                return i
        for e in elems():
            if which in desc_of(e).lower():
                return e["index"]
        return None

    def month_header_ok(y, m):
        names = (calendar.month_name[m].lower(), calendar.month_abbr[m].lower())
        ys = str(y)
        for e in elems():
            t = text_of(e).lower()
            if ys in t and any(n in t for n in names):
                return True
        return False

    def ensure_month(y, m):
        if month_header_ok(y, m):
            return
        name_to_month = {}
        for mm in range(1, 13):
            name_to_month[calendar.month_name[mm].lower()] = mm
            name_to_month[calendar.month_abbr[mm].lower()] = mm
        for _ in range(24):
            cur_m, cur_y = None, None
            for e in elems():
                t = text_of(e)
                tl = t.lower()
                if cur_m is None:
                    for name, mm2 in name_to_month.items():
                        if name in tl:
                            cur_m = mm2
                            break
                ym = re.search(r"\b(\d{4})\b", t)
                if ym is not None and cur_y is None:
                    cur_y = int(ym.group(1))
                if cur_m is not None and cur_y is not None:
                    break
            if cur_m is None:
                return  # cannot tell which month is shown; assume it is correct
            if cur_y is None:
                cur_y = y
            diff = (y * 12 + m) - (cur_y * 12 + cur_m)
            if diff == 0:
                return
            arrow = find_arrow("next" if diff > 0 else "previous")
            if arrow is None:
                return
            device.click(index=arrow)
            if month_header_ok(y, m):
                return

    def find_day_cell(d, m, y):
        ds = str(d)
        i = find_idx(text=ds, clickable=True)
        if i is None:
            i = find_idx(text=ds)
        if i is not None:
            return i
        names = (calendar.month_name[m].lower(), calendar.month_abbr[m].lower())
        pat = r"(?<!\d)%d(?!\d)" % d
        for e in elems():
            dl = desc_of(e).lower()
            if dl and re.search(pat, dl) and any(n in dl for n in names):
                return e["index"]
        for e in elems():
            dl = desc_of(e).lower()
            if dl and re.search(pat, dl) and str(y) in dl:
                return e["index"]
        return None

    def pick_date_in_dialog(y, m, d):
        if find_ok() is None:
            raise RuntimeError("Date picker dialog did not open")
        ensure_month(y, m)
        cell = find_day_cell(d, m, y)
        if cell is None:
            raise RuntimeError("Day %d was not found in the date picker" % d)
        device.click(index=cell)
        ok = find_ok()
        if ok is None:
            raise RuntimeError("Date picker OK button not found")
        device.click(index=ok)

    # ---------------- time picker ----------------
    def keyboard_toggle():
        for e in elems():
            blob = (desc_of(e) + " " + text_of(e) + " " + hint_of(e)).lower()
            if "keyboard" in blob:
                return e["index"]
        return None

    def pick_time_in_dialog(h, minute):
        if find_ok() is None:
            raise RuntimeError("Time picker dialog did not open")
        mstr = "%02d" % minute
        hour_cands = [str(h)]
        if h == 0:
            hour_cands += ["24", "00"]
        else:
            hour_cands.append("%02d" % h)
        for cand in hour_cands:
            i = find_idx(text=cand, clickable=True)
            if i is None:
                i = find_idx(text=cand)
            if i is None:
                continue
            device.click(index=i)
            # tapping an hour normally advances the clock to minute selection
            if find_idx(text="05") is not None:
                j = find_idx(text=mstr, clickable=True)
                if j is None:
                    j = find_idx(text=mstr)
                if j is not None:
                    device.click(index=j)
                    ok = find_ok()
                    if ok is None:
                        raise RuntimeError("Time picker OK button not found")
                    device.click(index=ok)
                    return
            break  # minute view not usable -> fall back to keyboard entry
        kb = keyboard_toggle()
        if kb is None:
            raise RuntimeError("Could not set time %02d:%s in the time picker" % (h, mstr))
        device.click(index=kb)
        fields = editable_fields()
        if len(fields) < 2:
            raise RuntimeError("Time picker keyboard-entry fields not found")
        device.input_text(str(h), index=fields[0]["index"])
        device.input_text(mstr, index=fields[1]["index"])
        ok = find_ok()
        if ok is None:
            raise RuntimeError("Time picker OK button not found (keyboard mode)")
        device.click(index=ok)

    # ---------------- app flow ----------------
    def open_new_event_screen():
        fab = find_idx(description="New Event")
        if fab is None:
            raise RuntimeError(
                "Simple Calendar Pro main screen did not open: 'New Event' button not found")
        device.click(index=fab)

        def find_event_option():
            i = find_idx(text="Event", clickable=True)
            if i is not None:
                return i
            for e in elems():
                if text_of(e).lower() == "event" and e.get("clickable"):
                    return e["index"]
            i = find_idx(text="Event")
            if i is not None:
                return i
            for e in elems():
                if text_of(e).lower() == "event":
                    return e["index"]
            return None

        def try_confirm():
            opt = find_event_option()
            if opt is not None:
                device.click(index=opt)
                if event_screen_open():
                    return True
            ok = find_ok()
            if ok is not None:
                device.click(index=ok)
                if event_screen_open():
                    return True
            return False

        if event_screen_open():
            return
        for _ in range(2):
            if try_confirm():
                return
            dismiss_stray_dialog()
            fab2 = find_idx(description="New Event")
            if fab2 is not None:
                device.click(index=fab2)
        raise RuntimeError("Could not reach the new-event screen")

    def set_date(target, row_pos, label):
        last_err = None
        for _ in range(2):
            try:
                sd, st, ed, et = locate_rows()
                click_row(sd if row_pos == 0 else ed, label)
                pick_date_in_dialog(target[0], target[1], target[2])
                sd, st, ed, et = locate_rows()
                row = sd if row_pos == 0 else ed
                if date_row_shows(row, target[0], target[1], target[2]):
                    return
                last_err = RuntimeError("%s row does not show the requested date" % label)
            except Exception as exc:
                last_err = exc
            dismiss_stray_dialog()
        raise last_err if last_err is not None else RuntimeError("%s could not be set" % label)

    def set_time(row_pos, label, h, minute):
        last_err = None
        for _ in range(2):
            try:
                sd, st, ed, et = locate_rows()
                click_row(st if row_pos == 0 else et, label)
                pick_time_in_dialog(h, minute)
                sd, st, ed, et = locate_rows()
                row = st if row_pos == 0 else et
                if time_row_shows(row, h, minute):
                    return
                last_err = RuntimeError("%s row does not show the requested time" % label)
            except Exception as exc:
                last_err = exc
            dismiss_stray_dialog()
        raise last_err if last_err is not None else RuntimeError("%s could not be set" % label)

    device.open_app("Simple Calendar Pro")
    device.settle(2)
    open_new_event_screen()

    # Title = first text field, Description = second text field
    title_idx = find_idx(hint="Title")
    if title_idx is None:
        fields = editable_fields()
        if not fields:
            raise RuntimeError("Title field not found on the new-event screen")
        title_idx = fields[0]["index"]
    device.input_text(event_title, index=title_idx)

    desc_idx = find_idx(hint="Description")
    if desc_idx is None:
        fields = editable_fields()
        if len(fields) < 2:
            raise RuntimeError("Description field not found on the new-event screen")
        desc_idx = fields[1]["index"]
    device.input_text(event_description, index=desc_idx)

    # Start date
    set_date((year, month, day), 0, "Start date")

    # Start time at {hour}:00
    set_time(0, "Start time", hour, 0)

    # End time = start time + duration_mins (the app has no duration field)
    end_dt = datetime(year, month, day, hour, 0) + timedelta(minutes=duration_mins)
    set_time(1, "End time", end_dt.hour, end_dt.minute)

    # If the event crosses midnight, fix the end date too
    if (end_dt.year, end_dt.month, end_dt.day) != (year, month, day):
        set_date((end_dt.year, end_dt.month, end_dt.day), 1, "End date")

    # Save and confirm we are back on the main screen
    save_idx = find_idx(description="Save")
    if save_idx is None:
        save_idx = find_idx(text="Save")
    if save_idx is None:
        for e in elems():
            blob = (desc_of(e) + " " + text_of(e) + " " + hint_of(e)).lower()
            if "save" in blob and e.get("clickable"):
                save_idx = e["index"]
                break
    if save_idx is None:
        raise RuntimeError("Save button not found on the event screen")
    device.click(index=save_idx)

    if event_screen_open() or find_idx(description="New Event") is None:
        raise RuntimeError("Event was not saved: editor did not close to the main screen")
    return True
