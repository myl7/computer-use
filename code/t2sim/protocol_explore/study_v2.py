"""Declared second exploration round with fresh parameter/seed validation.

Round one remains frozen. V2 candidates were motivated by round-one synthetic
results, not by V2 outcomes. The old real-log results are paired checks on the
same logs, not fresh independently collected deployment data.
"""
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent))
import study as old
import engine_v2 as engine
ROOT=old.ROOT
OUT=ROOT/'experimental-results/guiexp/t2_sim_v3/protocol_explore_20260922_v2'
NEW=[p.name for p in engine.POLICIES]
ALL=old.ALGORITHMS+NEW


def freeze():
    prior=json.loads((old.OUT/'config.json').read_text());jobs=[]
    for spec in prior['jobs']:
        if spec['suite'] in ('development','validation_real','boundary'):
            s=dict(spec);s['cached_v1']=str(old.destination(old.OUT,spec).relative_to(ROOT));s['suite']='paired_'+spec['suite'];jobs.append(s)
        elif spec['suite']=='validation_grid':
            s=dict(spec);s['seed']=271039;s['n']=1800;s['families']=40
            if s['world']=='failed_price_5x':s['cf_scale']=3.
            if s['world']=='both_prices_5x':s['price_scale']=7.
            if s['world']=='high_drift':s['h']=.07
            if s['world']=='high_router':s['m']=910.
            s['suite']='fresh_grid';jobs.append(s)
    files=[Path(__file__),Path(engine.__file__),Path(old.__file__),Path(old.engine.__file__),old.OUT/'config.json']
    config=dict(schema='protocol-exploration/2',jobs=jobs,source_sha256={str(f.relative_to(ROOT)):old.sha(f) for f in files},
                policies=[vars(p) for p in engine.POLICIES],
                rationale='Round one found that lifetime-only optimism overspends on short/low-success families, while the conservative projected trigger delays favorable compilation. V2 combines the existing history-based opportunity estimate with a fixed-count optimistic admission bound. Once is a deliberately strong/simple falsification control, not claimed a general solution.',
                fresh_validation='300 cells with new seed, 1800 arrivals, 40 recurrent families, probabilities .95/.35 or mixture {0,.05,.3,.7,1}, failure-only scale3, both-price scale7, high hazard .07 and high routing910. World identifiers inherited from v1 are labels only; inspect actual parameter fields.',
                status='Exploratory second round. First-round synthetic outcomes informed the proposals; all fresh-grid results and failed candidates will be retained.')
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'cells').mkdir(exist_ok=True)
    p=OUT/'config.json';text=old.dump(config)
    if p.exists() and p.read_text()!=text:raise SystemExit('config differs')
    p.write_text(text);return config


