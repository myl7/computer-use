"""Fragility probe: replay a compiled Android family program under adb-level
perturbations, with zero model tokens.

The Android counterpart of ``guiexp/drift_probe.py``, answering the same two
questions on a perturbation surface we did not author (see
docs/ui-instability-benchmarks.md, Rank 3, and perturb.py for the arms).

  survival   does the compiled program still pass the family's own oracle
             when the device configuration moves under it (B-MoCA arms) or an
             interruption lands mid trajectory (D-GARA arms)?
  loudness   when it stops passing, does it raise, or does it return happily
             over a device that is wrong?

What this feeds back into the decision law (docs/methods-v3.md):

  q          per-use failure under an arm, 1 - pass rate on the held-out
             bindings, reported per arm and split loud versus silent.
  h          break-given-change. Reported as D-GARA's Robust Success Rate
             restricted to the bindings that pass clean, so a family whose
             program is already broken cannot inflate the hazard.
  p          NOT measured here. p is the admission gate (gate_runner.py). The
             clean arm reruns the gate bindings, so a clean arm that does not
             reproduce the recorded gate number means the device drifted, not
             that p moved.

The third output is the one neither the web drift probe nor either source
benchmark produces: a locator-strategy tag for the step that failed. Every
``device.find`` call and every direct index click is recorded with the kind of
locator it used, so the summary can say which locator kinds survive which
arms. On this codebase the reachable kinds are text, content_desc and
coordinate; ``resource_id`` is in the taxonomy and will always read zero,
because ProgramDevice's element dicts do not carry resource names at all.
That zero is a finding about our compiler, not a gap in the probe.

Single seed by default. Zero model calls unless --reactive is passed, which
adds one discover-condition episode per arm under a hard spend cap.

    ../.venv-android/bin/python -m guiexp_android.fragility_probe \
        --family ContactsAddContact --model z-ai/glm-5.3-flash --k 5
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from . import android_env, perturb
from .gate_runner import GATE_SEED_DEFAULT, heldout_bindings
from .perturb import Adb, StepInjector, arm_session, device_fingerprint, fingerprint_hash
from .program_runtime import Program, ProgramDevice, ProgramRunner, program_from_path

RESULTS_ROOT = android_env.REPO_ROOT / "experimental-results" / "guiexp_android"
OUT_DIR_DEFAULT = RESULTS_ROOT / "t18_fragility"

# t14_compilepath keys its per-family directories by app rather than family.
T14_KEYS = {
    "ContactsAddContact": "contacts",
    "SimpleCalendarAddOneEvent": "calendar",
    "MarkorCreateNote": "markor",
}

OUTCOMES = ("PASS", "LOUD", "SILENT_WRONG", "FALSE_ALARM")
LOCATOR_KINDS = ("resource_id", "text", "content_desc", "coordinate", "none")


# -- locating the compiled program -----------------------------------------


def find_program(family: str, model: str, results_root: Path | None = None) -> Path:
    """The compiled program for (family, model): t16 first, then t14.

    t16_build is the current build batch and writes ``verified_program.py``
    per family; t14_compilepath predates it and keys directories by app, with
    ``summary.json`` naming the attempt that passed the gate. Preferring t16
    means the probe automatically moves to the newer programs as the batch
    lands, without the caller changing the command.
    """
    root = results_root or RESULTS_ROOT
    slug = model.replace("/", "_")

    t16 = root / "t16_build" / slug / family
    for candidate in (t16 / "verified_program.py", t16 / "family_program.py"):
        if candidate.is_file():
            return candidate

    key = T14_KEYS.get(family)
    if key:
        t14 = root / "t14_compilepath" / slug / key
        summary_path = root / "t14_compilepath" / slug / "summary.json"
        attempt = None
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text())
            entry = (summary.get("families") or {}).get(key) or {}
            attempt = entry.get("best_attempt")
        candidates = []
        if attempt:
            candidates.append(t14 / f"attempt{attempt}" / "family_program.py")
        candidates += sorted(t14.glob("attempt*/family_program.py"))
        for candidate in candidates:
            if candidate.is_file():
                return candidate

    raise FileNotFoundError(
        f"no compiled program for family={family} model={model} under {root} "
        f"(looked in t16_build/{slug}/{family} and t14_compilepath/{slug}/{key})"
    )


# -- locator taxonomy -------------------------------------------------------

# Non-locating filters. They narrow a find, they do not address an element,
# so a find made only of these is not a locator of any kind.
_FILTER_KEYS = frozenset({"clickable", "editable", "scrollable", "focusable",
                          "selected", "checked", "long_clickable"})

# Ranked most to least stable, which is also the order a mixed criteria dict
# is charged to: {"description": ..., "text": ...} is a text locator, because
# the text is what the language arm breaks first.
_KIND_BY_KEY = (
    ("resource_id", ("resource_id", "resource_name", "id")),
    ("text", ("text", "contains", "hint")),
    ("content_desc", ("description", "content_description", "tooltip")),
    ("coordinate", ("index", "x", "y")),
)


def classify_criteria(criteria: dict) -> str:
    """Which locator kind a find/click was addressed by.

    ``coordinate`` covers a direct element index as well as raw pixels: on
    Android the index is a position in the a11y dump, so it moves for the
    same reasons a coordinate does (an inserted dialog, a reflowed list).
    """
    keys = {k for k, v in (criteria or {}).items() if v is not None} - _FILTER_KEYS
    if not keys:
        return "none"
    for kind, kind_keys in _KIND_BY_KEY:
        if keys & set(kind_keys):
            return kind
    return "none"


# -- the tracing device -----------------------------------------------------


class TracingMixin:
    """Records every locator use and fires a step arm after N actions.

    Mixed in ahead of a ProgramDevice-shaped base so the real ``find`` and
    ``_require_index`` still run; the mixin only observes. Kept separate from
    :class:`TracingDevice` so the unit tests can put it over a fake device
    with no emulator behind it.
    """

    injector: StepInjector | None = None

    @property
    def locator_trace(self) -> list[dict]:
        return self.__dict__.setdefault("_locator_trace", [])

    @property
    def action_log(self) -> list[dict]:
        return self.__dict__.setdefault("_action_log", [])

    def _record_locator(self, criteria: dict, hit: bool, what: str) -> None:
        self.locator_trace.append({
            "seq": len(self.locator_trace),
            "after_actions": len(self.action_log),
            "what": what,
            "kind": classify_criteria(criteria),
            "criteria": {k: v for k, v in (criteria or {}).items() if v is not None},
            "hit": bool(hit),
        })

    # -- observed calls ----------------------------------------------------

    def find(self, **criteria):
        index = super().find(**criteria)
        self._record_locator(criteria, index is not None, "find")
        return index

    def _require_index(self, index, find_kwargs, what):
        if index is not None:
            # Addressed straight by element index: no find happens, so the
            # locator would otherwise go unrecorded.
            self._record_locator({"index": index}, True, what)
            return index
        return super()._require_index(index, find_kwargs, what)

    def execute(self, action: dict) -> None:
        super().execute(action)
        self.action_log.append(dict(action))
        if self.injector is not None:
            self.injector.after_action(len(self.action_log))


class TracingDevice(TracingMixin, ProgramDevice):
    """ProgramDevice plus the locator trace and the step-arm injector."""

    def __init__(self, env, injector: StepInjector | None = None, **kwargs):
        ProgramDevice.__init__(self, env, **kwargs)
        self.injector = injector


# -- outcome coding ---------------------------------------------------------


def outcome_of(error: str | None, reward: float) -> str:
    """PASS / LOUD / SILENT_WRONG / FALSE_ALARM.

    The first three are drift_probe's coding. FALSE_ALARM is the fourth cell
    the web probe never saw and Android does produce: the program raises
    (usually on its own post-condition check) over a device the oracle is
    happy with. It is a non-pass for q and it is loud, so a caller can fall
    back, but calling it LOUD would overstate the damage.
    """
    passed = reward >= 1.0
    if passed and not error:
        return "PASS"
    if passed and error:
        return "FALSE_ALARM"
    if error:
        return "LOUD"
    return "SILENT_WRONG"


def is_loud(outcome: str) -> bool:
    """Loud means the program said something went wrong."""
    return outcome in ("LOUD", "FALSE_ALARM")


def exception_type(error: str | None) -> str | None:
    """``RuntimeError`` out of ProgramRunner's ``"RuntimeError: msg"``."""
    if not error:
        return None
    head = error.split(":", 1)[0].strip()
    return head or None


