import json
from types import SimpleNamespace
import pytest
from guiexp_android.budget_client import BudgetStop
from guiexp_android.budget_client_v8 import BudgetClientV8,BudgetLedgerV8,validate_metadata
from guiexp_android.reconcile_saved_bills import reconcile
from guiexp_android.tests.test_budget_matched import MODEL,Response
from guiexp_android.tests.test_budget_matched_v2 import Clock
from guiexp_android.tests.test_budget_matched_v3 import SequenceSDK
from guiexp_android.tests.test_budget_matched_v4 import metadata
from guiexp_android.tests.test_budget_matched_v7 import Envelope

class Completion:
    def __init__(self,content=None,finish='length',cost=.00302939802):
        self.raw={'id':'gen-limit','choices':[{'message':{'role':'assistant','content':content},'finish_reason':finish}],
                  'usage':{'cost':cost,'prompt_tokens':21889,'completion_tokens':4096,'total_tokens':25985}}
        self.choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage=SimpleNamespace(**self.raw['usage'],prompt_tokens_details=None,model_extra={})
    def model_dump(self,mode):return self.raw

def client_for(tmp_path,outcomes):
    clock=Clock();ledger=BudgetLedgerV8(tmp_path/'budget.sqlite3',now=clock.now)
    client=BudgetClientV8(ledger,{MODEL:validate_metadata(MODEL,metadata(MODEL))},SequenceSDK(outcomes),metadata_fetcher=metadata,sleep=clock.sleep)
    client.begin_episode('fresh');return client,ledger

def ask(c):return c.create(model=MODEL,messages=[{'role':'user','content':'goal'}],temperature=0)

@pytest.mark.parametrize('content',[None,'','   '])
def test_length_limited_answer_is_billed_and_not_transport_retried(tmp_path,content):
    result=Completion(content);c,l=client_for(tmp_path,[result])
    assert ask(c) is result and len(c.sdk.calls)==1
    assert c.sdk.calls[0]['max_tokens']==4096
    assert l.summary()['actual_usd']=='0.003029399'
    assert l.summary()['unresolved_reserved_usd']=='0'

def test_original_agent_parser_gets_empty_reply_and_its_next_logical_call(tmp_path):
    from guiexp_android.agent import AndroidAgent
    from guiexp_android.actions import parse_action,ActionError
    c,l=client_for(tmp_path,[Completion(),Completion('action: {"action_type":"status","goal_status":"complete"}','stop',.001)])
    a=AndroidAgent(MODEL,client=c);obs={'url':'test','ax_tree_text':'screen'}
    text,usage=a.act('goal',obs)
    assert text=='' and usage['completion_tokens']==4096
    with pytest.raises(ActionError):parse_action(text)
    text,_=a.act(None,dict(obs,last_action_error='Reply must be an action'))
    parse_action(text)
    assert len(a.history)==4 and len(c.sdk.calls)==2
    with l.connect() as db:
        ids=[r[0] for r in db.execute('SELECT id FROM calls')]
    assert all('/attempt-1' in x for x in ids)

def test_explicit_provider_error_still_cannot_reach_agent(tmp_path):
    bad=Envelope();good=Completion('action: {"action_type":"status","goal_status":"complete"}','stop',.001)
    c,l=client_for(tmp_path,[bad,good]);assert ask(c) is good
    assert len(c.sdk.calls)==2 and l.summary()['unresolved_reserved_usd']=='0.09560064'

def test_billed_provider_error_is_charged_before_retry(tmp_path):
    bad=Envelope();bad.raw['usage']={'cost':.005}
    c,l=client_for(tmp_path,[bad,Completion('valid','stop',.001)])
    ask(c)
    assert l.summary()['actual_usd']=='0.006' and l.summary()['unresolved_reserved_usd']=='0'

def test_missing_bill_still_fails_closed_without_model_retry(tmp_path):
    c,l=client_for(tmp_path,[Completion(None,'length',None)])
    with pytest.raises(BudgetStop):ask(c)
    assert len(c.sdk.calls)==1 and l.summary()['unresolved_reserved_usd']=='0.09560064'

def test_missing_choices_remains_structurally_invalid(tmp_path):
    r=Completion();r.raw['choices']=[]
    c,l=client_for(tmp_path,[r])
    with pytest.raises(BudgetStop):ask(c)
    assert l.summary()['actual_usd']=='0.003029399'

def test_reconciliation_uses_only_saved_explicit_bills_and_keeps_original(tmp_path):
    c,l=client_for(tmp_path,[])
    for key,result in [('known',Completion()),('unknown',Completion(cost=None))]:
        l.reserve(key,key,MODEL,'h','.1');l.preserve_response(key,result.raw);l.uncertain(key,'prior_classifier');c.sleep(20)
    changes=reconcile(l.path);assert len(changes)==1 and changes[0]['id']=='known'
    assert reconcile(l.path)==[]
    with l.connect() as db:
        before=json.loads(db.execute('SELECT before_json FROM saved_bill_reconciliations').fetchone()[0])
        rows=db.execute('SELECT id,state,actual_nano,response_json FROM calls ORDER BY id').fetchall()
    assert before[5] is None and before[6]=='uncertain'
    assert rows[0][1:3]==('settled',3029399)
    assert rows[1][1:3]==('uncertain',None)
    assert json.loads(rows[0][3])==Completion().raw
