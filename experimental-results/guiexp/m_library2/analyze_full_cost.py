"""Analyze the deploy-full-cost measurement -> summary.json + printed tables.

Corrected accounting (per protocol):
  saving(n) = c0 - d_full(n) - q(n)*c0        with c0 = 203,764 (GLM wizard)
  N*(n)     = C_eff / saving(n)               with C_eff = 14,983
Routing/manifest cost is a COMMON PER-ARRIVAL TAX (paid by every arrival,
matched or not) and is therefore NOT counted as enlarging the saving -- the
d_full(n) term inside the saving is the matched-use path only; the tax on
non-matched arrivals is reported separately as the stream tax.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"

C0 = 203_764.0        # GLM wizard discover mean (t11_grid)
C_EFF = 14_983.0      # GLM compile (t13_compilepath, 3-attempt mean)
Q0 = 5 / 30           # deploy30 GLM oracle_fail rate = 0.1667
M = 91.0              # tokens per manifest entry (m_library, API ground truth)

MATCH_IDS = {"G1_calendar_wizard", "G2_todo_add", "G3_flights_roundtrip",
             "G4_email_attachment", "G5_spreadsheet_row", "G6_messages_send"}


def load(path: Path) -> list[dict]:
    recs = []
    for line in path.read_text().splitlines():
        if line.strip():
            recs.append(json.loads(line))
    return recs


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def combined_cells(recs, model, n, seed=None):
    return [r for r in recs if r["phase"] == "combined" and r["model"] == model
            and r["n"] == n and (seed is None or r["seed"] == seed)]


def cell_stats(rs):
    if not rs:
        return None
    match = [r for r in rs if r["goal_id"] in MATCH_IDS]
    scored = [r for r in rs if r.get("binding_exact") is not None]
    exact = [r for r in scored if r["binding_exact"]]
    # gt=use cells among match instances
    gt_use = [r for r in match if r["gt_kind"] == "use"]
    gt_none = [r for r in rs if r["gt_kind"] == "none"]
    # end-to-end exact over gt=use cells: routed AND exact binding
    e2e = [r for r in gt_use if r["routing_correct"] and r.get("binding_exact")]
    errs = Counter(r["error_type"] for r in rs)
    ffails = Counter()
    for r in scored:
        for (f, kind, got, exp) in (r["field_fails"] or []):
            ffails[kind] += 1
    tok_all = [r["prompt_tokens"] + r["completion_tokens"] for r in rs]
    tok_match = [r["prompt_tokens"] + r["completion_tokens"] for r in gt_use]
    tok_none = [r["prompt_tokens"] + r["completion_tokens"] for r in gt_none]
    return dict(
        calls=len(rs),
        routing_acc=errs["correct"] / len(rs),
        match_cells=len(gt_use),
        match_acc=(sum(r["routing_correct"] for r in gt_use) / len(gt_use)
                   if gt_use else None),
        none_cells=len(gt_none),
        none_acc=(sum(r["routing_correct"] for r in gt_none) / len(gt_none)
                  if gt_none else None),
        errors=dict(errs),
        binding_scored=len(scored),
        binding_exact=len(exact),
        fidelity=(len(exact) / len(scored) if scored else None),
        e2e_exact_cells=len(e2e),
        e2e_rate=(len(e2e) / len(gt_use) if gt_use else None),
        field_fail_taxonomy=dict(ffails),
        prompt_all=mean(r["prompt_tokens"] for r in rs),
        completion_all=mean(r["completion_tokens"] for r in rs),
        total_all=mean(tok_all),
        total_match=mean(tok_match) if tok_match else None,
        total_none=mean(tok_none) if tok_none else None,
        cost_per_call=mean(r["cost_usd"] or 0 for r in rs),
        cost_per_match_use=mean(r["cost_usd"] or 0 for r in gt_use) if gt_use else None,
    )


def main() -> None:
    recs = load(HERE / "full_cost_calls.jsonl")
    retry_path = HERE / "retry_calls.jsonl"
    retries = load(retry_path) if retry_path.exists() else []

    out = {"combined": {}, "split": {}, "retry": {}, "accounting": {}}

    # ---- combined ------------------------------------------------------
    for model in (GLM, DS):
        for n in (1, 5, 20, 100):
            seeds = ({0, 1} if model == GLM else {0})
            rs = [r for r in combined_cells(recs, model, n) if r["seed"] in seeds]
            if rs:
                out["combined"][f"{model}|n={n}"] = cell_stats(rs)

    # ---- split (GLM n=100 seed 0) --------------------------------------
    routes = [r for r in recs if r["phase"] == "split_route"]
    extracts = {(r["goal_id"], r.get("inst")): r for r in recs
                if r["phase"] == "split_extract"}
    if routes:
        st = cell_stats(routes)
        per_use_tokens = []
        per_use_cost = []
        e2e_exact = 0
        gt_use = [r for r in routes if r["gt_kind"] == "use"]
        for r in gt_use:
            ex = extracts.get((r["goal_id"], r.get("inst")))
            if r["routing_correct"] and ex is not None:
                per_use_tokens.append(
                    r["prompt_tokens"] + r["completion_tokens"]
                    + ex["prompt_tokens"] + ex["completion_tokens"])
                per_use_cost.append((r["cost_usd"] or 0) + (ex["cost_usd"] or 0))
                if ex["binding_exact"]:
                    e2e_exact += 1
            elif r["routing_correct"]:
                per_use_tokens.append(
                    r["prompt_tokens"] + r["completion_tokens"])
                per_use_cost.append(r["cost_usd"] or 0)
        ex_scored = [e for e in extracts.values() if e["binding_exact"] is not None]
        st["tokens_per_matched_use_route_extract"] = mean(per_use_tokens)
        st["cost_per_matched_use_route_extract"] = mean(per_use_cost)
        st["route_tokens_mean"] = mean(
            r["prompt_tokens"] + r["completion_tokens"] for r in routes)
        st["extract_tokens_mean"] = mean(
            e["prompt_tokens"] + e["completion_tokens"] for e in extracts.values())
        st["e2e_exact_route_extract"] = e2e_exact
        st["e2e_rate_route_extract"] = e2e_exact / len(gt_use) if gt_use else None
        st["extract_scored"] = len(ex_scored)
        st["extract_exact"] = sum(e["binding_exact"] for e in ex_scored)
        out["split"]["glm|n=100|s0"] = st

    # ---- retry ----------------------------------------------------------
    if retries:
        fixed = sum(r["fixed"] for r in retries)
        out["retry"] = dict(
            fails=len(retries), fixed=fixed, fixed_rate=fixed / len(retries),
            mean_retry_tokens=mean(r["prompt_tokens"] + r["completion_tokens"]
                                   for r in retries),
            mean_retry_cost=mean(r["cost_usd"] or 0 for r in retries),
            total_retry_cost=sum(r["cost_usd"] or 0 for r in retries),
        )

    # ---- corrected accounting -------------------------------------------
    acc = {}
    for n in (1, 5, 20, 100):
        cell = out["combined"].get(f"{GLM}|n={n}")
        if not cell:
            continue
        d_full = cell["total_match"]           # per matched use (combined call)
        d_arrival = cell["total_all"]          # per-arrival tax (all goals)
        fid = cell["fidelity"]
        q_fid = 1 - fid if fid is not None else None
        q_e2e = 1 - cell["e2e_rate"] if cell["e2e_rate"] is not None else None
        row = dict(d_full=d_full, d_per_arrival=d_arrival,
                   fidelity=fid, q_fid=q_fid, q_e2e=q_e2e)
        for qname, q in (("q0", Q0), ("q_fid", q_fid), ("q_e2e", q_e2e)):
            if q is None:
                continue
            saving = C0 - d_full - q * C0
            row[f"saving_{qname}"] = saving
            row[f"Nstar_{qname}"] = C_EFF / saving if saving > 0 else None
        acc[f"n={n}"] = row
    out["accounting"] = acc

    # stream-tax extrapolation: per-arrival prompt = a + m*n; completion = c
    prompt_pts = {n: out["combined"][f"{GLM}|n={n}"]["prompt_all"]
                  for n in (1, 5, 20, 100) if f"{GLM}|n={n}" in out["combined"]}
    if len(prompt_pts) >= 2:
        # least squares slope/intercept
        xs = list(prompt_pts)
        mx, my = mean(xs), mean(prompt_pts[x] for x in xs)
        slope = sum((x - mx) * (prompt_pts[x] - my) for x in xs) / sum(
            (x - mx) ** 2 for x in xs)
        intercept = my - slope * mx
        comp100 = out["combined"][f"{GLM}|n=100"]["completion_all"]
        out["stream_tax"] = dict(
            prompt_law=f"prompt(n) = {intercept:.0f} + {slope:.1f}·n",
            slope_tokens_per_entry=slope,
            completion_assumed=comp100,
            tax_n100=out["combined"][f"{GLM}|n=100"]["total_all"],
            tax_n846=intercept + slope * 846 + comp100,
            tax_n846_manifest_only=M * 846,
        )

    out["totals"] = dict(
        combined_calls=len([r for r in recs if r["phase"] == "combined"]),
        split_route_calls=len(routes),
        split_extract_calls=len(extracts),
        retry_calls=len(retries),
        total_cost_usd=sum((r["cost_usd"] or 0) for r in recs)
        + sum((r["cost_usd"] or 0) for r in retries),
    )
    (HERE / "summary.json").write_text(json.dumps(out, indent=1))

    # ---- printed digest -------------------------------------------------
    print("== COMBINED ==")
    for k, v in out["combined"].items():
        print(f"{k}: acc={v['routing_acc']:.3f} (match {v['match_acc']}/{v['match_cells']}"
              f" none {v['none_acc']:.3f}) fidelity={v['fidelity']} "
              f"({v['binding_exact']}/{v['binding_scored']}) "
              f"e2e={v['e2e_rate']} tok: p={v['prompt_all']:.0f} c={v['completion_all']:.0f} "
              f"d_full(match)={v['total_match']:.0f} tax(all)={v['total_all']:.0f} "
              f"${v['cost_per_call']:.5f}")
        if v["field_fail_taxonomy"]:
            print("   field fails:", v["field_fail_taxonomy"])
    if out.get("split"):
        v = out["split"]["glm|n=100|s0"]
        print("== SPLIT n=100 ==")
        print(f"route acc={v['routing_acc']:.3f} extract fidelity="
              f"{v['extract_exact']}/{v['extract_scored']} e2e={v['e2e_rate_route_extract']}"
              f" tok/use={v['tokens_per_matched_use_route_extract']:.0f} "
              f"(route {v['route_tokens_mean']:.0f} + extract {v['extract_tokens_mean']:.0f})"
              f" $/use={v['cost_per_matched_use_route_extract']:.6f}")
    if out["retry"]:
        print("== RETRY ==", out["retry"])
    print("== ACCOUNTING (c0=203764, C=14983) ==")
    for k, v in acc.items():
        print(k, {kk: (round(vv, 3) if isinstance(vv, float) else vv)
                  for kk, vv in v.items()})
    if "stream_tax" in out:
        print("== STREAM TAX ==", out["stream_tax"])
    print("== TOTALS ==", out["totals"])


if __name__ == "__main__":
    main()