def failing_locator(trace: list[dict], outcome: str) -> dict:
    """The locator to blame for a non-pass, with its kind.

    A miss is the strongest evidence, so the last find that returned nothing
    wins. Compiled programs wrap their finds in try/except ladders, so the
    raise itself often points at a different line than the locator that
    actually failed. With no miss anywhere (every locator hit, and the device
    still ended up wrong), the failure is semantic rather than a locator
    failure, and the tag is ``none``.
    """
    if outcome == "PASS":
        return {"kind": None, "reason": "passed"}
    misses = [t for t in trace if not t["hit"]]
    if misses:
        last = misses[-1]
        return {"kind": last["kind"], "criteria": last["criteria"],
                "what": last["what"], "after_actions": last["after_actions"],
                "reason": "last locator miss"}
    if not trace:
        return {"kind": "none", "reason": "no locator was reached"}
    last = trace[-1]
    return {"kind": "none", "criteria": last["criteria"], "what": last["what"],
            "after_actions": last["after_actions"],
            "reason": "every locator hit; failure is not a locator failure"}


# -- one replay -------------------------------------------------------------


def replay_binding(program: Program, family: str, draw: dict, runner: ProgramRunner,
                   arm: perturb.Arm, adb, context: dict) -> dict:
    """One held-out binding under one arm. Zero model tokens.

    Step arms are applied inside the replay by the injector and reverted here
    whatever happened, so an interruption can never leak into the next
    binding.
    """
    step_arm = arm if arm.scope == "step" else None
    injector = StepInjector(step_arm, adb, context)
    device_holder: dict[str, Any] = {}

    def device_factory(env):
        device = TracingDevice(env, injector=injector)
        device_holder["device"] = device
        return device

    t0 = time.time()
    try:
        outcome = runner.run(program, draw["binding"], family,
                             judge_params=draw["params"], device_factory=device_factory)
        if step_arm is not None and injector.armed:
            # The program finished (or died) before the scheduled step. Fire
            # anyway so the arm is not silently the clean arm, and say so.
            injector.force_fire()
    finally:
        injector.revert()

    device = outcome.get("device") or device_holder.get("device")
    trace = list(getattr(device, "locator_trace", []) or [])
    actions = list(getattr(device, "action_log", []) or [])
    error = outcome.get("error")
    reward = float(outcome.get("reward") or 0.0)
    code = outcome_of(error, reward)
    return {
        "binding": draw["binding"],
        "arm": arm.name,
        "outcome": code,
        "passed": code == "PASS",
        "loud": is_loud(code),
        "reward": reward,
        "error": error,
        "exception_type": exception_type(error),
        "locator": failing_locator(trace, code),
        "actions": len(actions),
        "locator_calls": len(trace),
        "locator_kinds_used": sorted({t["kind"] for t in trace}),
        "injection": injector.record() if step_arm is not None else None,
        "wall_s": round(time.time() - t0, 2),
    }


