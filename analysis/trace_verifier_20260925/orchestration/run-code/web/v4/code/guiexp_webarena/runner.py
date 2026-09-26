"""CLI: run one WebArena episode and write a JSONL trajectory.

Trajectory format is IDENTICAL to guiexp_android/runner.py (which is
identical to web guiexp/runner.py): one JSON record per step

    {"step": 0, "action_raw": "...", "action": "{...}",
     "usage": {...}, "obs_meta": {"url":..., "screenshot_file":...,
                                  "ax_chars":..., "last_action_error":...}}

plus same-step "retry" records for unparsable replies, and one final
record with the same field set. Success = the family checker (DB truth).
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from .actions import ActionError, is_done, parse_action, render
from .agent import WebAgent
from .conditions import build_prompt
from .family import FAMILY, task_for

MAX_PARSE_RETRIES = 3


def run_episode(
    family: str,
    condition: str,
    seed: int,
    model: str,
    obs_mode: str,
    max_steps: int,
    out_dir: Path | str,
    client=None,
    env=None,
    doc_text: str | None = None,
    goal_prefix: str | None = None,
    close_env: bool = True,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectory.jsonl"

    own_env = None
    if env is None:
        from .env import WebArenaEnv

        own_env = env = WebArenaEnv()

    agent = WebAgent(model=model, obs_mode=obs_mode, client=client)
    task = task_for(family, condition, seed, env=env)
    goal_prompt = build_prompt(condition, family, task.params, doc_text=doc_text, task=task)
    if goal_prefix:
        goal_prompt = goal_prefix.rstrip("\n") + "\n\n" + goal_prompt

    records: list[dict] = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    total_cached = 0
    traj_path.write_text("")

    def persist(rec: dict) -> None:
        with traj_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    def _record_step(step, reply, usage, obs, retry=None):
        nonlocal total_prompt, total_completion, total_cost, total_cached
        try:
            action = parse_action(reply)
            action_str = render(action)
        except ActionError:
            action = None
            action_str = None
        shot_file = None
        if obs.get("screenshot_b64"):
            shot_file = f"step_{step:03d}.png"
            (out_dir / shot_file).write_bytes(base64.b64decode(obs["screenshot_b64"]))
        total_prompt += usage.get("prompt_tokens") or 0
        total_completion += usage.get("completion_tokens") or 0
        total_cached += usage.get("cached_tokens") or 0
        total_cost += usage.get("cost_usd") or 0.0
        rec = {
            "step": step,
            "action_raw": reply,
            "action": action_str,
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
        return rec

    try:
        obs = env.reset(task)
        success = None

        if condition == "floor":
            # No task: one trivial observation, one call, exit. This cost is
            # the harness floor the accounting subtracts.
            reply, usage = agent.act(goal_prompt, obs)
            _record_step(0, reply, usage, obs)
        else:
            done = False
            step = 0
            while step < max_steps and not done:
                attempts = 0
                action = None
                reply = ""
                usage = {}
                while True:
                    first_turn = step == 0 and attempts == 0
                    reply, usage = agent.act(goal_prompt if first_turn else None, obs)
                    attempts += 1
                    try:
                        action = parse_action(reply)
                        break
                    except ActionError as exc:
                        if attempts > MAX_PARSE_RETRIES:
                            break  # give up parsing this reply; it is charged
                            # and reported back as a failed action below
                        _record_step(step + 1, reply, usage, obs, retry=attempts)
                        obs = {
                            **obs,
                            "last_action_error": (
                                f"{type(exc).__name__}: {exc} Your reply must be"
                                ' exactly one line: action: {"action_type": ...}'
                            ),
                        }
                if action is None:
                    # An unparsable reply after all retries: the call is
                    # charged, no action executes, the episode continues
                    # with the error reported back (the Android arm's env
                    # swallows the bad reply the same way).
                    obs = {
                        **obs,
                        "last_action_error": (
                            "ActionError: the reply was not a parsable action"
                            f" after {attempts} attempt(s); reply was: "
                            f"{(reply or '')[:120]!r}"
                        ),
                    }
                    _record_step(step + 1, reply, usage, obs)
                    step += 1
                    continue
                obs, done, _reward = env.step(action)
                step += 1
                _record_step(step, reply, usage, obs)
                if is_done(action):
                    break
            success = env.reward(task) >= 1.0

        final = {
            "success": success,
            "total_tokens": total_prompt + total_completion,
            "total_cost_usd": round(total_cost, 8),
            "condition": condition,
            "family": family,
            "seed": seed,
            "model": model,
            "obs_mode": obs_mode,
            "task_id": task.task_id,
            "steps": sum(1 for r in records if "retry" not in r),
            "model_calls": len(records),
            "record_type": "final",
        }
        if goal_prefix:
            final["goal_prefix"] = goal_prefix
        if doc_text is not None:
            final["doc_chars"] = len(doc_text)
        records.append(final)
        persist(final)
    finally:
        # close exactly once: either the caller-owned env (close_env=True was
        # the explicit request) or the env this function created itself.
        if close_env:
            env.close()

    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="run one WebArena episode")
    parser.add_argument("--family", default=FAMILY)
    parser.add_argument("--condition", default="discover")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--out", required=True)
    parser.add_argument("--doc", default=None)
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    doc_text = Path(args.doc).read_text() if args.doc else None
    final = run_episode(
        family=args.family, condition=args.condition, seed=args.seed,
        model=args.model, obs_mode=args.obs_mode, max_steps=args.max_steps,
        out_dir=args.out, doc_text=doc_text,
    )
    print(json.dumps(final, indent=1))
    print(f"trajectory: {Path(args.out) / 'trajectory.jsonl'}")
    return 0 if final.get("success") in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
