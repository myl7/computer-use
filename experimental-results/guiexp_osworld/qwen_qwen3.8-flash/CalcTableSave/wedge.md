# CalcTableSave x qwen/qwen3.8-flash — WEDGED at gate k=1 replay 1 (valid negative)

Date: 2026-09-20 (HKT), server cs11369a, L5 lane, cap $3.00. Cell terminated by
operator ruling at ~11:30 HKT while inside gate stage k=1, replay 1.

## What happened

Stages floor -> exploration -> translator -> builder all completed and are
checkpointed in `build.json` (builder: 6 calls, 183,332 tok total, $0.085013;
see `build.json`). The cell then entered `[4/7] held-out gate per k` at
~07:45 HKT and never printed `k=1: ...`. py-spy shows the process spent the
ENTIRE gate stage (~3h43m, two dumps 30 min apart) inside ONE
`ProgramRunner.run` call — gate k=1, held-out binding 1 — executing the
compiled program (`program (<string>:460)`, `_save_to_desktop` /
`_accept_overwrite`) blocked on guest a11y-dump HTTP GETs.

## Why the replay deadline did not bound it

The harness guard is a one-shot SIGALRM
(`guiexp_osworld/program_runtime.py`, `REPLAY_TIMEOUT_S = 1200`; "a wedged
guest stalls one binding, not the whole cell"). The qwen-compiled artifact
defeats it: every device call sits inside an `except Exception` retry loop,
which swallows the raised `ReplayTimeout` (an `Exception` subclass) and keeps
polling. From `artifact_k1_code.py` (`_find_name_field`; the same pattern
wraps ~30 other call sites):

```python
        for crit in criteria:
            try:
                idx = device.find(**crit)
            except Exception:
                idx = None
            if idx is not None:
                return idx
        return None
```

After the single itimer is spent, no further alarm exists, so the replay runs
at natural length. The program's loops are all finite (`range(3)`, `range(8)`,
`range(10)`, ...), so it neither crashes nor terminates in useful time:
each `elements()` a11y dump takes ~60-90 s on the busy guest (guest container
CPU 77-92% throughout) and one replay makes ~150 such dumps => ~3.5 h per
replay, x 15 gate replays ≈ 2 days for the gate stage alone, before
verification/deploy/doc_arm and the second cell. Not viable; terminated.

## Validity

- Zero API spend during the gate stage (gates make no model calls); total
  cell spend frozen at ~$0.137 (floor $0.0012 + translator $0.0509 + builder
  $0.0850). Well under the $3.00 cap — the cap never triggered.
- Evidence: `wedge_pyspy.txt` (two stacked observations summary; full dump at
  kill decision), `build.json` (checkpointed stages + builder artifacts),
  `artifact_k1_code.py` (the swallowing loops).
- This is a model-capability finding, not an infrastructure fault: the same
  harness completed the gate stage for deepseek/deepseek-v4-flash-vision-exp
  on 2026-09-18, and the floor/exploration/translator stages ran normally for
  qwen. The compiled program's dense exception-swallowing retry style is the
  defect; it made the replay-timeout contract unenforceable.

WriterMemoSave (same invocation, full $3.00 cap) was launched standalone right
after termination; its artifact will be checked early for the same
wedge-looping behavior before letting it run for hours.
