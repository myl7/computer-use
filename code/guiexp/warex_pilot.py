"""E10: the WAREX perturbation proxy in front of OpenApps, one seed.

Two measurements against the same six perturbation arms.

1. Zero model tokens. The compiled wizard family program is replayed on the
   five held-out bindings behind the proxy, judged by the environment's own
   six-field oracle, coded PASS / LOUD / SILENT_WRONG exactly as e7_drift
   codes drift. That gives q per arm and the loud/silent split.
2. One discover-condition reactive episode per arm with a spend cap, so the
   same perturbations can be priced against a reactive agent.

Arm 0 (baseline) runs first with the proxy in count-only mode; its per-episode
eligible-request counts calibrate the injection schedule of every other arm
(see warex_proxy.draw_k).

    ../.venv-gui/bin/python -m guiexp.warex_pilot --program <path> --seed 0
    ../.venv-gui/bin/python -m guiexp.warex_pilot --reactive --model z-ai/glm-5.3-flash
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path

from .drift_probe import (
    OPENAPPS_PY,  # noqa: F401  (import guard: the venv must exist)
    _events,
    _ground_truth,
    _tracked,
    drift_bindings,
    drift_server,
    outcome_of,
)
from .program_runtime import BindingRunner, program_from_path
from .runner import DEFAULT_OUT_ROOT, run_episode
from .warex_proxy import KINDS, WarexProxy

OUT_DIR_DEFAULT = DEFAULT_OUT_ROOT / "e10_warex"
PROGRAM_DEFAULT = (
    DEFAULT_OUT_ROOT / "t13_compilepath" / "z-ai_glm-5.3-flash" / "attempt1" / "family_program.py"
)
LAYOUT = "wizard"
WIZARD_OVERRIDES = ["apps/calendar/appearance=wizard"]

# (arm name, active perturbation kinds, in the reactive grid?)
ARMS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("baseline", (), True),
    ("latency", ("latency_only",), False),
    ("network", ("network",), True),
    ("server_500", ("server",), True),
    ("js_504", ("js",), True),
    ("popup", ("popup",), True),
    ("combined", ("network", "server", "js", "popup"), True),
)

PER_EPISODE_CAP_USD = 0.08
TOTAL_CAP_USD = 0.30


# ---------------------------------------------------------------------------
# Failure-mode coding
# ---------------------------------------------------------------------------

def failure_mode(prog: dict, truth: dict, outcome: str) -> str:
    """One label per run: what shape did the failure take?

    State first (a wrong or duplicated record is a fact about the app, not
    about the message), then the Playwright method the error names. A timeout
    with no method prefix and the program's own 15 s budget can only come from
    its ``page.wait_for_url``, the single 15 s wait it contains.

    ``prog["error"]`` matters as much as ``prog["exception"]``: a timeout on
    BindingRunner's own opening ``page.goto`` never reaches the program, so it
    arrives with exception_class "none" and must not be read as a silent no-op.
    """
    if outcome == "PASS":
        return "pass"
    if truth["record_duplicated"]:
        return "duplicate_record"
    if truth["record_exists"] and not truth["record_correct"]:
        return "wrong_record"
    exc_class = prog.get("exception_class") or "none"
    text = f"{prog.get('exception') or ''} {prog.get('error') or ''}".strip()
    low = text.lower()
    if "could not locate text field" in low or "no button matching" in low:
        return "element_not_found"
    if "timeout" in low:
        if "goto" in low or "wait_for_url" in low or "navigat" in low or "15000ms" in low:
            return "navigation_timeout"
        return "selector_timeout"
    if not text and not truth["record_exists"]:
        return "no_write"
    if exc_class != "none":
        return f"exception:{exc_class}"
    return "other"


# ---------------------------------------------------------------------------
# Zero-token arm: replay the compiled program behind the proxy
# ---------------------------------------------------------------------------

def run_program_arm(program_path: Path, arm: str, kinds: tuple[str, ...],
                    bindings: list[dict], seed: int, ceilings: dict[str, int],
                    headless: bool = True) -> dict:
    _module, program = program_from_path(program_path)
    print(f"\n[program/{arm}] kinds={list(kinds) or ['none']} ceilings={ceilings}")
    runs: list[dict] = []
    with drift_server(WIZARD_OVERRIDES, LAYOUT) as upstream:
        with WarexProxy(upstream, arm=arm, kinds=kinds, seed=seed,
                        ceilings=ceilings) as proxy:
            tracked_program, state = _tracked(program)

            def tracked(page, binding, base_url):
                # Control passes to the program here; perturbation starts now.
                proxy.arm_injection()
                return tracked_program(page, binding, base_url)

            # The browser talks to the proxy; the oracle reads the upstream
            # directly, so no perturbation can move the judgement.
            with BindingRunner(proxy.base_url, LAYOUT, headless=headless) as runner:
                runner.env.base_url = upstream.rstrip("/")
                for binding in bindings:
                    key = binding["title"]
                    proxy.begin_episode(key)
                    t0 = time.time()
                    before = _events(upstream)
                    result = runner.run(tracked, binding)
                    after = _events(upstream)
                    truth = _ground_truth(before, after, binding)
                    prog = {"returned": state["returned"], "raised": state["raised"],
                            "exception_class": state["exception_class"],
                            "exception": state["exception"], "error": result["error"]}
                    state.update(returned=None, raised=False,
                                 exception_class="none", exception="")
                    outcome = outcome_of(prog, truth)
                    proxy.end_episode()
                    injected = proxy.episode_log[-1] if proxy.episode_log else {}
                    runs.append({
                        "arm": arm, "binding": key, **prog, **truth,
                        "oracle_passed": result["passed"],
                        "outcome": outcome,
                        "failure_mode": failure_mode(prog, truth, outcome),
                        "injection": injected,
                        "wall_s": round(time.time() - t0, 1),
                    })
                    print(f"  {key:<20} {outcome:<12} {runs[-1]['failure_mode']:<22} "
                          f"{runs[-1]['wall_s']:>5.1f}s  fired="
                          f"{[f['kind'] for f in injected.get('fired', [])]}")
            schedule = proxy.schedule()
    return {"arm": arm, "kinds": list(kinds), "runs": runs, "schedule": schedule}


# ---------------------------------------------------------------------------
# Reactive arm: one discover episode per arm, hard spend cap
# ---------------------------------------------------------------------------

class BudgetExceeded(RuntimeError):
    pass


class _CappedClient:
    """OpenRouter client wrapper that aborts an episode at ``cap`` USD."""

    def __init__(self, cap: float, on_first_call=None):
        from openai import OpenAI

        self.on_first_call = on_first_call

        self._inner = OpenAI(base_url=os.environ["OPENROUTER_BASE_URL"],
                             api_key=os.environ["OPENROUTER_API_KEY"],
                             timeout=180.0, max_retries=2)
        self.cap = cap
        self.spent = 0.0
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kwargs):
                if outer.on_first_call is not None and outer.calls == 0:
                    outer.on_first_call()
                if outer.spent >= outer.cap:
                    raise BudgetExceeded(
                        f"episode spend {outer.spent:.4f} USD reached the cap {outer.cap}")
                response = outer._inner.chat.completions.create(**kwargs)
                outer.calls += 1
                usage = getattr(response, "usage", None)
                cost = getattr(usage, "cost", None) if usage is not None else None
                if cost is None and usage is not None and getattr(usage, "model_extra", None):
                    cost = (usage.model_extra or {}).get("cost")
                outer.spent += float(cost or 0.0)
                return response

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


class _ServerShim:
    """What run_episode wants from an AppServer: a base_url it can hand out."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    def start(self) -> str:
        return self.base_url


