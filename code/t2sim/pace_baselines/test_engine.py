"""Tests of literature adapters and shared simulator accounting."""
import importlib.util
import inspect
from pathlib import Path
import random
import unittest

_spec = importlib.util.spec_from_file_location("pace_test_engine", Path(__file__).with_name("engine.py"))
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)


class AdapterTests(unittest.TestCase):
    def run_case(self, stream, policy="autorpa_once", **kw):
        profile = dict(c=10.0, d=1.0, C=1.0, C_fail=1.0,
                       p=1.0, q=0.0, pi=1.0)
        profile.update(kw.pop("profile", {}))
        return engine.run(stream, {"p": profile}, {f: "p" for f in stream},
                          policy, trace=True, h=kw.pop("h", 0),
                          m=kw.pop("m", 0), tau0=kw.pop("tau0", 0), **kw)

    def test_compile_after_three_completed_services(self):
        for policy in engine.NAMED_POLICIES:
            result = self.run_case(["f"] * 4, policy)
            self.assertEqual([e["program"] for e in result["events"]],
                             [False, False, False, True])
            self.assertEqual(result["tokens"], 32)

    def test_final_arrival_build_still_costs_tokens(self):
        for policy in engine.NAMED_POLICIES:
            result = self.run_case(["f"] * 3, policy)
            self.assertEqual(result["tokens"], 31)
            self.assertEqual(result["program_uses"], 0)

    def test_autorpa_one_bundled_attempt_after_failed_build(self):
        result = self.run_case(["f"] * 100, profile={"p": 0, "C_fail": 7})
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["failed_compile_tokens"], 7)
        self.assertEqual(result["tokens"], 1007)

    def test_autorpa_drift_falls_back_without_rebuild(self):
        result = self.run_case(["f"] * 20, h=1)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["breaks"], 1)
        self.assertEqual(result["program_uses"], 1)
        self.assertEqual(result["events"][3]["service_cost"], 11)
        self.assertEqual(result["tokens"], 202)

    def test_regular_failure_keeps_artifact_and_charges_one_fallback(self):
        result = self.run_case(["f"] * 5, profile={"q": 1})
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["program_uses"], 2)
        self.assertEqual(result["reactive_uses"], 5)
        self.assertEqual(result["breaks"], 0)
        self.assertEqual(result["tokens"], 53)

    def test_expiry_relists_without_recompilation(self):
        result = self.run_case(["f"] * 3 + ["g"] * 3 + ["f"], ttl=1)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["relists"], 1)
        self.assertTrue(result["events"][-1]["program"])

    def test_toolpro_does_not_amortize_over_future_arrivals(self):
        result = self.run_case(["f"] * 300, "toolpro_cost",
                               profile={"C": 5, "C_fail": 7})
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(result["tokens"], 3000)

    def test_toolpro_updates_price_after_observed_failure(self):
        result = self.run_case(["f"] * 20, "toolpro_cost",
                               profile={"p": 0, "C": 4, "C_fail": 4})
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["events"][2]["p_est"], .5)
        self.assertEqual(result["events"][3]["p_est"], 1 / 3)
        self.assertEqual(result["failed_compile_tokens"], 4)

    def test_proposer_has_no_true_probability_or_horizon_input(self):
        fields = dict(demos=3, alive=False, attempts=0, admits=0,
                      c=10, d=1, C=1, C_fail=1, q=0, h=.02,
                      fallback_mult=1, k_min=3)
        for policy in engine.NAMED_POLICIES:
            for forbidden in ["true_p", "p", "horizon", "future_arrivals", "stream"]:
                with self.assertRaises(TypeError):
                    engine.propose(policy, **fields, **{forbidden: 1})
        self.assertNotIn("cfg", inspect.signature(engine.propose).parameters)

    def test_unknown_p_does_not_change_first_attempt_timing(self):
        for policy in engine.NAMED_POLICIES:
            results = [self.run_case(["f"] * 20, policy, profile={"p": p})
                       for p in [0, 1]]
            first = [next(e["t"] for e in result["events"] if e["attempt"])
                     for result in results]
            self.assertEqual(first, [2, 2])

    def test_decisions_do_not_change_when_future_stream_is_extended(self):
        for policy in engine.NAMED_POLICIES:
            prefix = ["f", "g"] * 20
            a = self.run_case(prefix, policy, profile={"p": .4, "pi": .8})
            b = self.run_case(prefix + ["h"] * 100, policy,
                              profile={"p": .4, "pi": .8})
            self.assertEqual(a["events"], b["events"][:len(prefix)])

    def test_shared_rng_reproduces_reference_when_decisions_coincide(self):
        rng = random.Random(23)
        stream = rng.choices(["a", "b", "c"], k=100)
        profile = dict(c=100.0, d=1.0, C=.01, C_fail=.01,
                       p=.6, q=.2, pi=.8)
        mapping = {f: "p" for f in stream}
        opts = dict(h=.05, m=3, tau0=2, seed=33, fallback_mult=2)
        expected = engine.reference.run(stream, {"p": profile}, mapping,
                                        "earliest", **opts)
        actual = engine.run(stream, {"p": profile}, mapping, "toolpro_cost", **opts)
        for key, value in expected.items():
            self.assertAlmostEqual(value, actual[key], places=8, msg=key)

    def test_accounting_includes_routing_compilation_and_fallback(self):
        result = self.run_case(["f", "g"] * 30, "toolpro_cost",
                               profile={"p": .4, "q": .2}, h=.1, m=2, tau0=3)
        self.assertAlmostEqual(result["token_cost"],
                               sum(result[k] for k in engine.COMPONENTS))
        self.assertEqual(result["baseline_cost"], 60 * 13)

    def test_narrow_price_matches_full_when_attempt_prices_match(self):
        stream = random.Random(31).choices(["a", "b", "c"], k=100)
        profiles = {"p": dict(c=10., d=1., C=3., C_fail=3., p=.4, q=.2, pi=.8)}
        mapping = {f: "p" for f in stream}
        opts = dict(seed=77, h=.02, m=1, tau0=2)
        narrow = engine.run(stream, profiles, mapping, "pace_narrow_price", **opts)
        full = engine.full_run(stream, profiles, mapping, "safe_projected_025", **opts)
        self.assertEqual(narrow, full)

    def test_narrow_price_keeps_actual_failure_bills_and_reservation(self):
        result = self.run_case(["f"] * 100, "pace_narrow_price",
                               profile={"p": 0, "C": 1, "C_fail": 40})
        self.assertGreater(result["attempts"], 0)
        self.assertEqual(result["failed_compile_tokens"], 40 * result["attempts"])
        self.assertLessEqual(result["max_prefix_ratio"], 1.250000013)
        self.assertFalse(any(e["attempt"] for e in result["events"][:15]))


if __name__ == "__main__":
    unittest.main()
