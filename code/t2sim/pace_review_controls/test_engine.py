"""Matched environment, economic-filter, and protection checks."""
import importlib.util
import json
from pathlib import Path
import random
import sys
import unittest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
engine = load("pace_control_test_engine", HERE / "engine.py")
prior = load("pace_control_test_prior", HERE.parent / "protocol_explore/engine.py")
study = load("pace_control_test_study", HERE / "study.py")


class ControlTests(unittest.TestCase):
    def profile(self, **kw):
        values = dict(c=10., d=1., C=1., C_fail=1., q=.1, p=.5, pi=.8)
        values.update(kw)
        return values

    def fields(self, **kw):
        fields = dict(demos=3, alive=False, arrivals=3, successes_since_admission=3,
                      attempts=0, admits=0, age=2, failed_spend=0., realized_saving=0.,
                      c=10., d=1., C=1., Cf=1., q=0., h=0., m=0., ttl=100,
                      silent=0., penalty=3., fallback_mult=1., k_min=3)
        fields.update(kw)
        return fields

    def run_case(self, policy, profile=None, stream=None, **kw):
        stream = stream or ["a"] * 60
        return engine.run(stream, {"p": profile or self.profile()},
                          {f: "p" for f in stream}, policy,
                          h=kw.pop("h", 0), m=kw.pop("m", 0),
                          tau0=kw.pop("tau0", 0), trace=True, **kw)

    def test_unprotected_allowance_controls_match_original_rules(self):
        stream = random.Random(31).choices(["a", "b", "c"], k=300)
        profiles = {"p": self.profile(C=2, C_fail=7)}
        mapping = {f: "p" for f in stream}
        opts = dict(seed=33, h=.1, m=3., tau0=2., fallback_mult=2.)
        for kind in ("earliest_cap", "fixed10_cap", "projected_cap"):
            a = engine.reference.run(stream, profiles, mapping, kind, **opts)
            b = engine.run(stream, profiles, mapping, engine.Policy("test", kind, None), **opts)
            for key, value in a.items():
                self.assertAlmostEqual(value, b[key], places=8, msg=kind + "/" + key)

    def test_full_pace_is_exactly_unchanged(self):
        stream = random.Random(37).choices(["a", "b", "c"], k=300)
        profiles = {"p": self.profile(C=2, C_fail=7)}
        mapping = {f: "p" for f in stream}
        opts = dict(seed=39, h=.1, m=3., tau0=2., fallback_mult=2., trace=True)
        a = prior.run(stream, profiles, mapping, "safe_projected_025", **opts)
        b = engine.run(stream, profiles, mapping, "safe_projected_025", **opts)
        self.assertEqual(a, b)

    def test_all_controls_keep_prefix_guard_under_failures_and_routing(self):
        for policy in engine.POLICIES:
            for p in (0., .3, 1.):
                result = self.run_case(policy, self.profile(C=2, C_fail=50, p=p, q=.6),
                                       stream=["a", "b"] * 100, h=.1, m=1000., tau0=3.)
                self.assertLessEqual(result["max_prefix_ratio"], 1.250000013)
                self.assertAlmostEqual(result["token_cost"], sum(result[k] for k in
                    ("reactive_tokens", "extraction_tokens", "compile_tokens", "router_tokens")))

    def test_allowance_accounts_for_past_failed_spend(self):
        fields = self.fields()
        self.assertTrue(engine.propose("earliest_cap", **fields))
        fields["failed_spend"] = 10.
        self.assertFalse(engine.propose("earliest_cap", **fields))
        fields["realized_saving"] = 20.
        self.assertTrue(engine.propose("earliest_cap", **fields))

    def test_after_ten_counts_arrivals_not_successes(self):
        fields = self.fields(arrivals=9, age=20, successes_since_admission=20)
        self.assertFalse(engine.propose("fixed10_cap", **fields))
        fields.update(arrivals=10, successes_since_admission=0)
        self.assertTrue(engine.propose("fixed10_cap", **fields))

    def test_count_control_changes_only_reuse_proposal_threshold(self):
        fields = self.fields(C=9., Cf=9.)
        self.assertFalse(engine.propose("projected", **fields))
        self.assertTrue(engine.propose("count", **fields))
        fields["m"] = 10.
        self.assertFalse(engine.propose("count", **fields))

    def test_count_control_keeps_lifetime_and_asymmetric_price(self):
        fields = self.fields(arrivals=100, age=100, C=1., Cf=1000., h=.02)
        self.assertFalse(engine.propose("count", **fields))
        fields["Cf"] = 1.
        self.assertTrue(engine.propose("count", **fields))
        fields["h"] = 1.
        self.assertFalse(engine.propose("count", **fields))

    def test_three_services_precede_compile_and_next_use(self):
        for policy in engine.POLICIES:
            if policy.proposer == "fixed10_cap":
                continue
            result = self.run_case(policy, self.profile(p=1., q=0.), stream=["a"] * 4)
            self.assertEqual([r["program"] for r in result["events"]], [False, False, False, True])

    def test_proposal_cannot_read_true_probability_or_horizon(self):
        for policy in engine.POLICIES:
            for forbidden in ("p", "true_p", "horizon", "stream", "future_arrivals"):
                with self.assertRaises(TypeError):
                    engine.propose(policy.proposer, **self.fields(), **{forbidden: 1})

    def test_unknown_probability_does_not_change_first_attempt(self):
        for policy in engine.POLICIES:
            first = []
            for p in (0., 1.):
                r = self.run_case(policy, self.profile(p=p, q=0.))
                first.append(next(e["t"] for e in r["events"] if e["attempt"]))
            self.assertEqual(first[0], first[1])

    def test_extending_horizon_cannot_change_prefix_actions(self):
        for policy in engine.POLICIES:
            a = self.run_case(policy, stream=["a", "b"] * 20)
            b = self.run_case(policy, stream=["a", "b"] * 20 + ["c"] * 30)
            self.assertEqual(a["events"], b["events"][:40])

    def test_all_condition_paths_are_unique(self):
        evidence = json.loads(study.EVIDENCE.read_text())
        paths = [study.destination(dict(group=g, key=c["key"]))
                 for g in study.GROUPS for c in evidence["cells"][g]]
        self.assertEqual(len(paths), 612)
        self.assertEqual(len(set(paths)), 612)


if __name__ == "__main__":
    unittest.main()
