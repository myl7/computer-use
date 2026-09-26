from types import SimpleNamespace
from unittest.mock import patch

import pytest

from guiexp_android import recovery_validation_v3 as rv


class DummyBase:
    def __init__(self,*args,**kwargs):
        self.last_observation={"activity":"app","action_elements":[],"ax_forest":{},"screenshot_b64":"x"}
        self.events=[]
        self.actions=0

    def observe(self):
        return self.last_observation

    def execute(self,action):
        self.actions+=1
        return self.last_observation

    def _event(self,event,**values):
        self.events.append({"event":event,**values})


def test_public_completion_survives_generated_exception_catches():
    with patch('guiexp_android.recovery_validation_device.RecoveryDevice',DummyBase):
        cls=rv.strong_device_class()
        d=cls(expected_app='Files')
        d.contract=SimpleNamespace(check=lambda:True,progress=lambda:{"verified":True})
        with pytest.raises(rv.PublicGoalComplete):
            try:
                d.execute({"action_type":"click","index":0})
            except Exception:
                pytest.fail('Completion must not be swallowed by generic generated exception handling')
        assert d.actions==1


def test_normalization_does_not_recurse():
    calls=[]
    def normalize(device,app):
        calls.append(app)
        device.observe()
        return {"actions":[]}
    with patch('guiexp_android.recovery_validation_device.RecoveryDevice',DummyBase),patch(
            'guiexp_android.recovery_validation_controls.normalize_ui',normalize):
        cls=rv.strong_device_class()
        d=cls(expected_app='Files')
        d.contract=object()
        d.observe()
        assert calls==['Files']


def test_false_completion_gets_feedback_and_can_continue():
    replies=iter([
        {"choices":[{"message":{"content":'{"action":{"action_type":"status","goal_status":"complete"}}'}}]},
        {"choices":[{"message":{"content":'{"action":{"action_type":"status","goal_status":"infeasible"}}'}}]},
    ])
    seen=[]
    def response(*args):
        seen.append(args)
        return next(replies)
    d=DummyBase()
    d.contract=SimpleNamespace(check=lambda:False,progress=lambda:{"verified":False})
    with patch.object(rv,'model_call',response):
        assert rv.fallback(None,'model','goal',d,'episode',2) is False
    assert len(seen)==2
    assert 'completion_assertion_failed' in str(seen[1])
