"""Frozen matched discover/doc revision experiment with shared USD 10 budget.

--prepare performs public metadata GETs, copies documents, and writes the plan.
--run performs paid calls. It never retries an interrupted episode or request.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

from .budget_client import (
    BudgetClient, BudgetLedger, BudgetStop, MAX_COMPLETION, MODEL_LOCKS,
    atomic_json, canonical, fetch_metadata, real_client, validate_metadata,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "experimental-results/guiexp_android/revision_20260913"
BUILD_ROOT = ROOT / "experimental-results/guiexp_android/t16_build"
SEEDS = (913101, 913102, 913103, 913104, 913105)
FAMILIES = (
    "MarkorDeleteNote", "ContactsAddContact", "OsmAndFavorite",
    "MarkorCreateNote", "OsmAndMarker", "FilesMoveFile", "SimpleCalendarAddOneEvent",
)
CAPS = dict(zip(FAMILIES, (10, 12, 13, 16, 20, 20, 34)))
MODELS = tuple(MODEL_LOCKS)
SOURCE_FILES = (
    "budget_client.py", "matched_run.py", "runner.py", "agent.py",
    "conditions.py", "android_env.py", "actions.py", "forksafe.py", "compiler.py",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def params_json(value):
    if dataclasses.is_dataclass(value):
        return params_json(dataclasses.asdict(value))
    if hasattr(value, "_asdict"):
        return params_json(value._asdict())
    if isinstance(value, dict):
        return {key: params_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [params_json(item) for item in value]
    return value


def source_hashes():
    from .android_env import _task_classes
    import inspect
    hashes = {name: sha(Path(__file__).with_name(name).read_bytes()) for name in SOURCE_FILES}
    for cls in _task_classes().values():
        path = Path(inspect.getfile(cls))
        hashes[str(path.relative_to(ROOT))] = sha(path.read_bytes())
    return hashes


def rows_for(docs, cell_seeds=None):
    rows = []
    # Rotate over all cells before advancing to the next seed, so a budget
    # interruption is less likely to omit one model or all difficult families.
    for si in range(5):
        for fi, family in enumerate(FAMILIES):
            for mi, model in enumerate(MODELS):
                slug = model.replace("/", "_")
                key = f"{slug}/{family}"
                seeds = cell_seeds[key] if cell_seeds is not None else SEEDS
                if si >= len(seeds):
                    continue
                seed = seeds[si]
                order = ("discover", "doc") if (si + fi + mi) % 2 == 0 else ("doc", "discover")
                for condition in order:
                    rows.append({"id": f"{key}/s{seed}/{condition}", "model": model,
                                 "family": family, "seed": seed, "condition": condition,
                                 "max_steps": CAPS[family], "doc": docs[key]})
    return rows


def audit_bindings():
    """Exclude exact historical bindings separately for each family/model."""
    from .android_env import instance_params
    from .compiler import binding_fields, params_to_binding
    keys = [(m, f) for m in MODELS for f in FAMILIES]
    prior = {k: set() for k in keys}
    prior_seeds = {k: set() for k in keys}
    examined = []
    result_root = ROOT / "experimental-results/guiexp_android"
    aliases = {m.replace("/", "_"): m for m in MODELS}

    def visit(value, model=None, family=None):
        if isinstance(value, dict):
            model = value.get("model", model)
            model = aliases.get(model, model)
            family = value.get("family", family)
            targets = [(m, f) for m, f in keys
                       if (model is None or m == model) and (family is None or f == family)]
            for key in targets:
                fields = binding_fields(key[1])
                if all(k in value for k in fields):
                    prior[key].add(sha(canonical({k: value[k] for k in fields}).encode()))
                if isinstance(value.get("seed"), int):
                    prior_seeds[key].add(value["seed"])
                if isinstance(value.get("seeds"), (list, tuple)):
                    prior_seeds[key].update(s for s in value["seeds"] if isinstance(s, int))
            for child in value.values():
                visit(child, model, family)
        elif isinstance(value, list):
            for child in value:
                visit(child, model, family)

    for path in sorted(result_root.rglob("*")):
        if path.suffix not in (".json", ".jsonl") or DEFAULT_OUT in path.parents:
            continue
        raw = path.read_bytes()
        model = next((m for m in MODELS if m.replace("/", "_") in str(path)), None)
        family = next((f for f in FAMILIES if f in str(path)), None)
        try:
            values = [json.loads(line) for line in raw.splitlines() if line.strip()] if path.suffix == ".jsonl" else [json.loads(raw)]
            for value in values:
                visit(value, model, family)
        except (ValueError, TypeError):
            raise BudgetStop("An existing result cannot be parsed for the binding exclusion audit.") from None
        examined.append({"path": str(path.relative_to(ROOT)), "sha256": sha(raw)})
    planned = {}
    for model, family in keys:
        old = prior[model, family]
        for seed in prior_seeds[model, family]:
            old.add(sha(canonical(params_to_binding(family, instance_params(family, seed))).encode()))
        selected = {}
        seen = set()
        # Finite OsmAnd support has only 24 binding strings. Stop searching
        # when every possible historically unseen binding has been found.
        target_n = 5
        support_n = None
        if family.startswith("OsmAnd"):
            from android_world.task_evals.single.osmand import _PRELOADED_MAP_LOCATIONS
            strings = list(_PRELOADED_MAP_LOCATIONS)
            strings += [f"{xy[0]}, {xy[1]}" for xy in _PRELOADED_MAP_LOCATIONS.values()]
            possible = {sha(canonical({"location": value}).encode()) for value in strings}
            support_n = len(possible)
            target_n = min(target_n, len(possible - old))
        for seed in range(913101, 923101):
            if len(selected) >= target_n:
                break
            if seed in prior_seeds[model, family]:
                continue
            params = instance_params(family, seed)
            binding = params_to_binding(family, params)
            digest = sha(canonical(binding).encode())
            if digest in old or digest in seen:
                continue
            seen.add(digest)
            selected[str(seed)] = {"params_sha256": sha(canonical(params_json(params)).encode()),
                                   "binding_sha256": digest}
        key = f"{model.replace('/', '_')}/{family}"
        planned[key] = {"bindings": selected, "fresh_distinct_n": len(selected),
                        "requested_n": 5, "finite_support_n": support_n,
                        "historical_binding_n": len(old),
                        "historical_recorded_seeds": sorted(prior_seeds[model, family])}
    return {"prior_files": examined, "planned": planned,
            "scope": "all historical Android JSON/JSONL; exact binding and regenerated seed exclusion per family/model"}


def pair_allowances():
    """Scheduling estimate only; per-call ledger reservations enforce the cap."""
    allowances = {}
    for model in MODELS:
        for family in FAMILIES:
            data = json.loads((BUILD_ROOT / model.replace("/", "_") / family / "build.json").read_text())
            exploration = data["exploration"]["per_episode"]
            doc = data["doc_arm"]
            reactive = sum(Decimal(str(x.get("cost_usd") or 0)) for x in exploration) / len(exploration)
            text = Decimal(str(doc["totals"]["cost_usd"])) / len(doc["episodes"])
            allowances[f"{model.replace('/', '_')}/{family}"] = str(2 * (reactive + text))
    return allowances


def prepare(out=DEFAULT_OUT, fetcher=fetch_metadata):
    out = Path(out)
    if (out / "spec.json").exists():
        spec = load_spec(out)
        return spec
    out.mkdir(parents=True, exist_ok=True)
    binding_audit = audit_bindings()
    cell_seeds = {}
    finite_cell = "z-ai_glm-5.3-flash/OsmAndMarker"
    for key, data in binding_audit["planned"].items():
        required = 1 if key == finite_cell else 5
        if data["fresh_distinct_n"] != required:
            raise BudgetStop(f"Unexpected fresh binding count for {key}; no paid plan prepared.")
        cell_seeds[key] = [int(seed) for seed in data["bindings"]]
    atomic_json(out / "binding_exclusion_audit.json", binding_audit)
    docs = {}
    for model in MODELS:
        slug = model.replace("/", "_")
        for family in FAMILIES:
            source = BUILD_ROOT / slug / family / "artifact_k3_doc.txt"
            if not source.is_file():
                raise BudgetStop("A required frozen document is absent.")
            data = source.read_bytes()
            target = Path("frozen_docs") / slug / family / "artifact_k3_doc.txt"
            (out / target).parent.mkdir(parents=True, exist_ok=True)
            (out / target).write_bytes(data)
            docs[f"{slug}/{family}"] = {"source": str(source), "frozen": str(target), "sha256": sha(data)}
    locks = {}
    for model in MODELS:
        metadata = fetcher(model)
        locks[model] = validate_metadata(model, metadata)
        atomic_json(out / "provider_metadata" / (model.replace("/", "_") + ".json"),
                    {"fetched_unix": time.time(), "response": metadata})
    spec = {"schema": "android-matched-revision/1", "models": list(MODELS),
            "per_cell_seeds": cell_seeds, "model_locks": locks, "source_sha256": source_hashes(),
            "budget": {"shared_ledger": str((out / "budget.sqlite3").resolve()),
                       "limit_usd": "10", "planning_hkd_per_usd": "8", "prior_new_spend_usd": "0"},
            "obs_mode": "screenshot+ax", "max_completion_tokens": MAX_COMPLETION,
            "binding_audit_sha256": sha(canonical(binding_audit).encode()),
            "planned_bindings": binding_audit["planned"],
            "pair_allowances_usd": pair_allowances(),
            "protocol": {"arms": ["discover", "doc"], "order": "alternating AB/BA across model/family/seed",
                         "reflection_retries": 0, "keep_failures": True,
                         "on_interrupted_episode": "record interrupted and never retry automatically",
                         "on_unknown_bill": "retain full request reserve and stop all paid work"},
            "finite_support_exception": {"cell": finite_cell, "n_pairs": 1, "distinct_binding_n": 1,
                                         "confidence_interval": False,
                                         "reason": "23 of the benchmark's 24 supported binding strings occur in historical records"},
            "pricing_sources": ["https://openrouter.ai/docs/guides/routing/provider-selection",
                                "https://openrouter.ai/docs/api/api-reference/endpoints/list-all-endpoints-for-a-model"],
            "episodes": rows_for(docs, cell_seeds)}
    spec["spec_sha256"] = sha(canonical(spec).encode())
    atomic_json(out / "spec.json", spec)
    ledger = BudgetLedger(out / "budget.sqlite3")
    atomic_json(out / "budget_summary.json", ledger.summary())
    return spec


def load_spec(out):
    out = Path(out)
    spec = json.loads((out / "spec.json").read_text())
    content = {k: v for k, v in spec.items() if k != "spec_sha256"}
    if sha(canonical(content).encode()) != spec.get("spec_sha256"):
        raise BudgetStop("Prepared specification changed.")
    if spec.get("source_sha256") != source_hashes():
        raise BudgetStop("Experiment source changed after preparation.")
    if spec["budget"]["shared_ledger"] != str((out / "budget.sqlite3").resolve()):
        raise BudgetStop("Prepared run moved away from its shared ledger.")
    for row in spec["episodes"]:
        doc = row["doc"]
        if sha((out / doc["frozen"]).read_bytes()) != doc["sha256"]:
            raise BudgetStop("A frozen document changed after preparation.")
    return spec


def progress(out, spec, ledger):
    counts = {}
    records = []
    for row in spec["episodes"]:
        path = Path(out) / "episodes" / row["id"] / "state.json"
        item = json.loads(path.read_text()) if path.exists() else {"status": "pending"}
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        records.append({"id": row["id"], **item})
    result = {"spec_sha256": spec["spec_sha256"], "planned": len(records),
              "counts": counts, "budget": ledger.summary(), "episodes": records}
    pairs = [records[i:i+2] for i in range(0, len(records), 2)]
    result["complete_pairs"] = sum(all(r["status"] == "done" for r in pair) for pair in pairs)
    result["analysis_rule"] = "Use complete pairs only; interrupted and budget-stopped pairs remain visible and excluded."
    atomic_json(Path(out) / "progress.json", result)
    atomic_json(Path(out) / "budget_summary.json", result["budget"])
    return result


def execute(out, spec, ledger, client, env, episode_runner, max_episodes=None):
    """Testable scheduler. Existing records are terminal and never re-requested."""
    completed_this_run = 0
    for index, row in enumerate(spec["episodes"]):
        ep_out = Path(out) / "episodes" / row["id"]
        state_path = ep_out / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if state["status"] == "running":
                state.update(status="interrupted", ended_unix=time.time())
                atomic_json(state_path, state)
            continue
        if ledger.summary()["blocked"]:
            break
        if max_episodes is not None and completed_this_run >= max_episodes:
            break
        pair_key = f"{row['model'].replace('/', '_')}/{row['family']}"
        if index % 2 == 0:
            # This is an intentionally conservative scheduling estimate, not
            # the hard bound. The latter is rechecked before every request.
            available = Decimal("10") - Decimal(ledger.summary()["actual_usd"])
            allowance = Decimal(spec["pair_allowances_usd"][pair_key])
            request_reserve = Decimal(spec["model_locks"][row["model"]]["reservation_usd"])
            if available < allowance + 2 * request_reserve:
                break
        else:
            first = spec["episodes"][index-1]
            first_state = json.loads((Path(out) / "episodes" / first["id"] / "state.json").read_text())
            if first_state["status"] != "done":
                atomic_json(state_path, {"status": "skipped_incomplete_pair"})
                continue
        state = {"status": "running", "started_unix": time.time(), "model": row["model"],
                 "family": row["family"], "seed": row["seed"], "condition": row["condition"]}
        if "planned_bindings" in spec:
            state.update(spec["planned_bindings"][pair_key]["bindings"][str(row["seed"])])
        atomic_json(state_path, state)
        client.begin_episode(row["id"])
        try:
            result = episode_runner(
                family=row["family"], condition=row["condition"], seed=row["seed"],
                model=row["model"], obs_mode=spec["obs_mode"], max_steps=row["max_steps"],
                out_dir=ep_out, client=client, env=env, close_env=False, keep_emulator=True,
                doc_text=(Path(out) / row["doc"]["frozen"]).read_text() if row["condition"] == "doc" else None,
            )
            state.update(status="done", result=result)
        except BudgetStop:
            state.update(status="budget_stopped")
        except BaseException as exc:
            # Exception text is excluded because SDK errors can contain headers.
            state.update(status="interrupted", error_type=type(exc).__name__)
        state["ended_unix"] = time.time()
        atomic_json(state_path, state)
        completed_this_run += 1
        current = progress(out, spec, ledger)
        print(f"{row['id']}: {state['status']}; actual USD {current['budget']['actual_usd']}", flush=True)
        if state["status"] != "done":
            break
    return progress(out, spec, ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true", help="public GET metadata only; no paid calls or device writes")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--run", action="store_true", help="start or resume paid Android episodes")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--env-file", type=Path, default=Path("/Users/myl/app/.env"))
    parser.add_argument("--max-episodes", type=int, default=None, help="optional bounded initial batch")
    parser.add_argument("--max-pairs", type=int, default=None, help="--max-pairs 1 runs one complete pilot pair")
    args = parser.parse_args()
    if args.out.resolve() != DEFAULT_OUT.resolve():
        raise BudgetStop("This task must use its fixed shared budget directory.")
    if args.max_pairs is not None:
        if args.max_episodes is not None or args.max_pairs < 1:
            raise BudgetStop("Use one positive pair limit.")
        args.max_episodes = 2 * args.max_pairs
    if args.max_episodes is not None and (args.max_episodes < 2 or args.max_episodes % 2):
        raise BudgetStop("Pilot limits must contain complete pairs.")
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "run.lock").open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another process owns this Android run.") from None
        if args.prepare:
            spec = prepare(args.out)
            print(f"Prepared {len(spec['episodes'])} episodes; USD 10 shared ceiling; no paid calls.")
            return 0
        spec = load_spec(args.out)
        ledger = BudgetLedger(args.out / "budget.sqlite3")
        if args.status:
            result = progress(args.out, spec, ledger)
            print(json.dumps({"counts": result["counts"], "budget": result["budget"]}, indent=2))
            return 0
        if ledger.summary()["blocked"]:
            raise BudgetStop("Unresolved ledger reservation prevents a paid run.")
        # Refresh both provider bounds before touching the emulator or making
        # the first paid request. No automatic provider substitutions.
        for model, expected in spec["model_locks"].items():
            validate_metadata(model, fetch_metadata(model), expected)
        client = real_client(ledger, spec["model_locks"], args.env_file)
        from .android_env import AndroidWorldEnv
        from .runner import run_episode
        env = AndroidWorldEnv(boot_if_needed=False)
        try:
            result = execute(args.out, spec, ledger, client, env, run_episode, args.max_episodes)
        finally:
            env.close()
            client.sdk.close()
        print(json.dumps({"counts": result["counts"], "budget": result["budget"]}, indent=2))
        return 0 if result["counts"].get("done", 0) == len(spec["episodes"]) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetStop as exc:
        print(f"STOPPED: {exc}")
        raise SystemExit(2)
