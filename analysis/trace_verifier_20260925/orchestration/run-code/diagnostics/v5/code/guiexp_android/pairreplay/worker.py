"""One paired-replay worker: one AVD, one shard of the 180 agent runs.

    .venv-android/bin/python -m guiexp_android.pairreplay.worker \
        --worker 0 --workers 3 \
        --deploy-root ~/app/guiexp/android-pair/deploy_inputs \
        --out-root ~/app/guiexp/android-pair/results/t21_paired_replay

Each worker owns one headless AVD (`guiexpPair-w<k>`, console port
8620+2k, grpc 8630+k) and processes every (cell, use) item whose global
index is congruent to k mod workers. Per item it runs one FULL discover
episode with the deploy record's exact goal text and binding, the family
step cap and `screenshot+ax` -- the same episode loop, record schema and
auto-termination rule as `guiexp_android.runner.run_episode`, re-pointed
at a per-worker emulator through the `android_env` module constants.

The existing modules are imported, never edited; the port/AVD constants
are assigned from the outside because `AndroidWorldEnv` reads them at
module scope and this host runs several emulators beside each other.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from pathlib import Path

from .. import android_env
from ..actions import ActionError, parse_action
from ..agent import AndroidAgent
from ..compiler import binding_to_params
from ..conditions import build_prompt
from ..explore import step_cap
from . import CELLS
from .transport import RetryingClient

MAX_PARSE_RETRIES = 3  # runner.py's cap, unchanged

# The per-worker emulator plan (console ports even, adb = console + 1).
WORKER_CONSOLE_BASE = 8620
WORKER_GRPC_BASE = 8630
BOOT_EXTRA = ("-no-window", "-no-audio", "-no-boot-anim", "-no-snapshot",
              "-no-metrics")


def configure_worker_ports(worker: int) -> None:
    """Point the android_env module constants at this worker's emulator."""
    console = WORKER_CONSOLE_BASE + 2 * worker
    grpc = WORKER_GRPC_BASE + worker
    android_env.AVD_NAME = f"guiexpPair-w{worker}"
    android_env.CONSOLE_PORT = console
    android_env.GRPC_PORT = grpc
    android_env.SERIAL = f"emulator-{console}"
    android_env.BOOT_CMD = (
        str(android_env.EMULATOR_PATH),
        "-avd", android_env.AVD_NAME,
        "-port", str(console),
        *BOOT_EXTRA,
        "-grpc", str(grpc),
    )


def load_items(deploy_root: Path) -> list[dict]:
    """The 180 (cell, use) items, in a fixed order, from the deploy records."""
    items = []
    for model, family in CELLS:
        slug = model.replace("/", "_")
        deploy = json.loads(
            (deploy_root / slug / family / "deploy.json").read_text())
        assert deploy["model"] == model and deploy["n"] == 30, (slug, family)
        for i, use in enumerate(deploy["uses"]):
            items.append({
                "model": model, "family": family, "use_index": i,
                "goal": use["goal"], "binding": use["expected"],
                # the program route's own record for this same binding
                "deploy": {k: use[k] for k in
                           ("success", "error_type", "retries", "tokens",
                            "cost_usd", "wall_s")},
            })
    return items


def shard(items: list[dict], worker: int, workers: int) -> list[dict]:
    return [it for i, it in enumerate(items) if i % workers == worker]


# ------------------------------------------------------------- one episode


