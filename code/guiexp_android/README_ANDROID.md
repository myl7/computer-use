# guiexp_android — the Android arm of the guiexp protocol

Wires android_world's M3A-style agent loop to the guiexp measurement
protocol: five prompt conditions x seven task families on the local
`AndroidWorldAvd` emulator, per-step observations, per-call usage recording,
and the SAME trajectory JSONL schema as the web-side `guiexp/` package so
later compile/estimate stages treat both arms identically. Zero LLM API
calls in tests (canned model stub only; real runs happen later).

Families (from guiexp/ANDROID_SETUP.md, all LLM-free oracles):

| family | app | oracle |
|---|---|---|
| `ContactsAddContact` | Google Contacts | contacts content-provider query |
| `SimpleCalendarAddOneEvent` | Simple Calendar Pro | app SQLite events diff |
| `MarkorCreateNote` | Markor | file exists + content fuzzy match |
| `MarkorDeleteNote` | Markor | file gone, the noise notes survive |
| `OsmAndFavorite` | OsmAnd | `favorites.gpx` parse, coordinate match |
| `OsmAndMarker` | OsmAnd | `map_markers` SQLite row, coordinate match |
| `FilesMoveFile` | Files | adb `ls`: absent in source, present in destination |

Per-family goal templates, parameters, told step lists and the commands a
device job runs are in `docs/android-families-wiring.md`.

## Boot / shutdown

Everything lives where `guiexp/ANDROID_SETUP.md` put it: SDK under
`third-party/android-sdk`, android_world clone at `third-party/android_world`
installed into `.venv-android` (Python 3.11), AVD `AndroidWorldAvd`
(API 33), benchmark apps already installed (`perform_emulator_setup` was run
once — do not repeat it).

The harness boots the emulator itself when no `emulator-5554` is attached,
with the exact command from ANDROID_SETUP.md (`-no-window -no-audio
-no-boot-anim -no-snapshot -no-metrics -grpc 8554`; the `-grpc` flag is
required by the a11y forwarding app), and kills it with `adb emu kill` at
the end **only if this process started it** — an emulator someone else
started is never touched. To boot/kill by hand:

```bash
source third-party/android-sdk/env.sh
$ANDROID_HOME/emulator/emulator -avd AndroidWorldAvd -no-window -no-audio \
    -no-boot-anim -no-snapshot -no-metrics -grpc 8554 &
adb shell getprop sys.boot_completed   # wait for 1 (~20-30 s)
adb emu kill                            # clean shutdown
```

