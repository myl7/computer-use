import json
from types import SimpleNamespace
from guiexp_android import wait_provider_ready as w
from guiexp_android.budget_client import BudgetStop


def test_waits_without_model_and_launches_once_after_ready(tmp_path,monkeypatch):
    calls=[];launch=[];alerts=[]
    def check(*args):
        calls.append(args)
        if len(calls)==1:raise BudgetStop('Pinned provider endpoint is not active.')
    module=SimpleNamespace(load_spec=lambda p:{'model_locks':{'model':{}}},validated_metadata=check)
    monkeypatch.setattr(w,'DEFAULT_OUT',tmp_path);monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w.importlib,'import_module',lambda n:module)
    monkeypatch.setattr(w,'host_awake',lambda:True)
    monkeypatch.setattr(w.time,'sleep',lambda s:None)
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**kw:launch.append(a) or SimpleNamespace(pid=1234))
    monkeypatch.setattr(w,'dispatch',lambda *a:alerts.append(a))
    w.run(9)
    assert len(calls)==2 and len(launch)==1 and not alerts
    state=json.loads((tmp_path/'provider_wait.json').read_text())
    assert state['status']=='launched_guarded_runner' and state['model_calls']==0


def test_invalid_price_never_launches(tmp_path,monkeypatch):
    def check(*args):raise BudgetStop('Provider price exceeds the frozen ceiling.')
    module=SimpleNamespace(load_spec=lambda p:{'model_locks':{'model':{}}},validated_metadata=check)
    alerts=[]
    monkeypatch.setattr(w,'DEFAULT_OUT',tmp_path);monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setattr(w.importlib,'import_module',lambda n:module)
    monkeypatch.setattr(w,'host_awake',lambda:True)
    monkeypatch.setattr(w,'dispatch',lambda *a:alerts.append(a))
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('must not launch')))
    w.run(9)
    assert len(alerts)==1
    assert json.loads((tmp_path/'provider_wait.json').read_text())['status']=='provider_wait_needs_attention'
