import unittest
import json
from pathlib import Path

from analysis.trace_verifier_20260925.results.rerun_simulations import (
    add_missing_cost_sensitivity,
    apply_partial_observation_bounds,
    main_storage_key,
    prepare_main_config,
    reusable_cell_matches,
    revised_observation,
    revised_profile,
    source_group_for_job,
    transform_config,
)

ROOT = Path(__file__).resolve().parents[3]


class RevisedProfileTest(unittest.TestCase):
    def test_preserves_assigned_probability_and_replaces_independent_costs(self):
        saved = {"C": 20.0, "C_fail": 15.0, "d": 4.0, "q": 0.25,
                 "p": 0.35, "observed_admitted": False, "source": "old"}
        update = {
            "previous": {"C": 10.0, "C_fail": 5.0, "d": 2.0, "q": 0.5},
            "revised": {"C": 12.0, "C_fail": 7.0, "d": 3.0, "q": 0.2,
                        "observed_admitted": True, "source": "new"},
        }
        result = revised_profile(saved, update)
        self.assertEqual(result["p"], 0.35)
        self.assertEqual(result["C"], 12.0)
        self.assertEqual(result["C_fail"], 7.0)
        self.assertEqual(result["d"], 3.0)
        self.assertEqual(result["q"], 0.2)
        self.assertTrue(result["observed_admitted"])

    def test_real_nested_comparison_jobs_are_changed(self):
        path = ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"
        config = json.loads(path.read_text())
        updates = {"models": {}}
        for job in config["jobs"]:
            spec = job["spec"]
            model_rows = updates["models"].setdefault(spec["model"], {})
            for name, row in spec["profiles"].items():
                model_rows.setdefault(name, {
                    "previous": {field: row[field] for field in ("C", "C_fail", "d", "q")},
                    "revised": {"C": row["C"] + 101, "C_fail": row["C_fail"] + 203,
                                "d": row["d"] + 3, "q": min(1, row["q"] + .01),
                                "observed_admitted": row["observed_admitted"], "source": "synthetic"},
                })
        revised, changed = transform_config(config, updates)
        expected = sum(len(job["spec"]["profiles"]) for job in config["jobs"])
        self.assertEqual(changed, expected)
        old = config["jobs"][0]["spec"]["profiles"]["Android/ContactsAddContact"]
        new = revised["jobs"][0]["spec"]["profiles"]["Android/ContactsAddContact"]
        self.assertEqual(new["C"], old["C"] + 101)
        self.assertEqual(new["p"], old["p"])

    def test_missing_cost_cells_have_unique_ids_and_preserve_core(self):
        base_path = ROOT / "experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/config.json"
        profile_path = ROOT / "analysis/trace_verifier_20260925/results/profiles.json"
        base = prepare_main_config(json.loads(base_path.read_text()))
        revised, _ = transform_config(base, json.loads(profile_path.read_text()))
        core = json.loads(json.dumps(revised["jobs"]))
        added = add_missing_cost_sensitivity(revised, json.loads(profile_path.read_text()))
        self.assertEqual(added, 4)
        self.assertEqual(revised["jobs"][:612], core)
        self.assertEqual(len({main_storage_key(job) for job in revised["jobs"]}), 616)
        extras = revised["jobs"][612:]
        self.assertEqual({job["_source_group"] for job in extras}, {"additional_missing_cost"})
        self.assertEqual({job["missing_cost_base_case"]["case"] for job in extras},
                         {"bursty_default", "bpi2019_real"})

    def test_reusable_main_cell_rejects_spec_mismatch(self):
        job = {"key": "k", "suite": "s", "_source_group": "g", "seed": 7}
        result = {"key": "k", "suite": "s", "spec": dict(job)}
        self.assertTrue(reusable_cell_matches("main", job, result))
        result["spec"]["seed"] = 8
        self.assertFalse(reusable_cell_matches("main", job, result))

    def test_reusable_sensitivity_cell_checks_original_hashes(self):
        job = {
            "key": "k", "scenario": "missing_price_r1", "kind": "missing_price", "ratio": 1.0,
            "source": "source.json", "source_sha256": "abc",
            "spec": {"model": "m", "pattern": "p"},
            "original_hashes": [{"stream": "s", "mapping": "m", "profiles": "p"}],
        }
        result = {
            "key": "k", "scenario": "missing_price_r1", "kind": "missing_price", "ratio": 1.0,
            "model": "m", "pattern": "p", "source": "source.json", "source_sha256": "abc",
            "input_hashes": [{"original": dict(job["original_hashes"][0])}],
        }
        self.assertTrue(reusable_cell_matches("sensitivity", job, result))
        result["input_hashes"][0]["original"]["stream"] = "changed"
        self.assertFalse(reusable_cell_matches("sensitivity", job, result))

    def test_imputed_failed_price_remains_partial_observation(self):
        revised = {
            "C": 100.0, "C_fail": 300.0, "observed_admitted": False, "source": "partial.json",
            "C_fail_imputation": {"observed_lower_bound_pw": 125.0},
        }
        observation = revised_observation(revised)
        self.assertEqual(observation["branch"], "partial")
        self.assertEqual(observation["observed_price"], 125.0)
        self.assertNotEqual(observation["observed_price"], revised["C_fail"])

    def test_downstream_jobs_relink_to_main_source_groups(self):
        self.assertEqual(source_group_for_job({"group": "fresh_grid"}, {"suite": "validation_grid"}),
                         "fresh_grid")
        self.assertEqual(source_group_for_job({}, {"suite": "paired_validation_real"}),
                         "real_arrivals")
        self.assertEqual(source_group_for_job({}, {"suite": "paired_validation_grid"}),
                         "original_grid")

    def test_partial_sensitivity_profile_uses_observed_bound(self):
        spec = {"profiles": {"Web/CommentPost": {"C": 100.0, "C_fail": 300.0}}}
        observations = {"Web/CommentPost": {"branch": "partial", "observed_price": 125.0}}
        overrides = apply_partial_observation_bounds(spec, observations)
        self.assertEqual(spec["profiles"]["Web/CommentPost"]["C_fail"], 125.0)
        self.assertEqual(overrides["Web/CommentPost"]["modeled_C_fail"], 300.0)


if __name__ == "__main__":
    unittest.main()
