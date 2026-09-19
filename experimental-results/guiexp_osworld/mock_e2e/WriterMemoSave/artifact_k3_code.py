import time

def program(device, binding):
    doc = device.find(role="paragraph")
    if doc is None:
        doc = device.find(role="document-text")
    if doc is None:
        raise ValueError("no document paragraph to type into")
    device.type_at_caret(binding["title"])
    time.sleep(0.4)
    device.press("enter")   # end of line 1
    device.press("enter")   # the empty second line
    device.type_at_caret(binding["body"])
    time.sleep(0.4)
    device.hotkey("ctrl", "s")
    time.sleep(2.0)
    place = device.find(contains="Desktop", role="label")
    if place is None:
        raise ValueError("Desktop place not found in save dialog")
    device.click(index=place)
    time.sleep(1.0)
    field = device.find(role="text", contains="Untitled")
    if field is None:
        raise ValueError("save-dialog Name entry not found")
    device.input_text(binding["file_name"], index=field)
    time.sleep(0.6)
    device.press("enter")
    time.sleep(2.5)
    return True
