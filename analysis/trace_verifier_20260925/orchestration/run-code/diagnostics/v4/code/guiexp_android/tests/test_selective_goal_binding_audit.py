"""Focused pure checks for source-goal binding validation."""

from __future__ import annotations

import json

from guiexp_android import selective_goal_binding_audit as audit


def _ax(nodes):
    return "\n".join(f"UI element {node['index']}: {json.dumps(node)}" for node in nodes)


def _record(step, action, pre, post):
    return {"step": step, "action": action, "pre_obs": {"ax_tree_text": _ax(pre)}, "post_obs": {"ax_tree_text": _ax(post)}}


def _two_ref_plan():
    stem = {"slot": "filename", "transform": "stem"}
    suffix = {"slot": "filename", "transform": "suffix"}
    return {
        "schema": "selective-plan/1",
        "slots": {"filename": "the public filename in the task"},
        "steps": [
            {"id": "stem", "intent": "type the stem", "action": {"action_type": "input_text", "text": stem}, "target": {"hint": "Stem", "editable": True}, "before": [{"hint": "Stem", "editable": True}], "after": [{"text": stem, "editable": True}]},
            {"id": "suffix", "intent": "type the extension", "action": {"action_type": "input_text", "text": suffix}, "target": {"hint": "Extension", "editable": True}, "before": [{"hint": "Extension", "editable": True}], "after": [{"text": suffix, "editable": True}]},
        ],
    }


def test_same_goal_binding_supports_stem_and_suffix_across_steps():
    plan = _two_ref_plan()
    records = [
        _record(1, {"action_type": "input_text", "text": "report", "index": 0}, [{"index": 0, "hint": "Stem", "editable": True}], [{"index": 0, "text": "report", "hint": "Stem", "editable": True}]),
        _record(2, {"action_type": "input_text", "text": ".md", "index": 0}, [{"index": 0, "hint": "Extension", "editable": True}], [{"index": 0, "text": ".md", "hint": "Extension", "editable": True}]),
    ]
    result = audit.forward_validate(plan, {"filename": "report.md"}, records)
    assert result["status"] == "compatible"
    assert result["valid"] is True
    assert result["effect_specific_after"]["status"] == "valid"


def test_missing_or_wrong_ui_prefix_binding_fails_without_repair():
    ref = {"slot": "name", "transform": "identity", "prefix": "File ", "suffix": " "}
    plan = {"schema": "selective-plan/1", "slots": {"name": "the public name"}, "steps": [{"id": "select", "intent": "select", "action": {"action_type": "click"}, "target": {"description": ref}, "before": [], "after": [{"text": "Selected"}]}]}
    records = [_record(1, {"action_type": "click", "index": 0}, [{"index": 0, "description": "File report ", "clickable": True}], [{"index": 0, "text": "Selected"}])]
    assert audit.forward_validate(plan, {"name": "report"}, records)["status"] == "compatible"
    wrong = audit.forward_validate(plan, {"name": "File report "}, records)
    assert wrong["valid"] is False
    assert wrong["trace_compatibility"]["first_divergence"]["kind"] == "target_mismatch"
    missing = audit.forward_validate(plan, {}, records)
    assert missing["trace_compatibility"]["first_divergence"]["kind"] == "missing_binding"


def test_reference_action_is_not_a_binding_source():
    plan = _two_ref_plan()
    records = [_record(1, {}, [{"index": 0, "hint": "Stem", "editable": True}], [{"index": 0, "text": "report", "hint": "Stem", "editable": True}]), _record(2, {"action_type": "input_text", "text": ".md", "index": 0}, [{"index": 0, "hint": "Extension", "editable": True}], [{"index": 0, "text": ".md", "hint": "Extension", "editable": True}])]
    result = audit.forward_validate(plan, {"filename": "report.md"}, records)
    assert result["valid"] is False
    assert result["trace_compatibility"]["first_divergence"]["kind"] in {"trace_missing", "target_mismatch"}
    assert "bindings" not in result


def test_structural_source_steps_require_exact_int_and_do_not_use_inverse(monkeypatch):
    plan = {"schema": "selective-plan/1", "slots": {}, "steps": [{"id": "open", "intent": "open", "action": {"action_type": "open_app", "app_name": "Markor"}, "target": None, "before": [], "after": [{"text": "Markor"}]}]}
    candidate = {"schema": "selective-prefix/1", "plan": plan, "source_steps": {"open": True}, "terminal_source_step": 1, "handoff_policy": "reactive"}
    monkeypatch.setattr(audit.trace_compatibility, "check_prefix", lambda *_: (_ for _ in ()).throw(AssertionError("inverse was used")))
    report = audit.validate_candidate(candidate, [{"step": 1}])
    assert report["valid"] is False
    assert "exact integers" in " ".join(report["errors"])


def test_effect_rule_rejects_empty_plan_and_empty_after_guards():
    assert audit.effect_after_rule({}, {}, [])["status"] == "invalid"
    plan = {"schema": "selective-plan/1", "slots": {}, "steps": [{"id": "open", "after": []}]}
    report = audit.effect_after_rule(plan, {}, [])
    assert report["status"] == "invalid"
    assert report["steps"][0]["reason"] == "after guards must be nonempty"


def test_effect_rule_rejects_unchanged_after_context():
    plan = {"schema": "selective-plan/1", "slots": {}, "steps": [{"id": "click", "intent": "click", "action": {"action_type": "click"}, "target": {"text": "Go"}, "before": [], "after": [{"text": "Go"}]}]}
    record = _record(1, {"action_type": "click", "index": 0}, [{"index": 0, "text": "Go"}], [{"index": 0, "text": "Go"}])
    report = audit.forward_validate(plan, {}, [record])
    assert report["trace_compatibility"]["status"] == "compatible"
    assert report["effect_specific_after"]["status"] == "invalid"
    assert report["status"] == "invalid"


def test_forward_report_declares_recorded_trace_scope_and_no_task_success():
    plan = {"schema": "selective-plan/1", "slots": {}, "steps": [{"id": "open", "intent": "open", "action": {"action_type": "open_app", "app_name": "Markor"}, "target": None, "before": [], "after": [{"text": "Markor"}]}]}
    record = _record(1, {"action_type": "open_app", "app_name": "Markor"}, [], [{"index": 0, "text": "Markor"}])
    report = audit.forward_validate(plan, {}, [record])
    assert report["scope"] == "source_trace_validation"
    assert report["task_success_evaluated"] is False
    assert report["counterfactual_ui_success"] is None
