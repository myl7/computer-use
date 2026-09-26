"""Row-level validation for selected-program diagnostic evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "analysis/trace_verifier_20260925/runners/diagnostics/manifest.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_diagnostics(require_complete: bool = True) -> dict:
    manifest = json.loads(MANIFEST.read_text())
    errors = []
    completed = 0
    expected = 0
    for cell in manifest.get("cells", []):
        for kind in ("t18", "t20"):
            plan = cell[kind]
            if plan["action"] == "reuse":
                path = ROOT / plan["historical_path"]
                if not path.is_file() or sha(path) != plan["historical_sha256"]:
                    errors.append(f"stale_reuse:{cell['model']}/{cell['family']}/{kind}")
                continue
        if cell["t18"]["action"] != "rerun" and cell["t20"]["action"] != "rerun":
            continue
        expected += 55
        diagnostic = cell.get("new_diagnostic") or {}
        diagnostic_path = diagnostic.get("path")
        if not diagnostic_path:
            errors.append(f"missing_result:{cell['model']}/{cell['family']}")
            continue
        path = ROOT / diagnostic_path
        if not path.is_file() or sha(path) != diagnostic.get("sha256"):
            errors.append(f"missing_or_stale_result:{cell['model']}/{cell['family']}")
            continue
        result = json.loads(path.read_text())
        t18, t20 = result.get("results", {}).get("t18", []), result.get("results", {}).get("t20", [])
        valid18 = [row for row in t18 if row.get("status") == "complete"]
        valid20 = [row for row in t20 if row.get("status") == "complete"
                   and ((row.get("model_calls") or 0) > 0
                        or len((row.get("extraction") or {}).get("calls") or []) > 0)]
        completed += len(valid18) + len(valid20)
        if len(valid18) != 50 or len(valid20) != 5:
            errors.append(f"incomplete_rows:{cell['model']}/{cell['family']}:t18={len(valid18)}/50:t20={len(valid20)}/5")
        if result.get("terminal_status") == "complete" and (len(valid18) != 50 or len(valid20) != 5):
            errors.append(f"false_complete_label:{cell['model']}/{cell['family']}")
    counts = manifest.get("counts", {})
    if counts.get("rerun_probes_completed") != completed:
        errors.append("manifest_completed_count_mismatch")
    if counts.get("rerun_probes_total") != expected:
        errors.append("manifest_expected_count_mismatch")
    if require_complete and (errors or completed != expected or manifest.get("missing_selected_artifact_coverage")):
        raise ValueError("Diagnostics incomplete: " + "; ".join(errors or [f"{completed}/{expected}"]))
    return {"protocol_id": manifest.get("protocol_id"), "expected": expected,
            "completed": completed, "remaining": expected-completed, "errors": errors,
            "missing_selected_artifact_coverage": manifest.get("missing_selected_artifact_coverage", []),
            "manifest_sha256": sha(MANIFEST)}
