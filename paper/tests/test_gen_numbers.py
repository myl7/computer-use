#!/usr/bin/env python3
"""Tests for gen_numbers.py.  Run with:

    python3 -m unittest discover -s paper/tests -v
    python3 paper/tests/test_gen_numbers.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_numbers as gn  # noqa: E402


SAMPLE = {
    "floor_raw_tokens": {"a/model-1.5": 5090},
    "cells": [
        {"model": "a/model-1.5", "model_slug": "a_model-1.5", "family": "Contacts",
         "headline": {"c": 65044.7, "q": 0.0333, "nstar_marginal": 2.3045,
                      "admitted": True, "d": 430.26, "p": 1.0},
         "prices": {"r_c": 0.0318}},
        {"model": "a/model-1.5", "model_slug": "a_model-1.5", "family": "Markor",
         "headline": {"c": 10762.4, "q": None, "nstar_marginal": "inf",
                      "admitted": False, "d": None, "p": 0.0},
         "prices": {"r_c": 0.0318}},
        {"model": "b/other", "model_slug": "b_other", "family": "Contacts",
         "headline": {"c": 98724.5, "q": 0.0, "nstar_marginal": -2.5,
                      "admitted": None, "d": 179.0, "p": 0.0},
         "prices": {"r_c": 0.2}},
    ],
    "per_model": {"a_model-1.5": {"cells": 2, "quantities": {"headline.c": {"median": 37903.5}}}},
    "routing": {"epsilon|a/model-1.5": {"100": {"epsilon": 0.0204}}},
    "nested": {"list": [{"v": 1}, {"v": 2}]},
    "blocks": {"a_cool/x": {"cand": {"rel": 2.0}}, "a_cool/y": {"cand": {"rel": 4.0}},
               "b_other/x": {"cand": {"rel": 9.0}}},
    "e11": {"cells": {
        "k1": {"_config": {"p": 0.0, "h": 0.0},
               "always_reactive": {"mean_tokens": 100.0},
               "always_compile_evict": {"mean_tokens": 200.0},
               "ours": {"mean_tokens": 150.0},
               "offline_opt_tax": {"mean_tokens": 80.0}},
        "k2": {"_config": {"p": 0.3, "h": 0.5},
               "always_reactive": {"mean_tokens": 400.0},
               "always_compile_evict": {"mean_tokens": 100.0},
               "ours": {"mean_tokens": 100.0},
               "offline_opt_tax": {"mean_tokens": 99.0}},
        "_lower_bound_violations": []}},
    "sim": {"meta": {"n_cells": 16},
            "cells": {"a": {"tag": "x", "always_reactive": {"rel": 2.0}, "ours": {"rel": 1.0}},
                      "b": {"tag": "y", "always_reactive": {"rel": 3.5}, "ours": {"rel": 1.2}},
                      "c": {"tag": "y", "ours": {"rel": 0.9}}}},
}


class PathTests(unittest.TestCase):
    def test_dotted(self):
        self.assertEqual(gn.resolve_path(SAMPLE, "cells[0].headline.c"), 65044.7)

    def test_quoted_key_with_dots_and_slashes(self):
        self.assertEqual(
            gn.resolve_path(SAMPLE, "per_model['a_model-1.5'].quantities['headline.c'].median"),
            37903.5)
        self.assertEqual(
            gn.resolve_path(SAMPLE, "routing['epsilon|a/model-1.5']['100'].epsilon"), 0.0204)

    def test_negative_index(self):
        self.assertEqual(gn.resolve_path(SAMPLE, "nested.list[-1].v"), 2)

    def test_missing_key_raises(self):
        with self.assertRaises(gn.PathError):
            gn.resolve_path(SAMPLE, "cells[0].headline.nope")

    def test_bad_syntax_raises(self):
        with self.assertRaises(gn.PathError):
            gn.resolve_path(SAMPLE, "cells[0].headline.c[")


class WildcardTests(unittest.TestCase):
    def test_star_collects_and_skips_misses(self):
        self.assertEqual(
            sorted(gn.resolve_path(SAMPLE, "sim.cells[*].always_reactive.rel")), [2.0, 3.5])
        self.assertEqual(sorted(gn.resolve_path(SAMPLE, "sim.cells[*].ours.rel")), [0.9, 1.0, 1.2])

    def test_star_over_a_list(self):
        self.assertEqual(gn.resolve_path(SAMPLE, "nested.list[*].v"), [1, 2])

    def test_star_with_a_key_prefix(self):
        vals = gn.resolve_path(SAMPLE, "blocks[*key^=a_cool/].cand.rel")
        self.assertEqual(sorted(vals), [2.0, 4.0])
        self.assertEqual(gn.aggregate(vals, "mean"), 3.0)

    def test_star_with_a_field_condition(self):
        self.assertEqual(gn.resolve_path(SAMPLE, "sim.cells[*tag=x].ours.rel"), [1.0])
        self.assertEqual(sorted(gn.resolve_path(SAMPLE, "sim.cells[*tag=y].ours.rel")), [0.9, 1.2])

    def test_star_with_no_hit_raises(self):
        with self.assertRaises(gn.PathError):
            gn.resolve_path(SAMPLE, "sim.cells[*].nothing.here")

    def test_aggregate(self):
        vals = gn.resolve_path(SAMPLE, "sim.cells[*].ours.rel")
        self.assertEqual(gn.aggregate(vals, "max"), 1.2)
        self.assertEqual(gn.aggregate(vals, "min"), 0.9)
        self.assertEqual(gn.aggregate(vals, "median"), 1.0)
        self.assertEqual(gn.aggregate(vals, "count"), 3)
        with self.assertRaises(gn.PathError):
            gn.aggregate(vals, "nope")


class FilterTests(unittest.TestCase):
    def test_family_and_model(self):
        v = gn.resolve_path(SAMPLE, "cells[family=Markor,model=a_model-1.5].headline.c")
        self.assertEqual(v, 10762.4)

    def test_model_matches_slug_or_full_name(self):
        self.assertEqual(
            gn.resolve_path(SAMPLE, "cells[family=Contacts,model=a/model-1.5].headline.c"), 65044.7)
        self.assertEqual(
            gn.resolve_path(SAMPLE, "cells[family=Contacts,model=b_other].headline.c"), 98724.5)

    def test_partial_filter_takes_first_match(self):
        self.assertEqual(gn.resolve_path(SAMPLE, "cells[model=a_model-1.5].prices.r_c"), 0.0318)

    def test_no_match_raises(self):
        with self.assertRaises(gn.PathError):
            gn.resolve_path(SAMPLE, "cells[family=Nothing,model=b_other].headline.c")


class FormatTests(unittest.TestCase):
    def test_k1_and_k0(self):
        self.assertEqual(gn.format_value(65044.7, "k1"), "65.0k")
        self.assertEqual(gn.format_value(1908700.0, "k0"), "1909k")

    def test_fixed_and_percent(self):
        self.assertEqual(gn.format_value(0.0333, "f2"), "0.03")
        self.assertEqual(gn.format_value(90.3490, "f1"), "90.3")
        self.assertEqual(gn.format_value(0.0333, "pct0"), "3\\%")
        self.assertEqual(gn.format_value(0.9322, "pct0"), "93\\%")

    def test_int_ratio_usd(self):
        self.assertEqual(gn.format_value(430.26, "int"), "430")
        self.assertEqual(gn.format_value(2.3045, "ratio2"), "2.30")
        self.assertEqual(gn.format_value(3.80623, "usd2"), "\\$3.81")

    def test_infinity_in_every_numeric_format(self):
        self.assertEqual(gn.format_value(float("inf"), "f2"), "$\\infty$")
        self.assertEqual(gn.format_value("inf", "f2"), "$\\infty$")
        self.assertEqual(gn.format_value("Infinity", "k1"), "$\\infty$")
        self.assertEqual(gn.format_value(float("-inf"), "ratio2"), "$-\\infty$")
        self.assertEqual(gn.format_value("inf", "inf"), "$\\infty$")

    def test_bool_and_pending(self):
        self.assertEqual(gn.format_value(True, "bool"), "yes")
        self.assertEqual(gn.format_value(False, "bool"), "no")
        self.assertEqual(gn.format_value(None, "bool"), "pending")

    def test_missing_and_undefined(self):
        self.assertEqual(gn.format_value(gn.Missing("gone"), "k1"), "\\textbf{??}")
        self.assertEqual(gn.format_value(gn.Undefined("null"), "f2"), "--")
        self.assertEqual(gn.format_value(None, "f2"), "\\textbf{??}")

    def test_unknown_format_and_nan(self):
        with self.assertRaises(ValueError):
            gn.format_value(1.0, "nope")
        with self.assertRaises(ValueError):
            gn.format_value(float("nan"), "f2")
        with self.assertRaises(ValueError):
            gn.format_value("not a number", "f2")


class ExprTests(unittest.TestCase):
    def test_arithmetic_and_names(self):
        self.assertEqual(gn.eval_expr("a / b", {"a": 10.0, "b": 4.0}), 2.5)

    def test_aggregates(self):
        vals = {"a": 1.0, "b": 5.0, "c": 3.0}
        self.assertEqual(gn.eval_expr("median(a, b, c)", vals), 3.0)
        self.assertEqual(gn.eval_expr("min(a, b, c)", vals), 1.0)
        self.assertEqual(gn.eval_expr("max(a, b, c)", vals), 5.0)
        self.assertEqual(gn.eval_expr("sum(a, b, c)", vals), 9.0)
        self.assertEqual(gn.eval_expr("mean(a, b, c)", vals), 3.0)

    def test_count_of_comparisons(self):
        vals = {"a": 0.0, "b": -1.0, "c": 2.0}
        self.assertEqual(gn.eval_expr("count(a == 0, b == 0, c == 0)", vals), 1)
        self.assertEqual(gn.eval_expr("count(a < 0, b < 0, c < 0)", vals), 1)

    def test_infinity_string_in_aggregate(self):
        self.assertEqual(gn.eval_expr("max(a, b)", {"a": "inf", "b": 2.0}), float("inf"))
        self.assertFalse(gn.eval_expr("isfinite(a)", {"a": "inf"}))

    def test_rejects_unsafe_source(self):
        for bad in ("__import__('os').system('ls')", "open('/etc/passwd')", "a.b",
                    "[1, 2][0]", "'text'", "lambda: 1"):
            with self.assertRaises((gn.ExprError, SyntaxError)):
                gn.eval_expr(bad, {"a": 1.0})

    def test_unknown_name_rejected(self):
        with self.assertRaises(gn.ExprError):
            gn.eval_expr("nothing + 1", {})

    def test_names_exclude_functions(self):
        self.assertEqual(gn.expr_names("median(nA, nB)"), {"nA", "nB"})


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.data = SAMPLE["e11"]

    def test_cell_count_skips_entries_without_config(self):
        self.assertEqual(gn.e11_cell_count(self.data), 2)
        self.assertEqual(gn.e11_cell_count(self.data, subset="p_zero"), 1)
        self.assertEqual(gn.e11_cell_count(self.data, subset="drift"), 1)

    def test_ratio_against_the_better_naive_rule(self):
        # k1 best fixed is 100, k2 best fixed is 100.
        self.assertEqual(gn.e11_ratio(self.data, "ours", "max"), 1.5)
        self.assertEqual(gn.e11_ratio(self.data, "ours", "median"), 1.25)
        self.assertEqual(gn.e11_ratio(self.data, "ours", "max", subset="p_zero"), 1.5)
        self.assertEqual(gn.e11_ratio(self.data, "ours", "max", subset="drift"), 1.0)

    def test_beats_share(self):
        self.assertEqual(gn.e11_beats_share(self.data, "offline_opt_tax", 0.05), 0.5)
        self.assertEqual(gn.e11_beats_share(self.data, "offline_opt_tax", 0.005), 1.0)

    def test_bad_subset_and_row(self):
        with self.assertRaises(ValueError):
            gn.e11_ratio(self.data, "ours", "max", subset="nope")
        with self.assertRaises(ValueError):
            gn.e11_ratio(self.data, "no_such_row", "max")


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "res"))
        with open(os.path.join(self.root, "res", "sample.json"), "w") as fh:
            json.dump(SAMPLE, fh)

    def tearDown(self):
        shutil.rmtree(self.root)

    def gen(self, manifest):
        return gn.Generator(manifest, repo_root=self.root)

    def test_end_to_end_render(self):
        manifest = [
            {"macro": "nAc", "source": "res/sample.json",
             "path": "cells[family=Contacts,model=a_model-1.5].headline.c",
             "fmt": "k1", "note": "reactive cost"},
            {"macro": "nBc", "source": "res/sample.json",
             "path": "cells[family=Contacts,model=b_other].headline.c", "fmt": "k1"},
            {"macro": "nAq", "source": "res/sample.json",
             "path": "cells[family=Markor,model=a_model-1.5].headline.q",
             "fmt": "f2", "optional": True},
            {"macro": "nAInf", "source": "res/sample.json",
             "path": "cells[family=Markor,model=a_model-1.5].headline.nstar_marginal",
             "fmt": "f2"},
            {"macro": "nAAdm", "source": "res/sample.json",
             "path": "cells[family=Contacts,model=b_other].headline.admitted", "fmt": "bool"},
            {"macro": "nRatio", "expr": "nBc / nAc", "fmt": "f1", "note": "derived"},
            {"macro": "nZeros", "expr": "count(nAc == 0, nBc == 0)", "fmt": "int"},
            {"macro": "nTau", "value": 368, "fmt": "int"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertEqual(problems, [])
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nAc}{65.0k}  % reactive cost", joined)
        self.assertIn("\\providecommand{\\nAq}{--}", joined)
        self.assertIn("\\providecommand{\\nAInf}{$\\infty$}", joined)
        self.assertIn("\\providecommand{\\nAAdm}{pending}", joined)
        self.assertIn("\\providecommand{\\nRatio}{1.5}", joined)
        self.assertIn("\\providecommand{\\nZeros}{0}", joined)
        self.assertIn("\\providecommand{\\nTau}{368}", joined)

    def test_agg_entry(self):
        manifest = [
            {"macro": "nWorst", "source": "res/sample.json",
             "path": "sim.cells[*].always_reactive.rel", "agg": "max", "fmt": "f1"},
            {"macro": "nCellCount", "source": "res/sample.json",
             "path": "sim.cells[*].ours.rel", "agg": "count", "fmt": "int"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertEqual(problems, [])
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nWorst}{3.5}", joined)
        self.assertIn("\\providecommand{\\nCellCount}{3}", joined)

    def test_parts_entry_renders_a_range(self):
        manifest = [
            {"macro": "nAc", "source": "res/sample.json", "path": "cells[0].headline.c", "fmt": "k1"},
            {"macro": "nBc", "source": "res/sample.json", "path": "cells[2].headline.c", "fmt": "k1"},
            {"macro": "nRange", "parts": ["min(nAc, nBc)", "max(nAc, nBc)"], "fmt": "k0"},
            {"macro": "nPair", "parts": ["nAc", "nBc"], "join": "/", "fmt": "k0"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertEqual(problems, [])
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nRange}{65k to 99k}", joined)
        self.assertIn("\\providecommand{\\nPair}{65k/99k}", joined)

    def test_default_covers_a_missing_source_file(self):
        manifest = [{"macro": "nLater", "source": "res/not_yet.json", "path": "a.b",
                     "fmt": "usd2", "default": 0.29}]
        g = self.gen(manifest)
        lines, values, problems = g.render()
        self.assertEqual(problems, [])
        self.assertIn("\\providecommand{\\nLater}{\\$0.29}", "\n".join(lines))
        self.assertIn("res/not_yet.json", g.missing_sources)

    def test_expr_on_a_pending_source_is_quiet(self):
        manifest = [
            {"macro": "nLater", "source": "res/not_yet.json", "path": "a.b", "fmt": "f2"},
            {"macro": "nDerived", "expr": "nLater - 1", "fmt": "pct0"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertEqual(problems, [])
        self.assertIn("\\providecommand{\\nDerived}{\\textbf{??}}", "\n".join(lines))

    def test_compute_entry(self):
        manifest = [
            {"macro": "nWorstRow", "source": "res/sample.json", "compute": "e11_ratio",
             "args": {"row": "ours", "stat": "max"}, "fmt": "f2"},
            {"macro": "nBadCompute", "source": "res/sample.json", "compute": "nope", "fmt": "f2"},
        ]
        # the sample file nests the E11 payload, so point the computer at it directly
        import copy
        g = self.gen(manifest)
        g._cache["res/sample.json"] = SAMPLE["e11"]
        g.source_mtimes["res/sample.json"] = 0
        lines, values, problems = g.render()
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nWorstRow}{1.50}", joined)
        self.assertIn("\\providecommand{\\nBadCompute}{\\textbf{??}}", joined)
        self.assertTrue(any(p[1] == "nBadCompute" for p in problems))

    def test_manifest_vars_are_expanded(self):
        path = os.path.join(self.root, "m.json")
        with open(path, "w") as fh:
            json.dump({"vars": {"ROW": "headline"},
                       "entries": [{"macro": "nAc", "source": "res/sample.json",
                                    "path": "cells[0].${ROW}.c", "fmt": "k1"}]}, fh)
        manifest = gn.load_manifest(path)
        self.assertEqual(manifest[0]["path"], "cells[0].headline.c")

    def test_missing_source_file(self):
        manifest = [
            {"macro": "nGone", "source": "res/not_yet.json", "path": "a.b", "fmt": "k1"},
            {"macro": "nDerived", "expr": "nGone * 2", "fmt": "k1"},
            {"macro": "nAc", "source": "res/sample.json", "path": "cells[0].headline.c", "fmt": "k1"},
        ]
        g = self.gen(manifest)
        lines, values, problems = g.render()
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nGone}{\\textbf{??}}", joined)
        self.assertIn("\\providecommand{\\nDerived}{\\textbf{??}}", joined)
        self.assertIn("\\providecommand{\\nAc}{65.0k}", joined)
        self.assertEqual(list(g.missing_sources), ["res/not_yet.json"])
        self.assertEqual(g.missing_sources["res/not_yet.json"], ["nGone"])

    def test_defaults_and_unresolved(self):
        manifest = [
            {"macro": "nDefault", "source": "res/sample.json",
             "path": "cells[0].headline.absent", "fmt": "int", "default": 0},
            {"macro": "nHard", "source": "res/sample.json",
             "path": "cells[0].headline.absent", "fmt": "int"},
        ]
        lines, values, problems = self.gen(manifest).render()
        joined = "\n".join(lines)
        self.assertIn("\\providecommand{\\nDefault}{0}", joined)
        self.assertIn("\\providecommand{\\nHard}{\\textbf{??}}", joined)
        self.assertEqual([p[0] for p in problems], ["unresolved"])

    def test_expr_cycle_is_reported(self):
        manifest = [
            {"macro": "nOne", "expr": "nTwo + 1", "fmt": "int"},
            {"macro": "nTwo", "expr": "nOne + 1", "fmt": "int"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertTrue(any(p[0] == "expr-cycle" for p in problems))
        self.assertIn("\\textbf{??}", "\n".join(lines))

    def test_expr_chain_resolves(self):
        manifest = [
            {"macro": "nAc", "source": "res/sample.json", "path": "cells[0].headline.c", "fmt": "k1"},
            {"macro": "nHalf", "expr": "nAc / 2", "fmt": "k1"},
            {"macro": "nQuarter", "expr": "nHalf / 2", "fmt": "k1"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertEqual(problems, [])
        self.assertIn("\\providecommand{\\nQuarter}{16.3k}", "\n".join(lines))

    def test_write_header_lists_sources(self):
        manifest = [{"macro": "nAc", "source": "res/sample.json",
                     "path": "cells[0].headline.c", "fmt": "k1"}]
        out = os.path.join(self.root, "numbers.tex")
        self.gen(manifest).write(out)
        text = open(out).read()
        self.assertIn("res/sample.json  mtime", text)
        self.assertIn("% Macros: 1", text)
        self.assertIn("\\providecommand{\\nAc}{65.0k}", text)

    def test_duplicate_macro_reported(self):
        manifest = [
            {"macro": "nAc", "source": "res/sample.json", "path": "cells[0].headline.c", "fmt": "k1"},
            {"macro": "nAc", "source": "res/sample.json", "path": "cells[1].headline.c", "fmt": "k1"},
        ]
        lines, values, problems = self.gen(manifest).render()
        self.assertTrue(any(p[0] == "duplicate" for p in problems))
        self.assertEqual(len(lines), 1)


class CheckModeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def write_tex(self, text):
        path = os.path.join(self.root, "body.tex")
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_reports_unknown_and_unused(self):
        tex = self.write_tex(
            "\\newcommand{\\x}{1}\n"
            "\\noindent The cost was \\nGLMContactsc tokens, break-even \\nGLMContactsNstarMarg.\n"
            "A macro nobody declared: \\nGhostMacro.\n")
        manifest = [{"macro": "nGLMContactsc"}, {"macro": "nGLMContactsNstarMarg"},
                    {"macro": "nUnusedOne"}, {"macro": "nUnusedTwo"}]
        unknown, unused = gn.check(manifest, tex)
        self.assertEqual(unknown, ["nGhostMacro"])
        self.assertEqual(unused, ["nUnusedOne", "nUnusedTwo"])

    def test_latex_builtins_are_not_macros(self):
        tex = self.write_tex("\\newcommand\\noindent\\newpage\\nonumber\\newline\\nabla\n")
        unknown, unused = gn.check([], tex)
        self.assertEqual(unknown, [])

    def test_commented_lines_ignored(self):
        tex = self.write_tex("% \\nCommentedOut is not used\n\\nRealUse\n")
        unknown, unused = gn.check([{"macro": "nRealUse"}], tex)
        self.assertEqual(unknown, [])
        self.assertEqual(unused, [])

    def test_main_check_exit_code(self):
        tex = self.write_tex("\\nGhostMacro\n")
        mpath = os.path.join(self.root, "m.json")
        with open(mpath, "w") as fh:
            json.dump([{"macro": "nSomething"}], fh)
        self.assertEqual(gn.main(["--manifest", mpath, "--check", tex, "--quiet"]), 1)
        with open(mpath, "w") as fh:
            json.dump([{"macro": "nGhostMacro"}], fh)
        self.assertEqual(gn.main(["--manifest", mpath, "--check", tex, "--quiet"]), 0)


class RealManifestTests(unittest.TestCase):
    """The shipped manifest must be well formed even when sources move."""

    def setUp(self):
        self.manifest = gn.load_manifest(gn.DEFAULT_MANIFEST)

    def test_macro_names_are_letters_only_and_unique(self):
        seen = set()
        for entry in self.manifest:
            name = entry["macro"]
            self.assertRegex(name, r"^n[A-Z][A-Za-z]*$", name)
            self.assertNotIn(name, seen, name)
            seen.add(name)

    def test_every_entry_has_a_source_expr_or_value(self):
        for entry in self.manifest:
            self.assertTrue(
                ("source" in entry and ("path" in entry or "compute" in entry))
                or "expr" in entry or "parts" in entry or "value" in entry,
                entry["macro"])
            self.assertIn("note", entry, entry["macro"])

    def test_formats_are_known(self):
        for entry in self.manifest:
            gn.format_value(1.0 if entry.get("fmt") != "bool" else True, entry["fmt"])

    def test_exprs_parse_and_reference_declared_macros(self):
        names = {e["macro"] for e in self.manifest}
        for entry in self.manifest:
            for one in ([entry["expr"]] if "expr" in entry else entry.get("parts", [])):
                for dep in gn.expr_names(one):
                    self.assertIn(dep, names, "%s -> %s" % (entry["macro"], dep))

    def test_paths_parse(self):
        for entry in self.manifest:
            if "path" in entry:
                gn.parse_path(entry["path"])

    def test_real_run_has_no_problems(self):
        gen = gn.Generator(self.manifest)
        lines, values, problems = gen.render()
        self.assertEqual(problems, [], problems[:5])
        self.assertEqual(len(lines), len(self.manifest))

    def test_rerun_is_byte_stable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.tex")
            b = os.path.join(tmp, "b.tex")
            gn.Generator(self.manifest, stable=True).write(a)
            gn.Generator(self.manifest, stable=True).write(b)
            self.assertEqual(open(a).read(), open(b).read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
