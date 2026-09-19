# When to Compile a Computer-Use Agent?

Measurement and decision code for the ICLR 2027 submission.

- `paper/` — LaTeX sources plus the number pipelines that embed and verify
  every figure in the text (`gen_numbers.py`, `check_numbers*.py`,
  `measurement_update_20260918.py`).
- `code/` — the experiment harness: `guiexp` (agent, compiler, gates,
  deployment), per-benchmark ports (`guiexp_android`, `guiexp_osworld`,
  `guiexp_webarena`), and `t2sim` (the zero-API policy simulator).
- `experimental-results/` — structured outputs of every run the paper cites
  (per-episode screenshots and raw device logs are kept out of the repo).
- `datasets/` — the real arrival streams (process-mining event logs and
  Wikipedia edit streams) consumed by `code/t2sim/streams.py`.

Every number in the paper is recomputed from these result files by the
checkers in `paper/`; nothing is hand-typed.
