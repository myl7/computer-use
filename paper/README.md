# Paper sources

ICLR 2027 paper: `When to Compile a Computer-Use Agent? Measuring Payback and Making Compilation Decisions for Token Efficiency`.
Both `main.tex` and `main_anon.tex` build an anonymous submission.

## Layout

| Path | Role |
|---|---|
| `main.tex`, `main_anon.tex` | Anonymous review-mode entry points |
| `head.tex` | Packages and notation macros |
| `body.tex` | Main sections, page-limit boundary, declarations, references, and appendices |
| `statements.tex` | Page-exempt AI use, ethics, and reproducibility statements before references |
| `references.bib` | Bibliography |
| `fig/` | PDF figures included by the paper |
| `figure-sources/` | Editable, self-contained HTML figure sources |
| `build/` | LaTeX outputs, including the current PDF |
| `../analysis/trace_verifier_20260925/results/render_revised_tables.py` | Current measurement and experiment table entry point |
| `rewrite-review/render_pace_tables.py` | Shared renderer, called with explicit revised inputs by the entry point above |
| `rewrite-review/render_live_tables.py` | Current live-study table renderer |

## Build and verify

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
```

The repository README gives the current offline recomputation commands.
Earlier numeric scripts remain for their corresponding historical measurements;
they do not render the current online comparison tables.
The official `.sty` and `.bst` files match the ICLR 2027 style archive byte for byte.

## PACE-normalized cost tables

Online comparison, ablation-ratio, and sensitivity tables use PACE as the denominator within each condition. Means and maxima are computed after normalization. The appendix table of percentage changes retains PACE at zero because it reports changes rather than ratios. The theoretical budget remains relative to ReAct.

```bash
python3 analysis/trace_verifier_20260925/results/produce_render_inputs.py --final
python3 analysis/trace_verifier_20260925/results/render_revised_tables.py --final
```

Run these commands from the repository root. The revised input producer uses retained per-run costs and writes its inputs and provenance under `analysis/trace_verifier_20260925/results/render-inputs/`. The final renderer requires complete measurement, diagnostic, and simulation records. Abstract savings use the equal-weight mean of each condition's percentage reduction relative to the named baseline, not the inverse of an aggregate table ratio. The older normalization script and the shared renderer's default inputs apply to the historical study.

## Approved teaser figure

Figure 1 uses `fig/teaser_iclr27.pdf` with its caption in `body.tex`. The accepted editable source is `figure-sources/teaser_iclr27.html`, with SVG and PNG copies beside it. The review history and comparison page are retained in `figure-review-20260924/`.

To export a later edit of the accepted teaser source, use the paper-figure skill's `export_pdf.mjs`:

```bash
node export_pdf.mjs figure-sources/teaser_iclr27.html fig/teaser_iclr27.pdf
```

## Split method figures

Figure 2 is `fig/measurement_iclr27.pdf`, placed near the start of `measurement.tex`.
Figure 3 is `fig/online_algorithm_iclr27.pdf`, placed near the start of `online_protocol.tex`.
Both figures have self-contained HTML, SVG, and PNG copies in `figure-sources/`.
The editable shared D3 source is `figure-review-20260924/new/workflow.js`, and `build_review.py` exports independent Measurement and Online Algorithm HTML files.
The pre-split candidate and manuscript wrappers are retained in `figure-review-20260924/workflow-before-split/`.

## Approved method figures with equations

The accepted Figure 2 and Figure 3 now include the reviewed semantic colors and point-of-use formulas. Their installed PDFs and canonical HTML/SVG/PNG assets match `figure-review-20260924/new/measurement.*` and `new/online-algorithm.*`. The formulas are rendered by `figure-review-20260924/build_math.py` and embedded as offline SVG paths. The previous installed assets are preserved under `figure-review-20260924/before-approved-method-install/`.

## Final equation placement

Measurement (Figure 2) uses the compact version without formulas. Equations remain in Online Algorithm (Figure 3), where they directly express decision conditions. Both installed figures and the manuscript PDFs reflect this choice. See `figure-review-20260924/final-equation-placement.json` for verification.
