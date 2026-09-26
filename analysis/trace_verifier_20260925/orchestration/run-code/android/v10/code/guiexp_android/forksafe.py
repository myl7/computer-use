"""Route subprocess launches through posix_spawn instead of fork on macOS.

Why this exists. Every runner in this package holds live gRPC channels
(android_env talks to the emulator over gRPC) and, through absl and the
emulator client libraries, CoreFoundation and libdispatch state. On macOS a
fork() in such a multi-threaded process runs the libraries' atfork handlers
in the child, and the child either crashes before exec (crash reports from
2026-09-10: "crashed on child side of fork pre-exec", libdispatch "BUG IN
CLIENT OF LIBDISPATCH: trying to lock recursively", CoreFoundation
"multi-threaded process forked", delivered to the parent as SIGTRAP, rc -5)
or hangs forever in an atfork mutex (the 7.5 hour stall of 2026-09-10).
Both show up as adb calls dying at random, in android_env's own adb
controller as well as in our wrappers.

CPython 3.11 already prefers posix_spawn, which runs no child-side code at
all, but only when close_fds is False (macOS has no closefrom for
posix_spawn) and a few other conditions hold (see subprocess.py,
_execute_child). subprocess.run defaults to close_fds=True, so every call
falls back to fork_exec. install() flips that one default for calls that
would otherwise qualify. Inherited descriptors are then limited to those
without CLOEXEC, and both Python (PEP 446) and gRPC mark theirs CLOEXEC.

Opt out with ANDROID_EXP_FORK_SAFE=0.
"""
from __future__ import annotations

import os
import subprocess
import sys

_installed = False
_ORIGINAL_INIT = subprocess.Popen.__init__

# Popen's positional parameters after ``args``. A caller passing close_fds or
# anything later positionally is left alone.
_POSITIONAL_BEFORE_CLOSE_FDS = 6  # bufsize, executable, stdin, stdout, stderr, preexec_fn


def _qualifies(a: tuple, kw: dict) -> bool:
    if len(a) > _POSITIONAL_BEFORE_CLOSE_FDS:
        return False
    if len(a) >= _POSITIONAL_BEFORE_CLOSE_FDS and a[5] is not None:  # preexec_fn positional
        return False
    if kw.get("preexec_fn") is not None or kw.get("pass_fds"):
        return False
    if kw.get("cwd") is not None:
        return False
    return kw.get("close_fds", True) is True


def install() -> bool:
    """Make close_fds default to False so eligible launches use posix_spawn.

    Returns True when the hook is active (installed now or earlier).
    """
    global _installed
    if _installed:
        return True
    if sys.platform != "darwin" or os.environ.get("ANDROID_EXP_FORK_SAFE") == "0":
        return False
    if not getattr(subprocess, "_USE_POSIX_SPAWN", False):
        return False

    def __init__(self, args, *a, **kw):
        if _qualifies(a, kw):
            kw["close_fds"] = False
        return _ORIGINAL_INIT(self, args, *a, **kw)

    subprocess.Popen.__init__ = __init__
    _installed = True
    return True


def uninstall() -> None:
    global _installed
    subprocess.Popen.__init__ = _ORIGINAL_INIT
    _installed = False
