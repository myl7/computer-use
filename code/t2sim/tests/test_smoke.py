"""Smoke tests: tiny end-to-end experiment runs with dummy constants.

Covers the full wiring -- constants checking, cell builders, the (serial)
worker path, assembly, printing and JSON output -- for E3 and E4 in one
<30 s budget, then E5/E8 at the same tiny scale.  No API calls, no network:
every number is a dummy constant written into a tmp constants file, and the
real datasets are only read through the loaders (small windows)."""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

import experiments
import sim
import streams as streams_mod

T2SIM_DIR = Path(__file__).resolve().parents[1]


def _write_wiki(path: Path, rows: int, hot: str, cold: list[str],
                seed: int) -> None:
    rng = random.Random(seed)
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(rows):
            fam = hot if rng.random() < 0.6 else rng.choice(cold)
            ts = f"2026-09-03T00:{i // 60:02d}:{i % 60:02d}Z"
            fh.write(json.dumps({"ts": ts, "family_id": fam}) + "\n")


def dummy_constants(tmp_path: Path) -> Path:
    wiki_a = tmp_path / "wiki_a.jsonl"
    wiki_b = tmp_path / "wiki_b.jsonl"
    _write_wiki(wiki_a, 120, "WA_hot", ["WA1", "WA2", "WA3"], seed=1)
    _write_wiki(wiki_b, 120, "WB_hot", ["WB1", "WB2", "WB3", "WB4"], seed=2)
    constants = {
        "schema": "t2sim.constants/1",
        "cost_sets": {
            "cs_dummy": {
                "label": "dummy smoke constants",
                "status": "measured",
                "r_cache": 0.2,
                "m": 91.0,
                "tau0": 568.0,
                "layouts": {
                    "lay1": {"status": "measured", "c": 95296.0,
                             "L": 22018.0, "rho": 0.769, "C": 14549.0,
                             "p": 1.0, "d": 347.0, "q0": 0.17},
                    "lay2": {"status": "measured", "c": 114518.0,
                             "L": 31626.0, "rho": 0.724, "C": 13361.0,
                             "p": 0.9, "d": 491.0, "q0": 0.23},
                },
            },
        },
        "price_ladder": [{"name": "native", "C": None},
                         {"name": "1M", "C": 1000000.0},
                         {"name": "5M", "C": 5000000.0}],
        "epsilon_cliff": {"enabled": False, "eps0": 0.3,
                          "cliff_start": 100, "cliff_slope": 20},
        "streams": {
            "synthetic": {"patterns": ["poisson", "zipf"],
                          "n_arrivals": 80, "n_arrivals_long": 200},
            "wiki_A": {"format": "wiki_jsonl", "path": str(wiki_a),
                       "window": 80, "label": "dummy wiki A",
                       "role": "main"},
            "wiki_B": {"format": "wiki_jsonl", "path": str(wiki_b),
                       "window": 80, "label": "dummy wiki B",
                       "role": "main"},
            "sepsis": {"format": "old_real_streams", "key": "sepsis",
                       "window": 150, "label": "Sepsis (smoke window)",
                       "role": "main"},
            "helpdesk": {"format": "old_real_streams", "key": "helpdesk",
                         "window": 200, "label": "Helpdesk (smoke window)",
                         "role": "e5_selection"},
            "bpi2019": {"format": "old_real_streams", "key": "bpi2019",
                        "window": 300, "label": "BPI 2019 (smoke window)",
                        "role": "held_out"},
        },
        "trigger": {"cooldown": "inflate", "cooldown_len": 3,
                    "half_life": 120.0, "prior_mode": "population",
                    "prior_shape": 1.0, "prior_rate": 5.0,
                    "horizon_mode": "doubling", "horizon_fixed": 60},
        "e3": {"cost_sets": ["cs_dummy"], "price": "native"},
        "e4": {"cost_set": "cs_dummy",
               "streams": ["wiki_A", "wiki_B", "sepsis"],
               "prices": ["native", "1M"],
               "bpi_heldout": True, "bpi_reps": None},
        "e5": {"cost_set": "cs_dummy",
               "selection_streams": ["sepsis"],
               "real_prices": ["native"],
               "stress_prices": ["native"],
               "gate_rates": [0.3],
               "long_n": 200,
               "bpi_horizon_heldout": False,
               "bpi_reps": 2},
        "e8": {"cost_set": "cs_dummy", "prices": ["native", "1M"],
               "strengths": [1.0], "include_native_strength": False,
               "tau_modes": ["off", "measured"],
               "eps_modes": ["off", "cliff"],
               "sens_prices": ["native"]},
    }
    out = tmp_path / "constants_smoke.json"
    out.write_text(json.dumps(constants, indent=1))
    return out


def test_check_constants_accepts_dummy(tmp_path):
    c = experiments.load_constants(dummy_constants(tmp_path))
    issues, placeholders = experiments.check_constants(c)
    assert issues == []
    assert placeholders == []            # every cell says "measured"
    issues2, _ = experiments.check_constants(
        {**c, "schema": "wrong"})
    assert any("schema" in i for i in issues2)


