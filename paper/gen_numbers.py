#!/usr/bin/env python3
"""Generate numbers.tex from the experimental result files.

README
======

Purpose
-------
The paper must never hand-copy a number.  Every quantity that appears in
body.tex comes from a LaTeX macro defined in numbers.tex, and numbers.tex is
generated from numbers_manifest.json, which points at the raw result JSON.
Change a result file, re-run this script, and the paper follows.

    python3 gen_numbers.py                  # write numbers.tex
    python3 gen_numbers.py --check body.tex # audit macro use against the manifest

Macro naming scheme
-------------------
Every macro starts with a lowercase ``n`` followed by an uppercase letter, so
``\\n[A-Z][A-Za-z]*`` identifies a generated number and never collides with a
LaTeX builtin such as ``\\newcommand`` or ``\\noindent``.  Names carry only
letters (no digits, no underscores), so numerals are spelled out.

Model tokens: ``GLM`` (z-ai/glm-5.3-flash), ``DS``
(deepseek/deepseek-v4-flash-vision-exp).
Family tokens: ``Contacts``, ``FilesMove``, ``MarkorCreate``, ``MarkorDelete``,
``OsmFav``, ``OsmMarker``, ``Calendar``.
Probe arm tokens: ``Clean``, ``FontLarge``, ``FontSmall``, ``DensitySmall``,
``LocaleFr``, ``DarkTheme``, ``Notification``, ``LowBattery``,
``PermissionDialog``, ``UpdatePrompt``.

  constants table, one cell   \\n{Model}{Family}{Quantity}
                              \\nGLMContactsc, \\nDSMarkorDeleteq
      Quantity in: c, Ldoc, d, q, ShareDoc, ShareProg, C, CNoRepair,
      pOne pTwo pThree (initial gate pass rate at k=1,2,3), pAfter, pHead, Adm,
      NstarMarg, NstarBuild, pi, Usd, Ref, DeployN, DeploySucc, DeployProgErr.
  per model aggregate         \\n{Model}{Quantity}{Stat}
                              \\nGLMcMedian, \\nDSNstarMargMax, \\nGLMFloor
  cross model                 \\nCellsTotal, \\nAdmittedTotal, \\nDocNegativeTotal
  fragility probe             \\nProbe{Model}{Arm}{Quantity}
                              \\nProbeGLMFontLargePass, \\nProbeDSNotificationLoud
  probe totals                \\nProbe{Model}{Quantity}  \\nProbeGLMBreakUniform
  router / m-library          \\nRouter{Model}{Quantity}  \\nRouterGLMm
                              \\nRouterGLMEpsNhundred, \\nRouterCalls

Manifest entries
----------------
numbers_manifest.json is a list of objects.  Three kinds:

  source entry   {"macro", "source", "path", "fmt", "note",
                  "optional": bool, "default": value, "agg": "max"}
      ``source`` is a result file relative to the repository root (the parent
      of this script's directory).  ``path`` selects inside it, see below.
      ``agg`` reduces a list that a ``[*]`` path produced: max, min, median,
      mean, sum, count, first, last.  ``default`` covers both an unresolved
      path and a source file that does not exist yet.
  expr entry     {"macro", "expr", "fmt", "note"}
      ``expr`` combines the raw (unformatted) values of other macros.
  parts entry    {"macro", "parts": [expr, expr], "join": " to ", "fmt", "note"}
      Several expressions formatted with the same ``fmt`` and joined, which is
      how ranges such as "3.5 to 4.6" are written.
  compute entry  {"macro", "source", "compute": "e11_ratio", "args": {...}, "fmt"}
      For numbers that are a computation over a whole file rather than one
      field: the named function lives in this script (see COMPUTERS) so the
      arithmetic is written once and reviewed, not retyped into the paper.
  literal entry  {"macro", "value", "fmt", "note"}
      A constant that has no machine-readable source yet.

The manifest may also be an object {"vars": {...}, "entries": [...]}.  Then
``${NAME}`` inside any entry string is replaced by that variable, which is how
a row key that the simulation may rename later (E11's ours row) is written down
once.

numbers_manifest.json itself is written by numbers_manifest_build.py, which
loops over models, families, probe arms and the n grid instead of spelling out
five hundred entries by hand.  A few expr entries name a fixed set of cells
(the admitted ones, say); rerun that script after an admission changes.

Path syntax
-----------
Dotted keys, with bracket forms for keys that contain dots or slashes:

    cells[family=ContactsAddContact,model=z-ai_glm-5.3-flash].headline.c
    per_model['z-ai_glm-5.3-flash'].quantities['headline.c'].median
    routing['epsilon|z-ai/glm-5.3-flash']['100'].epsilon
    m['z-ai/glm-5.3-flash'].tau_fit.m_slope
    cells[0].headline.c
    cells[*].always_reactive.rel_to_best_fixed      (with "agg": "max")

``[k=v,k2=v2]`` filters a list of objects and takes the first item where every
``k`` equals ``v`` as a string.  A key ``k`` also matches the item's ``k_slug``
field, so ``model=z-ai_glm-5.3-flash`` matches ``model_slug``.

Expression language
-------------------
``expr`` is Python source restricted by an AST whitelist: number literals,
names (which resolve to other macros' raw values), ``+ - * / // % **``, unary
``+ -``, parentheses, comparisons, and the calls ``min max median mean sum abs
round count isfinite``.  ``count(a == 0, b == 0, ...)`` counts true arguments.
Nothing else is allowed: no attributes, no subscripts, no strings, no lambdas.
Expr entries may depend on other expr entries; they are resolved by repeated
passes and a cycle is reported as an error.

Formats
-------
    k0 k1 k2     thousands, given decimals, trailing "k"      65044 -> 65.0k
    f0 f1 f2 f3  fixed decimals                               0.0333 -> 0.03
    pct0 pct1    percent, given decimals, escaped sign        0.0333 -> 3\\%
    int          rounded integer                              430.26 -> 430
    ratio2       like f2, for break-even counts               2.3046 -> 2.30
    usd2         escaped dollars                              3.8062 -> \\$3.81
    inf          %g, for values that are usually infinite
    bool         True/False/None -> yes/no/pending
    str          verbatim
Any non-finite number prints ``$\\infty$`` or ``$-\\infty$`` under every numeric
format.  A value that is null prints ``--`` when the entry is ``optional`` (or
its ``default`` when one is given) and ``\\textbf{??}`` otherwise.  An entry
whose source file is missing prints ``\\textbf{??}`` and is listed in the
warning summary, so the paper still compiles while an experiment is running.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
DEFAULT_MANIFEST = os.path.join(HERE, "numbers_manifest.json")
DEFAULT_OUT = os.path.join(HERE, "numbers.tex")

MACRO_RE = re.compile(r"\\(n[A-Z][A-Za-z]*)")
MACRO_NAME_RE = re.compile(r"^n[A-Za-z]+$")

INFINITY = r"$\infty$"
NEG_INFINITY = r"$-\infty$"
UNKNOWN = r"\textbf{??}"
UNDEFINED = "--"


class Missing:
    """A value that could not be resolved.  Prints as \\textbf{??}."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Missing(%r)" % self.reason


