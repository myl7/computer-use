"""Mock-only request, billing and protocol checks. No production ledger access."""
import json
from unittest.mock import patch
import pytest
from guiexp_android import recovery_validation_budget_v6 as v6
from guiexp_android.tests.test_recovery_validation_budget_v4 import (
    make_locks, receipt, OfflineTransport, GLM, MESSAGES,
)


def client(tmp_path, response=None):
    locks = make_locks()
    for lock in locks.values():
        lock['endpoints'][0]['supported_parameters'] = ['response_format', 'structured_outputs']
        lock['endpoints'][1]['supported_parameters'] = ['response_format']
    ledger = v6.base.BudgetLedger(tmp_path / 'offline.sqlite3')
    transport = OfflineTransport(response)
    return v6.StructuredActionClient(ledger, locks, tmp_path / 'unused.env', transport), ledger, transport


def call(c):
    with patch.object(v6, '_load_api_key', return_value='offline-no-real-key'):
        return c.complete_action(GLM, MESSAGES, 512, 'offline')


def valid_response():
    r = receipt()
    r['choices'][0]['message']['content'] = json.dumps({'reason': 'Wait', 'action': {'action_type': 'wait'}})
    return r


def test_schema_precedes_hash_reservation_and_preserves_full_ledger_pool(tmp_path):
    c, ledger, transport = client(tmp_path, valid_response())
    result = call(c)
    payload = transport.calls[0][1]
    assert payload['response_format']['json_schema']['strict'] is True
    assert payload['provider']['only'] == ['example-a']
    assert payload['provider']['require_parameters'] is True
    record = ledger.records()[0]
    assert record['request_hash'] == v6._hash(v6._json(payload))
    assert json.loads(record['request_json'])['response_format'] == payload['response_format']
    assert record['state'] == 'settled'
    assert len(c.locks[GLM]['endpoints']) == 2
    assert result['reply']['action']['action_type'] == 'wait'


def test_malformed_reply_is_billed_without_retry(tmp_path):
    c, ledger, transport = client(tmp_path)
    with pytest.raises(v6.ActionReplyError) as e:
        call(c)
    assert e.value.billed
    assert ledger.records()[0]['state'] == 'settled'
    assert len(transport.calls) == 1


def test_unknown_bill_stops_and_retains_reservation(tmp_path):
    r = valid_response()
    del r['usage']['cost']
    c, ledger, transport = client(tmp_path, r)
    with pytest.raises(v6.BillingError):
        call(c)
    assert ledger.summary()['blocked']
    assert ledger.records()[0]['state'] != 'settled'
    assert len(transport.calls) == 1


def test_unstructured_compilation_remains_available(tmp_path):
    c, ledger, transport = client(tmp_path)
    with patch.object(v6.base, '_load_api_key', return_value='offline-no-real-key'):
        c.complete(GLM, MESSAGES, 512, 'compile')
    assert 'response_format' not in transport.calls[0][1]


@pytest.mark.parametrize('action', [
    {'action_type': 'click', 'index': True},
    {'action_type': 'click', 'index': -1},
    {'action_type': 'wait', 'extra': 1},
    {'action_type': 'status', 'goal_status': 'unknown'},
])
def test_local_schema_rejects_invalid_actions(action):
    r = valid_response()
    r['choices'][0]['message']['content'] = json.dumps({'reason': '', 'action': action})
    with pytest.raises(v6.ActionReplyError):
        v6.parse_action_reply(r)


def test_unsupported_pool_fails_before_reserve(tmp_path):
    c, ledger, transport = client(tmp_path)
    for lock in c.locks.values():
        for endpoint in lock['endpoints']:
            endpoint['supported_parameters'] = []
    c._locks_hash = v6._hash(v6._json(c.locks))
    with pytest.raises(v6.QuoteError, match='No frozen endpoint'):
        call(c)
    assert not ledger.records()
    assert not transport.calls


def test_schema_failure_on_length_is_billed(tmp_path):
    response = valid_response()
    response['choices'][0]['finish_reason'] = 'length'
    c, ledger, transport = client(tmp_path, response)
    with pytest.raises(v6.ActionReplyError):
        call(c)
    assert ledger.records()[0]['state'] == 'settled'
    assert len(transport.calls) == 1
