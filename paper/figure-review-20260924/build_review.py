from pathlib import Path
import re, json
ROOT = Path(__file__).resolve().parent
style = json.loads((ROOT.parent/'figure-sources/figure-style.json').read_text())
old = (ROOT / 'old/teaser.html').read_text()
new = old.replace('const W = 960, H = 496;', 'const W = 1200, H = 672;')
new = new.replace('const PANEL_Y = 194, PANEL_H = 250', 'const PANEL_Y = 298, PANEL_H = 270')
new = new.replace('231, 246,', '231, 252,').replace('228, 246,', '228, 252,')
new = new.replace('teaser-t1-cost-of-reuse-uncertain', 'teaser-review-v1').replace('Teaser T1 - The cost of reuse is uncertain', 'Figure 1: Compilation payback challenges')
# Give program artifacts shallow depth without changing their semantic symbols.
new = new.replace('icon(g, kind, cx - size / 2, cy - size / 2, size, stroke, key)', '''if (kind === "code-xml") {
        const r = size / 2 + 3, depth = 4;
        g.append("path").attr("d", `M${cx-r},${cy-r} l${depth},${-depth} h${2*r} v${2*r} l${-depth},${depth} Z`)
          .attr("fill", "#D3E7DF").attr("stroke", C.prog).attr("stroke-width", 0.9).attr("data-node", `${key}-depth`);
        box(g, cx-r, cy-r, 2*r, 2*r, C.progFill, C.prog, `${key}-face`, 3);
      }
      icon(g, kind, cx - size / 2, cy - size / 2, size, stroke, key)''')
# Replace the context strip with two related semantic units.
a = new.index('    /* ================= context strip')
b = new.index('    /* ================= challenge panels')
new = new[:a] + (ROOT/'new/teaser-context.js').read_text() + new[b:]
a = new.index('    /* ================= challenge panels')
b = new.index('  </script>', a)
new = new[:a] + (ROOT/'new/teaser-challenges.js').read_text() + new[b:]
new = new.replace('p.y + p.h + 26', 'p.y + p.h + 30')
new = new.replace('Schematic futures', 'Possible futures')
new = new.replace('Illustrative token cost', 'Token cost')
# Semantic labels on inherited text nodes.
new = new.replace('.attr("text-anchor", anchor).text(value)', '.attr("text-anchor", anchor).attr("data-label", value).text(value)')
new = re.sub(r'const FONT = .*?;', 'const FONT = '+json.dumps(style['font'])+';', new, count=1)
new = re.sub(r'const C = \{.*?\};', 'const C = '+json.dumps(style['colors'])+';', new, count=1, flags=re.S)
(ROOT/'new/teaser.html').write_text(new)
# Preserve the existing offline D3 and embedded fonts.
head = old.split('<body>')[0]
head = re.sub(r'<title>.*?</title>', '<title>Figure 2: PACE measurement and online algorithm</title>', head)
icons = {}
for path in (ROOT.parent/'figure-sources/icons').glob('*.svg'):
    icons[path.stem] = re.sub(r'^.*?<svg[^>]*>|</svg>\s*$', '', path.read_text(), flags=re.S)
license_text = (ROOT.parent/'figure-sources/icons/LICENSE').read_text()
js = (ROOT/'new/workflow.js').read_text()
math_assets = json.loads((ROOT/'new/math-assets.json').read_text())
for name, part in [('workflow','combined'),('measurement','measurement'),('online-algorithm','online')]:
    page_head = re.sub(r'<title>.*?</title>', '<title>PACE '+name+'</title>', head)
    (ROOT/f'new/{name}.html').write_text(page_head+'<body><div id="chart"></div><script>\n/* Icons: Mizar77/ml-paper-icons, Lucide.\n'+license_text+'\n*/\nconst FIGURE_STYLE='+json.dumps(style)+';\nconst PART='+json.dumps(part)+';\nconst MATH='+json.dumps(math_assets)+';\nconst ICONS='+json.dumps(icons)+';\n'+js+'\n</script></body></html>')
