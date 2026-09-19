# AndroidWorld Local Setup (T0.5)

Date: 2026-09-05/06. Machine: macOS 26.6.2 (arm64), shared. Zero LLM API calls were
made for any of this — everything below is SDK/emulator/task-registry infrastructure
plus adb-driven validation.

## Result summary

- Android SDK installed under `third-party/android-sdk` (platform-tools, emulator,
  system images android-34 and android-33, google_apis, arm64-v8a).
- android_world (Google DeepMind, ICLR 2025) installed from a shallow clone at
  `third-party/android_world` into venv `.venv-android` (Python 3.11.16, uv).
  It is NOT on PyPI (`android-world` 404s); README install = clone +
  `pip install -r requirements.txt` + install package.
- AVD `AndroidWorldAvd` on **android-33** (upstream-tested config; see gotcha #1),
  Pixel 8 panel specs (1080x2400 @ 420dpi), 4 GB RAM, 6 GiB data partition.
- Emulator boots headless in ~20-30 s; `adb shell getprop sys.boot_completed` = 1.
- Benchmark app setup (`perform_emulator_setup` path) completed: 17 third-party task
  APKs installed + the accessibility-forwarding app `com.example.androidworld`.
- Task registry (LLM-free): **android_world family = 116 tasks across 19 apps**
  (91 "android" + 25 "information_retrieval"; miniwob family = 92, subset 62).
- LLM-free boot checks passed (see "Validation" below).
- Emulator was shut down cleanly at the end (`adb emu kill`).

## 1. Android SDK install (exact steps)

```bash
# cmdline-tools (mac arm64)
mkdir -p /Users/myl/app/computer-use/third-party/android-sdk && cd $_
curl -fSL -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip
unzip -q cmdtools.zip -d _tmp && mkdir -p cmdline-tools && mv _tmp/cmdline-tools cmdline-tools/latest
rmdir _tmp && rm cmdtools.zip

# env snippet (already written): third-party/android-sdk/env.sh
#   export ANDROID_HOME=/Users/myl/app/computer-use/third-party/android-sdk
#   export ANDROID_SDK_ROOT=$ANDROID_HOME
#   export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$ANDROID_HOME/cmdline-tools/latest/bin:$PATH"
source /Users/myl/app/computer-use/third-party/android-sdk/env.sh

yes | sdkmanager --licenses          # works with Homebrew OpenJDK 25
sdkmanager "platform-tools" "emulator" "system-images;android-34;google_apis;arm64-v8a"
sdkmanager "system-images;android-33;google_apis;arm64-v8a"   # added later, see gotcha #1
```

## 2. Python env + android_world

```bash
uv venv --python 3.11 /Users/myl/app/computer-use/.venv-android   # Python 3.11.16
cd /Users/myl/app/computer-use/third-party/android_world          # shallow clone of github.com/google-research/android_world
uv pip install --python ../../.venv-android/bin/python -r requirements.txt
uv pip install --python ../../.venv-android/bin/python -e .       # setup.py generates task protos via grpcio-tools
```

There is no `scripts/setup_avd.py` or in-emulator `run.sh` in the current repo —
AVD creation is via Android Studio/avdmanager, and app install is the
`--perform_emulator_setup` flag of `run.py` (which we invoke directly, see below).

## 3. AVD + emulator boot

```bash
source /Users/myl/app/computer-use/third-party/android-sdk/env.sh
# No "pixel_8" device profile ships with cmdline-tools 11076708 (max = pixel_7_pro).
# pixel_7 profile already has Pixel 8's exact panel (1080x2400 @ 420dpi).
echo no | avdmanager create avd -n AndroidWorldAvd \
    -k "system-images;android-33;google_apis;arm64-v8a" -d pixel_7
# config.ini tweaks (~/.android/avd/AndroidWorldAvd.avd/config.ini):
#   hw.ramSize=4096M  disk.dataPartition.size=6442450944  showDeviceFrame=no

# BOOT COMMAND (headless; -grpc 8554 is REQUIRED by android_world's a11y forwarding):
$ANDROID_HOME/emulator/emulator -avd AndroidWorldAvd -no-window -no-audio \
    -no-boot-anim -no-snapshot -no-metrics -grpc 8554 &

# wait for boot:
adb shell getprop sys.boot_completed    # -> 1  (reached in ~20-30 s)

# SHUT DOWN cleanly (emulator was started by us):
adb emu kill
```

## 4. Benchmark app installation (the `perform_emulator_setup` path)

```bash
cd /Users/myl/app/computer-use/third-party/android_world
/Users/myl/app/computer-use/.venv-android/bin/python -c "
from android_world.env import env_launcher
env = env_launcher.load_and_setup_env(
    console_port=5554,
    emulator_setup=True,          # installs APKs from storage.googleapis.com/gresearch/android_world/
    freeze_datetime=True,         # disables auto time/timezone, sets UTC, 24h clock
    adb_path='/Users/myl/app/computer-use/third-party/android-sdk/adb-monkey-shim',  # see gotcha #2
    grpc_port=8554)
env.close()"
```

APKs are downloaded on demand from `https://storage.googleapis.com/gresearch/android_world/<apk>`
to `$TMPDIR/android_world/app_data/` (605 MB cache) and installed via adb. Result:
17 task apps installed (`ca.zgrs.clipper`, `code.name.monkey.retromusic`,
`com.arduia.expense`, `com.dimowner.audiorecorder`, `com.example.androidworld` (a11y
forwarder), `com.flauschcode.broccoli`, `com.google.androidenv.miniwob`,
`com.simplemobiletools.{calendar,draw,gallery}.pro`, `com.simplemobiletools.smsmessenger`,
`de.dennisguse.opentracks`, `net.cozic.joplin`, `net.gsantner.markor`, `net.osmand`,
`org.tasks`, `org.videolan.vlc`). One benign warning: 1p Google Contacts first-run
setup ("Don't allow" dialog not found) — upstream code catches it and continues.

IMPORTANT: run emulator setup **once**; pass `emulator_setup=False` afterwards
(the AVD is already set up; reruns are merely wasteful, not destructive).

## 5. LLM-free validation performed

1. Registry: `TaskRegistry().get_registry('android_world')` loads — 116 task
   templates spanning 19 apps (miniwob: 92, miniwob_subset: 62, android: 91,
   information_retrieval: 25).
2. `ContactsAddContact` end-to-end WITHOUT any LLM:
   `generate_random_params()` -> `initialize_task` (cleared contacts via content
   provider) -> simulated the agent's WRITE with `contacts_utils.add_contact`
   (INSERT intent + Save click, ui_delay_sec=3.0) -> `is_successful` = **1.0**.
3. `SimpleCalendarAddOneEvent.initialize_task` seeds 10 random noise events into
   Simple Calendar Pro's SQLite DB via adb; `is_successful` correctly 0.0 before
   any agent action. No task initialization in the candidates requires an LLM.

## 6. Three candidate task families for the measurement protocol

All are parameterized WRITE templates with fully programmatic evaluators, and each
admits a hand-written step-by-step "told" instruction at field/button granularity.

### A. ContactsAddContact  (complexity 1.2)
- App: Google Contacts (system app on google_apis image).
- Source: `third-party/android_world/android_world/task_evals/single/contacts.py`
  (class) + `.../task_evals/common_validators/contacts_validators.py` (validator).
- Goal template: `"Create a new contact for {name}. Their number is {number}."`
- Randomization: `generate_random_params()` -> `user_data_generation.generate_random_name()`
  (e.g. "Isla Hernandez") + `generate_random_number()` (e.g. "+13840191614").
  `ContactsNewContactDraft` is the same write minus the save, with a first/last/
  phone/label schema.
- Success check: `contacts_utils.list_contacts` (adb `content query
  content://contacts/phones/`); exact match of (name, digits-only number).
- initialize_task: clears all contacts via content provider (LLM-free).
- Field-level "told" procedure is trivial: open Contacts > Create new > First name,
  Last name, Phone > Save.

### B. SimpleCalendarAddOneEvent  (complexity 3.4; +5 date-variant subclasses)
- App: Simple Calendar Pro (`com.simplemobiletools.calendar.pro`).
- Source: `third-party/android_world/android_world/task_evals/single/calendar/calendar.py`
  (+ `.../calendar/events_generator.py`, `.../common_validators/sqlite_validators.py`).
- Goal template: `"In Simple Calendar Pro, create a calendar event on {year}-{month}-{day}
  at {hour}h with the title '{event_title}' and the description '{event_description}'.
  The event should last for {duration_mins} mins."`
- Randomization: `events_generator.generate_event(random October-2023 timestamp)` ->
  title from prefix/name/subject pools, description from sentence pools, duration
  in {15,30,45,60} min; plus 0-20 "noise" events pre-seeded into the DB as distractors.
  Variants: `...InTwoWeeks`, `...Tomorrow`, `...RelativeDay`, `AddRepeatingEvent`,
  plus delete-family tasks.
- Success check: reads the app's SQLite events table over adb; diffs rows
  before/after and validates addition integrity against the reference row
  (`validate_event_addition_integrity`), extras-comparing repeat rule/interval.
- initialize_task: clears the events DB and inserts the noise rows via adb (LLM-free).
- "Told" procedure: open app > + > set date/time > Title > Description > duration > Save.

### C. MarkorCreateNote  (complexity 1.6; large Markor family: 17 tasks)
- App: Markor (`net.gsantner.markor`, markdown notes).
- Source: `third-party/android_world/android_world/task_evals/single/markor.py`
  (+ `.../common_validators/file_validators.py` `CreateFile`).
- Goal template: `"Create a new note in Markor named {file_name} with the following
  text: {text}"`
- Randomization: `_generate_random_note()` -> random file name + `.md`/`.txt`
  + one sentence from `user_data_generation.RANDOM_SENTENCES`.
  Siblings: `MarkorEditNote`, `MarkorAddNoteHeader`, `MarkorMoveNote`,
  `MarkorCreateFolder`, `MarkorDeleteNote`, `MarkorMergeNotes`, ...
- Success check: file exists under `device_constants.MARKOR_DATA` AND `adb shell cat`
  of its content fuzzy-matches the target text (`file_validators.CreateFile`).
- initialize_task / tear_down: clear Markor's data directory via adb (LLM-free).
- "Told" procedure: open Markor > + > Note > type file name > type body > save.

(All paths relative to repo root `/Users/myl/app/computer-use/`; the same files are
importable from `.venv-android` via the editable install.)

## 7. Gotchas / blockers found (and workarounds)

1. **API 34 (Android 14) image rejects `clipper.apk`**:
   `INSTALL_FAILED_DEPRECATED_SDK_VERSION: App package must target at least SDK
   version 23` — aborts the canonical `setup_apps` mid-run (RuntimeError).
   Upstream's tested config is Pixel 6 / **API 33**; switching the AVD to
   `system-images;android-33;google_apis;arm64-v8a` fixed it (clipper installs fine).
   The android-34 image is still downloaded in the SDK dir and can be removed with
   `sdkmanager --uninstall "system-images;android-34;google_apis;arm64-v8a"` (~1.4 GB).
2. **`monkey` is broken on this machine's emulator (37.1.11) for EVERY package**
   (exit 251 right after the "SYS_KEYS has no physical keys" warning; even as root,
   even bare `monkey 1`, on both API 33 and 34 images). android_world uses
   `adb shell monkey -p PKG -c...LAUNCHER 1` only as a one-shot app first-run launch
   (AudioRecorder, VlcApp, JoplinApp setups). Workaround:
   `third-party/android-sdk/adb-monkey-shim` — a transparent adb wrapper that rewrites
   exactly that invocation to `am start -n PKG/<launcher-activity>` and forwards
   everything else untouched. Pass its path as `adb_path`. No android_world code was
   modified.
3. **No "pixel_8" avdmanager device profile** in cmdline-tools 11076708 (max
   pixel_7_pro). Used `-d pixel_7` whose panel is identical to Pixel 8
   (1080x2400 @ 420dpi).
4. **`android-world` is not on PyPI** — install from the GitHub clone (done, editable).
5. **First UI-automation run after boot can be slow**: `contacts_utils.add_contact`
   with default `ui_delay_sec=1.0` missed the Save click on the cold Google Contacts
   first launch; `ui_delay_sec=3.0` works. Google Contacts' first-run dialog is also
   why setup logs a benign "Don't allow not found" warning.
6. **Emulator must run with `-grpc 8554`** for the a11y forwarding app — without it,
   `env_launcher` cannot get UI trees. Keep console port 5554 (first booted device).
7. Shared-machine etiquette: one emulator at a time, `-no-window -no-audio`, 4 GB RAM;
   shut down with `adb emu kill` (waits for clean exit).

## 8. Disk usage (total ~17.5 GB; 265 GB free before starting)

| Component | Size |
|---|---|
| `third-party/android-sdk` (2 system images + emulator + platform-tools) | 14 GB |
| `~/.android/avd/AndroidWorldAvd.avd` (userdata) | 2.3 GB |
| `$TMPDIR/android_world` (APK download cache) | 605 MB |
| `.venv-android` | 545 MB |
| `third-party/android_world` (clone) | 14 MB |