def test_smoke_e3_and_e4_under_30s(tmp_path):
    constants = experiments.load_constants(dummy_constants(tmp_path))
    out_dir = tmp_path / "out"
    t0 = time.time()
    p3 = experiments.run_experiment("E3", constants, out_dir=out_dir,
                                    reps=2, seed=7, jobs=1, quick=True,
                                    B=200)
    p4 = experiments.run_experiment("E4", constants, out_dir=out_dir,
                                    reps=2, seed=7, jobs=1, quick=True,
                                    B=200)
    elapsed = time.time() - t0
    assert elapsed < 30.0, f"E3+E4 smoke took {elapsed:.1f}s"

    e3 = json.loads(p3.read_text())
    assert e3["meta"]["quick"] is True
    assert e3["meta"]["n_cells"] >= 2           # 2 patterns x 1 cost set
    assert e3["meta"]["units"].startswith("cache-adjusted")
    keys = sorted(e3["cells"])
    assert any(k.startswith("poisson/") for k in keys)
    for key, cell in e3["cells"].items():
        for pol in sim.POLICIES + ["offline_opt"]:
            rec = cell[pol]
            assert rec["mean_tokens"] > 0
            assert 0.0 < rec["rel_to_ours"]
        assert cell["ours"]["rel_to_ours"] == 1.0
        assert len(cell["ours"]["ci95_tokens"]) == 2
        assert cell["_stream"]["n_arrivals"] == 60   # quick shrinks to 60
        assert cell["ours"]["mean_final_library"] <= \
            cell["always_compile"]["mean_final_library"]

    e4 = json.loads(p4.read_text())
    order = experiments.E4_ROW_ORDER
    n_price_cells = 0
    for key, cell in e4["cells"].items():
        for pol in order:
            assert pol in cell, (key, pol)
        assert "ours_g15" in cell and "ours_hfix" in cell
        assert cell["_stream"]["n_arrivals"] > 0
        if key.endswith("price=1M"):
            n_price_cells += 1
            assert cell["_n_star"] > 10          # 1M price: N* is large
    assert n_price_cells >= 4                    # 4 streams x {native,1M}
    # bpi held-out cell is included and windowed by quick mode
    assert any(k.startswith("bpi2019/") for k in e4["cells"])
    bpi = [v for k, v in e4["cells"].items()
           if k.startswith("bpi2019/")][0]
    assert bpi["_stream"]["n_arrivals"] == 1500


def test_smoke_e5_paired_mechanism_cells(tmp_path):
    constants = experiments.load_constants(dummy_constants(tmp_path))
    out_dir = tmp_path / "out"
    p5 = experiments.run_experiment("E5", constants, out_dir=out_dir,
                                    reps=2, seed=7, jobs=1, quick=True,
                                    B=100)
    e5 = json.loads(p5.read_text())
    assert e5["meta"]["n_cells"] >= 3
    for key, cell in e5["cells"].items():
        assert cell["best_variant"] in cell
        assert "clairvoyant_tokens" in cell
        for v in ("fixed_3", "inflate_2x", "blacklist"):
            if key.startswith("a_cooldown"):
                assert v in cell
        for v, rec in cell.items():
            if isinstance(rec, dict) and "mean_tokens" in rec:
                assert rec["rel_clairvoyant"] >= 0.9   # sanity band
                assert "paired_se" in rec


def test_smoke_e8_grid_and_tau_eps(tmp_path):
    constants = experiments.load_constants(dummy_constants(tmp_path))
    out_dir = tmp_path / "out"
    p8 = experiments.run_experiment("E8", constants, out_dir=out_dir,
                                    reps=2, seed=7, jobs=1, quick=True,
                                    B=100)
    e8 = json.loads(p8.read_text())
    for section in ("grid", "tau", "eps"):
        assert e8["cells"][section], section
    # tau=off vs measured: always-compile pays no manifest tax when off
    for key, cell in e8["cells"]["tau"].items():
        if key.endswith("/tau=off/poisson"):
            assert cell["always_compile"]["mean_tau_total"] == 0.0
        if key.endswith("/tau=measured/poisson"):
            assert cell["always_compile"]["mean_tau_total"] > 0.0
            assert cell["ours"]["mean_tau_total"] <= \
                cell["always_compile"]["mean_tau_total"]
    for key, cell in e8["cells"]["eps"].items():
        if "/eps=cliff/" in key:
            assert cell["always_compile"]["mean_tokens"] >= \
                cell["ours"]["mean_tokens"]


def test_run_py_cli_wiring(tmp_path):
    """the documented entry point works end to end (serial worker)."""
    cpath = dummy_constants(tmp_path)
    out_dir = tmp_path / "cli_out"
    res = subprocess.run(
        [sys.executable, str(T2SIM_DIR / "run.py"),
         "--constants", str(cpath), "--exp", "E3", "--reps", "2",
         "--jobs", "1", "--quick", "--out-dir", str(out_dir)],
        capture_output=True, text=True, timeout=120, check=True,
        cwd=str(T2SIM_DIR.parents[1]))
    assert "wrote" in res.stdout
    assert (out_dir / "E3_quick.json").exists()
