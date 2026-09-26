"""Exploration stage of the AutoRPA-style build protocol: N=3 building
episodes per task type, with reflection retries.

AutoRPA (Section 3.2, page 4; Implementation Details, page 7) samples N = 3
building instances per task type, seeds 1, 2 and 3, and reserves seed 0 for
the test instance (Appendix B, page 12). A failed building episode is retried
up to N_ref = 2 times: a reflection agent reads the failed episode's action
history and writes a short lesson, and the ReAct agent runs again with that
lesson in front of the goal. Every call, retries included, is charged.

Two protocol details this module owns:

  * the per-family step cap, ceil(10 * complexity) capped at 50, taken from
    android_world's own ``complexity`` attribute (docs/autorpa-replication-
    protocol.md section 5.1);
  * reuse of already-recorded discover episodes. Seeds 1 and 2 exist in
    ``t12_grid`` for the three original families; a reused episode is charged
    at its recorded usage and never re-run, so only the new seed costs money.
    Nothing under experimental-results is ever rewritten by this module: a
    reused trajectory is read where it lies.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.explore \
        --family ContactsAddContact --model z-ai/glm-5.3-flash \
        --out experimental-results/guiexp_android/t16_build/.../explore
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import android_env
from .runner import run_episode

BUILDING_SEEDS = (1, 2, 3)  # seed 0 stays the test instance
TEST_SEED = 0
N_REF_DEFAULT = 2  # reflection retries per failed building episode
STEP_CAP_MAX = 50

# android_world's own per-task complexity, the multiplier behind the step cap.
# Every value here is the ``complexity`` class attribute of the family's own
# TaskEval, read from third-party/android_world; none is guessed.
COMPLEXITY = {
    "MarkorCreateNote": 1.6,
    "MarkorDeleteNote": 1.0,
    "ContactsAddContact": 1.2,
    "SimpleCalendarAddOneEvent": 3.4,
    "MarkorMoveNote": 1.4,
    "OsmAndFavorite": 1.3,     # osmand.OsmAndFavorite.complexity -> cap 13
    "OsmAndMarker": 2.0,       # osmand.OsmAndMarker.complexity   -> cap 20
    "FilesMoveFile": 2.0,      # files.FilesMoveFile.complexity   -> cap 20
}

REFLECTION_SYSTEM = (
    "You review a failed attempt at an Android GUI task and write one short "
    "lesson for the next attempt. You reply with the lesson only."
)

RESULTS_ROOT = android_env.REPO_ROOT / "experimental-results" / "guiexp_android"


def step_cap(family: str) -> int:
    """ceil(10 x complexity), capped at 50 (AutoRPA Appendix B, page 12)."""
    if family not in COMPLEXITY:
        raise ValueError(f"no complexity recorded for family {family!r}")
    return min(STEP_CAP_MAX, math.ceil(10 * COMPLEXITY[family]))


# ------------------------------------------------------------------- reuse


def existing_trajectory(model: str, family: str, seed: int,
                        grid_root: Path | None = None) -> Path | None:
    """The recorded t12_grid discover episode for this cell, if there is one."""
    root = grid_root or (RESULTS_ROOT / "t12_grid")
    path = root / model.replace("/", "_") / f"discover__{family}__s{seed}" / "trajectory.jsonl"
    return path if path.is_file() else None


def read_final(trajectory_jsonl: Path | str) -> dict:
    """The final record of a runner trajectory (its charged totals)."""
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
    """Raw / cached / completion tokens and cost of one recorded episode.

    Kept in raw units so the cache-adjusted total (r = 0.20 GLM, 0.318
    DeepSeek, docs/cache-adjusted-accounting.md) can be computed later.
    """
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    prompt = cached = completion = 0
    cost = 0.0
    calls = 0
    for rec in records:
        usage = rec.get("usage")
        if not usage:
            continue
        calls += 1
        prompt += usage.get("prompt_tokens") or 0
        completion += usage.get("completion_tokens") or 0
        cached += usage.get("cached_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cost_usd": round(cost, 8),
        "model_calls": calls,
    }


# -------------------------------------------------------------- reflection


def build_reflection_prompt(goal_text: str, trajectory_jsonl: Path | str) -> list[dict]:
    """One reflection call: the failed action history in, one lesson out."""
    records = [
        json.loads(line)
        for line in Path(trajectory_jsonl).read_text().splitlines()
        if line.strip()
    ]
    lines = [
        "An agent was driving an Android phone to carry out this task and did",
        "not reach the goal.",
        "",
        f"TASK: {goal_text.strip()}",
        "",
        "ITS ACTION HISTORY (one line per step: the action it emitted, the",
        "foreground activity afterwards, and any error the action raised):",
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


LESSON_HEADER = (
    "A previous attempt at this task failed. The lesson from that attempt:"
)


def reflect(
    model: str,
    goal_text: str,
    trajectory_jsonl: Path | str,
    client=None,
) -> dict:
    """One charged reflection call; returns {lesson, usage, cost_usd}."""
    from .agent import AndroidAgent
    from .compiler import _openai_client

    messages = build_reflection_prompt(goal_text, trajectory_jsonl)
    if client is None:
        client = _openai_client()
    response = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
    usage = AndroidAgent._usage(response)
    lesson = (response.choices[0].message.content or "").strip()
    return {
        "lesson": lesson,
        "usage": usage,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "stage": "reflection",
    }


# --------------------------------------------------------------- the stage


def run_building_episode(
    family: str,
    seed: int,
    model: str,
    out_dir: Path | str,
    obs_mode: str = "screenshot+ax",
    n_ref: int = N_REF_DEFAULT,
    client=None,
    env=None,
    keep_emulator: bool = True,
    reuse: Path | None = None,
) -> dict:
    """One building instance: the discover episode plus its reflection retries.

    Returns {seed, attempts: [...], reflections: [...], success, reused}. An
    attempt that succeeds ends the instance; a failure spends one reflection
    call and one retry episode, up to ``n_ref`` times.
    """
    out_dir = Path(out_dir)
    task = android_env.get_task(family, "discover", seed)
    goal = android_env.goal_text(task)
    cap = step_cap(family)

    attempts: list[dict] = []
    reflections: list[dict] = []

    if reuse is not None:
        final = read_final(reuse)
        attempts.append({
            "attempt": 0,
            "trajectory": str(reuse),
            "reused": True,
            "success": bool(final.get("success")),
            "steps": final.get("steps"),
            "usage": episode_usage(reuse),
        })
    else:
        final = run_episode(
            family=family, condition="discover", seed=seed, model=model,
            obs_mode=obs_mode, max_steps=cap, out_dir=out_dir / f"s{seed}_a0",
            client=client, env=env, keep_emulator=keep_emulator, close_env=env is None,
        )
        traj = out_dir / f"s{seed}_a0" / "trajectory.jsonl"
        attempts.append({
            "attempt": 0,
            "trajectory": str(traj),
            "reused": False,
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
            client=client, env=env, keep_emulator=keep_emulator, goal_prefix=prefix,
            close_env=env is None,
        )
        traj = out_dir / f"s{seed}_a{retry}" / "trajectory.jsonl"
        attempts.append({
            "attempt": retry,
            "trajectory": str(traj),
            "reused": False,
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
    """Charged totals of the exploration stage, episodes and reflections."""
    prompt = cached = completion = 0
    cost = 0.0
    episodes = retries = reflection_calls = 0
    reused = 0
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
    family: str,
    model: str,
    out_dir: Path | str,
    seeds: tuple = BUILDING_SEEDS,
    obs_mode: str = "screenshot+ax",
    n_ref: int = N_REF_DEFAULT,
    client=None,
    env=None,
    keep_emulator: bool = True,
    reuse_grid: bool = True,
    grid_root: Path | None = None,
) -> dict:
    """The whole exploration stage for one family and one model."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    instances = []
    for seed in seeds:
        reuse = existing_trajectory(model, family, seed, grid_root) if reuse_grid else None
        instance = run_building_episode(
            family=family, seed=seed, model=model, out_dir=out_dir, obs_mode=obs_mode,
            n_ref=n_ref, client=client, env=env, keep_emulator=keep_emulator, reuse=reuse,
        )
        instances.append(instance)
        print(
            f"  seed {seed}: {'reused ' if reuse else ''}"
            f"{len(instance['attempts'])} episode(s), "
            f"{'ok' if instance['success'] else 'FAILED'}",
            flush=True,
        )
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
    (out_dir / "exploration.json").write_text(json.dumps(record, indent=1))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--seeds", default="1,2,3", help="building seeds (seed 0 is the test instance)")
    parser.add_argument("--n-ref", type=int, default=N_REF_DEFAULT)
    parser.add_argument("--no-reuse", action="store_true", help="re-run seeds that exist in t12_grid")
    parser.add_argument("--mock", action="store_true", help="deterministic offline MockOpenAI")
    parser.add_argument("--out", required=True)
    parser.add_argument("--keep-emulator", action="store_true", default=True)
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")

    client = None
    model = args.model
    if args.mock:
        from .mock_model import MockOpenAI

        client = MockOpenAI()
        model = "mock"

    env = android_env.AndroidWorldEnv()
    try:
        record = run_exploration(
            family=args.family, model=model, out_dir=args.out,
            seeds=tuple(int(s) for s in args.seeds.split(",")),
            obs_mode=args.obs_mode, n_ref=args.n_ref, client=client, env=env,
            keep_emulator=True, reuse_grid=not args.no_reuse,
        )
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()
    print(json.dumps(record["totals"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
