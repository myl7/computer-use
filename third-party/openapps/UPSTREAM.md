# Retained OpenApps source

The retained calendar implementation comes from
[facebookresearch/OpenApps](https://github.com/facebookresearch/OpenApps) at
commit `7edfa641ba83716acf44831aed640933b4a7ffa6` and includes local changes used
in the recorded experiments. Its original CC BY-NC 4.0 license is retained in
`LICENSE`. This directory contains the source needed to verify the recorded
calendar hash, not a full OpenApps installation.

For live execution, install the upstream repository and its dependencies at
that revision, then apply the retained calendar implementation. The recorded
environment used Python 3.11.16, open-apps 0.1.0, Playwright 1.44.0,
browsergym-core 0.13.4, OpenAI SDK 1.102.0, Flask 3.1.1, and hydra-core 1.3.2.
The experiment runner expects the environment's Python executable at
`third-party/openapps/.venv/bin/python`.
