"""Evaluate all V2 variants on every original validation-grid condition.

This is an explicitly retrospective stress-panel crosscheck, not independent
validation. It prevents the changed V2 grid from concealing older hard cases.
"""
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import study as old
import study_v2 as new
OUT=new.OUT/'original_grid_crosscheck'
config=json.loads((old.OUT/'config.json').read_text())
jobs=[]
for spec in config['jobs']:
    if spec['suite']=='validation_grid':
        s=dict(spec);s['suite']='paired_validation_grid';s['cached_v1']=str(old.destination(old.OUT,spec).relative_to(old.ROOT));jobs.append(s)
OUT.mkdir(exist_ok=True);(OUT/'cells').mkdir(exist_ok=True)
manifest=dict(status='Retrospective full-grid crosscheck after V2 candidate freeze. No policy or parameter changes. Retain all 300 cases, including unfavorable original high-failure-cost settings.',jobs=jobs,engine_sha256=old.sha(Path(new.engine.__file__)))
p=OUT/'config.json';encoded=old.dump(manifest)
if p.exists() and p.read_text()!=encoded:raise SystemExit('Frozen crosscheck differs')
p.write_text(encoded)
if __name__=='__main__':
    todo=[s for s in jobs if not old.destination(OUT,s).exists()]
    with ProcessPoolExecutor(max_workers=6) as pool:
        pending={pool.submit(new.cell,s):s for s in todo}
        for future in as_completed(pending):
            s=pending[future];result=future.result();old.destination(OUT,s).write_text(old.dump(result));print('Completed',s['key'],flush=True)
    print('Complete',len(jobs),flush=True)
