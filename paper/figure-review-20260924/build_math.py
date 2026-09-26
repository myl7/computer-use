"""Render the paper's equations as offline vector SVG assets."""
from pathlib import Path
import json, re, subprocess, tempfile
root=Path(__file__).resolve().parent
formulas={
 'compilation-costs':r'C,\;C^{\mathrm{fail}}',
 'saving':r'\begin{aligned}s&=(1-q)c\\[-1pt]&\quad-c^{\mathrm{prog}}\end{aligned}',
 'marginal-payback':r'C/s',
 'source-payback':r'(C+kc)/s',
 'reference-update':r'A_t=A_{t-1}+a_t',
 'service-admission':r'\begin{gathered}K_{t-1}+U_t^{\mathrm{serv}}\\[-1pt]\leq(1+\epsilon)A_t\end{gathered}',
 'proposal':r'\begin{gathered}s_h>0\quad\text{and}\\[5pt]\widehat Hs_h>\widehat B+m\min(T,d)\end{gathered}',
 'compile-admission':r'\begin{gathered}\widetilde K_t+\max(C,C^{\mathrm{fail}})\\[2pt]\leq(1+\epsilon)A_t\end{gathered}',
 'cost-bound':r'K_t\leq(1+\epsilon)A_t',
}
assets={}
with tempfile.TemporaryDirectory(prefix='pace-equations-') as directory:
 work=Path(directory)
 for key,tex in formulas.items():
  (work/f'{key}.tex').write_text(r'\documentclass[border=0pt]{standalone}'+'\n'+r'\usepackage{amsmath,amssymb,bm}'+'\n'+r'\begin{document}$\displaystyle '+tex+r'$\end{document}')
  result=subprocess.run(['pdflatex','-interaction=nonstopmode','-halt-on-error',key+'.tex'],cwd=work,capture_output=True,text=True)
  if result.returncode: raise RuntimeError(result.stdout[-3000:])
  subprocess.run(['pdftocairo','-svg',str(work/f'{key}.pdf'),str(work/f'{key}.svg')],check=True,capture_output=True)
  svg=(work/f'{key}.svg').read_text()
  box=[float(n) for n in re.search(r'viewBox="([^"]+)"',svg).group(1).split()]
  markup=re.search(r'<svg[^>]*>(.*)</svg>',svg,re.S).group(1)
  ids=re.findall(r'id="([^"]+)"',markup)
  for identifier in sorted(ids,key=len,reverse=True):
   markup=markup.replace(f'id="{identifier}"',f'id="math-{key}-{identifier}"').replace(f'#{identifier}"',f'#math-{key}-{identifier}"').replace(f'#{identifier})',f'#math-{key}-{identifier})')
  markup=re.sub(r'fill:rgb\(0%,0%,0%\)', 'fill:currentColor',markup)
  markup=re.sub(r'fill="rgb\(0%,\s*0%,\s*0%\)"', 'fill="currentColor"',markup)
  markup=markup.replace('xlink:href','href')
  assets[key]={'tex':tex,'w':box[2],'h':box[3],'markup':markup}
(root/'new/math-assets.json').write_text(json.dumps(assets))
print(json.dumps({k:[round(v['w'],1),round(v['h'],1)] for k,v in assets.items()}))