android_world is always handed the `adb-monkey-shim` path as its adb (the
transparent wrapper that rewrites the broken-on-this-machine `monkey`
launcher calls; ANDROID_SETUP gotcha #2).

## Running

```bash
cd computer-use
../.venv-android/bin/python -m guiexp_android.runner \
    --family ContactsAddContact --condition discover --seed 0 \
    --model z-ai/glm-4.7-flash --obs-mode screenshot+ax --max-steps 30
```

Credentials: `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` from the
environment, read only when a real client is constructed; never logged.
Output goes to `experimental-results/guiexp_android/<family>_<condition>_s<seed>_<model>/`
(`trajectory.jsonl` + raw per-step PNGs). `--mock` runs the offline canned
model; `--keep-emulator` leaves an emulator the run booted up (default:
shut it down again).

Tests (same venv, no LLM calls; set `ANDROID_EXP_SKIP_EMULATOR=1` to skip
the emulator round-trip):

```bash
cd computer-use
../.venv-android/bin/python -m pytest guiexp_android/tests -q
```

The e2e test boots the emulator if needed and kills it afterwards (again:
only if it booted it).

## The protocol pieces

* **Action space** (`actions.py`): android_world's `json_action.JSONAction`
  space, element-index based exactly like M3A — the index is the element's
  position in the a11y `ui_elements` list, the same number drawn on the SoM
  screenshot and printed in the numbered element list. Reply contract is the
  web side's: exactly one action per reply (`action: <json>` or bare JSON).
  Parsing reuses android_world's `agent_utils.extract_json`.
* **Observations** (`android_env.py`): raw screenshot PNG, a SoM screenshot
  (android_world's own `m3a_utils.add_ui_element_mark`: box + numeric index
  per valid element — SoM on Android IS practical, so screenshot mode is
  image-only like the web's; the numbered list is only sent in
  `screenshot+ax`), and the M3A-text-variant element list as `ax_tree_text`.
  So `obs_mode="screenshot"` = marked image, `"screenshot+ax"` = unmarked
  image + numbered list.
* **Agent** (`agent.py`): same message shape as web guiexp (system =
  action space + one-action-per-reply + where-am-I note; user = goal on
  first turn, then observation). Usage per call: prompt/completion tokens
  plus OpenRouter's `usage.cost` when present.
* **Conditions** (`conditions.py`): discover / told / mid / skill / floor,
  ported from `guiexp/conditions.py`. The `told` procedures were
  written by walking each app's real UI on this emulator (API 33, frozen
  October 2023): Google Contacts' Create contact screen (First name, Last
  name, Company, Phone with label spinner, ...), Simple Calendar Pro's
  New Event form (Task/Event sheet -> Title/Location/Description, date and
  time picker dialogs, duration expressed via the end time), and Markor's
  New File dialog (pre-filled Name + separate extension field -> editor ->
  Back saves). No instance values anywhere in told/mid/skill.
* **Runner** (`runner.py`): mirrors `guiexp/runner.py` step for step,
  including the capped parse-retry loop (retries are extra model calls on
  the same step), auto-termination at reward >= 1.0, and the floor probe
  (one observation, one call, exit).
* **Instances**: `instance_params(family, seed)` seeds the global `random`
  around android_world's own `generate_random_params()` (state saved and
  restored), so a seed binds the same instance on every machine and across
  conditions, like the web side's sha256 pool.

## The told admissibility gate (`told_check.py`)

`docs/android-told-diagnosis.md` section 7.2 found that a told procedure only
measures "cost without discovery" when it is at least as good as what the
agent finds by itself, and that two of the three original texts were not.
`told_check.py` is the gate: R1, a scripted replay of exactly the told steps
must reach the family's own oracle on every seen binding; R2, the told step
count must be no larger than the cheapest successful discover run of the same
family and model. `TOLD_SCRIPTS` holds each procedure as a step list of
`program_runtime` primitives, so step counts are checkable with no phone:

```bash
cd computer-use
../.venv-android/bin/python -m guiexp_android.told_check --all --steps-only
../.venv-android/bin/python -m guiexp_android.told_check --family MarkorCreateNote \
    --model z-ai/glm-5.3-flash --seeds 0,1,2 --keep-emulator   # + the replay
```

The calendar and markor told texts were replaced on 2026-09-09 with the
corrected versions from `docs/android-told-prompts-proposal.md` (14 and 6
steps, matching the cheapest successful discover runs). The superseded
strings stay in `conditions.TOLD_LEGACY` so the runs recorded under them
remain interpretable.

## Trajectory schema (identical to web guiexp)

Step record: `{"step", "action_raw", "action", "usage": {"prompt_tokens",
"completion_tokens", "cost_usd"}, "obs_meta": {"url", "screenshot_file",
"ax_chars", "last_action_error"}}` (+ `"retry"` on re-ask records). Final
record: `{"success", "total_tokens", "total_cost_usd", "condition",
"family", "seed", "model", "obs_mode", "task_id", "steps", "model_calls",
"record_type": "final"}`. `tests/test_runner_schema.py` extracts the record
keys from the LIVE `guiexp/runner.py` / `guiexp/agent.py` sources and
asserts our records match field-for-field, then cross-checks against an
on-disk web mock trajectory.

