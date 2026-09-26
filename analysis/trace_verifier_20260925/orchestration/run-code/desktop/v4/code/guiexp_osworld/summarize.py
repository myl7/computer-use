"""Summarise the measured cells: c / C / p / q / d / N* per family x model.

Reads each cell's build.json (t16-shaped) plus the floor record and prints
the numbers the paper's tables want, in priced-unit (floored) and raw form.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "experimental-results" / "guiexp_osworld"


def cell_dir(model: str, family: str) -> Path:
    return ROOT / model.replace("/", "_") / family


def load_cell(model: str, family: str) -> dict | None:
    path = cell_dir(model, family) / "build.json"
    return json.loads(path.read_text()) if path.is_file() else None


def main() -> int:
    models = (
        "z-ai/glm-5.3-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
    )
    families = ("CalcTableSave", "WriterMemoSave")
    print(f"{'cell':<44} {'c_raw':>9} {'c_flored':>9} {'C_marg':>8} {'C_auto':>9} "
          f"{'p_gate':>6} {'q':>5} {'d':>6} {'N*m':>7} {'N*auto':>7} {'usd':>8}")
    for model in models:
        for family in families:
            record = load_cell(model, family)
            if record is None or "exploration" not in record:
                done = ",".join((record or {}).get("stages_done") or [])
                print(f"{family} x {model.split('/')[-1][:18]:<22} (incomplete: {done or 'missing'})")
                continue
            be = record.get("break_even") or {}
            c_raw = record["exploration"]["instances"][0]["attempts"][0]["usage"]["total_tokens"]
            c_unit = be.get("c_unit_floored")
            builder_priced = (be.get("build_totals_priced") or {}).get("builder_selected")
            autorpa = (be.get("build_totals_priced") or {}).get("autorpa_build")
            deploy = record.get("deploy") or {}
            p_gate = None
            gate = (record.get("verification") or {}).get("gate") or {}
            if gate.get("bindings_total"):
                p_gate = round(gate["bindings_passed"] / gate["bindings_total"], 2)
            q = be.get("q")
            d = be.get("d_unit")
            nstar = be.get("nstar") or {}
            print(f"{family} x {model.split('/')[-1][:18]:<22}"
                  f"{c_raw:>9} {c_unit:>9} {builder_priced:>8} {autorpa:>9} "
                  f"{str(p_gate):>6} {str(q):>5} {str(d):>6} "
                  f"{str(nstar.get('marginal')):>7} {str(nstar.get('autorpa_build')):>7} "
                  f"{record.get('total_cost_usd'):>8.4f}")
            extra = {
                "gate_per_k": {k: f"{v['bindings_passed']}/{v['bindings_total']}"
                               for k, v in (record.get("gate_per_k") or {}).items()},
                "deploy_success": f"{deploy.get('success_count')}/{deploy.get('n')}",
                "doc_success": f"{(record.get('doc_arm') or {}).get('success_count')}/3",
                "L_doc": (record.get("doc_arm") or {}).get("totals", {}).get("total_tokens"),
                "refinements": (record.get("verification") or {}).get("refinements"),
                "unautomatable": (record.get("verification") or {}).get("unautomatable"),
                "floor_unit": (record.get("floor") or {}).get("floor_unit_mean"),
                "wall_s": record.get("wall_s"),
            }
            print("   ", json.dumps(extra))
    ledger = ROOT / "ledger.json"
    if ledger.is_file():
        data = json.loads(ledger.read_text())
        print(f"\nledger: ${data['total_cost_usd']:.4f} / HKD {data['total_cost_hkd']:.2f}"
              f" over {len(data['cells'])} cell record(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
