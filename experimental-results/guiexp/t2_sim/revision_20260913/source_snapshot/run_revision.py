"""Run corrected, known-cost scenario simulations without model/API calls."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import random
import statistics
import time

import revision_sim as engine
import streams
import stats

ROOT=Path(__file__).resolve().parents[2]
TABLE=ROOT/'experimental-results/guiexp_android/t16_build/constants_table.json'
CONSTANTS=Path(__file__).with_name('constants.measured.v2.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def profiles_from_table(table, model, mode, scale=1.0):
    cells=[c for c in table['cells'] if c['model']==model]
    admitted=[c['headline'] for c in cells if c['headline']['admitted']]
    rejected=[c['headline'] for c in cells if not c['headline']['admitted']]
    median_C=statistics.median(c['C_with_repair'] for c in admitted)
    median_fail=statistics.median(c['C_with_repair'] for c in rejected)
    median_d=statistics.median(c['d'] for c in admitted if c['d'] is not None)
    median_q=statistics.median(c['q'] for c in admitted if c['q'] is not None)
    profiles={}
    for cell in cells:
        a=cell['headline'];ok=a['admitted']
        probs={'binary_replay':(1.0,0.0),'soft':(.8,.2),'uniform':(.5,.5)}
        p=probs[mode][0 if ok else 1]
        imputed=[]
        def field(name,value,fallback):
            if value is None:
                imputed.append(name)
                return fallback
            return value
        profiles[cell['family']]=dict(
            c=a['c'], d=field('d',a['d'],median_d),
            q=field('q',a['q'],median_q), pi=a['pi'], p=p,
            C=scale*field('C',a['C_with_repair'] if ok else None,median_C),
            C_fail=scale*field('C_fail',None if ok else a['C_with_repair'],median_fail),
            imputed=imputed,observed_admitted=ok,
            c_unsubtracted=a['c_unsubtracted'])
    return profiles


def make_config(reps,seed):
    table=json.loads(TABLE.read_text())
    old=json.loads(CONSTANTS.read_text())
    models=sorted(set(c['model'] for c in table['cells']))
    jobs=[]
    # Fixed before inspecting results. Native cells cover all modes/models.
    for model in models:
        for mode in ('binary_replay','soft','uniform'):
            profiles=profiles_from_table(table,model,mode)
            for stream in ('poisson','zipf','bursty','sepsis','bpi2019','wiki_A','wiki_B'):
                spec=dict(model=model,admission_mode=mode,stream=stream,
                          profiles=profiles,reps=reps,seed=seed,n=300,ttl=100,
                          h=.02,m=91.0,tau0=368.0,silent=0.0,scale=1.0,
                          k_min=3,policies=list(engine.POLICIES))
                if stream in old['streams']:
                    spec['stream_spec']=old['streams'][stream]
                spec['key']=f'{model}/{mode}/{stream}/base'
                jobs.append(spec)
        # Targeted sensitivity: change one deployment assumption at a time.
        for tag,changes in [('ttl25',{'ttl':25}),('ttl400',{'ttl':400}),
                            ('no_router',{'m':0.0,'tau0':0.0}),
                            ('no_drift',{'h':0.0}),('high_drift',{'h':.1}),
                            ('silent30',{'silent':.3}),('price5x',{'scale':5.0})]:
            for stream in ('bursty','bpi2019'):
                spec=dict(model=model,admission_mode='soft',stream=stream,
                          profiles=profiles_from_table(table,model,'soft',changes.get('scale',1)),
                          reps=reps,seed=seed,n=300,ttl=100,h=.02,m=91.0,
                          tau0=368.0,silent=0.0,scale=1.0,k_min=3,
                          policies=list(engine.POLICIES))
                spec.update(changes)
                if stream in old['streams']:spec['stream_spec']=old['streams'][stream]
                spec['key']=f'{model}/soft/{stream}/{tag}'
                jobs.append(spec)
    files=[Path(__file__),Path(engine.__file__),Path(streams.__file__),Path(stats.__file__),TABLE,CONSTANTS]
    for s in old['streams'].values():
        if isinstance(s,dict) and s.get('format')=='wiki_jsonl':files.append(ROOT/s['path'])
    files.append(streams.OLD_REAL_STREAMS)
    return dict(schema='gui-revision-sim/1',reps=reps,seed=seed,jobs=jobs,
                source_sha256={str(p.relative_to(ROOT)):digest(p) for p in files},
                assumptions=[
                    'Known measured/imputed costs and known scenario hazard; online arrival/admission estimates only.',
                    'Admission modes are assumptions, not estimates of independent build probability.',
                    'Serving costs use floor-adjusted PW estimates; d retains historical bill conversion uncertainty.',
                    'Family IDs and archived-artifact lookup are supplied without error; router fees are a scenario.',
                    'Serving precedes compilation; three completed reactive attempts (including fallback) are required.',
                    'All policies use free relisting and the same fixed idle TTL; no policy sees future arrivals.',
                    'Compilation coins are keyed by seed/family/attempt; service coins by seed/family/arrival/channel.',
                    'Cost mapping on real streams is shuffled across repetitions and paired across policies.',
                    'Bootstrap intervals include arrival/mapping/outcome variation, not uncertainty in measured costs.',
                    'Cap reserves the next failed-attempt cost, and is a heuristic without competitive guarantee.'
                ])


def cell(spec):
    start=time.monotonic()
    rows={p:[] for p in spec['policies']}
    stream_hashes=[];mapping_hashes=[]
    for rep in range(spec['reps']):
        names=sorted(spec['profiles'])
        if 'stream_spec' in spec:
            arrivals=streams.load_stream(spec['stream_spec'])
            if spec.get('quick'):arrivals=arrivals[:300]
            templates=list(names)
            random.Random(spec['seed']+rep).shuffle(templates)
            mapping={f:templates[i%len(templates)] for i,f in enumerate(sorted(set(arrivals)))}
        else:
            arrivals=streams.gen_stream(spec['stream'],spec['n'],names,random.Random(spec['seed']*1000+rep))
            mapping={f:f for f in names}
        stream_hashes.append(hashlib.sha256(json.dumps(arrivals).encode()).hexdigest())
        mapping_hashes.append(hashlib.sha256(json.dumps(mapping,sort_keys=True).encode()).hexdigest())
        for policy in spec['policies']:
            result=engine.run(arrivals,spec['profiles'],mapping,policy,
                              seed=spec['seed']*1000+rep,
                              **{k:spec[k] for k in ('ttl','h','m','tau0','silent','k_min')})
            rows[policy].append(result)
    summary={}
    baseline=[r['tokens'] for r in rows['reactive']]
    for policy,runs in rows.items():
        costs=[r['tokens'] for r in runs]
        delta=[r['success_rate']-b['success_rate'] for r,b in zip(runs,rows['reactive'])]
        summary[policy]=dict(mean_tokens=stats.mean(costs),
             ratio_to_reactive=stats.mean(costs)/stats.mean(baseline),
             ratio_ci95=stats.bootstrap_ratio_ci(costs,baseline,B=2000,seed=spec['seed']),
             success_rate=stats.mean([r['success_rate'] for r in runs]),
             success_delta_ci95=stats.bootstrap_mean_ci(delta,B=2000,seed=spec['seed']),
             mean_failed_compile_tokens=stats.mean([r['failed_compile_tokens'] for r in runs]),
             mean_attempts=stats.mean([r['attempts'] for r in runs]))
    return dict(spec=spec,rows=rows,summary=summary,
                stream_summary=streams.stream_summary(arrivals),
                stream_sha256=stream_hashes,mapping_sha256=mapping_hashes,
                wall_s=time.monotonic()-start)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',required=True)
    ap.add_argument('--reps',type=int,default=20)
    ap.add_argument('--seed',type=int,default=20260913)
    ap.add_argument('--jobs',type=int,default=6)
    ap.add_argument('--quick',action='store_true')
    args=ap.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    config=make_config(args.reps,args.seed)
    if args.quick:
        config['jobs']=config['jobs'][:3]
        for j in config['jobs']:j.update(quick=True,n=30,reps=2)
    encoded=json.dumps(config,indent=2,sort_keys=True)+'\n'
    path=out/'config.json'
    if path.exists() and path.read_text()!=encoded:
        raise SystemExit('Refusing to reuse an output directory with different source/configuration.')
    path.write_text(encoded)
    work=[]
    for spec in config['jobs']:
        dest=out/(hashlib.sha256(spec['key'].encode()).hexdigest()[:20]+'.json')
        if not dest.exists():work.append((spec,dest))
    print(f'{len(work)} pending cells of {len(config["jobs"])}; no API calls',flush=True)
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        pending={pool.submit(cell,spec):(spec,dest) for spec,dest in work}
        for future in as_completed(pending):
            spec,dest=pending[future]
            result=future.result()
            temp=dest.with_suffix('.tmp')
            temp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
            temp.replace(dest)
            print(f'completed {spec["key"]} ({result["wall_s"]:.1f}s)',flush=True)
    report=[]
    for spec in config['jobs']:
        dest=out/(hashlib.sha256(spec['key'].encode()).hexdigest()[:20]+'.json')
        data=json.loads(dest.read_text())
        report.append(dict(key=spec['key'],summary=data['summary'],stream=data['stream_summary']))
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'Complete: {len(report)} cells. Summary: {out / "summary.json"}',flush=True)


if __name__=='__main__':main()
