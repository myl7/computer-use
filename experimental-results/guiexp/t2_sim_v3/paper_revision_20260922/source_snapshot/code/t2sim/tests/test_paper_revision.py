"""Input-boundary and accounting checks for the frozen paper study."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import revision_sim as engine
import run_paper_revision as study


class PaperRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(study.MEASUREMENT.read_text())

    def profiles(self, model, mode="soft", **kwargs):
        return study.profiles_from_measurement(self.table, model, mode, **kwargs)

    def test_three_model_scope_and_refused_web(self):
        self.assertEqual([len(self.profiles(m)) for m in study.MODELS], [7, 7, 6])
        self.assertNotIn("Web/CommentPost", self.profiles(study.QWEN))
        merged = copy.deepcopy(self.table)
        merged["rows"] += merged["qwen_rows"]
        self.assertEqual(len(study.merged_rows(merged)), 20)

    def test_qwen_complete_failure_is_not_replaced_by_imputed_C(self):
        p = self.profiles(study.QWEN)
        osm = p["Android/OsmAndMarker"]
        self.assertAlmostEqual(osm["C_fail"], 1585498.9733333334)
        self.assertAlmostEqual(osm["C"], 346595.7266666667)
        self.assertEqual(osm["imputed"], ["C", "d", "q"])
        for value in p.values():
            if value["observed_admitted"]:
                self.assertAlmostEqual(value["C_fail"], osm["C_fail"])

    def test_partial_calc_is_local_censored_cost_and_missing_gate(self):
        p = self.profiles(study.QWEN, "binary_replay")
        calc = p["Desktop/CalcTableSave"]
        self.assertAlmostEqual(calc["C_fail"], 847543.8933333333)
        self.assertTrue(calc["C_fail_partial"])
        self.assertIsNone(calc["final_gate"])
        self.assertEqual(calc["p"], 0)
        self.assertIn("terminated", calc["admission_evidence"])
        self.assertNotEqual(calc["C_fail"], p["Desktop/WriterMemoSave"]["C_fail"])

    def test_complete_observed_failures_and_passing_costs_stay_own(self):
        profiles = {m: self.profiles(m) for m in study.MODELS}
        for row in study.merged_rows(self.table):
            p = profiles[row["model"]][f'{row["platform"]}/{row["family"]}']
            if row["admitted"]:
                self.assertEqual(p["C"], row["C"])
                self.assertEqual(p["d"], row["d"])
                self.assertEqual(p["q"], row["q"])
            else:
                self.assertEqual(p["C_fail"], study.observed_failed_cost(row))

    def test_glm_failure_cost_is_assumption_with_prespecified_range(self):
        for p in self.profiles(study.GLM).values():
            self.assertEqual(p["C_fail"], p["C"])
            self.assertIn("assumed", p["origins"]["C_fail"])
            self.assertIn("C_fail", p["imputed"])
        for mult in (0.5, 5):
            for p in self.profiles(study.GLM, cf_multiplier=mult).values():
                self.assertEqual(p["C_fail"], mult * p["C"])

    def test_pi_counts_attempts_including_unsuccessful_attempts(self):
        self.assertEqual(self.profiles(study.GLM)["Android/OsmAndMarker"]["pi"], 1 / 8)
        self.assertEqual(self.profiles(study.DS)["Desktop/WriterMemoSave"]["pi"], 3 / 4)
        self.assertEqual(self.profiles(study.QWEN)["Android/OsmAndMarker"]["pi"], 1 / 7)

    def test_pure_tokens_exclude_harm_and_engine_output_is_preserved(self):
        p = dict(c=10, d=1, C=5, C_fail=7, p=1, q=1, pi=1)
        raw = engine.run(["f"] * 4, {"f": p}, {"f": "f"}, "earliest",
                         h=0, m=0, tau0=0, silent=1, penalty=3)
        saved = copy.deepcopy(raw)
        result = study.account_result(raw)
        self.assertEqual(raw, saved)
        self.assertEqual(result["engine"]["tokens"], 66)
        self.assertEqual(result["token_cost"], 36)
        self.assertEqual(result["auxiliary_penalty"], 30)
        self.assertEqual(result["engine"]["success_rate"], 0.75)

    def test_frozen_size_parameters_and_comparators(self):
        config = study.make_config()
        jobs = config["jobs"]
        self.assertEqual(len(jobs), 109)
        self.assertEqual(sum(j["tag"] == "base" for j in jobs), 63)
        self.assertEqual(sum(j["group"] == "additional_cf" for j in jobs), 4)
        self.assertEqual(config["comparator_definitions"]["best_fixed"], ["reactive", "earliest"])
        for spec in jobs:
            self.assertEqual(spec["reps"], 20)
            self.assertEqual(spec["seed"], 20260913)
            self.assertEqual(spec["penalty"], 3)
            self.assertEqual(spec["fallback_mult"], 1)
            self.assertEqual(spec["policies"], list(engine.POLICIES))

    def test_same_rep_uses_same_stream_mapping_across_scenarios(self):
        jobs = study.make_config()["jobs"]
        selected = [j for j in jobs if j["model"] == study.QWEN and j["stream"] == "bursty"]
        a, mapping = study.prepare_rep(selected[0], 3)
        for spec in selected[1:]:
            self.assertEqual(study.prepare_rep(spec, 3), (a, mapping))


if __name__ == "__main__":
    unittest.main()
