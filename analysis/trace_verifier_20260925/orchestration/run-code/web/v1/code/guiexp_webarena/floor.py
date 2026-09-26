"""The floor probe: 18 no-task empty runs, averaged (per model).

The harness's own per-episode overhead (the system prompt, one observation
of a forum page, one terminating reply) with no task work. The mean PRICED
unit (cold-host applied; the single call has no predecessor so it is fully
fresh) is subtracted from every agent-side and document-side episode cost
in build_protocol; compile and extraction are never floored.

Mirrors guiexp_osworld/floor.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from .cost_ledger import FLOOR_RUNS, episode_unit, weights_for


def measure_floor(model: str, out_dir: Path | str, env, runs: int = FLOOR_RUNS,
                  client=None, family: str = "CommentPost") -> dict:
    from .runner import run_episode

    out_dir = Path(out_dir)
    if (out_dir / "floor.json").is_file():  # a finished probe from an earlier run
        return json.loads((out_dir / "floor.json").read_text())
    per_run = []
    for i in range(runs):
        traj = out_dir / f"floor_{i:02d}" / "trajectory.jsonl"
        calls = None
        if traj.is_file():  # reuse a fully-recorded run (crash resume)
            try:
                records = [json.loads(line) for line in traj.read_text().splitlines()
                           if line.strip()]
                if any(r.get("record_type") == "final" for r in records):
                    calls = [r["usage"] for r in records if r.get("usage")]
            except Exception:  # noqa: BLE001 - truncated files are re-run
                calls = None
        if calls is None:
            run_episode(
                family=family, condition="floor", seed=100 + i, model=model,
                obs_mode="screenshot+ax", max_steps=1,
                out_dir=out_dir / f"floor_{i:02d}", client=client, env=env,
                close_env=False,
            )
            records = [json.loads(line) for line in traj.read_text().splitlines()
                       if line.strip()]
            calls = [r["usage"] for r in records if r.get("usage")]
        per_run.append({
            "run": i,
            "calls": len(calls),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in calls),
            "cached_tokens": sum(c.get("cached_tokens") or 0 for c in calls),
            "completion_tokens": sum(c.get("completion_tokens") or 0 for c in calls),
            "cost_usd": round(sum(c.get("cost_usd") or 0.0 for c in calls), 8),
        })
        print(f"  floor run {i + 1}/{runs}: {per_run[-1]['prompt_tokens']} prompt"
              f" tokens, ${per_run[-1]['cost_usd']:.6f}", flush=True)

    weights = weights_for(model)
    units = [
        episode_unit(
            [{"prompt_tokens": r["prompt_tokens"], "cached_tokens": r["cached_tokens"],
              "completion_tokens": r["completion_tokens"]}],
            weights["r_c"], weights["r_o"])["unit_tokens"]
        for r in per_run
    ]
    record = {
        "model": model,
        "runs": runs,
        "per_run": per_run,
        "unit_tokens_per_run": units,
        "floor_unit_mean": round(sum(units) / len(units), 1),
        "cost_usd_mean": round(sum(r["cost_usd"] for r in per_run) / len(per_run), 8),
        "record_type": "floor",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "floor.json").write_text(json.dumps(record, indent=1))
    return record