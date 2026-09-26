import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class RevisedMeasurementAuditTest(unittest.TestCase):
    def test_admitted_deployment_pw_targets(self):
        audit = json.loads((ROOT / "analysis/trace_verifier_20260925/results/deployment-pw-audit.json").read_text())
        rows = {(row["model"], row["family"]): row for row in audit["cells"]}
        expected = {
            ("deepseek/deepseek-v4-flash-vision-exp", "MarkorDeleteNote"): 330.6,
            ("qwen/qwen3.8-flash", "ContactsAddContact"): 552.0644444444445,
            ("qwen/qwen3.8-flash", "MarkorDeleteNote"): 388.11111111111114,
            ("z-ai/glm-5.3-flash", "ContactsAddContact"): 424.5311111111111,
            ("z-ai/glm-5.3-flash", "MarkorDeleteNote"): 331.6333333333333,
            ("z-ai/glm-5.3-flash", "SimpleCalendarAddOneEvent"): 639.9911111111111,
        }
        for key, value in expected.items():
            self.assertAlmostEqual(rows[key]["d_pw"], value)

    def test_paired_same_index_bootstrap_targets(self):
        data = json.loads((ROOT / "experimental-results/trace_verifier_20260925/render-producer-partial/table-data.json").read_text())
        rows = {(row["model"], row["family"]): row for row in data["measurement"]["paired_replays"]}
        expected = {
            ("deepseek/deepseek-v4-flash-vision-exp", "MarkorDeleteNote"): (0.9518668889468531, [0.8805308326538276, 0.9889984684995624]),
            ("qwen/qwen3.8-flash", "ContactsAddContact"): (0.9535705195712343, [0.8861234434940896, 0.987536812246213]),
            ("qwen/qwen3.8-flash", "MarkorDeleteNote"): (0.979869729313212, [0.9780699697565797, 0.9816085440134733]),
            ("z-ai/glm-5.3-flash", "ContactsAddContact"): (0.9936755625390459, [0.9928771596077884, 0.9943638212276348]),
            ("z-ai/glm-5.3-flash", "MarkorDeleteNote"): (0.9906372991906613, [0.9892737570509524, 0.9917779912348491]),
            ("z-ai/glm-5.3-flash", "SimpleCalendarAddOneEvent"): (0.9988044682738814, [0.9985573271625201, 0.9989987093663035]),
        }
        self.assertEqual(sum(row["n"] for row in rows.values()), 180)
        for key, (point, interval) in expected.items():
            self.assertAlmostEqual(rows[key]["saving_share"]["estimate"], point)
            for actual, target in zip(rows[key]["saving_share"]["ci95"], interval):
                self.assertAlmostEqual(actual, target)

    def test_revised_tables_keep_attempt_order_and_lower_bound(self):
        data = json.loads((ROOT / "experimental-results/trace_verifier_20260925/render-producer-partial/measurement-data.json").read_text())
        price = {row.split(" & ", 1)[0]: row for row in data["latex_triples"]["price"]}
        repeat = {row.split(" & ", 1)[0]: row for row in data["latex_triples"]["verification"]}
        self.assertIn(">=1397386", price["Reddit comment"])
        self.assertIn("Fail 0/1/2", price["Contacts"])
        self.assertIn("Pass 3/0/0", price["Contacts"])
        self.assertIn("P/G/P", repeat["Contacts"])
        self.assertIn("F/G/F", repeat["Calendar"])

    def test_serving_intervals_use_deployment_rows(self):
        data = json.loads((ROOT / "experimental-results/trace_verifier_20260925/render-producer-partial/table-data.json").read_text())
        rows = {(row["model"], row["family"]): row for row in data["measurement"]["serving"]}
        self.assertEqual(len(rows), 11)
        deepseek = rows[("deepseek/deepseek-v4-flash-vision-exp", "MarkorDeleteNote")]["program_cost"]
        self.assertAlmostEqual(deepseek["estimate"], 330.6)
        self.assertLess(deepseek["ci95"][0], deepseek["estimate"])
        self.assertGreater(deepseek["ci95"][1], deepseek["estimate"])


if __name__ == "__main__": unittest.main()
