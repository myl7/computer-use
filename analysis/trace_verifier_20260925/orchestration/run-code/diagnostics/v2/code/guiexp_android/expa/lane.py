"""Lane plumbing for the cs659b Exp A lane (expa, 2026-09).

New code only: nothing here edits an existing guiexp_android module. The lane
runs against its OWN AVD (``guiexpExpA``, console 5562, grpc 8600), patched
into ``android_env``'s module globals at run time; the stock module keeps its
defaults (AndroidWorldAvd / 5554 / 8554), which other lanes own.

Also carries the two machine mappings this lane needs: recorded trajectory
paths from the Mac repo onto this machine's layout, and the lane ``.env``
(OPENROUTER_* — read into the environment, never printed).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

MAC_REPO_ROOT = "/Users/myl/app/computer-use"
LANE_REPO_ROOT = Path(__file__).resolve().parents[3]  # expa -> guiexp_android -> computer-use -> repo root
CODE_DIR = LANE_REPO_ROOT / "computer-use"
HKD_PER_USD = 7.8


def load_env_file() -> None:
    """Read the lane .env into os.environ (values never printed or logged)."""
    env_path = LANE_REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def remap_trajectory_path(path) -> Path:
    """A trajectory path as recorded on the Mac, mapped onto this machine.

    Absolute /Users/myl/app/computer-use/... paths become this repo root;
    ../experimental-results/... paths (relative to the computer-use dir,
    how the later t16 records store them) resolve against this layout.
    """
    text = str(path)
    if text.startswith(MAC_REPO_ROOT):
        text = str(LANE_REPO_ROOT) + text[len(MAC_REPO_ROOT):]
    p = Path(text)
    if not p.is_absolute():
        p = (CODE_DIR / p).resolve()
    return p


def configure_lane(console_port: int, grpc_port: int, avd_name: str) -> None:
    """Point android_env's module globals at THIS lane's AVD and ports."""
    from .. import android_env

    android_env.AVD_NAME = avd_name
    android_env.CONSOLE_PORT = console_port
    android_env.GRPC_PORT = grpc_port
    android_env.SERIAL = f"emulator-{console_port}"
    android_env.BOOT_CMD = (
        str(android_env.EMULATOR_PATH),
        "-avd", avd_name,
        "-no-window", "-no-audio", "-no-boot-anim", "-no-snapshot", "-no-metrics",
        "-port", str(console_port),
        "-grpc", str(grpc_port),
    )


def make_client(max_retries: int = 8):
    """OpenRouter client. The OpenAI SDK retries 429/5xx with exponential
    backoff + jitter internally, so a high ``max_retries`` is the lane's
    exponential-backoff policy; call sites in the existing modules pass this
    client through untouched."""
    from openai import OpenAI

    return OpenAI(
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=240.0,
        max_retries=max_retries,
    )


def device_booted(timeout_s: float = 60.0) -> bool:
    """True iff sys.boot_completed==1 on this lane's serial right now."""
    from .. import android_env

    try:
        out = subprocess.run(
            [str(android_env.ADB_PATH), "-s", android_env.SERIAL,
             "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == "1"


def wait_for_device(timeout_s: float = 600.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if device_booted():
            return True
        time.sleep(5.0)
    return False


# ------------------------------------------------------------ spend ledger


class SpendLedger:
    """A JSONL running bill shared by every expa driver on one out-root."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, stage: str, cost_usd: float, **fields) -> float:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": stage,
                 "cost_usd": round(cost_usd, 8), **fields}
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
        return self.total()

    def total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text().splitlines():
            try:
                total += json.loads(line).get("cost_usd") or 0.0
            except json.JSONDecodeError:
                continue
        return total

    def report(self, label: str = "") -> str:
        usd = self.total()
        return f"spend{(' ' + label) if label else ''}: ${usd:.4f} = {usd * HKD_PER_USD:.2f} HKD"
