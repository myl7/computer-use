#!/usr/bin/env python3
"""Plan reuse versus rerun for revised Android initial-program diagnostics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
OLD = REPO / "experimental-results" / "guiexp_android"
NEW = REPO / "experimental-results" / "trace_verifier_20260925" / "android"
SELECTION = REPO / "analysis" / "trace_verifier_20260925" / "results" / "evidence-selection.json"
OUT = REPO / "analysis" / "trace_verifier_20260925" / "runners" / "diagnostics" / "manifest.json"
STUDY_FAMILIES = {"ContactsAddContact", "SimpleCalendarAddOneEvent",
                  "MarkorDeleteNote", "OsmAndMarker"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def old_program(model_slug: str, family: str) -> Path:
    return OLD / "t16_build" / model_slug / family / "verified_program.py"


def main() -> int:
    import sys

    sys.path.insert(0, str(REPO / "code"))
    from guiexp_android.deploy_runner import deploy_uses

    cells = []
    selection = json.loads(SELECTION.read_text())
    selected = [item for item in selection["selections"]
                if item["canonical_identity"].get("platform") == "android"
                and item["canonical_identity"].get("attempt_role") == "initial"]
    for item in selected:
        path = REPO / item["raw_path"]
        record = json.loads(path.read_text())
        if record.get("terminal_status") != "complete" or not record.get("admitted"):
            continue
        identity = item["canonical_identity"]
        family = identity["family"]
        model_slug = identity["model"].replace("/", "_")
        old_path = old_program(model_slug, family)
        old_hash = sha(old_path.read_bytes()) if old_path.is_file() else None
        new_hash = record["final_program_sha256"]
        identical = old_hash == new_hash
        t18 = OLD / "t18_fragility" / model_slug / "fragility.json"
        t20 = OLD / "t20_gate_extraction" / model_slug / family / "extraction.json"
        diagnostic = (REPO / "experimental-results" / "trace_verifier_20260925"
                      / "diagnostics" / "android" / model_slug / family / "initial"
                      / "result.json")
        completed_t18 = completed_t20 = 0
        diagnostic_hash = None
        if diagnostic.is_file():
            observed = json.loads(diagnostic.read_text())
            if observed.get("program_sha256") == new_hash:
                completed_t18 = sum(r.get("status") == "complete" for r in
                                    (observed.get("results") or {}).get("t18") or [])
                completed_t20 = sum(r.get("status") == "complete" for r in
                                    (observed.get("results") or {}).get("t20") or [])
                diagnostic_hash = sha(diagnostic.read_bytes())
        t21_root = OLD / "t21_paired_replay" / model_slug / family
        t21_match = None
        if t21_root.is_dir():
            expected_uses = deploy_uses(family, 30)
            t21_match = all(
                (lambda row, expected: row.get("goal") == expected[0]
                 and row.get("binding") == expected[1])(
                    json.loads((t21_root / f"use_{i:02d}" / "summary.json").read_text()),
                    use)
                for i, use in enumerate(expected_uses)
            )
        cells.append({
            "model": record["model"], "model_slug": model_slug, "family": family,
            "source_result": str(path.relative_to(REPO)), "source_result_sha256": sha(path.read_bytes()),
            "old_program": str(old_path.relative_to(REPO)), "old_program_sha256": old_hash,
            "selected_program_sha256": new_hash, "program_byte_identical": identical,
            "t18": {"action": "reuse" if identical and t18.is_file() else "rerun",
                    "historical_path": str(t18.relative_to(REPO)) if t18.is_file() else None,
                    "historical_sha256": sha(t18.read_bytes()) if t18.is_file() else None},
            "t20": {"action": "reuse" if identical and t20.is_file() else "rerun",
                    "historical_path": str(t20.relative_to(REPO)) if t20.is_file() else None,
                    "historical_sha256": sha(t20.read_bytes()) if t20.is_file() else None},
            "paired_agent_t21": {"action": "reuse_agent_only_for_identical_tasks",
                                 "original_tasks_match_current_generator": t21_match,
                                 "uses_checked": 30 if t21_match is not None else 0,
                                 "historical_path": (str(t21_root.relative_to(REPO))
                                                     if t21_root.is_dir() else None),
                                 "limitation": "program-side comparison must use revised program when changed"},
            "new_diagnostic": {"path": (str(diagnostic.relative_to(REPO))
                                           if diagnostic.is_file() else None),
                               "sha256": diagnostic_hash,
                               "t18_completed": completed_t18,
                               "t20_completed": completed_t20,
                               "complete": completed_t18 == 50 and completed_t20 == 5},
            "attempt_role": "initial",
            "selection_reason": item["selection_reason"],
        })
    expected = sorted({(p.parent.parent.name, p.parent.name)
                       for p in (OLD / "t16_build").glob("*/*/build.json")
                       if p.parent.name in STUDY_FAMILIES})
    covered = {(item["canonical_identity"]["model"].replace("/", "_"),
                item["canonical_identity"]["family"]) for item in selected}
    manifest = {"protocol_id": "three-building-diagnostics-v1",
                "policy": "reuse only byte-identical selected programs; repeats excluded",
                "selection_path": str(SELECTION.relative_to(REPO)),
                "selection_sha256": sha(SELECTION.read_bytes()),
                "cells": cells,
                "missing_selected_artifact_coverage": [
                    {"model_slug": model, "family": family}
                    for model, family in expected if (model, family) not in covered
                ]}
    manifest["counts"] = {
        "admitted_initial_android": len(cells),
        "reuse_cells": sum(c["program_byte_identical"] for c in cells),
        "rerun_cells": sum(not c["program_byte_identical"] for c in cells),
        "rerun_probes_total": sum(55 for c in cells if not c["program_byte_identical"]),
        "rerun_probes_completed": sum(
            c["new_diagnostic"]["t18_completed"] + c["new_diagnostic"]["t20_completed"]
            for c in cells if not c["program_byte_identical"]),
    }
    manifest["counts"]["rerun_probes_remaining"] = (
        manifest["counts"]["rerun_probes_total"]
        - manifest["counts"]["rerun_probes_completed"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"cells": len(cells),
                      "reuse_t18": sum(c["t18"]["action"] == "reuse" for c in cells),
                      "rerun_t18": sum(c["t18"]["action"] == "rerun" for c in cells),
                      "reuse_t20": sum(c["t20"]["action"] == "reuse" for c in cells),
                      "rerun_t20": sum(c["t20"]["action"] == "rerun" for c in cells)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
