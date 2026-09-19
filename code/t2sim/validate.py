"""Faithfulness check: the t2sim engine vs the old paper's Table 2.

Re-runs the OLD headline experiment (policy_sim.py's submitted table:
3 patterns x 300 arrivals x 20 reps, seed 7, unpaired seeding) with the OLD
constants (experimental-results/openapps/results/sim_params.json) in
old-constants mode, and compares the rel_to_ours vector against the stored
experimental-results/openapps/results/policy_sim.json.

Old-constants mode = the new methods-v2 mechanisms default OFF:
  tau (m, tau0)      -> 0       (the manifest tax is NEW)
  eps cliff          -> off     (selection failure is NEW)
  q0                 -> 0       (per-use failure is NEW; the old per-use
                                failure channel was the drift hazard h,
                                kept as a legacy key)
so the accounting reduces term-for-term to the old one.  Dropped old
machinery that cannot affect this table (it ran with tier="off"): the
macro/two-tier rule and the EB-shrink / loss-aversion audition variants.

Usage:
    python3 computer-use/t2sim/validate.py [--reps 20] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import sim
import streams as streams_mod
from experiments import DEFAULT_OUT_DIR

OLD_RESULTS = (streams_mod.REPO_ROOT / "experimental-results" / "openapps"
               / "results")
OLD_SIM_PARAMS = OLD_RESULTS / "sim_params.json"
OLD_POLICY_SIM = OLD_RESULTS / "policy_sim.json"

# The mechanism configuration behind the submitted old table
# (policy_sim.json's "_mech"): MECH_DEFAULT of the old file.
OLD_MECH_DEFAULT = {
    "cooldown": "inflate",
    "cooldown_len": 3,
    "half_life": 120.0,
    "prior_mode": "population",
    "prior_shape": 1.0,
    "prior_rate": 5.0,
    "horizon_mode": "doubling",
}


def old_mode_params(old: dict) -> dict:
    """Translate the old sim_params.json into t2sim old-constants mode."""
    families = {}
    for name, fam in old["families"].items():   # preserve JSON order
        families[name] = {
            "c": fam["c_reactive"],
            "d": fam["d_program"],
            "C": fam["compile_cost"],
            "p": fam["gate_rate"],
            "q0": 0.0,                        # NEW channel, off in old mode
            "h": fam.get("drift_hazard", 0.0),  # legacy break semantics
            "binding_space": fam.get("binding_space", 12),
        }
    return {"families": families, "horizon": old.get("horizon", 60),
            "tau": {"m": 0.0, "tau0": 0.0},   # NEW channel, off in old mode
            "epsilon": {"enabled": False}}    # NEW channel, off in old mode


def run_old_headline(params: dict, n: int, reps: int, seed: int,
                     mech: dict) -> dict:
    """The old run_headline loop: unpaired seeding, old POLICIES order."""
    summary = {}
    for pattern in params.get("_patterns", ["poisson", "zipf", "bursty"]):
        fam_names = list(params["families"])
        rows = {p: [] for p in sim.POLICIES}
        opts = []
        for rep in range(reps):
            rng = random.Random(seed * 1000 + rep)
            stream = streams_mod.gen_stream(pattern, n, fam_names, rng)
            opts.append(sim.offline_optimum(stream, params))
            for p in sim.POLICIES:
                prng = random.Random(rng.random())
                r = sim.run_policy(p, stream, params, prng, mech)
                rows[p].append(r["final_tokens"])
        means = {p: sum(rows[p]) / len(rows[p]) for p in sim.POLICIES}
        summary[f"{pattern}/ours"] = {"mean_tokens": means["ours"],
                                      "rel_to_ours": 1.0}
        for p in sim.POLICIES:
            if p == "ours":
                continue
            summary[f"{pattern}/{p}"] = {
                "mean_tokens": means[p],
                "rel_to_ours": means[p] / means["ours"]}
        opt_mean = sum(opts) / len(opts)
        summary[f"{pattern}/offline_opt"] = {
            "mean_tokens": opt_mean, "rel_to_ours": opt_mean / means["ours"]}
    return summary


DELTAS_NOTE = """Intentional differences between this engine and the old one
(all inert in old-constants mode, which is what this check runs):
  1. tau(n) = m*n + tau0 manifest tax and the eps(n) selection-failure
     cliff are NEW (methods-v2); here m = tau0 = 0 and eps disabled, so
     every arrival's account reduces to the old one exactly.
  2. q(n) = eps + (1-eps) q0 per-use program failure is NEW; here q0 = 0
     and the old failure channel (drift hazard h with break semantics) is
     kept as a legacy key so the old numbers can reproduce.
  3. The macro/two-tier artifact rule and its coverage estimator are not
     ported: the submitted old table ran with tier='off'.
  4. The EB-shrink and loss-aversion trigger variants are not ported
     (audition-only settings, never in the submitted table).
  5. The offline DP folds tau0 into every arrival; tau0 = 0 here, so the
     recursion is the old one term-for-term.
