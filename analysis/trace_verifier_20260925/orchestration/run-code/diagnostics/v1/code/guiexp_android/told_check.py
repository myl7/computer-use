"""Admissibility check for the ``told`` arm of the Android families.

``docs/android-told-diagnosis.md`` section 7.2 states two blocking gates on a
told procedure, and this module is their implementation:

    R1 replay validity  a scripted replay of exactly the told steps, with the
                        instance values substituted, must reach the family's
                        own oracle on every seen binding.
    R2 minimality       the told procedure's env-step count must be no larger
                        than the smallest step count among the SUCCESSFUL
                        discover runs of the same family and model.

The rule this module prints PASS/FAIL against is the conjunction: told must
pass the oracle by scripted replay and take no more steps than the cheapest
successful discover run.

Three pieces, deliberately separated so the cheap ones need no phone:

* :data:`TOLD_SCRIPTS` -- the told procedures as DATA, one hand-coded step
  list per family, each step naming a ``program_runtime`` primitive and the
  a11y criteria that pick its control. Step counts, and therefore R2, are
  computable from this table alone.
* :func:`cheapest_successful_discover` -- reads the recorded t12 grid under
  ``experimental-results/guiexp_android/t12_grid/<model>/discover__<family>__s*/``
  READ-ONLY and returns the cheapest successful run's step count.
* :func:`replay_told` -- the device-dependent half. It turns a step list into
  a ``program(device, binding)`` and runs it through the ordinary
  :class:`~guiexp_android.program_runtime.ProgramRunner`, so the judge is the
  family's own ``is_successful``, exactly as in the gate and deploy stages.
  A later job runs this on the emulator; nothing else here needs one.

    cd computer-use
    # step counts only, no emulator, no LLM
    ../.venv-android/bin/python -m guiexp_android.told_check --all --steps-only
    # the full gate for one family on the phone
    ../.venv-android/bin/python -m guiexp_android.told_check \\
        --family MarkorCreateNote --model z-ai/glm-5.3-flash --seeds 0,1,2
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import android_env

RESULTS_ROOT = android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
GRID_ROOT = RESULTS_ROOT / "t12_grid"
DEFAULT_SEEDS = (0, 1, 2)

PASS = "PASS"
FAIL = "FAIL"
PENDING = "PENDING"  # step counts checked, the device replay has not been run


# --------------------------------------------------------------- the scripts
#
# One entry per family. Each step is a dict:
#
#   op        one of open_app / click / long_press / input_text /
#             navigate_back / scroll  (all program_runtime primitives)
#   find      a11y criteria picking the control: text / contains / hint /
#             description / editable / clickable / long_clickable / scrollable,
#             plus "nth" (0-based, among the matches, default 0)
#   value     for input_text: what to type, as a binding reference
#   optional  True for a step that only fires when its control is on screen
#             (first-run dialogs, a scroll the target row may not need). An
#             optional step that does not fire is not a dead step and is not
#             counted; R1's "no state-preserving step" is about the required
#             ones.
#   skip_if_set  for input_text: skip when the field already holds the value
#             that would be typed. This is what "change it only if it does not
#             already hold ..." means in prose, and it is also R1's dead-step
#             rule: retyping a field with what it already says changes no
#             state, so the step must not run.
#   note      why this control and not the one beside it
#
# A ``find`` value or a ``value`` may itself be a reference:
#   {"field": "name", "transform": "first_word"}   one binding field
#   {"compute": "end_hour"}                        derived from the binding
#
# The criteria are the a11y labels the told texts name. Where the recorded
# trajectories did not persist ``ax_tree_text`` (see the diagnosis, section 6)
# they are the labels read off the stored PNGs, so the device replay is also
# the thing that confirms them.

TOLD_SCRIPTS: dict[str, list[dict]] = {
    "ContactsAddContact": [
        {"op": "open_app", "app_name": "Contacts"},
        {"op": "click", "find": {"contains": "Don't allow"}, "optional": True,
         "note": "first-run notification dialog; absent once the grant is applied"},
        {"op": "click", "find": {"contains": "Create contact"}},
        {"op": "input_text", "find": {"hint": "First name"},
         "value": {"field": "name", "transform": "first_word"}},
        {"op": "input_text", "find": {"hint": "Last name"},
         "value": {"field": "name", "transform": "last_word"}},
        {"op": "input_text", "find": {"hint": "Phone"},
         "value": {"field": "number"}},
        {"op": "click", "find": {"contains": "Save"}},
    ],
    "SimpleCalendarAddOneEvent": [
        {"op": "open_app", "app_name": "Simple Calendar Pro"},
        {"op": "click", "find": {"text": {"compute": "day"}},
         "note": "the day cell of the month grid; creating from the day view "
                 "pre-fills the date and skips the date picker entirely"},
        {"op": "click", "find": {"contains": "New Event"}},
        {"op": "click", "find": {"text": "Event"},
         "note": "not Task: the oracle reads the events table"},
        {"op": "input_text", "find": {"hint": "Title"},
         "value": {"field": "event_title"}},
        {"op": "input_text", "find": {"hint": "Description"},
         "value": {"field": "event_description"}},
        {"op": "click", "find": {"contains": "Start time"},
         "note": "the time on the start row; the row's a11y label reads "
                 "'Event start time'"},
        {"op": "click", "find": {"text": {"compute": "start_hour"}}},
        {"op": "click", "find": {"text": "OK"}},
        {"op": "click", "find": {"contains": "End time"},
         "note": "the time on the end row; the row's a11y label reads "
                 "'Event end time'. The end time IS the duration: there is no "
                 "duration field"},
        {"op": "click", "find": {"text": {"compute": "end_hour"}}},
        {"op": "click", "find": {"text": {"compute": "end_minute"}}},
        {"op": "click", "find": {"text": "OK"}},
        {"op": "click", "find": {"contains": "Save"}},
    ],
    "MarkorCreateNote": [
        {"op": "open_app", "app_name": "Markor"},
        {"op": "click", "find": {"contains": "Create a new file"},
         "note": "the round red + button; its a11y label reads "
                 "'Create a new file or folder'"},
        {"op": "input_text", "find": {"editable": True, "nth": 0},
         "value": {"field": "file_name", "transform": "basename"},
         "note": "the Name field, pre-filled with the placeholder my_note"},
        {"op": "input_text", "find": {"editable": True, "nth": 1},
         "value": {"field": "file_name", "transform": "extension"},
         "optional": True, "skip_if_set": True,
         "note": "the small extension field is pre-filled with .txt; the told "
                 "text says to change it only when the binding asks for a "
                 "different extension, so this step is skipped on a .txt "
                 "binding and the family costs 6 steps there"},
        {"op": "click", "find": {"text": "OK"}},
        {"op": "input_text", "find": {"editable": True, "nth": 0},
         "value": {"field": "text"}, "note": "the editor body"},
        {"op": "click", "find": {"contains": "Save"},
         "note": "the floppy-disk icon writes the file in one tap; Back needs "
                 "two presses because the first only closes the keyboard"},
    ],
    "MarkorDeleteNote": [
        {"op": "open_app", "app_name": "Markor"},
        {"op": "scroll", "direction": "down", "optional": True,
         "note": "only when the target row is below the fold"},
        {"op": "long_press", "find": {"description": {"compute": "markor_row"}},
         "note": "a file row carries no text; its a11y label is "
                 "'File <name> '. Matching the whole label rather than a "
                 "substring keeps the noise note a0pf_<name>.md, which "
                 "contains the target name, from being pressed instead"},
        {"op": "click", "find": {"contains": "Delete"}},
        {"op": "click", "find": {"text": "OK"}},
    ],
    "OsmAndFavorite": [
        {"op": "open_app", "app_name": "OsmAnd"},
        {"op": "click", "find": {"contains": "Skip"}, "optional": True,
         "note": "first-run map-download offer"},
        {"op": "click", "find": {"contains": "Search"}},
        {"op": "input_text", "find": {"editable": True, "nth": 0},
         "value": {"field": "location"},
         "note": "typed verbatim: the field takes both a place name and a "
                 "'lat, lon' pair"},
        {"op": "click", "find": {"contains": {"compute": "search_head"}, "nth": 0},
         "note": "the first result row"},
        {"op": "click", "find": {"contains": "Add to favorites"},
         "note": "the star, NOT Mark: only favorites.gpx is read by this "
                 "family's oracle"},
        {"op": "click", "find": {"contains": "Save"},
         "note": "the Favorite dialog keeps the name the app proposes"},
    ],
    "OsmAndMarker": [
        {"op": "open_app", "app_name": "OsmAnd"},
        {"op": "click", "find": {"contains": "Skip"}, "optional": True,
         "note": "first-run map-download offer"},
        {"op": "click", "find": {"contains": "Search"}},
        {"op": "input_text", "find": {"editable": True, "nth": 0},
         "value": {"field": "location"},
         "note": "typed verbatim: the field takes both a place name and a "
                 "'lat, lon' pair"},
        {"op": "click", "find": {"contains": {"compute": "search_head"}, "nth": 0},
         "note": "the first result row"},
        {"op": "click", "find": {"contains": "Mark"},
         "note": "the flag, NOT Add to favorites: only the map_markers table "
                 "is read by this family's oracle; no dialog follows"},
    ],
    "FilesMoveFile": [
        {"op": "open_app", "app_name": "Files"},
        {"op": "click", "find": {"contains": "Show roots"},
         "note": "the hamburger; the storage area is not on the first screen"},
        {"op": "click", "find": {"contains": "sdk_gphone"},
         "note": "the storage area's drawer entry is named after the emulator "
                 "image, so the goal template's sdk_gphone_x86_64 reads "
                 "sdk_gphone64_arm64 on an arm64 AVD; the common prefix "
                 "sdk_gphone picks the same single entry on either host"},
        {"op": "click", "find": {"text": {"field": "source_folder"}}},
        {"op": "long_press", "find": {"text": {"field": "file_name"}}},
        {"op": "click", "find": {"contains": "More options"}},
        {"op": "click", "find": {"text": "Cut"},
         "note": "Copy would leave the source file in place and fail the oracle"},
        {"op": "navigate_back",
         "note": "back to the storage area's top level; the clipboard is "
                 "invisible from here on"},
        {"op": "click", "find": {"text": {"field": "destination_folder"}}},
        {"op": "click", "find": {"text": "Paste"},
         "note": "the Paste control exists only while a cut file is pending"},
    ],
}


# ------------------------------------------------------------- value binding

def _first_word(value) -> str:
    return str(value).split()[0]


def _last_word(value) -> str:
    return str(value).split()[-1]


def _basename(value) -> str:
    text = str(value)
    return text.rsplit(".", 1)[0] if "." in text else text


def _extension(value) -> str:
    text = str(value)
    return "." + text.rsplit(".", 1)[1] if "." in text else ""


TRANSFORMS = {
    "verbatim": str,
    "first_word": _first_word,
    "last_word": _last_word,
    "basename": _basename,
    "extension": _extension,
}


def _end_minutes(binding: dict) -> int:
    return int(binding["hour"]) * 60 + int(binding["duration_mins"])


COMPUTED = {
    "day": lambda b: str(int(b["day"])),
    "start_hour": lambda b: str(int(b["hour"])),
    "end_hour": lambda b: str((_end_minutes(b) // 60) % 24),
    "end_minute": lambda b: f"{_end_minutes(b) % 60:02d}",
    # a place name gives its first token, a "lat, lon" pair gives the latitude;
    # either is enough to pick the first search result out of the list
    "search_head": lambda b: str(b["location"]).split(",")[0].strip(),
    # Markor's file rows expose no text, only the a11y label "File <name> "
    "markor_row": lambda b: f"File {b['file_name']}",
}


def resolve(spec, binding: dict):
    """A literal, a binding field, or a value computed from the binding."""
    if not isinstance(spec, dict):
        return spec
    if "compute" in spec:
        return COMPUTED[spec["compute"]](binding)
    if "field" in spec:
        return TRANSFORMS[spec.get("transform", "verbatim")](binding[spec["field"]])
    raise ValueError(f"unresolvable value spec {spec!r}")


_FLAG_KEYS = ("editable", "clickable", "long_clickable", "scrollable", "selected", "checked")
_TEXT_KEYS = ("text", "hint", "description")


def matching_indexes(elements: list[dict], criteria: dict, binding: dict) -> list[int]:
    """Every element index matching ``criteria``, in screen order.

    Text comparisons are case-insensitive; ``contains`` is a substring test
    over text + hint + description, the same shape ``ProgramDevice.find``
    uses. ``nth`` is applied by the caller, not here.
    """
    hits = []
    for element in elements:
        ok = True
        for key in _FLAG_KEYS:
            if key in criteria and bool(element.get(key)) is not bool(criteria[key]):
                ok = False
                break
        if not ok:
            continue
        for key in _TEXT_KEYS:
            if key not in criteria:
                continue
            want = str(resolve(criteria[key], binding)).strip().lower()
            if str(element.get(key) or "").strip().lower() != want:
                ok = False
                break
        if ok and "contains" in criteria:
            want = str(resolve(criteria["contains"], binding)).strip().lower()
            blob = " ".join(
                str(element.get(k) or "") for k in _TEXT_KEYS
            ).lower()
            ok = want in blob
        if ok:
            hits.append(element["index"])
    return hits


# ``ProgramDevice.elements`` dumps the tree with ``wait_to_stabilize=False``,
# so a screen read during a transition can be empty or half built. Re-read a
# bounded number of times before calling a control absent: a label that is
# genuinely wrong is still absent after the last read, and the step still
# fails with its step number.
RESOLVE_ATTEMPTS = 3
RESOLVE_RETRY_S = 1.0


def resolve_step_index(device, step: dict, binding: dict) -> int | None:
    """The element index this step acts on, or None when nothing matches.

    Re-reads the screen up to ``RESOLVE_ATTEMPTS`` times so a control that is
    merely late is not reported as missing.
    """
    criteria = dict(step["find"])
    nth = criteria.pop("nth", 0)
    for attempt in range(RESOLVE_ATTEMPTS):
        hits = matching_indexes(device.elements(), criteria, binding)
        if len(hits) > nth:
            return hits[nth]
        if attempt + 1 < RESOLVE_ATTEMPTS:
            time.sleep(RESOLVE_RETRY_S)
    return None


# ------------------------------------------------------------- step counting


def told_step_count(family: str) -> int:
    """Env steps the told procedure costs on a binding that needs no optional
    step. This is the number R2 compares against discovery."""
    return sum(1 for step in TOLD_SCRIPTS[family] if not step.get("optional"))


def told_step_count_max(family: str) -> int:
    """Upper bound: every optional step fires too."""
    return len(TOLD_SCRIPTS[family])


# ------------------------------------------------------ the scripted program


def script_program(family: str):
    """The told step list as a ``program(device, binding) -> bool``.

    Shaped exactly like a compiled family program, so it runs through the
    ordinary :class:`ProgramRunner` and is judged by the family's own oracle.
    """
    steps = TOLD_SCRIPTS[family]

    def program(device, binding: dict) -> bool:
        for position, step in enumerate(steps, start=1):
            op = step["op"]
            if op == "open_app":
                device.open_app(step["app_name"])
                continue
            if op == "navigate_back":
                device.navigate_back()
                continue
            if op == "scroll":
                if step.get("optional") and not _scrollable(device):
                    continue
                device.scroll(step.get("direction", "down"))
                continue
            index = resolve_step_index(device, step, binding)
            if index is None:
                if step.get("optional"):
                    continue
                raise ValueError(
                    f"told step {position} ({op}) found no element matching "
                    f"{step['find']}"
                )
            if op == "click":
                device.click(index=index)
            elif op == "long_press":
                device.long_press(index=index)
            elif op == "input_text":
                value = str(resolve(step["value"], binding))
                if step.get("skip_if_set") and _field_holds(device, index, value):
                    continue
                device.input_text(value, index=index)
            else:
                raise ValueError(f"told step {position}: unknown op {op!r}")
        return True

    program.__doc__ = f"scripted told replay for {family}"
    return program


def _scrollable(device) -> bool:
    return any(e.get("scrollable") for e in device.elements())


def _field_holds(device, index: int, value: str) -> bool:
    """True when the field at ``index`` already reads ``value`` (so typing it
    again would be a state-preserving, and therefore dead, step)."""
    for element in device.elements():
        if element["index"] == index:
            return str(element.get("text") or "").strip() == value.strip()
    return False


# --------------------------------------------------- the recorded grid (RO)


def discover_runs(family: str, model: str, grid_root: Path | str | None = None) -> list[dict]:
    """Final records of the recorded discover episodes for this cell.

    Read-only: the t12 grid under experimental-results is never written here.
    """
    root = Path(grid_root) if grid_root else GRID_ROOT
    directory = root / model.replace("/", "_")
    runs = []
    for path in sorted(directory.glob(f"discover__{family}__s*/trajectory.jsonl")):
        final = _final_record(path)
        if final is None:
            continue
        runs.append({
            "seed": final.get("seed"),
            "success": bool(final.get("success")),
            "steps": final.get("steps"),
            "total_tokens": final.get("total_tokens"),
            "trajectory": str(path),
        })
    return runs


def _final_record(path: Path) -> dict | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("record_type") == "final":
            return record
    return None


def cheapest_successful_discover(
    family: str, model: str, grid_root: Path | str | None = None
) -> dict | None:
    """The successful discover run with the fewest env steps, or None."""
    successes = [r for r in discover_runs(family, model, grid_root)
                 if r["success"] and isinstance(r["steps"], int)]
    if not successes:
        return None
    return min(successes, key=lambda r: (r["steps"], r["seed"]))


# ---------------------------------------------------------------- the device


def replay_told(
    family: str,
    seeds,
    env=None,
    runner=None,
) -> list[dict]:
    """Run the told script on the seen bindings; judge with the family oracle.

    This is the only device-dependent function in the module. ``runner`` is
    injected by tests; in production it is a
    :class:`~guiexp_android.program_runtime.ProgramRunner` over a live
    :class:`~guiexp_android.android_env.AndroidWorldEnv`.
    """
    from .compiler import params_to_binding

    if runner is None:
        from .program_runtime import ProgramRunner

        if env is None:
            raise ValueError("replay_told needs an env or a runner")
        runner = ProgramRunner(env)
    program = script_program(family)
    results = []
    for seed in seeds:
        params = android_env.instance_params(family, seed)
        binding = params_to_binding(family, params)
        outcome = runner.run(program, binding, family, judge_params=params)
        results.append({
            "seed": seed,
            "binding": binding,
            "passed": bool(outcome["passed"]),
            "error": outcome.get("error"),
        })
    return results


# ------------------------------------------------------------- the verdict


def verdict(
    family: str,
    model: str,
    cheapest: dict | None,
    replay: list[dict] | None = None,
) -> dict:
    """PASS/FAIL against the rule.

    The rule: told must pass the oracle by scripted replay on every seen
    binding, and take no more steps than the cheapest successful discover run
    of the same family and model.

    Statuses: PASS, FAIL, and PENDING when the step comparison holds but the
    device replay has not been run yet.
    """
    told_steps = told_step_count(family)
    reasons: list[str] = []

    if cheapest is None:
        minimal = False
        reasons.append(
            "no successful discover run recorded for this family and model, "
            "so minimality cannot be certified; run the discover arm first"
        )
    else:
        minimal = told_steps <= cheapest["steps"]
        if not minimal:
            reasons.append(
                f"told takes {told_steps} steps, more than the cheapest "
                f"successful discover run (seed {cheapest['seed']}, "
                f"{cheapest['steps']} steps)"
            )

    if replay is None:
        replayed = None
    else:
        failures = [r for r in replay if not r["passed"]]
        replayed = not failures and bool(replay)
        if not replay:
            reasons.append("the scripted replay ran on no seed")
        for failure in failures:
            reasons.append(
                f"scripted replay failed the oracle on seed {failure['seed']}"
                + (f": {failure['error']}" if failure.get("error") else "")
            )

    if replayed is None:
        status = PENDING if minimal else FAIL
    elif minimal and replayed:
        status = PASS
    else:
        status = FAIL

    return {
        "family": family,
        "model": model,
        "told_steps": told_steps,
        "told_steps_max": told_step_count_max(family),
        "discover_steps": cheapest["steps"] if cheapest else None,
        "discover_seed": cheapest["seed"] if cheapest else None,
        "minimal": minimal,
        "replay_passed": replayed,
        "replay": replay,
        "status": status,
        "reasons": reasons,
        "record_type": "told_check",
    }


def check_family(
    family: str,
    model: str,
    seeds=DEFAULT_SEEDS,
    env=None,
    runner=None,
    grid_root: Path | str | None = None,
    steps_only: bool = False,
) -> dict:
    """One family's full check: step comparison, then the device replay."""
    cheapest = cheapest_successful_discover(family, model, grid_root)
    replay = None
    if not steps_only:
        replay = replay_told(family, seeds, env=env, runner=runner)
    return verdict(family, model, cheapest, replay)


