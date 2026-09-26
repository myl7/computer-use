"""Audit immutable inputs behind the archived k=1/k=2/k=3 diagnostic."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEASUREMENT = ROOT / "paper/measurement_update_20260918.json"
OUT = Path(__file__).resolve().parent / "archived-trace-count-audit.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slug(model: str) -> str:
    return model.replace("/", "_")


def main() -> None:
    table = json.loads(MEASUREMENT.read_text())
    rows = table["rows"] + table["qwen_rows"]
    rows = [row for row in rows if not row.get("C_partial", False)]
    if len(rows) != 19:
        raise SystemExit(f"Expected 19 complete archived cells, found {len(rows)}")
    cells = []
    for row in rows:
        platform = row["platform"]
        if platform == "Android":
            directory = ROOT / "experimental-results/guiexp_android/t16_build" / slug(row["model"]) / row["family"]
        elif platform == "Desktop":
            directory = ROOT / "experimental-results/guiexp_osworld" / slug(row["model"]) / row["family"]
        else:
            directory = ROOT / "experimental-results/guiexp_webarena" / slug(row["model"]) / row["family"]
        build = directory / "build.json"
        artifacts = {f"k{k}": directory / f"artifact_k{k}_code.py" for k in (1, 2, 3)}
        missing = [str(path.relative_to(ROOT)) for path in (build, *artifacts.values()) if not path.is_file()]
        if missing:
            raise SystemExit(f"Missing archived trace-count source: {missing}")
        cells.append({
            "platform": platform, "model": row["model"], "family": row["family"],
            "build": {"path": str(build.relative_to(ROOT)), "sha256": sha(build)},
            "initial_programs": {key: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
                                 for key, path in artifacts.items()},
        })
    sources = [MEASUREMENT, ROOT / "paper/measurement_repeat_table.tex",
               ROOT / "analysis/safe_projected_paper_20260922/body-main-before.tex"]
    record = {
        "schema": "archived-trace-count-audit/1",
        "status": "verified",
        "cell_count": 19,
        "comparison_counts": {"k3_fewer_than_best_k1_k2": 6,
                              "k3_more_than_best_k1_k2": 4,
                              "same": 9},
        "scope": "Standalone archived evaluation on five generated supplied-input tasks using unrepaired initial k1/k2/k3 programs.",
        "exclusions": ["Not the three-building model-extracted admission test.",
                       "Does not select or repair a program in the revised pipeline."],
        "source_files": [{"path": str(path.relative_to(ROOT)), "sha256": sha(path)} for path in sources],
        "cells": cells,
    }
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"Verified {len(cells)} archived trace-count cells")


if __name__ == "__main__":
    main()
