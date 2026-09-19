"""Start/stop the OpenApps calendar app for one layout, as a subprocess.

A thin wrapper over the upstream ``launch.py`` (hydra). Layout is the
create-event interaction protocol (``apps.calendar.form_flow``), selected by
the appearance override:

    single_page  -> apps/calendar/appearance=default    (one screen)
    sectioned    -> apps/calendar/appearance=sectioned  (disclosure)
    wizard       -> apps/calendar/appearance=wizard     (three screens)

OpenApps has no HTTP reset hook, so a server carries whatever state earlier
episodes left in its calendar DB. That is fine for the oracle (an episode
succeeds iff an event matching the instance's six fields exists), and it
means a live server for a layout is reusable across episodes: ``start()`` is
idempotent. Port reuse detection: a registry file maps layout -> base URL;
on start we probe that URL's create-event form and reuse the server iff it
serves the requested layout, otherwise we spawn a fresh one.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

GUIEXP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GUIEXP_DIR.parents[1]
OPENAPPS_DIR = PROJECT_ROOT / "third-party" / "openapps"
OPENAPPS_PY = OPENAPPS_DIR / ".venv" / "bin" / "python"

BOOT_TIMEOUT_S = 180.0
PROBE_TIMEOUT_S = 5.0

LAYOUTS = ("single_page", "sectioned", "wizard")
LAYOUT_TO_APPEARANCE = {
    "single_page": "default",
    "sectioned": "sectioned",
    "wizard": "wizard",
}

REGISTRY_PATH = Path(tempfile.gettempdir()) / "guiexp_servers.json"
_PORT_RE = re.compile(r"Uvicorn running on http://([^:\s]+):(\d+)")


class ServerError(RuntimeError):
    pass


def _http_get(url: str, timeout: float = PROBE_TIMEOUT_S) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def detect_layout(base_url: str) -> str | None:
    """Which create-event flow this server serves, or None if unreachable.

    Screen 1 of each flow is a stable fingerprint: the wizard starts with
    only Title/Date and a Next button; sectioned hides the optional fields
    behind a disclosure button; single_page shows all six fields at once.
    """
    text = _http_get(base_url.rstrip("/") + "/calendar/create_event")
    if text is None:
        return None
    if 'id="wizard-next-1"' in text:
        return "wizard"
    if 'id="reveal-details"' in text:
        return "sectioned"
    if 'id="description"' in text and 'id="invitees"' in text:
        return "single_page"
    return None


def _read_registry() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text())
    except Exception:
        return {}


def _write_registry(data: dict) -> None:
    try:
        REGISTRY_PATH.write_text(json.dumps(data))
    except Exception:
        pass


class AppServer:
    """Start/stop the calendar app for one layout; reuse a live one if any."""

    def __init__(self, layout: str):
        if layout not in LAYOUT_TO_APPEARANCE:
            raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")
        self.layout = layout
        self.base_url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._log_path: str | None = None
        self._log_fh = None
        self._owned = False

    def start(self) -> str:
        """Return the base URL, spawning the app only if needed (idempotent)."""
        if self.base_url:
            return self.base_url
        entry = _read_registry().get(self.layout)
        if entry and entry.get("base_url") and detect_layout(entry["base_url"]) == self.layout:
            self.base_url, self._owned = entry["base_url"], False
            return self.base_url
        return self._spawn()

    def _spawn(self) -> str:
        if not OPENAPPS_PY.exists():
            raise ServerError(
                f"no OpenApps venv at {OPENAPPS_PY}; run `uv sync` in {OPENAPPS_DIR}"
            )
        self._log_path = str(
            Path(tempfile.gettempdir()) / f"guiexp_openapps_{self.layout}_{os.getpid()}.log"
        )
        self._log_fh = open(self._log_path, "w+")
        override = f"apps/calendar/appearance={LAYOUT_TO_APPEARANCE[self.layout]}"
        env = dict(os.environ)
        # The openapps venv's editable install points at a pre-reorg path;
        # put the real source tree on PYTHONPATH so `import open_apps` works.
        env["PYTHONPATH"] = str(OPENAPPS_DIR / "src") + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(
            [str(OPENAPPS_PY), "launch.py", override],
            cwd=str(OPENAPPS_DIR),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            # Own process group so we can kill uvicorn's children too.
            preexec_fn=os.setsid,
        )
        self._owned = True
        base_url = None
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self.stop()
                raise ServerError(f"server exited early (code {code}), see {self._log_path}")
            self._log_fh.flush()
            text = Path(self._log_path).read_text(errors="replace")
            match = _PORT_RE.search(text)
            if match:
                candidate = f"http://{match.group(1)}:{match.group(2)}"
                # uvicorn logs before it answers; wait until the app does.
                if detect_layout(candidate) == self.layout:
                    base_url = candidate
                    break
            time.sleep(0.3)
        if base_url is None:
            self.stop()
            raise ServerError(
                f"server did not come up within {BOOT_TIMEOUT_S}s, see {self._log_path}"
            )
        self.base_url = base_url
        _write_registry(
            {
                **_read_registry(),
                self.layout: {"base_url": base_url, "pid": self._proc.pid},
            }
        )
        return base_url

    def stop(self) -> None:
        """Kill the process only if we spawned it; a reused server stays up."""
        if self._owned and self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=15)
            except Exception:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            registry = _read_registry()
            entry = registry.get(self.layout)
            if entry and entry.get("pid") == self._proc.pid:
                registry.pop(self.layout, None)
                _write_registry(registry)
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        self._proc = None
        self._owned = False
        self.base_url = None


@contextmanager
def openapps_app_server(layout: str):
    """Yield the base URL of a server for ``layout``; stops it on exit iff owned."""
    server = AppServer(layout)
    url = server.start()
    try:
        yield url
    finally:
        server.stop()


if __name__ == "__main__":
    import sys

    with openapps_app_server(sys.argv[1] if len(sys.argv) > 1 else "wizard") as base:
        print(f"{base} serves layout {detect_layout(base)}")
