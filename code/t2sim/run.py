"""CLI for the T2 simulation layer.

    python3 computer-use/t2sim/run.py \
        --constants computer-use/t2sim/constants.template.json \
        --exp E3 --reps 20 --jobs 8

    --exp E3|E4|E5|E8|E3_tax|E4_tax|E5_tax|E4_crn|E4_evict|E4_retrieval|
    E4_kmin3, a comma list (E3,E4), or 'all'.

    The _tax variants rerun E3/E4 with the clairvoyant reference split into
    tax-blind and tax-aware rows (E3_tax.json / E4_tax.json); E5_tax rebuilds
    E5's regret against oracle_tax under the deployed mechanism.

    The four E4_* deployment variants rerun E4's 16 cells with one thing
    changed each, all under common random numbers: E4_crn is the baseline
    with paired coins, E4_evict adds the library residency rule at T*,
    E4_retrieval swaps the full-listing router for top-k retrieval, and
    E4_kmin3 gives the compiler an AutoRPA-style three-demonstration
    requirement.

    --quick shrinks every stream/grid for a smoke run.  Outputs land under
    experimental-results/guiexp/t2_sim/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiments
import sim

DEFAULT_CONSTANTS = Path(__file__).resolve().parent / "constants.template.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--constants", default=str(DEFAULT_CONSTANTS),
                    help="constants JSON (see constants.template.json)")
    ap.add_argument("--exp", required=True,
                    help="E3, E4, E5, E8, E3_tax, E4_tax, E5_tax, E4_crn, "
                         "E4_evict, E4_retrieval, E4_kmin3, a comma list, "
                         "or 'all'")
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--jobs", type=int, default=None,
                    help="worker processes (default: all cores; 1 = serial)")
    ap.add_argument("--bootstrap", type=int, default=2000, dest="B")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--quick", action="store_true",
                    help="tiny streams/grid for a smoke run")
    ap.add_argument("--tag", default="",
                    help="suffix for the output file name; a tagged run "
                         "refuses to overwrite an existing file")
    # Trigger overrides, so a rerun of a published experiment under a
    # different mechanism does not need its own constants file.  They patch
    # constants["trigger"], which is what experiments.trigger_mech reads,
    # and they are recorded in the output's meta.
    ap.add_argument("--gate-prior", default=None, dest="gate_prior",
                    choices=list(sim.GATE_PRIOR_MODES),
                    help="sim.GATE_PRIOR_DOC")
    ap.add_argument("--gate-prior-strength", type=float, default=None,
                    dest="gate_prior_strength",
                    help="pseudo-observations the pi / population prior is "
                         "worth")
    ap.add_argument("--spend-cap", default=None, dest="spend_cap",
                    choices=["off"] + list(sim.SPEND_CAP_MODES),
                    help="sim.SPEND_CAP_DOC")
    ap.add_argument("--spend-cap-mult", type=float, default=None,
                    dest="spend_cap_mult")
    ap.add_argument("--buy-formula", default=None, dest="buy_formula",
                    choices=list(sim.BUY_FORMULA_MODES),
                    help="sim.BUY_FORMULA_DOC: how the trigger prices an "
                         "attempt (full = Algorithm 1 B_hat = C + (1/p_hat "
                         "- 1)*C_fail, the default; narrow = the pre-W1.5 "
                         "C/p_hat)")
    ap.add_argument("--kmin-global", type=int, default=None,
                    dest="kmin_global",
                    help="raise the compiler's demonstration requirement "
                         "for every cell of the run (3 = Algorithm 1's "
                         "three-trace eligibility); recorded in "
                         "meta.deployment")
    args = ap.parse_args()

    constants = experiments.load_constants(args.constants)
    trig = constants.setdefault("trigger", {})
    if args.gate_prior is not None:
        trig["gate_prior"] = args.gate_prior
    if args.gate_prior_strength is not None:
        trig["gate_prior_strength"] = args.gate_prior_strength
    if args.spend_cap is not None:
        trig["spend_cap"] = (False if args.spend_cap == "off"
                             else args.spend_cap)
    if args.spend_cap_mult is not None:
        trig["spend_cap_mult"] = args.spend_cap_mult
    if args.buy_formula is not None:
        trig["buy_formula"] = args.buy_formula
    if args.kmin_global is not None:
        constants.setdefault("deployment", {})["k_min_global"] = \
            args.kmin_global
    if args.exp == "all":
        exps = ["E3", "E4", "E5", "E8"]
    else:
        exps = [e.strip().upper() for e in args.exp.split(",")]
    for e in exps:
        if e not in experiments.CELL_BUILDERS:
            ap.error(f"unknown experiment {e!r}")

    out_dir = Path(args.out_dir) if args.out_dir \
        else experiments.DEFAULT_OUT_DIR
    t0 = time.time()
    for exp in exps:
        experiments.run_experiment(exp, constants, out_dir=out_dir,
                                   reps=args.reps, seed=args.seed,
                                   jobs=args.jobs, quick=args.quick,
                                   B=args.B, tag=args.tag)
    print(f"\ntotal wall {time.time() - t0:.1f}s for {','.join(exps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
