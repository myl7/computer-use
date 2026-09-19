#!/usr/bin/env python3
"""W4: merge the per-grid-point E11 shard outputs into the final files.

Reads ~/w4_shards/<model>/E11_env_fragility_<shard>.json shard files (one per
grid point, produced by w4_run_shards.sh on cs11369a), unions their cells,
and writes, under --out-dir:

  E11_env_fragility_android_glm.{json,csv,md}
  E11_env_fragility_android_ds.{json,csv,md}
  E11_env_fragility.{json,csv}          (both models, key prefixed, csv has
                                          a model column)

The per-cell tables (means, bootstrap CIs, rel_to_ours) are computed inside
each shard by sweep_env_fragility.assemble; this script only unions them, so
no statistics are recomputed.  Reuses sweep_env_fragility.csv_rows and
.markdown_tables for exact output parity with a single-process run.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(
    "/Users/myl/app/computer-use/computer-use/t2sim")))
import sweep_env_fragility as sw  # noqa: E402

MODELS = {
    "android_ds": ("ds", 10.509389227369171),
    "android_glm": ("glm", 1.0),
}
STREAMS = sw.STREAMS


def merge_model(shard_dir: Path, cost_set: str, pref: str, ratio: float,
                reps: int, seed: int, B: int, phase_wall_s: float | None,
                n_shards_expected: int):
    shard_ids = [ln.split()[0] for ln in
                 Path(f"/tmp/w4_shards_{pref}.txt").read_text().splitlines()
                 if ln.strip()]
    cells: dict = {}
    violations: list[dict] = []
    meta0 = None
    shard_walls = {}
    missing = []
    for sid in shard_ids:
        p = shard_dir / f"E11_env_fragility_{sid}.json"
        if not p.exists():
            missing.append(sid)
            continue
        d = json.loads(p.read_text())
        if meta0 is None:
            meta0 = d["meta"]
        for k, v in d["cells"].items():
            if k == "_lower_bound_violations":
                violations.extend(v)
            else:
                assert k not in cells, f"duplicate cell {k}"
                cells[k] = v
        shard_walls[sid] = d["meta"]["wall_s"]
    if missing:
        raise SystemExit(f"{cost_set}: {len(missing)} shard(s) missing: "
                         f"{missing[:5]}... (expected "
                         f"{n_shards_expected})")
    meta = dict(meta0)
    grid = [dict(pt) for pt in sw._grid_points(ratio)]
    meta.update({
        "n_cells": len(cells), "n_grid_points": len(grid), "grid": grid,
        "jobs": "sharded: one grid point per shard, 24 shards x 4 workers",
        "shards": len(shard_ids),
        "shard_wall_s": shard_walls,
        "cells_wall_s_sum": round(sum(shard_walls.values()), 3),
        "phase_wall_s": phase_wall_s,
        "c_fail_ratio_note": (f"--c-fail-ratio {ratio:g} = the cost set's "
                              "measured C_fail_over_C_ratio in "
                              "constants.measured.v3.json"),
        "constants_path": "computer-use/t2sim/constants.measured.v3.json",
    })
    assembled = dict(cells)
    assembled["_lower_bound_violations"] = violations
    return meta, assembled


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ds-wall", type=float, default=None)
    ap.add_argument("--glm-wall", type=float, default=None)
    args = ap.parse_args()
    shard_dir = Path(args.shard_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    merged_cells: dict = {}
    merged_meta: dict = {"models": []}
    csv_fields = None
    all_rows = []
    for cost_set, (pref, ratio) in MODELS.items():
        phase_wall = {"android_ds": args.ds_wall,
                      "android_glm": args.glm_wall}[cost_set]
        n_expected = len(sw._grid_points(ratio))
        meta, assembled = merge_model(
            shard_dir, cost_set, pref, ratio, reps=10, seed=7, B=2000,
            phase_wall_s=phase_wall, n_shards_expected=n_expected)
        stem = f"E11_env_fragility_{cost_set}"
        (out_dir / f"{stem}.json").write_text(json.dumps(
            {"meta": meta, "cells": assembled}, indent=1))
        rows = sw.csv_rows(assembled)
        for r in rows:
            r["model"] = cost_set
        if csv_fields is None:
            csv_fields = list(rows[0].keys())
        all_rows.extend(rows)
        with (out_dir / f"{stem}.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=csv_fields)
            w.writeheader()
            w.writerows(rows)
        md = sw.markdown_tables(assembled, STREAMS,
                                sw._grid_points(ratio))
        (out_dir / f"{stem}.md").write_text(
            f"# {stem}\n\nconstants constants.measured.v3.json "
            f"(fingerprint {meta['constants_fingerprint']}), cost set "
            f"{cost_set}, 10 reps, seed 7, C_fail/C {ratio:g} (the "
            f"model's measured ratio).\nReference column: "
            f"{sw.REFERENCE} ({meta['reference_label']}); buy_formula "
            f"'full' (B_hat = C + (1/p_hat - 1) C_fail, W1.5).\n" + md + "\n")
        print(f"{stem}: {meta['n_cells']} cells, {meta['n_grid_points']} "
              f"grid points, {meta['shards']} shards, "
              f"cells_wall_sum {meta['cells_wall_s_sum']:.0f}s")
        merged_meta["models"].append({
            "cost_set": cost_set, "meta": {k: v for k, v in meta.items()
                                           if k != "grid"},
            "n_cells": meta["n_cells"]})
        for k, v in assembled.items():
            if k == "_lower_bound_violations":
                merged_cells.setdefault(k, []).extend(v)
            else:
                merged_cells[f"{cost_set}|{k}"] = v

    merged_meta["experiment"] = "E11_env_fragility (W4 rerun, merged)"
    merged_meta["note"] = ("cells of both models, keys prefixed "
                           "'<cost_set>|<config>|<stream>/price=<p>'; per-"
                           "model files carry the full meta and markdown")
    (out_dir / "E11_env_fragility.json").write_text(json.dumps(
        {"meta": merged_meta, "cells": merged_cells}, indent=1))
    with (out_dir / "E11_env_fragility.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=csv_fields)
        w.writeheader()
        w.writerows(all_rows)
    print(f"merged: {len([k for k in merged_cells if not k.startswith('_')])}"
          f" cells, {len(all_rows)} csv rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
