#!/usr/bin/env python3
"""Repair trajectory provenance in an existing result without model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True)
    parser.add_argument("--source-cell", required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(repo / "code"))
    from guiexp_android.expa.lane import remap_trajectory_path
    from guiexp_android.trace_verifier_v1 import (original_building_tasks,
                                                   resolve_recorded_trajectory,
                                                   sha256_path)

    result_path = Path(args.result).resolve()
    source_cell = Path(args.source_cell).resolve()
    result = json.loads(result_path.read_text())
    translation_path = source_cell / "translation.json"
    compile_path = source_cell / "compile.json"
    build_path = source_cell / "build.json"
    source_record = build_path if build_path.exists() else compile_path
    if not source_record.exists():
        raise FileNotFoundError("source cell has neither build.json nor compile.json")
    recorded_paths = {}
    if translation_path.exists():
        translation = json.loads(translation_path.read_text())
        recorded_paths = {seed: Path(translation[str(seed)]["trajectory"])
                          for seed in (1, 2, 3)}
        path_source_kind = "translation_json"
    else:
        compile_record = json.loads(compile_path.read_text())
        trajectories = compile_record.get("building_trajectories") or []
        seeds = list(compile_record.get("seeds") or [])
        if seeds != [1, 2, 3] or len(trajectories) != 3:
            raise ValueError("compile record does not identify exactly seeds 1,2,3")
        recorded_paths = {seed: Path(path) for seed, path in zip(seeds, trajectories)}
        path_source_kind = "compile_json_building_trajectories"
    paths = []
    resolutions = {}
    for seed in (1, 2, 3):
        recorded = recorded_paths[seed]
        resolved, resolution = resolve_recorded_trajectory(
            recorded, source_cell, seed, remap_trajectory_path(recorded))
        if not resolved.exists():
            raise FileNotFoundError(f"no source trajectory for seed {seed}: {recorded}")
        if resolution:
            resolutions[str(seed)] = resolution
        paths.append(resolved)
    audited = original_building_tasks(
        result["family"], paths,
        source_identity={"family": result["family"], "seeds": [1, 2, 3],
                         "path": str(source_record),
                         "sha256": sha256_path(source_record),
                         "trajectory_resolution": resolutions},
    )
    if [row["prompt"] for row in audited] != [row["prompt"] for row in result["tasks"]]:
        raise ValueError("reconstructed prompts differ from the completed result")
    if [row["prompt"] for row in audited] != [row["input"]["goal"] for row in result["extractions"]]:
        raise ValueError("audited prompts differ from extraction inputs")
    result["tasks"] = audited
    result.setdefault("provenance_audits", []).append({
        "kind": "source_cell_relative_trajectory_alias_resolution",
        "model_calls": 0,
        "path_source_kind": path_source_kind,
        "source_record_path": str(source_record),
        "source_record_sha256": sha256_path(source_record),
        "translation_path": str(translation_path) if translation_path.exists() else None,
        "translation_sha256": sha256_path(translation_path) if translation_path.exists() else None,
        "resolved_trajectory_sha256": {str(seed): sha256_path(path)
                                        for seed, path in zip((1, 2, 3), paths)},
        "prompt_equality_with_completed_extractions": True,
    })
    tmp = result_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=1, default=str))
    tmp.replace(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
