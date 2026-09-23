import re

PARAMS_SCHEMA = {
    "title": {"type": "string", "description": "Event title"},
    "date": {"type": "string", "description": "Event date in YYYY-MM-DD format"},
    "description": {"type": "string", "description": "Event description"},
    "url": {"type": "string", "description": "Event URL"},
    "invitees": {"type": "string", "description": "Event invitees"},
    "location": {"type": "string", "description": "Event location"},
}


def program(page, binding: dict, base_url: str) -> bool:
    title = binding["title"]
    date = binding["date"]
    description = binding["description"]
    url = binding["url"]
    invitees = binding["invitees"]
    location = binding["location"]

    page.goto(base_url + "/calendar")
    page.wait_for_load_state("networkidle")

    # Open the create-event form via the "Add Event" button.
    add_btn = page.get_by_role("button", name="Add Event")
    if add_btn.count() == 0:
        # May need scrolling to become visible/clickable.
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(300)
        add_btn = page.get_by_role("button", name="Add Event")
    add_btn.first.click()
    page.wait_for_load_state("networkidle")

    # Fill the top part of the form.
    title_box = page.get_by_role("textbox", name="Event title")
    title_box.wait_for(state="visible")
    title_box.fill(title)

    date_box = page.get_by_role("textbox", name=re.compile("Event date"))
    date_box.fill(date)

    desc_box = page.get_by_role("textbox", name="Event description")
    desc_box.fill(description)

    url_box = page.get_by_role("textbox", name="Event URL")
    url_box.fill(url)

    # Scroll down to reveal the lower part of the form.
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(300)

    invitees_box = page.get_by_role("textbox", name="Event invitees")
    invitees_box.wait_for(state="visible")
    invitees_box.fill(invitees)

    location_box = page.get_by_role("textbox", name="Event location")
    location_box.fill(location)

    # Submit button label varies between builds ("Submit" / "Save event").
    submit_btn = page.get_by_role("button", name=re.compile(r"^(Submit|Save event)$"))
    if submit_btn.count() == 0:
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(300)
        submit_btn = page.get_by_role("button", name=re.compile(r"^(Submit|Save event)$"))
    submit_btn.first.click()

    # Wait until we are back on the calendar view.
    page.wait_for_load_state("networkidle")
    page.get_by_role("heading", name="OpenCalendar").wait_for(state="visible")

    return True