def run_reactive_arm(arm: str, kinds: tuple[str, ...], seed: int,
                     ceilings: dict[str, int], model: str, obs_mode: str,
                     max_steps: int, out_dir: Path, headless: bool = True) -> dict:
    print(f"\n[reactive/{arm}] kinds={list(kinds) or ['none']}")
    t0 = time.time()
    record: dict = {"arm": arm, "kinds": list(kinds)}
    with drift_server(WIZARD_OVERRIDES, LAYOUT) as upstream:
        with WarexProxy(upstream, arm=f"reactive:{arm}", kinds=kinds, seed=seed,
                        ceilings=ceilings) as proxy:
            proxy.begin_episode(f"reactive:{arm}")
            # The agent is in control once it has been asked for its first
            # action, which is after env.reset's opening navigation.
            client = _CappedClient(PER_EPISODE_CAP_USD,
                                   on_first_call=proxy.arm_injection)
            try:
                final = run_episode(
                    layout=LAYOUT, condition="discover", seed=seed, model=model,
                    obs_mode=obs_mode, max_steps=max_steps,
                    out_dir=out_dir / f"reactive_{arm}", client=client,
                    headless=headless, server=_ServerShim(proxy.base_url),
                )
                record.update(success=bool(final.get("success")),
                              steps=final.get("steps"),
                              model_calls=final.get("model_calls"),
                              total_tokens=final.get("total_tokens"),
                              aborted=False, abort_reason=None)
            except BudgetExceeded as exc:
                record.update(success=False, steps=None, model_calls=client.calls,
                              total_tokens=None, aborted=True, abort_reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - the failure shape is data
                record.update(success=False, steps=None, model_calls=client.calls,
                              total_tokens=None, aborted=True,
                              abort_reason=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
            proxy.end_episode()
            record["injection"] = proxy.episode_log[-1] if proxy.episode_log else {}
            record["popup_clickthroughs"] = proxy.accept_hits
    record["cost_usd"] = round(client.spent, 6)
    record["wall_s"] = round(time.time() - t0, 1)
    print(f"  success={record.get('success')} cost=${record['cost_usd']:.4f} "
          f"calls={record.get('model_calls')} aborted={record.get('aborted')}")
    return record


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------

def counts(runs: list[dict]) -> dict:
    return {
        "pass": sum(1 for r in runs if r["outcome"] == "PASS"),
        "loud": sum(1 for r in runs if r["outcome"] == "LOUD"),
        "silent": sum(1 for r in runs if r["outcome"] == "SILENT_WRONG"),
        "total": len(runs),
    }


def _table_md(summary: dict) -> str:
    lines = [
        "# E10 WAREX pilot -- perturbation proxy in front of OpenApps",
        "",
        f"Program: `{summary['program']}`",
        f"Seed: {summary['seed']}, bindings: {', '.join(summary['bindings'])}",
        "",
        "| Arm | program pass /5 | loud | silent | q | failure modes | reactive | reactive cost |",
        "|---|---:|---:|---:|---:|---|---|---:|",
    ]
    for arm in summary["arm_order"]:
        block = summary["program_arms"][arm]
        c = block["counts"]
        q = 1.0 - c["pass"] / c["total"] if c["total"] else float("nan")
        modes = ", ".join(f"{k} x{v}" for k, v in sorted(block["failure_modes"].items())
                          if k != "pass") or "none"
        rea = summary["reactive_arms"].get(arm)
        if rea is None:
            rea_cell, cost_cell = "not run", "--"
        else:
            rea_cell = ("success" if rea.get("success") else
                        ("aborted" if rea.get("aborted") else "fail"))
            if rea.get("steps") is not None:
                rea_cell += f" ({rea['steps']} steps)"
            cost_cell = f"${rea['cost_usd']:.4f}"
        lines.append(f"| {arm} | {c['pass']} | {c['loud']} | {c['silent']} | {q:.2f} "
                     f"| {modes} | {rea_cell} | {cost_cell} |")
    lines += [
        "",
        f"- total reactive spend: ${summary['reactive_total_usd']:.4f}",
        f"- program replays: {summary['n_program_runs']} (zero model tokens)",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", default=str(PROGRAM_DEFAULT))
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--k", type=int, default=5, help="held-out bindings")
    parser.add_argument("--out", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--reactive", action="store_true",
                        help="also run one discover episode per arm (costs money)")
    parser.add_argument("--program-only", action="store_true")
    parser.add_argument("--skip-program", action="store_true",
                        help="reuse the program arms already in <out>/warex_pilot.json "
                             "(failure modes are recomputed on load) and run only the "
                             "reactive grid; the zero-token grid costs 12 minutes of wall "
                             "clock and no tokens, so a rerun is optional, not required")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash")
    parser.add_argument("--obs-mode", default="screenshot+ax")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    program_path = Path(args.program)
    bindings = drift_bindings(args.k, seed=args.seed)
    headless = not args.show
    t_start = time.time()

    program_arms: dict[str, dict] = {}
    if args.skip_program:
        prior = json.loads((out_dir / "warex_pilot.json").read_text())
        ceilings = prior["ceilings"]
        for arm, block in prior["program_arms"].items():
            runs = block["runs"]
            for r in runs:
                r["failure_mode"] = failure_mode(r, r, r["outcome"])
            program_arms[arm] = {"arm": arm, "kinds": block["kinds"], "runs": runs,
                                 "schedule": block["schedule"]}
        print(f"reusing {len(program_arms)} program arms from {out_dir / 'warex_pilot.json'}")
    else:
        # --- baseline first: it calibrates every other arm's schedule -----
        base_block = run_program_arm(program_path, "baseline", (), bindings, args.seed,
                                     ceilings={}, headless=headless)
        program_arms["baseline"] = base_block

        per_episode = [ep["eligible_counts"] for ep in base_block["schedule"]["episodes"]]
        ceilings = {}
        for kind in KINDS:
            vals = [c.get(kind, 0) for c in per_episode]
            ceilings[kind] = max(1, int(statistics.median(vals))) if vals else 1
        print(f"\nceilings from baseline traffic "
              f"(median eligible requests per episode): {ceilings}")

        for arm, kinds, _in_reactive in ARMS:
            if arm == "baseline":
                continue
            program_arms[arm] = run_program_arm(program_path, arm, kinds, bindings,
                                                args.seed, ceilings, headless=headless)

    reactive_arms: dict[str, dict] = {}
    reactive_total = 0.0
    reactive_stopped = None
    if args.reactive and not args.program_only:
        from dotenv import load_dotenv

        load_dotenv(os.path.expanduser("~/app/.env"))
        for arm, kinds, in_reactive in ARMS:
            if not in_reactive:
                continue
            if reactive_total + PER_EPISODE_CAP_USD > TOTAL_CAP_USD + 1e-9:
                reactive_stopped = (f"total cap ${TOTAL_CAP_USD} would be exceeded before "
                                    f"arm {arm} (spent ${reactive_total:.4f})")
                print(f"\nstopping reactive grid: {reactive_stopped}")
                break
            rec = run_reactive_arm(arm, kinds, args.seed, ceilings, args.model,
                                   args.obs_mode, args.max_steps, out_dir,
                                   headless=headless)
            reactive_arms[arm] = rec
            reactive_total += rec["cost_usd"]
            if rec["cost_usd"] >= PER_EPISODE_CAP_USD:
                reactive_stopped = (f"arm {arm} hit the per-episode cap "
                                    f"${PER_EPISODE_CAP_USD} (${rec['cost_usd']:.4f})")
                print(f"\nstopping reactive grid: {reactive_stopped}")
                break

    all_runs = [r for b in program_arms.values() for r in b["runs"]]
    summary = {
        "experiment": "e10_warex",
        "warex_source": "arXiv:2510.03285 (no code released; minimal equivalent in "
                        "guiexp/warex_proxy.py)",
        "program": str(program_path),
        "layout": LAYOUT,
        "seed": args.seed,
        "bindings": [b["title"] for b in bindings],
        "arm_order": [a for a, _, _ in ARMS],
        "ceilings": ceilings,
        "program_arms": {
            arm: {
                "kinds": block["kinds"],
                "counts": counts(block["runs"]),
                "q": round(1.0 - counts(block["runs"])["pass"] / max(1, len(block["runs"])), 4),
                "failure_modes": {
                    m: sum(1 for r in block["runs"] if r["failure_mode"] == m)
                    for m in sorted({r["failure_mode"] for r in block["runs"]})
                },
                "exception_classes": {
                    e: sum(1 for r in block["runs"] if r["exception_class"] == e)
                    for e in sorted({r["exception_class"] for r in block["runs"]})
                },
                "runs": block["runs"],
                "schedule": block["schedule"],
            }
            for arm, block in program_arms.items()
        },
        "reactive_arms": reactive_arms,
        "reactive_total_usd": round(reactive_total, 6),
        "reactive_stopped": reactive_stopped,
        "reactive_model": args.model if args.reactive else None,
        "reactive_max_steps": args.max_steps if args.reactive else None,
        "n_program_runs": len(all_runs),
        "wall_s": round(time.time() - t_start, 1),
    }

    (out_dir / "warex_pilot.json").write_text(json.dumps(summary, indent=1))
    (out_dir / "table.md").write_text(_table_md(summary))
    print(f"\nwrote {out_dir / 'warex_pilot.json'}")
    print(f"wrote {out_dir / 'table.md'}")
    print()
    print(_table_md(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
