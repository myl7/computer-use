"""Isolation C: channel-flip matrix on the long streams.

Start from the OLD-constants configuration (verified bitwise-identical to
the old engine by isolation_a) and flip one methods-v2 channel at a time,
measuring ours / always_reactive / oracle per step.  The first channel that
flips reactive/ours below 1 is the regression carrier.

Channels (cumulative):
  old        : old sim_params costs (c~480k, d=20k), h=0.02, q0=0, tau=0
  +costs     : methods-v2 measured layouts (c~73k, d=347, q0 in next step)
  +q0        : q0=0.17 (s and per-use fallback)
  +h0        : drift hazard removed (h=0; programs never break)
  +tau       : manifest tax tau(n)=91n+368 per arrival

Also prints, for the first N compile decisions under the final config, the
trigger arithmetic lambda_hat * H_hat * (c_hat - d_hat) vs C_eff at each
compile decision for family #k (debug log requested by the isolation plan).
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
sys.path.insert(0, str(T2SIM))

import sim
from validate import OLD_MECH_DEFAULT

OLD_RESULTS = REPO_ROOT / "experimental-results" / "openapps" / "results"
REAL_STREAMS = (REPO_ROOT / "experimental-results" / "openapps" /
                "real-streams" / "real_streams.json")
MEASURED = json.loads((T2SIM / "constants.measured.json").read_text())

TRIGGER = {"cooldown": "inflate", "cooldown_len": 3, "half_life": 120.0,
           "prior_mode": "population", "prior_shape": 1.0, "prior_rate": 5.0,
           "horizon_mode": "doubling"}


def profiles_for(stage: str):
    """Family profile dict for a given flip stage (c, d, C, p, q0, h)."""
    old = json.loads((OLD_RESULTS / "sim_params.json").read_text())
    lay = MEASURED["cost_sets"]["openapps_glm"]["layouts"]
    new_profiles = [
        {"c": lay[k]["c"], "d": lay[k]["d"], "C": lay[k]["C"],
         "p": lay[k]["p"], "q0": lay[k]["q0"], "h": 0.0,
         "binding_space": 12}
        for k in lay
    ]
    old_profiles = [
        {"c": f["c_reactive"], "d": f["d_program"], "C": f["compile_cost"],
         "p": f["gate_rate"], "q0": 0.0, "h": f.get("drift_hazard", 0.0),
         "binding_space": f.get("binding_space", 12)}
        for f in old["families"].values()
    ]
    if stage == "old":
        return old_profiles, {"m": 0.0, "tau0": 0.0}
    if stage == "+costs":          # new c/d/C but still q0=0, h=0.02, tau=0
        profs = [dict(p) for p in new_profiles]
        for p, o in zip(profs, old_profiles):
            p["q0"], p["h"] = 0.0, o["h"]
        return profs, {"m": 0.0, "tau0": 0.0}
    if stage == "+q0":
        profs = [dict(p) for p in new_profiles]
        for p, o in zip(profs, old_profiles):
            p["h"] = o["h"]
        return profs, {"m": 0.0, "tau0": 0.0}
    if stage == "+h0":
        return [dict(p) for p in new_profiles], {"m": 0.0, "tau0": 0.0}
    if stage == "+tau":
        cs = MEASURED["cost_sets"]["openapps_glm"]
        return ([dict(p) for p in new_profiles],
                {"m": float(cs["m"]), "tau0": float(cs["tau0"])})
    raise ValueError(stage)


STAGES = ["old", "+costs", "+q0", "+h0", "+tau"]


def build_params(fam_names, price, stage):
    profs, tau = profiles_for(stage)
    families = {}
    for i, name in enumerate(sorted(fam_names)):
        p = dict(profs[i % len(profs)])
        p["C"] = price
        families[name] = p
    return {"families": families, "horizon": 60, "tau": tau,
            "epsilon": {"enabled": False}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", default="bpi2019",
                    choices=["bpi2019", "wiki_B20k"])
    ap.add_argument("--price", type=float, default=1e6)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--log-decisions", type=int, default=0,
                    help="print trigger arithmetic for the first N compile "
                         "decisions of the final stage")
    args = ap.parse_args()

    if args.stream == "bpi2019":
        stream = json.loads(REAL_STREAMS.read_text())["bpi2019"]["stream"]
    else:
        path = REPO_ROOT / "datasets" / "wiki-stream" / \
            "stream_content_tail.jsonl"
        stream = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                stream.append(json.loads(line)["family_id"])
        rows = sorted(range(len(stream)))  # already ts-sorted snapshot
        stream = [stream[i] for i in rows][:20000]

    fams = set(stream)
    print(f"stream={args.stream} n={len(stream)} fams={len(fams)} "
          f"price={args.price:g} seed={args.seed}")
    print(f"{'stage':<8} {'ours(M)':>10} {'reac(M)':>10} {'oracle(M)':>10} "
          f"{'reac/ours':>9} {'n_lib':>6} {'wasted':>6} {'tau(M)':>9}")
    for stage in STAGES:
        params = build_params(fams, args.price, stage)
        r = {}
        for pol in ("ours", "always_reactive", "oracle"):
            r[pol] = sim.run_policy(pol, stream, params,
                                    random.Random(args.seed),
                                    dict(TRIGGER))
        o, a, g = r["ours"], r["always_reactive"], r["oracle"]
        print(f"{stage:<8} {o['final_tokens']/1e6:>10.1f} "
              f"{a['final_tokens']/1e6:>10.1f} {g['final_tokens']/1e6:>10.1f} "
              f"{a['final_tokens']/o['final_tokens']:>9.3f} "
              f"{o['final_library']:>6d} {o['wasted_compiles']:>6d} "
              f"{o['tau_total']/1e6:>9.1f}")

    if args.log_decisions:
        log_decision_trace(stream, fams, args.price, args.seed,
                           args.log_decisions)
    return 0


def log_decision_trace(stream, fams, price, seed, n_log):
    """Debug log: lambda_hat * H_hat * (c_hat - d_hat) vs C_eff at each of
    the first N compile decisions (final stage, methods-v2 constants)."""
    params = build_params(fams, price, "+tau")
    records: list[dict] = []
    orig = sim.FamilyState.try_compile

    def traced(self, spend_price=None):
        records.append({
            "name": self.name, "k": self.k,
            "lam_hat": self.lam_hat(),
            "stat_n": self.stat_n, "stat_gap": self.stat_gap,
            "s": (1.0 - params["families"][self.name]["q0"]) * self.c - self.d,
            "C_eff": self.C / self.p_gate * self.price_multiplier(),
            "t": None,
        })
        return orig(self, spend_price)

    sim.FamilyState.try_compile = traced
    t_counter = {"t": 0}
    orig_obs = sim.FamilyState.observe_arrival

    def counted_obs(self, t):
        t_counter["t"] = t
        return orig_obs(self, t)

    sim.FamilyState.observe_arrival = counted_obs
    try:
        # patch to record t at decision time: run once, then post-process by
        # replaying the arrival counter -- simpler: record inside try_compile
        # via the shared counter that observe_arrival updates just before.
        def traced2(self, spend_price=None):
            records[-1 if False else len(records)]["t"] = t_counter["t"]
            return orig(self, spend_price)
        # rebind: chain traced then fix t
        sim.FamilyState.try_compile = traced
        sim.run_policy("ours", stream, params, random.Random(seed),
                       dict(TRIGGER))
    finally:
        sim.FamilyState.try_compile = orig
        sim.FamilyState.observe_arrival = orig_obs
    # t was not captured per record with the simple chain; approximate by a
    # second pass that stores t directly
    records2: list[dict] = []

    def traced3(self, spend_price=None):
        records2.append({
            "t": t_counter["t"], "name": self.name, "k": self.k,
            "lam_hat": self.lam_hat(),
            "s": (1.0 - params["families"][self.name]["q0"]) * self.c - self.d,
            "C_eff": self.C / self.p_gate * self.price_multiplier(),
        })
        return orig(self, spend_price)

    sim.FamilyState.try_compile = traced3
    try:
        sim.run_policy("ours", stream, params, random.Random(seed),
                       dict(TRIGGER))
    finally:
        sim.FamilyState.try_compile = orig
    # total future uses actually observed per family (for waste check)
    counts: dict[str, int] = {}
    for nm in stream:
        counts[nm] = counts.get(nm, 0) + 1
    print(f"\ntrigger arithmetic at the first {n_log} compile decisions "
          f"(doubling horizon, methods-v2 constants):")
    print(f"{'t':>7} {'family':>14} {'k':>3} {'lam_hat':>9} "
          f"{'E=l*h*t':>10} {'s':>8} {'E*s(M)':>8} {'C_eff(M)':>9} "
          f"{'fires':>5} {'tot_uses':>8}")
    for rec in records2[:n_log]:
        E = rec["lam_hat"] * rec["t"]
        fires = E * rec["s"] > rec["C_eff"]
        print(f"{rec['t']:>7} {rec['name']:>14} {rec['k']:>3} "
              f"{rec['lam_hat']:>9.5f} {E:>10.1f} {rec['s']:>8.0f} "
              f"{E * rec['s'] / 1e6:>8.2f} {rec['C_eff'] / 1e6:>9.2f} "
              f"{str(fires):>5} {counts.get(rec['name'], 0):>8}")


if __name__ == "__main__":
    raise SystemExit(main())
