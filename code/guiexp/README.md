# guiexp — GUI experiment harness for the OpenApps calendar ("When to Compile a GUI Agent?")

- Venv (repo root): `/Users/myl/app/computer-use/.venv-gui` (browsergym-core, playwright, openai, pillow, pytest).
- Run tests (offline mock model, real app + Chromium): `cd /Users/myl/app/computer-use && .venv-gui/bin/python -m pytest computer-use/guiexp/tests -q`
- Run one real episode (needs `OPENROUTER_BASE_URL` + `OPENROUTER_API_KEY` in env; key never printed): `cd /Users/myl/app/computer-use/code && ../.venv-gui/bin/python -m guiexp.runner --layout wizard --condition discover --seed 0 --model z-ai/glm-4.7-flash --obs-mode screenshot+ax --max-steps 30`
- Add `--mock` for a deterministic offline episode (writes `trajectory.jsonl` + step screenshots under `experimental-results/guiexp/` at repo root).
- Conditions: `discover|told|mid|skill|floor` — layouts: `single_page|sectioned|wizard` — obs modes: `screenshot|screenshot+ax` — viewport 1024x640 (AX tree and SoM badges are visible-only, identically in both modes). Note: 1920x1080 was tried and reverted — it quadrupled per-step tokens, and the "below-fold entry button" theory was a misdiagnosis of the real bug: the "Add Event" control is an `<a target="_blank">` whose popup the click action silently dropped; the env now adopts such popups as the single active page (BrowserGym active-page semantics).

## Compile / gate / deploy (stage 2)

Files: `compiler.py` (prompt + LLM compile + deterministic `MockCompiler`), `program_runtime.py` (load a compiled program, run it on a `GuiEnv` page, judge with the env's own six-field oracle), `gate_runner.py` (K held-out bindings), `deploy_runner.py` (NL-goal -> extraction -> type check with one bounded retry -> program -> oracle). Conventions follow `openapps-exp/compile_gate.py`: held-out bindings are the same five the old harness gated on, selected seed-deterministically (`heldout_bindings`, sha256 over a `guiexp:gate:` namespace, disjoint from the trajectory's instance); the gate/deploy judge is `GuiEnv.reward()` itself, never a re-implementation.

A compiled program is one function `def program(page, binding: dict, base_url: str) -> bool` plus a module-level `PARAMS_SCHEMA` (the six calendar fields). All commands run from `computer-use/computer-use/` (server boots automatically; results land in `experimental-results/guiexp/` at the repo root).

Offline, deterministic (no LLM API calls; real app + Chromium):

    ../.venv-gui/bin/python -m guiexp.compiler --mock \
        --trajectory ../experimental-results/guiexp/wizard_discover_s0_mock/trajectory.jsonl
    ../.venv-gui/bin/python -m guiexp.gate_runner --mock \
        --trajectory ../experimental-results/guiexp/wizard_discover_s0_mock/trajectory.jsonl \
        --layout wizard --k 5
    ../.venv-gui/bin/python -m guiexp.deploy_runner --mock \
        --program ../experimental-results/guiexp/gate_mock_wizard/family_program.py

Real compile (needs `OPENROUTER_BASE_URL` + `OPENROUTER_API_KEY` in env; key never printed). `--annotate` shadow-replays the trajectory against the live app to attach each bid's real role/name to the compile prompt:

    ../.venv-gui/bin/python -m guiexp.compiler --model z-ai/glm-4.7-flash --layout wizard \
        --annotate --trajectory ../experimental-results/guiexp/wizard_discover_s0/trajectory.jsonl
    ../.venv-gui/bin/python -m guiexp.gate_runner --model z-ai/glm-4.7-flash \
        --trajectory ../experimental-results/guiexp/wizard_discover_s0/trajectory.jsonl --k 5
    ../.venv-gui/bin/python -m guiexp.deploy_runner --model z-ai/glm-4.7-flash \
        --program ../experimental-results/guiexp/gate_z-ai-glm-4.7-flash_wizard/family_program.py

Outputs: `family_program.py` + `compile.json` (compile price C), `gate.json` (`bindings_passed`, per-binding detail), `deploy.json` (per-use success/error_type/tokens/cost, `d_tokens_mean`). Exit codes: 0 iff gate 5/5 (resp. deploy N/N).

