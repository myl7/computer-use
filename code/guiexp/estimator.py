"""Split a reactive episode's token log into discovery vs execution cost.

Policy background
-----------------
The paper prices the *discovery* cost of a GUI procedure separately from its
*execution* cost. In a reactive episode the token log is one stream; the split
point is the moment the agent stops finding out what to do and starts doing
it. The old openapps-exp harness (``discovery_cost.py``) cut at the first
tool call that carried all six calendar field values; the new guiexp harness
emits step-primitive actions (click / fill / select / scroll / goto / press /
done, element bids, per-step ``usage`` in the JSONL), so the cut is
re-defined as:

    the FIRST STATE-CHANGING ACTION -- the first ``fill(...)`` or
    ``select(...)`` (both write values into the form), or the first ``click``
    on a submit/save-type control (keyword list below).

Tokens of steps strictly before the cut are *discovery*; the cut step and
everything after are *execution* (the agent emits the cut action only once it
already knows the procedure). Failed actions never cut: a step whose
``obs_meta.last_action_error`` is set changed nothing (stale bid, malformed
action), so the split looks past it to the next effective action. ``scroll``
/ ``goto`` / ``press`` / ``done`` never count as state-changing. (``press``
CAN submit a form via Enter; that is not detectable from the JSONL and is a
documented limitation: the cut then falls on the next state-changing action,
mis-attributing at most that one step to discovery.)

Submit/save keyword list
------------------------
A click cuts only when the clicked control's identity matches a keyword
(matched case-insensitively, on word boundaries, against the element's role
and accessible name -- NOT its HTML type):

    SUBMIT_KEYWORDS = ("submit", "save", "create")

Grounded in the calendar app's actual controls:

    cuts:    "Create"  (wizard screen 3, id=wizard-create)
             "Submit"  (sectioned and single_page save buttons)
    ignores: "Next"       (wizard navigation, wizard-next-1/2 -- HTML
                           type="submit" but navigation-only; this is why we
                           match the accessible name, not the type attribute)
             "More details" (sectioned reveal, id=reveal-details)
             "Add Event"    (footer LINK with role="button" that navigates to
                             the form -- why "add" is deliberately NOT a
                             keyword)
             "Delete Event", "Close", "x" (destructive/dialog controls)

``element_labels``
------------------
Trajectories record only the bid of a click, not the element's role/name, so
click classification needs a side channel: ``element_labels`` maps
``bid (str) -> descriptor`` where the descriptor is the element's AX line
(``"[49] button 'Create'"``), a ``"role 'name'"`` pair, or a bare name. For
real runs build it from the AX-tree dumps of the observations (or re-derive
from the app). A click whose bid has no label is conservatively treated as
NON-submit (navigation) and counted in ``unresolved_clicks``. ``fill`` and
``select`` need no labels -- the action itself is the state change.

Floor
-----
The *floor* is the token cost of harness overhead: one model call that sees a
single trivial observation and no goal (condition ``floor``; the runner
records it as a one-step trajectory). ``floor_estimate`` is:

    1. ``floor_tokens`` when passed explicitly -- preferred for paper numbers;
       use the paired floor run for the same layout (e.g.
       ``experimental-results/guiexp/wizard_floor_s0_mock``).
    2. otherwise the trajectory's own step-1 usage. For a ``floor``-condition
       trajectory that is exact (the run IS the measurement); for any other
       condition it is an UPPER BOUND (step 1 also carries the goal text).

Floor subtraction: ``split_trajectory(..., floor_tokens=F)`` subtracts ``F``
from the discovery side only (the floor is spent inside step 1, which is
always before the cut), clamped at zero; ``estimated_share`` is then computed
on the floor-subtracted basis. The un-subtracted value is kept in
``discovery_tokens_raw``.

Negative control (design note)
------------------------------
On ``told`` runs the procedure is given to the agent up front, so its first
procedural action is already state-changing: the cut lands at (or within a
step of) step 1 and the discovery side contains nothing but harness overhead,
i.e. the floor. The Phase-1 validation invariant is:

    for every told run: split_trajectory(traj, floor_tokens=<paired floor>)
    gives estimated_share ~= 0  (discovery_tokens clamps to ~0 after floor
    subtraction, and cut_step <= 2).

A told run with a materially positive floor-subtracted share means either the
cut rule is wrong or the condition prompt is leaking the procedure -- both
are experiment-stopping findings, which is exactly what a negative control
is for.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from .actions import ActionError, parse_action, parse_first_action

# Click targets that count as state-changing (see module docstring for the
# grounding in the calendar app's controls and the deliberate exclusions).
SUBMIT_KEYWORDS = ("submit", "save", "create")

# Navigation/reveal labels that must never match, kept for documentation and
# for tests; the allowlist above already excludes them.
NON_SUBMIT_LABELS = ("Next", "More details", "Add Event", "Delete Event", "Close")


def _load_records(trajectory_jsonl) -> list[dict]:
    """Accept a JSONL path or an already-decoded iterable of records."""
    if isinstance(trajectory_jsonl, (str, Path)):
        with Path(trajectory_jsonl).open() as fh:
            return [json.loads(line) for line in fh if line.strip()]
    return [rec if isinstance(rec, dict) else dict(rec) for rec in trajectory_jsonl]


def _step_tokens(rec: dict) -> int:
    usage = rec.get("usage") or {}
    return (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)


def _parse_step_action(rec: dict):
    """The Action a step record executed, or None if it executed none."""
    for candidate in (rec.get("action"), rec.get("action_raw")):
        if not candidate or not isinstance(candidate, str):
            continue
        for parser in (parse_action, parse_first_action):
            try:
                return parser(candidate)
            except ActionError:
                continue
    return None


def _action_failed(rec: dict) -> bool:
    return bool((rec.get("obs_meta") or {}).get("last_action_error"))


def _label_for(bid: str, element_labels: dict | None) -> str | None:
    if not element_labels:
        return None
    return element_labels.get(bid) or element_labels.get(str(bid))


def _is_submit_label(label: str | None, keywords: Iterable[str]) -> bool:
    if not label:
        return False
    text = label.lower()
    return any(re.search(rf"\b{re.escape(kw.lower())}\b", text) for kw in keywords)


def split_trajectory(
    trajectory_jsonl,
    *,
    floor_tokens: int | None = None,
    submit_keywords: tuple[str, ...] = SUBMIT_KEYWORDS,
    element_labels: dict | None = None,
) -> dict:
    """Split one trajectory's tokens into discovery vs execution.

    See the module docstring for the cut rule, the submit/save keyword list,
    the ``element_labels`` side channel, floor semantics, and the told-run
    negative-control invariant.

    Returns a dict with (at least)::

        discovery_tokens   tokens before the cut (floor-subtracted iff
                           floor_tokens was given; clamped at 0)
        execution_tokens   tokens of the cut step and after
        cut_step           step number of the first state-changing action,
                           or None when the episode never changed state
        cut_action         that action's rendered string, or None
        floor_estimate     floor_tokens if given, else step 1's usage (exact
                           for floor-condition runs, upper bound otherwise)
        estimated_share    discovery / (discovery + execution) on the same
                           (floor-subtracted iff applicable) basis; None when
                           the trajectory holds no tokens

    plus ``discovery_tokens_raw``, ``floor_subtracted``, ``unresolved_clicks``,
    ``condition``, ``n_steps`` and ``total_tokens`` for context.
    """
    records = _load_records(trajectory_jsonl)
    steps = [r for r in records if "step" in r]  # the final record has none
    final = next((r for r in records if r.get("record_type") == "final"), {})
    condition = final.get("condition")

    cut_index: int | None = None
    cut_action: str | None = None
    unresolved_clicks = 0
    for i, rec in enumerate(steps):
        action = _parse_step_action(rec)
        if action is None or _action_failed(rec):
            continue  # no action, or an action that changed nothing
        if action.name in ("fill", "select"):
            cut_index, cut_action = i, action.render()
            break
        if action.name == "click":
            label = _label_for(action.args[0], element_labels)
            if label is None:
                unresolved_clicks += 1  # conservatively: navigation
                continue
            if _is_submit_label(label, submit_keywords):
                cut_index, cut_action = i, action.render()
                break

    tokens = [_step_tokens(r) for r in steps]
    if cut_index is None:
        # No state change at all: everything is discovery, the cut is at the
        # (virtual) end of the trajectory.
        discovery_raw, execution = sum(tokens), 0
        cut_step = None
    else:
        discovery_raw, execution = sum(tokens[:cut_index]), sum(tokens[cut_index:])
        cut_step = steps[cut_index].get("step")

    if floor_tokens is not None:
        floor_estimate = floor_tokens
    else:
        # Exact for floor-condition runs (the trajectory IS the measurement);
        # for other conditions an upper bound (step 1 also carries the goal).
        floor_estimate = tokens[0] if tokens else 0

    floor_subtracted = floor_tokens is not None
    discovery = max(0, discovery_raw - floor_tokens) if floor_subtracted else discovery_raw

    total_after_floor = discovery + execution
    estimated_share = (discovery / total_after_floor) if total_after_floor > 0 else None

    return {
        "discovery_tokens": discovery,
        "execution_tokens": execution,
        "cut_step": cut_step,
        "cut_action": cut_action,
        "floor_estimate": floor_estimate,
        "estimated_share": estimated_share,
        "discovery_tokens_raw": discovery_raw,
        "floor_subtracted": floor_subtracted,
        "unresolved_clicks": unresolved_clicks,
        "condition": condition,
        "n_steps": len(steps),
        "total_tokens": sum(tokens),
    }
