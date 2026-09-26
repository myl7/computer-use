"""Verification with hybrid repair: AutoRPA's Section 3.4 loop, plus our
own held-out gate.

The loop (docs/autorpa-replication-protocol.md section 5.3(c)):

    seen = []
    for g in building_instances(seeds 1,2,3):
        seen.append(g)
        for m in range(M+1):              # M = 3
            fail = first_failure(program, seen)   # replay in order, stop at
            if fail is None: break                # the first failure
            A = analyzer(program_trace(fail), observation_at_breakpoint(fail))
            tau_hybrid = react_resume(fail, A)    # continue, or restart
            program = builder_refine(program, tau_hybrid, conclusions)
        else:
            mark_type_unautomatable(); break

Charging, stage by stage:

  * program replays are ZERO model tokens. Our programs address elements
    through ``device.find`` on the a11y list, so no grounding model runs; the
    paper pays a GUI grounder on every ``find_element``. That is a real
    methodological difference and is reported, not hidden.
  * the analyzer is one model call per failure: the executed trace plus the
    element list at the breakpoint.
  * the reactive resume is a partial episode on the live emulator, charged in
    full, exactly like a building episode.
  * every builder refinement is charged into C.

Our own gate runs on every version the loop produces, not just the last one:
5 held-out bindings the builder never saw, each judged by the family's own
oracle. The initial k=3 program is gated before any repair, each refinement
is gated after it, and the best version (at least GATE_MIN_PASS of 5) is what
comes back and what gets deployed. The loop's ``unautomatable`` verdict is
reported and decides nothing. All of it lands in verify.json.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.verify_runner \
        --family ContactsAddContact --program .../family_program.py \
        --model z-ai/glm-5.3-flash --out .../verify.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import android_env
from .accounting_check import call_record
from .actions import ActionError, parse_action
from .compiler import params_to_binding, refine_artifact
from .explore import BUILDING_SEEDS, step_cap
from .gate_runner import GATE_K, GATE_MIN_PASS, heldout_bindings, run_gate
from .program_runtime import ProgramDevice, ProgramRunner, program_from_source

M_DEFAULT = 3  # refinement budget per building instance (AutoRPA page 7)

ANALYZER_SYSTEM = (
    "You diagnose a failed run of an automation program on an Android phone. "
    "You reply in the fixed shape you are asked for, nothing else."
)


# ---------------------------------------------------------- tracing device


class TracingDevice(ProgramDevice):
    """A ProgramDevice that records what the program actually did.

    The trace is the repair loop's evidence: one line per action with the
    activity it landed on, and one line per lookup that found nothing (the
    most common way a compiled program breaks on a new screen).
    """

    def __init__(self, env, settle_s=None, max_actions: int = 120):
        super().__init__(env, settle_s=settle_s, max_actions=max_actions)
        self.trace: list[str] = []

    def find(self, **kwargs):
        index = super().find(**kwargs)
        if index is None:
            criteria = {k: v for k, v in kwargs.items() if v is not None}
            self.trace.append(f"find({criteria}) -> NOT FOUND")
        return index

    def execute(self, action: dict) -> None:
        try:
            super().execute(action)
        except Exception as exc:  # noqa: BLE001 - the failure shape is the evidence
            self.trace.append(
                f"{json.dumps(action)} -> RAISED {type(exc).__name__}: "
                f"{str(exc).splitlines()[0][:120]}"
            )
            raise
        self.trace.append(f"{json.dumps(action)} -> {self.current_activity()}")


def screen_text(device: ProgramDevice, max_lines: int = 40) -> str:
    """The element list at the breakpoint, one line per element."""
    try:
        elements = device.elements()
    except Exception as exc:  # noqa: BLE001 - a wedged device is still evidence
        return f"(could not read the screen: {type(exc).__name__})"
    lines = []
    for element in elements[:max_lines]:
        parts = [f"{key}={element[key]!r}" for key in ("text", "hint", "description")
                 if element.get(key)]
        flags = [key for key in ("clickable", "editable", "scrollable") if element.get(key)]
        lines.append(
            f"  {element['index']}: {', '.join(parts) or '(no text)'}"
            + (f" [{'/'.join(flags)}]" if flags else "")
        )
    if len(elements) > max_lines:
        lines.append(f"  ... {len(elements) - max_lines} more elements")
    return "\n".join(lines)


# ------------------------------------------------------------------ replay


def building_instances(family: str, seeds=BUILDING_SEEDS) -> list[dict]:
    """The building instances in the order they were taken up."""
    out = []
    for seed in seeds:
        params = android_env.instance_params(family, seed)
        out.append({
            "seed": seed,
            "params": params,
            "binding": params_to_binding(family, params),
            "goal": android_env.goal_text(android_env.get_task(family, "discover", seed)),
        })
    return out


def first_failure(program, family: str, seen: list[dict], runner: ProgramRunner) -> dict:
    """Replay the program over the seen instances in order, stopping at the
    first failure. Zero model tokens. Returns {failure, replays, detail}."""
    detail = []
    for instance in seen:
        device_holder = {}

        def factory(env, holder=device_holder):
            holder["device"] = TracingDevice(env)
            return holder["device"]

        outcome = runner.run(
            program, instance["binding"], family,
            judge_params=instance["params"], device_factory=factory,
        )
        device = outcome.get("device") or device_holder.get("device")
        detail.append({
            "seed": instance["seed"],
            "passed": outcome["passed"],
            "error": outcome["error"],
        })
        if not outcome["passed"]:
            return {
                "failure": {
                    "instance": instance,
                    "error": outcome["error"],
                    "trace": list(getattr(device, "trace", [])),
                    "screen": screen_text(device) if device is not None else "",
                },
                "replays": len(detail),
                "detail": detail,
            }
    return {"failure": None, "replays": len(detail), "detail": detail}


# ---------------------------------------------------------------- analyzer


def build_analyzer_prompt(family: str, failure: dict) -> list[dict]:
    """One analyzer call: the executed trace and the breakpoint screen in,
    cause plus continuation plan plus a continue-or-restart decision out."""
    instance = failure["instance"]
    lines = [
        f"An automation program for the task type {family} on Android failed.",
        "",
        f"TASK IT WAS RUNNING: {instance['goal'].strip()}",
        f"PARAMETERS: {json.dumps(instance['binding'])}",
        f"HOW IT ENDED: {failure['error'] or 'it ran to the end but the task oracle rejected the result'}",
        "",
        "WHAT IT ACTUALLY DID (one line per action, with the screen it landed",
        "on; 'NOT FOUND' lines are lookups that matched no element):",
    ]
    lines += [f"  {line}" for line in failure["trace"]] or ["  (no action reached the device)"]
    lines += [
        "",
        "THE SCREEN WHERE IT STOPPED (the a11y element list, index: content):",
        failure["screen"] or "  (empty)",
        "",
        "Answer in exactly this shape:",
        "CAUSE: <one or two lines: why it failed here>",
        "DONE: <the subtasks that are already completed on this device>",
        "PLAN: <how to finish the task from the screen above, step by step>",
        "DECISION: continue|restart   <- 'continue' if the plan can be carried",
        "out from the screen above, 'restart' if the device must go back to a",
        "clean start first",
    ]
    return [
        {"role": "system", "content": ANALYZER_SYSTEM},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_analysis(reply: str) -> dict:
    """The analyzer's fields; an unreadable decision falls back to restart."""
    text = (reply or "").strip()
    fields = {"cause": "", "done": "", "plan": "", "decision": "restart"}
    current = None
    for line in text.splitlines():
        upper = line.strip().upper()
        for key in ("CAUSE", "DONE", "PLAN", "DECISION"):
            if upper.startswith(key + ":"):
                current = key.lower()
                fields[current] = line.split(":", 1)[1].strip()
                break
        else:
            if current and line.strip():
                fields[current] += ("\n" + line.rstrip())
    decision = fields["decision"].strip().lower()
    fields["decision"] = "continue" if decision.startswith("continue") else "restart"
    fields["text"] = text
    return fields