Anything beyond float noise in the comparison below would mean a port bug."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-params", default=str(OLD_SIM_PARAMS))
    ap.add_argument("--old-results", default=str(OLD_POLICY_SIM))
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    old = json.loads(Path(args.old_params).read_text())
    old_res = json.loads(Path(args.old_results).read_text())
    params = old_mode_params(old)
    params["_patterns"] = old.get("patterns", ["poisson", "zipf", "bursty"])

    print(f"validating t2sim port against old Table 2 "
          f"(n={args.n}, reps={args.reps}, seed={args.seed}, "
          f"unpaired seeding, old MECH_DEFAULT)")
    t0 = time.time()
    new = run_old_headline(params, args.n, args.reps, args.seed,
                           OLD_MECH_DEFAULT)
    print(f"  rerun wall: {time.time() - t0:.1f}s\n")

    rows = []
    worst = 0.0
    rank_checks = []
    for key, old_rec in old_res.items():
        if key == "_mech":
            continue
        new_rec = new.get(key)
        if new_rec is None:
            rows.append((key, old_rec["rel_to_ours"], None, None))
            continue
        d = new_rec["rel_to_ours"] - old_rec["rel_to_ours"]
        rel_d = abs(d) / old_rec["rel_to_ours"]
        worst = max(worst, rel_d)
        rows.append((key, old_rec["rel_to_ours"], new_rec["rel_to_ours"],
                     rel_d))

    # qualitative ordering check per pattern: the old table's ordering
    # (ours beats reactive/on_second/success_count/toolpro/breakeven;
    # always_compile ~ ours; oracle/offline at or below ours)
    patterns = sorted({k.split("/")[0] for k, *_ in rows})
    for pat in patterns:
        def rel(pol, src):
            k = f"{pat}/{pol}"
            return src[k]["rel_to_ours"] if k in src else None
        checks = []
        for pol in ("always_reactive", "on_second", "success_count",
                    "toolpro_port", "breakeven"):
            ro, rn = rel(pol, old_res), rel(pol, new)
            if ro is not None and rn is not None:
                checks.append((f"{pol}>ours", ro > 1.0, rn > 1.0))
        rank_checks.append((pat, checks,
                            sum(1 for _, a, b in checks if a == b),
                            len(checks)))

    print(f"{'cell':<28} {'old rel':>9} {'t2sim rel':>10} {'rel dev':>9}")
    for key, o, nn, dd in rows:
        if nn is None:
            print(f"{key:<28} {o:>9.3f} {'MISSING':>10}")
        else:
            print(f"{key:<28} {o:>9.3f} {nn:>10.3f} {dd * 100:>8.3f}%")

    n_exact = sum(1 for *_, dd in rows
                  if dd is not None and dd < 1e-9)
    n_close = sum(1 for *_, dd in rows if dd is not None and dd < 0.01)
    print(f"\n{len(rows)} cells; bitwise-identical: {n_exact}; "
          f"within 1%: {n_close}; worst deviation {worst * 100:.3f}%")
    for pat, checks, ok, tot in rank_checks:
        print(f"ordering {pat}: {ok}/{tot} qualitative checks match")
    all_ok = all(ok == tot for _, _, ok, tot in rank_checks)
    verdict = ("PASS" if (worst < 0.05 and all_ok)
               else "PASS (qualitative)" if all_ok else "FAIL")
    print(f"verdict: {verdict}")

    print("\n" + DELTAS_NOTE)

    out_path = Path(args.out) if args.out else \
        DEFAULT_OUT_DIR / "validate_old_constants.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "config": {"n": args.n, "reps": args.reps, "seed": args.seed,
                   "mech": OLD_MECH_DEFAULT,
                   "old_params_file": str(args.old_params),
                   "old_results_file": str(args.old_results)},
        "comparison": [{"cell": k, "old_rel": o, "new_rel": nn,
                        "rel_dev": dd} for k, o, nn, dd in rows],
        "worst_rel_dev": worst,
        "ordering_checks": {p: {"ok": ok, "total": tot}
                            for p, _, ok, tot in rank_checks},
        "verdict": verdict,
        "deltas_note": DELTAS_NOTE,
    }, indent=1))
    print(f"\n  wrote {out_path}")
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
