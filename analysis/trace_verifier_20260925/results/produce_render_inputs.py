"""Produce revised renderer inputs from selected evidence and simulation cells."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import random
import statistics
from pathlib import Path

from normalize_results import normalize, usage_pw
from validate_results import ROOT, check_record, expected_main, expected_repeats
from diagnostics_validation import validate_diagnostics

HERE = Path(__file__).resolve().parent
FINAL = HERE / "render-inputs"
PARTIAL = ROOT / "experimental-results/trace_verifier_20260925/render-producer-partial"
SELECTIONS = HERE / "evidence-selection.json"
MODELS = ["z-ai/glm-5.3-flash", "deepseek/deepseek-v4-flash-vision-exp", "qwen/qwen3.8-flash"]
FAMILIES = ["ContactsAddContact", "MarkorDeleteNote", "SimpleCalendarAddOneEvent",
            "OsmAndMarker", "CalcTableSave", "WriterMemoSave", "CommentPost"]
NAMES = dict(zip(FAMILIES, ["Contacts", "Markor delete", "Calendar", "OsmAnd marker",
                            "Calc table", "Writer memo", "Reddit comment"]))
B = 2000
PROTOCOL = "three-building-model-extracted-v1"
OLD_MEASUREMENT = ROOT / "paper/measurement_update_20260918.json"
PROFILES = HERE / "profiles.json"
MEASUREMENT_CLAIMS = HERE / "measurement-claim-deltas.json"
AGGREGATION = HERE / "aggregation.json"
COVERAGE = HERE / "coverage-report.json"
DIAGNOSTICS = HERE / "diagnostics-aggregation.json"
DIAGNOSTICS_MANIFEST = ROOT / "analysis/trace_verifier_20260925/runners/diagnostics/manifest.json"
SIMULATION_AUDIT = ROOT / "experimental-results/trace_verifier_20260925/simulations/final-audit.json"
SENSITIVITY_SCENARIOS = (
    "missing_price_r0.5", "missing_price_r1", "missing_price_r2", "missing_price_r5",
    "adverse_probability", "adverse_probability_recurrence",
)
STANDARD_GROUP_COUNTS = {"original_grid": 300, "fresh_grid": 300, "real_arrivals": 12}
EXPECTED_STUDY_CELLS = {"main": 616, "comparisons": 612, "ablations": 612, "sensitivity": 72}
PREFIX_RATIO_TOLERANCE = 1.26e-8


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(encoded)
    temporary.replace(path)


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def record_status(record: dict, tasks: list[dict] | None = None) -> str:
    if record.get("terminal_status") == "generation_output_budget_failure":
        return "Gen. fail"
    tasks = record.get("task_results", []) if tasks is None else tasks
    counts = Counter(row.get("status") for row in tasks)
    if counts["pass"] == 3:
        return "Pass 3/0/0"
    return f"Fail {counts['pass']}/{counts['fail']}/{counts['untested']}"


def initial_status(record: dict) -> str:
    history = record.get("candidate_history") or []
    if history and history[0].get("tasks"):
        return record_status(record, history[0]["tasks"])
    if record.get("repairs"):
        return "Fail 0/1/2"
    return record_status(record)


def deployment_costs(record: dict) -> tuple[list[float], list[int], list[str]]:
    deployment = record.get("deployment") or {}
    rows = deployment.get("records") or []
    if len(rows) != 30:
        raise ValueError(f"Expected 30 deployment records: {record['model']}/{record['family']}")
    costs, failures, goals = [], [], []
    for use in rows:
        extraction = use.get("extraction") or {}
        calls = extraction.get("calls") or use.get("calls_detail") or []
        costs.append(sum(usage_pw(call.get("usage", call), record["model"]) for call in calls))
        passed = use.get("status") == "pass" or use.get("success") is True
        failures.append(int(not passed))
        goals.append(use.get("goal"))
    metrics = record.get("profile_metrics") or {}
    if not math.isclose(statistics.mean(costs), metrics.get("d", -1), rel_tol=1e-12, abs_tol=1e-9):
        raise ValueError(f"Deployment frozen-pw mean mismatch: {record['model']}/{record['family']}")
    if sum(failures) != metrics.get("failures"):
        raise ValueError(f"Deployment failure mismatch: {record['model']}/{record['family']}")
    return costs, failures, goals


def selected_records() -> list[tuple[dict, dict, Path]]:
    rows = []
    for selection in json.loads(SELECTIONS.read_text())["selections"]:
        if selection.get("aggregation_state", "selected") != "selected":
            continue
        path = ROOT / selection["raw_path"]
        if sha(path) != selection["raw_sha256"]:
            raise ValueError("Stale selection: " + str(path))
        record = normalize(json.loads(path.read_text()))
        record.update(selection["canonical_identity"])
        errors, eligible = check_record(path, record)
        if errors:
            raise ValueError(f"Invalid selected evidence {path}: {errors}")
        rows.append((selection, record, path))
    return rows


def outcome_text(record: dict | None) -> str:
    if record is None:
        return "--"
    return record_status(record)


def measurement_data(records: list[tuple[dict, dict, Path]]) -> tuple[dict, dict]:
    initial = {(r["model"], r["family"]): r for _, r, _ in records
               if r["attempt_role"] == "initial" and r["attempt_id"] == "initial"
               and (r["platform"], r["model"], r["family"]) in expected_main()}
    repeats = defaultdict(list)
    for _, record, _ in records:
        if record["attempt_role"] == "repeat":
            repeats[(record["model"], record["family"])].append(record)
    old_measurement = json.loads(OLD_MEASUREMENT.read_text())
    old_rows = {(r["model"], r["family"]): r for r in old_measurement["rows"] + old_measurement["qwen_rows"]}
    published_paired = {(r["model"], r["family"]): r for r in
                        old_measurement["paired_replays"] + old_measurement["qwen_paired_replays"]}
    diagnostics = {(r["model"], r["family"]): r for r in
                   json.loads(MEASUREMENT_CLAIMS.read_text())["t20"]}
    paired_records = {}
    measurement_sources = {relative(OLD_MEASUREMENT): sha(OLD_MEASUREMENT),
                           relative(MEASUREMENT_CLAIMS): sha(MEASUREMENT_CLAIMS)}
    for model, family in published_paired:
        record = initial.get((model, family))
        if not record or not record.get("admission") or record.get("platform") != "android":
            continue
        slug = model.replace("/", "_")
        paths = sorted((ROOT / f"experimental-results/guiexp_android/t21_paired_replay/{slug}/{family}").glob("use_*/summary.json"))
        program, failures, goals = deployment_costs(record)
        if len(paths) != 30:
            raise ValueError(f"Matched replay/deployment coverage mismatch: {model}/{family}")
        agent, agent_success = [None] * 30, [None] * 30
        seen = set()
        deploy = record["deployment"]["records"]
        for path in paths:
            entry = json.loads(path.read_text())
            index = entry["use_index"]
            if index in seen or not 0 <= index < 30:
                raise ValueError(f"Invalid matched use index: {model}/{family}/{index}")
            seen.add(index)
            use = deploy[index]
            if entry["goal"] != goals[index] or entry.get("binding") != use.get("expected"):
                raise ValueError(f"Matched goal mismatch: {model}/{family}/{entry['use_index']}")
            agent[index] = usage_pw(entry["usage"], model) - old_rows[(model, family)]["floor"]
            agent_success[index] = int(bool(entry["success"]))
        if seen != set(range(30)) or any(value is None for value in agent + agent_success):
            raise ValueError(f"Incomplete matched use indices: {model}/{family}")
        seed = 20260923071 + int(hashlib.sha256((model + family).encode()).hexdigest()[:7], 16)
        rng = random.Random(seed); samples = [[rng.randrange(30) for _ in range(30)] for _ in range(B)]
        boot = lambda values: [statistics.mean(values[i] for i in ids) for ids in samples]
        ba, bp, bq = boot(agent), boot(program), boot(failures)
        ac, pc, q = statistics.mean(agent), statistics.mean(program), statistics.mean(failures)
        shares = [1-f-p/a for f,p,a in zip(bq,bp,ba)]
        paired_records[(model, family)] = {
            "model": model, "family": family, "n": 30, "bootstrap_seed": seed,
            "agent_cost": estimate(ac, ba), "program_cost": estimate(pc, bp),
            "agent_cv": {"estimate": statistics.stdev(agent)/ac, "ci95": None, "reporting": "Descriptive coefficient of variation."},
            "agent_success": count_record(sum(agent_success), 30),
            "program_success": count_record(30-sum(failures), 30),
            "program_failure": count_record(sum(failures), 30),
            "saving_share": estimate(1-q-pc/ac, shares),
            "sources": [str(path.relative_to(ROOT)) for path in paths] +
                       [record["deployment"].get("resolved_record_path", "embedded deployment records")]
        }
        for path in paths:
            measurement_sources[relative(path)] = sha(path)
    triples = {kind: [] for kind in ("price", "share", "paired", "verification")}
    compilation, serving, repeat_rows = [], [], []
    for family in FAMILIES:
        price_cols = [[] for _ in range(5)]
        share_cols = [[] for _ in range(4)]
        repeat_cols = [[] for _ in range(3)]
        paired_cols = [[] for _ in range(4)]
        for model in MODELS:
            record = initial.get((model, family))
            if record:
                c = record["charges"]["C"]
                ctext = (f">={c:.0f}" if not record["charges"].get("exact", True) else f"{c:.0f}")
                first, status = initial_status(record), outcome_text(record)
                old = old_rows[(model, family)]
                compilation.append({"model": model, "family": family,
                                    "attempt_cost": {"estimate": c, "lower_bound": not record["charges"].get("exact", True)},
                                    "initial_pass": {"count_display": first}, "final_pass": {"count_display": status},
                                    "source": record.get("source_artifacts", [])})
                metrics = record.get("profile_metrics")
                d, q = ((metrics["d"], metrics["q"]) if metrics else (None, None))
                if metrics:
                    costs, failures, _goals = deployment_costs(record)
                    seed = 20260923061 + int(hashlib.sha256((model + family).encode()).hexdigest()[:7], 16)
                    rng = random.Random(seed)
                    samples = [[rng.randrange(30) for _ in range(30)] for _ in range(B)]
                    cost_draws = [statistics.mean(costs[i] for i in ids) for ids in samples]
                    saving = (1 - q) * old["c"] - d
                    share = saving / old["c"]
                    payback = c / saving if saving > 0 else None
                    payback_with_traces = (c + 3 * old["c"]) / saving if saving > 0 else None
                    serving.append({"model": model, "family": family,
                                    "agent_cost": {"estimate": old["c"], "ci95": None,
                                                   "reason": "Retained identical historical exploration tasks."},
                                    "program_cost": estimate(d, cost_draws),
                                    "program_failure": count_record(sum(failures), 30),
                                    "saving_share": {"estimate": share, "ci95": None,
                                                     "reason": "Plug-in estimate from retained agent cost and revised deployment records."},
                                    "source": record["deployment"].get("resolved_record_path")})
                price_values = [ctext, first, status,
                                "--" if d is None or payback is None else f"{payback:.3g}",
                                "--" if d is None or payback_with_traces is None else f"{payback_with_traces:.3g}"]
                share_values = (["--", "--", "--", "--"] if d is None else
                                [f"{old['c']:.6f}", f"{d:.6f}", f"{q:.3f}", f"{share:.3f}"])
            else:
                price_values, share_values = ["--"] * 5, ["--"] * 4
            for column, value in zip(price_cols, price_values): column.append(value)
            for column, value in zip(share_cols, share_values): column.append(value)
            rs = sorted(repeats.get((model, family), []), key=lambda x: x["attempt_id"])
            attempts = ([record] if record else []) + rs
            codes = {"Pass 3/0/0": "P", "Gen. fail": "G"}
            verifier = "/".join(codes.get(outcome_text(x), "F") for x in attempts) if len(attempts) == 3 else "--"
            diagnostic = diagnostics.get((model, family))
            repeat_values = [verifier,
                             f"{diagnostic['direct']}/5" if diagnostic else "--",
                             f"{diagnostic['extracted']}/5" if diagnostic else "--"]
            for column, value in zip(repeat_cols, repeat_values): column.append(value)
            paired = paired_records.get((model, family))
            paired_values = ([f"{paired['agent_cost']['estimate']:.1f}", paired["agent_success"]["count_display"],
                              paired["program_success"]["count_display"], f"{paired['saving_share']['estimate']:.3f}"]
                             if paired else ["--"]*4)
            for column, value in zip(paired_cols, paired_values): column.append(value)
            if rs:
                repeat_rows.append({"model": model, "family": family,
                                    "attempts": [{"attempt_id": x["attempt_id"], "outcome": outcome_text(x)} for x in attempts],
                                    "supplied": (count_record(diagnostic["direct"], 5) if diagnostic else None),
                                    "extracted": (count_record(diagnostic["extracted"], 5) if diagnostic else None)})
        def line(columns): return NAMES[family] + " & " + " & ".join(v for col in columns for v in col) + r" \\"
        triples["price"].append(line(price_cols)); triples["share"].append(line(share_cols))
        triples["paired"].append(line(paired_cols)); triples["verification"].append(line(repeat_cols))
    data = {"schema": "three-building-measurement-render/1", "protocol_id": PROTOCOL,
            "latex_triples": triples, "rows": [], "qwen_rows": [], "prices": old_measurement["prices"]}
    uncertainty = {"serving": serving, "compilation": compilation, "paired_replays": list(paired_records.values()),
                   "repeat_verification": repeat_rows, "working_model_examples": {},
                   "source_sha256": measurement_sources}
    return data, uncertainty


def count_record(successes: int, n: int) -> dict:
    estimate = successes / n
    def cdf(k, probability):
        return sum(math.comb(n, i) * probability**i * (1-probability)**(n-i) for i in range(k+1))
    def invert(k, target):
        low, high = 0.0, 1.0
        for _ in range(80):
            mid = (low+high)/2
            if cdf(k, mid) > target: low = mid
            else: high = mid
        return (low+high)/2
    ci = [0.0 if successes == 0 else invert(successes-1, .975),
          1.0 if successes == n else invert(successes, .025)]
    return {"successes": successes, "trials": n, "estimate": estimate,
            "count_display": f"{successes}/{n}", "ci95": ci,
            "method": "Clopper-Pearson interval under independent Bernoulli working model"}


def load_cells(study: str, simulation_root: Path) -> list[dict]:
    directory = simulation_root / study / "cells"
    rows = []
    for path in sorted(directory.glob("*.json")):
        cell = json.loads(path.read_text())
        cell["_path"] = relative(path)
        cell["_sha256"] = sha(path)
        rows.append(cell)
    return rows


def interval(values):
    values = sorted(values); lo = values[int(.025 * (len(values) - 1))]; hi = values[int(.975 * (len(values) - 1))]
    return [lo, hi]


def estimate(point, draws):
    ci = interval(draws)
    return {"estimate": point, "ci95": ci, "minus": point-ci[0], "plus": ci[1]-point,
            "method": "paired percentile bootstrap", "bootstrap_samples": len(draws)}


def repetitions(cell: dict) -> int:
    return int(cell.get("repetitions") or cell.get("spec", {}).get("reps") or 0)


def cell_group(cell: dict) -> str | None:
    return (cell.get("group") or cell.get("spec", {}).get("_source_group") or
            {"paired_validation_grid": "original_grid", "fresh_grid": "fresh_grid",
             "paired_validation_real": "real_arrivals"}.get(cell.get("spec", {}).get("suite")))


def verify_cell_rows(cell: dict) -> int:
    reps = repetitions(cell)
    if reps <= 0 or not cell.get("rows"):
        raise ValueError(f"Invalid simulation cell: {cell.get('_path', cell.get('key'))}")
    for policy, rows in cell["rows"].items():
        if len(rows) != reps or any(not isinstance(row.get("cost"), (int, float)) for row in rows):
            raise ValueError(f"Invalid policy repetitions: {cell.get('_path')}/{policy}")
    for policy, summary in (cell.get("summary") or {}).items():
        if policy in cell["rows"] and isinstance(summary, dict) and "mean_cost" in summary:
            point = statistics.mean(row["cost"] for row in cell["rows"][policy])
            if not math.isclose(point, summary["mean_cost"], rel_tol=1e-12, abs_tol=1e-6):
                raise ValueError(f"Saved summary mismatch: {cell.get('_path')}/{policy}")
    return reps


def aggregate_conditions(cells: list[dict], seed: int) -> tuple[dict, list[dict], int]:
    if not cells:
        return {"by_model": {}}, [], 0
    reps = verify_cell_rows(cells[0])
    if any(verify_cell_rows(cell) != reps for cell in cells):
        raise ValueError("Mixed repetition counts in one condition group")
    policies = sorted(set.intersection(*(set(cell["rows"]) for cell in cells)))
    required = {"reactive", "safe_projected_025"}
    if not required.issubset(policies):
        raise ValueError(f"Required policies absent: {required - set(policies)}")
    rng = random.Random(seed)
    samples = [[rng.randrange(reps) for _ in range(reps)] for _ in range(B)]
    boot, output, checked = {}, [], 0
    for cell in cells:
        means = {policy: sum(row["cost"] for row in cell["rows"][policy]) / reps for policy in policies}
        boot_means = {
            policy: [sum(cell["rows"][policy][i]["cost"] for i in ids) / reps for ids in samples]
            for policy in policies
        }
        policy_out = {}
        for policy in policies:
            pace_draws = [x / y for x, y in zip(boot_means[policy], boot_means["safe_projected_025"])]
            agent_draws = [x / y for x, y in zip(boot_means[policy], boot_means["reactive"])]
            saving_draws = [1 - y / x for x, y in zip(boot_means[policy], boot_means["safe_projected_025"])]
            prefix_ratios = [row.get("prefix_ratio") for row in cell["rows"][policy]
                             if isinstance(row.get("prefix_ratio"), (int, float))]
            boot[(cell["key"], policy)] = {"pace": pace_draws, "agent": agent_draws, "saving": saving_draws}
            policy_out[policy] = {
                "ratio_pace": estimate(means[policy] / means["safe_projected_025"], pace_draws),
                "ratio_agent": estimate(means[policy] / means["reactive"], agent_draws),
                "pace_saving_vs_policy": estimate(1 - means["safe_projected_025"] / means[policy], saving_draws),
                "mean_cost": means[policy],
                "prefix_budget": {"runs": len(prefix_ratios),
                                  "maximum": max(prefix_ratios) if prefix_ratios else None,
                                  "raw_above_1.25": sum(value > 1.25 for value in prefix_ratios),
                                  "violations_above_1.25_with_tolerance": sum(
                                      value > 1.25 + PREFIX_RATIO_TOLERANCE for value in prefix_ratios),
                                  "ratio_tolerance": PREFIX_RATIO_TOLERANCE},
            }
            checked += 3
        spec = cell.get("spec") or {}
        output.append({
            "key": cell["key"], "model": cell.get("model") or spec["model"],
            "pattern": cell.get("pattern") or spec["pattern"], "repetitions": reps,
            "sources": [cell["_path"]], "policies": policy_out,
        })
    summary = {"bootstrap_seed": seed, "repetitions_per_condition": reps, "by_model": {}}
    for model in MODELS + ["all_models"]:
        selected = [cell for cell in output if model == "all_models" or cell["model"] == model]
        summary["by_model"][model] = {}
        for policy in policies:
            if not selected:
                continue
            pace = [cell["policies"][policy]["ratio_pace"]["estimate"] for cell in selected]
            agent = [cell["policies"][policy]["ratio_agent"]["estimate"] for cell in selected]
            saving = [cell["policies"][policy]["pace_saving_vs_policy"]["estimate"] for cell in selected]
            count = len(selected)
            pace_draws = [sum(values) / count for values in zip(*[boot[(cell["key"], policy)]["pace"] for cell in selected])]
            agent_draws = [sum(values) / count for values in zip(*[boot[(cell["key"], policy)]["agent"] for cell in selected])]
            saving_draws = [sum(values) / count for values in zip(*[boot[(cell["key"], policy)]["saving"] for cell in selected])]
            summary["by_model"][model][policy] = {
                "conditions": count,
                "mean_ratio_pace": estimate(sum(pace) / count, pace_draws),
                "mean_ratio_agent": estimate(sum(agent) / count, agent_draws),
                "mean_pace_saving_vs_policy": estimate(sum(saving) / count, saving_draws),
                "max_condition_mean_ratio_agent": {"estimate": max(agent), "ci95": None},
            }
            checked += 4
    return summary, output, checked


def online_data(final: bool, simulation_root: Path) -> tuple[dict, dict, dict, dict]:
    studies = {name: load_cells(name, simulation_root) for name in ("main", "comparisons", "ablations", "sensitivity")}
    source_hashes = {}
    if final:
        for name, rows in studies.items():
            config_path = simulation_root / name / "config.json"
            if not config_path.is_file():
                raise ValueError(f"Missing simulation config: {name}")
            expected = len(json.loads(config_path.read_text())["jobs"])
            if expected != EXPECTED_STUDY_CELLS[name]:
                raise ValueError(f"Unexpected simulation config size: {name}: {expected}")
            if len(rows) != expected:
                raise ValueError(f"Incomplete simulation cells: {name}: {len(rows)}/{expected}")
            completion = simulation_root / name / "rerun-complete.json"
            if not completion.is_file():
                raise ValueError(f"Missing simulation completion record: {name}")
            done = json.loads(completion.read_text())
            if (done.get("study") != name or done.get("status") != "complete" or
                    done.get("completed") != expected or done.get("expected") != expected or
                    done.get("config_sha256") != sha(config_path)):
                raise ValueError(f"Invalid simulation completion record: {name}")
            source_hashes[relative(config_path)] = sha(config_path)
            source_hashes[relative(completion)] = sha(completion)
            validation_path = simulation_root / name / "cell-validation.json"
            if (not validation_path.is_file() or done.get("cell_validation_sha256") != sha(validation_path)):
                raise ValueError(f"Invalid cell-validation record: {name}")
            validation = json.loads(validation_path.read_text())
            if (validation.get("status") != "validated" or validation.get("validated") != expected or
                    validation.get("unique_destinations") != expected):
                raise ValueError(f"Incomplete cell validation: {name}")
            source_hashes[relative(validation_path)] = sha(validation_path)
            summary_path = ROOT / done["result_summary"]
            if not summary_path.is_file() or done.get("result_summary_sha256") != sha(summary_path):
                raise ValueError(f"Invalid simulation summary: {name}")
            source_hashes[relative(summary_path)] = sha(summary_path)
            profile_mapping = simulation_root / name / "profile_mapping.json"
            if not profile_mapping.is_file() or done.get("profile_mapping_sha256") != sha(profile_mapping):
                raise ValueError(f"Invalid simulation profile mapping: {name}")
            source_hashes[relative(profile_mapping)] = sha(profile_mapping)
            for audit_name, hash_key in (("reuse-audit.json", "reuse_audit_sha256"),
                                         ("core-preservation-audit.json", "core_preservation_audit_sha256")):
                expected_hash = done.get(hash_key)
                if expected_hash:
                    audit_path = simulation_root / name / audit_name
                    if not audit_path.is_file() or sha(audit_path) != expected_hash:
                        raise ValueError(f"Invalid simulation audit: {name}/{audit_name}")
                    source_hashes[relative(audit_path)] = expected_hash
    for rows in studies.values():
        source_hashes.update({cell["_path"]: cell["_sha256"] for cell in rows})
    if final:
        if not SIMULATION_AUDIT.is_file():
            raise ValueError("Missing cross-study simulation audit")
        final_audit = json.loads(SIMULATION_AUDIT.read_text())
        if final_audit.get("status") != "complete" or final_audit.get("protocol_id") != PROTOCOL:
            raise ValueError("Cross-study simulation audit is incomplete")
        source_hashes[relative(SIMULATION_AUDIT)] = sha(SIMULATION_AUDIT)
    joined = {}
    additional = []
    for cell in studies["main"]:
        group = cell_group(cell)
        if group in {"original_grid", "fresh_grid", "real_arrivals"}:
            key = (group, cell["key"])
            if key in joined:
                raise ValueError(f"Duplicate main condition: {key}")
            joined[key] = cell
        elif group == "additional_missing_cost":
            additional.append(cell)
    for study in ("comparisons", "ablations"):
        seen = set()
        for cell in studies[study]:
            key = (cell_group(cell), cell["key"])
            if key in seen:
                raise ValueError(f"Duplicate {study} condition: {key}")
            seen.add(key)
            if key not in joined:
                if final:
                    raise ValueError(f"Unmatched {study} condition: {key}")
                continue
            base = joined[key]
            for policy, rows in cell["rows"].items():
                if policy in base["rows"]:
                    left = [row["cost"] for row in base["rows"][policy]]
                    right = [row["cost"] for row in rows]
                    if left != right:
                        raise ValueError(f"Conflicting joined policy: {study}/{key}/{policy}")
                else:
                    base["rows"][policy] = rows
            base.setdefault("_joined_sources", []).append(cell["_path"])
        if final and seen != set(joined):
            raise ValueError(f"{study} condition set mismatch: {len(seen)}/{len(joined)}")
    summaries, output_cells, point_checks = {}, {}, 0
    for group in ("original_grid", "fresh_grid", "real_arrivals"):
        cells = [cell for (g, _), cell in joined.items() if g == group]
        if not cells:
            continue
        if final and len(cells) != STANDARD_GROUP_COUNTS[group]:
            raise ValueError(f"Condition coverage mismatch: {group}: {len(cells)}")
        reps = repetitions(cells[0])
        seed = {8: 20260923008, 10: 20260922017}.get(reps, 20260923008)
        summaries[group], output_cells[group], checked = aggregate_conditions(cells, seed)
        for output, source in zip(output_cells[group], cells):
            output["sources"] += source.get("_joined_sources", [])
        point_checks += checked
    sensitivity_cells, sensitivity_summaries = [], {}
    for scenario in SENSITIVITY_SCENARIOS:
        cells = [cell for cell in studies["sensitivity"] if cell.get("scenario") == scenario]
        if final and len(cells) != 12:
            raise ValueError(f"Sensitivity coverage mismatch: {scenario}: {len(cells)}")
        if cells:
            summary, rendered, checked = aggregate_conditions(cells, 20260923041)
            sensitivity_summaries[scenario] = summary
            sensitivity_cells.extend({"scenario": scenario, **cell} for cell in rendered)
            point_checks += checked
    extra_summary, extra_cells = {}, []
    for index, cell in enumerate(additional):
        summary, rendered, checked = aggregate_conditions([cell], 20260923053 + index)
        extra_summary[cell["key"]] = summary
        extra_cells.extend(rendered)
        point_checks += checked
    for output, source in zip(extra_cells, additional):
        spec = source.get("spec") or source
        web = spec["profiles"]["Web/CommentPost"]
        sensitivity = web["missing_cost_sensitivity"]
        base_ref = spec["missing_cost_base_case"]
        base = joined[(base_ref["source_group"], base_ref["key"])]
        base_pace = statistics.mean(row["cost"] for row in base["rows"]["safe_projected_025"])
        base_react = statistics.mean(row["cost"] for row in base["rows"]["reactive"])
        output.update({
            "scenario": sensitivity["scenario"],
            "assigned_C_fail_pw": sensitivity["assigned_pw"],
            "base_modeled_C_fail_pw": sensitivity["base_modeled_pw"],
            "observed_lower_bound_pw": sensitivity["lower_bound_pw"],
            "base_condition": base_ref,
            "base_modeled_pace_to_react_ratio": base_pace / base_react,
            "budget_max_prefix_ratio": source.get("summary", {}).get("safe_projected_025", {}).get("max_prefix_ratio"),
        })
    if final and len(additional) != 4:
        raise ValueError(f"Additional missing-cost coverage mismatch: {len(additional)}/4")
    online = {"schema": "three-building-online-uncertainty/1", "protocol_id": PROTOCOL,
              "models_in_display_order": MODELS, "method": {"bootstrap_samples": B},
              "online": {"summaries": summaries, "cells": output_cells},
              "sensitivity": {"cells": sensitivity_cells, "summaries": sensitivity_summaries},
              "additional_missing_cost": {"cells": extra_cells, "summary": extra_summary}}
    table = {"schema": online["schema"], "protocol_id": PROTOCOL,
             "models_in_display_order": MODELS, "method": online["method"],
             "online_summary": summaries, "real_cells": output_cells.get("real_arrivals", []),
             "sensitivity_summary": sensitivity_summaries}
    return online, table, source_hashes, {
        "independent_point_checks": point_checks,
        "simulation_cells": {name: len(rows) for name, rows in studies.items()},
        "standard_conditions": sum(len(rows) for rows in output_cells.values()),
        "sensitivity_conditions": len(sensitivity_cells),
        "additional_missing_cost_conditions": len(extra_cells),
    }


def online_claims(online: dict, final: bool) -> dict:
    summaries = online["online"]["summaries"]
    cells = online["online"]["cells"]
    claims = {
        "status": "complete" if final else "partial",
        "condition_counts": {group: len(rows) for group, rows in cells.items()},
        "run_counts": {group: sum(row["repetitions"] for row in rows) for group, rows in cells.items()},
        "recorded_arrivals": {}, "grids": {}, "ablations": {}, "sensitivity": {},
        "prefix_budget": {}, "baseline_equalities": {}, "proposal_controls": {},
    }
    if "real_arrivals" in summaries:
        real = summaries["real_arrivals"]["by_model"]["all_models"]
        for policy in ("reactive", "autorpa_once", "toolpro_cost"):
            if policy not in real:
                continue
            ratios = [
                row["policies"]["safe_projected_025"]["ratio_agent"]["estimate"]
                if policy == "reactive" else 1 / row["policies"][policy]["ratio_pace"]["estimate"]
                for row in cells["real_arrivals"]
            ]
            claims["recorded_arrivals"][policy] = {
                "equal_weight_pace_saving_fraction": real[policy]["mean_pace_saving_vs_policy"],
                "pace_to_baseline_condition_ratio": {
                    "minimum": min(ratios), "maximum": max(ratios),
                    "lower_cost": sum(value < 1 - 1e-12 for value in ratios),
                    "ties": sum(abs(value - 1) <= 1e-12 for value in ratios),
                    "higher_cost": sum(value > 1 + 1e-12 for value in ratios),
                    "conditions": len(ratios),
                },
                "baseline_to_pace_mean_ratio": real[policy]["mean_ratio_pace"],
                "baseline_condition_counts": {
                    "cheaper_than_pace": sum(row["policies"][policy]["ratio_pace"]["estimate"] < 1 - 1e-12
                                             for row in cells["real_arrivals"]),
                    "ties": sum(abs(row["policies"][policy]["ratio_pace"]["estimate"] - 1) <= 1e-12
                                for row in cells["real_arrivals"]),
                    "costlier_than_pace": sum(row["policies"][policy]["ratio_pace"]["estimate"] > 1 + 1e-12
                                              for row in cells["real_arrivals"]),
                },
            }
    for group in ("original_grid", "fresh_grid"):
        if group not in summaries:
            continue
        all_models = summaries[group]["by_model"]["all_models"]
        claims["grids"][group] = {
            policy: {
                "baseline_to_pace_mean_ratio": all_models[policy]["mean_ratio_pace"],
                "baseline_to_agent_mean_ratio": all_models[policy]["mean_ratio_agent"],
                "maximum_condition_baseline_to_pace_ratio": max(
                    row["policies"][policy]["ratio_pace"]["estimate"] for row in cells[group]),
                "maximum_condition_policy_to_agent_ratio": max(
                    row["policies"][policy]["ratio_agent"]["estimate"] for row in cells[group]),
            }
            for policy in ("reactive", "autorpa_once", "toolpro_cost", "safe_projected_025")
            if policy in all_models
        }
        claims["baseline_equalities"][group] = {}
        for model in MODELS:
            selected = [row for row in cells[group] if row["model"] == model]
            if selected and all("toolpro_cost" in row["policies"] for row in selected):
                equal = sum(math.isclose(row["policies"]["toolpro_cost"]["ratio_agent"]["estimate"], 1.0,
                                         rel_tol=1e-12, abs_tol=1e-12) for row in selected)
                claims["baseline_equalities"][group][model] = {
                    "toolpro_equals_react_conditions": equal, "conditions": len(selected)}
    for policy in ("safe_projected_025", "safe_earliest_025", "projected", "pace_narrow_price", "earliest"):
        groups = {}
        for group in ("original_grid", "fresh_grid", "real_arrivals"):
            if group not in summaries or policy not in summaries[group]["by_model"]["all_models"]:
                continue
            record = summaries[group]["by_model"]["all_models"][policy]
            ratios = [row["policies"][policy]["ratio_pace"]["estimate"] for row in cells[group]]
            groups[group] = {
                "mean_ratio_to_pace": record["mean_ratio_pace"],
                "mean_percent_change_from_pace": {
                    "estimate": 100 * (record["mean_ratio_pace"]["estimate"] - 1),
                    "ci95": [100 * (value - 1) for value in record["mean_ratio_pace"]["ci95"]],
                },
                "cheaper_conditions": sum(value < 1 - 1e-12 for value in ratios),
                "ties": sum(abs(value - 1) <= 1e-12 for value in ratios),
                "costlier_conditions": sum(value > 1 + 1e-12 for value in ratios),
                "minimum_ratio": min(ratios), "maximum_ratio": max(ratios),
                "maximum_condition_ratio_to_agent": max(
                    row["policies"][policy]["ratio_agent"]["estimate"] for row in cells[group]),
                "by_model_mean_ratio_to_pace": {
                    model: summaries[group]["by_model"][model][policy]["mean_ratio_pace"]
                    for model in MODELS
                },
            }
        claims["ablations"][policy] = groups
    for policy in ("safe_earliest_allowance_025", "safe_arrival10_allowance_025",
                   "safe_projected_allowance_025", "safe_count_025", "safe_history_025", "safe_once_025"):
        claims["proposal_controls"][policy] = {
            group: {
                "mean_ratio_to_agent": summaries[group]["by_model"]["all_models"][policy]["mean_ratio_agent"],
                "mean_ratio_to_pace": summaries[group]["by_model"]["all_models"][policy]["mean_ratio_pace"],
            }
            for group in ("original_grid", "fresh_grid", "real_arrivals")
            if group in summaries and policy in summaries[group]["by_model"]["all_models"]
        }
    sensitivity_cells = online["sensitivity"]["cells"]
    for scenario, summary in online["sensitivity"]["summaries"].items():
        rows = [row for row in sensitivity_cells if row["scenario"] == scenario]
        all_models = summary["by_model"]["all_models"]
        pace_ratios = [row["policies"]["safe_projected_025"]["ratio_agent"]["estimate"] for row in rows]
        equal_ratios = [row["policies"]["pace_narrow_price"]["ratio_pace"]["estimate"] for row in rows]
        claims["sensitivity"][scenario] = {
            "conditions": len(rows),
            "pace_to_react_mean_ratio": all_models["safe_projected_025"]["mean_ratio_agent"],
            "equal_attempt_cost_to_pace_mean_ratio": all_models["pace_narrow_price"]["mean_ratio_pace"],
            "pace_vs_react": {
                "lower_cost": sum(value < 1 - 1e-12 for value in pace_ratios),
                "ties": sum(abs(value - 1) <= 1e-12 for value in pace_ratios),
                "higher_cost": sum(value > 1 + 1e-12 for value in pace_ratios),
                "minimum_ratio": min(pace_ratios), "maximum_ratio": max(pace_ratios),
            },
            "equal_attempt_cost_vs_pace": {
                "lower_cost": sum(value < 1 - 1e-12 for value in equal_ratios),
                "ties": sum(abs(value - 1) <= 1e-12 for value in equal_ratios),
                "higher_cost": sum(value > 1 + 1e-12 for value in equal_ratios),
                "minimum_ratio": min(equal_ratios), "maximum_ratio": max(equal_ratios),
            },
            "by_model": {
                model: {
                    "pace_to_react_mean_ratio": summary["by_model"][model]["safe_projected_025"]["mean_ratio_agent"],
                    "equal_attempt_cost_to_pace_mean_ratio": summary["by_model"][model]["pace_narrow_price"]["mean_ratio_pace"],
                } for model in MODELS
            },
        }
    main_budget = [row["policies"]["safe_projected_025"]["prefix_budget"]
                   for group_rows in cells.values() for row in group_rows]
    claims["prefix_budget"]["main_pace"] = {
        "runs": sum(row["runs"] for row in main_budget),
        "maximum": max(row["maximum"] for row in main_budget if row["maximum"] is not None),
        "raw_above_1.25": sum(row["raw_above_1.25"] for row in main_budget),
        "violations_above_1.25_with_tolerance": sum(
            row["violations_above_1.25_with_tolerance"] for row in main_budget),
        "ratio_tolerance": PREFIX_RATIO_TOLERANCE,
        "engine_tolerance": "1e-8 * max(1, 1.25 * A_t), represented conservatively as 1.26e-8 in ratio checks.",
    }
    sensitivity_budget = [row["policies"][policy]["prefix_budget"]
                          for row in sensitivity_cells
                          for policy in ("safe_projected_025", "pace_narrow_price")]
    if sensitivity_budget:
        claims["prefix_budget"]["sensitivity_protected_policies"] = {
            "policies": ["safe_projected_025", "pace_narrow_price"],
            "runs": sum(row["runs"] for row in sensitivity_budget),
            "maximum": max(row["maximum"] for row in sensitivity_budget if row["maximum"] is not None),
            "raw_above_1.25": sum(row["raw_above_1.25"] for row in sensitivity_budget),
            "violations_above_1.25_with_tolerance": sum(
                row["violations_above_1.25_with_tolerance"] for row in sensitivity_budget),
            "ratio_tolerance": PREFIX_RATIO_TOLERANCE,
            "engine_tolerance": "1e-8 * max(1, 1.25 * A_t), represented conservatively as 1.26e-8 in ratio checks.",
        }
    extra = online["additional_missing_cost"]
    claims["additional_missing_cost"] = {
        "scope": "Four DeepSeek Web failed-cost cells kept separate from the 612 main conditions.",
        "conditions": len(extra["cells"]),
        "cells": [{
            key: cell[key] for key in (
                "key", "pattern", "repetitions", "scenario", "assigned_C_fail_pw",
                "base_modeled_C_fail_pw", "observed_lower_bound_pw", "base_condition",
                "base_modeled_pace_to_react_ratio", "budget_max_prefix_ratio",
            )
        } | {
            "pace_to_react_ratio": cell["policies"]["safe_projected_025"]["ratio_agent"],
            "budget_prefix_runs": cell["policies"]["safe_projected_025"]["prefix_budget"]["runs"],
            "budget_raw_above_1.25": cell["policies"]["safe_projected_025"]["prefix_budget"]["raw_above_1.25"],
            "budget_violations_above_1.25_with_tolerance": cell["policies"]["safe_projected_025"]["prefix_budget"]["violations_above_1.25_with_tolerance"],
        } for cell in extra["cells"]],
    }
    return claims


def produce(out: Path, final: bool, simulation_root: Path) -> None:
    records = selected_records(); measurement, measurement_unc = measurement_data(records)
    if final:
        paired = measurement_unc["paired_replays"]
        if len(paired) != 6 or sum(row["n"] for row in paired) != 180:
            raise ValueError("Final paired measurement must contain six cells and 180 bindings")
        if any(row["agent_cost"] is None or row["program_cost"] is None or row["saving_share"] is None
               for row in paired):
            raise ValueError("Final paired measurement contains placeholder records")
    measurement_sources = measurement_unc.pop("source_sha256")
    online, table, simulation_sources, online_checks = online_data(final, simulation_root)
    table["measurement"] = measurement_unc
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "measurement-data.json", measurement)
    dump(out / "online-uncertainty.json", online)
    dump(out / "table-data.json", table)
    aggregation = json.loads(AGGREGATION.read_text())
    measurement_claims = json.loads(MEASUREMENT_CLAIMS.read_text())
    claims = {
        "schema": "three-building-claim-deltas/3", "protocol_id": PROTOCOL,
        "status": "complete" if final else "partial",
        "aggregation": {key: aggregation[key] for key in
                        ("main_selected", "outcome_eligible", "exact_cost_eligible", "admitted", "rejected", "undetermined_or_missing")},
        "measurement": {key: measurement_claims[key] for key in
                        ("main", "payback_admitted", "admitted_serving", "matched_android", "repeats", "fragility")},
        "online": online_claims(online, final),
        "lower_bounds": [{"identity": row["identity"], "C_pw": row["C_pw"], "cost_labels": row.get("cost_labels")}
                         for row in aggregation["rows"] if row.get("C_is_lower_bound")],
        "paper_rule": "Use only complete claims for final prose; retain first-failure untested counts and lower-bound labels.",
    }
    dump(out / "claim-deltas.json", claims)
    sources = {relative(path): sha(path) for _, _, path in records}
    sources.update(measurement_sources)
    sources.update(simulation_sources)
    for path in (SELECTIONS, PROFILES, MEASUREMENT_CLAIMS, AGGREGATION, COVERAGE):
        sources[relative(path)] = sha(path)
    if DIAGNOSTICS.is_file():
        sources[relative(DIAGNOSTICS)] = sha(DIAGNOSTICS)
    if DIAGNOSTICS_MANIFEST.is_file():
        sources[relative(DIAGNOSTICS_MANIFEST)] = sha(DIAGNOSTICS_MANIFEST)
        diagnostic_manifest = json.loads(DIAGNOSTICS_MANIFEST.read_text())
        for cell in diagnostic_manifest.get("cells", []):
            for kind in ("t18", "t20"):
                plan = cell[kind]
                if plan.get("action") == "reuse":
                    path = ROOT / plan["historical_path"]
                    sources[relative(path)] = sha(path)
            path_text = (cell.get("new_diagnostic") or {}).get("path")
            if path_text:
                path = ROOT / path_text
                sources[relative(path)] = sha(path)
    for row in measurement_unc["paired_replays"]:
        for source in row["sources"]:
            path = ROOT / source
            if path.is_file():
                sources[source] = sha(path)
    provenance = {"schema": "three-building-render-provenance/2", "protocol_id": PROTOCOL,
                  "source_sha256": sources, "checks": {**online_checks,
                  "selected_records": len(records), "main_expected": 20, "repeat_expected": 22,
                  "paired_cells": len(measurement_unc["paired_replays"]),
                  "paired_bindings": sum(row["n"] for row in measurement_unc["paired_replays"]),
                  "deployment_frozen_pw_cells": len(measurement_unc["serving"])},
                  "outputs_sha256": {name: sha(out/name) for name in
                                     ("measurement-data.json", "online-uncertainty.json", "table-data.json", "claim-deltas.json")}}
    dump(out / "provenance.json", provenance)
    if final:
        simulation_records = {name: relative(simulation_root / name / "rerun-complete.json")
                              for name in ("main", "comparisons", "ablations", "sensitivity")}
        manifest_sources = {relative(out / name): sha(out / name) for name in
                            ("measurement-data.json", "online-uncertainty.json", "table-data.json",
                             "claim-deltas.json", "provenance.json")}
        for path in (AGGREGATION, COVERAGE, PROFILES, MEASUREMENT_CLAIMS):
            manifest_sources[relative(path)] = sha(path)
        manifest = {
            "schema": "three-building-render-inputs/1", "protocol_id": PROTOCOL, "status": "complete",
            "aggregation": relative(AGGREGATION), "coverage": relative(COVERAGE),
            "profiles": relative(PROFILES),
            "diagnostics": relative(DIAGNOSTICS) if DIAGNOSTICS.is_file() else None,
            "claim_deltas": relative(out / "claim-deltas.json"),
            "provenance": relative(out / "provenance.json"),
            "simulations": simulation_records, "source_sha256": manifest_sources,
        }
        dump(out / "manifest.json", manifest)
    print(json.dumps({"mode":"final" if final else "partial","selected":len(records),"online_cells":sum(len(v) for v in online["online"]["cells"].values())}))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--partial",action="store_true"); parser.add_argument("--final",action="store_true"); parser.add_argument("--simulation-root",type=Path,default=ROOT/"experimental-results/trace_verifier_20260925/simulations"); args=parser.parse_args()
    if args.final:
        coverage=json.loads((HERE/"coverage-report.json").read_text())
        if not coverage.get("coverage_ready"):
            raise SystemExit("Coverage is incomplete")
        if coverage.get("invalid_records"):
            raise SystemExit("Coverage contains invalid records")
        if coverage.get("duplicate_selections"):
            raise SystemExit("Coverage contains duplicate selections")
        if coverage.get("seen_main_count") != 20 or coverage.get("seen_repeat_count") != 22:
            raise SystemExit("Coverage counts do not match 20 main and 22 repeat attempts")
        try:
            validate_diagnostics(require_complete=True)
        except ValueError as exc:
            raise SystemExit(str(exc))
        produce(FINAL,True,args.simulation_root)
    elif args.partial: produce(PARTIAL,False,args.simulation_root)
    else: parser.error("Choose --partial or --final")


if __name__=="__main__": main()