def analyze(model: str, family: str, failure: dict, client=None, temperature: float = 0.0) -> dict:
    """One charged analyzer call."""
    from .agent import AndroidAgent
    from .compiler import _openai_client

    messages = build_analyzer_prompt(family, failure)
    if client is None:
        client = _openai_client()
    response = client.chat.completions.create(model=model, messages=messages, temperature=temperature)
    usage = AndroidAgent._usage(response)
    parsed = parse_analysis(response.choices[0].message.content or "")
    return {
        **parsed,
        "usage": usage,
        "cost_usd": usage.get("cost_usd") or 0.0,
        "stage": "analyzer",
    }


# ------------------------------------------------------------ react resume


RESUME_HEADER = (
    "You are taking over an Android phone part-way through a task. An\n"
    "automation script ran first and stopped without finishing. A diagnosis\n"
    "of where it stopped and how to carry on follows; the phone is on the\n"
    "screen it stopped on, so check what you see against the diagnosis before\n"
    "acting, and finish the task from here."
)


def react_resume(
    model: str,
    env,
    family: str,
    failure: dict,
    analysis: dict,
    obs_mode: str = "screenshot+ax",
    client=None,
    max_steps: int | None = None,
) -> dict:
    """A partial reactive episode from the breakpoint, charged in full.

    ``analysis['decision']`` chooses between carrying on from the screen the
    program left behind and restarting the instance from a clean state.
    """
    from .agent import AndroidAgent
    from .conditions import build_prompt

    instance = failure["instance"]
    max_steps = max_steps or step_cap(family)
    task = android_env.AndroidTask(
        family=family, condition="discover", seed=instance["seed"], params=instance["params"]
    )
    restarted = analysis.get("decision") != "continue"
    if restarted:
        obs = env.reset(task)
        goal_prompt = build_prompt("discover", family, instance["goal"])
    else:
        obs = env.observe(instance["goal"])
        goal_prompt = (
            RESUME_HEADER
            + "\n\nDIAGNOSIS OF THE BREAKPOINT:\n"
            + (analysis.get("text") or "").strip()
            + "\n\n"
            + build_prompt("discover", family, instance["goal"])
        )

    agent = AndroidAgent(model=model, obs_mode=obs_mode, client=client)
    actions: list[str] = []
    calls_detail: list[dict] = []
    prompt_tokens = cached_tokens = completion_tokens = 0
    cost = 0.0
    calls = 0
    done = False
    reward = 0.0
    step = 0
    while step < max_steps and not done:
        reply, usage = agent.act(goal_prompt if step == 0 else None, obs)
        calls += 1
        prompt_tokens += usage.get("prompt_tokens") or 0
        cached_tokens += usage.get("cached_tokens") or 0
        completion_tokens += usage.get("completion_tokens") or 0
        cost += usage.get("cost_usd") or 0.0
        try:
            action_text = parse_action(reply).json_str()
        except ActionError:
            action_text = reply.strip()[:200]
        obs, done, reward = env.step(reply)
        step += 1
        if "image_413_events" in usage:
            # Same dicts the agent keeps; call_record embeds the usage dict
            # verbatim, so the stamped events land inside calls_detail too.
            for ev in usage["image_413_events"]:
                ev["step"] = step
        calls_detail.append(call_record(step, calls, usage, stage="react_resume",
                                        action=action_text))
        actions.append(f"step {step}: {action_text} -> {obs.get('url', '')}")

    return {
        "restarted": restarted,
        "actions": actions,
        "calls_detail": calls_detail,
        "steps": step,
        "success": reward >= 1.0,
        "usage": {
            "calls": calls,
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost_usd": round(cost, 8),
        },
        "stage": "react_resume",
    }


