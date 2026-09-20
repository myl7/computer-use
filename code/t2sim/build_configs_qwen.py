"""Build the three qw (android_qw) run-config constants files, mirroring the
e4dsprices / a1k3dsprices scratch-config pattern:

  constants.measured.v3.qwen.e3qw.json
      v3-qwen verbatim + e3.cost_sets = ["android_qw"] -- the synthetic
      headline E3 on the third model (3 patterns x android_qw at native).

  constants.measured.v3.qwen.e4qwprices.json
      v3-qwen verbatim + e4.cost_set = "android_qw" -- the E4 real-stream
      price ladder (native / autorpa_233k / 1M / 5M, exactly the e4dsprices
      ladder) on the third model.  Carries the e12 block too (see below).

  constants.measured.v3.a1k3qwprices.json
      e4qwprices + the three canonical run patches BAKED IN, exactly how
      build_constants_e12.py freezes the authoritative DS E12 config:
        1. trigger.gate_prior = "add_one"      (run.py --gate-prior add_one)
        2. trigger.buy_formula = "full"        (run.py --buy-formula full)
        3. deployment.k_min_global = 3         (run.py --kmin-global 3)
      This is the E12 config.  No source-path revert (patch 3 of the DS
      builder): the qwen block has no pre-reorganization history to match.

Fingerprint contract (the e12_postcheck shared-cell guarantee): an E4 run
launched on e4qwprices with the three patches ON THE COMMAND LINE loads
exactly the dict a1k3qwprices holds, so both runs record the SAME
constants_fingerprint and every E12/E4 shared cell is bit-identical by
construction.  For that to hold, the e12 block (E12's cost set narrowed to
android_qw; the streams/prices defaults already derive from the e4 block)
lives in e4qwprices itself -- e4 cells never read it.

Deterministic, local-only: reads one JSON file, writes three.  No
hand-typed numbers -- every value is copied verbatim from
constants.measured.v3.qwen.json except the block notes.

Usage:
    python3 computer-use/t2sim/build_configs_qwen.py [--check-only] [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

T2SIM = Path(__file__).resolve().parent
sys.path.insert(0, str(T2SIM))

import experiments  # noqa: E402  (fingerprint definition + constants check)

BASE_PATH = T2SIM / "constants.measured.v3.qwen.json"
E3_PATH = T2SIM / "constants.measured.v3.qwen.e3qw.json"
E4_PATH = T2SIM / "constants.measured.v3.qwen.e4qwprices.json"
E12_PATH = T2SIM / "constants.measured.v3.a1k3qwprices.json"

# The three run.py CLI patches the canonical a1k3 runs apply, in one place
# so the E4 command line, this builder and the tests cannot drift apart.
RUN_PATCHES = (("trigger", "gate_prior", "add_one"),
               ("trigger", "buy_formula", "full"),
               ("deployment", "k_min_global", 3))

E3_NOTE = ("e3qw: same as constants.measured.v3.qwen.json but e3.cost_sets "
           "narrowed to android_qw, for the third-model synthetic headline "
           "(E3_full_v3_a1k3_qw: poisson/zipf/bursty x android_qw at the "
           "native price, reps 20 / seed 7 / add_one / buy full / "
           "k_min_global 3). Built 2026-09-21; scratch config, do not "
           "publish as a constants release.")

E4_NOTE = ("e4qwprices: same as constants.measured.v3.qwen.json but "
           "e4.cost_set switched to android_qw, for the qwen real-stream "
           "price ladder (Table 6 third model; the e4dsprices pattern, same "
           "four prices native/autorpa_233k/1M/5M). The e12 block rides "
           "along so constants.measured.v3.a1k3qwprices.json (the E12 "
           "config) differs from this file ONLY by the three baked run "
           "patches (gate_prior add_one, buy_formula full, k_min_global 3) "
           "and therefore hashes to the same constants_fingerprint as an "
           "E4 run launched on this file with those patches on the run.py "
           "command line -- the e12_postcheck shared-cell contract. Built "
           "2026-09-21; scratch config, do not publish as a constants "
           "release.")

E12_NOTE = ("e12: cost_sets narrowed to android_qw (the third-model "
            "ablation); streams and prices are left to default to the e4 "
            "streams + the bpi2019 held-out at native and 5M. Present in "
            "the e4qwprices file as well so the a1k3qwprices E12 config "
            "stays patch-only and shares the E4 run's fingerprint "
            "(see the e4qwprices note).")


def apply_run_patches(config: dict) -> dict:
    """run.py's CLI trigger/deployment patches, as one named code path."""
    out = json.loads(json.dumps(config))        # deep copy
    for section, key, val in RUN_PATCHES:
        out.setdefault(section, {})[key] = val
    return out


