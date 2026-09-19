import re

PARAMS_SCHEMA = {
    'title': {'type': 'string', 'description': 'Event title', 'required': True},
    'date': {'type': 'string', 'description': 'Event date in YYYY-MM-DD format', 'required': True},
    'description': {'type': 'string', 'description': 'Event description', 'required': True},
    'location': {'type': 'string', 'description': 'Event location', 'required': True},
    'url': {'type': 'string', 'description': 'Event URL', 'required': True},
    'invitees': {'type': 'string', 'description': 'Event invitees', 'required': True},
}

def program(page, binding: dict, base_url: str) -> bool:
    MONTHS = [
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December'
    ]

    def get_current_month_year():
        headings = page.get_by_role('heading').all_text_contents()
        for h in headings:
            h = h.strip()
            for i, m in enumerate(MONTHS, start=1):
                if h.startswith(m):
                    parts = h.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        return i, int(parts[1])
        raise RuntimeError('Could not find current month heading')

    def wait_for_month(year, month):
        month_name = MONTHS[month - 1]
        page.get_by_role('heading', name=re.compile(f'^{month_name} {year}$')).first.wait_for()

    # Navigate to the home page
    page.goto(base_url + '/')

    # Click the OpenCalendar link
    page.get_by_role('link', name=re.compile('OpenCalendar')).first.click()

    # Wait for the calendar month heading to appear
    page.get_by_role('heading', name=re.compile(r'[A-Z][a-z]+ \d{4}')).first.wait_for()

    # Parse the date from the binding
    year, month, day = map(int, binding['date'].split('-'))

    # Navigate to the correct month using Prev/Next buttons
    cur_month, cur_year = get_current_month_year()
    target_month, target_year = month, year

    for _ in range(1200):
        if cur_year == target_year and cur_month == target_month:
            break
        cur_num = cur_year * 12 + cur_month
        target_num = target_year * 12 + target_month
        if target_num < cur_num:
            page.get_by_role('button', name='< Prev').click()
            if cur_month == 1:
                new_month, new_year = 12, cur_year - 1
            else:
                new_month, new_year = cur_month - 1, cur_year
        else:
            page.get_by_role('button', name='Next >').click()
            if cur_month == 12:
                new_month, new_year = 1, cur_year + 1
            else:
                new_month, new_year = cur_month + 1, cur_year
        wait_for_month(new_year, new_month)
        cur_month, cur_year = new_month, new_year
    else:
        raise RuntimeError('Could not navigate to target month')

    # Switch to Agenda view
    page.get_by_role('button', name='Agenda').click()

    # Click Add Event
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

    # Wait for the calendar page to load after creation
    page.wait_for_url(re.compile(r'.*/calendar$'))

    return True
