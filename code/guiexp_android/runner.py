"""CLI: run one Android episode and write a JSONL trajectory plus screenshots.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.runner --family ContactsAddContact \
        --condition discover --seed 0 --model z-ai/glm-4.7-flash \
        --obs-mode screenshot+ax --max-steps 30

Boots the AndroidWorldAvd emulator if none is attached (and shuts it down
again if it started it). Trajectory format is IDENTICAL to web guiexp's
runner.py: one JSON record per step

    {"step": 0, "action_raw": "...", "action": "{\"action_type\":...}",
     "usage": {...}, "obs_meta": {"url": ..., "screenshot_file": ...,
                                  "ax_chars": ..., "last_action_error": ...}}

(plus same-step "retry" records when a reply fails to parse) and one final
record

    {"success": ..., "total_tokens": ..., "total_cost_usd": ...,
     "condition": ..., "family": ..., "seed": ..., "model": ...,
     "obs_mode": ..., "task_id": ..., "steps": ..., "model_calls": ...,
     "record_type": "final"}

The only field-name difference to the web side is ``family`` where the web
writes ``layout`` (the task-axis name); everything else matches so later
compile/estimate stages treat both arms identically. In obs_meta, ``url``
carries the foreground activity on this side.

With --mock (or a client passed programmatically) the deterministic
MockOpenAI is used and no network call is made.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from .actions import ActionError, parse_action
from .agent import AndroidAgent, OBS_MODES
from .conditions import CONDITIONS, FAMILIES, build_prompt
from . import android_env

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../computer-use/guiexp_android -> repo root
DEFAULT_OUT_ROOT = REPO_ROOT / "experimental-results" / "guiexp_android"

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
    keep_emulator: bool = False,
    doc_text: str | None = None,
    goal_prefix: str | None = None,
    close_env: bool = True,
) -> dict:
    """Run one episode; returns the final record and writes the trajectory.

    ``doc_text`` carries the compiled operation document for the ``doc``
    condition. ``goal_prefix`` is prepended to the goal turn and is how a
    reflection lesson from a failed building episode reaches the retry; it is
    recorded in the final record so a retry episode is identifiable.
    ``close_env`` stays True for one-episode-per-process use; a driver that
    runs many episodes on ONE env passes False and closes that env itself.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectory.jsonl"

    own_env = None
    if env is None:
        own_env = android_env.AndroidWorldEnv()
        env = own_env

    agent = AndroidAgent(model=model, obs_mode=obs_mode, client=client)
    task = android_env.get_task(family, condition, seed)
    goal_prompt = build_prompt(condition, family, android_env.goal_text(task), doc_text=doc_text)
    if goal_prefix:
        goal_prompt = goal_prefix.rstrip("\n") + "\n\n" + goal_prompt

    records: list[dict] = []
    total_prompt = total_completion = 0
    total_cost = 0.0
    # Keep completed calls even if the next request or device action fails.
    # Paid-run scheduling uses a fresh directory per episode and never retries
    # an interrupted episode automatically.
    traj_path.write_text("")

    def persist(rec: dict) -> None:
        with traj_path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()

    def _record_step(step: int, reply: str, usage: dict, obs: dict, retry: int | None = None) -> dict:
        nonlocal total_prompt, total_completion, total_cost
        try:
            action = parse_action(reply).json_str()
        except ActionError:
            action = None
        # The RAW screenshot is always what goes to disk (the model may have
        # seen the SoM-marked variant).
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
            # No task: the harness shows one trivial observation and exits.
            # Whatever this costs is harness overhead, not task work.
            reply, usage = agent.act(goal_prompt, obs)
            _record_step(0, reply, usage, obs)
        else:
            done = False
            step = 0
            reward = 0.0
            while step < max_steps and not done:
                # Ask for one action; a malformed reply is sent back as a
                # parse error and re-asked WITHOUT advancing the step
                # (retries are extra model calls on the same step, capped).
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
                                f"{type(exc).__name__}: {exc} Your reply must be exactly"
                                ' one line: action: {"action_type": ...}'
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
            # env steps taken (retry re-asks are recorded but are not steps)
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
        if own_env is not None and not keep_emulator:
            own_env.stop_emulator()  # adb emu kill, only if we booted it

    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", default="ContactsAddContact", choices=FAMILIES)
    parser.add_argument("--condition", default="discover", choices=CONDITIONS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--obs-mode", default="screenshot+ax", choices=OBS_MODES)
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--out", default=None, help="output directory (default: experimental-results/guiexp_android/<run id>)")
    parser.add_argument("--mock", action="store_true", help="use the deterministic offline MockOpenAI")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    parser.add_argument("--doc", default=None,
                        help="path to the compiled operation document (required by --condition doc)")
    args = parser.parse_args()

    if args.condition == "doc" and not args.doc:
        parser.error("--condition doc needs --doc <path to the compiled document>")
    doc_text = Path(args.doc).read_text() if args.doc else None

    client = None
    model = args.model
    if args.mock:
        from .mock_model import MockOpenAI

        client = MockOpenAI()
        model = "mock"

    out_dir = Path(args.out) if args.out else (
        DEFAULT_OUT_ROOT
        / f"{args.family}_{args.condition}_s{args.seed}_{model.replace('/', '-')}"
    )
    final = run_episode(
        family=args.family,
        condition=args.condition,
        seed=args.seed,
        model=model,
        obs_mode=args.obs_mode,
        max_steps=args.max_steps,
        out_dir=out_dir,
        client=client,
        keep_emulator=args.keep_emulator,
        doc_text=doc_text,
    )
    print(json.dumps(final, indent=1))
    print(f"trajectory: {out_dir / 'trajectory.jsonl'}")
    return 0 if final.get("success") in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
