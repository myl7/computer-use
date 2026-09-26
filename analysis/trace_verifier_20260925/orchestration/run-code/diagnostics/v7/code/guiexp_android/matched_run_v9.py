"""Version 9 preserves the output cap and handles billed empty completions."""
from __future__ import annotations
import argparse,copy,fcntl,json,sys,time
from decimal import Decimal
from pathlib import Path
from . import matched_run as v1
from . import matched_run_v8 as previous
from .budget_client import BudgetStop,atomic_json,canonical
from .budget_client_v9 import BudgetLedgerV9,real_client,validated_metadata

DEFAULT_OUT=v1.DEFAULT_OUT
SPEC_NAME="spec.v9.json"
runtime_metadata=previous.runtime_metadata


def source_hashes():
    result=previous.source_hashes()
    for name in ("budget_client_v9.py","matched_run_v9.py"):
        result[name]=v1.sha(Path(__file__).with_name(name).read_bytes())
    return result


def prepare(out=DEFAULT_OUT):
    out=Path(out)
    if (out/SPEC_NAME).exists():return load_spec(out)
    old=previous.load_spec(out);ledger=BudgetLedgerV9(out/"budget.sqlite3")
    spec=copy.deepcopy(old);spec.pop("spec_sha256")
    spec.update(schema="android-matched-revision/9",version=9,source_sha256=source_hashes(),runtime=runtime_metadata())
    spec["protocol"]["zero_call_preflight_restart"]={"allowed_stop_reason":"Pinned provider endpoint is not active.","required_evidence":"zero receipts, empty trajectory, no step screenshots, no active runner", "prior_artifacts":"archived with hashes before reset", "execution_ids":"fresh nonce; canonical episode and binding unchanged", "paid_episode_replay":False}
    spec["recovery"]={"from_version":8,"from_spec_sha256":old["spec_sha256"],"original_spec_file_sha256":v1.sha((out/"spec.v8.json").read_bytes()),"prepared_unix":time.time(),"budget_at_preparation":ledger.summary(),"provider_readiness":"deferred until runtime; unchanged price/token caps must validate before paid work"}
    spec["spec_sha256"]=v1.sha(canonical(spec).encode());atomic_json(out/SPEC_NAME,spec)
    return spec


def load_spec(out):
    out=Path(out);old=previous.load_spec(out);s=json.loads((out/SPEC_NAME).read_text())
    body={k:v for k,v in s.items() if k!="spec_sha256"}
    if v1.sha(canonical(body).encode())!=s["spec_sha256"] or s["source_sha256"]!=source_hashes():raise BudgetStop("Version9 source/spec changed.")
    if old["spec_sha256"]!=s["recovery"]["from_spec_sha256"] or v1.sha((out/"spec.v8.json").read_bytes())!=s["recovery"]["original_spec_file_sha256"]:raise BudgetStop("Prior frozen specification changed.")
    if runtime_metadata()!=s["runtime"]:raise BudgetStop("Frozen runtime changed.")
    return s


def recover_zero_call_preflights(out,spec,ledger):
    # Caller must own run.lock. No model request or agent action may have occurred.
    out=Path(out);recovered=[]
    for row in spec["episodes"]:
        ep=out/"episodes"/row["id"];state_path=ep/"state.json"
        if not state_path.exists():continue
        state=json.loads(state_path.read_text())
        if state.get("status")!="budget_stopped" or state.get("stop_reason")!="Pinned provider endpoint is not active.":continue
        with ledger.connect() as db:
            if db.execute("SELECT 1 FROM calls WHERE episode=?",(row["id"],)).fetchone():continue
        trajectory=ep/"trajectory.jsonl"
        if not trajectory.exists() or trajectory.stat().st_size or list(ep.glob("step_*.png")):continue
        archive=out/"preflight_restarts"/row["id"]/str(time.time_ns())
        archive.mkdir(parents=True,exist_ok=False)
        record={"episode":row["id"],"time":time.time(),"state_sha256":v1.sha(state_path.read_bytes()),"trajectory_sha256":v1.sha(trajectory.read_bytes()),"reason":"verified zero-receipt, zero-agent-action provider preflight failure"}
        # Keep byte-identical originals; the next run starts from task reset.
        state_path.rename(archive/"state.json");trajectory.rename(archive/"trajectory.jsonl")
        atomic_json(archive/"restart_evidence.json",record);recovered.append(record)
    return recovered


def pair_key(row):
    return row["id"].rsplit("/", 1)[0]


def recover_running(out, spec):
    """Caller owns run.lock, so a prior running record is an interrupted run."""
    changed = 0
    for row in spec["episodes"]:
        path = Path(out) / "episodes" / row["id"] / "state.json"
        if not path.exists():
            continue
        state = json.loads(path.read_text())
        if state.get("status") == "running":
            state.update(status="interrupted", ended_unix=time.time(),
                         recovery_note="v9 acquired the process lock after the previous writer stopped; never resend this episode")
            atomic_json(path, state)
            changed += 1
    return changed


