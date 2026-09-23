"""Frozen development and validation study for protocol exploration.

All configurations are written before any policy evaluation. Paper files and
previous simulations are read-only. These are offline synthetic simulations.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import revision_sim as reference
import run_paper_revision as profiles_reader
import streams
sys.path.insert(0,str(Path(__file__).resolve().parent))
import engine

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'experimental-results/guiexp/t2_sim_v3/protocol_explore_20260922'
OLD=ROOT/'experimental-results/guiexp/t2_sim_v3/paper_revision_20260922'
CANDIDATES=[p.name for p in engine.POLICIES]
BASELINES=list(reference.POLICIES)
ALGORITHMS=BASELINES+CANDIDATES
COMPONENTS=('reactive_tokens','extraction_tokens','compile_tokens','router_tokens')
MODELS=profiles_reader.MODELS


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(x):return json.dumps(x,indent=2,sort_keys=True,allow_nan=False)+'\n'
def fingerprint(x):return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()
def destination(out,spec):return out/'cells'/(hashlib.sha256(spec['key'].encode()).hexdigest()[:20]+'.json')


def freeze(out):
    old=json.loads((OLD/'config.json').read_text())
    table=json.loads(profiles_reader.MEASUREMENT.read_text())
    base_profiles={model:profiles_reader.profiles_from_measurement(table,model,'soft') for model in MODELS}
    jobs=[]
    # Known development set. Its measurements/results were inspected before
    # this task. It is not presented as independent validation.
    for spec in old['jobs']:
        if spec['tag']=='base' and spec['stream'] in ('poisson','zipf','bursty'):
            job=dict(spec)
            job.update(suite='development',reps=10,algorithms=CANDIDATES,
                       cached_reference=str(profiles_reader.cell_path(OLD,spec).relative_to(ROOT)),
                       key='development/'+spec['key'])
            jobs.append(job)
    worlds={
        'default':{},
        'failed_price_5x':{'cf_scale':5.},
        'both_prices_5x':{'price_scale':5.},
        'high_drift':{'h':.1},
        'high_router':{'m':1820.},
    }
    for model in MODELS:
        for admission in ('all_good','half','mixture','all_bad'):
            for pattern in ('poisson','zipf','bursty','regime_shift','sparse'):
                for world,change in worlds.items():
                    job=dict(suite='validation_grid',model=model,admission=admission,
                             pattern=pattern,world=world,profiles=base_profiles[model],
                             n=1200,families=30,reps=8,seed=71023,ttl=100,k_min=3,
                             h=.02,m=91.,tau0=368.,silent=0.,penalty=3.,fallback_mult=1.,
                             price_scale=1.,cf_scale=1.,algorithms=ALGORITHMS)
                    job.update(change)
                    job['key']=f'validation_grid/{model}/{admission}/{pattern}/{world}'
                    jobs.append(job)
    # Real sequence structure with fresh seeds and independently assigned p.
    # Reusing the same underlying logs is declared, not called new real data.
    for model in MODELS:
        for pattern in ('sepsis','bpi2019','wiki_A','wiki_B'):
            source=next(s for s in old['jobs'] if s['model']==model and s['tag']=='base' and s['stream']==pattern)
            jobs.append(dict(suite='validation_real',model=model,admission='mixture',
                             pattern=pattern,profiles=base_profiles[model],stream_spec=source['stream_spec'],
                             n=None,reps=10,seed=92033,ttl=100,k_min=3,h=.02,m=91.,tau0=368.,
                             silent=0.,penalty=3.,fallback_mult=1.,price_scale=1.,cf_scale=1.,
                             algorithms=ALGORITHMS,key=f'validation_real/{model}/{pattern}'))
    # Boundary cases are reported separately, not mixed into broad-domain
    # averages. They explicitly test the safety-versus-delay impossibility.
    for n in (3,10,30,100,300):
        for buy_ratio in (.25,2.,10.,100.):
            for failure_ratio in (1.,10.,100.):
                for p in (0.,1.):
                    for hazard in (0.,.1):
                        profile=dict(c=1.,d=0.,C=buy_ratio,C_fail=buy_ratio*failure_ratio,
                                     p=p,q=0.,pi=1.)
                        key=f'boundary/n{n}/C{buy_ratio}/F{failure_ratio}/p{p}/h{hazard}'
                        jobs.append(dict(suite='boundary',key=key,profiles={'only':profile},n=n,
                                         reps=3,seed=120047,ttl=100,k_min=3,h=hazard,m=0.,tau0=0.,
                                         silent=0.,penalty=3.,fallback_mult=1.,algorithms=ALGORITHMS))
    sources=[Path(__file__),Path(engine.__file__),Path(reference.__file__),
             Path(profiles_reader.__file__),Path(streams.__file__),profiles_reader.MEASUREMENT,
             OLD/'config.json',Path(__file__).with_name('test_engine.py')]
    config=dict(schema='protocol-exploration/1',jobs=jobs,policies=[vars(p) for p in engine.POLICIES],
                baselines=BASELINES,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},
                definitions={
                    'scope':'Exploratory protocol research only. No paper writes, new model calls, or changes to old results.',
                    'safety':'Prefix pure-token cost <= (1+epsilon) sum(c_f+tau0), conditional on the supplied deterministic cost profiles. Hidden penalty is excluded.',
                    'robustness':'Also report ratios to best fixed and all eight original online baselines. Agent safety alone does not satisfy near-optimality.',
                    'top_two':'Rank by distinct mean costs, treating differences <=1e-9 of baseline cost as numerical ties. Report near-best 5/10/25% coverage separately.',
                    'development':'27 existing synthetic cells, first 10 saved paired repetitions. Known development evidence, not held-out data.',
                    'validation_grid':'300 fresh scenario cells. Gate probabilities are assigned independently of cost profiles; no original admission flags are exposed to policies.',
                    'validation_real':'12 fresh mapping/outcome settings on existing real-log sequences. New realizations, not independent newly collected logs.',
                    'boundary':'240 single-family cost/horizon cases, reported separately.',
                    'quality':'Detected-failure quality is protected by the inherited simulator construction. No live quality claim.',
                    'optimism':'A fixed-count one-sided 90% Clopper-Pearson upper bound and optimistic program lifetime. Not a time-uniform confidence or regret guarantee.',
                    'selection':'All ten predefined candidates and all eight original baselines are retained. No winner is silently selected or tuned on validation.',
                },reps_by_suite={'development':10,'validation_grid':8,'validation_real':10,'boundary':3})
    path=out/'config.json';out.mkdir(parents=True,exist_ok=True);(out/'cells').mkdir(exist_ok=True)
    encoded=dump(config)
    if path.exists() and path.read_text()!=encoded:raise SystemExit('Frozen study differs; use a new version directory.')
    path.write_text(encoded)
    return config


def prepare(spec,rep):
    if spec['suite']=='development':return profiles_reader.prepare_rep(spec,rep)+(spec['profiles'],)
    if spec['suite']=='boundary':return ['only']*spec['n'],{'only':'only'},spec['profiles']
    # The scenario key contributes reproducibly to generation seeds.
    salt=int(hashlib.sha256(spec['key'].encode()).hexdigest()[:8],16)
    rng=random.Random(spec['seed']*1000+rep+salt)
    if spec['suite']=='validation_real':
        arrivals=streams.load_stream(spec['stream_spec'])
    elif spec['pattern']=='sparse':
        arrivals=[];i=0
        while len(arrivals)<spec['n']:
            count=rng.choice((1,1,2,2,3,4,6))
            arrivals.extend([f'f{i:05d}']*count);i+=1
        arrivals=arrivals[:spec['n']]
        rng.shuffle(arrivals)
    else:
        families=[f'f{i:03d}' for i in range(spec['families'])]
        arrivals=streams.gen_stream(spec['pattern'],spec['n'],families,rng)
    names=sorted(spec['profiles']);rng.shuffle(names)
    profiles={};mapping={}
    for i,family in enumerate(sorted(set(arrivals))):
        original=spec['profiles'][names[i%len(names)]]
        row={k:original[k] for k in ('c','d','C','C_fail','q','pi')}
        row['C']*=spec['price_scale'];row['C_fail']*=spec['price_scale']*spec['cf_scale']
        mode=spec['admission']
        row['p']=.9 if mode=='all_good' else .5 if mode=='half' else 0. if mode=='all_bad' else rng.choice((0.,.1,.5,.9,1.))
        profiles[family]=row;mapping[family]=family
    return arrivals,mapping,profiles


def compact(raw):
    pure=sum(raw[k] for k in COMPONENTS)
    if 'token_cost' in raw and not math.isclose(pure,raw['token_cost'],rel_tol=1e-10,abs_tol=1e-7):raise ValueError('bad account')
    return dict(cost=pure,quality=raw['success_rate'],penalty=raw['harm_tokens'],
                prefix_ratio=raw.get('max_prefix_ratio'),attempts=raw['attempts'],
                failed_spend=raw['failed_compile_tokens'],components={k:raw[k] for k in COMPONENTS},
                safety_rejections=raw.get('safety_compile_rejections',0))


def cell(spec):
    start=time.monotonic();rows={p:[] for p in ALGORITHMS};hashes=[]
    cached=None
    if spec['suite']=='development':cached=json.loads((ROOT/spec['cached_reference']).read_text())
    for rep in range(spec['reps']):
        arrival,mapping,profiles=prepare(spec,rep)
        hashes.append({'stream':fingerprint(arrival),'mapping':fingerprint(mapping),'profiles':fingerprint(profiles)})
        if cached is not None:
            assert hashes[-1]['stream']==cached['stream_sha256'][rep]
            assert hashes[-1]['mapping']==cached['mapping_sha256'][rep]
            for policy in BASELINES:rows[policy].append(compact(cached['rows'][policy][rep]['engine']))
        opts={k:spec[k] for k in ('ttl','k_min','h','m','tau0','silent','penalty','fallback_mult')}
        for policy in spec['algorithms']:
            runner=reference.run if policy in BASELINES else engine.run
            raw=runner(arrival,profiles,mapping,policy,seed=spec['seed']*1000+rep,**opts)
            rows[policy].append(compact(raw))
    assert all(len(v)==spec['reps'] for v in rows.values())
    means={p:statistics.mean(r['cost'] for r in rs) for p,rs in rows.items()}
    best_fixed=min(means[p] for p in ('reactive','earliest'))
    best_baseline=min(means[p] for p in BASELINES)
    best_all=min(means.values())
    result={}
    for p in ALGORITHMS:
        lower=sorted(v for v in means.values() if v<means[p]-1e-9*max(1,means['reactive']))
        unique=[]
        for v in lower:
            if not unique or v>unique[-1]+1e-9*max(1,means['reactive']):unique.append(v)
        pr=[r['prefix_ratio'] for r in rows[p] if r['prefix_ratio'] is not None]
        result[p]=dict(mean_cost=means[p],ratio_agent=means[p]/means['reactive'],
                       ratio_best_fixed=means[p]/best_fixed,ratio_best_baseline=means[p]/best_baseline,
                       ratio_best_all=means[p]/best_all,rank=1+len(unique),
                       mean_quality=statistics.mean(r['quality'] for r in rows[p]),
                       max_prefix_ratio=max(pr) if pr else None)
    return dict(key=spec['key'],suite=spec['suite'],spec=spec,rows=rows,summary=result,
                input_hashes=hashes,elapsed_s=time.monotonic()-start)


def percentile(v,p):
    s=sorted(v);return s[round((len(s)-1)*p)]


def summarize(out,config,partial=False):
    cells=[]
    for spec in config['jobs']:
        p=destination(out,spec)
        if p.exists():cells.append(json.loads(p.read_text()))
        elif not partial:raise ValueError('missing '+spec['key'])
    suites={}
    for suite in ['development','validation_grid','validation_real','boundary']:
        cs=[c for c in cells if c['suite']==suite]
        if not cs:continue
        data={}
        for policy in ALGORITHMS:
            ratios=[c['summary'][policy]['ratio_best_baseline'] for c in cs]
            allratios=[c['summary'][policy]['ratio_best_all'] for c in cs]
            ordered=sorted(ratios);tail=ordered[-max(1,math.ceil(.1*len(ordered))):]
            prefixes=[c['summary'][policy]['max_prefix_ratio'] for c in cs if c['summary'][policy]['max_prefix_ratio'] is not None]
            worst=max(cs,key=lambda c:c['summary'][policy]['ratio_best_baseline'])
            data[policy]=dict(cells=len(cs),mean_ratio_best_baseline=statistics.mean(ratios),
                              worst_ratio_best_baseline=max(ratios),p90_ratio_best_baseline=percentile(ratios,.9),
                              worst_decile_mean=statistics.mean(tail),
                              near5=sum(r<=1.05 for r in ratios),near10=sum(r<=1.10 for r in ratios),
                              near25=sum(r<=1.25 for r in ratios),
                              near10_best_all=sum(r<=1.10 for r in allratios),
                              top2=sum(c['summary'][policy]['rank']<=2 for c in cs),
                              mean_ratio_agent=statistics.mean(c['summary'][policy]['ratio_agent'] for c in cs),
                              max_prefix_ratio=max(prefixes) if prefixes else None,
                              worst_cell=worst['key'])
        suites[suite]=data
    report=dict(completed=len(cells),expected=len(config['jobs']),suites=suites,
                cells=[dict(key=c['key'],suite=c['suite'],summary=c['summary']) for c in cells])
    (out/('partial_summary.json' if partial else 'summary.json')).write_text(dump(report))
    return report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--freeze',action='store_true');ap.add_argument('--run',action='store_true')
    ap.add_argument('--suite',choices=['development','validation_grid','validation_real','boundary']);ap.add_argument('--workers',type=int,default=6)
    ap.add_argument('--summary',action='store_true');args=ap.parse_args()
    if args.freeze:
        cfg=freeze(OUT);print('Frozen',len(cfg['jobs']),'cells',flush=True)
    else:cfg=json.loads((OUT/'config.json').read_text())
    if args.run:
        for f,h in cfg['source_sha256'].items():
            if sha(ROOT/f)!=h:raise SystemExit('Frozen source changed: '+f)
        work=[s for s in cfg['jobs'] if (not args.suite or s['suite']==args.suite) and not destination(OUT,s).exists()]
        print('Pending',len(work),flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(cell,s):s for s in work}
            for future in as_completed(futures):
                s=futures[future];r=future.result();p=destination(OUT,s);tmp=p.with_suffix('.tmp');tmp.write_text(dump(r));tmp.replace(p)
                print('Completed',s['key'],f'({r["elapsed_s"]:.1f}s)',flush=True)
    if args.summary or args.run:
        r=summarize(OUT,cfg,partial=any(not destination(OUT,s).exists() for s in cfg['jobs']))
        print('Summary',r['completed'],'of',r['expected'],flush=True)

if __name__=='__main__':main()
