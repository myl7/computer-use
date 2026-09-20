"""Reproduce the September 18 measurement tables without model or device calls.

Run from any directory. --check also compares the rendered rows with body.tex.
The older number generators retain their historical Android-only scope.
"""
from pathlib import Path
import json, statistics, argparse, hashlib
parser=argparse.ArgumentParser()
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
parser.add_argument('--output', type=Path, default=Path(__file__).with_suffix('.json'))
parser.add_argument('--check', action='store_true')
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
for platform,dirname in [('Desktop','guiexp_osworld'),('Web','guiexp_webarena')]:
 for path in sorted((ROOT/'experimental-results'/dirname).glob('*/*/build.json')):
  if 'mock' in str(path):continue
  x=json.loads(path.read_text());m=x['model'];n=x['exploration']['totals']['episodes'];f=statistics.mean(pw(u,m) for u in x['floor']['per_run']);et=pw(x['exploration']['totals'],m);c=et/n-f;doc=x['doc_arm'];ld=pw(doc['totals'],m)/len(doc['episodes'])-f;v=x['verification'];vt=v['totals'];sel=x['builder']['selected_arm'];init=x['builder']['initial'][f"k{sel['k']}_{sel['artifact']}"]['usage'];Cparts={'translator':pw(x['translator']['totals'],m),'builder_initial':pw(init,m),'builder_refinements':pw(vt['builder_refinements'],m),'verification_analyzer':pw(vt['analyzer'],m),'verification_resume':pw(vt['resume_episodes'],m)};C=sum(Cparts.values());dp=path.with_name('deploy.json');d=q=None;dn=ds=None
  if v['admitted']:
   de=json.loads(dp.read_text()); assert all(u.get('calls_detail') for u in de['uses']);d=statistics.mean(sum(pw(call,m) for call in u['calls_detail']) for u in de['uses']);dn=de['n'];ds=de['success_count'];q=1-ds/dn
  s=(1-q)*c-d if q is not None else None
  rows.append(dict(platform=platform,model=m,family=x['family'],source=str(path.relative_to(ROOT)),deploy_source=str(dp.relative_to(ROOT)) if v['admitted'] else None,c=c,L_doc=ld,d=d,q=q,C=C,C_parts=Cparts,floor=f,doc_share=(c-ld)/c,program_share=ratio(s,c),nstar=ratio(C,s),nstar_incl_3c=ratio(C+3*c,s),nstar_incl_observed_floored=ratio(C+et-n*f,s),n_exploration_episodes=n,agent_successes=sum(bool(a.get('success')) for inst in x['exploration']['instances'] for a in inst['attempts']),doc_successes=doc['success_count'],deploy_n=dn,deploy_successes=ds,admitted=v['admitted'],gates=[x['gate_per_k'][f'k{k}']['bindings_passed'] for k in (1,2,3)],final_gate=v['gate']['bindings_passed'],refinements=v['refinements'],original_port_break_even=x['break_even'],cost_counts_limitation='Recomputed provider-counts unit; no cold-host cache imputation; original port break_even values deliberately not used. C excludes exploration and unselected k/code/document builder arms, includes selected code refinement and all logged analyzer/resume calls. Interrupted lost calls absent.'))
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

family_order = ['ContactsAddContact', 'MarkorDeleteNote', 'SimpleCalendarAddOneEvent', 'OsmAndMarker', 'CalcTableSave', 'WriterMemoSave', 'CommentPost']
names = dict(zip(family_order, ['Contacts', 'Markor delete', 'Calendar', 'OsmAnd marker', 'Calc table', 'Writer memo', 'Reddit comment']))
def order(row):
    return (0 if row['model'].startswith('z-ai/') else 1, family_order.index(row['family']))
def prefix(row):
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
        return [tok3k(row['c']), tok3k(row['L_doc']), tok3(row['d']), fmt(row['q']), fmt(row['doc_share']), fmt(row['program_share'])]
    if table == 'price':
        final = '--' if row['platform']=='Android' and not row['admitted'] else fmt(row['final_gate']/5)
        return [tok3k(row['C']), *[fmt(g/5) for g in row['gates']], final, sig3(row['nstar']), sig3(row['nstar_incl_3c'])]
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
args.output.write_text(json.dumps(out, indent=2)+'\n')
print('Wrote', args.output)