def build(base: dict) -> tuple[dict, dict, dict]:
    """The three qw run configs. Raises on any inconsistency."""
    if "android_qw" not in base.get("cost_sets", {}):
        raise AssertionError("constants.measured.v3.qwen.json has no "
                             "android_qw cost set; run "
                             "build_constants_qwen.py first")

    e3 = json.loads(json.dumps(base))
    e3["e3"] = dict(base["e3"], cost_sets=["android_qw"], note=E3_NOTE)

    e4 = json.loads(json.dumps(base))
    e4["e4"] = dict(base["e4"], cost_set="android_qw", note=E4_NOTE)
    e4["e12"] = dict(base.get("e12") or {}, cost_sets=["android_qw"],
                     note=E12_NOTE)

    # The E12 config: the e4qwprices dict plus the baked run patches, and
    # NOTHING else -- any extra key would break the fingerprint contract.
    e12 = apply_run_patches(e4)
    return e3, e4, e12


def frozen_checks(e3: dict, e4: dict, e12: dict, base: dict) -> None:
    """Pin the generated configs against structural expectations."""
    for cfg in (e3, e4, e12):
        issues, _ = experiments.check_constants(cfg)
        assert issues == [], issues
    # verbatim propagation: only the declared blocks move
    assert e3["e3"]["cost_sets"] == ["android_qw"]
    assert e3["e3"]["price"] == base["e3"]["price"]
    for k, v in base.items():
        if k == "e3":
            continue
        assert e3[k] == v, k
    assert e4["e4"]["cost_set"] == "android_qw"
    assert e4["e4"]["prices"] == base["e4"]["prices"] \
        == ["native", "autorpa_233k", "1M", "5M"]
    assert e4["e4"]["streams"] == base["e4"]["streams"]
    for k, v in base.items():
        if k in ("e4", "e12"):
            continue
        assert e4[k] == v, k
    assert e4["e12"]["cost_sets"] == ["android_qw"]
    # the E12 config is patch-only w.r.t. the E4 config: the top-level
    # blocks differ only where the patches sit, and only at those keys
    diff = {k for k in set(e4) | set(e12) if e4.get(k) != e12.get(k)}
    assert diff == {"trigger", "deployment"}, diff
    assert {k for k in e12["trigger"]
            if e12["trigger"][k] != e4["trigger"].get(k)} \
        == {"gate_prior", "buy_formula"}
    assert {k for k in e12["deployment"]
            if e12["deployment"][k] != (e4.get("deployment") or {}).get(k)} \
        == {"k_min_global"}
    patched = apply_run_patches(e4)
    assert patched == e12, "a1k3qwprices is not patch-only vs e4qwprices"
    # the fingerprint contract e12_postcheck relies on
    assert experiments.constants_fingerprint(patched) == \
        experiments.constants_fingerprint(e12)
    # and the trigger/deployment the runs record
    assert e12["trigger"]["gate_prior"] == "add_one"
    assert e12["trigger"]["buy_formula"] == "full"
    assert e12["deployment"]["k_min_global"] == 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check-only", action="store_true",
                    help="build in memory, run the checks, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="additionally require the on-disk files to equal "
                         "the rebuild byte for byte")
    args = ap.parse_args()

    base = json.loads(BASE_PATH.read_text())
    e3, e4, e12 = build(base)
    frozen_checks(e3, e4, e12, base)
    fps = {p.name: experiments.constants_fingerprint(c)
           for p, c in ((E3_PATH, e3), (E4_PATH, e4), (E12_PATH, e12))}
    for name, fp in fps.items():
        print(f"{name}: fingerprint {fp}")
    print("fingerprint contract: e4qwprices + CLI run patches == "
          "a1k3qwprices: OK")
    if args.check:
        for path, cfg in ((E3_PATH, e3), (E4_PATH, e4), (E12_PATH, e12)):
            if not path.exists():
                raise SystemExit(f"--check: {path.name} missing; run without "
                                 "--check once to write it")
            if json.loads(path.read_text()) != cfg:
                raise SystemExit(f"--check: {path.name} differs from the "
                                 "rebuild; regenerate it")
        print("CHECK PASS: all three on-disk configs match the rebuild")
    if args.check_only:
        return 0
    for path, cfg in ((E3_PATH, e3), (E4_PATH, e4), (E12_PATH, e12)):
        path.write_text(json.dumps(cfg, indent=1) + "\n")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
