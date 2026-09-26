"""The prompt conditions for the seven Android task families.

Same ladder as guiexp/conditions.py, ported to the android_world families:

    discover  goal only (android_world's own goal template filled with the
              instance values)
    told      + the exact interface procedure (screens, fields in the order
              they are met, the buttons between them) -- but NO instance
              values. These were written by walking each app's real UI on
              the AndroidWorldAvd emulator (API 33, frozen October 2023):
              Google Contacts, Simple Calendar Pro 6.x, Markor 2.10.9,
              OsmAnd and the system Files app. The calendar and markor
              texts were rewritten on 2026-09-09 to the minimal path of the
              cheapest successful discover run (docs/android-told-prompts-
              proposal.md); TOLD_LEGACY below keeps what the earlier runs
              saw.
    mid       + structure only (screen count, field grouping/order), no
              controls, no labels
    skill     + a compressed family-level skill doc (~200-260 tokens) with
              honest cautions; no selectors, no field order, no screen count
    doc       + a COMPILED operation document (the "doc" artifact the
              AutoRPA-style builder produces from the building trajectories),
              injected exactly the way told injects the hand-written
              procedure. Unlike told, its text is not authored here: it is
              passed in per run, so runner.py can measure the per-use cost
              L_doc of a text artifact on new bindings.
    floor     no task at all; the harness shows one trivial observation and
              the episode exits (pure harness-overhead baseline)

All conditions except floor share the same goal block, so a difference in
tokens between two conditions is a difference in knowledge, not in task.
"""

from __future__ import annotations

CONDITIONS = ("discover", "told", "mid", "skill", "doc", "floor")

FAMILIES = (
    "ContactsAddContact",
    "SimpleCalendarAddOneEvent",
    "MarkorCreateNote",
    "MarkorDeleteNote",
    "OsmAndFavorite",
    "OsmAndMarker",
    "FilesMoveFile",
)

COMMON_TEMPLATE = (
    "You are operating an Android phone.\n"
    "\n"
    "{goal}"
    "\n"
    "Rules:\n"
    "- Complete the task by driving the phone's touchscreen UI with the JSON\n"
    "  actions provided (click / input_text / scroll / open_app / status).\n"
    "- Only interaction through the app's own UI counts; do not try to reach\n"
    "  the app's files or databases by other means.\n"
    "- When the task is finished, reply with the status action with\n"
    "  goal_status complete.\n"
)

