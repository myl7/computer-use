PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place to save: either a place name such as 'Schaan, Liechtenstein' or a 'lat, lon' coordinate pair",
        "required": True
    }
}

def program(device, binding: dict) -> bool:
    def find_first_result(location):
        # Try exact text match on a clickable, non-editable element
        idx = device.find(text=location, clickable=True, editable=False)
        if idx is not None:
            return idx
        # Try contains
        idx = device.find(contains=location, clickable=True, editable=False)
        if idx is not None:
            return idx
        # Fallback: scan elements, find first clickable after search field
        elements = device.elements()
        search_field_idx = None
        for e in elements:
            if e.get('editable') and (e.get('hint') or '').lower() == 'type to search all':
                search_field_idx = e['index']
                break
        if search_field_idx is None:
            search_field_idx = device.find(hint='Type to search all', editable=True)
        if search_field_idx is None:
            raise RuntimeError("Search field not found for result lookup")
        for e in elements:
            if e['index'] > search_field_idx and e.get('clickable') and not e.get('editable'):
                text = (e.get('text') or '').strip()
                desc = (e.get('description') or '').strip()
                if text.upper() == 'SHOW ON MAP' or desc.upper() == 'SHOW ON MAP':
                    continue
                if text in ['✕', 'X', 'Clear'] or desc in ['Clear', 'Clear text']:
                    continue
                return e['index']
        raise RuntimeError("First search result not found")

    def find_favorite_button():
        for kwargs in [
            {'description': 'Favorite'},
            {'text': 'Favorite'},
            {'description': 'Add to favorites'},
            {'text': 'Add to favorites'},
            {'description': 'favorite'},
            {'text': 'favorite'},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        elements = device.elements()
        for e in elements:
            if e.get('clickable'):
                combined = ((e.get('text') or '') + ' ' + (e.get('description') or '')).lower()
                if 'favorite' in combined:
                    return e['index']
        raise RuntimeError("Favorite button not found")

    def find_ok_button():
        for kwargs in [
            {'text': 'OK'},
            {'text': 'Save'},
            {'text': 'Add'},
            {'description': 'OK'},
            {'description': 'Save'},
        ]:
            idx = device.find(**kwargs)
            if idx is not None:
                return idx
        elements = device.elements()
        for e in elements:
            if e.get('clickable'):
                text = (e.get('text') or '').strip()
                if text.upper() in ['OK', 'SAVE', 'ADD', 'CONFIRM']:
                    return e['index']
        raise RuntimeError("OK/Save button not found")

    # Open OsmAnd
    device.open_app("OsmAnd")

    # Click Search button
    search_btn = device.find(description='Search')
    if search_btn is None:
        search_btn = device.find(text='Search')
    if search_btn is None:
        raise RuntimeError("Search button not found")
    device.click(index=search_btn)

    # Find search field
    search_field = device.find(hint='Type to search all', editable=True)
    if search_field is None:
        elements = device.elements()
        for e in elements:
            if e.get('editable') and 'search' in (e.get('hint') or '').lower():
                search_field = e['index']
                break
    if search_field is None:
        raise RuntimeError("Search field not found")

    # Input location
    device.input_text(binding['location'], index=search_field)
    device.settle(1)

    # Click first search result
    result = find_first_result(binding['location'])
    device.click(index=result)
    device.settle(1)

    # Click favorite button
    fav_btn = find_favorite_button()
    device.click(index=fav_btn)
    device.settle(1)

    # Accept the name in dialog
    ok_btn = find_ok_button()
    device.click(index=ok_btn)
    device.settle(1)

    return True
