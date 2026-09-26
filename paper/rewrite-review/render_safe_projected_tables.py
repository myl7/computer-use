'Render compact tables from saved measurement and paired PACE data.\n\nEach metric cell contains fixed-width GLM, DeepSeek and Qwen subcells.\nNo model, device or simulator is called by this renderer.\n'
from pathlib import Path
import hashlib,json,re,statistics
ROOT=Path(__file__).resolve().parents[2]
PAPER=ROOT/'paper'
MEASUREMENT=json.loads((PAPER/'measurement_update_20260918.json').read_text())
EVIDENCE=ROOT/'analysis/safe_projected_paper_20260922/evaluation-evidence.json'
DATA=json.loads(EVIDENCE.read_text())
ADDITIONS=ROOT/'experimental-results/guiexp/t2_sim_v3/pace_baselines_20260923/paired_v2/evidence.json'
EXTRA=json.loads(ADDITIONS.read_text())
CONTROLS=ROOT/'experimental-results/guiexp/t2_sim_v3/pace_review_controls_20260923/evidence.json'
CONTROL_DATA=json.loads(CONTROLS.read_text())
for group in ['original_grid','fresh_grid','real_arrivals']:
    for source in [EXTRA,CONTROL_DATA]:
        updates={c['key']:c for c in source['cells'][group]}
        assert set(updates)=={c['key'] for c in DATA['cells'][group]},group
        for c in DATA['cells'][group]:
            extra=updates[c['key']]
            assert c['model']==extra['model'] and c['pattern']==extra['pattern']
            for policy,values in extra['policies'].items():
                if policy in c['policies']:
                    assert abs(c['policies'][policy]['ratio_agent']-values['ratio_agent'])<1e-12
                c['policies'][policy]=values
MODEL_IDS=['z-ai/glm-5.3-flash','deepseek/deepseek-v4-flash-vision-exp','qwen/qwen3.8-flash']
ORDER=['Contacts','Markor delete','Calendar','OsmAnd marker','Calc table','Writer memo','Reddit comment']
LEGEND='Within each cell, values are GLM (blue), DeepSeek (amber), and Qwen (green), from left to right.'


def cell(width,values):
    assert len(values)==3
    return r'\modelcell{'+width+'}'+''.join('{'+str(v)+'}' for v in values)


def tab(label,heads,rows,caption,size=r'\footnotesize',spacing='3pt'):
    centered_heads=[heads[0]]+[r'\multicolumn{1}{c}{'+head+'}' for head in heads[1:]]
    return '\n'.join([r'\begin{table}[t]',r'\centering',r'\caption{'+caption+'}',
                      r'\label{'+label+'}',size,r'\setlength{\tabcolsep}{'+spacing+'}',
                      r'\begin{tabular}{l'+'r'*(len(heads)-1)+'}',r'\toprule',
                      ' & '.join(centered_heads)+r' \\',r'\midrule',*rows,
                      r'\bottomrule',r'\end{tabular}',r'\end{table}'])


def measured_rows(kind,widths):
    rows=[];expected=[]
    for raw in MEASUREMENT['latex_triples'][kind]:
        fields=[v.strip() for v in raw.strip().rstrip('\\').split('&')]
        family=fields[0];triples=[fields[i:i+3] for i in range(1,len(fields),3)]
        if kind=='price':
            if family in ['Calendar','OsmAnd marker']:triples[2][1]='0.00'
            if family=='OsmAnd marker':triples[2][2]='0.20'
        assert len(triples)==len(widths)
        expected.extend(v for trip in triples for v in trip)
        rows.append(' & '.join([family]+[cell(w,trip) for w,trip in zip(widths,triples)])+r' \\')
        if kind in ['share','price'] and family in ['OsmAnd marker','Writer memo']:rows.append(r'\addlinespace[2pt]')
    return rows,expected

