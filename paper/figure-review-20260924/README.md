# PACE figure review, 2026-09-24

Open `index.html` for the self-contained old/new comparison, including both captions. The left images preserve the PDFs from before this review. The approved Figure 1 and its caption have now been installed into the manuscript. The revised method diagram is split into Figure 2 (Measurement) and Figure 3 (Online Algorithm), installed at the beginning of their respective sections.

## Files

- `old/`: snapshots of the current PDFs, matching HTML sources, rendered PNGs, and caption-containing LaTeX sources.
- `new/`: candidate HTML, SVG, PNG, PDF, and one-sentence-per-line caption snippets.
- `new/workflow.js`: editable, grid-based D3 workflow diagram.
- `new/teaser-context.js` and `new/teaser-challenges.js`: editable task/execution and challenge panels.
- `build_review.py`: creates the candidate HTML files, reusing the existing embedded fonts, D3, and licensed Lucide paths.
- `build_page.py`: embeds the figures and captions in the comparison page.
- `verification.json`: rendered text counts, canvas bounds, and JavaScript error checks.
- `page-verification.json`: image loading, zoom, paper-width mode, and mobile overflow checks.

The two method figures use 179 English/alphanumeric tokens separated by whitespace in total, compared with 235 in the original. Exact counts are recorded in `verification.json`. This count excludes captions. The teaser presents calendar entries as task examples with imperative Add prompts, speech bubbles, and a calendar-plus symbol, and adds vertical spacing. Small extruded cards depict programs and source traces.

## Regenerate

Run from the repository root:

```sh
python3 paper/figure-review-20260924/build_review.py
node paper/figure-review-20260924/verify.mjs
python3 paper/figure-review-20260924/build_page.py
node export_pdf.mjs paper/figure-review-20260924/new/teaser.html paper/figure-review-20260924/new/teaser.pdf
node export_pdf.mjs paper/figure-review-20260924/new/workflow.html paper/figure-review-20260924/new/workflow.pdf
```

`export_pdf.mjs` comes from the paper-figure skill. The verification scripts import Playwright from the module named by `PLAYWRIGHT_MODULE`, or from the installed `playwright` package when that variable is unset. For the comparison-page check, serve this folder on port 18925, then run `node paper/figure-review-20260924/verify_page.mjs`.

## Review decisions

- Figure 1 caption states the research question of when to compile, then locates the task example and the visual cost encodings without repeating the Introduction.
- Figure 1 aligns the task-description group with the three prompts. Its execution options compare three aligned columns, with a compact compilation node beneath the source agent and artifact branches to two program uses.
- Figure 1 uses matching red callouts for the two consequences. The right callout explicitly identifies insufficient reuse to recover compilation cost.
- Figure 1 separates the example task from execution options. Its challenge panels emphasize paid failures without a program and unrecovered compilation spend. Bar lengths are illustrative, not measurements.
- The deprecated term was removed from the active manuscript caption in `paper/body.tex`; the full approved caption is now installed in `paper/body.tex`.
- Figure 2 preserves measurement, current service, subsequent compilation, price estimation, and shared cost admission.
- Verification takes the generated program and bindings extracted by a model call from the same three source traces. This follows the author's 2026-09-25 update, ahead of the separate measurement-text and experiment revision.
- Pass and fail costs feed the measured profile separately from matched serving comparisons.
- Service branches explicitly distinguish program availability, budget rejection, and detected failures.
- The compilation proposal compares expected uses times per-use saving with compilation and routing costs.
- Current service finishes before the compilation decision. The figure labels stored programs as available for later tasks.
- Online terminology is now algorithm. Measurement protocol remains unchanged. Historical snapshots retain their original wording.
- The previous workflow review candidate is saved under `workflow-before-20260925/`.
- A separate visual review checked diagram semantics and spacing. All final labels fit within their SVG canvases.
- Icons retain the local Mizar77/ml-paper-icons Lucide attribution and embedded license notices.

## Split revision

