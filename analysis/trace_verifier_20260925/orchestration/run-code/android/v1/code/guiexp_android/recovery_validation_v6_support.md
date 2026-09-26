# V6 support readiness (offline only)

## Canonical public-file initialization

`recovery_validation_v6_support.canonical_noise_setup()` is a temporary,
source-hash-guarded replacement for AndroidWorld's noise-file generator. It keeps
its random naming draws and creates the resulting names in sorted order. It
restores the original function on success or exception, and rejects nested use
or a changed upstream implementation. All current upstream callers resolve the
function through `user_data_generation`, so the scoped replacement reaches them.

Integration point for a future harness:

```python
task.params['seed'] = seed
with canonical_noise_setup():
    env.reset(task)
```

Use an exclusive, single-threaded initialization process. This support changes
only noise file creation order. It does not reset apps, seed other generators,
or prove complete device equivalence. Keep the V5 app-snapshot reset and initial
public-state fingerprints, and reject mismatched pairs. Before paid execution,
run each proposed task binding in separate fresh Python processes and compare
complete checked public-state path/content maps plus goal/effect bindings.

The offline regression runs the real upstream naming and `create_file` content
generator in six separate Python processes with three distinct PYTHONHASHSEED
values. Only device writes are mocked. It reproduces the old content permutation
and confirms canonical output equality, equal filenames, and equal content
multisets. Two tests pass. No emulator validation has been performed.

## Structured action requests

V5 `AGENT_PROMPT` asks for JSON in text. `model_call` uses
`recovery_validation_budget_v4.BudgetClient.complete`, which has no
`response_format`, `tools`, or `tool_choice` argument. The payload's
`provider.require_parameters=True` therefore does not enforce structured action
output. `json_reply` can extract JSON from prose, but cannot repair absent JSON.

Saved V5 endpoint metadata advertises both `response_format` and
`structured_outputs` for GLM fireworks and baseten/fp8, and DeepSeek together,
wafer, and parasail/fp8. GLM siliconflow/fp8 has tools/tool_choice but no
response_format. DeepSeek siliconflow/fp8 has response_format but no
structured_outputs or tools. These are saved capability declarations, not a
live provider guarantee.

Narrow proposed implementation: a new V6 budget client accepting one fixed,
strict JSON schema for `{reason, action}`. Action branches specify action_type
and only their required arguments. Reject unknown keys and locally validate the
current element index and supported action before execution. Add response_format
before payload serialization, request hash, ledger reservation, and saved
request redaction. Do not inject it in the transport after reservation. Preserve
the same image/AX observation, token cap, reasoning setting and price-vector
constraints. Recheck endpoint capabilities and freeze a same-price compatible
subset. No fallback to unsupported endpoints or silent unstructured retries.
Record refusal/truncation/schema failure as billed protocol errors. Tool calls
are an alternative requiring a new response decoder and forced single-tool
selection. A JSON schema keeps the existing content decoder closest to V5.

This may reduce format failures. It does not establish valid indices, better
reasoning, lower total cost, or live provider support. No OpenRouter request or
metadata refresh was made for this support work.

## Existing native interruptions and transient UI facilities

| Facility | Exact source/API | Limits for future qualification |
|---|---|---|
| Android runtime permission dialog | `perturb.py:PermissionArm`, `PERMISSION_TARGETS`; `pm revoke`, `am force-stop`; restore `pm grant` | Targets Contacts/Calendar POST_NOTIFICATIONS and OsmAnd ACCESS_FINE_LOCATION. No target for either V5 family. Dialog appears only if the app requests permission after launch. Verify a raw screenshot/AX exposure. |
| Android low-battery warning | `perturb.py:BatteryArm`; `dumpsys battery unplug`, `dumpsys battery set level 5`; restore `dumpsys battery reset` | Framework chooses notification/dialog presentation. Battery level alone proves no modal exposure. Existing source describes a warning, not a guaranteed modal. |
| Notification/shade | `perturb.py:NotificationArm`; `cmd notification post`, `cmd statusbar expand-notifications` | Native SystemUI obstruction, not an app modal. Revert collapses shade and clears shell package notifications, including unrelated shell notifications. |
| Update prompt | `perturb.py:ARMS` entry `update_prompt` | Explicit `stand_in=True` notification. No genuine system-update modal implementation exists here. |
| App-native confirmation/pickers | `conditions.py` MarkorDeleteNote confirmation, Markor New File, SimpleCalendar date/time pickers/reminder prompt, OsmAnd favorite/download prompts | These are existing app workflow UI documented locally. They have no generic popup-injection API, and task-required dialogs must not be counted as external disruptions. Current availability needs live confirmation. |
| Font, density, locale, dark mode | `perturb.py:SettingsArm`, `DensityArm`, `LocaleArm`, `ThemeArm` | Configuration changes with apply/revert/fingerprint, not arbitrary native modal injection. Locale requires app restart. |
| Step scheduling | `perturb.py:StepInjector.after_action`, `arm_session` | Fires at an action count. Its `force_fire` can fire after early termination; future matched comparisons must distinguish late/missing exposure. |
| Semantic scheduling and raw exposure | `recovery_validation_v5.py:Perturbation.hook` | Existing notification after long press, Home after confirmation, 1.5-second wait, then raw observation before generic normalization. No delayed effect or network-commit emulator. |
| Async observations/waits | `recovery_validation_device.py:RecoveryDevice.observe`, `wait`, action wait hooks | `get_state(wait_to_stabilize=False)` plus bounded waits exposes timing. No reproducible async data-arrival/reordering injector found in the existing perturbation module. |

