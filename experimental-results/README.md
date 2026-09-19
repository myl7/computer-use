# Experimental results

This directory contains experimental outputs only. Executable experiment code
is under `../computer-use/`.

## OpenApps

- `openapps/results/`: primary JSON summaries and AgentLab run directories.
- `openapps/artifacts/`: generated programs, prompts, and archived run artifacts.
- `openapps/discovery-scratch/`: preserved Claude discovery-run materials used by the paper checker.
- `openapps/real-streams/`: derived process-stream data and policy outputs.
- `openapps/summaries/`: standalone arm summaries.
- `openapps/openapps-log-outputs/`: logs emitted by the upstream OpenApps launcher.
- `openapps/openapps-scratchpad/`: scratch output emitted by the upstream checkout.

The two upstream output directories have compatibility symlinks inside
`third-party/openapps/`; the stored data lives here.

The retired WorkArena outputs are archived under
`../misc/workarena/experimental-results/`.
