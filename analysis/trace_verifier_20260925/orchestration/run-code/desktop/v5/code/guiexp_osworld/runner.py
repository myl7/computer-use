"""CLI: run one OSWorld episode and write a JSONL trajectory + screenshots.

Trajectory schema is IDENTICAL to guiexp_android/runner.py (which is
field-for-field the web guiexp one): one JSON record per step

    {"step": 0, "action_raw": "...", "action": "{\"action_type\":...}",
     "usage": {...}, "obs_meta": {"url": <active window title>,
                                  "screenshot_file": ..., "ax_chars": ...,
                                  "last_action_error": ...}}

(plus same-step "retry" records) and one final record

    {"success", "total_tokens", "total_cost_usd", "condition", "family",
     "seed", "model", "obs_mode", "task_id", "steps", "model_calls",
     "record_type": "final"}

``obs_mode`` is always "screenshot+ax" on this arm.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from . import guest_env
from .actions import ActionError, parse_action, render
from .agent import OBS_MODES, OSWorldAgent
from .conditions import CONDITIONS, FAMILIES, build_prompt

DEFAULT_OUT_ROOT = guest_env.RESULTS_ROOT

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
    keep_container: bool = False,
    doc_text: str | None = None,
    goal_prefix: str | None = None,
    close_env: bool = True,
) -> dict:
    """Run one episode; returns the final record and writes the trajectory.

    ``doc_text`` carries the compiled operation document for the ``doc``
    condition; ``goal_prefix`` is the reflection-lesson path of the
    exploration stage; ``close_env`` stays True for one-episode-per-process
    use and False for drivers that reuse ONE env across many episodes.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectory.jsonl"

    own_env = None
    if env is None:
        own_env = env = guest_env.OSWorldEnv()

    agent = OSWorldAgent(model=model, obs_mode=obs_mode, client=client)
    task = guest_env.get_task(family, condition, seed)
    goal_prompt = build_prompt(condition, family, guest_env.task_goal(task),
                               doc_text=doc_text)
    if goal_prefix:
        goal_prompt = goal_prefix.rstrip("\n") + "\n\n" + goal_prompt

    records: list[dict] = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    traj_path.write_text("")

    def persist(rec: dict) -> None:
        with traj_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    def _record_step(step: int, reply: str, usage: dict, obs: dict,
                     retry: int | None = None) -> dict:
        nonlocal total_prompt, total_completion, total_cost
        try:
            action = render(parse_action(reply))
        except ActionError:
            action = None
        shot_file = None
        if obs.get("screenshot_b64"):
            shot_file = f"step_{step:03d}.png"
            (out_dir / shot_file).write_bytes(base64.b64decode(obs["screenshot_b64"]))
        total_prompt += usage.get("prompt_tokens") or 0
        total_completion += usage.get("completion_tokens") or 0
        total_cost += usage.get("cost_usd") or 0.0
        rec = {
            "step": step,
            "action_raw": reply,
            "action": action,
            "usage": usage,
            "obs_meta": {
                "url": obs.get("url"),
                "screenshot_file": shot_file,
                "ax_chars": len(obs.get("ax_tree_text") or ""),
                "ax_elements": obs.get("ax_elements"),
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
            reply, usage = agent.act(goal_prompt, obs)
            _record_step(0, reply, usage, obs)
        else:
            done = False
            step = 0
            reward = 0.0
            while step < max_steps and not done:
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
        if close_env:
            env.close()
        if own_env is not None and not keep_container:
            own_env.stop_container()

    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", default="CalcTableSave", choices=FAMILIES)
    parser.add_argument("--condition", default="discover", choices=CONDITIONS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax", choices=OBS_MODES)
    parser.add_argument("--max-steps", type=int, default=22)
    parser.add_argument("--out", default=None)
    parser.add_argument("--keep-container", action="store_true")
    parser.add_argument("--doc", default=None,
                        help="path to the compiled operation document (--condition doc)")
    args = parser.parse_args()

    if args.condition == "doc" and not args.doc:
        parser.error("--condition doc needs --doc <path>")
    doc_text = Path(args.doc).read_text() if args.doc else None

    out_dir = Path(args.out) if args.out else (
        DEFAULT_OUT_ROOT / f"{args.family}_{args.condition}_s{args.seed}_{args.model.replace('/', '-')}"
    )
    final = run_episode(
        family=args.family, condition=args.condition, seed=args.seed,
        model=args.model, obs_mode=args.obs_mode, max_steps=args.max_steps,
        out_dir=out_dir, keep_container=args.keep_container, doc_text=doc_text,
    )
    print(json.dumps(final, indent=1))
    print(f"trajectory: {out_dir / 'trajectory.jsonl'}")
    return 0 if final.get("success") in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
