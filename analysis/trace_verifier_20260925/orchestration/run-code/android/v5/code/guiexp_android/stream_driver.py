"""Online stream driver: one arrival stream end to end under one policy.

The bridge between t2sim's stream account (``t2sim.sim.run_policy``) and the
real guiexp_android machinery.  Per arrival it

  1. loads the next arrival of the stream (``t2sim.streams`` loaders, the
     t2sim real-stream family assignment: measured layouts round-robin over
     the SORTED family names, ``experiments.real_params``' rule),
  2. draws/looks up the arrival's binding for its family,
  3. asks the policy -- ``ours`` through
     ``guiexp_android.trigger_online.OnlineTrigger`` (Algorithm 1), the fixed
     rules through the same state machine's trivial branches, ``toolpro_port``
     through its windowed-rate rule,
  4. executes the decision, and
  5. appends one JSONL ledger record and finally a summary JSON.

Two execution modes:

  * ``--mock`` (used by the parity test): episode outcomes are sampled from
    the same per-family constants the engine replays (agent cost c, program
    cost d, per-use failure q, break hazards, gate rate p) through a
    PRE-GENERATED tape -- a ``t2sim.sim.CoinBook``-shaped object whose coins
    are ADDRESSED by (family, index), exactly like the engine's common
    random numbers, so the mock consumes the identical tape a
    ``sim.run_policy`` run consumes and the parity test can assert identical
    decisions and identical totals.  No RNG of its own: feed the same
    CoinBook, get the same run.
  * real mode (wired here, run later on a server): react = one full agent
    episode via the ``t21`` paired-replay entry point
    (``pairreplay.worker.run_replay_episode``); serve = the deployment chain
    (``deploy_runner.run_single_use``: extraction -> type check -> program
    -> oracle) over the family's deploy-use binding pool; compile = the
    ``build_protocol.run_build`` invocation (k_min = 3 building traces,
    held-out gate, hybrid-repair verification, four-of-five admission).
    These seams need a device and an API key; nothing in mock mode imports
    them.

    cd computer-use
    ../.venv-android/bin/python -m guiexp_android.stream_driver --policy ours \
        --stream sepsis --n-arrivals 60 --seed 7 --model z-ai/glm-5.3-flash \
        --out /tmp/stream_demo --mock
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ``computer-use`` is not an importable identifier; both this package and
# t2sim expect the parent directory on sys.path (same convention as t2sim).
# t2sim.experiments additionally does BARE intra-package imports (import
# sim / import streams), so the t2sim directory itself must be importable
# too -- the bootstrap its own entry modules perform.
_PKG_PARENT = Path(__file__).resolve().parents[1]
for _entry in (str(_PKG_PARENT), str(_PKG_PARENT / "t2sim")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from t2sim import sim as t2sim_sim                      # noqa: E402
from t2sim import streams as t2sim_streams              # noqa: E402

from .trigger_online import OnlineTrigger               # noqa: E402

POLICIES = ("ours", "always_reactive", "always_compile", "toolpro_port")

# The four stream variants of the mixed-family experiment, as t2sim stream
# specs (constants.measured.v3.json 'streams' block names wiki_A / wiki_B and
# the old real-stream keys sepsis / bpi2019).
STREAM_SPECS = {
    "sepsis": {"format": "old_real_streams", "key": "sepsis"},
    "bpi2019": {"format": "old_real_streams", "key": "bpi2019"},
    "wiki_tools": {"format": "wiki_jsonl",
                   "path": "datasets/wiki-stream/stream_tool_hot_tail.jsonl"},
    "wiki_humans": {"format": "wiki_jsonl",
                    "path": "datasets/wiki-stream/stream_content_tail.jsonl",
                    "window": 100000},
}

DEFAULT_CONSTANTS = "constants.measured.v3.a1.json"


# ---------------------------------------------------------------------------
# streams and the family -> layout assignment
# ---------------------------------------------------------------------------

def _ensure_real_streams_source() -> None:
    """Repoint t2sim's old-real-streams file if this checkout lacks it.

    ``t2sim.streams`` reads its module constant ``OLD_REAL_STREAMS``; where
    the raw logs were never converted in this checkout, a sibling checkout's
    copy (or ``$T2SIM_REAL_STREAMS_JSON``) is used read-only.  No file is
    modified.
    """
    import os

    default = t2sim_streams.OLD_REAL_STREAMS
    if default.is_file():
        return
    candidates = []
    if os.environ.get("T2SIM_REAL_STREAMS_JSON"):
        candidates.append(Path(os.environ["T2SIM_REAL_STREAMS_JSON"]))
    siblings = sorted((_PKG_PARENT.parent).glob(
        "computer-use*/experimental-results/openapps/real-streams/"
        "real_streams.json"))
    candidates.extend(siblings)
    for cand in candidates:
        if cand.is_file():
            t2sim_streams.OLD_REAL_STREAMS = cand
            return
    raise FileNotFoundError(
        f"{default} is missing; set $T2SIM_REAL_STREAMS_JSON to a "
        f"real_streams.json copy (sepsis/helpdesk/bpi2019)")


def load_arrival_stream(name: str, n_arrivals: int | None = None) -> list[str]:
    """The arrival sequence of a stream variant, sliced to ``n_arrivals``."""
    if name not in STREAM_SPECS:
        raise ValueError(f"unknown stream {name!r}; expected one of "
                         f"{sorted(STREAM_SPECS)}")
    _ensure_real_streams_source()
    stream = t2sim_streams.load_stream(dict(STREAM_SPECS[name]))
    return stream[:n_arrivals] if n_arrivals is not None else stream


def assign_layouts(families: set[str], layouts: list[str]) -> dict[str, str]:
    """The engine's round-robin: sorted family names over the cost set's
    layouts in constants order (``experiments.real_params``, verbatim)."""
    return {name: layouts[i % len(layouts)]
            for i, name in enumerate(sorted(families))}


def load_constants(path: str | Path | None = None) -> dict:
    path = Path(path) if path else (
        Path(t2sim_streams.__file__).parent / DEFAULT_CONSTANTS)
    return json.loads(Path(path).read_text())


def deployment_mech(constants: dict) -> dict:
    """The trigger block as the deployment runs it.

    ``experiments.trigger_mech`` reads the block; the constants' pre-estimator
    boolean ``spend_cap: true`` is read as Algorithm 1's realized-saving
    credit (paper appendix app:cap: realized savings plus projected savings
    is the allowance), which is what the online trigger implements.
    """
    from t2sim import experiments as t2exp

    mech = t2exp.trigger_mech(constants)
    if mech.get("spend_cap") is True:
        mech["spend_cap"] = "realized"
    return mech


# ---------------------------------------------------------------------------
# MOCK execution: the engine's outcome channels over an addressed tape
# ---------------------------------------------------------------------------

def mock_program_use(profile: dict, tape, family: str, use_index: int,
                     q_now: float) -> tuple[float, bool]:
    """One program-served use; returns (cost, broke).

    Verbatim mirror of ``sim.FamilyState._serve_program`` over the tape's
    addressed coins (use / death / silence), so a mock run consumes the
    tape exactly as ``sim.run_policy`` does.
    """
    c, d = profile["c"], profile["d"]
    h = profile.get("h", 0.0)
    h_env = profile.get("h_env", 0.0)
    sigma = profile.get("sigma", 0.0)
    r_fb = profile.get("r_fallback", 1.0)
    pen = profile.get("silent_penalty", 1.0)
    coin = tape.use(family, use_index)
    if h > 0.0:                       # LEGACY drift channel (validation only)
        if coin < h:
            return c + d, True
        return d, False
    if h_env <= 0.0 and sigma <= 0.0 and r_fb == 1.0:
        if coin < q_now:
            return d + c, False
        return d, False
    # environment-level fragility: both channels act on the same use
    died = h_env > 0.0 and tape.death(family, use_index) < h_env
    if not (died or coin < q_now):
        return d, False
    if sigma > 0.0 and tape.silence(family, use_index) < sigma:
        return d + pen * c, False     # silent: artifact survives unnoticed
    return d + r_fb * c, died


def _sanitise(obj):
    """JSON-safe copy: inf/nan floats (an inestimable c_eff) become null."""
    if isinstance(obj, float):
        return obj if obj == obj and abs(obj) != float("inf") else None
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# REAL execution seams (wired here, exercised later on a server)
# ---------------------------------------------------------------------------

def binding_pair(family: str, index: int) -> tuple[str, dict]:
    """(goal, ground-truth binding) at a binding index of the family's
    deterministic deploy-use pool (``deploy_runner.deploy_uses``) -- the same
    pool the t21 replays and the deploy runs draw from."""
    from .deploy_runner import deploy_uses

    return deploy_uses(family, index + 1)[index]


def run_reactive_episode_real(family: str, index: int, model: str,
                              out_dir: Path, client, env) -> dict:
    """SEAM react: one full agent episode for the arrival's binding.

    The ``t21`` deploy_use=false replay entry point
    (``pairreplay.worker.run_replay_episode``): the task instance is built
    from the ground-truth binding, the goal block wraps the pool's
    natural-language goal, the loop / record schema / auto-termination are
    ``runner.run_episode``'s.
    """
    from .pairreplay.worker import run_replay_episode

    goal, binding = binding_pair(family, index)
    item = {"family": family, "use_index": index, "goal": goal,
            "binding": binding, "deploy": {}}
    final = run_replay_episode(item, client, env, Path(out_dir))
    return {"success": bool(final["success"]),
            "tokens_raw": final["total_tokens"],
            "cost_usd": final.get("total_cost_usd"), "raw": final}


def run_serve_use_real(family: str, index: int, program, model: str,
                       client, runner) -> dict:
    """SEAM serve: the program-runtime chain for one use.

    The t21 deploy_use=true entry point (``deploy_runner.run_single_use``):
    ONE extraction call, a type check with one bounded retry, the compiled
    program, the family's own oracle.
    """
    from .compiler import binding_to_params
    from .deploy_runner import run_single_use

    goal, expected = binding_pair(family, index)
    expected_params = binding_to_params(family, expected)
    rec = run_single_use(program, family, goal, expected, expected_params,
                         client, model, runner)
    return {"success": bool(rec["success"]),
            "tokens_raw": rec["tokens"], "cost_usd": rec["cost_usd"],
            "raw": rec}


def run_compile_real(family: str, model: str, out_dir: Path, env,
                     client=None, k_traces: int = 3,
                     max_cost_usd: float | None = None) -> dict:
    """SEAM compile: the build protocol's compile stage, k_min traces.

    ``build_protocol.run_build`` is the invocation pattern of record:
    k_traces building episodes (the template's three-trace eligibility),
    translator, builder, held-out gate, hybrid-repair verification with the
    four-of-five admission gate.  Returns the build record; the caller reads
    ``verification.admitted`` and the artifact on disk.
    """
    from . import build_protocol

    seeds = tuple(range(1, k_traces + 1))     # seed 0 stays the test instance
    return build_protocol.run_build(
        family, model, Path(out_dir), env, seeds=seeds, client=client,
        max_cost_usd=max_cost_usd)


class RealRuntime:
    """Owns the device, the LLM client and the program runner for real runs.

    Constructed lazily so ``--mock`` never touches the emulator stack.  The
    price-weighted conversion of the raw records happens through
    ``to_pw``/the constants' price sheet once the server runs land (the raw
    prompt/cached/completion totals are preserved in the ledger records).
    """

    def __init__(self, model: str, out_root: Path):
        self.model = model
        self.out_root = Path(out_root)
        self.env = None
        self.client = None
        self.runner = None
        self.programs: dict[str, object] = {}
        self.spent_usd = 0.0

    def ensure(self) -> None:
        if self.env is not None:
            return
        from . import android_env
        from .compiler import _openai_client
        from .program_runtime import ProgramRunner

        self.env = android_env.AndroidWorldEnv()
        self.client = _openai_client()
        self.runner = ProgramRunner(self.env)

    def close(self) -> None:
        if self.env is not None:
            self.env.close()
            self.env.stop_emulator()
            self.env = None

    def react(self, family: str, index: int, family_dir: Path) -> dict:
        self.ensure()
        rec = run_reactive_episode_real(family, index, self.model,
                                        family_dir / f"react_u{index:05d}",
                                        self.client, self.env)
        self.spent_usd += rec["cost_usd"] or 0.0
        return rec

    def serve(self, family: str, index: int) -> dict:
        self.ensure()
        rec = run_serve_use_real(family, index, self.programs[family],
                                 self.model, self.client, self.runner)
        self.spent_usd += rec["cost_usd"] or 0.0
        return rec

    def compile(self, family: str, k_traces: int,
                max_cost_usd: float | None) -> dict:
        self.ensure()
        out_dir = self.out_root / "compiles" / family
        record = run_compile_real(family, self.model, out_dir, self.env,
                                  client=self.client, k_traces=k_traces,
                                  max_cost_usd=max_cost_usd)
        self.spent_usd += record.get("total_cost_usd") or 0.0
        verified = bool((record.get("verification") or {}).get("admitted"))
        if verified:
            from .program_runtime import program_from_source
            from . import build_protocol as bp

            artifact = bp.resumed_verification(out_dir)["final_artifact"]
            _module, program = program_from_source(artifact)
            self.programs[family] = program
        return {"verified": verified, "raw": record}


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------

def make_trigger(constants: dict, stream: set[str], policy: str,
                 k_min: int, mech: dict | None,
                 cost_set: str | None = None) -> OnlineTrigger:
    """The policy's trigger over the engine's family assignment."""
    cs_name = cost_set or constants.get("e4", {}).get(
        "cost_set") or next(iter(constants["cost_sets"]))
    layouts = list(constants["cost_sets"][cs_name]["layouts"])
    layout_of = assign_layouts(stream, layouts)
    return OnlineTrigger.from_constants(
        constants, layout_of, cs_name, k_min=k_min,
        mech=mech or deployment_mech(constants), policy=policy)


def run_stream(policy: str, stream: list[str], constants: dict,
               model: str = "mock", seed: int = 0, mock: bool = True,
               tape=None, k_min: int = 3, mech: dict | None = None,
               cost_set: str | None = None, out_dir: Path | str | None = None,
               max_cost_usd: float | None = None,
               runtime: RealRuntime | None = None,
               stream_name: str | None = None,
               trigger: OnlineTrigger | None = None) -> dict:
    """Drive one arrival stream end to end; returns the summary record.

    Mock mode (``mock=True``) requires ``tape`` -- any object with the
    ``sim.CoinBook`` protocol (``gate``/``use``/``binding``/``death``/
    ``silence``, each addressed by family and index); pass the SAME tape a
    ``sim.run_policy`` run uses and the parity pairing is exact.  Real mode
    ignores the tape and needs a ``runtime``.  ``trigger`` overrides the
    internally constructed policy state machine (tests hand-match the sim
    side's family constants this way).
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    if trigger is None:
        trig = make_trigger(constants, set(stream), policy, k_min, mech,
                            cost_set)
    else:
        trig = trigger
    cs_name = cost_set or constants.get("e4", {}).get(
        "cost_set")
    if cs_name is None:
        sets = constants.get("cost_sets") or {}
        cs_name = next(iter(sets)) if sets else "synthetic"
    if mock and tape is None:
        raise ValueError("mock mode needs the pre-generated tape "
                         "(a t2sim.sim.CoinBook)")

    ledger: list[dict] = []
    compile_points: dict[str, list[dict]] = {}
    # The token total accumulates in the engine's per-arrival order
    # (tax, then compile, then service) so the parity totals are bit-equal,
    # not just close.
    totals = {"tokens_pw": 0.0, "tau_pw": 0.0, "compile_pw": 0.0,
              "service_pw": 0.0, "attempts": 0, "admitted": 0, "failed": 0}
    n_success = 0
    out_path = Path(out_dir) if out_dir else None
    if out_path is not None:
        (out_path / "ledger.jsonl").parent.mkdir(parents=True,
                                                 exist_ok=True)
        ledger_fh = (out_path / "ledger.jsonl").open("w")
    else:
        ledger_fh = None

    def emit(record: dict) -> None:
        ledger.append(record)
        if ledger_fh is not None:
            ledger_fh.write(json.dumps(_sanitise(record)) + "\n")

    for t, name in enumerate(stream):
        t0 = time.time()
        # The CoinBook's binding() returns the DERIVED index (it applies
        # min(space-1, int(coin * space)) itself), so hand the trigger the
        # finished id -- the same expression the engine uses.
        binding_idx = (tape.binding(name, trig.peek_k(name),
                                    trig.state(name).binding_space)
                       if mock else None)
        binding = trig.observe_arrival(
            name, binding=(f"b{binding_idx}" if binding_idx is not None
                           else None))
        tax = trig.tau_now()
        decision = trig.decide(name)
        snapshot = dict(trig.last_snapshot or {})

        verified = None
        compile_pw = 0.0
        if decision == "compile":
            C_k, C_fail_k, p_k = trig.compile_price_info(name)
            attempt = trig.peek_attempt_index(name)
            if mock:
                verified = tape.gate(name, attempt) < p_k
                compile_pw = C_k if verified else C_fail_k
            else:
                rec = runtime.compile(
                    family=name, k_traces=k_min, max_cost_usd=max_cost_usd)
                verified = rec["verified"]
                compile_pw = 0.0     # pw conversion is a server-side step
                snapshot["raw"] = True
            trig.observe_compile(name, verified, compile_pw)
            totals["attempts"] += 1
            totals["admitted" if verified else "failed"] += 1
            totals["compile_pw"] += compile_pw
            compile_points.setdefault(name, []).append(
                {"t": t, "verified": bool(verified)})

        # (5) serve: a program bought THIS arrival serves the deciding
        # arrival itself, exactly like the engine's step order.
        profile = trig.state(name).profile
        served_by_program = (trig.has_live_program(name)
                             and policy != "always_reactive")
        broke = False
        if served_by_program:
            use_index = trig.peek_use_index(name)
            if mock:
                service_pw, broke = mock_program_use(
                    profile, tape, name, use_index,
                    q_now=trig.last_q_now())
                success = service_pw == profile["d"]
            else:
                rec = runtime.serve(name, use_index)
                service_pw = float(rec["tokens_raw"])
                success = rec["success"]
        else:
            if mock:
                service_pw = profile["c"]
                success = True
            else:
                rec = runtime.react(
                    name, trig.peek_k(name) - 1,
                    out_path / "episodes" / name
                    if out_path else Path("/tmp/stream_driver") / name)
                service_pw = float(rec["tokens_raw"])
                success = rec["success"]
        # The engine bills a REBINDING ToolPro arrival at c even when its
        # program served the use, while the realized-saving credit is still
        # computed on the program cost -- bill and episode bookkeeping part
        # ways here, exactly like sim's step (5).
        billed_pw = service_pw
        if policy == "toolpro_port" and trig.last_rebinding():
            billed_pw = profile["c"]
        trig.observe_episode(name, service_pw, success, served_by_program,
                             program_broke=broke)

        totals["tau_pw"] += tax
        totals["service_pw"] += billed_pw
        totals["tokens_pw"] += tax
        totals["tokens_pw"] += compile_pw
        totals["tokens_pw"] += billed_pw
        n_success += bool(success)
        emit({"t": t, "family": name, "binding": binding,
              "decision": decision,
              "executor": "program" if served_by_program else "agent",
              "tokens_pw": tax + compile_pw + billed_pw,
              "tax_pw": tax, "compile_pw": compile_pw,
              "service_pw": billed_pw, "success": bool(success),
              "compiled": verified, "program_broke": broke,
              "wall_s": round(time.time() - t0, 4),
              "trigger_state": _sanitise(snapshot)
              if policy == "ours" else None})

    if ledger_fh is not None:
        ledger_fh.close()

    n = len(stream)
    summary = {
        "record_type": "stream_driver_summary",
        "policy": policy, "stream": stream_name,
        "n_arrivals": n, "seed": seed, "model": model, "mock": mock,
        "cost_set": cs_name, "k_min": k_min, "mech": trig.mech,
        "totals": {
            "tokens_pw": totals["tokens_pw"],
            "tau_pw": totals["tau_pw"],
            "compile_pw": totals["compile_pw"],
            "service_pw": totals["service_pw"],
            "n_compiles": totals["attempts"],
            "n_admitted": totals["admitted"],
            "n_failed_compiles": totals["failed"],
            "success_rate": (n_success / n) if n else None,
            "final_library": trig.library_size(),
        },
        "compile_points": compile_points,
        "trigger_state": trig.summary_state(),
    }
    if runtime is not None:
        summary["spent_usd"] = round(runtime.spent_usd, 4)
    if out_path is not None:
        (out_path / "summary.json").write_text(
            json.dumps(_sanitise(summary), indent=1))
    summary["_ledger"] = ledger          # in-memory only; not written
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", required=True, choices=POLICIES)
    parser.add_argument("--stream", required=True,
                        choices=sorted(STREAM_SPECS))
    parser.add_argument("--n-arrivals", type=int, default=None,
                        help="slice of the stream to run (default: all)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--model", default="mock",
                        help="model id (recorded; used by real execution)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--mock", action="store_true",
                        help="mock execution over the seeded coin tape")
    parser.add_argument("--max-cost-usd", type=float, default=None,
                        help="spend cap for real execution (mock: inert)")
    parser.add_argument("--k-min", type=int, default=3,
                        help="traces the compiler needs (Algorithm 1: 3)")
    parser.add_argument("--constants", default=None,
                        help="t2sim constants JSON "
                             f"(default: {DEFAULT_CONSTANTS})")
    parser.add_argument("--cost-set", default=None)
    args = parser.parse_args()

    constants = load_constants(args.constants)
    stream = load_arrival_stream(args.stream, args.n_arrivals)
    tape = t2sim_sim.CoinBook(args.seed * 104729) if args.mock else None
    runtime = None if args.mock else RealRuntime(
        args.model, Path(args.out))
    summary = run_stream(
        policy=args.policy, stream=stream, constants=constants,
        model=args.model, seed=args.seed, mock=args.mock, tape=tape,
        k_min=args.k_min, cost_set=args.cost_set, out_dir=args.out,
        max_cost_usd=args.max_cost_usd, runtime=runtime,
        stream_name=args.stream)
    if runtime is not None:
        runtime.close()
    totals = summary["totals"]
    print(f"{args.policy} on {args.stream}[{summary['n_arrivals']}]: "
          f"{totals['tokens_pw']:.0f} pw-tokens, "
          f"{totals['n_admitted']}/{totals['n_compiles']} compiles admitted, "
          f"success {totals['success_rate']}")
    print(f"wrote {Path(args.out) / 'summary.json'} and ledger.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
