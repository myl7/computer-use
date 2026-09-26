"""Build validated simulator profile updates from complete revision records."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from normalize_results import normalize
from validate_results import check_record, expected_main

ROOT = Path(__file__).resolve().parents[3]
RESULT_ROOT = ROOT / "experimental-results/trace_verifier_20260925"
OUT = Path(__file__).resolve().parent / "profiles.json"
OLD_CONFIG = ROOT / "experimental-results/guiexp/t2_sim_v3/paper_revision_20260922/config.json"
SELECTIONS = Path(__file__).resolve().parent / "evidence-selection.json"
PLATFORM_NAME = {"android": "Android", "desktop": "Desktop", "web": "Web"}


def old_profiles() -> dict:
    config = json.loads(OLD_CONFIG.read_text())
    result = {}
    for job in config["jobs"]:
        model = job["model"]
        for name, profile in job["profiles"].items():
            result.setdefault(model, {}).setdefault(name, profile)
    return result


def deployment_metrics(record: dict, previous: dict) -> tuple[float, float]:
    deployment = record["deployment"]
    metrics = record.get("profile_metrics") or deployment.get("profile_metrics") or {}
    if isinstance(metrics.get("d"), (int, float)) and isinstance(metrics.get("q"), (int, float)):
        return float(metrics["d"]), float(metrics["q"])
    if deployment["status"] == "reused":
        return float(previous["d"]), float(previous["q"])
    d, q = metrics.get("d"), metrics.get("q")
    if not isinstance(d, (int, float)) or not isinstance(q, (int, float)):
        raise ValueError("New deployment lacks reconciled profile_metrics d/q")
    if d < 0 or not 0 <= q <= 1:
        raise ValueError("Invalid deployment d/q")
    return float(d), float(q)


def main() -> None:
    previous = old_profiles()
    records = {}
    selections = json.loads(SELECTIONS.read_text())["selections"]
    for selection in selections:
        if selection.get("aggregation_state", "selected") != "selected":
            continue
        path = ROOT / selection["raw_path"]
        record = normalize(json.loads(path.read_text()))
        for field, value in selection["canonical_identity"].items():
            record[field] = value
        key = (record.get("platform"), record.get("model"), record.get("family"))
        if record.get("attempt_role") != "initial" or record.get("attempt_id") != "initial" or key not in expected_main():
            continue
        errors, _eligible = check_record(path, record)
        if errors:
            raise SystemExit(f"Invalid main result {path}: {errors}")
        if record["terminal_status"] not in {"complete", "extraction_failure", "generation_output_budget_failure"}:
            raise SystemExit(f"Main result is terminal but not profile-measurable: {path}: {record['terminal_status']}")
        if key in records:
            raise SystemExit(f"Duplicate main result: {key}")
        records[key] = (path, record)
    missing = expected_main() - set(records)
    if missing:
        raise SystemExit(f"Validated profiles unavailable; missing {len(missing)} main results")

    measured = {}
    admitted_by_model = {}
    failed_by_model = {}
    for (platform, model, family), (path, record) in records.items():
        name = f"{PLATFORM_NAME[platform]}/{family}"
        old = previous[model][name]
        admitted = record["admission"]
        row = {"C": record["charges"]["C"], "C_exact": record["charges"].get("exact", True),
               "observed_admitted": admitted,
               "source": str(path.relative_to(ROOT))}
        if admitted:
            row["d"], row["q"] = deployment_metrics(record, old)
            admitted_by_model.setdefault(model, []).append(row)
        else:
            failed_by_model.setdefault(model, []).append(row)
        measured[(model, name)] = row

    output = {"schema": "three-building-simulator-profiles/1",
              "protocol_id": "three-building-model-extracted-v1", "status": "validated", "models": {}}
    for model, model_profiles in previous.items():
        admitted = admitted_by_model.get(model, [])
        if not admitted:
            raise SystemExit(f"No admitted revised profiles for {model}")
        medians = {field: statistics.median(row[field] for row in admitted) for field in ("C", "d", "q")}
        failed_rows = [row for row in failed_by_model.get(model, []) if row["C_exact"]]
        failed_costs = [row["C"] for row in failed_rows]
        output["models"][model] = {}
        for name, old in model_profiles.items():
            row = measured[(model, name)]
            if row["observed_admitted"]:
                revised = {field: row[field] for field in ("C", "d", "q")}
                revised["C_fail"] = statistics.median(failed_costs) if failed_costs else row["C"]
            else:
                revised = dict(medians)
                if row["C_exact"]:
                    revised["C_fail"] = row["C"]
                else:
                    if not failed_costs:
                        raise SystemExit(f"No same-model complete failed-attempt donor for {model}/{name}")
                    donor_median = statistics.median(failed_costs)
                    revised["C_fail"] = max(donor_median, row["C"])
                    revised["C_fail_imputation"] = {
                        "rule": "same-model median complete failed-attempt charge clamped to observed lower bound",
                        "donor_ids": [donor["source"] for donor in failed_rows],
                        "donor_median_pw": donor_median,
                        "observed_lower_bound_pw": row["C"],
                        "clamp_applied": row["C"] > donor_median,
                    }
            revised.update(observed_admitted=row["observed_admitted"], source=row["source"])
            output["models"][model][name] = {
                "previous": {field: old[field] for field in ("C", "C_fail", "d", "q")},
                "revised": revised,
            }
    OUT.write_text(json.dumps(output, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(f"Wrote validated profiles: {OUT}")


if __name__ == "__main__":
    main()
