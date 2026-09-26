# Three-building-task verification revision

User authorization: implement the revision, run experiments in parallel, and update the paper as soon as possible within a 24-hour target. API spend is not the bottleneck. Experiment execution and monitoring are delegated to `gpt-5.6-sol` agents. Available hosts are the local laptop, `ssh cs659b`, and `ssh cs11369a`. The authorized API credential is in `~/app/.env` on the laptop. Never print credentials or put them in command lines or tracked files.

## Scope and immutable evidence

- Keep the current initial compiler: three completed source traces, their translations, and the saved initial k=3 code artifact. Do not change to AutoRPA's sequential one-task build schedule.
- Reuse the saved source traces, translations, and initial builder outputs with their recorded charges. Start verification from `artifact_k3_code.py`, never from a program selected by the old five-binding gate.
- Preserve all historical experiment files. Write new results only under `experimental-results/trace_verifier_20260925/`. Write orchestration, analysis, status, and new aggregators under `analysis/trace_verifier_20260925/`.
- Do not revert unrelated working-tree changes. A baseline snapshot of the platform Python sources and current paper files is under `baseline_source/` beside this file.

## Verification contract

1. Use the three original building tasks (recorded seeds 1, 2, 3). Recover their original task prompts from source records when available. Reconstructing a prompt from its recorded seed and the same task generator is allowed only with explicit provenance and matching recorded task parameters. Do not replace them with the first three draws of the old gate.
2. Extract each binding with the original cell's model, using the task prompt and the existing program-facing parameter schema. Record the input, response, extracted binding, type-check outcome, and provider usage. Reuse the existing deployment extraction/type-check machinery. Permit its existing one bounded retry for invalid JSON/types. Do not supply benchmark ground-truth parameters to the extraction model.
3. Extract once per compilation attempt and cache the three bindings for all versions with the same parameter interface. These model calls are compilation/verification charges, not deployment costs. If the interface changes, extraction must be repeated and charged. An extraction failure rejects the attempt and must remain distinguishable from program failure or provider interruption.
4. Execute the candidate on these original tasks with the extracted bindings. Reset the environment to each task's original initial condition and judge against the original task parameters/goal, never against the extracted values. Reuse the platform's existing success checker.
5. Admission requires all three tasks to pass. Every new candidate is eligible for this check. Stop on the first failed task when deciding admission, recording remaining tasks as untested rather than failed. Stop repair immediately once a candidate passes all three.
6. Reuse the existing analyzer, ReAct continuation, and builder-refinement machinery with a budget of M=3 refinements per original building task. In the revised fixed-three-task check, charge each refinement to the first failed task's seed, permit at most three for each seed, and at most nine for an attempt. Recheck all three source tasks from the beginning after each refinement, with first-failure short circuit. This makes the per-task budget explicit for the revised verifier rather than reproducing the old incremental seen-task loop verbatim. For program failures, repair using the relevant original task and execution evidence. Use extracted bindings consistently in repair replay; never silently substitute supplied bindings to make the new verifier pass. Do not repair a program solely to compensate for invalid or missing extraction output. When an isolated replay worker cannot preserve a browser's breakpoint state, restart the original task explicitly and record the restart reason and full cost.
7. Preserve the existing replay deadlines (Android/Desktop 1200 s, Web 900 s), but enforce them outside generated code with a killable worker or equivalent hard guard. A generated `except Exception` must not disable the deadline. Reset/recover the environment after termination. Keep provider failures, runtime timeout failures, and operator-aborted runs distinct. Never claim an unfinished run completed.

## Result and accounting contract

Each cell/attempt must retain a machine-readable record with:
- protocol identifier `three-building-model-extracted-v1`;
- platform, model, family, attempt identity, old source paths and SHA-256 hashes;
- original seeds/prompts and provenance, extraction inputs/outputs/call records;
- initial and final program hashes, candidate history, per-task pass/fail/untested outcomes;
- admission, provider/timeout/error status, repair rounds and all model-call usage;
- reused initial translation/builder charges separately from new extraction/repair charges;
- elapsed wall time per extraction, replay, repair and complete attempt;
- deployment reuse justification or new 30-use deployment records.

For the main calibration configurations, existing deployment records may be reused only if the newly selected program is byte-identical to the historical deployed program and its execution/input protocol is unchanged. Otherwise run the established 30-use deployment procedure for admitted main programs. Keep tasks and evaluation generators unchanged. New admission of a formerly rejected main configuration requires deployment evidence before assigning q or d.

The 22 additional repeated-build attempts measure compilation/verification outcomes and charges only. An admitted repeat is complete after its three-task verification and cost reconciliation; it does not need a 30-use deployment. Use `deployment.status=not_required_repeat`, and never derive serving q/d from an unmeasured repeat. Any extra repeat deployments already executed remain auxiliary evidence rather than a prerequisite for completion. This scope distinction does not change their verifier, repair budget, or cost accounting.

