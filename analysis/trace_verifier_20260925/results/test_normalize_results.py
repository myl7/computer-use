import hashlib
import json
import unittest
from pathlib import Path

from analysis.trace_verifier_20260925.results.normalize_results import normalize, usage_pw

ROOT = Path(__file__).resolve().parents[3]


class NativeNormalizationTest(unittest.TestCase):
    def source(self):
        path = ROOT / "analysis/trace_verifier_20260925/PROTOCOL.md"
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_weighted_tokens_use_frozen_model_ratios(self):
        prices = json.loads((ROOT / "paper/measurement_update_20260918.json").read_text())["prices"]
        model = "qwen/qwen3.8-flash"
        usage = {"prompt_tokens": 100, "cached_tokens": 50, "completion_tokens": 10}
        expected = 50 + 50 * prices[model]["p_c"] / prices[model]["p_in"] + 10 * prices[model]["p_o"] / prices[model]["p_in"]
        self.assertAlmostEqual(usage_pw(usage, model), expected)

    def test_desktop_native_charge_references_are_preserved(self):
        path, digest = self.source()
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "desktop",
            "model": "z-ai/glm-5.3-flash", "family": "CalcTableSave",
            "attempt_id": "initial", "attempt_role": "initial", "terminal_status": "complete",
            "source": {"build_path": str(path), "build_sha256": digest,
                       "artifact_path": str(path), "artifact_sha256": digest},
            "tasks": [{"seed": i, "prompt": f"p{i}", "prompt_provenance": "recorded",
                       "status": "pass", "extraction": {"binding": {}, "type_check": True,
                       "calls": [], "wall_s": 0.1}} for i in (1, 2, 3)],
            "initial_program_sha256": digest, "final_program_sha256": digest,
            "candidate_history": [{"sha256": digest, "tasks": [{"seed": i, "status": "pass"} for i in (1, 2, 3)]}],
            "admitted": True, "wall_s": 1.0,
            "charges_pw": {"reused_initial_translation": 1, "reused_initial_builder": 2,
                           "new_extraction": 3, "new_repair": 4,
                           "references": {"reused_initial_translation": "build#/translator",
                                          "reused_initial_builder": "build#/builder",
                                          "new_extraction": ["#/tasks/0/extraction/calls/0"],
                                          "new_repair": ["#/repairs/0/calls/0"]}},
            "deployment": {"status": "reused", "historical_deployed_program_sha256": digest,
                           "final_program_sha256": digest, "program_hash_equal": True,
                           "task_generator_identical": True, "input_protocol_identical": True},
        }
        normalized = normalize(record)
        self.assertEqual(normalized["charges"]["C"], 10)
        self.assertEqual(normalized["charges"]["price_sheet"], "measurement_update_20260918-prices-v1")
        refs = {row["category"]: row for row in normalized["charges"]["charge_records"]}
        self.assertEqual(refs["new_extraction"]["call_record"], ["#/tasks/0/extraction/calls/0"])
        self.assertEqual(refs["reused_initial_builder"]["source_artifact"], ["build#/builder"])
        self.assertEqual(normalized["deployment"]["deployed_program_sha256"], digest)

    def test_web_usage_is_reconciled_from_native_records(self):
        path, digest = self.source()
        model = "deepseek/deepseek-v4-flash-vision-exp"
        usage = {"prompt_tokens": 10, "cached_tokens": 2, "completion_tokens": 3}
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "web", "model": model,
            "family": "CommentPost", "attempt_id": "initial", "attempt_role": "initial",
            "terminal_status": "provider_error", "source_paths": {"build": str(path)},
            "source_sha256": {"build": digest},
            "reused_initial_charges": {"reused_initial_translation": usage,
                                       "reused_initial_builder": usage},
            "new_charges": {"extraction": usage, "repair": usage}, "wall_s": 2,
        }
        normalized = normalize(record)
        one = usage_pw(usage, model)
        self.assertAlmostEqual(normalized["charges"]["C"], 4 * one)
        self.assertEqual(normalized["terminal_status"], "provider_error")

    def test_desktop_complete_thirty_use_deployment_is_canonical(self):
        path, digest = self.source()
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "desktop",
            "model": "z-ai/glm-5.3-flash", "family": "WriterMemoSave",
            "attempt_id": "initial", "attempt_role": "initial", "terminal_status": "complete",
            "source": {"build_path": str(path), "build_sha256": digest,
                       "artifact_path": str(path), "artifact_sha256": digest},
            "tasks": [], "charges_pw": {"reused_initial_translation": 0,
                "reused_initial_builder": 0, "new_extraction": 0, "new_repair": 0,
                "references": {}},
            "deployment": {"status": "complete", "n": 30,
                           "uses": [{"status": "pass"} for _ in range(30)]},
        }
        normalized = normalize(record)
        self.assertEqual(normalized["deployment"]["status"], "new_30_use")
        self.assertEqual(len(normalized["deployment"]["records"]), 30)

    def test_typed_repeat_stage_error_is_generation_failure(self):
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "Android",
            "model": "deepseek/deepseek-v4-flash-vision-exp",
            "family": "SimpleCalendarAddOneEvent", "attempt_id": "attempt1",
            "attempt_role": "repeat", "terminal_status": "failed_stage_error",
            "failure_kind": "empty_artifact_responses_exhausted", "admitted": False,
            "source_artifacts": [], "usage": {}, "deployment": {"status": "not_required_repeat"},
        }
        normalized = normalize(record)
        self.assertEqual(normalized["terminal_status"], "generation_output_budget_failure")
        self.assertFalse(normalized["admission"])

    def test_exhausted_artifact_generation_repeat_is_generation_failure(self):
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "android",
            "model": "qwen/qwen3.8-flash", "family": "ContactsAddContact",
            "attempt_id": "attempt1", "attempt_role": "repeat",
            "terminal_status": "failed_stage_error",
            "failure_kind": "artifact_generation_attempts_exhausted", "admitted": False,
            "source_artifacts": [], "usage": {},
            "deployment": {"status": "not_required_repeat"},
        }
        normalized = normalize(record)
        self.assertEqual(normalized["terminal_status"], "generation_output_budget_failure")

    def test_official_qwen_cny_stays_separate_from_frozen_pw(self):
        record = {
            "protocol_id": "three-building-model-extracted-v1", "platform": "android",
            "model": "qwen/qwen3.8-flash", "family": "ContactsAddContact",
            "attempt_id": "repeat-1", "attempt_role": "repeat", "terminal_status": "provider_error",
            "admitted": False, "source_artifacts": [], "usage": {},
            "deployment": {"status": "not_required_repeat"},
            "official_route_accounting": {"separate_from_frozen_pw": True, "calls": 1,
                "prompt_tokens": 121, "cached_tokens": 0, "completion_tokens": 44,
                "cost_cny": 0.0002156, "receipts": [{"route": "official-qwen",
                "response_id": "r1", "requested_temperature": 0}]},
        }
        normalized = normalize(record)
        official = normalized["official_route_accounting"]
        self.assertEqual(official["cost_cny"], 0.0002156)
        self.assertAlmostEqual(official["frozen_pw_from_actual_tokens"], 121 + 44 * (.47 / .15))

    def test_cached_tokens_must_be_prompt_subset(self):
        with self.assertRaises(ValueError):
            usage_pw({"prompt_tokens": 10, "cached_tokens": 11, "completion_tokens": 1},
                     "qwen/qwen3.8-flash")


if __name__ == "__main__":
    unittest.main()