def run_arm(program: Program, family: str, draws: list[dict], runner: ProgramRunner,
            arm: perturb.Arm, adb, verbose: bool = True) -> dict:
    """Every held-out binding under one arm, with the arm's device state held
    for the whole block (session arms) and reverted afterwards no matter what.
    """
    context = {"family": family, "package": perturb.FAMILY_PACKAGES.get(family)}
    runs: list[dict] = []
    before = device_fingerprint(adb)
    session = arm if arm.scope == "session" else None
    skipped: str | None = None
    applied_fp: dict = {}
    if session is not None:
        applied_fp = session.apply(adb, context)
        skipped = session.skipped_reason
    try:
        if skipped:
            if verbose:
                print(f"  arm {arm.name}: skipped ({skipped})", flush=True)
        else:
            for draw in draws:
                record = replay_binding(program, family, draw, runner, arm, adb, context)
                runs.append(record)
                if verbose:
                    label = str(next(iter(record["binding"].values())))[:26]
                    print(f"  {arm.name:<18} {label:<28} {record['outcome']:<13}"
                          f" {record['locator'].get('kind') or '-'}"
                          f" {record['exception_type'] or ''}", flush=True)
    finally:
        if session is not None:
            session.revert(adb)
    after = device_fingerprint(adb)
    return {
        "arm": arm.describe(),
        "skipped": skipped,
        "runs": runs,
        "fingerprint": {
            "before": before,
            "applied": applied_fp or None,
            "after": after,
            "before_hash": fingerprint_hash(before),
            "applied_hash": fingerprint_hash(applied_fp) if applied_fp else None,
            "after_hash": fingerprint_hash(after),
            "reverted_clean": fingerprint_hash(before) == fingerprint_hash(after),
        },
        **counts(runs),
    }


