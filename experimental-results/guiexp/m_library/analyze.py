"""Analyze the m_library experiment -> analysis.json (feeds the report).

Inputs (all under this dir):
  routing_calls.jsonl  per-call routing records (run_routing.py)
  e2e/floor_n0_s0/trajectory.jsonl, e2e/floor_n100_s0/trajectory.jsonl
  e2e/discover_n{0,20,100}_s{0,1}/trajectory.jsonl
  manifest_stats.json  (build_manifests.py)

Outputs: analysis.json with sections m / routing / e2e / projection.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GLM = "z-ai/glm-5.3-flash"
DS = "deepseek/deepseek-v4-flash-vision-exp"

# paper constants (given)
C0 = 203_764      # wizard GLM discover episode tokens
S_STEPS = 12      # steps per reactive episode (paper constant)
D0 = 347          # deployed-use tokens
Q0 = 0.17         # program failure rate
C_EFF = 14_983    # effective compile cost per family (tokens)
F = 846           # Sepsis families


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def main() -> None:
    out: dict = {}

    # ---------------- m (token cost per manifest entry) -------------------
    stats = json.loads((HERE / "manifest_stats.json").read_text())
    per_entry = stats["per_entry_tokens"]
    out["m"] = {
        "m_design_tokens_per_entry": sum(per_entry.values()) / len(per_entry),
        "manifest_tokens_by_n": {k: v["tokens_o200k"] for k, v in stats["manifests"].items()},
        "wizard_position_by_n": {k: v["wizard_position"] for k, v in stats["manifests"].items()},
    }

    calls = load_jsonl(HERE / "routing_calls.jsonl")

    # API ground truth: mean prompt tokens per (model, n)
    def mean_prompt(model: str, n: int) -> float:
        rs = [c for c in calls if c["model"] == model and c["n"] == n]
        return sum(c["prompt_tokens"] for c in rs) / max(len(rs), 1)

    glm0 = mean_prompt(GLM, 0)
    m_api = {}
    for n in (1, 5, 20, 100):
        m_api[n] = (mean_prompt(GLM, n) - glm0) / n
    out["m"]["m_api_per_entry_by_n"] = m_api
    out["m"]["m_api_best"] = m_api[100]  # full manifest, least rounding

    # ---------------- routing accuracy -------------------------------------
    routing: dict = {"cells": {}, "per_goal_n100": {}}
    for model in (GLM, DS):
        for n in (0, 1, 5, 20, 100):
            rs = [c for c in calls if c["model"] == model and c["n"] == n]
            if not rs:
                continue
            errs = {e: sum(1 for c in rs if c["error_type"] == e)
                    for e in ("correct", "wrong_program", "false_positive",
                              "false_negative", "unlisted_name", "malformed")}
            match_cells = [c for c in rs if c["gt_kind"] == "use"]
            dist_cells = [c for c in rs if c["gt_kind"] == "none"]
            routing["cells"][f"{model}|n={n}"] = {
                "calls": len(rs),
                "accuracy": errs["correct"] / len(rs),
                "err_rate": 1 - errs["correct"] / len(rs),
                "match_cell_accuracy": (sum(c["correct"] for c in match_cells) / len(match_cells)) if match_cells else None,
                "match_cells": len(match_cells),
                "distractor_cell_accuracy": (sum(c["correct"] for c in dist_cells) / len(dist_cells)) if dist_cells else None,
                "distractor_cells": len(dist_cells),
                "errors": errs,
                "mean_prompt_tokens": sum(c["prompt_tokens"] for c in rs) / len(rs),
                "mean_completion_tokens": sum(c["completion_tokens"] for c in rs) / len(rs),
                "cost_usd": sum(c["cost_usd"] or 0 for c in rs),
            }
    # epsilon(n) = err_rate(n) - err_rate(0) for GLM; DS: raw err (no n=0 cell)
    eps: dict = {}
    glm_cells = {n: routing["cells"].get(f"{GLM}|n={n}") for n in (0, 1, 5, 20, 100)}
    e0 = glm_cells[0]["err_rate"] if glm_cells.get(0) else 0.0
    for n, cell in glm_cells.items():
        if cell:
            eps[str(n)] = {"err_rate": cell["err_rate"], "epsilon": cell["err_rate"] - e0}
    routing["epsilon_glm"] = eps
    for n in (20, 100):
        cell = routing["cells"].get(f"{DS}|n={n}")
        if cell:
            routing.setdefault("epsilon_ds_spot", {})[str(n)] = {
                "err_rate": cell["err_rate"], "epsilon_vs_glm0": cell["err_rate"] - e0}
    routing["total_cost_usd"] = sum(c["cost_usd"] or 0 for c in calls)
    routing["total_calls"] = len(calls)

    # per-goal detail at each n for the report (GLM seed-0/1 aggregated)
    for gid in sorted({c["goal_id"] for c in calls}):
        row = {}
        for n in (0, 1, 5, 20, 100):
            rs = [c for c in calls if c["model"] == GLM and c["n"] == n and c["goal_id"] == gid]
            if rs:
                row[str(n)] = {"gt": rs[0]["gt"], "ok": sum(c["correct"] for c in rs),
                               "preds": sorted({(c["pred_name"] or c["pred_kind"]) for c in rs})}
        routing["per_goal_n100"][gid] = row

    out["routing"] = routing

    # ---------------- e2e probe -------------------------------------------
    e2e: dict = {}
    floor0 = load_jsonl(HERE / "e2e/floor_n0_s0/trajectory.jsonl")
    floor100 = load_jsonl(HERE / "e2e/floor_n100_s0/trajectory.jsonl")
    f0_call = next(r for r in floor0 if "prompt_tokens" in r.get("usage", {}))
    f100_call = next(r for r in floor100 if "prompt_tokens" in r.get("usage", {}))
    delta_floor = f100_call["usage"]["prompt_tokens"] - f0_call["usage"]["prompt_tokens"]
    e2e["floor"] = {
        "floor0_prompt_tokens": f0_call["usage"]["prompt_tokens"],
        "floor0_cost_usd": f0_call["usage"].get("cost_usd"),
        "floor100_prompt_tokens": f100_call["usage"]["prompt_tokens"],
        "floor100_cost_usd": f100_call["usage"].get("cost_usd"),
        "delta_prompt_tokens_n100": delta_floor,
        "m_e2e_per_entry": delta_floor / 100,
        "floor_projection_n846": f0_call["usage"]["prompt_tokens"] + delta_floor / 100 * 846,
    }

    e2e["episodes"] = {}
    per_call_mean_prompt: dict[str, float] = {}
    for n in (0, 20, 100):
        for seed in (0, 1):
            tag = f"discover_n{n}_s{seed}"
            tr = HERE / "e2e" / tag / "trajectory.jsonl"
            if not tr.exists():
                continue
            recs = load_jsonl(tr)
            final = next((r for r in recs if r.get("record_type") == "final"), None)
            steps = [r for r in recs if "prompt_tokens" in r.get("usage", {})]
            prompts = [r["usage"]["prompt_tokens"] for r in steps]
            e2e["episodes"][tag] = {
                "success": final and final.get("success"),
                "steps": final and final.get("steps"),
                "model_calls": final and final.get("model_calls"),
                "total_tokens": final and final.get("total_tokens"),
                "total_cost_usd": final and final.get("total_cost_usd"),
                "mean_prompt_tokens": sum(prompts) / len(prompts),
                "first_call_prompt_tokens": prompts[0] if prompts else None,
                "manifest": final and final.get("manifest"),
            }
            per_call_mean_prompt[tag] = sum(prompts) / len(prompts)

    # per-call delta vs same-seed n=0
    deltas = {}
    for n in (20, 100):
        for seed in (0, 1):
            a = per_call_mean_prompt.get(f"discover_n0_s{seed}")
            b = per_call_mean_prompt.get(f"discover_n{n}_s{seed}")
            if a and b:
                deltas[f"n{n}_s{seed}"] = b - a
    e2e["per_call_prompt_delta_vs_n0"] = deltas
    if deltas:
        e2e["measured_extra_per_call_per_entry"] = sum(deltas.values()) / len(deltas)

    out["e2e"] = e2e

    # ---------------- projection: fits the formula ------------------------
    m = out["m"]["m_api_best"]
    eps_pts = [(n, eps[str(n)]["epsilon"]) for n in (0, 1, 5, 20, 100) if str(n) in eps]
    # linear fit epsilon(n) = a*n + b through the measured GLM points
    xs = [p[0] for p in eps_pts]
    ys = [p[1] for p in eps_pts]
    nx = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in eps_pts)
    denom = nx * sxx - sx * sx
    a = (nx * sxy - sx * sy) / denom if denom else 0.0
    b = (sy - a * sx) / nx

    def eps_at(n: int) -> float:
        if n <= 100:
            # piecewise: measured points, linear interp between
            for (x0, y0), (x1, y1) in zip(eps_pts, eps_pts[1:]):
                if x0 <= n <= x1:
                    return y0 + (y1 - y0) * (n - x0) / (x1 - x0)
            return eps_pts[-1][1] if n >= eps_pts[-1][0] else eps_pts[0][1]
        # extrapolate with fitted slope beyond the measured range
        return max(0.0, a * n + b)

    floor0_tok = e2e["floor"]["floor0_prompt_tokens"]
    table = {}
    for n in (0, 1, 5, 20, 100, 200, 500, 846):
        c_n = C0 + S_STEPS * m * n
        d_n = D0 + m * n
        q_n = Q0 + eps_at(n)
        saving = c_n - d_n - q_n * c_n
        table[str(n)] = {
            "eps": eps_at(n),
            "floor": floor0_tok + m * n,
            "c": c_n,
            "d": d_n,
            "q": q_n,
            "saving_per_use": saving,
            "N_star": (C_EFF / saving) if saving > 0 else None,
        }
    out["projection"] = {
        "constants": {"c0": C0, "S": S_STEPS, "d0": D0, "q0": Q0,
                      "C_eff": C_EFF, "F": F, "m": m,
                      "floor0_measured": floor0_tok},
        "epsilon_fit": {"slope_a": a, "intercept_b": b,
                        "points": dict((str(x), y) for x, y in eps_pts)},
        "table": table,
        "always_compile_end_state": {
            "n": F,
            "floor": floor0_tok + m * F,
            "c": C0 + S_STEPS * m * F,
            "q": Q0 + eps_at(F),
            "saving_per_use": table["846"]["saving_per_use"],
            "N_star": table["846"]["N_star"],
        },
    }

    (HERE / "analysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["projection"]["table"], indent=1))
    print("m =", out["m"])
    print("eps glm =", routing["epsilon_glm"])
    print("routing cost:", routing["total_cost_usd"])


if __name__ == "__main__":
    main()