# The told arm: the exact procedure at form-field/button granularity. It names
# screens, fields in the order they are met, and the buttons between them, but
# never the instance's parameter values (those live only in the goal block).
TOLD_PROCEDURE = {
    "ContactsAddContact": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Contacts app. If a first-run system dialog asks whether\n"
        "   Contacts may send notifications, tap Don't allow.\n"
        "2. The app's main screen shows the contact list. Tap the round\n"
        "   Create contact button at the bottom right.\n"
        "3. On the Create contact screen the fields appear in this order:\n"
        "   First name, Last name, Company, Phone, Email, Significant date.\n"
        "   The name in the task is two words: type the first word into the\n"
        "   First name field and the second word into the Last name field.\n"
        "4. Type the phone number given in the task into the Phone field;\n"
        "   leave the label next to it at its default.\n"
        "5. Tap the Save button at the top right of the screen.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "SimpleCalendarAddOneEvent": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Simple Calendar Pro app. Its main screen is a month\n"
        "   grid, and the month it shows is already the month the task\n"
        "   asks for.\n"
        "2. Tap the cell of the day the task asks for, inside the month\n"
        "   grid. The screen becomes that one day's view, headed with that\n"
        "   date (for example 'October 27 (Fri)'). An event created from\n"
        "   this screen already carries that date, so the date is now set\n"
        "   and the date picker is never needed.\n"
        "3. Tap the round + button at the bottom right. Two small buttons\n"
        "   appear above it, labelled Task and Event. Tap Event.\n"
        "4. The New Event screen opens. Its three text fields, top to\n"
        "   bottom, are Title, Location and Description. Type the event\n"
        "   title into Title and the given description into Description.\n"
        "   Leave Location empty.\n"
        "5. Under the text fields are two rows, each holding a date on the\n"
        "   left and a time on the right: the start row and the end row.\n"
        "   Both dates already show the day you picked in step 2. Do not\n"
        "   tap either date and do not open the date picker.\n"
        "6. Tap the time on the start row. A Select time dialog opens with\n"
        "   an hour box, a minute box and a round clock face, in hour\n"
        "   mode. Tap the hour on the clock face. The clock is 24-hour and\n"
        "   the hours 13 to 23 are on the inner ring. The dialog switches\n"
        "   to minute mode: tap the minute on the face only if the minute\n"
        "   box does not already show the minute you need, then tap OK.\n"
        "7. Tap the time on the end row and set it the same way to the\n"
        "   start time plus the number of minutes the task gives.\n"
        "8. Tap the check mark at the top right to save. If a Disclaimer\n"
        "   dialog about reminders appears, tap its OK button.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "MarkorCreateNote": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Markor app. Its main screen is the notebook's file\n"
        "   list, with a round red + button at the bottom right.\n"
        "2. Tap that + button. A New File dialog opens: a Name field\n"
        "   pre-filled with the placeholder my_note, a small second field\n"
        "   to its right holding the file extension and pre-filled .txt,\n"
        "   Type and Template dropdowns, and the FOLDER, CANCEL and OK\n"
        "   buttons.\n"
        "3. Type the note's file name without its extension into the Name\n"
        "   field, which replaces the placeholder. Then look at the small\n"
        "   extension field and change it only if it does not already hold\n"
        "   the extension the task asks for. Leave Type and Template as\n"
        "   they are.\n"
        "4. Tap OK. The note opens in the editor. Its top bar holds, to\n"
        "   the right of the file name, an undo arrow, a redo arrow, an\n"
        "   eye for preview, a floppy-disk Save icon, a magnifier and a\n"
        "   three-dot menu.\n"
        "5. Type the note text given in the task into the editor body.\n"
        "6. Tap the floppy-disk Save icon in the top bar. The file is\n"
        "   written and the icon greys out. The note is saved in place and\n"
        "   you do not have to leave the editor.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "MarkorDeleteNote": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Markor app. Its main screen is the notebook's file\n"
        "   list, which also holds other files besides the one named in the\n"
        "   task.\n"
        "2. Find the row whose title is the file named in the task; scroll\n"
        "   the list down if that row is not on screen.\n"
        "3. Long-press that row (the long_press action). The row becomes\n"
        "   selected and the top bar turns into a selection bar whose icons\n"
        "   act on the selected file.\n"
        "4. Tap the trash-can icon in that top bar. A confirmation dialog\n"
        "   appears with CANCEL and OK buttons.\n"
        "5. Tap OK. The file list comes back without the deleted row.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "OsmAndFavorite": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the OsmAnd app. Its main screen is the map. If a first-run\n"
        "   dialog offers to download maps or asks about sending data, close\n"
        "   it with its Skip or Cancel button; the map this task needs is\n"
        "   already on the device.\n"
        "2. Tap the round Search button, the magnifier at the bottom left of\n"
        "   the map. A search screen opens with one text field at the top.\n"
        "3. Type the place the task names into that search field. The task\n"
        "   gives either a place name or a latitude and longitude pair, and\n"
        "   this one field takes both forms.\n"
        "4. Tap the first result row under the field. The map comes back with\n"
        "   that place selected and a bottom sheet at the foot of the screen\n"
        "   carrying the place's name, a Directions button, and a row of\n"
        "   round buttons among which are Add to favorites and Mark.\n"
        "5. Tap the Add to favorites button in that row. This is the star\n"
        "   button; do not tap Mark and do not tap Directions, which write\n"
        "   somewhere else. A Favorite dialog opens with a Name field already\n"
        "   filled with the place's name.\n"
        "6. Leave that Name field as it is and tap the dialog's Save button.\n"
        "   The map comes back with a star drawn at the place.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "OsmAndMarker": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the OsmAnd app. Its main screen is the map. If a first-run\n"
        "   dialog offers to download maps or asks about sending data, close\n"
        "   it with its Skip or Cancel button; the map this task needs is\n"
        "   already on the device.\n"
        "2. Tap the round Search button, the magnifier at the bottom left of\n"
        "   the map. A search screen opens with one text field at the top.\n"
        "3. Type the place the task names into that search field. The task\n"
        "   gives either a place name or a latitude and longitude pair, and\n"
        "   this one field takes both forms.\n"
        "4. Tap the first result row under the field. The map comes back with\n"
        "   that place selected and a bottom sheet at the foot of the screen\n"
        "   carrying the place's name, a Directions button, and a row of\n"
        "   round buttons among which are Add to favorites and Mark.\n"
        "5. Tap the Mark button in that row. This is the flag button; do not\n"
        "   tap Add to favorites and do not tap Directions, which write\n"
        "   somewhere else. The marker is added straight away: no naming\n"
        "   dialog opens and nothing has to be confirmed.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "FilesMoveFile": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Files app.\n"
        "2. Tap the Show roots button, the three-line hamburger icon at the\n"
        "   left of the top bar. A drawer slides in from the left listing the\n"
        "   storage volumes and shortcuts.\n"
        "3. Tap the storage row in that drawer whose name begins with\n"
        "   sdk_gphone. It is named after the emulator image, so its ending\n"
        "   varies with the host and need not match the name in the task.\n"
        "   The screen becomes\n"
        "   the top level of that storage area, one row per folder.\n"
        "4. Tap the row of the source folder the task names. Its contents\n"
        "   open, holding the file the task names among several others.\n"
        "5. Long-press the row of that file (the long_press action). The row\n"
        "   becomes selected and the top bar turns into a selection bar\n"
        "   headed 1 selected, with a three-dot More options button at its\n"
        "   right.\n"
        "6. Tap that More options button. A menu opens whose entries include\n"
        "   Copy to..., Move to... and Cut.\n"
        "7. Tap Cut. The selection clears and the screen goes back to the\n"
        "   plain file list. Nothing on screen now says a file is waiting to\n"
        "   be moved, but it is.\n"
        "8. Go back to the top level of the storage area with the back arrow\n"
        "   at the left of the top bar (the navigate_back action does the\n"
        "   same).\n"
        "9. Tap the row of the destination folder the task names. Its\n"
        "   contents open, and the top bar now carries a Paste button, which\n"
        "   is there only while a cut file is pending.\n"
        "10. Tap Paste. The file appears in this folder and is gone from the\n"
        "    source folder.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
}

