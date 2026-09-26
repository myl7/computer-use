# Android downstream diagnostics handoff

The experiment manager owns launches and the exclusive diagnostics lane on
`cs659b`: AVD `traceVerifier-w8`, console 8644, ADB 8645, gRPC 8646. Never run
this queue on a main or repeat measurement AVD.

The queue consumes canonical initial identities from
`analysis/trace_verifier_20260925/results/evidence-selection.json`. Repeats are
excluded. Regenerate `manifest.json` with `build_manifest.py` whenever the
selection file changes. Reuse t18/t20 only when the selected program SHA-256
is byte-identical to the historical t16 program.

Current known plan:

- Reuse Qwen Contacts and Qwen Markor t18/t20.
- Rerun GLM Contacts and GLM Markor t18/t20.
- Keep `missing_selected_artifact_coverage` until remaining canonical initials
  appear in evidence selection.

For each rerun, `run_cell.py` materializes and hash-checks the selected program.
It executes each t18 arm/binding and each t20 binding in a separate child.
The parent has a 1200 s hard deadline. Every child atomically writes a unique
`.worker-*.json` before exit. The parent checkpoints `result.json` after every
probe. t18 has no model calls. t20 uses the original model and default
extraction settings, with one bounded type retry, and judges against original
task parameters rather than the extracted binding.

Use `--resume` after interruption. Resume validates the selected-program hash,
keeps only completed t18 rows whose raw or canonical fingerprint was restored,
keeps completed t20 rows, and reruns missing `(arm, binding_index)` keys. It
does not repeat completed extraction calls. Infrastructure rows remain under
`infrastructure_events` and never count as probe outcomes.

Pass the private credential file with `--env-file`; the parent loads both
`OPENROUTER_BASE_URL` and `OPENROUTER_API_KEY` before spawning workers, and
workers inherit that environment. A t20 row is resumable only when it is
complete, contains at least one model call, and its declared call count equals
the retained call ledger. Worker errors and zero-call rows are never outcomes.

Canonical fingerprint equivalences are explicit and narrow: unset
`system.font_scale` equals Android's default `1.0`; unset
`system.system_locales` equals `en-US`. Raw fingerprints and hashes remain in
each row. Every other field must match exactly.

After any worker error, timeout, or non-equivalent fingerprint restoration,
invoke the exclusive-lane recovery callback before another probe:

```text
/home/yulong/app/guiexp/android/analysis/trace_verifier_20260925/orchestration/recover_android_w8.sh
```

It validates the owned process, wipes and restarts only `traceVerifier-w8`,
waits for boot, and reruns benchmark setup. If recovery fails, stop the queue.
Do not retry a model extraction whose completed row already exists.

GLM Contacts output:

```text
experimental-results/trace_verifier_20260925/diagnostics/android/
  z-ai_glm-5.3-flash/ContactsAddContact/initial/result.json
```

The detached launch command is the manager-owned command previously recorded,
with the immutable bundle's `code` first in `PYTHONPATH`, the matching bundled
`runner/run_cell.py`, the canonical source result, the ports above,
`--replay-timeout-s 1200`, the recovery callback, and `--resume`.

Completion requires 50 counted t18 rows, five counted t20 rows,
`terminal_status=complete`, zero t18 model calls, and complete t20 per-call
usage/cost records. Sync the final result and immutable bundle manifest to the
local workspace before selecting it for aggregation.

Known parallel queue after the 2026-09-26 audit:

- GLM Contacts: resume 49 valid t18 rows; run one missing t18 and five t20.
- GLM Markor: run 50 t18 and five t20 on an independent AVD.
- GLM Calendar: run 50 t18 and five t20 on an independent AVD.

Each lane needs a distinct AVD, console/ADB/gRPC ports, lock, output directory,
and recovery script. A recovery script must validate the owned emulator PID
before wiping and must never accept another lane's AVD or ports.
