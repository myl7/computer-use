import hashlib
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("validate_results", HERE / "validate_results.py")
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def valid_record():
    source = VALIDATOR.ROOT / "analysis/trace_verifier_20260925/PROTOCOL.md"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tasks = [{"seed": seed, "prompt": f"task {seed}", "prompt_provenance": "recorded"}
             for seed in (1, 2, 3)]
    extractions = [{"seed": seed, "input": {}, "output": {}, "binding": {},
                    "type_check": True, "retry_count": 0, "calls": [], "elapsed_seconds": 0.1}
                   for seed in (1, 2, 3)]
    return {
        "protocol_id": VALIDATOR.PROTOCOL,
        "platform": "android", "model": "z-ai/glm-5.3-flash",
        "family": "ContactsAddContact", "attempt_id": "initial", "attempt_role": "initial",
        "terminal_status": "complete",
        "source_artifacts": [{"role": "protocol", "path": str(source.relative_to(VALIDATOR.ROOT)), "sha256": digest}],
        "original_tasks": tasks, "extractions": extractions,
        "initial_program_sha256": "0" * 64,
        "final_program_sha256": "0" * 64,
        "candidate_history": [{"sha256": "0" * 64}],
        "task_results": [{"seed": seed, "status": "pass"} for seed in (1, 2, 3)],
        "admission": True, "repair_rounds": [],
        "charges": {"unit": "pw", "price_sheet": "frozen-test",
                    "reused_initial_translation": 1.0, "reused_initial_builder": 2.0,
                    "new_extraction": 3.0, "new_repair": 4.0, "C": 10.0,
                    "charge_records": [
                        {"category": "reused_initial_translation", "amount_pw": 1.0, "source_artifact": "translation"},
                        {"category": "reused_initial_builder", "amount_pw": 2.0, "source_artifact": "builder"},
                        {"category": "new_extraction", "amount_pw": 3.0, "call_record": "extract"},
                        {"category": "new_repair", "amount_pw": 4.0, "call_record": "repair"}]},
        "timings": {"total_seconds": 1.0},
        "deployment": {"status": "reused", "deployed_program_sha256": "0" * 64,
                       "byte_identical_program": True,
                       "unchanged_execution_protocol": True, "unchanged_input_protocol": True},
    }


class ResultValidationTest(unittest.TestCase):
    def test_valid_complete_record_is_eligible(self):
        errors, eligible = VALIDATOR.check_record(Path("fixture"), valid_record())
        self.assertEqual(errors, [])
        self.assertTrue(eligible)

    def test_admission_requires_all_three_passes(self):
        record = valid_record()
        record["task_results"][1]["status"] = "fail"
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertIn("admission_requires_three_passes", errors)
        self.assertFalse(eligible)

    def test_C_includes_all_four_components(self):
        record = valid_record()
        record["charges"]["C"] = 9.0
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertIn("C_must_equal_four_charge_components", errors)
        self.assertFalse(eligible)

    def test_timeout_is_never_measurement_eligible(self):
        record = valid_record()
        record["terminal_status"] = "timeout"
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertEqual(errors, [])
        self.assertFalse(eligible)

    def test_extraction_failure_is_a_measured_rejection(self):
        record = valid_record()
        record["terminal_status"] = "extraction_failure"
        record["admission"] = False
        record.pop("task_results")
        record.pop("candidate_history")
        record.pop("deployment")
        record["extractions"] = record["extractions"][:1]
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertEqual(errors, [])
        self.assertTrue(eligible)

    def test_generation_failure_with_lower_bound_is_outcome_not_exact_profile(self):
        record = valid_record()
        record["terminal_status"] = "generation_output_budget_failure"
        record["admission"] = False
        record["charges"]["exact"] = False
        record.pop("task_results")
        record.pop("candidate_history")
        record.pop("deployment")
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertEqual(errors, [])
        self.assertFalse(eligible)

    def test_short_circuit_keeps_remaining_tasks_untested(self):
        record = valid_record()
        record["admission"] = False
        record["task_results"] = [
            {"seed": 1, "status": "fail"},
            {"seed": 2, "status": "pass"},
            {"seed": 3, "status": "untested"},
        ]
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertIn("tested_task_after_first_failure", errors)
        self.assertFalse(eligible)

    def test_admitted_repeat_does_not_require_serving_deployment(self):
        record = valid_record()
        record["attempt_id"] = "repeat-1"
        record["attempt_role"] = "repeat"
        record["deployment"] = {"status": "not_required_repeat"}
        errors, eligible = VALIDATOR.check_record(Path("fixture"), record)
        self.assertEqual(errors, [])
        self.assertTrue(eligible)


if __name__ == "__main__":
    unittest.main()
