"""Test bootstrap: make the guiexp_android package importable and register markers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG_PARENT = Path(__file__).resolve().parents[2]  # .../computer-use
if str(PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(PKG_PARENT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "e2e: boots/reuses the AndroidWorldAvd emulator (slow, no LLM)"
    )


@pytest.fixture(autouse=True)
def no_resolve_retry_sleep(monkeypatch):
    """Drop the told-step re-read delay to zero.

    ``told_check.resolve_step_index`` waits between screen reads so a control
    that is merely late is not called missing. Fake devices serve their screen
    instantly, so on them the wait only costs test time.
    """
    from guiexp_android import told_check

    monkeypatch.setattr(told_check, "RESOLVE_RETRY_S", 0.0)
