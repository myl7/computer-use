"""Isolation A: t2sim 'ours' vs the OLD policy_sim engine on the SAME BPI
stream, old constants (tau/eps/q0 off = the validate-old path), our policy
only, identical seed.  Prints per-arrival trigger arithmetic at the first
diverging compile decision (debug log of lambda_hat * H_hat * (c_hat - d_hat)
vs C_eff for the family at that step).

Run:  python3 experimental-results/guiexp/t2_sim/isolation_a.py [--window N]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]          # /Users/myl/app/computer-use
T2SIM = REPO_ROOT / "computer-use" / "t2sim"
OLD_EXP = REPO_ROOT / "computer-use" / "openapps-exp"

sys.path.insert(0, str(T2SIM))
sys.path.insert(0, str(OLD_EXP))

import sim                                   # t2sim
import policy_sim                            # OLD engine
from validate import OLD_MECH_DEFAULT, old_mode_params  # t2sim validate path

OLD_RESULTS = REPO_ROOT / "experimental-results" / "openapps" / "results"
REAL_STREAMS = (REPO_ROOT / "experimental-results" / "openapps" /
                "real-streams" / "real_streams.json")


def load_bpi(window: int | None):
    data = json.loads(REAL_STREAMS.read_text())
    stream = data["bpi2019"]["stream"]
    return stream[:window] if window else stream


def old_engine_params(fam_names, price: float) -> dict:
    """real_stream_pilot.make_params: old profiles round-robin over sorted
    family names, compile price overridden."""
    old = json.loads((OLD_RESULTS / "sim_params.json").read_text())
    profiles = list(old["families"].values())
    families = {}
    for i, name in enumerate(sorted(fam_names)):
        prof = dict(profiles[i % len(profiles)])
        prof["compile_cost"] = price
        families[name] = prof
    return {"families": families, "horizon": old["horizon"]}, old


def new_engine_params(fam_names, price: float) -> dict:
    """old_mode_params translation (tau=0, eps off, q0=0, h=drift_hazard
    legacy) round-robbed over the stream's sorted family names exactly like
    real_stream_pilot.make_params."""
    old = json.loads((OLD_RESULTS / "sim_params.json").read_text())
    p = old_mode_params(old)
    profiles = list(p["families"].values())
    families = {}
    for i, name in enumerate(sorted(fam_names)):
        prof = dict(profiles[i % len(profiles)])
        prof["C"] = price
        families[name] = prof
    p["families"] = families
    return p


def run_isolation(stream, price: float, seed: int, k_log: int = 3):
    old_params, _ = old_engine_params(set(stream), price)
    new_params = new_engine_params(set(stream), price)

    rng_old = random.Random(seed)
    r_old = policy_sim.run_policy("ours", stream, old_params, rng_old,
                                  dict(policy_sim.MECH_DEFAULT))
    rng_new = random.Random(seed)
    r_new = sim.run_policy("ours", stream, new_params, rng_new,
                           dict(OLD_MECH_DEFAULT))
    print(f"stream n={len(stream)} price={price:g} seed={seed}")
    print(f"  OLD engine ours: {r_old['final_tokens']:.1f} tok  "
          f"compiles={r_old['n_compiles']} wasted={r_old['wasted_compiles']}")
    print(f"  NEW engine ours: {r_new['final_tokens']:.1f} tok  "
          f"compiles={r_new['n_compiles']} wasted={r_new['wasted_compiles']}")
    same = abs(r_old["final_tokens"] - r_new["final_tokens"]) < 1e-6
    print(f"  identical: {same}")
    return same, old_params, new_params, r_old, r_new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--price", type=float, default=1e6)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--log-family", default=None,
                    help="family name to log trigger arithmetic for")
    args = ap.parse_args()

    stream = load_bpi(args.window)
    run_isolation(stream, args.price, args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
