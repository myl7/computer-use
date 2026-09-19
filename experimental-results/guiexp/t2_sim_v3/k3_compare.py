#!/usr/bin/env python3
"""k3 rerun comparison: old tag a1 vs new tag a1k3.

The a1k3 files are being produced by a separate process and may not all exist
yet; a pair whose old or new file is missing (or not yet valid JSON) is
skipped with a note instead of crashing.

For every cell and every policy/variant row the script prints the paper's
per-row mean summary value, old -> new with the new/old ratio (2 decimals):

  * E3/E4 cell files        value = rel_to_ours   (policy rows, "cells" map)
  * mech E5 files           value = rel_clairvoyant (= mean_tokens /
                            clairvoyant_tokens) per variant row
  * eligible_fraction file  the per-cell policy rows as above, plus the
                            per-stream eligible_fraction_arrivals and
                            eligible_fraction_families fields

mean_tokens is printed alongside as the raw quantity; any row whose value or
tokens moved by more than 5 percent relative is flagged.  Stdlib only, no
arguments (paths hardcoded to this directory), output to stdout.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MECH = HERE / "mech"
FLAG = 0.05  # flag movement beyond 5 percent relative

PAIRS = [
    ("E3 full",      HERE / "E3_full_v3_a1.json",          HERE / "E3_full_v3_a1k3.json"),
    ("E3 narrow",    HERE / "E3_narrow_v3_a1.json",        HERE / "E3_narrow_v3_a1k3.json"),
    ("E4 GLM full",  HERE / "E4_full_v3_a1.json",          HERE / "E4_full_v3_a1k3.json"),
    ("E4 DS full",   HERE / "E4_full_v3_a1_e4ds.json",     HERE / "E4_full_v3_a1k3_e4ds.json"),
    ("E4 DS narrow", HERE / "E4_narrow_v3_a1_e4ds.json",   HERE / "E4_narrow_v3_a1k3_e4ds.json"),
    ("eligible",     HERE / "eligible_fraction_v3_a1.json", HERE / "eligible_fraction_v3_a1k3.json"),
    ("E5 mech GLM",  MECH / "E5_v3w5_android_glm.json",    MECH / "E5_v3w5_android_glm_k3.json"),
    ("E5 mech DS",   MECH / "E5_v3w5_android_ds.json",     MECH / "E5_v3w5_android_ds_k3.json"),
]


def load(path: Path):
    if not path.exists():
        print(f"[skip] {path.name} not found yet")
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        print(f"[skip] {path.name} unreadable/partial: "
              f"{exc.__class__.__name__}: {exc}")
        return None


def rows_of(cell: dict) -> dict:
    """Policy/variant rows: dicts that carry a mean_tokens stat."""
    return {k: v for k, v in cell.items()
            if isinstance(v, dict) and "mean_tokens" in v}


def safe_ratio(new, old):
    if new is None or old is None or abs(old) < 1e-12:
        return None
    return new / old


def fmt_ratio(r) -> str:
    return " n/a " if r is None else f"x{r:.2f}"


def moved(r) -> bool:
    return r is not None and abs(r - 1.0) > FLAG


def mech_value(cell: dict, row: dict):
    v = row.get("rel_clairvoyant")
    if v is None:
        ct = cell.get("clairvoyant_tokens")
        if ct:
            v = row["mean_tokens"] / ct
    return v


def compare_row(name: str, ro: dict, rn: dict, cell_o: dict, cell_n: dict,
                mech: bool) -> tuple[int, int]:
    """Print one row comparison; return (value_flags, token_flags)."""
    if mech:
        vo = mech_value(cell_o, ro)
        vn = mech_value(cell_n, rn)
    else:
        vo, vn = ro.get("rel_to_ours"), rn.get("rel_to_ours")
    to, tn = ro.get("mean_tokens"), rn.get("mean_tokens")
    rv, rt = safe_ratio(vn, vo), safe_ratio(tn, to)

    mark = "!!" if moved(rv) else "  "
    tok_note = ""
    if moved(rt) and not moved(rv):
        tok_note = f"  [tokens moved >{FLAG:.0%}: {fmt_ratio(rt)}]"
    vtxt = (f"{'n/a' if vo is None else f'{vo:.4f}'} -> "
            f"{'n/a' if vn is None else f'{vn:.4f}'} {fmt_ratio(rv)}")
    ttxt = (f"{'n/a' if to is None else format(to, ',.0f')} -> "
            f"{'n/a' if tn is None else format(tn, ',.0f')} {fmt_ratio(rt)}")
    print(f"    {mark} {name:<20} val {vtxt:<30} tok {ttxt}{tok_note}")
    return (1 if moved(rv) else 0), (1 if moved(rt) else 0)


def union_keys(old_d: dict, new_d: dict) -> list:
    keys = list(old_d)
    keys += [k for k in new_d if k not in old_d]
    return keys


def compare_cells(old_cells: dict, new_cells: dict, mech: bool
                  ) -> tuple[int, int, int, int]:
    """Compare per cell / per row.  Returns (cells, rows, value_flags,
    token_flags)."""
    n_cells = n_rows = n_vflag = n_tflag = 0
    for ck in union_keys(old_cells, new_cells):
        co, cn = old_cells.get(ck), new_cells.get(ck)
        if not isinstance(co, dict) or not isinstance(cn, dict):
            print(f"  {ck:<32} [cell only in "
                  f"{'new' if co is None else 'old'} file -- skipped]")
            continue
        n_cells += 1
        ro, rn = rows_of(co), rows_of(cn)
        print(f"  {ck}")
        for rk in union_keys(ro, rn):
            if rk not in ro or rk not in rn:
                print(f"       {rk:<20} [row only in "
                      f"{'new' if rk not in ro else 'old'} file]")
                continue
            vf, tf = compare_row(rk, ro[rk], rn[rk], co, cn, mech)
            n_rows += 1
            n_vflag += vf
            n_tflag += tf
    return n_cells, n_rows, n_vflag, n_tflag


def compare_streams(old_s: dict, new_s: dict) -> tuple[int, int]:
    n = n_flag = 0
    fields = ("eligible_fraction_arrivals", "eligible_fraction_families")
    for sk in union_keys(old_s, new_s):
        so, sn = old_s.get(sk), new_s.get(sk)
        if not isinstance(so, dict) or not isinstance(sn, dict):
            print(f"  stream {sk}: [only in "
                  f"{'new' if so is None else 'old'} file -- skipped]")
            continue
        for f in fields:
            vo, vn = so.get(f), sn.get(f)
            r = safe_ratio(vn, vo)
            mark = "!!" if moved(r) else "  "
            print(f"    {mark} {sk:<12} {f:<30} "
                  f"{'n/a' if vo is None else f'{vo:.4f}'} -> "
                  f"{'n/a' if vn is None else f'{vn:.4f}'} {fmt_ratio(r)}")
            n += 1
            n_flag += 1 if moved(r) else 0
    return n, n_flag


def mode_of(data: dict) -> str:
    if "streams" in data:
        return "eligible"
    cells = data.get("cells", {})
    for k, c in cells.items():
        if k.startswith("_") or not isinstance(c, dict):
            continue
        return "mech" if "clairvoyant_tokens" in c else "policy"
    return "policy"


def main() -> None:
    skipped: list = []
    grand = {"pairs": 0, "cells": 0, "rows": 0, "vflag": 0, "tflag": 0}
    for label, old_p, new_p in PAIRS:
        print(f"\n===== {label}: {old_p.name} vs {new_p.name} =====")
        old = load(old_p)
        new = load(new_p)
        if old is None or new is None:
            skipped.append(label)
            continue
        mode = mode_of(old)
        if mode_of(new) != mode:
            print(f"[skip] structure changed between old ({mode}) and "
                  f"new ({mode_of(new)}) -- not comparable")
            skipped.append(label)
            continue
        n_cells = n_rows = n_vflag = n_tflag = 0
        if mode in ("policy", "mech"):
            n_cells, n_rows, n_vflag, n_tflag = compare_cells(
                old.get("cells", {}), new.get("cells", {}), mode == "mech")
        elif mode == "eligible":
            so, sn = old.get("streams", {}), new.get("streams", {})
            print("  -- per-stream eligible fractions --")
            nf_s, fl_s = compare_streams(so, sn)
            print("  -- per-cell policy rows --")
            n_cells, n_rows, n_vflag, n_tflag = compare_cells(
                old.get("cells", {}), new.get("cells", {}), False)
            n_tflag += fl_s  # stream-fraction flags travel with the total
            n_rows += nf_s
        status = (f"OK, no row moved >{FLAG:.0%}"
                  if (n_vflag == 0 and n_tflag == 0)
                  else f"{n_vflag} value-flag(s), {n_tflag} token-flag(s)")
        print(f"  -- {label}: {n_cells} cells, {n_rows} rows compared; "
              f"{status}")
        grand["pairs"] += 1
        grand["cells"] += n_cells
        grand["rows"] += n_rows
        grand["vflag"] += n_vflag
        grand["tflag"] += n_tflag

    print("\n===== summary =====")
    print(f"pairs compared : {grand['pairs']}/{len(PAIRS)}")
    if skipped:
        print(f"skipped (file absent/partial): {', '.join(skipped)}")
    print(f"cells compared : {grand['cells']}")
    print(f"rows compared  : {grand['rows']}")
    print(f"flagged >{FLAG:.0%}: {grand['vflag']} on the summary value, "
          f"{grand['tflag']} on mean_tokens/streams")


if __name__ == "__main__":
    main()
