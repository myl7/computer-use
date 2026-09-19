"""Isolation A2: find the FIRST diverging decision between the OLD
policy_sim engine and t2sim on the same stream/seed (old constants).

Monkeypatches try_compile in both engines to record every compile decision
(family, arrival step t, k, ok).  The first record that differs is the
divergence point; at that point both engines' family state and trigger
arithmetic (lam_hat, E_use, s, price) are printed for the diff.

Run:  python3 experimental-results/guiexp/t2_sim/isolation_a2.py \
          [--window 20000] [--price 1e6] [--seed 1000]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
T2SIM = REPO_ROOT / "computer-use" / "t2sim"
OLD_EXP = REPO_ROOT / "computer-use" / "openapps-exp"

sys.path.insert(0, str(T2SIM))
sys.path.insert(0, str(OLD_EXP))

import sim                                    # t2sim
import policy_sim                             # OLD
from validate import OLD_MECH_DEFAULT, old_mode_params

OLD_RESULTS = REPO_ROOT / "experimental-results" / "openapps" / "results"
REAL_STREAMS = (REPO_ROOT / "experimental-results" / "openapps" /
                "real-streams" / "real_streams.json")

LOG_OLD: list = []
LOG_NEW: list = []
STATE_OLD: dict = {}
STATE_NEW: dict = {}


def patch(old_or_new: str):
    if old_or_new == "old":
        cls, log, states = policy_sim.FamilyState, LOG_OLD, STATE_OLD
    else:
        cls, log, states = sim.FamilyState, LOG_NEW, STATE_NEW
    orig_try = cls.try_compile
    orig_obs = cls.observe_arrival

    def try_compile(self, price=None, *a, **kw):
        log.append({"name": self.name, "k": self.k, "price": price,
                    "compiled": self.compiled, "alive": self.program_alive,
                    "consec": self.consec_failures})
        return orig_try(self, price, *a, **kw)

    def observe_arrival(self, t):
        states[(self.name, t)] = dict(
            k=self.k, stat_n=self.stat_n, stat_gap=self.stat_gap,
            lam_hat=self.lam_hat(), first_seen=self.first_seen,
            last_t=self.last_t, compiled=self.compiled,
            alive=self.program_alive, inter=self.inter_arrivals[-3:])
        return orig_obs(self, t)

    cls.try_compile = try_compile
    cls.observe_arrival = observe_arrival


def params_for(fam_names, price, engine: str):
    old = json.loads((OLD_RESULTS / "sim_params.json").read_text())
    if engine == "old":
        profiles = list(old["families"].values())
        families = {}
        for i, name in enumerate(sorted(fam_names)):
            prof = dict(profiles[i % len(profiles)])
            prof["compile_cost"] = price
            families[name] = prof
        return {"families": families, "horizon": old["horizon"]}
    p = old_mode_params(old)
    profiles = list(p["families"].values())
    families = {}
    for i, name in enumerate(sorted(fam_names)):
        prof = dict(profiles[i % len(profiles)])
        prof["C"] = price
        families[name] = prof
    p["families"] = families
    return p


def trigger_snapshot(fams_cls, name, t, horizon=60, mode="doubling"):
    """Recompute the trigger arithmetic for family `name` at step t from the
    recorded state, exactly as the engines do (doubling, population prior)."""
    st = (fams_cls := None)  # unused shim
    raise NotImplementedError


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=20000)
    ap.add_argument("--price", type=float, default=1e6)
    ap.add_argument("--seed", type=int, default=1000)
    args = ap.parse_args()

    data = json.loads(REAL_STREAMS.read_text())
    stream = data["bpi2019"]["stream"][:args.window]
    params_old = params_for(set(stream), args.price, "old")
    params_new = params_for(set(stream), args.price, "new")

    patch("old")
    r_old = policy_sim.run_policy("ours", stream, params_old,
                                  random.Random(args.seed),
                                  dict(policy_sim.MECH_DEFAULT))
    patch("new")
    r_new = sim.run_policy("ours", stream, params_new,
                           random.Random(args.seed), dict(OLD_MECH_DEFAULT))

    print(f"OLD: tokens={r_old['final_tokens']:.0f} compiles="
          f"{r_old['n_compiles']} wasted={r_old['wasted_compiles']}")
    print(f"NEW: tokens={r_new['final_tokens']:.0f} compiles="
          f"{r_new['n_compiles']} wasted={r_new['wasted_compiles']}")

    # index LOG_NEW by (name, k) to align: the k-th compile of a family
    from collections import defaultdict
    old_by = defaultdict(int)
    new_by = defaultdict(int)
    n_div = None
    for i, rec in enumerate(LOG_OLD):
        key = (rec["name"], rec["k"])
        old_by[rec["name"]] += 1
        # find the next NEW record for this family
        idx = new_by[rec["name"]]
        if idx >= len([x for x in LOG_NEW if x["name"] == rec["name"]]):
            n_div = ("old-only", i, rec)
            break
    if n_div is None and len(LOG_NEW) != len(LOG_OLD):
        more = "new" if len(LOG_NEW) > len(LOG_OLD) else "old"
        n_div = (f"{more}-has-more", None, None)

    print(f"\nOLD compile log: {len(LOG_OLD)} entries; "
          f"NEW compile log: {len(LOG_NEW)} entries")

    # walk both logs in order and find the first mismatch in sequence
    i = 0
    first_mismatch = None
    seq_new = [x["name"] for x in LOG_NEW]
    seq_old = [x["name"] for x in LOG_OLD]
    for i in range(min(len(seq_old), len(seq_new))):
        if seq_old[i] != seq_new[i]:
            first_mismatch = i
            break
    if first_mismatch is None and len(seq_old) != len(seq_new):
        first_mismatch = min(len(seq_old), len(seq_new))
    if first_mismatch is None:
        print("compile sequences identical (divergence is in serve/coins)")
        return 0

    i = first_mismatch
    print(f"first compile-sequence mismatch at index {i}: "
          f"OLD {seq_old[i:i+3]} vs NEW {seq_new[i:i+3]}")
    name = seq_old[i]
    rec_o, rec_n = LOG_OLD[i], LOG_NEW[i]
    print(f"OLD rec: {rec_o}")
    print(f"NEW rec: {rec_n}")
    # dump every recorded state for this family, first divergence
    ts = sorted(t for (nm, t) in STATE_OLD if nm == name)
    print(f"\nstate trace for family {name!r} "
          f"({len(ts)} recorded arrivals):")
    for t in ts:
        so = STATE_OLD.get((name, t))
        sn = STATE_NEW.get((name, t))
        flag = "" if so == sn else "   <-- DIVERGES"
        if flag or len(ts) <= 12:
            print(f"  t={t:<6} OLD {so}\n         NEW {sn}{flag}")
        if flag:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
