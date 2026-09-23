"""Ensure inherited scenario keys cannot collide across paired grids."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("pace_study_v2_test", Path(__file__).with_name("study_v2.py"))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class StorageTests(unittest.TestCase):
    def test_same_scenario_key_in_two_groups_has_distinct_destination(self):
        a = study.destination(dict(group="original_grid", key="same"))
        b = study.destination(dict(group="fresh_grid", key="same"))
        self.assertNotEqual(a, b)

    def test_all_retained_conditions_have_distinct_destinations(self):
        evidence = json.loads(study.EVIDENCE.read_text())
        paths = [study.destination(dict(group=group, key=cell["key"]))
                 for group in study.GROUPS for cell in evidence["cells"][group]]
        self.assertEqual(len(paths), 612)
        self.assertEqual(len(set(paths)), 612)


if __name__ == "__main__":
    unittest.main()
