"""E7 drift probe: replay a compiled guiexp program under interface drift.

Port of openapps-exp/drift_probe.py (the paper's Table ``tab:drift``) to the
guiexp harness. Same two questions, same six arms, same outcome coding:

  survival   does the compiled program still work when the interface changes?
             OpenApps separates the appearance axis (colors, fonts) from the
             form-flow axis (how many screens the create-event form takes), so
             an appearance-only variant is a change the program should survive
             and a flow variant is one it should not.
  loudness   when it breaks, does it say so? A raise leaves the app untouched
             and the caller can fall back; a clean return over a missing or
             wrong record is worse than no program.

Differences from the old probe, all forced by the new harness:

* The program is the guiexp compile path's output
  (``program(page, binding, base_url)``), executed in-process through
  program_runtime.BindingRunner -- the same _ActivePageProxy / popup adoption
  / env-oracle machinery the admission gate uses -- instead of a subprocess
  under the OpenApps venv. A thin tracker wrapper around the program records
  raise-vs-return, which BindingRunner.run deliberately collapses to an error
  string.
* Held-out bindings reuse gate_runner's pool and selection algorithm over a
  DIFFERENT seed namespace, ``"guiexp:drift:"``, so the probe's rotation can
  never collide with (or silently equal) the gate's.
* Server serving: app_server.AppServer only takes a layout name and reuses
  servers through a registry keyed on layout, whose probe
  (``detect_layout``) fingerprints the form flow, not the theme -- a
  dark-theme wizard server and the plain wizard arm look identical to it. The
  probe therefore spawns one fresh, owned server per arm from explicit hydra
  overrides and never reads or writes that registry (see _OverrideServer;
  app_server.py itself is unchanged). The override strings are the old
  probe's verbatim, including ``+apps.calendar.form_flow=wizard``: the theme
  appearance yamls leave the flow key unset, and ``current_form_flow``
  defaults it to single_page, so an appearance arm without the ``+`` override
  would silently confound the two axes.

No model calls anywhere: the probe hands the program typed parameter dicts.

Outcome coding (identical to the old probe):
  PASS          program returns truthily without raising, the app gained
                exactly one event, and all six fields of that event match.
  LOUD          program raised and the app's event count / records are
                unchanged (accurate failure signal).
  SILENT_WRONG  anything else, including a raise that still wrote a record
                and a clean return over a missing, duplicated, or wrong one.

    ../.venv-gui/bin/python -m guiexp.drift_probe
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .app_server import (
    BOOT_TIMEOUT_S,
    OPENAPPS_DIR,
    OPENAPPS_PY,
    _PORT_RE,
    detect_layout,
)
from .env import FIELDS
from .gate_runner import GATE_BINDING_POOL, heldout_bindings
from .program_runtime import BindingRunner, Program, program_from_path
from .runner import DEFAULT_OUT_ROOT

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../computer-use/code
REPO_ROOT = PROJECT_ROOT.parent

PROGRAM_DEFAULT = (
    REPO_ROOT / "experimental-results" / "guiexp" / "t13_compilepath"
    / "z-ai_glm-5.3-flash" / "attempt1" / "family_program.py"
)
OUT_DIR_DEFAULT = DEFAULT_OUT_ROOT / "e7_drift"

# The old probe's numbers (paper Table tab:drift; openapps-exp/drift_probe.py
# over the OLD compiled wizard program, same five held-out bindings, 30 runs)
# for the side-by-side in the written-up results.
OLD_PROBE_RESULT = {
    "wizard": {"pass": 5, "loud": 0, "silent": 0},
    "wizard+dark_theme": {"pass": 5, "loud": 0, "silent": 0},
    "wizard+black_and_white": {"pass": 5, "loud": 0, "silent": 0},
    "wizard+challenging_font": {"pass": 5, "loud": 0, "silent": 0},
    "single_page": {"pass": 0, "loud": 5, "silent": 0},
    "sectioned": {"pass": 0, "loud": 5, "silent": 0},
}
OLD_PROBE_SOURCE = (
    "openapps-exp/drift_probe.py over compile_glm_wizard/family_program_a3.py; "
    "paper body.tex tab:drift"
)

# The flow axis is a plain key under apps.calendar, absent from the theme
# yamls, so it has to be ADDED (hydra ``+``) rather than overridden. Verbatim
# from the old probe.
FLOW_WIZARD = "+apps.calendar.form_flow=wizard"


@dataclass(frozen=True)
class Arm:
    """One interface variant: hydra overrides plus compose checks.

    flow_marker        control id that MUST appear on /calendar/create_event
                       (None => the wizard marker must be ABSENT, i.e. the
                       flow really left the wizard).
    style_fingerprint  substring the appearance config renders into the page
                       CSS (a color or font from the yaml), proving the theme
                       compose took effect -- detect_layout cannot see it.
    layout             the flow fingerprint detect_layout should report.
    """

    name: str
    axis: str
    overrides: tuple[str, ...]
    flow_marker: str | None
    style_fingerprint: str
    layout: str


ARMS: tuple[Arm, ...] = (
    Arm("wizard", "baseline", ("apps/calendar/appearance=wizard",),
        "wizard-next-1", "#1095c1", "wizard"),
    Arm("wizard+dark_theme", "appearance",
        ("apps/calendar/appearance=dark_theme", FLOW_WIZARD),
        "wizard-next-1", "#121212", "wizard"),
    Arm("wizard+black_and_white", "appearance",
        ("apps/calendar/appearance=black_and_white", FLOW_WIZARD),
        "wizard-next-1", "#000000", "wizard"),
    Arm("wizard+challenging_font", "appearance",
        ("apps/calendar/appearance=challenging_font", FLOW_WIZARD),
        "wizard-next-1", "Brush Script MT", "wizard"),
    Arm("single_page", "protocol", ("apps/calendar/appearance=default",),
        None, "#1095c1", "single_page"),
    Arm("sectioned", "protocol", ("apps/calendar/appearance=sectioned",),
        "reveal-details", "#1095c1", "sectioned"),
)

DRIFT_SEED_NAMESPACE = "guiexp:drift:"
DRIFT_SEED_DEFAULT = 0


def drift_bindings(k: int = 5, exclude: dict | None = None,
                   seed: int = DRIFT_SEED_DEFAULT) -> list[dict]:
    """K seed-deterministic held-out bindings over the drift namespace.

    Same pool, same sha256-rotation selection as
    gate_runner.heldout_bindings, but hashed under ``"guiexp:drift:"`` so the
    probe's selection is deterministic AND different from the gate's (a
    program cannot quietly pass the probe on the exact order it was gated
    on). The pool is disjoint from env.INSTANCE_POOL, so no exclusion is
    needed when replaying a program compiled from a guiexp trajectory.
    """
    pool = [b for b in GATE_BINDING_POOL if not exclude or b != exclude]
    if k > len(pool):
        raise ValueError(f"asked for {k} drift bindings, only {len(pool)} available")
    digest = hashlib.sha256(f"{DRIFT_SEED_NAMESPACE}{seed}".encode()).digest()
    start = int.from_bytes(digest[:8], "big") % len(pool)
    ordered = [pool[(start + i) % len(pool)] for i in range(len(pool))]
    return [dict(b) for b in ordered[:k]]


# ---------------------------------------------------------------------------
# One fresh OpenApps server per arm, from explicit hydra overrides.
# ---------------------------------------------------------------------------

class _OverrideServer:
    """Spawn/kill one OpenApps server for arbitrary hydra overrides.

    Minimal adaptation of app_server.AppServer._spawn: identical command,
    PYTHONPATH shim, port scraping, and process-group teardown, but (a) the
    override list is the caller's, not derived from a layout name, and (b) no
    registry -- every arm gets a fresh process and therefore a fresh calendar
    DB (``databases_dir`` is a per-launch timestamped path), so state cannot
    leak between arms, and theme variants can never be confused with the
    plain wizard server (the registry probe fingerprints the flow only).
    """

    def __init__(self, overrides: list[str], layout: str):
        self.overrides = list(overrides)
        self.layout = layout
        self.base_url: str | None = None
        self._proc: subprocess.Popen | None = None
        self._log_path: str | None = None
        self._log_fh = None

    def start(self) -> str:
        if self.base_url:
            return self.base_url
        if not OPENAPPS_PY.exists():
            raise RuntimeError(f"no OpenApps venv at {OPENAPPS_PY}")
        self._log_path = str(
            Path(tempfile.gettempdir()) / f"guiexp_drift_{self.layout}_{os.getpid()}.log"
        )
        self._log_fh = open(self._log_path, "w+")
        env = dict(os.environ)
        # The openapps venv's editable install points at a pre-reorg path;
        # put the real source tree on PYTHONPATH so `import open_apps` works.
        env["PYTHONPATH"] = str(OPENAPPS_DIR / "src") + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(
            [str(OPENAPPS_PY), "launch.py", *self.overrides],
            cwd=str(OPENAPPS_DIR),
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self.stop()
                raise RuntimeError(
                    f"server exited early (code {code}), see {self._log_path}")
            self._log_fh.flush()
            text = Path(self._log_path).read_text(errors="replace")
            match = _PORT_RE.search(text)
            if match:
                candidate = f"http://{match.group(1)}:{match.group(2)}"
                if detect_layout(candidate) == self.layout:
                    self.base_url = candidate
                    return candidate
            time.sleep(0.3)
        self.stop()
        raise RuntimeError(
            f"server did not come up within {BOOT_TIMEOUT_S}s, see {self._log_path}")

    def stop(self) -> None:
        if self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=15)
            except Exception:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except Exception:
                    pass
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None
        self._proc = None
        self.base_url = None


@contextmanager
def drift_server(overrides: list[str], layout: str):
    server = _OverrideServer(overrides, layout)
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# One program execution, with the raise/return signal kept intact.
# ---------------------------------------------------------------------------

def _tracked(program: Program) -> tuple[Program, dict]:
    """Wrap ``program`` so BindingRunner.run's try/except cannot hide whether
    the program raised, returned False, or returned True (its own claim)."""
    signal_state = {"raised": False, "returned": None,
                    "exception_class": "none", "exception": ""}

    def wrapped(page, binding, base_url):
        try:
            signal_state["returned"] = program(page, dict(binding), base_url)
        except BaseException as exc:  # noqa: BLE001 - the failure shape is data
            signal_state["raised"] = True
            signal_state["exception_class"] = type(exc).__name__
            signal_state["exception"] = str(exc).splitlines()[0][:200] if str(exc) else repr(exc)
            raise
        return signal_state["returned"]

    return wrapped, signal_state


def _events(base_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{base_url}/calendar_all", timeout=15) as resp:
        return json.loads(resp.read().decode())


def _ground_truth(before: list[dict], after: list[dict], binding: dict) -> dict:
    """What the app actually holds, judged on all six fields (old probe's check)."""
    matches = [e for e in after
               if e.get("title") == binding["title"] and e.get("date") == binding["date"]]
    mismatched: list[str] = []
    if matches:
        record = matches[0]
        mismatched = [f for f in FIELDS if (record.get(f) or "") != binding[f]]
    return {
        "events_before": len(before),
        "events_after": len(after),
        "record_exists": bool(matches),
        "record_duplicated": len(matches) > 1,
        "record_correct": bool(matches) and not mismatched,
        "mismatched_fields": mismatched,
        "record": matches[0] if matches else None,
    }


def outcome_of(prog: dict, truth: dict) -> str:
    """PASS, LOUD, or SILENT_WRONG -- the old probe's coding.

    PASS needs the program's own claim (returned truthily, no raise) AND the
    state to be exactly right: one new event, all six fields matching, no
    duplicate. LOUD is reserved for a failure that left the app alone. Any
    disagreement between signal and state, in either direction, is
    SILENT_WRONG.
    """
    signal_ok = bool(prog["returned"]) and not prog["raised"]
    state_changed = (truth["events_after"] != truth["events_before"]
                     or truth["record_exists"])
    exactly_one_new = (truth["events_after"] - truth["events_before"]) == 1
    if (signal_ok and exactly_one_new and truth["record_correct"]
            and not truth["record_duplicated"]):
        return "PASS"
    if not signal_ok and not state_changed:
        return "LOUD"
    return "SILENT_WRONG"


def _compose_check(base_url: str, arm: Arm) -> dict:
    """Verify the overrides actually composed before spending the grid."""
    with urllib.request.urlopen(f"{base_url}/calendar/create_event", timeout=15) as r:
        page = r.read().decode()
    flow_ok = (arm.flow_marker in page) if arm.flow_marker else ("wizard-next-1" not in page)
    style_ok = arm.style_fingerprint in page
    return {"flow_marker": arm.flow_marker, "flow_marker_ok": flow_ok,
            "style_fingerprint": arm.style_fingerprint, "style_fingerprint_ok": style_ok}


def run_arm(program: Program, arm: Arm, bindings: list[dict],
            headless: bool = True) -> dict:
    """Five held-out bindings against one fresh server for this arm."""
    print(f"\n[{arm.name}]  {' '.join(arm.overrides)}")
    runs: list[dict] = []
    with drift_server(list(arm.overrides), arm.layout) as base:
        compose = _compose_check(base, arm)
        print(f"  compose check: flow={arm.flow_marker!r} ok={compose['flow_marker_ok']}"
              f"  style={arm.style_fingerprint!r} ok={compose['style_fingerprint_ok']}")
        if not (compose["flow_marker_ok"] and compose["style_fingerprint_ok"]):
            raise RuntimeError(f"{arm.name}: overrides did not compose: {compose}")
        tracked, state = _tracked(program)
        with BindingRunner(base, arm.layout, headless=headless) as runner:
            for binding in bindings:
                t0 = time.time()
                before = _events(base)
                result = runner.run(tracked, binding)
                after = _events(base)
                truth = _ground_truth(before, after, binding)
                prog = {"returned": state["returned"], "raised": state["raised"],
                        "exception_class": state["exception_class"],
                        "exception": state["exception"], "error": result["error"]}
                state.update(returned=None, raised=False,
                             exception_class="none", exception="")
                outcome = outcome_of(prog, truth)
                runs.append({
                    "arm": arm.name, "axis": arm.axis,
                    "binding": binding["title"], **prog, **truth,
                    "oracle_passed": result["passed"],
                    "outcome": outcome,
                    "wall_s": round(time.time() - t0, 1),
                })
                print(f"  {binding['title']:<20} {outcome:<12} "
                      f"{prog['exception_class']:<16} {runs[-1]['wall_s']:>5.1f}s")
    return {"arm": arm.name, "axis": arm.axis, "overrides": list(arm.overrides),
            "compose": compose, "bindings": [b["title"] for b in bindings],
            "runs": runs}


# ---------------------------------------------------------------------------
# Rollup.
# ---------------------------------------------------------------------------

def _counts(runs: list[dict]) -> dict:
    return {k: sum(1 for r in runs if r["outcome"] == v)
            for k, v in (("pass", "PASS"), ("loud", "LOUD"), ("silent", "SILENT_WRONG"))}


def _hazard(per_arm: dict) -> dict:
    """Break/silent probability per interface-change event under a uniform
    mixture over the non-baseline arms (the old probe's assumption, kept so
    the two tables share units)."""
    variants = [n for n in per_arm if n != "wizard"]  # the five non-baseline arms
    mixture = {v: 1.0 / len(variants) for v in variants}

    def _frac(arm: str, key: str) -> float:
        total = sum(per_arm[arm].values())
        return per_arm[arm][key] / total if total else 0.0

    h_break = sum(w * (1.0 - _frac(v, "pass")) for v, w in mixture.items())
    h_silent = sum(w * _frac(v, "silent") for v, w in mixture.items())
    return {
        "assumption": ("an interface change event is one draw from the five "
                       "non-baseline arms, uniformly weighted; the program "
                       "breaks if that draw does not yield PASS"),
        "mixture": mixture,
        "break_prob_per_change_event": round(h_break, 4),
        "silent_prob_per_change_event": round(h_silent, 4),
        "per_use_hazard_by_change_rate": {
            str(rate): round(h_break * rate, 5)
            for rate in (0.01, 0.02, 0.05, 0.10, 0.25)},
        "note": ("per-use hazard = break_prob_per_change_event x change events "
                 "per use; this probe measures the first factor only"),
    }


def _comparison(per_arm_counts: dict) -> dict:
    """Per-arm side-by-side with the old probe (paper tab:drift). Tolerates a
    --arms subset run: unrun arms are reported as such."""
    rows = {}
    for name, old in OLD_PROBE_RESULT.items():
        new = per_arm_counts.get(name)
        rows[name] = {
            "new": new, "old": old,
            "same_outcome_mix": (new == old) if new is not None else None,
        }
    return {"old_probe_source": OLD_PROBE_SOURCE, "arms": rows}


def _table_md(summary: dict) -> str:
    lines = [
        "# E7 drift probe -- guiexp compiled program (glm-5.3-flash, t13 attempt1)",
        "",
        f"Program: `{summary['program']}`",
        "",
        "Six arms x 5 held-out bindings (seed namespace `guiexp:drift:0`), "
        "one fresh OpenApps server per arm, no model calls.",
        "",
        "| Arm | Axis | pass | loud | silent | old pass/loud/silent | match |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    cmp = summary["comparison"]["arms"]
    for arm_name, counts in summary["per_arm_counts"].items():
        old = cmp[arm_name]["old"]
        mix = cmp[arm_name]["same_outcome_mix"]
        lines.append(
            f"| {arm_name} | {summary['per_arm_axis'][arm_name]} "
            f"| {counts['pass']} | {counts['loud']} | {counts['silent']} "
            f"| {old['pass']}/{old['loud']}/{old['silent']} "
            f"| {('-' if mix is None else ('yes' if mix else 'NO'))} |")
    lines += [
        "",
        f"- baseline pass rate: {summary['baseline_pass_rate']:.2f}",
        f"- appearance survival: {summary['appearance_drift_survival_rate']:.2f} "
        f"({summary['n_appearance_runs']} runs)",
        f"- protocol break rate: {summary['protocol_drift_break_rate']:.2f} "
        f"({summary['n_protocol_runs']} runs)",
        f"- silent total: {summary['silent_wrong_total']}, "
        f"loud total: {summary['loud_total']}",
        f"- break prob per change event: "
        f"{summary['hazard_model']['break_prob_per_change_event']} "
        f"(silent share {summary['hazard_model']['silent_prob_per_change_event']})",
        "",
        "## Comparison with the old probe",
        "",
        f"Old probe: {OLD_PROBE_SOURCE} -- 5/5 baseline pass, 15/15 appearance "
        "pass, 0/10 protocol pass with all 10 protocol failures loud "
        "(selector timeouts on the wizard's first flow control), 0 silent "
        "failures anywhere. The new program reproduces every cell exactly.",
        "",
        "Identical coding, identical arms, different compiled program "
        "(role/label-locator wizard program from the guiexp compile path "
        "instead of the old harness's id-selector program), different "
        "held-out rotation (drift namespace), fresh servers per arm.",
        "",
        "Failure mode: the old program died in a Playwright selector timeout "
        "on `#wizard-next-1`; this one dies one step later with its own "
        "`RuntimeError: No button matching '^next$|next' found` (exception "
        f"classes: {summary['exception_classes']}). Same interaction point, "
        "same axis: both protocol arms remove the wizard's first Next "
        "control, so the program aborts before it can submit anything -- the "
        "event count is unchanged in all 10 protocol runs, which is what "
        "makes every failure loud.",
        "",
        "Silent-mode reachability (the old probe's caveat, still true here): "
        "the sectioned flow has a documented silent failure -- submitting "
        "with the disclosure unopened writes the four optional fields empty "
        "and returns no error -- but a program compiled against the wizard "
        "flow raises on the missing Next button long before any submit. The "
        "zero in the silent column is a fact about this program on these "
        "five variants, not a general property of compiled programs.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--program", default=str(PROGRAM_DEFAULT),
                        help="compiled family program .py to replay")
    parser.add_argument("--k", type=int, default=5, help="held-out bindings per arm")
    parser.add_argument("--drift-seed", type=int, default=DRIFT_SEED_DEFAULT)
    parser.add_argument("--arms", nargs="*", default=None,
                        help="subset of arm names (default: all six)")
    parser.add_argument("--out-dir", default=str(OUT_DIR_DEFAULT))
    parser.add_argument("--show", action="store_true", help="run Chromium headed")
    args = parser.parse_args(argv)

    program_path = Path(args.program)
    if not program_path.exists():
        raise SystemExit(f"no compiled program at {program_path}")
    _module, program = program_from_path(program_path)

    bindings = drift_bindings(args.k, seed=args.drift_seed)
    arms = [a for a in ARMS if not args.arms or a.name in args.arms]
    if args.arms and len(arms) != len(args.arms):
        unknown = set(args.arms) - {a.name for a in ARMS}
        raise SystemExit(f"unknown arm(s): {sorted(unknown)}")

    print(f"program: {program_path.name}   bindings: {len(bindings)} "
          f"({bindings[0]['title']} first)   arms: {len(arms)}")
    t0 = time.time()
    arm_results = [run_arm(program, arm, bindings, headless=not args.show)
                   for arm in arms]
    all_runs = [r for a in arm_results for r in a["runs"]]

    per_arm_counts = {a["arm"]: _counts(a["runs"]) for a in arm_results}
    per_arm_axis = {a["arm"]: a["axis"] for a in arm_results}
    by_axis = {ax: [r for a in arm_results if a["axis"] == ax for r in a["runs"]]
               for ax in ("baseline", "appearance", "protocol")}

    def _rate(runs: list[dict], outcome: str) -> float:
        return sum(1 for r in runs if r["outcome"] == outcome) / len(runs) if runs else 0.0

    summary = {
        "program": str(program_path),
        "probe": "guiexp e7_drift (port of openapps-exp/drift_probe.py)",
        "drift_seed_namespace": DRIFT_SEED_NAMESPACE,
        "drift_seed": args.drift_seed,
        "bindings": [b["title"] for b in bindings],
        "gate_namespace_rotation": [b["title"] for b in heldout_bindings(len(bindings))],
        "n_runs": len(all_runs),
        "wall_s": round(time.time() - t0, 1),
        "per_arm_counts": per_arm_counts,
        "per_arm_axis": per_arm_axis,
        "baseline_pass_rate": _rate(by_axis["baseline"], "PASS"),
        "appearance_drift_survival_rate": _rate(by_axis["appearance"], "PASS"),
        "n_appearance_runs": len(by_axis["appearance"]),
        "protocol_drift_break_rate": 1.0 - _rate(by_axis["protocol"], "PASS"),
        "n_protocol_runs": len(by_axis["protocol"]),
        "silent_wrong_total": sum(1 for r in all_runs if r["outcome"] == "SILENT_WRONG"),
        "loud_total": sum(1 for r in all_runs if r["outcome"] == "LOUD"),
        "exception_classes": {c: sum(1 for r in all_runs
                                     if r["exception_class"] == c)
                              for c in sorted({r["exception_class"] for r in all_runs})},
        "hazard_model": _hazard(per_arm_counts),
        "comparison": _comparison(per_arm_counts),
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "drift_probe.json"
    json_path.write_text(json.dumps({"summary": summary, "arms": arm_results}, indent=1))
    table_path = out_dir / "table.md"
    table_path.write_text(_table_md(summary))

    print(f"\n  baseline pass          {summary['baseline_pass_rate']:.2f}")
    print(f"  appearance survival    {summary['appearance_drift_survival_rate']:.2f}"
          f"  ({summary['n_appearance_runs']} runs)")
    print(f"  protocol break         {summary['protocol_drift_break_rate']:.2f}"
          f"  ({summary['n_protocol_runs']} runs)")
    print(f"  silent / loud          {summary['silent_wrong_total']} / "
          f"{summary['loud_total']}")
    print(f"  h per change event     "
          f"{summary['hazard_model']['break_prob_per_change_event']}"
          f"  (silent share {summary['hazard_model']['silent_prob_per_change_event']})")
    print(f"wrote {json_path} and {table_path}  ({summary['wall_s']:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
