PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim",
    },
}


def _txt(e, *keys):
    out = []
    for k in keys:
        v = e.get(k)
        if isinstance(v, str):
            out.append(v)
    return ' '.join(out)


def _low(e, *keys):
    return _txt(e, *keys).lower()


def _digits(s):
    return ''.join(ch for ch in (s or '') if ch.isdigit())


def program(device, binding: dict) -> bool:
    name = (binding.get('name') or '').strip()
    number = (binding.get('number') or '').strip()
    words = name.split()
    first = words[0] if words else name
    last = words[1] if len(words) > 1 else ''
    num_digits = _digits(number)

    def elements():
        try:
            return device.elements() or []
        except Exception:
            return []

    def current_text(i):
        for e in elements():
            if e.get('index') == i:
                return _txt(e, 'text', 'hint')
        return ''

    def number_on_screen():
        if not num_digits:
            return False
        for e in elements():
            if num_digits in _digits(_txt(e, 'text', 'hint', 'description')):
                return True
        return False

    # ---------------- launch app ----------------
    device.navigate_home()
    device.open_app("Contacts")
    device.settle(1.0)

    # ---------------- create contact ----------------
    create_idx = None
    for attr in ('description', 'text'):
        for val in ("Create new contact", "Create contact", "New contact",
                    "Add contact", "Add new contact", "Create a new contact"):
            create_idx = device.find(**{attr: val})
            if create_idx is not None:
                break
        if create_idx is not None:
            break
    if create_idx is None:
        for attr in ('description', 'text'):
            for val in ("Create new contact", "Create contact", "New contact",
                        "Add contact", "Create"):
                create_idx = device.find(contains=val, clickable=True)
                if create_idx is not None:
                    break
            if create_idx is not None:
                break
    if create_idx is None:
        for e in elements():
            t = _low(e, 'text', 'description')
            if ('create' in t or 'new contact' in t or 'add contact' in t) and e.get('clickable'):
                create_idx = e.get('index')
                break
    if create_idx is None:
        raise RuntimeError("Could not find create contact button")
    device.click(index=create_idx)
    device.settle(1.2)

    # ---------------- name fields ----------------
    def wait_editable(keys, tries=10):
        for _ in range(tries):
            for k in keys:
                i = device.find(hint=k, editable=True)
                if i is not None:
                    return i
            for k in keys:
                i = device.find(text=k, editable=True)
                if i is not None:
                    return i
            for k in keys:
                i = device.find(contains=k, editable=True)
                if i is not None:
                    return i
            device.settle(0.5)
        return None

    first_idx = wait_editable(["First name"])
    if first_idx is None:
        raise RuntimeError("First name field not found")
    device.click(index=first_idx)
    device.input_text(first, index=first_idx)
    device.settle(0.4)

    if last:
        last_idx = wait_editable(["Last name"])
        if last_idx is None:
            raise RuntimeError("Last name field not found")
        device.click(index=last_idx)
        device.input_text(last, index=last_idx)
        device.settle(0.4)

    # ---------------- helpers for the phone field ----------------
    PHONE_WORDS = ('phone', 'mobile', 'tel', 'number', 'cell')
    BAD_WORDS = ('email', 'mail', 'address', 'company', 'note', 'website',
                 'birthday', 'event', 'fax', 'pager')

    def name_indexes():
        s = set()
        for e in elements():
            w = _low(e, 'hint', 'text', 'description')
            if 'first name' in w or 'last name' in w:
                s.add(e.get('index'))
                continue
            t = _txt(e, 'text').strip()
            if t and t in (first, last) and e.get('editable'):
                s.add(e.get('index'))
        return s

    def collect_candidates():
        els = elements()
        ex = name_indexes()
        pairs = []
        # 1. anything whose a11y text mentions phone
        for e in els:
            i = e.get('index')
            if i is None or i in ex:
                continue
            w = _low(e, 'hint', 'text', 'description')
            if any(k in w for k in PHONE_WORDS):
                pairs.append((0 if e.get('editable') else 1, i))
        # 2. label based: a "Phone"/"Mobile"... label, then the nearest editable after it
        for e in els:
            lab = _txt(e, 'text', 'description').strip().lower()
            if lab in ('phone', 'phone number', 'mobile', 'home', 'work',
                       'tel', 'telephone', 'cell', 'number'):
                li = e.get('index')
                if li is None:
                    continue
                sub = sorted([x for x in els
                              if x.get('editable') and x.get('index') is not None
                              and x['index'] > li and x['index'] not in ex],
                             key=lambda x: x['index'])
                if sub:
                    pairs.append((2, sub[0]['index']))
        # 3. generic editable after the names (last resort), excluding obvious non-phone
        gen = []
        for e in els:
            i = e.get('index')
            if i is None or i in ex or not e.get('editable'):
                continue
            w = _low(e, 'hint', 'text', 'description')
            if any(k in w for k in BAD_WORDS):
                continue
            gen.append(i)
        gen.sort()
        for i in gen:
            pairs.append((3, i))
        best = {}
        for r, i in pairs:
            if i not in best or r < best[i]:
                best[i] = r
        return sorted(best.items(), key=lambda kv: (kv[1], kv[0]))

    def click_phone_row():
        for t in ("Add phone number", "Add phone", "Phone number", "Phone"):
            i = device.find(text=t)
            if i is not None:
                return i
        for t in ("Add phone number", "Add phone"):
            i = device.find(contains=t)
            if i is not None:
                return i
        return None

    def type_number_into(pairs):
        for i in [idx for r, idx in pairs]:
            device.click(index=i)
            device.settle(0.3)
            device.input_text(number, index=i)
            device.settle(0.5)
            if num_digits and num_digits in _digits(current_text(i)):
                device.settle(0.5)
                return True
        return False

    # ---------------- enter phone in the editor ----------------
    pairs = collect_candidates()
    strong = [i for r, i in pairs if r <= 2]
    if not strong:
        for _ in range(5):
            device.scroll(direction='down')
            device.settle(0.5)
            pairs = collect_candidates()
            strong = [i for r, i in pairs if r <= 2]
            if strong:
                break
    if not strong:
        row = click_phone_row()
        if row is not None:
            device.click(index=row)
            device.settle(0.8)
            pairs = collect_candidates()
            strong = [i for r, i in pairs if r <= 2]

    editor_targets = strong if strong else [i for r, i in pairs if r == 3]
    if editor_targets:
        type_number_into([(0, i) for i in editor_targets])

    # ---------------- save ----------------
    def find_save():
        for t in ("Save", "Done", "Save contact"):
            i = device.find(text=t, clickable=True)
            if i is not None:
                return i
        for d in ("Save", "Done", "Save contact"):
            i = device.find(description=d, clickable=True)
            if i is not None:
                return i
        for t in ("Save", "Done"):
            i = device.find(contains=t, clickable=True)
            if i is not None:
                return i
        for attr in ('text', 'description'):
            for v in ("Save", "Done", "Save contact"):
                i = device.find(**{attr: v})
                if i is not None:
                    return i
        return None

    save_idx = find_save()
    if save_idx is None:
        for _ in range(5):
            device.scroll(direction='up')
            save_idx = find_save()
            if save_idx is not None:
                break
    if save_idx is not None:
        device.click(index=save_idx)
        device.settle(1.2)
    else:
        device.navigate_back()
        device.settle(1.2)

    # ---------------- fix-up: add phone from the contact detail screen ----------------
    if not number_on_screen():
        row = None
        for t in ("Add phone number", "Add phone", "Phone number", "Phone"):
            row = device.find(text=t)
            if row is not None:
                break
        if row is None:
            for t in ("Add phone number", "Add phone"):
                row = device.find(contains=t)
                if row is not None:
                    break
        if row is not None:
            device.click(index=row)
            device.settle(1.2)
            pairs = collect_candidates()
            strong = [i for r, i in pairs if r <= 2]
            targets = strong if strong else [i for r, i in pairs if r == 3]
            ok = False
            for i in targets:
                device.click(index=i)
                device.settle(0.3)
                device.input_text(number, index=i)
                device.settle(0.5)
                if num_digits and num_digits in _digits(current_text(i)):
                    ok = True
                    break
            if not ok:
                device.input_text(number)
                device.settle(0.5)
            save_idx = find_save()
            if save_idx is not None:
                device.click(index=save_idx)
                device.settle(1.2)
            else:
                device.navigate_back()
                device.settle(1.2)

    return True
