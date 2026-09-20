"""Reproduce the September 18 measurement tables without model or device calls.

Run from any directory. --check also compares the rendered rows with body.tex.
The older number generators retain their historical Android-only scope.

A third model layer, qwen/qwen3.8-flash (measured 2026-09-20, Android no-task
floor calibrated 2026-09-21 in t12_grid), exists since
2026-09-20: its six rows (4 AndroidWorld + 2 OSWorld; the WebArena cell
provider-refused before producing stage data and has no row) are always
computed from the raw cell records and written under the top-level
`qwen_rows` / `aggregate['qwen/qwen3.8-flash']` / `qwen_disclosures` /
`qwen_android_floor` keys.
The body.tex tables and every legacy count remain glm/ds-only; the qwen
body/table integration is PENDING (--include-qwen previews a merged `rows`
array in the JSON only).  --check-qwen pins the qwen rows against frozen
literals.

The triple renderer (2026-09-21) prepares the body integration: the qwen
t19/t20 verification halves (`qwen_repeated_and_extraction`) and t21 paired
halves (`qwen_paired_replays`) are computed from the same raw records, and
`latex_rows_qwen` / `qw_halves` / `latex_triples` carry the THIRD half for
Tables 1/2/3/4 under the established cell conventions (program-outcome
cells masked for non-admitted rows; single-model color cells where a model
half is absent).  --check-qwen-rows freezes the qwen half-strings against
literals so the body edit can be diffed against them; --check still
compares the glm/ds dual rows only.
"""
from pathlib import Path
import json, statistics, argparse, hashlib
parser=argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
parser.add_argument('--output', type=Path, default=Path(__file__).with_suffix('.json'))
parser.add_argument('--check', action='store_true')
parser.add_argument('--include-qwen', action='store_true',
 help='also merge the qwen rows into the JSON `rows` array (sorted). '
 'OFF by default: the JSON always carries qwen_rows regardless, the '
 'body.tex comparison stays glm/ds, and the rendered latex tables are '
 'unaffected (body/table integration pending).')
parser.add_argument('--check-qwen', action='store_true',
 help='assert internal consistency of the qwen layer only: recompute the '
 'rows from the same raw sources and compare them against frozen literals.')
parser.add_argument('--check-qwen-rows', action='store_true',
 help='assert the rendered qwen half-strings of Tables 1/2/3/4 (share, '
 'price, verification, paired) against frozen literals, so the pending '
 'body.tex triple edit can be diffed against them.  Does NOT touch --check, '
 'which keeps comparing the glm/ds dual rows only.')
args=parser.parse_args()
ROOT=args.root.resolve()
OLD=ROOT/'experimental-results/guiexp_android/t16_build/constants_table.json'
old=json.loads(OLD.read_text()); prices=old['price_sheet']
keep={'ContactsAddContact','MarkorDeleteNote','SimpleCalendarAddOneEvent','OsmAndMarker'}
def pw(x,m):
 p=prices[m];return (x.get('prompt_tokens') or 0)-(1-p['p_c']/p['p_in'])*(x.get('cached_tokens') or 0)+(p['p_o']/p['p_in'])*(x.get('completion_tokens') or 0)
def ratio(a,b):return a/b if a is not None and b is not None and b>0 else None
rows=[]
for x in old['cells']:
 if x['family'] not in keep:continue
 h=x['headline'];g=x['gate'];c=h['c'];C=h['C_with_repair'];s=h['s_arrival'];f=h['floor_pw'];n=x['exploration']['attempts']
 rows.append(dict(platform='Android',model=x['model'],family=x['family'],source=str(OLD.relative_to(ROOT)),c=c,L_doc=h['L_doc'],d=h['d'],q=h['q'],C=C,floor=f,doc_share=h['share_doc'],program_share=h['share_prog'],nstar=ratio(C,s),nstar_incl_3c=ratio(C+3*c,s),nstar_incl_observed_floored=ratio(C+h['exploration_total']-n*f,s),n_exploration_episodes=n,agent_successes=x['exploration']['successes'],doc_successes=x['doc_arm']['successes'],admitted=h['admitted'],gates=[g[f'gate_k{k}_passed'] for k in (1,2,3)],final_gate=g['p_headline']*5 if g.get('p_headline') is not None else None,refinements=x['verification']['refinements'],cost_counts_limitation='Exploration cached counts imputed for reused episodes where missing; deployment uses recorded bill divided by fresh-input price.',cache_imputed_episodes=x['exploration']['reused_episodes_cache_imputed'],cache_imputation_share=x['exploration']['reused_cache_share_used']))
def desktop_web_row(platform,path):
  """The 2026-09-18 Desktop/Web mapping, shared verbatim by the glm/ds loop
  below and by the qwen WriterMemoSave row. Reads path's build.json (and its
  sibling deploy.json when admitted); returns one row dict."""
  x=json.loads(path.read_text());m=x['model'];n=x['exploration']['totals']['episodes'];f=statistics.mean(pw(u,m) for u in x['floor']['per_run']);et=pw(x['exploration']['totals'],m);c=et/n-f;doc=x['doc_arm'];ld=pw(doc['totals'],m)/len(doc['episodes'])-f;v=x['verification'];vt=v['totals'];sel=x['builder']['selected_arm'];init=x['builder']['initial'][f"k{sel['k']}_{sel['artifact']}"]['usage'];Cparts={'translator':pw(x['translator']['totals'],m),'builder_initial':pw(init,m),'builder_refinements':pw(vt['builder_refinements'],m),'verification_analyzer':pw(vt['analyzer'],m),'verification_resume':pw(vt['resume_episodes'],m)};C=sum(Cparts.values());dp=path.with_name('deploy.json');d=q=None;dn=ds=None
  if v['admitted']:
   de=json.loads(dp.read_text()); assert all(u.get('calls_detail') for u in de['uses']);d=statistics.mean(sum(pw(call,m) for call in u['calls_detail']) for u in de['uses']);dn=de['n'];ds=de['success_count'];q=1-ds/dn
  s=(1-q)*c-d if q is not None else None
  return dict(platform=platform,model=m,family=x['family'],source=str(path.relative_to(ROOT)),deploy_source=str(dp.relative_to(ROOT)) if v['admitted'] else None,c=c,L_doc=ld,d=d,q=q,C=C,C_parts=Cparts,floor=f,doc_share=(c-ld)/c,program_share=ratio(s,c),nstar=ratio(C,s),nstar_incl_3c=ratio(C+3*c,s),nstar_incl_observed_floored=ratio(C+et-n*f,s),n_exploration_episodes=n,agent_successes=sum(bool(a.get('success')) for inst in x['exploration']['instances'] for a in inst['attempts']),doc_successes=doc['success_count'],deploy_n=dn,deploy_successes=ds,admitted=v['admitted'],gates=[x['gate_per_k'][f'k{k}']['bindings_passed'] for k in (1,2,3)],final_gate=v['gate']['bindings_passed'],refinements=v['refinements'],original_port_break_even=x['break_even'],cost_counts_limitation='Recomputed provider-counts unit; no cold-host cache imputation; original port break_even values deliberately not used. C excludes exploration and unselected k/code/document builder arms, includes selected code refinement and all logged analyzer/resume calls. Interrupted lost calls absent.')
