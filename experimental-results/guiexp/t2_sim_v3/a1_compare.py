#!/usr/bin/env python3
"""W7b: compare the pi (v3) and add_one (v3_a1) E3/E4 runs.

For every cell and row:
  * FIXED rows (policies that never read the trigger's gate prior --
    always_reactive / always_compile / on_second / success_count /
    toolpro_port / oracle / offline_opt, plus ours_trueP which pins
    gate_prior="true") must be BIT-IDENTICAL in their raw stat fields
    between the two runs; only rel_to_ours moves, through the `ours`
    denominator.
  * TRIGGER rows (ours, breakeven, breakeven_cap, ours_g15/g120/hfix)
    are diffed field by field.

Usage: a1_compare.py            # E3 + E4 GLM + E4 DS, full dump
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW_FIELDS = ("mean_tokens", "n_reps", "mean_final_library",
              "mean_tau_total", "mean_wasted", "mean_n_compiles")
# Rows whose behaviour cannot read the trigger's gate prior, plus the
# ours_trueP ablation which pins gate_prior="true" in both runs.
FIXED_ROWS = {"always_reactive", "always_compile", "on_second",
              "success_count", "toolpro_port", "oracle", "offline_opt",
              "ours_trueP"}
TRIGGER_ROWS = {"ours", "breakeven", "breakeven_cap", "ours_g15",
                "ours_g120", "ours_hfix"}

PAIRS = [
    ("E3 GLM+openapps", "E3_full_v3.json", "E3_full_v3_a1.json"),
    ("E4 android_glm", "E4_full_v3.json", "E4_full_v3_a1.json"),
    ("E4 android_ds", "E4_full_v3_e4ds.json", "E4_full_v3_a1_e4ds.json"),
]


def rows_of(cell: dict) -> dict:
    return {k: v for k, v in cell.items()
            if isinstance(v, dict) and "mean_tokens" in v}


def compare(label: str, old_name: str, new_name: str) -> None:
    old = json.loads((HERE / old_name).read_text())["cells"]
    new = json.loads((HERE / new_name).read_text())["cells"]
    assert old.keys() == new.keys(), (old.keys() ^ new.keys())
    fixed_ok = True
    print(f"\n===== {label}: {old_name} (pi) vs {new_name} (add_one) =====")
    for ck in old:
        ro, rn = rows_of(old[ck]), rows_of(new[ck])
        assert ro.keys() == rn.keys()
        for row in ro:
            vo, vn = ro[row], rn[row]
            if row in FIXED_ROWS:
                assert set(vo) == set(vn), (ck, row, set(vo) ^ set(vn))
                for f in RAW_FIELDS:
                    if f not in vo:
                        continue
                    if vo[f] != vn[f]:
                        fixed_ok = False
                        print(f"  !! FIXED ROW MOVED {ck} {row} {f}: "
                              f"{vo[f]!r} -> {vn[f]!r}")
    print(f"  fixed raw-field bit-identity: "
          f"{'PASS' if fixed_ok else 'FAIL'}")

    hdr = f"  {'cell':<28}{'row':<16}{'old tok':>14}{'new tok':>14}" \
          f"{'d_tok%':>8}{'old rel':>8}{'new rel':>8}"
    print(hdr)
    for ck in old:
        ro, rn = rows_of(old[ck]), rows_of(new[ck])
        for row in ("ours", "breakeven", "breakeven_cap", "ours_g15",
                    "ours_g120", "ours_hfix", "ours_trueP", "always_compile",
                    "success_count", "oracle", "offline_opt"):
            if row not in ro:
                continue
            vo, vn = ro[row], rn[row]
            dt = (vn["mean_tokens"] / vo["mean_tokens"] - 1.0) * 100.0
            print(f"  {ck:<28}{row:<16}{vo['mean_tokens']:>14,.0f}"
                  f"{vn['mean_tokens']:>14,.0f}{dt:>7.2f}%"
                  f"{vo['rel_to_ours']:>8.3f}{vn['rel_to_ours']:>8.3f}")


def main() -> None:
    for label, o, n in PAIRS:
        compare(label, o, n)


if __name__ == "__main__":
    main()
