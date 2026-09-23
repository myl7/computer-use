"""Frozen sensitivity to missing prices and adverse cost dependence."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experimental-results/guiexp/t2_sim_v3/pace_price_sensitivity_20260923"
EVIDENCE = ROOT / "analysis/safe_projected_paper_20260922/evaluation-evidence.json"
MEASUREMENT = ROOT / "paper/measurement_update_20260918.json"
MAIN = "safe_projected_025"
EQUAL = "pace_narrow_price"
POLICIES = (MAIN, EQUAL, "reactive")
RATIOS = (0.5, 1.0, 2.0, 5.0)
SCENARIOS = tuple(f"missing_price_r{r:g}" for r in RATIOS) + (
    "adverse_probability", "adverse_probability_recurrence")
PARAMETERS = ("ttl", "k_min", "h", "m", "tau0", "silent", "penalty", "fallback_mult")
BOOTSTRAPS = 2000
BOOTSTRAP_SEED = 20260923041


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paired = load("pace_price_source_study", ROOT / "code/t2sim/pace_baselines/study_v2.py")
engine = paired.engine
original = paired.original


def encoded(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(value):
    return original.fingerprint(value)


def destination(job):
    return OUT / "cells" / (hashlib.sha256(job["key"].encode()).hexdigest()[:20] + ".json")


def identify_observations(table):
    observed = {}
    for row in original.profiles_reader.merged_rows(table):
        model = row["model"]
        name = row["platform"] + "/" + row["family"]
        if type(row.get("admitted")) is not bool:
            raise ValueError("Missing observed admission outcome: " + model + "/" + name)
        partial = bool(row.get("C_partial", False))
        if row["admitted"] and partial:
            raise ValueError("Accepted price cannot be partial: " + name)
        if row["admitted"]:
            branch = "accepted"
            price = row["C"]
        else:
            branch = "partial" if partial else "rejected_complete"
            price = original.profiles_reader.observed_failed_cost(row)
        if not math.isfinite(price) or price <= 0 or not row.get("source"):
            raise ValueError("Unsupported observed price: " + model + "/" + name)
        observed.setdefault(model, {})[name] = dict(
            branch=branch, observed_price=price, source=row["source"],
            row_sha256=fingerprint(row),
            extraction="C" if row["admitted"] or "C_with_repair" not in row.get("measured", {})
            else "measured.C_with_repair")
    return observed


def modify_prices(profiles, observations, ratio):
    if ratio <= 0:
        raise ValueError("Ratio must be positive")
    result = deepcopy(profiles)
    for name, profile in result.items():
        item = observations[name]
        branch, price = item["branch"], item["observed_price"]
        if branch == "accepted":
            assert profile["C"] == price
            profile["C_fail"] = ratio * price
        elif branch == "rejected_complete":
            assert profile["C_fail"] == price
            profile["C"] = price / ratio
        elif branch == "partial":
            assert profile["C_fail"] == price
            profile["C_fail"] = max(price, ratio * profile["C"])
        else:
            raise ValueError("Unknown observed-price branch: " + branch)
    return result


def adverse_probabilities(profiles):
    names = sorted(profiles, key=lambda name: (profiles[name]["C"] / profiles[name]["c"], name))
    if len(names) < 2:
        raise ValueError("Rank sensitivity requires at least two profiles")
    return {name: 0.9 - 0.8 * rank / (len(names) - 1)
            for rank, name in enumerate(names)}


def origin_assignment(spec, rep, arrivals):
    """Recover the existing profile copies without using simulation outcomes."""
    assert spec["suite"].removeprefix("paired_") == "validation_real"
    salt = int(hashlib.sha256(spec["key"].encode()).hexdigest()[:8], 16)
    rng = random.Random(spec["seed"] * 1000 + rep + salt)
    names = sorted(spec["profiles"])
    rng.shuffle(names)
    return {family: names[i % len(names)] for i, family in enumerate(sorted(set(arrivals)))}


def remap_by_recurrence(origins, profiles, arrivals):
    counts = Counter(arrivals)
    families = sorted(origins, key=lambda family: (-counts[family], family))
    copies = sorted(origins, key=lambda family: (
        -profiles[origins[family]]["C"] / profiles[origins[family]]["c"],
        origins[family], family))
    return {target: origins[source] for target, source in zip(families, copies)}


def prepare(job, rep):
    spec = job["spec"]
    arrivals, mapping, profiles = paired.prepare(spec, "real_arrivals", rep)
    hashes = dict(stream=fingerprint(arrivals), mapping=fingerprint(mapping),
                  profiles=fingerprint(profiles))
    if hashes != job["original_hashes"][rep]:
        raise ValueError((job["key"], rep, "source input hash mismatch"))
    origins = origin_assignment(spec, rep, arrivals)
    original_origins = dict(origins)
    keys = ("c", "d", "C", "C_fail", "q", "pi")
    assert spec["price_scale"] == spec["cf_scale"] == 1
    for family, name in origins.items():
        assert all(profiles[family][key] == spec["profiles"][name][key] for key in keys)
    if job["kind"] == "missing_price":
        changed = modify_prices(spec["profiles"], job["observations"], job["ratio"])
        for family, name in origins.items():
            profiles[family]["C"] = changed[name]["C"]
            profiles[family]["C_fail"] = changed[name]["C_fail"]
    else:
        hidden = adverse_probabilities(spec["profiles"])
        if job["kind"] == "adverse_probability_recurrence":
            origins = remap_by_recurrence(origins, spec["profiles"], arrivals)
        for family, name in origins.items():
            profiles[family] = {key: spec["profiles"][name][key] for key in keys}
            profiles[family]["p"] = hidden[name]
    changed_hashes = dict(stream=fingerprint(arrivals), mapping=fingerprint(mapping),
                          profiles=fingerprint(profiles), origins=fingerprint(origins))
    assert changed_hashes["stream"] == hashes["stream"]
    assert changed_hashes["mapping"] == hashes["mapping"]
    assert Counter(origins.values()) == Counter(original_origins.values())
    if job["kind"] != "adverse_probability_recurrence":
        assert origins == original_origins
    return arrivals, mapping, profiles, dict(
        original=hashes, transformed=changed_hashes,
        original_origins=fingerprint(original_origins),
        profile_assignment_preserved=origins == original_origins,
        profile_multiset_preserved=True, outcome_seed=spec["seed"] * 1000 + rep,
        arrivals=len(arrivals), families=len(mapping),
        origin_counts=dict(Counter(origins.values())),
        hidden_probability_min=min(row["p"] for row in profiles.values()),
        hidden_probability_max=max(row["p"] for row in profiles.values()))


def make_config():
    table = json.loads(MEASUREMENT.read_text())
    observations = identify_observations(table)
    evidence = json.loads(EVIDENCE.read_text())
    saved_cells = evidence["cells"]["real_arrivals"]
    assert len(saved_cells) == 12
    jobs, files = [], {Path(__file__), Path(__file__).with_name("README.md"),
                       Path(__file__).with_name("test_study.py"), EVIDENCE, MEASUREMENT,
                       Path(paired.__file__), Path(engine.__file__), Path(original.__file__),
                       Path(original.engine.__file__), Path(original.reference.__file__),
                       Path(original.streams.__file__), Path(original.profiles_reader.__file__),
                       original.streams.OLD_REAL_STREAMS}
    observation_models = {}
    for saved in saved_cells:
        source = ROOT / saved["source"]
        assert sha(source) == saved["source_sha256"]
        files.add(source)
        raw = json.loads(source.read_text())
        spec = raw["spec"]
        assert spec["reps"] == 10 and spec["silent"] == 0
        model = spec["model"]
        regenerated = original.profiles_reader.profiles_from_measurement(table, model, "soft")
        assert regenerated == spec["profiles"], (model, "profile identification no longer matches")
        observed = observations[model]
        assert set(observed) == set(spec["profiles"])
        observation_models[model] = dict(profiles=observed,
                                         original_profiles=spec["profiles"],
                                         adverse_p=adverse_probabilities(spec["profiles"]))
        for item in observed.values():
            path = ROOT / item["source"]
            assert path.exists(), path
            files.add(path)
        if "path" in spec["stream_spec"]:
            files.add(ROOT / spec["stream_spec"]["path"])
        base = dict(source=saved["source"], source_sha256=saved["source_sha256"],
                    spec=spec, observations=observed, original_hashes=raw["input_hashes"])
        for ratio in RATIOS:
            scenario = f"missing_price_r{ratio:g}"
            jobs.append(dict(base, key=scenario + "/" + raw["key"],
                             scenario=scenario, kind="missing_price", ratio=ratio))
        for scenario in SCENARIOS[-2:]:
            jobs.append(dict(base, key=scenario + "/" + raw["key"],
                             scenario=scenario, kind=scenario))
    assert len(jobs) == 72 and len({str(destination(job)) for job in jobs}) == 72
    branch_counts = Counter(item["branch"] for model in observation_models.values()
                            for item in model["profiles"].values())
    assert branch_counts == {"accepted": 15, "rejected_complete": 4, "partial": 1}
    return dict(schema="pace-price-dependence-sensitivity/1", jobs=jobs,
                policies=list(POLICIES), main_policy=MAIN, observations=observation_models,
                branch_counts=dict(branch_counts), expected_conditions=72,
                repetitions_per_condition=10, scenarios=list(SCENARIOS),
                bootstrap=dict(samples=BOOTSTRAPS, seed=BOOTSTRAP_SEED),
                definitions={
                    "scope": "Prespecified sensitivity after exploratory development. No calibration or independent deployment claim.",
                    "price_rule": "Preserve accepted C or complete rejected C_fail. Infer the missing price at r in [.5,1,2,5]. Qwen Calc retains imputed C and uses max(partial spend,r*C).",
                    "dependence": "Hidden p=.9-.8*rank/(M-1) for base profiles ordered by original supplied C/c, then profile name. Copies inherit p.",
                    "recurrence": "Preserve the original multiset of profile copies. Assign decreasing C/c copies to decreasing family frequency, with lexical ties.",
                    "information": "Full-stream counts and hidden p construct the environment only. Unchanged proposal sees observed history and supplied costs/hazard.",
                    "pairing": "Original seeds, streams, and base hashes checked per repetition. Same environment and keyed outcome coins across PACE, equal-price, and ReAct.",
                    "summary": "Ratio of mean policy cost to mean reference cost within each condition. Equal-weight mean across conditions. Retain all wins and losses.",
                    "interval": "2000 shared bootstrap resamples of the ten repetition indices, conditional on retained logs and profiles.",
                    "guard": "Every-prefix budget checked by unchanged engine with tolerance 1e-8*max(1,1.25*A_t).",
                },
                source_sha256={str(p.relative_to(ROOT)): sha(p) for p in sorted(files)})


def freeze():
    config = make_config()
    manifest = OUT / "config.json"
    value = encoded(config)
    if manifest.exists():
        if manifest.read_text() != value:
            raise SystemExit("Frozen study differs. Preserve this output and create a new version.")
        return config
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit("Freeze requires an empty output directory")
    (OUT / "cells").mkdir(parents=True)
    for name in config["source_sha256"]:
        path = ROOT / name
        if path.suffix == ".py" or path in (MEASUREMENT, Path(__file__).with_name("README.md")):
            target = OUT / "source_snapshot" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    manifest.write_text(value)
    (OUT / "freeze-record.json").write_text(encoded(dict(
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        config_sha256=sha(manifest), design_sha256=sha(Path(__file__).with_name("README.md")),
        conditions=72, repetitions=720, status="frozen before sensitivity runs")))
    return config


def check_account(raw, agent_cost, protected):
    total = sum(raw[key] for key in original.COMPONENTS)
    assert math.isclose(total, raw["tokens"] - raw["harm_tokens"], rel_tol=1e-10, abs_tol=1e-6)
    assert math.isclose(total, raw.get("token_cost", total), rel_tol=1e-10, abs_tol=1e-6)
    if protected:
        assert math.isclose(raw["baseline_cost"], agent_cost, rel_tol=1e-10, abs_tol=1e-5)
        assert raw["max_prefix_ratio"] <= 1.25 + 1.26e-8
    return total


def cell(job):
    started = time.monotonic()
    source = ROOT / job["source"]
    assert sha(source) == job["source_sha256"]
    saved = json.loads(source.read_text())
    spec = job["spec"]
    rows = {policy: [] for policy in POLICIES}
    hashes, checks = [], []
    for rep in range(spec["reps"]):
        arrivals, mapping, profiles, pairing = prepare(job, rep)
        opts = {key: spec[key] for key in PARAMETERS}
        opts["seed"] = pairing["outcome_seed"]
        reactive = original.reference.run(arrivals, profiles, mapping, "reactive", **opts)
        agent_cost = sum(reactive[key] for key in original.COMPONENTS)
        check_account(reactive, agent_cost, False)
        direct_agent = math.fsum(profiles[mapping[family]]["c"] + spec["tau0"] for family in arrivals)
        assert math.isclose(agent_cost, direct_agent, rel_tol=1e-10, abs_tol=1e-5)
        if job["kind"] != "adverse_probability_recurrence":
            assert original.compact(reactive) == saved["rows"]["reactive"][rep]
        for policy in POLICIES:
            if policy == "reactive":
                raw = reactive
            elif policy == MAIN:
                raw = engine.full_run(arrivals, profiles, mapping, MAIN, **opts)
            else:
                raw = engine.run(arrivals, profiles, mapping, EQUAL, **opts)
            check_account(raw, agent_cost, policy != "reactive")
            rows[policy].append(dict(original.compact(raw), engine=raw))
        hashes.append(pairing)
        checks.append(dict(rep=rep, components_reconcile=True, reference_matches=True,
                           analytical_reference_matches=True, prefix_guard_passed=True,
                           retained_reference_matches=job["kind"] != "adverse_probability_recurrence"))
    agent_mean = statistics.mean(row["cost"] for row in rows["reactive"])
    main_mean = statistics.mean(row["cost"] for row in rows[MAIN])
    summary = {}
    for policy in POLICIES:
        values = rows[policy]
        prefixes = [r["prefix_ratio"] for r in values if r["prefix_ratio"] is not None]
        summary[policy] = dict(mean_cost=statistics.mean(r["cost"] for r in values),
                               ratio_agent=statistics.mean(r["cost"] for r in values) / agent_mean,
                               ratio_pace=statistics.mean(r["cost"] for r in values) / main_mean,
                               mean_attempts=statistics.mean(r["attempts"] for r in values),
                               mean_quality=statistics.mean(r["quality"] for r in values),
                               max_prefix_ratio=max(prefixes) if prefixes else 1.0)
    return dict(key=job["key"], scenario=job["scenario"], kind=job["kind"],
                ratio=job.get("ratio"), model=spec["model"], pattern=spec["pattern"],
                source=job["source"], source_sha256=job["source_sha256"],
                repetitions=spec["reps"], rows=rows, input_hashes=hashes,
                checks=checks, summary=summary, elapsed_s=time.monotonic() - started)


def interval(values):
    values = sorted(values)
    def quantile(q):
        z = (len(values) - 1) * q
        lower, upper = math.floor(z), math.ceil(z)
        return values[lower] + (values[upper] - values[lower]) * (z - lower)
    return [quantile(.025), quantile(.975)]


def group_summary(cells):
    rng = random.Random(BOOTSTRAP_SEED)
    resamples = [[rng.randrange(10) for _ in range(10)] for _ in range(BOOTSTRAPS)]
    summaries = {}
    for policy in POLICIES:
        ratios = [c["summary"][policy]["ratio_agent"] for c in cells]
        versus = [c["summary"][policy]["ratio_pace"] for c in cells]
        draws, relative_draws = [], []
        if policy != "reactive":
            for indices in resamples:
                agent_ratios, pace_ratios = [], []
                for c in cells:
                    cost = sum(c["rows"][policy][i]["cost"] for i in indices)
                    agent = sum(c["rows"]["reactive"][i]["cost"] for i in indices)
                    pace = sum(c["rows"][MAIN][i]["cost"] for i in indices)
                    agent_ratios.append(cost / agent)
                    pace_ratios.append(cost / pace)
                draws.append(statistics.mean(agent_ratios))
                relative_draws.append(statistics.mean(pace_ratios))
        summaries[policy] = dict(
            cells=len(cells), mean_ratio_agent=statistics.mean(ratios),
            min_ratio_agent=min(ratios), max_ratio_agent=max(ratios),
            cheaper_than_agent_cells=sum(r < 1 - 1e-9 for r in ratios),
            mean_ratio_pace=statistics.mean(versus),
            min_ratio_pace=min(versus), max_ratio_pace=max(versus),
            cheaper_than_pace_cells=sum(r < 1 - 1e-9 for r in versus),
            tied_with_pace_cells=sum(abs(r - 1) <= 1e-9 for r in versus),
            more_costly_than_pace_cells=sum(r > 1 + 1e-9 for r in versus),
            max_prefix_ratio=max(c["summary"][policy]["max_prefix_ratio"] for c in cells),
            mean_attempts=statistics.mean(c["summary"][policy]["mean_attempts"] for c in cells),
            mean_ratio_agent_ci95=interval(draws) if draws else [1.0, 1.0],
            mean_ratio_pace_ci95=interval(relative_draws) if relative_draws else None)
    return summaries


def summarize(config):
    cells = [json.loads(destination(job).read_text()) for job in config["jobs"]]
    groups = {scenario: group_summary([c for c in cells if c["scenario"] == scenario])
              for scenario in SCENARIOS}
    models = {model: {scenario: group_summary([c for c in cells if c["model"] == model
                                              and c["scenario"] == scenario])
                      for scenario in SCENARIOS}
              for model in sorted({c["model"] for c in cells})}
    condition_rows = []
    for c in cells:
        path = destination(c)
        condition_rows.append({key: c[key] for key in (
            "key", "scenario", "kind", "ratio", "model", "pattern", "repetitions", "summary")}
            | dict(source=str(path.relative_to(ROOT)), source_sha256=sha(path),
                   original_source=c["source"], original_source_sha256=c["source_sha256"]))
    prefix_records = sum(c["repetitions"] * 2 for c in cells)
    prefix_arrivals = sum(sum(h["arrivals"] for h in c["input_hashes"]) * 2 for c in cells)
    result = dict(schema=config["schema"], config_sha256=sha(OUT / "config.json"),
                  completed=len(cells), expected=config["expected_conditions"],
                  main_policy=MAIN, policies=list(POLICIES), groups=groups,
                  by_model=models, cells=condition_rows, observations=config["observations"],
                  checks=dict(all_source_hashes_match=True, all_pairing_hashes_match=True,
                              all_components_reconcile=True, all_reference_accounts_match=True,
                              retained_reference_checks=48 * 10 + 12 * 10,
                              every_prefix_checked=True, protected_repetitions=prefix_records,
                              protected_arrival_prefixes=prefix_arrivals,
                              max_prefix_ratio=max(c["summary"][p]["max_prefix_ratio"]
                                                   for c in cells for p in (MAIN, EQUAL))),
                  definitions=config["definitions"], branch_counts=config["branch_counts"])
    assert result["completed"] == result["expected"] == 72
    (OUT / "evidence.json").write_text(encoded(result))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    config = freeze() if args.freeze else json.loads((OUT / "config.json").read_text())
    if args.freeze:
        print("Frozen", len(config["jobs"]), "conditions", sha(OUT / "config.json"), flush=True)
    if args.run:
        for name, expected in config["source_sha256"].items():
            assert sha(ROOT / name) == expected, name
        pending_jobs = [job for job in config["jobs"] if not destination(job).exists()]
        print("Pending", len(pending_jobs), "of", len(config["jobs"]), flush=True)
        completed = len(config["jobs"]) - len(pending_jobs)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending = {pool.submit(cell, job): job for job in pending_jobs}
            for future in as_completed(pending):
                job = pending[future]
                result = future.result()
                path = destination(job)
                if path.exists():
                    raise RuntimeError("Concurrent result write: " + str(path))
                temporary = path.with_suffix(".tmp")
                temporary.write_text(encoded(result))
                temporary.replace(path)
                completed += 1
                print("Completed", completed, "of", len(config["jobs"]), job["key"], flush=True)
        result = summarize(config)
        print("Summary", result["completed"], "conditions", flush=True)
        print(json.dumps(result["groups"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
