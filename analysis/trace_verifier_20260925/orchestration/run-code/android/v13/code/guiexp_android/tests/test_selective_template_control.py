"""Focused tests for the explicit goal-template control."""

from __future__ import annotations

import copy
import hashlib

from guiexp_android import selective_template_control as control


def _training(family: str) -> dict:
    item = next(row for row in control.TRAINING_TRACES if row["family"] == family)
    path = control.literal_control._trace_path(control.DEFAULT_ORIGIN, item["relative_path"])
    demo, _ = control.literal_control.load_public_demo(path, family)
    return demo


def test_parser_is_anchored_and_abstains_on_different_phrasing():
    exact = "Create a new note in Markor named fresh.txt with the following text: hello."
    parsed = control.parse_goal(control.CREATE, exact)
    assert parsed == {"filename": "fresh.txt", "filename_stem": "fresh", "body": "hello."}
    changed = "Create a new note in Markor named 2023_07_16_fierce_house.txt with the following text: Winter is coming."
    assert control.parse_goal(control.CREATE, changed)["filename_stem"] == "2023_07_16_fierce_house"
    assert control.parse_goal(control.CREATE, "Please create a note named fresh.txt with text: hello.") is None
    assert control.parse_goal(control.DELETE, "Delete the note in Markor named fresh.") == {
        "name": "fresh",
        "file_label": "File fresh ",
    }


def test_parameterization_uses_goal_value_and_keeps_observed_literals():
    demo = _training(control.CREATE)
    original = copy.deepcopy(demo)
    item = next(row for row in control.TRAINING_TRACES if row["family"] == control.CREATE)
    source = control.literal_control._trace_path(control.DEFAULT_ORIGIN, item["relative_path"])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    built = control.build_parameterized_plan(control.CREATE, demo)
    step = built["plan"]["steps"][2]
    reference = {"slot": "filename_stem", "transform": "identity"}
    assert step["action"]["text"] == reference
    assert step["after"] == [{"text": reference}]
    assert step["target"] == {"text": "my_note"}
    assert built["prefix_length"] == 3
    assert demo == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_delete_wrapper_is_goal_bound_and_checker_does_not_repair_wrong_binding():
    demo = _training(control.DELETE)
    built = control.build_parameterized_plan(control.DELETE, demo)
    assert built["plan"]["steps"][1]["target"] == {
        "description": {"slot": "file_label", "transform": "identity"}
    }
    wrong = dict(built["bindings"], file_label="File wrong ")
    report = control.selective_trace_compatibility.check_prefix(
        built["plan"], wrong, demo["steps"]
    )
    assert report["prefix_steps_matched"] == 1
    assert report["first_divergence"]["kind"] == "target_mismatch"


def test_run_reports_heldout_compatibility_without_ui_outcomes(tmp_path):
    result = control.run_control(control.DEFAULT_ORIGIN, tmp_path / "template_v1")
    assert {row["family"]: row["compatibility"]["prefix_steps_matched"] for row in result["training"]} == {
        control.CREATE: 3,
        control.DELETE: 4,
    }
    assert [(row["family"], row["compatibility"]["prefix_steps_matched"]) for row in result["heldout"]] == [
        (control.CREATE, 3),
        (control.CREATE, 3),
        (control.DELETE, 4),
        (control.DELETE, 1),
    ]
    assert result["heldout"][3]["compatibility"]["first_divergence"]["source_step"] == 2
    assert result["heldout"][3]["compatibility"]["first_divergence"]["kind"] == "target_mismatch"
    assert result["task_success_evaluated"] is False
    assert result["diagnostic_only"]["ui_outcome"] is None
    assert {path.name for path in (tmp_path / "template_v1").iterdir()} == {
        "config.json", "plan.json", "source_manifest.json", "results.json"
    }
