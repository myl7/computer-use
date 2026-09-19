"""Combine versioned simulation cells without discarding their provenance."""
import hashlib
import json
from pathlib import Path
import argparse


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',required=True,type=Path)
    ap.add_argument('--correction',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path)
    args=ap.parse_args()
    sources={}
    for directory in (args.base,args.correction):
        config=json.loads((directory/'config.json').read_text())
        for spec in config['jobs']:
            path=directory/(hashlib.sha256(spec['key'].encode()).hexdigest()[:20]+'.json')
            if not path.exists():raise SystemExit(f'Incomplete cell: {spec["key"]}')
            data=json.loads(path.read_text())
            assert data['spec']==spec
            for runs in data['rows'].values():
                for r in runs:
                    amount=sum(r[k] for k in ('reactive_tokens','extraction_tokens','compile_tokens','router_tokens','harm_tokens'))
                    assert abs(amount-r['tokens'])<1e-6*max(1,amount)
                    assert r['reactive_uses']+r['program_uses']>=r['arrivals']
            sources[spec['key']]=dict(file=str(path.resolve()),
                file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                source_sha256=config['source_sha256'],summary=data['summary'],
                spec=spec,stream=data['stream_summary'])
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'combined.json').write_text(json.dumps(sources,indent=2)+'\n')
    text=['# Corrected simulator results','',
          'Known-cost scenario simulation. No model calls or new measurement claims.','',
          'The four silent-failure cells use the corrected observer that excludes hidden harm from controller savings. All other cells retain their frozen initial source snapshot.','',
          'Ratios below compare mean accounted cost with always reactive. Intervals are paired bootstrap intervals over 20 repetitions, including real-stream mapping variation, conditional on measured/imputed cost constants.','',
          '| Model | Admission assumption | Stream | Earliest + cap | Success 10 + cap | Break-even + cap | Projected + cap (95% CI) |',
          '|---|---|---|---:|---:|---:|---:|']
    for key,d in sources.items():
        if not key.endswith('/base'):continue
        s=d['spec'];r=d['summary'];v=r['projected_cap'];ci=v['ratio_ci95']
        model='GLM' if 'glm' in s['model'] else 'DeepSeek'
        nums=[r[p]['ratio_to_reactive'] for p in ['earliest_cap','success10_cap','breakeven_cap']]
        text.append(f'| {model} | {s["admission_mode"]} | {s["stream"]} | {nums[0]:.3f} | {nums[1]:.3f} | {nums[2]:.3f} | {v["ratio_to_reactive"]:.3f} [{ci[0]:.3f}, {ci[1]:.3f}] |')
    text += ['', '## Interpretation limits','',
             '- Admission probabilities are binary-replay, soft (0.8/0.2), or uniform (0.5) scenarios, not repeated-build measurements.',
             '- Costs and hazard are supplied; arrival and admission estimates use only past observations.',
             '- The hard failed-spend check reserves the next failed-attempt cost; no competitive guarantee is asserted.',
             '- Fixed10 starts after ten total family arrivals and remains eligible after a break. Success10 resets its successful-reactive counter after admission or an observed break.',
             '- Real logs supply arrival order only; shuffled family cost mappings are held identical across policies in a repetition.',
             '- All policies have free family-ID lookup, free relisting, common fixed TTL, and the same router-cost scenario.',
             '- Harm penalties are evaluator costs and are not charged API expenditure.',
             '- No paper number or manuscript source was updated by this run.','']
    (args.out/'SUMMARY.md').write_text('\n'.join(text))
    print(f'Validated and combined {len(sources)} cells -> {args.out}')


if __name__=='__main__':main()
