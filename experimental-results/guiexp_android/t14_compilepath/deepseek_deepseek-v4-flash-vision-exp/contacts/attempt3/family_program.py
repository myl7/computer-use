import re

PARAMS_SCHEMA = {
    "name": {
        "type": "string",
        "description": "Full contact name, exactly two words (first and last)",
        "required": True
    },
    "number": {
        "type": "string",
        "description": "Phone number, one string copied verbatim",
        "required": True
    }
}


def program(device, binding: dict) -> bool:
    # Step 1: Ensure we start from the dialer/contacts app context.
    # The recorded trajectory begins with an implicit "click on index 2" that
    # results in the dialer activity. This is likely a launcher shortcut or
    # the dialer app's "Contacts" tab. We'll explicitly open the Google
    # Dialer app to align with the recorded flow.
    device.open_app("Google Dialer")
    device.wait()
    
    # Step 2: Click on the element that opens the Contact Editor.
    # In the recorded trajectory, index 1 after settling in the dialer opens
    # the contact editor. This is likely the "Contacts" tab or a floating
    # action button. We'll search for a clickable element with hint/description
    # that suggests creating a new contact, e.g., "Create new contact".
    # If not found, fall back to clicking index 1 (as recorded).
    editor_idx = None
    elements = device.elements()
    # Look for an element that typically opens the contact editor.
    for el in elements:
        text = (el.get("text") or "").lower()
        hint = (el.get("hint") or "").lower()
        desc = (el.get("description") or "").lower()
        if "create" in text or "new contact" in text or "add contact" in text:
            editor_idx = el["index"]
            break
    if editor_idx is None:
        # Fallback to recorded index 1
        editor_idx = 1
    device.click(index=editor_idx)
    device.wait()
    
    # Now we should be in the ContactEditorActivity.
    # Step 3: Click on the 'First name' field.
    # Recorded index 7 is the first name field. But we should re-locate it.
    first_name_idx = device.find(hint="First name")
    if first_name_idx is None:
        # Try description or text
        elements = device.elements()
        for el in elements:
            if "first name" in (el.get("hint") or "").lower() or \
               "first name" in (el.get("description") or "").lower():
                first_name_idx = el["index"]
                break
    if first_name_idx is None:
        # Fallback to recorded index 7
        first_name_idx = 7
    device.click(index=first_name_idx)
    device.wait()
    
    # Step 4: Type first word of name
    first_word = binding["name"].split()[0]
    device.input_text(text=first_word, index=first_name_idx)
    device.wait()
    
    # Step 5: Click on the 'Last name' field.
    last_name_idx = device.find(hint="Last name")
    if last_name_idx is None:
        elements = device.elements()
        for el in elements:
            if "last name" in (el.get("hint") or "").lower() or \
               "last name" in (el.get("description") or "").lower():
                last_name_idx = el["index"]
                break
    if last_name_idx is None:
        # Fallback to recorded index 8
        last_name_idx = 8
    device.click(index=last_name_idx)
    device.wait()
    
    # Step 6: Type second word of name
    second_word = binding["name"].split()[1]
    device.input_text(text=second_word, index=last_name_idx)
    device.wait()
    
    # Step 7: Scroll down to reveal the Phone field.
    device.scroll(direction="down")
    device.wait()
    
    # Step 8: Type number into the Phone field.
    # Recorded index 5 is the phone field. Re-locate it.
    phone_idx = device.find(hint="Phone")
    if phone_idx is None:
        elements = device.elements()
        for el in elements:
            if "phone" in (el.get("hint") or "").lower() or \
               "phone" in (el.get("description") or "").lower() or \
               "phone" in (el.get("text") or "").lower():
                phone_idx = el["index"]
                break
    if phone_idx is None:
        # Fallback to recorded index 5
        phone_idx = 5
    device.input_text(text=binding["number"], index=phone_idx)
    device.wait()
    
    # Step 9: Click the "Save" button (back or save). Recorded index 2 returns
    # to the dialer activity, meaning it saved the contact. This is typically
    # the "Save" button in the toolbar or the back arrow which auto-saves.
    # Re-locate a save button if possible.
    save_idx = None
    elements = device.elements()
    for el in elements:
        text = (el.get("text") or "").lower()
        desc = (el.get("description") or "").lower()
        if "save" in text or "save" in desc:
            save_idx = el["index"]
            break
    if save_idx is None:
        # Fallback to recorded index 2 (likely the save/back button)
        save_idx = 2
    device.click(index=save_idx)
    device.wait()
    
    # Verify we are back to the dialer activity (optional but good).
    # The flow is complete.
    return True
