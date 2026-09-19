"""Build the library-manifest experiment artifacts.

Writes, under this directory:
  - manifest_entries.json : the 100 canonical entries (5 real OpenApps
    programs + 95 synthetic-but-realistic ones), in canonical order
    (the n=100 order, calendar-wizard late-middle at position 65).
  - manifest_n{0,1,5,20,100}.txt : plain-text manifest blocks actually
    pasted into prompts.
  - manifest_stats.json : per-entry and per-n tiktoken token counts.

Entry format (one line per program):
  PROGRAM <name> — use when <family/scenario trigger>; parameters: <names (types)>.

Subset policy (fixed, documented):
  - n=1  : [openapps_calendar_create_event_wizard] at position 1 ("early").
  - n=5  : the 5 REAL programs as
           [single_page, todo, wizard, messages, sectioned]; wizard at
           position 3 ("middle").
  - n=20 : 19 synthetic entries drawn with random.Random(20260905).sample
           from the 95 synthetic entries, force-including the three
           synthetic routing-match targets (flights_book_roundtrip,
           email_send_with_attachment, spreadsheet_append_row; three
           drawn entries are swapped out for them when missing), plus the
           wizard inserted at position 10 (middle; unspecified by protocol).
  - n=100: all entries; wizard at position 65 ("late-middle").
Token counts: tiktoken o200k_base (GLM's actual tokenizer differs; API
usage.prompt_tokens deltas are the ground-truth m, tiktoken is the
design-time estimate).
"""

from __future__ import annotations

import json
import random
import zlib
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("o200k_base")
HERE = Path(__file__).resolve().parent

WIZARD = "openapps_calendar_create_event_wizard"
SINGLE = "openapps_calendar_create_event_single_page"
SECTIONED = "openapps_calendar_create_event_sectioned"
TODO = "openapps_todo_add_item"
MESSAGES = "openapps_messages_send"

