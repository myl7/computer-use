import re

PARAMS_SCHEMA = {
    "title": {"type": "string", "description": "Event title", "required": True},
    "date": {"type": "string", "description": "Event date", "required": True},
    "description": {"type": "string", "description": "Event description", "required": True},
    "location": {"type": "string", "description": "Event location", "required": True},
    "url": {"type": "string", "description": "Event URL", "required": True},
    "invitees": {"type": "string", "description": "Event invitees", "required": True},
}

def program(page, binding: dict, base_url: str) -> bool:
    def click_button(patterns):
        for pattern in patterns:
            locator = page.get_by_role("button", name=re.compile(pattern, re.I))
            if locator.count() > 0:
                locator.first.click()
                return
        raise Exception(f"No button found matching {patterns}")

    # Navigate to the create event form
    page.goto(base_url.rstrip("/") + "/calendar/create_event")

    # Step 1: title and date
    page.get_by_role("textbox", name="title").fill(binding["title"])
    page.get_by_role("textbox", name="date").fill(binding["date"])

    # Go to step 2
    click_button([r"next", r"continue", r"proceed"])
    page.wait_for_url("**/step2")

    # Step 2: description and location
    page.get_by_role("textbox", name="description").fill(binding["description"])
    page.get_by_role("textbox", name="location").fill(binding["location"])

    # Go to step 3
    click_button([r"next", r"continue", r"proceed"])
    page.wait_for_url("**/step3")

    # Step 3: url and invitees
    page.get_by_role("textbox", name="url").fill(binding["url"])
    page.get_by_role("textbox", name="invitees").fill(binding["invitees"])

    # Submit the event
    click_button([r"create", r"save", r"submit", r"add event", r"confirm", r"done"])
    page.wait_for_url("**/calendar")

    return True
