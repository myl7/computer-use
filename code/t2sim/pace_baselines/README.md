# PACE named decision-rule adaptations

This study adds two baselines and one ablation to the exact 600 grid and 12 recorded-arrival
conditions retained for PACE. It does not rerun a language model or a device.
Definitions are frozen in `config.json` before evaluation, and all source,
scenario, stream, mapping, and supplied-profile hashes are verified.

## Frozen definitions

**AutoRPA (build once)**, key `autorpa_once`, makes one bundled compilation
attempt after three completed agent runs. The bundled charge includes the
bounded verification and repair used by the shared measured compiler. It
uses an admitted program and falls back on detected execution failure. After
a rejected build or detected drift, it stays with agent service. It never
restarts compilation. Idle delisting may be followed by free relisting of a
still-valid archived program, just as in the common serving environment.

This decision adaptation follows AutoRPA's build-and-test design, bounded
builder repair, and ReAct fallback after the build fails. AutoRPA §3.4 states
that after `M` unsuccessful code modifications, tasks of that type use ReAct
at test time. Its implementation uses three building tasks and `M=3`.
Our measured compiler replaces its compiler, and completed-run eligibility
replaces its separate collection stage. Drift retirement is an explicit
common-environment extension, not a claimed AutoRPA lifecycle rule.

Primary source: Chen et al., *AutoRPA: Efficient GUI Automation through
LLM-Driven Code Synthesis from Interactions*, arXiv:2605.21082, §§3.4 and 4.
Saved source: `.firecrawl/rewrite-20260922/autorpa-current.md`.

**ToolPro (cost adaptation)**, key `toolpro_cost`, proposes compilation when
the expected saving from one program use, `c-d-[h+(1-h)q]c`, exceeds the
estimated acquisition cost `C+(1/p_hat-1)C_fail`. Here
`p_hat=(admissions+1)/(attempts+2)` uses only observed outcomes. The rule has
the same three-completed-run eligibility, reusable artifacts, fallback,
drift, and idle limit as PACE. It estimates no future recurrence or horizon.
It has no cost-protection layer. After a failed attempt the next proposal
uses the updated estimate.

ToolPro §3.4 makes a per-instance choice when
`(N-1)(mean RTT+mean decision time)-mean build time > 0`, where `N` counts
endpoint calls inside the current task. Our logs do not contain `N`, RTT,
or decision/build latency. The adaptation therefore replaces the original
latency difference with the shared one-use token saving, and replaces its
profiled build latency with the shared observed-outcome acquisition price.
It retains the original single-instance economic comparison, not the
historical simulator's future-arrival heuristic or binding restriction.

Primary source: Liu et al., *Beyond Static Endpoints: Tool Programs as an
Interface for Flexible Agentic Web Services*, arXiv:2606.19992, §3.4.
Saved source: `.firecrawl/rewrite-20260922/toolpro-current.md`.

Both rows compare published decision patterns under one cost environment.
They are not end-to-end reproductions of the cited systems. All original
PACE results and their unfavorable conditions remain unchanged.

**PACE without failed-attempt pricing**, key `pace_narrow_price`, changes
only the proposal's estimated acquisition price from
`C+(1/p_hat-1)C_fail` to `C/p_hat`. The simulation still charges the actual
failed-attempt price and reserves `max(C,C_fail)` under the same 0.25 budget.
The projection, verification estimate, hazard, routing, service order, and
all protection actions are unchanged. This ablation uses an independent
module instance of the existing safety engine and changes only its proposal
function's failure-price input. The original source file is unchanged.

Every repetition also reruns unmodified PACE and compares its cost, outcome,
attempt count, and components with the original stored row. These crosscheck
rows are kept with the adapter results and are not a new treatment.

## Reproduction

From the repository root:

```
python3 -m unittest discover -s code/t2sim/pace_baselines -p 'test_*.py' -q
python3 code/t2sim/pace_baselines/study.py --freeze
python3 code/t2sim/pace_baselines/study.py --run --workers 6
```

The freeze refuses to replace a different manifest. A run refuses source
drift and compares each regenerated input with its original recorded hash.
Existing result cells are never overwritten. The summary reports all cells
with equal condition weight, the same weighting as the retained paper.