## Deviations from the web side (and why)

1. **`family` instead of `layout`** in the final record — the task-axis
   name for this arm; every other field name is identical.
2. **`obs_meta.url` carries the foreground activity**, not a URL — Android
   has no URLs; the key name is kept for schema equality. The agent's
   observation line says `Current app: <activity>`.
3. **`action` is a compact JSON string** (`{"action_type":"click","index":12}`),
   the Android action space, instead of the web's `click('42')` grammar.
4. **SoM is real** on Android (android_world ships the mark renderer), so
   screenshot mode does not need the numbered a11y list; the choice is
   documented in `agent.py`.
5. **First-run dialogs are pre-granted at env construction**
   (`pm grant` for Contacts/Calendar notifications, storage access for
   Markor). Upstream avoids these by keeping one emulator up for a whole
   benchmark; on a cold boot the first episode would otherwise be noisier
   than the rest. All grants are idempotent, failures ignored; the told
   text still mentions the notification dialog in case it appears.
6. **Markor's create flow is a dialog, not a "+ > Note" menu** on the
   installed version (2.10.9): pre-filled Name field + separate extension
   field + OK. The told text reflects the real flow.
7. **The canned stub is trivially scripted** (terminating status action or a
   caller-provided script) rather than a UI-solving policy like the web
   `MockOpenAI`; emulator interaction is covered by the e2e test instead.
8. **floor** replies with the Android terminating action
   (`{"action_type": "status", "goal_status": "complete"}`) instead of the
   web's `done()` — same probe, native syntax.

## The compile path (T1.4): compile -> gate -> deploy

The E2-equivalent of the web side's `guiexp/{compiler,gate_runner,deploy_runner}.py`,
mirrored onto the android_world families. A recorded reactive trajectory is
compiled into ONE parameterized function

    def program(device, binding: dict) -> bool

that completes the family's task for ANY binding, driving the phone through
a `ProgramDevice` (`program_runtime.py`): fresh a11y dumps as dicts,
`find(text=/hint=/contains=...)`, the JSONAction space via `execute({...})`,
and a raw `adb_shell` escape hatch. The judge is ALWAYS the family's own
android_world evaluator (`is_successful`), never a re-implementation.

Per-family binding contracts (android_world's own `generate_random_params`
keys): Contacts `{name, number}` (name = first + last, two fields),
Calendar `{year, month, day, hour, duration_mins, event_title,
event_description}` (duration = end time minus start), Markor
`{file_name, text}` (file_name includes the extension; the dialog splits
base and extension).

* **compiler.py** — `build_compile_prompt`: compact text-only step view
  (action JSON, the target element's text/hint when `--annotate`
  shadow-replays the trajectory on the emulator, typed values mapped to
  named placeholder expressions like `binding['name'].split()[0]`, and the
  foreground activity after each step); `compile_trajectory`: OpenRouter
  call with a 3-attempt transport retry, key never printed; `MockCompiler`:
  deterministic replay program with fresh element resolution (indexes are
  re-resolved against a fresh a11y dump at execution time — the same
  resolution the reactive harness used).