# ------------------------------------------------------------- the verifier


def _zero_totals() -> dict:
    return {
        "calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
        "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0,
    }


def _add(totals: dict, usage: dict, calls: int = 1) -> None:
    totals["calls"] += calls
    totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
    totals["cached_tokens"] += usage.get("cached_tokens") or 0
    totals["completion_tokens"] += usage.get("completion_tokens") or 0
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    totals["cost_usd"] = round(totals["cost_usd"] + (usage.get("cost_usd") or 0.0), 8)


def verify_and_repair(
    model: str,
    family: str,
    program_source: str,
    env,
    seeds=BUILDING_SEEDS,
    m_max: int = M_DEFAULT,
    client=None,
    obs_mode: str = "screenshot+ax",
    artifact: str = "code",
    out_dir: Path | str | None = None,
) -> dict:
    """AutoRPA's verification loop, with our held-out gate as the admission
    criterion for every candidate version, not only the last one.

    Each refinement OVERWRITES the program, so the loop on its own can throw
    away a good version for a worse one: a program that passed the gate 5/5
    has been overwritten by repairs that passed 3/5 and then deployed. So
    every version the loop produces is gated as it appears -- the initial
    k=3 program before any repair, and each refined program after it -- and
    the best gate-passing version is what comes back (highest passed count,
    ties to the earliest, so an equally good later version never displaces
    an earlier one).

    The extra gating costs wall time only. A gate replay goes through
    ``run_gate`` on the live device and spends zero model tokens: our
    programs address elements through ``device.find`` on the a11y list, so
    no grounding model runs. The token totals of this function are exactly
    the ones the AutoRPA loop spends.

    Refinement stops as soon as a version passes all ``GATE_K`` bindings,
    since nothing better exists to find; a version at ``GATE_MIN_PASS`` or
    above is admitted but still goes on collecting repairs up to M, and the
    best of them is kept.

    Returns the whole record: per-round evidence, stage totals, the
    returned artifact and its gate, the gate of every version, whether the
    returned version is admitted, and the loop's own ``unautomatable``
    verdict, which is reported and decides nothing.
    """
    runner = ProgramRunner(env)
    instances = building_instances(family, seeds)
    _module, program = program_from_source(program_source)

    # One set of held-out draws, reused for every version so the gate
    # numbers of two versions are comparable.
    gate_draws = heldout_bindings(family, k=GATE_K, exclude_params=instances[0]["params"])
    gate_history: list[dict] = []
    best: dict = {"version": None, "source": None, "gate": None, "passed": -1}
    last: dict = {"version": None, "source": None, "gate": None}

    def gate_version(version: str, candidate, source: str) -> dict:
        """Gate one candidate version. Zero model tokens, wall time only."""
        gate = run_gate(candidate, family, gate_draws, runner)
        gate_history.append({
            "version": version,
            "passed": gate["bindings_passed"],
            "total": gate["bindings_total"],
        })
        last.update(version=version, source=source, gate=gate)
        if gate["bindings_passed"] > best["passed"]:  # ties keep the earlier
            best.update(version=version, source=source, gate=gate,
                        passed=gate["bindings_passed"])
        return gate

    rounds: list[dict] = []
    conclusions: list[str] = []
    analyzer_totals = _zero_totals()
    resume_totals = _zero_totals()
    refine_totals = _zero_totals()
    replays = 0
    refinements = 0
    unautomatable = False
    seen: list[dict] = []

    initial_gate = gate_version("initial", program, program_source)
    full_pass = initial_gate["bindings_passed"] >= len(gate_draws)

    for instance in instances:
        if full_pass:
            break  # the program the loop would repair already passes every binding
        seen.append(instance)
        repaired = False
        for m in range(m_max + 1):
            scan = first_failure(program, family, seen, runner)
            replays += scan["replays"]
            if scan["failure"] is None:
                repaired = True
                break
            if m == m_max:
                break  # M modifications spent and it still fails

            failure = scan["failure"]
            analysis = analyze(model, family, failure, client=client)
            _add(analyzer_totals, analysis["usage"])

            resume = react_resume(
                model, env, family, failure, analysis,
                obs_mode=obs_mode, client=client,
            )
            _add(resume_totals, resume["usage"], calls=resume["usage"]["calls"])

            hybrid = {
                "binding": failure["instance"]["binding"],
                "goal": failure["instance"]["goal"],
                "failure": failure["error"],
                "program_trace": failure["trace"],
                "breakpoint_screen": failure["screen"],
                "analysis": analysis.get("text", ""),
                "resume_actions": resume["actions"],
                "resume_success": resume["success"],
            }
            refined = refine_artifact(
                model, family, program_source, hybrid, conclusions,
                artifact=artifact, client=client,
            )
            _add(refine_totals, refined["usage"])
            refinements += 1
            program_source = refined["artifact_text"]
            _module, program = program_from_source(program_source)
            refined_gate = gate_version(f"refine{refinements}", program, program_source)
            conclusions.append(
                f"seed {failure['instance']['seed']}: {analysis.get('cause', '').strip()[:300]}"
            )
            # Every model call of this round goes on disk, not just its total:
            # the cache-adjusted unit needs each call's prompt size and its
            # predecessor's prompt plus completion.
            analysis_record = {k: analysis[k] for k in ("cause", "done", "plan", "decision")}
            analysis_record.update(call_record(1, 1, analysis["usage"], stage="analyzer"))
            resume_record = {k: resume[k] for k in ("restarted", "steps", "success")}
            resume_record["calls"] = resume["usage"]["calls"]
            resume_record["calls_detail"] = resume["calls_detail"]
            rounds.append({
                "seed_added": instance["seed"],
                "seen_seeds": [i["seed"] for i in seen],
                "failure_seed": failure["instance"]["seed"],
                "error": failure["error"],
                "replay_detail": scan["detail"],
                "analysis": analysis_record,
                "resume": resume_record,
                "refinement": refinements,
                "gate": {"passed": refined_gate["bindings_passed"],
                         "total": refined_gate["bindings_total"]},
                "refinement_usage": call_record(
                    1, 1, refined["usage"], stage="builder_refine",
                    attempts=refined.get("attempts"),
                    calls_detail=refined.get("calls_detail") or [],
                ),
            })
            if out_dir is not None:
                path = Path(out_dir) / f"refine{refinements}_program.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(program_source)
                calls_path = Path(out_dir) / f"resume{refinements}_calls.jsonl"
                with calls_path.open("w") as fh:
                    for entry in resume["calls_detail"]:
                        fh.write(json.dumps(entry) + "\n")
            if refined_gate["bindings_passed"] >= len(gate_draws):
                full_pass = True  # nothing better to find; stop refining
                break
        if full_pass:
            break
        if not repaired:
            unautomatable = True
            break

    # Admission: the best gate-passing version, or the last one if none was
    # admitted (so the record always carries a program and its gate).
    admitted = best["passed"] >= GATE_MIN_PASS
    chosen = best if admitted else last
    final_source = chosen["source"]
    gate = chosen["gate"]
    _module, final_program = program_from_source(final_source)

    # The AutoRPA-comparable single test instance: seed 0.
    test_params = android_env.instance_params(family, 0)
    test_outcome = runner.run(
        final_program, params_to_binding(family, test_params), family,
        judge_params=test_params,
    )

    return {
        "family": family,
        "model": model,
        "artifact": artifact,
        "m_max": m_max,
        "seeds": list(seeds),
        "rounds": rounds,
        "refinements": refinements,
        "replays": replays,
        "unautomatable": unautomatable,  # the AutoRPA-loop verdict, reported only
        "admitted": admitted,
        "admitted_version": chosen["version"],
        "gate_min_pass": GATE_MIN_PASS,
        "gate_history": gate_history,
        "final_artifact": final_source,
        "last_artifact": last["source"],
        "conclusions": conclusions,
        "totals": {
            "analyzer": analyzer_totals,
            "resume_episodes": resume_totals,
            "builder_refinements": refine_totals,
            "program_replays": {"count": replays, "total_tokens": 0, "cost_usd": 0.0},
        },
        "gate": gate,
        "test_seed0": {"passed": test_outcome["passed"], "error": test_outcome["error"]},
        "record_type": "verification",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", required=True)
    parser.add_argument("--program", required=True, help="the initial compiled program .py")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--m", type=int, default=M_DEFAULT)
    parser.add_argument("--out", required=True, help="verify.json path")
    parser.add_argument("--keep-emulator", action="store_true")
    args = parser.parse_args()

    from .conditions import FAMILIES

    if args.family not in FAMILIES:
        parser.error(f"unknown family {args.family!r}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    source = Path(args.program).read_text()
    env = android_env.AndroidWorldEnv()
    t0 = time.time()
    try:
        record = verify_and_repair(
            args.model, args.family, source, env,
            seeds=tuple(int(s) for s in args.seeds.split(",")),
            m_max=args.m, obs_mode=args.obs_mode, out_dir=out.parent,
        )
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()
    record["wall_s"] = round(time.time() - t0, 1)
    out.write_text(json.dumps(record, indent=1))
    (out.parent / "verified_program.py").write_text(record["final_artifact"])
    gate = record["gate"]
    print(f"refinements: {record['refinements']}, replays: {record['replays']}, "
          f"unautomatable: {record['unautomatable']}")
    for entry in record["gate_history"]:
        print(f"  gate {entry['version']:<10} {entry['passed']}/{entry['total']}")
    if gate:
        print(f"kept {record['admitted_version']}: {gate['bindings_passed']}/"
              f"{gate['bindings_total']}, admitted: {record['admitted']} "
              f"(threshold {GATE_MIN_PASS})")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
