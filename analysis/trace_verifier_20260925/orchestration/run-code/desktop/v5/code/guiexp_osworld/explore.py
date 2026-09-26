"""Exploration stage: N=3 building episodes per family, reflection retries.

Ported from guiexp_android/explore.py: seeds 1, 2, 3 (seed 0 stays the test
instance), up to N_ref=2 reflection retries per failed episode, step cap
ceil(10 x complexity) capped at 50 (complexity is OUR assignment for these
families, recorded in families.py). There is no t12_grid to reuse on this
arm, so every building episode is fresh.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import families, guest_env
from .runner import run_episode

BUILDING_SEEDS = (1, 2, 3)
TEST_SEED = 0
N_REF_DEFAULT = 2
STEP_CAP_MAX = 50

REFLECTION_SYSTEM = (
    "You review a failed attempt at a Linux desktop GUI task and write one "
    "short lesson for the next attempt. You reply with the lesson only."
)

RESULTS_ROOT = guest_env.RESULTS_ROOT


def step_cap(family: str) -> int:
    return min(STEP_CAP_MAX, math.ceil(10 * families.COMPLEXITY[family]))


def read_final(trajectory_jsonl: Path | str) -> dict:
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{trajectory_jsonl}: no final record")
    return finals[-1]


def episode_usage(trajectory_jsonl: Path | str) -> dict:
    """Raw/cached/completion tokens and cost of one recorded episode, plus
    the per-call records the cold-host unit needs."""
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    prompt = cached = completion = 0
    cost = 0.0
    calls = 0
    call_records: list[dict] = []
    for rec in records:
        usage = rec.get("usage")
        if not usage:
            continue
        calls += 1
        prompt += usage.get("prompt_tokens") or 0
        completion += usage.get("completion_tokens") or 0
        cached += usage.get("cached_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        call_records.append({
            "call": calls,
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "cached_tokens": usage.get("cached_tokens"),
            "completion_tokens": usage.get("completion_tokens") or 0,
            "cost_usd": usage.get("cost_usd"),
        })
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(cost, 8),
        "model_calls": calls,
        "calls_detail": call_records,
    }


def build_reflection_prompt(goal: str, trajectory_jsonl: Path | str) -> list[dict]:
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    lines = [
        "An agent was driving a Linux desktop to carry out this task and did",
        "not reach the goal.",
        "",
        f"TASK: {goal.strip()}",
        "",
        "ITS ACTION HISTORY (one line per step: the action it emitted, the",
        "active window afterwards, and any error the action raised):",
    ]
    for rec in records:
        if rec.get("record_type") == "final" or rec.get("action_raw") is None:
            continue
        meta = rec.get("obs_meta") or {}
        line = f"  step {rec.get('step')}: {(rec.get('action') or rec['action_raw']).strip()[:200]}"
        if meta.get("url"):
            line += f"  -> {meta['url']}"
        if meta.get("last_action_error"):
            line += f"  ERROR: {str(meta['last_action_error'])[:160]}"
        if rec.get("retry"):
            line += "  (unparsable reply, re-asked)"
        lines.append(line)
    lines += [
        "",
        "Write the lesson the next attempt needs: what went wrong, and what to",
        "do differently on the screens where it went wrong. Be concrete about",
        "controls and screens; do not restate the task. At most 8 short lines.",
        "Reply with the lesson only.",
    ]
    return [
        {"role": "system", "content": REFLECTION_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


LESSON_HEADER = "A previous attempt at this task failed. The lesson from that attempt:"


def reflect(model: str, goal: str, trajectory_jsonl: Path | str, client=None) -> dict:
    """One charged reflection call."""
    from .agent import OSWorldAgent
    from .compiler import _openai_client

    messages = build_reflection_prompt(goal, trajectory_jsonl)
    if client is None:
        client = _openai_client()
    response = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
    usage = OSWorldAgent._usage(response)
    lesson = (response.choices[0].message.content or "").strip()
    return {
        "lesson": lesson,
        "usage": usage,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "stage": "reflection",
    }


def run_building_episode(
    family: str,
    seed: int,
    model: str,
    out_dir: Path | str,
    obs_mode: str = "screenshot+ax",
    n_ref: int = N_REF_DEFAULT,
    client=None,
    env=None,
) -> dict:
    """One building instance: the discover episode plus its reflection retries."""
    out_dir = Path(out_dir)
    task = guest_env.get_task(family, "discover", seed)
    goal = guest_env.task_goal(task)
    cap = step_cap(family)

    attempts: list[dict] = []
    reflections: list[dict] = []

    final = run_episode(
        family=family, condition="discover", seed=seed, model=model,
        obs_mode=obs_mode, max_steps=cap, out_dir=out_dir / f"s{seed}_a0",
        client=client, env=env, close_env=False,
    )
    traj = out_dir / f"s{seed}_a0" / "trajectory.jsonl"
    attempts.append({
        "attempt": 0,
        "trajectory": str(traj),
        "success": bool(final.get("success")),
        "steps": final.get("steps"),
        "usage": episode_usage(traj),
    })

    for retry in range(1, n_ref + 1):
        if attempts[-1]["success"]:
            break
        reflection = reflect(model, goal, attempts[-1]["trajectory"], client=client)
        reflection["for_attempt"] = retry
        reflections.append(reflection)
        prefix = LESSON_HEADER + "\n" + reflection["lesson"]
        final = run_episode(
            family=family, condition="discover", seed=seed, model=model,
            obs_mode=obs_mode, max_steps=cap, out_dir=out_dir / f"s{seed}_a{retry}",
            client=client, env=env, goal_prefix=prefix, close_env=False,
        )
        traj = out_dir / f"s{seed}_a{retry}" / "trajectory.jsonl"
        attempts.append({
            "attempt": retry,
            "trajectory": str(traj),
            "success": bool(final.get("success")),
            "steps": final.get("steps"),
            "usage": episode_usage(traj),
        })

    return {
        "family": family,
        "seed": seed,
        "goal": goal,
        "step_cap": cap,
        "attempts": attempts,
        "reflections": reflections,
        "success": attempts[-1]["success"],
        "best_trajectory": next(
            (a["trajectory"] for a in reversed(attempts) if a["success"]),
            attempts[-1]["trajectory"],
        ),
    }


def stage_totals(instances: list[dict]) -> dict:
    prompt = cached = completion = 0
    cost = 0.0
    episodes = retries = reflection_calls = 0
    for instance in instances:
        for attempt in instance["attempts"]:
            usage = attempt["usage"]
            prompt += usage["prompt_tokens"]
            cached += usage["cached_tokens"]
            completion += usage["completion_tokens"]
            cost += usage["cost_usd"]
            episodes += 1
            retries += 1 if attempt["attempt"] > 0 else 0
        for reflection in instance["reflections"]:
            usage = reflection["usage"]
            prompt += usage.get("prompt_tokens") or 0
            cached += usage.get("cached_tokens") or 0
            completion += usage.get("completion_tokens") or 0
            cost += reflection["cost_usd"]
            reflection_calls += 1
    return {
        "episodes": episodes,
        "retry_episodes": retries,
        "reflection_calls": reflection_calls,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(cost, 8),
    }


def run_exploration(
    family: str,
    model: str,
    out_dir: Path | str,
    seeds: tuple = BUILDING_SEEDS,
    obs_mode: str = "screenshot+ax",
    n_ref: int = N_REF_DEFAULT,
    client=None,
    env=None,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    instances = []
    for seed in seeds:
        instance = run_building_episode(
            family=family, seed=seed, model=model, out_dir=out_dir,
            obs_mode=obs_mode, n_ref=n_ref, client=client, env=env,
        )
        instances.append(instance)
        print(f"  seed {seed}: {len(instance['attempts'])} episode(s), "
              f"{'ok' if instance['success'] else 'FAILED'}", flush=True)
    record = {
        "family": family,
        "model": model,
        "seeds": list(seeds),
        "test_seed": TEST_SEED,
        "n_ref": n_ref,
        "step_cap": step_cap(family),
        "instances": instances,
        "totals": stage_totals(instances),
        "record_type": "exploration",
    }
    (out_dir / "exploration.json").write_text(json.dumps(record, indent=1, default=str))
    return record
