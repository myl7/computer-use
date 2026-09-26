"""B' paired replays (t21): re-run each deploy-served binding as a full agent run.

A new subpackage for the paired-replay protocol extension
(body.tex Section `Paired replays`): for each of the six verified cells
(GLM: ContactsAddContact, MarkorDeleteNote, SimpleCalendarAddOneEvent,
OsmAndMarker; DS: ContactsAddContact, MarkorDeleteNote) the same thirty
bindings the deployed program served (the `t16_build` deploy records) are
re-run as complete discover-condition agent runs -- no program, no
document, the standard exploration configuration (`screenshot+ax`, the
family step cap, the COMMON_TEMPLATE goal block around the deploy goal).

Nothing in the existing modules is modified: this package monkeypatches
the `android_env` module constants (AVD name / ports) from the outside so
several independent emulator workers can run beside each other and beside
the other lane's AVDs on the same host.

Layout of the outputs (rsynced back to the Mac as
`experimental-results/guiexp_android/t21_paired_replay/`):

    <model_slug>/<Family>/use_<ii>/trajectory.jsonl   # runner.py schema
    <model_slug>/<Family>/use_<ii>/summary.json       # the paired record
    runs_worker<k>.jsonl                              # append-only ledger
"""

from __future__ import annotations

# The six verified cells, model first (GLM's four, then DS's two).
CELLS: tuple[tuple[str, str], ...] = (
    ("z-ai/glm-5.3-flash", "ContactsAddContact"),
    ("z-ai/glm-5.3-flash", "MarkorDeleteNote"),
    ("z-ai/glm-5.3-flash", "SimpleCalendarAddOneEvent"),
    ("z-ai/glm-5.3-flash", "OsmAndMarker"),
    ("deepseek/deepseek-v4-flash-vision-exp", "ContactsAddContact"),
    ("deepseek/deepseek-v4-flash-vision-exp", "MarkorDeleteNote"),
)

# The table shares these runs are compared against (numbers.tex,
# *ShareProg), recorded here so the analysis is self-contained.
TABLE_SHARE = {
    ("z-ai/glm-5.3-flash", "ContactsAddContact"): 1.00,
    ("z-ai/glm-5.3-flash", "MarkorDeleteNote"): 0.79,
    ("z-ai/glm-5.3-flash", "SimpleCalendarAddOneEvent"): 1.00,
    ("z-ai/glm-5.3-flash", "OsmAndMarker"): 1.00,
    ("deepseek/deepseek-v4-flash-vision-exp", "ContactsAddContact"): 0.96,
    ("deepseek/deepseek-v4-flash-vision-exp", "MarkorDeleteNote"): 0.93,
}