for platform,dirname in [('Desktop','guiexp_osworld'),('Web','guiexp_webarena')]:
 for path in sorted((ROOT/'experimental-results'/dirname).glob('*/*/build.json')):
  if 'mock' in str(path):continue
  if json.loads(path.read_text())['model'] not in prices:continue
  rows.append(desktop_web_row(platform,path))
rows.sort(key=lambda r:(r['model'],['Android','Desktop','Web'].index(r['platform']),r['family']))
ag={}
for m in prices:
 cells=[r for r in rows if r['model']==m];ok=[r for r in cells if r['admitted']];bad=[r for r in cells if not r['admitted']]
 ag[m]={'admitted':len(ok),'total':len(cells),'C_admitted_median':statistics.median(r['C'] for r in ok),'C_rejected_median':statistics.median(r['C'] for r in bad) if bad else None,'nstar_range':[min(r['nstar'] for r in ok),max(r['nstar'] for r in ok)],'nstar_incl_3c_range':[min(r['nstar_incl_3c'] for r in ok),max(r['nstar_incl_3c'] for r in ok)]}
out={'definition':{'pw':'(prompt-cached)+(p_c/p_in)*cached+(p_o/p_in)*completion; exact price ratios from existing Android table','c':'exploration total including all retry and reflection calls / number of exploration episodes, minus measured platform/model floor','C':'translator + selected k3 code builder + refinements + analyzer + resume. Resume is not floored, matching Android C_with_repair','inclusive':'Two definitions supplied: C+3c (current paper caption) and C+observed exploration total minus one floor per episode. Do not conflate when there are retries.','platform_scope':'OSWorld custom parameterized Calc/Writer tasks in its environment, WebArena custom CommentPost on self-hosted Reddit, AndroidWorld four retained native families','new_floor':'Recomputed as mean pw(per_run) from each build floor; preserves per-cell GLM desktop calibration, DeepSeek desktop shares Calc calibration','Android_limitations':'Existing imputation of missing cached counts for reused episodes retained, Android d uses bill/p_in proxy; full strict all-count table is unavailable for old deployment stage.'},'prices':prices,'rows':rows,'aggregate':ag,'overall':{'doc_share_min':min(r['doc_share'] for r in rows),'doc_share_max':max(r['doc_share'] for r in rows),'doc_share_median':statistics.median(r['doc_share'] for r in rows),'doc_share_negative_count':sum(r['doc_share']<0 for r in rows),'doc_share_gt_quarter_count':sum(r['doc_share']>.25 for r in rows),'program_share_min':min(r['program_share'] for r in rows if r['program_share'] is not None),'program_share_max':max(r['program_share'] for r in rows if r['program_share'] is not None),'admitted_count':sum(r['admitted'] for r in rows),'k1_k2_admitted_count':sum(g>=4 for r in rows for g in r['gates'][:2])}}
out['source_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [OLD]+[ROOT/r[k] for r in rows if r['platform']!='Android' for k in ['source','deploy_source'] if r.get(k)]}

def read_source(path):
    out['source_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return json.loads(path.read_text())

# ------------------------------------------------------------------ qwen layer
# Third model qwen/qwen3.8-flash, measured 2026-09-20. DATA LAYER ONLY: the
# rows live under `qwen_rows` (never in `rows`), so the glm/ds tables,
# `overall`, repeated/paired sections and every legacy count are unchanged;
# body/table integration is PENDING.
#
# Field mapping vs the frozen Android reader (constants_table.json): c, L_doc,
# d, q, C, floor, doc_share, program_share, nstar, gates and admitted use the
# same formulas, derived from each cell's own build.json/deploy.json instead
# of the frozen table's headline. Fields that map differently:
#   * floor: measured 2026-09-21 in t12_grid (18 no-task runs, 3 families x
#     6 seeds), parsed exactly like the glm/ds floor reader -- the single
#     model call's usage per run -- and priced per the floor-convention
#     ruling: mean pw(per_run) AS RECORDED, cached tokens AT the model's
#     documented cache ratio (r_c 0.107, r_o 3.13, the build cells'
#     price_weights). This gives 1031.8 pw against a 4432.3 raw mean,
#     because 16/18 runs hit the repeated 4352-token cached prefix (the
#     glm/ds floors are raw-uncached). Per-run numbers are frozen under
#     `qwen_android_floor`.
#   * d: deployment bill / p_in proxy (mean use cost_usd / p_in), i.e. the
#     same bill convention the table's headline d carries.
#   * C: translator + selected builder arm + refinements + analyzer + resume
#     from the raw stage records (= the table's C_with_repair definition).
#     qwen builder arms carry their usage at the top level (the Desktop
#     records wrap it in a 'usage' dict).
#   * final gate: verification.gate_after_repair.bindings_passed (the table's
#     p_headline*5); per-k gates come from gate_per_k as before.
#   * no cache imputation: qwen episodes record complete prompt/cached/
#     completion counts, so cache_imputed_episodes is 0.
#   * rejected row (OsmAndMarker): C/d/q carry the model median over the
#     ADMITTED qwen rows (the t2sim rejected-row convention; the engine and
#     the constants builder need a price for an attempt that passes); the
#     cell's own failed-build price is kept under measured.C_with_repair.
#   * terminated row (CalcTableSave): C is the PARTIAL translator+builder
#     spend flagged C_partial (the gate stage makes no model calls), with
#     terminated_reason set and program fields None.
#   * WebArena CommentPost has NO row: provider-refused at exploration seed 1
#     (qwen_disclosures.webarena).
QWEN='qwen/qwen3.8-flash'
QWEN_TERMINATED_REASON='artifact wedge: exception-swallowing retry loops defeat the replay deadline (wedge.md)'
qwen_files=[]
def qread(p):
 qwen_files.append(p);return json.loads(p.read_text())
prices[QWEN]={'p_in':1.5e-07,'p_c':1.6e-08,'p_o':4.7e-07}   # OpenRouter list 2026-09-20 (r_c 0.107, r_o 3.13); out['prices'] shares this dict
# Floor calibration: t12_grid 18 no-task runs, glm/ds reader parsing (one
# model call per run), floor = mean pw(per_run) at the DOCUMENTED price
# weights (cached AT the cache ratio), per the 2026-09-21 convention ruling.
QWEN_R_C_DOC=0.107;QWEN_R_O_DOC=3.13   # the build cells' recorded price_weights
QWEN_FLOOR_CACHE_FULL=4352
floor_runs=[]
for p in sorted((ROOT/'experimental-results/guiexp_android/t12_grid/qwen_qwen3.8-flash').glob('floor__*/trajectory.jsonl')):
 recs=[json.loads(l) for l in p.read_text().splitlines() if l.strip()]
 uses=[r['usage'] for r in recs if r.get('usage')]
 assert len(uses)==1,(p,len(uses))
 u=uses[0];cached=u.get('cached_tokens') or 0
 fam,seed=p.parent.name.split('__')[1:]
 floor_runs.append(dict(family=fam,seed=int(seed[1:]),prompt_tokens=u['prompt_tokens'],cached_tokens=cached,completion_tokens=u['completion_tokens'],raw_tokens=u['prompt_tokens']+u['completion_tokens'],pw_tokens=(u['prompt_tokens']-cached)+QWEN_R_C_DOC*cached+QWEN_R_O_DOC*u['completion_tokens'],pw_tokens_exact_price_ratios=(u['prompt_tokens']-cached)+(prices[QWEN]['p_c']/prices[QWEN]['p_in'])*cached+(prices[QWEN]['p_o']/prices[QWEN]['p_in'])*u['completion_tokens'],cache_state='full' if cached>=QWEN_FLOOR_CACHE_FULL else ('partial' if cached>0 else 'cold'),source=str(p.relative_to(ROOT))))
 qwen_files.append(p)
QWEN_FLOOR=statistics.mean(r['pw_tokens'] for r in floor_runs)
QWEN_FLOOR_RAW=statistics.mean(r['raw_tokens'] for r in floor_runs)
QWEN_FLOOR_EXACT=statistics.mean(r['pw_tokens_exact_price_ratios'] for r in floor_runs)
QWEN_FLOOR_N_FULL=sum(r['cache_state']=='full' for r in floor_runs)
QWEN_FLOOR_NOTE=(f'Android no-task floor {QWEN_FLOOR:.1f} pw (raw mean {QWEN_FLOOR_RAW:.1f} tokens), mean of {len(floor_runs)} t12_grid no-task runs (3 families x 6 seeds), each run parsed as its single model call and priced per the floor convention with the documented price weights r_c 0.107 / r_o 3.13: cached tokens charged AT the cache ratio, not as uncached raw (the GLM 5090 / DeepSeek 2290 floors are raw-uncached). Cache-state divergence: {QWEN_FLOOR_N_FULL}/{len(floor_runs)} runs hit the repeated {QWEN_FLOOR_CACHE_FULL}-token cached prefix, one partial (512), one cold, so the pw floor sits far below the raw floor. Per-run numbers: qwen_android_floor.')
out['qwen_android_floor']={'source':'experimental-results/guiexp_android/t12_grid/qwen_qwen3.8-flash/floor__<Family>__s<K>/trajectory.jsonl','runs':len(floor_runs),'price_weights':{'r_c':QWEN_R_C_DOC,'r_o':QWEN_R_O_DOC},'floor_pw':QWEN_FLOOR,'floor_raw_tokens':QWEN_FLOOR_RAW,'floor_pw_exact_price_ratios':QWEN_FLOOR_EXACT,'cache_state':{'full':QWEN_FLOOR_N_FULL,'partial':sum(r['cache_state']=='partial' for r in floor_runs),'cold':sum(r['cache_state']=='cold' for r in floor_runs)},'convention':QWEN_FLOOR_NOTE,'per_run':floor_runs}
qwen_rows=[];qwen_android={}
for fam in ['ContactsAddContact','MarkorDeleteNote','SimpleCalendarAddOneEvent','OsmAndMarker']:
 cell=ROOT/'experimental-results/guiexp_android/t16_build/qwen_qwen3.8-flash'/fam
 x=qread(cell/'build.json');v=x['verification']
 ex=x['exploration'];n=len(ex['per_episode']);et=pw(ex['totals'],QWEN)
 floor=QWEN_FLOOR
 c=et/n-floor
 doc=x['doc_arm'];ld=pw(doc['totals'],QWEN)/len(doc['episodes'])-floor
 sel=x['builder']['selected_arm'];init=x['builder']['initial'][f"k{sel['k']}_{sel['artifact']}"]
 Cparts={'translator':pw(x['translator']['totals'],QWEN),'builder_initial':pw(init,QWEN),'builder_refinements':pw(v['builder_refinements'],QWEN),'verification_analyzer':pw(v['analyzer'],QWEN),'verification_resume':pw(v['resume_episodes'],QWEN)};C=sum(Cparts.values())
 dp=cell/'deploy.json';d=q=None;dn=ds=None
 if v['admitted']:
  de=qread(dp);assert all(u.get('calls_detail') for u in de['uses']);d=statistics.mean(u['cost_usd'] for u in de['uses'])/prices[QWEN]['p_in'];dn=de['n'];ds=de['success_count'];q=1-ds/dn
 s=(1-q)*c-d if q is not None else None
 row=dict(platform='Android',model=QWEN,family=fam,source=str((cell/'build.json').relative_to(ROOT)),deploy_source=str(dp.relative_to(ROOT)) if v['admitted'] else None,c=c,L_doc=ld,d=d,q=q,C=C,C_parts=Cparts,floor=floor,doc_share=(c-ld)/c,program_share=ratio(s,c),nstar=ratio(C,s),nstar_incl_3c=ratio(C+3*c,s),nstar_incl_observed_floored=ratio(C+et-n*floor,s),n_exploration_episodes=n,agent_successes=sum(bool(e['success']) for e in ex['per_episode']),doc_successes=doc['success_count'],deploy_n=dn,deploy_successes=ds,admitted=v['admitted'],gates=[x['gate_per_k'][f'k{k}']['bindings_passed'] for k in (1,2,3)],final_gate=v['gate_after_repair']['bindings_passed'],refinements=v['refinements'],floor_note=QWEN_FLOOR_NOTE,cost_counts_limitation='Android no-task floor for qwen measured in t12_grid (18 no-task runs) and subtracted from c and L_doc at the documented price weights; see floor_note and qwen_android_floor. d uses the deployment-bill/p_in proxy, matching the Android convention. Raw prompt/cached/completion counts are recorded for every call, so no cache imputation was needed.',cache_imputed_episodes=0,cache_imputation_share=0.0)
 qwen_rows.append(row);qwen_android[fam]=row
wm=ROOT/'experimental-results/guiexp_osworld/qwen_qwen3.8-flash/WriterMemoSave/build.json'
qwen_files+=[wm,wm.with_name('deploy.json')]
qwen_rows.append(desktop_web_row('Desktop',wm))     # admitted; identical Desktop mapping as the glm/ds rows
calc_path=ROOT/'experimental-results/guiexp_osworld/qwen_qwen3.8-flash/CalcTableSave/build.json'
x=qread(calc_path)
fc=statistics.mean(pw(u,QWEN) for u in x['floor']['per_run']);nc=x['exploration']['totals']['episodes'];c=pw(x['exploration']['totals'],QWEN)/nc-fc
C_partial=pw(x['translator']['totals'],QWEN)+sum(pw(a['usage'] if 'usage' in a else a,QWEN) for a in x['builder']['initial'].values())   # gate stage makes no model calls (wedge.md)
calc_row=dict(platform='Desktop',model=QWEN,family='CalcTableSave',source=str(calc_path.relative_to(ROOT)),deploy_source=None,c=c,L_doc=None,d=None,q=None,C=C_partial,C_partial=True,terminated_reason=QWEN_TERMINATED_REASON,floor=fc,doc_share=None,program_share=None,nstar=None,nstar_incl_3c=None,nstar_incl_observed_floored=None,n_exploration_episodes=nc,agent_successes=sum(bool(a.get('success')) for inst in x['exploration']['instances'] for a in inst['attempts']),doc_successes=None,deploy_n=None,deploy_successes=None,admitted=False,gates=[None,None,None],final_gate=None,refinements=None,original_port_break_even=None,cost_counts_limitation='Cell TERMINATED by operator ruling inside the held-out gate stage (wedge.md): the qwen-compiled artifact swallows the one-shot replay timeout inside exception retry loops, so verification/deploy/doc_arm never ran. C is the PARTIAL translator+builder spend; builder covers all six unselected arms because no gate ranking happened; the gate stage itself makes no model calls.',measured={'admitted':False,'terminated':True,'C_with_repair':C_partial})
qwen_rows.append(calc_row)
qok=[r for r in qwen_rows if r['admitted']]
fill={'C':statistics.median([r['C'] for r in qok]),'d':statistics.median([r['d'] for r in qok if r['d'] is not None]),'q':statistics.median([r['q'] for r in qok if r['q'] is not None])}
osm=qwen_android['OsmAndMarker'];osm_own_C=osm['C']
osm['C']=fill['C'];osm['d']=fill['d'];osm['q']=fill['q']
osm['measured']={'admitted':False,'C_with_repair':osm_own_C,'fill':"model median over the admitted qwen rows (C, d, q); the row's c/L_doc/doc_share stay the cell's own measurements",'gate_history':[g['passed'] for g in json.loads((ROOT/osm['source']).read_text())['verification']['gate_history']]}
osm['rejected_note']='unautomatable at verification after the repair rounds; deploy skipped (no version passed the held-out gate)'
qwen_rows.sort(key=lambda r:(r['model'],['Android','Desktop','Web'].index(r['platform']),r['family']))
C_adm=statistics.median([r['C'] for r in qok]);C_fail=statistics.median([osm_own_C,C_partial])
qwen_aggregate={'admitted':len(qok),'total':len(qwen_rows),'attempted':len(qwen_rows)+1,'attempted_note':'includes WebArena CommentPost, which provider-refused before exploration and has no row (qwen_disclosures.webarena)','C_admitted_median':C_adm,'C_rejected_median':C_fail,'C_fail_side':{'Android/OsmAndMarker':"measured.C_with_repair (the cell's own failed-build price)",'Desktop/CalcTableSave':'C (partial translator+builder spend, C_partial=true)'},'C_fail_multiple':C_fail/C_adm,'nstar_range':[min(r['nstar'] for r in qok),max(r['nstar'] for r in qok)],'nstar_incl_3c_range':[min(r['nstar_incl_3c'] for r in qok),max(r['nstar_incl_3c'] for r in qok)]}
out['qwen_rows']=qwen_rows
out['aggregate'][QWEN]=qwen_aggregate
out['qwen_disclosures']={'webarena':'provider content-filter refusal (400 data_inspection_failed), deterministic, floor 18/18 passed; single Alibaba endpoint','osmand':'rejected at verification after repair rounds under the image-413-guard completion','calc':QWEN_TERMINATED_REASON}
cp=ROOT/'experimental-results/guiexp_webarena/qwen_qwen3.8-flash/CommentPost'
qwen_files+=[cp/'build.json',cp/'provider_refused.md',cp/'floor/floor.json',ROOT/'experimental-results/guiexp_osworld/qwen_qwen3.8-flash/CalcTableSave/wedge.md']
for p in qwen_files:
 out['source_sha256'][str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()

android = ROOT / 'experimental-results/guiexp_android'
repeated = read_source(android / 't19_repeated/_lane_summary.json')['cells']
gate = read_source(android / 't20_gate_extraction/summary.json')['cells']
out['repeated_and_extraction'] = []
out['paired_replays'] = []
for row in rows:
    if row['platform'] != 'Android':
        continue
    key = row['model'].replace('/', '_') + '/' + row['family']
    attempts = repeated[key]['attempts']
    ext = gate[key]
    out['repeated_and_extraction'].append(dict(
        model=row['model'], family=row['family'],
        verified_initial=int(row['admitted']),
        verified_extra=sum(a['admitted'] is True for a in attempts),
        provider_errors=sum(bool(a['error']) for a in attempts),
        exhausted_repairs=sum(a['admitted'] is False for a in attempts),
        injection_now=ext['injection_passed_now'],
        extraction=ext['extraction_passed'],
        extraction_type_check_fails=ext['extraction_type_check_fails']))
    if not row['admitted']:
        continue
    records = [read_source(p) for p in sorted((android / 't21_paired_replay' / key).glob('use_*/summary.json'))]
    assert len(records) == 30 and all(r['model_calls'] > 0 for r in records)
    costs = [pw(r['usage'], row['model']) - row['floor'] for r in records]
    deploy = read_source(android / 't16_build' / key / 'deploy.json')['uses']
    assert len(deploy) == len(records)
    ds = []
    for record in records:
        use = deploy[record['use_index']]
        assert record['goal'] == use['goal']
        assert record['binding'] == use['expected']
        ds.append(use['cost_usd'] / prices[row['model']]['p_in'])
    c = statistics.mean(costs)
    q = sum(not u['success'] for u in deploy) / len(deploy)
    out['paired_replays'].append(dict(
        model=row['model'], family=row['family'], n=len(records),
        c=c, cv=statistics.stdev(costs)/c,
        agent_success=sum(bool(r['success']) for r in records),
        program_success=sum(bool(u['success']) for u in deploy),
        q=q, d=statistics.mean(ds),
        share=(1-q)-statistics.mean(ds)/c,
        observed_failure_proxy_share=sum((r_cost if deploy[r['use_index']]['success'] else 0)-d for r,r_cost,d in zip(records,costs,ds))/sum(costs)))

# ------------------------------------------------------------------ qwen t19/t20/t21 halves
# The verification and paired-replay halves for the triple renderer: same
# formulas as the two loops above, read from the qwen per-family t19
# summaries because _lane_summary.json carries no qwen cells (the t20
# summary does).  Data layer only: out['repeated_and_extraction'] and
# out['paired_replays'] stay glm/ds, so every legacy count is unchanged.
qwen_rep=[]
for fam in ['ContactsAddContact','MarkorDeleteNote','SimpleCalendarAddOneEvent']:
 t19=qread(android/'t19_repeated/qwen_qwen3.8-flash'/fam/'summary.json');attempts=t19['attempts']
 ext=gate[f'qwen_qwen3.8-flash/{fam}']   # gate = the t20 summary's cells dict
 qwen_rep.append(dict(model=QWEN,family=fam,
  verified_initial=int(qwen_android[fam]['admitted']),
  verified_extra=sum(a['admitted'] is True for a in attempts),
  provider_errors=sum(bool(a.get('error')) for a in attempts),
  exhausted_repairs=sum(a['admitted'] is False for a in attempts),
  injection_now=ext['injection_passed_now'],
  extraction=ext['extraction_passed'],
  extraction_type_check_fails=ext['extraction_type_check_fails']))
out['qwen_repeated_and_extraction']=qwen_rep
qwen_paired=[]
for fam in ['ContactsAddContact','MarkorDeleteNote','SimpleCalendarAddOneEvent']:
 row=qwen_android[fam];key=f'qwen_qwen3.8-flash/{fam}'
 records=[read_source(p) for p in sorted((android/'t21_paired_replay'/key).glob('use_*/summary.json'))]
 assert len(records)==30 and all(r['model_calls']>0 for r in records)
 costs=[pw(r['usage'],QWEN)-row['floor'] for r in records]
 deploy=read_source(android/'t16_build'/key/'deploy.json')['uses']
 assert len(deploy)==len(records)
 ds=[]
 for record in records:
  use=deploy[record['use_index']]
  assert record['goal']==use['goal']
  assert record['binding']==use['expected']
  ds.append(use['cost_usd']/prices[QWEN]['p_in'])
 c=statistics.mean(costs)
 q=sum(not u['success'] for u in deploy)/len(deploy)
 qwen_paired.append(dict(model=QWEN,family=fam,n=len(records),
  c=c,cv=statistics.stdev(costs)/c,
  agent_success=sum(bool(r['success']) for r in records),
  program_success=sum(bool(u['success']) for u in deploy),
  q=q,d=statistics.mean(ds),
  share=(1-q)-statistics.mean(ds)/c,
  observed_failure_proxy_share=sum((r_cost if deploy[r['use_index']]['success'] else 0)-d for r,r_cost,d in zip(records,costs,ds))/sum(costs)))
out['qwen_paired_replays']=qwen_paired
for p in qwen_files:   # hash the t19/t21 qwen files qread/read_source just touched
 out['source_sha256'].setdefault(str(p.relative_to(ROOT)),hashlib.sha256(p.read_bytes()).hexdigest())

family_order = ['ContactsAddContact', 'MarkorDeleteNote', 'SimpleCalendarAddOneEvent', 'OsmAndMarker', 'CalcTableSave', 'WriterMemoSave', 'CommentPost']
names = dict(zip(family_order, ['Contacts', 'Markor delete', 'Calendar', 'OsmAnd marker', 'Calc table', 'Writer memo', 'Reddit comment']))
def order(row):
    return (0 if row['model'].startswith('z-ai/') else 1, family_order.index(row['family']))
def prefix(row):
    if row['model'] == QWEN:
        return '\\rowcolor{qwTint} ' + names[row['family']]
    return ('\\rowcolor{glmTint}' if row['model'].startswith('z-ai/') else '\\rowcolor{dsTint}') + ' ' + names[row['family']]
def fmt(value, nd=2):
    return '--' if value is None else f'{value:.{nd}f}'
def sig3(value):
    """3 significant figures, ROUND_HALF_UP on the raw value, rendered with
    the fewest characters that carry exactly 3 significant digits."""
    from decimal import Decimal, ROUND_HALF_UP
    if value is None:
        return '--'
    d = Decimal(repr(float(value)))
    if d == 0:
        return '0'
    neg = d < 0
    d = abs(d)
    exp = d.adjusted()                      # floor(log10|d|)
    q = d.scaleb(2 - exp).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    if q >= 1000:                           # 999.6 carried into a fourth digit
        q //= 10
        exp += 1
    digits = str(q)                         # exactly 3 digits
    point = exp + 1                         # digits left of the decimal point
    if point <= 0:
        s = '0.' + '0' * (-point) + digits
    elif point >= len(digits):
        s = digits + '0' * (point - len(digits))
    else:
        s = digits[:point] + '.' + digits[point:]
    return ('-' if neg else '') + s
def tok3(value):
    """Token counts at 3 significant figures: plain below 1k, k-suffixed above."""
    if value is None:
        return '--'
    v = float(value)
    if abs(v) < 1000:
        return sig3(v)
    return sig3(v / 1000) + 'k'
def tok3k(value):
    """k-units column (value already in raw tokens): 3 significant figures in k."""
    return '--' if value is None else sig3(float(value) / 1000) + 'k'
def line(values):
    return ' & '.join(values) + r' \\'

def value_cells(table, row):
    if table == 'share':
        # d/q are masked for non-admitted rows: the qwen fill convention
        # records simulator-price d/q in rejected rows, while the paper's
        # program columns are measured only (the glm/ds rejected rows carry
        # None here, so the mask changes nothing for them).
        adm = row['admitted']
        return [tok3k(row['c']), tok3k(row['L_doc']),
                tok3(row['d']) if adm else '--', fmt(row['q']) if adm else '--',
                fmt(row['doc_share']), fmt(row['program_share'])]
    if table == 'price':
        C = row['C']
        m = row.get('measured') or {}
        if not row['admitted'] and m.get('C_with_repair') is not None:
            # the qwen rejected row carries the admitted-median fill in C; the
            # column prints the cell's own recorded build price (Calc's
            # top-level C is already its partial recorded spend)
            C = m['C_with_repair']
        final = '--' if (row['final_gate'] is None
                         or (row['platform']=='Android' and not row['admitted'])
                         ) else fmt(row['final_gate']/5)
        return [tok3k(C),
                *[fmt(g/5) if g is not None else '--' for g in row['gates']],
                final, sig3(row['nstar']), sig3(row['nstar_incl_3c'])]
    if table == 'verification':
        pe = row['provider_errors']
        mark = {0: '', 1: '$^{\\dagger}$', 2: '$^{\\dagger\\dagger}$'}[pe]
        return [str(row['verified_initial'] + row['verified_extra']) + '/3' + mark,
                str(row['injection_now']) + '/5', str(row['extraction']) + '/5']
    return [tok3k(row['c']), sig3(row['cv']), str(row['agent_success'])+'/30', str(row['program_success'])+'/30', fmt(row['share'],3)]

def _dual_cell(g, d):
    """One merged body cell: dual when both models have a value; a
    single-model color cell when the other half is absent or '--' (that
    model produced no verified program)."""
    if g is None or g == '--':
        assert d is not None and d != '--', (g, d)
        return r'\dualD{' + d + '}'
    if d is None or d == '--':
        return r'\dualG{' + g + '}'
    return r'\dual{' + g + '}{' + d + '}'

def _tri_cell(g, d, q):
    """One merged body cell over the three models, extending the dual
    convention with a qwTint third half: \\tri{G}{DS}{QW} when all three
    have a value; the unchanged dual forms when qwen has none; \\triGQ /
    \\triDQ when exactly one of glm/ds is present next to qwen; a
    single-model \\triG/\\triD/\\triQ color cell otherwise.  Absent means
    the row is missing or the cell rendered as '--'."""
    gp = g is not None and g != '--'
    dp = d is not None and d != '--'
    qp = q is not None and q != '--'
    if qp and gp and dp:
        return r'\tri{' + g + '}{' + d + '}{' + q + '}'
    if not qp:
        return _dual_cell(g, d)
    if gp and dp:
        raise AssertionError((g, d, q))          # handled by the \tri branch
    if gp:
        return r'\triGQ{' + g + '}{' + q + '}'
    if dp:
        return r'\triDQ{' + d + '}{' + q + '}'
    return r'\triQ{' + q + '}'

# Per-model rows are kept in the JSON output; body.tex now prints one dual row
# per family with \dual{GLM cell}{DS cell}, so the same cells are merged for the
# body.tex assertion (single-model cells where a model half is absent).
out['latex_rows'] = dict(share=[], price=[], verification=[], paired=[])
dual_rows = dict(share=[], price=[], verification=[], paired=[])
dual_model_cells = 0
for table, source in [('share', rows), ('price', rows), ('verification', out['repeated_and_extraction']), ('paired', out['paired_replays'])]:
    halves = {}
    for row in sorted(source, key=order):
        cells = value_cells(table, row)
        out['latex_rows'][table].append(line([prefix(row), *cells]))
        halves.setdefault(row['family'], {})[('glm' if row['model'].startswith('z-ai/') else 'ds')] = cells
    for family in family_order:
        half = halves.get(family)
        if half is None:
            continue
        glm_cells, ds_cells = half.get('glm'), half.get('ds')
        n = len(glm_cells or ds_cells)
        merged = [_dual_cell(glm_cells[i] if glm_cells else None,
                             ds_cells[i] if ds_cells else None)
                  for i in range(n)]
        dual_rows[table].append(line([names[family], *merged]))
        dual_model_cells += sum(h is not None for h in (glm_cells, ds_cells))
assert len(rows) == 14 and len(out['paired_replays']) == 6

# Triple renderer: the same merge over glm+ds+qw halves, kept OUT of --check
# (the body lands separately).  out['latex_rows_qwen'] holds the per-family
# qw rows under the qwTint prefix, out['qw_halves'] the raw qw cell strings
# per table/family (frozen by --check-qwen-rows), and out['latex_triples']
# the merged triple rows the body edit can be diffed against.
out['latex_rows_qwen'] = dict(share=[], price=[], verification=[], paired=[])
qw_halves = {}
triple_rows = dict(share=[], price=[], verification=[], paired=[])
for table, qw_source in [('share', qwen_rows), ('price', qwen_rows),
                         ('verification', out['qwen_repeated_and_extraction']),
                         ('paired', out['qwen_paired_replays'])]:
    halves3 = {}
    for row in sorted(rows if table in ('share', 'price') else
                      (out['repeated_and_extraction'] if table == 'verification'
                       else out['paired_replays']), key=order):
        halves3.setdefault(row['family'], {})[
            'glm' if row['model'].startswith('z-ai/') else 'ds'] = value_cells(table, row)
    for row in sorted(qw_source, key=order):
        cells = value_cells(table, row)
        out['latex_rows_qwen'][table].append(line([prefix(row), *cells]))
        halves3.setdefault(row['family'], {})['qw'] = cells
        qw_halves.setdefault(table, {})[row['family']] = cells
    for family in family_order:
        half = halves3.get(family)
        if half is None:
            continue
        g, d, q = half.get('glm'), half.get('ds'), half.get('qw')
        n = len(g or d or q)
        merged = [_tri_cell(g[i] if g else None, d[i] if d else None,
                            q[i] if q else None) for i in range(n)]
        triple_rows[table].append(line([names[family], *merged]))
out['qw_halves'] = qw_halves
out['latex_triples'] = triple_rows
if args.check:
    body = (Path(__file__).parent / 'body.tex').read_text().split('\\appendix\n', 1)[0]
    for table, expected in dual_rows.items():
        for expected_row in expected:
            assert expected_row in body, (table, expected_row)
    assert out['overall']['admitted_count'] == 11
    assert out['overall']['doc_share_negative_count'] == 7
    assert sum(r['verified_extra'] for r in out['repeated_and_extraction']) == 9
    assert sum(r['provider_errors'] for r in out['repeated_and_extraction']) == 3
    print(f'PASS: {sum(len(v) for v in dual_rows.values())} dual rows ({dual_model_cells} model cells), 180 binding matches, and measurement counts.')
if args.include_qwen:
    # Preview of the pending body/table integration: merge the qwen rows into
    # the JSON `rows` array only. The latex tables above and the glm/ds
    # checks are untouched (consumers bucket `rows` by model prefix, so this
    # mode is NOT read by paper/check_numbers_new.py or the body renderer).
    out['rows']=sorted(rows+qwen_rows,key=lambda r:(r['model'],['Android','Desktop','Web'].index(r['platform']),r['family']))
if args.check_qwen:
    def qclose(got,want,tag):
        assert got is not None and abs(got-want)<=1e-9*max(1.0,abs(want)),(tag,got,want)
    qw={(r['platform'],r['family']):r for r in out['qwen_rows']}
    assert set(qw)=={('Android','ContactsAddContact'),('Android','MarkorDeleteNote'),('Android','SimpleCalendarAddOneEvent'),('Android','OsmAndMarker'),('Desktop','WriterMemoSave'),('Desktop','CalcTableSave')},set(qw)
    QW_FROZEN={
     ('Android','ContactsAddContact'):dict(admitted=True,c=39168.48233333334,C=136655.21333333335,d=546.5444444444445,q=0.033333333333333326,nstar=3.6620761827775743,nstar_incl_observed_floored=6.810978360081989,doc_share=-0.1909936992112374,doc_successes=3,agent_successes=3,n_exploration_episodes=3,gates=[2,5,5],final_gate=5,refinements=0,floor=1031.7576666666666),
     ('Android','MarkorDeleteNote'):dict(admitted=True,c=21487.260111111107,C=207527.08000000002,d=384.2266666666667,q=0.0,nstar=9.833992849716747,nstar_incl_observed_floored=12.888614380930944,doc_successes=3,agent_successes=3,n_exploration_episodes=3,gates=[5,5,5],final_gate=5,refinements=0,floor=1031.7576666666666),
     ('Android','SimpleCalendarAddOneEvent'):dict(admitted=True,c=134632.32677777775,C=1541962.3466666667,d=835.6733333333334,q=0.0,nstar=11.524670512831074,nstar_incl_observed_floored=14.5434080517415,doc_successes=3,agent_successes=3,n_exploration_episodes=3,gates=[5,0,0],final_gate=4,refinements=3,floor=1031.7576666666666),
     ('Android','OsmAndMarker'):dict(admitted=False,c=122265.79471428572,L_doc=42116.393444444446,C=346595.7266666667,d=660.6433333333334,q=0.0,nstar=None,program_share=None,agent_successes=1,doc_successes=2,n_exploration_episodes=7,gates=[0,0,3],final_gate=1,refinements=3,floor=1031.7576666666666),
     ('Desktop','WriterMemoSave'):dict(admitted=True,c=36984.96888888889,C=485664.3733333333,d=774.7422222222223,q=0.0,nstar=13.41235385804452,doc_successes=3,agent_successes=3,n_exploration_episodes=3,gates=[0,0,5],final_gate=5,refinements=0,floor=531.7155555555557),
     ('Desktop','CalcTableSave'):dict(admitted=False,c=168641.09703703705,L_doc=None,d=None,q=None,C=847543.8933333333,nstar=None,agent_successes=3,doc_successes=None,n_exploration_episodes=3,gates=[None,None,None],final_gate=None,refinements=None,terminated_reason=QWEN_TERMINATED_REASON),
    }
    for key,exp in QW_FROZEN.items():
        r=qw[key]
        for k,want in exp.items():
            if isinstance(want,bool) or not isinstance(want,(int,float)) or isinstance(want,int) and not isinstance(want,bool) and k in ('admitted','doc_successes','agent_successes','n_exploration_episodes','gates','final_gate','refinements'):
                assert r.get(k)==want,(key,k,r.get(k),want)
            else:
                qclose(r.get(k),want,f'{key}.{k}')
    assert qw[('Desktop','CalcTableSave')]['C_partial'] is True
    assert abs(qw[('Android','OsmAndMarker')]['measured']['C_with_repair']-1585498.9733333334)<=1e-6
    ag=out['aggregate'][QWEN]
    assert ag['admitted']==4 and ag['total']==6 and ag['attempted']==7
    qclose(ag['C_admitted_median'],346595.7266666667,'aggregate.C_admitted_median')
    qclose(ag['C_rejected_median'],1216521.4333333333,'aggregate.C_rejected_median')
    qclose(ag['C_fail_multiple'],3.5099146923507942,'aggregate.C_fail_multiple')
    qclose(ag['nstar_range'][0],3.6620761827775743,'aggregate.nstar_range[0]')
    qclose(ag['nstar_range'][1],13.41235385804452,'aggregate.nstar_range[1]')
    fl=out['qwen_android_floor']
    assert fl['runs']==18 and fl['cache_state']=={'full':16,'partial':1,'cold':1}
    qclose(fl['floor_pw'],1031.7576666666666,'qwen_android_floor.floor_pw')
    qclose(fl['floor_raw_tokens'],4432.277777777777,'qwen_android_floor.floor_raw_tokens')
    assert len(fl['per_run'])==18
    assert out['qwen_disclosures']['calc']==QWEN_TERMINATED_REASON and 'data_inspection_failed' in out['qwen_disclosures']['webarena'] and 'image-413-guard' in out['qwen_disclosures']['osmand']
    assert prices[QWEN]=={'p_in':1.5e-07,'p_c':1.6e-08,'p_o':4.7e-07}
    assert 'CommentPost' not in {r['family'] for r in out['qwen_rows']}
    print('PASS qwen: 6 rows (4 admitted) recomputed from the raw cells match the frozen literals; aggregate, prices and disclosures consistent.')
if args.check_qwen_rows:
    # The rendered qwen half-strings of Tables 1/2/3/4, frozen so the
    # operator's body.tex triple edit can be diffed against them.  Program
    # columns of the two rejected/terminated cells are '--' by design (the
    # fill d/q in the OsmAnd row exists only for simulator pricing), and the
    # OsmAnd C cell prints the cell's own recorded build price
    # (measured.C_with_repair), not the admitted-median fill.
    QW_HALF_FROZEN={
     'share':{
      'ContactsAddContact':['39.2k','46.6k','547','0.03','-0.19','0.95'],
      'MarkorDeleteNote':['21.5k','18.6k','384','0.00','0.13','0.98'],
      'SimpleCalendarAddOneEvent':['135k','129k','836','0.00','0.04','0.99'],
      'OsmAndMarker':['122k','42.1k','--','--','0.66','--'],
      'WriterMemoSave':['37.0k','58.0k','775','0.00','-0.57','0.98'],
      'CalcTableSave':['169k','--','--','--','--','--'],
     },
     'price':{
      'ContactsAddContact':['137k','0.40','1.00','1.00','1.00','3.66','6.81'],
      'MarkorDeleteNote':['208k','1.00','1.00','1.00','1.00','9.83','12.9'],
      'SimpleCalendarAddOneEvent':['1540k','1.00','0.00','0.00','0.80','11.5','14.5'],
      'OsmAndMarker':['1590k','0.00','0.00','0.60','--','--','--'],
      'WriterMemoSave':['486k','0.00','0.00','1.00','1.00','13.4','16.5'],
      'CalcTableSave':['848k','--','--','--','--','--','--'],
     },
     'verification':{
      'ContactsAddContact':['3/3','5/5','5/5'],
      'MarkorDeleteNote':['3/3','5/5','5/5'],
      'SimpleCalendarAddOneEvent':['2/3','4/5','4/5'],
     },
     'paired':{
      'ContactsAddContact':['42.2k','0.0993','30/30','29/30','0.954'],
      'MarkorDeleteNote':['19.3k','0.128','30/30','30/30','0.980'],
      'SimpleCalendarAddOneEvent':['316k','0.504','15/30','30/30','0.997'],
     },
    }
    got=out['qw_halves']
    assert got==QW_HALF_FROZEN,{t:{f:(got.get(t,{}).get(f),QW_HALF_FROZEN[t].get(f)) for f in set(got.get(t,{}))|set(QW_HALF_FROZEN[t]) if got.get(t,{}).get(f)!=QW_HALF_FROZEN[t].get(f)} for t in set(got)|set(QW_HALF_FROZEN)}
    assert {t:len(v) for t,v in out['latex_rows_qwen'].items()}=={'share':6,'price':6,'verification':3,'paired':3}
    assert {t:len(v) for t,v in out['latex_triples'].items()}=={'share':7,'price':7,'verification':4,'paired':4}
    ntri=sum(''.join(v).count('\\tri{') for v in out['latex_triples'].values())
    ngq=sum(''.join(v).count('\\triGQ') for v in out['latex_triples'].values())
    ndual=sum(''.join(v).count('\\dual') for v in out['latex_triples'].values())
    nsgl=sum(sum(''.join(v).count(f'\\tri{m}') for v in out['latex_triples'].values()) for m in ('G','D','Q'))
    print(f"PASS qwen-rows: 18 frozen qw half-rows over 4 tables match the recompute; "
          f"22 triple rows carry {ntri} \\tri, {ngq} \\triGQ, {ndual} unchanged \\dual, {nsgl} single-color cells; "
          f"the merged rows are in out['latex_triples'].")
args.output.write_text(json.dumps(out, indent=2)+'\n')
print('Wrote', args.output)