- `new/measurement.*` and `new/online-algorithm.*` are the separate current method figures. Each has its own caption snippet.
- Bindings and source traces connect to their specific receiving nodes. Task prompts feed input extraction and agent execution.
- A report-shaped Results panel distinguishes measured outputs from procedure steps.
- The repeat scope groups source traces and compilation, as specified by the repeated-compilation experiment.
- Failed checks and failed compilation reach a Wait node. Successful compilation reaches a Saved program output.
- The comparison page shows the pre-split candidate on the left and the two current figures on the right. `old/` retains the earlier manuscript version.
- `measurement-in-paper.png` and `online-in-paper.png` show the integrated figures on pages 4 and 6. Both paper builds have 28 total pages and 10 main-text pages.

## Terminology and connector refinement

- Figure labels now follow the paper's binding extraction, agent execution, program execution, cost-aware proposal, cumulative cost budget, and stored program terminology.
- All arrows are solid. There is no arrow-style legend. Headless branch segments merge into one headed connection at each output.
- Generate program and binding extraction carry robot-head icons to identify model calls. Card headings no longer repeat their labels as icons.
- `before-terminology-arrows/` preserves the preceding source and independent PDFs.

## Shared figure style

`paper/figure-sources/figure-style.json` defines the approved teaser font, palette, strokes, corner radius, and semantic icons. The builder applies these settings to all three figures. Model calls use the same Lucide robot head, program artifacts use the green code card, and failures use the red circle-x. Method figures use neutral containers and the teaser's cost/program/failure colors, without a separate blue or purple palette. The teaser's remaining extraction icon now uses the same robot head.

## Current color variant (review history)

The current `new/measurement.*` and `new/online-algorithm.*` are a color-only review variant. Their layout and labels are unchanged. Large yellow budget backgrounds are removed. Thin panel accents and colored headings use the existing teaser palette. `before-color-variant/` is the preceding version shown on the left of the comparison page. The installed manuscript figures still match that preceding version until this visual direction is reviewed.

## Semantic color revision (review history)

Color describes evidence and outcomes rather than stage identity: green denotes verified usable programs, their execution path, and recovered cost; red denotes verification/runtime failure, invalidation, and unrecovered cost; ochre denotes cost and budget quantities; inputs, extraction, undecided tests, waiting, and generic record keeping remain neutral. Budget rejection is not a program failure. The current comparison uses `before-semantic-colors/` on the left and the candidate on the right, including the teaser's neutral task examples and extraction label. The active manuscript terminology now uses retain/retained for programs and program artifacts, including Appendix pseudocode and the installed Figure 3. The final semantic colors were subsequently approved for the method figures.

## Equation placement variant (review history)

`before-equations/` preserves the accepted visual baseline for this iteration. The comparison shows that baseline on the left and equations at their points of use on the right. Figure 2 adds compilation-cost symbols, no-drift per-use saving, and conditional payback ratios. Figure 3 adds the actual service and compilation admission inequalities and the cost-aware trigger, and typesets the existing reference update and cumulative bound consistently. Long parameter-estimator definitions remain in the text. Equations replace prose where possible.

`build_math.py` renders formulas using the manuscript's amsmath/amssymb math fonts via local pdfLaTeX and pdftocairo. The resulting `new/math-assets.json` contains offline SVG paths with namespaced glyph IDs. Regenerate it before `build_review.py` when changing formula source. The equation variant was subsequently approved and installed into the manuscript.

## Accepted method figure installation

The user approved the current Figure 2 and Figure 3 with equations. Both are now installed in the manuscript, and all four asset formats match the review files. `approved-method-installation.json` records the asset checks. Figure 1 was not changed by this installation. Earlier pending-review statements above describe intermediate versions, not the current installation.

## Final equation placement

Measurement (Figure 2) uses the compact version without formulas. Equations remain in Online Algorithm (Figure 3), where they directly express decision conditions. Both installed figures and the manuscript PDFs reflect this choice. See `figure-review-20260924/final-equation-placement.json` for verification.