CAPTIONS={
 'share':r'''Serving costs and estimated savings for the initial programs.
Costs use the token unit in Section~\ref{sec:formulation}, with k denoting $10^3$.
$c$ is the mean cost of baseline-subtracted agent exploration runs, while $c^{\mathrm{prog}}$ and $q$ summarize thirty deployment uses.
The saving share charges one mean-cost agent fallback for a failed use.
'''+LEGEND+'\nA dash means an unavailable program-path value, and the Qwen web request was refused by the provider.',
 'price':r'''Initial compilation outcomes and conditional payback counts.
Attempt cost includes translation, the selected builder, and recorded repair calls, in tokens with k denoting $10^3$.
Initial and final pass give the number of successful checks out of five, before and after repair.
$N^*_{\mathrm{marg}}=C/s$ uses the recorded successful attempt, and $N^*_{\mathrm{incl}}=(C+3c)/s$ adds three mean-cost agent runs.
These counts exclude earlier failed attempts and future replacement.
'''+LEGEND+'\nQwen Calc reports partial saved spend before candidate selection completed, and the refused web cell has no attempt cost.',
 'verification':'Repeated compilation and checks of the original programs.\nVerified counts successful compilations out of three, including the original attempt.\nEach $\\dagger$ marks an attempt lost to repeated empty provider replies.\nThe Known and Extracted columns rerun the original program on five new bindings with known or model-extracted parameters.\n'+LEGEND+'\nThe two rejected DeepSeek programs were also checked, while Qwen OsmAnd has no additional compilation attempts.',
 'paired':r'''Agent replays and original program runs on matched task bindings.
Each populated model--family entry has thirty bindings.
$c$ is the mean baseline-subtracted agent cost in tokens, with k denoting $10^3$, and CV is its standard deviation divided by its mean.
The saving share uses expected fallback cost $qc$.
'''+LEGEND+r'''
Agent replays use a later emulator setup than the original program deployments.'''
}
# Raw strings above use doubled backslashes for readable escaped source text.
CAPTIONS={k:v.replace('\\\\','\\') for k,v in CAPTIONS.items()}
SPECS={
 'share':('tab:share',['Family','$c$','$c^{\\mathrm{prog}}$','$q$','Saving share'],['2.7em','2.0em','2.1em','2.1em'],r'\footnotesize'),
 'price':('tab:price',['Family','Attempt cost','Initial pass','Final pass','$N^*_{\\mathrm{marg}}$','$N^*_{\\mathrm{incl}}$'],['2.7em','2.1em','2.1em','2.7em','2.7em'],r'\scriptsize'),
 'verification':('tab:verification-repeat',['Family','Verified (of 3)','Known (of 5)','Extracted (of 5)'],['3.2em','2.2em','2.2em'],r'\footnotesize'),
 'paired':('tab:paired-replays',['Family','$c$','CV','Agent success','Program success','Saving share'],['2.7em','3.0em','3.0em','3.0em','2.7em'],r'\scriptsize')}

body_path=PAPER/'body.tex';original=body_path.read_text();main,sep,appendix=original.partition('\\appendix\n');assert sep
measurement_checks={}
for kind,(label,heads,widths,size) in SPECS.items():
    rows,values=measured_rows(kind,widths)
    heads=[h.replace('\\\\','\\') for h in heads]
    replacement=tab(label,heads,rows,CAPTIONS[kind],size,'1.3pt' if kind=='paired' else '2.5pt')
    blocks=re.findall(r'\\begin\{table\}.*?\\end\{table\}',main,re.S)
    found=[b for b in blocks if '\\label{'+label+'}' in b];assert len(found)==1,label
    main=main.replace(found[0],replacement)
    measurement_checks[label]=len(values)
assert body_path.read_text()==original,'Concurrent body edit; merge required.'
body_path.write_text(main+sep+appendix)