def cell(spec):
    start=time.monotonic();rows={p:[] for p in ALL};hashes=[]
    cached=json.loads((ROOT/spec['cached_v1']).read_text()) if 'cached_v1' in spec else None
    for rep in range(spec['reps']):
        base=dict(spec)
        base['suite']=base['suite'].removeprefix('paired_')
        if base['suite']=='fresh_grid':base['suite']='validation_grid'
        arrivals,mapping,profiles=old.prepare(base,rep)
        if spec['suite']=='fresh_grid':
            rng=random.Random(930007+rep+int(hashlib.sha256(spec['key'].encode()).hexdigest()[:8],16))
            for row in profiles.values():
                if spec['admission']=='all_good':row['p']=.95
                elif spec['admission']=='half':row['p']=.35
                elif spec['admission']=='mixture':row['p']=rng.choice((0.,.05,.3,.7,1.))
        hashes.append({'stream':old.fingerprint(arrivals),'mapping':old.fingerprint(mapping),'profiles':old.fingerprint(profiles)})
        if cached:
            assert hashes[-1]==cached['input_hashes'][rep]
            for p in old.ALGORITHMS:rows[p].append(cached['rows'][p][rep])
        opts={k:spec[k] for k in ('ttl','k_min','h','m','tau0','silent','penalty','fallback_mult')}
        for p in (NEW if cached else ALL):
            runner=engine.run if p in NEW else old.reference.run if p in old.BASELINES else old.engine.run
            result=runner(arrivals,profiles,mapping,p,seed=spec['seed']*1000+rep,**opts)
            rows[p].append(old.compact(result))
    means={p:statistics.mean(r['cost'] for r in rs) for p,rs in rows.items()}
    best_base=min(means[p] for p in old.BASELINES);best_all=min(means.values());summary={}
    for p in ALL:
        unique=[]
        for v in sorted(v for v in means.values() if v<means[p]-1e-9*max(1,means['reactive'])):
            if not unique or v>unique[-1]+1e-9*max(1,means['reactive']):unique.append(v)
        prefixes=[r['prefix_ratio'] for r in rows[p] if r['prefix_ratio'] is not None]
        summary[p]=dict(mean_cost=means[p],ratio_agent=means[p]/means['reactive'],ratio_best_baseline=means[p]/best_base,
                        ratio_best_all=means[p]/best_all,rank=1+len(unique),max_prefix_ratio=max(prefixes) if prefixes else None)
    return dict(key=spec['key'],suite=spec['suite'],spec=spec,rows=rows,summary=summary,input_hashes=hashes,elapsed_s=time.monotonic()-start)


def summarize(config):
    cells=[json.loads(old.destination(OUT,s).read_text()) for s in config['jobs'] if old.destination(OUT,s).exists()]
    result=dict(completed=len(cells),expected=len(config['jobs']),suites={},cells=[dict(key=c['key'],suite=c['suite'],summary=c['summary']) for c in cells])
    for suite in sorted({c['suite'] for c in cells}):
        cs=[c for c in cells if c['suite']==suite];d={}
        for p in ALL:
            ratios=[c['summary'][p]['ratio_best_baseline'] for c in cs];newratios=[c['summary'][p]['ratio_best_all'] for c in cs]
            tail=sorted(ratios)[-max(1,__import__('math').ceil(len(cs)*.1)):]
            d[p]=dict(cells=len(cs),mean=statistics.mean(ratios),worst=max(ratios),p90=old.percentile(ratios,.9),tail=statistics.mean(tail),
                      near5=sum(r<=1.05 for r in ratios),near10=sum(r<=1.1 for r in ratios),near25=sum(r<=1.25 for r in ratios),
                      near10_best_all=sum(r<=1.1 for r in newratios),top2=sum(c['summary'][p]['rank']<=2 for c in cs))
        result['suites'][suite]=d
    (OUT/'summary.json').write_text(old.dump(result));return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--freeze',action='store_true');ap.add_argument('--run',action='store_true');ap.add_argument('--workers',type=int,default=6);ap.add_argument('--suite');args=ap.parse_args()
    config=freeze() if args.freeze else json.loads((OUT/'config.json').read_text())
    if args.freeze:print('Frozen',len(config['jobs']),'second-round conditions',flush=True)
    if args.run:
        for f,h in config['source_sha256'].items():
            assert old.sha(ROOT/f)==h,f
        todo=[s for s in config['jobs'] if (not args.suite or args.suite==s['suite']) and not old.destination(OUT,s).exists()]
        print('Pending',len(todo),flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending={pool.submit(cell,s):s for s in todo}
            for fut in as_completed(pending):
                s=pending[fut];r=fut.result();p=old.destination(OUT,s);tmp=p.with_suffix('.tmp');tmp.write_text(old.dump(r));tmp.replace(p)
                print('Completed',s['suite'],s['key'],f'({r["elapsed_s"]:.1f}s)',flush=True)
        r=summarize(config);print('Summary',r['completed'],'of',r['expected'],flush=True)

if __name__=='__main__':main()
