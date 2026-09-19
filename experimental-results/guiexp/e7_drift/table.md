# E7 drift probe -- guiexp compiled program (glm-5.3-flash, t13 attempt1)

Program: `/Users/myl/app/computer-use/experimental-results/guiexp/t13_compilepath/z-ai_glm-5.3-flash/attempt1/family_program.py`

Six arms x 5 held-out bindings (seed namespace `guiexp:drift:0`), one fresh OpenApps server per arm, no model calls.

| Arm | Axis | pass | loud | silent | old pass/loud/silent | match |
|---|---|---:|---:|---:|---|---|
| wizard | baseline | 5 | 0 | 0 | 5/0/0 | yes |
| wizard+dark_theme | appearance | 5 | 0 | 0 | 5/0/0 | yes |
| wizard+black_and_white | appearance | 5 | 0 | 0 | 5/0/0 | yes |
| wizard+challenging_font | appearance | 5 | 0 | 0 | 5/0/0 | yes |
| single_page | protocol | 0 | 5 | 0 | 0/5/0 | yes |
| sectioned | protocol | 0 | 5 | 0 | 0/5/0 | yes |

- baseline pass rate: 1.00
- appearance survival: 1.00 (15 runs)
- protocol break rate: 1.00 (10 runs)
- silent total: 0, loud total: 10
- break prob per change event: 0.4 (silent share 0.0)

## Comparison with the old probe

Old probe: openapps-exp/drift_probe.py over compile_glm_wizard/family_program_a3.py; paper body.tex tab:drift -- 5/5 baseline pass, 15/15 appearance pass, 0/10 protocol pass with all 10 protocol failures loud (selector timeouts on the wizard's first flow control), 0 silent failures anywhere. The new program reproduces every cell exactly.

Identical coding, identical arms, different compiled program (role/label-locator wizard program from the guiexp compile path instead of the old harness's id-selector program), different held-out rotation (drift namespace), fresh servers per arm.

Failure mode: the old program died in a Playwright selector timeout on `#wizard-next-1`; this one dies one step later with its own `RuntimeError: No button matching '^next$|next' found` (exception classes: {'RuntimeError': 10, 'none': 20}). Same interaction point, same axis: both protocol arms remove the wizard's first Next control, so the program aborts before it can submit anything -- the event count is unchanged in all 10 protocol runs, which is what makes every failure loud.

Silent-mode reachability (the old probe's caveat, still true here): the sectioned flow has a documented silent failure -- submitting with the disclosure unopened writes the four optional fields empty and returns no error -- but a program compiled against the wizard flow raises on the missing Next button long before any submit. The zero in the silent column is a fact about this program on these five variants, not a general property of compiled programs.