SELECT=[('reactive','ReAct'),('autorpa_once','AutoRPA (one build)'),
        ('toolpro_cost','ToolPro (cost rule)'),('earliest_cap','Eager + allowance'),
        ('fixed10_cap','After 10 arrivals'),('success10_cap','After 10 successes'),
        ('breakeven_cap','Savings threshold'),('projected_cap','Projected + allowance'),
        ('safe_earliest_allowance_025','Eager + both checks'),
        ('safe_arrival10_allowance_025','Arrival-10 + both'),
        ('safe_projected_allowance_025','Projected + both'),
        ('safe_count_025','Count + budget'),
        ('safe_history_025','Optimistic + budget'),
        ('safe_once_025','One initial try'),
        ('safe_projected_025',r'\textbf{PACE}')]
ABLATIONS=[('safe_projected_025',r'\textbf{PACE}'),
           ('safe_earliest_025','No savings test'),('projected','No cost budget'),
           ('pace_narrow_price','Equal attempt costs'),('earliest','Eager retry (remove both)')]

def val(c,p):return c['policies'][p]['ratio_agent']

def triple_for_real(policy,pattern):
    return [f'{val(next(c for c in DATA["cells"]["real_arrivals"] if c["model"]==m and c["pattern"]==pattern),policy):.3f}' for m in MODEL_IDS]

realrows=[]
for policy,label in SELECT:
    if policy=='safe_earliest_allowance_025':realrows.append(r'\midrule')
    realrows.append(' & '.join([label]+[cell('2.6em',triple_for_real(policy,s)) for s in ['sepsis','bpi2019','wiki_A','wiki_B']])+r' \\')
realcap=r'''Cost on four recorded arrival sequences with three model profiles.
Each value divides the policy's mean cost by ReAct's mean cost in the same condition, using ten paired runs.
'''+LEGEND+"\nSection~\\ref{sec:baselines} defines every rule, including the scope of the AutoRPA and ToolPro adaptations.\nThe final block uses PACE's total budget with $\\epsilon=0.25$."
real=tab('tab:realstreams',['Policy','Sepsis','BPI 2019','Wiki tools','Wiki humans'],realrows,realcap,r'\footnotesize','1.1pt')
real=real.replace(r'\begin{table}[t]',r'\begin{table}[!t]')


def grid_values(policy,group,mode):
    out=[]
    for m in MODEL_IDS:
        values=[val(c,policy) for c in DATA['cells'][group] if c['model']==m]
        assert len(values)==100
        number=statistics.mean(values) if mode=='mean' else max(values)
        out.append(f'{number:.3f}')
    return out

gridrows=[]
for policy,label in SELECT:
    if policy=='safe_earliest_allowance_025':gridrows.append(r'\midrule')
    gridrows.append(' & '.join([label]+[cell('3.4em',grid_values(policy,group,mode)) for group in ['original_grid','fresh_grid'] for mode in ['mean','max']])+r' \\')
gridcap=r'''Cost across all conditions in the two parameter grids.
Each grid contains one hundred conditions per model and eight paired runs per condition.
Mean is the mean of the cost ratios to ReAct for individual conditions, and Max is the largest such ratio of means across runs.
'''+LEGEND+'\nSection~\\ref{sec:baselines} defines every rule and adaptation.\nThe final block uses the same total budget with $\\epsilon=0.25$; Theorem~\\ref{thm:cost-protection} gives its prefix bound.'
gridcap=gridcap.replace('\\\\','\\')
grid=tab('tab:policysim',['Policy',r'\shortstack{Grid 1\\Mean}',r'\shortstack{Grid 1\\Max}',r'\shortstack{Grid 2\\Mean}',r'\shortstack{Grid 2\\Max}'],gridrows,gridcap,r'\scriptsize','0.8pt')
grid=grid.replace(r'\begin{table}[t]',r'\begin{table}[!t]')
(PAPER/'online_results_tables.tex').write_text('% Generated from paired source records by rewrite-review/render_safe_projected_tables.py.\n'+grid+'\n\n'+real+'\n')