def format_verdict(result: dict) -> str:
    told = result["told_steps"]
    discover = result["discover_steps"]
    baseline = f"{discover} (seed {result['discover_seed']})" if discover is not None else "none"
    head = (f"{result['status']:<7} {result['family']:<26} told {told:>3} steps, "
            f"cheapest discover {baseline}")
    lines = [head]
    for reason in result["reasons"]:
        lines.append(f"        - {reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", default=None, help="one family (default: --all)")
    parser.add_argument("--all", action="store_true", help="every family")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--seeds", default="0,1,2", help="seen bindings to replay")
    parser.add_argument("--steps-only", action="store_true",
                        help="skip the device replay; compare step counts only")
    parser.add_argument("--grid-root", default=None,
                        help="t12_grid root to read discover runs from (read-only)")
    parser.add_argument("--out", default=None, help="write the verdicts as JSON here")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family and args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")
    families = FAMILIES if (args.all or not args.family) else (args.family,)
    seeds = tuple(int(s) for s in args.seeds.split(","))

    env = None
    results = []
    try:
        if not args.steps_only:
            env = android_env.AndroidWorldEnv()
        for family in families:
            result = check_family(family, args.model, seeds=seeds, env=env,
                                  grid_root=args.grid_root, steps_only=args.steps_only)
            results.append(result)
            print(format_verdict(result), flush=True)
    finally:
        if env is not None:
            env.close()
            if not args.keep_emulator:
                env.stop_emulator()

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=1))
        print(f"wrote {out}")
    return 0 if all(r["status"] == PASS for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