# -- aggregation ------------------------------------------------------------


def counts(runs: list[dict]) -> dict:
    out = {key.lower(): sum(1 for r in runs if r["outcome"] == key) for key in OUTCOMES}
    out["n"] = len(runs)
    out["pass_rate"] = (out["pass"] / len(runs)) if runs else None
    out["loud_share"] = (
        (sum(1 for r in runs if r["loud"] and r["outcome"] != "PASS")
         / max(1, len(runs) - out["pass"])) if runs and len(runs) > out["pass"] else None
    )
    return out


def robust_success_rate(clean_runs: list[dict], arm_runs: list[dict]) -> float | None:
    """D-GARA's RSR: of the bindings that pass clean, how many still pass.

    Keyed on the binding, not on position, so a skipped or partial arm cannot
    silently compare different bindings.
    """
    def key(run: dict) -> str:
        return json.dumps(run["binding"], sort_keys=True)

    clean_pass = {key(r) for r in clean_runs if r["passed"]}
    if not clean_pass:
        return None
    considered = [r for r in arm_runs if key(r) in clean_pass]
    if not considered:
        return None
    return sum(1 for r in considered if r["passed"]) / len(considered)


def locator_survival(per_arm: list[dict]) -> dict:
    """Which locator kinds are blamed for failures, per arm.

    Read as: under this arm, the failing step addressed its element by this
    kind. A kind that never appears here under an arm is a kind that arm did
    not break.
    """
    table: dict[str, dict[str, int]] = {}
    for entry in per_arm:
        row = {kind: 0 for kind in LOCATOR_KINDS}
        for run in entry["runs"]:
            if run["passed"]:
                continue
            kind = run["locator"].get("kind") or "none"
            row[kind] = row.get(kind, 0) + 1
        table[entry["arm"]["name"]] = row
    return table


def summarize(family_results: list[dict]) -> dict:
    """Roll per-family, per-arm results into the numbers the paper needs."""
    arm_names: list[str] = []
    for result in family_results:
        for entry in result["arms"]:
            if entry["arm"]["name"] not in arm_names:
                arm_names.append(entry["arm"]["name"])

    per_arm: dict[str, dict] = {}
    for name in arm_names:
        runs: list[dict] = []
        clean: list[dict] = []
        rsr_terms: list[float] = []
        for result in family_results:
            by_name = {e["arm"]["name"]: e for e in result["arms"]}
            entry = by_name.get(name)
            clean_entry = by_name.get("clean")
            if entry is None:
                continue
            runs += entry["runs"]
            if clean_entry is not None:
                clean += clean_entry["runs"]
                rsr = robust_success_rate(clean_entry["runs"], entry["runs"])
                if rsr is not None:
                    rsr_terms.append(rsr)
        row = counts(runs)
        row["rsr"] = (sum(rsr_terms) / len(rsr_terms)) if rsr_terms else None
        row["break_given_change"] = (1 - row["rsr"]) if row["rsr"] is not None else None
        per_arm[name] = row

    perturbed = [name for name in arm_names if name != "clean"]
    breaks = [per_arm[n]["break_given_change"] for n in perturbed
              if per_arm[n]["break_given_change"] is not None]
    all_runs = [r for result in family_results for e in result["arms"] for r in e["runs"]]
    non_pass = [r for r in all_runs if not r["passed"]]
    return {
        "per_arm": per_arm,
        "arms": arm_names,
        "families": [r["family"] for r in family_results],
        # h's second factor: break probability under a uniform draw over the
        # perturbed arms, the same estimator the web drift probe reports.
        "break_given_change_uniform": (sum(breaks) / len(breaks)) if breaks else None,
        "q_clean": (1 - per_arm["clean"]["pass_rate"]) if per_arm.get("clean")
        and per_arm["clean"]["pass_rate"] is not None else None,
        "loud_share_overall": (sum(1 for r in non_pass if r["loud"]) / len(non_pass))
        if non_pass else None,
        "silent_total": sum(1 for r in non_pass if not r["loud"]),
        "loud_total": sum(1 for r in non_pass if r["loud"]),
        "runs_total": len(all_runs),
        "locator_blame": locator_survival(
            [e for result in family_results for e in result["arms"]]
        ),
    }