* **gate_runner.py** — 5 held-out bindings, seed-deterministic draws of the
  family's `generate_random_params` over a `guiexp_android:gate:` sha256
  namespace (disjoint from the trajectory's instance and de-duplicated),
  judged by the family oracle; writes `gate.json`.
* **deploy_runner.py** — per use: ONE extraction call (NL goal -> binding
  JSON) -> type check with ONE bounded retry (non-empty strings, integer
  coercion for the calendar's numeric fields, family shape rules: name
  exactly two words / file_name has an extension) -> program -> oracle.
  `MockExtractor` is the deterministic offline stand-in.

### AutoRPA-style build protocol (t16)

The stages that make our build accounting comparable to AutoRPA's, so a
break-even count can be reported under both. Spec:
`docs/autorpa-replication-protocol.md` section 5.

* **explore.py**: 3 building episodes per type (seeds 1, 2, 3; seed 0 stays
  the test instance), up to 2 reflection retries per failed episode, step cap
  `ceil(10 x complexity)` capped at 50. Recorded `t12_grid` discover episodes
  for a seed are reused at their recorded usage instead of being re-run.
* **translator.py**: one model call per effective action of a building
  trajectory. Text-shaped input (action JSON, the target element's a11y
  record, a compact before/after element-list diff from a zero-token shadow
  replay); output is a `device.find` plus action snippet. Writes
  `translation.json` with per-call usage.
* **compiler.py** additions: `build_builder_prompt` / `compile_trajectories`
  take k = 1, 2 or 3 building trajectories plus their translations and an
  `artifact` of `"code"` (a parameterized program) or `"doc"` (a plain-text
  operation document with parameter placeholders). `refine_artifact` is the
  repair loop's entry point. The single-trajectory `build_compile_prompt`
  path is unchanged.
* **verify_runner.py**: replay the program over the seen building instances
  in order, stop at the first failure, one analyzer call on the breakpoint
  observation and trace, a partial reactive resume charged in full, one
  builder refinement, up to M = 3, then the type is declared unautomatable.
  Program replays themselves cost zero model tokens. The held-out gate runs
  after the loop and both results are recorded.
* **conditions.py**: the `doc` condition injects a compiled document at
  exactly the slot `told` uses for its hand-written procedure, so
  `runner.py --condition doc --doc <path>` measures the text artifact's
  per-use cost `L_doc` on new bindings.
* **build_protocol.py**: the driver. Runs all of the above for one family and
  one model plus 30 deploy uses and 3 doc-arm episodes, and writes
  `build.json` with per-stage raw prompt / cached / completion tokens and N*
  under both the marginal and the AutoRPA build accounting.

CLI (same venv; the three stages share the emulator lifecycle convention —
boot if none attached, `adb emu kill` only for one the process started,
`--keep-emulator` to leave a booted emulator up):

```bash
cd computer-use

# compile (real model, with element annotations from a live shadow replay)
../.venv-android/bin/python -m guiexp_android.compiler \
    --model z-ai/glm-5.3-flash --family ContactsAddContact \
    --trajectory ../experimental-results/guiexp_android/t12_grid/z-ai_glm-5.3-flash/discover__ContactsAddContact__s0/trajectory.jsonl \
    --annotate --out ../experimental-results/guiexp_android/t14_compilepath/glm/contacts/attempt1 --keep-emulator

# gate the compiled program on 5 held-out bindings
../.venv-android/bin/python -m guiexp_android.gate_runner \
    --program .../attempt1/family_program.py --family ContactsAddContact \
    --out .../gate1.json --keep-emulator
#   or in one shot: --trajectory ... --model ... [--annotate] [--mock] --k 5

# deploy 30 natural-language uses through extract -> check -> program -> oracle
../.venv-android/bin/python -m guiexp_android.deploy_runner \
    --program .../attempt1/family_program.py --family ContactsAddContact \
    --model z-ai/glm-5.3-flash --uses 30 --role deploy30-best \
    --out .../deploy30/deploy.json --keep-emulator
#   fully offline variant: --mock (MockExtractor)

# offline end-to-end check of the whole chain (no LLM, real emulator)
../.venv-android/bin/python -m pytest guiexp_android/tests/test_compile_unit.py -q
../.venv-android/bin/python -m pytest guiexp_android/tests/test_compile_integration_emulator.py -q
```

Output records are web-schema-compatible: `compile.json`
{program_source usage, cost_usd, annotations}, `gate.json`
{bindings_passed/total, detail, tokens, cost_usd}, `deploy.json`
{success_count/rate, d_tokens_mean, per-use error_type ∈ extraction_json |
type_check | program_error | oracle_fail}.
