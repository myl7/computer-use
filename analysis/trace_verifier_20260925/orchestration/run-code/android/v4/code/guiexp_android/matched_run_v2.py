"""Versioned continuation of the matched experiment after the censored pilot.

--prepare makes no paid calls and never changes original sources/spec/receipts.
--run keeps the same USD 10 ledger, including all unresolved v1 reservations.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import json
import importlib.metadata
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import matched_run as v1
from .budget_client import BudgetStop, atomic_json, canonical, fetch_metadata, validate_metadata
from .budget_client_v2 import BudgetLedgerV2, real_client

DEFAULT_OUT = v1.DEFAULT_OUT
SPEC_NAME = "spec.v2.json"
CENSORED_PAIR = "z-ai_glm-5.3-flash/MarkorDeleteNote/s913101"


def runtime_metadata():
    return {"python": sys.version.split()[0], "executable": sys.executable,
            "packages": {name: importlib.metadata.version(name) for name in ("openai", "httpx", "httpcore")}}


def source_hashes():
    hashes = v1.source_hashes()
    for name in ("budget_client_v2.py", "matched_run_v2.py"):
        hashes[name] = v1.sha(Path(__file__).with_name(name).read_bytes())
    return hashes


def prepare(out=DEFAULT_OUT):
    out = Path(out)
    if (out / SPEC_NAME).exists():
        return load_spec(out)
    old = v1.load_spec(out)
    ledger = BudgetLedgerV2(out / "budget.sqlite3")
    with ledger.connect() as db:
        receipts = [{"id": r[0], "episode": r[1], "state": r[2], "actual_nano": r[3],
                     "reserved_nano": r[4], "error_type": r[5]}
                    for r in db.execute("SELECT id,episode,state,actual_nano,reserved_nano,error_type FROM calls ORDER BY created")]
    # Archive copies make the v1 executable artifact independently reviewable.
    archive = out / "source_v1"
    for name, digest in old["source_sha256"].items():
        source = v1.ROOT / name if "/" in name else Path(__file__).with_name(name)
        data = source.read_bytes()
        if v1.sha(data) != digest:
            raise BudgetStop("Original source changed; cannot archive a faithful v1.")
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != data:
            raise BudgetStop("Conflicting v1 archive exists.")
        target.write_bytes(data)
    (archive / "spec.json").write_bytes((out / "spec.json").read_bytes())
    for model, lock in old["model_locks"].items():
        validate_metadata(model, fetch_metadata(model), lock)
    spec = copy.deepcopy(old)
    spec.pop("spec_sha256")
    spec.update(schema="android-matched-revision/2", version=2, source_sha256=source_hashes())
    spec["runtime"] = runtime_metadata()
    spec["runtime_note"] = "HTTP client dependencies were installed after the first launch failed before any paid request; this v2 snapshot records the working runtime."
    spec["protocol"]["on_unknown_bill"] = "stop current batch; keep reservation as unknown budget occupancy; explicit later v2 invocation may continue new episodes"
    spec["protocol"]["global_pacing_seconds_after_response"] = 10
    spec["protocol"]["on_429"] = "stop batch; persist safe status/retry-after/request-id fields; honor Retry-After before any future request"
    spec["censored_pairs"] = {CENSORED_PAIR: {
        "reason": "v1 pilot stopped after one paid response and one RateLimitError with unknown bill",
        "resume_policy": "neither arm will be resent; excluded from paired comparisons",
    }}
    spec["recovery"] = {
        "from_spec_sha256": old["spec_sha256"], "original_spec_file_sha256": v1.sha((out / "spec.json").read_bytes()),
        "original_receipts": receipts, "prepared_unix": time.time(),
        "budget_at_preparation": ledger.summary(),
        "billing_rule": "known actual plus every unresolved/inflight reservation plus new reservation must fit USD 10",
        "unknown_bill_is_zero": False,
        "request_retry_policy": "no SDK retry, no provider fallback, and no automatic reuse of any prior episode id",
    }
    spec["spec_sha256"] = v1.sha(canonical(spec).encode())
    atomic_json(out / SPEC_NAME, spec)
    atomic_json(out / "budget_summary.v2.json", ledger.summary())
    return spec


def load_spec(out):
    out = Path(out)
    old = v1.load_spec(out)
    spec = json.loads((out / SPEC_NAME).read_text())
    body = {k: v for k, v in spec.items() if k != "spec_sha256"}
    if v1.sha(canonical(body).encode()) != spec.get("spec_sha256") or spec.get("source_sha256") != source_hashes():
        raise BudgetStop("Version 2 specification or executable source changed.")
    if old["spec_sha256"] != spec["recovery"]["from_spec_sha256"]:
        raise BudgetStop("Version 1 provenance changed.")
    if v1.sha((out / "spec.json").read_bytes()) != spec["recovery"]["original_spec_file_sha256"]:
        raise BudgetStop("Original specification bytes changed.")
    if spec["runtime"] != runtime_metadata():
        raise BudgetStop("Runtime dependency versions changed after v2 preparation.")
    return spec


def pair_key(row):
    return row["id"].rsplit("/", 1)[0]


def progress(out, spec, ledger):
    counts = {}
    records = []
    for row in spec["episodes"]:
        path = Path(out) / "episodes" / row["id"] / "state.json"
        if pair_key(row) in spec["censored_pairs"]:
            item = {"status": "censored_v1", "reason": spec["censored_pairs"][pair_key(row)]["reason"]}
        else:
            item = json.loads(path.read_text()) if path.exists() else {"status": "pending"}
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        records.append({"id": row["id"], **item})
    result = {"version": 2, "spec_sha256": spec["spec_sha256"], "planned": len(records),
              "counts": counts, "budget": ledger.summary(), "episodes": records,
              "complete_pairs": sum(all(r["status"] == "done" for r in records[i:i+2]) for i in range(0, len(records), 2)),
              "analysis_rule": "complete known-bill pairs only; the original censored pair and its costs remain visible"}
    atomic_json(Path(out) / "progress.v2.json", result)
    atomic_json(Path(out) / "budget_summary.v2.json", result["budget"])
    return result


def execute(out, spec, ledger, client, env, episode_runner, max_pairs=None):
    completed = 0
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
            # Interrupted/budget-stopped episodes are terminal, including v2.
            continue
        if ledger.summary()["blocked"] or (max_pairs is not None and completed >= max_pairs):
            break
        row = pair[0]
        cell = f"{row['model'].replace('/', '_')}/{row['family']}"
        allowance = Decimal(spec["pair_allowances_usd"][cell])
        reserve = Decimal(spec["model_locks"][row["model"]]["reservation_usd"])
        if Decimal(ledger.summary()["available_usd"]) < allowance + 2 * reserve:
            break
        stop = False
        for row, previous in zip(pair, states):
            if previous:
                continue
            ep_out = Path(out) / "episodes" / row["id"]
            state_path = ep_out / "state.json"
            state = {"status": "running", "version": 2, "started_unix": time.time(),
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
            except BudgetStop:
                state.update(status="budget_stopped")
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
            break
        completed += 1
    return progress(out, spec, ledger)


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
            print(f"Prepared v2: {len(spec['episodes'])} original episodes, one censored pair; shared USD 10 ceiling; no paid calls.")
            return 0
        spec = load_spec(out)
        ledger = BudgetLedgerV2(out / "budget.sqlite3")
        if args.status:
            result = progress(out, spec, ledger)
        else:
            for model, expected in spec["model_locks"].items():
                validate_metadata(model, fetch_metadata(model), expected)
            client = real_client(ledger, spec["model_locks"], args.env_file)
            from .android_env import AndroidWorldEnv
            from .runner import run_episode
            env = AndroidWorldEnv(boot_if_needed=False)
            try:
                result = execute(out, spec, ledger, client, env, run_episode, args.max_pairs)
            finally:
                env.close()
                client.sdk.close()
        print(json.dumps({"counts": result["counts"], "complete_pairs": result["complete_pairs"], "budget": result["budget"]}, indent=2))
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BudgetStop as exc:
        print(f"STOPPED: {exc}")
        raise SystemExit(2)