def table_md(summary: dict) -> str:
    """The per-arm table, in the shape the paper's drift table already uses."""
    lines = [
        "| arm | n | pass | loud | silent | false alarm | pass rate | RSR |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in summary["arms"]:
        row = summary["per_arm"][name]
        rate = "-" if row["pass_rate"] is None else f"{row['pass_rate']:.2f}"
        rsr = "-" if row.get("rsr") is None else f"{row['rsr']:.2f}"
        lines.append(
            f"| {name} | {row['n']} | {row['pass']} | {row['loud']} | "
            f"{row['silent_wrong']} | {row['false_alarm']} | {rate} | {rsr} |"
        )
    lines.append("")
    lines.append("| arm | resource_id | text | content_desc | coordinate | none |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for name, row in summary["locator_blame"].items():
        lines.append(
            f"| {name} | {row.get('resource_id', 0)} | {row.get('text', 0)} | "
            f"{row.get('content_desc', 0)} | {row.get('coordinate', 0)} | "
            f"{row.get('none', 0)} |"
        )
    return "\n".join(lines)


# -- reactive comparison ----------------------------------------------------


def run_reactive_arm(family: str, arm: perturb.Arm, adb, env, model: str,
                     out_dir: Path, seed: int = 0, client=None) -> dict:
    """One discover-condition episode under one arm, for the fallback price.

    This is the only part of the probe that spends money. The caller enforces
    the cap; this function reports what one episode cost.
    """
    from .explore import step_cap
    from .runner import run_episode

    context = {"family": family, "package": perturb.FAMILY_PACKAGES.get(family)}
    with arm_session(arm, adb, context) as state:
        if state["skipped"]:
            return {"arm": arm.name, "family": family, "skipped": state["skipped"],
                    "cost_usd": 0.0, "tokens": 0}
        final = run_episode(
            family=family, condition="discover", seed=seed, model=model,
            obs_mode="screenshot+ax", max_steps=step_cap(family),
            out_dir=out_dir / f"reactive_{arm.name}", client=client, env=env,
            keep_emulator=True, close_env=False,
        )
    return {
        "arm": arm.name,
        "family": family,
        "seed": seed,
        "success": final.get("success"),
        "steps": final.get("steps"),
        "tokens": final.get("total_tokens"),
        "cost_usd": final.get("total_cost_usd"),
    }


# -- driver -----------------------------------------------------------------


def run_family(family: str, program_path: Path, arms: list[perturb.Arm], runner: ProgramRunner,
               adb, k: int = 5, gate_seed: int = GATE_SEED_DEFAULT,
               binding_namespace: str = "gate", verbose: bool = True) -> dict:
    """Every arm on one family's compiled program."""
    _module, program = program_from_path(program_path)
    draws = probe_bindings(family, k, gate_seed, binding_namespace)
    entries = [run_arm(program, family, draws, runner, arm, adb, verbose=verbose)
               for arm in arms]
    return {
        "family": family,
        "program": str(program_path),
        "k": k,
        "gate_seed": gate_seed,
        "binding_namespace": binding_namespace,
        "bindings": [d["binding"] for d in draws],
        "arms": entries,
    }


def probe_bindings(family: str, k: int, seed: int, namespace: str) -> list[dict]:
    """The bindings to replay on.

    ``gate`` (default) reuses gate_runner's held-out draws, so the clean arm
    reruns exactly the gate and reproduces its recorded number, which is the
    probe's own sanity check. ``fresh`` walks a different offset of the same
    seed-deterministic pool, for a second, non-overlapping sample.
    """
    if namespace == "gate":
        return heldout_bindings(family, k, seed=seed)
    if namespace == "fresh":
        pool = heldout_bindings(family, 2 * k, seed=seed)
        return pool[k:]
    raise ValueError(f"unknown binding namespace {namespace!r}; use gate or fresh")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--family", action="append", default=None,
                        help="family to probe; repeatable; default all seven")
    parser.add_argument("--model", default="z-ai/glm-5.3-flash",
                        help="which model's compiled program to replay")
    parser.add_argument("--program", default=None,
                        help="explicit program .py (only valid with a single --family)")
    parser.add_argument("--arms", nargs="*", default=None, help="subset of arm names")
    parser.add_argument("--k", type=int, default=5, help="held-out bindings per arm")
    parser.add_argument("--gate-seed", type=int, default=GATE_SEED_DEFAULT)
    parser.add_argument("--bindings", choices=("gate", "fresh"), default="gate")
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve programs, bindings and arms and print the plan; "
                             "touches no device")
    parser.add_argument("--reactive", action="store_true",
                        help="also run one discover episode per arm (costs money)")
    parser.add_argument("--reactive-model", default=None,
                        help="model for the reactive arms (default --model)")
    parser.add_argument("--reactive-cap-usd", type=float, default=1.00,
                        help="hard cap; the reactive stage stops when spend crosses it")
    parser.add_argument("--reactive-seed", type=int, default=0)
    parser.add_argument("--serial", default=None, help="adb serial (default the AVD)")
    parser.add_argument("--keep-emulator", action="store_true",
                        help="do not shut down an emulator this run booted")
    args = parser.parse_args(argv)

    from .conditions import FAMILIES

    families = args.family or list(FAMILIES)
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        parser.error(f"unknown families {unknown}")
    if args.program and len(families) != 1:
        parser.error("--program needs exactly one --family")

    try:
        arms = perturb.select_arms(args.arms)
    except ValueError as exc:
        parser.error(str(exc))

    programs: dict[str, Path] = {}
    missing: list[str] = []
    for family in families:
        try:
            programs[family] = (Path(args.program) if args.program
                                else find_program(family, args.model))
        except FileNotFoundError as exc:
            missing.append(str(exc))
    if missing and not args.dry_run:
        for line in missing:
            print(f"skip: {line}")
    families = [f for f in families if f in programs]
    if not families:
        print("no compiled program found for any requested family")
        return 2

    out_dir = Path(args.out_dir) / args.model.replace("/", "_")

    if args.dry_run:
        replays = len(families) * len(arms) * args.k
        plan = {
            "families": families,
            "programs": {f: str(p) for f, p in programs.items()},
            "arms": [a.describe() for a in arms],
            "k": args.k,
            "bindings": {f: [d["binding"] for d in
                             probe_bindings(f, args.k, args.gate_seed, args.bindings)]
                         for f in families},
            "replays": replays,
            "model_tokens": 0,
            "reactive_episodes": len(families) * len(arms) if args.reactive else 0,
            "out_dir": str(out_dir),
        }
        print(json.dumps(plan, indent=1))
        if missing:
            print(f"({len(missing)} families have no compiled program yet)")
        return 0

    adb = Adb(serial=args.serial)
    env = android_env.AndroidWorldEnv()
    runner = ProgramRunner(env)
    results: list[dict] = []
    reactive: list[dict] = []
    t0 = time.time()
    try:
        for family in families:
            print(f"[{family}] {programs[family]}", flush=True)
            results.append(run_family(
                family, programs[family], arms, runner, adb,
                k=args.k, gate_seed=args.gate_seed, binding_namespace=args.bindings,
            ))
        if args.reactive:
            spent = 0.0
            model = args.reactive_model or args.model
            for family in families:
                for arm in arms:
                    if spent >= args.reactive_cap_usd:
                        print(f"reactive: spend cap ${args.reactive_cap_usd:.2f} reached",
                              flush=True)
                        break
                    record = run_reactive_arm(
                        family, arm, adb, env, model,
                        out_dir / family, seed=args.reactive_seed,
                    )
                    spent += record.get("cost_usd") or 0.0
                    record["cumulative_cost_usd"] = round(spent, 6)
                    reactive.append(record)
                    print(f"  reactive {family}/{arm.name}: {record.get('success')} "
                          f"${record.get('cost_usd') or 0:.4f} (cum ${spent:.4f})", flush=True)
                else:
                    continue
                break
    finally:
        env.close()
        if not args.keep_emulator:
            env.stop_emulator()

    summary = summarize(results)
    record = {
        "model": args.model,
        "families": families,
        "k": args.k,
        "gate_seed": args.gate_seed,
        "binding_namespace": args.bindings,
        "arms": [a.describe() for a in arms],
        "results": results,
        "summary": summary,
        "reactive": reactive,
        "reactive_cost_usd": round(sum(r.get("cost_usd") or 0.0 for r in reactive), 6),
        "replay_tokens": 0,
        "wall_s": round(time.time() - t0, 1),
        "record_type": "fragility",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fragility.json").write_text(json.dumps(record, indent=1, default=str))
    (out_dir / "fragility.md").write_text(table_md(summary) + "\n")
    print(table_md(summary))
    print(f"wrote {out_dir / 'fragility.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
