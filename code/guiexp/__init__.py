"""guiexp: a thin BrowserGym-based GUI experiment harness for the OpenApps
calendar app (paper: "When to Compile a GUI Agent?").

Five prompt conditions (discover / told / mid / skill / floor), per-step
observations (screenshot + BrowserGym AX-tree with [bid] ids), a minimal
action space over bids, per-model-call token/cost accounting, and a
deterministic mock model so tests make ZERO network calls.
"""
