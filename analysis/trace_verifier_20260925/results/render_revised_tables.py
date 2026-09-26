"""Gate and render revised generated paper tables from explicit manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from diagnostics_validation import validate_diagnostics

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RENDERER = ROOT / "paper/rewrite-review/render_pace_tables.py"
INPUTS = HERE / "render-inputs"
SMOKE = ROOT / "experimental-results/trace_verifier_20260925/render-smoke"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def require_complete() -> dict:
    manifest_path = INPUTS / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"Revised render manifest unavailable: {manifest_path}")
    manifest = load(manifest_path)
    if manifest.get("protocol_id") != "three-building-model-extracted-v1" or manifest.get("status") != "complete":
        raise SystemExit("Revised render inputs are not complete")
    aggregation = load(ROOT / manifest["aggregation"])
    if aggregation.get("status") != "complete" or aggregation.get("main_selected") != 20:
        raise SystemExit("Main 20-cell aggregation is incomplete")
    coverage = load(ROOT / manifest["coverage"])
    if not coverage.get("coverage_ready"):
        raise SystemExit("Main/repeat coverage is incomplete")
    if coverage.get("invalid_records"):
        raise SystemExit("Coverage contains invalid records")
    if coverage.get("duplicate_selections"):
        raise SystemExit("Coverage contains duplicate selections")
    if coverage.get("seen_main_count") != 20 or coverage.get("seen_repeat_count") != 22:
        raise SystemExit("Coverage counts do not match 20 main and 22 repeat attempts")
    profiles = load(ROOT / manifest["profiles"])
    if profiles.get("status") != "validated":
        raise SystemExit("Profiles are not validated")
    expected_studies = {"main", "comparisons", "ablations", "sensitivity"}
    if set(manifest.get("simulations", {})) != expected_studies:
        raise SystemExit("Render manifest must name exactly four revised studies")
    for study, relative in manifest["simulations"].items():
        record = load(ROOT / relative)
        if (record.get("study") != study or record.get("status") != "complete"
                or record.get("completed") != record.get("expected") or not record.get("completed")):
            raise SystemExit(f"Simulation completion mismatch: {study}")
    try:
        diagnostics = validate_diagnostics(require_complete=True)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if diagnostics["completed"] != diagnostics["expected"]:
        raise SystemExit("Selected-program diagnostics are incomplete")
    claims = load(ROOT / manifest["claim_deltas"])
    if claims.get("status") != "complete":
        raise SystemExit("Paper claim deltas are incomplete")
    for relative, expected in manifest["source_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise SystemExit(f"Render input hash mismatch: {relative}")
    provenance_path = ROOT / manifest["provenance"]
    provenance = load(provenance_path)
    if provenance.get("protocol_id") != "three-building-model-extracted-v1":
        raise SystemExit("Render provenance protocol mismatch")
    checks = provenance.get("checks", {})
    if (checks.get("main_expected") != 20 or checks.get("repeat_expected") != 22 or
            checks.get("paired_cells") != 6 or checks.get("paired_bindings") != 180 or
            checks.get("deployment_frozen_pw_cells") != 11 or
            checks.get("simulation_cells") != {"main": 616, "comparisons": 612, "ablations": 612, "sensitivity": 72} or
            checks.get("standard_conditions") != 612 or checks.get("sensitivity_conditions") != 72 or
            checks.get("additional_missing_cost_conditions") != 4 or
            not checks.get("independent_point_checks")):
        raise SystemExit("Render provenance checks are incomplete")
    for relative, expected in provenance.get("source_sha256", {}).items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"Evidence provenance hash mismatch: {relative}")
    for name, expected in provenance.get("outputs_sha256", {}).items():
        path = INPUTS / name
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"Generated input hash mismatch: {name}")
    return manifest


def run_renderer(analysis_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PACE_REVISED_INPUTS": "1",
        "PACE_TABLE_ANALYSIS_DIR": str(analysis_dir),
        "PACE_TABLE_DATA": str(analysis_dir / "table-data.json"),
        "PACE_ONLINE_DATA": str(analysis_dir / "online-uncertainty.json"),
        "PACE_MEASUREMENT_DATA": str(analysis_dir / "measurement-data.json"),
        "PACE_TABLE_OUTPUT_DIR": str(output_dir),
    })
    subprocess.run(["python3", str(RENDERER)], cwd=ROOT, env=env, check=True)


def smoke() -> None:
    if SMOKE.exists():
        shutil.rmtree(SMOKE)
    analysis = SMOKE / "inputs"
    output = SMOKE / "tables"
    analysis.mkdir(parents=True)
    old = ROOT / "analysis/pace_edit_20260923/uncertainty"
    shutil.copyfile(old / "table-data.json", analysis / "table-data.json")
    shutil.copyfile(old / "online-uncertainty.json", analysis / "online-uncertainty.json")
    shutil.copyfile(ROOT / "paper/measurement_update_20260918.json", analysis / "measurement-data.json")
    provenance = load(old / "provenance.json")
    provenance["outputs_sha256"]["table-data.json"] = sha(analysis / "table-data.json")
    provenance["outputs_sha256"]["online-uncertainty.json"] = sha(analysis / "online-uncertainty.json")
    provenance["checks"]["independent_point_checks"] = provenance["checks"].get("published_point_estimates_checked", 1)
    provenance["source_sha256"] = {str((analysis / name).relative_to(ROOT)): sha(analysis / name)
                                   for name in ("table-data.json", "online-uncertainty.json", "measurement-data.json")}
    (analysis / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    run_renderer(analysis, output)
    expected = {"measurement_price_table.tex", "measurement_share_table.tex",
                "measurement_paired_table.tex", "measurement_repeat_table.tex",
                "measurement_uncertainty_tables.tex", "online_results_tables.tex",
                "online_results_full.tex", "online_ablation_table.tex",
                "online_ablation_effects_full.tex", "online_ablation_absolute.tex",
                "online_sensitivity_table.tex", "table_macros.tex"}
    if not expected.issubset({path.name for path in output.glob("*.tex")}):
        raise SystemExit("Scratch render omitted generated paper tables")
    (SMOKE / "smoke.json").write_text(json.dumps({
        "schema": "three-building-render-smoke/1", "status": "passed",
        "fixture_scope": "historical data used only to test explicit input routing and scratch output isolation",
        "renderer_sha256": sha(RENDERER),
        "outputs": {path.name: sha(path) for path in sorted(output.glob("*.tex"))},
    }, indent=2, sort_keys=True) + "\n")
    print(f"Scratch render passed: {SMOKE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        smoke()
    if args.final:
        manifest = require_complete()
        run_renderer(INPUTS, ROOT / "paper")
        print(f"Rendered revised tables from {INPUTS / 'manifest.json'}")
    if not args.smoke and not args.final:
        parser.error("Choose --smoke or --final")


if __name__ == "__main__":
    main()