# (name, "use when ..." trigger, "parameters" text)
SYNTHETIC: list[tuple[str, str, str]] = [
    ("flights_book_roundtrip",
     "the user asks to book a round-trip flight: both outbound and return legs on an airline site, "
     "the chosen cabin; the program completes the purchase through to the confirmation page",
     "origin (IATA/airport string), destination (IATA/airport string), depart_date (YYYY-MM-DD), "
     "return_date (YYYY-MM-DD), cabin (economy|business), passengers (int)"),
    ("flights_book_oneway",
     "a one-way flight reservation is wanted: single leg, no return, the program picks the matching "
     "departure from the search results and completes checkout on the travel site end to end",
     "origin (airport string), destination (airport string), depart_date (YYYY-MM-DD), "
     "cabin (economy|business), passengers (int)"),
    ("flights_select_seat",
     "an existing airline reservation needs seat assignments changed or added: open the booking, "
     "open the seat map for a leg, and pick specific seats such as a window or aisle",
     "confirmation_code (string), leg (outbound|return), seat_preference (window|aisle|string), "
     "seat_count (int)"),
    ("hotel_book_room",
     "the goal is reserving a hotel room for a stay: search a booking site by city and dates, choose "
     "a room type from the listings, and complete the reservation through payment confirmation",
     "city (string), checkin_date (YYYY-MM-DD), checkout_date (YYYY-MM-DD), guests (int), "
     "room_type (string)"),
    ("hotel_request_late_checkout",
     "the user already has a hotel stay booked and wants a late checkout added to it: locate the "
     "reservation in the hotel portal and submit the late-departure request form",
     "confirmation_number (string), requested_checkout_time (HH:MM string)"),
    ("rental_car_reserve",
     "a rental car is needed at a destination: search the agency site by pickup location and times, "
     "select a vehicle class from the results, and reserve it with the driver details on file",
     "pickup_location (string), pickup_datetime (YYYY-MM-DD HH:MM), dropoff_datetime (YYYY-MM-DD HH:MM), "
     "vehicle_class (string)"),
    ("train_book_ticket",
     "intercity or regional train travel: look up rail connections between two stations for a travel "
     "day, pick a departure, and buy the ticket on the rail operator site",
     "from_station (string), to_station (string), travel_date (YYYY-MM-DD), passengers (int), "
     "class (1st|2nd)"),
    ("restaurant_reserve_table",
     "the user wants a restaurant table: search a dining-reservation service by restaurant name and "
     "party size, choose an available time slot, and book it under the account name",
     "restaurant (string), date (YYYY-MM-DD), time (HH:MM), party_size (int), occasion (string, optional)"),
    ("restaurant_cancel_reservation",
     "an existing dining reservation must be called off: find it in the reservation service under the "
     "account and cancel it so the slot is released",
     "restaurant (string), date (YYYY-MM-DD), confirmation_ref (string)"),
    ("restaurant_update_party_size",
     "a booked table needs more or fewer seats: open the existing dining reservation and change the "
     "guest count, keeping the same date and time",
     "confirmation_ref (string), new_party_size (int)"),
    ("email_send_with_attachment",
     "an email must go out with a file attached: compose a new message in the mail app, fill in the "
     "recipient and subject, write the body, attach the given document, and send it",
     "to (address string), subject (string), body (string), attachment_path (file name string)"),
    ("email_draft_new_message",
     "the user wants a fresh email composed but kept as a draft for review rather than sent: create "
     "the message, write subject and body, and save it to Drafts without delivering",
     "to (address string), subject (string), body (string)"),
    ("email_reply_to_thread",
     "a reply is needed on an existing conversation: open the thread matching the subject or sender, "
     "write the reply text, and send it keeping the thread context",
     "thread_subject (string), reply_body (string), reply_all (bool)"),
    ("email_forward_message",
     "an existing received email should be passed along: open the message and forward it verbatim, "
     "optionally with a short note, to another address",
     "message_subject (string), forward_to (address string), note (string, optional)"),
    ("email_search_inbox",
     "the user needs specific messages found in the mail account: run a search over inbox and labels "
     "by sender, subject keyword, or date range, and report the matching messages",
     "query (string), sender (address string, optional), since_date (YYYY-MM-DD, optional)"),
    ("email_archive_from_sender",
     "the mailbox is being tidied: gather messages from one sender currently in the inbox and archive "
     "them so the inbox holds only what still needs attention",
     "sender (address string), also_future (bool)"),
    ("email_set_vacation_autoreply",
     "an out-of-office period starts: configure the mail app's vacation responder with first and last "
     "day and the automatic reply text, and enable it",
     "start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), reply_text (string)"),
    ("spreadsheet_append_row",
     "new data belongs at the bottom of a sheet: open the workbook, append one row with the supplied "
     "values in column order after the last used row, and save; fits 'log this entry' style goals",
     "workbook (file name string), sheet (string), values (list of cell values)"),
    ("spreadsheet_create_new_sheet",
     "a fresh spreadsheet is needed with headers set up: create the workbook, name the first sheet, "
     "write the header row, and save the empty but structured file",
     "file_name (string), sheet_name (string), headers (list of strings)"),
    ("spreadsheet_sort_range",
     "a data range must be reordered: select the range in the sheet and sort it by a chosen column, "
     "ascending or descending, then save",
     "sheet (string), range (A1-style string), sort_column (letter or header string), "
     "order (asc|desc)"),
    ("spreadsheet_apply_column_formula",
     "a computed column is wanted: fill a formula down an entire column for every existing data row, "
     "such as multiplying price by quantity into a total column",
     "sheet (string), column (letter), formula (spreadsheet formula string)"),
    ("spreadsheet_export_csv",
     "the sheet data must leave the app: export a chosen sheet as a CSV download with current values "
     "and save it under the requested file name",
     "workbook (file name string), sheet (string), output_name (file name string)"),
    ("spreadsheet_freeze_header_row",
     "long sheets scroll off their headers: freeze the first row (and optionally the first column) of "
     "the sheet so labels stay visible while scrolling",
     "sheet (string), freeze_first_column (bool)"),
    ("spreadsheet_delete_blank_rows",
     "a sheet has stray empty rows from imports: find and remove fully blank rows within a range so "
     "the data block is contiguous, then save",
     "sheet (string), range (A1-style string)"),
    ("docs_create_document",
     "a new text document is needed: create it in the docs app from blank or a named template, set "
     "the title, and leave it ready for content",
     "title (string), template (string, optional)"),
    ("docs_insert_table",
     "tabular content belongs inside a document: insert a table with the requested rows and columns "
     "at the end (or after a heading) and optionally fill the header cells",
     "document (string), rows (int), columns (int), header_cells (list of strings, optional)"),
    ("docs_add_table_of_contents",
     "a long document needs navigation: insert an auto-generated, clickable table of contents at "
     "the top of the document, built from its existing heading structure and refreshed on save",
     "document (string)"),
    ("docs_export_pdf",
     "a document must be shared as a fixed-layout file: export the doc as a PDF download saved under "
     "the requested name, without altering the source",
     "document (string), output_name (file name string)"),
    ("docs_apply_heading_styles",
     "a document is a wall of plain paragraphs: apply heading levels to the specified lines so the "
     "outline and table of contents work",
     "document (string), headings (list of 'text -> level' pairs)"),
    ("forms_fill_insurance_claim",
     "an insurance claim form must be completed on the provider portal: open the claim form, fill "
     "policy holder fields, incident details, and amounts, and submit for review",
     "policy_number (string), incident_date (YYYY-MM-DD), incident_description (string), "
     "amount_claimed (number)"),
    ("forms_submit_job_application",
     "applying to a posting on a careers site: fill the applicant profile fields, cover-letter text "
     "box, and screening questions, attach the resume, and submit the application",
     "job_posting_url (URL string), full_name (string), cover_letter (string), resume_path (file name string)"),
    ("forms_complete_survey",
     "a survey or questionnaire must be answered: step through every page of the form choosing the "
     "requested answers and free-text responses, and submit at the end",
     "form_url (URL string), answers (mapping of question -> answer)"),
    ("forms_upload_supporting_docs",
     "a pending form or case needs evidence attached: upload one or more documents to the case page, "
     "label each, and confirm the upload",
     "case_ref (string), files (list of file name strings), labels (list of strings)"),
    ("forms_sign_and_submit_waiver",
     "a liability or participation waiver needs signing and sending: open the waiver, type the "
     "participant name into the signature field, check the agreement box, and submit",
     "waiver_url (URL string), participant_name (string), date_signed (YYYY-MM-DD)"),
    ("settings_enable_dark_mode",
     "the interface should switch to a dark theme: open the system or app appearance settings page "
     "and turn dark mode on for the requested scope, leaving every other setting untouched",
     "scope (device|app name string)"),
    ("settings_change_language",
     "the user wants the software displayed in another language: change the display language in the "
     "settings panel and confirm through the reload prompt so menus render in the new language",
     "language (BCP-47 or name string)"),
    ("settings_adjust_notification_prefs",
     "notifications are too noisy or missing: open notification settings and toggle the requested "
     "channels (email, push, digest) on or off for the named product areas",
     "channels (list of email|push|sms), areas (list of strings), enabled (bool)"),
    ("settings_set_timezone",
     "times display in the wrong zone: set the account or device timezone in settings so meeting "
     "schedules, logs, and timestamps render in the user's local time",
     "timezone (IANA name string)"),
    ("settings_manage_connected_accounts",
     "third-party service connections need review: open the connected-accounts settings page and "
     "link a new service or revoke an existing one as requested",
     "action (link|revoke), service (string)"),
    ("settings_update_billing_address",
     "invoices carry an outdated address: open billing settings and replace the street, city, and "
     "postal fields with the supplied values, then save",
     "street (string), city (string), postal_code (string), country (string)"),
    ("search_web_and_summarize",
     "the user wants an answer from the open web: run a web search on the query, open the most "
     "relevant results, and return a short cited summary of what they say",
     "query (string), max_results (int), focus_sites (list of domains, optional)"),
    ("search_compare_product_prices",
     "a purchase decision needs prices compared: search shopping sites for the exact product model, "
     "collect unit prices from several retailers, and report the cheapest with links",
     "product (string), model (string, optional), retailers (list of site names, optional)"),
    ("search_find_nearby_stores",
     "the user needs a physical location: search the maps or store-locator service near an address "
     "and report the closest open branches with hours",
     "chain (string), near_address (string), max_results (int)"),
    ("crm_create_contact",
     "a new person enters the CRM: create a contact record with name, employer, email, and phone in "
     "the sales tool, filling every standard field",
     "full_name (string), company (string), email (address string), phone (string)"),
    ("crm_log_call_note",
     "a call with a customer just finished: open the contact or deal in the CRM and append a dated "
     "note summarizing what was discussed and the agreed next step",
     "contact_name (string), call_date (YYYY-MM-DD), summary (string), next_step (string)"),
    ("crm_update_deal_stage",
     "an opportunity moved forward: open the deal in the CRM and drag it to the new pipeline stage, "
     "updating amount or close date when supplied",
     "deal_name (string), new_stage (string), amount (number, optional), close_date (YYYY-MM-DD, optional)"),
    ("crm_merge_duplicate_contacts",
     "two CRM records describe the same person: merge the named duplicate into the primary record, "
     "keeping the richer field values from either side and carrying over the activity history",
     "primary (string), duplicate (string)"),
    ("invoice_create_draft",
     "billable work needs invoicing: create a draft invoice for a client with line items, quantities, "
     "and rates, saved but not yet sent",
     "client (string), line_items (list of 'description qty rate'), due_days (int)"),
    ("invoice_send_to_client",
     "an existing draft invoice should go out: open it in the invoicing app, verify the total, and "
     "email the invoice to the client contact on file",
     "invoice_number (string)"),
    ("invoice_record_payment",
     "money arrived for an invoice: mark the invoice paid in the invoicing app, recording amount and "
     "payment date so receivables stay accurate",
     "invoice_number (string), amount (number), received_date (YYYY-MM-DD)"),
    ("expenses_log_receipt",
     "a spending receipt must be captured: create an expense entry with merchant, date, amount, and "
     "category in the expense app and attach the receipt image",
     "merchant (string), date (YYYY-MM-DD), amount (number), category (string), "
     "receipt_path (file name string)"),
    ("expenses_submit_report",
     "logged expenses are ready to file: assemble the selected entries into an expense report, add "
     "the report title, and submit it for approval",
     "report_title (string), entries (list of expense ids or dates)"),
    ("hr_request_time_off",
     "vacation or personal days must be requested: open the HR portal's time-off page, pick the leave "
     "type and dates, enter the reason, and submit the request",
     "leave_type (string), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), reason (string)"),
    ("hr_update_emergency_contact",
     "the emergency contact on file is out of date: replace it in the HR profile with the supplied "
     "person, relationship, and phone number",
     "full_name (string), relationship (string), phone (string)"),
    ("hr_enroll_benefits",
     "open-enrollment decisions are made: walk the benefits wizard selecting the chosen medical, "
     "dental, and vision plans and dependent coverage, then confirm the elections",
     "medical_plan (string), dental_plan (string), vision_plan (string), dependents (list of names)"),
    ("tickets_file_bug_report",
     "a software defect must be reported: create an issue in the tracker with title, severity, "
     "component, reproduction steps, and environment, then submit it",
     "title (string), severity (blocker|major|minor), component (string), steps (multi-line string), "
     "environment (string)"),
    ("tickets_escalate_priority",
     "an existing ticket deserves more urgency: open the issue and raise its priority or severity, "
     "leaving a comment explaining why",
     "ticket_id (string), new_priority (string), comment (string)"),
    ("tickets_add_comment",
     "new information belongs on an existing issue: open the ticket and append a comment with the "
     "supplied text, optionally mentioning teammates",
     "ticket_id (string), comment (string), mention (list of user names, optional)"),
    ("meetings_schedule_video_call",
     "a remote meeting must be created in the video-conferencing tool: schedule a call with title, "
     "start time and duration, invite the guest list, and send the invites with the join link",
     "title (string), start_time (YYYY-MM-DD HH:MM), duration_minutes (int), "
     "invitees (list of emails)"),
    ("meetings_find_shared_slot",
     "several busy people need a common time: compare the calendars of the named attendees for the "
     "requested window and report the open slots that fit everyone",
     "attendees (list of names), window_start (YYYY-MM-DD), window_end (YYYY-MM-DD), "
     "length_minutes (int)"),
    ("meetings_cancel_scheduled_call",
     "a video conference on the books is no longer needed: open it in the conferencing tool, cancel "
     "it, and notify the invited attendees",
     "meeting_title (string), notify_invitees (bool)"),
    ("music_create_playlist",
     "the user wants a new playlist in the music app: create it under the given name and visibility, "
     "optionally seeded with the first few tracks",
     "playlist_name (string), public (bool), seed_tracks (list of song names, optional)"),
    ("music_add_songs_to_playlist",
     "more music belongs on an existing playlist: search the catalog for each requested song and add "
     "the matching track to the playlist",
     "playlist_name (string), songs (list of 'title artist' strings)"),
    ("music_download_for_offline",
     "tracks must be available without network: mark the requested albums or playlist for offline "
     "download in the music app and wait until the downloads finish",
     "items (list of album or playlist names)"),
    ("photos_upload_to_album",
     "pictures on the device belong in a cloud album: open the photos app, select the album, and "
     "upload the supplied image files with their captions",
     "album (string), files (list of file name strings), captions (list of strings, optional)"),
    ("photos_share_album_link",
     "someone should see an album without an account: generate the album's external share link, set "
     "view-only access, and return the URL",
     "album (string), expiry_days (int, optional)"),
    ("photos_rotate_and_crop",
     "an image needs quick correction: open it in the photos editor, rotate by the requested quarter "
     "turns and crop to the stated aspect, saving a copy",
     "file (file name string), quarter_turns (int), aspect (e.g. 4:3 string)"),
    ("maps_get_directions",
     "the user needs a route: open the maps service, set origin and destination, choose the travel "
     "mode, and report the best route with distance and time",
     "origin (string), destination (string), mode (drive|transit|walk|bike)"),
    ("maps_save_favorite_place",
     "a location will be needed again later: look the place up on the maps service and save it to "
     "the favorites list under a custom label for one-tap future directions",
     "place (string), label (string)"),
    ("maps_report_missing_road",
     "the map shows a street that does not exist: submit a missing-or-wrong-road report for the "
     "location with the user's description",
     "location (string or coordinates), problem (string), description (string)"),
    ("notes_create_note",
     "a quick note must be captured in the notes app: create a note with the given title and body "
     "text, tagging it with the requested notebook and tags",
     "title (string), body (string), notebook (string, optional), tags (list of strings, optional)"),
    ("notes_pin_to_top",
     "a note needs to stay visible: find the matching note in the notes app and pin it to the top "
     "of the list so it stops slipping below newer items as they arrive",
     "note_title (string)"),
    ("contacts_add_new_contact",
     "a new person should be saved on the phone or contacts app: create the contact with name, "
     "phone, email, and company fields",
     "full_name (string), phone (string), email (address string), company (string, optional)"),
    ("contacts_export_vcard",
     "a contact must be shared as a file: export the matching contact record as a .vcf download and "
     "save it under the requested name",
     "full_name (string), output_name (file name string)"),
    ("files_rename_selected",
     "a file has the wrong name: locate it in the file manager and rename it to the new name, "
     "keeping the extension",
     "current_name (file name string), new_name (file name string)"),
    ("files_move_to_folder",
     "files are in the wrong place: select the listed files in the cloud drive and move them into "
     "the named folder, creating the folder first if it does not exist",
     "files (list of file name strings), target_folder (string), create_if_missing (bool)"),
    ("files_upload_to_drive",
     "local documents belong in cloud storage: upload each supplied file from the machine into the "
     "chosen cloud-drive folder and confirm every transfer finished",
     "files (list of file name strings), target_folder (string)"),
    ("files_share_view_only_link",
     "an outside collaborator should look at a file without editing: generate a view-only share link "
     "for the file and set the requested expiry",
     "file (file name string), expiry_days (int, optional)"),
    ("grocery_add_to_list",
     "items must go onto the shared grocery list: append each requested item with quantity to the "
     "list in the list app, merging duplicates when the item already exists",
     "items (list of strings), quantities (list of strings)"),
    ("grocery_order_delivery_slot",
     "the weekly grocery order needs a delivery window: pick the requested items into the cart on "
     "the grocery site and reserve the chosen delivery slot at checkout",
     "items (list of strings), delivery_day (string), slot (HH:MM-HH:MM string)"),
    ("recipes_save_to_collection",
     "a recipe found online should be kept: save the recipe page into the recipe-box app under the "
     "named collection, capturing ingredients and steps",
     "recipe_url (URL string), collection (string)"),
    ("recipes_build_shopping_list",
     "cooking is planned and ingredients are missing: read the chosen recipes, compile the missing "
     "ingredients into the shopping list app, and check pantry items off",
     "recipes (list of names or URLs), servings (int)"),
    ("events_buy_concert_tickets",
     "the user wants seats at a live show: search the ticketing site for the event, pick a section "
     "and quantity within budget, and complete the purchase",
     "event (string), city (string), tickets (int), max_price (number)"),
    ("events_register_conference",
     "attendance at a conference or meetup must be secured: open the registration page, fill the "
     "attendee form, select the ticket tier, and register",
     "conference (string), ticket_tier (string), attendee_email (address string)"),
    ("utilities_schedule_meter_reading",
     "the utility company needs access: book a meter-reading appointment slot on the provider portal "
     "for the requested date window",
     "utility (string), date_window (string), account_ref (string)"),
    ("parking_extend_session",
     "a parking session is about to expire: open the parking app's active session and extend it by "
     "the requested additional time, confirming the charge",
     "session_ref (string), extra_minutes (int)"),
    ("lms_enroll_course",
     "a learner must join a class: search the learning platform for the course, select the next "
     "available cohort, and enroll with the supplied account",
     "course (string), cohort (string, optional), learner_email (address string)"),
    ("lms_submit_assignment",
     "coursework is due: open the assignment page in the course, attach the submission files, add "
     "any required comments, and submit before the deadline",
     "course (string), assignment (string), files (list of file name strings), comment (string, optional)"),
    ("weather_get_forecast_brief",
     "the user asks what the sky will do: look up the forecast for the location and window and "
     "return a short brief covering temperature, precipitation risk, and any warnings",
     "location (string), days_ahead (int)"),
    ("news_compile_daily_digest",
     "the morning read should assemble itself: scan the subscribed sources in the news app, gather "
     "today's top stories per topic, and compile them into a single digest",
     "topics (list of strings), max_stories (int)"),
    ("vpn_connect_server",
     "traffic should route through another region: open the VPN client, connect to the named server "
     "or the best server in the requested country, and verify the connected state",
     "country (string), server_name (string, optional)"),
    ("wifi_configure_guest_network",
     "visitors need internet: open the router admin page, create or update the guest Wi-Fi network "
     "with the supplied name and password, and save the configuration",
     "network_name (string), password (string), bandwidth_limit (string, optional)"),
    ("time_tracker_start_timer",
     "work on a task is starting and should be timed: start a timer in the time-tracking app against "
     "the named project and task with an optional billing flag",
     "project (string), task (string), billable (bool)"),
    ("invoice_generate_statement",
     "a client needs an account statement: generate the statement covering all invoices in the date "
     "range for the client and export it as a PDF",
     "client (string), from_date (YYYY-MM-DD), to_date (YYYY-MM-DD)"),
    ("browser_clear_cache",
     "the browser is misbehaving: open settings and clear the cache and optional cookies for the "
     "chosen time range, keeping saved logins when asked",
     "time_range (hour|day|all), clear_cookies (bool)"),
]

