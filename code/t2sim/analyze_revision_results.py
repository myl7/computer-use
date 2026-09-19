"""Exploratory paired comparisons of the frozen revision simulations."""
from pathlib import Path
import json,statistics,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import stats
root=Path(__file__).resolve().parents[2]/'experimental-results/guiexp/t2_sim/revision_20260913_report'
data=json.loads((root/'combined.json').read_text())
base={k:v for k,v in data.items() if k.endswith('/base')}
comps=['reactive','earliest','earliest_cap','fixed10_cap','success10_cap','breakeven_cap']
results=[]
for k,v in data.items():
 raw=json.loads(Path(v['file']).read_text())['rows'];s=v['summary']
 ours=[r['tokens'] for r in raw['projected_cap']]
 best=min(comps,key=lambda p:s[p]['mean_tokens'])
 early=[r['tokens'] for r in raw['earliest_cap']]
 item=dict(key=k,model=v['spec']['model'],admission=v['spec']['admission_mode'],stream=v['spec']['stream'],
  best_simple=best,ours_over_best_simple=s['projected_cap']['mean_tokens']/s[best]['mean_tokens'],
  ours_over_earliest_cap=s['projected_cap']['mean_tokens']/s['earliest_cap']['mean_tokens'],
  ours_vs_earliest_ci=stats.bootstrap_ratio_ci(ours,early,B=5000,seed=20260913),
  earliest_cap_over_uncapped=s['earliest_cap']['mean_tokens']/s['earliest']['mean_tokens'],
  projected_cap_over_uncapped=s['projected_cap']['mean_tokens']/s['projected']['mean_tokens'],
  ours_success_delta=s['projected_cap']['success_rate']-s['reactive']['success_rate'],
  ours_success_delta_ci=s['projected_cap']['success_delta_ci95'],
  ours_failed_spend_share=s['projected_cap']['mean_failed_compile_tokens']/s['projected_cap']['mean_tokens'])
 results.append(item)
base_rows=[r for r in results if r['key'].endswith('/base')]
print('BASE 42')
print('Wins vs best simple',sum(r['ours_over_best_simple']<1 for r in base_rows),'wins >1%',sum(r['ours_over_best_simple']<.99 for r in base_rows))
print('Range vs best simple',min(r['ours_over_best_simple'] for r in base_rows),max(r['ours_over_best_simple'] for r in base_rows))
print('Mean-best wins detail',[(r['key'],r['best_simple'],round(r['ours_over_best_simple'],6)) for r in base_rows if r['ours_over_best_simple']<1])
print('vs earliest cap better point/CI/worseCI',sum(r['ours_over_earliest_cap']<1 for r in base_rows),sum(r['ours_vs_earliest_ci'][1]<1 for r in base_rows),sum(r['ours_vs_earliest_ci'][0]>1 for r in base_rows))
for model in ['deepseek','z-ai']:
 rs=[r for r in base_rows if r['model'].startswith(model)]
 print(model,'cap vs nocap earliest median/range',statistics.median(r['earliest_cap_over_uncapped'] for r in rs),min(r['earliest_cap_over_uncapped'] for r in rs),max(r['earliest_cap_over_uncapped'] for r in rs))
 print(model,'cap vs nocap projected median/range',statistics.median(r['projected_cap_over_uncapped'] for r in rs),min(r['projected_cap_over_uncapped'] for r in rs),max(r['projected_cap_over_uncapped'] for r in rs))
 print(model,'quality delta range',min(r['ours_success_delta'] for r in rs),max(r['ours_success_delta'] for r in rs))
for key in ['deepseek/deepseek-v4-flash-vision-exp/binary_replay/poisson/base','z-ai/glm-5.3-flash/binary_replay/bpi2019/base','z-ai/glm-5.3-flash/soft/poisson/base']:
 v=data[key];r=next(r for r in results if r['key']==key)
 print('EXAMPLE',key,r, 'cost ratios',{p:round(v['summary'][p]['ratio_to_reactive'],6) for p in ['earliest','earliest_cap','projected','projected_cap','success10_cap']})
print('Sensitivity projected cap relative reactive:')
for key,v in data.items():
 if key.endswith('/base'):continue
 print(key,round(v['summary']['projected_cap']['ratio_to_reactive'],4),round(v['summary']['projected_cap']['success_rate']-v['summary']['reactive']['success_rate'],4))
(root/'comparative_analysis.json').write_text(json.dumps(dict(base_cells=42,total_cells=70,comparisons=results,interval_note='Paired percentile bootstrap; descriptive, no multiple-comparison adjustment; best-simple selected on cell means.'),indent=2)+'\n')
