"""Tests for build_constants_qwen: the android_qw third-model cost set.

Fully offline: rebuilds the block from the committed
paper/measurement_update_20260918.json (qwen_rows) and
constants.measured.v3.json, then checks conventions, engine acceptance and
idempotence with the file on disk."""
from __future__ import annotations

import json
import statistics

import pytest

import build_constants_qwen as bq


@pytest.fixture(scope="module")
def built():
    v3 = json.loads(bq.V3_PATH.read_text())
    meas = json.loads(bq.MEAS_PATH.read_text())
    return bq.build_constants(v3, meas), v3, meas


def test_engine_constants_check_clean(built):
    out, _, _ = built
    import experiments
    issues, _ = experiments.check_constants(out)
    assert issues == []


def test_nothing_but_android_qw_changed(built):
    out, v3, _ = built
    assert set(out) == set(v3)
    for k, v in v3.items():
        if k != "cost_sets":
            assert out[k] == v, k
    assert set(out["cost_sets"]) - set(v3["cost_sets"]) == {"android_qw"}
    for name, blk in v3["cost_sets"].items():
        assert out["cost_sets"][name] == blk, name


def test_layouts_cover_exactly_the_measured_cells(built):
    out, _, meas = built
    lay = out["cost_sets"]["android_qw"]["layouts"]
    assert set(lay) == {f for _, f in bq.EXPECTED_CELLS}
    assert "CommentPost" not in lay          # provider-refused: no row
    assert {r["family"] for r in meas["qwen_rows"]} == set(lay)


def test_admitted_rows_keep_own_measurements(built):
    lay = built[0]["cost_sets"]["android_qw"]["layouts"]
    for fam in ("ContactsAddContact", "MarkorDeleteNote",
                "SimpleCalendarAddOneEvent", "WriterMemoSave"):
        l = lay[fam]
        assert l["p"] == 1.0
        assert l["measured"]["admitted"] is True
        assert l["measured"]["C_with_repair"] == l["C"]
        assert l["measured"]["s_arrival"] == (1 - l["q0"]) * l["c"] - l["d"]


def test_rejected_row_uses_admitted_median_fill(built):
    out, _, meas = built
    lay = out["cost_sets"]["android_qw"]["layouts"]
    adm = [r for r in meas["qwen_rows"] if r["admitted"]]
    fill_c = statistics.median([r["C"] for r in adm])
    osm = lay["OsmAndMarker"]
    assert osm["p"] == 0.0
    assert osm["C"] == fill_c                # engine-facing fill
    assert osm["measured"]["admitted"] is False
    assert osm["measured"]["C_with_repair"] > fill_c   # own failure price
    # the measurement row must carry the SAME fill (single convention)
    row = next(r for r in meas["qwen_rows"]
               if r["family"] == "OsmAndMarker")
    assert row["C"] == fill_c
    assert row["measured"]["C_with_repair"] == \
        osm["measured"]["C_with_repair"]


def test_terminated_row_flags_partial_price(built):
    lay = built[0]["cost_sets"]["android_qw"]["layouts"]
    calc = lay["CalcTableSave"]
    assert calc["p"] == 0.0
    assert calc["measured"]["partial"] is True
    assert calc["measured"]["terminated_reason"] == bq.TERMINATED_REASON
    assert calc["measured"]["C_with_repair"] == 847543.8933333333


def test_block_level_c_fail_mult(built):
    out, _, meas = built
    blk = out["cost_sets"]["android_qw"]
    adm = [r for r in meas["qwen_rows"] if r["admitted"]]
    fail = [r["measured"]["C_with_repair"] for r in meas["qwen_rows"]
            if not r["admitted"]]
    expect = statistics.median(fail) / statistics.median(
        [r["C"] for r in adm])
    mult = blk["layouts"]["MarkorDeleteNote"]["C_fail_mult"]
    assert mult == pytest.approx(expect)
    assert blk["per_model"]["C_fail_over_C_ratio"] == pytest.approx(expect)
    assert set(blk["per_model"]["C_fail_side"]) == \
        {"Android/OsmAndMarker", "Desktop/CalcTableSave"}


def test_qwen_price_sheet_and_verbatim_blocks(built):
    out, v3, _ = built
    blk = out["cost_sets"]["android_qw"]
    assert blk["price_sheet"] == bq.QWEN_PRICES
    assert blk["r_cache"] == pytest.approx(
        bq.QWEN_PRICES["p_c"] / bq.QWEN_PRICES["p_in"])
    # price ladder / streams / trigger / e3 / e4 / e5 / e8 / extras verbatim
    for k in ("price_ladder", "epsilon_cliff", "streams", "trigger",
              "e3", "e4", "e5", "e8", "extras", "schema", "notes"):
        assert out[k] == v3[k], k


def test_android_floor_subtracted(built):
    """The t12_grid floor (1031.76 pw, documented price weights) is wired
    into every AndroidWorld row and into the block metadata."""
    out, _, meas = built
    blk = out["cost_sets"]["android_qw"]
    floor = blk["floor_raw_tokens"]
    assert floor == pytest.approx(1031.7576666666666)
    assert floor == pytest.approx(meas["qwen_android_floor"]["floor_pw"])
    rec = meas["qwen_android_floor"]
    assert rec["runs"] == 18
    assert rec["cache_state"] == {"full": 16, "partial": 1, "cold": 1}
    assert rec["floor_raw_tokens"] == pytest.approx(4432.277777777777)
    lay = blk["layouts"]
    for fam in ("ContactsAddContact", "MarkorDeleteNote",
                "SimpleCalendarAddOneEvent", "OsmAndMarker"):
        row = next(r for r in meas["qwen_rows"] if r["family"] == fam)
        assert row["floor"] == pytest.approx(floor)
        assert lay[fam]["c"] == pytest.approx(row["c"])
    for fam in ("ContactsAddContact", "MarkorDeleteNote",
                "SimpleCalendarAddOneEvent"):
        assert lay[fam]["measured"]["c_unsubtracted"] == \
            pytest.approx(lay[fam]["c"] + floor)
    # WriterMemoSave's floor is its own per-cell OSWorld calibration
    assert lay["WriterMemoSave"]["measured"]["c_unsubtracted"] == \
        pytest.approx(lay["WriterMemoSave"]["c"] + 531.7155555555557)


def test_frozen_literals(built):
    bq.frozen_checks(built[0])


def test_on_disk_file_matches_rebuild(built):
    out, _, _ = built
    if not bq.OUT_PATH.exists():             # builder not yet run: skip
        pytest.skip("constants.measured.v3.qwen.json not built yet")
    assert json.loads(bq.OUT_PATH.read_text()) == out