ablationrows=[]
for policy,label in ABLATIONS:
    means=[]
    for group in ['original_grid','fresh_grid','real_arrivals']:
        values=[f'{statistics.mean(val(c,policy) for c in DATA["cells"][group] if c["model"]==m):.3f}' for m in MODEL_IDS]
        means.append(cell('3.2em',values))
    worst=max(val(c,policy) for group in ['original_grid','fresh_grid'] for c in DATA['cells'][group])
    ablationrows.append(' & '.join([label,*means,f'{worst:.3f}'])+r' \\')
ablcap=r'''Ablation of PACE's decision components on the same conditions and paired outcomes.
The first three columns report mean condition-wise cost ratios to ReAct.
'''+LEGEND+"\nThe last column is the largest condition-mean ratio across both grids and all three models.\nEqual attempt costs changes only the proposal's compilation cost estimate; actual charges and cost reservations remain unchanged.\nNo cost budget removes the whole protection layer, including its program list control."
ablation=tab('tab:ablation',['Variant','Grid 1 mean','Grid 2 mean','Logs mean','Grid max'],ablationrows,ablcap,r'\scriptsize','2pt')
(PAPER/'online_ablation_table.tex').write_text('% Generated from paired source records.\n'+ablation+'\n')
SENSITIVITY=ROOT/'experimental-results/guiexp/t2_sim_v3/pace_price_sensitivity_20260923/evidence.json'
SENS=json.loads(SENSITIVITY.read_text())
SENSITIVITY_ROWS=[('missing_price_r0.5',r'$r=0.5$'),('missing_price_r1',r'$r=1$'),
                  ('missing_price_r2',r'$r=2$'),('missing_price_r5',r'$r=5$'),
                  ('adverse_probability',r'Cost-dependent $p$'),
                  ('adverse_probability_recurrence',r'Cost-dependent $p$ + repeats')]
sensrows=[]
for scenario,label in SENSITIVITY_ROWS:
    values=[]
    for policy in ['safe_projected_025','pace_narrow_price']:
        values.append(cell('2.8em',[f'{SENS["by_model"][model][scenario][policy]["mean_ratio_agent"]:.3f}' for model in MODEL_IDS]))
    comparison=SENS['groups'][scenario]['pace_narrow_price']['mean_ratio_pace']
    sensrows.append(' & '.join([label,*values,f'{comparison:.3f}'])+r' \\')
senscap=r'''Sensitivity to missing costs and assigned cost dependencies.
Each row uses twelve combinations of models and logs and ten paired runs.
The first two columns show mean cost relative to ReAct.
'''+LEGEND+"\nThe last column gives the mean equal-cost/PACE ratio across the twelve conditions.\nThe listed $r=C^{\\mathrm{fail}}/C$ is nominal: Qwen Calc's saved partial-spend floor can raise its actual ratio.\nBoth policies use the same $0.25$ budget."
sensitivity=tab('tab:price-sensitivity',['Scenario','PACE','Equal attempt costs',r'Equal / PACE'],sensrows,senscap,r'\scriptsize','2pt')
(PAPER/'online_sensitivity_table.tex').write_text('% Generated from frozen sensitivity records.\n'+sensitivity+'\n')
proof={'measurement_cells':measurement_checks,'measurement_values':sum(measurement_checks.values()),
       'comparison_values':len(SELECT)*4*2*3,'ablation_values':len(ABLATIONS)*10,
       'sensitivity_values':len(SENSITIVITY_ROWS)*7,
       'selected_policies':SELECT,'ablation_policies':ABLATIONS,
       'evidence_sha256':hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
       'additional_evidence_sha256':hashlib.sha256(ADDITIONS.read_bytes()).hexdigest(),
       'matched_controls_sha256':hashlib.sha256(CONTROLS.read_bytes()).hexdigest(),
       'sensitivity_evidence_sha256':hashlib.sha256(SENSITIVITY.read_bytes()).hexdigest(),
       'appendix_sha256':hashlib.sha256((sep+appendix).encode()).hexdigest()}
(ROOT/'analysis/pace_revision_20260923/table-render-record.json').write_text(json.dumps(proof,indent=2)+'\n')
print('Rendered four measurement, two comparison, one ablation and one sensitivity table.')