REAL: dict[str, tuple[str, str]] = {
    WIZARD:
        ("a new OpenApps calendar event is wanted and this deployment's create form is a multi-step "
         "wizard (Next screens: details, date, where, who); the program drives every step until the "
         "event is saved on the calendar",
         "title (string), date (YYYY-MM-DD), description (string), "
         "location (string), url (URL string), invitees (comma-separated names)"),
    SINGLE:
        ("an OpenApps calendar event must be created on a deployment whose create form is one "
         "scrolling page with all fields visible; the program fills the six fields in one pass and saves",
         "title (string), date (YYYY-MM-DD), description (string), "
         "location (string), url (URL string), invitees (comma-separated names)"),
    SECTIONED:
         ("the OpenApps calendar create form on this deployment is one page grouped into titled "
          "sections (details, when, where, who); the program fills each section in order and saves",
          "title (string), date (YYYY-MM-DD), description (string), "
          "location (string), url (URL string), invitees (comma-separated names)"),
    TODO:
        ("the user wants a task added to a to-do list in the todo app: open the list, add a new "
         "item with its title, set the due date and priority, and leave it as not yet done",
         "item_title (short string), due_date (YYYY-MM-DD string), priority "
         "(high|medium|low), notes (string, optional)"),
    MESSAGES:
        ("a text message must go out through the messages app: pick the conversation with the named "
         "contact (or start one), type the message body, and send it so it shows as delivered",
         "recipient_name (string), message_body (string)"),
}


