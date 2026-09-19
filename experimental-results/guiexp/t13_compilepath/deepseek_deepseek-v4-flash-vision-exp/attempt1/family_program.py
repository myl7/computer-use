import re

PARAMS_SCHEMA = {
    "title": {"type": "string", "required": True, "description": "Event title"},
    "date": {"type": "string", "required": True, "description": "Event date"},
    "description": {"type": "string", "required": True, "description": "Event description"},
    "location": {"type": "string", "required": True, "description": "Event location"},
    "url": {"type": "string", "required": True, "description": "Event URL"},
    "invitees": {"type": "string", "required": True, "description": "Event invitees"},
}

def program(page, binding: dict, base_url: str) -> bool:
    page.goto(base_url + "/calendar")

    # Click the "Create event" button
    create_btn = page.get_by_role("button", name=re.compile("create event|new event|add event", re.I))
    create_btn.first.click()
    page.wait_for_url(re.compile(r"/calendar/create_event$"))

    # Step 1: title and date
    page.locator("[name='title']:visible").first.fill(binding["title"])
    page.locator("[name='date']:visible").first.fill(binding["date"])

    # Go to step 2
    next_btn = page.get_by_role("button", name=re.compile("next|continue", re.I))
    next_btn.first.click()
    page.wait_for_url(re.compile(r"/calendar/create_event/step2$"))

    # Step 2: description and location
    page.locator("[name='description']:visible").first.fill(binding["description"])
    page.locator("[name='location']:visible").first.fill(binding["location"])

    # Go to step 3
    next_btn = page.get_by_role("button", name=re.compile("next|continue", re.I))
    next_btn.first.click()
    page.wait_for_url(re.compile(r"/calendar/create_event/step3$"))

    # Step 3: url and invitees
    page.locator("[name='url']:visible").first.fill(binding["url"])
    page.locator("[name='invitees']:visible").first.fill(binding["invitees"])

    # Submit the event
    submit_btn = page.get_by_role("button", name=re.compile("submit|create|save", re.I))
    submit_btn.first.click()
    page.wait_for_url(re.compile(r"/calendar$"))

    return True
