"""Exp A lane code (cs659b, 2026-09): repeated compile paths + gate extraction.

New-code-only subpackage for the Android supplementary experiments; it drives
the EXISTING guiexp_android stages (compiler/verify_runner/gate_runner/
deploy_runner) as libraries and never modifies them. Lane plumbing
(``lane.py``) points ``android_env`` at this lane's own AVD/ports at run time.
"""
