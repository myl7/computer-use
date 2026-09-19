import re

PARAMS_SCHEMA = {
    "title": {"type": "string", "minLength": 1},
    "date": {"type": "string", "minLength": 1},
    "description": {"type": "string", "minLength": 1},
    "location": {"type": "string", "minLength": 1},
    "url": {"type": "string", "minLength": 1},
    "invitees": {"type": "string", "minLength": 1},
}


def _find_button(page, names):
    for name in names:
        loc = page.get_by_role(
            "button",
            name=re.compile("^" + re.escape(name) + "$", re.IGNORECASE),
        )
        if loc.count() > 0:
            return loc.first
    return None


def program(page, binding: dict, base_url: str) -> bool:
    # Navigate to the calendar
    page.goto(base_url + "/calendar")

    # Click the "Create event" button
    create_button = _find_button(page, ["Create event", "New event", "Add event"])
    if create_button is None:
        raise Exception("Create event button not found")
    create_button.click()
    page.wait_for_url("**/calendar/create_event")

    # Step 1: title and date
    page.locator("[name='title']").fill(binding["title"])
    page.locator("[name='date']").fill(binding["date"])

    # Click Next
    next_button = _find_button(page, ["Next", "Next >", "Continue", "Continue >"])
    if next_button is None:
        raise Exception("Next button not found on step 1")
    next_button.click()
    page.wait_for_url("**/calendar/create_event/step2")

    # Step 2: description and location
    page.locator("[name='description']").fill(binding["description"])
    page.locator("[name='location']").fill(binding["location"])

    # Click Next
    next_button = _find_button(page, ["Next", "Next >", "Continue", "Continue >"])
    if next_button is None:
        raise Exception("Next button not found on step 2")
    next_button.click()
    page.wait_for_url("**/calendar/create_event/step3")

    # Step 3: url and invitees
    page.locator("[name='url']").fill(binding["url"])
    page.locator("[name='invitees']").fill(binding["invitees"])

    # Click Submit
    submit_button = _find_button(page, ["Create", "Save", "Submit", "Finish", "Done"])
    if submit_button is None:
        raise Exception("Submit button not found on step 3")
    submit_button.click()
    page.wait_for_url("**/calendar")

    return True
