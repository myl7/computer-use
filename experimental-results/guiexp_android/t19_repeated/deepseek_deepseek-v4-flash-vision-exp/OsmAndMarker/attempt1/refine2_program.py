def program(device, binding: dict) -> bool:
    location = binding['location']

    def find_search_field():
        for _ in range(5):
            for el in device.elements():
                if el.get('editable'):
                    return el['index']
            device.settle(1)
        return None

    def find_search_button():
        for criteria in (
            {"description": "Search", "clickable": True},
            {"text": "Search", "clickable": True},
            {"description": "Search button", "clickable": True},
            {"text": "Search button", "clickable": True},
        ):
            idx = device.find(**criteria)
            if idx is not None:
                return idx
        for el in device.elements():
            if el.get('clickable') and not el.get('editable'):
                text = (el.get('text') or '').lower()
                desc = (el.get('description') or '').lower()
                hint = (el.get('hint') or '').lower()
                if 'search' in text or 'search' in desc or 'search' in hint:
                    return el['index']
        return None

    def find_first_result():
        for el in device.elements():
            if el.get('editable'):
                continue
            if not el.get('clickable'):
                continue
            text = (el.get('text') or '').strip()
            desc = (el.get('description') or '').strip()
            if not text and not desc:
                continue
            lower_text = text.lower()
            lower_desc = desc.lower()
            if any(k in lower_text or k in lower_desc for k in [
                'search', 'clear', 'back', 'menu', 'more', 'close',
                'cancel', 'show on map', 'directions', 'favorite', 'marker'
            ]):
                continue
            return el['index']
        return None

    def find_marker():
        for el in device.elements():
            text = (el.get('text') or '').lower()
            desc = (el.get('description') or '').lower()
            if 'marker' in text or 'marker' in desc:
                if el.get('clickable'):
                    return el['index']
        for el in device.elements():
            text = (el.get('text') or '').lower()
            desc = (el.get('description') or '').lower()
            if 'marker' in text or 'marker' in desc:
                return el['index']
        return None

    def is_place_panel_open():
        for el in device.elements():
            text = (el.get('text') or '').upper()
            desc = (el.get('description') or '').upper()
            if 'SHOW ON MAP' in text or 'SHOW ON MAP' in desc:
                return True
            if 'MARKER' in text or 'MARKER' in desc:
                return True
        return False

    device.open_app("OsmAnd")
    device.settle(2)

    search_field = find_search_field()
    if search_field is None:
        search_btn = find_search_button()
        if search_btn is None:
            raise RuntimeError("Search button not found")
        device.click(index=search_btn)
        device.settle(1)
        search_field = find_search_field()
    if search_field is None:
        raise RuntimeError("Search field not found")

    device.input_text(location, index=search_field)
    device.settle(1)

    if is_place_panel_open():
        marker = find_marker()
        if marker is not None:
            device.click(index=marker)
            device.settle(1)
            return True

    result = find_first_result()
    if result is None:
        device.keyboard_enter()
        device.settle(2)
        if is_place_panel_open():
            marker = find_marker()
            if marker is not None:
                device.click(index=marker)
                device.settle(1)
                return True
        result = find_first_result()

    if result is None:
        for _ in range(5):
            device.settle(1)
            if is_place_panel_open():
                marker = find_marker()
                if marker is not None:
                    device.click(index=marker)
                    device.settle(1)
                    return True
            result = find_first_result()
            if result is not None:
                break

    if result is None:
        raise RuntimeError("Search result not found")

    device.click(index=result)
    device.settle(2)

    marker = None
    for _ in range(10):
        marker = find_marker()
        if marker is not None:
            break
        device.settle(1)

    if marker is None:
        device.scroll(direction='down')
        device.settle(1)
        for _ in range(5):
            marker = find_marker()
            if marker is not None:
                break
            device.settle(1)

    if marker is None:
        raise RuntimeError("MARKER button not found")

    device.click(index=marker)
    device.settle(1)

    return True


PARAMS_SCHEMA = {
    "location": {
        "type": "string",
        "description": "Place name or 'lat, lon' coordinate pair to mark in OsmAnd"
    }
}