def progress(out, spec, ledger):
    counts = {}
    records = []
    for row in spec["episodes"]:
        path = Path(out) / "episodes" / row["id"] / "state.json"
        if pair_key(row) in spec["censored_pairs"]:
            item = {"status": "censored_prior", "reason": spec["censored_pairs"][pair_key(row)]["reason"]}
        else:
            item = json.loads(path.read_text()) if path.exists() else {"status": "pending"}
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        records.append({"id": row["id"], **item})
    result = {"version": 9, "spec_sha256": spec["spec_sha256"], "planned": len(records),
              "counts": counts, "budget": ledger.summary(), "episodes": records,
              "complete_pairs": sum(all(r["status"] == "done" for r in records[i:i+2]) for i in range(0, len(records), 2)),
              "analysis_rule": "complete known-bill pairs only; the original censored pair and its costs remain visible"}
    atomic_json(Path(out) / "progress.v9.json", result)
    atomic_json(Path(out) / "budget_summary.v9.json", result["budget"])
    return result


def execute(out, spec, ledger, client, env, episode_runner, max_pairs=None):
    completed = 0
    batch_status = "complete"
    for i in range(0, len(spec["episodes"]), 2):
        pair = spec["episodes"][i:i+2]
        if pair_key(pair[0]) in spec["censored_pairs"]:
            continue
        states = []
        for row in pair:
            path = Path(out) / "episodes" / row["id"] / "state.json"
            states.append(json.loads(path.read_text()) if path.exists() else None)
        if all(state and state["status"] == "done" for state in states):
            continue
        if any(state and state["status"] != "done" for state in states):
            # Interrupted/budget-stopped episodes are terminal, including v9.
            for row, state in zip(pair, states):
                if state is None:
                    atomic_json(Path(out) / "episodes" / row["id"] / "state.json",
                                {"status": "skipped_incomplete_pair", "version": 9})
            continue
        if max_pairs is not None and completed >= max_pairs:
            batch_status = "pilot_limit"
            break
        if ledger.summary()["blocked"]:
            batch_status = "budget_stopped"
            break
        row = pair[0]
        cell = f"{row['model'].replace('/', '_')}/{row['family']}"
        allowance = Decimal(spec["pair_allowances_usd"][cell])
        reserve = Decimal(spec["model_locks"][row["model"]]["reservation_usd"])
        if Decimal(ledger.summary()["available_usd"]) < allowance + 2 * reserve:
            batch_status = "budget_stopped"
            break
        stop = False
        for row, previous in zip(pair, states):
            if previous:
                continue
            ep_out = Path(out) / "episodes" / row["id"]
            state_path = ep_out / "state.json"
            state = {"status": "running", "version": 9, "started_unix": time.time(),
                     "model": row["model"], "family": row["family"], "seed": row["seed"], "condition": row["condition"]}
            state.update(spec["planned_bindings"][cell]["bindings"][str(row["seed"])])
            atomic_json(state_path, state)
            try:
                client.begin_episode(row["id"])
                result = episode_runner(
                    family=row["family"], condition=row["condition"], seed=row["seed"], model=row["model"],
                    obs_mode=spec["obs_mode"], max_steps=row["max_steps"], out_dir=ep_out,
                    client=client, env=env, close_env=False, keep_emulator=True,
                    doc_text=(Path(out) / row["doc"]["frozen"]).read_text() if row["condition"] == "doc" else None,
                )
                state.update(status="done", result=result)
            except BudgetStop as exc:
                state.update(status="budget_stopped", stop_reason=str(exc))
                stop = True
            except BaseException as exc:
                state.update(status="interrupted", error_type=type(exc).__name__)
                stop = True
            state["ended_unix"] = time.time()
            atomic_json(state_path, state)
            current = progress(out, spec, ledger)
            print(f"{row['id']}: {state['status']}; actual USD {current['budget']['actual_usd']}; occupied USD {current['budget']['budget_occupied_usd']}", flush=True)
            if stop:
                break
        if stop:
            batch_status = state["status"]
            break
        completed += 1
    result = progress(out, spec, ledger)
    result["batch_status"] = batch_status
    atomic_json(Path(out) / "progress.v9.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--max-pairs", type=int)
    parser.add_argument("--env-file", type=Path, default=Path("/Users/myl/app/.env"))
    args = parser.parse_args()
    if args.max_pairs is not None and args.max_pairs < 1:
        raise BudgetStop("Pair count must be positive.")
    out = DEFAULT_OUT
    with (out / "run.lock").open("a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BudgetStop("Another process owns this Android run.") from None
        if args.prepare:
            spec = prepare(out)
            print(f"Prepared v9: {len(spec['episodes'])} original episodes, {len(spec['censored_pairs'])} censored pairs; shared USD 10 ceiling; no paid calls.")
            return 0
        spec = load_spec(out)
        ledger = BudgetLedgerV9(out / "budget.sqlite3")
        if args.status:
            result = progress(out, spec, ledger)
        else:
            recover_running(out, spec)
            for model, expected in spec["model_locks"].items():
                validated_metadata(model, expected)
            client = real_client(ledger, spec["model_locks"], args.env_file)
            from .android_env import AndroidWorldEnv
            from .runner import run_episode
            env = AndroidWorldEnv(boot_if_needed=False)
            try:
                recover_zero_call_preflights(out,spec,ledger)
                result = execute(out, spec, ledger, client, env, run_episode, args.max_pairs)
            finally:
                env.close()
                client.sdk.close()
        print(json.dumps({"counts": result["counts"], "complete_pairs": result["complete_pairs"], "budget": result["budget"]}, indent=2))
        return 0 if result.get("batch_status", "complete") in ("complete", "pilot_limit") else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetStop as exc:
        print(f"STOPPED: {exc}")
        raise SystemExit(2)