def entry_line(name: str, trigger: str, params: str, example: str, tail: str) -> str:
    return (
        f"PROGRAM {name} — use when {trigger}; parameters: {params}. "
        f'Typical ask: "{example}". {tail}'
    )


# Short operational notes rotated over entries (varied wording, no duplicates
# within a small manifest by construction of the hash choice).
TAILS = [
    "Compiled from a verified trajectory and gate-checked on held-out bindings.",
    "Runs unattended and reports success with a short action log when done.",
    "Assumes the app account is already signed in; no login handling.",
    "Covers only the stated scenario - adjacent asks must route elsewhere or to NONE.",
    "Parameter values are entered verbatim; nothing is invented beyond them.",
    "Ends on the app's own confirmation screen, which doubles as the success check.",
]

EXAMPLE: dict[str, str] = {
    WIZARD: "add Quarterly sync Apr 1, Room 3, invite Dennis - the wizard-form site",
    SINGLE: "create the Quarterly sync, Apr 1, Room 3, with link, invite Dennis - the one-page "
            "scrolling form",
    SECTIONED: "new event Quarterly sync, Apr 1, Room 3, invite Dennis - the sectioned-form site",
    TODO: "add 'renew passport' to my to-do list, due Oct 1, high priority",
    MESSAGES: "text Mom 'running 15 minutes late' from the messages app",
    "flights_book_roundtrip": "get me round trip to London Oct 12 to Oct 19, economy for two",
    "flights_book_oneway": "one-way JFK to SFO next Friday morning, just me",
    "flights_select_seat": "grab two window seats on my BA 0114 booking",
    "hotel_book_room": "need a double in Berlin for three nights from Nov 3, two guests",
    "hotel_request_late_checkout": "ask the hotel for a 2pm checkout on our last day",
    "rental_car_reserve": "reserve a compact at Lyon airport, pickup Thu 9am to Sun 6pm",
    "train_book_ticket": "two second-class tickets Paris to Lyon on the 14th",
    "restaurant_reserve_table": "table for six at L'Atelier Friday 8pm, it's a birthday",
    "restaurant_cancel_reservation": "cancel our L'Atelier booking on the 12th, ref LR-8842",
    "restaurant_update_party_size": "we are now eight instead of four, same slot",
    "email_send_with_attachment": "email the Q3 report PDF to Dana, subject 'Q3 documents'",
    "email_draft_new_message": "draft a note to Prof. Lee about the extension - don't send yet",
    "email_reply_to_thread": "reply to the 'Invoice 204' thread: payment went out today",
    "email_forward_message": "forward the boarding info to Sam with a short heads-up",
    "email_search_inbox": "find emails from billing@acme received since March",
    "email_archive_from_sender": "archive everything from notifications@github in my inbox",
    "email_set_vacation_autoreply": "set out-of-office July 1 to 12 with a 'slow to reply' text",
    "spreadsheet_append_row": "add today's numbers to the October sheet: 42 units, 5039 dollars",
    "spreadsheet_create_new_sheet": "make a tracker sheet 'Leads' with columns Name, Source, Score",
    "spreadsheet_sort_range": "sort A2:F200 by column F, descending",
    "spreadsheet_apply_column_formula": "put =C2*D2 down column E for all rows",
    "spreadsheet_export_csv": "export the 'October' sheet as CSV named oct.csv",
    "spreadsheet_freeze_header_row": "freeze row 1 and column A on the Data sheet",
    "spreadsheet_delete_blank_rows": "strip the empty rows out of A1:K400",
    "docs_create_document": "start a doc titled 'Launch retro' from the meeting template",
    "docs_insert_table": "add a 5x3 table at the end of the plan doc",
    "docs_add_table_of_contents": "put a table of contents at the top of the handbook",
    "docs_export_pdf": "export the contract doc to PDF as contract-v2.pdf",
    "docs_apply_heading_styles": "make 'Overview' and 'Risks' Heading 1 in the brief",
    "forms_fill_insurance_claim": "file a claim for the March 14 fender bender, 1200 dollars",
    "forms_submit_job_application": "apply to the backend engineer posting with my resume",
    "forms_complete_survey": "fill the team pulse survey: mostly agree, comment on tooling",
    "forms_upload_supporting_docs": "upload the two receipts to claim CLM-991",
    "forms_sign_and_submit_waiver": "sign the gym waiver as Robin Tate, today's date",
    "settings_enable_dark_mode": "switch the app over to dark mode, please",
    "settings_change_language": "change the interface language to German",
    "settings_adjust_notification_prefs": "turn off push for marketing, keep transactional email",
    "settings_set_timezone": "set my account timezone to Europe/Berlin",
    "settings_manage_connected_accounts": "revoke the old Slack workspace connection",
    "settings_update_billing_address": "update billing to 12 Rue Verte, 75011 Paris, France",
    "search_web_and_summarize": "what are the current rules for carry-on lithium batteries? cite sources",
    "search_compare_product_prices": "cheapest price for a Whirlpool WRS325SDHZ refrigerator",
    "search_find_nearby_stores": "which DM drugstores near Hauptstrasse 12 are open now?",
    "crm_create_contact": "add M. Bellamy at Northwind, mbellamy@northwind.io, +33145550123",
    "crm_log_call_note": "log today's call with Ava Chen: pricing objections, demo Thursday",
    "crm_update_deal_stage": "move 'Northwind renewal' to Negotiation, amount 48000",
    "crm_merge_duplicate_contacts": "merge the two Dana White records, keep the newer one",
    "invoice_create_draft": "draft an invoice for Kramer GmbH: 2 days consulting at 900, net 14",
    "invoice_send_to_client": "send invoice INV-0234 out to the client",
    "invoice_record_payment": "mark INV-0234 paid - 1800 received today",
    "expenses_log_receipt": "log the taxi receipt, Apr 2, 34.50, category Ground transport",
    "expenses_submit_report": "submit all April expenses as 'April - Berlin trip'",
    "hr_request_time_off": "request vacation Aug 4-15, reason 'family visit'",
    "hr_update_emergency_contact": "my emergency contact is now my sister Lena, +491705550199",
    "hr_enroll_benefits": "enroll me in Plan B medical, Core dental, Standard vision, one dependent",
    "tickets_file_bug_report": "file a major bug: checkout button dead on Safari, steps attached",
    "tickets_escalate_priority": "bump PROJ-771 to urgent, the customer is blocked",
    "tickets_add_comment": "comment on PROJ-650: reproduced on staging, logs attached",
    "meetings_schedule_video_call": "schedule 'Design sync' Thu 15:00 for 45 with the design team",
    "meetings_find_shared_slot": "find a 30-minute slot all four of us share next week",
    "meetings_cancel_scheduled_call": "cancel Thursday's design sync and notify everyone",
    "music_create_playlist": "make a private playlist 'Deep Focus' seeded with three ambient tracks",
    "music_add_songs_to_playlist": "add 'Gymnopedie No. 1' and 'Weightless' to Deep Focus",
    "music_download_for_offline": "download the Road-Trip playlist for the flight",
    "photos_upload_to_album": "upload IMG_4412 to IMG_4415 to 'Iceland 2026', caption the first",
    "photos_share_album_link": "get a view-only link for 'Iceland 2026', valid two weeks",
    "photos_rotate_and_crop": "rotate IMG_3301 a quarter turn and crop it to 4:3",
    "maps_get_directions": "walking directions from Gare du Nord to 12 Rue Verte",
    "maps_save_favorite_place": "save the coworking space as a favorite called 'Office - Berlin'",
    "maps_report_missing_road": "report the footbridge near the park as a missing path",
    "notes_create_note": "new note 'Books to read' with the five titles, tag it reading",
    "notes_pin_to_top": "pin the 'Flat handover' note to the top of my notes",
    "contacts_add_new_contact": "save Ravi Patel, +44 20 7946 0958, r.patel@example.co.uk",
    "contacts_export_vcard": "export Ravi Patel's contact card as ravi.vcf",
    "files_rename_selected": "rename 'final_final.docx' to 'contract-v2.docx'",
    "files_move_to_folder": "move the three scan PDFs into a new folder 'Taxes 2026'",
    "files_upload_to_drive": "upload receipts.zip into the Finance folder",
    "files_share_view_only_link": "share the pitch deck view-only for the next 7 days",
    "grocery_add_to_list": "add oat milk (2), rye bread (1) and coffee beans (500g)",
    "grocery_order_delivery_slot": "order the weekly basics for the Tuesday 18-20 delivery window",
    "recipes_save_to_collection": "save that ramen recipe to my 'Weeknight dinners' collection",
    "recipes_build_shopping_list": "shopping list for the ramen and the tacos, 4 servings, skip what I have",
    "events_buy_concert_tickets": "two tickets for the Friday show, under 150 each",
    "events_register_conference": "register me for the API conference, standard tier",
    "utilities_schedule_meter_reading": "book a meter reading any slot in the week of the 20th, acct 4471-A",
    "parking_extend_session": "extend the session on plate BK-XY-4 by two hours",
    "lms_enroll_course": "enroll me in 'Applied Statistics', the fall cohort",
    "lms_submit_assignment": "submit homework 3 for Statistics: report.pdf plus a comment",
    "weather_get_forecast_brief": "what is the weather looking like in Lisbon over the next 5 days?",
    "news_compile_daily_digest": "pull together today's top 8 stories on chips and AI",
    "vpn_connect_server": "connect the VPN through a Tokyo server",
    "wifi_configure_guest_network": "set up guest Wi-Fi 'Flat-Guest' with password pickles123",
    "time_tracker_start_timer": "start a billable timer on Northwind migration, task schema fix",
    "invoice_generate_statement": "statement of account for Kramer GmbH, Jan 1 to Jun 30, as PDF",
    "browser_clear_cache": "clear the browser cache for the last day, keep my logins",
}


