"""Exploration stage: 3 building episodes (seeds 1,2,3) with reflection retries.

The WebArena mirror of guiexp_android/explore.py, same numbers: N=3
building instances, up to N_REF=2 reflection retries per failed episode
(one reflection call writes a lesson; the retry runs with it in front of
the goal). Every call, retries included, is charged.
"""

from __future__ import annotations

import json
from pathlib import Path

from .runner import run_episode

BUILDING_SEEDS = (1, 2, 3)
TEST_SEED = 0
N_REF_DEFAULT = 2
STEP_CAP_MAX = 50
COMPLEXITY = {"CommentPost": 2.0}  # comment task: forum page -> post -> box -> submit
STEP_CAP = 20  # ceil(10 x 2.0) = 20

REFLECTION_SYSTEM = (
    "You review a failed attempt at a web GUI task and write one short "
    "lesson for the next attempt. You reply with the lesson only."
)

LESSON_HEADER = "A previous attempt at this task failed. The lesson from that attempt:"


def step_cap(family: str) -> int:
    if family not in COMPLEXITY:
        raise ValueError(f"no complexity recorded for family {family!r}")
    return min(STEP_CAP_MAX, STEP_CAP)


def read_final(trajectory_jsonl) -> dict:
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    finals = [r for r in records if r.get("record_type") == "final"]
    if not finals:
        raise ValueError(f"{trajectory_jsonl}: no final record")
    return finals[-1]


def episode_usage(trajectory_jsonl) -> dict:
    """Raw/cached/completion tokens and cost of one recorded episode, plus
    the per-call records (in call order) the cold-host unit needs."""
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