def run_replay_episode(item: dict, client, env, out_dir: Path) -> dict:
    """One full discover episode over a deploy-served binding.

    Same loop, records and auto-termination as `runner.run_episode`; the
    differences are exactly the pairing ones: the task instance is built
    from the deploy record's ground-truth binding (`binding_to_params`) and
    the goal block wraps the deploy record's natural-language goal.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectory.jsonl"
    family, model = item["family"], item["model"]
    params = binding_to_params(family, item["binding"])
    task = android_env.AndroidTask(
        family=family, condition="discover",
        seed=item["use_index"], params=params)
    task_id = f"{family}__paired__u{item['use_index']:02d}"
    goal_prompt = build_prompt("discover", family, item["goal"])

    agent = AndroidAgent(model=model, obs_mode="screenshot+ax", client=client)
    cap = step_cap(family)

    records: list[dict] = []
    totals = {"prompt_tokens": 0, "cached_tokens": 0,
              "completion_tokens": 0, "cost_usd": 0.0}

    def persist(rec: dict) -> None:
        with traj_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    def _record_step(step, reply, usage, obs, retry=None):
        for key in totals:
            value = usage.get(key)
            if key == "cost_usd":
                totals[key] += value or 0.0
            else:
                totals[key] += value or 0
        try:
            action = parse_action(reply).json_str()
        except ActionError:
            action = None
        shot_file = None
        if obs.get("screenshot_b64"):
            shot_file = f"step_{step:03d}.png"
            (out_dir / shot_file).write_bytes(
                base64.b64decode(obs["screenshot_b64"]))
        rec = {
            "step": step,
            "action_raw": reply,
            "action": action,
            "usage": usage,
            "obs_meta": {
                "url": obs.get("url"),
                "screenshot_file": shot_file,
                "ax_chars": len(obs.get("ax_tree_text") or ""),
                "last_action_error": obs.get("last_action_error"),
            },
        }
        if retry is not None:
            rec["retry"] = retry
        records.append(rec)
        persist(rec)

    t0 = time.time()
    # Bounded reset retry, same rationale as ProgramRunner._run_once:
    # android_world's setup does raw adb clears whose glob rm can transiently
    # fail; that is harness noise, not an episode measurement.
    obs = None
    reset_error: str | None = None
    for attempt in range(2):
        try:
            obs = env.reset(task)
            reset_error = None
            break
        except Exception as exc:  # noqa: BLE001
            reset_error = f"reset: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
            time.sleep(3.0)
    if reset_error is not None or obs is None:
        raise RuntimeError(reset_error or "reset failed")
    done, step, reward = False, 0, 0.0
    while step < cap and not done:
        attempts = 0
        while True:
            first_turn = step == 0 and attempts == 0
            reply, usage = agent.act(goal_prompt if first_turn else None, obs)
            attempts += 1
            try:
                parse_action(reply)
                break
            except ActionError as exc:
                if attempts > MAX_PARSE_RETRIES:
                    break
                _record_step(step + 1, reply, usage, obs, retry=attempts)
                obs = {
                    **obs,
                    "last_action_error": (
                        f"{type(exc).__name__}: {exc} Your reply must be"
                        ' exactly one line: action: {"action_type": ...}'
                    ),
                }
        obs, done, reward = env.step(reply)
        step += 1
        _record_step(step, reply, usage, obs)
    success = reward >= 1.0

    final = {
        "success": success,
        "total_tokens": totals["prompt_tokens"] + totals["completion_tokens"],
        "total_cost_usd": round(totals["cost_usd"], 8),
        "condition": "discover",
        "family": family,
        "seed": item["use_index"],
        "model": model,
        "obs_mode": "screenshot+ax",
        "task_id": task_id,
        "steps": sum(1 for r in records if "retry" not in r),
        "model_calls": len(records),
        "record_type": "final",
        "paired_replay": True,
    }
    records.append(final)
    persist(final)
    return final


def run_item(item: dict, client, env, out_root: Path) -> dict:
    """One item end to end; returns the paired record (never raises)."""
    slug = item["model"].replace("/", "_")
    out_dir = out_root / slug / item["family"] / f"use_{item['use_index']:02d}"
    if (out_dir / "summary.json").exists():  # idempotent resume
        return json.loads((out_dir / "summary.json").read_text())
    traj_path = out_dir / "trajectory.jsonl"
    traj_path.parent.mkdir(parents=True, exist_ok=True)
    traj_path.write_text("")
    t0 = time.time()
    error = None
    final = None
    try:
        final = run_replay_episode(item, client, env, out_dir)
    except Exception as exc:  # noqa: BLE001 - the failure shape is data
        error = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    summary = {
        "cell": f"{slug}/{item['family']}",
        "model": item["model"],
        "family": item["family"],
        "use_index": item["use_index"],
        "goal": item["goal"],
        "binding": item["binding"],
        "deploy_use": item["deploy"],
        "success": bool(final["success"]) if final else False,
        "steps": final.get("steps") if final else None,
        "model_calls": final.get("model_calls") if final else None,
        "total_tokens": final.get("total_tokens") if final else None,
        "total_cost_usd": final.get("total_cost_usd") if final else None,
        "wall_s": round(time.time() - t0, 2),
        "error": error,
        "record_type": "paired_replay_summary",
    }
    # Fold the per-call usage out of the trajectory (cached included).
    usage = {"prompt_tokens": 0, "cached_tokens": 0,
             "completion_tokens": 0, "cost_usd": 0.0, "calls": 0}
    if traj_path.exists():
        for line in traj_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            u = rec.get("usage") or {}
            if not u:
                continue
            usage["calls"] += 1
            usage["prompt_tokens"] += u.get("prompt_tokens") or 0
            usage["cached_tokens"] += u.get("cached_tokens") or 0
            usage["completion_tokens"] += u.get("completion_tokens") or 0
            usage["cost_usd"] += u.get("cost_usd") or 0.0
    summary["usage"] = usage
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


# ------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--worker", type=int, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--deploy-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    configure_worker_ports(args.worker)
    out_root = args.out_root
    out_root.mkdir(parents=True, exist_ok=True)
    ledger = out_root / f"runs_worker{args.worker}.jsonl"

    items = shard(load_items(args.deploy_root), args.worker, args.workers)
    print(f"[w{args.worker}] {len(items)} runs on {android_env.AVD_NAME} "
          f"({android_env.SERIAL})", flush=True)

    if not android_env.emulator_is_running():
        android_env.boot_emulator(timeout=600.0)
        print(f"[w{args.worker}] emulator booted", flush=True)
    client = RetryingClient()
    # Explicit ports: the class defaults bind 5554/8554 at definition time,
    # so re-pointing the module constants alone would not move the env.
    env = android_env.AndroidWorldEnv(
        console_port=android_env.CONSOLE_PORT,
        grpc_port=android_env.GRPC_PORT,
        boot_if_needed=False,
    )

    try:
        for n, item in enumerate(items, 1):
            if not android_env.emulator_is_running():  # crashed mid-shard
                print(f"[w{args.worker}] emulator gone; rebooting", flush=True)
                try:
                    env.close()
                except Exception:  # noqa: BLE001
                    pass
                android_env.boot_emulator(timeout=600.0)
                env = android_env.AndroidWorldEnv(
                    console_port=android_env.CONSOLE_PORT,
                    grpc_port=android_env.GRPC_PORT,
                    boot_if_needed=False,
                )
            t0 = time.time()
            summary = run_item(item, client, env, out_root)
            with ledger.open("a") as fh:
                fh.write(json.dumps(summary) + "\n")
            ok = "ok" if summary["success"] else (
                "ERR:" + str(summary["error"])[:60] if summary["error"] else "fail")
            print(f"[w{args.worker}] {n}/{len(items)} "
                  f"{summary['cell']} u{summary['use_index']:02d} {ok} "
                  f"{summary['steps']} steps {summary['wall_s']}s "
                  f"({time.time() - t0:.0f}s)", flush=True)
    finally:
        try:
            env.close()
        except Exception:  # noqa: BLE001
            pass
    print(f"[w{args.worker}] shard complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
