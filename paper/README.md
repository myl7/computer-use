# Paper sources

ICLR 2027 paper: `When to Compile a Computer-Use Agent? Measuring Payback and Bounding Total Cost`.
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
| `rewrite-review/render_pace_tables.py` | Current measurement and simulation table renderer |
| `rewrite-review/render_live_tables.py` | Current live-study table renderer |

## Build and verify

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
```

The repository README gives the current offline recomputation commands.
Earlier numeric scripts remain for their corresponding historical measurements;
they do not render the current online comparison tables.
The official `.sty` and `.bst` files match the ICLR 2027 style archive byte for byte.
