# Live billing and GUI recovery validation

The current frozen experiment is `experimental-results/pace_live_20260923/v3/manifest.json`.
The executable is `run_joint_v3.py`.
V2 remains stopped and preserved after its nineteenth request returned an unresolved provider error.
The earlier `v1` manifest is preserved without any paid execution.
A pre-call code audit found two acquisition-check omissions, so v2 adds unchanged-existing-state verification and rejects truncated compiler responses before validation.
No model result informed that revision.

## Questions and scope

1. Can a request-level guard enforce a cumulative declared billing allowance when actual OpenRouter charges vary?
2. Can a reactive agent finish from the actual GUI state left by a failed compiled program, with costs and task success measured against a matched agent-only run?

This is a component experiment, not a complete deployment of the PACE projected compilation rule.
The deployment program is a frozen existing GLM artifact, selected before this experiment.
Historical program acquisition is not charged again or represented as newly validated.
V3 adds one bounded model call that extracts program fields from the same natural-language task text given to the reactive agent.
The program receives only those extracted fields, not the evaluator binding.
Malformed extraction falls back to the reactive agent and remains charged.
The study supplies one family, so routing overhead is explicitly zero (tau0=m=0).
V2 supplied task fields directly and is not pooled with V3.

The separate cold-build pilot makes one newly charged compilation request after the first three completed agent-only traces.
Its trigger is pre-scheduled, not the PACE projected trigger.
It validates the candidate on two unseen bindings without repair or model calls.
This is a two-instance pilot gate, not the manuscript's four-of-five gate.
The candidate does not replace the fixed deployment program.
Its entire charge enters the declared-budget prefix.

## Frozen workload

- OpenApps calendar, one family, four independent goal bindings.
- Three conditions per binding: unchanged GUI, submit button renamed, blocking DOM dialog after four nonempty form fields.
- Twelve matched pairs, agent-only versus program followed by agent fallback after a program exception.
- Arm order alternates by binding seed.
- Four extra fixed program-fallback tasks use a tighter declared reference as an availability diagnostic.
- One GLM model, one pinned Io Net endpoint, temperature zero, 2,048 output tokens and 20 calls per service episode.
- One compiler call, 8,192 output tokens, and two no-model GUI validations.
- One program binding call, at most 1,024 output tokens, counted within the service cap.
- No physical request retry, no episode replay, no repair or condition change after model outcomes.

Each arm starts a fresh server and browser context.
The paired initial calendar-state and normalized accessibility-tree hashes must match.
Fallback uses the same browser and partially completed form without resetting state.
Six no-model qualification episodes established matching initial hashes, clean program completion, and actual program failures under both perturbations.
Qualification artifacts are under `qualification2_*`, with the summary in `qualification2.json`.
Earlier infrastructure qualification failures remain visible and are not evaluation episodes.

Success is evaluated only after a policy terminates or reaches a cap.
Exactly one additional event must match all six goal fields, and all existing events must remain unchanged.
Screenshots, accessibility trees, GUI results, submitted requests, response receipts, and generation IDs are retained.

## Executable billing limits

The pinned endpoint has a 262,144-token context bound.
Its uncached-input, cached-input, and output quotes are respectively USD 0.15, 0.03, and 0.50 per million tokens.
A service request reserves USD 0.040345600 using the full context bound and 2,048 output tokens.
A binding request reserves USD 0.039833600 with 1,024 output tokens.
Text-only binding and compilation use the same quote, reservation, and receipt validation as screenshot/AX action requests.
The request constrains provider selection, unit prices, and output length.
A paid request is sent only if the complete reservation fits both its episode remainder and the applicable cumulative allowance.
Actual settled charges release unused reservations.
V2 stopped all paid work after its first unknown bill.
V3 retains each unknown bill at its full reservation, terminates that task as a failure, and permits independent predeclared tasks only if their full liabilities still fit.
Three consecutive unresolved provider errors stop V3.
A violated price, identity, or reservation assumption stops immediately.

- Combined V2 plus V3 ceiling: USD 5.
- V2 known bills: USD 0.008983262; unknown retained reservation: USD 0.040345600.
- V3 ledger ceiling: USD 4.950671138, after subtracting the entire V2 exposure.
- Default service episode ceiling: USD 0.08.
- Compiler episode ceiling: USD 0.06.
- Main declared reference increment: USD 0.08 per arrival, with epsilon 0.25.
- Tight diagnostic increment: USD 0.02 per arrival, with epsilon 0.25.

The main default service is affordable under the per-arrival allowance, although a truncated service can fail the task.
The tight diagnostic deliberately falls outside that affordable-default assumption.
Its denied calls and task failures must be reported.
The declared reference is not the observed matched agent-only bill.
No bound relative to that observed bill follows from enforcing the declared reference.

## Analysis

Report every pre-specified task, including budget denials, program errors, parse failures, and unsuccessful paid runs.
Separate known actual charges, unsettled reservations, and total budget exposure.
Verify that generation IDs are unique and recorded charges do not exceed reservations.
Report paired costs and success rates, together with seed-cluster bootstrap intervals across four goal bindings.
Those intervals describe this small controlled workload, not model-generation variance or a natural drift distribution.
The controlled label change and DOM dialog are synthetic UI perturbations, although execution, state recovery, and billing are real.

Credentials are read from `~/app/.env` only inside paid experiment requests.
The credential is not copied into requests, results, logs, or analysis.

## V2 interruption and V3 correction

V2 completed one clean pair. Its nineteenth request, during the second agent-only episode, returned error 502, `Provider returned an empty response`, with a generation ID and no usage. Two read-only generation metadata queries returned 404. The unknown charge remains reserved and is never treated as zero. V2 has 18 settled receipts and one unknown receipt.

V3 was requested to include the binding-extraction path missing from V2. It repeats the full twelve-pair design with both policies receiving the same natural-language task. Its provider-error rule was frozen after diagnosing the V2 infrastructure failure and before V3 paid calls. There is no request retry or outcome-based task repetition. Cost intervals must include unresolved liabilities.
