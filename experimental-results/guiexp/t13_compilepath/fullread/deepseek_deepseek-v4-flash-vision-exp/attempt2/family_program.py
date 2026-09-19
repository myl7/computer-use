import re

PARAMS_SCHEMA = {
    "title": {"type": "string", "description": "Event title", "required": True},
    "date": {"type": "string", "description": "Event date in YYYY-MM-DD format", "required": True},
    "description": {"type": "string", "description": "Event description", "required": True},
    "location": {"type": "string", "description": "Event location", "required": True},
    "url": {"type": "string", "description": "Event URL", "required": True},
    "invitees": {"type": "string", "description": "Event invitees", "required": True},
}

def program(page, binding: dict, base_url: str) -> bool:
    # Navigate to the calendar app
    page.goto(base_url + "/calendar")

    # Parse target date
    year_str, month_str, _ = binding["date"].split("-")
    target_year = int(year_str)
    target_month = int(month_str)

    MONTHS = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    # Locate the month heading (e.g., "September 2026")
    month_heading = page.get_by_role("heading").filter(
        has_text=re.compile(r"^(January|February|March|April|May|June|July|August|September|October|November|December) \d{4}$")
    ).first

    # Navigate to the correct month/year
    while True:
        text = month_heading.inner_text().strip()
        parts = text.split()
        current_month_name = parts[0]
        current_year = int(parts[1])
        current_month = MONTHS.index(current_month_name) + 1

        if current_year == target_year and current_month == target_month:
            break

        if current_year > target_year or (current_year == target_year and current_month > target_month):
            page.get_by_role("button", name="< Prev").click()
        else:
            page.get_by_role("button", name="Next >").click()

        page.wait_for_timeout(200)

    # Switch to Agenda view to reveal the "Add Event" button
    page.get_by_role("button", name="Agenda").click()
    page.get_by_role("button", name="Add Event").click()

    # Step 1: Title and Date
    page.get_by_role("textbox", name="Event title").fill(binding["title"])
    page.get_by_role("textbox", name="Event date, YYYY-MM-DD").fill(binding["date"])
    page.get_by_role("button", name="Next").click()

    # Step 2: Description and Location
    page.get_by_role("textbox", name="Event description").fill(binding["description"])
    page.get_by_role("textbox", name="Event location").fill(binding["location"])
    page.get_by_role("button", name="Next").click()

    # Step 3: URL and Invitees
    page.get_by_role("textbox", name="Event URL").fill(binding["url"])
    page.get_by_role("textbox", name="Event invitees").fill(binding["invitees"])
    page.get_by_role("button", name="Create").click()

    return True