def manifest_header(n: int) -> str:
    if n == 0:
        return "LIBRARY MANIFEST — 0 compiled programs available. (The library is empty.)"
    return f"LIBRARY MANIFEST — {n} compiled program(s) available:"


def build_manifest(lines: list[str]) -> str:
    return manifest_header(len(lines)) + ("\n" + "\n".join(lines) if lines else "")


def main() -> None:
    assert len(SYNTHETIC) == 95, len(SYNTHETIC)
    names = [s[0] for s in SYNTHETIC] + list(REAL)
    assert len(set(names)) == 100, "duplicate program names"

    # Canonical order = n=100 order: synthetics in written order, real entries
    # interleaved at fixed 1-based positions (12, 33, 50, 65, 81).
    positions = {12: SINGLE, 33: SECTIONED, 50: TODO, 65: WIZARD, 81: MESSAGES}
    order: list[str] = []
    synth_iter = iter(SYNTHETIC)
    for i in range(1, 101):
        if i in positions:
            order.append(positions[i])
        else:
            order.append(next(synth_iter)[0])

    def spec_of(name: str) -> tuple[str, str]:
        if name in REAL:
            return REAL[name]
        for n_, t_, p_ in SYNTHETIC:
            if n_ == name:
                return t_, p_
        raise KeyError(name)

    missing = [n for n in names if n not in EXAMPLE]
    assert not missing, f"missing examples: {missing}"

    entries = []
    for pos, name in enumerate(order, start=1):
        trigger, params = spec_of(name)
        tail = TAILS[zlib.crc32(name.encode()) % len(TAILS)]
        line = entry_line(name, trigger, params, EXAMPLE[name], tail)
        entries.append({
            "pos_canonical": pos,
            "name": name,
            "real": name in REAL,
            "text": line,
            "tokens_o200k": len(ENC.encode(line)),
        })

    # token-count sanity: every entry inside 75..125
    outliers = [e for e in entries if not 75 <= e["tokens_o200k"] <= 125]
    for e in outliers:
        print(f"OUT OF RANGE ({e['tokens_o200k']} tok, pos {e['pos_canonical']}): {e['name']}")

    # ---- derived manifests ------------------------------------------------
    n1 = [WIZARD]
    n5 = [SINGLE, TODO, WIZARD, MESSAGES, SECTIONED]

    rng = random.Random(20260905)
    synth_names = [s[0] for s in SYNTHETIC]
    sample = rng.sample(synth_names, 19)
    forced = ["flights_book_roundtrip", "email_send_with_attachment", "spreadsheet_append_row"]
    for f in forced:  # swap out drawn entries until all forced ones are in
        if f not in sample:
            for i, s in enumerate(sample):
                if s not in forced and sample.count(s):
                    sample[i] = f
                    break
    assert all(f in sample for f in forced)
    n20 = sample[:9] + [WIZARD] + sample[9:]  # wizard at 1-based position 10

    by_name = {e["name"]: e for e in entries}
    manifests = {
        0: [],
        1: n1,
        5: n5,
        20: n20,
        100: order,
    }
    assert all(p in manifests[100] for p in manifests[20] if p != WIZARD)

    stats = {"encoding": "tiktoken o200k_base (approximation; API deltas are ground truth)",
             "per_entry_tokens": {e["name"]: e["tokens_o200k"] for e in entries},
             "manifests": {}}
    for n, names_n in manifests.items():
        text = build_manifest([by_name[p]["text"] for p in names_n])
        (HERE / f"manifest_n{n}.txt").write_text(text)
        stats["manifests"][n] = {
            "entries": len(names_n),
            "tokens_o200k": len(ENC.encode(text)),
            "wizard_position": (names_n.index(WIZARD) + 1) if WIZARD in names_n else None,
            "member_names": names_n,
        }

    (HERE / "manifest_entries.json").write_text(json.dumps(entries, indent=1))
    (HERE / "manifest_stats.json").write_text(json.dumps(stats, indent=1))

    print("entries:", len(entries))
    print("mean tokens/entry:", sum(e["tokens_o200k"] for e in entries) / len(entries))
    for n in (0, 1, 5, 20, 100):
        print(f"n={n}: manifest tokens={stats['manifests'][n]['tokens_o200k']}, "
              f"wizard pos={stats['manifests'][n]['wizard_position']}")


if __name__ == "__main__":
    main()
