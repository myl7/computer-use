"""Freeze and run the three-model paper study using revision_sim unchanged.

This is an offline scenario study. It makes no model, device, or API calls.
Run --freeze once, then --run to execute or resume the exact frozen study.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import time

import revision_sim as engine
import stats
import streams

ROOT = Path(__file__).resolve().parents[2]
MEASUREMENT = ROOT / "paper/measurement_update_20260918.json"
CONSTANTS = Path(__file__).with_name("constants.measured.v2.json")
DEFAULT_OUT = ROOT / "experimental-results/guiexp/t2_sim_v3/paper_revision_20260922"
GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"
QWEN = "qwen/qwen3.8-flash"
MODELS = (GLM, DS, QWEN)
MODES = {"binary_replay": (1.0, 0.0), "soft": (0.8, 0.2),
         "uniform": (0.5, 0.5)}
STREAM_NAMES = ("poisson", "zipf", "bursty", "sepsis", "bpi2019",
                "wiki_A", "wiki_B")
SENSITIVITIES = (("ttl25", {"ttl": 25}), ("ttl400", {"ttl": 400}),
                 ("no_router", {"m": 0.0, "tau0": 0.0}),
                 ("no_drift", {"h": 0.0}), ("high_drift", {"h": 0.1}),
                 ("silent30", {"silent": 0.3}), ("price5x", {"scale": 5.0}))
COMPONENTS = ("reactive_tokens", "extraction_tokens", "compile_tokens",
              "router_tokens")
PARAMETERS = ("ttl", "h", "m", "tau0", "silent", "penalty", "fallback_mult",
              "k_min")
BOOTSTRAPS = 2000
POLICY_DEFINITIONS = {
    "reactive": "Serve every arrival with the agent. Pay the same empty-manifest router fee.",
    "earliest": "After three completed reactive attempts, compile at the first eligible arrival and after each later eligible failure or break.",
    "earliest_cap": "Use earliest eligibility and reserve the next failed-build cost under the shared spending cap.",
    "fixed10_cap": "After three completed reactive attempts and ten observed family arrivals, compile subject to the cap.",
    "success10_cap": "After three completed reactive attempts and ten successful reactive attempts since admission, compile subject to the cap.",
    "breakeven_cap": "Compile when accumulated positive expected per-use savings cover estimated successful admission cost, subject to the cap.",
    "projected": "Compile when estimated future-use savings exceed estimated admission cost plus the idle-residency router allowance.",
    "projected_cap": "Use projected eligibility and reserve the next failed-build cost under the shared spending cap.",
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def merged_rows(table):
    """Accept the default split table or a table that already includes Qwen."""
    unique = {}
    for section in ("rows", "qwen_rows"):
        for row in table.get(section, []):
            key = (row["model"], row["platform"], row["family"])
            if key in unique and unique[key] != row:
                raise ValueError(f"Conflicting duplicate measurement: {key}")
            unique[key] = row
    return [unique[key] for key in sorted(unique)]


def observed_failed_cost(row):
    return row.get("measured", {}).get("C_with_repair", row["C"])


def profiles_from_measurement(table, model, mode, scale=1.0, cf_multiplier=1.0):
    """Keep observed sides and explicitly impute unobserved counterfactuals."""
    rows = [r for r in merged_rows(table) if r["model"] == model]
    admitted = [r for r in rows if r["admitted"]]
    complete_failed = [r for r in rows if not r["admitted"]
                       and not r.get("C_partial", False)]
    if not admitted:
        raise ValueError(f"No admitted profiles for {model}")
    medians = {key: statistics.median(r[key] for r in admitted)
               for key in ("C", "d", "q")}
    fail_median = (statistics.median(observed_failed_cost(r) for r in complete_failed)
                   if complete_failed else None)
    profiles = {}
    for row in rows:
        name = f'{row["platform"]}/{row["family"]}'
        ok = row["admitted"]
        origins = {"c": "observed floor-adjusted cost per exploration attempt",
                   "pi": "observed agent_successes / n_exploration_episodes",
                   "p": f"assumed {mode} probability by observed admission status"}
        imputed = []
        values = {}
        for key in ("C", "d", "q"):
            if ok:
                values[key] = row[key]
                origins[key] = "observed admitted-cell value"
            else:
                values[key] = medians[key]
                origins[key] = "imputed median over this model's admitted cells"
                imputed.append(key)
        if not ok:
            cf = observed_failed_cost(row)
            origins["C_fail"] = ("observed partial/censored spend for this terminated cell"
                                 if row.get("C_partial", False)
                                 else "observed complete failed-build spend for this cell")
        elif fail_median is not None:
            cf = fail_median
            origins["C_fail"] = "imputed median over this model's complete failed builds, excluding partial/censored builds"
            imputed.append("C_fail")
        else:
            cf = values["C"]
            origins["C_fail"] = "assumed equal to this cell's admitted cost because no complete failed build was observed"
            imputed.append("C_fail")
        n = row["n_exploration_episodes"]
        profile = dict(c=row["c"], d=values["d"], q=values["q"],
                       pi=row["agent_successes"] / n,
                       p=MODES[mode][0 if ok else 1], C=scale * values["C"],
                       C_fail=scale * cf_multiplier * cf,
                       observed_admitted=ok, imputed=imputed, origins=origins,
                       c_unsubtracted=row["c"] + row["floor"],
                       source=row.get("source"), final_gate=row.get("final_gate"),
                       cost_counts_limitation=row.get("cost_counts_limitation"),
                       C_fail_partial=bool(row.get("C_partial", False)),
                       admission_evidence=("terminated before a gate result"
                                           if row.get("C_partial", False)
                                           else "completed admission result"),
                       scale=scale, cf_multiplier=cf_multiplier)
        for key in ("c", "d", "C", "C_fail", "p", "q", "pi"):
            if not math.isfinite(profile[key]) or profile[key] < 0:
                raise ValueError(f"Invalid {key}: {model}/{name}")
        for key in ("p", "q", "pi"):
            if profile[key] > 1:
                raise ValueError(f"Invalid probability {key}: {model}/{name}")
        profiles[name] = profile
    return profiles


def make_config():
    table = json.loads(MEASUREMENT.read_text())
    old = json.loads(CONSTANTS.read_text())
    jobs = []

    def add(model, mode, stream, tag="base", changes=None, group="core"):
        spec = dict(model=model, admission_mode=mode, stream=stream, tag=tag,
                    group=group, reps=20, seed=20260913, n=300, ttl=100,
                    h=0.02, m=91.0, tau0=368.0, silent=0.0, penalty=3.0,
                    fallback_mult=1.0, scale=1.0, cf_multiplier=1.0, k_min=3,
                    policies=list(engine.POLICIES))
        spec.update(changes or {})
        spec["profiles"] = profiles_from_measurement(
            table, model, mode, spec["scale"], spec["cf_multiplier"])
        if stream in old["streams"]:
            spec["stream_spec"] = old["streams"][stream]
        spec["key"] = f"{model}/{mode}/{stream}/{tag}"
        jobs.append(spec)

    for model in MODELS:
        for mode in MODES:
            for stream in STREAM_NAMES:
                add(model, mode, stream)
        for tag, changes in SENSITIVITIES:
            for stream in ("bursty", "bpi2019"):
                add(model, "soft", stream, tag, changes)
    for tag, mult in (("failure_cost_half", 0.5), ("failure_cost_5x", 5.0)):
        for stream in ("bursty", "bpi2019"):
            add(GLM, "soft", stream, tag, {"cf_multiplier": mult}, "additional_cf")
    files = [Path(__file__), Path(engine.__file__), Path(streams.__file__),
             Path(stats.__file__), MEASUREMENT, CONSTANTS,
             Path(__file__).with_name("tests") / "test_paper_revision.py",
             Path(__file__).with_name("tests") / "test_revision_sim.py",
             streams.OLD_REAL_STREAMS]
    files += [ROOT / old["streams"][name]["path"] for name in ("wiki_A", "wiki_B")]
    definitions = dict(POLICY_DEFINITIONS)
    definitions["shared_protocol"] = (
        "Serve first, then decide. The compiled program is first usable on a later arrival. "
        "Three completed reactive attempts, including fallbacks, are required. "
        "All policies use the same fixed idle TTL and free archived-artifact relisting. "
        "All decisions use observations through the current arrival and supplied scenario costs/hazard.")
    return dict(
        schema="gui-paper-revision-sim/1", study_date="2026-09-22", jobs=jobs,
        reps=20, seed=20260913, expected_cells=109, core_cells=105,
        base_cells=63, additional_failure_cost_cells=4, bootstrap_samples=BOOTSTRAPS,
        source_sha256={str(p.relative_to(ROOT)): digest(p) for p in files},
        measurement_definitions=table["definition"],
        qwen_disclosures=table["qwen_disclosures"], policy_definitions=definitions,
        metric_definitions={
            "token_cost": "Sum of reactive_tokens, extraction_tokens, compile_tokens, router_tokens, in price-weighted input-token units.",
            "engine.tokens": "Original engine field, which includes harm_tokens. Preserved unchanged inside each row's engine object.",
            "auxiliary_penalty": "Engine harm_tokens: penalty * c per silent failure. This is not model token expenditure.",
            "penalized_objective": "token_cost + auxiliary_penalty, an explicitly assumed loss scale.",
            "quality": "Engine successes / stream arrivals, including unsuccessful reactive attempts and silent failures.",
            "ratio": "Ratio of arithmetic mean token costs across the 20 paired repetitions, not the mean of repetition ratios.",
            "interval": "95% paired percentile bootstrap with 2000 resamples of repetition indices. All policy outcomes stay paired. Best-comparator intervals reselect the lowest mean comparator within every resample.",
            "aggregate": "Each stream cell receives equal weight when averaging normalized cell ratios. Replicate resampling is shared across all cells in an aggregate. Absolute price-weighted token units are never pooled across models.",
        },
        comparator_definitions={
            "best_fixed": ["reactive", "earliest"],
            "best_other_rule": [p for p in engine.POLICIES if p != "projected_cap"],
            "selection": "Retrospective minimum of 20-repetition mean costs in each cell. This is a descriptive comparison, not an executable policy or an offline optimal schedule.",
        },
        assumptions=[
            "The measured cost profiles and scenario hazard are supplied. Only arrival and admission rates are estimated online.",
            "Admission scenarios are assumptions, not independent-build probability estimates. binary_replay repeats observed admission status, including no-admission for the terminated Qwen Calc cell.",
            "Unadmitted profiles receive admitted-model-median C/d/q counterfactuals, even when an upstream row already contains a fill.",
            "Observed failed costs are retained. Missing failed costs use the model median over complete failed builds only. Qwen Calc partial spend is excluded from this median.",
            "GLM has no complete failed build. Its base C_fail equals its own C, with predeclared 0.5C and 5C sensitivities on soft/bursty and soft/bpi2019.",
            "Qwen Calc uses observed partial/censored spend as its own failure charge. The full failed-build cost is unknown. Qwen Web is excluded because refusal left no retained cost profile.",
            "Exploration success probability is successes divided by completed exploration attempts, not by three task bindings.",
            "Family IDs and archived-artifact lookup are supplied without error. Router fees are scenario inputs.",
            "Real logs supply only an arrival order and family recurrence. GUI costs are shuffled onto their IDs each repetition and paired across policies.",
            "Synthetic poisson is the inherited uniform family-label sampler at fixed length, not a model of wall-clock interarrival times.",
            "Compile coins use seed/family/attempt. Service coins use seed/family/arrival/channel. This preserves common random numbers across policies.",
            "Bootstrap variation covers synthetic arrivals, real-stream cost mappings, and engine outcomes. It does not include uncertainty in measured costs or new real-stream samples.",
            "The spending cap reserves the next failed-attempt cost. The full online rule has no claimed competitive ratio.",
            "The 63 base, 42 standard sensitivity, and four GLM failed-cost cells were fixed before running this study. Negative results are retained.",
        ])


def freeze(out):
    config = make_config()
    if len(config["jobs"]) != config["expected_cells"]:
        raise ValueError("Unexpected study size")
    text = encoded(config)
    path = out / "config.json"
    if path.exists():
        if path.read_text() != text:
            raise SystemExit("Refusing to replace a different frozen configuration.")
        return config
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Freeze requires an empty output directory.")
    out.mkdir(parents=True, exist_ok=True)
    for relative in config["source_sha256"]:
        source = ROOT / relative
        if source.suffix == ".py":
            dest = out / "source_snapshot" / relative
        elif source in (MEASUREMENT, CONSTANTS):
            dest = out / "input_snapshot" / relative
        else:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    (out / "profile_mapping.json").write_text(encoded({
        model: profiles_from_measurement(json.loads(MEASUREMENT.read_text()), model, "soft")
        for model in MODELS}))
    path.write_text(text)
    return config


def prepare_rep(spec, rep):
    names = sorted(spec["profiles"])
    if "stream_spec" in spec:
        arrivals = streams.load_stream(spec["stream_spec"])
        templates = list(names)
        random.Random(spec["seed"] + rep).shuffle(templates)
        mapping = {family: templates[i % len(templates)]
                   for i, family in enumerate(sorted(set(arrivals)))}
    else:
        arrivals = streams.gen_stream(spec["stream"], spec["n"], names,
                                     random.Random(spec["seed"] * 1000 + rep))
        mapping = {family: family for family in names}
    return arrivals, mapping


def account_result(raw):
    token_cost = sum(raw[key] for key in COMPONENTS)
    if not math.isclose(raw["tokens"], token_cost + raw["harm_tokens"],
                        rel_tol=1e-12, abs_tol=1e-7):
        raise ValueError("Engine cost components do not reconcile")
    return dict(engine=raw, token_cost=token_cost,
                auxiliary_penalty=raw["harm_tokens"],
                penalized_objective=raw["tokens"])


def paired_best_comparison(rows, candidates, seed):
    values = {p: [r["token_cost"] for r in rows[p]]
              for p in ["projected_cap", *candidates]}
    best = min(candidates, key=lambda p: stats.mean(values[p]))
    ours = values["projected_cap"]
    point = stats.mean(ours) / stats.mean(values[best])
    rng = random.Random(seed)
    ratios = []
    n = len(ours)
    for _ in range(BOOTSTRAPS):
        ids = [rng.randrange(n) for _ in range(n)]
        denominator = min(sum(values[p][i] for i in ids) for p in candidates)
        ratios.append(sum(ours[i] for i in ids) / denominator)
    ratios.sort()
    return dict(selected=best, candidates=list(candidates), ratio=point,
                savings_fraction=1 - point,
                ratio_ci95=[stats._percentile(ratios, 0.025),
                            stats._percentile(ratios, 0.975)])


def summarize(rows, seed):
    baseline = [r["token_cost"] for r in rows["reactive"]]
    summaries = {}
    for policy, runs in rows.items():
        costs = [r["token_cost"] for r in runs]
        quality = [r["engine"]["success_rate"] for r in runs]
        delta = [q - b["engine"]["success_rate"]
                 for q, b in zip(quality, rows["reactive"])]
        ratio = stats.mean(costs) / stats.mean(baseline)
        summaries[policy] = dict(
            mean_token_cost=stats.mean(costs), token_ratio_to_reactive=ratio,
            savings_fraction=1 - ratio,
            token_ratio_ci95=stats.bootstrap_ratio_ci(costs, baseline, B=BOOTSTRAPS, seed=seed),
            success_rate=stats.mean(quality), quality_delta=stats.mean(delta),
            quality_delta_ci95=stats.bootstrap_mean_ci(delta, B=BOOTSTRAPS, seed=seed),
            mean_auxiliary_penalty=stats.mean([r["auxiliary_penalty"] for r in runs]),
            mean_penalized_objective=stats.mean([r["penalized_objective"] for r in runs]),
            penalized_objective_ratio_to_reactive=stats.mean([r["penalized_objective"] for r in runs]) / stats.mean(baseline),
            mean_components={k: stats.mean([r["engine"][k] for r in runs]) for k in COMPONENTS},
            mean_failed_compile_tokens=stats.mean([r["engine"]["failed_compile_tokens"] for r in runs]),
            mean_attempts=stats.mean([r["engine"]["attempts"] for r in runs]),
            mean_silent_failures=stats.mean([r["engine"]["silent_failures"] for r in runs]))
    comparisons = {
        "best_fixed": paired_best_comparison(rows, ["reactive", "earliest"], seed),
        "best_other_rule": paired_best_comparison(
            rows, [p for p in engine.POLICIES if p != "projected_cap"], seed)}
    return summaries, comparisons


def cell(spec):
    start = time.monotonic()
    rows = {p: [] for p in spec["policies"]}
    stream_hashes, mapping_hashes = [], []
    for rep in range(spec["reps"]):
        arrivals, mapping = prepare_rep(spec, rep)
        stream_hashes.append(fingerprint(arrivals))
        mapping_hashes.append(fingerprint(mapping))
        for policy in spec["policies"]:
            raw = engine.run(arrivals, spec["profiles"], mapping, policy,
                             seed=spec["seed"] * 1000 + rep,
                             **{key: spec[key] for key in PARAMETERS})
            rows[policy].append(account_result(raw))
    summary, comparisons = summarize(rows, spec["seed"])
    return dict(spec=spec, rows=rows, summary=summary, comparisons=comparisons,
                stream_summary=streams.stream_summary(arrivals),
                stream_sha256=stream_hashes, mapping_sha256=mapping_hashes,
                wall_s=time.monotonic() - start)


def cell_path(out, spec):
    return out / "cells" / (hashlib.sha256(spec["key"].encode()).hexdigest()[:20] + ".json")


def aggregate(cells, seed):
    """Average cell ratios, with paired repetition indices shared across cells."""
    rng = random.Random(seed)
    resamples = [[rng.randrange(20) for _ in range(20)] for _ in range(BOOTSTRAPS)]
    out = {"cells": len(cells), "weighting": "equal weight per stream cell", "policies": {}}
    for policy in engine.POLICIES:
        ratios = [c["summary"][policy]["token_ratio_to_reactive"] for c in cells]
        draws = []
        for ids in resamples:
            draws.append(stats.mean([
                sum(c["rows"][policy][i]["token_cost"] for i in ids) /
                sum(c["rows"]["reactive"][i]["token_cost"] for i in ids)
                for c in cells]))
        draws.sort()
        out["policies"][policy] = dict(
            mean_cell_token_ratio=stats.mean(ratios),
            mean_cell_token_ratio_ci95=[stats._percentile(draws, 0.025),
                                      stats._percentile(draws, 0.975)])
    return out


def report(out, config):
    cells = [json.loads(cell_path(out, s).read_text()) for s in config["jobs"]]
    base = [c for c in cells if c["spec"]["tag"] == "base"]
    entries = []
    for c in cells:
        s = c["spec"]
        entries.append(dict(key=s["key"], model=s["model"], stream=s["stream"],
                            admission_mode=s["admission_mode"], tag=s["tag"],
                            group=s["group"], file=str(cell_path(out, s).relative_to(out)),
                            stream_summary=c["stream_summary"], summary=c["summary"],
                            comparisons=c["comparisons"]))
    aggregates = {}
    for model in MODELS:
        for mode in MODES:
            selected = [c for c in base if c["spec"]["model"] == model
                        and c["spec"]["admission_mode"] == mode]
            aggregates[f"{model}/{mode}"] = aggregate(selected, config["seed"])
    headline = dict(
        completed_cells=len(cells), base_cells=len(base),
        base_projected_cap_ratio_range=[min(c["summary"]["projected_cap"]["token_ratio_to_reactive"] for c in base),
                                        max(c["summary"]["projected_cap"]["token_ratio_to_reactive"] for c in base)],
        base_quality_delta_range=[min(c["summary"]["projected_cap"]["quality_delta"] for c in base),
                                  max(c["summary"]["projected_cap"]["quality_delta"] for c in base)],
        comparisons={})
    for name in ("best_fixed", "best_other_rule"):
        cs = [c["comparisons"][name] for c in base]
        headline["comparisons"][name] = dict(
            lower_point_estimate=sum(c["ratio"] < 1 - 1e-12 for c in cs),
            equal_point_estimate=sum(abs(c["ratio"] - 1) <= 1e-12 for c in cs),
            higher_point_estimate=sum(c["ratio"] > 1 + 1e-12 for c in cs),
            ci_entirely_below_one=sum(c["ratio_ci95"][1] < 1 for c in cs),
            ci_entirely_above_one=sum(c["ratio_ci95"][0] > 1 for c in cs),
            ratio_range=[min(c["ratio"] for c in cs), max(c["ratio"] for c in cs)])
    result = dict(schema=config["schema"], headline=headline,
                  metric_definitions=config["metric_definitions"],
                  comparator_definitions=config["comparator_definitions"],
                  aggregates=aggregates, cells=entries)
    (out / "summary.json").write_text(encoded(result))
    write_report(out, config, result)
    return result


def write_report(out, config, result):
    lines = ["# Frozen three-model revision simulation", "",
             f"Completed {result['headline']['completed_cells']} cells with 20 paired repetitions and eight policies per cell.",
             "", "All token ratios use pure model token expenditure. The engine's silent-failure penalty is reported separately.",
             "", "## Frozen design", ""]
    lines += ["- " + a for a in config["assumptions"]]
    lines += ["", "## Policy definitions", ""]
    lines += [f"- `{p}`: {d}" for p, d in config["policy_definitions"].items()]
    lines += ["", "## Reporting conventions", ""]
    lines += ["- " + text for text in config["metric_definitions"].values()]
    lines += ["- Best fixed compares `reactive` and `earliest`. Best other rule compares all seven other policies. Both select the lowest mean cost after the study and are descriptive references.",
              "- Confidence intervals are descriptive and are not adjusted for multiple comparisons.",
              "", "## Base cells", "",
              "`PC` means projected_cap. Ratios below one favor PC. Quality deltas are percentage points relative to reactive.", "",
              "| Model | Admission | Stream | PC / reactive [95% CI] | PC / best fixed [95% CI] | Best fixed | PC / best other [95% CI] | Best other | Quality delta [95% CI] |",
              "|---|---|---|---:|---:|---|---:|---|---:|"]
    aliases = {GLM: "GLM", DS: "DeepSeek", QWEN: "Qwen"}

    def ci(point, interval, scale=1):
        return f"{point * scale:.4f} [{interval[0] * scale:.4f}, {interval[1] * scale:.4f}]"

    for row in result["cells"]:
        if row["tag"] != "base":
            continue
        s = row["summary"]["projected_cap"]
        a, b = (row["comparisons"][k] for k in ("best_fixed", "best_other_rule"))
        lines.append(f"| {aliases[row['model']]} | {row['admission_mode']} | {row['stream']} | "
                     f"{ci(s['token_ratio_to_reactive'], s['token_ratio_ci95'])} | {ci(a['ratio'], a['ratio_ci95'])} | "
                     f"{a['selected']} | {ci(b['ratio'], b['ratio_ci95'])} | {b['selected']} | "
                     f"{ci(s['quality_delta'], s['quality_delta_ci95'], 100)} |")
    lines += ["", "## Sensitivity cells", "",
              "Auxiliary penalty is a loss-scale value, not tokens. Quality deltas are percentage points.", "",
              "| Model | Stream | Change | PC / reactive [95% CI] | Quality delta [95% CI] | PC tokens | Auxiliary penalty | Penalized objective / reactive |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in result["cells"]:
        if row["tag"] == "base":
            continue
        s = row["summary"]["projected_cap"]
        lines.append(f"| {aliases[row['model']]} | {row['stream']} | {row['tag']} | "
                     f"{ci(s['token_ratio_to_reactive'], s['token_ratio_ci95'])} | "
                     f"{ci(s['quality_delta'], s['quality_delta_ci95'], 100)} | "
                     f"{s['mean_token_cost']:.1f} | {s['mean_auxiliary_penalty']:.1f} | "
                     f"{s['penalized_objective_ratio_to_reactive']:.4f} |")
    lines += ["", "## Reproduction", "", "```sh",
              "python3 -m unittest discover -s code/t2sim/tests -p 'test*revision*.py'",
              "python3 code/t2sim/run_paper_revision.py --freeze",
              "python3 code/t2sim/run_paper_revision.py --run --workers 6",
              "```", "", "`config.json` freezes all profiles, scenario parameters, source hashes, and baseline definitions. "
              "`profile_mapping.json` records every imputation. `cells/` retains all 20 repetitions, engine metrics, stream hashes, and mapping hashes. "
              "`summary.json` contains every policy and comparison. No prior result directory is changed.", ""]
    (out / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if not any((args.freeze, args.run, args.report)):
        parser.error("Choose --freeze, --run, or --report")
    out = args.out.resolve()
    if args.freeze:
        config = freeze(out)
        print(f"Frozen {len(config['jobs'])} cells: {out / 'config.json'}", flush=True)
    else:
        config = json.loads((out / "config.json").read_text())
    if args.run or args.report:
        for relative, expected in config["source_sha256"].items():
            if digest(ROOT / relative) != expected:
                raise SystemExit(f"Frozen source changed: {relative}")
    if args.run:
        (out / "cells").mkdir(exist_ok=True)
        pending = []
        for spec in config["jobs"]:
            dest = cell_path(out, spec)
            if dest.exists():
                prior = json.loads(dest.read_text())
                if prior["spec"] != spec or any(len(r) != spec["reps"] for r in prior["rows"].values()):
                    raise SystemExit(f"Existing cell does not match frozen spec: {dest}")
            else:
                pending.append((spec, dest))
        print(f"{len(pending)} pending cells of {len(config['jobs'])}. No API calls.", flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(cell, s): (s, dest) for s, dest in pending}
            for future in as_completed(futures):
                spec, dest = futures[future]
                result = future.result()
                temp = dest.with_suffix(".tmp")
                temp.write_text(encoded(result))
                temp.replace(dest)
                print(f"Completed {spec['key']} ({result['wall_s']:.1f}s)", flush=True)
        args.report = True
    if args.report:
        result = report(out, config)
        print(f"Complete: {result['headline']['completed_cells']} cells. {out / 'REPORT.md'}", flush=True)


if __name__ == "__main__":
    main()