Reassess all 20 main configurations (19 previously complete, one partial Qwen Calc) and the 22 additional Android attempts. Preserve provider refusals/interruptions as such and check cached initial-artifact provenance before deciding whether an interrupted attempt can legitimately resume. Never relabel an old failure as a new completed experiment.

For changed programs, update dependent extracted/supplied checks, paired comparisons, and GUI-perturbation probes. Existing agent-only baseline records can be reused for identical tasks/conditions with their historical limitations retained. Old five-binding diagnostic studies may remain explicitly historical, but must not be described as measurements of the new verifier. Audit the k=1/k=2 comparison scope separately rather than silently changing its meaning.

Update C, C_fail, d, q, admission counts, uncertainty calculations, online simulation profiles, comparisons, ablations, and paper claims from recorded new evidence. The online decision equations remain unchanged. Do not infer a new verification probability from a changed gate threshold; the simulator's scenario probabilities remain assigned as before.

## Provider availability and incomplete billing

User correction: an empty response alone does not establish failure of the method. Inspect complete response metadata, finish reasons, content/reasoning/tool-call fields, usage, model/provider identity, and transport errors. Distinguish provider unavailability or malformed delivery from output-length exhaustion, unsupported response format, and actual program failure.

The existing three-try builder cycle remains bounded, but provider-availability recovery may use a later retry cycle or another provider for the same requested model, as explicitly authorized by the user. Record those retries, route changes, settings, and charges separately. Resume valid cached extraction/analysis/continuation stages rather than repeating them. Do not change the model identity. Preserve the method's per-task program-refinement budget. Escalate if serial/delayed calls and available same-model routes remain unusable.

Latest user budget ruling: a functioning provider's complete response that exhausts a reasonable output budget without emitting a program is a model-generation failure under that budget, not a transport outage. Preserve the existing common main-study generation settings (temperature zero, output/reasoning limits omitted and thus provider-default). Do not tune one family to a different reasoning/output budget to obtain success. Log requested settings, observed completion counts, finish reason and provider separately; omitted requested limits must not be described as an explicitly fixed limit.

The cross-family audit found archived DeepSeek Calc and Writer responses also exhausted exactly 131,072 completion tokens. Explicit accepted single-call program records peaked at24756 DeepSeek tokens; Android aggregate-only builder totals reached 42,258 but are not per-call limits. AutoRPA's paper and official OpenAI wrapper do not establish a numeric output cap. These facts justify treating the observed131072 budget as generous for this study without claiming an exact AutoRPA cap match.

Web DeepSeek first produced three empty replies whose usage was not retained by the old exception path. Read-only accounting recovery could not identify those bills. Its subsequent same-model Fireworks recovery returned three HTTP-success responses ending in `length`, each with 131,072 completion tokens, reasoning-only output and no program. All three Fireworks responses and costs are recorded. The canonical outcome is now unsuccessful generation under the observed budget. Preserve the first three unknown bills and every known recovery charge, so the total attempt cost remains a lower bound. No further full attempts or case-specific 16k/4k tuning belong to this main record. Future responses must be checkpointed before parsing with usage and response identifiers.

The paper's measurement table must retain a lower-bound/partial designation wherever charges remain missing; it must not present a modeled replacement as measured. After the final outcome is known, the results agent must propose an explicit donor-based imputation and missing-cost sensitivity for any incomplete outcome charge. Record donor identities and ensure a modeled charge is at least its observed lower bound. If appropriate same-model donors do not exist, ask root for a declared fallback rather than inventing one.

## Ownership

- Root: protocol decisions, integration, paper prose, final evidence audit. Root does not supervise experiment processes.
- `experiment_manager`: host inventory, isolated lane allocation, deployment/sync coordination, process launch/restart/monitoring, status ledger. Own `orchestration/` and `STATUS.md` here. Report meaningful changes and blockers to root.
- `android_revision`: Android verifier, relevant tests and runner, Android experimental diagnosis. Own `code/guiexp_android/` changes and `runners/android/` here.
- `desktop_revision`: Desktop verifier, relevant tests and runner, Desktop experimental diagnosis. Own `code/guiexp_osworld/` changes and `runners/desktop/` here.
- `web_revision`: Web verifier, relevant tests and runner, Web experimental diagnosis. Own `code/guiexp_webarena/` changes and `runners/web/` here.
- `results_revision`: new aggregation, schema validation, simulation reruns, generated tables. Own `analysis/trace_verifier_20260925/results/`, new simulation outputs, and generated table files. Do not edit paper prose.

Coordinate changes to shared contracts through root. Experiment manager owns host/lane assignments so two processes never operate the same GUI instance concurrently. Use only verified free resources and do not stop unrelated work.
