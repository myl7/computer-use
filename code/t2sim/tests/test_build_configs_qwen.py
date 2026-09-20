"""Tests for build_configs_qwen: the three android_qw run-config constants
files (e3qw / e4qwprices / a1k3qwprices).

Fully offline: rebuilds the configs from the committed
constants.measured.v3.qwen.json, checks the declared-block-only diffs, the
baked a1k3 run patches, the E12/E4 fingerprint contract that
e12_postcheck.py relies on, engine acceptance and idempotence with the
files on disk."""
from __future__ import annotations

import json

import pytest

import build_configs_qwen as bc


@pytest.fixture(scope="module")
def built():
    base = json.loads(bc.BASE_PATH.read_text())
    return bc.build(base), base


def test_engine_constants_check_clean(built):
    (e3, e4, e12), _ = built
    import experiments
    for cfg in (e3, e4, e12):
        issues, _ = experiments.check_constants(cfg)
        assert issues == []


def test_e3qw_only_e3_block_changed(built):
    (e3, _, _), base = built
    assert e3["e3"]["cost_sets"] == ["android_qw"]
    assert e3["e3"]["price"] == base["e3"]["price"]
    for k, v in base.items():
        if k == "e3":
            continue
        assert e3[k] == v, k


def test_e4qwprices_only_e4_and_e12_blocks_changed(built):
    (_, e4, _), base = built
    assert e4["e4"]["cost_set"] == "android_qw"
    assert e4["e12"]["cost_sets"] == ["android_qw"]
    for k, v in base.items():
        if k in ("e4", "e12"):
            continue
        assert e4[k] == v, k


def test_e4_prices_mirror_the_ds_ladder_exactly(built):
    (_, e4, _), base = built
    # the four e4dsprices prices: native + 233k / 1M / 5M (nothing added)
    assert e4["e4"]["prices"] == base["e4"]["prices"] \
        == ["native", "autorpa_233k", "1M", "5M"]
    assert e4["e4"]["streams"] == base["e4"]["streams"]
    assert e4["e4"]["bpi_heldout"] is True
    names = [p["name"] for p in e4["price_ladder"]]
    assert {"native", "autorpa_233k", "1M", "5M"} <= set(names)


def test_a1k3_run_patches_baked(built):
    (_, _, e12), _ = built
    assert e12["trigger"]["gate_prior"] == "add_one"
    assert e12["trigger"]["buy_formula"] == "full"
    assert e12["deployment"]["k_min_global"] == 3


def test_e12_config_is_patch_only_vs_e4_config(built):
    (_, e4, e12), _ = built
    diff = {k for k in set(e4) | set(e12) if e4.get(k) != e12.get(k)}
    assert diff == {"trigger", "deployment"}
    patched = bc.apply_run_patches(e4)
    assert patched == e12


def test_fingerprint_contract_e4_cli_equals_e12_config(built):
    """An E4 run on e4qwprices with the three patches ON THE CLI loads the
    a1k3qwprices dict, so both runs record the same fingerprint -- the
    shared-cell identity e12_postcheck.py enforces."""
    (_, e4, e12), _ = built
    import experiments
    assert experiments.constants_fingerprint(bc.apply_run_patches(e4)) \
        == experiments.constants_fingerprint(e12)


def test_e12_block_defaults_derive_from_e4(built):
    (_, e4, _), _ = built
    streams = list(e4["e4"].get("streams", ["wiki_A", "wiki_B", "sepsis"]))
    if e4["e4"].get("bpi_heldout", True):
        streams.append("bpi2019")
    assert streams == ["wiki_A", "wiki_B", "sepsis", "bpi2019"]
    assert e4["e12"]["cost_sets"] == ["android_qw"]


def test_on_disk_files_match_rebuild(built):
    (e3, e4, e12), _ = built
    for path, cfg in ((bc.E3_PATH, e3), (bc.E4_PATH, e4),
                      (bc.E12_PATH, e12)):
        if not path.exists():            # builder not yet run: skip
            pytest.skip(f"{path.name} not built yet")
        assert json.loads(path.read_text()) == cfg, path.name
