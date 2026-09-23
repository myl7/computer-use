"""Same frozen adapters, with group-aware result paths for the two grids.

The first attempt used the inherited scenario key alone. Both grids reuse
those keys, so its no-overwrite check stopped evaluation at the first path
collision. The failed manifest and partial files remain unchanged. This
version changes only storage identifiers, not any scientific definition.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import statistics
import time


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2"
EVIDENCE = ROOT / "analysis/safe_projected_paper_20260922/evaluation-evidence.json"
MAIN = "safe_projected_025"
GROUPS = ("original_grid", "fresh_grid", "real_arrivals")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load_module("pace_baseline_engine", Path(__file__).with_name("engine.py"))
original = load_module("pace_original_study", ROOT / "code/t2sim/protocol_explore/study.py")


def dump(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def destination(job):
    storage_key = job["group"] + "/" + job["key"]
    return OUT / "cells" / (hashlib.sha256(storage_key.encode()).hexdigest()[:20] + ".json")


def freeze():
    evidence = json.loads(EVIDENCE.read_text())
    jobs = []
    for group in GROUPS:
        for saved in evidence["cells"][group]:
            source = ROOT / saved["source"]
            assert sha(source) == saved["source_sha256"]
            raw = json.loads(source.read_text())
            assert len(raw["rows"][MAIN]) == raw["spec"]["reps"]
            assert raw["spec"]["silent"] == 0
            jobs.append(dict(key=raw["key"], group=group, source=saved["source"],
                             source_sha256=saved["source_sha256"], spec=raw["spec"]))
    assert len(jobs) == 612
    assert len({str(destination(job)) for job in jobs}) == len(jobs)
    files = [Path(__file__), Path(engine.__file__), Path(original.__file__),
             Path(original.reference.__file__), Path(original.streams.__file__),
             Path(original.engine.__file__),
             Path(original.profiles_reader.__file__),
             Path(__file__).with_name("test_engine.py"),
             Path(__file__).with_name("test_study_v2.py"),
             Path(__file__).with_name("README.md"), EVIDENCE,
             Path(__file__).with_name("README_v2.md"),
             ROOT / ".firecrawl/rewrite-20260922/autorpa-current.md",
             ROOT / ".firecrawl/rewrite-20260922/toolpro-current.md"]
    config = dict(
        schema="pace-literature-adaptations/2", jobs=jobs,
        policies=list(engine.POLICIES), main_policy=MAIN,
        source_sha256={str(p.relative_to(ROOT)): sha(p) for p in files},
        definitions={
            "autorpa_once": "One bundled build after three completed agent runs. Failed build or drift permanently returns the family to agent service. No compilation retry.",
            "toolpro_cost": "Compile if one-use saving c-d-[h+(1-h)q]c exceeds C+(1/p_hat-1)C_fail, p_hat=(admits+1)/(attempts+2). No future recurrence forecast or budget.",
            "pace_narrow_price": "Change only the proposal price to C/p_hat. Preserve all actual bills, C/C_fail maximum reservation, .25 budget, projection, routing, and service actions.",
            "shared_environment": "Same measured compiler, completed-trace eligibility, service-before-compilation, artifact reuse, detected-failure fallback, TTL, known costs and hazard, and family-arrival/attempt-indexed outcome channels as retained PACE records.",
            "scope": "Paper-inspired decision adaptations under a shared token model, not end-to-end replications of AutoRPA or ToolPro. No paid calls.",
            "selection": "All 600 existing main-grid conditions and all 12 existing recorded-arrival conditions. No outcome-based exclusion.",
            "pairing": "Each regenerated stream, mapping, profile hash and original scenario seed is checked against the saved source cell before simulation.",
            "reporting": "Equal-weight means of condition-wise policy/agent ratios. Preserve every cell, including losses to the named baselines.",
            "infrastructure_revision": "Use group+key to avoid inherited duplicate grid keys. Original interrupted manifest and partial cells remain in parent directory. No policy, parameter, or outcome definition changed.",
        },
    )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "cells").mkdir(exist_ok=True)
    manifest = OUT / "config.json"
    encoded = dump(config)
    if manifest.exists() and manifest.read_text() != encoded:
        raise SystemExit("Frozen adapter study differs; preserve it and use another output version.")
    manifest.write_text(encoded)
    return config


def prepare(spec, group, rep):
    base = dict(spec)
    base["suite"] = base["suite"].removeprefix("paired_")
    if base["suite"] == "fresh_grid":
        base["suite"] = "validation_grid"
    arrivals, mapping, profiles = original.prepare(base, rep)
    if group == "fresh_grid":
        rng = random.Random(930007 + rep + int(hashlib.sha256(spec["key"].encode()).hexdigest()[:8], 16))
        for row in profiles.values():
            if spec["admission"] == "all_good":
                row["p"] = .95
            elif spec["admission"] == "half":
                row["p"] = .35
            elif spec["admission"] == "mixture":
                row["p"] = rng.choice((0., .05, .3, .7, 1.))
    return arrivals, mapping, profiles


def cell(job):
    started = time.monotonic()
    source = ROOT / job["source"]
    assert sha(source) == job["source_sha256"]
    saved = json.loads(source.read_text())
    spec = job["spec"]
    rows = {p: [] for p in engine.POLICIES + (MAIN,)}
    hashes = []
    for rep in range(spec["reps"]):
        arrivals, mapping, profiles = prepare(spec, job["group"], rep)
        fingerprint = dict(stream=original.fingerprint(arrivals),
                           mapping=original.fingerprint(mapping),
                           profiles=original.fingerprint(profiles))
        assert fingerprint == saved["input_hashes"][rep], (job["key"], rep)
        hashes.append(fingerprint)
        opts = {k: spec[k] for k in ("ttl", "k_min", "h", "m", "tau0", "silent", "penalty", "fallback_mult")}
        agent_cost = saved["rows"]["reactive"][rep]["cost"]
        full = original.compact(engine.full_run(arrivals, profiles, mapping, MAIN,
                                               seed=spec["seed"] * 1000 + rep, **opts))
        assert full == saved["rows"][MAIN][rep], (job["key"], rep, "full PACE parity")
        rows[MAIN].append(full)
        for policy in engine.POLICIES:
            result = engine.run(arrivals, profiles, mapping, policy,
                                seed=spec["seed"] * 1000 + rep, **opts)
            assert math.isclose(result["baseline_cost"], agent_cost, rel_tol=1e-10, abs_tol=1e-6)
            rows[policy].append(original.compact(result))
    agent_mean = statistics.mean(r["cost"] for r in saved["rows"]["reactive"])
    main_mean = statistics.mean(r["cost"] for r in saved["rows"][MAIN])
    summary = {}
    for policy, values in rows.items():
        mean_cost = statistics.mean(r["cost"] for r in values)
        summary[policy] = dict(mean_cost=mean_cost, ratio_agent=mean_cost / agent_mean,
                               ratio_pace=mean_cost / main_mean,
                               mean_quality=statistics.mean(r["quality"] for r in values),
                               mean_attempts=statistics.mean(r["attempts"] for r in values),
                               max_prefix_ratio=max(r["prefix_ratio"] for r in values))
    return dict(key=job["key"], group=job["group"], spec=spec,
                source=job["source"], source_sha256=job["source_sha256"],
                rows=rows, summary=summary, input_hashes=hashes,
                elapsed_s=time.monotonic() - started)


def interval(values):
    values = sorted(values)
    def percentile(q):
        z = q * (len(values) - 1)
        lo, hi = int(z), min(int(z) + 1, len(values) - 1)
        return values[lo] * (hi - z) + values[hi] * (z - lo) if lo != hi else values[lo]
    return [percentile(.025), percentile(.975)]


def summarize(config):
    cells = [json.loads(destination(job).read_text()) for job in config["jobs"]]
    groups = {}
    for group in GROUPS:
        values = [c for c in cells if c["group"] == group]
        groups[group] = {}
        for policy in engine.POLICIES:
            ratios = [c["summary"][policy]["ratio_agent"] for c in values]
            versus_pace = [c["summary"][policy]["ratio_pace"] for c in values]
            groups[group][policy] = dict(
                cells=len(values), mean_ratio_agent=statistics.mean(ratios),
                max_cell_ratio_agent=max(ratios),
                mean_ratio_to_pace=statistics.mean(versus_pace),
                max_ratio_to_pace=max(versus_pace), min_ratio_to_pace=min(versus_pace),
                cheaper_than_pace_cells=sum(x < 1 - 1e-9 for x in versus_pace),
                tied_with_pace_cells=sum(abs(x - 1) <= 1e-9 for x in versus_pace),
                more_costly_than_pace_cells=sum(x > 1 + 1e-9 for x in versus_pace),
                at_least_twice_pace_cells=sum(x >= 2 for x in versus_pace),
                max_prefix_ratio=max(c["summary"][policy]["max_prefix_ratio"] for c in values),
                mean_attempts_per_cell=statistics.mean(c["summary"][policy]["mean_attempts"] for c in values),
            )
    real = [c for c in cells if c["group"] == "real_arrivals"]
    rng = random.Random(20260922017)
    resamples = [[rng.randrange(10) for _ in range(10)] for _ in range(2000)]
    for policy in engine.POLICIES:
        ratio_draws, main_draws = [], []
        sources = {c["key"]: json.loads((ROOT / c["source"]).read_text()) for c in real}
        for indices in resamples:
            ratios, main_ratios = [], []
            for c in real:
                raw = sources[c["key"]]
                cost = sum(c["rows"][policy][i]["cost"] for i in indices)
                ratios.append(cost / sum(raw["rows"]["reactive"][i]["cost"] for i in indices))
                main_ratios.append(cost / sum(raw["rows"][MAIN][i]["cost"] for i in indices))
            ratio_draws.append(statistics.mean(ratios))
            main_draws.append(statistics.mean(main_ratios))
        groups["real_arrivals"][policy]["mean_ratio_agent_ci95"] = interval(ratio_draws)
        groups["real_arrivals"][policy]["mean_ratio_to_pace_ci95"] = interval(main_draws)
    result = dict(completed=len(cells), expected=len(config["jobs"]), groups=groups,
                  policies=list(engine.POLICIES),
                  cells=[dict(key=c["key"], group=c["group"], model=c["spec"]["model"],
                              pattern=c["spec"]["pattern"], summary=c["summary"])
                         for c in cells])
    (OUT / "summary.json").write_text(dump(result))
    evidence = dict(schema="pace-literature-additions/1", main_policy=MAIN,
                    groups=groups, policies=list(engine.POLICIES), cells={g: [] for g in GROUPS})
    for c in cells:
        raw_path = destination(c)
        evidence["cells"][c["group"]].append(dict(
            key=c["key"], model=c["spec"]["model"], pattern=c["spec"]["pattern"],
            source=str(raw_path.relative_to(ROOT)), source_sha256=sha(raw_path),
            original_source=c["source"], original_source_sha256=c["source_sha256"],
            repetitions=c["spec"]["reps"], policies=c["summary"],
        ))
    (OUT / "evidence.json").write_text(dump(evidence))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    config = freeze() if args.freeze else json.loads((OUT / "config.json").read_text())
    if args.freeze:
        print("Frozen", len(config["jobs"]), "paired conditions", flush=True)
    if args.run:
        for path, expected in config["source_sha256"].items():
            assert sha(ROOT / path) == expected, path
        pending_jobs = [job for job in config["jobs"] if not destination(job).exists()]
        print("Pending", len(pending_jobs), flush=True)
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
                temporary.write_text(dump(result))
                temporary.replace(path)
                completed += 1
                if completed % 25 == 0 or job["group"] == "real_arrivals":
                    print("Completed", completed, "of", len(config["jobs"]),
                          job["group"], job["key"], flush=True)
        result = summarize(config)
        print("Summary", result["completed"], "of", result["expected"], flush=True)
        print(json.dumps(result["groups"], sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
