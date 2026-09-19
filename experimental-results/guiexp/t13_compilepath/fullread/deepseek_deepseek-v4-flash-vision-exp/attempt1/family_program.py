import re
from datetime import datetime

PARAMS_SCHEMA = {
    "title": {"type": "string", "description": "Event title"},
    "date": {"type": "string", "description": "Event date in YYYY-MM-DD format"},
    "description": {"type": "string", "description": "Event description"},
    "location": {"type": "string", "description": "Event location"},
    "url": {"type": "string", "description": "Event URL"},
    "invitees": {"type": "string", "description": "Event invitees"},
}

def program(page, binding: dict, base_url: str) -> bool:
    MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']

    def get_current_month_year():
        headings = page.get_by_role('heading').all()
        for h in headings:
            text = h.inner_text().strip()
            m = re.match(r'([A-Za-z]+) (\d{4})', text)
            if m:
                month_name = m.group(1)
                year = int(m.group(2))
                if month_name in MONTHS:
                    return year, MONTHS.index(month_name) + 1
        raise Exception('Could not find current month heading')

    # Navigate to home and open calendar
    page.goto(base_url + '/')
    page.get_by_role('link', name=re.compile('OpenCalendar')).click()
    page.wait_for_load_state('networkidle')

    # Parse target date
    target_date = datetime.strptime(binding['date'], '%Y-%m-%d')
    target_year = target_date.year
    target_month = target_date.month

    # Navigate to the target month
    current_year, current_month = get_current_month_year()
    diff = (target_year - current_year) * 12 + (target_month - current_month)
    if diff < 0:
        for _ in range(-diff):
            page.get_by_role('button', name='< Prev').click()
    elif diff > 0:
        for _ in range(diff):
            page.get_by_role('button', name='Next >').click()

    # Switch to Agenda view and open the create event form
    page.get_by_role('button', name='Agenda').click()
    page.get_by_role('button', name='Add Event').click()

    # Step 1: Title and Date
    page.get_by_role('textbox', name='Event title').fill(binding['title'])
    page.get_by_role('textbox', name='Event date, YYYY-MM-DD').fill(binding['date'])
    page.get_by_role('button', name='Next').click()

    # Step 2: Description and Location
    page.get_by_role('textbox', name='Event description').fill(binding['description'])
    page.get_by_role('textbox', name='Event location').fill(binding['location'])
    page.get_by_role('button', name='Next').click()

    # Step 3: URL and Invitees
    page.get_by_role('textbox', name='Event URL').fill(binding['url'])
    page.get_by_role('textbox', name='Event invitees').fill(binding['invitees'])
    page.get_by_role('button', name='Create').click()

    page.wait_for_load_state('networkidle')
    return True
