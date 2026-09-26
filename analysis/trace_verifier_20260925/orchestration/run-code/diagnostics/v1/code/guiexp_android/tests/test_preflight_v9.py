import json
from pathlib import Path
import pytest
from guiexp_android.budget_client import BudgetStop
from guiexp_android.budget_client_v9 import BudgetClientV9,BudgetLedgerV9,validate_metadata
from guiexp_android.matched_run_v9 import recover_zero_call_preflights
from guiexp_android.tests.test_budget_matched import MODEL
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android.tests.test_budget_matched_v3 import SequenceSDK
from guiexp_android.tests.test_budget_matched_v4 import metadata
from guiexp_android.tests.test_budget_matched_v8 import Completion


def fixture(tmp_path,stop='Pinned provider endpoint is not active.',trace=''):
    l=BudgetLedgerV9(tmp_path/'budget.sqlite3');ep=tmp_path/'episodes/fresh';ep.mkdir(parents=True)
    state={'status':'budget_stopped','stop_reason':stop,'seed':123}
    (ep/'state.json').write_text(json.dumps(state));(ep/'trajectory.jsonl').write_text(trace)
    return l,ep,{'episodes':[{'id':'fresh'}]}

def test_only_zero_call_empty_preflight_can_restart(tmp_path):
    l,ep,s=fixture(tmp_path);original=(ep/'state.json').read_bytes()
    assert len(recover_zero_call_preflights(tmp_path,s,l))==1
    assert not (ep/'state.json').exists()
    archived=list((tmp_path/'preflight_restarts').rglob('state.json'))
    assert len(archived)==1 and archived[0].read_bytes()==original
    assert recover_zero_call_preflights(tmp_path,s,l)==[]

@pytest.mark.parametrize('evidence',['receipt','trajectory','screenshot','different_error'])
def test_never_replays_model_or_ui_evidence(tmp_path,evidence):
    l,ep,s=fixture(tmp_path,stop='different' if evidence=='different_error' else 'Pinned provider endpoint is not active.',trace='{}\n' if evidence=='trajectory' else '')
    if evidence=='receipt':l.reserve('already','fresh',MODEL,'h','.1')
    if evidence=='screenshot':(ep/'step_001.png').write_bytes(b'proof')
    assert recover_zero_call_preflights(tmp_path,s,l)==[]
    assert (ep/'state.json').exists()

def test_execution_nonce_changes_but_paid_episode_cannot_restart(tmp_path):
    clock=Clock();l=BudgetLedgerV9(tmp_path/'budget.sqlite3',now=clock.now)
    client=BudgetClientV9(l,{MODEL:validate_metadata(MODEL,metadata(MODEL))},SequenceSDK([Completion('action','stop',.001)]),metadata_fetcher=metadata,sleep=clock.sleep)
    client.begin_episode('fresh');first=client.execution_id
    client.begin_episode('fresh');assert first!=client.execution_id
    client.create(model=MODEL,messages=[{'role':'user','content':'goal'}],temperature=0)
    with pytest.raises(BudgetStop):client.begin_episode('fresh')
    with l.connect() as db:row=db.execute('SELECT id,episode FROM calls').fetchone()
    assert '/execution-'+client.execution_id+'/' in row[0] and row[1]=='fresh'
