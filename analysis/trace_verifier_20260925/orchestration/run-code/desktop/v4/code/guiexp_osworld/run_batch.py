"""Batch runner: the 4 cells (2 families x 2 models), sequentially, with the
spend ledger the 200-HKD gate reads.

Per cell: floor -> explore -> translator -> builder(6) -> gates -> verify ->
deploy(30) -> doc(3), resumable after a crash (`--resume`). The ledger file
experimental-results/guiexp_osworld/ledger.json accumulates every recorded
cost so far (this arm only; the Android arm's ledger is separate) and the
run stops BEFORE a cell whose projected total would cross the budget gate.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import families, guest_env
from .build_protocol import run_build

MODELS = (
    "z-ai/glm-5.3-flash",
    "deepseek/deepseek-v4-flash-vision-exp",
)
FAMILIES = (
    "CalcTableSave",
    "WriterMemoSave",
)
BUDGET_GATE_HKD = 200.0
HKD_PER_USD = 7.8


def load_env_file(path: str = None) -> dict:
    """Read OPENROUTER_* keys from the app .env into os.environ (values are
    never printed or logged). Default: <repo>/../.env, i.e. ~/app/.env."""
    import os

    if path is not None:
        candidates = [Path(path).expanduser()]
    else:
        repo_root = Path(__file__).resolve().parents[2]  # .../computer-use
        candidates = [repo_root.parent / ".env", repo_root / ".env"]
    loaded = {}
    for env_path in candidates:
        if not env_path.is_file():
            continue
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
                       "QWEN_OFFICIAL_API_KEY"):
                os.environ[key] = value.strip()
                loaded[key] = "<set>"
        break
    return loaded


def ledger_path() -> Path:
    return guest_env.RESULTS_ROOT / "ledger.json"


def read_ledger() -> dict:
    path = ledger_path()
    if path.is_file():
        return json.loads(path.read_text())
    return {"cells": [], "total_cost_usd": 0.0}


def append_ledger(cell: dict) -> dict:
    ledger = read_ledger()
    ledger["cells"] = [c for c in ledger["cells"]
                       if not (c["family"] == cell["family"] and c["model"] == cell["model"])]
    ledger["cells"].append(cell)
    ledger["total_cost_usd"] = round(sum(c["cost_usd"] for c in ledger["cells"]), 6)
    ledger["total_cost_hkd"] = round(ledger["total_cost_usd"] * HKD_PER_USD, 2)
    ledger_path().write_text(json.dumps(ledger, indent=1))
    return ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cells", default=None,
                        help="family:model pairs, comma separated (default: all 4)")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    loaded = load_env_file()
    if "OPENROUTER_API_KEY" not in loaded:
        print("WARNING: OPENROUTER_API_KEY not found; real cells will fail")
    else:
        print("env: OPENROUTER_API_KEY set (value not shown), "
              f"base_url={'set' if 'OPENROUTER_BASE_URL' in loaded else 'default'}")

    if args.cells:
        pairs = []
        for item in args.cells.split(","):
            family, model = item.split(":")
            pairs.append((family, model))
    else:
        pairs = [(family, model) for model in MODELS for family in FAMILIES]

    ledger = read_ledger()
    print(f"ledger so far: ${ledger['total_cost_usd']:.4f} "
          f"(HKD {ledger.get('total_cost_hkd', 0):.2f})", flush=True)

    if args.dry_run:
        for family, model in pairs:
            print(f"  cell {family} x {model} -> {guest_env.RESULTS_ROOT / model.replace('/', '_') / family}")
        return 0

    env = guest_env.OSWorldEnv()
    exit_code = 0
    try:
        for family, model in pairs:
            ledger = read_ledger()
            projected_hkd = ledger["total_cost_usd"] * HKD_PER_USD + 60.0  # 1 cell worst case
            if projected_hkd > BUDGET_GATE_HKD:
                print(f"BUDGET GATE: HKD {ledger.get('total_cost_hkd', 0):.2f} spent, "
                      f"projected {projected_hkd:.1f} > {BUDGET_GATE_HKD:.0f}; "
                      "stopping for instructions (see docs/osworld-port-log-2026-09.md)",
                      flush=True)
                exit_code = 2
                break
            out = guest_env.RESULTS_ROOT / model.replace("/", "_") / family
            print(f"=== cell {family} x {model} -> {out}", flush=True)
            t0 = time.time()
            try:
                record = run_build(
                    family=family, model=model, out_dir=out, env=env,
                    resume=args.resume,
                )
                append_ledger({
                    "family": family, "model": model,
                    "cost_usd": record["total_cost_usd"],
                    "cost_hkd": round(record["total_cost_usd"] * HKD_PER_USD, 2),
                    "wall_s": record["wall_s"],
                    "stages_done": record.get("stages_done"),
                    "finished": True,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                })
                print(f"=== cell done: ${record['total_cost_usd']:.4f} "
                      f"({round(time.time() - t0)}s)", flush=True)
            except Exception as exc:  # noqa: BLE001 - record and go on to next cell
                append_ledger({
                    "family": family, "model": model, "cost_usd": 0.0,
                    "error": f"{type(exc).__name__}: {exc}"[:400],
                    "finished": False,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                })
                print(f"=== cell FAILED: {type(exc).__name__}: {exc}", flush=True)
                exit_code = 1
    finally:
        env.close()
    ledger = read_ledger()
    print(f"ledger total: ${ledger['total_cost_usd']:.4f} / "
          f"HKD {ledger['total_cost_hkd']:.2f}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
