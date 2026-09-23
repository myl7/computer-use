"""Render PACE tables with paired uncertainty from frozen source records.

This script changes only generated table files and its verification record.
It does not edit body.tex/head.tex, run simulations, or make model calls.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
ANALYSIS = ROOT / "analysis/pace_edit_20260923/uncertainty"
TABLE_DATA = ANALYSIS / "table-data.json"
MEASUREMENT_DATA = PAPER / "measurement_update_20260918.json"
DATA = json.loads(TABLE_DATA.read_text())
MEASUREMENT = json.loads(MEASUREMENT_DATA.read_text())
PROVENANCE = json.loads((ANALYSIS / "provenance.json").read_text())
MODELS = DATA["models_in_display_order"]
FAMILIES = ["ContactsAddContact", "MarkorDeleteNote", "SimpleCalendarAddOneEvent",
            "OsmAndMarker", "CalcTableSave", "WriterMemoSave", "CommentPost"]
NAMES = dict(zip(FAMILIES, ["Contacts", "Markor delete", "Calendar", "OsmAnd marker",
                          "Calc table", "Writer memo", "Reddit comment"]))
MEASURE = DATA["measurement"]
MAPS = {name: {(row["model"], row["family"]): row for row in rows}
        for name, rows in MEASURE.items() if isinstance(rows, list)}
LEGEND = "Blue, amber, and green identify GLM, DeepSeek, and Qwen, respectively."
CI_TEXT = "Nonzero-width 95 percent paired bootstrap intervals appear as upper and lower offsets below the mean; exact zero-width intervals are omitted."
CI_SCOPE = "Intervals use 2000 shared repetition resamples and condition on the tested scenarios and supplied profiles."
SMALL_TEXT = r"An offset marked $<.001$ is positive but smaller than $0.001$."
MAIN_POLICIES = [
    ("reactive", "ReAct"),
    ("autorpa_once", "AutoRPA"),
    ("toolpro_cost", "ToolPro"),
    ("safe_projected_025", r"\textbf{PACE}"),
]
FULL_POLICIES = [
    ("reactive", "ReAct"), ("autorpa_once", "AutoRPA"),
    ("toolpro_cost", "ToolPro"), ("earliest_cap", "Eager + allowance"),
    ("fixed10_cap", "After 10 arrivals"), ("success10_cap", "After 10 successes"),
    ("breakeven_cap", "Savings threshold"), ("projected_cap", "Projected + allowance"),
    ("safe_earliest_allowance_025", "Eager + both checks"),
    ("safe_arrival10_allowance_025", "Arrival-10 + both"),
    ("safe_projected_allowance_025", "Projected + both"),
    ("safe_count_025", "Count + budget"), ("safe_history_025", "Optimistic + budget"),
    ("safe_once_025", "One initial try"), ("safe_projected_025", r"\textbf{PACE}"),
]
ABLATIONS = [("safe_projected_025", r"\textbf{PACE}"),
             ("safe_earliest_025", "No savings test"), ("projected", "No cost budget"),
             ("pace_narrow_price", "Equal attempt prices"), ("earliest", "Eager retry")]
SENSITIVITY_ROWS = [("missing_price_r0.5", r"$r=0.5$"), ("missing_price_r1", r"$r=1$"),
                    ("missing_price_r2", r"$r=2$"), ("missing_price_r5", r"$r=5$"),
                    ("adverse_probability", r"Cost-dependent $p$"),
                    ("adverse_probability_recurrence", r"Cost-dependent $p$ + recurrence")]
RECORDS = []
OUTPUTS = {}
RANKS = []


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(left, right):
    assert math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-8), (left, right)


def triple(values, width="3.4em"):
    assert len(values) == 3
    return r"\modelcell{" + width + "}" + "".join("{" + value + "}" for value in values)


def decimal(value, digits):
    return f"{value:.{digits}f}"


def offset(value, digits, unit=""):
    if abs(value) <= 1e-12:
        return "0"
    assert value > 0, value
    quantum = 10 ** (-digits)
    if value < quantum:
        threshold = decimal(quantum, digits)
        if threshold.startswith("0."):
            threshold = threshold[1:]
        return r"{<}" + threshold + unit
    out = decimal(value, digits)
    if out.startswith("0."):
        out = out[1:]
    return out + unit


def marked(text, rank):
    if rank == 1:
        return r"\textbf{" + text + "}"
    if rank == 2:
        return r"\underline{" + text + "}"
    return text


def ranking(values, where, digits=3):
    shown = {key: float(f"{value:.{digits}f}") for key, value in values.items()}
    levels = sorted(set(shown.values()))
    best = [key for key, value in shown.items() if value == levels[0]]
    result = {key: 1 for key in best}
    if len(best) == 1 and len(levels) > 1:
        result.update({key: 2 for key, value in shown.items() if value == levels[1]})
    RANKS.append({"where": where, "displayed_values": shown, "ranks": result})
    return result


def point(value, where, *, digits=3, tall=False, text=None, rank=None):
    rendered = text if text is not None else decimal(value, digits)
    RECORDS.append({"where": where, "kind": "point", "estimate": value,
                    "display": rendered, "ci95": None, "rank": rank})
    rendered = marked(rendered, rank)
    return r"\statpoint{" + rendered + "}" if tall else rendered


def ci(record, where, *, digits=3, scale=1.0, unit="", point_text=None, rank=None):
    value = record["estimate"] / scale
    low, high = [v / scale for v in record["ci95"]]
    assert low - 1e-10 <= value <= high + 1e-10, (where, value, low, high)
    rendered = point_text if point_text is not None else decimal(value, digits) + unit
    rendered = marked(rendered, rank)
    if abs(high - low) <= 1e-12 and abs(value - low) <= 1e-12:
        result = r"\statpoint{" + rendered + "}"
        errors = None
    else:
        upper, lower = offset(high - value, digits, unit), offset(value - low, digits, unit)
        result = r"\estci{" + rendered + "}{+" + upper + "}{-" + lower + "}"
        errors = {"upper": upper, "lower": lower}
    RECORDS.append({"where": where, "kind": "interval", "estimate": record["estimate"],
                    "ci95": record["ci95"], "scale": scale, "unit": unit,
                    "display_point": rendered, "display_offsets": errors,
                    "method": record["method"], "rank": rank})
    return result


def count_ci(record, where):
    lower, upper = record["ci95"]
    text = r"\countci{" + record["count_display"] + "}{" + decimal(lower, 3) + "}{" + decimal(upper, 3) + "}"
    RECORDS.append({"where": where, "kind": "count_interval", "successes": record["successes"],
                    "trials": record["trials"], "estimate": record["estimate"],
                    "ci95": record["ci95"], "display_count": record["count_display"],
                    "display_interval": [decimal(lower, 3), decimal(upper, 3)], "method": record["method"]})
    return text


def missing(tall=False):
    return r"\statpoint{--}" if tall else "--"


def table(label, headers, rows, caption, spacing="1.2pt", placement="htbp"):
    heads = [r"\multicolumn{1}{c}{" + value + "}" for value in headers]
    return "\n".join([
        r"\begin{table}[" + placement + "]", r"\centering", r"\caption{" + caption + "}",
        r"\label{" + label + "}", r"\tablefont", r"\setlength{\tabcolsep}{" + spacing + "}",
        r"\renewcommand{\arraystretch}{0.95}", r"\begin{tabular}{@{}l" + "r" * (len(headers) - 1) + "@{}}",
        r"\toprule", " & ".join(heads) + r" \\", r"\midrule", *rows,
        r"\bottomrule", r"\end{tabular}", r"\end{table}"])


def row(label, cells):
    return " & ".join([label] + cells) + r" \\"


def caption(*sentences):
    return "\n".join(sentence for sentence in sentences if sentence)


def summary(group, model, policy):
    return DATA["online_summary"][group]["by_model"][model][policy]


def render_comparisons(policies, suffix=""):
    gridrows, realrows = [], []
    ranks = {}
    if not suffix:
        for group in ("original_grid", "fresh_grid"):
            for model in MODELS:
                for field in ("mean_ratio_agent", "max_condition_mean_ratio_agent"):
                    key = (group, model, field)
                    ranks[key] = ranking({p: summary(group, model, p)[field]["estimate"] for p, _ in policies}, "/".join(key))
        for pattern in ("sepsis", "bpi2019", "wiki_A", "wiki_B"):
            for model in MODELS:
                cell = next(c for c in DATA["real_cells"] if c["model"] == model and c["pattern"] == pattern)
                ranks[(pattern, model)] = ranking({p: cell["policies"][p]["ratio_agent"]["estimate"] for p, _ in policies}, f"{pattern}/{model}")
    for policy, label in policies:
        if policy == ("safe_earliest_allowance_025" if suffix else "safe_projected_025"):
            gridrows.append(r"\midrule")
            realrows.append(r"\midrule")
        metrics = []
        for group in ("original_grid", "fresh_grid"):
            values = []
            for model in MODELS:
                record = summary(group, model, policy)["mean_ratio_agent"]
                where = f"tab:policysim{suffix}/{group}/{model}/{policy}/mean"
                rank = ranks.get((group, model, "mean_ratio_agent"), {}).get(policy)
                values.append(ci(record, where) if suffix else point(record["estimate"], where, rank=rank))
            metrics.append(triple(values))
            metrics.append(triple([point(summary(group, model, policy)["max_condition_mean_ratio_agent"]["estimate"],
                                         f"tab:policysim{suffix}/{group}/{model}/{policy}/max",
                                         rank=ranks.get((group, model, "max_condition_mean_ratio_agent"), {}).get(policy)) for model in MODELS]))
        gridrows.append(row(label, metrics))
        logmetrics = []
        for pattern in ("sepsis", "bpi2019", "wiki_A", "wiki_B"):
            values = []
            for model in MODELS:
                record = next(c for c in DATA["real_cells"] if c["model"] == model and c["pattern"] == pattern)["policies"][policy]["ratio_agent"]
                where = f"tab:realstreams{suffix}/{pattern}/{model}/{policy}"
                values.append(ci(record, where) if suffix else point(record["estimate"], where, rank=ranks.get((pattern, model), {}).get(policy)))
            logmetrics.append(triple(values))
        realrows.append(row(label, logmetrics))
    adaptation = r"AutoRPA and ToolPro are decision-rule adaptations defined in Appendix~\ref{app:baselines}."
    complete = (r"The selected comparisons appear in Tables~\ref{tab:policysim} and~\ref{tab:realstreams}." if suffix
                else r"Appendix~\ref{app:full-comparisons} reports all fifteen rules.")
    rank_note = ("" if suffix else r"For each model and column, bold marks the lowest displayed value and underlining marks second place; a tied best receives no second-place mark.")
    rank_scope = "" if suffix else "Ranks use the displayed precision and do not denote statistical significance."
    grid_small = [SMALL_TEXT] if any("{<}" in r for r in gridrows) else []
    real_small = [SMALL_TEXT] if any("{<}" in r for r in realrows) else []
    gridcap = caption("Cost across the two parameter grids, relative to ReAct.",
                      "Each grid contains one hundred conditions per model and eight paired repetitions per condition.",
                      "Mean averages the condition-wise cost ratios, and Max is the largest tested condition mean.",
                      CI_TEXT, CI_SCOPE, *grid_small, LEGEND, rank_note, rank_scope, adaptation, complete)
    realcap = caption("Cost on four recorded arrival sequences, relative to ReAct.",
                      "Each value divides the policy's mean cost by ReAct's mean cost in the same condition, using ten paired repetitions.",
                      CI_TEXT, CI_SCOPE, *real_small, LEGEND, rank_note, rank_scope, adaptation, complete)
    if not suffix:
        rank_short = "Bold and underlining mark the best and second displayed values; tied best values suppress second-place marks."
        gridcap = caption("Cost ratios over two grids of 300 conditions each.",
                          "Mean averages condition-wise ratios; max is the largest tested condition mean.",
                          LEGEND, rank_short,
                          r"Conditional 95\% intervals are in Appendix~\ref{app:full-comparisons}.")
        realcap = caption("Cost ratios on four recorded arrival sequences, using ten paired repetitions.",
                          LEGEND, rank_short,
                          r"Conditional 95\% intervals are in Appendix~\ref{app:full-comparisons}.")
    else:
        gridcap += "\nReAct is exactly one by normalization; other fixed ratios also have zero-width intervals.\nThe descriptive maxima have no sampling interval."
        realcap += "\nReAct is exactly one by normalization; other fixed ratios also have zero-width intervals."
    grid = table("tab:policysim" + suffix,
                 ["Policy", r"\shortstack{Grid 1 mean\\($\times$ ReAct) $\downarrow$}", r"\shortstack{Grid 1 max\\($\times$ ReAct) $\downarrow$}",
                  r"\shortstack{Grid 2 mean\\($\times$ ReAct) $\downarrow$}", r"\shortstack{Grid 2 max\\($\times$ ReAct) $\downarrow$}"], gridrows, gridcap,
                 spacing="0.6pt", placement="!htbp")
    real = table("tab:realstreams" + suffix, ["Policy"] + [r"\shortstack{" + name + r"\\($\times$ ReAct) $\downarrow$}" for name in ["Sepsis", "BPI 2019", "Wiki tools", "Wiki humans"]],
                 realrows, realcap, spacing="0.6pt", placement="!htbp")
    return grid + "\n\n" + real


def render_ablation_absolute():
    rows = []
    for policy, label in ABLATIONS:
        cells = [triple([ci(summary(group, model, policy)["mean_ratio_agent"], f"tab:ablation/{group}/{model}/{policy}")
                         for model in MODELS]) for group in ("original_grid", "fresh_grid", "real_arrivals")]
        worst = max(summary(group, "all_models", policy)["max_condition_mean_ratio_agent"]["estimate"]
                    for group in ("original_grid", "fresh_grid"))
        cells.append(point(worst, f"tab:ablation/{policy}/max", tall=True))
        rows.append(row(label, cells))
    cap = caption("Absolute costs for the component ablations, relative to ReAct.",
                  "The first three columns show mean condition-wise cost ratios to ReAct, with positive and negative offsets of the 95 percent paired bootstrap interval.",
                  "Grid max is the largest tested condition mean across both grids and all three models.",
                  LEGEND, *([SMALL_TEXT] if any("{<}" in r for r in rows) else []),
                  "Equal attempt prices changes the proposal's price estimate while keeping actual charges and reservations unchanged.",
                  "No cost budget removes the protection layer, including manifest control.")
    return table("tab:ablation-absolute", ["Variant"] + [r"\shortstack{" + name + r"\\($\times$ ReAct)}" for name in ["Grid 1 mean", "Grid 2 mean", "Logs mean", "Grid max"]], rows, cap, spacing="1.0pt")


def signed_percent(value):
    if abs(value) <= 1e-12:
        return "0.00"
    if abs(value) < 0.1:
        digits = 3
        while round(value, digits) == 0:
            digits += 1
        return f"{value:+.{digits}f}"
    return f"{value:+.0f}" if abs(value) >= 100 else f"{value:+.2f}"


def render_ablation(with_intervals=False):
    rows = []
    suffix = "-effects-full" if with_intervals else ""
    for policy, label in ABLATIONS:
        cells = []
        for group in ("original_grid", "fresh_grid", "real_arrivals"):
            values = []
            for model in MODELS:
                source = summary(group, model, policy)["mean_ratio_pace"]
                delta = {"estimate": 100 * (source["estimate"] - 1),
                         "ci95": [100 * (x - 1) for x in source["ci95"]],
                         "method": "Paired percent cost change relative to PACE"}
                where = f"tab:ablation{suffix}/{group}/{model}/{policy}/percent"
                if with_intervals:
                    digits = 3 if 0 < abs(delta["estimate"]) < 0.1 else 1
                    values.append(ci(delta, where, digits=digits))
                else:
                    values.append(point(delta["estimate"], where, text=signed_percent(delta["estimate"])))
            cells.append(triple(values, "3.8em" if with_intervals else "3.4em"))
        maxima = [max(summary(group, model, policy)["max_condition_mean_ratio_agent"]["estimate"]
                      for group in ("original_grid", "fresh_grid")) for model in MODELS]
        cells.append(triple([point(x, f"tab:ablation{suffix}/{model}/{policy}/max") for model, x in zip(MODELS, maxima)]))
        rows.append(row(label, cells))
    cap = caption("Cost change after removing or simplifying PACE components.",
                  r"$\Delta$ averages within-condition percentage cost changes from full PACE; positive values mean higher cost.",
                  LEGEND,
                  ("Offsets show the conditional 95 percent paired interval in percentage points; exact zero-width intervals are omitted."
                   if with_intervals else r"Paired intervals are in Appendix~\ref{app:ablation-absolute}."),
                  "Grid max is the largest tested condition-mean ratio, without a sampling interval.")
    return table("tab:ablation" + suffix, ["Variant", r"\shortstack{Grid 1 $\Delta$ cost\\(\%)}",
                 r"\shortstack{Grid 2 $\Delta$ cost\\(\%)}", r"\shortstack{Logs $\Delta$ cost\\(\%)}",
                 r"\shortstack{Grid max\\($\times$ ReAct)}"], rows, cap, spacing="0.6pt" if with_intervals else "0.9pt")


def render_sensitivity():
    rows = []
    for scenario, label in SENSITIVITY_ROWS:
        metrics = [triple([ci(DATA["sensitivity_summary"][scenario]["by_model"][model][policy]["mean_ratio_agent"],
                              f"tab:price-sensitivity/{scenario}/{model}/{policy}/agent") for model in MODELS])
                   for policy in ("safe_projected_025", "pace_narrow_price")]
        metrics.append(ci(DATA["sensitivity_summary"][scenario]["by_model"]["all_models"]["pace_narrow_price"]["mean_ratio_pace"],
                          f"tab:price-sensitivity/{scenario}/all_models/pace_narrow_price/pace"))
        rows.append(row(label, metrics))
    cap = caption("Sensitivity to missing prices and assigned cost dependencies.",
                  "Each row uses twelve model--log conditions and ten paired repetitions.",
                  "The first two columns show mean cost relative to ReAct, and the last averages condition-wise equal-price/PACE ratios.",
                  CI_TEXT, LEGEND, *([SMALL_TEXT] if any("{<}" in r for r in rows) else []),
                  r"The listed $r=C^{\mathrm{fail}}/C$ is nominal because Qwen Calc's retained partial-spend floor can raise its actual ratio.",
                  r"Both policies use the same $\epsilon=0.25$ budget.")
    return table("tab:price-sensitivity", [r"\shortstack{Scenario\\$r$: price ratio}", r"\shortstack{PACE\\($\times$ ReAct)}", r"\shortstack{Equal attempt prices\\($\times$ ReAct)}", r"\shortstack{Equal / PACE\\($\times$ PACE)}"], rows, cap, spacing="2pt")


def raw_measurement_rows(kind):
    result = []
    for raw in MEASUREMENT["latex_triples"][kind]:
        fields = [part.strip() for part in raw.strip().rstrip("\\").split("&")]
        name, values = fields[0], fields[1:]
        if kind == "price":
            if name in ("Calendar", "OsmAnd marker"):
                values[7] = "0.00"
            if name == "OsmAnd marker":
                values[8] = "0.20"
        result.append((name, [values[i:i + 3] for i in range(0, len(values), 3)]))
    return result


def preserve_raw(text, where):
    RECORDS.append({"where": where, "kind": "retained_measurement_display", "display": text,
                    "source": "paper/measurement_update_20260918.json/latex_triples"})
    return text


def in_thousands(text):
    if text == "--":
        return text
    value = float(text[:-1]) if text.endswith("k") else float(text) / 1000
    digits = max(0, 2 - math.floor(math.log10(abs(value)))) if value else 0
    return f"{value:.{digits}f}"


def render_share():
    rows = []
    widths = ["2.7em", "2.7em", "2.1em", "2.1em"]
    for name, columns in raw_measurement_rows("share"):
        cells = [triple([in_thousands(preserve_raw(v, f"tab:share/{name}/{metric}/{model}")) if metric < 2
                         else preserve_raw(v, f"tab:share/{name}/{metric}/{model}")
                         for model, v in zip(MODELS, values)], width)
                 for metric, (values, width) in enumerate(zip(columns, widths))]
        rows.append(row(name, cells))
        if name in ("OsmAnd marker", "Writer memo"):
            rows.append(r"\addlinespace[2pt]")
    cap = caption("Serving costs and estimated savings for the initial programs.",
                  r"Both cost columns use thousands of the weighted tokens defined in Section~\ref{sec:formulation}.",
                  r"$c$ averages baseline-subtracted agent exploration runs, while $c^{\mathrm{prog}}$ and $q$ summarize thirty program uses.",
                  "The saving share charges one mean-cost agent fallback for a failed use.", LEGEND,
                  r"Appendix~\ref{app:measurement-uncertainty} reports uncertainty for the deployment estimates.",
                  "A dash denotes an unavailable program-path value, and the Qwen web request was refused by the provider.")
    return table("tab:share", ["Family", r"\shortstack{$c$\\(K tokens)}", r"\shortstack{$c^{\mathrm{prog}}$\\(K tokens)}", r"\shortstack{$q$\\(fraction)}", r"\shortstack{Saving share\\(fraction)}"], rows, cap, spacing="2.5pt")


def render_paired():
    rows = []
    raw_map = dict(raw_measurement_rows("paired"))
    for family in FAMILIES[:4]:
        name = NAMES[family]
        columns = []
        for field in ("agent_cost", "agent_success", "program_success", "saving_share"):
            values = []
            for model in MODELS:
                record = MAPS["paired_replays"].get((model, family))
                where = f"tab:paired-replays/{family}/{model}/{field}"
                if record is None:
                    values.append(missing())
                elif field == "agent_cost":
                    values.append(point(record[field]["estimate"] / 1000, where, digits=1))
                elif field in ("agent_success", "program_success"):
                    values.append(point(record[field]["estimate"], where, text=record[field]["count_display"]))
                else:
                    values.append(point(record[field]["estimate"], where))
            columns.append(triple(values, "3.4em" if field == "agent_cost" else "2.9em"))
        rows.append(row(name, columns))
        # The omitted CV and retained shares are still verified against the
        # original table, rather than reconstructed from rounded values.
        for model_index, model in enumerate(MODELS):
            record = MAPS["paired_replays"].get((model, family))
            if record:
                assert f"{record['saving_share']['estimate']:.3f}" == raw_map[name][4][model_index]
    cap = caption("Agent replay costs and success counts on thirty matched deployment bindings per populated entry.",
                  r"$c$ is the mean baseline-subtracted cost in K weighted tokens, with K=$10^3$.",
                  r"Saving share uses the mean-cost fallback estimate $qc$.", LEGEND,
                  r"Appendix~\ref{app:measurement-uncertainty} reports the uncertainty estimates.")
    return table("tab:paired-replays", ["Family", r"\shortstack{Mean $c$\\(K tokens)}", r"\shortstack{Agent successes\\(of 30)}", r"\shortstack{Program successes\\(of 30)}", r"\shortstack{Saving share\\(fraction)}"], rows, cap, spacing="1.5pt")


def render_price():
    rows = []
    widths = ["3.2em", "2.4em", "2.4em", "2.8em", "2.8em"]
    for family in FAMILIES:
        name = NAMES[family]
        _, columns = next((name_, cols) for name_, cols in raw_measurement_rows("price") if name_ == name)
        cells = []
        for column, (values, width) in enumerate(zip(columns, widths)):
            rendered = []
            for model, value in zip(MODELS, values):
                record = MAPS["compilation"].get((model, family))
                if column in (1, 2) and record is not None:
                    gate = record["initial_pass" if column == 1 else "final_pass"]
                    value = gate["count_display"] if gate is not None else "--"
                source = preserve_raw(value, f"tab:price/{family}/{model}/{column}")
                rendered.append(in_thousands(source) if column == 0 else source)
            cells.append(triple(rendered, width))
        rows.append(row(name, cells))
        if name in ("OsmAnd marker", "Writer memo"):
            rows.append(r"\addlinespace[2pt]")
    cap = caption("Initial compilation costs, validation counts, and conditional payback.",
                  
                  
                  r"The two payback columns report $C/s$ and $(C+kc)/s$, with $k=3$ agent runs here.",
                  "Payback excludes earlier failed attempts and future replacement.", LEGEND,
                  "Qwen Calc gives partial spend; dashes mark unavailable values.")
    return table("tab:price", ["Family", r"\shortstack{Attempt cost\\(K tokens)}", r"\shortstack{Initial pass\\(of 5)}", r"\shortstack{Final pass\\(of 5)}",
                               r"\shortstack{Compilation-only\\payback (uses)}", r"\shortstack{Trace-inclusive\\payback (uses)}"],
                 rows, cap, spacing="1.8pt")


def render_repeat():
    rows = []
    for name, columns in raw_measurement_rows("verification"):
        rows.append(row(name, [triple([preserve_raw(v, f"tab:verification-repeat/{name}/{column}/{model}")
                                      for model, v in zip(MODELS, values)], width)
                               for column, (values, width) in enumerate(zip(columns, ["3.2em", "2.2em", "2.2em"]))]))
    cap = caption("Repeated compilation and checks of the original programs.",
                  "Verified counts successful compilations out of three, including the initial attempt.",
                  r"Each $\dagger$ marks an attempt lost to repeated empty provider replies.",
                  "Supplied and extracted columns rerun the original artifact on five new bindings with known or model-extracted parameters.",
                  LEGEND, "The two rejected DeepSeek artifacts were also checked, while Qwen OsmAnd has no repeated series.",
                  "The three compilation attempts share traces and translation, so the verified count is not assigned an independent-trial confidence interval.")
    return table("tab:verification-repeat", ["Family", "Verified (of 3)", "Supplied (of 5)", "Extracted (of 5)"], rows, cap, spacing="2.5pt")


def measurement_detail_rows(fields, families, table_label, source_map, widths=None):
    rows = []
    for family in families:
        columns = []
        for index, (field, formatter) in enumerate(fields):
            values = []
            for model in MODELS:
                record = source_map.get((model, family))
                value = record.get(field) if record else None
                where = f"{table_label}/{family}/{model}/{field}"
                values.append(formatter(value, where) if value is not None else missing(tall=True))
            columns.append(triple(values, widths[index] if widths else "3.4em"))
        rows.append(row(NAMES[family], columns))
    return rows


def render_measurement_uncertainty():
    deploy_rows = measurement_detail_rows(
        [("program_cost", lambda r, w: ci(r, w, digits=1)), ("program_failure", count_ci)], FAMILIES,
        "tab:deployment-uncertainty", MAPS["serving"])
    deploy_cap = caption("Uncertainty in the thirty-use deployment estimates for each verified initial program.",
                         r"$c^{\mathrm{prog}}$ uses weighted tokens and gives the mean followed by positive and negative offsets of its 95 percent binding-bootstrap interval.",
                         "Failures shows an observed count followed by the lower and upper endpoints of a 95 percent Clopper--Pearson interval under an independent Bernoulli working model.",
                         LEGEND, "These intervals condition on the recorded program and task-binding protocol.",
                         "The adapted exploration episodes and single acquisition bills do not support an independent-trial cost interval.")
    deploy = table("tab:deployment-uncertainty", ["Family", r"\shortstack{$c^{\mathrm{prog}}$\\(weighted tokens)}", r"\shortstack{Failures (of 30)\\95\% CI (fraction)}"],
                   deploy_rows, deploy_cap, spacing="4pt", placement="H")
    cost_rows = measurement_detail_rows(
        [("agent_cost", lambda r, w: ci(r, w, digits=1, scale=1000)),
         ("agent_cv", lambda r, w: point(r["estimate"], w, tall=True))],
        FAMILIES[:4], "tab:paired-cost-uncertainty", MAPS["paired_replays"], widths=["3.8em", "2.8em"])
    cost_cap = caption("Agent-cost uncertainty for the matched replays in Table~\\ref{tab:paired-replays}.",
                       "Each populated entry averages thirty recorded bindings.",
                       "The offsets below the mean give its 95 percent binding-bootstrap interval in K weighted tokens.",
                       "CV divides the sample standard deviation by the mean.", LEGEND)
    cost = table("tab:paired-cost-uncertainty", ["Family", r"\shortstack{Mean $c$\\(K tokens)}", r"\shortstack{Agent CV\\(ratio)}"],
                 cost_rows, cost_cap, spacing="4pt", placement="H")
    paired_rows = measurement_detail_rows(
        [("agent_success", count_ci),
         ("program_success", count_ci), ("saving_share", ci)], FAMILIES[:4], "tab:paired-uncertainty",
        MAPS["paired_replays"], widths=["3.4em", "3.4em", "3.4em"])
    paired_cap = caption("Success and saving-share uncertainty on the matched replay bindings.",
                         "Successes gives observed counts and 95 percent Clopper--Pearson endpoints under an independent Bernoulli working model.",
                         "Saving share gives the mean with positive and negative 95 percent bootstrap offsets, resampling matched agent costs, program charges, and failure indicators together.",
                         LEGEND, "The saving bootstrap cannot reveal a failure mode absent from the sample.",
                         *([SMALL_TEXT] if any("{<}" in r for r in paired_rows) else []),
                         "For zero failures in thirty uses, the binomial failure interval is $[0,0.116]$ even when the empirical saving interval is narrow.")
    paired = table("tab:paired-uncertainty", ["Family", r"\shortstack{Agent successes (of 30)\\95\% CI (fraction)}", r"\shortstack{Program successes (of 30)\\95\% CI (fraction)}", r"\shortstack{Saving share\\(fraction)}"],
                   paired_rows, paired_cap, spacing="1.2pt", placement="H")
    repeat_rows = measurement_detail_rows([("supplied", count_ci), ("extracted", count_ci)], FAMILIES[:4],
                                         "tab:recheck-uncertainty", MAPS["repeat_verification"])
    repeat_cap = caption("New-binding checks of the original artifacts, with 95 percent Clopper--Pearson intervals.",
                         "Each cell shows the observed count and then the lower and upper endpoints under an independent Bernoulli working model.",
                         LEGEND, "Five checks provide limited evidence about future binding reliability.",
                         "The candidate-selection and repair gates use their bindings adaptively and are reported as exact counts without test-set intervals.")
    repeat = table("tab:recheck-uncertainty", ["Family", r"\shortstack{Supplied (of 5)\\95\% CI (fraction)}", r"\shortstack{Extracted (of 5)\\95\% CI (fraction)}"], repeat_rows, repeat_cap, spacing="4pt", placement="H")
    return "\n\n".join([deploy, cost, paired, repeat])


def independent_point_checks():
    # Re-read the published summaries, independent of table-data.json's
    # aggregation, to ensure the shorter main selection does not change results.
    paths = [ROOT / "analysis/safe_projected_paper_20260922/evaluation-evidence.json",
             ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/evidence.json",
             ROOT / "experimental-results/guiexp/t2_sim_v3/pace_review_controls_20260923/evidence.json"]
    loaded = [json.loads(path.read_text()) for path in paths]
    checked = 0
    for group in ("original_grid", "fresh_grid", "real_arrivals"):
        joined = {c["key"]: dict(c, policies=c["policies"].copy()) for c in loaded[0]["cells"][group]}
        for extra in loaded[1:]:
            for entry in extra["cells"][group]:
                joined[entry["key"]]["policies"].update(entry["policies"])
        for model in MODELS + ["all_models"]:
            for policy in {p for p, _ in FULL_POLICIES + ABLATIONS}:
                values = [c["policies"][policy]["ratio_agent"] for c in joined.values()
                          if model == "all_models" or c["model"] == model]
                close(statistics.mean(values), summary(group, model, policy)["mean_ratio_agent"]["estimate"])
                close(max(values), summary(group, model, policy)["max_condition_mean_ratio_agent"]["estimate"])
                relative = [c["policies"][policy]["ratio_agent"] / c["policies"]["safe_projected_025"]["ratio_agent"]
                            for c in joined.values() if model == "all_models" or c["model"] == model]
                close(statistics.mean(relative), summary(group, model, policy)["mean_ratio_pace"]["estimate"])
                checked += 3
    return checked, {str(path.relative_to(ROOT)): sha(path) for path in paths}


def main():
    assert sha(TABLE_DATA) == PROVENANCE["outputs_sha256"]["table-data.json"], "Uncertainty data hash changed."
    checks, source_hashes = independent_point_checks()
    assert ranking({"a": 0.12311, "b": 0.12312, "c": 0.2}, "test/tied-best") == {"a": 1, "b": 1}
    assert ranking({"a": 0.1, "b": 0.2, "c": 0.2, "d": 0.3}, "test/tied-second") == {"a": 1, "b": 2, "c": 2}
    assert ranking({"a": 0.1, "b": 0.2, "c": 0.3}, "test/unique") == {"a": 1, "b": 2}
    RANKS.clear()
    OUTPUTS["table_macros.tex"] = r"""% Generated table helpers. Every displayed line inherits the table font.
