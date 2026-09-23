"""Targeted checks for observed-price transformations and study pairing."""
from collections import Counter
from copy import deepcopy
import importlib.util
import inspect
import json
from pathlib import Path
import random
import unittest

spec = importlib.util.spec_from_file_location("pace_price_tests", Path(__file__).with_name("study.py"))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class SensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = study.make_config()

    def test_complete_condition_set_without_duplicates(self):
        jobs = self.config["jobs"]
        self.assertEqual(len(jobs), 72)
        self.assertEqual(Counter(j["scenario"] for j in jobs), {s: 12 for s in study.SCENARIOS})
        self.assertEqual(len({str(study.destination(j)) for j in jobs}), 72)

    def test_observed_price_identification_and_qwen_override(self):
        self.assertEqual(self.config["branch_counts"],
                         {"accepted": 15, "rejected_complete": 4, "partial": 1})
        qwen = self.config["observations"]["qwen/qwen3.8-flash"]
        item = qwen["profiles"]["Android/OsmAndMarker"]
        self.assertEqual(item["extraction"], "measured.C_with_repair")
        self.assertEqual(item["observed_price"], 1585498.9733333334)
        self.assertEqual(qwen["profiles"]["Desktop/CalcTableSave"]["branch"], "partial")

    def test_unsupported_observation_stops_identification(self):
        with self.assertRaises(ValueError):
            study.identify_observations({"rows": [dict(model="m", platform="x", family="f", C=3)]})

    def test_price_transform_preserves_observed_branch_and_other_values(self):
        for item in self.config["observations"].values():
            original = item["original_profiles"]
            untouched = deepcopy(original)
            for ratio in study.RATIOS:
                changed = study.modify_prices(original, item["profiles"], ratio)
                for name, row in changed.items():
                    observed = item["profiles"][name]
                    if observed["branch"] == "accepted":
                        self.assertEqual(row["C"], observed["observed_price"])
                        self.assertEqual(row["C_fail"], ratio * row["C"])
                    elif observed["branch"] == "rejected_complete":
                        self.assertEqual(row["C_fail"], observed["observed_price"])
                        self.assertEqual(row["C"], row["C_fail"] / ratio)
                    else:
                        self.assertEqual(row["C"], original[name]["C"])
                        self.assertEqual(row["C_fail"], max(observed["observed_price"], ratio * row["C"]))
                    for key in original[name].keys() - {"C", "C_fail"}:
                        self.assertEqual(row[key], original[name][key])
            self.assertEqual(original, untouched)

    def test_partial_spend_floor_is_active_and_disclosed(self):
        item = self.config["observations"]["qwen/qwen3.8-flash"]
        for ratio in (.5, 1, 2):
            row = study.modify_prices(item["original_profiles"], item["profiles"], ratio)["Desktop/CalcTableSave"]
            self.assertGreater(row["C_fail"] / row["C"], ratio)
        row = study.modify_prices(item["original_profiles"], item["profiles"], 5)["Desktop/CalcTableSave"]
        self.assertAlmostEqual(row["C_fail"] / row["C"], 5)

    def test_adverse_probability_monotone_and_shared_by_profile(self):
        for item in self.config["observations"].values():
            profiles = item["original_profiles"]
            probabilities = study.adverse_probabilities(profiles)
            order = sorted(profiles, key=lambda name: (profiles[name]["C"] / profiles[name]["c"], name))
            self.assertAlmostEqual(probabilities[order[0]], .9)
            self.assertAlmostEqual(probabilities[order[-1]], .1)
            self.assertTrue(all(probabilities[a] > probabilities[b] for a, b in zip(order, order[1:])))

    def test_remap_preserves_multiset_and_assigns_expensive_to_frequent(self):
        profiles = {"a": dict(C=1, c=1), "b": dict(C=4, c=1), "c": dict(C=9, c=1)}
        origins = {"f": "a", "g": "c", "h": "a", "i": "b"}
        arrivals = ["f"] * 9 + ["g"] * 2 + ["h"] * 5 + ["i"]
        new = study.remap_by_recurrence(origins, profiles, arrivals)
        self.assertEqual(Counter(new.values()), Counter(origins.values()))
        self.assertEqual(new, {"f": "c", "h": "b", "g": "a", "i": "a"})

    def test_all_sepsis_sources_reproduce_hashes_and_pairings(self):
        for job in self.config["jobs"]:
            if job["spec"]["pattern"] != "sepsis":
                continue
            for rep in (0, 9):
                arrivals, mapping, profiles, record = study.prepare(job, rep)
                self.assertEqual(record["original"], job["original_hashes"][rep])
                self.assertEqual(record["arrivals"], 1050)
                self.assertEqual(record["families"], 846)
                self.assertEqual(set(profiles), set(mapping))

    def test_equal_attempt_prices_give_exact_policy_parity(self):
        stream = random.Random(4).choices(["a", "b", "c"], k=200)
        profiles = {"p": dict(c=20., d=1., C=10., C_fail=10., p=.4, q=.2, pi=.8)}
        mapping = {family: "p" for family in stream}
        kwargs = dict(seed=71, h=.04, m=1, tau0=2)
        self.assertEqual(study.engine.full_run(stream, profiles, mapping, study.MAIN, **kwargs),
                         study.engine.run(stream, profiles, mapping, study.EQUAL, **kwargs))

    def test_proposal_has_no_hidden_probability_or_future_count_input(self):
        signature = inspect.signature(study.engine._full_safety.propose)
        self.assertTrue({"p", "true_p", "future_counts", "stream"}.isdisjoint(signature.parameters))

    def test_appended_future_arrivals_do_not_change_prefix_actions(self):
        prefix = ["a", "b"] * 30
        profiles = {"p": dict(c=20., d=1., C=10., C_fail=25., p=.4, q=.2, pi=.8)}
        mapping = {"a": "p", "b": "p"}
        kwargs = dict(seed=13, h=.03, m=1, tau0=2, trace=True)
        for policy in (study.MAIN, study.EQUAL):
            runner = study.engine.full_run if policy == study.MAIN else study.engine.run
            a = runner(prefix, profiles, mapping, policy, **kwargs)
            b = runner(prefix + ["a"] * 100, profiles, mapping, policy, **kwargs)
            self.assertEqual(a["events"], b["events"][:len(prefix)])

    def test_actual_failure_bills_and_every_prefix_constraint(self):
        stream = ["a"] * 100
        profiles = {"p": dict(c=10., d=1., C=1., C_fail=40., p=0., q=0., pi=1.)}
        mapping = {"a": "p"}
        for policy in (study.MAIN, study.EQUAL):
            runner = study.engine.full_run if policy == study.MAIN else study.engine.run
            raw = runner(stream, profiles, mapping, policy, seed=1, h=0, m=0, tau0=0, trace=True)
            self.assertGreater(raw["attempts"], 0)
            self.assertEqual(raw["failed_compile_tokens"], 40 * raw["attempts"])
            study.check_account(raw, 1000., True)
            self.assertTrue(all(e["paid"] <= 1.25 * e["baseline"] + 1e-8 * max(1, e["limit"])
                                for e in raw["events"]))


if __name__ == "__main__":
    unittest.main()
