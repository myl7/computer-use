"""Reconcile only explicit bills already present in preserved responses.

No API requests, inferred prices, or UI actions. Original row snapshots and
response hashes are recorded before a ledger-state transition.
"""
from __future__ import annotations
import argparse,hashlib,json,sqlite3,time
from pathlib import Path
from .budget_client import BudgetStop,nano_usd
from .matched_run import DEFAULT_OUT


def reconcile(path):
    changes=[]
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS saved_bill_reconciliations (call_id TEXT PRIMARY KEY, before_json TEXT NOT NULL, response_sha256 TEXT NOT NULL, actual_nano INTEGER NOT NULL, reconciled_unix REAL NOT NULL)')
        for row in db.execute("SELECT id,episode,model,request_sha,reserved_nano,actual_nano,state,response_json,error_type,created FROM calls WHERE state='uncertain' AND actual_nano IS NULL AND response_json IS NOT NULL").fetchall():
            try:
                raw=json.loads(row[7]);amount=nano_usd((raw.get('usage') or {}).get('cost'))
            except (ValueError,TypeError,BudgetStop):continue
            db.execute('INSERT INTO saved_bill_reconciliations VALUES (?,?,?,?,?)',(row[0],json.dumps(row),hashlib.sha256(row[7].encode()).hexdigest(),amount,time.time()))
            state='settled' if amount<=row[4] else 'overrun'
            db.execute('UPDATE calls SET actual_nano=?,state=? WHERE id=? AND state=\'uncertain\' AND actual_nano IS NULL',(amount,state,row[0]))
            changes.append({'id':row[0],'actual_nano':amount,'released_reservation_nano':row[4],'state':state})
    return changes


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    if not args.apply:raise SystemExit('Use --apply to record and reconcile explicit saved bills.')
    print(json.dumps(reconcile(DEFAULT_OUT/'budget.sqlite3'),indent=2))

if __name__=='__main__':main()
