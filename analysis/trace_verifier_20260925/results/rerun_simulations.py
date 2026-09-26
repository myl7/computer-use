"""Prepare and run revised offline studies with frozen streams and settings.

The driver changes measured profile fields only. It preserves every job's
assigned `p`, stream specification, seed, repetitions, and policy settings.
It refuses to prepare until `profiles.json` contains validated old/new values.
No API calls are made.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PROFILE_INPUT = HERE / "profiles.json"
SIM_ROOT = ROOT / "experimental-results/trace_verifier_20260925/simulations"
STUDIES = {
    "main": (ROOT / "code/t2sim/protocol_explore/study_v2.py",
             ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"),
    "comparisons": (ROOT / "code/t2sim/pace_baselines/study_v2.py",
                    ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"),
    "ablations": (ROOT / "code/t2sim/pace_review_controls/study.py",
                  ROOT / "experimental-results/guiexp/t2_sim_v3/pace_review_controls_20260923/config.json"),
    "sensitivity": (ROOT / "code/t2sim/pace_price_sensitivity/study.py",
                    ROOT / "experimental-results/guiexp/t2_sim_v3/pace_price_sensitivity_20260923/config.json"),
}
FIELDS = ("C", "C_fail", "d", "q")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def main_destination(out: Path, spec: dict) -> Path:
    storage_key = main_storage_key(spec)
    return out / "cells" / (hashlib.sha256(storage_key.encode()).hexdigest()[:20] + ".json")


def main_storage_key(spec: dict) -> str:
    return spec.get("_source_group", spec.get("suite", "unknown")) + "/" + spec["key"]


def assert_unique_destinations(name: str, config: dict, destination) -> None:
    paths = [str(destination(job)) for job in config["jobs"]]
    duplicates = [path for path, count in Counter(paths).items() if count > 1]
    if duplicates:
        raise ValueError(f"{name} has duplicate result destinations: {duplicates}")


def load_module(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def revised_profile(saved: dict, update: dict) -> dict:
    out = dict(saved)
    revised = update["revised"]
    for field in FIELDS:
        out[field] = revised[field]
    out["observed_admitted"] = revised["observed_admitted"]
    out["source"] = revised["source"]
    if revised.get("C_fail_imputation"):
        out["C_fail_imputation"] = revised["C_fail_imputation"]
    out.setdefault("origins", {})["revision"] = "validated three-building-model-extracted-v1 profile"
    if out.get("p") != saved.get("p"):
        raise AssertionError("Assigned scenario probability changed")
    return out


def transform_config(config: dict, profiles: dict) -> tuple[dict, int]:
    config = json.loads(json.dumps(config))
    changed = 0
    for job in config["jobs"]:
        spec = job.get("spec", job)
        model_updates = profiles["models"].get(spec["model"], {})
        for name, saved in spec.get("profiles", {}).items():
            if name not in model_updates:
                raise ValueError(f"Missing validated profile: {spec['model']}/{name}")
            before_p = saved.get("p")
            spec["profiles"][name] = revised_profile(saved, model_updates[name])
            if spec["profiles"][name].get("p") != before_p:
                raise AssertionError("Scenario p changed")
            changed += 1
    config["revision"] = {
        "protocol_id": "three-building-model-extracted-v1",
        "profile_input": str(PROFILE_INPUT.relative_to(ROOT)),
        "profile_input_sha256": sha(PROFILE_INPUT) if PROFILE_INPUT.is_file() else None,
        "base_config_sha256": None,
        "assigned_scenario_probabilities_preserved": True,
    }
    return config, changed


def prepare_main_config(base: dict) -> dict:
    """Use the active 300 original + 300 fresh + 12 real condition specs."""
    jobs = []
    for wrapper in base["jobs"]:
        spec = json.loads(json.dumps(wrapper["spec"]))
        spec["_source_group"] = wrapper["group"]
        jobs.append(spec)
    for job in jobs:
        job.pop("cached_v1", None)
    groups = [job["group"] for job in base["jobs"]]
    if Counter(groups) != Counter({"original_grid": 300, "fresh_grid": 300, "real_arrivals": 12}):
        raise ValueError("Active source coverage is not 300+300+12")
    return {"schema": "three-building-active-main/1", "jobs": jobs,
            "source_groups": dict(Counter(groups)),
            "source_config": "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"}


def relink_to_revised_main(config: dict) -> None:
    module = load_module("trace_verifier_main_link", STUDIES["main"][0])
    main_out = SIM_ROOT / "main"
    for job in config["jobs"]:
        spec = job.get("spec", job)
        source_spec = dict(spec, _source_group=source_group_for_job(job, spec))
        source = main_destination(main_out, source_spec)
        if not source.is_file():
            raise SystemExit("Revised main cells must complete before downstream preparation: " + str(source))
        job["source"] = str(source.relative_to(ROOT))
        job["source_sha256"] = sha(source)


def source_group_for_job(job: dict, spec: dict) -> str:
    if job.get("group") in {"original_grid", "fresh_grid", "real_arrivals"}:
        return job["group"]
    suite = spec.get("suite", "")
    if suite == "fresh_grid":
        return "fresh_grid"
    if suite.removeprefix("paired_") == "validation_real":
        return "real_arrivals"
    return "original_grid"


def rebuild_sensitivity_dependencies(config: dict, profiles: dict, module) -> None:
    observations = {}
    for model, rows in profiles["models"].items():
        observations[model] = {}
        for name, update in rows.items():
            revised = update["revised"]
            observations[model][name] = revised_observation(revised)
    config["observations"] = observations
    for job in config["jobs"]:
        spec = job["spec"]
        job["observations"] = observations[spec["model"]]
        job["partial_price_overrides"] = apply_partial_observation_bounds(
            spec, job["observations"])
        hashes = []
        for rep in range(spec["reps"]):
            arrivals, mapping, generated = module.paired.prepare(spec, "real_arrivals", rep)
            hashes.append({"stream": module.fingerprint(arrivals),
                           "mapping": module.fingerprint(mapping),
                           "profiles": module.fingerprint(generated)})
        job["original_hashes"] = hashes
    config["branch_counts"] = dict(Counter(item["branch"] for rows in observations.values() for item in rows.values()))


def apply_partial_observation_bounds(spec: dict, observations: dict) -> dict:
    overrides = {}
    for name, observation in observations.items():
        if observation["branch"] != "partial":
            continue
        profile = spec["profiles"][name]
        lower_bound = observation["observed_price"]
        modeled = profile["C_fail"]
        if modeled < lower_bound:
            raise ValueError(f"Modeled C_fail is below observed lower bound: {name}")
        profile["C_fail"] = lower_bound
        overrides[name] = {
            "modeled_C_fail": modeled,
            "observed_lower_bound_C_fail": lower_bound,
            "reason": "Sensitivity starts from the observed partial charge and applies max(bound, ratio*C).",
        }
    return overrides


def revised_observation(revised: dict) -> dict:
    admitted = revised["observed_admitted"]
    imputation = revised.get("C_fail_imputation")
    if admitted:
        branch, price, extraction = "accepted", revised["C"], "C"
    elif imputation:
        branch = "partial"
        price = imputation["observed_lower_bound_pw"]
        extraction = "observed lower-bound failed-attempt charge; modeled C_fail remains unobserved"
    else:
        branch, price, extraction = "rejected_complete", revised["C_fail"], "C_fail"
    if not isinstance(price, (int, float)) or price <= 0:
        raise ValueError("Sensitivity observation price must be positive")
    return {
        "branch": branch,
        "observed_price": price,
        "source": revised["source"],
        "row_sha256": hashlib.sha256(json.dumps(revised, sort_keys=True).encode()).hexdigest(),
        "extraction": extraction,
    }


def add_missing_cost_sensitivity(config: dict, profiles: dict) -> int:
    """Bracket the one lower-bound DeepSeek Web failed charge without changing p."""
    model = "deepseek/deepseek-v4-flash-vision-exp"
    name = "Web/CommentPost"
    update = profiles["models"].get(model, {}).get(name, {}).get("revised", {})
    imputation = update.get("C_fail_imputation")
    if not imputation:
        return 0
    lower = imputation["observed_lower_bound_pw"]
    modeled = update["C_fail"]
    selections = (
        ("bursty_default", {
            "_source_group": "original_grid", "model": model, "pattern": "bursty",
            "admission": "half", "world": "default",
        }),
        ("bpi2019_real", {
            "_source_group": "real_arrivals", "model": model, "pattern": "bpi2019",
            "admission": "mixture",
        }),
    )
    additions = []
    for case, criteria in selections:
        matches = [job for job in config["jobs"]
                   if all(job.get(field) == value for field, value in criteria.items())]
        if len(matches) != 1:
            raise ValueError(f"Expected one missing-cost template for {case}, found {len(matches)}")
        template = matches[0]
        for tag, value in (("missing_cost_lower_bound", lower), ("missing_cost_2x_modeled", 2 * modeled)):
            job = json.loads(json.dumps(template))
            job["tag"] = tag
            job["group"] = "additional_missing_cost"
            job["_source_group"] = "additional_missing_cost"
            job["key"] = f"missing_cost/{model}/{case}/{tag}"
            job["missing_cost_base_case"] = {
                "case": case, "source_group": template["_source_group"],
                "key": template["key"], "seed": template["seed"],
            }
            job["profiles"][name]["C_fail"] = value
            job["profiles"][name]["missing_cost_sensitivity"] = {
                "base_modeled_pw": modeled, "assigned_pw": value,
                "lower_bound_pw": lower, "scenario": tag,
            }
            additions.append(job)
    config["jobs"].extend(additions)
    assert_unique_destinations("main", config, lambda job: main_destination(Path("."), job))
    config["expected_cells"] = len(config["jobs"])
    config["additional_missing_cost_cells"] = len(additions)
    config.setdefault("assumptions", []).append(
        "DeepSeek Web missing-cost sensitivity uses its observed lower bound and twice the modeled clamped donor value; assigned admission probabilities are unchanged.")
    return len(additions)


def reusable_cell_matches(name: str, job: dict, result: dict) -> bool:
    if result.get("key") != job.get("key"):
        return False
    if name == "main":
        return result.get("suite") == job.get("suite") and result.get("spec") == job
    if name in {"comparisons", "ablations"}:
        return (result.get("group") == job.get("group")
                and result.get("spec") == job.get("spec")
                and result.get("source") == job.get("source")
                and result.get("source_sha256") == job.get("source_sha256"))
    if name == "sensitivity":
        expected_hashes = job.get("original_hashes", [])
        actual_hashes = [row.get("original") for row in result.get("input_hashes", [])]
        return (result.get("scenario") == job.get("scenario")
                and result.get("kind") == job.get("kind")
                and result.get("ratio") == job.get("ratio")
                and result.get("model") == job.get("spec", {}).get("model")
                and result.get("pattern") == job.get("spec", {}).get("pattern")
                and result.get("source") == job.get("source")
                and result.get("source_sha256") == job.get("source_sha256")
                and actual_hashes == expected_hashes)
    raise ValueError(f"Unknown study: {name}")


def summarize_main(module, config: dict, out: Path) -> dict:
    core = [job for job in config["jobs"] if job.get("_source_group") != "additional_missing_cost"]
    additions = [job for job in config["jobs"] if job.get("_source_group") == "additional_missing_cost"]
    result = module.summarize(dict(config, jobs=core))
    result["core_completed"] = result["completed"]
    result["core_expected"] = result["expected"]
    result["completed"] = len(core) + len(additions)
    result["expected"] = len(config["jobs"])
    result["additional_missing_cost"] = {
        "completed": len(additions), "expected": len(additions),
        "excluded_from_core_suite_aggregates": True,
        "cells": [json.loads(main_destination(out, job).read_text()) for job in additions],
    }
    (out / "summary.json").write_text(dump(result))
    return result


def validate_completed_cells(name: str, config: dict, destination, out: Path) -> dict:
    rows = []
    for index, job in enumerate(config["jobs"]):
        path = destination(job)
        if not path.is_file():
            raise RuntimeError(f"Missing completed cell: {path}")
        result = json.loads(path.read_text())
        if not reusable_cell_matches(name, job, result):
            raise RuntimeError(f"Completed cell does not match frozen job: {path}")
        rows.append({
            "index": index,
            "destination": str(path.relative_to(ROOT)),
            "cell_sha256": sha(path),
            "job_sha256": hashlib.sha256(json.dumps(job, sort_keys=True).encode()).hexdigest(),
        })
    audit = {
        "schema": "trace-verifier-simulation-cell-validation/1",
        "study": name,
        "status": "validated",
        "config_sha256": sha(out / "config.json"),
        "profile_input_sha256": sha(PROFILE_INPUT),
        "profile_mapping_sha256": sha(out / "profile_mapping.json"),
        "expected": len(config["jobs"]),
        "validated": len(rows),
        "unique_destinations": len({row["destination"] for row in rows}),
        "cells": rows,
    }
    (out / "cell-validation.json").write_text(dump(audit))
    return audit


def prepare(names: list[str]) -> None:
    if not PROFILE_INPUT.is_file():
        raise SystemExit(f"Validated profiles unavailable: {PROFILE_INPUT}")
    profiles = json.loads(PROFILE_INPUT.read_text())
    if profiles.get("status") != "validated" or profiles.get("protocol_id") != "three-building-model-extracted-v1":
        raise SystemExit("profiles.json is not marked validated for this protocol")
    for name in names:
        _module_path, base_path = STUDIES[name]
        base = json.loads(base_path.read_text())
        if name == "main":
            base = prepare_main_config(base)
        revised, changed = transform_config(base, profiles)
        missing_cost_cells = add_missing_cost_sensitivity(revised, profiles) if name == "main" else 0
        if name in {"comparisons", "ablations", "sensitivity"}:
            relink_to_revised_main(revised)
        if name == "sensitivity":
            module = load_module("trace_verifier_sensitivity_prepare", _module_path)
            rebuild_sensitivity_dependencies(revised, profiles, module)
        revised["revision"]["base_config_sha256"] = sha(base_path)
        out = SIM_ROOT / name
        out.mkdir(parents=True, exist_ok=True)
        target = out / "config.json"
        if name == "main":
            assert_unique_destinations(name, revised, lambda job: main_destination(out, job))
        encoded = dump(revised)
        if target.exists() and target.read_text() != encoded:
            raise SystemExit(f"Refusing to replace different frozen config: {target}")
        target.write_text(encoded)
        (out / "profile_mapping.json").write_text(dump(profiles))
        print(f"Prepared {name}: {len(revised['jobs'])} jobs, {changed} profile copies, {missing_cost_cells} missing-cost sensitivity cells")


def run_one(name: str, workers: int) -> None:
    module_path, _base = STUDIES[name]
    out = SIM_ROOT / name
    config = json.loads((out / "config.json").read_text())
    module = load_module(f"trace_verifier_{name}_study", module_path)
    module.OUT = out
    (out / "cells").mkdir(exist_ok=True)
    if name == "main":
        module.old.destination = main_destination
        destination = lambda job: main_destination(out, job)
        summarize = lambda: summarize_main(module, config, out)
    else:
        destination = module.destination
        summarize = lambda: module.summarize(config)
    assert_unique_destinations(name, config, destination)
    pending = []
    for job in config["jobs"]:
        dest = destination(job)
        if not dest.exists():
            pending.append(job)
            continue
        try:
            saved = json.loads(dest.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unreadable reusable cell {dest}: {exc}") from exc
        if not reusable_cell_matches(name, job, saved):
            raise RuntimeError(f"Reusable cell does not match frozen job: {dest}")
    print(f"{name}: {len(pending)} pending of {len(config['jobs'])}", flush=True)
    context = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        futures = {pool.submit(module.cell, job): job for job in pending}
        for future in as_completed(futures):
            job = futures[future]
            result = future.result()
            dest = destination(job)
            if dest.exists():
                raise RuntimeError(f"Concurrent write: {dest}")
            temp = dest.with_suffix(".tmp")
            temp.write_text(dump(result))
            temp.replace(dest)
    result = summarize()
    audit = validate_completed_cells(name, config, destination, out)
    if result.get("completed") != len(config["jobs"]) or result.get("expected") != len(config["jobs"]):
        raise RuntimeError(f"{name} summary coverage does not match frozen config")
    result_path = out / ("evidence.json" if name == "sensitivity" else "summary.json")
    if not result_path.is_file():
        raise RuntimeError(f"{name} result summary was not written: {result_path}")
    (out / "rerun-complete.json").write_text(dump({
        "study": name, "status": "complete", "config_sha256": sha(out / "config.json"),
        "profile_input_sha256": sha(PROFILE_INPUT),
        "profile_mapping_sha256": sha(out / "profile_mapping.json"),
        "completed": audit["validated"], "expected": len(config["jobs"]),
        "unique_destinations": audit["unique_destinations"],
        "cell_validation_sha256": sha(out / "cell-validation.json"),
        "result_summary": str(result_path.relative_to(ROOT)),
        "result_summary_sha256": sha(result_path),
        "reuse_audit_sha256": sha(out / "reuse-audit.json") if (out / "reuse-audit.json").is_file() else None,
        "core_preservation_audit_sha256": sha(out / "core-preservation-audit.json") if (out / "core-preservation-audit.json").is_file() else None,
    }))
    print(f"{name}: complete", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", nargs="?", const="all", choices=["all", *STUDIES])
    parser.add_argument("--run", choices=["all", *STUDIES])
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    selected = args.run or args.prepare
    names = list(STUDIES) if selected == "all" else [selected]
    if args.prepare:
        prepare(names)
    if args.run:
        for name in names:
            run_one(name, args.workers)
    if not args.prepare and not args.run:
        parser.error("Choose --prepare and/or --run")


if __name__ == "__main__":
    main()