class Undefined:
    """A value that is legitimately absent.  Prints as --."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Undefined(%r)" % self.reason


class PathError(Exception):
    pass


class ExprError(Exception):
    pass


# ---------------------------------------------------------------- path parsing

_SEGMENT_RE = re.compile(
    r"""
      \.
    | \[\s*\*(?P<all>[^\]]*)\]
    | \[\s*(?P<idx>-?\d+)\s*\]
    | \[\s*(?P<quote>['"])(?P<key>.*?)(?P=quote)\s*\]
    | \[(?P<filt>[^\]]*=[^\]]*)\]
    | (?P<name>[^.\[\]]+)
    """,
    re.VERBOSE,
)


def parse_path(path: str):
    """Turn a selector string into a list of ("key"|"index"|"filter", value)."""
    segments = []
    pos = 0
    while pos < len(path):
        m = _SEGMENT_RE.match(path, pos)
        if not m or m.end() == pos:
            raise PathError("cannot parse path %r at offset %d" % (path, pos))
        pos = m.end()
        if m.group("all") is not None:
            segments.append(("all", _parse_conditions(m.group("all"), path)))
        elif m.group("idx") is not None:
            segments.append(("index", int(m.group("idx"))))
        elif m.group("key") is not None:
            segments.append(("key", m.group("key")))
        elif m.group("filt") is not None:
            spec = {}
            for part in m.group("filt").split(","):
                part = part.strip()
                if not part:
                    continue
                if "=" not in part:
                    raise PathError("bad filter %r in path %r" % (part, path))
                k, v = part.split("=", 1)
                spec[k.strip()] = v.strip()
            if not spec:
                raise PathError("empty filter in path %r" % path)
            segments.append(("filter", spec))
        elif m.group("name") is not None:
            segments.append(("key", m.group("name")))
    if not segments:
        raise PathError("empty path")
    return segments


def _parse_conditions(text, path):
    """Conditions inside [* ... ]: key^=prefix, key=exact, field=value."""
    conds = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "^=" in part:
            k, v = part.split("^=", 1)
            conds.append((k.strip(), "prefix", v.strip()))
        elif "=" in part:
            k, v = part.split("=", 1)
            conds.append((k.strip(), "eq", v.strip()))
        else:
            raise PathError("bad [* ] condition %r in path %r" % (part, path))
    return conds


def _child_matches(key, child, conds):
    for field, op, want in conds:
        if field == "key":
            got = key
        elif isinstance(child, dict) and field in child:
            got = child[field]
        else:
            return False
        got = str(got)
        if op == "prefix" and not got.startswith(want):
            return False
        if op == "eq" and got != want:
            return False
    return True


def _item_matches(item, spec):
    if not isinstance(item, dict):
        return False
    for k, v in spec.items():
        candidates = []
        if k in item:
            candidates.append(item[k])
        if (k + "_slug") in item:
            candidates.append(item[k + "_slug"])
        if not candidates:
            return False
        if not any(str(c) == v for c in candidates):
            return False
    return True


def resolve_path(data, path: str):
    """Resolve a selector against loaded JSON.  Raises PathError when absent.

    A ``[*]`` segment fans out over every value of a dict or list and returns a
    list of whatever the rest of the path selects, skipping elements where the
    rest of the path does not resolve.
    """
    return _walk(data, parse_path(path), path)


def _walk(cur, segments, path):
    for i, (kind, value) in enumerate(segments):
        if kind == "key":
            if not isinstance(cur, dict) or value not in cur:
                raise PathError("no key %r in %s (path %r)" % (value, _brief(cur), path))
            cur = cur[value]
        elif kind == "index":
            if not isinstance(cur, list):
                raise PathError("not a list at index %r (path %r)" % (value, path))
            try:
                cur = cur[value]
            except IndexError:
                raise PathError("index %d out of range (path %r)" % (value, path))
        elif kind == "filter":
            if not isinstance(cur, list):
                raise PathError("filter needs a list (path %r)" % path)
            hits = [it for it in cur if _item_matches(it, value)]
            if not hits:
                raise PathError("filter %r matched nothing (path %r)" % (value, path))
            cur = hits[0]
        else:  # "all"
            if isinstance(cur, dict):
                pairs = list(cur.items())
            elif isinstance(cur, list):
                pairs = list(enumerate(cur))
            else:
                raise PathError("[*] needs a list or object (path %r)" % path)
            children = [c for k, c in pairs if _child_matches(k, c, value)]
            rest = segments[i + 1:]
            out = []
            for child in children:
                try:
                    out.append(_walk(child, rest, path) if rest else child)
                except PathError:
                    continue
            if not out:
                raise PathError("[*] selected nothing (path %r)" % path)
            return out
    return cur


def _brief(obj):
    if isinstance(obj, dict):
        return "<object with keys %s>" % ", ".join(list(obj)[:6])
    return type(obj).__name__


# ------------------------------------------------------------------ formatting

AGGREGATORS = {
    "max": lambda xs: max(xs),
    "min": lambda xs: min(xs),
    "median": statistics.median,
    "mean": statistics.fmean,
    "sum": sum,
    "count": len,
    "first": lambda xs: xs[0],
    "last": lambda xs: xs[-1],
}


def aggregate(values, how):
    """Reduce the list a [*] path produced."""
    if how not in AGGREGATORS:
        raise PathError("unknown agg %r" % how)
    if not isinstance(values, list):
        values = [values]
    if not values:
        raise PathError("agg %r over an empty selection" % how)
    if how == "count":
        return len(values)
    return AGGREGATORS[how]([coerce_number(v) for v in values])


def coerce_number(value, fmt="a number"):
    """Numbers, plus the strings the result files use for infinity."""
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("inf", "+inf", "infinity", "+infinity"):
            return math.inf
        if text in ("-inf", "-infinity"):
            return -math.inf
        try:
            return float(text)
        except ValueError:
            pass
    raise ValueError("format %r wants a number, got %r" % (fmt, value))


_KFMT = re.compile(r"^k(\d)$")
_FFMT = re.compile(r"^f(\d)$")
_PFMT = re.compile(r"^pct(\d)$")


def format_value(value, fmt: str, join: str = " to ") -> str:
    if isinstance(value, Missing):
        return UNKNOWN
    if isinstance(value, Undefined):
        return UNDEFINED
    if isinstance(value, (list, tuple)):
        return join.join(format_value(v, fmt) for v in value)
    if fmt == "bool":
        if value is None:
            return "pending"
        return "yes" if value else "no"
    if fmt == "str":
        return str(value)
    if value is None:
        return UNKNOWN
    if isinstance(value, bool):
        value = int(value)
    value = coerce_number(value, fmt)
    if math.isnan(value):
        raise ValueError("NaN cannot be formatted")
    if math.isinf(value):
        return INFINITY if value > 0 else NEG_INFINITY

    m = _KFMT.match(fmt)
    if m:
        return "%.*fk" % (int(m.group(1)), value / 1000.0)
    m = _FFMT.match(fmt)
    if m:
        return "%.*f" % (int(m.group(1)), value)
    m = _PFMT.match(fmt)
    if m:
        return "%.*f\\%%" % (int(m.group(1)), value * 100.0)
    if fmt == "int":
        return "%d" % int(round(value))
    if fmt == "ratio2":
        return "%.2f" % value
    if fmt == "usd2":
        return "\\$%.2f" % value
    if fmt == "inf":
        return "%g" % value
    raise ValueError("unknown format %r" % fmt)


# ------------------------------------------------------------ expression layer


def _median(*args):
    return statistics.median(_flatten(args))


def _mean(*args):
    return statistics.fmean(_flatten(args))


def _flatten(args):
    out = []
    for a in args:
        if isinstance(a, (list, tuple)):
            out.extend(_flatten(a))
        else:
            out.append(coerce_number(a))
    if not out:
        raise ExprError("aggregate over an empty set")
    return out


def _count(*args):
    return sum(1 for a in args if a)


EXPR_FUNCS = {
    "min": lambda *a: min(_flatten(a)),
    "max": lambda *a: max(_flatten(a)),
    "median": _median,
    "mean": _mean,
    "sum": lambda *a: sum(_flatten(a)),
    "abs": abs,
    "round": round,
    "count": _count,
    "isfinite": lambda x: math.isfinite(coerce_number(x)),
}

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.BoolOp,
    ast.And,
    ast.Or,
)


def expr_names(expr: str):
    """Names an expression reads, excluding whitelisted function names."""
    tree = ast.parse(expr, mode="eval")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in EXPR_FUNCS:
            names.add(node.id)
    return names


def eval_expr(expr: str, values):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExprError("node %s is not allowed in an expr" % type(node).__name__)
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ExprError("only numeric constants are allowed in an expr")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in EXPR_FUNCS:
                raise ExprError("only %s may be called" % ", ".join(sorted(EXPR_FUNCS)))
            if node.keywords:
                raise ExprError("keyword arguments are not allowed in an expr")

    env = dict(EXPR_FUNCS)
    for name in expr_names(expr):
        if name not in values:
            raise ExprError("expr refers to unknown macro %r" % name)
        env[name] = values[name]
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, env)


# ------------------------------------------------------- computed sources
# A few numbers are a small computation over a whole result file rather than one
# field of it: E11 compares every policy against the better of the two naive
# rules cell by cell.  Those live here as named functions a manifest entry calls
# with {"compute": name, "args": {...}}, so the arithmetic sits in one audited
# place instead of being retyped into the paper.


def _e11_cells(data):
    cells = data.get("cells", {})
    return [c for c in cells.values() if isinstance(c, dict) and isinstance(c.get("_config"), dict)]


def _e11_subset(cells, subset):
    if subset in (None, "", "all"):
        return cells
    if subset == "p_zero":
        return [c for c in cells if c["_config"].get("p") == 0]
    if subset == "drift":
        return [c for c in cells if (c["_config"].get("h") or 0) > 0]
    raise ValueError("unknown E11 subset %r" % subset)


def _e11_best_fixed(cell, compile_row="always_compile_evict", reactive_row="always_reactive"):
    return min(cell[compile_row]["mean_tokens"], cell[reactive_row]["mean_tokens"])


def e11_cell_count(data, subset=None):
    return len(_e11_subset(_e11_cells(data), subset))


def e11_ratio(data, row, stat="max", subset=None):
    """Statistic of row_tokens / min(always_compile_evict, always_reactive)."""
    cells = _e11_subset(_e11_cells(data), subset)
    ratios = []
    for cell in cells:
        if row not in cell:
            raise ValueError("E11 cell has no row %r" % row)
        ratios.append(cell[row]["mean_tokens"] / _e11_best_fixed(cell))
    if not ratios:
        raise ValueError("E11 subset %r selected no cells" % subset)
    return aggregate(ratios, stat)


def e11_beats_share(data, row="offline_opt_tax", margin=0.05, subset=None, as_count=False):
    """Share (or count) of cells where row is cheaper than both naive rules by
    more than margin."""
    cells = _e11_subset(_e11_cells(data), subset)
    if not cells:
        raise ValueError("E11 subset %r selected no cells" % subset)
    hits = sum(1 for c in cells
               if c[row]["mean_tokens"] < (1.0 - margin) * _e11_best_fixed(c))
    return hits if as_count else hits / float(len(cells))


def e11_pair_ratio(data, row, vs, stat="median", subset=None):
    """Statistic of row_tokens / vs_tokens, cell by cell."""
    cells = _e11_subset(_e11_cells(data), subset)
    ratios = [c[row]["mean_tokens"] / c[vs]["mean_tokens"] for c in cells]
    if not ratios:
        raise ValueError("E11 subset %r selected no cells" % subset)
    return aggregate(ratios, stat)


def cell_gate_margin(data, model, family):
    """The gate a cell was finally judged on: the redeploy gate when there was
    one, otherwise the post-repair gate, and None when neither was run."""
    cell = resolve_path(data, "cells[family=%s,model=%s].gate" % (family, model))
    if cell.get("redeploy_present") and cell.get("redeploy_gate_total"):
        return cell["redeploy_gate_passed"] / float(cell["redeploy_gate_total"])
    if cell.get("gate_after_repair_total"):
        return cell["gate_after_repair_passed"] / float(cell["gate_after_repair_total"])
    return None


COMPUTERS = {
    "cell_gate_margin": cell_gate_margin,
    "e11_cell_count": e11_cell_count,
    "e11_ratio": e11_ratio,
    "e11_beats_share": e11_beats_share,
    "e11_pair_ratio": e11_pair_ratio,
}


# ---------------------------------------------------------------- the pipeline


class Generator:
    def __init__(self, manifest, repo_root=REPO_ROOT, stable=False):
        self.manifest = manifest
        self.repo_root = repo_root
        self.stable = stable
        self._cache = {}
        self.source_mtimes = {}
        self.missing_sources = {}

    def load_source(self, rel):
        if rel in self._cache:
            return self._cache[rel]
        full = os.path.join(self.repo_root, rel)
        if not os.path.exists(full):
            self._cache[rel] = None
            self.missing_sources.setdefault(rel, [])
            return None
        with open(full) as fh:
            data = json.load(fh)
        self._cache[rel] = data
        self.source_mtimes[rel] = os.path.getmtime(full)
        return data

    def resolve_all(self):
        """Return (values, entries, problems).  values maps macro -> raw value."""
        values = {}
        problems = []
        by_macro = {}
        deferred = []

        for entry in self.manifest:
            macro = entry.get("macro")
            if not macro or not MACRO_NAME_RE.match(macro):
                problems.append(("bad-macro-name", macro, "not a letters-only n* name"))
                continue
            if macro in by_macro:
                problems.append(("duplicate", macro, "defined more than once"))
                continue
            by_macro[macro] = entry

            if "expr" in entry or "parts" in entry:
                deferred.append(entry)
                continue
            if "value" in entry:
                values[macro] = entry["value"]
                continue

            rel = entry.get("source")
            data = self.load_source(rel) if rel else None
            where = entry.get("path", entry.get("compute", "?"))
            if rel and data is None:
                self.missing_sources[rel].append(macro)
                if "default" in entry:
                    values[macro] = entry["default"]
                else:
                    values[macro] = Missing("source file missing: %s" % rel)
                continue
            try:
                if "compute" in entry:
                    fn = COMPUTERS.get(entry["compute"])
                    if fn is None:
                        raise PathError("unknown compute %r" % entry["compute"])
                    raw = fn(data, **entry.get("args", {}))
                else:
                    raw = resolve_path(data, entry["path"])
            except (PathError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
                if "default" in entry:
                    values[macro] = entry["default"]
                elif entry.get("optional"):
                    values[macro] = Undefined(str(exc))
                else:
                    problems.append(("unresolved", macro, str(exc)))
                    values[macro] = Missing(str(exc))
                continue
            if "agg" in entry and raw is not None:
                try:
                    raw = aggregate(raw, entry["agg"])
                except (PathError, ValueError) as exc:
                    problems.append(("agg", macro, str(exc)))
                    values[macro] = Missing(str(exc))
                    continue
            if raw is None:
                if "default" in entry:
                    values[macro] = entry["default"]
                elif entry.get("fmt") == "bool":
                    values[macro] = None  # prints "pending"
                elif entry.get("optional"):
                    values[macro] = Undefined("null in source")
                else:
                    problems.append(("null", macro, "value is null at %s" % where))
                    values[macro] = Missing("null value")
            else:
                values[macro] = raw

        # Expr entries, repeated passes so an expr may build on another expr.
        pending = list(deferred)
        while pending:
            progressed = False
            still = []
            for entry in pending:
                macro = entry["macro"]
                exprs = entry["parts"] if "parts" in entry else [entry["expr"]]
                try:
                    deps = set()
                    for one in exprs:
                        deps |= expr_names(one)
                except SyntaxError as exc:
                    problems.append(("expr-syntax", macro, str(exc)))
                    values[macro] = Missing("expr syntax error")
                    progressed = True
                    continue
                unknown = [d for d in deps if d not in by_macro]
                if unknown:
                    problems.append(("expr-unknown-dep", macro, ", ".join(sorted(unknown))))
                    values[macro] = Missing("unknown dependency")
                    progressed = True
                    continue
                if any(d not in values for d in deps):
                    still.append(entry)
                    continue
                bad = [d for d in deps if isinstance(values[d], (Missing, Undefined))]
                if bad:
                    detail = ", ".join(sorted(bad))
                    # A dependency on a result file that has not been produced yet is
                    # the designed behaviour, not a problem to report.
                    pending_source = all(
                        isinstance(values[d], Missing)
                        and values[d].reason.startswith("source file missing")
                        for d in bad)
                    if pending_source:
                        values[macro] = Missing("source file missing, via %s" % detail)
                    else:
                        values[macro] = Missing("depends on unresolved %s" % detail)
                        problems.append(("expr-dep-missing", macro, detail))
                    progressed = True
                    continue
                try:
                    out = [eval_expr(one, values) for one in exprs]
                    values[macro] = out if "parts" in entry else out[0]
                except Exception as exc:
                    problems.append(("expr-error", macro, str(exc)))
                    values[macro] = Missing(str(exc))
                progressed = True
            if not progressed:
                for entry in still:
                    problems.append(("expr-cycle", entry["macro"], "circular expr dependency"))
                    values[entry["macro"]] = Missing("circular expr dependency")
                break
            pending = still

        return values, by_macro, problems

    def render(self):
        values, by_macro, problems = self.resolve_all()
        lines = []
        for entry in self.manifest:
            macro = entry.get("macro")
            if macro not in by_macro or by_macro[macro] is not entry:
                continue
            value = values.get(macro, Missing("not resolved"))
            fmt = entry.get("fmt", "str")
            try:
                text = format_value(value, fmt, entry.get("join", " to "))
            except Exception as exc:
                problems.append(("format", macro, str(exc)))
                text = UNKNOWN
            note = entry.get("note", "")
            line = "\\providecommand{\\%s}{%s}" % (macro, text)
            if note:
                line += "  %% %s" % note
            lines.append(line)
        return lines, values, problems

    def write(self, out_path):
        lines, values, problems = self.render()
        header = [
            "% numbers.tex -- generated by gen_numbers.py, do not edit by hand.",
            "% Every number in the paper comes from a macro defined here.",
            "% Regenerate with:  python3 gen_numbers.py",
            "%% Generated: %s" % ("(timestamp suppressed by --stable)" if self.stable
                                 else time.strftime("%Y-%m-%d %H:%M:%S %z")),
            "%% Macros: %d" % len(lines),
            "% Sources:",
        ]
        for rel in sorted(set(list(self.source_mtimes) + list(self.missing_sources))):
            if rel in self.source_mtimes:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.source_mtimes[rel]))
                header.append("%%   %s  mtime %s" % (rel, stamp))
            else:
                header.append("%%   %s  MISSING" % rel)
        for rel, macros in sorted(self.missing_sources.items()):
            if macros:
                header.append("%%   ?? %d macro(s) unresolved from %s" % (len(macros), rel))
        text = "\n".join(header + [""] + lines) + "\n"
        with open(out_path, "w") as fh:
            fh.write(text)
        return lines, values, problems


# ------------------------------------------------------------------ check mode


def scan_tex(path):
    with open(path) as fh:
        text = fh.read()
    text = re.sub(r"(?<!\\)%.*", "", text)  # drop LaTeX comments
    return set(MACRO_RE.findall(text))


def check(manifest, tex_path):
    known = [e["macro"] for e in manifest if e.get("macro")]
    used = scan_tex(tex_path)
    unknown = sorted(used - set(known))
    unused = sorted(set(known) - used)
    return unknown, unused


# ------------------------------------------------------------------------ main


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z_0-9]*)\}")


def _expand(obj, variables):
    if isinstance(obj, str):
        def sub(m):
            if m.group(1) not in variables:
                raise SystemExit("manifest uses undefined var ${%s}" % m.group(1))
            return str(variables[m.group(1)])
        return _VAR_RE.sub(sub, obj)
    if isinstance(obj, list):
        return [_expand(x, variables) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v, variables) for k, v in obj.items()}
    return obj


def load_manifest(path):
    with open(path) as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "entries" in data:
        variables = data.get("vars", {})
        data = data["entries"]
        if variables:
            data = [_expand(e, variables) for e in data]
    if not isinstance(data, list):
        raise SystemExit("manifest must be a list of entries")
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--repo-root", default=REPO_ROOT)
    ap.add_argument("--check", metavar="TEXFILE", help="audit a .tex file against the manifest")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--stable", action="store_true",
                    help="omit the generation timestamp so reruns are byte-identical")
    args = ap.parse_args(argv)

    manifest = load_manifest(args.manifest)

    if args.check:
        unknown, unused = check(manifest, args.check)
        print("check %s against %s" % (args.check, os.path.basename(args.manifest)))
        print("  manifest macros: %d" % len(manifest))
        if unknown:
            print("  USED BUT NOT IN MANIFEST (%d):" % len(unknown))
            for name in unknown:
                print("    \\%s" % name)
        else:
            print("  used but not in manifest: none")
        print("  in manifest but unused in %s: %d" % (os.path.basename(args.check), len(unused)))
        if unused and not args.quiet:
            for name in unused[:20]:
                print("    \\%s" % name)
            if len(unused) > 20:
                print("    ... and %d more" % (len(unused) - 20))
        return 1 if unknown else 0

    gen = Generator(manifest, repo_root=args.repo_root, stable=args.stable)
    lines, values, problems = gen.write(args.out)
    print("wrote %s" % args.out)
    print("  macros: %d" % len(lines))
    missing = {rel: ms for rel, ms in gen.missing_sources.items()}
    unknown_macros = sorted(m for m, v in values.items() if isinstance(v, Missing))
    if missing:
        print("  WARNING missing source files: %d" % len(missing))
        for rel, macros in sorted(missing.items()):
            print("    %s  -> %d macro(s) read it directly" % (rel, len(macros)))
    else:
        print("  missing source files: none")
    if unknown_macros:
        print("  macros printed as ?? : %d (%s)" % (
            len(unknown_macros), ", ".join("\\" + m for m in unknown_macros[:8])
            + (", ..." if len(unknown_macros) > 8 else "")))
    undefined = [m for m, v in values.items() if isinstance(v, Undefined)]
    if undefined:
        print("  undefined by design (printed as --): %d" % len(undefined))
    if problems:
        print("  PROBLEMS: %d" % len(problems))
        for kind, macro, detail in problems[:25]:
            print("    [%s] %s: %s" % (kind, macro, detail))
        if len(problems) > 25:
            print("    ... and %d more" % (len(problems) - 25))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