def build_reflection_prompt(goal_text: str, trajectory_jsonl) -> list[dict]:
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    lines = [
        "An agent was driving a forum website in a browser to carry out this",
        "task and did not reach the goal.",
        "",
        f"TASK: {goal_text.strip()}",
        "",
        "ITS ACTION HISTORY (one line per step: the action it emitted, the",
        "page URL afterwards, and any error the action raised):",
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
        "Write the lesson the next attempt needs: what went wrong, and what",
        "to do differently on the pages where it went wrong. Be concrete",
        "about controls and pages; do not restate the task. At most 8 short",
        "lines. Reply with the lesson only.",
    ]
    return [
        {"role": "system", "content": REFLECTION_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def reflect(model, goal_text, trajectory_jsonl, client=None) -> dict:
    from .agent import WebAgent, _with_backoff
    from .compiler import _openai_client

    messages = build_reflection_prompt(goal_text, trajectory_jsonl)
    if client is None:
        client = _openai_client()
    response = _with_backoff(
        client.chat.completions.create, model=model, messages=messages, temperature=0.0
    )
    usage = WebAgent._usage(response)
    lesson = (response.choices[0].message.content or "").strip()
    return {"lesson": lesson, "usage": usage, "cost_usd": usage.get("cost_usd") or 0.0,
            "stage": "reflection"}


def _instance_complete(attempts: list, n_ref: int) -> bool:
    """An instance is terminal: its last attempt succeeded or the retry
    budget (n_ref) was used up."""
    if not attempts:
        return False
    return bool(attempts[-1].get("success")) or len(attempts) >= 1 + n_ref


def run_building_episode(
    family, seed, model, out_dir, obs_mode="screenshot+ax",
    n_ref=N_REF_DEFAULT, client=None, env=None, goal_for_seed=None,
    resume: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    from .family import task_for, goal_text

    state_path = out_dir / f"s{seed}_instance.json"
    attempts: list[dict] = []
    reflections: list[dict] = []
    goal = None
    cap = step_cap(family)

    if resume and state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
        except Exception:  # noqa: BLE001 - a truncated file is no resume base
            state = None
        if state and state.get("family") == family and state.get("seed") == seed \
                and all(Path(a["trajectory"]).is_file() for a in state.get("attempts") or []):
            attempts = state.get("attempts") or []
            reflections = state.get("reflections") or []
            goal = state.get("goal")
            cap = state.get("step_cap") or cap
            if _instance_complete(attempts, n_ref):
                print(f"  seed {seed}: recovered {len(attempts)} attempt(s) "
                      f"from disk, {'ok' if attempts[-1]['success'] else 'FAILED'}",
                      flush=True)
                return {
                    "family": family, "seed": seed, "goal": goal, "step_cap": cap,
                    "attempts": attempts, "reflections": reflections,
                    "success": bool(attempts[-1]["success"]),
                    "best_trajectory": next(
                        (a["trajectory"] for a in reversed(attempts) if a["success"]),
                        attempts[-1]["trajectory"]),
                }
            print(f"  seed {seed}: resuming after attempt {len(attempts) - 1}",
                  flush=True)

    if goal is None:
        task = task_for(family, "discover", seed, env=env)
        goal = goal_text(task)
        if goal_for_seed:
            goal = goal_for_seed.get(seed, goal)

    def _persist():
        state_path.write_text(json.dumps({
            "family": family, "seed": seed, "goal": goal, "step_cap": cap,
            "attempts": attempts, "reflections": reflections,
        }, indent=1, default=str))

    if not attempts:
        final = run_episode(
            family=family, condition="discover", seed=seed, model=model,
            obs_mode=obs_mode, max_steps=cap, out_dir=out_dir / f"s{seed}_a0",
            client=client, env=env, close_env=env is None,
        )
        traj = out_dir / f"s{seed}_a0" / "trajectory.jsonl"
        attempts.append({
            "attempt": 0, "trajectory": str(traj), "reused": False,
            "success": bool(final.get("success")), "steps": final.get("steps"),
            "usage": episode_usage(traj),
        })
        _persist()

    for retry in range(len(attempts), n_ref + 1):
        if attempts[-1]["success"]:
            break
        have_ref = any(r.get("for_attempt") == retry for r in reflections)
        if not have_ref:
            reflection = reflect(model, goal, attempts[-1]["trajectory"], client=client)
            reflection["for_attempt"] = retry
            reflections.append(reflection)
            _persist()
        lesson = next(r["lesson"] for r in reflections if r.get("for_attempt") == retry)
        prefix = LESSON_HEADER + "\n" + lesson
        final = run_episode(
            family=family, condition="discover", seed=seed, model=model,
            obs_mode=obs_mode, max_steps=cap, out_dir=out_dir / f"s{seed}_a{retry}",
            client=client, env=env, goal_prefix=prefix, close_env=env is None,
        )
        traj = out_dir / f"s{seed}_a{retry}" / "trajectory.jsonl"
        attempts.append({
            "attempt": retry, "trajectory": str(traj), "reused": False,
            "success": bool(final.get("success")), "steps": final.get("steps"),
            "usage": episode_usage(traj),
        })
        _persist()

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


def stage_totals(instances) -> dict:
    prompt = cached = completion = 0
    cost = 0.0
    episodes = retries = reflection_calls = reused = 0
    for instance in instances:
        for attempt in instance["attempts"]:
            usage = attempt["usage"]
            prompt += usage["prompt_tokens"]
            cached += usage["cached_tokens"]
            completion += usage["completion_tokens"]
            cost += usage["cost_usd"]
            episodes += 1
            retries += 1 if attempt["attempt"] > 0 else 0
            reused += 1 if attempt["reused"] else 0
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
        "reused_episodes": reused,
        "reflection_calls": reflection_calls,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(cost, 8),
    }


def run_exploration(
    family, model, out_dir, seeds=BUILDING_SEEDS, obs_mode="screenshot+ax",
    n_ref=N_REF_DEFAULT, client=None, env=None, resume: bool = False,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    instances = []
    for seed in seeds:
        instance = run_building_episode(
            family=family, seed=seed, model=model, out_dir=out_dir,
            obs_mode=obs_mode, n_ref=n_ref, client=client, env=env,
            resume=resume,
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