\providecommand{\tablefont}{\scriptsize}
\newcommand{\estci}[3]{\begin{tabular}[c]{@{}r@{}}\strut #1\\[-0.2ex]\ensuremath{#2}\\[-0.2ex]\ensuremath{#3}\end{tabular}}
\newcommand{\statpoint}[1]{\strut #1}
\newcommand{\countci}[3]{\begin{tabular}[c]{@{}r@{}}\strut #1\\[-0.2ex]\ensuremath{[#2,}\\[-0.2ex]\ensuremath{#3]}\end{tabular}}
"""
    OUTPUTS["online_results_tables.tex"] = render_comparisons(MAIN_POLICIES)
    OUTPUTS["online_results_full.tex"] = render_comparisons(FULL_POLICIES, "-full")
    OUTPUTS["online_ablation_table.tex"] = render_ablation()
    OUTPUTS["online_ablation_effects_full.tex"] = render_ablation(with_intervals=True)
    OUTPUTS["online_ablation_absolute.tex"] = render_ablation_absolute()
    OUTPUTS["online_sensitivity_table.tex"] = render_sensitivity()
    OUTPUTS["measurement_share_table.tex"] = render_share()
    OUTPUTS["measurement_paired_table.tex"] = render_paired()
    OUTPUTS["measurement_price_table.tex"] = render_price()
    OUTPUTS["measurement_repeat_table.tex"] = render_repeat()
    OUTPUTS["measurement_uncertainty_tables.tex"] = render_measurement_uncertainty()
    for name, content in OUTPUTS.items():
        text = "% Generated by rewrite-review/render_pace_tables.py from frozen evidence.\n" + content.rstrip() + "\n"
        assert not re.search(r"\\(?:tiny|footnotesize|small)\b", text), name
        assert "\\resizebox" not in text, name
        if name != "table_macros.tex":
            assert text.count(r"\begin{table}") == text.count(r"\tablefont"), name
        (PAPER / name).write_text(text)
    manifest = {
        "schema": "pace-table-render-verification-v1", "main_policies": MAIN_POLICIES,
        "full_policies": FULL_POLICIES, "ablation_policies": ABLATIONS,
        "uniform_font": "All table bodies/headers use tablefont, defined as scriptsize.",
        "interval_display": "Point, then upper and lower offsets on separate same-font lines. Positive offsets below displayed precision use a strict less-than marker.",
        "maximum_display": "Descriptive tested maximum, no CI.",
        "main_comparison_ranking": RANKS,
        "independent_aggregate_point_checks": checks,
        "uncertainty_source_verification": PROVENANCE["checks"],
        "renderer_sha256": sha(Path(__file__)), "source_sha256": source_hashes | {
            str(TABLE_DATA.relative_to(ROOT)): sha(TABLE_DATA), str(MEASUREMENT_DATA.relative_to(ROOT)): sha(MEASUREMENT_DATA)},
        "output_sha256": {str((PAPER / name).relative_to(ROOT)): sha(PAPER / name) for name in OUTPUTS},
        "rendered_cell_records": RECORDS,
    }
    (ANALYSIS / "table-render-verification.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Rendered {len(OUTPUTS) - 1} table files and table_macros.tex; independently checked {checks} aggregate estimates.")


if __name__ == "__main__":
    main()