# The told texts these two families ran under before 2026-09-09. They failed
# the two admissibility gates of docs/android-told-diagnosis.md section 7.2
# (R1 replay validity, R2 minimality): the calendar text routed through the
# date picker, costing three steps the month-grid path does not need, and the
# markor text saved with Back, whose first press only closes the keyboard.
# Runs recorded before that date used these strings, so they are kept here to
# keep those trajectories interpretable.
TOLD_LEGACY = {
    "SimpleCalendarAddOneEvent": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Simple Calendar Pro app. Its main screen is a month\n"
        "   calendar. Tap the round New Event button at the bottom right; a\n"
        "   bottom sheet offers Task and Event; tap Event.\n"
        "2. On the New Event screen fill the text fields top to bottom: type\n"
        "   the event title into the Title field and the given description\n"
        "   into the Description field; leave Location empty.\n"
        "3. Tap the start date row (the first date shown under the text\n"
        "   fields). In the date picker, move with the Previous month and\n"
        "   Next month arrows until the picker shows the year and month\n"
        "   given in the task, tap the day number on the calendar grid, then\n"
        "   tap OK.\n"
        "4. Tap the start time row (the time shown next to that date). In the\n"
        "   time picker, set the hour and the minutes with the two wheels,\n"
        "   then tap OK. The phone uses a 24-hour clock.\n"
        "5. The event length is set with the end time: tap the second time\n"
        "   row (the one below the start time) and set it to the start time\n"
        "   plus the number of minutes given in the task.\n"
        "6. Tap the Save button at the top right.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
    "MarkorCreateNote": (
        "The interaction procedure for this app is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Open the Markor app. Its main screen is the notebook's file\n"
        "   list.\n"
        "2. Tap the round button at the bottom right, labelled\n"
        "   Create a new file or folder. A New File dialog opens with a\n"
        "   Name field that is pre-filled with placeholder text, a small\n"
        "   second field holding the file extension, Type and Template\n"
        "   dropdowns, and FOLDER, CANCEL and OK buttons.\n"
        "3. Clear the Name field and type the note's file name without its\n"
        "   extension; put the extension (the part after the last dot) into\n"
        "   the small extension field. Leave Type and Template as they are.\n"
        "4. Tap OK. The new note opens in the editor.\n"
        "5. Type the note text given in the task into the editor.\n"
        "6. Press Back (the navigate_back action); Markor saves the file.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
}

# The mid arm: structure only. Screen count and which fields in which
# grouping, but no controls and no labels.
MID_STEPS = {
    "ContactsAddContact": (
        "- open the contacts app\n"
        "- start creating a new contact\n"
        "- the form is one screen: name fields first, then the phone number\n"
        "  field, then further optional fields; fill the name fields and the\n"
        "  phone number field\n"
        "- save the new contact"
    ),
    "SimpleCalendarAddOneEvent": (
        "- open the calendar app\n"
        "- start creating a new event (the plain event kind, not a task)\n"
        "- the form is one screen: title and description fields at the top,\n"
        "  then the start date and start time, then the end time; the event\n"
        "  length is entered by setting the end time relative to the start\n"
        "- date and time each open a picker that must be confirmed\n"
        "- save the event"
    ),
    "MarkorCreateNote": (
        "- open the notes app\n"
        "- start creating a new note\n"
        "- a dialog asks for the file name first (name and extension are\n"
        "  entered separately on that one screen)\n"
        "- after confirming the dialog, the note opens in an editor screen;\n"
        "  type the text there\n"
        "- leaving the editor saves the note"
    ),
    "MarkorDeleteNote": (
        "- open the notes app\n"
        "- its first screen lists the existing notes; the note named in the\n"
        "  task is one row among several others\n"
        "- select that one row, then use the list screen's own removal\n"
        "  action on the selection\n"
        "- a confirmation must be accepted before the removal takes effect"
    ),
    "OsmAndFavorite": (
        "- open the maps app; its first screen is the map itself\n"
        "- the place is reached through the app's own search: one screen with a\n"
        "  single text field and a list of results under it\n"
        "- picking a result returns to the map and opens a panel at the foot of\n"
        "  the screen holding several actions for that one place\n"
        "- the action this task needs is on that panel, next to others that\n"
        "  store the same place somewhere else\n"
        "- a naming step may follow and must be accepted if it appears"
    ),
    "OsmAndMarker": (
        "- open the maps app; its first screen is the map itself\n"
        "- the place is reached through the app's own search: one screen with a\n"
        "  single text field and a list of results under it\n"
        "- picking a result returns to the map and opens a panel at the foot of\n"
        "  the screen holding several actions for that one place\n"
        "- the action this task needs is on that panel, next to others that\n"
        "  store the same place somewhere else\n"
        "- a naming step may follow and must be accepted if it appears"
    ),
    "FilesMoveFile": (
        "- open the file manager app\n"
        "- the storage area is not on the first screen: it is reached from a\n"
        "  panel that slides in from the edge\n"
        "- from the storage area's top level, folders open one inside another;\n"
        "  the file sits in the source folder among other files\n"
        "- the move has two halves: first mark the one file and hand it to the\n"
        "  file list's own move mechanism, then open the destination folder and\n"
        "  finish the move there\n"
        "- between the two halves nothing on screen records that a move is\n"
        "  pending"
    ),
}

MID_SUFFIX = (
    "The structure of this flow is known, but its controls are not. The\n"
    "steps are:\n"
    "{steps}\n"
    "The exact controls and labels are not specified; locate each named\n"
    "field or control yourself. No other exploration is needed."
)

# The skill arm: family-level skill entries, the kind a workflow memory or an
# induced skill actually stores: task and trigger, the recorded values, and
# honest cautions -- no selectors, no field order, no screen counts.
SKILL_DOC = {
    "ContactsAddContact": (
        "Skill: add a contact to the phone's Contacts app\n"
        "\n"
        "Use when a request asks to create, save, or add a contact on this\n"
        "phone.\n"
        "\n"
        "1. Open the Contacts app and find its create-contact control.\n"
        "2. Record the requested name and phone number. Names here are a\n"
        "   first and a last name, and the app takes them in separate\n"
        "   fields; copy both values exactly.\n"
        "3. The create screen also offers fields the request will not\n"
        "   mention (company, email, dates); fill only what was asked for.\n"
        "4. Save from the create screen and make sure you actually left it\n"
        "   before reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. The first app start can show a\n"
        "system notification dialog; dismissing it is not part of the task.\n"
        "A tip banner can sit over part of the list; the create control\n"
        "stays reachable without acting on it. Leave the phone-number label\n"
        "at its default unless the request says otherwise."
    ),
    "SimpleCalendarAddOneEvent": (
        "Skill: add an event to Simple Calendar Pro\n"
        "\n"
        "Use when a request asks to create, add, or schedule an event in\n"
        "this phone's calendar app.\n"
        "\n"
        "1. Open Simple Calendar Pro and find its new-event control. The app\n"
        "   can also create tasks; a request for an event wants the event\n"
        "   kind.\n"
        "2. Record the requested title, description, date, start time, and\n"
        "   duration; copy them exactly.\n"
        "3. Duration is not a field of its own: it is the gap between the\n"
        "   start and end times, so set the end time to the start time plus\n"
        "   the duration. Date and time are edited in picker dialogs that\n"
        "   must each be confirmed.\n"
        "4. Save, then check the event landed on the requested day before\n"
        "   reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. The clock is 24-hour, and the\n"
        "month on screen need not be the event's month, so navigate the\n"
        "picker. Events from earlier tasks may be on the calendar; do not\n"
        "touch them. The keyboard can cover the form; dismiss it to reach\n"
        "the date rows."
    ),
    "MarkorCreateNote": (
        "Skill: create a note in Markor\n"
        "\n"
        "Use when a request asks to create, write, or add a note (a named\n"
        "text file) in the Markor app.\n"
        "\n"
        "1. Open Markor and find its create-file control.\n"
        "2. Record the requested file name (with its extension) and the note\n"
        "   text; copy both exactly.\n"
        "3. The name dialog expects base name and extension separately and\n"
        "   comes pre-filled with placeholder text; replace it. A template\n"
        "   may pre-fill the editor, so make sure the final content is\n"
        "   exactly the requested text.\n"
        "4. The note is saved by leaving the editor; check the file list\n"
        "   shows the new note before reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. Do not change existing notes or\n"
        "   folders. The first app start can ask for file access; that is a\n"
        "   one-time device step, not part of the task. Names are\n"
        "   case-sensitive and existing files are not overwritten, so enter\n"
        "   the requested name exactly."
    ),
    "MarkorDeleteNote": (
        "Skill: delete a note in Markor\n"
        "\n"
        "Use when a request asks to delete, remove, or get rid of a named\n"
        "note (a text file) in the Markor app.\n"
        "\n"
        "1. Open Markor. Its opening screen lists the notes that exist.\n"
        "2. Record the requested file name exactly, extension included, and\n"
        "   locate the row that carries it. Several other notes sit in the\n"
        "   same list, so read the row titles rather than picking the top\n"
        "   one; scroll if the row is not on screen.\n"
        "3. Removal in this app works on a selection: mark the one row you\n"
        "   want, then use the removal action the list screen offers once\n"
        "   something is selected. A confirmation step follows and must be\n"
        "   accepted for the file to actually go.\n"
        "4. Check the list no longer shows the note before reporting\n"
        "   success.\n"
        "\n"
        "Cautions: drive the app's UI only. The other notes in the list\n"
        "belong to the environment and must survive untouched, so never\n"
        "clear the whole notebook. Names are case-sensitive. The first app\n"
        "start can ask for file access; that is a one-time device step, not\n"
        "part of the task."
    ),
    "OsmAndFavorite": (
        "Skill: save a favorite location in the OsmAnd maps app\n"
        "\n"
        "Use when a request asks to add, save, or star a favorite location, or a\n"
        "favorite place marker, in the phone's offline maps app.\n"
        "\n"
        "1. Open OsmAnd and use its own search to reach the place.\n"
        "2. Record the place the request names exactly. It is given either as a\n"
        "   place name or as a latitude and longitude pair, and the search takes\n"
        "   both forms unchanged.\n"
        "3. Selecting a result puts the place on the map with a panel of actions\n"
        "   for it. That panel offers more than one way to keep a place, and only\n"
        "   the favorites one writes where this task is checked.\n"
        "4. If a naming step follows, accept the name the app proposes, then\n"
        "   check the place is kept before reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. The map data is already on the\n"
        "device, so decline any offer to download more. A first result that is\n"
        "a different place of a similar name is a real risk; read the result\n"
        "row before tapping it. Coordinates must survive unchanged to five\n"
        "decimal places."
    ),
    "OsmAndMarker": (
        "Skill: add a location marker in the OsmAnd maps app\n"
        "\n"
        "Use when a request asks to add, drop, or place a location marker, rather\n"
        "than a favorite, in the phone's offline maps app.\n"
        "\n"
        "1. Open OsmAnd and use its own search to reach the place.\n"
        "2. Record the place the request names exactly. It is given either as a\n"
        "   place name or as a latitude and longitude pair, and the search takes\n"
        "   both forms unchanged.\n"
        "3. Selecting a result puts the place on the map with a panel of actions\n"
        "   for it. That panel offers more than one way to keep a place; the\n"
        "   markers list and the favorites list are different stores, and only\n"
        "   the marker one counts here.\n"
        "4. Check the place is kept before reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. The map data is already on the\n"
        "device, so decline any offer to download more. A first result that is a\n"
        "different place of a similar name is a real risk; read the result row\n"
        "before tapping it. Coordinates must survive to five decimal places, so\n"
        "never retype or round them."
    ),
    "FilesMoveFile": (
        "Skill: move a file between folders in the Files app\n"
        "\n"
        "Use when a request asks to move, relocate, or transfer a named file from\n"
        "one folder of the phone's storage area to another.\n"
        "\n"
        "1. Open Files. Its first screen is not the storage area the request\n"
        "   means, and the storage volumes are listed somewhere off it.\n"
        "2. Record the file name, the source folder and the destination folder\n"
        "   exactly; copy all three, the extension included.\n"
        "3. The source folder holds other files as well, so match the row by its\n"
        "   name. Moving is a two-part operation: the file is handed to the app\n"
        "   from the source folder, and the move only completes in the\n"
        "   destination folder. Between the two parts the screen shows no sign\n"
        "   that anything is pending.\n"
        "4. Check the file is in the destination and gone from the source before\n"
        "   reporting success.\n"
        "\n"
        "Cautions: drive the app's UI only. The other files belong to the\n"
        "environment and must survive untouched. A copy is not a move: the file\n"
        "must be gone from the source folder as well."
    ),
}

SKILL_SUFFIX = (
    "A skill entry from memory may help. It was written for this family of\n"
    "tasks, not for this specific screen, so parts may not match what you\n"
    "see:\n"
    "\n"
    "{doc}\n"
    "\n"
    "Follow it where it applies and work out the rest from the interface."
)

# The floor probe: no task. The harness shows one trivial observation (the
# home screen) and the episode exits; whatever tokens this costs is harness
# overhead, not task work, and must be subtracted from every condition before
# shares are computed.
FLOOR_PROMPT = (
    'Reply with exactly {"action_type": "status", "goal_status": "complete"}'
    " and nothing else. Do not take any other action."
)


def told_procedure(family: str) -> str:
    return TOLD_PROCEDURE[family]


def mid_steps(family: str) -> str:
    return MID_STEPS[family]


def skill_doc(family: str) -> str:
    return SKILL_DOC[family]


def approx_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token heuristic)."""
    return len(text) // 4


def build_prompt(
    condition: str,
    family: str,
    goal_text: str,
    doc_text: str | None = None,
) -> str:
    """The full user-side prompt for one condition. floor has no task.

    ``doc_text`` is required by, and only used by, the ``doc`` condition: the
    compiled operation document is injected at exactly the position, and with
    exactly the framing, that ``told`` uses for its hand-written procedure, so
    a doc-arm episode differs from a told-arm episode only in the text of that
    block.
    """
    if condition == "floor":
        return FLOOR_PROMPT
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}; expected one of {CONDITIONS}")
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; expected one of {FAMILIES}")
    if condition == "doc" and not (doc_text or "").strip():
        raise ValueError("condition 'doc' needs a compiled document (doc_text)")
    prompt = COMMON_TEMPLATE.format(goal=goal_text.rstrip("\n"))
    if condition == "told":
        prompt += "\n" + told_procedure(family) + "\n"
    elif condition == "doc":
        # Same slot and same shape as told; only the source of the text differs.
        prompt += "\n" + doc_text.strip("\n") + "\n"
    elif condition == "mid":
        prompt += "\n" + MID_SUFFIX.format(steps=mid_steps(family)) + "\n"
    elif condition == "skill":
        prompt += "\n" + SKILL_SUFFIX.format(doc=skill_doc(family)) + "\n"
    return prompt
