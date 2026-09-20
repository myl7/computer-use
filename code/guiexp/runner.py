"""CLI: run one episode and write a JSONL trajectory plus screenshots.

    cd computer-use
    ../.venv-gui/bin/python -m guiexp.runner --layout wizard --condition discover \
        --seed 0 --model z-ai/glm-4.7-flash --obs-mode screenshot+ax --max-steps 30

Trajectory format: one JSON record per step (plus, when a reply fails to
parse, same-step records carrying a "retry" key for each re-ask)

    {"step": 0, "action_raw": "...", "action": "click('42')", "usage": {...},
     "obs_meta": {"url": ..., "screenshot_file": ..., "ax_chars": ...,
                  "last_action_error": ...}}

then one final record

    {"success": ..., "total_tokens": ..., "total_cost_usd": ...,
     "condition": ..., "layout": ..., "seed": ..., "model": ..., "obs_mode": ...}

Screenshots are saved as PNG files next to the JSONL. With --mock (or a
client passed programmatically) the deterministic MockOpenAI is used and no
network call is made.

When the per-request image budget freezes screenshots mid-episode (large
map screenshots vs the model channel's 30MB limit; see guiexp.agent), the
freeze step's record carries ``usage.image_budget_freeze`` and the final
record adds ``image_budget_frozen`` plus the full ``image_budget_event``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from .actions import ActionError, parse_first_action
from .agent import GuiAgent, OBS_MODES
from .app_server import LAYOUTS, AppServer
from .conditions import CONDITIONS, build_prompt
from .env import GuiEnv, get_task

REPO_ROOT = Path(__file__).resolve().parents[2]  # .../computer-use/guiexp -> repo root
DEFAULT_OUT_ROOT = REPO_ROOT / "experimental-results" / "guiexp"

# How many extra model calls a step may consume on malformed replies before
# the step is burned (each retry counts as the SAME step, never a new one).
MAX_PARSE_RETRIES = 3

# Anti-flail nudge: after this many IDENTICAL actions with an unchanged page
# signature, the next user message carries this generic system-level note.
# Identical wording in all five conditions -- no task or procedure content.
FLAIL_STREAK = 3
FLAIL_NOTE = (
    "Note: your last few identical actions did not change the page. That "
    "control is not working; scroll or choose a different control."
)


def _page_signature(obs: dict) -> str:
    """URL + hash of the AX tree: changes whenever the page really changes."""
    ax = obs.get("ax_tree_text") or ""
    return f'{obs.get("url", "")}|{hashlib.sha256(ax.encode()).hexdigest()[:16]}'


def _is_flailing(history: list[tuple[str, str]]) -> bool:
    """True when the last FLAIL_STREAK actions were identical AND the page
    signature did not change across them (including the state before the
    first of the streak, when known)."""
    if len(history) < FLAIL_STREAK:
        return False
    recent = history[-FLAIL_STREAK:]
    actions = {a for a, _ in recent}
    sigs = {s for _, s in recent}
    if len(actions) != 1 or len(sigs) != 1:
        return False
    if len(history) > FLAIL_STREAK and history[-FLAIL_STREAK - 1][1] not in sigs:
        return False
    return True


def run_episode(
    layout: str,
    condition: str,
    seed: int,
    model: str,
    obs_mode: str,
    max_steps: int,
    out_dir: Path | str,
    client=None,
    headless: bool = True,
    server: AppServer | None = None,
    manifest_path: Path | str | None = None,
) -> dict:
    """Run one episode; returns the final record and writes the trajectory."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectory.jsonl"

    own_server = None
    if server is None:
        own_server = AppServer(layout)
        base_url = own_server.start()
    else:
        base_url = server.base_url or server.start()

    # Optional library manifest (m_library experiment): the manifest block is
    # appended to the agent's system prompt, so every model call carries it.
    manifest_text = None
    manifest_n = None
    if manifest_path is not None:
        manifest_text = Path(manifest_path).read_text()
        manifest_n = manifest_text.count("PROGRAM ")

    agent = GuiAgent(model=model, obs_mode=obs_mode, client=client, manifest=manifest_text)
    task = get_task(layout, condition, seed, base_url)
    goal_prompt = build_prompt(condition, layout, task.event, base_url)

    env = GuiEnv(base_url, layout, headless=headless, annotate_som=(obs_mode == "screenshot"))
    records: list[dict] = []
    total_prompt = total_completion = 0
    total_cost = 0.0

    def _record_step(
        step: int,
        reply: str,
        usage: dict,
        obs: dict,
        retry: int | None = None,
        flail_nudge: bool = False,
    ) -> dict:
        nonlocal total_prompt, total_completion, total_cost
        try:
            action = parse_first_action(reply).render()
        except ActionError:
            action = None
        # The RAW screenshot is always what goes to disk (the model may have
        # seen the SoM-annotated variant).
        shot_file = None
        if obs.get("screenshot_b64"):
            shot_file = f"step_{step:03d}.png"
            (out_dir / shot_file).write_bytes(base64.b64decode(obs["screenshot_b64"]))
        total_prompt += usage.get("prompt_tokens") or 0
        total_completion += usage.get("completion_tokens") or 0
        total_cost += usage.get("cost_usd") or 0.0
        if "image_budget_freeze" in usage:
            # The event is the same dict the agent keeps; stamping here also
            # fills the step index into the final record's event below.
            usage["image_budget_freeze"]["step"] = step
        obs_meta = {
            "url": obs.get("url"),
            "screenshot_file": shot_file,
            "ax_chars": len(obs.get("ax_tree_text") or ""),
            "last_action_error": obs.get("last_action_error"),
        }
        if flail_nudge:
            obs_meta["flail_nudge"] = True
        rec = {
            "step": step,
            "action_raw": reply,
            "action": action,
            "usage": usage,
            "obs_meta": obs_meta,
        }
        if retry is not None:
            rec["retry"] = retry
        records.append(rec)
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
            sig_history: list[tuple[str, str]] = []  # (action, page signature)
            nudged_streak = False  # one nudge per flail streak
            while step < max_steps and not done:
                # Ask for one action; a malformed reply is sent back as a
                # parse error and re-asked WITHOUT advancing the step
                # (retries are extra model calls on the same step, capped).
                attempts = 0
                while True:
                    first_turn = step == 0 and attempts == 0
                    note = FLAIL_NOTE if nudged_streak and attempts == 0 else None
                    reply, usage = agent.act(goal_prompt if first_turn else None, obs, note=note)
                    attempts += 1
                    try:
                        parse_first_action(reply)
                        break
                    except ActionError as exc:
                        if attempts > MAX_PARSE_RETRIES:
                            break
                        _record_step(step + 1, reply, usage, obs, retry=attempts)
                        obs = {
                            **obs,
                            "last_action_error": (
                                f"{type(exc).__name__}: {exc} Your reply must be exactly "
                                "one line: action: <action>"
                            ),
                        }
                obs, done, reward = env.step(reply)
                step += 1
                nudged = nudged_streak  # this action was produced under a nudge
                _record_step(step, reply, usage, obs, flail_nudge=nudged)
                try:
                    action_str = parse_first_action(reply).render()
                except ActionError:
                    action_str = (reply or "").strip()
                sig_history.append((action_str, _page_signature(obs)))
                if _is_flailing(sig_history):
                    if not nudged_streak:
                        nudged_streak = True  # arm: the NEXT call carries the note
                        # a fresh 3-streak is required before another nudge
                        sig_history = sig_history[:-1]
                else:
                    nudged_streak = False  # streak broken (action or page changed)
            success = reward >= 1.0

        final = {
            "success": success,
            "total_tokens": total_prompt + total_completion,
            "total_cost_usd": round(total_cost, 8),
            "condition": condition,
            "layout": layout,
            "seed": seed,
            "model": model,
            "obs_mode": obs_mode,
            "task_id": task.task_id,
            "manifest": str(manifest_path) if manifest_path is not None else None,
            "manifest_entries": manifest_n,
            # env steps taken (retry re-asks are recorded but are not steps)
            "steps": sum(1 for r in records if "retry" not in r),
            "model_calls": len(records),
            "record_type": "final",
        }
        if agent.image_budget_event is not None:
            # Footnote for experiment runners: this episode's screenshots
            # crossed the per-request image budget mid-run (see guiexp.agent);
            # from the freeze step on, observations were text-only.
            final["image_budget_frozen"] = True
            final["image_budget_event"] = dict(agent.image_budget_event)
        records.append(final)
    finally:
        env.close()
        if own_server is not None:
            own_server.stop()

    with traj_path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--layout", default="wizard", choices=LAYOUTS)
    parser.add_argument("--condition", default="discover", choices=CONDITIONS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="openai/gpt-4o-mini")
    parser.add_argument("--obs-mode", default="screenshot+ax", choices=OBS_MODES)
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--out", default=None, help="output directory (default: experimental-results/guiexp/<run id>)")
    parser.add_argument("--mock", action="store_true", help="use the deterministic offline MockOpenAI")
    parser.add_argument("--show", action="store_true", help="run Chromium headed")
    parser.add_argument("--manifest", default=None,
                        help="optional manifest text file appended to the system prompt "
                             "(library-manifest cost experiment; unset = unchanged behavior)")
    args = parser.parse_args()

    client = None
    model = args.model
    if args.mock:
        from .mock_model import MockOpenAI

        client = MockOpenAI()
        model = "mock"

    out_dir = Path(args.out) if args.out else (
        DEFAULT_OUT_ROOT
        / f"{args.layout}_{args.condition}_s{args.seed}_{model.replace('/', '-')}"
    )
    final = run_episode(
        layout=args.layout,
        condition=args.condition,
        seed=args.seed,
        model=model,
        obs_mode=args.obs_mode,
        max_steps=args.max_steps,
        out_dir=out_dir,
        client=client,
        headless=not args.show,
        manifest_path=args.manifest,
    )
    print(json.dumps(final, indent=1))
    print(f"trajectory: {out_dir / 'trajectory.jsonl'}")
    return 0 if final.get("success") in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