No new perturbation distribution or escalation was selected. A future protocol
must independently choose applicable interruptions and verify actual exposure,
restore behavior, and equal exposure across policies before interpreting results.

## Implemented strict-action API (follow-up)

`recovery_validation_budget_v6.StructuredActionClient(ledger, locks, env_path,
transport=None, timeout=120)` accepts an already-open original USD 30 ledger.
It does not instantiate a ledger. Pass the original full frozen lock pool so
its existing ledger lock hash remains unchanged. A request filters the pool to
endpoints advertising both `response_format` and `structured_outputs` and keeps
`require_parameters=True`, original full price constraints, vision input,
reservation accounting and billing checks. Filtering to a subset does not alter
the ledger's frozen quote metadata.

`client.complete_action(model, messages, max_tokens, episode, temperature=0,
reasoning_effort='low')` returns `{'response': raw_receipt, 'reply': {reason,
action}}`. The schema enters the payload before canonical serialization, hashing,
reservation and safe request persistence. `ActionReplyError` has `.response`,
`.generation_id` and `.billed=True` for settled schema, refusal, truncation or
structured-subset routing failures. Save that failure in the episode record and
stop or apply only the future protocol's declared policy. No retry is built in.
Unknown billing retains the original conservative reservation and raises the
existing billing exception. `client.complete(...)` remains the inherited
unstructured API for Python compilation. Local parsing enforces the frozen
schema; the executor must additionally validate current observation index bounds
and public-file inspection scope. The schema is Android-specific.

Ten focused mock tests pass, including schema-before-reservation hash equality,
safe receipt persistence, provider filtering, unsupported pool rejection before
reservation, known-bill malformed output, unknown-bill stop, output truncation,
argument validation and unchanged compilation requests. These tests use only
new temporary ledgers and fake credentials. Production budget.sqlite3 was not
opened by this work.

## Reusable local browser facilities (source inventory, no launch)

- `computer-use/guiexp/env.py:GuiEnv` already launches headless Chromium and
  returns PNG base64 plus BrowserGym marked AX, DOM extraction and element
  properties. `reset(task)` and `step(action_text)` are callable. The latter
  currently checks a hidden calendar-state reward before/after actions and
  auto-terminates on success, so it cannot be reused unchanged for a protocol
  requiring evaluator isolation until termination.
- `computer-use/guiexp/actions.py` supports click/fill/select/scroll/goto/press/
  done using observed element bids. `GuiEnv` adopts target=_blank popups. It has
  no explicit browser-native alert/confirm/prompt action contract. Such dialogs
  need explicit event capture/action handling before they can be a fair task.
- `computer-use/openapps-exp/oa_server.py:openapps_server(overrides, log_path)`
  starts the existing `third-party/openapps` local server and tears it down.
  Restart the process to reset app state. `paths.py` supplies the checkout and
  server interpreter. Local `.venv-gui/bin/python` imports Playwright,
  BrowserGym, PIL and NumPy successfully. The OpenApps interpreter and Chromium
  cache are present. No server/browser was launched to check runtime health.
- OpenApps already contains task-native modal UI:
  `third-party/openapps/src/open_apps/apps/todo_app/main.py` has `reveal-add` and
  `modal-next`; `computer-use/openapps-exp/todo_protocols.py:_modal` implements
  that multi-step flow. Select it with `apps/todo/appearance=modal`. Calendar
  single_page/sectioned/wizard flows have existing programs in
  `calendar_protocols.py`. These local controlled apps need no external account.
- `computer-use/guiexp/warex_proxy.py:WarexProxy(upstream, arm, kinds, seed,
  ceilings)` provides `start`, `begin_episode`, `arm_injection`, `end_episode`,
  `schedule`, `stop`. Existing kinds are network, latency_only, server, js and
  popup. Popup injects a visible DOM modal overlay with Close and ACCEPT
  controls. This is genuine on-page blocking UI, but an injected synthetic
  advertisement, not an app-native workflow or OS dialog. Request-count
  ceilings determine scheduling. Existing delays/errors are proxy responses,
  not evidence of a real app's natural failure distribution.
- The vendored AndroidWorld MiniWoB HTML assets include `login-user-popup.html`
  (focus-triggered popup with OK/Cancel), `click-dialog-2.html` (jQuery dialog),
  and delayed task variants. They are local source candidates with their own
  task/reward conventions; no adaptation was built or launch verified.

These facilities permit account-free local browser work, but task selection,
terminal evaluator isolation, reset equivalence, model accounting and modal
exposure matching still require a separately frozen design.
