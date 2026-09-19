# Paper sources

ICLR 2027 paper: `When to Compile a GUI Agent?`

## Layout

| Path | Role |
|---|---|
| `main.tex` | Preamble, title, author block, bibliography, and document skeleton |
| `head.tex` | Packages and notation macros |
| `body.tex` | Abstract through the AI-use statement, the bibliography, and the appendices |
| `references.bib` | Bibliography |
| `fig/` | PDF figures included by the paper |
| `figure-sources/` | Editable HTML sources and PNG previews |
| `build/` | LaTeX outputs, including the current PDF |
| `submission/` | Reserved for future submission packages |
| `check_numbers.py` | Recomputes quantitative claims from experimental results |

## Build and verify

```bash
latexmk -pdf -interaction=nonstopmode -outdir=build main.tex
python3 check_numbers.py
```

Historical drafts, the previous paper README, and the legacy arXiv package are
under `../misc/old-paper-backups/`.
